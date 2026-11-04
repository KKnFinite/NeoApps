"""Auditable personnel handoffs and read-only, per-cycle PROF eligibility.

Existing WorkState JSON and append-only fuel audits provide the durable evidence;
no new visit, event, inventory entry, schema, or background task is required.
"""
from sqlalchemy import tuple_

from app.extensions import db
from app.models import (NeoScorpionFuelWorkState as Work, NeoScorpionFuelingEvent as Event,
                        NeoScorpionFuelAuditEntry as Audit, NeoScorpionFuelAssignment as Assignment)

LIMIT = 20000
MULTIPLE_FUELERS = 'MULTIPLE FUELERS'
MULTIPLE_TRUCKS = 'MULTIPLE TRUCKS'
UNVERIFIED = 'PARTICIPATION UNVERIFIED'


def key(event):
    return event.fuel_assignment_id, int(event.cycle_number or 1)


def owner_id(work):
    """Editing handoff is distinct from the immutable operator at physical ON."""
    data=(getattr(work, 'performance_context', None) or {}).get('participation') or {}
    if data.get('handoff_fueler_id'): return data['handoff_fueler_id']
    return getattr(work,'_audited_handoff_owner',getattr(work, 'physical_fueler_user_id', None))


def hydrate_legacy_owners(works):
    """Read-through recovery of already-audited, unfinished pre-release handoffs.

    No backfill or permission write. Only an exact assignment/visit-linked audit
    can grant the replacement access; unrelated or unlinked audits are ignored.
    """
    candidates={w.id:w for w in works if not w.physical_finalized_at_utc
        and not (w.performance_context or {}).get('participation')}
    if not candidates:return
    for w in candidates.values():
        if 'Fueler changed during physical segment' in ((w.performance_context or {}).get('exclusions') or ()):
            w._audited_handoff_owner=None
    audits=db.session.query(Audit.fuel_work_state_id,Audit.new_value).join(
        Assignment,Assignment.id==Audit.fuel_assignment_id).filter(
        Audit.sort_date_operation_id==Assignment.sort_date_operation_id,
        tuple_(Audit.fuel_assignment_id,Audit.fuel_work_state_id).in_(
            [(w.fuel_assignment_id,w.id) for w in candidates.values()]),
        Audit.action=='swap_fueler',Audit.field_name=='assigned_fueler_user_id'
        ).order_by(Audit.id).limit(LIMIT+1).all()
    if len(audits)>LIMIT:
        for w in candidates.values():w._audited_handoff_owner=None
        return
    for row in audits:
        try: owner=int(row.new_value)
        except (TypeError,ValueError):owner=None
        candidates[row.fuel_work_state_id]._audited_handoff_owner=owner if owner and owner>0 else None


def record(work, fueler_id, *, truck_id=None):
    """Append a proven contributor under the caller's existing mutation locks."""
    facts = dict(work.performance_context or {})
    prior = facts.get('participation')
    data = dict(prior or dict(version=1, fueler_ids=[], truck_ids=[], verified=True))
    fuelers, trucks = set(data['fueler_ids']), set(data['truck_ids'])
    if prior is None and (work.manual_on_at_utc or work.confirmed_on_at_utc):
        # Preserve the original physical operator; assignment is current access,
        # not permission to rewrite that historical attribution.
        original = work.physical_fueler_user_id
        proven = (facts.get('fueler_id') == original and original is not None
                  and (work.manual_on_by_user_id == original or facts.get('on_source') == 'trusted automatic'))
        data['verified'] = bool(proven)
        if proven: fuelers.add(original)
        if work.physical_truck_id: trucks.add(work.physical_truck_id)
    if fueler_id is not None:
        fuelers.add(fueler_id)
        data['handoff_fueler_id'] = fueler_id
    else: data['verified'] = False
    if truck_id is not None: trucks.add(truck_id)
    data.update(fueler_ids=sorted(fuelers), truck_ids=sorted(trucks))
    if data != prior:
        facts['participation'] = data
        work.performance_context = facts


def handoff(work, old_id, new_id):
    if work is None: return
    facts = work.performance_context or {}
    physical = bool(work.manual_on_at_utc or work.confirmed_on_at_utc or work.off_at_utc)
    legacy_data = (not facts.get('participation') and (work.preparation_received_at_utc
        or work.actuals_received_at_utc or work.apu_running is not None or work.notes
        or any(t.remaining_lbs is not None or t.actual_lbs is not None for t in work.tank_states)))
    if legacy_data and not physical:
        facts = dict(facts)
        facts['participation'] = dict(version=1,fueler_ids=[],truck_ids=[],verified=False)
        work.performance_context = facts
    if physical:
        if old_id is not None: record(work, old_id)
        record(work, new_id)
    facts = dict(work.performance_context or {})
    data = dict(facts.get('participation') or dict(version=1,verified=True,fueler_ids=[],truck_ids=[]))
    data['handoff_fueler_id'] = new_id
    facts['participation'] = data
    work.performance_context = facts


