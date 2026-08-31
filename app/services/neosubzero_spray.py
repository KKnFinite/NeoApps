"""NeoSubZero current-sort trucks, spray gallons, and Deice Log projections."""

from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroSprayRecord,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    StaffingPerson,
)
from app.services.live_collaboration import entity_version
from app.services.neosubzero_constants import RAMP_ORDER
from app.services.neosubzero_staffing import current_subzero_staffing_pool
from app.services.time_display import format_local_hhmm


DEICER_REFRESH_KEY = "neosubzero.deicer_mobile"


class NeoSubZeroSprayError(ValueError):
    """Safe operator-facing truck/gallons validation error."""


def set_neosubzero_ucc_truck(
    operation,
    ramp,
    position_number,
    truck_number,
    *,
    user_id=None,
    assignment=None,
):
    """Stage the current truck for one ramp position without committing."""
    ramp, position = _slot(ramp, position_number)
    if operation is None:
        raise NeoSubZeroSprayError("No current sort is available.")
    truck = _short_text(truck_number, "Truck number", 32)
    if assignment is not None and (
        assignment.sort_date_operation_id != operation.id
        or assignment.ramp != ramp
        or assignment.position_number != position
    ):
        raise NeoSubZeroSprayError("Truck assignment does not match this position.")
    if assignment is None:
        if not truck:
            return None
        assignment = NeoSubZeroUccTruckAssignment(
            sort_date_operation_id=operation.id,
            ramp=ramp,
            position_number=position,
        )
        db.session.add(assignment)
    assignment.truck_number = truck or None
    assignment.updated_by_user_id = user_id
    return assignment


def decorate_departure_rows_with_spray(operation, rows):
    """Attach bounded current ownership and spray data to departure rows."""
    if operation is None or not rows:
        return rows
    staffing_rows = (
        NeoSubZeroUccAssignment.query.options(joinedload(NeoSubZeroUccAssignment.person))
        .filter_by(sort_date_operation_id=operation.id)
        .all()
    )
    truck_rows = NeoSubZeroUccTruckAssignment.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    spray_rows = NeoSubZeroSprayRecord.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    staffing = {
        (row.ramp, row.position_number, row.team_role): row
        for row in staffing_rows
    }
    trucks = {(row.ramp, row.position_number): row for row in truck_rows}
    sprays = {
        (row.departure_deice_event_id, row.pass_number, row.position_number): row
        for row in spray_rows
    }
    for departure in rows:
        event = departure.get("event")
        ramp = departure.get("ramp")
        ownership = []
        for position in range(1, 5):
            driver = staffing.get((ramp, position, "driver"))
            flyer = staffing.get((ramp, position, "flyer"))
            truck = trucks.get((ramp, position))
            ownership.append(
                {
                    "position": position,
                    "driver": driver.person if driver else None,
                    "flyer": flyer.person if flyer else None,
                    "truck": truck,
                    "truck_number": getattr(truck, "truck_number", None) or "",
                    "truck_version": entity_version(truck),
                }
            )
        departure["spray_positions"] = tuple(ownership)
        passes = []
        for pass_number, pass_type in enumerate(departure.get("pass_types") or (), 1):
            positions = []
            for owner in ownership:
                position = owner["position"]
                record = sprays.get((getattr(event, "id", None), pass_number, position))
                positions.append(
                    {
                        **owner,
                        "record": record,
                        "record_version": entity_version(record),
                        "gallons": _decimal_display(getattr(record, "gallons", None)),
                        "recorded_truck": getattr(record, "truck_number_snapshot", None),
                    }
                )
            passes.append(
                {
                    "number": pass_number,
                    "type": pass_type,
                    "surface": departure.get(f"pass{pass_number}_surface"),
                    "start": departure.get(f"pass{pass_number}_start"),
                    "end": departure.get(f"pass{pass_number}_end"),
                    "positions": tuple(positions),
                }
            )
        departure["application_passes"] = tuple(passes)
    return rows


