"""Recorded native rows for a two-wave, 52-mission synthetic Sort."""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount, NeoSektorBallmatWaveCount, NeoSektorOpenBayState,
    NeoSektorSortState, NeoSektorWaveState, NeoSubZeroDepartureDeiceEvent,
    NeoScorpionFuelAssignment, NeoScorpionFuelTankState, NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState, NeoScorpionFuelingEvent,
    SortDateMission, StaffingDailyAttendance, StaffingPerson, StaffingUnit,
)
from tests.test_neoraiden import seed_sort


def record_departure_milestones(operation):
    row = SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
    complete = row.filter_by(flight_number="FX101").one()
    fob = row.filter_by(flight_number="FX202").one()
    truck = NeoScorpionFuelTruck(gateway_id=operation.gateway_id, truck_number="82")
    db.session.add(truck)
    for mission, tail in ((complete, "N431UP"), (fob, "N432UP")):
        mission.assigned_tail_number = tail
        mission.wave = "1"
        mission.planned_fuel_load = 40000
        mission.fuel_status = "complete"
    complete.planned_datetime_utc = datetime(2026, 4, 13, 6)
    complete.actual_block_out_datetime_utc = datetime(2026, 4, 13, 6, 9)
    complete.ramp_load_completed_at_utc = datetime(2026, 4, 13, 5, 59)
    complete.crew_load_completed_at_utc = datetime(2026, 4, 13, 6, 2)
    complete.actual_pure_pull_time_local = datetime(2026, 4, 12, 23, 47).time()
    complete.actual_mix_pull_time_local = datetime(2026, 4, 13, 0, 57).time()
    complete.departure_status = "departed"
    fob.actual_block_out_datetime_utc = datetime(2026, 4, 13, 4, 55)
    fob.departure_status = "departed"
    for mission in (complete, fob):
        assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id, review_status="complete",
            confirmed_tail_number=mission.assigned_tail_number)
        if mission is complete:
            assignment.completed_at_utc = datetime(2026, 4, 13, 5, 35)
        else:
            assignment.fuel_on_board_at_utc = datetime(2026, 4, 13, 4, 30)
            assignment.transfer_fuel_gallons = 0
        db.session.add(assignment)
        db.session.flush()
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
            tail_number=mission.assigned_tail_number, apu_running=False, apu_allowance_lbs=0)
        db.session.add(work)
        db.session.flush()
        assignment.active_work_state_id = work.id
        if mission is complete:
            db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,
                fuel_assignment_id=assignment.id, fuel_work_state_id=work.id, fuel_truck_id=truck.id,
                sequence_number=1, tail_number=mission.assigned_tail_number, neo_fuel_lbs=45600))
        else:
            for code in ("left", "ctr", "right"):
                db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code, actual_lbs=15000))
    db.session.commit()


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
