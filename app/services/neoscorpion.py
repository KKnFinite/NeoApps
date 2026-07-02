from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask_login import current_user

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,
    NeoScorpionSettings,
    NeoScorpionTailFuelState,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.services.parking_aircraft import resolve_parking_aircraft_type_from_tail
from app.services.time_display import format_local_hhmm


DEFAULT_FUEL_DENSITY_LBS_PER_GALLON = 6.7
CALCULATION_NOT_CONFIGURED_MESSAGE = "Fuel calculation not configured for this aircraft type yet."


def display_thousands_to_lbs(value):
    amount = _decimal_or_none(value)
    if amount is None:
        return None
    if amount < 0:
        raise ValueError("Fuel pounds cannot be negative.")
    return int((amount * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def lbs_to_display_thousands(value):
    amount = _decimal_or_none(value)
    if amount is None:
        return None
    return (amount / Decimal("1000")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def gallons_to_lbs(gallons, density_lbs_per_gallon):
    gallons_value = _decimal_or_none(gallons)
    density = _positive_decimal(density_lbs_per_gallon, "Fuel density must be greater than zero.")
    if gallons_value is None:
        return None
    if gallons_value < 0:
        raise ValueError("Fuel gallons cannot be negative.")
    return int((gallons_value * density).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def lbs_to_gallons(lbs, density_lbs_per_gallon):
    lbs_value = _decimal_or_none(lbs)
    density = _positive_decimal(density_lbs_per_gallon, "Fuel density must be greater than zero.")
    if lbs_value is None:
        return None
    if lbs_value < 0:
        raise ValueError("Fuel pounds cannot be negative.")
    return int((lbs_value / density).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class NeoScorpionMenuItem:
    label: str
    endpoint: str
    permission: str
    key: str
    active: bool = False


NEOSCORPION_MENU = (
    NeoScorpionMenuItem("Fuel Dispatch", "neoscorpion.fuel_dispatch", "neoscorpion.fuel_dispatch.view", "dispatch"),
    NeoScorpionMenuItem("Fueler", "neoscorpion.fueler", "neoscorpion.fueler.view", "fueler"),
    NeoScorpionMenuItem("Truck Manager", "neoscorpion.truck_manager", "neoscorpion.truck_manager.view", "trucks"),
    NeoScorpionMenuItem("Settings", "neoscorpion.settings", "neoscorpion.settings.view", "settings"),
    NeoScorpionMenuItem("Fuel History", "neoscorpion.history", "neoscorpion.history.view", "history"),
)


def visible_neoscorpion_menu_items(user_can_func, current_endpoint=None):
    items = []
    for item in NEOSCORPION_MENU:
        if not user_can_func(item.permission):
            continue
        items.append(
            NeoScorpionMenuItem(
                item.label,
                item.endpoint,
                item.permission,
                item.key,
                active=item.endpoint == current_endpoint,
            )
        )
    return items


def current_sort_operation(gateway):
    return (
        SortDateOperation.query.filter(
            SortDateOperation.archived_at_utc.is_(None),
            db.or_(
                SortDateOperation.gateway_id == gateway.id,
                SortDateOperation.gateway_code == gateway.code,
            ),
        )
        .order_by(
            SortDateOperation.sort_date.desc(),
            SortDateOperation.generated_at_utc.desc(),
            SortDateOperation.id.desc(),
        )
        .first()
    )


def fuel_dispatch_context(gateway):
    operation = current_sort_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "fuelers": _fueler_users(),
            "trucks": _fuel_trucks(gateway),
            "settings": ensure_neoscorpion_settings(gateway),
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        }

    missions = _departure_missions(operation)
    return {
        "operation": operation,
        "rows": _fuel_rows(operation, missions),
        "fuelers": _fueler_users(),
        "trucks": _fuel_trucks(gateway),
        "settings": ensure_neoscorpion_settings(gateway),
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
    }


def fueler_context(gateway, user):
    operation = current_sort_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "settings": ensure_neoscorpion_settings(gateway),
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        }

    assignments = (
        NeoScorpionFuelAssignment.query.join(SortDateMission)
        .filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelAssignment.assigned_fueler_user_id == user.id,
            SortDateMission.mission_type == "departure",
        )
        .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
        .all()
    )
    missions = [assignment.sort_date_mission for assignment in assignments if assignment.sort_date_mission]
    return {
        "operation": operation,
        "rows": _fuel_rows(operation, missions),
        "settings": ensure_neoscorpion_settings(gateway),
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
    }


def truck_manager_context(gateway):
    return {"trucks": _fuel_trucks(gateway)}


def settings_context(gateway):
    return {"settings": ensure_neoscorpion_settings(gateway)}


def history_context(gateway):
    operation = current_sort_operation(gateway)
    completed = []
    if operation:
        assignments = (
            NeoScorpionFuelAssignment.query.join(SortDateMission)
            .filter(
                NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
                db.or_(
                    NeoScorpionFuelAssignment.review_status == "complete",
                    SortDateMission.fuel_status == "complete",
                ),
            )
            .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
            .all()
        )
        completed = _fuel_rows(operation, [assignment.sort_date_mission for assignment in assignments])
    return {"operation": operation, "completed_rows": completed}


def save_dispatch_row(gateway, form):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion dispatch.")

    mission_id = _int_or_none(form.get("mission_id"))
    mission = _departure_mission_for_operation(operation, mission_id)
    if not mission:
        raise ValueError("Departure mission was not found for the current sort operation.")

    mission.planned_fuel_load = display_thousands_to_lbs(form.get("required_fuel"))
    mission.planned_fuel_updated_at = datetime.utcnow()

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if tail_number:
        tail_fuel_state = ensure_tail_fuel_state(operation, tail_number)
        tail_fuel_state.inbound_fuel_lbs = display_thousands_to_lbs(form.get("inbound_fuel"))
        tail_fuel_state.apu_lbs = _int_or_none(form.get("apu_lbs"))

    assignment = ensure_fuel_assignment(operation, mission)
    assignment.assigned_fueler_user_id = _int_or_none(form.get("assigned_fueler_user_id"))
    assignment.assigned_truck_id = _int_or_none(form.get("assigned_truck_id"))
    assignment.review_status = _clean_choice(
        form.get("review_status"),
        {"pending", "assigned", "review", "complete"},
        "pending",
    )
    assignment.load_planning_note = (form.get("load_planning_note") or "").strip()
    db.session.flush()
    return assignment


def save_fueler_entry(gateway, user, form):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion fueler entry.")

    assignment_id = _int_or_none(form.get("assignment_id"))
    assignment = NeoScorpionFuelAssignment.query.filter_by(
        id=assignment_id,
        sort_date_operation_id=operation.id,
        assigned_fueler_user_id=user.id,
    ).first()
    if not assignment:
        raise ValueError("Fuel assignment was not found for this fueler.")

    mission = assignment.sort_date_mission
    tail_number = _normalize_tail(mission.assigned_tail_number if mission else "")
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")

    tail_fuel_state = ensure_tail_fuel_state(operation, tail_number)
    tail_fuel_state.fob_lbs = display_thousands_to_lbs(form.get("fob"))
    tail_fuel_state.center_fuel_lbs = display_thousands_to_lbs(form.get("center_fuel"))
    tail_fuel_state.apu_lbs = _int_or_none(form.get("apu_lbs"))
    tail_fuel_state.actual_fuel_lbs = display_thousands_to_lbs(form.get("actual_fuel"))
    tail_fuel_state.notes = (form.get("notes") or "").strip()
    tail_fuel_state.status = _clean_choice(
        form.get("tail_fuel_status"),
        {"pending", "in_progress", "review", "complete"},
        "pending",
    )
    assignment.transfer_fuel_gallons = _int_or_none(form.get("transfer_fuel_gallons"))
    db.session.flush()
    return tail_fuel_state


def save_truck(gateway, form):
    truck_id = _int_or_none(form.get("truck_id"))
    truck_number = (form.get("truck_number") or "").strip().upper()
    if not truck_number:
        raise ValueError("Truck number is required.")

    truck = None
    if truck_id:
        truck = NeoScorpionFuelTruck.query.filter_by(id=truck_id, gateway_id=gateway.id).first()
        if not truck:
            raise ValueError("Fuel truck was not found.")
    if not truck:
        truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number=truck_number)
        db.session.add(truck)

    truck.truck_number = truck_number
    truck.description = (form.get("description") or "").strip()
    truck.capacity_gallons = _int_or_none(form.get("capacity_gallons"))
    truck.remaining_fuel_gallons = _int_or_none(form.get("remaining_fuel_gallons"))
    truck.vendor_driver_name = (form.get("vendor_driver_name") or "").strip()
    truck.is_active = form.get("is_active") == "1"
    truck.is_out_of_service = form.get("is_out_of_service") == "1"
    db.session.flush()
    return truck


def deactivate_truck(gateway, form):
    truck_id = _int_or_none(form.get("truck_id"))
    truck = NeoScorpionFuelTruck.query.filter_by(id=truck_id, gateway_id=gateway.id).first()
    if not truck:
        raise ValueError("Fuel truck was not found.")
    truck.is_active = False
    db.session.flush()
    return truck


def save_settings(gateway, form):
    settings = ensure_neoscorpion_settings(gateway)
    density = _decimal_or_none(form.get("fuel_density_lbs_per_gallon"))
    if density is None:
        settings.fuel_density_lbs_per_gallon = None
    else:
        settings.fuel_density_lbs_per_gallon = float(
            _positive_decimal(density, "Fuel density must be greater than zero.")
        )
    settings.fob_difference_threshold_lbs = _int_or_none(form.get("fob_difference_threshold_lbs"))
    settings.tf_vs_estimated_threshold_lbs = _int_or_none(
        form.get("tf_vs_estimated_threshold_lbs")
    )
    if current_user and getattr(current_user, "is_authenticated", False):
        settings.updated_by_user_id = current_user.id
    db.session.flush()
    return settings


def ensure_neoscorpion_settings(gateway):
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    if settings:
        return settings
    settings = NeoScorpionSettings(
        gateway_id=gateway.id,
        fuel_density_lbs_per_gallon=DEFAULT_FUEL_DENSITY_LBS_PER_GALLON,
    )
    db.session.add(settings)
    db.session.flush()
    return settings


def ensure_tail_fuel_state(operation, tail_number):
    tail_number = _normalize_tail(tail_number)
    tail_fuel_state = NeoScorpionTailFuelState.query.filter_by(
        sort_date_operation_id=operation.id,
        tail_number=tail_number,
    ).first()
    if tail_fuel_state:
        return tail_fuel_state

    tail_state = _tail_states_by_tail(operation).get(tail_number)
    tail_fuel_state = NeoScorpionTailFuelState(
        sort_date_operation_id=operation.id,
        sort_date_tail_state_id=tail_state.id if tail_state else None,
        tail_number=tail_number,
    )
    db.session.add(tail_fuel_state)
    db.session.flush()
    return tail_fuel_state


def ensure_fuel_assignment(operation, mission):
    assignment = NeoScorpionFuelAssignment.query.filter_by(
        sort_date_mission_id=mission.id,
    ).first()
    if assignment:
        return assignment
    assignment = NeoScorpionFuelAssignment(
        sort_date_operation_id=operation.id,
        sort_date_mission_id=mission.id,
        calculation_status="not_configured",
        review_status="pending",
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def _fuel_rows(operation, missions):
    tail_states = _tail_states_by_tail(operation)
    tail_fuel_states = _tail_fuel_states_by_tail(operation)
    parking = _parking_by_tail(operation)
    assignments = _assignments_by_mission(operation)
    trucks = {truck.id: truck for truck in _fuel_trucks(operation.gateway or _gateway_stub(operation))}

    rows = []
    for mission in missions:
        tail_number = _normalize_tail(mission.assigned_tail_number)
        tail_state = tail_states.get(tail_number)
        tail_fuel_state = tail_fuel_states.get(tail_number)
        assignment = assignments.get(mission.id)
        truck = assignment.assigned_truck if assignment and assignment.assigned_truck else (
            trucks.get(assignment.assigned_truck_id) if assignment else None
        )
        aircraft_type = _aircraft_type_for_mission(mission, tail_state)
        rows.append(
            {
                "mission": mission,
                "assignment": assignment,
                "tail_number": tail_number or "-",
                "aircraft_type": aircraft_type,
                "destination": mission.destination or "-",
                "departure_time": format_local_hhmm(
                    mission.eta_datetime_utc or mission.planned_datetime_utc,
                    mission.timezone,
                ),
                "parking_position": parking.get(tail_number, "-") if tail_number else "-",
                "required_fuel_display": format_display_thousands(mission.planned_fuel_load),
                "inbound_fuel_display": format_display_thousands(
                    tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None
                ),
                "fob_display": format_display_thousands(
                    tail_fuel_state.fob_lbs if tail_fuel_state else None
                ),
                "center_fuel_display": format_display_thousands(
                    tail_fuel_state.center_fuel_lbs if tail_fuel_state else None
                ),
                "actual_fuel_display": format_display_thousands(
                    tail_fuel_state.actual_fuel_lbs if tail_fuel_state else None
                ),
                "apu_lbs": tail_fuel_state.apu_lbs if tail_fuel_state else None,
                "transfer_fuel_gallons": (
                    assignment.transfer_fuel_gallons if assignment else None
                ),
                "estimated_fuel_display": "-",
                "estimated_fuel_status": CALCULATION_NOT_CONFIGURED_MESSAGE,
                "assigned_fueler": assignment.assigned_fueler if assignment else None,
                "assigned_truck": truck,
                "truck_remaining_fuel": (
                    truck.remaining_fuel_gallons if truck and truck.remaining_fuel_gallons is not None else None
                ),
                "review_status": (
                    assignment.review_status if assignment else (mission.fuel_status or "pending")
                ),
                "load_planning_note": (
                    assignment.load_planning_note if assignment and assignment.load_planning_note else ""
                ),
                "load_planning_placeholder": "Copy-ready load planning not configured yet.",
                "tail_fuel_state": tail_fuel_state,
            }
        )
    return rows


def format_display_thousands(value):
    converted = lbs_to_display_thousands(value)
    if converted is None:
        return ""
    return f"{converted:.1f}"


def _departure_missions(operation):
    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .filter(SortDateMission.departure_status != "cancelled")
        .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
        .all()
    )


def _departure_mission_for_operation(operation, mission_id):
    if not mission_id:
        return None
    return SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).first()


