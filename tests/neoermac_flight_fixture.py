"""Canonical synthetic Master -> current Mission -> signed Lineup selection.

Destination labels are only a convenience for unambiguous test schedules.
They never resolve missions: the application receives the actual master ID.
Repeated destinations require the caller to choose an explicit master.
"""
from datetime import time
from app.extensions import db
from app.models import MasterFlightSchedule
from app.services.neoermac_building_lineup import get_building_lineup_rows, lineup_field_name, lineup_original, slot_flight_value, save_building_lineup_destination
from tests.neoermac_lineup_forms import lineup_form as signed_lineup_form

def master(gateway, number, destination, *, sort_name='night', pure=None, mix=None):
    destination = str(destination or '').strip().upper()
    rows = MasterFlightSchedule.query.filter_by(gateway_id=gateway.id, sort_name=sort_name, mission_type='departure', flight_number=number, destination=destination).all()
    if len(rows) > 1:
        raise AssertionError('Choose an explicit duplicate schedule ID in this fixture')
    if rows:
        return rows[0]
    row = MasterFlightSchedule(gateway_id=gateway.id, gateway_code=gateway.code, sort_name=sort_name, mission_type='departure', flight_number=number, origin=gateway.code, destination=destination, active=True, active_days='monday,tuesday,wednesday,thursday,friday,saturday,sunday', planned_time_local=time(23), timezone='America/Chicago', pure_pull_time_local=pure, mix_pull_time_local=mix)
    db.session.add(row)
    db.session.flush()
    return row

def bind(mission, gateway, *, schedule=None):
    schedule = schedule or master(gateway, mission.flight_number, mission.destination, sort_name=mission.sort_name, pure=mission.pure_pull_time_local, mix=mission.mix_pull_time_local)
    assert schedule.gateway_id == gateway.id and schedule.sort_name == mission.sort_name
    assert schedule.flight_number == mission.flight_number and schedule.destination == mission.destination
    mission.master_flight_schedule_id = schedule.id
    db.session.flush()
    return schedule

def flight_id(gateway, destination):
    destination = str(destination or '').strip().upper()
    rows = MasterFlightSchedule.query.filter_by(gateway_id=gateway.id, sort_name='night', mission_type='departure', destination=destination, active=True).all()
    if len(rows) != 1:
        raise AssertionError(f'Test must select an explicit master: {destination} has {len(rows)} choices')
    return str(rows[0].id)

def select(gateway, runout, field, schedule_id):
    row = next((row for row in get_building_lineup_rows(gateway, initialize=False) if row.runout_key == runout))
    token = lineup_field_name(row, field)
    return save_building_lineup_destination(gateway, token, str(schedule_id or ''), expected_original=lineup_original(gateway, token, slot_flight_value(row, field)))

def lineup_form(gateway, values):
    """Send IDs selected from the real options, retaining signed originals.

    Unique test labels abbreviate an explicit schedule selection. Invalid text
    remains invalid for negative HTTP cases; ambiguity is never resolved.
    """
    values = dict(values)
    for key, value in list(values.items()):
        if key != 'destination' and (not key.startswith('lineup_')):
            continue
        text = str(value or '').strip()
        if not text or text.isdecimal():
            continue
        choices = MasterFlightSchedule.query.filter_by(gateway_id=gateway.id, sort_name='night', mission_type='departure', destination=text.upper(), active=True).all()
        if len(choices) > 1:
            raise AssertionError('Pass an explicit master ID for a repeated destination')
        if choices:
            values[key] = str(choices[0].id)
    return signed_lineup_form(gateway, values)
