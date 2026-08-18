from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTankState,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionTailFuelState,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.services.parking_aircraft import resolve_parking_aircraft_type_from_tail
from app.services.neoscorpion_assets import (
    eligible_nightly_fueler_users,
    lock_nightly_asset_scope_for_mutation,
    nightly_asset_context,
    record_nightly_operational_change,
)
from app.services.time_display import format_local_hhmm


DEFAULT_FUEL_DENSITY_LBS_PER_GALLON = 6.7
DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR = Decimal("0.30")
CALCULATION_NOT_CONFIGURED_MESSAGE = "Fuel calculation not configured for this aircraft type yet."

NEOSCORPION_TANK_LAYOUTS = {
    "B757": (
        ("left", "LEFT"),
        ("ctr", "CTR"),
        ("right", "RIGHT"),
    ),
    "A300": (
        ("l_out", "L-OUT"),
        ("l_in", "L-IN"),
        ("ctr", "CTR"),
        ("r_in", "R-IN"),
        ("r_out", "R-OUT"),
        ("tt", "TT"),
    ),
    "B767ER": (
        ("left", "LEFT"),
        ("ctr", "CTR"),
        ("right", "RIGHT"),
    ),
    "B747-400": (
        ("main_l_out", "MAIN-L-OUT"),
        ("main_l_in", "MAIN-L-IN"),
        ("main_r_in", "MAIN R IN"),
        ("main_r_out", "MAIN R OUT"),
        ("reserve_2_l", "RESERVE 2 L"),
        ("reserve_3_r", "RESERVE 3 R"),
        ("center_wing", "CENTER WING"),
    ),
    "B747-8": (
        ("main_l_out", "MAIN-L-OUT"),
        ("main_l_in", "MAIN-L-IN"),
        ("main_r_in", "MAIN R IN"),
        ("main_r_out", "MAIN R OUT"),
        ("reserve_1_l", "RESERVE 1 L"),
        ("reserve_4_r", "RESERVE 4 R"),
        ("center_wing", "CENTER WING"),
    ),
}

NEOSCORPION_APU_AIRCRAFT_TYPES = (
    "A300",
    "B757",
    "B767ER",
    "B747-400",
    "B747-8",
)

_NEOSCORPION_DETAILED_TYPE_BY_FIRST_DIGIT = {
    "1": "A300",
    "4": "B757",
    "3": "B767ER",
    "9": "B767ER",
    "5": "B747-400",
    "6": "B747-8",
}


def detailed_aircraft_type_for_tail(tail_number):
    normalized_tail = _normalize_tail(tail_number)
    first_digit = next(
        (character for character in normalized_tail if character.isdigit()),
        "",
    )
    return _NEOSCORPION_DETAILED_TYPE_BY_FIRST_DIGIT.get(
        first_digit,
        "UNCONFIGURED",
    )


def tank_layout_for_tail(tail_number):
    return NEOSCORPION_TANK_LAYOUTS.get(
        detailed_aircraft_type_for_tail(tail_number),
        (),
    )


def calculate_apu_allowance_lbs(
    planned_departure_utc,
    window_minutes,
    confirmed_at_utc,
    rate_thousand_lbs_per_hour,
):
    if planned_departure_utc is None:
        raise ValueError("Planned departure is required to confirm APU Running as Yes.")
    rate = Decimal(str(rate_thousand_lbs_per_hour))
    if not rate.is_finite() or rate < 0:
        raise ValueError("APU rate cannot be negative.")
    effective_departure = planned_departure_utc + timedelta(
        minutes=int(window_minutes or 0)
    )
    remaining_seconds = max(
        Decimal("0"),
        Decimal(str((effective_departure - confirmed_at_utc).total_seconds())),
    )
    raw_thousand_lbs = remaining_seconds * rate / Decimal("3600")
    rounded_thousand_lbs = raw_thousand_lbs.quantize(
        Decimal("0.1"),
        rounding=ROUND_CEILING,
    )
    return int(rounded_thousand_lbs * Decimal("1000"))


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
    NeoScorpionMenuItem(
        "Fueler",
        "neoscorpion.fueler",
        "neoscorpion.fuel_assignments.view",
        "fueler",
    ),
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


