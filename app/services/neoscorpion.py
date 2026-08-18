from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from flask_login import current_user
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
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
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_value,
    live_screen_refresh_values,
)
from app.services.neoscorpion_assets import (
    eligible_nightly_fueler_users,
    hold_active_assignments_for_truck,
    lock_nightly_asset_scope_for_mutation,
    nightly_asset_context,
    record_nightly_operational_change,
)
from app.services.neoscorpion_fuel_planning import plan_fuel_by_tank
from app.services.time_display import format_local_hhmm


DEFAULT_FUEL_DENSITY_LBS_PER_GALLON = 6.7
DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR = Decimal("0.30")
CALCULATION_NOT_CONFIGURED_MESSAGE = "Fuel calculation not configured for this aircraft type yet."
NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY = "neoscorpion.fuel_dispatch"
NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY = "neoscorpion.fuel_assignments"
NEOSCORPION_LIVE_REFRESH_SCREENS = (
    (NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY, "Fuel Dispatch"),
    (NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY, "Fuel Assignments"),
)
NEOSCORPION_LIVE_REFRESH_SCREEN_KEYS = frozenset(
    screen_key for screen_key, _label in NEOSCORPION_LIVE_REFRESH_SCREENS
)

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


def neoscorpion_live_revision(gateway):
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


def fuel_assignments_live_revision(gateway):
    """Backward-compatible name for the shared NeoScorpion fingerprint."""
    return neoscorpion_live_revision(gateway)


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
    refresh_setting = live_screen_refresh_value(
        gateway,
        NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY,
    )
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
            "fuel_dispatch_refresh": refresh_setting,
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
            **asset_context,
        }

    missions = _departure_missions(operation)
    assignments_by_mission = _assignments_by_mission(operation)
    fuel_work_states = _fuel_work_states_by_assignment_tail(
        assignments_by_mission.values()
    )
    fueling_event_work_state_ids = {
        work_state_id
        for (work_state_id,) in db.session.query(
            NeoScorpionFuelingEvent.fuel_work_state_id
        )
        .filter(
            NeoScorpionFuelingEvent.sort_date_operation_id == operation.id
        )
        .distinct()
        .all()
    }
    nightly_truck_states_by_truck_id = {
        row["selection"].fuel_truck_id: row["selection"]
        for row in asset_context["nightly_trucks"]
    }
    return {
        "operation": operation,
        "rows": _fuel_rows(
            operation,
            missions,
            fuel_trucks=trucks,
            assignments_by_mission=assignments_by_mission,
            fuel_work_states_by_assignment_tail=fuel_work_states,
            nightly_truck_states_by_truck_id=nightly_truck_states_by_truck_id,
            fueling_event_work_state_ids=fueling_event_work_state_ids,
        ),
        "fuelers": fuelers,
        "trucks": trucks,
        "settings": settings,
        "fuel_dispatch_refresh": refresh_setting,
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        **asset_context,
    }


