"""Nonphysical Dispatcher completion. No assignments, visits or inventory writes."""
from datetime import datetime
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import select, union_all

from app.extensions import db
from app.models import (NeoScorpionDirectEntry, NeoScorpionFuelAssignment,
    NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelAuditEntry,
    NeoScorpionFuelCycleHistory, NeoScorpionPendingUplift, SortDateMission)
from app.services.live_collaboration import entity_version
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change


class DirectEntryConflict(ValueError):
    pass


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='scorpion-direct-entry-v1')


def mission_state(operation, mission):
    from app.services import neoscorpion as fuel
    aircraft = fuel.detailed_aircraft_type_for_tail(mission.assigned_tail_number)
    limits = fuel._effective_aircraft_fuel_configuration(operation.gateway_id)[1]
    return dict(operation_id=operation.id, mission_id=mission.id, gateway_id=operation.gateway_id,
        tail=fuel._normalize_tail(mission.assigned_tail_number), version=entity_version(mission),
        aircraft_type=aircraft, window_minutes=operation.window_minutes,
        apu_rate=str(fuel._effective_apu_rate(operation.gateway_id,aircraft)),
        lateral_limit=int(limits[aircraft]) if limits.get(aircraft) is not None else None,
        required_fuel_lbs=mission.planned_fuel_load)


def conflict_reason(mission, assignment=None, *, has_work=False, has_record=False):
    if has_record or mission.fuel_status == 'complete' or mission.fuel_completed_at_utc:
        return 'Existing completion evidence must not be replaced.'
    if str(mission.departure_status or '').lower() in ('cancelled', 'canceled'):
        return 'Cancelled missions cannot receive direct entry.'
    if assignment and (assignment.assigned_fueler_user_id or assignment.assigned_truck_id
            or assignment.fueler_assigned_at_utc or assignment.truck_assigned_at_utc
            or assignment.active_work_state_id or assignment.completed_at_utc or assignment.fuel_on_board_at_utc
            or assignment.transfer_fuel_gallons is not None or int(assignment.current_cycle_number or 1) != 1
            or assignment.operational_status == 'hold_review' or assignment.review_status not in ('pending', None)):
        return 'Assigned resources or operational fuel evidence conflict with direct entry.'
    if has_work:
        return 'Recorded visits, submissions, plans or audit history require the operational workflow.'
    return ''


def token_for(operation, mission, user):
    return serializer().dumps({**mission_state(operation, mission), 'actor':user.id, 'request_key':str(uuid4())})


def completed_record(operation, mission_id):
    return NeoScorpionDirectEntry.query.filter_by(sort_date_operation_id=operation.id,
        sort_date_mission_id=mission_id).first()


def _locked_scope(gateway, mission_id):
    from app.services import neoscorpion as fuel
    operation = fuel.current_sort_operation(gateway)
    if not operation:
        raise DirectEntryConflict('No current sort is available. Draft retained.')
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    mission = SortDateMission.query.filter_by(id=mission_id, sort_date_operation_id=operation.id,
        mission_type='departure').populate_existing().with_for_update().first()
    if not mission:
        raise DirectEntryConflict('Mission is not in the current gateway/sort. Draft retained.')
    return operation, state, mission


def _has_history(assignment):
    return bool(assignment and history_assignment_ids([assignment]))


def history_assignment_ids(assignments):
    """One read-only batch for eligibility, including cleared old resources."""
    ids=[assignment.id for assignment in assignments if assignment is not None]
    if not ids:
        return set()
    statement=union_all(*(select(model.fuel_assignment_id).where(model.fuel_assignment_id.in_(ids))
        for model in (NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,
                      NeoScorpionFuelAuditEntry,NeoScorpionFuelCycleHistory,NeoScorpionPendingUplift)))
    return {row[0] for row in db.session.execute(statement)}