def fuel_assignments_live_revision(gateway):
    """Return the current sort/revision fingerprint without building screen state."""
    row = (
        db.session.query(
            SortDateOperation.id.label("operation_id"),
            db.func.coalesce(NeoScorpionSortAssetState.revision, 0).label("revision"),
        )
        .outerjoin(
            NeoScorpionSortAssetState,
            NeoScorpionSortAssetState.sort_date_operation_id == SortDateOperation.id,
        )
        .filter(
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
    if row is None:
        return {
            "current_operation": False,
            "operation_id": None,
            "revision": 0,
        }
    return {
        "current_operation": True,
        "operation_id": row.operation_id,
        "revision": int(row.revision or 0),
    }


def _fuel_assignments_revision_for_operation(operation):
    if operation is None:
        return 0
    revision = (
        db.session.query(NeoScorpionSortAssetState.revision)
        .filter(NeoScorpionSortAssetState.sort_date_operation_id == operation.id)
        .scalar()
    )
    return int(revision or 0)


def fuel_dispatch_context(gateway, *, include_asset_choices=False):
    operation = current_sort_operation(gateway)
    fuelers = _fueler_users()
    trucks = _fuel_trucks(gateway)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    asset_context = nightly_asset_context(
        gateway,
        operation,
        active_users=fuelers,
        fuel_trucks=trucks,
        include_choices=include_asset_choices,
    )
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "fuelers": fuelers,
            "trucks": trucks,
            "settings": settings,
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
            **asset_context,
        }

    missions = _departure_missions(operation)
    assignments_by_mission = _assignments_by_mission(operation)
    fuel_work_states = _fuel_work_states_by_assignment_tail(
        assignments_by_mission.values()
    )
    return {
        "operation": operation,
        "rows": _fuel_rows(
            operation,
            missions,
            fuel_trucks=trucks,
            assignments_by_mission=assignments_by_mission,
            fuel_work_states_by_assignment_tail=fuel_work_states,
        ),
        "fuelers": fuelers,
        "trucks": trucks,
        "settings": settings,
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        **asset_context,
    }


def fueler_context(gateway, user):
    operation = current_sort_operation(gateway)
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "fuel_assignments_revision": 0,
            "settings": NeoScorpionSettings.query.filter_by(
                gateway_id=gateway.id
            ).first(),
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        }

    assignments = (
        NeoScorpionFuelAssignment.query.join(SortDateMission)
        .options(
            joinedload(NeoScorpionFuelAssignment.sort_date_mission),
            joinedload(NeoScorpionFuelAssignment.assigned_fueler),
            joinedload(NeoScorpionFuelAssignment.assigned_truck),
        )
        .filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelAssignment.assigned_fueler_user_id == user.id,
            NeoScorpionFuelAssignment.review_status != "complete",
            SortDateMission.mission_type == "departure",
            db.or_(
                SortDateMission.fuel_status.is_(None),
                SortDateMission.fuel_status != "complete",
            ),
        )
        .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
        .all()
    )
    missions = [assignment.sort_date_mission for assignment in assignments if assignment.sort_date_mission]
    assignments_by_mission = {
        assignment.sort_date_mission_id: assignment for assignment in assignments
    }
    fuel_work_states = _fuel_work_states_by_assignment_tail(assignments)
    return {
        "operation": operation,
        "rows": _fuel_rows(
            operation,
            missions,
            estimated_fuel_status=CALCULATION_NOT_CONFIGURED_MESSAGE,
            assignments_by_mission=assignments_by_mission,
            fuel_work_states_by_assignment_tail=fuel_work_states,
        ),
        "fuel_assignments_revision": _fuel_assignments_revision_for_operation(operation),
        "settings": NeoScorpionSettings.query.filter_by(
            gateway_id=gateway.id
        ).first(),
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
    }


def truck_manager_context(gateway):
    return {"trucks": _fuel_trucks(gateway)}


def settings_context(gateway):
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    if settings is None:
        settings = {
            "fuel_density_lbs_per_gallon": DEFAULT_FUEL_DENSITY_LBS_PER_GALLON,
            "fob_difference_threshold_lbs": None,
            "tf_vs_estimated_threshold_lbs": None,
        }
    overrides = {
        row.aircraft_type: row
        for row in NeoScorpionAircraftFuelSetting.query.filter(
            NeoScorpionAircraftFuelSetting.gateway_id == gateway.id,
            NeoScorpionAircraftFuelSetting.aircraft_type.in_(
                NEOSCORPION_APU_AIRCRAFT_TYPES
            ),
        ).all()
    }
    return {
        "settings": settings,
        "apu_rate_settings": [
            {
                "aircraft_type": aircraft_type,
                "field_name": _apu_rate_field_name(aircraft_type),
                "rate": (
                    overrides[aircraft_type].apu_rate_thousand_lbs_per_hour
                    if aircraft_type in overrides
                    else DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
                ),
                "is_override": aircraft_type in overrides,
            }
            for aircraft_type in NEOSCORPION_APU_AIRCRAFT_TYPES
        ],
    }


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


@dataclass(frozen=True)
class DispatchSaveResult:
    assignment: NeoScorpionFuelAssignment
    changed: bool
    assignment_changed: bool
    revision: int


@dataclass(frozen=True)
class FuelerSaveResult:
    changed: bool
    revision: int
    tail_fuel_state: NeoScorpionTailFuelState | None
    fuel_work_state: NeoScorpionFuelWorkState | None


@dataclass(frozen=True)
class FuelerOffResult:
    changed: bool
    revision: int
    fuel_work_state: NeoScorpionFuelWorkState


@dataclass(frozen=True)
class FuelOnBoardResult:
    changed: bool
    revision: int
    assignment: NeoScorpionFuelAssignment
    mission: SortDateMission


@dataclass(frozen=True)
class AircraftFuelSettingsSaveResult:
    changed: bool