def fueler_context(gateway, user):
    operation = current_sort_operation(gateway)
    refresh_setting = live_screen_refresh_value(
        gateway,
        NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY,
    )
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "fuel_assignments_revision": 0,
            "fuel_assignments_refresh": refresh_setting,
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
    apu_rates_by_aircraft_type = _effective_apu_rates(gateway.id)
    return {
        "operation": operation,
        "rows": _fuel_rows(
            operation,
            missions,
            estimated_fuel_status=CALCULATION_NOT_CONFIGURED_MESSAGE,
            assignments_by_mission=assignments_by_mission,
            fuel_work_states_by_assignment_tail=fuel_work_states,
            apu_rates_by_aircraft_type=apu_rates_by_aircraft_type,
        ),
        "fuel_assignments_revision": _fuel_assignments_revision_for_operation(operation),
        "fuel_assignments_refresh": refresh_setting,
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
    refresh_values = live_screen_refresh_values(
        gateway,
        tuple(screen_key for screen_key, _label in NEOSCORPION_LIVE_REFRESH_SCREENS),
    )
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
        "live_refresh_settings": [
            {
                "screen_key": screen_key,
                "label": label,
                "value": refresh_values[screen_key],
            }
            for screen_key, label in NEOSCORPION_LIVE_REFRESH_SCREENS
        ],
        "live_refresh_allowed_seconds": LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
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
class DispatcherCompleteResult:
    changed: bool
    revision: int
    movement_status: str
    assignment: NeoScorpionFuelAssignment
    mission: SortDateMission
    fueling_event: NeoScorpionFuelingEvent | None


@dataclass(frozen=True)
class FuelCorrectionResult:
    changed: bool
    revision: int
    fuel_work_state: NeoScorpionFuelWorkState | None
    audit_entries: tuple[NeoScorpionFuelAuditEntry, ...]


@dataclass(frozen=True)
class FuelInterruptionResult:
    changed: bool
    revision: int
    assignment: NeoScorpionFuelAssignment
    fuel_work_state: NeoScorpionFuelWorkState | None
    fueling_event: NeoScorpionFuelingEvent | None = None
    audit_entries: tuple[NeoScorpionFuelAuditEntry, ...] = ()


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
    if assignment and (
        assignment.fuel_on_board_at_utc is not None
        or assignment.completed_at_utc is not None
        or assignment.review_status == "complete"
        or mission.fuel_status == "complete"
    ):
        raise ValueError("Completed fuel assignments cannot be edited.")
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

    resource_change_requested = bool(
        requested_fueler_id != current_fueler_id
        or requested_truck_id != current_truck_id
    )
    if assignment is not None and resource_change_requested:
        confirmed_tail = _effective_confirmed_tail(assignment, mission)
        fuel_work_state = _locked_fuel_work_state_for_tail(
            assignment,
            confirmed_tail,
        )
        if _fuel_work_has_begun(fuel_work_state, assignment):
            raise ValueError(
                "Fuel work has begun. Use the dedicated FUELER SWAP or TRUCK SWAP action."
            )

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
            confirmed_tail_number=_normalize_tail(mission.assigned_tail_number),
        )
        db.session.add(assignment)
        changed = True

    requested_review_status = (form.get("review_status") or "pending").strip()
    if requested_review_status == "complete":
        raise ValueError("Use the COMPLETE action to complete fueled work.")
    review_status = _clean_choice(
        requested_review_status,
        {"pending", "assigned", "review"},
        "pending",
    )
    load_planning_note = (form.get("load_planning_note") or "").strip()
    assignment_changed = assignment_created
    if (
        assignment.confirmed_tail_number is None
        and (requested_fueler_id is not None or requested_truck_id is not None)
    ):
        assignment.confirmed_tail_number = _normalize_tail(
            mission.assigned_tail_number
        )
        changed = True
        assignment_changed = True
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
    _validate_fueler_work_access(assignment, mission, fuel_work_state)
    if fuel_work_state is not None and fuel_work_state.off_at_utc is not None:
        raise ValueError(
            "Fuel work is OFF. A dispatcher must REOPEN OFF before editing."
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

    current_apu_source_tank_code = (
        fuel_work_state.apu_source_tank_code if fuel_work_state else None
    )
    target_apu_source_tank_code = current_apu_source_tank_code
    if "apu_source_tank_code" in form:
        target_apu_source_tank_code = (
            (form.get("apu_source_tank_code") or "").strip() or None
        )
    if target_apu_running is True:
        if target_apu_source_tank_code not in expected_tank_codes:
            raise ValueError("Select a valid APU source tank for this aircraft.")
    else:
        target_apu_source_tank_code = None
    apu_source_changed = (
        target_apu_source_tank_code != current_apu_source_tank_code
    )

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
    changed = (
        tank_changed
        or apu_changed
        or apu_source_changed
        or tail_changed
        or transfer_changed
    )
    revision = int(asset_state.revision if asset_state else 0)
    if not changed:
        return FuelerSaveResult(
            changed=False,
            revision=revision,
            tail_fuel_state=tail_fuel_state,
            fuel_work_state=fuel_work_state,
        )

    if tank_changed or apu_changed or apu_source_changed:
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
        if apu_changed or apu_source_changed:
            fuel_work_state.apu_source_tank_code = target_apu_source_tank_code
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
    _validate_fueler_work_access(assignment, mission, fuel_work_state)
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
    tank_layout = tank_layout_for_tail(tail_number)
    if (
        fuel_work_state.apu_running is True
        and fuel_work_state.apu_source_tank_code
        not in {code for code, _label in tank_layout}
    ):
        raise ValueError("Select a valid APU source tank before OFF.")
    (
        _remaining_complete,
        _remaining_total_lbs,
        _actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout,
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
    if assignment.operational_status == "hold_review":
        raise ValueError(
            "HOLD / REVIEW REQUIRED must be resolved before Fuel On Board."
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
    if _effective_confirmed_tail(assignment, mission) != tail_number:
        raise ValueError("Confirm the current mission tail before Fuel On Board.")
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
    if fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("Ended Early fuel work cannot use Fuel On Board.")
    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    tank_layout = tank_layout_for_tail(tail_number)
    if not _apu_source_is_valid(fuel_work_state, tank_layout):
        raise ValueError("Select a valid APU source tank before Fuel On Board.")
    (
        _remaining_complete,
        _remaining_total_lbs,
        _actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout,
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


def complete_fueled_assignment(gateway, user, assignment_id, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError(
            "No current sort operation is available for fuel completion."
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
        raise ValueError("Fuel On Board assignments do not use normal COMPLETE.")
    if assignment.completed_at_utc is not None:
        return DispatcherCompleteResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            movement_status=(
                "moved"
                if assignment.transfer_fuel_gallons is not None
                and assignment.transfer_fuel_gallons > 0
                else "not_moved"
            ),
            assignment=assignment,
            mission=mission,
            fueling_event=None,
        )
    if assignment.review_status == "complete" or mission.fuel_status == "complete":
        raise ValueError("REVIEW REQUIRED: completion audit is missing.")
    if assignment.operational_status == "hold_review":
        raise ValueError("HOLD / REVIEW REQUIRED must be resolved before COMPLETE.")

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    confirmed_tail = _effective_confirmed_tail(assignment, mission)
    if confirmed_tail != tail_number:
        raise ValueError("Confirm the current mission tail before COMPLETE.")
    fuel_work_state = (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    if fuel_work_state is None or fuel_work_state.off_at_utc is None:
        raise ValueError("Fueler must MARK OFF before dispatcher COMPLETE.")
    if fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("Ended Early fuel work cannot use normal COMPLETE.")

    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    tank_layout = tank_layout_for_tail(tail_number)
    if not _apu_source_is_valid(fuel_work_state, tank_layout):
        raise ValueError("Select a valid APU source tank before COMPLETE.")
    (
        _remaining_complete,
        _remaining_total_lbs,
        _actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout,
        tank_states_by_code,
        fuel_work_state.apu_running,
        fuel_work_state.apu_allowance_lbs,
    )
    if neo_fuel_lbs is None:
        raise ValueError("Complete Actual fuel and confirm APU before COMPLETE.")

    existing_events = (
        NeoScorpionFuelingEvent.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .order_by(NeoScorpionFuelingEvent.sequence_number)
        .all()
    )
    if existing_events and fuel_work_state.truck_segment_started_at_utc is None:
        raise ValueError("REVIEW REQUIRED: an unexpected fueling event already exists.")
    movement_status = classify_fuel_movement(
        assignment,
        fuel_work_state,
        tank_states=tank_states,
    )
    if movement_status == "unknown" and not existing_events:
        raise ValueError("Fuel movement cannot yet be determined.")
    if existing_events:
        movement_status = "moved"

    fueling_event = None
    if existing_events and assignment.transfer_fuel_gallons in (None, 0):
        fueling_event = None
    elif movement_status == "moved":
        fueling_event = _close_current_truck_segment(
            gateway,
            operation,
            assignment,
            fuel_work_state,
            existing_events,
            fuel_work_state.off_at_utc,
        )

    assignment.completed_at_utc = now_utc
    assignment.completed_by_user_id = user.id
    assignment.review_status = "complete"
    mission.fuel_status = "complete"
    mission.fuel_completed_at_utc = now_utc
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return DispatcherCompleteResult(
        changed=True,
        revision=int(asset_state.revision),
        movement_status=movement_status,
        assignment=assignment,
        mission=mission,
        fueling_event=fueling_event,
    )


def reopen_fueler_off(gateway, user, assignment_id, reason, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for REOPEN OFF.")
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="REOPEN OFF",
    )
    _validate_precompletion_assignment(assignment, mission, "REOPEN OFF")
    fuel_work_state = _locked_current_fuel_work_state(assignment, mission)
    if fuel_work_state is None or fuel_work_state.off_at_utc is None:
        return FuelCorrectionResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            fuel_work_state=fuel_work_state,
            audit_entries=(),
        )

    reason = _required_correction_reason(reason)
    old_off_value = (
        f"off_at_utc={fuel_work_state.off_at_utc.isoformat()};"
        f"off_by_user_id={fuel_work_state.off_by_user_id or ''}"
    )
    audit_entry = NeoScorpionFuelAuditEntry(
        sort_date_operation_id=operation.id,
        fuel_assignment_id=assignment.id,
        fuel_work_state_id=fuel_work_state.id,
        action="reopen_off",
        field_name="off",
        old_value=old_off_value,
        new_value=None,
        reason=reason,
        changed_by_user_id=user.id,
        created_at=now_utc,
    )
    db.session.add(audit_entry)
    fuel_work_state.off_at_utc = None
    fuel_work_state.off_by_user_id = None
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelCorrectionResult(
        changed=True,
        revision=int(asset_state.revision),
        fuel_work_state=fuel_work_state,
        audit_entries=(audit_entry,),
    )


def correct_fuel_actuals(gateway, user, form, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError(
            "No current sort operation is available for Actual correction."
        )
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        form.get("assignment_id"),
        action_label="Actual correction",
    )
    _validate_precompletion_assignment(assignment, mission, "Actual correction")
    reason = _required_correction_reason(form.get("correction_reason"))
    fuel_work_state = _locked_current_fuel_work_state(assignment, mission)
    if fuel_work_state is None:
        raise ValueError("No current-tail fuel work exists to correct.")
    if fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("Ended Early fuel work cannot be corrected through this path.")
    if (
        NeoScorpionFuelingEvent.query.filter_by(
            fuel_work_state_id=fuel_work_state.id
        )
        .with_for_update()
        .first()
        is not None
    ):
        raise ValueError("REVIEW REQUIRED: a fueling event already exists.")

    tank_layout = tank_layout_for_tail(fuel_work_state.tail_number)
    if not tank_layout:
        raise ValueError(CALCULATION_NOT_CONFIGURED_MESSAGE)
    expected_tank_codes = {code for code, _label in tank_layout}
    submitted_values = {
        key.removeprefix("correct_actual_"): value
        for key, value in form.items()
        if key.startswith("correct_actual_")
    }
    if not submitted_values:
        raise ValueError("Submit at least one Actual tank value to correct.")
    if set(submitted_values) - expected_tank_codes:
        raise ValueError("The submitted tank layout does not match this aircraft.")

    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    changes = []
    for tank_code, submitted_value in submitted_values.items():
        tank_state = tank_states_by_code.get(tank_code)
        new_actual_lbs = display_thousands_to_lbs(submitted_value)
        old_actual_lbs = tank_state.actual_lbs if tank_state else None
        remaining_lbs = tank_state.remaining_lbs if tank_state else None
        if new_actual_lbs is not None and remaining_lbs is None:
            raise ValueError(
                f"Enter Remaining before Actual for {dict(tank_layout)[tank_code]}."
            )
        if old_actual_lbs != new_actual_lbs:
            changes.append((tank_code, tank_state, old_actual_lbs, new_actual_lbs))

    if not changes:
        return FuelCorrectionResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            fuel_work_state=fuel_work_state,
            audit_entries=(),
        )

    audit_entries = []
    for tank_code, tank_state, old_actual_lbs, new_actual_lbs in changes:
        if tank_state is None:
            raise ValueError("Remaining is required before correcting Actual fuel.")
        tank_state.actual_lbs = new_actual_lbs
        audit_entry = NeoScorpionFuelAuditEntry(
            sort_date_operation_id=operation.id,
            fuel_assignment_id=assignment.id,
            fuel_work_state_id=fuel_work_state.id,
            action="correct_actual",
            field_name=f"actual_{tank_code}",
            old_value=(str(old_actual_lbs) if old_actual_lbs is not None else None),
            new_value=(str(new_actual_lbs) if new_actual_lbs is not None else None),
            reason=reason,
            changed_by_user_id=user.id,
            created_at=now_utc,
        )
        db.session.add(audit_entry)
        audit_entries.append(audit_entry)

    actual_values = [
        (
            tank_states_by_code[tank_code].actual_lbs
            if tank_code in tank_states_by_code
            else None
        )
        for tank_code, _label in tank_layout
    ]
    actual_total_lbs = (
        sum(actual_values)
        if tank_layout and all(value is not None for value in actual_values)
        else None
    )
    tail_fuel_state = (
        NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=fuel_work_state.tail_number,
        )
        .with_for_update()
        .first()
    )
    if tail_fuel_state is None:
        tail_fuel_state = NeoScorpionTailFuelState(
            sort_date_operation_id=operation.id,
            tail_number=fuel_work_state.tail_number,
        )
        db.session.add(tail_fuel_state)
    tail_fuel_state.actual_fuel_lbs = actual_total_lbs

    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelCorrectionResult(
        changed=True,
        revision=int(asset_state.revision),
        fuel_work_state=fuel_work_state,
        audit_entries=tuple(audit_entries),
    )


def resume_held_fuel_assignment(gateway, user, assignment_id, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for RESUME.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="RESUME",
    )
    _validate_precompletion_assignment(assignment, mission, "RESUME")
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        _effective_confirmed_tail(assignment, mission),
    )
    if assignment.operational_status != "hold_review":
        return FuelInterruptionResult(
            False,
            int(asset_state.revision if asset_state else 0),
            assignment,
            fuel_work_state,
        )
    blocker = _assignment_resume_blocker(
        gateway,
        operation,
        assignment,
        mission,
        fuel_work_state,
    )
    if blocker:
        raise ValueError(f"HOLD remains: {blocker}")

    audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "resume_hold",
        "operational_status",
        "hold_review",
        "active",
        "Dispatcher resumed the assignment after revalidation.",
        fuel_work_state=fuel_work_state,
        now_utc=now_utc,
    )
    _clear_assignment_hold(assignment)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        fuel_work_state,
        audit_entries=(audit,),
    )