def set_neosubzero_spray_gallons(
    operation,
    mission,
    event,
    pass_number,
    position_number,
    gallons,
    *,
    fluid_settings,
    application_context=None,
    user_id=None,
    record=None,
):
    """Stage one position/pass gallon record or explicit removal."""
    from app.services.neosubzero_departure_deice import PLAN_PASS_TYPES

    if operation is None or mission is None or event is None:
        raise NeoSubZeroSprayError("Choose a current departure-deice mission.")
    if (
        event.sort_date_operation_id != operation.id
        or event.sort_date_mission_id != mission.id
        or mission.sort_date_operation_id != operation.id
        or mission.mission_type != "departure"
    ):
        raise NeoSubZeroSprayError("Spray record does not match this current departure.")
    try:
        pass_index = int(pass_number)
        position = int(position_number)
    except (TypeError, ValueError) as exc:
        raise NeoSubZeroSprayError("Choose a valid pass and position.") from exc
    pass_types = PLAN_PASS_TYPES.get(event.treatment_plan, ())
    if pass_index < 1 or pass_index > len(pass_types) or position not in range(1, 5):
        raise NeoSubZeroSprayError("Choose a valid pass and position.")
    if record is not None and (
        record.departure_deice_event_id != event.id
        or record.pass_number != pass_index
        or record.position_number != position
    ):
        raise NeoSubZeroSprayError("Spray record does not match this pass position.")
    raw_gallons = str(gallons or "").strip()
    if not raw_gallons:
        if record is not None:
            db.session.delete(record)
        return None
    try:
        amount = Decimal(raw_gallons)
    except InvalidOperation as exc:
        raise NeoSubZeroSprayError("Gallons must be a positive number.") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise NeoSubZeroSprayError(
            "Gallons must be positive with no more than two decimal places."
        )

    start = getattr(event, f"pass{pass_index}_started_at_utc")
    end = getattr(event, f"pass{pass_index}_ended_at_utc")
    surface = getattr(event, f"pass{pass_index}_surface_area")
    if not start or not end or not surface:
        raise NeoSubZeroSprayError(
            "Complete this pass timing and surface before recording gallons."
        )
    ramp = _mission_ramp_from_rows(operation, mission)
    truck = NeoSubZeroUccTruckAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
        ramp=ramp,
        position_number=position,
    ).one_or_none()
    if not truck or not str(truck.truck_number or "").strip():
        raise NeoSubZeroSprayError("Assign a truck before recording gallons.")

    if record is None:
        staffing = {
            row.team_role: row
            for row in NeoSubZeroUccAssignment.query.options(
                joinedload(NeoSubZeroUccAssignment.person)
            ).filter_by(
                sort_date_operation_id=operation.id,
                ramp=ramp,
                position_number=position,
            ).all()
        }
        driver = staffing.get("driver")
        flyer = staffing.get("flyer")
        context = application_context or {}
        pass_type = "type_iv" if pass_types[pass_index - 1] == "Type IV" else "type_i"
        record = NeoSubZeroSprayRecord(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            departure_deice_event_id=event.id,
            pass_number=pass_index,
            position_number=position,
            truck_number_snapshot=str(truck.truck_number).strip(),
            pass_type=pass_type,
            fluid_name_snapshot=(
                fluid_settings.type_iv_fluid_name
                if pass_type == "type_iv"
                else fluid_settings.type_i_fluid_name
            ),
            concentration_percent_snapshot=(
                100 if pass_type == "type_iv" else fluid_settings.type_i_concentration_percent
            ),
            surface_area=surface,
            started_at_utc=start,
            ended_at_utc=end,
            driver_person_id=getattr(driver, "person_id", None),
            driver_name_snapshot=getattr(getattr(driver, "person", None), "full_name", None),
            flyer_person_id=getattr(flyer, "person_id", None),
            flyer_name_snapshot=getattr(getattr(flyer, "person", None), "full_name", None),
            reason_for_application=_short_text(
                context.get("reason_for_application"), "Reason for Application", 120
            ) or None,
            active_precipitation=_short_text(
                context.get("active_precipitation"), "Active Precipitation", 120
            ) or None,
            ambient_temperature=_short_text(
                context.get("ambient_temperature"), "Ambient temperature", 32
            ) or None,
            dew_point=_short_text(context.get("dew_point"), "Dew point", 32) or None,
            notes=_notes(context.get("notes")),
            recorded_by_user_id=user_id,
        )
        db.session.add(record)
    record.gallons = amount
    return record


def current_user_ucc_assignment(operation, user):
    """Resolve the linked active person's current UCC slot, if any."""
    employee_id = str(getattr(user, "employee_id", None) or "").strip().casefold()
    if operation is None or not employee_id:
        return None
    person = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        func.lower(func.trim(StaffingPerson.employee_id)) == employee_id,
    ).one_or_none()
    if person is None:
        return None
    eligible_ids = {row["person"].id for row in current_subzero_staffing_pool(operation)}
    if person.id not in eligible_ids:
        return None
    return (
        NeoSubZeroUccAssignment.query.options(joinedload(NeoSubZeroUccAssignment.person))
        .filter_by(sort_date_operation_id=operation.id, person_id=person.id)
        .one_or_none()
    )