def parse_entry(gateway, operation, mission, form, now):
    from app.services import neoscorpion as fuel
    layout = fuel.tank_layout_for_tail(mission.assigned_tail_number)
    aircraft = fuel.detailed_aircraft_type_for_tail(mission.assigned_tail_number)
    if not mission.assigned_tail_number or not layout:
        raise ValueError('A real aircraft tail with a configured tank layout is required.')
    codes = {code for code, _ in layout}
    if fuel._submitted_tank_codes(form) - codes:
        raise ValueError('Tank layout does not match this aircraft.')
    tanks = {}
    for code, label in layout:
        actual = fuel.display_thousands_to_lbs(form.get('actual_'+code))
        remaining = fuel.display_thousands_to_lbs(form.get('remaining_'+code))
        if actual is None:
            raise ValueError(f'Enter measured Actual for {label}. Estimates cannot complete direct entry.')
        if any(value is not None and value > 2_147_483_647 for value in (actual,remaining)):
            raise ValueError(f'{label} fuel is outside the supported tank-reading storage range.')
        tanks[code] = dict(actual_lbs=actual, remaining_lbs=remaining)
    choices = {'':None, 'not_confirmed':None, 'no':False, 'yes':True}
    if form.get('apu_running', '') not in choices:
        raise ValueError('Select a valid APU Running status.')
    running = choices[form.get('apu_running', '')]
    source = form.get('apu_source_tank_code') or None
    rate = fuel._effective_apu_rate(gateway.id, aircraft) if running else None
    automatic = (fuel.calculate_apu_allowance_lbs(mission.planned_datetime_utc,
        operation.window_minutes, now, rate) if running else 0 if running is False else None)
    override = running is True and form.get('apu_override_enabled') == '1'
    allowance = fuel.display_thousands_to_lbs(form.get('apu_override_allowance')) if override else automatic
    if running is True and source not in codes:
        raise ValueError('Select a valid APU source tank.')
    if running is True and allowance is None:
        raise ValueError('A valid recommended or applied manual APU allowance is required.')
    if running is True and allowance > tanks[source]['actual_lbs']:
        raise ValueError('APU allowance exceeds the measured source tank fuel.')
    raw_tf = (form.get('transfer_fuel_gallons') or '').strip()
    if raw_tf and (not raw_tf.isascii() or not raw_tf.isdigit()):
        raise ValueError('T/F must be blank or a nonnegative whole number of gallons.')
    transfer = int(raw_tf) if raw_tf else None
    if transfer is not None and transfer > 2_147_483_647:
        raise ValueError('T/F is outside the supported range.')
    # Freeze the configured planner inputs and calculated evidence. Later
    # Settings changes cannot reinterpret this completed attestation.
    limits = fuel._effective_aircraft_fuel_configuration(gateway.id)[1]
    planned = fuel.plan_fuel_by_tank(aircraft, mission.planned_fuel_load,
        remaining_lbs_by_tank={c:t['remaining_lbs'] for c,t in tanks.items()},
        actual_lbs_by_tank={c:t['actual_lbs'] for c,t in tanks.items()},
        apu_running=running, apu_allowance_lbs=allowance, apu_source_tank_code=source,
        max_lateral_imbalance_lbs=limits.get(aircraft)) if all(t['remaining_lbs'] is not None for t in tanks.values()) else None
    return dict(version=1, source='dispatcher_direct_entry', tanks=tanks,
        tank_layout=layout, planned_lbs_by_tank={c:int(v) for c,v in planned.items()} if planned else None,
        max_lateral_imbalance_lbs=int(limits[aircraft]) if limits.get(aircraft) is not None else None,
        required_fuel_lbs=mission.planned_fuel_load, apu_running=running,
        apu_source_tank_code=source if running else None, apu_allowance_lbs=allowance,
        automatic_apu_allowance_lbs=automatic, apu_override_enabled=override,
        apu_override_allowance_lbs=allowance if override else None,
        applied_apu_rate_thousand_lbs_per_hour=str(rate) if rate is not None else None,
        transfer_fuel_gallons=transfer, notes=(form.get('notes') or '').strip())