def swap_assignment_fueler(
    gateway,
    user,
    assignment_id,
    replacement_user_id,
    *,
    now_utc=None,
):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for FUELER SWAP.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="FUELER SWAP",
    )
    _validate_precompletion_assignment(assignment, mission, "FUELER SWAP")
    replacement_user_id = _int_or_none(replacement_user_id)
    if replacement_user_id is None:
        raise ValueError("Select a replacement fueler.")
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        _effective_confirmed_tail(assignment, mission),
    )
    if fuel_work_state is not None and fuel_work_state.off_at_utc is not None:
        raise ValueError("REOPEN OFF before swapping the assigned fueler.")
    if assignment.assigned_fueler_user_id == replacement_user_id:
        return FuelInterruptionResult(
            False,
            int(asset_state.revision if asset_state else 0),
            assignment,
            fuel_work_state,
        )
    _validate_nightly_fueler_assignment(
        gateway,
        operation,
        replacement_user_id,
    )
    old_user_id = assignment.assigned_fueler_user_id
    assignment.assigned_fueler_user_id = replacement_user_id
    audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "swap_fueler",
        "assigned_fueler_user_id",
        old_user_id,
        replacement_user_id,
        "Dispatcher changed the assigned fueler.",
        fuel_work_state=fuel_work_state,
        now_utc=now_utc,
    )
    _clear_hold_after_explicit_resolution(
        gateway,
        operation,
        assignment,
        mission,
        fuel_work_state,
    )
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        fuel_work_state,
        audit_entries=(audit,),
    )