def neosubzero_deice_log(operation):
    """Build Cryotech-oriented application groups from immutable spray snapshots."""
    if operation is None:
        return ()
    records = (
        NeoSubZeroSprayRecord.query.options(joinedload(NeoSubZeroSprayRecord.mission))
        .filter_by(sort_date_operation_id=operation.id)
        .all()
    )
    grouped = {}
    for record in records:
        key = (record.truck_number_snapshot.casefold(), record.departure_deice_event_id)
        grouped.setdefault(key, []).append(record)
    result = []
    for (_truck_key, _event_id), group_records in grouped.items():
        group_records.sort(key=lambda row: (row.started_at_utc, row.pass_number, row.position_number))
        first = group_records[0]
        operators = []
        for row in group_records:
            for name in (row.driver_name_snapshot, row.flyer_name_snapshot):
                if name and name not in operators:
                    operators.append(name)
        result.append(
            {
                "truck": first.truck_number_snapshot,
                "tail": str(first.mission.assigned_tail_number or "").strip().upper() or "-",
                "flight": str(first.mission.flight_number or "").strip() or "-",
                "reason": first.reason_for_application or "-",
                "precipitation": first.active_precipitation or "-",
                "ambient_temperature": first.ambient_temperature or "-",
                "dew_point": first.dew_point or "-",
                "notes": first.notes or "",
                "operators": tuple(operators),
                "sort_time": min(row.started_at_utc for row in group_records),
                "applications": tuple(
                    {
                        "pass_number": row.pass_number,
                        "type": "Type IV" if row.pass_type == "type_iv" else "Type I",
                        "fluid": row.fluid_name_snapshot,
                        "concentration": row.concentration_percent_snapshot,
                        "gallons": _decimal_display(row.gallons),
                        "surface": row.surface_area.replace("_", " ").title(),
                        "start": format_local_hhmm(row.started_at_utc, row.mission.timezone or None),
                        "end": format_local_hhmm(row.ended_at_utc, row.mission.timezone or None),
                        "duration": int((row.ended_at_utc - row.started_at_utc).total_seconds() // 60),
                        "position": row.position_number,
                    }
                    for row in group_records
                ),
            }
        )
    result.sort(key=lambda row: (row["truck"].casefold(), row["sort_time"], row["tail"]))
    return tuple(result)


def _mission_ramp_from_rows(operation, mission):
    from app.models import SortDateParkingAssignment
    from app.neonodes.neosubzero.services import _tail
    from app.services.neosubzero_departure_deice import _ramp_name

    tail = _tail(mission.assigned_tail_number)
    parking = SortDateParkingAssignment.query.filter(
        SortDateParkingAssignment.sort_date_operation_id == operation.id,
        func.upper(func.trim(SortDateParkingAssignment.tail_number)) == tail,
    ).one_or_none()
    ramp = _ramp_name(getattr(parking, "ramp_code", None))
    if ramp not in RAMP_ORDER:
        raise NeoSubZeroSprayError("This departure does not have a current UCC ramp.")
    return ramp


def _slot(ramp, position_number):
    normalized_ramp = str(ramp or "").strip().title()
    try:
        position = int(position_number)
    except (TypeError, ValueError) as exc:
        raise NeoSubZeroSprayError("Choose a valid ramp position.") from exc
    if normalized_ramp not in RAMP_ORDER or position not in range(1, 5):
        raise NeoSubZeroSprayError("Choose a valid ramp position.")
    return normalized_ramp, position


def _short_text(value, label, maximum):
    normalized = str(value or "").strip()
    if len(normalized) > maximum:
        raise NeoSubZeroSprayError(f"{label} must be {maximum} characters or fewer.")
    return normalized


def _notes(value):
    normalized = str(value or "").strip()
    if len(normalized) > 2000:
        raise NeoSubZeroSprayError("Notes must be 2000 characters or fewer.")
    return normalized or None


def _decimal_display(value):
    if value is None:
        return ""
    normalized = format(Decimal(value), "f").rstrip("0").rstrip(".")
    return normalized or "0"