def save_dispatch_row(gateway, form):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion dispatch.")

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)

    mission_id = _int_or_none(form.get("mission_id"))
    mission = _departure_mission_for_operation(operation, mission_id, for_update=True)
    if not mission:
        raise ValueError("Departure mission was not found for the current sort operation.")

    assignment = (
        NeoScorpionFuelAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
        )
        .with_for_update()
        .first()
    )
    assignment_created = assignment is None
    current_fueler_id = assignment.assigned_fueler_user_id if assignment else None
    current_truck_id = assignment.assigned_truck_id if assignment else None
    if (
        "expected_assigned_fueler_user_id" not in form
        or "expected_assigned_truck_id" not in form
    ):
        raise ValueError("Fuel assignment changed. Reload Fuel Dispatch and try again.")
    expected_fueler_id = _int_or_none(
        form.get("expected_assigned_fueler_user_id")
    )
    expected_truck_id = _int_or_none(form.get("expected_assigned_truck_id"))
    if (
        expected_fueler_id != current_fueler_id
        or expected_truck_id != current_truck_id
    ):
        raise ValueError("Fuel assignment changed. Reload Fuel Dispatch and try again.")
    requested_fueler_id = _int_or_none(form.get("assigned_fueler_user_id"))
    requested_truck_id = _int_or_none(form.get("assigned_truck_id"))

    if requested_fueler_id != current_fueler_id and requested_fueler_id is not None:
        _validate_nightly_fueler_assignment(
            gateway,
            operation,
            requested_fueler_id,
        )
    if requested_truck_id != current_truck_id and requested_truck_id is not None:
        _validate_nightly_truck_assignment(
            gateway,
            operation,
            requested_truck_id,
        )

    changed = False
    planned_fuel_load = display_thousands_to_lbs(form.get("required_fuel"))
    if mission.planned_fuel_load != planned_fuel_load:
        mission.planned_fuel_load = planned_fuel_load
        mission.planned_fuel_updated_at = datetime.utcnow()
        changed = True

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if tail_number:
        existing_tail_fuel_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        ).first()
        tail_fuel_state = existing_tail_fuel_state or ensure_tail_fuel_state(
            operation,
            tail_number,
        )
        if existing_tail_fuel_state is None:
            changed = True
        inbound_fuel_lbs = display_thousands_to_lbs(form.get("inbound_fuel"))
        apu_lbs = _int_or_none(form.get("apu_lbs"))
        if tail_fuel_state.inbound_fuel_lbs != inbound_fuel_lbs:
            tail_fuel_state.inbound_fuel_lbs = inbound_fuel_lbs
            changed = True
        if tail_fuel_state.apu_lbs != apu_lbs:
            tail_fuel_state.apu_lbs = apu_lbs
            changed = True

    if assignment is None:
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            calculation_status="not_configured",
            review_status="pending",
        )
        db.session.add(assignment)
        changed = True

    review_status = _clean_choice(
        form.get("review_status"),
        {"pending", "assigned", "review", "complete"},
        "pending",
    )
    load_planning_note = (form.get("load_planning_note") or "").strip()
    assignment_changed = assignment_created
    for field_name, value in (
        ("assigned_fueler_user_id", requested_fueler_id),
        ("assigned_truck_id", requested_truck_id),
        ("review_status", review_status),
        ("load_planning_note", load_planning_note),
    ):
        if getattr(assignment, field_name) != value:
            setattr(assignment, field_name, value)
            changed = True
            assignment_changed = True

    revision = int(asset_state.revision if asset_state else 0)
    if assignment_changed:
        asset_state = record_nightly_operational_change(asset_state, operation.id)
        revision = int(asset_state.revision)

    db.session.flush()
    return DispatchSaveResult(
        assignment=assignment,
        changed=changed or assignment_created,
        assignment_changed=assignment_changed,
        revision=revision,
    )