def swap_assignment_truck(
    gateway,
    user,
    assignment_id,
    replacement_truck_id,
    *,
    now_utc=None,
):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for TRUCK SWAP.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="TRUCK SWAP",
    )
    _validate_precompletion_assignment(assignment, mission, "TRUCK SWAP")
    replacement_truck_id = _int_or_none(replacement_truck_id)
    if replacement_truck_id is None:
        raise ValueError("Select a replacement truck.")
    confirmed_tail = _effective_confirmed_tail(assignment, mission)
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        confirmed_tail,
    )
    if fuel_work_state is not None and fuel_work_state.off_at_utc is not None:
        raise ValueError("REOPEN OFF before swapping the assigned truck.")
    if assignment.assigned_truck_id == replacement_truck_id:
        return FuelInterruptionResult(
            False,
            int(asset_state.revision if asset_state else 0),
            assignment,
            fuel_work_state,
        )
    _validate_nightly_truck_assignment(
        gateway,
        operation,
        replacement_truck_id,
    )
    tank_states = _locked_tank_states(fuel_work_state)
    existing_events = _locked_fueling_events(fuel_work_state)
    movement_status = _segment_movement_status(
        assignment,
        fuel_work_state,
        tank_states,
    )
    if movement_status == "unknown":
        raise ValueError(
            "REVIEW REQUIRED: fuel movement cannot be determined safely for this truck swap."
        )

    old_truck_id = assignment.assigned_truck_id
    fueling_event = None
    if movement_status == "moved":
        fueling_event = _close_current_truck_segment(
            gateway,
            operation,
            assignment,
            fuel_work_state,
            existing_events,
            now_utc,
        )
        assignment.transfer_fuel_gallons = None
    assignment.assigned_truck_id = replacement_truck_id
    if fuel_work_state is not None and _fuel_work_has_begun(
        fuel_work_state,
        assignment,
    ):
        fuel_work_state.truck_segment_started_at_utc = now_utc
    audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "swap_truck",
        "assigned_truck_id",
        old_truck_id,
        replacement_truck_id,
        "Dispatcher changed the assigned fuel truck.",
        fuel_work_state=fuel_work_state,
        now_utc=now_utc,
    )
    _clear_hold_after_explicit_resolution(
        gateway,
        operation,
        assignment,
        mission,
        fuel_work_state,
    )
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        fuel_work_state,
        fueling_event=fueling_event,
        audit_entries=(audit,),
    )


def confirm_assignment_tail(gateway, user, assignment_id, *, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for tail confirmation.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="CONFIRM NEW TAIL",
    )
    _validate_precompletion_assignment(assignment, mission, "CONFIRM NEW TAIL")
    current_tail = _normalize_tail(mission.assigned_tail_number)
    if not current_tail:
        raise ValueError("The mission does not have a tail to confirm.")
    old_tail = _normalize_tail(assignment.confirmed_tail_number)
    if old_tail == current_tail:
        return FuelInterruptionResult(
            False,
            int(asset_state.revision if asset_state else 0),
            assignment,
            _locked_fuel_work_state_for_tail(assignment, current_tail),
        )
    old_work_state = _locked_fuel_work_state_for_tail(assignment, old_tail)
    if (
        _fuel_work_has_begun(old_work_state, assignment)
        and old_work_state is not None
        and old_work_state.ended_early_at_utc is None
    ):
        raise ValueError("END EARLY the prior-tail fuel work before confirming the new tail.")

    assignment.confirmed_tail_number = current_tail
    current_work_state = _locked_fuel_work_state_for_tail(assignment, current_tail)
    audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "confirm_tail",
        "confirmed_tail_number",
        old_tail,
        current_tail,
        "Dispatcher confirmed the current mission tail.",
        fuel_work_state=old_work_state,
        now_utc=now_utc,
    )
    _clear_hold_after_explicit_resolution(
        gateway,
        operation,
        assignment,
        mission,
        current_work_state,
    )
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        current_work_state,
        audit_entries=(audit,),
    )


