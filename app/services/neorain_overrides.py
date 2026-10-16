"""Explicit field authority over canonical missions; no background write replay."""
from contextlib import contextmanager
from datetime import datetime
import re
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateMission
from app.services.time_display import format_local_hhmm


# Values are projected into existing canonical facts, not parallel calculations.
FIELDS = {
    'sta': ('arrival', ('planned_datetime_utc', 'planned_datetime_local', 'planned_source')),
    'block_in': ('arrival', ('actual_block_in_datetime_utc', 'actual_block_in_source')),
    'etd': ('departure', ('eta_datetime_utc', 'eta_source')),
    'elmac': ('departure', ('elmac_completed_at_utc', 'elmac_completed_source')),
    'ramp_load_complete': ('departure', ('ramp_load_completed_at_utc', 'ramp_load_completed_source')),
    'crew_load_complete': ('departure', ('crew_load_completed_at_utc', 'crew_load_completed_source')),
    'official_block_out': ('departure', ('actual_block_out_datetime_utc', 'actual_block_out_source')),
    'included': ('departure', ('late_metrics_included_override',)),
    'no_return': ('departure', ('departure_status', 'departure_status_source')),
}


def pack(value):
    return value.isoformat() if isinstance(value, datetime) else value


def unpack(attr, value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value) if value is not None and ('datetime' in attr or attr.endswith('_at_utc')) else value


def snapshot(mission, field):
    return {attr: pack(getattr(mission, attr)) for attr in FIELDS[field][1]}


@contextmanager
def operator_projection(session):
    old = session.info.get('rain_operator_projection', False)
    session.info['rain_operator_projection'] = True
    try:
        yield
    finally:
        session.info['rain_operator_projection'] = old


@event.listens_for(Session, 'before_flush')
def protect_explicit_authority(session, _context, _instances):
    if session.info.get('rain_operator_projection'):
        return
    protected_attrs = {attr for _direction, attrs in FIELDS.values() for attr in attrs}
    missions = {m.id: m for m in session.dirty if isinstance(m, SortDateMission) and m.id
                and any(inspect(m).attrs[attr].history.has_changes() for attr in protected_attrs)}
    if not missions:
        return
    # Lock canonical missions first, then override rows, in stable order. A poll
    # that loaded old ORM state must observe a concurrently committed override.
    ids = sorted(missions)
    locked = dict(session.execute(select(SortDateMission.id, SortDateMission.departure_status)
        .where(SortDateMission.id.in_(ids)).order_by(SortDateMission.id).with_for_update()).all())
    rows = session.scalars(select(NeoRainMissionOverride)
        .where(NeoRainMissionOverride.sort_date_mission_id.in_(ids))
        .order_by(NeoRainMissionOverride.sort_date_mission_id, NeoRainMissionOverride.field)
        .with_for_update()).all()
    fresh = {r.id:r for r in session.execute(select(NeoRainMissionOverride.id,
        NeoRainMissionOverride.active, NeoRainMissionOverride.value, NeoRainMissionOverride.generation)
        .where(NeoRainMissionOverride.sort_date_mission_id.in_(ids))).all()}
    for row in rows:
        for attr in ('active', 'value', 'generation'):
            set_committed_value(row, attr, getattr(fresh[row.id], attr))
        mission = missions[row.sort_date_mission_id]
        attrs = FIELDS[row.field][1]
        state = inspect(mission)
        changed = {attr: pack(getattr(mission, attr)) for attr in attrs
                   if state.attrs[attr].history.has_changes()}
        if not changed:
            continue
        row.upstream = {**(row.upstream or {}), **changed}
        if row.active:
            if row.field == 'no_return' and row.value.get('departure_status') != 'departed':
                if mission.departure_status == 'departed':
                    mission.departure_status = locked[mission.id]
                    mission.departure_status_source = 'neorain'
                continue
            for attr, value in row.value.items():
                setattr(mission, attr, unpack(attr, value))


def observe_import(mission, field, values):
    """Capture a real upstream fact even when legacy native-source guards skip it."""
    with db.session.no_autoflush:
        db.session.execute(select(SortDateMission.id).where(SortDateMission.id == mission.id).with_for_update()).all()
        row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=mission.id, field=field)
        row = row.populate_existing().with_for_update().one_or_none()
        if row is None:
            return False
        row.upstream = {**(row.upstream or {}), **{key:pack(value) for key,value in values.items()}}
        return row.active


def override_is_cleared(row):
    """An active explicit null is different from upstream authority or no edit."""
    if not row or not row.active or row.field in ('included', 'no_return'):
        return False
    attr = FIELDS[row.field][1][0]
    return isinstance(row.value, dict) and attr in row.value and row.value[attr] is None


