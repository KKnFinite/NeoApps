"""Read-only assignment receipts. Effective activation/ON times are not sent times."""
import json
from datetime import datetime, timezone

from app.models import (NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionPendingUplift,
                        NeoScorpionFuelTruck, User)
from app.services.gateway_matrix import gateway_timezone
from app.services.time_display import format_local_hhmm


FIELDS = (('fueler', 'assigned_fueler_user_id'), ('truck', 'assigned_truck_id'))


def _resource_change(audit, field):
    return audit.field_name == field and (audit.action in ('swap_fueler', 'swap_truck') or (
        audit.action == 'segment_transfer' and audit.reason.startswith('Dispatcher assigned resources to a new ')))


def _time(value):
    try:
        value = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(value, datetime):
            return None
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    except (ValueError, TypeError):
        return None


def _id(value):
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _planned_times(plan, audits):
    """Only resource changes in saved Dispatcher plans advance the sent time.

    Promotion/review/target-only edits are not new assignment decisions. A plan
    created without a truck is not evidence that its eventual truck was sent.
    """
    result = {}
    previous = {}
    for audit in audits:
        if audit.field_name != 'pending_uplift':
            continue
        try:
            value = json.loads(audit.new_value or '')
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict) or value.get('id') != plan.id:
            continue
        if value.get('status') not in ('pending', 'review'):
            continue
        # These existing audit messages identify intentional Dispatcher saves;
        # automatic target reconciliation/review also serializes the same plan.
        if not audit.reason.startswith(('Dispatcher create ', 'Dispatcher save ')):
            continue
        for kind, _ in FIELDS:
            resource = _id(value.get(kind))
            if resource != previous.get(kind):
                result[kind] = (resource, audit.created_at, audit.changed_by_user_id)
            previous[kind] = resource
    return result