def end_fuel_work_early(
    gateway,
    user,
    assignment_id,
    reason,
    *,
    now_utc=None,
):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for END EARLY.")
    now_utc = now_utc or datetime.utcnow()
    reason = _required_interruption_reason(reason, "END EARLY")
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label="END EARLY",
    )
    _validate_precompletion_assignment(assignment, mission, "END EARLY")
    confirmed_tail = _effective_confirmed_tail(assignment, mission)
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        confirmed_tail,
    )
    if fuel_work_state is None:
        raise ValueError("No fuel work exists to END EARLY.")
    if fuel_work_state.ended_early_at_utc is not None:
        return FuelInterruptionResult(
            False,
            int(asset_state.revision if asset_state else 0),
            assignment,
            fuel_work_state,
        )

    tank_states = _locked_tank_states(fuel_work_state)
    existing_events = _locked_fueling_events(fuel_work_state)
    if existing_events and assignment.transfer_fuel_gallons in (None, 0):
        movement_status = "not_moved"
    else:
        movement_status = _segment_movement_status(
            assignment,
            fuel_work_state,
            tank_states,
        )
    if movement_status == "unknown":
        raise ValueError(
            "REVIEW REQUIRED: fuel movement cannot be determined safely for END EARLY."
        )

    fueling_event = None
    if movement_status == "moved":
        fueling_event = _close_current_truck_segment(
            gateway,
            operation,
            assignment,
            fuel_work_state,
            existing_events,
            now_utc,
        )
    assignment.transfer_fuel_gallons = None
    fuel_work_state.ended_early_at_utc = now_utc
    fuel_work_state.ended_early_by_user_id = user.id
    fuel_work_state.ended_early_reason = reason
    assignment.operational_status = "hold_review"
    assignment.hold_reason = "Fuel work ended early; dispatcher review is required."
    assignment.hold_at_utc = now_utc
    assignment.hold_by_user_id = user.id
    audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "end_early",
        "ended_early_at_utc",
        None,
        now_utc.isoformat(),
        reason,
        fuel_work_state=fuel_work_state,
        now_utc=now_utc,
    )
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        fuel_work_state,
        fueling_event=fueling_event,
        audit_entries=(audit,),
    )


def _locked_current_fuel_assignment(operation, assignment_id, *, action_label):
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
            f"Fuel assignment was not found for the current sort operation during {action_label}."
        )
    return assignment_row


def _locked_current_fuel_work_state(assignment, mission):
    tail_number = _effective_confirmed_tail(assignment, mission)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    return (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )


def _validate_precompletion_assignment(assignment, mission, action_label):
    if assignment.fuel_on_board_at_utc is not None:
        raise ValueError(f"Fuel On Board completion cannot use {action_label}.")
    if (
        assignment.completed_at_utc is not None
        or assignment.review_status == "complete"
        or mission.fuel_status == "complete"
    ):
        raise ValueError(f"Completed fuel assignments cannot use {action_label}.")


def _required_correction_reason(value):
    reason = (value or "").strip()
    if not reason:
        raise ValueError("A correction reason is required.")
    return reason


def _effective_confirmed_tail(assignment, mission):
    return _normalize_tail(
        assignment.confirmed_tail_number or mission.assigned_tail_number
    )


def _locked_fuel_work_state_for_tail(assignment, tail_number):
    tail_number = _normalize_tail(tail_number)
    if not tail_number:
        return None
    return (
        NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )


def _locked_tank_states(fuel_work_state):
    if fuel_work_state is None:
        return []
    return (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .all()
    )


def _locked_fueling_events(fuel_work_state):
    if fuel_work_state is None:
        return []
    return (
        NeoScorpionFuelingEvent.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .order_by(NeoScorpionFuelingEvent.sequence_number)
        .all()
    )


def _fuel_work_has_begun(fuel_work_state, assignment=None):
    return bool(
        fuel_work_state is not None
        or (
            assignment is not None
            and assignment.transfer_fuel_gallons is not None
            and assignment.transfer_fuel_gallons > 0
        )
    )


def _validate_fueler_work_access(assignment, mission, fuel_work_state):
    _validate_precompletion_assignment(assignment, mission, "Fueler entry")
    if assignment.operational_status == "hold_review":
        raise ValueError(
            "HOLD / REVIEW REQUIRED. A dispatcher must resolve the assignment before fuel work continues."
        )
    current_tail = _normalize_tail(mission.assigned_tail_number)
    confirmed_tail = _effective_confirmed_tail(assignment, mission)
    if current_tail != confirmed_tail:
        message = (
            "HOLD / STOP & REVIEW: the mission tail changed after fuel work began."
            if _fuel_work_has_begun(fuel_work_state, assignment)
            else "NEEDS RECONFIRMATION: a dispatcher must confirm the new mission tail."
        )
        raise ValueError(message)
    if fuel_work_state is not None and fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("This tail's fuel work was ENDED EARLY and cannot be edited.")


def _assignment_resume_blocker(
    gateway,
    operation,
    assignment,
    mission,
    fuel_work_state,
):
    if assignment.assigned_fueler_user_id is None:
        return "Assign an eligible nightly fueler."
    try:
        _validate_nightly_fueler_assignment(
            gateway,
            operation,
            assignment.assigned_fueler_user_id,
        )
    except ValueError as exc:
        return str(exc)
    if assignment.assigned_truck_id is not None:
        try:
            _validate_nightly_truck_assignment(
                gateway,
                operation,
                assignment.assigned_truck_id,
            )
        except ValueError as exc:
            return str(exc)
    if _effective_confirmed_tail(assignment, mission) != _normalize_tail(
        mission.assigned_tail_number
    ):
        return "Confirm the current mission tail."
    if fuel_work_state is not None and fuel_work_state.ended_early_at_utc is not None:
        return "The confirmed-tail work was Ended Early. Confirm the new tail."
    return None


def _clear_hold_after_explicit_resolution(
    gateway,
    operation,
    assignment,
    mission,
    fuel_work_state,
):
    if assignment.operational_status != "hold_review":
        return False
    if _assignment_resume_blocker(
        gateway,
        operation,
        assignment,
        mission,
        fuel_work_state,
    ):
        return False
    _clear_assignment_hold(assignment)
    return True


def _clear_assignment_hold(assignment):
    assignment.operational_status = "active"
    assignment.hold_reason = None
    assignment.hold_at_utc = None
    assignment.hold_by_user_id = None