def reasons(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        return [UNVERIFIED]
    fuelers, trucks = set(data.get('fueler_ids') or ()), set(data.get('truck_ids') or ())
    result = []
    if len(fuelers) > 1: result.append(MULTIPLE_FUELERS)
    if len(trucks) > 1: result.append(MULTIPLE_TRUCKS)
    if not data.get('verified') or len(fuelers) != 1 or len(trucks) != 1:
        if not result or not data.get('verified'): result.append(UNVERIFIED)
    return result


def cycles(events):
    """Two bounded batch queries; include peers outside a report's page/window.

    Late visits can disqualify an earlier immutable event. Derive the assessment
    without editing that event or any previously archived historical receipt.
    """
    events = list(events)
    keys = {key(e) for e in events}
    if not keys: return {}
    result = {k:dict(version=1,assignment_id=k[0],cycle_number=k[1],verified=True,
                     fueler_ids=set(),truck_ids=set(),work_ids=set()) for k in keys}
    works = db.session.query(Work.id,Work.fuel_assignment_id,Work.physical_cycle_number,
        Work.physical_fueler_user_id,Work.physical_truck_id,Work.tail_number,Work.manual_on_at_utc,
        Work.manual_on_by_user_id,Work.manual_on_truck_id,Work.confirmed_on_at_utc,
        Work.off_at_utc,Work.ended_early_at_utc,
        Work.performance_context['participation'].as_json().label('participation'),
        Work.performance_context['version'].as_integer().label('version'),
        Work.performance_context['fueler_id'].as_integer().label('fueler'),
        Work.performance_context['truck_id'].as_integer().label('truck'),
        Work.performance_context['on_source'].as_string().label('on_source'),
        Work.performance_context['exclusions'].as_json().label('exclusions'),
        Work.performance_context['duplicate_of_work_id'].as_integer().label('duplicate_of')).filter(
        db.or_(tuple_(Work.fuel_assignment_id,Work.physical_cycle_number).in_(keys),
            db.and_(Work.fuel_assignment_id.in_({k[0] for k in keys}),Work.physical_cycle_number.is_(None)))
        ).order_by(Work.id).limit(LIMIT+1).all()
    peers = db.session.query(Event.fuel_assignment_id,Event.cycle_number,Event.fuel_work_state_id,
        Event.fueler_user_id,Event.fuel_truck_id,Event.started_at_utc,Event.ended_at_utc,Event.tail_number).filter(
            tuple_(Event.fuel_assignment_id,Event.cycle_number).in_(keys)).limit(LIMIT+1).all()
    if len(works) > LIMIT or len(peers) > LIMIT:
        for item in result.values(): item['verified'] = False
    seen = {}
    tails = {k:set() for k in keys}
    for w in works[:LIMIT]:
        if w.duplicate_of: continue
        physical = bool(w.manual_on_at_utc or w.confirmed_on_at_utc or w.off_at_utc or w.ended_early_at_utc)
        if not physical and not w.participation: continue  # Planned/unused resources aren't contributors.
        scope = (w.fuel_assignment_id,w.physical_cycle_number)
        if scope not in result:
            for k,item in result.items():
                if k[0] == w.fuel_assignment_id: item['verified'] = False
            continue
        item = result[scope]
        item['work_ids'].add(w.id)
        proven = (w.version in (3,4) and w.fueler == w.physical_fueler_user_id and w.fueler is not None
                  and w.truck == w.physical_truck_id and w.truck is not None
                  and (w.manual_on_at_utc and w.manual_on_by_user_id == w.fueler and w.manual_on_truck_id == w.truck
                       or w.confirmed_on_at_utc and w.on_source == 'trusted automatic'))
        if physical:
            seen[w.id] = (scope,w.manual_on_at_utc or w.confirmed_on_at_utc,w.off_at_utc or w.ended_early_at_utc,w.tail_number)
            tails[scope].add(w.tail_number)
            item['verified'] &= bool(proven)
        if isinstance(w.participation,dict) and w.participation.get('version') == 1:
            item['fueler_ids'].update(w.participation.get('fueler_ids') or ())
            item['truck_ids'].update(w.participation.get('truck_ids') or ())
            item['verified'] &= bool(w.participation.get('verified'))
        if physical:
            if w.physical_fueler_user_id: item['fueler_ids'].add(w.physical_fueler_user_id)
            else: item['verified'] = False
            if w.physical_truck_id: item['truck_ids'].add(w.physical_truck_id)
            else: item['verified'] = False
        if 'Fueler changed during physical segment' in (w.exclusions or ()):
            # An old exclusion cannot distinguish pre-work reassignment from
            # actual participation. Retain it as unverified, without invented IDs.
            item['verified'] = False
    for e in [*peers[:LIMIT],*events]:
        item = result[key(e)]
        identity = (key(e),e.started_at_utc,e.ended_at_utc,e.tail_number)
        if seen.get(e.fuel_work_state_id) != identity: item['verified'] = False
        tails[key(e)].add(e.tail_number)
        if e.fueler_user_id: item['fueler_ids'].add(e.fueler_user_id)
        else: item['verified'] = False
        if e.fuel_truck_id: item['truck_ids'].add(e.fuel_truck_id)
        else: item['verified'] = False
    for scope,item in result.items():
        if len(tails[scope]) != 1: item['verified'] = False
        for field in ('fueler_ids','truck_ids','work_ids'): item[field] = sorted(item[field])
        item['exclusions'] = reasons(item)
    return result