def _tail_states_by_tail(operation):
    return {
        _normalize_tail(state.tail_number): state
        for state in SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
    }


def _tail_fuel_states_by_tail(operation):
    return {
        _normalize_tail(state.tail_number): state
        for state in NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
    }


def _parking_by_tail(operation):
    positions = {}
    assignments = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
    ).all()
    for assignment in assignments:
        tail_number = _normalize_tail(assignment.tail_number)
        lane_suffix = f" / S{assignment.lane_number}" if assignment.lane_number == 2 else ""
        positions[tail_number] = (
            f"{assignment.ramp_code or ''}{assignment.position_code or ''}{lane_suffix}".strip()
            or "-"
        )
    return positions


def _assignments_by_mission(operation):
    return {
        assignment.sort_date_mission_id: assignment
        for assignment in NeoScorpionFuelAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
    }


def _fuel_trucks(gateway):
    return (
        NeoScorpionFuelTruck.query.filter_by(gateway_id=gateway.id)
        .order_by(
            NeoScorpionFuelTruck.is_active.desc(),
            NeoScorpionFuelTruck.truck_number,
        )
        .all()
    )


def _fueler_users():
    return User.query.filter_by(is_active=True).order_by(User.last_name, User.first_name, User.username).all()


def _aircraft_type_for_mission(mission, tail_state):
    if tail_state and tail_state.aircraft_type:
        return tail_state.aircraft_type
    if mission.api_aircraft_model:
        return mission.api_aircraft_model
    if mission.assigned_tail_number:
        return resolve_parking_aircraft_type_from_tail(mission.assigned_tail_number)
    return "UNKNOWN"


def _normalize_tail(value):
    return (value or "").strip().upper()


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Fuel value must be numeric.") from exc


def _positive_decimal(value, message):
    amount = _decimal_or_none(value)
    if amount is None or amount <= 0:
        raise ValueError(message)
    return amount


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        parsed = int(Decimal(str(value).strip()).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Numeric value must be a whole number.") from exc
    if parsed < 0:
        raise ValueError("Numeric value cannot be negative.")
    return parsed


def _clean_choice(value, choices, default):
    normalized = (value or "").strip().lower()
    return normalized if normalized in choices else default


def _gateway_stub(operation):
    return type("GatewayStub", (), {"id": operation.gateway_id})()