def _new_fuel_audit(
    operation,
    assignment,
    user,
    action,
    field_name,
    old_value,
    new_value,
    reason,
    *,
    fuel_work_state=None,
    now_utc=None,
):
    entry = NeoScorpionFuelAuditEntry(
        sort_date_operation_id=operation.id,
        fuel_assignment_id=assignment.id,
        fuel_work_state_id=(fuel_work_state.id if fuel_work_state else None),
        action=action,
        field_name=field_name,
        old_value=(str(old_value) if old_value is not None else None),
        new_value=(str(new_value) if new_value is not None else None),
        reason=reason,
        changed_by_user_id=user.id,
        created_at=now_utc or datetime.utcnow(),
    )
    db.session.add(entry)
    return entry


def _segment_movement_status(assignment, fuel_work_state, tank_states):
    if (
        assignment.transfer_fuel_gallons is not None
        and assignment.transfer_fuel_gallons > 0
    ):
        return "moved"
    if fuel_work_state is None:
        return "not_moved"
    if not any(state.actual_lbs is not None for state in tank_states):
        return "not_moved"
    return classify_fuel_movement(
        assignment,
        fuel_work_state,
        tank_states=tank_states,
    )


def _close_current_truck_segment(
    gateway,
    operation,
    assignment,
    fuel_work_state,
    existing_events,
    ended_at_utc,
):
    if fuel_work_state is None:
        raise ValueError("Fuel work is required to close a truck segment.")
    if assignment.assigned_truck_id is None:
        raise ValueError("Assign the truck used before closing this fuel segment.")
    transfer_gallons = assignment.transfer_fuel_gallons
    if transfer_gallons is None or transfer_gallons <= 0:
        raise ValueError("Enter a positive T/F for the current truck segment.")
    truck_row = (
        db.session.query(NeoScorpionSortTruck, NeoScorpionFuelTruck)
        .join(
            NeoScorpionFuelTruck,
            NeoScorpionFuelTruck.id == NeoScorpionSortTruck.fuel_truck_id,
        )
        .filter(
            NeoScorpionSortTruck.sort_date_operation_id == operation.id,
            NeoScorpionSortTruck.fuel_truck_id == assignment.assigned_truck_id,
            NeoScorpionFuelTruck.gateway_id == gateway.id,
        )
        .with_for_update()
        .first()
    )
    if truck_row is None:
        raise ValueError("The assigned truck is missing from tonight's assets.")
    nightly_truck, fuel_truck = truck_row
    if nightly_truck.current_gallons is None:
        raise ValueError("The assigned truck's current gallons are unknown.")
    if transfer_gallons > nightly_truck.current_gallons:
        raise ValueError("T/F exceeds the assigned truck's current gallons.")

    sequence_number = max(
        (event.sequence_number for event in existing_events),
        default=0,
    ) + 1
    fueling_event = NeoScorpionFuelingEvent(
        sort_date_operation_id=operation.id,
        fuel_assignment_id=assignment.id,
        fuel_work_state_id=fuel_work_state.id,
        tail_number=fuel_work_state.tail_number,
        fuel_truck_id=fuel_truck.id,
        sequence_number=sequence_number,
        started_at_utc=(
            fuel_work_state.truck_segment_started_at_utc
            or fuel_work_state.on_at_utc
        ),
        ended_at_utc=ended_at_utc,
        transfer_fuel_gallons=transfer_gallons,
    )
    db.session.add(fueling_event)
    nightly_truck.current_gallons -= transfer_gallons
    return fueling_event


def _required_interruption_reason(value, action_label):
    reason = (value or "").strip()
    if not reason:
        raise ValueError(f"A reason is required for {action_label}.")
    return reason


def save_truck(gateway, form, user=None):
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

    became_blocked = bool(
        truck.id
        and not truck.is_out_of_service
        and form.get("is_out_of_service") == "1"
    )
    interruption_scope = None
    if became_blocked:
        current_operation = current_sort_operation(gateway)
        if current_operation is not None:
            interruption_scope = lock_nightly_asset_scope_for_mutation(
                current_operation
            )
    truck.truck_number = truck_number
    truck.description = (form.get("description") or "").strip()
    truck.capacity_gallons = _int_or_none(form.get("capacity_gallons"))
    truck.remaining_fuel_gallons = _int_or_none(form.get("remaining_fuel_gallons"))
    truck.vendor_driver_name = (form.get("vendor_driver_name") or "").strip()
    truck.is_active = form.get("is_active") == "1"
    truck.is_out_of_service = form.get("is_out_of_service") == "1"
    if interruption_scope is not None:
        operation, asset_state = interruption_scope
        held_count = hold_active_assignments_for_truck(
            operation,
            truck,
            user,
            "Assigned persistent truck was marked OOS.",
        )
        if held_count:
            record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return truck


