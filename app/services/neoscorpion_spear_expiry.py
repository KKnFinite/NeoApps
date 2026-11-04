"""Bounded local SPEAR cleanup when a configured operational Sort ends.

Only unmatched, non-final SPEAR decision observations and their local derived
records are pruned. Never touches physical fueling history or R2 archives.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment as Assignment, NeoScorpionFuelWorkState as Work,
                        NeoScorpionFuelingEvent, SortDateOperation, SortTimelineSortSetting)
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
        zone = ZoneInfo(tz_name)
        # Ambiguous/nonexistent local times must never expire evidence early.
        end_utc = max(end.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
                      for fold in (0, 1)).replace(tzinfo=None)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return False
    return now_utc >= end_utc


def _connected():
    """Indexed EXISTS, including a physical visit not yet posted as an event."""
    p = Observation.payload['payload']
    work_id = p['work_id'].as_integer()
    fueler, truck = p['selected']['fueler_id'].as_integer(), p['selected']['truck_id'].as_integer()
    later = aliased(Observation)
    boundary = select(func.min(later.created_at)).where(
        later.assignment_id == Observation.assignment_id, later.cycle_number == Observation.cycle_number,
        later.kind == 'decision', later.created_at > Observation.created_at).correlate(Observation).scalar_subquery()
    event = select(NeoScorpionFuelingEvent.id).where(
        NeoScorpionFuelingEvent.fuel_assignment_id == Observation.assignment_id,
        NeoScorpionFuelingEvent.cycle_number == Observation.cycle_number,
        NeoScorpionFuelingEvent.tail_number == p['tail'].as_string(),
        NeoScorpionFuelingEvent.fueler_user_id == fueler, NeoScorpionFuelingEvent.fuel_truck_id == truck,
        or_(work_id.is_(None), NeoScorpionFuelingEvent.fuel_work_state_id == work_id),
        NeoScorpionFuelingEvent.started_at_utc >= Observation.created_at,
        or_(boundary.is_(None), NeoScorpionFuelingEvent.started_at_utc < boundary)
    ).correlate(Observation).exists()
    physical = select(Work.id).where(
        Work.fuel_assignment_id == Observation.assignment_id, Work.tail_number == p['tail'].as_string(),
        Work.physical_cycle_number == Observation.cycle_number, Work.physical_truck_id == truck,
        or_(work_id == Work.id, and_(work_id.is_(None), Work.physical_fueler_user_id == fueler)),
        or_(Work.manual_on_at_utc.is_not(None), Work.confirmed_on_at_utc.is_not(None))
    ).correlate(Observation).exists()
    return or_(event, physical)


def purge_closed_unmatched(gateway_id, *, now_utc=None, limit=400):
    now_utc = now_utc or datetime.utcnow()
    windows = {w.sort_name: w for w in SortTimelineSortSetting.query.filter_by(gateway_id=gateway_id)}
    if not windows:
        return 0
    tz = gateway_timezone()
    try:
        today = now_utc.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).date()
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return 0
    closed_sorts = []
    for name, window in windows.items():
        if not window.sort_window_start_local or not window.sort_window_end_local:
            continue
        day = today
        for _ in range(2):
            if sort_ended(type('SortDay', (), {'sort_date': day})(), window, now_utc, tz):
                break
            day -= timedelta(days=1)
        closed_sorts.append(and_(SortDateOperation.sort_name == name, SortDateOperation.sort_date <= day))
    if not closed_sorts:
        return 0
    connected = _connected()
    candidates = db.session.scalars(
        select(Observation).join(SortDateOperation, SortDateOperation.id == Observation.operation_id)
        .where(Observation.gateway_id == gateway_id, SortDateOperation.gateway_id == gateway_id,
               Observation.kind == 'decision', Observation.reconciled_at.is_(None),
               or_(*closed_sorts), ~connected,
               ~and_(Observation.attempts > 0, Observation.verified_at.is_(None)))
        .order_by(SortDateOperation.sort_date, Observation.created_at, Observation.id)
        .limit(max(1, min(int(limit), 400))).with_for_update(of=Observation, skip_locked=True)
    ).all()
    if not candidates:
        return 0
    # Canonical ON/completion writers lock the assignment. Skip busy owners,
    # then recheck after the lock: a newly committed event/ON must win.
    owners = set(db.session.scalars(select(Assignment.id).where(
        Assignment.id.in_({c.assignment_id for c in candidates})).with_for_update(skip_locked=True)))
    ids = [c.id for c in candidates if c.assignment_id in owners]
    protected = set(db.session.scalars(select(Observation.id).where(
        Observation.id.in_(ids), connected))) if ids else set()
    discard = [identifier for identifier in ids if identifier not in protected]
    if not discard:
        db.session.rollback()
        return 0
    parent = Observation.payload['payload']['decision_id'].as_string()
    children = db.session.execute(select(Observation.id, parent.label('parent')).where(
        Observation.gateway_id == gateway_id, Observation.kind.in_(('outcome', 'reason')),
        parent.in_(discard)).limit(2001)).all()
    if len(children) > 2000:
        db.session.rollback()
        return 0
    linked = db.session.scalars(select(Observation).where(Observation.id.in_([c.id for c in children]))
        .with_for_update(skip_locked=True).execution_options(populate_existing=True)).all() if children else []
    locked = {r.id:r for r in linked}
    # A lost PUT response or expired crash lease is still uncertain. Retain the
    # whole family until delivery verifies it, never orphan an in-flight export.
    blocked = {c.parent for c in children if c.id not in locked
               or (locked[c.id].attempts > 0 and locked[c.id].verified_at is None)}
    discard = [identifier for identifier in discard if identifier not in blocked]
    if not discard:
        db.session.rollback()
        return 0
    linked = [locked[c.id] for c in children if c.parent in discard]
    if linked:
        db.session.execute(delete(Observation).where(
            Observation.gateway_id == gateway_id, Observation.kind.in_(('outcome', 'reason')),
            Observation.id.in_([l.id for l in linked])))
    db.session.execute(delete(Observation).where(
        Observation.gateway_id == gateway_id, Observation.kind == 'decision',
        Observation.reconciled_at.is_(None), Observation.id.in_(discard)))
    db.session.commit()
    return len(discard)