def complete_entry(gateway, user, mission_id, form, *, now_utc=None):
    # Authorization is also checked here: callers must not bypass the route.
    from app.services.permission_rules import user_can
    if not user_can('neoscorpion.fuel_dispatch.edit', user):
        raise PermissionError('Dispatcher permission is required.')
    now = now_utc or datetime.utcnow()
    try:
        expected = serializer().loads(form.get('direct_entry_token', ''), max_age=12*60*60)
    except BadSignature as exc:
        raise DirectEntryConflict('Entry expired or invalid. Draft retained; reopen current mission to review.') from exc
    operation, state, mission = _locked_scope(gateway, mission_id)
    if expected.get('actor') != user.id or expected.get('mission_id') != mission.id:
        raise DirectEntryConflict('This entry belongs to another user or mission.')
    # Fingerprint the submitted facts, not a recalculated APU clock, so an
    # identical timed-out request can safely recover its original receipt.
    facts = {key:value for key,value in form.items() if key.startswith(('remaining_', 'actual_'))
        or key in ('apu_running','apu_source_tank_code','apu_override_enabled','apu_override_allowance',
                   'transfer_fuel_gallons','notes')}
    fingerprint = sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
    existing = completed_record(operation, mission.id)
    if existing:
        if existing.request_key == expected.get('request_key') and existing.request_fingerprint == fingerprint:
            return existing, False
        raise DirectEntryConflict('Already completed. Existing evidence has not been changed; draft retained.')
    if any(expected.get(key) != value for key,value in mission_state(operation, mission).items()):
        raise DirectEntryConflict('Mission/tail/requirements changed. Draft retained; review current values.')
    assignment = NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mission.id).populate_existing().with_for_update().first()
    reason = conflict_reason(mission, assignment, has_work=_has_history(assignment))
    if reason:
        raise DirectEntryConflict(reason+' Draft retained.')
    snapshot = parse_entry(gateway, operation, mission, form, now)
    record = NeoScorpionDirectEntry(gateway_id=gateway.id, sort_date_operation_id=operation.id,
        sort_date_mission_id=mission.id, tail_number=expected['tail'], aircraft_type=expected['aircraft_type'],
        request_key=expected['request_key'], request_fingerprint=fingerprint, completed_by_user_id=user.id,
        completed_at_utc=now, snapshot=snapshot)
    db.session.add(record)
    mission.fuel_status = 'complete'; mission.fuel_completed_at_utc = now
    record_nightly_operational_change(state, operation.id)
    db.session.flush()
    return record, True


def project(row, snapshot):
    """Use the same server math and tank presentation as the operational card."""
    from app.services import neoscorpion as fuel
    layout = snapshot['tank_layout']
    tanks = {code:SimpleNamespace(tank_code=code, **values) for code,values in snapshot.get('tanks', {}).items()}
    running, allowance = snapshot.get('apu_running'), snapshot.get('apu_allowance_lbs')
    planned = snapshot.get('planned_lbs_by_tank')
    row.update(fuel.tank_presentation(layout, tanks, planned, running, allowance))
    row.update(apu_running=running, apu_running_label='YES' if running else 'NO' if running is False else 'NOT CONFIRMED',
        apu_allowance_lbs=allowance, apu_allowance_display=fuel.format_apu_display_thousands(allowance) or 'UNKNOWN',
        automatic_apu_allowance_lbs=snapshot.get('automatic_apu_allowance_lbs'),
        automatic_apu_allowance_display=fuel.format_apu_display_thousands(snapshot.get('automatic_apu_allowance_lbs')),
        apu_source_tank_code=snapshot.get('apu_source_tank_code'),
        apu_source_tank_label=dict(layout).get(snapshot.get('apu_source_tank_code'), '—'),
        apu_override_enabled=snapshot.get('apu_override_enabled', False),
        apu_override_allowance_display=fuel.format_dispatch_thousands(snapshot.get('apu_override_allowance_lbs')),
        transfer_fuel_gallons=snapshot.get('transfer_fuel_gallons'), fueler_notes=snapshot.get('notes',''))
    row['neo_fuel_available'] = row['neo_fuel_lbs'] is not None
    if row['neo_fuel_lbs'] is None:
        row['neo_fuel_display'] = 'UNKNOWN'
    row['center_actual_lbs'] = tanks['ctr'].actual_lbs if row['detailed_aircraft_type'] == 'A300' and 'ctr' in tanks else None
    row['load_planning_output'] = fuel.load_planning_text(row['mission'],row['tail_number'],
        row['neo_fuel_lbs'],row['center_actual_lbs'])
    row['load_planning_ready'] = True
    row['estimated_fuel_display'] = 'UNKNOWN'
    row['fuel_load_display'] = fuel.format_dispatch_thousands(
        row['required_fuel_lbs']+allowance if row['required_fuel_lbs'] is not None and allowance is not None else None) or 'UNKNOWN'


def completed_values(record):
    """Canonical final readout without manufacturing a transfer event."""
    from app.services import neoscorpion as fuel
    snapshot=record.snapshot
    tanks={code:SimpleNamespace(**values) for code,values in snapshot['tanks'].items()}
    neo=fuel._fuel_work_calculation(snapshot['tank_layout'],tanks,
        snapshot.get('apu_running'),snapshot.get('apu_allowance_lbs'))[4]
    center=tanks['ctr'].actual_lbs if record.aircraft_type=='A300' and 'ctr' in tanks else None
    return neo,center