def edit_override(mission, operation, field, raw, actor, *, reset=False, clear=False, confirmed=False):
    from app.neonodes.neorain.services import (_parse_operational_hhmm,
        _mutate_no_return, _advance_departure_status)
    if field not in FIELDS or mission.mission_type != FIELDS[field][0]:
        raise ValueError('Field does not belong to this mission direction.')
    if mission.sort_date_operation_id != operation.id:
        raise ValueError('Mission is outside the current sort.')
    if clear and (reset or field in ('included', 'no_return') or raw not in (None, '')):
        raise ValueError('Only an empty time can be explicitly cleared.')
    row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=mission.id, field=field)
    row = row.populate_existing().with_for_update().one_or_none()
    before = row.value if row and row.active else None
    if reset:
        if not row or not row.active:
            return row, False
        values = row.upstream or {}
        with operator_projection(db.session):
            for attr, value in values.items():
                setattr(mission, attr, unpack(attr, value))
            row.active, row.value = False, None
            row.generation += 1
            row.modified_by_user_id, row.modified_at = actor.id, datetime.utcnow()
            row.sync_status, row.sync_detail = 'RESET', 'Upstream authority restored; no Google write.'
            mission.updated_at = datetime.utcnow()
            db.session.add(NeoRainOverrideAudit(override_id=row.id, generation=row.generation,
                action='RESET', actor_user_id=actor.id, before_value=before, after_value=None))
            db.session.flush()
        return row, True
    if clear and override_is_cleared(row):
        return row, False
    if not clear and (raw is None or (isinstance(raw, str) and not raw.strip())):
        return row, False  # Blank/untouched never becomes false or a write.
    if clear:
        value = None
    elif field in ('included', 'no_return'):
        if type(raw) is not bool:
            raise ValueError('Enter a typed true or false checkbox value.')
        value = raw
    else:
        if field in ('sta', 'etd') and not re.fullmatch(r'[0-9]{4}', str(raw)):
            raise ValueError('Enter exactly four digits (HHMM).')
        value = _parse_operational_hhmm(raw, operation, field.upper())
        if value is None:
            raise ValueError('Enter a complete valid time.')
    upstream = snapshot(mission, field) if row is None else row.upstream
    with operator_projection(db.session):
        if field == 'no_return':
            if not value and mission.departure_status == 'departed':
                if not confirmed:
                    raise ValueError('Confirm the No Return correction before unchecking.')
                if mission.departure_status_source != 'neorain':
                    raise ValueError('This departure is finalized by another authority; No Return cannot safely be reversed.')
            _mutate_no_return(mission, value)
        elif field == 'included':
            mission.late_metrics_included_override = value
        else:
            attrs = FIELDS[field][1]
            setattr(mission, attrs[0], value)
            setattr(mission, attrs[-1], 'neorain')
            if field == 'sta':
                from zoneinfo import ZoneInfo
                from datetime import timezone
                mission.planned_datetime_local = (value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(mission.timezone)).replace(tzinfo=None)
                    if value is not None else None)
            target = {'ramp_load_complete':'ramp_load_complete',
                      'crew_load_complete':'crew_load_complete', 'official_block_out':'blocked_out'}.get(field)
            if target:
                if clear:
                    from app.services.departure_progress import recompute_departure_status_after_external_clear
                    recompute_departure_status_after_external_clear(mission, target)
                else:
                    _advance_departure_status(mission, target)
        if row is None:
            row = NeoRainMissionOverride(gateway_id=operation.gateway_id,
                sort_date_operation_id=operation.id, sort_date_mission_id=mission.id,
                field=field, generation=0, upstream=upstream)
            db.session.add(row)
        row.active, row.value = True, snapshot(mission, field)
        row.generation = (row.generation or 0) + 1
        row.modified_by_user_id, row.modified_at = actor.id, datetime.utcnow()
        row.sync_status = 'NEO_ONLY' if clear else 'PENDING'
        row.sync_detail = 'Explicitly cleared in Neo; no Google write.' if clear else None
        row.sync_attempted_at = None
        mission.updated_at = datetime.utcnow()
        db.session.flush()
        db.session.add(NeoRainOverrideAudit(override_id=row.id, generation=row.generation,
            action='CLEAR' if clear else 'EDIT', actor_user_id=actor.id, before_value=before, after_value=row.value))
        db.session.flush()
    return row, True


def override_display(mission, field):
    row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=mission.id, field=field).one_or_none()
    return {'active': bool(row and row.active), 'cleared': override_is_cleared(row),
            'source': 'NEO' if row and row.active else 'UPSTREAM',
            'sync_status': row.sync_status if row else 'UNSET',
            'sync_detail': row.sync_detail if row else None}


def serialized_value(mission, field):
    if field == 'included':
        from app.neonodes.neorain.services import neorain_late_metrics_inclusion
        return neorain_late_metrics_inclusion(mission)['included']
    if field == 'no_return':
        return mission.departure_status == 'departed'
    return format_local_hhmm(getattr(mission, FIELDS[field][1][0]), mission.timezone) or ''