def save_fueler_entry(gateway, user, form, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion fueler entry.")
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment_id = _int_or_none(form.get("assignment_id"))
    assignment_row = (
        db.session.query(NeoScorpionFuelAssignment, SortDateMission)
        .join(
            SortDateMission,
            SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id,
        )
        .filter(
            NeoScorpionFuelAssignment.id == assignment_id,
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelAssignment.assigned_fueler_user_id == user.id,
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
        )
        .with_for_update()
        .first()
    )
    if not assignment_row:
        raise ValueError("Fuel assignment was not found for this fueler.")

    assignment, mission = assignment_row
    tail_number = _normalize_tail(mission.assigned_tail_number if mission else "")
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")

    aircraft_type = detailed_aircraft_type_for_tail(tail_number)
    tank_layout = tank_layout_for_tail(tail_number)
    expected_tank_codes = {code for code, _label in tank_layout}
    submitted_tank_codes = _submitted_tank_codes(form)
    invalid_tank_codes = submitted_tank_codes - expected_tank_codes
    if invalid_tank_codes:
        if not tank_layout:
            raise ValueError("Tank entry is not configured for this aircraft.")
        raise ValueError("The submitted tank layout does not match this aircraft.")

    fuel_work_state = (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    tank_states = []
    if fuel_work_state is not None:
        tank_states = (
            NeoScorpionFuelTankState.query.filter_by(
                fuel_work_state_id=fuel_work_state.id,
            )
            .with_for_update()
            .all()
        )
    tank_states_by_code = {state.tank_code: state for state in tank_states}

    final_tank_values = {}
    tank_changed = False
    submitted_nonnull_remaining = False
    for tank_code, _label in tank_layout:
        tank_state = tank_states_by_code.get(tank_code)
        remaining_lbs = tank_state.remaining_lbs if tank_state else None
        actual_lbs = tank_state.actual_lbs if tank_state else None
        remaining_field = f"remaining_{tank_code}"
        actual_field = f"actual_{tank_code}"
        if remaining_field in form:
            remaining_lbs = display_thousands_to_lbs(form.get(remaining_field))
            submitted_nonnull_remaining = (
                submitted_nonnull_remaining or remaining_lbs is not None
            )
        if actual_field in form:
            actual_lbs = display_thousands_to_lbs(form.get(actual_field))
        if actual_lbs is not None and remaining_lbs is None:
            raise ValueError(f"Enter Remaining before Actual for {_label}.")
        final_tank_values[tank_code] = (remaining_lbs, actual_lbs)
        if tank_state is None:
            tank_changed = tank_changed or remaining_lbs is not None or actual_lbs is not None
        elif (
            tank_state.remaining_lbs != remaining_lbs
            or tank_state.actual_lbs != actual_lbs
        ):
            tank_changed = True

    tail_fuel_state = (
        NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    current_apu_running = fuel_work_state.apu_running if fuel_work_state else None
    target_apu_running = current_apu_running
    apu_changed = False
    if "apu_running" in form:
        submitted_apu_running = (form.get("apu_running") or "").strip()
        apu_running_choices = {
            "not_confirmed": None,
            "no": False,
            "yes": True,
        }
        if submitted_apu_running not in apu_running_choices:
            raise ValueError("Select a valid APU Running status.")
        target_apu_running = apu_running_choices[submitted_apu_running]
        apu_changed = target_apu_running is not current_apu_running

    target_apu_confirmed_at_utc = (
        fuel_work_state.apu_confirmed_at_utc if fuel_work_state else None
    )
    target_apu_allowance_lbs = (
        fuel_work_state.apu_allowance_lbs if fuel_work_state else None
    )
    target_apu_rate = (
        fuel_work_state.applied_apu_rate_thousand_lbs_per_hour
        if fuel_work_state
        else None
    )
    if apu_changed:
        if target_apu_running is None:
            target_apu_confirmed_at_utc = None
            target_apu_allowance_lbs = None
            target_apu_rate = None
        elif target_apu_running is False:
            target_apu_confirmed_at_utc = now_utc
            target_apu_allowance_lbs = 0
            target_apu_rate = None
        else:
            if aircraft_type not in NEOSCORPION_APU_AIRCRAFT_TYPES:
                raise ValueError("APU allowance is not configured for this aircraft.")
            target_apu_rate = _effective_apu_rate(gateway.id, aircraft_type)
            target_apu_confirmed_at_utc = now_utc
            target_apu_allowance_lbs = calculate_apu_allowance_lbs(
                mission.planned_datetime_utc,
                operation.window_minutes,
                now_utc,
                target_apu_rate,
            )

    target_apu_lbs = target_apu_allowance_lbs
    target_notes = (form.get("notes") or "").strip()
    target_transfer_gallons = _int_or_none(form.get("transfer_fuel_gallons"))

    remaining_values = [values[0] for values in final_tank_values.values()]
    actual_values = [values[1] for values in final_tank_values.values()]
    target_fob_lbs = (
        sum(remaining_values)
        if tank_layout and all(value is not None for value in remaining_values)
        else None
    )
    target_actual_lbs = (
        sum(actual_values)
        if tank_layout and all(value is not None for value in actual_values)
        else None
    )
    target_center_lbs = (
        final_tank_values["ctr"][0]
        if aircraft_type == "A300" and "ctr" in final_tank_values
        else None
    )

    current_apu_lbs = tail_fuel_state.apu_lbs if tail_fuel_state else None
    current_notes = tail_fuel_state.notes if tail_fuel_state else ""
    current_fob_lbs = tail_fuel_state.fob_lbs if tail_fuel_state else None
    current_actual_lbs = tail_fuel_state.actual_fuel_lbs if tail_fuel_state else None
    current_center_lbs = tail_fuel_state.center_fuel_lbs if tail_fuel_state else None
    tail_changed = any(
        (
            current_apu_lbs != target_apu_lbs,
            current_notes != target_notes,
            current_fob_lbs != target_fob_lbs,
            current_actual_lbs != target_actual_lbs,
            current_center_lbs != target_center_lbs,
        )
    )
    transfer_changed = assignment.transfer_fuel_gallons != target_transfer_gallons
    changed = tank_changed or apu_changed or tail_changed or transfer_changed
    revision = int(asset_state.revision if asset_state else 0)
    if not changed:
        return FuelerSaveResult(
            changed=False,
            revision=revision,
            tail_fuel_state=tail_fuel_state,
            fuel_work_state=fuel_work_state,
        )

    if tank_changed or apu_changed:
        if fuel_work_state is None:
            fuel_work_state = NeoScorpionFuelWorkState(
                fuel_assignment_id=assignment.id,
                tail_number=tail_number,
            )
            db.session.add(fuel_work_state)
        for tank_code, (remaining_lbs, actual_lbs) in final_tank_values.items():
            tank_state = tank_states_by_code.get(tank_code)
            if tank_state is None:
                if remaining_lbs is None and actual_lbs is None:
                    continue
                tank_state = NeoScorpionFuelTankState(tank_code=tank_code)
                fuel_work_state.tank_states.append(tank_state)
                tank_states_by_code[tank_code] = tank_state
            tank_state.remaining_lbs = remaining_lbs
            tank_state.actual_lbs = actual_lbs
        if apu_changed:
            fuel_work_state.apu_running = target_apu_running
            fuel_work_state.apu_confirmed_at_utc = target_apu_confirmed_at_utc
            fuel_work_state.apu_allowance_lbs = target_apu_allowance_lbs
            fuel_work_state.applied_apu_rate_thousand_lbs_per_hour = target_apu_rate
        if (
            fuel_work_state.on_at_utc is None
            and submitted_nonnull_remaining
        ):
            fuel_work_state.on_at_utc = now_utc

    if tail_changed or tank_changed:
        if tail_fuel_state is None:
            sort_tail_state = SortDateTailState.query.filter_by(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number=tail_number,
            ).first()
            tail_fuel_state = NeoScorpionTailFuelState(
                sort_date_operation_id=operation.id,
                sort_date_tail_state_id=(
                    sort_tail_state.id if sort_tail_state else None
                ),
                tail_number=tail_number,
            )
            db.session.add(tail_fuel_state)
        tail_fuel_state.fob_lbs = target_fob_lbs
        tail_fuel_state.actual_fuel_lbs = target_actual_lbs
        tail_fuel_state.center_fuel_lbs = target_center_lbs
        tail_fuel_state.apu_lbs = target_apu_lbs
        tail_fuel_state.notes = target_notes

    assignment.transfer_fuel_gallons = target_transfer_gallons
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelerSaveResult(
        changed=True,
        revision=int(asset_state.revision),
        tail_fuel_state=tail_fuel_state,
        fuel_work_state=fuel_work_state,
    )


def mark_fueler_off(gateway, user, assignment_id, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion OFF.")
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment_row = (
        db.session.query(NeoScorpionFuelAssignment, SortDateMission)
        .join(
            SortDateMission,
            SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id,
        )
        .filter(
            NeoScorpionFuelAssignment.id == _int_or_none(assignment_id),
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelAssignment.assigned_fueler_user_id == user.id,
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
        )
        .with_for_update()
        .first()
    )
    if not assignment_row:
        raise ValueError("Fuel assignment was not found for this fueler.")

    assignment, mission = assignment_row
    tail_number = _normalize_tail(mission.assigned_tail_number if mission else "")
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")

    fuel_work_state = (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    if fuel_work_state is None:
        raise ValueError("Complete Actual fuel and confirm APU before OFF.")
    if fuel_work_state.off_at_utc is not None:
        return FuelerOffResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            fuel_work_state=fuel_work_state,
        )

    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    (
        _remaining_complete,
        _remaining_total_lbs,
        _actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout_for_tail(tail_number),
        tank_states_by_code,
        fuel_work_state.apu_running,
        fuel_work_state.apu_allowance_lbs,
    )
    if neo_fuel_lbs is None:
        raise ValueError("Complete Actual fuel and confirm APU before OFF.")

    fuel_work_state.off_at_utc = now_utc
    fuel_work_state.off_by_user_id = user.id
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelerOffResult(
        changed=True,
        revision=int(asset_state.revision),
        fuel_work_state=fuel_work_state,
    )


def complete_fuel_on_board(gateway, user, assignment_id, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError(
            "No current sort operation is available for Fuel On Board."
        )
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment_row = (
        db.session.query(NeoScorpionFuelAssignment, SortDateMission)
        .join(
            SortDateMission,
            SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id,
        )
        .filter(
            NeoScorpionFuelAssignment.id == _int_or_none(assignment_id),
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
        )
        .with_for_update()
        .first()
    )
    if not assignment_row:
        raise ValueError(
            "Fuel assignment was not found for the current sort operation."
        )

    assignment, mission = assignment_row
    if assignment.fuel_on_board_at_utc is not None:
        return FuelOnBoardResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            assignment=assignment,
            mission=mission,
        )
    if assignment.assigned_fueler_user_id is None:
        raise ValueError("Assign a fueler before Fuel On Board.")
    if assignment.assigned_truck_id is not None:
        raise ValueError("Clear the unused truck before Fuel On Board.")
    if assignment.transfer_fuel_gallons not in (None, 0):
        raise ValueError("T/F must be blank or 0 for Fuel On Board.")

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    fuel_work_state = (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    if fuel_work_state is None:
        raise ValueError(
            "Complete Actual fuel and confirm APU before Fuel On Board."
        )
    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    (
        _remaining_complete,
        _remaining_total_lbs,
        _actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout_for_tail(tail_number),
        tank_states_by_code,
        fuel_work_state.apu_running,
        fuel_work_state.apu_allowance_lbs,
    )
    if neo_fuel_lbs is None:
        raise ValueError(
            "Complete Actual fuel and confirm APU before Fuel On Board."
        )

    assignment.fuel_on_board_at_utc = now_utc
    assignment.fuel_on_board_by_user_id = user.id
    assignment.review_status = "complete"
    assignment.transfer_fuel_gallons = 0
    mission.fuel_status = "complete"
    mission.fuel_completed_at_utc = now_utc
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelOnBoardResult(
        changed=True,
        revision=int(asset_state.revision),
        assignment=assignment,
        mission=mission,
    )


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


def save_aircraft_fuel_settings(gateway, user, form):
    existing = {
        row.aircraft_type: row
        for row in NeoScorpionAircraftFuelSetting.query.filter(
            NeoScorpionAircraftFuelSetting.gateway_id == gateway.id,
            NeoScorpionAircraftFuelSetting.aircraft_type.in_(
                NEOSCORPION_APU_AIRCRAFT_TYPES
            ),
        )
        .with_for_update()
        .all()
    }
    changed = False
    for aircraft_type in NEOSCORPION_APU_AIRCRAFT_TYPES:
        field_name = _apu_rate_field_name(aircraft_type)
        if field_name not in form:
            continue
        submitted = (form.get(field_name) or "").strip()
        target_rate = (
            DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
            if not submitted
            else _parse_apu_rate(submitted)
        )
        setting = existing.get(aircraft_type)
        if target_rate == DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR:
            if setting is not None:
                db.session.delete(setting)
                changed = True
            continue
        row_changed = False
        if setting is None:
            setting = NeoScorpionAircraftFuelSetting(
                gateway_id=gateway.id,
                aircraft_type=aircraft_type,
                apu_rate_thousand_lbs_per_hour=target_rate,
            )
            db.session.add(setting)
            existing[aircraft_type] = setting
            row_changed = True
        elif Decimal(setting.apu_rate_thousand_lbs_per_hour) != target_rate:
            setting.apu_rate_thousand_lbs_per_hour = target_rate
            row_changed = True
        if row_changed:
            setting.updated_by_user_id = user.id
            changed = True

    if changed:
        db.session.flush()
    return AircraftFuelSettingsSaveResult(changed=changed)


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


def _fuel_work_calculation(
    tank_layout,
    tank_states_by_code,
    apu_running,
    apu_allowance_lbs,
):
    remaining_values = [
        (
            tank_states_by_code[tank_code].remaining_lbs
            if tank_code in tank_states_by_code
            else None
        )
        for tank_code, _tank_label in tank_layout
    ]
    actual_values = [
        (
            tank_states_by_code[tank_code].actual_lbs
            if tank_code in tank_states_by_code
            else None
        )
        for tank_code, _tank_label in tank_layout
    ]
    remaining_complete = bool(tank_layout) and all(
        value is not None for value in remaining_values
    )
    remaining_total_lbs = sum(remaining_values) if remaining_complete else None
    actual_complete = bool(tank_layout) and all(
        value is not None for value in actual_values
    )
    actual_total_lbs = sum(actual_values) if actual_complete else None
    neo_fuel_lbs = (
        actual_total_lbs - apu_allowance_lbs
        if actual_total_lbs is not None
        and apu_running is not None
        and apu_allowance_lbs is not None
        else None
    )
    return (
        remaining_complete,
        remaining_total_lbs,
        actual_complete,
        actual_total_lbs,
        neo_fuel_lbs,
    )


def classify_fuel_movement(assignment, fuel_work_state, *, tank_states=None):
    if assignment is not None and assignment.fuel_on_board_at_utc is not None:
        return "not_moved"
    if (
        assignment is not None
        and assignment.transfer_fuel_gallons is not None
        and assignment.transfer_fuel_gallons > 0
    ):
        return "moved"
    if fuel_work_state is None:
        return "unknown"

    states = fuel_work_state.tank_states if tank_states is None else tank_states
    tank_states_by_code = {state.tank_code: state for state in states}
    (
        remaining_complete,
        remaining_total_lbs,
        actual_complete,
        actual_total_lbs,
        _neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout_for_tail(fuel_work_state.tail_number),
        tank_states_by_code,
        fuel_work_state.apu_running,
        fuel_work_state.apu_allowance_lbs,
    )
    if (
        not remaining_complete
        or not actual_complete
        or fuel_work_state.apu_running is None
        or fuel_work_state.apu_allowance_lbs is None
    ):
        return "unknown"

    adjusted_actual_lbs = (
        actual_total_lbs + fuel_work_state.apu_allowance_lbs
    )
    return "moved" if adjusted_actual_lbs > remaining_total_lbs else "not_moved"


def _fuel_rows(
    operation,
    missions,
    estimated_fuel_status="INOP",
    *,
    fuel_trucks=None,
    assignments_by_mission=None,
    fuel_work_states_by_assignment_tail=None,
):
    tail_states = _tail_states_by_tail(operation)
    tail_fuel_states = _tail_fuel_states_by_tail(operation)
    parking = _parking_by_tail(operation)
    assignments = (
        _assignments_by_mission(operation)
        if assignments_by_mission is None
        else assignments_by_mission
    )
    arrivals = _arrivals_by_tail(operation)
    if fuel_trucks is None:
        fuel_trucks = _fuel_trucks(operation.gateway or _gateway_stub(operation))
    trucks = {truck.id: truck for truck in fuel_trucks}
    fuel_work_states = fuel_work_states_by_assignment_tail or {}

    rows = []
    for mission in missions:
        tail_number = _normalize_tail(mission.assigned_tail_number)
        tail_state = tail_states.get(tail_number)
        tail_fuel_state = tail_fuel_states.get(tail_number)
        assignment = assignments.get(mission.id)
        arrival = arrivals.get(tail_number)
        truck = trucks.get(assignment.assigned_truck_id) if assignment else None
        if truck is None and assignment is not None:
            truck = assignment.assigned_truck
        aircraft_type = _aircraft_type_for_mission(mission, tail_state)
        detailed_aircraft_type = detailed_aircraft_type_for_tail(tail_number)
        tank_layout = tank_layout_for_tail(tail_number)
        fuel_work_state = fuel_work_states.get(
            (assignment.id, tail_number)
            if assignment is not None and tail_number
            else None
        )
        tank_states_by_code = {
            state.tank_code: state
            for state in (fuel_work_state.tank_states if fuel_work_state else ())
        }
        tank_rows = []
        for tank_code, tank_label in tank_layout:
            tank_state = tank_states_by_code.get(tank_code)
            tank_rows.append(
                {
                    "code": tank_code,
                    "label": tank_label,
                    "remaining_lbs": tank_state.remaining_lbs if tank_state else None,
                    "actual_lbs": tank_state.actual_lbs if tank_state else None,
                    "remaining_display": format_display_thousands(
                        tank_state.remaining_lbs if tank_state else None
                    ),
                    "actual_display": format_display_thousands(
                        tank_state.actual_lbs if tank_state else None
                    ),
                }
            )
        apu_running = fuel_work_state.apu_running if fuel_work_state else None
        apu_allowance_lbs = (
            fuel_work_state.apu_allowance_lbs
            if fuel_work_state and apu_running is not None
            else None
        )
        (
            remaining_complete,
            remaining_total_lbs,
            actual_complete,
            actual_total_lbs,
            neo_fuel_lbs,
        ) = _fuel_work_calculation(
            tank_layout,
            tank_states_by_code,
            apu_running,
            apu_allowance_lbs,
        )
        fueling_target_lbs = (
            mission.planned_fuel_load + apu_allowance_lbs
            if mission.planned_fuel_load is not None
            and apu_allowance_lbs is not None
            else None
        )
        fuel_on_board_complete = bool(
            assignment and assignment.fuel_on_board_at_utc
        )
        fuel_on_board_ready = bool(
            assignment
            and not fuel_on_board_complete
            and assignment.assigned_fueler_user_id is not None
            and assignment.assigned_truck_id is None
            and assignment.transfer_fuel_gallons in (None, 0)
            and neo_fuel_lbs is not None
        )
        if fuel_on_board_complete:
            fuel_on_board_reason = "COMPLETE"
        elif not assignment or assignment.assigned_fueler_user_id is None:
            fuel_on_board_reason = "Assign fueler first."
        elif assignment.assigned_truck_id is not None:
            fuel_on_board_reason = "Clear unused truck first."
        elif assignment.transfer_fuel_gallons not in (None, 0):
            fuel_on_board_reason = "T/F must be blank or 0."
        elif neo_fuel_lbs is None:
            fuel_on_board_reason = "Actual/APU incomplete."
        else:
            fuel_on_board_reason = ""
        rows.append(
            {
                "mission": mission,
                "assignment": assignment,
                "arrival_mission": arrival,
                "tail_number": tail_number or "-",
                "aircraft_type": aircraft_type,
                "detailed_aircraft_type": detailed_aircraft_type,
                "fuel_work_state": fuel_work_state,
                "tank_rows": tank_rows,
                "on_time": (
                    format_local_hhmm(fuel_work_state.on_at_utc, mission.timezone)
                    if fuel_work_state and fuel_work_state.on_at_utc
                    else "-"
                ),
                "off_time": (
                    format_local_hhmm(fuel_work_state.off_at_utc, mission.timezone)
                    if fuel_work_state and fuel_work_state.off_at_utc
                    else "-"
                ),
                "off_ready": bool(
                    fuel_work_state
                    and fuel_work_state.off_at_utc is None
                    and neo_fuel_lbs is not None
                ),
                "is_off": bool(fuel_work_state and fuel_work_state.off_at_utc),
                "remaining_total_display": (
                    format_display_thousands(remaining_total_lbs)
                    if remaining_complete
                    else "INCOMPLETE"
                ),
                "actual_total_display": (
                    format_display_thousands(actual_total_lbs)
                    if actual_complete
                    else "INCOMPLETE"
                ),
                "required_display": (
                    format_display_thousands(mission.planned_fuel_load)
                    if mission.planned_fuel_load is not None
                    else "INCOMPLETE"
                ),
                "apu_running": apu_running,
                "apu_running_label": (
                    "YES"
                    if apu_running is True
                    else "NO"
                    if apu_running is False
                    else "NOT CONFIRMED"
                ),
                "apu_allowance_display": (
                    format_display_thousands(apu_allowance_lbs)
                    if apu_allowance_lbs is not None
                    else "INCOMPLETE"
                ),
                "fueling_target_display": (
                    format_display_thousands(fueling_target_lbs)
                    if fueling_target_lbs is not None
                    else "INCOMPLETE"
                ),
                "neo_fuel_display": (
                    format_display_thousands(neo_fuel_lbs)
                    if neo_fuel_lbs is not None
                    else "INCOMPLETE"
                ),
                "neo_fuel_available": neo_fuel_lbs is not None,
                "fuel_on_board_complete": fuel_on_board_complete,
                "fuel_on_board_ready": fuel_on_board_ready,
                "fuel_on_board_reason": fuel_on_board_reason,
                "destination": mission.destination or "-",
                "arrival_eta": _arrival_eta_display(arrival),
                "arrival_status": _arrival_status_display(arrival),
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
                "estimated_fuel_display": estimated_fuel_status,
                "estimated_fuel_status": estimated_fuel_status,
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


def _fuel_work_states_by_assignment_tail(assignments):
    assignment_ids = [assignment.id for assignment in assignments]
    if not assignment_ids:
        return {}
    work_states = (
        NeoScorpionFuelWorkState.query.filter(
            NeoScorpionFuelWorkState.fuel_assignment_id.in_(assignment_ids)
        )
        .options(joinedload(NeoScorpionFuelWorkState.tank_states))
        .all()
    )
    return {
        (state.fuel_assignment_id, _normalize_tail(state.tail_number)): state
        for state in work_states
    }


def format_display_thousands(value):
    converted = lbs_to_display_thousands(value)
    if converted is None:
        return ""
    return f"{converted:.1f}"


def _departure_missions(operation):
    return (
        SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
            db.or_(
                SortDateMission.departure_status.is_(None),
                SortDateMission.departure_status != "cancelled",
            ),
        )
        .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
        .all()
    )


def _arrivals_by_tail(operation):
    arrivals = {}
    missions = (
        SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "arrival",
            SortDateMission.assigned_tail_number.isnot(None),
            db.or_(
                SortDateMission.arrival_status.is_(None),
                SortDateMission.arrival_status != "cancelled",
            ),
        )
        .order_by(
            SortDateMission.eta_datetime_utc.is_(None),
            SortDateMission.eta_datetime_utc,
            SortDateMission.planned_datetime_utc,
            SortDateMission.flight_number,
        )
        .all()
    )
    for mission in missions:
        tail_number = _normalize_tail(mission.assigned_tail_number)
        if tail_number and tail_number not in arrivals:
            arrivals[tail_number] = mission
    return arrivals


def _arrival_eta_display(mission):
    if not mission:
        return "-"
    return format_local_hhmm(
        mission.actual_block_in_datetime_utc
        or mission.eta_datetime_utc
        or mission.planned_datetime_utc,
        mission.timezone,
    )


def _arrival_status_display(mission):
    if not mission:
        return "-"
    status = mission.arrival_status or mission.api_status or mission.api_status_raw or ""
    return status.replace("_", " ").title() if status else "-"


def _departure_mission_for_operation(operation, mission_id, *, for_update=False):
    if not mission_id:
        return None
    query = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        mission_type="departure",
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


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
        )
        .options(
            joinedload(NeoScorpionFuelAssignment.assigned_fueler),
            joinedload(NeoScorpionFuelAssignment.assigned_truck),
        )
        .all()
    }


def _validate_nightly_fueler_assignment(gateway, operation, user_id):
    selected = (
        db.session.query(NeoScorpionSortFueler, User)
        .join(User, User.id == NeoScorpionSortFueler.user_id)
        .filter(
            NeoScorpionSortFueler.sort_date_operation_id == operation.id,
            NeoScorpionSortFueler.user_id == user_id,
            User.is_active.is_(True),
        )
        .first()
    )
    if selected is None:
        raise ValueError("That fueler is no longer selected for tonight.")
    if not eligible_nightly_fueler_users(gateway, active_users=[selected[1]]):
        raise ValueError("That fueler no longer has Fuel Assignments access.")


def _validate_nightly_truck_assignment(gateway, operation, truck_id):
    selected = (
        db.session.query(NeoScorpionSortTruck, NeoScorpionFuelTruck)
        .join(
            NeoScorpionFuelTruck,
            NeoScorpionFuelTruck.id == NeoScorpionSortTruck.fuel_truck_id,
        )
        .filter(
            NeoScorpionSortTruck.sort_date_operation_id == operation.id,
            NeoScorpionSortTruck.fuel_truck_id == truck_id,
            NeoScorpionFuelTruck.gateway_id == gateway.id,
        )
        .first()
    )
    if selected is None:
        raise ValueError("That truck is no longer selected for tonight.")
    status = selected[0].status
    if status == "topping_off":
        raise ValueError("That truck is currently topping off.")
    if status != "available":
        raise ValueError("That truck is currently unavailable / OOS.")


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


def _submitted_tank_codes(form):
    tank_codes = set()
    for field_name in form.keys():
        if field_name.startswith("remaining_"):
            tank_codes.add(field_name.removeprefix("remaining_"))
        elif field_name.startswith("actual_") and field_name != "actual_fuel":
            tank_codes.add(field_name.removeprefix("actual_"))
    return tank_codes


def _effective_apu_rate(gateway_id, aircraft_type):
    setting = NeoScorpionAircraftFuelSetting.query.filter_by(
        gateway_id=gateway_id,
        aircraft_type=aircraft_type,
    ).first()
    if setting is None:
        return DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
    return Decimal(setting.apu_rate_thousand_lbs_per_hour)


def _apu_rate_field_name(aircraft_type):
    return f"apu_rate_{aircraft_type.lower().replace('-', '_')}"


def _parse_apu_rate(value):
    try:
        rate = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("APU rate must be numeric.") from exc
    if not rate.is_finite():
        raise ValueError("APU rate must be numeric.")
    if rate < 0:
        raise ValueError("APU rate cannot be negative.")
    return rate.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


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
