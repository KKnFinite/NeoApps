"""Recorded native rows for a two-wave, 52-mission synthetic Sort."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount, NeoSektorBallmatWaveCount, NeoSektorOpenBayState,
    NeoSektorSortState, NeoSektorWaveState, NeoSubZeroDepartureDeiceEvent,
    SortDateMission, StaffingDailyAttendance, StaffingPerson, StaffingUnit,
)
from tests.test_neoraiden import seed_sort


def record_attendance(operation):
    """Seed one HERE, one call-in and one employee without an attendance mark."""
    unit = StaffingUnit(unit_type="sort", name="Night")
    db.session.add(unit)
    for index, status in enumerate(("here", "call_in", None)):
        person = StaffingPerson(employee_id=f"RD-{index}", first_name="Test",
                                last_name=str(index), classification="seasonal")
        db.session.add(person)
        if status:
            db.session.add(StaffingDailyAttendance(person=person, sort=unit, status=status,
                attendance_date=operation.sort_date, sort_date_operation=operation))
    db.session.commit()


def record_counts(gateway, operation):
    state = NeoSektorSortState(gateway_id=gateway.id, gateway_code=gateway.code,
        sort_date=operation.sort_date, sort_name=operation.sort_name, planned_total=100)
    db.session.add(state)
    db.session.flush()
    for side, opening in (("EAST", 2), ("WEST", 3)):
        db.session.add(NeoSektorOpenBayState(sort_state_id=state.id, side=side, open_count=opening))
        db.session.add(NeoSektorBallmatCount(sort_state_id=state.id, side=side))
    for wave, left, east, west in (("1ST WAVE", 15, 6, 0), ("2ND WAVE", 32, 4, 7)):
        db.session.add(NeoSektorWaveState(sort_state_id=state.id, wave_name=wave, planned_count=left))
        for side, count in (("EAST", east), ("WEST", west)):
            db.session.add(NeoSektorBallmatWaveCount(sort_state_id=state.id, wave_name=wave, side=side, count=count))
    db.session.commit()
    return state


def busy_sort(gateway, *, extra_arrivals=26, extra_departures=24):
    op = seed_sort(gateway)
    record_counts(gateway, op)
    base = datetime(2026,4,13,2)
    stations = ("SDF","PHL","ONT","DFW","OAK","PDX","DEN","MSP")
    for kind, size in (("arrival", extra_arrivals), ("departure", extra_departures)):
        for index in range(size):
            station = stations[index % len(stations)]
            time = base + timedelta(minutes=index * 9)
            inbound = kind == "arrival"
            mission = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
                gateway_code=gateway.code, flight_number=f"UPS{(400 if inbound else 700) + index:04d}",
                mission_type=kind, origin=station if inbound else gateway.code,
                destination=gateway.code if inbound else station, planned_datetime_utc=time,
                assigned_tail_number=f"N{400 + index}UP" if index % 7 else None,
                arrival_status=("scheduled","en_route","on_ground","arrived","unloaded")[index%5] if inbound else None,
                departure_status=None if inbound else ("loading","scheduled","last_uld_enroute","departed")[index%4],
                eta_datetime_utc=time + timedelta(minutes=5) if index % 3 else None,
                actual_block_in_datetime_utc=time if inbound and index%5 in (3,4) else None,
                actual_block_out_datetime_utc=time if not inbound and index%4 == 3 else None,
                fuel_status=None if inbound else "assigned",planned_fuel_load=None if inbound else 36000)
            db.session.add(mission)
    first = SortDateMission.query.filter_by(sort_date_operation_id=op.id,flight_number="FX101").one()
    db.session.add(NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op.id,sort_date_mission_id=first.id,
        tail_number=first.assigned_tail_number,status="configured",configured_at_utc=base))
    db.session.commit()
    return op
