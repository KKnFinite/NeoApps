"""Bounded local SPEAR cleanup when a configured operational Sort ends.

Only unmatched, non-final SPEAR decision observations and their local derived
records are pruned. Never touches physical fueling history or R2 archives.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, SortDateOperation, SortTimelineSortSetting
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services.gateway_matrix import gateway_timezone


def sort_ended(operation, window, now_utc, tz_name):
    if not operation or not window or not window.sort_window_start_local or not window.sort_window_end_local:
        return False
    start = datetime.combine(operation.sort_date, window.sort_window_start_local)
    end = datetime.combine(operation.sort_date, window.sort_window_end_local)
    if end <= start:
        end += timedelta(days=1)
    try:
        end_utc = end.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return False
    return now_utc >= end_utc


def purge_closed_unmatched(gateway_id, *, now_utc=None, limit=400):
    now_utc = now_utc or datetime.utcnow()
    windows = {w.sort_name: w for w in SortTimelineSortSetting.query.filter_by(gateway_id=gateway_id)}
    if not windows:
        return 0
    candidates = db.session.scalars(
        select(Observation).join(SortDateOperation, SortDateOperation.id == Observation.operation_id)
        .where(Observation.gateway_id == gateway_id, SortDateOperation.gateway_id == gateway_id,
               Observation.kind == 'decision', Observation.reconciled_at.is_(None))
        .order_by(SortDateOperation.sort_date, Observation.created_at, Observation.id)
        .limit(max(1, min(int(limit), 400))).with_for_update(of=Observation, skip_locked=True)
    ).all()
    if not candidates:
        return 0
    operations = {o.id: o for o in db.session.scalars(
        select(SortDateOperation).where(SortDateOperation.id.in_({c.operation_id for c in candidates})))}
    tz = gateway_timezone()
    closed = [c for c in candidates if c.operation_id in operations and sort_ended(
        operations[c.operation_id], windows.get(operations[c.operation_id].sort_name), now_utc, tz)]
    if not closed:
        return 0

    complete = [c for c in closed if all(c.payload['payload']['selected'].get(k) is not None
                                       for k in ('truck_id', 'fueler_id'))]
    events = {}
    next_boundary = {}
    if complete:
        for event in db.session.scalars(select(NeoScorpionFuelingEvent).where(
            NeoScorpionFuelingEvent.fuel_assignment_id.in_({c.assignment_id for c in complete}),
            NeoScorpionFuelingEvent.cycle_number.in_({c.cycle_number for c in complete}))):
            events.setdefault((event.fuel_assignment_id, int(event.cycle_number or 1)), []).append(event)
        later = aliased(Observation)
        next_boundary = dict(db.session.execute(select(
            Observation.id,
            select(func.min(later.created_at)).where(
                later.assignment_id == Observation.assignment_id,
                later.cycle_number == Observation.cycle_number, later.kind == 'decision',
                later.created_at > Observation.created_at).correlate(Observation).scalar_subquery()
        ).where(Observation.id.in_([c.id for c in complete]))).all())

    discard = []
    for row in closed:
        p = row.payload['payload']
        selected = p['selected']
        pair = (selected.get('fueler_id'), selected.get('truck_id'))
        upper = next_boundary.get(row.id) or datetime.max
        connected = None not in pair and any(
            e.fueler_user_id == pair[0] and e.fuel_truck_id == pair[1] and e.tail_number == p['tail']
            and (p['work_id'] is None or e.fuel_work_state_id == p['work_id'])
            and e.started_at_utc is not None and row.created_at <= e.started_at_utc < upper
            and e.started_at_utc <= now_utc
            for e in events.get((row.assignment_id, row.cycle_number), ()))
        if not connected:
            discard.append(row.id)
    if not discard:
        return 0

    linked = db.session.scalars(select(Observation).where(
        Observation.gateway_id == gateway_id, Observation.kind.in_(('outcome', 'reason')),
        Observation.payload['payload']['decision_id'].as_string().in_(discard)
    ).with_for_update(of=Observation)).all()
    if linked:
        db.session.execute(delete(Observation).where(
            Observation.gateway_id == gateway_id, Observation.kind.in_(('outcome', 'reason')),
            Observation.id.in_([l.id for l in linked])))
    db.session.execute(delete(Observation).where(
        Observation.gateway_id == gateway_id, Observation.kind == 'decision',
        Observation.reconciled_at.is_(None), Observation.id.in_(discard)))
    db.session.commit()
    return len(discard)