def deactivate_truck(gateway, form, user=None):
    truck_id = _int_or_none(form.get("truck_id"))
    truck = NeoScorpionFuelTruck.query.filter_by(id=truck_id, gateway_id=gateway.id).first()
    if not truck:
        raise ValueError("Fuel truck was not found.")
    became_inactive = truck.is_active
    interruption_scope = None
    if became_inactive:
        current_operation = current_sort_operation(gateway)
        if current_operation is not None:
            interruption_scope = lock_nightly_asset_scope_for_mutation(
                current_operation
            )
    truck.is_active = False
    if interruption_scope is not None:
        operation, asset_state = interruption_scope
        held_count = hold_active_assignments_for_truck(
            operation,
            truck,
            user,
            "Assigned persistent truck was deactivated.",
        )
        if held_count:
            record_nightly_operational_change(asset_state, operation.id)
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
        confirmed_tail_number=_normalize_tail(mission.assigned_tail_number),
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def _apu_source_is_valid(fuel_work_state, tank_layout):
    if fuel_work_state is None or fuel_work_state.apu_running is not True:
        return True
    return fuel_work_state.apu_source_tank_code in {
        tank_code for tank_code, _tank_label in tank_layout
    }


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
    nightly_truck_states_by_truck_id=None,
    fueling_event_work_state_ids=None,
    apu_rates_by_aircraft_type=None,
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
    event_work_state_ids = fueling_event_work_state_ids or set()
    effective_apu_rates = apu_rates_by_aircraft_type or {}

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
        confirmed_tail_number = (
            _effective_confirmed_tail(assignment, mission)
            if assignment is not None
            else tail_number
        )
        current_fuel_work_state = fuel_work_states.get(
            (assignment.id, tail_number)
            if assignment is not None and tail_number
            else None
        )
        confirmed_fuel_work_state = fuel_work_states.get(
            (assignment.id, confirmed_tail_number)
            if assignment is not None and confirmed_tail_number
            else None
        )
        tail_mismatch = bool(
            assignment
            and confirmed_tail_number
            and tail_number != confirmed_tail_number
        )
        fuel_work_state = (
            confirmed_fuel_work_state
            if tail_mismatch and confirmed_fuel_work_state is not None
            else current_fuel_work_state
        )
        work_tail_number = (
            fuel_work_state.tail_number if fuel_work_state else tail_number
        )
        aircraft_type = _aircraft_type_for_mission(mission, tail_state)
        detailed_aircraft_type = detailed_aircraft_type_for_tail(work_tail_number)
        tank_layout = tank_layout_for_tail(work_tail_number)
        work_has_begun = _fuel_work_has_begun(fuel_work_state, assignment)
        work_ended_early = bool(
            fuel_work_state and fuel_work_state.ended_early_at_utc
        )
        effective_hold = bool(
            assignment
            and (
                assignment.operational_status == "hold_review"
                or (tail_mismatch and work_has_begun)
            )
        )
        tail_safety_label = (
            "HOLD / STOP & REVIEW"
            if tail_mismatch and work_has_begun and not work_ended_early
            else "NEEDS RECONFIRMATION"
            if tail_mismatch
            else ""
        )
        tank_states_by_code = {
            state.tank_code: state
            for state in (fuel_work_state.tank_states if fuel_work_state else ())
        }
        movement_status = classify_fuel_movement(
            assignment,
            fuel_work_state,
            tank_states=tuple(tank_states_by_code.values()),
        )
        has_prior_fueling_events = bool(
            fuel_work_state and fuel_work_state.id in event_work_state_ids
        )
        if has_prior_fueling_events:
            movement_status = "moved"
        apu_running = fuel_work_state.apu_running if fuel_work_state else None
        apu_allowance_lbs = (
            fuel_work_state.apu_allowance_lbs
            if fuel_work_state and apu_running is not None
            else None
        )
        apu_source_tank_code = (
            fuel_work_state.apu_source_tank_code if fuel_work_state else None
        )
        apu_source_valid = bool(
            apu_running is not True
            or apu_source_tank_code in {code for code, _label in tank_layout}
        )
        planned_by_tank = plan_fuel_by_tank(
            detailed_aircraft_type,
            mission.planned_fuel_load,
            remaining_lbs_by_tank={
                code: state.remaining_lbs
                for code, state in tank_states_by_code.items()
            },
            actual_lbs_by_tank={
                code: state.actual_lbs
                for code, state in tank_states_by_code.items()
            },
            apu_running=apu_running,
            apu_allowance_lbs=apu_allowance_lbs,
            apu_source_tank_code=apu_source_tank_code,
        )
        tank_rows = []
        for tank_code, tank_label in tank_layout:
            tank_state = tank_states_by_code.get(tank_code)
            planned_lbs = (
                planned_by_tank.get(tank_code)
                if planned_by_tank is not None
                else None
            )
            tank_rows.append(
                {
                    "code": tank_code,
                    "label": tank_label,
                    "remaining_lbs": tank_state.remaining_lbs if tank_state else None,
                    "actual_lbs": tank_state.actual_lbs if tank_state else None,
                    "planned_lbs": planned_lbs,
                    "remaining_display": format_display_thousands(
                        tank_state.remaining_lbs if tank_state else None
                    ),
                    "planned_display": (
                        format_display_thousands(planned_lbs)
                        if planned_lbs is not None
                        else "INCOMPLETE"
                    ),
                    "actual_display": format_display_thousands(
                        tank_state.actual_lbs if tank_state else None
                    ),
                }
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
        normal_completion_complete = bool(
            assignment and assignment.completed_at_utc
        )
        administratively_complete = bool(
            fuel_on_board_complete
            or normal_completion_complete
            or (assignment and assignment.review_status == "complete")
            or mission.fuel_status == "complete"
        )
        nightly_truck_state = (
            nightly_truck_states_by_truck_id.get(assignment.assigned_truck_id)
            if nightly_truck_states_by_truck_id is not None and assignment
            else None
        )
        fuel_on_board_ready = bool(
            assignment
            and not fuel_on_board_complete
            and not effective_hold
            and not tail_mismatch
            and not work_ended_early
            and assignment.assigned_fueler_user_id is not None
            and assignment.assigned_truck_id is None
            and assignment.transfer_fuel_gallons in (None, 0)
            and apu_source_valid
            and neo_fuel_lbs is not None
        )
        if fuel_on_board_complete:
            fuel_on_board_reason = "COMPLETE"
        elif effective_hold:
            fuel_on_board_reason = "HOLD / REVIEW REQUIRED"
        elif tail_mismatch:
            fuel_on_board_reason = tail_safety_label
        elif work_ended_early:
            fuel_on_board_reason = "ENDED EARLY"
        elif not assignment or assignment.assigned_fueler_user_id is None:
            fuel_on_board_reason = "Assign fueler first."
        elif assignment.assigned_truck_id is not None:
            fuel_on_board_reason = "Clear unused truck first."
        elif assignment.transfer_fuel_gallons not in (None, 0):
            fuel_on_board_reason = "T/F must be blank or 0."
        elif not apu_source_valid:
            fuel_on_board_reason = "APU source tank required."
        elif neo_fuel_lbs is None:
            fuel_on_board_reason = "Actual/APU incomplete."
        else:
            fuel_on_board_reason = ""
        normal_completion_ready = bool(
            assignment
            and not administratively_complete
            and not effective_hold
            and not tail_mismatch
            and not work_ended_early
            and fuel_work_state
            and fuel_work_state.off_at_utc
            and apu_source_valid
            and neo_fuel_lbs is not None
            and movement_status != "unknown"
            and (
                movement_status == "not_moved"
                or (
                    has_prior_fueling_events
                    and assignment.transfer_fuel_gallons in (None, 0)
                )
                or (
                    assignment.assigned_truck_id is not None
                    and nightly_truck_state is not None
                    and nightly_truck_state.current_gallons is not None
                    and assignment.transfer_fuel_gallons is not None
                    and assignment.transfer_fuel_gallons > 0
                    and assignment.transfer_fuel_gallons
                    <= nightly_truck_state.current_gallons
                )
            )
        )
        if fuel_on_board_complete:
            normal_completion_reason = "FUEL ON BOARD"
        elif normal_completion_complete:
            normal_completion_reason = "COMPLETE"
        elif administratively_complete:
            normal_completion_reason = "REVIEW REQUIRED"
        elif effective_hold:
            normal_completion_reason = "HOLD / REVIEW REQUIRED"
        elif tail_mismatch:
            normal_completion_reason = tail_safety_label
        elif work_ended_early:
            normal_completion_reason = "ENDED EARLY"
        elif not fuel_work_state or not fuel_work_state.off_at_utc:
            normal_completion_reason = "Fueler OFF required."
        elif not apu_source_valid:
            normal_completion_reason = "APU source tank required."
        elif neo_fuel_lbs is None:
            normal_completion_reason = "NEO FUEL incomplete."
        elif movement_status == "unknown":
            normal_completion_reason = "Movement unknown."
        elif movement_status == "moved" and not assignment.assigned_truck_id:
            normal_completion_reason = "Assign truck."
        elif movement_status == "moved" and nightly_truck_state is None:
            normal_completion_reason = "Truck nightly state missing."
        elif movement_status == "moved" and (
            assignment.transfer_fuel_gallons is None
            or assignment.transfer_fuel_gallons <= 0
        ):
            normal_completion_reason = "Positive T/F required."
        elif movement_status == "moved" and nightly_truck_state.current_gallons is None:
            normal_completion_reason = "Truck gallons unknown."
        elif movement_status == "moved" and (
            assignment.transfer_fuel_gallons > nightly_truck_state.current_gallons
        ):
            normal_completion_reason = "Insufficient truck gallons."
        else:
            normal_completion_reason = ""
        rows.append(
            {
                "mission": mission,
                "assignment": assignment,
                "arrival_mission": arrival,
                "tail_number": tail_number or "-",
                "confirmed_tail_number": confirmed_tail_number or "-",
                "work_tail_number": work_tail_number or "-",
                "tail_mismatch": tail_mismatch,
                "tail_safety_label": tail_safety_label,
                "work_has_begun": work_has_begun,
                "work_ended_early": work_ended_early,
                "effective_hold": effective_hold,
                "fueler_work_blocked": bool(
                    effective_hold or tail_mismatch or work_ended_early
                ),
                "hold_reason_display": (
                    assignment.hold_reason
                    if assignment and assignment.hold_reason
                    else tail_safety_label
                ),
                "resume_available": bool(
                    assignment
                    and assignment.operational_status == "hold_review"
                    and not administratively_complete
                ),
                "confirm_tail_available": bool(
                    assignment and tail_mismatch and not administratively_complete
                ),
                "end_early_available": bool(
                    assignment
                    and tail_mismatch
                    and work_has_begun
                    and not work_ended_early
                    and not administratively_complete
                ),
                "resource_swap_required": bool(
                    assignment and work_has_begun and not administratively_complete
                ),
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
                    and apu_source_valid
                    and neo_fuel_lbs is not None
                    and not effective_hold
                    and not tail_mismatch
                    and not work_ended_early
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
                "apu_source_tank_code": apu_source_tank_code,
                "apu_source_tank_label": (
                    dict(tank_layout).get(apu_source_tank_code, "-")
                ),
                "apu_source_valid": apu_source_valid,
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
                "apu_allowance_lbs": apu_allowance_lbs,
                "effective_apu_rate": str(
                    effective_apu_rates.get(
                        detailed_aircraft_type,
                        DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR,
                    )
                ),
                "planned_departure_utc": (
                    mission.planned_datetime_utc.isoformat() + "Z"
                    if mission.planned_datetime_utc is not None
                    else ""
                ),
                "operation_window_minutes": int(operation.window_minutes or 0),
                "planned_total_display": (
                    format_display_thousands(sum(planned_by_tank.values()))
                    if planned_by_tank is not None
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
                "movement_status": movement_status,
                "has_prior_fueling_events": has_prior_fueling_events,
                "normal_completion_complete": normal_completion_complete,
                "administratively_complete": administratively_complete,
                "normal_completion_ready": normal_completion_ready,
                "normal_completion_reason": normal_completion_reason,
                "correction_allowed": bool(
                    assignment
                    and fuel_work_state
                    and not work_ended_early
                    and not administratively_complete
                ),
                "reopen_off_available": bool(
                    assignment
                    and fuel_work_state
                    and fuel_work_state.off_at_utc
                    and not work_ended_early
                    and not administratively_complete
                ),
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
                    nightly_truck_state.current_gallons
                    if nightly_truck_state is not None
                    else truck.remaining_fuel_gallons
                    if truck
                    and truck.remaining_fuel_gallons is not None
                    else None
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
    nightly_truck, persistent_truck = selected
    if not persistent_truck.is_active or persistent_truck.is_out_of_service:
        raise ValueError("That persistent truck is inactive or OOS.")
    status = nightly_truck.status
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


def _effective_apu_rates(gateway_id):
    rates = {
        aircraft_type: DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
        for aircraft_type in NEOSCORPION_APU_AIRCRAFT_TYPES
    }
    overrides = NeoScorpionAircraftFuelSetting.query.filter(
        NeoScorpionAircraftFuelSetting.gateway_id == gateway_id,
        NeoScorpionAircraftFuelSetting.aircraft_type.in_(
            NEOSCORPION_APU_AIRCRAFT_TYPES
        ),
    ).all()
    for override in overrides:
        rates[override.aircraft_type] = Decimal(
            override.apu_rate_thousand_lbs_per_hour
        )
    return rates


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