def attach_assignment_times(context):
    """Batch-resolve immutable visit facts/audits without writing on refresh.

    No created/updated/ON timestamp is ever used as an assignment timestamp.
    Unlinked legacy audits are usable only before a visit's physical boundary
    and when their latest resource identity agrees. Missing proof stays unknown.
    """
    op = context.get('operation')
    rows = context.get('rows', ())
    if not op:
        return context
    owners = {r['assignment'].id: getattr(r['assignment'], '_parent_assignment', r['assignment'])
              for r in rows if r.get('assignment')}
    if not owners:
        return context
    audits = NeoScorpionFuelAuditEntry.query.filter(
        NeoScorpionFuelAuditEntry.sort_date_operation_id == op.id,
        NeoScorpionFuelAuditEntry.fuel_assignment_id.in_(owners),
        NeoScorpionFuelAuditEntry.field_name.in_([field for _, field in FIELDS] + ['pending_uplift']),
    ).order_by(NeoScorpionFuelAuditEntry.created_at, NeoScorpionFuelAuditEntry.id).all()
    plans = NeoScorpionPendingUplift.query.filter(
        NeoScorpionPendingUplift.sort_date_operation_id == op.id,
        NeoScorpionPendingUplift.fuel_assignment_id.in_(owners),
    ).all()
    by_owner = {owner: [a for a in audits if a.fuel_assignment_id == owner] for owner in owners}
    plan_times = {p.id: _planned_times(p, by_owner[p.fuel_assignment_id]) for p in plans}
    promoted = {p.promoted_work_state_id: p for p in plans if p.promoted_work_state_id}
    events_by_work = {}
    for event in NeoScorpionFuelingEvent.query.filter(
        NeoScorpionFuelingEvent.sort_date_operation_id == op.id,
        NeoScorpionFuelingEvent.fuel_assignment_id.in_(owners),
    ).all():
        events_by_work.setdefault(event.fuel_work_state_id, []).append(event)
    names = {
        'fueler': {u.id: u.display_name for u in User.query.filter(User.id.in_(
            {a.assigned_fueler_user_id for a in owners.values() if a.assigned_fueler_user_id}
            | {_id(e.new_value) for e in audits if e.field_name == 'assigned_fueler_user_id'}
            | {w.physical_fueler_user_id for r in rows for w in r.get('console_visits', ())}
            | {p.proposed_fueler_user_id for p in plans})).all()},
        'truck': {t.id: t.truck_number for t in NeoScorpionFuelTruck.query.filter(
            NeoScorpionFuelTruck.gateway_id == op.gateway_id).all()},
    }
    zone = gateway_timezone(op.gateway)

    def receipt(kind, resource, at=None, actor=None, source=None):
        at = _time(at)
        return dict(kind=kind, resource_id=resource, name=names[kind].get(resource, '-'),
                    at=at, actor_id=actor, source=source,
                    display=format_local_hhmm(at, zone) if at else '-',
                    utc=at.isoformat() + 'Z' if at else '')

    def resolve(owner, work, kind, resource, *, current=False):
        if resource is None:
            return receipt(kind, resource)
        field = dict(FIELDS)[kind]
        facts = (work.performance_context or {}) if work else {}
        if work and not _time(facts.get(kind + '_assigned_at')):
            recorded = [e for e in events_by_work.get(work.id, ()) if e.tail_number == work.tail_number
                        and (not work.physical_cycle_number or e.cycle_number == work.physical_cycle_number)]
            # A legacy Work can own several old events. Never pick first/latest
            # when its event-to-visit provenance is not independently unique.
            if len(recorded) == 1:
                event = recorded[0]
                snapshot = event.performance_snapshot or {}
                if getattr(event, 'fueler_user_id' if kind == 'fueler' else 'fuel_truck_id') == resource:
                    facts = snapshot
        on = (work.manual_on_at_utc or work.confirmed_on_at_utc or work.on_at_utc) if work else None
        end = (work.off_at_utc or work.ended_early_at_utc or work.physical_finalized_at_utc) if work else None
        cutoff = (end or datetime.max) if current else (on or end or (
            datetime.max if work and work.id == owner.active_work_state_id else work.created_at if work else datetime.max))
        events = [a for a in by_owner[owner.id] if _resource_change(a, field) and a.created_at <= cutoff
                  and (a.fuel_work_state_id is None or (work and a.fuel_work_state_id == work.id))
                  and (not work or int(work.physical_cycle_number or 1) == 1
                       or a.fuel_work_state_id == work.id or a.created_at >= work.created_at)]
        # A Dispatcher swap within the same visit has a newer, explicit receipt.
        latest = events[-1] if events else None
        plan = promoted.get(work.id) if work else None
        if plan:
            if (latest and latest.fuel_work_state_id == work.id and latest.created_at >= work.created_at
                    and _id(latest.new_value) == resource):
                return receipt(kind, resource, latest.created_at, latest.changed_by_user_id, 'Assignment audit')
            planned = plan_times[plan.id].get(kind)
            if planned and planned[0] == resource:
                return receipt(kind, resource, planned[1], planned[2], 'Dispatcher plan')
            return receipt(kind, resource)  # Never relabel activation as sent.
        fact_at = _time(facts.get(kind + '_assigned_at'))
        fact_id = facts.get('fueler_id' if kind == 'fueler' else 'truck_id')
        if latest and _id(latest.new_value) == resource and (not fact_at or latest.created_at >= fact_at):
            return receipt(kind, resource, latest.created_at, latest.changed_by_user_id, 'Assignment audit')
        if not plan and fact_at and fact_id == resource and fact_at <= cutoff and facts.get('assignment_actor_id'):
            return receipt(kind, resource, fact_at, facts['assignment_actor_id'], 'Physical visit snapshot')
        if current and not plan:
            at = _time(getattr(owner, kind + '_assigned_at_utc'))
            # Current fields are only evidence for their own current resources,
            # not a historical same-truck visit or restored source assignment.
            if (at and at <= cutoff and getattr(owner, field) == resource and owner.resource_assignment_by_user_id
                    and (not latest or (_id(latest.new_value) == resource and latest.created_at <= at))):
                return receipt(kind, resource, at, owner.resource_assignment_by_user_id, 'Current assignment')
        return receipt(kind, resource)

    for row in rows:
        if not row.get('assignment'):
            row['assignment_receipt'] = []
            continue
        owner = owners[row['assignment'].id]
        visits = sorted(row.get('console_visits', ()), key=lambda w: (w.visit_number or 0, w.id))
        previous_end = datetime.min
        for work in visits:
            work.console_assignment_receipt = [resolve(owner, work, kind, getattr(work,
                'physical_fueler_user_id' if kind == 'fueler' else 'physical_truck_id')) for kind, _ in FIELDS]
            # Explicitly linked mid-visit replacements retain their own actor/time
            # instead of rewriting the original physical Fueler's receipt.
            boundary = work.off_at_utc or work.ended_early_at_utc or work.physical_finalized_at_utc
            work.console_assignment_changes = [receipt(kind, _id(a.new_value), a.created_at,
                a.changed_by_user_id, 'Assignment audit') for kind, field in FIELDS for a in by_owner[owner.id]
                if (a.fuel_work_state_id == work.id or (a.fuel_work_state_id is None
                    and previous_end is not None and previous_end < a.created_at <= (boundary or datetime.max)))
                and _resource_change(a, field) and a.new_value is not None
                and not any(x['kind'] == kind and x['resource_id'] == _id(a.new_value)
                            and x['at'] == a.created_at for x in work.console_assignment_receipt)]
            previous_end = boundary
        work = next((w for w in visits if w.id == owner.active_work_state_id), None)
        row['assignment_receipt'] = [resolve(owner, work, kind, getattr(owner, field), current=True)
                                     for kind, field in FIELDS]
        history = ([*work.console_assignment_receipt, *work.console_assignment_changes] if work else
                   [receipt(kind, _id(a.new_value), a.created_at, a.changed_by_user_id, 'Assignment audit')
                    for kind, field in FIELDS for a in by_owner[owner.id]
                    if _resource_change(a, field) and a.new_value is not None])
        row['assignment_changes'] = [item for item in history
            if item['resource_id'] is not None and not any(
                item['kind'] == live['kind'] and item['resource_id'] == live['resource_id']
                and item['at'] == live['at'] for live in row['assignment_receipt'])]
        p = row.get('pending_uplift')
        if p:
            row['planned_assignment_receipt'] = [receipt(kind, getattr(p, 'proposed_fueler_user_id' if kind == 'fueler' else 'proposed_truck_id'),
                *(plan_times[p.id].get(kind, (None, None, None))[1:]), source='Dispatcher plan') for kind, _ in FIELDS]
    return context
