import json
from math import ceil
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
import re
from types import SimpleNamespace

from flask import current_app, has_request_context, request
from flask_login import current_user
from sqlalchemy.orm import joinedload, selectinload, lazyload
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelingEventTankSnapshot,
    NeoScorpionTruckInventoryAudit,
    NeoScorpionFuelCycleHistory,
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
from app.neonodes.neorain.services import (
    effective_neorain_load_planner,
    eligible_neorain_load_planners,
)
from app.services.neoscorpion_fueling_status import fueling_status, fob_assessment, utc_naive
from app.services.neoscorpion_fueling_start import fueling_start_state, truck_start_gate
from app.services.neoscorpion_prof import policy_snapshot, load_observation_jobs, totals as prof_totals, iso as prof_iso
from app.services.neoscorpion_spear_forecasts import physical_off_forecast
from app.services.neoscorpion_segments import current_work, new_work, WorkIndex, all_work
from app.services.parking_aircraft import resolve_parking_aircraft_type_from_tail
from app.services.operation_lifecycle import current_existing_operational_sort_operations
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.neoscorpion_dispatch_checks import checked_dispatch_mission_ids
from app.services.neoscorpion_assets import (
    eligible_nightly_fueler_users,
    hold_active_assignments_for_truck,
    lock_nightly_asset_scope_for_mutation,
    nightly_asset_context,
    record_nightly_operational_change,
)
from app.services.neoscorpion_fueler_names import (
    OperationalFuelerDisplay,
    fueler_nicknames,
    operational_fueler_name,
)
from app.services.neoscorpion_fuel_planning import plan_fuel_by_tank
from app.services.neoermac_tail_presence import (
    arrival_presence_by_tail,
    departure_tail_presence,
)
from app.services.neoscorpion_spear import (
    SPEAR_HARD_CONSTRAINTS,
    SPEAR_READINESS_REASON_LABELS,
    build_spear_plan,
    _current_resource_owners,
    active_fueling_finish,
    active_fueler_off,
    effective_spear_settings,
    first_automatic_step,
    priority_rows,
    spear_dispatch_status,
)
from app.services.neoscorpion_learning_vault import learning_vault_status
from app.services.neoscorpion_spear_calibration import (
    build_live_calibration,
    calibration_summary,
)
from app.services.neoscorpion_dispatch_planning import (
    _assignment_finished,
    DEFAULT_PLANNING_INBOUND_FALLBACK_LBS,
    estimate_fuel_demand_gallons,
    project_truck_remaining,
)
from app.services.time_display import format_local_hhmm
from app.services.gateway_matrix import gateway_timezone


DEFAULT_FUEL_DENSITY_LBS_PER_GALLON = 6.7
DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR = Decimal("0.30")
DEFAULT_ASSIGNMENT_ETA_SAFETY_BUFFER_MINUTES = Decimal("5")
DEFAULT_ASSIGNMENT_SETUP_MINUTES = Decimal("10")
DEFAULT_ASSIGNMENT_FINISHING_MINUTES = Decimal("5")
DEFAULT_ASSIGNMENT_PUMP_RATE_GALLONS_PER_MINUTE = Decimal("300")
CALCULATION_NOT_CONFIGURED_MESSAGE = "Fuel calculation not configured for this aircraft type yet."
NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY = "neoscorpion.fuel_dispatch"
NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY = "neoscorpion.fuel_assignments"
NEOSCORPION_HANZO_REFRESH_KEY = "neoscorpion.hanzo"
NEOSCORPION_LIVE_REFRESH_SCREENS = (
    (NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY, "Fuel Dispatch"),
    (NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY, "Fuel Assignments"),
    (NEOSCORPION_HANZO_REFRESH_KEY, "Hanzo"),
)
NEOSCORPION_LIVE_REFRESH_SCREEN_KEYS = frozenset(
    screen_key for screen_key, _label in NEOSCORPION_LIVE_REFRESH_SCREENS
)
TAIL_SWAP_INHERITED_EVENT_AUDIT_FIELD = "tail_swap_inherited_event_id"
NEOSCORPION_FUEL_CYCLE_TYPES = frozenset({"fuel", "uplift", "defuel"})

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
NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES = (
    "B757",
    "A300",
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
    step = Decimal("0.05")
    rounded_thousand_lbs = (
        (raw_thousand_lbs / step).to_integral_value(rounding=ROUND_CEILING)
        * step
    )
    return int(rounded_thousand_lbs * Decimal("1000"))


def display_thousands_to_lbs(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not re.fullmatch(r"(?:\d+(?:\.\d{1,2})?|\.\d{1,2})", text):
        raise ValueError("Fuel value must be a nonnegative K-LB value with up to two decimals.")
    return int(Decimal(text) * Decimal("1000"))


def lbs_to_display_thousands(value):
    amount = _decimal_or_none(value)
    if amount is None:
        return None
    return amount / Decimal("1000")


def format_apu_display_thousands(value):
    amount = _decimal_or_none(value)
    if amount is None:
        return ""
    return format_entered_thousands(amount)


def format_entered_thousands(value):
    amount = _decimal_or_none(value)
    if amount is None:
        return ""
    return format((amount / Decimal("1000")).normalize(), "f")


def format_dispatch_thousands(value):
    """Render Dispatch K-LB fields consistently without changing stored units."""
    amount = _decimal_or_none(value)
    if amount is None:
        return ""
    return f"{amount / Decimal('1000'):.1f}"


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
    permission: str | None
    key: str
    active: bool = False


NEOSCORPION_MENU = (
    NeoScorpionMenuItem("Dashboard", "neoscorpion.index", "neoscorpion.dashboard.view", "dashboard"),
    NeoScorpionMenuItem("Fuel Dispatch", "neoscorpion.fuel_dispatch", "neoscorpion.fuel_dispatch.view", "dispatch"),
    NeoScorpionMenuItem("Fueling Board", "neoscorpion.fueling_board", "neoscorpion.fueling_board.view", "fueling-board"),
    NeoScorpionMenuItem("Truck Driver", "neoscorpion.truck_driver", "neoscorpion.truck_driver.view", "truck-driver"),
    NeoScorpionMenuItem(
        "Truck Manager",
        "neoscorpion.truck_manager",
        "neoscorpion.truck_manager.view",
        "trucks",
    ),
    NeoScorpionMenuItem("Fueler", "neoscorpion.fueler", "neoscorpion.fuel_assignments.view", "fueler"),
    NeoScorpionMenuItem("Quick Fuel", "neoscorpion.quick_fuel", None, "quick-fuel"),
    NeoScorpionMenuItem("Reports", "neoscorpion.reports", "neoscorpion.history.view", "reports"),
    NeoScorpionMenuItem("Fuel History", "neoscorpion.history", "neoscorpion.history.view", "history"),
    NeoScorpionMenuItem("Hanzo", "neoscorpion.hanzo", "neoscorpion.hanzo.view", "hanzo"),
    NeoScorpionMenuItem("Settings", "neoscorpion.settings", "neoscorpion.settings.view", "settings"),
)


def visible_neoscorpion_menu_items(user_can_func, current_endpoint=None):
    items = []
    for item in NEOSCORPION_MENU:
        if item.permission and not user_can_func(item.permission):
            continue
        items.append(
            NeoScorpionMenuItem(
                item.label,
                item.endpoint,
                item.permission,
                item.key,
                active=(
                    item.endpoint == current_endpoint
                    or (
                        item.endpoint == "neoscorpion.reports"
                        and current_endpoint == "neoscorpion.fuel_report"
                    )
                ),
            )
        )
    return items


def current_sort_operation(gateway, now=None):
    operations = current_existing_operational_sort_operations(gateway, now=now)
    return operations[0] if operations else None


def neoscorpion_live_revision(gateway):
    """Return the current sort/revision fingerprint without building screen state."""
    operation = current_sort_operation(gateway)
    if operation is None:
        return {
            "current_operation": False,
            "operation_id": None,
            "revision": 0,
        }
    revision = (
        db.session.query(NeoScorpionSortAssetState.revision)
        .filter(NeoScorpionSortAssetState.sort_date_operation_id == operation.id)
        .scalar()
    )
    return {
        "current_operation": True,
        "operation_id": operation.id,
        "revision": int(revision or 0),
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


def fuel_dispatch_context(gateway, *, include_asset_choices=False, display_nicknames=True,
                          include_fueling_performance=True, include_spear=True):
    """Build core Dispatch first, then add optional planning intelligence."""
    context = _manual_fuel_dispatch_context(
        gateway, include_asset_choices=include_asset_choices
    )
    if context["operation"] is None:
        context["fueling_performance"] = {"fuelers": (), "trucks": ()}
        return context

    nicknames = {}
    if display_nicknames:
        users = [*context["eligible_nightly_fuelers"],
                 *context["nightly_assignment_fuelers"],
                 *(row["user"] for row in context["nightly_fuelers"]),
                 *(row["assigned_fueler"] for row in context["rows"])]
        nicknames = fueler_nicknames(gateway, users)

    if not include_spear:
        _apply_operational_fueler_names(context, nicknames)
        context["fueling_performance"] = {"fuelers": (), "trucks": ()}
        return context

    configured_planning_settings = None
    stage = "assignment recommendations"
    try:
        # Read-only enrichment must remain isolated from operational Dispatch.
        # A savepoint keeps a provider/ORM failure from contaminating the manual
        # context assembly; the fallback below still rolls back and rebuilds.
        with db.session.begin_nested():
            stage = "live calibration"
            configured_planning_settings = assignment_planning_settings(
                gateway, settings=context["settings"]
            )
            live_calibrations = build_live_calibration(
                context["operation"], configured_planning_settings, context["rows"]
            )
            _attach_fueling_timing(context["rows"], context["operation"], context["settings"],
                                   configured_planning_settings, live_calibrations, context["spear_settings"])
            stage = "SPEAR plan"
            spear_plan = build_spear_plan(
                context["rows"],
                operation=context["operation"],
                planning_settings=configured_planning_settings,
                spear_settings=context["spear_settings"],
                nightly_fuelers=context["nightly_assignment_fuelers"],
                nightly_trucks=context["nightly_trucks"],
                now_utc=datetime.utcnow(),
                calibrations=live_calibrations,
            )
            from app.services.neoscorpion_spear_replanning import enrich
            spear_plan = enrich(context, spear_plan, configured_planning_settings, live_calibrations)
            stage = "SPEAR presentation"
            _attach_spear_plan(context["rows"], context["truck_visuals"], spear_plan)
    except Exception:
        current_app.logger.exception(
            "SPEAR optional enrichment failed during %s; rebuilding manual Fuel Dispatch.",
            stage,
        )
        db.session.rollback()
        # A failed ORM/database operation can expire or poison the objects used
        # above.  Never pass them to Jinja: rebuild canonical manual state.
        context = _manual_fuel_dispatch_context(
            gateway, include_asset_choices=include_asset_choices
        )
        _mark_spear_unavailable(context)
        if display_nicknames:
            _apply_operational_fueler_names(context, nicknames)
        if include_fueling_performance:
            context["fueling_performance"] = _fueling_performance_context(
                context, gateway, nicknames,
            )
        return context

    context.update(
        spear_plan=spear_plan,
        spear_unavailable=False,
        spear_automatic_available=first_automatic_step(spear_plan) is not None,
        spear_indicator=spear_dispatch_status(spear_plan, context["spear_settings"]),
        spear_calibration=calibration_summary(live_calibrations),
    )
    if display_nicknames:
        _apply_operational_fueler_names(context, nicknames)
    if include_fueling_performance:
        context["fueling_performance"] = _fueling_performance_context(
            context, gateway, nicknames, planning_settings=configured_planning_settings,
        )
        _attach_dispatch_off_estimates(context, gateway, configured_planning_settings, live_calibrations)
    from app.services.neoscorpion_spear import attach_top_off_approvals
    attach_top_off_approvals(context, getattr(current_user, "id", None))
    from app.services.neoscorpion_spear_decisions import attach_references
    try:
        with db.session.begin_nested():
            attach_references(context, gateway)
    except Exception:
        current_app.logger.warning("SPEAR rendered learning evidence unavailable", exc_info=True)
    return context


def _attach_fueling_timing(rows, operation, settings, planning=None, calibrations=None, spear=None):
    planning = planning or assignment_planning_settings(operation.gateway, settings=settings)
    spear = spear or effective_spear_settings(settings)
    jobs = load_observation_jobs(operation.gateway_id)
    from app.services.neoscorpion_resource_timing import attach_resource_evidence
    attach_resource_evidence(rows,operation,settings,planning,datetime.utcnow())
    for row in rows:
        prediction = physical_off_forecast(row, planning, settings=settings, jobs=jobs, mode=spear.live_calibration_mode)
        row["physical_off_forecast"] = prediction
    from app.services.neoscorpion_spear import _parking_ramp, _travel_minutes
    for row in rows:
        assignment = row.get("assignment")
        if row.get('resource_timing') is not None or assignment is None or row.get("fueling_effective_on_utc") or row.get("truck_start_reason") != "TRUCK STILL FUELING":
            continue
        occupied = [other for other in rows if other.get("assignment") and other["assignment"].id != assignment.id
                    and other["assignment"].assigned_truck_id == assignment.assigned_truck_id
                    and other.get("fueling_on_trusted") and not other.get("is_off")]
        future = [other for other in rows if other.get("assignment") and other["assignment"].assigned_truck_id == assignment.assigned_truck_id
                  and not other.get("fueling_effective_on_utc") and not other.get("is_off") and not other.get("administratively_complete")]
        if len(occupied) != 1 or len(future) != 1:
            continue  # Conflicting future order is not fabricated from card order.
        previous = occupied[0]
        release = previous["physical_off_forecast"]["likely"]
        origin, destination = _parking_ramp(previous.get("parking_position")), _parking_ramp(row.get("parking_position"))
        if release is None or origin is None or destination is None:
            continue
        row["forecast_truck_available_at"] = release+timedelta(minutes=float(_travel_minutes(origin,destination,spear.truck_minutes_per_ramp_move)))
        if previous["assignment"].assigned_fueler_user_id == assignment.assigned_fueler_user_id:
            if planning.finishing_minutes is None:
                continue
            row["forecast_fueler_available_at"] = release+timedelta(minutes=float(planning.finishing_minutes))
        row["physical_off_forecast"] = physical_off_forecast(row, planning, settings=settings, jobs=jobs, mode=spear.live_calibration_mode)
    for row in rows:
        prediction = row["physical_off_forecast"]
        row["fueling_predicted_finish_utc"] = prediction["likely"]
        row["fueler_off_goal"] = (format_local_hhmm(prediction["goal"], gateway_timezone(operation.gateway))
                                  if prediction["goal"] else "UNKNOWN")
        row.update(fueling_status(row, threshold=getattr(settings, "red_assignment_alert_threshold_minutes", 30)))
    return jobs


def _attach_dispatch_off_estimates(context, gateway, planning, calibrations, *, now_utc=None):
    """Format the shared SPEAR physical OFF forecast for compact Dispatch."""
    for row in context["rows"]:
        row.update(fueler_off_estimate_utc="", fueler_off_estimate_clock="", fueler_off_estimate_display="", fueler_off_status_note="")
    if not any(row["dispatch_status_key"] == "fueling" for row in context["rows"]):
        return
    now = now_utc or datetime.utcnow()
    timezone_name = gateway_timezone(gateway)
    for row in context["rows"]:
        if row["dispatch_status_key"] != "fueling":
            continue
        forecast = row.get("physical_off_forecast", {})
        finish = forecast.get("likely")
        clock = format_local_hhmm(finish, timezone_name) if finish else ""
        row["fueler_off_estimate_utc"] = finish.isoformat() + "Z" if finish else ""
        row["fueler_off_estimate_clock"] = clock
        row["fueler_off_prof_difference"] = forecast.get("difference")
        row["fueler_off_estimate_source"] = forecast.get("source", "TIMING UNKNOWN")
        difference = forecast.get("difference")
        suffix = f" {difference:+d}m PROF" if difference else " 0m PROF"
        if forecast.get("source") == "BASELINE":
            suffix += " · BASELINE"
        remaining = ceil(abs((finish-now).total_seconds()) / 60) if finish else 0
        row["fueler_off_estimate_display"] = (
            "TIMING UNKNOWN" if finish is None else
            f"EST OFF {clock} ({'overdue ' if finish <= now else 'in '}{remaining}m){suffix}"
        )
        row["fueler_off_status_note"] = ("TIMING UNKNOWN" if finish is None else
            f"OVER +{difference} MIN" if difference is not None and difference > 0 else f"EST OFF {clock}")


def _ready_to_assign(row):
    """Resource readiness follows the same canonical progress as other views."""
    return fueling_status(row)["dispatch_status_key"] == "ready"


def _fueling_performance_context(context, gateway, nicknames, *, planning_settings=None):
    """Current-sort physical event scores for selected resources only."""
    fuelers = [row["user"] for row in context["nightly_fuelers"] if row["user"].is_active]
    trucks = [row["truck"] for row in context["nightly_trucks"] if row["truck"].is_active]
    operation = context["operation"]
    jobs = load_observation_jobs(gateway.id, operation_id=operation.id, limit=5000) if operation else []

    return {
        "fuelers": tuple({"id": user.id, "name": operational_fueler_name(user, nicknames),
                          **prof_totals(jobs, "fueler", user.id)} for user in fuelers),
        "trucks": tuple({"id": truck.id, "name": f"TRUCK {truck.truck_number}",
                         **prof_totals(jobs, "truck", truck.id)} for truck in trucks),
    }


def _apply_operational_fueler_names(context, nicknames):
    """Adapt only live presentation; canonical SPEAR execution/audits stay unchanged."""
    def display(user):
        return OperationalFuelerDisplay(user, nicknames)

    context["nightly_fuelers"] = [
        {**row, "user": display(row["user"])} for row in context["nightly_fuelers"]
    ]
    for key in ("eligible_nightly_fuelers", "nightly_assignment_fuelers"):
        context[key] = [display(user) for user in context[key]]
    context["fueler_nicknames"] = nicknames
    for row in context["rows"]:
        if row.get("assigned_fueler") is not None:
            row["assigned_fueler"] = display(row["assigned_fueler"])
    plan = context.get("spear_plan")
    if plan is None:
        return
    displayed_steps = []
    for step in plan.steps:
        explanation = step.explanation
        if explanation and explanation.get("alternatives"):
            explanation = {
                **explanation,
                "alternatives": [
                    {**option, "fueler_name": nicknames.get(option["fueler_id"], option["fueler_name"])}
                    for option in explanation["alternatives"]
                ],
            }
        displayed_steps.append(replace(
            step, fueler_name=nicknames.get(step.fueler_id, step.fueler_name),
            explanation=explanation,
        ))
    displayed_next = {
        truck_id: replace(next_job, step=replace(
            next_job.step,
            fueler_name=nicknames.get(next_job.step.fueler_id, next_job.step.fueler_name),
        )) if next_job.step is not None else next_job
        for truck_id, next_job in plan.occupied_truck_next.items()
    }
    displayed_plan = replace(
        plan, steps=tuple(displayed_steps), occupied_truck_next=displayed_next,
    )
    context["spear_plan"] = displayed_plan
    _attach_spear_plan(context["rows"], context["truck_visuals"], displayed_plan)


def _manual_fuel_dispatch_context(gateway, *, include_asset_choices=False):
    """Return a fully renderable canonical manual Dispatch context only."""
    operation = current_sort_operation(gateway)
    refresh_setting = live_screen_refresh_value(
        gateway,
        NEOSCORPION_FUEL_DISPATCH_REFRESH_KEY,
    )
    fuelers = _fueler_users()
    trucks = _fuel_trucks(gateway)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    spear_settings = effective_spear_settings(settings)
    fuel_density = (
        settings.fuel_density_lbs_per_gallon
        if settings is not None
        else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON
    )
    planning_inbound_fallback_lbs = (
        settings.planning_inbound_fuel_fallback_lbs
        if settings is not None
        and settings.planning_inbound_fuel_fallback_lbs is not None
        else DEFAULT_PLANNING_INBOUND_FALLBACK_LBS
    )
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
            "red_assignment_alert_threshold_minutes": (
                settings.red_assignment_alert_threshold_minutes if settings else 30
            ),
            "truck_visuals": [],
            "spear_plan": None,
            "spear_unavailable": False,
            "spear_automatic_available": False,
            "spear_settings": spear_settings,
            "spear_indicator": spear_dispatch_status(None, spear_settings),
            "spear_calibration": {"active_count": 0, "collecting_count": 0, "label": "SPEAR CALIBRATION · COLLECTING", "items": ()},
            "fuel_dispatch_refresh": refresh_setting,
            "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
            **asset_context,
        }

    missions = _departure_missions(operation, include_load_planners=True)
    (
        apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type,
    ) = _effective_aircraft_fuel_configuration(gateway.id)
    assignments_by_mission = _assignments_by_mission(operation)
    fuel_work_states = _fuel_work_states_by_assignment_tail(
        assignments_by_mission.values()
    )
    # Reuse the existing cycle lookup for totals; assignment T/F is a mirror of
    # completed events and must not be added to the physical event ledger.
    fueling_events = (
        db.session.query(
            NeoScorpionFuelingEvent.id,
            NeoScorpionFuelingEvent.fuel_work_state_id,
            NeoScorpionFuelingEvent.cycle_number,
            NeoScorpionFuelingEvent.transfer_fuel_gallons,
        )
        .filter(
            NeoScorpionFuelingEvent.sort_date_operation_id == operation.id
        )
        .all()
    )
    fueling_event_cycle_keys = {
        (event.fuel_work_state_id, int(event.cycle_number or 1))
        for event in fueling_events
    }

    nightly_truck_states_by_truck_id = {
        row["selection"].fuel_truck_id: row["selection"]
        for row in asset_context["nightly_trucks"]
    }
    parking = _parking_by_tail(operation)
    start_evidence = {}
    rows = _fuel_rows(
        operation,
        missions,
        parking_by_tail=parking,
        start_evidence=start_evidence,
        fuel_trucks=trucks,
        assignments_by_mission=assignments_by_mission,
        fuel_work_states_by_assignment_tail=fuel_work_states,
        nightly_truck_states_by_truck_id=nightly_truck_states_by_truck_id,
        fueling_event_cycle_keys=fueling_event_cycle_keys,
        apu_rates_by_aircraft_type=apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type=(
            lateral_imbalance_limits_by_aircraft_type
        ),
        fuel_density_lbs_per_gallon=fuel_density,
        planning_inbound_fallback_lbs=planning_inbound_fallback_lbs,
        status_settings=settings,
    )
    _attach_dispatch_load_planners(rows)
    from app.services.neoscorpion_call_dispatch import attach_call_dispatch_alerts
    attach_call_dispatch_alerts(rows, operation, settings)
    checked_missions = checked_dispatch_mission_ids(operation, current_user)
    excess_threshold = getattr(settings, "neo_fuel_excess_alert_gallons", 500)
    for row in rows:
        row["dispatcher_checked"] = row["mission"].id in checked_missions
        # Compare the displayed departure Fuel Load, not an earlier visit's
        # immutable target retained for handoff/physical-work calculations.
        comparison_required = row["departure_required_fuel_lbs"]
        row["neo_fuel_excess_alert"] = neo_fuel_excess_alert(
            row["neo_fuel_lbs"], comparison_required, fuel_density, excess_threshold)
        row["neo_fuel_shortfall_alert"] = neo_fuel_shortfall_alert(
            row["neo_fuel_lbs"], comparison_required)
    rows.sort(
        key=lambda row: (
            0
            if str(row["mission"].destination or "").strip().upper() == "HOT"
            else 1,
        )
    )
    history_by_mission = {}
    histories = (
        NeoScorpionFuelCycleHistory.query.filter_by(sort_date_operation_id=operation.id)
        .order_by(NeoScorpionFuelCycleHistory.cycle_number)
        .all()
    )
    for history in histories:
        history_by_mission.setdefault(history.mission_id, []).append(history)
    for row in rows:
        row["history"] = [history for history in history_by_mission.get(row["mission"].id, [])
                          if row["assignment"] and history.cycle_number < (row["assignment"].current_cycle_number or 1)]
        cycle_number = row["assignment"].current_cycle_number if row["assignment"] else 1
        row["row_key"] = f"{row['mission'].id}-{cycle_number or 1}"
        if int(cycle_number or 1) > 1 and row["history"]:
            original = row["history"][0].snapshot
            if "inbound_fuel_lbs" in original:
                row["inbound_fuel_lbs"] = original["inbound_fuel_lbs"]
                row["inbound_fuel_display"] = format_dispatch_thousands(original["inbound_fuel_lbs"])
        row["ready_to_assign"] = _ready_to_assign(row)
    truck_visuals = _dispatch_truck_visuals(
        asset_context["nightly_trucks"],
        rows,
        timezone_name=gateway_timezone(gateway),
    )
    _attach_dispatch_truck_activity(
        truck_visuals, assignments_by_mission.values(),
        start_evidence, nightly_truck_states_by_truck_id, settings,
    )
    _attach_dispatch_mission_truck_visuals(
        rows,
        truck_visuals,
    )
    context = {
        "operation": operation,
        "rows": rows,
        "sort_fuel_totals": _sort_fuel_totals(rows, fueling_events, fuel_density),
        "truck_visuals": truck_visuals,
        "fuelers": fuelers,
        "trucks": trucks,
        "settings": settings,
        "red_assignment_alert_threshold_minutes": (
            settings.red_assignment_alert_threshold_minutes if settings else 30
        ),
        "spear_plan": None,
        "spear_unavailable": False,
        "spear_automatic_available": False,
        "spear_settings": spear_settings,
        "spear_indicator": spear_dispatch_status(None, spear_settings),
        "spear_calibration": _empty_spear_calibration(),
        "fuel_dispatch_refresh": refresh_setting,
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
        "nightly_truck_states_by_truck_id": nightly_truck_states_by_truck_id,
        **asset_context,
    }
    _clear_optional_dispatch_enrichment(context)
    from app.services.neoscorpion_pending_uplift import attach_pending,attach_console
    return attach_console(attach_pending(context))


def _empty_spear_calibration():
    return {
        "active_count": 0,
        "collecting_count": 0,
        "label": "SPEAR CALIBRATION · COLLECTING",
        "items": (),
    }


def _clear_optional_dispatch_enrichment(context):
    for row in context["rows"]:
        row.update(
            assignment_recommendation=None,
            assignment_recommendation_display=None,
            assignment_recommendation_time_display=None,
            assignment_recommendation_reason_display=None,
            spear_step=None,
            spear_risk=None,
            spear_ready=False,
            spear_waiting_for_data=None,
            spear_readiness_reasons=(),
            spear_problem=None,
        )
    for visual in context["truck_visuals"]:
        visual["spear_recommendation"] = None


def _mark_spear_unavailable(context):
    _clear_optional_dispatch_enrichment(context)
    context.update(
        spear_plan=None,
        spear_unavailable=True,
        spear_automatic_available=False,
        spear_indicator={
            "state": "unavailable",
            "label": "SPEAR · UNAVAILABLE",
            "detail": "Recommendations are unavailable for this refresh.",
            "automation_enabled": False,
        },
        spear_calibration=_empty_spear_calibration(),
    )


def hanzo_context(gateway):
    """Build the read-only Hanzo shadow-validation view from dispatcher state."""
    operation = current_sort_operation(gateway)
    refresh_setting = live_screen_refresh_value(
        gateway,
        NEOSCORPION_HANZO_REFRESH_KEY,
    )
    if operation is None:
        return {
            "operation": None,
            "rows": [],
            "hanzo_revision": 0,
            "hanzo_refresh": refresh_setting,
        }

    missions = _departure_missions(operation)
    assignments_by_mission = _assignments_by_mission(operation)
    fuel_work_states = _fuel_work_states_by_assignment_tail(
        assignments_by_mission.values()
    )
    (
        apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type,
    ) = _effective_aircraft_fuel_configuration(gateway.id)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    rows = _fuel_rows(
        operation,
        missions,
        status_settings=settings,
        fuel_density_lbs_per_gallon=(settings.fuel_density_lbs_per_gallon if settings else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON),
        planning_inbound_fallback_lbs=(settings.planning_inbound_fuel_fallback_lbs if settings else DEFAULT_PLANNING_INBOUND_FALLBACK_LBS),
        assignments_by_mission=assignments_by_mission,
        fuel_work_states_by_assignment_tail=fuel_work_states,
        apu_rates_by_aircraft_type=apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type=(
            lateral_imbalance_limits_by_aircraft_type
        ),
    )
    try:
        with db.session.begin_nested():
            _attach_fueling_timing(rows, operation, settings)
    except Exception:
        current_app.logger.exception("Hanzo fueling timing unavailable.")
    nicknames = fueler_nicknames(gateway, (row["assigned_fueler"] for row in rows))
    timezone_name = gateway_timezone(gateway)
    for row in rows:
        row["hanzo_status"] = _hanzo_planning_status(row)
        row["hanzo_fueler_name"] = (
            operational_fueler_name(row["assigned_fueler"], nicknames)
            if row["assigned_fueler"] else "—"
        )
        work = row["fuel_work_state"]
        row["hanzo_on_time"] = (
            format_local_hhmm(row["fueling_effective_on_utc"], timezone_name)
            if row.get("fueling_effective_on_utc") else "—"
        )
        row["hanzo_off_time"] = (
            format_local_hhmm(work.off_at_utc, timezone_name)
            if work and work.off_at_utc else "—"
        )
        predicted_off = row.get("physical_off_forecast", {}).get("likely")
        row["hanzo_est_off_time"] = format_local_hhmm(predicted_off, timezone_name) if predicted_off else "-"
        row["hanzo_actual_off_time"] = format_local_hhmm(work.off_at_utc, timezone_name) if work and work.off_at_utc else "-"
        row["hanzo_fuel_short"] = _hanzo_fuel_short(
            row["required_fuel_lbs"], row["neo_fuel_lbs"]
        )
    return {
        "operation": operation,
        "rows": rows,
        "hanzo_revision": _fuel_assignments_revision_for_operation(operation),
        "hanzo_refresh": refresh_setting,
    }


def fueler_context(gateway, user, *, assignment_id=None, dispatcher=False, physical_work_id=None, physical_sequence=None,
                   truck_id=None, include_completed=False, include_timing=True):
    operation = current_sort_operation(gateway)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    refresh_setting = live_screen_refresh_value(
        gateway,
        NEOSCORPION_FUEL_ASSIGNMENTS_REFRESH_KEY,
    )
    operational_name = operational_fueler_name(user, fueler_nicknames(gateway, [user]))
    if not operation:
        return {
            "operation": None,
            "rows": [],
            "fuel_assignments_revision": 0,
            "fuel_assignments_refresh": refresh_setting,
            "settings": settings,
            "operational_fueler_name": operational_name,
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
            SortDateMission.mission_type == "departure",
        )
        .order_by(SortDateMission.planned_datetime_utc, SortDateMission.flight_number)
        .all()
    )
    # Inspect the same sort's other work without exposing those assignments in
    # this user's cards. Reuse this batch and its work states for truck status.
    visible_assignments = [assignment for assignment in assignments
                           if (assignment.assigned_truck_id == truck_id if truck_id is not None else
                               assignment.assigned_fueler_user_id is not None if dispatcher
                               else assignment.assigned_fueler_user_id == user.id)
                           and (assignment_id is None or assignment.id == assignment_id)
                           and (include_completed or (assignment.review_status != "complete"
                           and assignment.sort_date_mission.fuel_status != "complete"))]
    missions = [assignment.sort_date_mission for assignment in assignments if assignment.sort_date_mission]
    assignments_by_mission = {
        assignment.sort_date_mission_id: assignment for assignment in assignments
    }
    fuel_work_states = _fuel_work_states_by_assignment_tail(assignments)
    by_assignment = {item.id: item for item in assignments}
    sources = []
    for work in all_work(fuel_work_states):
        owner = by_assignment[work.fuel_assignment_id]
        if (work.physical_sequence_number is None or (work.physical_finalized_at_utc is not None and not include_completed)
                or (work.id == owner.active_work_state_id and work.tail_number == _normalize_tail(owner.sort_date_mission.assigned_tail_number))
                or (not dispatcher and work.physical_fueler_user_id != user.id)
                or (assignment_id is not None and owner.id != assignment_id)):
            continue
        source_assignment, source_mission = _segment_views(owner, owner.sort_date_mission, work)
        sources.append((source_mission, source_assignment, work))
    parking = _parking_by_tail(operation)
    (
        apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type,
    ) = _effective_aircraft_fuel_configuration(gateway.id)
    truck_ids = {assignment.assigned_truck_id for assignment in assignments
                 if assignment.assigned_truck_id is not None}
    truck_ids.update(work.physical_truck_id for work in all_work(fuel_work_states)
                     if work.physical_truck_id is not None)
    selections = (NeoScorpionSortTruck.query.filter(
        NeoScorpionSortTruck.sort_date_operation_id == operation.id,
        NeoScorpionSortTruck.fuel_truck_id.in_(truck_ids),
    ).all() if truck_ids else [])
    rows = _fuel_rows(
        operation,
        missions,
        estimated_fuel_status=CALCULATION_NOT_CONFIGURED_MESSAGE,
        fuel_density_lbs_per_gallon=(
            settings.fuel_density_lbs_per_gallon
            if settings is not None else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON
        ),
        planning_inbound_fallback_lbs=(
            settings.planning_inbound_fuel_fallback_lbs
            if settings is not None
            and settings.planning_inbound_fuel_fallback_lbs is not None
            else DEFAULT_PLANNING_INBOUND_FALLBACK_LBS
        ),
        status_settings=settings,
        projection_sources=sources,
        parking_by_tail=parking,
        nightly_truck_states_by_truck_id={item.fuel_truck_id:item for item in selections},
        assignments_by_mission=assignments_by_mission,
        fuel_work_states_by_assignment_tail=fuel_work_states,
        apu_rates_by_aircraft_type=apu_rates_by_aircraft_type,
        lateral_imbalance_limits_by_aircraft_type=(
            lateral_imbalance_limits_by_aircraft_type
        ),
    )
    _attach_fueler_truck_status(rows, assignments, fuel_work_states, parking, selections,
                               timezone_name=gateway_timezone(gateway))
    try:
        with db.session.begin_nested():
            if include_timing:
                _attach_fueling_timing(rows, operation, settings)
    except Exception:
        current_app.logger.exception("Fueling timing unavailable; retaining TIMING UNKNOWN.")
        for row in rows:
            row.pop("fueling_predicted_finish_utc", None)
            row.update(fueling_status(row, threshold=getattr(settings, "red_assignment_alert_threshold_minutes", 30)))
    visible_ids = {assignment.id for assignment in visible_assignments}
    rows = [row for row in rows if row.get("assignment") and (row["assignment"].id in visible_ids or row.get("is_source_segment"))]
    if truck_id is not None:
        rows = [row for row in rows if row["assigned_truck"] and row["assigned_truck"].id == truck_id]
    if physical_work_id is not None:
        rows = [row for row in rows if row.get("physical_work_id") == _int_or_none(physical_work_id)
                and str(row.get("physical_sequence")) == str(physical_sequence)]
    verification_by_truck = {}
    from app.models import NeoScorpionTruckDriver
    from app.services.neoscorpion_vendors import driver_name
    driver_records = {d.fuel_truck_id:d for d in NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id).all()}
    driver_names = {}
    for row in rows:
        work = row["fuel_work_state"]
        row["edit_baseline"] = fueler_edit_state(
            row["assignment"], row["mission"], work, row["tail_fuel_state"],
            work.tank_states if work else [],
        )
        from app.services.neoscorpion_inventory import verification_available
        truck = row.get('assigned_truck')
        if truck and truck.id not in driver_names:
            driver_names[truck.id] = driver_name(gateway,operation,truck.id,driver_records)
        row['truck_driver_name'] = driver_names.get(truck.id,'UNKNOWN') if truck else 'UNKNOWN'
        if row.get('is_off') and truck and truck.id not in verification_by_truck:
            verification_by_truck[truck.id] = verification_available(operation, truck.id)
        row['truck_verification_available'] = bool(row.get('is_off') and truck and verification_by_truck.get(truck.id))
    return {
        "operation": operation,
        "rows": rows,
        "fuel_assignments_revision": _fuel_assignments_revision_for_operation(operation),
        "fuel_assignments_refresh": refresh_setting,
        "settings": settings,
        "operational_fueler_name": operational_name,
        "calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
    }


def _attach_fueler_truck_status(rows, assignments, work_states, parking, selections, *, timezone_name):
    """Read-only current activity, never projected assignment locations."""
    active_by_truck = {}
    for assignment in assignments:
        mission = assignment.sort_date_mission
        tail = _normalize_tail(mission.assigned_tail_number)
        work = work_states.get((assignment.id, tail))
        if (assignment.assigned_truck_id is None or work is None or
                (work.on_at_utc is None and work.manual_on_at_utc is None)
                or _assignment_finished(assignment, work)
                or assignment.review_status == "complete"
                or mission.fuel_status in {"complete", "fob", "fuel_on_board"}
                or assignment.operational_status != "active"):
            continue
        active_by_truck.setdefault(assignment.assigned_truck_id, []).append(tail)

    selections_by_truck = {selection.fuel_truck_id: selection for selection in selections}
    for row in rows:
        truck_id = row["assignment"].assigned_truck_id
        active = active_by_truck.get(truck_id, [])
        selection = selections_by_truck.get(truck_id)
        topping_off = bool(selection and selection.status == "topping_off")
        status, sent_time = "", ""
        if len(active) > 1 or (active and topping_off):
            status = "CURRENT STATUS UNKNOWN"
        elif active:
            position, valid = parking.get(active[0], ("-", False))
            status = (f"CURRENTLY FUELING ON {position}" if valid
                      else "CURRENTLY FUELING · LOCATION UNKNOWN")
        elif topping_off:
            status = "CURRENTLY TOPPING OFF"
            sent_time = (format_local_hhmm(selection.top_off_sent_at_utc, timezone_name)
                         if selection.top_off_sent_at_utc else "")
        if not topping_off:
            status = row.get("truck_current_activity", status)
        row["assigned_truck_current_status"] = status
        row["assigned_truck_top_off_sent_time"] = sent_time


def fueling_board_context(gateway, user):
    """Read-only projection of the same current-sort work visible to Fuelers."""
    context = fueler_context(gateway, user, dispatcher=True)
    rows = context["rows"]
    nicknames = fueler_nicknames(gateway, (row["assigned_fueler"] for row in rows))

    def etd_key(row):
        mission = row["mission"]
        departure = mission.eta_datetime_utc or mission.planned_datetime_utc
        if departure is not None and departure.tzinfo is not None:
            departure = departure.astimezone(timezone.utc).replace(tzinfo=None)
        return (departure is None, departure or datetime.max, mission.flight_number or "", row["assignment"].id)

    board_rows = []
    for row in sorted(rows, key=etd_key):
        if row["administratively_complete"]:
            continue
        progress = _fueling_board_progress(row)

        exceptions = []
        if row["fueler_work_blocked"] or row["dispatch_status_key"] == "review":
            exceptions.append(row["hold_reason_display"] or row["dispatch_status_detail"] or "Dispatcher review required")
        if row["direction_mismatch"]:
            exceptions.append("Opposite fuel direction requires review.")
        if row["fuel_configuration_message"]:
            exceptions.append(row["fuel_configuration_message"])
        board_rows.append({
            "row": row,
            "fueler_name": operational_fueler_name(row["assigned_fueler"], nicknames),
            "progress": progress,
            "exceptions": tuple(dict.fromkeys(exceptions)),
        })

    return {
        "operation": context["operation"],
        "board_rows": board_rows,
        "fuel_assignments_revision": context["fuel_assignments_revision"],
        "fuel_assignments_refresh": context["fuel_assignments_refresh"],
    }


def _fueling_board_progress(row):
    return row["dispatch_status_label"]


def truck_manager_context(gateway):
    trucks = _fuel_trucks(gateway)
    operation = current_sort_operation(gateway)
    nightly_trucks_by_id = {}
    if operation is not None:
        nightly_trucks_by_id = {
            row.fuel_truck_id: row
            for row in NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation.id
            ).all()
        }
    return {
        "trucks": trucks,
        "nightly_trucks_by_id": nightly_trucks_by_id,
    }


def neo_fuel_shortfall_alert(neo_lbs, required_lbs):
    """Presentation only: any shortfall in unrounded, available fuel pounds."""
    if neo_lbs is None or required_lbs is None:
        return False
    try:
        neo, required = Decimal(str(neo_lbs)), Decimal(str(required_lbs))
        return bool(neo.is_finite() and required.is_finite()
                    and neo >= 0 and required >= 0 and neo < required)
    except (InvalidOperation, ValueError, TypeError):
        return False


def neo_fuel_excess_alert(neo_lbs, required_lbs, density, threshold_gallons=500):
    """Compare unrounded pounds at the gateway density, without changing fuel."""
    if any(value is None for value in (neo_lbs, required_lbs, density)):
        return False
    try:
        neo, required, density, threshold = (Decimal(str(value)) for value in
            (neo_lbs, required_lbs, density, 500 if threshold_gallons is None else threshold_gallons))
        if not all(value.is_finite() for value in (neo, required, density, threshold)):
            return False
        return bool(neo >= 0 and required >= 0 and density > 0 and threshold >= 0
                    and neo > required and neo - required >= threshold * density)
    except (InvalidOperation, ValueError, TypeError):
        return False


def dispatch_neo_fuel_presentation(gateway, mission, assignment, work=None):
    """Reconcile only the changed mission after Required or APU edits."""
    if work is not None and _normalize_tail(work.tail_number) != _normalize_tail(mission.assigned_tail_number):
        work = None
    if work is None and assignment is not None:
        work = _fuel_work_states_by_assignment_tail([assignment]).get(
            (assignment.id, _normalize_tail(mission.assigned_tail_number)))
    tanks = {tank.tank_code: tank for tank in work.tank_states} if work else {}
    neo_lbs = _fuel_work_calculation(tank_layout_for_tail(mission.assigned_tail_number), tanks,
        work.apu_running if work else None, _effective_apu_allowance_lbs(work))[4]
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    density = settings.fuel_density_lbs_per_gallon if settings else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON
    return {"available": neo_lbs is not None, "display": format_display_thousands(neo_lbs),
            "shortfall": neo_fuel_shortfall_alert(neo_lbs, mission.planned_fuel_load),
            "excess": neo_fuel_excess_alert(neo_lbs, mission.planned_fuel_load, density,
                                           getattr(settings, "neo_fuel_excess_alert_gallons", 500))}


def settings_context(gateway):
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    if settings is None:
        settings = {
            "fuel_density_lbs_per_gallon": DEFAULT_FUEL_DENSITY_LBS_PER_GALLON,
            "red_assignment_alert_threshold_minutes": 30,
            "ready_assignment_pulse_enabled": True,
            "mission_row_height_percent": 100,
            "neo_fuel_excess_alert_gallons": 500,
            "planning_inbound_fuel_fallback_lbs": (
                DEFAULT_PLANNING_INBOUND_FALLBACK_LBS
            ),
            "fob_difference_threshold_lbs": None,
            "tf_vs_estimated_threshold_lbs": None,
        }
    planning_inbound_fallback_lbs = (
        settings["planning_inbound_fuel_fallback_lbs"]
        if isinstance(settings, dict)
        else settings.planning_inbound_fuel_fallback_lbs
    )
    if planning_inbound_fallback_lbs is None:
        planning_inbound_fallback_lbs = DEFAULT_PLANNING_INBOUND_FALLBACK_LBS
    overrides = {
        row.aircraft_type: row
        for row in NeoScorpionAircraftFuelSetting.query.filter(
            NeoScorpionAircraftFuelSetting.gateway_id == gateway.id,
            NeoScorpionAircraftFuelSetting.aircraft_type.in_(
                NEOSCORPION_APU_AIRCRAFT_TYPES
            ),
        ).all()
    }
    assignment_planning = assignment_planning_settings(gateway)
    spear_settings = effective_spear_settings(
        settings if not isinstance(settings, dict) else None
    )
    return {
        "settings": settings,
        "planning_inbound_fallback_display": format_display_thousands(
            planning_inbound_fallback_lbs
        ),
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
        "assignment_planning": assignment_planning,
        "spear_settings": spear_settings,
        "spear_learning_vault": learning_vault_status(),
        "spear_priority_rows": priority_rows(spear_settings),
        "spear_hard_constraints": SPEAR_HARD_CONSTRAINTS,
        "assignment_pump_rate_settings": [
            {
                "aircraft_type": aircraft_type,
                "field_name": _assignment_pump_rate_field_name(aircraft_type),
                "rate": assignment_planning.pump_rate_for(aircraft_type),
            }
            for aircraft_type in NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
        ],
        "lateral_imbalance_settings": [
            {
                "aircraft_type": aircraft_type,
                "field_name": _max_lateral_imbalance_field_name(aircraft_type),
                "value": (
                    overrides[aircraft_type].max_lateral_imbalance_lbs
                    if aircraft_type in overrides
                    and overrides[aircraft_type].max_lateral_imbalance_lbs is not None
                    else ""
                ),
            }
            for aircraft_type in NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
        ],
    }


def assignment_planning_settings(gateway, *, settings=None):
    if settings is None:
        settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    aircraft_settings = {
        row.aircraft_type: row
        for row in NeoScorpionAircraftFuelSetting.query.filter(
            NeoScorpionAircraftFuelSetting.gateway_id == gateway.id,
            NeoScorpionAircraftFuelSetting.aircraft_type.in_(
                NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
            ),
        ).all()
    }
    return AssignmentPlanningSettings(
        setup_minutes=(
            Decimal(settings.assignment_setup_minutes)
            if settings is not None and settings.assignment_setup_minutes is not None
            else DEFAULT_ASSIGNMENT_SETUP_MINUTES
        ),
        finishing_minutes=(
            Decimal(settings.assignment_finishing_minutes)
            if settings is not None and settings.assignment_finishing_minutes is not None
            else DEFAULT_ASSIGNMENT_FINISHING_MINUTES
        ),
        eta_safety_buffer_minutes=(
            Decimal(settings.assignment_eta_safety_buffer_minutes)
            if settings is not None
            and settings.assignment_eta_safety_buffer_minutes is not None
            else DEFAULT_ASSIGNMENT_ETA_SAFETY_BUFFER_MINUTES
        ),
        pump_rates_gallons_per_minute={
            aircraft_type: (
                Decimal(
                    aircraft_settings[aircraft_type].assignment_pump_rate_gallons_per_minute
                )
                if aircraft_type in aircraft_settings
                and aircraft_settings[
                    aircraft_type
                ].assignment_pump_rate_gallons_per_minute is not None
                else DEFAULT_ASSIGNMENT_PUMP_RATE_GALLONS_PER_MINUTE
            )
            for aircraft_type in NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
        },
    )


def history_context(gateway):
    operation = current_sort_operation(gateway)
    completed = []
    if operation:
        assignments = (
            NeoScorpionFuelAssignment.query.join(SortDateMission)
            .options(
                joinedload(NeoScorpionFuelAssignment.sort_date_mission),
                joinedload(NeoScorpionFuelAssignment.assigned_fueler),
                joinedload(NeoScorpionFuelAssignment.assigned_truck),
            )
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
        assignments_by_mission = {
            assignment.sort_date_mission_id: assignment for assignment in assignments
        }
        completed = _fuel_rows(
            operation,
            [assignment.sort_date_mission for assignment in assignments],
            assignments_by_mission=assignments_by_mission,
            fuel_work_states_by_assignment_tail=(
                _fuel_work_states_by_assignment_tail(assignments)
            ),
        )
    return {"operation": operation, "completed_rows": completed}


def _sort_fuel_totals(rows, events, fuel_density_lbs_per_gallon):
    """Current canonical departure loads and every physical sort event once."""
    missions = {row["mission"].id: row for row in rows}
    physical_events = {event.id: event for event in events}

    def total(values, unit, item):
        values = list(values)
        known = [value for value in values if value is not None]
        missing = len(values) - len(known)
        value = sum(known) if known or not values else None
        display = f"{value:,}" if value is not None else "—"
        if missing and known:
            display += "*"
        title = (f"{'Known subtotal; ' if known else ''}{missing} {item} missing."
                 if missing else "Current sort total")
        return {"value": value, "display": display, "unit": unit,
                "incomplete": bool(missing), "title": title}

    required_lbs = total((row["required_fuel_lbs"] for row in missions.values()),
                         "LBS", "fuel load values")
    invalid_density = False
    try:
        required_gallons = lbs_to_gallons(
            required_lbs["value"], fuel_density_lbs_per_gallon,
        )
    except (InvalidOperation, TypeError, ValueError):
        required_gallons = None
        invalid_density = True
        required_lbs["incomplete"] = True
        required_lbs["title"] = "Configure a valid fuel density to show fuel load gallons."
    required_lbs.update(
        value=required_gallons,
        display=(f"{required_gallons:,}" + ("*" if required_lbs["incomplete"] and
                not invalid_density else "")
                 if required_gallons is not None else "—"),
        unit="GAL",
    )
    return {
        "estimated": total((row["estimated_fuel_gallons"] for row in missions.values()),
                           "GAL", "estimated fuel values"),
        "transfer": total((event.transfer_fuel_gallons for event in physical_events.values()),
                          "GAL", "recorded transfer values"),
        "required": required_lbs,
    }


def _estimated_dispatch_fuel(mission, assignment, tail_fuel_state, remaining_lbs,
                             fuel_density, inbound_fallback_lbs):
    """Shared current-cycle estimate for Dispatch and sort reports."""
    follow_up = bool(assignment and int(assignment.current_cycle_number or 1) > 1)
    return estimate_fuel_demand_gallons(
        mission.planned_fuel_load,
        None if follow_up else tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None,
        fuel_density,
        measured_fob_lbs=(remaining_lbs if remaining_lbs is not None else
                         tail_fuel_state.fob_lbs if follow_up and tail_fuel_state else None),
        fallback_inbound_lbs=None if follow_up else inbound_fallback_lbs,
    )


def _fuel_report_current_rows(gateway, operation, settings):
    """Load only canonical estimate inputs in batches, without building a board."""
    assignments = _assignments_by_mission(operation)
    work_states = _fuel_work_states_by_assignment_tail(assignments.values())
    tail_fuel_states = _tail_fuel_states_by_tail(operation)
    density = (settings.fuel_density_lbs_per_gallon if settings
               else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON)
    fallback = (settings.planning_inbound_fuel_fallback_lbs
                if settings and settings.planning_inbound_fuel_fallback_lbs is not None
                else DEFAULT_PLANNING_INBOUND_FALLBACK_LBS)
    rows = []
    for mission in _departure_missions(operation):
        tail = _normalize_tail(mission.assigned_tail_number)
        assignment = assignments.get(mission.id)
        work = work_states.get((assignment.id, tail)) if assignment else None
        tanks = {tank.tank_code: tank for tank in work.tank_states} if work else {}
        remaining_lbs = _fuel_work_calculation(tank_layout_for_tail(tail), tanks,
                                             None, None)[1]
        estimate = _estimated_dispatch_fuel(mission, assignment, tail_fuel_states.get(tail),
                                            remaining_lbs, density, fallback)
        rows.append({"mission": mission, "estimated_fuel_gallons": estimate.gallons,
                     "required_fuel_lbs": mission.planned_fuel_load})
    return rows


def fuel_report_context(gateway):
    """Return one immutable report row per persisted physical fueling event."""
    operation = current_sort_operation(gateway)
    rows = []
    counts = {"fuel": 0, "uplift": 0, "defuel": 0}
    events = []
    if operation:
        events = (
            NeoScorpionFuelingEvent.query.options(
                joinedload(NeoScorpionFuelingEvent.fuel_assignment).joinedload(
                    NeoScorpionFuelAssignment.sort_date_mission
                ),
                joinedload(NeoScorpionFuelingEvent.fueler_user),
                joinedload(NeoScorpionFuelingEvent.fuel_truck),
            )
            .filter(
                NeoScorpionFuelingEvent.sort_date_operation_id == operation.id
            )
            .all()
        )
        events.sort(
            key=lambda event: (
                event.ended_at_utc
                or event.started_at_utc
                or event.created_at
                or datetime.min,
                event.id,
            )
        )
        for index, event in enumerate(events, start=1):
            assignment = event.fuel_assignment
            mission = assignment.sort_date_mission if assignment else None
            timezone = mission.timezone if mission and mission.timezone else "UTC"
            event_type = str(event.event_type or "fuel").strip().lower()
            if event_type in counts:
                counts[event_type] += 1
            rows.append(
                {
                    "number": index,
                    "event_id": event.id,
                    "event_type": event_type,
                    "event_type_label": event_type.upper(),
                    "cycle_number": int(event.cycle_number or 1),
                    "sequence_number": int(event.sequence_number or 1),
                    "flight_number": (
                        mission.flight_number if mission else "-"
                    ),
                    "destination": mission.destination if mission else "-",
                    "tail_number": event.tail_number,
                    "fueler_name": (
                        event.fueler_user.display_name
                        if event.fueler_user
                        else "UNASSIGNED"
                    ),
                    "truck_number": (
                        event.fuel_truck.truck_number
                        if event.fuel_truck
                        else "UNASSIGNED"
                    ),
                    "started_time": (
                        format_local_hhmm(event.started_at_utc, timezone)
                        if event.started_at_utc
                        else "-"
                    ),
                    "ended_time": (
                        format_local_hhmm(event.ended_at_utc, timezone)
                        if event.ended_at_utc
                        else "-"
                    ),
                    "transfer_fuel_gallons": event.transfer_fuel_gallons,
                    "required_fuel_display": (
                        format_display_thousands(event.required_fuel_lbs) or "-"
                    ),
                    "neo_fuel_display": (
                        format_display_thousands(event.neo_fuel_lbs) or "-"
                    ),
                }
            )
    settings = (NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
                if operation else None)
    density = (settings.fuel_density_lbs_per_gallon if settings
               else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON)
    totals = _sort_fuel_totals(
        _fuel_report_current_rows(gateway, operation, settings) if operation else [],
        events, density,
    )
    return {
        "operation": operation,
        "sort_fuel_totals": totals,
        "fuel_report_rows": rows,
        "fuel_report_summary": {
            "event_count": len(rows),
            "fuel_count": counts["fuel"],
            "uplift_count": counts["uplift"],
            "defuel_count": counts["defuel"],
            "total_transfer_gallons": totals["transfer"]["value"],
        },
    }


@dataclass(frozen=True)
class DispatchSaveResult:
    assignment: NeoScorpionFuelAssignment
    changed: bool
    assignment_changed: bool
    revision: int
    fuel_work_state: NeoScorpionFuelWorkState | None = None


@dataclass(frozen=True)
class DispatchAutosaveResult:
    changed: bool
    operation_id: int
    revision: int
    field_name: str
    display_value: str
    neo_fuel_presentation: dict | None = None


@dataclass(frozen=True)
class FuelerAssignmentUpdateAckResult:
    changed: bool
    acknowledged_version: int


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
class FuelCycleStartResult:
    changed: bool
    revision: int
    assignment: NeoScorpionFuelAssignment
    mission: SortDateMission
    fuel_work_state: NeoScorpionFuelWorkState


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


@dataclass(frozen=True)
class AssignmentPlanningSettings:
    setup_minutes: Decimal | None
    finishing_minutes: Decimal | None
    eta_safety_buffer_minutes: Decimal
    pump_rates_gallons_per_minute: dict[str, Decimal | None]

    def pump_rate_for(self, aircraft_type):
        return self.pump_rates_gallons_per_minute.get(aircraft_type)

    def is_complete_for(self, aircraft_type):
        return bool(
            aircraft_type in NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
            and self.setup_minutes is not None
            and self.finishing_minutes is not None
            and self.pump_rate_for(aircraft_type) is not None
        )


@dataclass(frozen=True)
class AssignmentPlanningSettingsSaveResult:
    changed: bool


def save_dispatch_row(gateway, form):
    return _save_dispatch_assignment(gateway, form, include_legacy_fields=True)


def save_dispatch_assignment(gateway, form):
    return _save_dispatch_assignment(gateway, form, include_legacy_fields=False)


def autosave_dispatch_field(
    gateway,
    mission_id,
    field_name,
    value,
    *,
    expected_value=None,
    now_utc=None,
    user=None,
):
    if field_name not in {"required_fuel", "inbound_fuel"}:
        raise ValueError("Select a supported Fuel Dispatch autosave field.")
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for NeoScorpion dispatch.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    mission = _departure_mission_for_operation(
        operation,
        _int_or_none(mission_id),
        for_update=True,
    )
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
    _validate_dispatch_assignment_editable(assignment, mission)
    if field_name == "inbound_fuel" and assignment and int(assignment.current_cycle_number or 1) > 1:
        raise ValueError("Original Inbound Fuel is read-only during follow-up cycles.")

    requested_lbs = display_thousands_to_lbs(value)
    expected_lbs = display_thousands_to_lbs(expected_value)
    tail_fuel_state = None
    if field_name == "required_fuel":
        current_lbs = mission.planned_fuel_load
    else:
        tail_number = _normalize_tail(mission.assigned_tail_number)
        if not tail_number:
            raise ValueError("Inbound Fuel cannot be saved until the mission has a tail.")
        tail_fuel_state = (
            NeoScorpionTailFuelState.query.filter_by(
                sort_date_operation_id=operation.id,
                tail_number=tail_number,
            )
            .with_for_update()
            .first()
        )
        current_lbs = tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None

    if current_lbs != expected_lbs and current_lbs != requested_lbs:
        raise ValueError(
            "Live data changed for this field. Refresh Fuel Dispatch and review the current value."
        )
    revision = int(asset_state.revision if asset_state else 0)
    if current_lbs == requested_lbs:
        return DispatchAutosaveResult(
            False,
            operation.id,
            revision,
            field_name,
            format_display_thousands(current_lbs),
            dispatch_neo_fuel_presentation(gateway, mission, assignment) if field_name == "required_fuel" else None,
        )

    if field_name == "required_fuel":
        from app.services.neoscorpion_pending_uplift import target_changed
        target_changed(gateway,user or current_user,operation,assignment,mission,current_lbs,requested_lbs,now_utc)
        change_message = _fuel_change_message(
            "Required Fuel",
            current_lbs,
            requested_lbs,
        )
    else:
        if tail_fuel_state is None:
            tail_fuel_state = NeoScorpionTailFuelState(
                sort_date_operation_id=operation.id,
                tail_number=_normalize_tail(mission.assigned_tail_number),
            )
            db.session.add(tail_fuel_state)
        tail_fuel_state.inbound_fuel_lbs = requested_lbs
        change_message = _fuel_change_message(
            "Inbound Fuel",
            current_lbs,
            requested_lbs,
        )

    _record_assigned_fueler_update(assignment, (change_message,), now_utc=now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return DispatchAutosaveResult(
        True,
        operation.id,
        int(asset_state.revision),
        field_name,
        format_display_thousands(requested_lbs),
        dispatch_neo_fuel_presentation(gateway, mission, assignment) if field_name == "required_fuel" else None,
    )


def acknowledge_fueler_assignment_update(
    gateway,
    user,
    assignment_id,
    update_version,
):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for Fuel Assignments.")
    assignment = (
        NeoScorpionFuelAssignment.query.filter_by(
            id=_int_or_none(assignment_id),
            sort_date_operation_id=operation.id,
            assigned_fueler_user_id=user.id,
        )
        .with_for_update()
        .first()
    )
    if assignment is None:
        raise ValueError("Fuel assignment was not found for this fueler.")
    requested_version = _int_or_none(update_version)
    if requested_version is None or requested_version < 0:
        raise ValueError("Select a valid assignment update to acknowledge.")
    current_version = int(assignment.fueler_update_version or 0)
    target_version = min(requested_version, current_version)
    acknowledged_version = int(assignment.fueler_update_acknowledged_version or 0)
    if target_version <= acknowledged_version:
        return FuelerAssignmentUpdateAckResult(False, acknowledged_version)
    assignment.fueler_update_acknowledged_version = target_version
    db.session.flush()
    return FuelerAssignmentUpdateAckResult(True, target_version)


def _save_dispatch_assignment(gateway, form, *, include_legacy_fields):
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
        .populate_existing()
        .first()
    )
    _check_dispatch_segment_reference(assignment,form)
    from app.services.neoscorpion_spear_decisions import prepare_scope as scope, observe
    spear_before = scope(operation, mission, assignment, int(asset_state.revision if asset_state else 0))
    assignment_created = assignment is None
    current_fueler_id = assignment.assigned_fueler_user_id if assignment else None
    current_truck_id = assignment.assigned_truck_id if assignment else None
    _validate_dispatch_assignment_editable(assignment, mission)
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

    fueler_change_requested = requested_fueler_id != current_fueler_id
    truck_change_requested = requested_truck_id != current_truck_id
    resource_change_requested = bool(
        fueler_change_requested or truck_change_requested
    )
    if assignment is not None and resource_change_requested:
        confirmed_tail = _normalize_tail(mission.assigned_tail_number)
        fuel_work_state = _locked_fuel_work_state_for_tail(
            assignment,
            confirmed_tail,
        )
        inherited_measurement = _validated_tail_swap_inherited_measurement(
            operation,
            assignment,
            mission,
            fuel_work_state,
        )
        if (
            _fuel_work_has_begun(fuel_work_state, assignment)
            and inherited_measurement is None
            and not _unstarted_follow_up_cycle(assignment, fuel_work_state)
        ):
            initial_truck_assignment = bool(
                not fueler_change_requested
                and current_fueler_id is not None
                and current_truck_id is None
                and requested_truck_id is not None
                and assignment.operational_status != "hold_review"
                and not (
                    fuel_work_state
                    and fuel_work_state.ended_early_at_utc is not None
                )
            )
            if not initial_truck_assignment:
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
    now_utc = datetime.utcnow()
    change_messages = []
    if include_legacy_fields:
        planned_fuel_load = display_thousands_to_lbs(form.get("required_fuel"))
        if mission.planned_fuel_load != planned_fuel_load:
            change_messages.append(
                _fuel_change_message(
                    "Required Fuel",
                    mission.planned_fuel_load,
                    planned_fuel_load,
                )
            )
            from app.services.neoscorpion_pending_uplift import target_changed
            target_changed(gateway,current_user,operation,assignment,mission,mission.planned_fuel_load,planned_fuel_load,now_utc)
            changed = True

        tail_number = _normalize_tail(mission.assigned_tail_number)
        if tail_number and not (assignment and int(assignment.current_cycle_number or 1) > 1):
            tail_fuel_state = NeoScorpionTailFuelState.query.filter_by(
                sort_date_operation_id=operation.id,
                tail_number=tail_number,
            ).first()
            inbound_fuel_lbs = display_thousands_to_lbs(form.get("inbound_fuel"))
            needs_tail_state = bool(
                tail_fuel_state is not None
                or inbound_fuel_lbs is not None
            )
            if tail_fuel_state is None and needs_tail_state:
                tail_fuel_state = NeoScorpionTailFuelState(
                    sort_date_operation_id=operation.id,
                    tail_number=tail_number,
                )
                db.session.add(tail_fuel_state)
                changed = True
            if tail_fuel_state is not None:
                if tail_fuel_state.inbound_fuel_lbs != inbound_fuel_lbs:
                    change_messages.append(
                        _fuel_change_message(
                            "Inbound Fuel",
                            tail_fuel_state.inbound_fuel_lbs,
                            inbound_fuel_lbs,
                        )
                    )
                    tail_fuel_state.inbound_fuel_lbs = inbound_fuel_lbs
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

    # Pending/Assigned/Review is legacy storage, not an operator-controlled
    # workflow. Keep it synchronized from the actual resource assignment while
    # visible Dispatch status is derived from canonical operational milestones.
    review_status = (
        "assigned"
        if requested_fueler_id is not None or requested_truck_id is not None
        else "pending"
    )
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
    assignment_audits = []
    for field_name, value in (
        ("assigned_fueler_user_id", requested_fueler_id),
        ("assigned_truck_id", requested_truck_id),
        ("review_status", review_status),
    ):
        old_value = getattr(assignment, field_name)
        if old_value != value:
            setattr(assignment, field_name, value)
            if field_name == "assigned_fueler_user_id":
                assignment.fueler_assigned_at_utc = now_utc if value is not None else None
                assignment.resource_assignment_by_user_id = getattr(current_user, "id", None)
                if getattr(current_user, "id", None):
                    assignment_audits.append(_new_fuel_audit(operation, assignment, current_user, "swap_fueler", field_name,
                        old_value, value, "Dispatcher recorded the Fueler assignment.", now_utc=now_utc))
            elif field_name == "assigned_truck_id":
                assignment.truck_assigned_at_utc = now_utc if value is not None else None
                assignment.resource_assignment_by_user_id = getattr(current_user, "id", None)
            changed = True
            assignment_changed = True
            if field_name == "assigned_truck_id":
                if getattr(current_user, "id", None):
                    assignment_audits.append(_new_fuel_audit(operation, assignment, current_user, "swap_truck", field_name,
                        old_value, value, "Dispatcher recorded the truck assignment.", now_utc=now_utc))
                change_messages.append(
                    f"Truck: {_resource_change_label(old_value)} -> "
                    f"{_resource_change_label(value)}"
                )

    fuel_work_state = _locked_current_fuel_work_state(assignment, mission)
    for audit in assignment_audits:
        audit.fuel_work_state = fuel_work_state
    if resource_change_requested and fuel_work_state and not _fuel_work_has_begun(fuel_work_state, assignment):
        fuel_work_state.physical_truck_id = requested_truck_id
        fuel_work_state.physical_fueler_user_id = requested_fueler_id
        if truck_change_requested:
            assignment.transfer_fuel_gallons = None
            fuel_work_state.physical_transfer_gallons = None
            fuel_work_state.truck_segment_started_at_utc = None
    if "apu_override_present" in form:
        confirmed_tail = _normalize_tail(mission.assigned_tail_number)
        fuel_work_state = _locked_fuel_work_state_for_tail(
            assignment,
            confirmed_tail,
        )
        if fuel_work_state is None or fuel_work_state.apu_running is not True:
            raise ValueError(
                "Fueler must confirm APU Running as Yes before an APU override can be set."
            )
        target_override_enabled = form.get("apu_override_enabled") == "1"
        target_override_allowance_lbs = None
        if target_override_enabled:
            target_override_allowance_lbs = display_thousands_to_lbs(
                form.get("apu_override_allowance")
            )
            if target_override_allowance_lbs is None:
                raise ValueError("Enter a nonnegative APU override allowance.")
        automatic_allowance_lbs = _automatic_apu_allowance_lbs(fuel_work_state)
        if automatic_allowance_lbs is None:
            raise ValueError("APU automatic allowance is incomplete.")
        target_effective_allowance_lbs = (
            target_override_allowance_lbs
            if target_override_enabled
            else automatic_allowance_lbs
        )
        current_override_enabled = bool(fuel_work_state.apu_override_enabled)
        current_override_allowance_lbs = (
            fuel_work_state.apu_override_allowance_lbs
            if current_override_enabled
            else None
        )
        current_effective_allowance_lbs = _effective_apu_allowance_lbs(
            fuel_work_state
        )
        if (
            target_override_enabled != current_override_enabled
            or target_override_allowance_lbs != current_override_allowance_lbs
            or target_effective_allowance_lbs != current_effective_allowance_lbs
        ):
            fuel_work_state.automatic_apu_allowance_lbs = automatic_allowance_lbs
            fuel_work_state.apu_override_enabled = target_override_enabled
            fuel_work_state.apu_override_allowance_lbs = (
                target_override_allowance_lbs
            )
            from app.services.neoscorpion_submission import invalidate
            invalidate(fuel_work_state)
            fuel_work_state.apu_allowance_lbs = target_effective_allowance_lbs
            tail_fuel_state = (
                NeoScorpionTailFuelState.query.filter_by(
                    sort_date_operation_id=operation.id,
                    tail_number=confirmed_tail,
                )
                .with_for_update()
                .first()
            )
            if tail_fuel_state is not None:
                tail_fuel_state.apu_lbs = target_effective_allowance_lbs
            change_messages.append(
                _apu_override_change_message(
                    current_override_enabled,
                    current_effective_allowance_lbs,
                    target_override_enabled,
                    target_effective_allowance_lbs,
                )
            )
            changed = True
            assignment_changed = True

    fueler_changed = current_fueler_id != requested_fueler_id
    if fueler_changed:
        _clear_assignment_update_notice(assignment)
    elif not assignment_created:
        _record_assigned_fueler_update(
            assignment,
            tuple(change_messages),
            now_utc=now_utc,
        )

    revision = int(asset_state.revision if asset_state else 0)
    if changed or assignment_created:
        asset_state = record_nightly_operational_change(asset_state, operation.id)
        revision = int(asset_state.revision)

    if changed or assignment_created:
        db.session.flush()
    if resource_change_requested:
        action = 'unassignment' if requested_fueler_id is None or requested_truck_id is None else ('initial_assignment' if assignment_created or not (current_fueler_id or current_truck_id) else 'reassignment')
        observe(gateway, assignment, spear_before, action, now=now_utc,
            reference=form.get('spear_decision_reference'))
    return DispatchSaveResult(
        assignment=assignment,
        changed=changed or assignment_created,
        assignment_changed=assignment_changed,
        revision=revision,
        fuel_work_state=fuel_work_state,
    )


def _validate_dispatch_assignment_editable(assignment, mission):
    if mission.fuel_status == "complete" or (
        assignment
        and (
            assignment.fuel_on_board_at_utc is not None
            or assignment.completed_at_utc is not None
            or assignment.review_status == "complete"
        )
    ):
        raise ValueError("Completed fuel assignments cannot be edited.")


def _record_assigned_fueler_update(assignment, change_messages, *, now_utc):
    messages = tuple(message for message in change_messages if message)
    if assignment is None or assignment.assigned_fueler_user_id is None or not messages:
        return False
    assignment.fueler_update_version = int(assignment.fueler_update_version or 0) + 1
    assignment.fueler_update_message = "; ".join(messages)
    assignment.fueler_update_at_utc = now_utc
    return True


def _clear_assignment_update_notice(assignment):
    assignment.fueler_update_acknowledged_version = int(
        assignment.fueler_update_version or 0
    )
    assignment.fueler_update_message = None
    assignment.fueler_update_at_utc = None


def _fuel_change_message(label, old_lbs, new_lbs):
    old_value = format_display_thousands(old_lbs) or "blank"
    new_value = format_display_thousands(new_lbs) or "blank"
    return f"{label}: {old_value} -> {new_value}"


def _apu_override_change_message(
    old_enabled,
    old_effective_lbs,
    new_enabled,
    new_effective_lbs,
):
    old_value = format_apu_display_thousands(old_effective_lbs) or "blank"
    new_value = format_apu_display_thousands(new_effective_lbs) or "blank"
    label = "APU Override" if new_enabled else "APU Override Cleared"
    return f"{label}: {old_value} -> {new_value}"


def _resource_change_label(value):
    return f"Truck #{value}" if value is not None else "Unassigned"


class FuelerDataConflict(ValueError):
    def __init__(self):
        super().__init__("DATA CHANGED · REVIEW CURRENT VALUES")


def fueler_edit_state(assignment, mission, work, tail, tanks):
    state = {
        "operation_id": assignment.sort_date_operation_id,
        "mission_id": mission.id,
        "cycle": int(assignment.current_cycle_number or 1),
        "tail": _normalize_tail(mission.assigned_tail_number),
        "required_fuel_lbs": work.physical_required_fuel_lbs if work and work.target_frozen else mission.planned_fuel_load,
        "assigned_fueler_user_id": assignment.assigned_fueler_user_id,
        "assigned_truck_id": assignment.assigned_truck_id,
        "apu_running": work.apu_running if work else None,
        "apu_source_tank_code": work.apu_source_tank_code if work else None,
        "apu_override_enabled": bool(work and work.apu_override_enabled),
        "apu_override_allowance": work.apu_override_allowance_lbs if work else None,
        "transfer_fuel_gallons": assignment.transfer_fuel_gallons,
        "notes": (work.notes or "") if work else "",
        "submission_version": int(work.fueler_submission_version or 0) if work else 0,
        "fueling_resume_version": int((work.performance_context or {}).get('fueling_resume_version',0)) if work else 0,
        "physical_sequence": work.physical_sequence_number if work else None,
        "work_ref": getattr(assignment, "active_work_state_id", None),
    }
    by_code = {tank.tank_code: tank for tank in tanks}
    for code, _label in tank_layout_for_tail(state["tail"]):
        tank = by_code.get(code)
        state[f"remaining_{code}"] = tank.remaining_lbs if tank else None
        state[f"actual_{code}"] = tank.actual_lbs if tank else None
    return state


def _fueler_expected(form, *, required=False):
    raw = form.get("expected")
    if raw is None and not required:
        return None  # Trusted service callers may perform a locked, current-state write.
    try:
        expected = json.loads(raw or "")
    except (ValueError, TypeError):
        raise ValueError("Reload Fueler Data before saving.") from None
    if not isinstance(expected, dict):
        raise ValueError("Reload Fueler Data before saving.")
    return expected


def _check_fueler_expected(expected, current, requested):
    if expected is None:
        return
    for key in ("operation_id", "mission_id", "cycle", "tail", "assigned_fueler_user_id", "assigned_truck_id"):
        if key not in expected or expected[key] != current[key]:
            raise FuelerDataConflict()
    if expected.get("physical_sequence") != current.get("physical_sequence") and "physical_sequence" in current:
        raise FuelerDataConflict()
    if "submission_version" in current and expected.get("submission_version", 0) != current["submission_version"]:
        raise FuelerDataConflict()
    if "fueling_resume_version" in current and expected.get("fueling_resume_version",0) != current["fueling_resume_version"]:
        raise FuelerDataConflict()
    if "work_ref" in current and expected.get("work_ref") != current["work_ref"]:
        raise FuelerDataConflict()
    for key, value in requested.items():
        if key not in expected or (current[key] != expected[key] and current[key] != value):
            raise FuelerDataConflict()


def _segment_views(assignment, mission, work):
    """Detached operational views: never write source-tail data to replacement."""
    values = {column.name: getattr(assignment, column.name) for column in assignment.__table__.columns}
    values.update(assigned_truck_id=work.physical_truck_id,
        assigned_fueler_user_id=work.physical_fueler_user_id,
        current_cycle_number=work.physical_cycle_number,
        current_cycle_type=work.physical_cycle_type,
        transfer_fuel_gallons=work.physical_transfer_gallons,
        completed_at_utc=work.physical_finalized_at_utc,
        fuel_on_board_at_utc=None,
        review_status="complete" if work.physical_finalized_at_utc else "pending",
        confirmed_tail_number=work.tail_number)
    view = SimpleNamespace(**values)
    view.assigned_truck = work.physical_truck
    view.assigned_fueler = work.physical_fueler
    view._source_work = work
    view._parent_assignment = assignment
    view._detached_segment = (work.id != assignment.active_work_state_id or work.tail_number != _normalize_tail(mission.assigned_tail_number)
        or work.physical_cycle_number != int(assignment.current_cycle_number or 1)
        or work.physical_truck_id != assignment.assigned_truck_id)
    if work.id == assignment.active_work_state_id and work.tail_number == _normalize_tail(mission.assigned_tail_number):
        # Operational assignment changes before/within this visit remain subject
        # to existing access checks. Immutable physical attribution stays on work.
        view.assigned_fueler_user_id = assignment.assigned_fueler_user_id
        view.assigned_truck_id = assignment.assigned_truck_id
        view.assigned_fueler = assignment.assigned_fueler
        view.assigned_truck = assignment.assigned_truck
    facts = work.performance_context or {}
    view.fueler_assigned_at_utc = _segment_timestamp(facts.get("fueler_assigned_at"))
    view.truck_assigned_at_utc = _segment_timestamp(facts.get("truck_assigned_at"))
    mission_view = SimpleNamespace(**({c.name: getattr(mission, c.name) for c in mission.__table__.columns}
        if hasattr(mission, "__table__") else vars(mission)))
    mission_view.assigned_tail_number = work.tail_number
    mission_view.planned_fuel_load = work.physical_required_fuel_lbs
    if not view._detached_segment and not work.target_frozen:
        mission_view.planned_fuel_load = mission.planned_fuel_load
    if facts.get("planned_departure_at"):
        mission_view.planned_datetime_utc = _segment_timestamp(facts["planned_departure_at"])
    mission_view.fuel_status = "complete" if work.physical_finalized_at_utc else "pending"
    mission_view.fuel_completed_at_utc = work.physical_finalized_at_utc
    if not view._detached_segment:
        # Closing a segment (END EARLY/FOB/history) is not itself evidence that
        # the current mission completed. Preserve the parent's actual decision.
        view.completed_at_utc=assignment.completed_at_utc
        view.fuel_on_board_at_utc=assignment.fuel_on_board_at_utc
        view.review_status=assignment.review_status
        mission_view.fuel_status=mission.fuel_status
        mission_view.fuel_completed_at_utc=mission.fuel_completed_at_utc
    view.sort_date_mission = mission_view
    view.active_work_state_id = work.id
    return view, mission_view


def _segment_timestamp(value):
    return datetime.fromisoformat(value) if value else None


def _locked_source_segment(assignment, mission, work_id, sequence, user, *, dispatcher=False):
    work = NeoScorpionFuelWorkState.query.filter_by(id=_int_or_none(work_id),
        fuel_assignment_id=assignment.id).with_for_update().populate_existing().first()
    if (work is None or work.physical_sequence_number is None or
            str(work.physical_sequence_number) != str(sequence)):
        raise FuelerDataConflict()
    if work.physical_cycle_number is None:
        raise ValueError("REVIEW REQUIRED: legacy physical cycle identity is unknown; do not guess its transfer allocation.")
    is_current = work.id == assignment.active_work_state_id and work.tail_number == _normalize_tail(mission.assigned_tail_number)
    owner_id = assignment.assigned_fueler_user_id if is_current else work.physical_fueler_user_id
    if not dispatcher and owner_id != user.id:
        raise ValueError("This physical segment belongs to another Fueler.")
    return (*_segment_views(assignment, mission, work), work)


def _bind_physical_segment(assignment, mission, work, *, legacy=False):
    """Allocate under the existing operation/assignment lock; no history rewrite."""
    legacy = legacy or work.physical_transfer_review
    if work.off_at_utc or work.ended_early_at_utc or work.physical_finalized_at_utc:
        raise ValueError("This physical visit is closed. Start a new visit; never reuse its segment.")
    if (work.performance_context or {}).get("on_at"):
        if (work.physical_truck_id != assignment.assigned_truck_id or
                work.physical_cycle_number != int(assignment.current_cycle_number or 1)):
            raise ValueError("Finalize the outstanding physical segment before changing its identity.")
    if work.visit_number is None:
        maximum = db.session.query(db.func.max(NeoScorpionFuelWorkState.visit_number)).filter_by(fuel_assignment_id=assignment.id).scalar() or 0
        work.visit_number = maximum + 1
    work.physical_sequence_number = work.visit_number
    assignment.active_work_state_id = work.id
    work.physical_cycle_number = int(assignment.current_cycle_number or 1)
    work.physical_cycle_type = _assignment_cycle_type(assignment)
    work.physical_truck_id = assignment.assigned_truck_id
    work.physical_fueler_user_id = assignment.assigned_fueler_user_id
    if not work.target_frozen:
        work.physical_required_fuel_lbs = mission.planned_fuel_load
    work.physical_transfer_gallons = None if legacy else assignment.transfer_fuel_gallons
    work.physical_transfer_review = legacy
    work.physical_finalized_at_utc = None
    work.physical_finalized_by_user_id = None


def save_fueler_entry(gateway, user, form, *, now_utc=None, dispatcher=False, require_expected=False):
    expected = _fueler_expected(form, required=require_expected)
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
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not assignment_row:
        raise ValueError("Fuel assignment was not found for this fueler.")

    assignment, mission = assignment_row
    source_work = None
    if form.get("physical_work_id"):
        assignment, mission, source_work = _locked_source_segment(assignment, mission,
            form.get("physical_work_id"), form.get("physical_sequence"), user, dispatcher=dispatcher)
    elif not dispatcher and assignment.assigned_fueler_user_id != user.id:
        raise ValueError("Fuel assignment was not found for this fueler.")
    _check_fueler_expected(expected, {
        "operation_id": operation.id, "mission_id": mission.id,
        "cycle": int(assignment.current_cycle_number or 1),
        "tail": _normalize_tail(mission.assigned_tail_number),
        "assigned_fueler_user_id": assignment.assigned_fueler_user_id,
        "assigned_truck_id": assignment.assigned_truck_id,
        "work_ref": assignment.active_work_state_id,
    }, {})
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

    fuel_work_state = source_work or current_work(assignment, tail_number, lock=True)
    if source_work is not None:
        fuel_work_state = source_work
    elif _normalize_tail(assignment.confirmed_tail_number) not in ("", tail_number):
        assignment.transfer_fuel_gallons = fuel_work_state.physical_transfer_gallons if fuel_work_state else None
    tank_states = []
    if fuel_work_state is not None:
        tank_states = (
            NeoScorpionFuelTankState.query.filter_by(
                fuel_work_state_id=fuel_work_state.id,
            )
            .with_for_update()
            .populate_existing()
            .all()
        )
    tank_states_by_code = {state.tank_code: state for state in tank_states}

    tail_fuel_state = (
        NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    current = fueler_edit_state(assignment, mission, fuel_work_state, tail_fuel_state, tank_states)
    submitted = {}
    for key in current:
        if key not in form:
            continue
        if key.startswith(("remaining_", "actual_")) or key == "apu_override_allowance":
            submitted[key] = display_thousands_to_lbs(form.get(key))
        elif key == "apu_running":
            choices = {"not_confirmed": None, "no": False, "yes": True}
            if form.get(key) not in choices:
                raise ValueError("Select a valid APU Running status.")
            submitted[key] = choices[form.get(key)]
        elif key == "apu_override_enabled":
            submitted[key] = form.get(key) == "1"
        elif key == "transfer_fuel_gallons":
            submitted[key] = _int_or_none(form.get(key))
        elif key == "notes":
            submitted[key] = (form.get(key) or "").strip()
        elif key == "apu_source_tank_code":
            submitted[key] = (form.get(key) or "").strip() or None
    _check_fueler_expected(expected, current, submitted)
    if assignment.completed_at_utc is not None and all(current.get(key) == value for key,value in submitted.items()):
        return FuelerSaveResult(False, int(asset_state.revision if asset_state else 0), tail_fuel_state, fuel_work_state)
    _validate_fueler_work_access(assignment, mission, fuel_work_state)
    if fuel_work_state and fuel_work_state.physical_transfer_review and "transfer_fuel_gallons" in form:
        if not dispatcher or not (form.get("segment_review_reason") or "").strip():
            raise ValueError("REVIEW REQUIRED: dispatcher must verify segment T/F with a reason.")
        fuel_work_state.physical_transfer_review = False
        _new_fuel_audit(operation, assignment, user, "segment_transfer_review", "transfer_fuel_gallons",
            (str(assignment._parent_assignment.transfer_fuel_gallons) if source_work is not None and not assignment._detached_segment
             else str(assignment.transfer_fuel_gallons) if source_work is None else None),
            form.get("transfer_fuel_gallons"), form.get("segment_review_reason"),
            fuel_work_state=fuel_work_state, now_utc=now_utc)

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
    target_automatic_apu_allowance_lbs = _automatic_apu_allowance_lbs(
        fuel_work_state
    )
    target_apu_override_enabled = bool(
        fuel_work_state
        and fuel_work_state.apu_running is True
        and fuel_work_state.apu_override_enabled
    )
    target_apu_override_allowance_lbs = (
        fuel_work_state.apu_override_allowance_lbs
        if target_apu_override_enabled
        else None
    )
    target_apu_rate = (
        fuel_work_state.applied_apu_rate_thousand_lbs_per_hour
        if fuel_work_state
        else None
    )
    if apu_changed:
        if target_apu_running is None:
            target_apu_confirmed_at_utc = None
            target_automatic_apu_allowance_lbs = None
            target_apu_override_enabled = False
            target_apu_override_allowance_lbs = None
            target_apu_rate = None
        elif target_apu_running is False:
            target_apu_confirmed_at_utc = now_utc
            target_automatic_apu_allowance_lbs = 0
            target_apu_override_enabled = False
            target_apu_override_allowance_lbs = None
            target_apu_rate = None
        else:
            if aircraft_type not in NEOSCORPION_APU_AIRCRAFT_TYPES:
                raise ValueError("APU allowance is not configured for this aircraft.")
            target_apu_rate = _effective_apu_rate(gateway.id, aircraft_type)
            target_apu_confirmed_at_utc = now_utc
            target_automatic_apu_allowance_lbs = calculate_apu_allowance_lbs(
                mission.planned_datetime_utc,
                operation.window_minutes,
                now_utc,
                target_apu_rate,
            )
    elif target_apu_running is True and target_automatic_apu_allowance_lbs is None:
        # A legacy pre-override row may not have the new automatic snapshot.
        # Preserve its current allowance instead of recalculating the clock.
        target_automatic_apu_allowance_lbs = (
            fuel_work_state.apu_allowance_lbs if fuel_work_state else None
        )

    if target_apu_running is True and "apu_override_present" in form:
        target_apu_override_enabled = (form.get("apu_override_enabled") == "1"
                                       if "apu_override_enabled" in form or expected is None else target_apu_override_enabled)
        if target_apu_override_enabled:
            if "apu_override_allowance" in form:
                target_apu_override_allowance_lbs = display_thousands_to_lbs(
                    form.get("apu_override_allowance")
                )
            if target_apu_override_allowance_lbs is None:
                raise ValueError("Enter a nonnegative APU override allowance.")
        else:
            target_apu_override_allowance_lbs = None
    elif target_apu_running is not True:
        target_apu_override_enabled = False
        target_apu_override_allowance_lbs = None

    target_apu_allowance_lbs = (
        target_apu_override_allowance_lbs
        if target_apu_running is True and target_apu_override_enabled
        else target_automatic_apu_allowance_lbs
    )
    current_automatic_apu_allowance_lbs = _automatic_apu_allowance_lbs(
        fuel_work_state
    )
    current_apu_override_enabled = bool(
        fuel_work_state
        and fuel_work_state.apu_running is True
        and fuel_work_state.apu_override_enabled
    )
    current_apu_override_allowance_lbs = (
        fuel_work_state.apu_override_allowance_lbs
        if current_apu_override_enabled
        else None
    )
    apu_override_changed = any(
        (
            target_automatic_apu_allowance_lbs
            != current_automatic_apu_allowance_lbs,
            target_apu_override_enabled != current_apu_override_enabled,
            target_apu_override_allowance_lbs
            != current_apu_override_allowance_lbs,
            target_apu_allowance_lbs
            != (fuel_work_state.apu_allowance_lbs if fuel_work_state else None),
        )
    )
    target_apu_lbs = target_apu_allowance_lbs
    target_notes = ((form.get("notes") or "").strip() if "notes" in form
                    else ((fuel_work_state.notes or "") if fuel_work_state else ""))
    target_transfer_gallons = (_int_or_none(form.get("transfer_fuel_gallons"))
                               if "transfer_fuel_gallons" in form else assignment.transfer_fuel_gallons)

    targets = {
        "apu_running": target_apu_running,
        "apu_source_tank_code": target_apu_source_tank_code,
        "apu_override_enabled": target_apu_override_enabled,
        "apu_override_allowance": target_apu_override_allowance_lbs,
        "notes": target_notes,
        "transfer_fuel_gallons": target_transfer_gallons,
    }
    for code, (remaining, actual) in final_tank_values.items():
        targets[f"remaining_{code}"] = remaining
        targets[f"actual_{code}"] = actual
    # Include implicit APU resets as well as explicitly submitted fields.
    requested = {key: value for key, value in targets.items()
                 if key in form or value != current[key]}
    if "apu_override_present" in form:
        requested.update({key: targets[key] for key in
                          ("apu_override_enabled", "apu_override_allowance")})
    _check_fueler_expected(expected, current, requested)

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
        final_tank_values["ctr"][1]
        if aircraft_type == "A300" and "ctr" in final_tank_values
        else None
    )

    current_apu_lbs = tail_fuel_state.apu_lbs if tail_fuel_state else None
    current_notes = (fuel_work_state.notes or "") if fuel_work_state else ""
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
        or apu_override_changed
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

    if tank_changed or apu_changed or apu_source_changed or apu_override_changed or transfer_changed or current_notes != target_notes:
        if fuel_work_state is None:
            fuel_work_state = new_work(assignment, tail_number, user, now_utc)
            fuel_work_state.physical_required_fuel_lbs = mission.planned_fuel_load
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
            fuel_work_state.applied_apu_rate_thousand_lbs_per_hour = target_apu_rate
        if apu_changed or apu_override_changed:
            fuel_work_state.automatic_apu_allowance_lbs = (
                target_automatic_apu_allowance_lbs
            )
            fuel_work_state.apu_override_enabled = target_apu_override_enabled
            fuel_work_state.apu_override_allowance_lbs = (
                target_apu_override_allowance_lbs
            )
            fuel_work_state.apu_allowance_lbs = target_apu_allowance_lbs
        if apu_changed or apu_source_changed:
            fuel_work_state.apu_source_tank_code = target_apu_source_tank_code
        _capture_resource_data_milestones(assignment, fuel_work_state, tank_layout, tank_states_by_code, now_utc,
                                          preparation_only=True)

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
        if "notes" in form:
            if tail_fuel_state.notes and tail_fuel_state.notes != target_notes:
                # Archive the compatibility field before replacing it. This is
                # evidence of this preservation action, not historical note
                # ownership: old mission/cycle/visit provenance is unverified.
                _new_fuel_audit(operation,assignment,user,"segment_transfer","legacy_tail_notes",
                    tail_fuel_state.notes,None,
                    f"Preserved shared-tail note for {tail_number}; historical mission/cycle/visit provenance unverified.",
                    now_utc=now_utc)
            tail_fuel_state.notes = target_notes

    if fuel_work_state is not None and "notes" in form:
        fuel_work_state.notes = target_notes
    assignment.transfer_fuel_gallons = target_transfer_gallons
    if transfer_changed and fuel_work_state:
        from app.services.neoscorpion_inventory import on_transfer_entry
        on_transfer_entry(operation, assignment, fuel_work_state, user)
    if source_work is not None and not assignment._detached_segment:
        assignment._parent_assignment.transfer_fuel_gallons = target_transfer_gallons
    if fuel_work_state is not None:
        fuel_work_state.physical_transfer_gallons = target_transfer_gallons
    entry_changes={key:value for key,value in requested.items() if current.get(key)!=value
        and (key.startswith(("remaining_","actual_","apu_")) or key in ("transfer_fuel_gallons", "notes"))}
    if fuel_work_state is not None and any(key != "notes" for key in entry_changes):
        from app.services.neoscorpion_submission import invalidate
        invalidate(fuel_work_state)
    if fuel_work_state is not None and entry_changes:
        _new_fuel_audit(operation,assignment,user,"segment_transfer","fuel_entry",
            json.dumps({key:current.get(key) for key in entry_changes},sort_keys=True),
            json.dumps(entry_changes,sort_keys=True),"Segment fuel entry; tank/APU quantities are pounds.",
            fuel_work_state=fuel_work_state,now_utc=now_utc)
    if fuel_work_state is not None:
        if (fuel_work_state.preparation_received_at_utc is None and tank_layout
                and all(code in tank_states_by_code and tank_states_by_code[code].remaining_lbs is not None for code,_ in tank_layout)
                and fuel_work_state.apu_running is not None
                and _effective_apu_allowance_lbs(fuel_work_state) is not None
                and _apu_source_is_valid(fuel_work_state,tank_layout)):
            fuel_work_state.preparation_received_at_utc = now_utc
        actuals_complete = bool(tank_layout) and all(
            code in tank_states_by_code and tank_states_by_code[code].actual_lbs is not None
            for code, _label in tank_layout)
        if actuals_complete and (fuel_work_state.actuals_received_at_utc is None or
                                any(key.startswith("actual_") and current.get(key) != value
                                    for key, value in submitted.items())):
            fuel_work_state.actuals_received_at_utc = now_utc
        elif not actuals_complete:
            fuel_work_state.actuals_received_at_utc = None
        _capture_resource_data_milestones(assignment, fuel_work_state, tank_layout, tank_states_by_code, now_utc,
                                          data_changed=any(key != "notes" for key in entry_changes))
        _capture_physical_forecast(gateway, assignment, mission, fuel_work_state, now_utc)
        _record_defuel_service_requirement(operation,assignment,fuel_work_state,user,now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelerSaveResult(
        changed=True,
        revision=int(asset_state.revision),
        tail_fuel_state=tail_fuel_state,
        fuel_work_state=fuel_work_state,
    )


def mark_ready_for_fuel(gateway, user, assignment_id, *, now_utc=None):
    """Record the operational Ready for Fuel milestone without forcing a workflow."""
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for Ready for Fuel.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation, assignment_id, action_label="READY FOR FUEL"
    )
    _validate_precompletion_assignment(assignment, mission, "READY FOR FUEL")
    if assignment.ready_for_fuel_at_utc is not None:
        return FuelInterruptionResult(
            False, int(asset_state.revision), assignment, None
        )
    assignment.ready_for_fuel_at_utc = now_utc
    assignment.ready_for_fuel_by_user_id = getattr(user, "id", None)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(True, int(asset_state.revision), assignment, None)


def confirm_fueler_truck_on(gateway, user, assignment_id, *, expected_cycle,
                            expected_tail, expected_truck_id, now_utc=None, expected_work_ref=None,
                            require_work_ref=False, resume_off_at=None):
    """Record a Fueler's physical arrival once for this cycle and truck segment."""
    operation = current_sort_operation(gateway)
    if operation is None:
        raise ValueError("No current sort operation is available for TRUCK ON.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation, assignment_id, action_label="TRUCK ON",
    )
    if assignment.assigned_fueler_user_id != user.id:
        raise ValueError("This fuel assignment belongs to another Fueler.")
    if expected_cycle is None or expected_tail is None or expected_truck_id is None:
        raise ValueError("Refresh before confirming TRUCK ON.")
    _validate_cycle_request(assignment, mission, expected_cycle, expected_tail)
    if str(assignment.assigned_truck_id or "") != str(expected_truck_id or ""):
        raise ValueError("Assigned truck changed. Refresh before TRUCK ON.")
    work = _locked_current_fuel_work_state(assignment, mission)
    if require_work_ref and str(assignment.active_work_state_id or "") != str(expected_work_ref or ""):
        # A retry of the very first ON may still carry the pre-allocation empty
        # reference. It is safe only for that initial, still-ON visit. Never let
        # an empty/old form address a subsequent physical visit.
        initial_on_retry = (not expected_work_ref and work is not None
            and work.visit_number == 1 and work.manual_on_at_utc is not None
            and work.created_at == work.manual_on_at_utc and work.off_at_utc is None)
        if not initial_on_retry:
            raise FuelerDataConflict()
    _validate_fueler_work_access(assignment, mission, work)
    if work and work.continuation_review_reason:
        raise ValueError(work.continuation_review_reason)
    if resume_off_at is not None:
        from app.services.neoscorpion_resume import resume_same_visit
        return resume_same_visit(gateway,user,operation,asset_state,assignment,mission,work,
                                 resume_off_at,now_utc)
    if work and work.off_at_utc is not None:
        raise ValueError("Fueler is already OFF for this cycle.")
    if assignment.assigned_truck_id is None:
        raise ValueError("Assign a truck before TRUCK ON.")
    _validate_nightly_truck_assignment(gateway, operation, assignment.assigned_truck_id)
    cycle = int(assignment.current_cycle_number or 1)
    if (work and work.manual_on_at_utc is not None
            and work.manual_on_cycle_number == cycle
            and work.manual_on_truck_id == assignment.assigned_truck_id):
        return False, int(asset_state.revision if asset_state else 0), work

    assignments = _assignments_by_mission(operation)
    works = _fuel_work_states_by_assignment_tail(assignments.values())
    selections = {item.fuel_truck_id: item for item in NeoScorpionSortTruck.query.filter_by(
        sort_date_operation_id=operation.id).all()}
    records, releases = _fuel_start_records(operation, assignments.values(), works,
                                            _parking_by_tail(operation), selections)
    record = next(item for item in records if item["id"] == assignment.id)
    settings = effective_spear_settings(NeoScorpionSettings.query.filter_by(
        gateway_id=gateway.id).first())
    gate = truck_start_gate(record, records, releases, settings, at=now_utc)
    # A deliberate physical arrival can override an estimate, not a confirmed
    # resource conflict or an unavailable truck.
    if gate["reason"] in {"TRUCK STILL FUELING", "TRUCK UNAVAILABLE", "DEFUEL RECOVERY REVIEW", "Needs Fueler", "Needs Truck"}:
        raise ValueError(gate["reason"])
    if work is None:
        work = new_work(assignment, _normalize_tail(mission.assigned_tail_number), user, now_utc)
    work.manual_on_at_utc = now_utc
    work.manual_on_by_user_id = user.id
    work.manual_on_truck_id = assignment.assigned_truck_id
    work.manual_on_cycle_number = cycle
    work.truck_segment_started_at_utc = now_utc
    _snapshot_physical_start(gateway, assignment, mission, work, now_utc, source="manual", actor=user)
    _capture_physical_forecast(gateway, assignment, mission, work, now_utc)
    db.session.flush()
    _new_fuel_audit(operation, assignment, user, "truck_on", "manual_on_at_utc",
                    None, now_utc.isoformat(),
                    f"Fueler confirmed truck {assignment.assigned_truck_id} physically ON for cycle {cycle}.",
                    fuel_work_state=work, now_utc=now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return True, int(asset_state.revision), work


def begin_physical_visit(gateway, user, assignment_id, *, expected, now_utc=None, dispatcher=False,
                         separate_visit_confirmed=False):
    operation = current_sort_operation(gateway)
    if operation is None:
        raise ValueError("No current sort for a physical visit.")
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(operation, assignment_id, action_label="NEW PHYSICAL VISIT")
    if not dispatcher and assignment.assigned_fueler_user_id != user.id:
        raise ValueError("This fuel assignment belongs to another Fueler.")
    if assignment.operational_status == "hold_review":
        raise ValueError("Resolve HOLD/REVIEW before beginning another visit.")
    if assignment.fuel_on_board_at_utc is not None:
        raise ValueError("Fuel On Board completion cannot start another physical visit.")
    work = current_work(assignment, _normalize_tail(mission.assigned_tail_number), lock=True)
    _check_fueler_expected(expected, {"operation_id":operation.id,"mission_id":mission.id,
        "cycle":int(assignment.current_cycle_number or 1),"tail":_normalize_tail(mission.assigned_tail_number),
        "assigned_fueler_user_id":assignment.assigned_fueler_user_id,"assigned_truck_id":assignment.assigned_truck_id,
        "work_ref":assignment.active_work_state_id,"physical_sequence":work.physical_sequence_number if work else None}, {})
    if work is None or not (work.off_at_utc or work.ended_early_at_utc or work.physical_finalized_at_utc):
        raise ValueError("Record physical TRUCK OFF before beginning another visit.")
    if not dispatcher and not work.physical_finalized_at_utc:
        from app.services.neoscorpion_submission import state as submission_state
        submitted = submission_state(assignment, mission, work, _locked_tank_states(work),
            on_trusted=bool(work.manual_on_at_utc or work.confirmed_on_at_utc))["fueler_submitted"]
        # A checkbox cannot turn an accidental OFF into separate physical work.
        # Require the valid receipt, not a stale/invalidated submission timestamp.
        if not submitted:
            raise ValueError("Use TRUCK ON / RESUME FUELING for this unfinished visit. "
                             "Submit COMPLETE ASSIGNMENT before starting a new visit.")
    now = now_utc or datetime.utcnow()
    fresh = new_work(assignment, work.tail_number, user, now)
    fresh.physical_required_fuel_lbs = mission.planned_fuel_load
    assignment.completed_at_utc = None
    assignment.completed_by_user_id = None
    assignment.fuel_on_board_at_utc = None
    assignment.review_status = "assigned"
    mission.fuel_status = "assigned"
    mission.fuel_completed_at_utc = None
    _new_fuel_audit(operation, assignment, user, "segment_transfer", "physical_visit", work.id, fresh.id,
        "Started a new preparing physical visit; prior segment was not recycled.",fuel_work_state=fresh,now_utc=now)
    state = record_nightly_operational_change(state, operation.id)
    db.session.flush()
    return FuelerSaveResult(True,int(state.revision),None,fresh)


def submit_fueler_assignment(gateway,user,assignment_id,*,expected,now_utc=None,physical_work_id=None,physical_sequence=None):
    # Pre-hotfix pages have no resume token. They still represent version zero,
    # never permission to submit stale evidence after an actual resumption.
    if expected is not None:
        expected=dict(expected);expected.setdefault('fueling_resume_version',0)
    operation=current_sort_operation(gateway)
    if operation is None:raise ValueError("No current sort operation.")
    operation,asset_state=lock_nightly_asset_scope_for_mutation(operation)
    parent,mission=_locked_current_fuel_assignment(operation,assignment_id,action_label="COMPLETE ASSIGNMENT")
    assignment=parent
    work=None
    if physical_work_id is not None:
        assignment,mission,work=_locked_source_segment(parent,mission,physical_work_id,physical_sequence,user)
    if assignment.assigned_fueler_user_id != user.id:
        raise ValueError("Only the assigned Fueler can COMPLETE ASSIGNMENT.")
    work=work or current_work(parent,_normalize_tail(mission.assigned_tail_number),lock=True)
    _validate_fueler_work_access(assignment,mission,work)
    tanks=_locked_tank_states(work)
    current=fueler_edit_state(assignment,mission,work,None,tanks)
    if expected is None:raise FuelerDataConflict()
    from app.services.neoscorpion_submission import evidence
    # A lost response/double tap may carry the baseline immediately preceding
    # the accepted receipt. Only that exact, unchanged visit is idempotent;
    # material corrections increment the version and revoke its fingerprint.
    if (work and work.fueler_submitted_at_utc
            and work.fueler_submission_evidence == evidence(assignment,mission,work,tanks)
            and all(expected.get(k) == v for k,v in current.items() if k not in {"notes","submission_version"})
            and expected.get("submission_version") in {current["submission_version"],current["submission_version"]-1}):
        return FuelerSaveResult(False,int(asset_state.revision if asset_state else 0),None,work)
    _check_fueler_expected(expected,current,{k:v for k,v in current.items() if k != "notes"})
    if any(k not in expected or expected[k] != v for k,v in current.items() if k not in {"notes"}):
        raise FuelerDataConflict()
    # Physical disconnection is an independent, explicit action. Submission
    # must never manufacture it, even when all fuel data is already valid.
    context=fueler_context(gateway,user,assignment_id=parent.id,
        physical_work_id=physical_work_id,physical_sequence=physical_sequence,include_timing=False)
    row=next((r for r in context['rows'] if r['physical_work_id']==(work.id if work else None)),None)
    if not row or not row['fueler_completion_ready']:
        raise ValueError(row['fueler_completion_reason'] if row else "Physical work is not eligible for submission.")
    from app.services.neoscorpion_submission import evidence
    fingerprint=evidence(assignment,mission,work,tanks)
    if work.fueler_submitted_at_utc and work.fueler_submission_evidence==fingerprint:
        return FuelerSaveResult(False,int(asset_state.revision if asset_state else 0),None,work)
    now=now_utc or datetime.utcnow()
    work.fueler_submission_version=int(work.fueler_submission_version or 0)+1
    work.fueler_submitted_at_utc=now
    work.fueler_submitted_by_user_id=user.id
    work.fueler_submission_evidence=evidence(assignment,mission,work,tanks)
    _new_fuel_audit(operation,assignment,user,"segment_transfer","fueler_submission",None,
        work.fueler_submission_evidence,"Fueler submitted the exact physical visit/cycle for Dispatcher review.",
        fuel_work_state=work,now_utc=now)
    asset_state=record_nightly_operational_change(asset_state,operation.id)
    db.session.flush()
    if work.id == parent.active_work_state_id:
        from app.services.neoscorpion_pending_uplift import activate_after_submission
        activate_after_submission(gateway,user,operation,parent,parent.sort_date_mission,now)
    return FuelerSaveResult(True,int(asset_state.revision),None,work)


def mark_fueler_off(gateway, user, assignment_id, *, now_utc=None, dispatcher=False, expected=None, defer_completion=False,
                    physical_work_id=None, physical_sequence=None):
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
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
        )
        .with_for_update()
        .populate_existing()
        .first()
    )
    if not assignment_row:
        raise ValueError("Fuel assignment was not found for this fueler.")

    assignment, mission = assignment_row
    source_work = None
    if physical_work_id is not None:
        assignment, mission, source_work = _locked_source_segment(assignment, mission,
            physical_work_id, physical_sequence, user, dispatcher=dispatcher)
    elif not dispatcher and assignment.assigned_fueler_user_id != user.id:
        raise ValueError("Fuel assignment was not found for this fueler.")
    tail_number = _normalize_tail(mission.assigned_tail_number if mission else "")
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")

    fuel_work_state = source_work or current_work(assignment, tail_number, lock=True)
    if fuel_work_state and fuel_work_state.target_frozen and source_work is None:
        _visit_assignment, mission = _segment_views(assignment,mission,fuel_work_state)
    if expected is not None:
        _check_fueler_expected(expected, {
            "operation_id": operation.id, "mission_id": mission.id,
            "cycle": int(assignment.current_cycle_number or 1), "tail": tail_number,
            "assigned_fueler_user_id": assignment.assigned_fueler_user_id,
            "assigned_truck_id": assignment.assigned_truck_id,
            "work_ref": assignment.active_work_state_id,
        }, {})
    if source_work is not None:
        fuel_work_state = source_work
    if fuel_work_state is None:
        raise ValueError("Confirm physical TRUCK ON before TRUCK OFF.")
    if fuel_work_state.off_at_utc is not None:
        return FuelerOffResult(
            changed=False,
            revision=int(asset_state.revision if asset_state else 0),
            fuel_work_state=fuel_work_state,
        )
    if expected is not None and int(expected.get('fueling_resume_version',0)) != int((fuel_work_state.performance_context or {}).get('fueling_resume_version',0)):
        raise FuelerDataConflict()
    _validate_fueler_work_access(assignment, mission, fuel_work_state)

    if assignment.assigned_truck_id is None:
        if assignment.transfer_fuel_gallons not in (None, 0):
            raise ValueError("Assign the truck used for positive T/F before OFF.")
        raise ValueError("No physical truck is assigned. Use Dispatcher Fuel On Board verification; do not record TRUCK OFF.")

    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    if expected is not None:
        _check_fueler_expected(expected, {
            "operation_id": operation.id, "mission_id": mission.id,
            "cycle": int(assignment.current_cycle_number or 1), "tail": tail_number,
            "assigned_fueler_user_id": assignment.assigned_fueler_user_id,
            "assigned_truck_id": assignment.assigned_truck_id,
        }, {})
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    tank_layout = tank_layout_for_tail(tail_number)
    if not tank_layout:
        raise ValueError("Fuel calculation is not configured for this aircraft.")
    if assignment.assigned_truck_id is None and fuel_work_state.apu_running is None:
        raise ValueError("Confirm APU Running before OFF.")
    if (
        assignment.assigned_truck_id is None and fuel_work_state.apu_running is True
        and fuel_work_state.apu_source_tank_code
        not in {code for code, _label in tank_layout}
    ):
        raise ValueError("Select a valid APU source tank before OFF.")
    (
        remaining_complete,
        _remaining_total_lbs,
        actual_complete,
        _actual_total_lbs,
        neo_fuel_lbs,
    ) = _fuel_work_calculation(
        tank_layout,
        tank_states_by_code,
        fuel_work_state.apu_running,
        _effective_apu_allowance_lbs(fuel_work_state),
    )
    if assignment.assigned_truck_id is None and not remaining_complete:
        raise ValueError("Complete Remaining fuel before OFF.")
    if assignment.assigned_truck_id is None and (not actual_complete or neo_fuel_lbs is None):
        raise ValueError("Complete Actual fuel before OFF.")
    transfer_gallons = assignment.transfer_fuel_gallons
    if assignment.assigned_truck_id is None:
        if transfer_gallons not in (None, 0):
            raise ValueError(
                "Assign the truck used for positive T/F before OFF."
            )
    if assignment.assigned_truck_id is not None:
        assignments = _assignments_by_mission(operation)
        works = _fuel_work_states_by_assignment_tail(assignments.values())
        selections = {item.fuel_truck_id:item for item in NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=operation.id).all()}
        records, releases = _fuel_start_records(operation, assignments.values(), works,
                                                _parking_by_tail(operation), selections)
        record = next(item for item in records if item["id"] == (
            ("tail_segment", fuel_work_state.id) if getattr(assignment, "_detached_segment", False) else assignment.id))
        start = fueling_start_state(record, records, releases,
            effective_spear_settings(NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()), now=now_utc)
        if not start["on_trusted"]:
            raise ValueError("Confirm physical TRUCK ON before TRUCK OFF.")
        if truck_start_gate(record, records, releases, effective_spear_settings(
                NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()), at=now_utc)["reason"] == "TRUCK STILL FUELING":
            raise ValueError("TRUCK STILL FUELING on a conflicting mission; dispatcher review required.")

    from app.services.neoscorpion_inventory import expire_pending
    if fuel_work_state.physical_truck_id:
        expire_pending(operation, fuel_work_state.physical_truck_id, next_work_id=fuel_work_state.id, reason='next_physical_off', actor=user)
    fuel_work_state.off_at_utc = now_utc
    fuel_work_state.off_by_user_id = user.id
    if assignment.assigned_truck_id is not None:
        selection = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=assignment.assigned_truck_id).with_for_update().populate_existing().one()
        fuel_work_state.off_inventory_epoch = selection.inventory_epoch
        if selection.inventory_verified:
            selection.inventory_pending_epoch = selection.inventory_epoch
        selection.inventory_verified = False
        from app.services.neoscorpion_resource_timing import record_truck_availability
        record_truck_availability(selection,assignment.assigned_truck,user,'physical_off',now_utc)
        _record_defuel_service_requirement(operation,assignment,fuel_work_state,user,now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelerOffResult(
        changed=True,
        revision=int(asset_state.revision),
        fuel_work_state=fuel_work_state,
    )


def _truck_sumped_after(work, selection):
    from app.services.neoscorpion_resource_timing import stamp
    return bool(work and work.off_at_utc and selection and any(
        item.get("source") == "sumped" and item.get("actor_id") is not None
        and stamp(item.get("at")) is not None and stamp(item["at"]) >= utc_naive(work.off_at_utc)
        for item in (selection.availability_history or []) if isinstance(item,dict)))


def _record_defuel_service_requirement(operation, assignment, work, actor, now):
    """Serviceability follows proven received fuel, never administrative approval.

    No inventory movement is posted here. A later authorized sump verification
    remains effective when the older physical visit is finally approved.
    """
    if (not work or not work.off_at_utc or work.physical_finalized_at_utc
            or _assignment_cycle_type(assignment) != "defuel"
            or not work.physical_truck_id or not work.physical_transfer_gallons
            or _cycle_direction_evidence(assignment,work,list(work.tank_states)) != "expected"):
        return
    selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=work.physical_truck_id).with_for_update().populate_existing().one()
    if selection.status != "available" or _truck_sumped_after(work,selection):
        return
    selection.status="needs_sump"
    from app.services.neoscorpion_resource_timing import record_truck_availability
    record_truck_availability(selection,work.physical_truck,actor,"defuel_needs_sump",now)
    _new_fuel_audit(operation,assignment,actor,"segment_transfer","defuel_serviceability",
        "available","needs_sump","Physical DEFUEL OFF with verified decreasing tanks and positive T/F; inventory not yet posted.",
        fuel_work_state=work,now_utc=now)
    hold_active_assignments_for_truck(operation,work.physical_truck,actor,
        "Assigned truck received DEFUEL fuel and requires sumping.",now_utc=now,exclude_assignment_id=assignment.id)


def _capture_resource_data_milestones(assignment, work, layout, tanks, now, *, preparation_only=False, data_changed=False):
    """Performance evidence only; never OFF, COMPLETE, inventory or permission changes."""
    facts=dict(work.performance_context or {})
    if facts.get('version') == 3:
        return  # Do not add new-policy milestones to an already-started v3 job.
    prepared=bool(layout and all(code in tanks and tanks[code].remaining_lbs is not None for code,_ in layout)
        and work.apu_running is not None and _effective_apu_allowance_lbs(work) is not None
        and _apu_source_is_valid(work,layout))
    identity=dict(fueler_id=assignment.assigned_fueler_user_id,tail=work.tail_number,
                  assigned_at=prof_iso(assignment.fueler_assigned_at_utc))
    old=facts.get('preparation_evidence') or {}
    if prepared:
        if any(old.get(key) != value for key,value in identity.items()) or not old.get('at'):
            facts['preparation_evidence']={**identity,'at':prof_iso(now)}
    else:
        facts.pop('preparation_evidence',None)
    if not preparation_only:
        actuals=bool(layout and all(code in tanks and tanks[code].actual_lbs is not None for code,_ in layout))
        direction=_cycle_direction_evidence(assignment,work,list(tanks.values()))
        ready=bool(prepared and actuals and work.actuals_received_at_utc
            and direction in {'expected','equal'} and assignment.operational_status != 'hold_review'
            and not work.ended_early_at_utc and assignment.transfer_fuel_gallons is not None
            and assignment.transfer_fuel_gallons > 0)
        if ready:
            old_ready=facts.get('fueler_data_ready') or {}
            if data_changed or not old_ready.get('at') or any(old_ready.get(k) != v for k,v in identity.items()):
                facts['fueler_data_ready']={**identity,'at':prof_iso(now),'source':'validated_fueler_save'}
        else:
            facts.pop('fueler_data_ready',None)
    work.performance_context=facts


def _snapshot_physical_start(gateway, assignment, mission, work, now, *, source, actor=None):
    if _normalize_tail(assignment.confirmed_tail_number) not in ("", work.tail_number):
        assignment.transfer_fuel_gallons = work.physical_transfer_gallons
    _bind_physical_segment(assignment, mission, work)
    db.session.flush()
    if assignment.assigned_truck_id is not None:
        _lock_physical_truck_start(assignment.assigned_truck_id,work.id)
    from app.services.neoscorpion_inventory import on_physical_start
    on_physical_start(db.session.get(SortDateOperation, assignment.sort_date_operation_id), work)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    previous = work.performance_context or {}
    if assignment.assigned_truck_id:
        from app.services.neoscorpion_resource_timing import record_truck_availability
        selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=assignment.sort_date_operation_id,
            fuel_truck_id=assignment.assigned_truck_id).one()
        record_truck_availability(selection,db.session.get(NeoScorpionFuelTruck,assignment.assigned_truck_id),
            actor,'physical_on',now)
    planning=assignment_planning_settings(gateway, settings=settings)
    from app.services.neoscorpion_resource_timing import snapshot_resources
    if previous.get('version') == 3:
        facts=dict(previous)  # An in-flight v3 policy does not migrate mid-job.
    else:
        try:
            with db.session.begin_nested():
                resources=snapshot_resources(gateway,assignment,mission,work,settings,planning,now)
        except Exception:
            current_app.logger.exception('Resource-aware PROF evidence unavailable for physical work %s; fueling continues.',work.id)
            resources=dict(work_id=work.id,on_at=prof_iso(now),setup_allowance=str(planning.setup_minutes) if planning.setup_minutes is not None else None,
                setup_reason='Full setup; resource evidence capture unavailable',
                truck_reasons=['Resource evidence capture unavailable'],fueler_reasons=['Resource evidence capture unavailable'])
        facts = policy_snapshot(assignment, settings, planning,
                                detailed_aircraft_type_for_tail(mission.assigned_tail_number), resources=resources)
    for key in ('preparation_evidence','fueler_data_ready'):
        if key in previous:facts[key]=previous[key]
    facts.update(on_source=source, on_at=prof_iso(now), forecasts=previous.get("forecasts", []),
                 exclusions=previous.get("exclusions", []),
                 planned_departure_at=prof_iso(mission.planned_datetime_utc),
                 required_fuel_lbs=mission.planned_fuel_load)
    work.performance_context = facts


def _lock_physical_truck_start(truck_id,work_id):
    # Sort locks serialize the usual current-sort path. The persistent truck
    # row also serializes starts across overlapping sort operations. Read
    # other occupations under that lock; do not lock unrelated work rows.
    NeoScorpionFuelTruck.query.filter_by(id=truck_id).with_for_update().populate_existing().one()
    db.session.flush()
    conflict=NeoScorpionFuelWorkState.query.filter(
        NeoScorpionFuelWorkState.id != work_id,
        NeoScorpionFuelWorkState.physical_truck_id==truck_id,
        NeoScorpionFuelWorkState.off_at_utc.is_(None),
        NeoScorpionFuelWorkState.ended_early_at_utc.is_(None),
        NeoScorpionFuelWorkState.physical_finalized_at_utc.is_(None),
        db.or_(
            NeoScorpionFuelWorkState.manual_on_at_utc.is_not(None),
            NeoScorpionFuelWorkState.confirmed_on_at_utc.is_not(None),
        )).first()
    if conflict:
        raise ValueError("TRUCK STILL FUELING on another physical segment; record its TRUCK OFF first.")


def _capture_physical_forecast(gateway, assignment, mission, work, now):
    if not work.performance_context or work.off_at_utc or work.ended_early_at_utc:
        return
    try:
        settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
        remaining = {t.tank_code: t.remaining_lbs for t in work.tank_states}
        layout = tank_layout_for_tail(work.tail_number)
        demand = None
        if layout and all(remaining.get(code) is not None for code, _ in layout):
            demand = lbs_to_gallons(max(0, (mission.planned_fuel_load or 0)-sum(remaining.values())),
                                   settings.fuel_density_lbs_per_gallon if settings else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON)
        with db.session.begin_nested():
            history = load_observation_jobs(gateway.id)
        prediction = physical_off_forecast(dict(assignment=assignment, fuel_work_state=work,
            detailed_aircraft_type=detailed_aircraft_type_for_tail(work.tail_number),
            fueling_effective_on_utc=work.manual_on_at_utc or work.confirmed_on_at_utc,
            planning_demand_gallons=demand), assignment_planning_settings(gateway, settings=settings),
            settings=settings, jobs=history, mode=effective_spear_settings(settings).live_calibration_mode)
        snapshot = dict(goal=prof_iso(prediction["goal"]), likely=prof_iso(prediction["likely"]),
                        source=prediction["source"], samples=prediction["samples"], gallons=assignment.transfer_fuel_gallons or demand)
        facts = dict(work.performance_context)
        previous = facts.get("forecasts", [])
        if not previous or {k:v for k,v in previous[-1].items() if k != "observed_at"} != snapshot:
            facts["forecasts"] = [*previous, dict(snapshot, observed_at=prof_iso(now))]
            work.performance_context = facts
    except Exception:
        current_app.logger.exception("SPEAR forecast capture unavailable; physical operation continues.")


def _try_finalize_retired_segment(gateway, user, assignment, mission, work, now):
    # Only an independently addressed retired visit may finalize here. The
    # current mission always awaits an explicit Dispatcher COMPLETE, even when
    # its physical OFF and final readings are already valid.
    if not getattr(assignment, "_detached_segment", False):
        return False
    if not work.off_at_utc or assignment.completed_at_utc or assignment.assigned_truck_id is None:
        return False
    if classify_fuel_movement(assignment, work, tank_states=work.tank_states) == "not_moved":
        return False  # Truckless/zero-transfer FOB retains its separate explicit workflow.
    try:
        with db.session.begin_nested():
            result = complete_fueled_assignment(gateway, user, assignment.id, now_utc=now,
                physical_work_id=work.id if getattr(assignment, "_source_work", None) else None,
                physical_sequence=work.physical_sequence_number)
        return result.changed
    except ValueError:
        # Incomplete data and genuine exceptions remain visible through the
        # canonical readiness/review projection; physical OFF remains recorded.
        return False


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
            "REVIEW REQUIRED must be resolved before Fuel On Board."
        )

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    fuel_work_state = current_work(assignment, tail_number, lock=True)
    tank_states = _locked_tank_states(fuel_work_state)
    inherited_measurement = _validated_tail_swap_inherited_measurement(
        operation,
        assignment,
        mission,
        fuel_work_state,
        tank_states=tank_states,
    )
    if assignment.assigned_truck_id is not None:
        raise ValueError("Clear the unused truck before Fuel On Board.")
    if assignment.transfer_fuel_gallons not in (None, 0):
        raise ValueError("T/F must be blank or 0 for Fuel On Board.")
    if fuel_work_state and (fuel_work_state.manual_on_at_utc or fuel_work_state.confirmed_on_at_utc
            or fuel_work_state.on_at_utc or fuel_work_state.off_at_utc or fuel_work_state.physical_transfer_review):
        raise ValueError("REVIEW REQUIRED: physical truck evidence cannot be completed as no-truck FOB.")
    confirmed_apu = (_effective_apu_allowance_lbs(fuel_work_state) if fuel_work_state
        and fuel_work_state.apu_running is not None
        and _apu_source_is_valid(fuel_work_state,tank_layout_for_tail(tail_number)) else None)
    inherited_fob_ready = bool(inherited_measurement and mission.planned_fuel_load is not None
                              and confirmed_apu is not None
                              and inherited_measurement["total_lbs"] - confirmed_apu >= mission.planned_fuel_load)
    if _assignment_cycle_type(assignment) == "defuel":
        raise ValueError("DEFUEL requires physical completion, not Fuel On Board.")

    if not inherited_fob_ready:
        if assignment.assigned_fueler_user_id is None:
            raise ValueError("Assign a fueler before Fuel On Board.")
        if assignment.assigned_truck_id is not None:
            raise ValueError("Clear the unused truck before Fuel On Board.")
        if assignment.transfer_fuel_gallons not in (None, 0):
            raise ValueError("T/F must be blank or 0 for Fuel On Board.")
        if fuel_work_state is None:
            raise ValueError(
                "Complete Actual fuel and confirm APU before Fuel On Board."
            )
        if fuel_work_state.ended_early_at_utc is not None:
            raise ValueError("Ended Early fuel work cannot use Fuel On Board.")
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
            _effective_apu_allowance_lbs(fuel_work_state),
        )
        if neo_fuel_lbs is None:
            raise ValueError(
                "Complete Actual fuel and confirm APU before Fuel On Board."
            )

        if mission.planned_fuel_load is None or neo_fuel_lbs < mission.planned_fuel_load:
            raise ValueError("Insufficient verified fuel for Fuel On Board.")

    assignment.fuel_on_board_at_utc = now_utc
    assignment.fuel_on_board_by_user_id = user.id
    assignment.review_status = "complete"
    assignment.transfer_fuel_gallons = 0
    if fuel_work_state is not None:
        fuel_work_state.physical_transfer_gallons=0
        fuel_work_state.physical_finalized_at_utc=now_utc
        fuel_work_state.physical_finalized_by_user_id=user.id
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


def _unstarted_follow_up_cycle(assignment, state):
    # Copied Remaining is a starting measurement, not new physical work.
    return bool(
        assignment and int(assignment.current_cycle_number or 1) > 1
        and assignment.transfer_fuel_gallons in (None, 0)
        and state and state.on_at_utc is None and state.manual_on_at_utc is None and state.off_at_utc is None
        and state.ended_early_at_utc is None and state.apu_running is None
        and state.truck_segment_started_at_utc is None
        and all(tank.actual_lbs is None for tank in state.tank_states)
    )


def _freeze_cycle_display(operation, assignment, mission, label, *, events=None):
    existing = NeoScorpionFuelCycleHistory.query.filter_by(
        fuel_assignment_id=assignment.id, cycle_number=assignment.current_cycle_number or 1,
    ).with_for_update().first()
    # Cancellation restores this completed cycle. Preserve its display and
    # append future rollback records rather than freezing the cycle twice.
    if existing is not None:
        return existing
    # A detached presentation object selects the confirmed old tail without ever
    # writing it back to the canonical MotherBrain mission.
    proxy = SimpleNamespace(**{
        col.name: getattr(mission, col.name) for col in mission.__table__.columns
    })
    proxy.timezone = mission.timezone
    proxy.assigned_tail_number = assignment.confirmed_tail_number or mission.assigned_tail_number
    states = _fuel_work_states_by_assignment_tail([assignment])
    if events is None:
        events = NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=assignment.id).all()
    keys = {(event.fuel_work_state_id, event.cycle_number) for event in events}
    row = _fuel_rows(
        operation, [proxy], assignments_by_mission={mission.id: assignment},
        fuel_work_states_by_assignment_tail=states, fueling_event_cycle_keys=keys,
    )[0]

    def primitive(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {key: primitive(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [primitive(item) for item in value]
        return None
    snapshot = primitive(row)
    snapshot.update(
        flight_number=mission.flight_number,
        fueler=assignment.assigned_fueler.display_name if assignment.assigned_fueler else "UNASSIGNED",
        truck=assignment.assigned_truck.truck_number if assignment.assigned_truck else "UNASSIGNED",
    )
    history = NeoScorpionFuelCycleHistory(
        sort_date_operation_id=operation.id,
        fuel_assignment_id=assignment.id, mission_id=mission.id,
        cycle_number=assignment.current_cycle_number or 1, label=label, snapshot=snapshot,
    )
    db.session.add(history)
    db.session.flush()
    return history


# Private JSON in existing cycle history, never included in editable UI state.
# Keep the original key for rollback records already written in production.
_UPLIFT_ROLLBACK_KEY = "_uplift_rollbacks"
_MISSION_FUEL_FIELDS = ("planned_fuel_load", "planned_fuel_updated_at", "fuel_status", "fuel_completed_at_utc", "updated_at")


def _fuel_rollback_values(row, fields=None):
    if row is None:
        return None
    values = {}
    for name in fields or row.__table__.columns.keys():
        value = getattr(row, name)
        values[name] = value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, Decimal) else value
    return values


def _restore_fuel_rollback_values(row, values):
    for name, value in values.items():
        column = row.__table__.columns[name]
        if value is not None and column.type.python_type is datetime:
            value = datetime.fromisoformat(value)
        elif value is not None and column.type.python_type is Decimal:
            value = Decimal(value)
        setattr(row, name, value)
    # Restore exact saved timestamps instead of SQLAlchemy's onupdate clock.
    if "updated_at" in values:
        flag_modified(row, "updated_at")


def _cancel_follow_up_cycle(gateway, user, assignment_id, cycle_type, *, expected_cycle, expected_tail, now_utc=None):
    label = cycle_type.upper()
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError(f"No current sort operation is available for CANCEL {label}.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(operation, assignment_id, action_label=f"CANCEL {label}")
    if expected_cycle is None or expected_tail is None:
        raise ValueError(f"Refresh before cancelling the {cycle_type}.")
    _validate_cycle_request(assignment, mission, expected_cycle, expected_tail)
    if _assignment_cycle_type(assignment) != cycle_type:
        raise ValueError(f"The current fuel cycle is not a {cycle_type}.")
    tail = _normalize_tail(mission.assigned_tail_number)
    if not tail:
        raise ValueError("Fuel assignment does not have a tail number.")
    work = _locked_fuel_work_state_for_tail(assignment, tail)
    tanks = _locked_tank_states(work)
    events = NeoScorpionFuelingEvent.query.filter_by(
        fuel_assignment_id=assignment.id, cycle_number=assignment.current_cycle_number,
    ).with_for_update().all()
    cycle_works=NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id,
        physical_cycle_number=assignment.current_cycle_number).with_for_update().populate_existing().all()
    # Setup measurements, ON/OFF, and APU fields are not confirmed transfer.
    # Current-cycle T/F and persisted transfer events are the two authorities.
    if (assignment.transfer_fuel_gallons not in (None, 0)
            or any(item.physical_transfer_gallons not in (None,0) or item.physical_transfer_review for item in cycle_works)
            or any(event.transfer_fuel_gallons not in (None, 0) for event in events)):
        raise ValueError("Fuel movement occurred. Use the existing correction or interruption workflow.")
    histories = NeoScorpionFuelCycleHistory.query.filter_by(
        fuel_assignment_id=assignment.id,
    ).with_for_update().all()
    selected = next(((history, index, record) for history in histories
        for index, record in enumerate(history.snapshot.get(_UPLIFT_ROLLBACK_KEY, []))
        if record["cycle"] == assignment.current_cycle_number and not record.get("cancelled_at")), None)
    if selected is None:
        raise ValueError(f"REVIEW REQUIRED: exact pre-{cycle_type} rollback state is unavailable for this cycle.")
    history, index, rollback = selected
    # Tail fuel is shared canonical state. A later physical event on another
    # mission must not be overwritten with this assignment's old measurements.
    if NeoScorpionFuelingEvent.query.filter(
        NeoScorpionFuelingEvent.sort_date_operation_id == operation.id,
        NeoScorpionFuelingEvent.tail_number == tail,
        NeoScorpionFuelingEvent.fuel_assignment_id != assignment.id,
        NeoScorpionFuelingEvent.ended_at_utc >= datetime.fromisoformat(rollback["started_at"]),
    ).with_for_update().first() is not None:
        raise ValueError("REVIEW REQUIRED: another mission updated this tail's physical fuel state.")
    if rollback["tail"] != tail:
        raise ValueError(f"REVIEW REQUIRED: {cycle_type} rollback tail does not match the current mission.")
    tail_state = NeoScorpionTailFuelState.query.filter_by(
        sort_date_operation_id=operation.id, tail_number=tail,
    ).with_for_update().first()
    saved_work = (NeoScorpionFuelWorkState.query.filter_by(id=rollback["work"]["id"],fuel_assignment_id=assignment.id)
        .with_for_update().populate_existing().first()) if rollback["work"] is not None else None
    if rollback["work"] is not None and saved_work is None:
        raise ValueError("REVIEW REQUIRED: saved fuel work state is unavailable.")
    saved_work_tanks = _locked_tank_states(saved_work)
    saved_tanks = {row["id"]: row for row in rollback["tanks"]}
    if not set(saved_tanks).issubset({tank.id for tank in saved_work_tanks}):
        raise ValueError("REVIEW REQUIRED: saved tank state is unavailable.")
    if rollback["tail_state"] is not None and (tail_state is None or tail_state.id != rollback["tail_state"]["id"]):
        raise ValueError("REVIEW REQUIRED: saved tail fuel state is unavailable.")
    _restore_fuel_rollback_values(assignment, rollback["assignment"])
    _restore_fuel_rollback_values(mission, rollback["mission"])
    assignment.active_work_state_id = saved_work.id if saved_work else None
    if rollback["work"] is not None:
        if saved_work.id == getattr(work,"id",None):
            # Exact, audited compatibility rollback of a pre-segment cycle.
            _restore_fuel_rollback_values(saved_work, rollback["work"])
            for tank in saved_work_tanks:
                _restore_fuel_rollback_values(tank, saved_tanks[tank.id])
    for cancelled_work in cycle_works:
        if cancelled_work.id == getattr(saved_work,"id",None) or cancelled_work.physical_finalized_at_utc:
            continue
        # Cancel reservations, not physical facts; do not fabricate OFF.
        cancelled_work.ended_early_at_utc = now_utc
        cancelled_work.ended_early_by_user_id = user.id
        cancelled_work.ended_early_reason = f"Cancelled {cycle_type} before physical transfer."
        cancelled_work.physical_finalized_at_utc = now_utc
        cancelled_work.physical_finalized_by_user_id = user.id
    if rollback["tail_state"] is not None:
        _restore_fuel_rollback_values(tail_state, rollback["tail_state"])
    elif tail_state is not None:
        db.session.delete(tail_state)
    records = list(history.snapshot[_UPLIFT_ROLLBACK_KEY])
    records[index] = {**rollback, "cancelled_at": now_utc.isoformat(), "cancelled_by": user.id,
                      "cancelled_segment_id": work.id if work else None}
    history.snapshot = {**history.snapshot, _UPLIFT_ROLLBACK_KEY: records}
    audit = _new_fuel_audit(operation, assignment, user, f"cancel_{cycle_type}", "current_cycle_number",
        expected_cycle, assignment.current_cycle_number, f"Dispatcher cancelled {cycle_type} before fuel movement.",
        fuel_work_state=work if rollback["work"] is not None else None, now_utc=now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(True, int(asset_state.revision), assignment, work, audit_entries=(audit,))


def cancel_uplift(gateway, user, assignment_id, *, expected_cycle, expected_tail, now_utc=None):
    return _cancel_follow_up_cycle(gateway, user, assignment_id, "uplift",
        expected_cycle=expected_cycle, expected_tail=expected_tail, now_utc=now_utc)


def cancel_defuel(gateway, user, assignment_id, *, expected_cycle, expected_tail, now_utc=None):
    return _cancel_follow_up_cycle(gateway, user, assignment_id, "defuel",
        expected_cycle=expected_cycle, expected_tail=expected_tail, now_utc=now_utc)


def _validate_cycle_request(assignment, mission, expected_cycle, expected_tail):
    if expected_cycle is not None and str(assignment.current_cycle_number or 1) != str(expected_cycle):
        raise ValueError("Fuel cycle changed. Refresh before starting another cycle.")
    if expected_tail is not None and _normalize_tail(expected_tail) != _normalize_tail(mission.assigned_tail_number):
        raise ValueError("Mission tail changed again. Refresh and review the current tail.")


def start_follow_up_fuel_cycle(
    gateway,
    user,
    assignment_id,
    cycle_type,
    required_fuel,
    fueler_user_id,
    fuel_truck_id,
    *,
    now_utc=None,
    expected_cycle=None,
    expected_tail=None,
    pending_uplift_id=None,
):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for follow-up fuel work.")
    cycle_type = str(cycle_type or "").strip().lower()
    if cycle_type not in {"uplift", "defuel"}:
        raise ValueError("Select UPLIFT or DEFUEL.")
    required_fuel_lbs = display_thousands_to_lbs(required_fuel)
    if required_fuel_lbs is None:
        raise ValueError("Enter a new Required Fuel target.")
    fueler_user_id = _int_or_none(fueler_user_id)
    fuel_truck_id = _int_or_none(fuel_truck_id)
    if cycle_type == "defuel" and fueler_user_id is None:
        raise ValueError("Select an eligible nightly fueler.")
    if cycle_type == "defuel" and fuel_truck_id is None:
        raise ValueError("Select an available nightly truck.")
    now_utc = now_utc or datetime.utcnow()

    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(
        operation,
        assignment_id,
        action_label=f"START {cycle_type.upper()}",
    )
    from app.services.neoscorpion_spear_decisions import prepare_scope, observe
    spear_before = prepare_scope(operation, mission, assignment, int(asset_state.revision if asset_state else 0))
    _validate_cycle_request(assignment, mission, expected_cycle, expected_tail)
    from app.services.neoscorpion_pending_uplift import outstanding
    queued=outstanding(assignment.id,lock=True)
    if queued is not None and queued.id != pending_uplift_id:
        raise ValueError("Resolve the saved pending uplift before starting another fuel cycle.")
    if assignment.fuel_on_board_at_utc is not None:
        raise ValueError("Fuel On Board assignments cannot start a physical follow-up cycle.")
    if assignment.completed_at_utc is None:
        raise ValueError("Complete the current physical fuel cycle before starting another.")
    if assignment.review_status != "complete" or mission.fuel_status != "complete":
        raise ValueError("REVIEW REQUIRED: completion state is inconsistent.")

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("The mission does not have a tail for follow-up work.")
    prior_events = (
        NeoScorpionFuelingEvent.query.filter_by(
            fuel_assignment_id=assignment.id,
        )
        # Keep the row lock on the fueling-event table only. PostgreSQL rejects
        # FOR UPDATE when joinedload adds the nullable snapshot outer join.
        .options(selectinload(NeoScorpionFuelingEvent.tank_snapshots))
        .with_for_update()
        .order_by(
            NeoScorpionFuelingEvent.cycle_number.desc(),
            NeoScorpionFuelingEvent.sequence_number.desc(),
        )
        .all()
    )
    if not prior_events:
        raise ValueError("A completed physical fuel event is required first.")

    fuel_work_state = _locked_fuel_work_state_for_tail(assignment, tail_number)
    rollback_tail_state = NeoScorpionTailFuelState.query.filter_by(
        sort_date_operation_id=operation.id, tail_number=tail_number,
    ).with_for_update().first()
    rollback = {
        "tail": tail_number, "assignment": _fuel_rollback_values(assignment),
        "mission": _fuel_rollback_values(mission, _MISSION_FUEL_FIELDS),
        "work": _fuel_rollback_values(fuel_work_state),
        "tanks": [_fuel_rollback_values(tank) for tank in _locked_tank_states(fuel_work_state)],
        "tail_state": _fuel_rollback_values(rollback_tail_state),
    }
    if fuel_work_state is None:
        fuel_work_state = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number=tail_number,
        )
        db.session.add(fuel_work_state)

    if cycle_type == "defuel":
        _validate_nightly_fueler_assignment(gateway, operation, fueler_user_id)
        _validate_nightly_truck_assignment(gateway, operation, fuel_truck_id)
    else:
        fueler_user_id = fuel_truck_id = None
    tank_states = _locked_tank_states(fuel_work_state)
    event_ids_with_snapshots = {event.id for event in prior_events if event.tank_snapshots}
    for event in prior_events:
        if event.id not in event_ids_with_snapshots:
            if event.fuel_work_state_id != fuel_work_state.id:
                continue
            _populate_fueling_event_snapshot(
                event,
                assignment,
                mission,
                fuel_work_state,
                tank_states,
            )

    prior_actual_by_tank = {}
    measured = _latest_trusted_completed_tail_measurement(operation, tail_number)
    if measured:
        prior_actual_by_tank = measured["actual_by_tank"]
    tail_fuel_state = (
        NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        )
        .with_for_update()
        .first()
    )
    if tail_fuel_state is None and prior_actual_by_tank:
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

    history = _freeze_cycle_display(
        operation, assignment, mission, _assignment_cycle_type(assignment).upper(),
        events=prior_events,
    )
    assignment.ready_for_fuel_at_utc = None
    assignment.ready_for_fuel_by_user_id = None
    assignment.load_planning_note = ""
    assignment.estimated_fuel_gallons = None
    assignment.current_cycle_type = cycle_type
    assignment.current_cycle_number = max(
        [int(event.cycle_number or 1) for event in prior_events]
        + [int(assignment.current_cycle_number or 1)]
        + [record["cycle"] for record in history.snapshot.get(_UPLIFT_ROLLBACK_KEY, [])]
    ) + 1
    if rollback is not None:
        history.snapshot = {**history.snapshot, _UPLIFT_ROLLBACK_KEY: [
            *history.snapshot.get(_UPLIFT_ROLLBACK_KEY, []),
            {**rollback, "cycle": assignment.current_cycle_number, "started_at": now_utc.isoformat()},
        ]}
    assignment.assigned_fueler_user_id = fueler_user_id
    assignment.assigned_truck_id = fuel_truck_id
    assignment.fueler_assigned_at_utc = now_utc if fueler_user_id is not None else None
    assignment.truck_assigned_at_utc = now_utc if fuel_truck_id is not None else None
    assignment.resource_assignment_by_user_id = user.id
    assignment.transfer_fuel_gallons = None
    assignment.completed_at_utc = None
    assignment.completed_by_user_id = None
    assignment.review_status = "assigned"
    assignment.operational_status = "active"
    assignment.hold_reason = None
    assignment.hold_at_utc = None
    assignment.hold_by_user_id = None
    assignment.confirmed_tail_number = tail_number

    # Follow-up calculations may carry measured FOB, but physical milestones,
    # Actuals, T/F and identity must belong to a new row.
    fuel_work_state = new_work(assignment, tail_number, user, now_utc)
    fuel_work_state.physical_required_fuel_lbs = required_fuel_lbs
    # A new explicitly assigned cycle has its own immutable assignment receipt,
    # even if the same resources were used by the previous completed cycle.
    for field_name, value in (
        ('assigned_fueler_user_id', fueler_user_id),
        ('assigned_truck_id', fuel_truck_id),
    ):
        if value is not None:
            _new_fuel_audit(operation, assignment, user, 'segment_transfer', field_name, None, value,
                'Dispatcher assigned resources to a new ' + cycle_type + ' cycle.',
                fuel_work_state=fuel_work_state, now_utc=now_utc)
    tank_states = []

    mission.planned_fuel_load = required_fuel_lbs
    mission.planned_fuel_updated_at = now_utc
    mission.fuel_status = "assigned"
    mission.fuel_completed_at_utc = None

    fuel_work_state.on_at_utc = None
    fuel_work_state.confirmed_on_at_utc = None
    fuel_work_state.manual_on_at_utc = None
    fuel_work_state.manual_on_by_user_id = None
    fuel_work_state.manual_on_truck_id = None
    fuel_work_state.manual_on_cycle_number = None
    fuel_work_state.actuals_received_at_utc = None
    fuel_work_state.preparation_received_at_utc = None
    fuel_work_state.completion_ready_at_utc = None
    fuel_work_state.off_inventory_epoch = None
    fuel_work_state.performance_context = None
    fuel_work_state.apu_running = None
    fuel_work_state.apu_confirmed_at_utc = None
    fuel_work_state.apu_allowance_lbs = None
    fuel_work_state.automatic_apu_allowance_lbs = None
    fuel_work_state.apu_override_enabled = False
    fuel_work_state.apu_override_allowance_lbs = None
    fuel_work_state.applied_apu_rate_thousand_lbs_per_hour = None
    fuel_work_state.apu_source_tank_code = None
    fuel_work_state.off_at_utc = None
    fuel_work_state.off_by_user_id = None
    fuel_work_state.truck_segment_started_at_utc = None
    fuel_work_state.ended_early_at_utc = None
    fuel_work_state.ended_early_by_user_id = None
    fuel_work_state.ended_early_reason = None
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    target_layout = tank_layout_for_tail(tail_number)
    for tank_code, _label in target_layout:
        tank_state = tank_states_by_code.get(tank_code)
        if tank_state is None and tank_code not in prior_actual_by_tank:
            continue
        if tank_state is None:
            tank_state = NeoScorpionFuelTankState(tank_code=tank_code)
            fuel_work_state.tank_states.append(tank_state)
        tank_state.remaining_lbs = prior_actual_by_tank.get(tank_code)
        tank_state.actual_lbs = None

    if tail_fuel_state is not None:
        tail_fuel_state.fob_lbs = (
            sum(prior_actual_by_tank.values())
            if target_layout
            and all(code in prior_actual_by_tank for code, _label in target_layout)
            else None
        )
        tail_fuel_state.actual_fuel_lbs = None
        tail_fuel_state.center_fuel_lbs = None
        tail_fuel_state.apu_lbs = None

    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    if fueler_user_id is not None or fuel_truck_id is not None:
        observe(gateway, assignment, spear_before, 'initial_assignment', actor=user, now=now_utc)
    return FuelCycleStartResult(
        changed=True,
        revision=int(asset_state.revision),
        assignment=assignment,
        mission=mission,
        fuel_work_state=fuel_work_state,
    )


def complete_fueled_assignment(gateway, user, assignment_id, *, now_utc=None,
                               physical_work_id=None, physical_sequence=None):
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
        .populate_existing()
        .first()
    )
    if not assignment_row:
        raise ValueError(
            "Fuel assignment was not found for the current sort operation."
        )

    assignment, mission = assignment_row
    source_work = None
    parent_assignment, parent_mission = assignment, mission
    if physical_work_id is None and has_request_context():
        _check_dispatch_segment_reference(assignment,request.form)
    if physical_work_id is not None:
        assignment, mission, source_work = _locked_source_segment(assignment, mission,
            physical_work_id, physical_sequence, user, dispatcher=True)
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
    if physical_work_id is None and has_request_context():
        _validate_cycle_request(assignment, mission, request.form.get("expected_cycle"), request.form.get("expected_tail"))
        current_segment = current_work(assignment, _normalize_tail(mission.assigned_tail_number), lock=True)
        if "expected_submission_version" in request.form and str(request.form["expected_submission_version"]) != str(
                current_segment.fueler_submission_version if current_segment else ""):
            raise FuelerDataConflict()
    if assignment.operational_status == "hold_review":
        raise ValueError("REVIEW REQUIRED must be resolved before COMPLETE.")

    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    fuel_work_state = source_work or current_work(assignment, tail_number, lock=True)
    if (source_work is not None and has_request_context()
            and "expected_submission_version" in request.form
            and str(request.form["expected_submission_version"]) != str(source_work.fueler_submission_version)):
        raise FuelerDataConflict()
    if fuel_work_state is None or fuel_work_state.off_at_utc is None:
        raise ValueError("Fueler must MARK OFF before dispatcher COMPLETE.")
    if fuel_work_state.target_frozen and source_work is None:
        _visit_assignment, mission = _segment_views(assignment,mission,fuel_work_state)
    if fuel_work_state.continuation_review_reason:
        raise ValueError(fuel_work_state.continuation_review_reason)
    if fuel_work_state.physical_transfer_review:
        raise ValueError("REVIEW REQUIRED: verify this physical segment's T/F first.")
    if fuel_work_state.physical_sequence_number is not None and fuel_work_state.physical_cycle_type is None:
        raise ValueError("REVIEW REQUIRED: the original physical cycle type is unknown; do not guess its transfer direction.")
    if fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("Ended Early fuel work cannot use normal COMPLETE.")
    if fuel_work_state.physical_finalized_at_utc is not None:
        # This exact visit was verified, even if another visit keeps the parent
        # mission unfinished. Never post its transfer again or reset its time.
        return DispatcherCompleteResult(changed=False,revision=int(asset_state.revision),
            movement_status="moved" if fuel_work_state.physical_transfer_gallons else "not_moved",
            assignment=assignment,mission=mission,fueling_event=None)

    tank_states = (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    from app.services.neoscorpion_resume import guard_duplicate_transfer
    guard_duplicate_transfer(fuel_work_state,tank_states)
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

    from app.services.neoscorpion_submission import evidence
    if (fuel_work_state.fueler_submitted_at_utc is None or
            fuel_work_state.fueler_submission_evidence != evidence(assignment,mission,fuel_work_state,tank_states)):
        raise ValueError("Fueler COMPLETE ASSIGNMENT required before dispatcher COMPLETE.")

    existing_events = (
        NeoScorpionFuelingEvent.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .order_by(NeoScorpionFuelingEvent.sequence_number)
        .all()
    )
    cycle_number = int(assignment.current_cycle_number or 1)
    current_cycle_events = [
        event
        for event in existing_events
        if int(event.cycle_number or 1) == cycle_number
    ]
    if current_cycle_events and fuel_work_state.truck_segment_started_at_utc is None:
        raise ValueError("REVIEW REQUIRED: an unexpected fueling event already exists.")
    _validate_cycle_direction(assignment, fuel_work_state, tank_states)
    movement_status = classify_fuel_movement(
        assignment,
        fuel_work_state,
        tank_states=tank_states,
    )
    if movement_status == "unknown" and not current_cycle_events:
        raise ValueError("Fuel movement cannot yet be determined.")
    if current_cycle_events:
        movement_status = "moved"
    cycle_type = _assignment_cycle_type(assignment)
    if cycle_type in {"uplift", "defuel"} and movement_status != "moved":
        raise ValueError(
            f"REVIEW REQUIRED: {cycle_type.upper()} requires fuel movement "
            "in the selected direction."
        )

    fueling_event = None
    if current_cycle_events and assignment.transfer_fuel_gallons in (None, 0):
        fueling_event = None
    elif movement_status == "moved":
        fueling_event = _close_current_truck_segment(
            gateway,
            operation,
            assignment,
            mission,
            fuel_work_state,
            tank_states,
            existing_events,
            fuel_work_state.off_at_utc,
            changed_by_user=user,
        )

    if source_work is None or not assignment._detached_segment:
        parent_assignment.completed_at_utc = now_utc
        parent_assignment.completed_by_user_id = user.id
        parent_assignment.review_status = "complete"
        parent_mission.fuel_status = "complete"
        parent_mission.fuel_completed_at_utc = now_utc
    fuel_work_state.physical_finalized_at_utc = now_utc
    fuel_work_state.physical_finalized_by_user_id = user.id
    assignment.completed_at_utc = now_utc
    fuel_work_state.completion_ready_at_utc = now_utc
    if fueling_event is not None:
        fueling_event.performance_snapshot = {**(fueling_event.performance_snapshot or {}),
            "completion_ready_at": prof_iso(now_utc), "completed_at": prof_iso(now_utc)}
    assignment.completed_by_user_id = user.id
    assignment.review_status = "complete"
    mission.fuel_status = "complete"
    mission.fuel_completed_at_utc = now_utc
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    if source_work is None or not assignment._detached_segment:
        from app.services.neoscorpion_pending_uplift import promote_after_completion
        promote_after_completion(gateway,user,operation,parent_assignment,parent_mission,now_utc)
        asset_state=db.session.get(NeoScorpionSortAssetState,asset_state.id)
    from app.services.neoscorpion_pending_uplift import reconcile_completion
    reconcile_completion(operation,parent_assignment,parent_mission,user,now_utc)
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
    db.session.flush([audit_entry])  # PostgreSQL OFF guard requires its audit first.
    from app.services.neoscorpion_submission import invalidate
    invalidate(fuel_work_state)
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

    from app.services.neoscorpion_submission import invalidate
    invalidate(fuel_work_state)
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
    if detailed_aircraft_type_for_tail(fuel_work_state.tail_number) == "A300":
        center_tank_state = tank_states_by_code.get("ctr")
        tail_fuel_state.center_fuel_lbs = (
            center_tank_state.actual_lbs if center_tank_state is not None else None
        )

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
        _normalize_tail(mission.assigned_tail_number),
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
    from app.services.neoscorpion_spear_decisions import prepare_scope as scope, observe
    spear_before = scope(operation, mission, assignment, int(asset_state.revision if asset_state else 0))
    _validate_precompletion_assignment(assignment, mission, "FUELER SWAP")
    replacement_user_id = _int_or_none(replacement_user_id)
    if replacement_user_id is None:
        raise ValueError("Select a replacement fueler.")
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        _normalize_tail(mission.assigned_tail_number),
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
    assignment.fueler_assigned_at_utc = now_utc
    assignment.resource_assignment_by_user_id = user.id
    if fuel_work_state and fuel_work_state.performance_context:
        fuel_work_state.performance_context = {**fuel_work_state.performance_context,
            "exclusions": [*fuel_work_state.performance_context.get("exclusions", []), "Fueler changed during physical segment"]}
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
    observe(gateway, assignment, spear_before, 'fueler_swap', actor=user, now=now_utc)
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
    from app.services.neoscorpion_spear_decisions import prepare_scope as scope, observe
    spear_before = scope(operation, mission, assignment, int(asset_state.revision if asset_state else 0))
    _validate_precompletion_assignment(assignment, mission, "TRUCK SWAP")
    replacement_truck_id = _int_or_none(replacement_truck_id)
    if replacement_truck_id is None:
        raise ValueError("Select a replacement truck.")
    confirmed_tail = _normalize_tail(mission.assigned_tail_number)
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        confirmed_tail,
    )
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
    old_truck_id = assignment.assigned_truck_id
    fueling_event = None
    _retire_visit(gateway, operation, assignment, mission, fuel_work_state, tank_states, user, now_utc, "swap_truck")
    old_work = fuel_work_state
    assignment.assigned_truck_id = replacement_truck_id
    assignment.truck_assigned_at_utc = now_utc
    assignment.resource_assignment_by_user_id = user.id
    fuel_work_state = new_work(assignment, confirmed_tail, user, now_utc)
    fuel_work_state.physical_required_fuel_lbs = mission.planned_fuel_load
    _copy_same_aircraft_preparation(old_work, fuel_work_state, tank_states)
    if old_work and old_work.off_at_utc:
        old_assignment, old_mission = _segment_views(assignment, mission, old_work)
        _try_finalize_retired_segment(gateway, user, old_assignment, old_mission, old_work, now_utc)
        fueling_event = NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=old_work.id).first()
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
    observe(gateway, assignment, spear_before, 'truck_swap', actor=user, now=now_utc)
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        fuel_work_state,
        fueling_event=fueling_event,
        audit_entries=(audit,),
    )


def unassign_assignment_truck(gateway, user, assignment_id, *, expected_truck_id,
        expected_cycle, expected_tail, now_utc=None):
    operation = current_sort_operation(gateway)
    if not operation:
        raise ValueError("No current sort operation is available for UNASSIGN TRUCK.")
    now_utc = now_utc or datetime.utcnow()
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    assignment, mission = _locked_current_fuel_assignment(operation, assignment_id, action_label="UNASSIGN TRUCK")
    from app.services.neoscorpion_spear_decisions import prepare_scope as scope, observe
    spear_before = scope(operation, mission, assignment, int(asset_state.revision if asset_state else 0))
    if expected_cycle is None or expected_tail is None:
        raise ValueError("Refresh before unassigning the truck.")
    _validate_cycle_request(assignment, mission, expected_cycle, expected_tail)
    if str(assignment.assigned_truck_id or "") != str(expected_truck_id or ""):
        raise ValueError("Assigned truck changed. Refresh before unassigning the truck.")
    _validate_precompletion_assignment(assignment, mission, "UNASSIGN TRUCK")
    work = _locked_current_fuel_work_state(assignment, mission)
    if assignment.assigned_truck_id is None:
        return FuelInterruptionResult(False, int(asset_state.revision if asset_state else 0), assignment, work)
    if not _fuel_work_has_begun(work, assignment):
        old_truck_id=assignment.assigned_truck_id
        assignment.assigned_truck_id=None
        assignment.truck_assigned_at_utc=None
        assignment.transfer_fuel_gallons=None
        if work:
            work.physical_truck_id=None
            work.physical_transfer_gallons=None
        _new_fuel_audit(operation,assignment,user,"unassign_truck","assigned_truck_id",old_truck_id,None,
            "Dispatcher cleared the truck before physical ON; preparation preserved.",fuel_work_state=work,now_utc=now_utc)
        asset_state=record_nightly_operational_change(asset_state,operation.id)
        db.session.flush()
        observe(gateway, assignment, spear_before, 'unassignment', actor=user, now=now_utc)
        return FuelInterruptionResult(True,int(asset_state.revision),assignment,work)
    tanks = _locked_tank_states(work)
    events = _locked_fueling_events(work)
    event = None
    _retire_visit(gateway, operation, assignment, mission, work, tanks, user, now_utc, "unassign_truck")
    old_truck_id = assignment.assigned_truck_id
    assignment.assigned_truck_id = None
    assignment.truck_assigned_at_utc = None
    assignment.resource_assignment_by_user_id = user.id
    assignment.transfer_fuel_gallons = None
    old_work = work
    work = new_work(assignment, _normalize_tail(mission.assigned_tail_number), user, now_utc)
    work.physical_required_fuel_lbs = mission.planned_fuel_load
    _copy_same_aircraft_preparation(old_work, work, tanks)
    if old_work and old_work.off_at_utc:
        old_assignment, old_mission = _segment_views(assignment, mission, old_work)
        _try_finalize_retired_segment(gateway, user, old_assignment, old_mission, old_work, now_utc)
        event = NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=old_work.id).first()
    _clear_hold_after_explicit_resolution(gateway, operation, assignment, mission, work)
    audit = _new_fuel_audit(operation, assignment, user, "unassign_truck", "assigned_truck_id",
        old_truck_id, None, "Dispatcher unassigned the fuel truck.", fuel_work_state=work, now_utc=now_utc)
    _record_assigned_fueler_update(assignment, ("Truck: UNASSIGNED",), now_utc=now_utc)
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    observe(gateway, assignment, spear_before, 'unassignment', actor=user, now=now_utc)
    return FuelInterruptionResult(True, int(asset_state.revision), assignment, work,
        fueling_event=event, audit_entries=(audit,))


def _retire_visit(gateway, operation, assignment, mission, work, tanks, user, now, action):
    if work is None or work.physical_finalized_at_utc:
        return
    if work.manual_on_at_utc or work.confirmed_on_at_utc:
        if work.off_at_utc is None:
            raise ValueError("Record physical TRUCK OFF for this visit before changing resources.")
        return  # Pending Actuals/APU/T/F remain on this exact retired segment.
    if assignment.transfer_fuel_gallons not in (None, 0):
        raise ValueError("REVIEW REQUIRED: transferred fuel without trusted physical ON.")
    _archive_nontransfer_segment(operation, assignment, work, tanks, user, now, action)
    work.ended_early_at_utc = now
    work.ended_early_by_user_id = user.id
    work.ended_early_reason = "Preparation retired without physical truck ON."
    work.physical_finalized_at_utc = now
    work.physical_finalized_by_user_id = user.id


def _copy_same_aircraft_preparation(previous, target, tanks):
    if previous is None:
        return
    for name in ("apu_running", "apu_confirmed_at_utc", "apu_allowance_lbs", "automatic_apu_allowance_lbs",
            "apu_override_enabled", "apu_override_allowance_lbs", "applied_apu_rate_thousand_lbs_per_hour", "apu_source_tank_code"):
        setattr(target, name, getattr(previous, name))
    no_transfer = previous.physical_transfer_gallons in (None, 0) and all(t.actual_lbs == t.remaining_lbs for t in tanks)
    for tank in tanks:
        reading = tank.actual_lbs if tank.actual_lbs is not None else tank.remaining_lbs if no_transfer else None
        if reading is not None:
            target.tank_states.append(NeoScorpionFuelTankState(tank_code=tank.tank_code, remaining_lbs=reading))


def confirm_assignment_tail(
    gateway, user, assignment_id, *, required_fuel=None, expected_cycle=None,
    expected_tail=None, now_utc=None,
):
    # The legacy required_fuel keyword is accepted for callers already using
    # it, but acknowledging a tail never changes the fuel plan or work state.
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
    _validate_cycle_request(assignment, mission, expected_cycle, expected_tail)
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
    old_work = _locked_fuel_work_state_for_tail(assignment, old_tail)
    if old_work is None and assignment.transfer_fuel_gallons not in (None,0):
        legacy_gallons=assignment.transfer_fuel_gallons
        old_work=new_work(assignment,old_tail,user,now_utc)
        # An assignment-wide reading with no work record proves no physical
        # tail/interval. Retain its value in review audit, never allocate it to
        # the replacement or manufacture ON/OFF evidence.
        old_work.physical_transfer_review=True
        _new_fuel_audit(operation,assignment,user,"segment_transfer_review_required",
            "transfer_fuel_gallons",str(legacy_gallons),None,
            "Legacy assignment T/F has no physical work record; explicit review required.",
            fuel_work_state=old_work,now_utc=now_utc)
    if old_work and assignment.completed_at_utc is not None and old_work.physical_finalized_at_utc is None:
        old_work.physical_finalized_at_utc = assignment.completed_at_utc
        old_work.physical_finalized_by_user_id = assignment.completed_by_user_id
    if old_work and not old_work.physical_finalized_at_utc and old_work.physical_sequence_number is None and (old_work.manual_on_at_utc or old_work.confirmed_on_at_utc or old_work.on_at_utc or assignment.transfer_fuel_gallons):
        # Pre-segment live work has no trustworthy gallon allocation. Retain
        # the old value in an audit, require explicit review, never backfill it.
        if old_work.visit_number is None:
            maximum = db.session.query(db.func.max(NeoScorpionFuelWorkState.visit_number)).filter_by(fuel_assignment_id=assignment.id).scalar() or 0
            old_work.visit_number = maximum + 1
        latest = db.session.query(db.func.max(NeoScorpionFuelingEvent.sequence_number)).filter_by(fuel_work_state_id=old_work.id).scalar() or 0
        old_work.physical_sequence_number = latest + 1
        old_work.physical_cycle_number = old_work.manual_on_cycle_number or int(assignment.current_cycle_number or 1)
        old_work.physical_cycle_type = _assignment_cycle_type(assignment)
        old_work.physical_transfer_gallons = None
        old_work.physical_transfer_review = True
        old_work.physical_required_fuel_lbs = mission.planned_fuel_load
        old_facts = old_work.performance_context or {}
        old_work.physical_truck_id = old_work.manual_on_truck_id or old_facts.get("truck_id")
        old_work.physical_fueler_user_id = old_facts.get("fueler_id") or old_work.manual_on_by_user_id
        _new_fuel_audit(operation, assignment, user, "segment_transfer_review_required",
            "transfer_fuel_gallons", str(assignment.transfer_fuel_gallons), None,
            "Legacy assignment-wide T/F requires explicit physical-tail allocation.",
            fuel_work_state=old_work, now_utc=now_utc)
    elif old_work and old_work.physical_transfer_review and assignment.transfer_fuel_gallons not in (None,0):
        _new_fuel_audit(operation,assignment,user,"segment_transfer_review_required","transfer_fuel_gallons",
            str(assignment.transfer_fuel_gallons),None,"Retained unallocated legacy T/F before switching the current visit.",
            fuel_work_state=old_work,now_utc=now_utc)
    # Active projection is not source truth. A new tail never inherits old T/F.
    replacement_work = _locked_fuel_work_state_for_tail(assignment, current_tail)
    if replacement_work is None:
        replacement_work = new_work(assignment, current_tail, user, now_utc)
        replacement_work.physical_required_fuel_lbs = mission.planned_fuel_load
        measurement = _latest_trusted_completed_tail_measurement(operation,current_tail)
        if measurement:
            for code, reading in measurement["actual_by_tank"].items():
                replacement_work.tank_states.append(NeoScorpionFuelTankState(tank_code=code,remaining_lbs=reading))
            _new_fuel_audit(operation,assignment,user,"confirm_tail",TAIL_SWAP_INHERITED_EVENT_AUDIT_FIELD,
                current_tail,str(measurement["event"].id),
                "Verified same-tail completed Actual carried into Remaining only; fresh Actual/APU required.",
                fuel_work_state=replacement_work,now_utc=now_utc)
        assignment.completed_at_utc = None
        assignment.completed_by_user_id = None
        assignment.fuel_on_board_at_utc = None
        assignment.review_status = "pending"
        mission.fuel_status = "assigned" if assignment.assigned_fueler_user_id else "waiting"
        mission.fuel_completed_at_utc = None
    assignment.transfer_fuel_gallons = replacement_work.physical_transfer_gallons
    assignment.confirmed_tail_number = current_tail
    current_work_state = _locked_fuel_work_state_for_tail(assignment, current_tail)
    confirmation_audit = _new_fuel_audit(
        operation,
        assignment,
        user,
        "confirm_tail",
        "confirmed_tail_number",
        old_tail,
        current_tail,
        "Dispatcher acknowledged the mission tail; fuel work and readings were unchanged.",
        fuel_work_state=current_work_state,
        now_utc=now_utc,
    )
    asset_state = record_nightly_operational_change(asset_state, operation.id)
    db.session.flush()
    return FuelInterruptionResult(
        True,
        int(asset_state.revision),
        assignment,
        current_work_state,
        audit_entries=(confirmation_audit,),
    )


def end_fuel_work_early(
    gateway,
    user,
    assignment_id,
    reason,
    *,
    now_utc=None,
    physical_work_id=None,
    physical_sequence=None,
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
    source_work = None
    if physical_work_id is not None:
        assignment, mission, source_work = _locked_source_segment(assignment, mission,
            physical_work_id, physical_sequence, user, dispatcher=True)
    _validate_precompletion_assignment(assignment, mission, "END EARLY")
    confirmed_tail = _effective_confirmed_tail(assignment, mission)
    fuel_work_state = _locked_fuel_work_state_for_tail(
        assignment,
        confirmed_tail,
    )
    if fuel_work_state is None:
        raise ValueError("No fuel work exists to END EARLY.")
    if fuel_work_state.physical_transfer_review:
        raise ValueError("REVIEW REQUIRED: verify this segment's T/F before END EARLY.")
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
            mission,
            fuel_work_state,
            tank_states,
            existing_events,
            now_utc,
            changed_by_user=user,
        )
    elif fuel_work_state.physical_sequence_number is not None:
        _archive_nontransfer_segment(operation, assignment, fuel_work_state, tank_states, user, now_utc, "end_early")
        fuel_work_state.physical_finalized_at_utc = now_utc
        fuel_work_state.physical_finalized_by_user_id = user.id
    assignment.transfer_fuel_gallons = None
    from app.services.neoscorpion_inventory import expire_pending
    if fuel_work_state.physical_truck_id:
        expire_pending(operation, fuel_work_state.physical_truck_id,
            next_work_id=fuel_work_state.id, reason='next_physical_end_early', actor=user)
    fuel_work_state.ended_early_at_utc = now_utc
    fuel_work_state.ended_early_by_user_id = user.id
    fuel_work_state.ended_early_reason = reason
    from app.services.neoscorpion_submission import invalidate
    invalidate(fuel_work_state)
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
        .populate_existing()
        .first()
    )
    if not assignment_row:
        raise ValueError(
            f"Fuel assignment was not found for the current sort operation during {action_label}."
        )
    if has_request_context() and not request.form.get("physical_work_id"):
        _check_dispatch_segment_reference(assignment_row[0],request.form)
    return assignment_row


def _check_dispatch_segment_reference(assignment,form):
    if "expected_work_ref" in form and str(form.get("expected_work_ref") or "") != str(
            assignment.active_work_state_id if assignment and assignment.active_work_state_id else ""):
        raise FuelerDataConflict()


def _locked_current_fuel_work_state(assignment, mission):
    tail_number = _normalize_tail(mission.assigned_tail_number)
    if not tail_number:
        raise ValueError("Fuel assignment does not have a tail number.")
    return current_work(assignment, tail_number, lock=True)


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
    return current_work(assignment, tail_number, lock=True)


def _locked_tank_states(fuel_work_state):
    if fuel_work_state is None:
        return []
    return (
        NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=fuel_work_state.id,
        )
        .with_for_update()
        .populate_existing()
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
        .populate_existing()
        .all()
    )


def _fuel_work_has_begun(fuel_work_state, assignment=None):
    work=fuel_work_state
    return bool(work and (work.manual_on_at_utc or work.confirmed_on_at_utc or work.off_at_utc
        or work.physical_finalized_at_utc or (work.on_at_utc and work.physical_truck_id
        and work.truck_segment_started_at_utc and work.truck_segment_started_at_utc <= work.on_at_utc)))


def _validate_fueler_work_access(assignment, mission, fuel_work_state):
    _validate_precompletion_assignment(assignment, mission, "Fueler entry")
    if assignment.operational_status == "hold_review":
        raise ValueError(
            "REVIEW REQUIRED. A dispatcher must resolve the assignment before fuel work continues."
        )
    if fuel_work_state is not None and fuel_work_state.ended_early_at_utc is not None:
        raise ValueError("This tail's fuel work was ENDED EARLY and cannot be edited.")
    if fuel_work_state is not None and fuel_work_state.physical_finalized_at_utc is not None:
        raise ValueError("This physical segment is finalized; use an authorized audited correction.")


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
    if fuel_work_state is not None and fuel_work_state.ended_early_at_utc is not None:
        return "The current-tail work was Ended Early."
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
    if assignment.operational_status == "hold_review" and assignment.active_work_state_id:
        from app.services.neoscorpion_submission import invalidate
        invalidate(db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id))
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
    entry.fuel_assignment = getattr(assignment, "_parent_assignment", assignment)
    db.session.add(entry)
    return entry


def _fueling_event_actual_measurement(event, tail_number):
    tail_number = _normalize_tail(tail_number)
    tank_layout = tank_layout_for_tail(tail_number)
    if not tail_number or not tank_layout or event is None:
        return None
    snapshots_by_code = {
        snapshot.tank_code: snapshot for snapshot in event.tank_snapshots
    }
    actual_by_tank = {}
    for tank_code, _tank_label in tank_layout:
        snapshot = snapshots_by_code.get(tank_code)
        if snapshot is None or snapshot.actual_lbs is None:
            return None
        actual_by_tank[tank_code] = int(snapshot.actual_lbs)
    return {
        "event": event,
        "actual_by_tank": actual_by_tank,
        "total_lbs": sum(actual_by_tank.values()),
    }


def _latest_trusted_completed_tail_measurement(operation, tail_number):
    tail_number = _normalize_tail(tail_number)
    if not operation or not tail_number:
        return None
    source_row = (
        db.session.query(
            NeoScorpionFuelingEvent,
            NeoScorpionFuelAssignment,
        )
        .join(
            NeoScorpionFuelAssignment,
            NeoScorpionFuelAssignment.id
            == NeoScorpionFuelingEvent.fuel_assignment_id,
        )
        .filter(
            NeoScorpionFuelingEvent.sort_date_operation_id == operation.id,
            NeoScorpionFuelingEvent.tail_number == tail_number,
            NeoScorpionFuelingEvent.ended_at_utc.isnot(None),
        )
        # Follow-up creation already loaded these immutable snapshots under
        # the operation lock. Reuse them; load once only for a different tail.
        .options(lazyload(NeoScorpionFuelingEvent.tank_snapshots))
        .order_by(
            NeoScorpionFuelingEvent.ended_at_utc.desc(),
            NeoScorpionFuelingEvent.id.desc(),
        )
        .first()
    )
    if source_row is None:
        return None
    source_event, source_assignment = source_row
    # Never fall back past a newer active/incomplete physical event. The latest
    # event must itself belong to a canonically completed fuel assignment.
    if (
        source_assignment.completed_at_utc is None
        or source_assignment.review_status != "complete"
    ):
        return None
    return _fueling_event_actual_measurement(source_event, tail_number)


def _validated_tail_swap_inherited_measurement(
    operation,
    assignment,
    mission,
    fuel_work_state,
    *,
    tank_states=None,
):
    if not operation or not assignment or not mission or fuel_work_state is None:
        return None
    current_tail = _normalize_tail(mission.assigned_tail_number)
    if (
        not current_tail
        or _effective_confirmed_tail(assignment, mission) != current_tail
        or _normalize_tail(fuel_work_state.tail_number) != current_tail
        or assignment.transfer_fuel_gallons not in (None, 0)
        or fuel_work_state.on_at_utc is not None
        or fuel_work_state.manual_on_at_utc is not None
        or fuel_work_state.off_at_utc is not None
        or fuel_work_state.ended_early_at_utc is not None
        or fuel_work_state.truck_segment_started_at_utc is not None
    ):
        return None

    inheritance_audit = (
        NeoScorpionFuelAuditEntry.query.filter_by(
            sort_date_operation_id=operation.id,
            fuel_assignment_id=assignment.id,
            action="confirm_tail",
            fuel_work_state_id=fuel_work_state.id,
            field_name=TAIL_SWAP_INHERITED_EVENT_AUDIT_FIELD,
            old_value=current_tail,
        )
        .order_by(
            NeoScorpionFuelAuditEntry.created_at.desc(),
            NeoScorpionFuelAuditEntry.id.desc(),
        )
        .first()
    )
    if inheritance_audit is None:
        return None
    try:
        source_event_id = int(inheritance_audit.new_value)
    except (TypeError, ValueError):
        return None

    source_row = (
        db.session.query(
            NeoScorpionFuelingEvent,
            NeoScorpionFuelAssignment,
        )
        .join(
            NeoScorpionFuelAssignment,
            NeoScorpionFuelAssignment.id
            == NeoScorpionFuelingEvent.fuel_assignment_id,
        )
        .filter(
            NeoScorpionFuelingEvent.id == source_event_id,
            NeoScorpionFuelingEvent.sort_date_operation_id == operation.id,
            NeoScorpionFuelingEvent.tail_number == current_tail,
        )
        .options(selectinload(NeoScorpionFuelingEvent.tank_snapshots))
        .first()
    )
    if source_row is None:
        return None
    source_event, source_assignment = source_row
    if (
        source_assignment.completed_at_utc is None
        or source_assignment.review_status != "complete"
    ):
        return None
    measurement = _fueling_event_actual_measurement(source_event, current_tail)
    if measurement is None:
        return None

    states = list(fuel_work_state.tank_states) if tank_states is None else list(tank_states)
    states_by_code = {state.tank_code: state for state in states}
    for tank_code, _tank_label in tank_layout_for_tail(current_tail):
        state = states_by_code.get(tank_code)
        if (
            state is None
            or state.remaining_lbs != measurement["actual_by_tank"][tank_code]
            or state.actual_lbs is not None
        ):
            return None
    return measurement


def _segment_movement_status(assignment, fuel_work_state, tank_states, *, existing_events=()):
    prior = [event for event in existing_events if int(event.cycle_number or 1) == int(assignment.current_cycle_number or 1)]
    if prior and fuel_work_state is not None:
        latest = max(prior, key=lambda event: event.sequence_number)
        baseline = {snapshot.tank_code: snapshot.actual_lbs for snapshot in latest.tank_snapshots}
        segment_tanks = [SimpleNamespace(tank_code=state.tank_code,
            remaining_lbs=baseline.get(state.tank_code), actual_lbs=state.actual_lbs) for state in tank_states]
        segment_work = SimpleNamespace(tail_number=fuel_work_state.tail_number,
            apu_running=fuel_work_state.apu_running,
            apu_allowance_lbs=(max(0, fuel_work_state.apu_allowance_lbs - latest.apu_allowance_lbs)
                if fuel_work_state.apu_allowance_lbs is not None and latest.apu_allowance_lbs is not None else None))
        _validate_cycle_direction(assignment, segment_work, segment_tanks)
        return classify_fuel_movement(assignment, segment_work, tank_states=segment_tanks)
    if fuel_work_state is None:
        return (
            "moved"
            if assignment.transfer_fuel_gallons is not None
            and assignment.transfer_fuel_gallons > 0
            else "not_moved"
        )
    if not any(state.actual_lbs is not None for state in tank_states):
        if (assignment.transfer_fuel_gallons is None and
                (fuel_work_state.manual_on_at_utc or fuel_work_state.confirmed_on_at_utc)):
            return "unknown"
        return (
            "moved"
            if assignment.transfer_fuel_gallons is not None
            and assignment.transfer_fuel_gallons > 0
            else "not_moved"
        )
    _validate_cycle_direction(assignment, fuel_work_state, tank_states)
    return classify_fuel_movement(
        assignment,
        fuel_work_state,
        tank_states=tank_states,
    )


def _archive_nontransfer_segment(operation, assignment, work, tanks, user, now, action):
    """Retain a confirmed non-transfer physical interval without a fuel posting."""
    snapshot = dict(work=_fuel_rollback_values(work), tanks=[_fuel_rollback_values(tank) for tank in tanks],
                    closed_at=prof_iso(now), movement="not_moved")
    _new_fuel_audit(operation, assignment, user, action, "closed_physical_segment", None,
        json.dumps(snapshot), "Closed physical segment without fuel transfer; excluded from PROF.",
        fuel_work_state=work, now_utc=now)


def _close_current_truck_segment(
    gateway,
    operation,
    assignment,
    mission,
    fuel_work_state,
    tank_states,
    existing_events,
    ended_at_utc,
    *,
    changed_by_user,
):
    if fuel_work_state is None:
        raise ValueError("Fuel work is required to close a truck segment.")
    if fuel_work_state.physical_transfer_review:
        raise ValueError("REVIEW REQUIRED: ambiguous assignment-wide T/F cannot be posted.")
    if fuel_work_state.physical_sequence_number is not None:
        posted = next((event for event in existing_events if event.sequence_number == fuel_work_state.physical_sequence_number), None)
        if posted is not None:
            return posted
        if (fuel_work_state.physical_truck_id != assignment.assigned_truck_id or
                fuel_work_state.physical_cycle_number != int(assignment.current_cycle_number or 1)):
            raise ValueError("Physical segment identity changed; dispatcher review required.")
    if assignment.assigned_truck_id is None:
        raise ValueError("Assign the truck used before closing this fuel segment.")
    transfer_gallons = (fuel_work_state.physical_transfer_gallons
        if fuel_work_state.physical_sequence_number is not None else assignment.transfer_fuel_gallons)
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
        .populate_existing()
        .first()
    )
    if truck_row is None:
        raise ValueError("The assigned truck is missing from tonight's assets.")
    nightly_truck, fuel_truck = truck_row
    cycle_type = _assignment_cycle_type(assignment)
    covered_by_new_reading = (fuel_work_state.off_inventory_epoch is not None
                             and nightly_truck.inventory_epoch != fuel_work_state.off_inventory_epoch)
    from app.services.neoscorpion_inventory import transfer_covered, expire_pending
    covered_by_new_reading = transfer_covered(operation, nightly_truck, fuel_work_state, covered_by_new_reading)
    expire_pending(operation, fuel_truck.id, next_work_id=fuel_work_state.id, reason='physical_transfer_posting', actor=changed_by_user)
    if cycle_type == "defuel":
        if (not (nightly_truck.inventory_verified or nightly_truck.inventory_pending_epoch == nightly_truck.inventory_epoch)
                or nightly_truck.current_gallons is None):
            raise ValueError("Verify truck gallons before completing DEFUEL capacity accounting.")
        post_transfer_gallons = nightly_truck.current_gallons if covered_by_new_reading else nightly_truck.current_gallons + transfer_gallons
        if (
            fuel_truck.capacity_gallons is not None
            and post_transfer_gallons > fuel_truck.capacity_gallons
        ):
            raise ValueError("DEFUEL T/F would exceed the assigned truck's capacity.")
    else:
        reliable = (nightly_truck.inventory_verified or
                    nightly_truck.inventory_pending_epoch == nightly_truck.inventory_epoch) and nightly_truck.current_gallons is not None
        discrepancy = not reliable or transfer_gallons > nightly_truck.current_gallons
        if covered_by_new_reading:
            discrepancy = False
        post_transfer_gallons = (
            nightly_truck.current_gallons if covered_by_new_reading else
            nightly_truck.current_gallons - transfer_gallons if not discrepancy else None
        )

    sequence_number = fuel_work_state.physical_sequence_number or max(
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
        event_type=cycle_type,
        cycle_number=int(assignment.current_cycle_number or 1),
        started_at_utc=(
            fuel_work_state.manual_on_at_utc
            or (fuel_work_state.confirmed_on_at_utc if (
                fuel_work_state.confirmed_on_at_utc is not None
                and (fuel_work_state.truck_segment_started_at_utc is None
                     or fuel_work_state.confirmed_on_at_utc >= fuel_work_state.truck_segment_started_at_utc)
            ) else None)
        ),
        ended_at_utc=ended_at_utc,
        transfer_fuel_gallons=transfer_gallons,
    )
    _populate_fueling_event_snapshot(
        fueling_event,
        operation,
        assignment,
        mission,
        fuel_work_state,
        tank_states,
    )
    db.session.add(fueling_event)
    facts = dict(fuel_work_state.performance_context or {})
    if facts.get('version') == 4:
        from app.services.neoscorpion_resource_timing import finalized_resources, truck_interval
        resources=finalized_resources(facts.get('resource_timing') or {},fuel_work_state,datetime.utcnow())
        interval=truck_interval(resources,fueling_event.started_at_utc,fueling_event.ended_at_utc)
        resources['excluded_wait_intervals']=interval['excluded_wait_intervals']
        resources['excluded_wait_minutes']=str(interval['excluded_wait_minutes'])
        facts['resource_timing']=resources
    exclusions = list(facts.get("exclusions", []))
    if fuel_work_state.off_at_utc is None or fuel_work_state.ended_early_at_utc:
        exclusions.append("Interrupted truck segment")
    if assignment.hold_at_utc:
        exclusions.append("Hold/review affected this cycle")
    facts.update(actuals_received_at=prof_iso(fuel_work_state.actuals_received_at_utc),
                 preparation_completed_at=prof_iso(fuel_work_state.preparation_received_at_utc),
                 exclusions=exclusions, inventory_epoch=fuel_work_state.off_inventory_epoch)
    if facts.get('version') == 4:
        from app.services.neoscorpion_resource_timing import fueler_work_end
        facts['fueler_work_end_at']=prof_iso(fueler_work_end(fuel_work_state))
    facts.update(physical_work_id=fuel_work_state.id, physical_sequence=sequence_number,
                 physical_tail=fuel_work_state.tail_number)
    fueling_event.performance_snapshot = facts
    if cycle_type != "defuel" and discrepancy:
        db.session.flush()
        db.session.add(NeoScorpionTruckInventoryAudit(
            sort_date_operation_id=operation.id,
            fuel_truck_id=fuel_truck.id,
            fuel_assignment_id=assignment.id,
            fueling_event_id=fueling_event.id,
            action="discrepancy",
            prior_recorded_gallons=nightly_truck.current_gallons,
            transfer_fuel_gallons=transfer_gallons,
            reason=("Recorded truck gallons unknown" if nightly_truck.current_gallons is None else
                    "Recorded truck gallons already unverified" if not nightly_truck.inventory_verified else
                    "Physical T/F exceeds recorded truck gallons"),
            actor_user_id=changed_by_user.id,
            created_at=datetime.utcnow(),
        ))
        nightly_truck.inventory_verified = False
        nightly_truck.inventory_pending_epoch = None
    else:
        nightly_truck.current_gallons = post_transfer_gallons
        pending_works = db.session.query(NeoScorpionFuelWorkState).join(NeoScorpionFuelAssignment,
            NeoScorpionFuelAssignment.id == NeoScorpionFuelWorkState.fuel_assignment_id).join(
            SortDateMission, SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id).filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            db.or_(NeoScorpionFuelWorkState.physical_truck_id == fuel_truck.id,
                   db.and_(NeoScorpionFuelWorkState.physical_truck_id.is_(None),
                           NeoScorpionFuelAssignment.assigned_truck_id == fuel_truck.id)),
            NeoScorpionFuelWorkState.id != fuel_work_state.id,
            NeoScorpionFuelWorkState.physical_finalized_at_utc.is_(None),
            NeoScorpionFuelWorkState.off_at_utc.is_not(None),
        ).all()
        # A baseline correction advances the epoch without measuring away
        # outstanding transfers. Only explicit reading coverage can retire
        # an older OFF visit; epoch equality alone is not ownership evidence.
        pending=any(not transfer_covered(operation,nightly_truck,work,
            work.off_inventory_epoch is not None and work.off_inventory_epoch != nightly_truck.inventory_epoch)
            for work in pending_works)
        if pending:
            nightly_truck.inventory_verified=False
            nightly_truck.inventory_pending_epoch=nightly_truck.inventory_epoch
        elif nightly_truck.inventory_pending_epoch == nightly_truck.inventory_epoch:
            nightly_truck.inventory_verified = True
            nightly_truck.inventory_pending_epoch = None
        if covered_by_new_reading:
            db.session.flush()
            db.session.add(NeoScorpionTruckInventoryAudit(
                sort_date_operation_id=operation.id, fuel_truck_id=fuel_truck.id,
                fuel_assignment_id=assignment.id, fueling_event_id=fueling_event.id,
                action="verified", prior_recorded_gallons=nightly_truck.current_gallons,
                transfer_fuel_gallons=transfer_gallons, confirmed_gallons=nightly_truck.current_gallons,
                reason="Physical OFF predates a newly verified reading; transfer already covered, no second deduction.",
                actor_user_id=changed_by_user.id, created_at=datetime.utcnow()))
    if cycle_type == "defuel" and not _truck_sumped_after(fuel_work_state,nightly_truck):
        nightly_truck.status = "needs_sump"
        from app.services.neoscorpion_resource_timing import record_truck_availability
        record_truck_availability(nightly_truck,fuel_truck,changed_by_user,'defuel_needs_sump',ended_at_utc)
        hold_active_assignments_for_truck(
            operation,
            fuel_truck,
            changed_by_user,
            "Assigned truck completed a DEFUEL and requires sumping.",
            now_utc=ended_at_utc,
            exclude_assignment_id=assignment.id,
        )
    fuel_work_state.physical_finalized_at_utc = ended_at_utc
    fuel_work_state.physical_finalized_by_user_id = changed_by_user.id
    return fueling_event


def _populate_fueling_event_snapshot(
    fueling_event,
    operation,
    assignment,
    mission,
    fuel_work_state,
    tank_states,
):
    fueling_event.fueler_user_id = (fuel_work_state.physical_fueler_user_id
        if fuel_work_state.physical_sequence_number is not None else assignment.assigned_fueler_user_id)
    fueling_event.required_fuel_lbs = mission.planned_fuel_load
    fueling_event.apu_running = fuel_work_state.apu_running
    fueling_event.apu_allowance_lbs = fuel_work_state.apu_allowance_lbs
    fueling_event.apu_source_tank_code = fuel_work_state.apu_source_tank_code
    tank_states_by_code = {state.tank_code: state for state in tank_states}
    tank_layout = tank_layout_for_tail(fuel_work_state.tail_number)
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
    fueling_event.neo_fuel_lbs = neo_fuel_lbs
    detailed_aircraft_type = detailed_aircraft_type_for_tail(
        fuel_work_state.tail_number
    )
    fueling_event.center_fuel_lbs = (
        tank_states_by_code["ctr"].actual_lbs
        if detailed_aircraft_type == "A300"
        and tank_states_by_code.get("ctr") is not None
        else None
    )
    planned_by_tank = plan_fuel_by_tank(
        detailed_aircraft_type,
        mission.planned_fuel_load,
        remaining_lbs_by_tank={
            code: state.remaining_lbs for code, state in tank_states_by_code.items()
        },
        actual_lbs_by_tank={
            code: state.actual_lbs for code, state in tank_states_by_code.items()
        },
        apu_running=fuel_work_state.apu_running,
        apu_allowance_lbs=fuel_work_state.apu_allowance_lbs,
        apu_source_tank_code=fuel_work_state.apu_source_tank_code,
        max_lateral_imbalance_lbs=_effective_lateral_imbalance_limit(
            operation.gateway_id,
            detailed_aircraft_type,
        ),
        rebalance_remaining_floors=(
            _assignment_cycle_type(assignment) != "defuel"
        ),
    )
    for tank_code, _tank_label in tank_layout:
        tank_state = tank_states_by_code.get(tank_code)
        planned_lbs = (
            planned_by_tank.get(tank_code) if planned_by_tank is not None else None
        )
        db.session.add(
            NeoScorpionFuelingEventTankSnapshot(
                fueling_event=fueling_event,
                tank_code=tank_code,
                remaining_lbs=(tank_state.remaining_lbs if tank_state else None),
                planned_lbs=(
                    int(
                        Decimal(planned_lbs).quantize(
                            Decimal("1"),
                            rounding=ROUND_HALF_UP,
                        )
                    )
                    if planned_lbs is not None
                    else None
                ),
                actual_lbs=(tank_state.actual_lbs if tank_state else None),
            )
        )


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

    availability_scope=None
    if truck.id:
        current_operation=current_sort_operation(gateway)
        if current_operation is not None:
            availability_scope=lock_nightly_asset_scope_for_mutation(current_operation)
        truck=NeoScorpionFuelTruck.query.filter_by(id=truck.id).with_for_update().populate_existing().one()
    became_blocked = bool(
        truck.id
        and not truck.is_out_of_service
        and form.get("is_out_of_service") == "1"
    )
    interruption_scope = None
    availability_changed=(not truck.id or truck.is_active != (form.get('is_active')=='1')
                          or truck.is_out_of_service != (form.get('is_out_of_service')=='1'))
    if became_blocked:
        interruption_scope=availability_scope
    truck.truck_number = truck_number
    truck.description = (form.get("description") or "").strip()
    if 'capacity_gallons' in form:
        text_capacity = str(form.get('capacity_gallons') or '').strip()
        if text_capacity and not re.fullmatch(r'\d{1,9}', text_capacity):
            raise ValueError('Truck capacity must be positive whole gallons.')
        capacity = int(text_capacity) if text_capacity else None
        if capacity is None and not truck.id:
            capacity = 10000
        if capacity is not None and capacity <= 0:
            raise ValueError('Truck capacity must be positive whole gallons.')
        if capacity is not None:
            truck.capacity_gallons = capacity
    elif truck.capacity_gallons is None and not truck.id:
        truck.capacity_gallons = 10000
    if 'remaining_fuel_gallons' in form:
        truck.remaining_fuel_gallons = _int_or_none(form.get("remaining_fuel_gallons"))
    if 'vendor_driver_name' in form:
        truck.vendor_driver_name = (form.get("vendor_driver_name") or "").strip()
    truck.is_active = form.get("is_active") == "1"
    truck.is_out_of_service = form.get("is_out_of_service") == "1"
    if availability_changed:
        from app.services.neoscorpion_resource_timing import record_master_availability
        record_master_availability(truck,user,'master_settings',datetime.utcnow())
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
    truck=NeoScorpionFuelTruck.query.filter_by(id=truck.id).with_for_update().populate_existing().one()
    truck.is_active = False
    from app.services.neoscorpion_resource_timing import record_master_availability
    record_master_availability(truck,user,'master_deactivated',datetime.utcnow())
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
    row_height = None
    if "mission_row_height_percent" in form:
        value = str(form.get("mission_row_height_percent", "")).strip()
        if not re.fullmatch(r"[0-9]{2,3}", value) or not 75 <= int(value) <= 150:
            raise ValueError("Mission Row Height must be a whole percentage from 75 to 150.")
        row_height = int(value)
    if "ready_assignment_pulse_enabled" in form and form.get("ready_assignment_pulse_enabled") not in {"0", "1"}:
        raise ValueError("READY Assignment Pulse must be ON or OFF.")
    if 'truck_fuel_discrepancy_threshold_gallons' in form:
        threshold_text = str(form.get('truck_fuel_discrepancy_threshold_gallons',''))
        if not re.fullmatch(r'\d{1,9}', threshold_text):
            raise ValueError('Truck fuel discrepancy threshold must be nonnegative whole gallons.')
    settings = ensure_neoscorpion_settings(gateway)
    old_density = settings.fuel_density_lbs_per_gallon
    old_excess_threshold = settings.neo_fuel_excess_alert_gallons
    old_ready_pulse = settings.ready_assignment_pulse_enabled
    old_row_height = settings.mission_row_height_percent
    if row_height is not None:
        settings.mission_row_height_percent = row_height
    if "ready_assignment_pulse_enabled" in form:
        settings.ready_assignment_pulse_enabled = form.get("ready_assignment_pulse_enabled") == "1"
    if "neo_fuel_excess_alert_gallons" in form:
        submitted_excess = str(form.get("neo_fuel_excess_alert_gallons", "")).strip()
        if not re.fullmatch(r"[0-9]+", submitted_excess):
            raise ValueError("Neo Fuel Excess Alert must be a nonnegative whole number of gallons.")
        if len(submitted_excess) > 10 or int(submitted_excess) > 2147483647:
            raise ValueError("Neo Fuel Excess Alert is too large.")
        settings.neo_fuel_excess_alert_gallons = int(submitted_excess)
    if 'truck_fuel_discrepancy_threshold_gallons' in form:
        settings.truck_fuel_discrepancy_threshold_gallons = int(threshold_text)
    submitted_threshold = str(form.get("red_assignment_alert_threshold_minutes", "")).strip()
    if not re.fullmatch(r"[0-9]+", submitted_threshold):
        raise ValueError("Red assignment alert threshold must be a nonnegative whole number of minutes.")
    if len(submitted_threshold) > 10:
        raise ValueError("Red assignment alert threshold is too large.")
    threshold = int(submitted_threshold)
    if threshold > 2147483647:
        raise ValueError("Red assignment alert threshold is too large.")
    threshold_changed = settings.red_assignment_alert_threshold_minutes != threshold
    settings.red_assignment_alert_threshold_minutes = threshold
    density = _decimal_or_none(form.get("fuel_density_lbs_per_gallon"))
    if density is None:
        settings.fuel_density_lbs_per_gallon = None
    else:
        settings.fuel_density_lbs_per_gallon = float(
            _positive_decimal(density, "Fuel density must be greater than zero.")
        )
    settings.planning_inbound_fuel_fallback_lbs = display_thousands_to_lbs(
        form.get("planning_inbound_fuel_fallback")
    )
    settings.fob_difference_threshold_lbs = _int_or_none(form.get("fob_difference_threshold_lbs"))
    settings.tf_vs_estimated_threshold_lbs = _int_or_none(
        form.get("tf_vs_estimated_threshold_lbs")
    )
    if current_user and getattr(current_user, "is_authenticated", False):
        settings.updated_by_user_id = current_user.id
    if (threshold_changed or old_density != settings.fuel_density_lbs_per_gallon
            or old_excess_threshold != settings.neo_fuel_excess_alert_gallons
            or old_ready_pulse != settings.ready_assignment_pulse_enabled
            or old_row_height != settings.mission_row_height_percent):
        operation = current_sort_operation(gateway)
        if operation is not None:
            operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
            record_nightly_operational_change(asset_state, operation.id)
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
                has_other_aircraft_settings = bool(
                    setting.assignment_pump_rate_gallons_per_minute is not None
                    or setting.max_lateral_imbalance_lbs is not None
                )
                if not has_other_aircraft_settings:
                    db.session.delete(setting)
                    changed = True
                elif Decimal(setting.apu_rate_thousand_lbs_per_hour) != target_rate:
                    setting.apu_rate_thousand_lbs_per_hour = target_rate
                    setting.updated_by_user_id = user.id
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


def save_assignment_planning_settings(gateway, user, form):
    settings = ensure_neoscorpion_settings(gateway)
    advance = getattr(settings, "advance_setup_window_minutes", None)
    if "advance_setup_window_minutes" in form:
        raw = (form.get("advance_setup_window_minutes") or "").strip()
        if raw and (not raw.isdigit() or int(raw) > 2147483647):
            raise ValueError("Advance Setup Window must be a valid nonnegative whole number of minutes.")
        advance = int(raw) if raw else None
    setup_minutes = _parse_optional_minutes(
        form.get("assignment_setup_minutes"),
        "Setup Time",
    )
    finishing_minutes = _parse_optional_minutes(
        form.get("assignment_finishing_minutes"),
        "Finishing Time",
    )
    eta_safety_buffer_minutes = _parse_optional_minutes(
        form.get("assignment_eta_safety_buffer_minutes"),
        "Arrival ETA Safety Buffer",
    )
    changed = False
    for attribute, value in (
        ("advance_setup_window_minutes", advance),
        ("assignment_setup_minutes", setup_minutes),
        ("assignment_finishing_minutes", finishing_minutes),
        ("assignment_eta_safety_buffer_minutes", eta_safety_buffer_minutes),
    ):
        if getattr(settings, attribute) != value:
            setattr(settings, attribute, value)
            changed = True
    if changed:
        settings.updated_by_user_id = user.id

    existing = {
        row.aircraft_type: row
        for row in NeoScorpionAircraftFuelSetting.query.filter(
            NeoScorpionAircraftFuelSetting.gateway_id == gateway.id,
            NeoScorpionAircraftFuelSetting.aircraft_type.in_(
                NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
            ),
        )
        .with_for_update()
        .all()
    }
    for aircraft_type in NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES:
        pump_field = _assignment_pump_rate_field_name(aircraft_type)
        imbalance_field = _max_lateral_imbalance_field_name(aircraft_type)
        if pump_field not in form and imbalance_field not in form:
            continue
        target_rate = (
            _parse_optional_pump_rate(form.get(pump_field), aircraft_type)
            if pump_field in form
            else None
        )
        target_imbalance = (
            _parse_optional_lateral_imbalance(
                form.get(imbalance_field),
                aircraft_type,
            )
            if imbalance_field in form
            else None
        )
        setting = existing.get(aircraft_type)
        if setting is None and target_rate is None and target_imbalance is None:
            continue
        if setting is None:
            setting = NeoScorpionAircraftFuelSetting(
                gateway_id=gateway.id,
                aircraft_type=aircraft_type,
                apu_rate_thousand_lbs_per_hour=DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR,
                assignment_pump_rate_gallons_per_minute=target_rate,
                max_lateral_imbalance_lbs=target_imbalance,
                updated_by_user_id=user.id,
            )
            db.session.add(setting)
            existing[aircraft_type] = setting
            changed = True
            continue

        row_changed = False
        if (
            pump_field in form
            and setting.assignment_pump_rate_gallons_per_minute != target_rate
        ):
            setting.assignment_pump_rate_gallons_per_minute = target_rate
            row_changed = True
        if (
            imbalance_field in form
            and setting.max_lateral_imbalance_lbs != target_imbalance
        ):
            setting.max_lateral_imbalance_lbs = target_imbalance
            row_changed = True
        if row_changed:
            setting.updated_by_user_id = user.id
            changed = True

    if changed:
        db.session.flush()
    return AssignmentPlanningSettingsSaveResult(changed=changed)


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


def _automatic_apu_allowance_lbs(fuel_work_state):
    if fuel_work_state is None or fuel_work_state.apu_running is None:
        return None
    if fuel_work_state.automatic_apu_allowance_lbs is not None:
        return fuel_work_state.automatic_apu_allowance_lbs
    # Pre-override rows stored their automatic value directly in the legacy
    # compatibility field. Read it without changing historical data on GET.
    return fuel_work_state.apu_allowance_lbs


def _effective_apu_allowance_lbs(fuel_work_state):
    if fuel_work_state is None or fuel_work_state.apu_running is None:
        return None
    if (
        fuel_work_state.apu_running is True
        and fuel_work_state.apu_override_enabled
        and fuel_work_state.apu_override_allowance_lbs is not None
    ):
        return fuel_work_state.apu_override_allowance_lbs
    return _automatic_apu_allowance_lbs(fuel_work_state)


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
    if fuel_work_state is None:
        return (
            "moved"
            if assignment is not None
            and assignment.transfer_fuel_gallons is not None
            and assignment.transfer_fuel_gallons > 0
            else "unknown"
        )

    states = fuel_work_state.tank_states if tank_states is None else tank_states
    direction_evidence = _cycle_direction_evidence(
        assignment,
        fuel_work_state,
        states,
    )
    if direction_evidence == "expected":
        return "moved"
    if (
        assignment is not None
        and assignment.transfer_fuel_gallons is not None
        and assignment.transfer_fuel_gallons > 0
        and direction_evidence != "opposite"
    ):
        return "moved"
    if direction_evidence in {"equal", "opposite"}:
        return "not_moved"
    return "unknown"


def _assignment_cycle_type(assignment):
    cycle_type = str(
        getattr(assignment, "current_cycle_type", None) or "fuel"
    ).strip().lower()
    return cycle_type if cycle_type in NEOSCORPION_FUEL_CYCLE_TYPES else "fuel"


def _cycle_direction_evidence(assignment, fuel_work_state, tank_states):
    if fuel_work_state is None:
        return "unknown"
    tank_states_by_code = {state.tank_code: state for state in tank_states}
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
    adjusted_actual_lbs = actual_total_lbs + fuel_work_state.apu_allowance_lbs
    if adjusted_actual_lbs == remaining_total_lbs:
        return "equal"
    cycle_type = _assignment_cycle_type(assignment)
    expected = (
        adjusted_actual_lbs < remaining_total_lbs
        if cycle_type == "defuel"
        else adjusted_actual_lbs > remaining_total_lbs
    )
    return "expected" if expected else "opposite"


def _validate_cycle_direction(assignment, fuel_work_state, tank_states):
    if _cycle_direction_evidence(assignment, fuel_work_state, tank_states) != "opposite":
        return
    cycle_type = _assignment_cycle_type(assignment)
    expected_direction = (
        "aircraft to truck"
        if cycle_type == "defuel"
        else "truck to aircraft"
    )
    raise ValueError(
        "REVIEW REQUIRED: tank readings show the opposite fuel direction; "
        f"{cycle_type.upper()} must move fuel {expected_direction}."
    )


def _fuel_rows(
    operation,
    missions,
    estimated_fuel_status="INCOMPLETE",
    *,
    fuel_trucks=None,
    assignments_by_mission=None,
    fuel_work_states_by_assignment_tail=None,
    parking_by_tail=None,
    start_evidence=None,
    nightly_truck_states_by_truck_id=None,
    fueling_event_cycle_keys=None,
    apu_rates_by_aircraft_type=None,
    lateral_imbalance_limits_by_aircraft_type=None,
    fuel_density_lbs_per_gallon=None,
    planning_inbound_fallback_lbs=None,
    status_settings=None,
    now_utc=None,
    projection_sources=(),
):
    now_utc = now_utc or datetime.utcnow()
    tail_states = _tail_states_by_tail(operation)
    tail_fuel_states = _tail_fuel_states_by_tail(operation)
    from app.services.neoscorpion_tail_notes import tail_notes_by_tail
    aircraft_notes = tail_notes_by_tail(operation.gateway_id,
        (_normalize_tail(item.assigned_tail_number) for item in [*missions, *(source[0] for source in projection_sources)]))
    parking = _parking_by_tail(operation) if parking_by_tail is None else parking_by_tail
    assignments = (
        _assignments_by_mission(operation)
        if assignments_by_mission is None
        else assignments_by_mission
    )
    start_selections = nightly_truck_states_by_truck_id
    if start_selections is None:
        start_selections = {selection.fuel_truck_id: selection for selection in
            NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id).all()}
    arrivals = _arrivals_by_tail(operation)
    arrival_presence = arrival_presence_by_tail(operation)
    if fuel_trucks is None:
        fuel_trucks = _fuel_trucks(operation.gateway or _gateway_stub(operation))
    trucks = {truck.id: truck for truck in fuel_trucks}
    # An empty current-tail WorkIndex can still retain an open physical visit.
    fuel_work_states = (fuel_work_states_by_assignment_tail
                        if fuel_work_states_by_assignment_tail is not None else {})
    event_cycle_keys = fueling_event_cycle_keys or set()
    effective_apu_rates = apu_rates_by_aircraft_type or {}
    effective_lateral_limits = lateral_imbalance_limits_by_aircraft_type or {}

    rows = []
    for mission, source_assignment, source_work in [*((item, None, None) for item in missions), *projection_sources]:
        departure_required = mission.planned_fuel_load
        tail_number = _normalize_tail(mission.assigned_tail_number)
        tail_state = tail_states.get(tail_number)
        tail_fuel_state = tail_fuel_states.get(tail_number)
        assignment = source_assignment or assignments.get(mission.id)
        arrival = arrivals.get(tail_number)
        tail_presence = departure_tail_presence(mission, arrival_presence)
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
        if source_work is not None:
            current_fuel_work_state = source_work
        elif current_fuel_work_state is not None and current_fuel_work_state.visit_number is not None:
            assignment, _unused_mission = _segment_views(assignment, mission, current_fuel_work_state)
            if current_fuel_work_state.target_frozen:
                # Freeze the visit's fuel target, not the live departure clock
                # used by Dispatch sorting and SPEAR priority.
                _unused_mission.planned_datetime_utc=mission.planned_datetime_utc
                mission = _unused_mission
        elif assignment is not None and confirmed_tail_number != tail_number:
            projected = SimpleNamespace(**{column.name: getattr(assignment, column.name) for column in assignment.__table__.columns})
            projected._parent_assignment = assignment
            projected.assigned_truck = assignment.assigned_truck
            projected.assigned_fueler = assignment.assigned_fueler
            projected.sort_date_mission = mission
            projected.transfer_fuel_gallons = current_fuel_work_state.physical_transfer_gallons if current_fuel_work_state else None
            assignment = projected
        tail_mismatch = bool(
            assignment
            and confirmed_tail_number
            and tail_number != confirmed_tail_number
        )
        prior_tail_work_state = (
            fuel_work_states.get((assignment.id, confirmed_tail_number))
            if tail_mismatch else None
        )
        # The acknowledgment marker is advisory. Physical work belongs to the
        # assignment and the mission's current tail, never another mission.
        fuel_work_state = current_fuel_work_state
        work_tail_number = tail_number
        aircraft_type = _aircraft_type_for_mission(mission, tail_state)
        detailed_aircraft_type = detailed_aircraft_type_for_tail(work_tail_number)
        tank_layout = tank_layout_for_tail(work_tail_number)
        tank_states_by_code = {
            state.tank_code: state
            for state in (fuel_work_state.tank_states if fuel_work_state else ())
        }
        inherited_measurement = (
            _validated_tail_swap_inherited_measurement(
                operation,
                assignment,
                mission,
                fuel_work_state,
                tank_states=tuple(tank_states_by_code.values()),
            )
            if assignment
            else None
        )
        work_has_begun = bool(
            _fuel_work_has_begun(fuel_work_state, assignment)
            and inherited_measurement is None
            and not _unstarted_follow_up_cycle(assignment, fuel_work_state)
        )
        work_ended_early = bool(
            fuel_work_state and fuel_work_state.ended_early_at_utc
        )
        effective_hold = bool(
            assignment
            and assignment.operational_status == "hold_review"
        )
        tail_safety_label = "CONFIRM TAIL SWAP" if tail_mismatch else ""
        direction_evidence = _cycle_direction_evidence(
            assignment,
            fuel_work_state,
            tuple(tank_states_by_code.values()),
        )
        movement_status = classify_fuel_movement(
            assignment,
            fuel_work_state,
            tank_states=tuple(tank_states_by_code.values()),
        )
        has_prior_fueling_events = bool(
            fuel_work_state
            and assignment
            and (
                fuel_work_state.id,
                int(assignment.current_cycle_number or 1),
            )
            in event_cycle_keys
        )
        if has_prior_fueling_events:
            movement_status = "moved"
        apu_running = fuel_work_state.apu_running if fuel_work_state else None
        automatic_apu_allowance_lbs = _automatic_apu_allowance_lbs(
            fuel_work_state
        )
        apu_override_enabled = bool(
            fuel_work_state
            and fuel_work_state.apu_running is True
            and fuel_work_state.apu_override_enabled
        )
        apu_override_allowance_lbs = (
            fuel_work_state.apu_override_allowance_lbs
            if apu_override_enabled
            else None
        )
        apu_allowance_lbs = _effective_apu_allowance_lbs(fuel_work_state)
        apu_source_tank_code = (
            fuel_work_state.apu_source_tank_code if fuel_work_state else None
        )
        apu_source_valid = bool(
            apu_running is not True
            or apu_source_tank_code in {code for code, _label in tank_layout}
        )
        remaining_readings_complete = bool(tank_layout) and all(
            tank_states_by_code.get(code) is not None
            and tank_states_by_code[code].remaining_lbs is not None
            for code, _label in tank_layout
        )
        planned_by_tank = (
            plan_fuel_by_tank(
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
                max_lateral_imbalance_lbs=effective_lateral_limits.get(
                    detailed_aircraft_type
                ),
                rebalance_remaining_floors=(
                    _assignment_cycle_type(assignment) != "defuel"
                ),
            )
            if remaining_readings_complete
            else None
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
                    "remaining_display": format_dispatch_thousands(
                        tank_state.remaining_lbs if tank_state else None
                    ),
                    "planned_display": (
                        format_display_thousands(planned_lbs)
                        if planned_lbs is not None
                        else "-"
                    ),
                    "actual_display": format_dispatch_thousands(
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
        fuel_load_lbs = (
            mission.planned_fuel_load + apu_allowance_lbs
            if mission.planned_fuel_load is not None
            and apu_allowance_lbs is not None
            else None
        )
        estimated_fuel = _estimated_dispatch_fuel(
            mission, assignment, tail_fuel_state, remaining_total_lbs,
            fuel_density_lbs_per_gallon, planning_inbound_fallback_lbs,
        )
        cycle_type = _assignment_cycle_type(assignment)
        planning_demand_gallons = (
            -assignment.transfer_fuel_gallons
            if cycle_type == "defuel"
            and assignment is not None
            and assignment.transfer_fuel_gallons is not None
            else None
            if cycle_type == "defuel"
            else assignment.transfer_fuel_gallons
            if assignment is not None
            and assignment.transfer_fuel_gallons is not None
            else estimated_fuel.gallons
        )
        # APU burn affects the truck's projected inventory only. Keep the
        # canonical estimate and shared SPEAR demand unchanged; recorded T/F
        # already includes the physical transfer and must not receive it again.
        truck_projection_demand_gallons = planning_demand_gallons
        if (
            apu_running is True
            and cycle_type != "defuel"
            and (assignment is None or assignment.transfer_fuel_gallons is None)
        ):
            apu_gallons = (
                lbs_to_gallons(apu_allowance_lbs, fuel_density_lbs_per_gallon)
                if planning_demand_gallons is not None
                else None
            )
            truck_projection_demand_gallons = (
                planning_demand_gallons + apu_gallons
                if planning_demand_gallons is not None and apu_gallons is not None
                else None
            )
        center_actual_lbs = (
            tank_states_by_code["ctr"].actual_lbs
            if detailed_aircraft_type == "A300"
            and tank_states_by_code.get("ctr") is not None
            else None
        )
        load_planning_output = (
            f"{mission.flight_number or '-'} {mission.destination or '-'} "
            f"{tail_number or '-'}"
        )
        if neo_fuel_lbs is not None:
            load_planning_output += f" NEO > {format_display_thousands(neo_fuel_lbs)}"
        if detailed_aircraft_type == "A300" and center_actual_lbs is not None:
            load_planning_output += f" CF > {int(center_actual_lbs)}"
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
        fob_apu_allowance = apu_allowance_lbs
        if apu_running is None:
            # Forecast-only APU burn needs a departure time. A mission without
            # ETD must remain viewable; leave FOB likelihood unknown rather
            # than treating missing timing as zero burn or crashing Dispatch.
            fob_departure_utc = mission.eta_datetime_utc or mission.planned_datetime_utc
            if fob_departure_utc is not None:
                fob_apu_allowance = calculate_apu_allowance_lbs(
                    fob_departure_utc,
                    operation.window_minutes, now_utc,
                    effective_apu_rates.get(
                        detailed_aircraft_type, DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
                    ),
                )
        fob = fob_assessment(
            required=mission.planned_fuel_load,
            inbound=tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None,
            remaining=remaining_total_lbs if remaining_complete else None,
            actual=actual_total_lbs if actual_complete else None,
            apu_allowance=fob_apu_allowance,
            apu_confirmed=apu_running is not None, apu_source_valid=apu_source_valid,
            inherited=inherited_measurement["total_lbs"] if inherited_measurement else None,
            transfer=assignment.transfer_fuel_gallons if assignment else None,
            cycle_type=cycle_type,
        )
        inherited_fob_ready = bool(inherited_measurement and fob["fob_verified"])
        fuel_on_board_ready = bool(
            assignment and fob["fob_verified"]
            and not administratively_complete and not effective_hold and not work_ended_early
            and assignment.assigned_fueler_user_id is not None and assignment.assigned_truck_id is None
            and not (fuel_work_state and (fuel_work_state.manual_on_at_utc or fuel_work_state.confirmed_on_at_utc
                or fuel_work_state.on_at_utc or fuel_work_state.off_at_utc or fuel_work_state.physical_transfer_review))
        )
        off_transfer_ready = bool(
            assignment
            and (
                (
                    assignment.assigned_truck_id is not None
                    and assignment.transfer_fuel_gallons is not None
                    and assignment.transfer_fuel_gallons > 0
                )
                or (
                    assignment.assigned_truck_id is None
                    and assignment.transfer_fuel_gallons in (None, 0)
                )
            )
        )
        physical_off_ready = bool(
            fuel_work_state
            and fuel_work_state.off_at_utc is None
            and assignment and assignment.assigned_truck_id is not None
            and not effective_hold
            and not work_ended_early
        )
        off_reason = ("Dispatcher review required before TRUCK OFF." if effective_hold or work_ended_early else
                      ("Complete Fuel On Board readings and APU before OFF." if assignment and assignment.assigned_truck_id is None
                       else "Confirm physical TRUCK ON before TRUCK OFF.") if not physical_off_ready else "")
        if fuel_on_board_complete:
            fuel_on_board_reason = "COMPLETE"
        elif effective_hold:
            fuel_on_board_reason = "REVIEW REQUIRED"
        elif work_ended_early:
            fuel_on_board_reason = "ENDED EARLY"
        elif inherited_fob_ready:
            fuel_on_board_reason = ""
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
        elif not fuel_on_board_ready:
            fuel_on_board_reason = "Insufficient verified fuel for FOB."
        else:
            fuel_on_board_reason = ""
        normal_completion_ready = bool(
            assignment
            and not administratively_complete
            and not effective_hold
            and not work_ended_early
            and fuel_work_state
            and fuel_work_state.physical_finalized_at_utc is None
            and fuel_work_state.off_at_utc
            and apu_source_valid
            and neo_fuel_lbs is not None
            and movement_status != "unknown"
            and direction_evidence != "opposite"
            and (
                cycle_type == "fuel"
                or movement_status == "moved"
            )
            and (
                movement_status == "not_moved"
                or (
                    has_prior_fueling_events
                    and assignment.transfer_fuel_gallons in (None, 0)
                )
                or (
                    assignment.assigned_truck_id is not None
                    and nightly_truck_state is not None
                    and assignment.transfer_fuel_gallons is not None
                    and assignment.transfer_fuel_gallons > 0
                    and (cycle_type != "defuel" or (
                        (nightly_truck_state.inventory_verified or nightly_truck_state.inventory_pending_epoch == nightly_truck_state.inventory_epoch)
                        and nightly_truck_state.current_gallons is not None
                        and (assignment.assigned_truck.capacity_gallons is None or
                             nightly_truck_state.current_gallons + assignment.transfer_fuel_gallons
                             <= assignment.assigned_truck.capacity_gallons)))
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
            normal_completion_reason = "REVIEW REQUIRED"
        elif work_ended_early:
            normal_completion_reason = "ENDED EARLY"
        elif not fuel_work_state or not fuel_work_state.off_at_utc:
            normal_completion_reason = "Fueler OFF required."
        elif not apu_source_valid:
            normal_completion_reason = "APU source tank required."
        elif neo_fuel_lbs is None:
            normal_completion_reason = "NEO FUEL incomplete."
        elif direction_evidence == "opposite":
            normal_completion_reason = "REVIEW REQUIRED: opposite fuel direction."
        elif movement_status == "unknown":
            normal_completion_reason = "Movement unknown."
        elif cycle_type in {"uplift", "defuel"} and movement_status != "moved":
            normal_completion_reason = (
                f"{cycle_type.upper()} movement is not confirmed."
            )
        elif movement_status == "moved" and not assignment.assigned_truck_id:
            normal_completion_reason = "Assign truck."
        elif movement_status == "moved" and nightly_truck_state is None:
            normal_completion_reason = "Truck nightly state missing."
        elif movement_status == "moved" and (
            assignment.transfer_fuel_gallons is None
            or assignment.transfer_fuel_gallons <= 0
        ):
            normal_completion_reason = "Positive T/F required."
        elif cycle_type == "defuel" and movement_status == "moved" and (
            not (nightly_truck_state.inventory_verified or nightly_truck_state.inventory_pending_epoch == nightly_truck_state.inventory_epoch) or nightly_truck_state.current_gallons is None
        ):
            normal_completion_reason = "Verify truck gallons for DEFUEL capacity."
        elif cycle_type == "defuel" and movement_status == "moved" and (
            assignment.assigned_truck.capacity_gallons is not None
            and nightly_truck_state.current_gallons + assignment.transfer_fuel_gallons
            > assignment.assigned_truck.capacity_gallons
        ):
            normal_completion_reason = "DEFUEL exceeds truck capacity."
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
                    effective_hold or work_ended_early
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
                    assignment and tail_mismatch
                ),
                "end_early_available": bool(
                    assignment
                    and tail_mismatch
                    and prior_tail_work_state
                    and _fuel_work_has_begun(prior_tail_work_state, assignment)
                    and not prior_tail_work_state.ended_early_at_utc
                    and not administratively_complete
                ),
                "fueler_completion_ready": False, "fueler_submitted": False,
                "fueler_completion_reason": "Assign resources and confirm physical TRUCK ON.",
                "resource_swap_required": bool(
                    assignment and work_has_begun and not administratively_complete
                ),
                "initial_truck_assignment_available": bool(
                    assignment
                    and work_has_begun
                    and assignment.assigned_fueler_user_id is not None
                    and assignment.assigned_truck_id is None
                    and not administratively_complete
                    and not effective_hold
                    and not work_ended_early
                ),
                "aircraft_type": aircraft_type,
                "detailed_aircraft_type": detailed_aircraft_type,
                "fuel_configuration_message": (
                    "Awaiting tail / aircraft type"
                    if not tail_number
                    else CALCULATION_NOT_CONFIGURED_MESSAGE
                    if not tank_layout
                    else ""
                ),
                "fuel_work_state": fuel_work_state,
                "physical_work_id": fuel_work_state.id if fuel_work_state and fuel_work_state.physical_sequence_number is not None else None,
                "physical_finalized": bool(fuel_work_state and fuel_work_state.physical_finalized_at_utc),
                "physical_sequence": fuel_work_state.physical_sequence_number if fuel_work_state else None,
                "physical_transfer_review": bool(fuel_work_state and fuel_work_state.physical_transfer_review),
                "is_source_segment": source_work is not None,
                "tank_rows": tank_rows,
                "planned_ready": remaining_readings_complete,
                "on_time": (
                    format_local_hhmm(fuel_work_state.manual_on_at_utc or fuel_work_state.on_at_utc, mission.timezone)
                    if fuel_work_state and (fuel_work_state.manual_on_at_utc or fuel_work_state.on_at_utc)
                    else "-"
                ),
                "off_time": (
                    format_local_hhmm(fuel_work_state.off_at_utc, mission.timezone)
                    if fuel_work_state and fuel_work_state.off_at_utc
                    else "-"
                ),
                "off_ready": physical_off_ready,
                "off_reason": off_reason,
                "is_off": bool(fuel_work_state and fuel_work_state.off_at_utc),
                "remaining_total_display": (
                    format_display_thousands(remaining_total_lbs)
                    if remaining_complete
                    else "INCOMPLETE"
                ),
                "actual_total_lbs": actual_total_lbs if actual_complete else None,
                "actual_complete": actual_complete,
                "fuel_density": fuel_density_lbs_per_gallon,
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
                    format_dispatch_thousands(apu_allowance_lbs)
                    if apu_allowance_lbs is not None
                    else "INCOMPLETE"
                ),
                "apu_allowance_lbs": apu_allowance_lbs,
                "automatic_apu_allowance_lbs": automatic_apu_allowance_lbs,
                "automatic_apu_allowance_display": (
                    format_dispatch_thousands(automatic_apu_allowance_lbs)
                    if automatic_apu_allowance_lbs is not None
                    else "INCOMPLETE"
                ),
                "apu_override_enabled": apu_override_enabled,
                "apu_override_allowance_lbs": apu_override_allowance_lbs,
                "apu_override_allowance_display": (
                    format_dispatch_thousands(apu_override_allowance_lbs)
                    if apu_override_allowance_lbs is not None
                    else "-"
                ),
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
                "fuel_load_display": (
                    format_display_thousands(fuel_load_lbs)
                    if fuel_load_lbs is not None
                    else "INCOMPLETE"
                ),
                "neo_fuel_display": (
                    format_display_thousands(neo_fuel_lbs)
                    if neo_fuel_lbs is not None
                    else "INCOMPLETE"
                ),
                "neo_fuel_available": neo_fuel_lbs is not None,
                "neo_fuel_lbs": neo_fuel_lbs,
                "center_actual_lbs": center_actual_lbs,
                "fuel_on_board_complete": fuel_on_board_complete,
                "fuel_on_board_ready": fuel_on_board_ready,
                "fuel_on_board_reason": fuel_on_board_reason,
                "tail_swap_inherited_fuel_lbs": (
                    inherited_measurement["total_lbs"]
                    if inherited_measurement is not None
                    else None
                ),
                "tail_swap_inherited_event_id": (
                    inherited_measurement["event"].id
                    if inherited_measurement is not None
                    else None
                ),
                "movement_status": movement_status,
                "cycle_type": cycle_type,
                "cycle_type_label": cycle_type.upper(),
                "cycle_number": (
                    int(assignment.current_cycle_number or 1)
                    if assignment
                    else 1
                ),
                "direction_mismatch": direction_evidence == "opposite",
                "has_prior_fueling_events": has_prior_fueling_events,
                "normal_completion_complete": normal_completion_complete,
                "administratively_complete": administratively_complete,
                "follow_up_available": bool(
                    assignment
                    and normal_completion_complete
                    and not fuel_on_board_complete
                    and has_prior_fueling_events
                    and tail_number
                ),
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
                "arrival_status": (
                    "Arrived"
                    if (
                        str(
                            getattr(tail_state, "operational_status", "") or ""
                        ).strip().lower() == "hot"
                        and tail_presence["is_present"]
                    )
                    else _arrival_status_display(arrival)
                ),
                "departure_time": format_local_hhmm(
                    mission.eta_datetime_utc or mission.planned_datetime_utc,
                    mission.timezone,
                ),
                "parking_position": parking.get(tail_number, ("-", False))[0] if tail_number else "-",
                "parking_valid": parking.get(tail_number, ("-", False))[1] if tail_number else False,
                "departure_required_fuel_lbs": departure_required,
                "required_fuel_display": format_dispatch_thousands(departure_required),
                "required_fuel_lbs": mission.planned_fuel_load,
                "inbound_fuel_display": format_dispatch_thousands(
                    tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None
                ),
                "inbound_fuel_lbs": (
                    tail_fuel_state.inbound_fuel_lbs if tail_fuel_state else None
                ),
                "measured_inbound_fuel_lbs": (
                    remaining_total_lbs if remaining_complete else None
                ),
                "measured_inbound_fuel_display": format_dispatch_thousands(
                    remaining_total_lbs if remaining_complete else None
                ),
                "fob_display": format_display_thousands(
                    tail_fuel_state.fob_lbs if tail_fuel_state else None
                ),
                "center_fuel_display": format_display_thousands(
                    tail_fuel_state.center_fuel_lbs if tail_fuel_state else None
                ),
                "actual_fuel_display": (
                    format_display_thousands(actual_total_lbs)
                    if actual_complete
                    else "INCOMPLETE"
                ),
                "apu_lbs": tail_fuel_state.apu_lbs if tail_fuel_state else None,
                "transfer_fuel_gallons": (
                    assignment.transfer_fuel_gallons if assignment else None
                ),
                "estimated_fuel_display": (
                    f"{estimated_fuel.gallons:,} gal"
                    if estimated_fuel.gallons is not None
                    else "-"
                ),
                "estimated_fuel_status": (
                    f"{estimated_fuel.gallons:,} gal"
                    if estimated_fuel.gallons is not None
                    else estimated_fuel_status
                ),
                "estimated_fuel_gallons": estimated_fuel.gallons,
                "estimated_fuel_source": estimated_fuel.source,
                "estimated_fuel_source_label": estimated_fuel.source_label,
                "planning_demand_gallons": planning_demand_gallons,
                "truck_projection_demand_gallons": truck_projection_demand_gallons,
                "assigned_fueler": assignment.assigned_fueler if assignment else None,
                "assigned_truck": truck,
                "assignment_update_pending": bool(
                    assignment
                    and assignment.assigned_fueler_user_id is not None
                    and int(assignment.fueler_update_version or 0)
                    > int(assignment.fueler_update_acknowledged_version or 0)
                ),
                "assignment_update_version": (
                    int(assignment.fueler_update_version or 0)
                    if assignment
                    else 0
                ),
                "assignment_update_message": (
                    assignment.fueler_update_message if assignment else None
                ),
                "truck_remaining_fuel": (
                    nightly_truck_state.current_gallons
                    if nightly_truck_state is not None and nightly_truck_state.inventory_verified
                    else None
                ),
                "projected_truck_gallons": None,
                "projected_truck_display": "-",
                "projected_truck_short": False,
                "review_status": (
                    assignment.review_status if assignment else (mission.fuel_status or "pending")
                ),
                **fob,
                "fueler_notes": (fuel_work_state.notes or "") if fuel_work_state else "",
                "tail_note": aircraft_notes.get(tail_number),
                "load_planning_output": load_planning_output,
                "load_planning_ready": load_planning_output is not None,
                "load_planning_placeholder": "-",
                "tail_fuel_state": tail_fuel_state,
            }
        )
        rows[-1].update(fueling_status(
            rows[-1], now=now_utc,
            threshold=getattr(status_settings, "red_assignment_alert_threshold_minutes", 30),
        ))
    if nightly_truck_states_by_truck_id is not None:
        projections = project_truck_remaining(
            {
                truck_id: (truck_state.current_gallons if truck_state.inventory_verified else None)
                for truck_id, truck_state in nightly_truck_states_by_truck_id.items()
                if truck_state.status != "needs_sump"
            },
            [
                (
                    row["mission"].id,
                    row["assignment"].assigned_truck_id,
                    row["truck_projection_demand_gallons"],
                )
                for row in rows
                if row["assignment"] is not None
                and row["assignment"].assigned_truck_id is not None
                and not row["administratively_complete"]
                and (
                    nightly_truck_states_by_truck_id.get(
                        row["assignment"].assigned_truck_id
                    )
                    is not None
                    and nightly_truck_states_by_truck_id[
                        row["assignment"].assigned_truck_id
                    ].status
                    != "needs_sump"
                )
            ],
        )
        for row in rows:
            projection = projections.get(row["mission"].id)
            if projection is None:
                continue
            row["projected_truck_gallons"] = projection.gallons
            row["projected_truck_short"] = projection.short
            row["projected_truck_display"] = (
                f"SHORT ({projection.gallons:,} gal)"
                if projection.short
                else f"{projection.gallons:,} gal"
                if projection.gallons is not None
                else "INCOMPLETE"
            )
    if assignments:
        _attach_fueling_start_state(rows, operation, assignments, fuel_work_states, parking,
                                   start_selections, status_settings, now=now_utc,
                                   evidence=start_evidence)
    return rows


def _attach_spear_plan(rows, truck_visuals, plan):
    """Decorate the existing bounded dispatch rows/cards with the fleet plan."""
    rows_by_mission_id = {row["mission"].id: row for row in rows}
    for row in rows:
        mission_id = row["mission"].id
        row["assignment_recommendation"] = None
        row["assignment_recommendation_display"] = None
        row["assignment_recommendation_time_display"] = None
        row["assignment_recommendation_reason_display"] = None
        row["spear_step"] = None
        row["spear_risk"] = plan.risks_by_mission_id.get(mission_id)
        row["spear_ready"] = (
            mission_id in plan.readiness_by_mission_id
            and not plan.readiness_by_mission_id[mission_id]
        )
        row["spear_waiting_for_data"] = plan.waiting_for_data_by_mission_id.get(
            mission_id
        )
        row["spear_readiness_reasons"] = tuple(
            {
                "code": reason,
                "label": SPEAR_READINESS_REASON_LABELS[reason],
            }
            for reason in plan.readiness_by_mission_id.get(mission_id, ())
        )
        row["spear_problem"] = (
            plan.unavailable_by_mission_id.get(mission_id)
            or row["spear_waiting_for_data"]
        )
    visuals_by_truck_id = {item["truck_id"]: item for item in truck_visuals}
    for visual in truck_visuals:
        visual["spear_recommendation"] = None
        visual["spear_parking_position"] = None
        visual["spear_next_status"] = None
        visual["spear_advisory_only"] = False
    for step in plan.steps:
        if step.mission_id in rows_by_mission_id:
            target = rows_by_mission_id[step.mission_id]
            target["spear_step"] = step
            if step.action_type == "assign" and step.fueler_id and step.truck_id:
                target["assignment_recommendation"] = SimpleNamespace(
                    available=not step.requires_resource_release, fueler_id=step.fueler_id,
                    truck_id=step.truck_id, feasible_start_utc=step.projected_start_at_utc,
                    predicted_finish_utc=step.projected_complete_at_utc)
                target["assignment_recommendation_display"] = f"{step.fueler_name} / Truck {step.truck_number}"
                target["assignment_recommendation_time_display"] = (
                    f"{format_local_hhmm(step.projected_start_at_utc, getattr(target['mission'], 'timezone', None))} - "
                    f"{format_local_hhmm(step.projected_complete_at_utc, getattr(target['mission'], 'timezone', None))}")

        visual = visuals_by_truck_id.get(step.truck_id)
        if (visual is not None and visual["spear_recommendation"] is None
                and step.truck_id not in plan.occupied_truck_next):
            if (step.action_type == "assign" and
                    (step.risk == "TIMING UNKNOWN" or step.projected_complete_at_utc is None)):
                visual["spear_next_status"] = "NEXT PENDING · MISSION DATA"
                continue
            visual["spear_recommendation"] = step
            visual["spear_advisory_only"] = step.action_type == "assign" and step.requires_resource_release
            mission_row = rows_by_mission_id.get(step.mission_id, {})
            visual["spear_parking_position"] = (
                mission_row.get("parking_position") if mission_row.get("parking_valid") else None
            )
    for truck_id, next_job in plan.occupied_truck_next.items():
        visual = visuals_by_truck_id.get(truck_id)
        if visual is None:
            continue
        visual["spear_recommendation"] = next_job.step
        visual["spear_next_status"] = next_job.status
        visual["spear_advisory_only"] = True
        if next_job.step is not None:
            mission_row = rows_by_mission_id.get(next_job.step.mission_id, {})
            visual["spear_parking_position"] = (
                mission_row.get("parking_position") if mission_row.get("parking_valid") else None
            )
    if plan.status_text != "SPEAR: RECOMMENDATIONS OFF":
        has_unassigned_mission = any(
            not row.get("administratively_complete") and (
                row.get("assignment") is None or not (
                    row["assignment"].assigned_truck_id
                    or row["assignment"].assigned_fueler_user_id
                )
            )
            for row in rows
        )
        for visual in truck_visuals:
            if visual["spear_recommendation"] is not None or visual["spear_next_status"]:
                continue
            visual["spear_next_status"] = (
                "NEXT UNAVAILABLE · TRUCK STATUS" if visual["status"] != "available" else
                "NO NEXT MISSION" if not has_unassigned_mission else
                "NEXT PENDING · MISSION DATA" if plan.waiting_for_data_count else
                "NEXT UNAVAILABLE"
            )


def _fuel_work_states_by_assignment_tail(assignments):
    assignments = list(assignments)
    assignment_ids = [assignment.id for assignment in assignments]
    if not assignment_ids:
        return {}
    work_states = (
        NeoScorpionFuelWorkState.query.filter(
            NeoScorpionFuelWorkState.fuel_assignment_id.in_(assignment_ids)
        )
        .options(joinedload(NeoScorpionFuelWorkState.tank_states),
                 joinedload(NeoScorpionFuelWorkState.physical_truck),
                 joinedload(NeoScorpionFuelWorkState.physical_fueler))
        .populate_existing()
        .all()
    )
    return WorkIndex(assignments, work_states)


def _fuel_start_records(operation, assignments, work_states, parking, selections):
    """Bounded current-sort evidence shared by the save gate and live views."""
    assignments = list(assignments)
    audits = db.session.query(NeoScorpionFuelAuditEntry.fuel_assignment_id,
        NeoScorpionFuelAuditEntry.new_value, NeoScorpionFuelAuditEntry.created_at).filter(
        NeoScorpionFuelAuditEntry.sort_date_operation_id == operation.id,
        NeoScorpionFuelAuditEntry.field_name == "assigned_truck_id",
    ).all()
    dates = {}
    for audit in audits:
        key = (audit.fuel_assignment_id, audit.new_value)
        dates[key] = max(dates.get(key, audit.created_at), audit.created_at)
    records = []
    works_by_owner = {}
    for owner_work in all_work(work_states):
        works_by_owner.setdefault(owner_work.fuel_assignment_id, []).append((owner_work.tail_number,owner_work))
    for assignment in assignments:
        mission = assignment.sort_date_mission
        tail = _normalize_tail(mission.assigned_tail_number)
        work = work_states.get((assignment.id, tail))
        on = utc_naive(work.on_at_utc) if work else None
        confirmed = utc_naive(work.confirmed_on_at_utc) if work else None
        manual = (utc_naive(work.manual_on_at_utc) if work and
                  work.manual_on_cycle_number == int(assignment.current_cycle_number or 1) and
                  work.manual_on_truck_id == assignment.assigned_truck_id else None)
        tanks = {tank.tank_code: tank for tank in work.tank_states} if work else {}
        layout = tank_layout_for_tail(tail)
        complete = bool(layout) and all(code in tanks and tanks[code].remaining_lbs is not None for code, _ in layout)
        at_on = bool(on and complete and all(tanks[code].created_at is not None
            and utc_naive(tanks[code].created_at) <= on + timedelta(seconds=1) for code, _ in layout))
        premature_readings = bool(on and layout and any(code not in tanks or
            (tanks[code].created_at is not None and utc_naive(tanks[code].created_at) > on + timedelta(seconds=1))
            for code, _ in layout))
        assigned_at = assignment.truck_assigned_at_utc or dates.get((assignment.id, str(assignment.assigned_truck_id))) or assignment.created_at
        if work and confirmed is None and work.truck_segment_started_at_utc and not dates.get((assignment.id, str(assignment.assigned_truck_id))):
            assigned_at = max(assigned_at, work.truck_segment_started_at_utc) if assigned_at else work.truck_segment_started_at_utc
        selection = selections.get(assignment.assigned_truck_id)
        truck = assignment.assigned_truck
        records.append(dict(id=assignment.id, assignment_id=assignment.id,
            work_id=work.id if work else None, truck_id=assignment.assigned_truck_id,
            fueler_id=assignment.assigned_fueler_user_id, on=on, confirmed_on=confirmed,
            manual_on=manual,
            defuel_recovery_review=bool(work and work.physical_cycle_type == "defuel" and work.off_at_utc
                and not work.ended_early_at_utc and not work.physical_finalized_at_utc
                and not _truck_sumped_after(work,selection)),
            release=utc_naive((work.off_at_utc or work.ended_early_at_utc) if work else None)
                # Administrative completion cannot release this visit's open ON.
                or (utc_naive(assignment.completed_at_utc or assignment.fuel_on_board_at_utc)
                    if not (manual or confirmed or on) else None),
            assigned_at=utc_naive(assigned_at), remaining_complete=complete, remaining_at_on=at_on,
            premature_readings=premature_readings,
            parking=parking.get(tail, ("-", False))[0],
            truck_available=bool(selection and selection.status == "available" and truck
                                 and truck.is_active and not truck.is_out_of_service)))
        # A tail acknowledgment never releases an already confirmed physical
        # segment. Keep only identities proved by manual ON or its v3 snapshot;
        # do not guess a historical truck from today's assignment.
        for old_tail, old_work in works_by_owner.get(assignment.id, ()):
            if work is not None and old_work.id == work.id:
                continue
            if old_work.physical_finalized_at_utc is not None:
                continue  # Historical event owners are not live occupations; event releases remain below.
            facts = old_work.performance_context or {}
            old_truck = old_work.manual_on_truck_id or (facts.get("truck_id") if facts.get("on_source") == "trusted automatic" else None)
            old_on = old_work.manual_on_at_utc or (old_work.confirmed_on_at_utc if facts.get("on_source") == "trusted automatic" else None)
            if old_truck is None or old_on is None:
                continue
            old_selection = selections.get(old_truck)
            records.append(dict(id=("tail_segment",old_work.id), assignment_id=assignment.id,
                work_id=old_work.id,
                historical_segment=True, truck_id=old_truck,
                defuel_recovery_review=bool(old_work.physical_cycle_type == "defuel" and old_work.off_at_utc
                    and not old_work.ended_early_at_utc and not _truck_sumped_after(old_work,old_selection)),
                fueler_id=facts.get("fueler_id") or old_work.manual_on_by_user_id,
                on=None, confirmed_on=utc_naive(old_on), manual_on=utc_naive(old_work.manual_on_at_utc),
                release=utc_naive(old_work.off_at_utc or old_work.ended_early_at_utc),
                assigned_at=None, remaining_complete=False, remaining_at_on=False, premature_readings=False,
                parking=parking.get(old_tail,("-",False))[0],
                truck_available=bool(old_selection and old_selection.status == "available")))
    releases = [dict(id=event.fuel_assignment_id, work_id=event.fuel_work_state_id, truck_id=event.fuel_truck_id,
                    release=utc_naive(event.ended_at_utc), parking=parking.get(_normalize_tail(event.tail_number), ("-", False))[0])
                for event in db.session.query(NeoScorpionFuelingEvent.fuel_assignment_id,
                    NeoScorpionFuelingEvent.fuel_truck_id, NeoScorpionFuelingEvent.ended_at_utc,
                    NeoScorpionFuelingEvent.tail_number, NeoScorpionFuelingEvent.fuel_work_state_id).filter(
                    NeoScorpionFuelingEvent.sort_date_operation_id == operation.id,
                    NeoScorpionFuelingEvent.ended_at_utc.isnot(None)).all()]
    return records, releases


def _attach_fueling_start_state(rows, operation, assignments, work_states, parking, selections, settings, *, now, evidence=None):
    records, releases = _fuel_start_records(operation, assignments.values(), work_states, parking, selections)
    if evidence is not None:
        evidence.update(records=records, releases=releases)
    by_id = {record["id"]: record for record in records}
    spear_settings = effective_spear_settings(settings)
    states = {record["id"]:fueling_start_state(record, records, releases, spear_settings, now=now)
              for record in records}
    for row in rows:
        assignment, work = row.get("assignment"), row.get("fuel_work_state")
        if not assignment:
            continue
        record_key = ("tail_segment", work.id) if row.get("is_source_segment") else assignment.id
        if row.get("is_source_segment") and work.physical_finalized_at_utc:
            # Read-only previous visits are not live resource occupations. Use
            # only their recorded provenance; never today's assignment timing.
            manual = work.manual_on_at_utc if (work.manual_on_truck_id == work.physical_truck_id
                and work.manual_on_cycle_number == work.physical_cycle_number) else None
            automatic = work.confirmed_on_at_utc if (work.performance_context or {}).get("on_source") == "trusted automatic" else None
            effective = manual or automatic
            row.update(fueling_on_trusted=bool(effective),fueling_on_active=bool(effective),
                fueling_effective_on_utc=effective,manual_on_confirmed=bool(manual),
                on_time=format_local_hhmm(effective,row["mission"].timezone) if effective else "-",
                outstanding_segments=[],pending_segment_fuelers=[],physical_segment_resources=[],
                truck_start_reason="",truck_travel_origin="",truck_travel_until="")
            continue
        record = by_id.get(record_key)
        if record is None and row.get("is_source_segment"):
            # Retained preparation or uncertain legacy work has no confirmed
            # physical occupation. Keep its data accessible without inventing
            # an ON record or borrowing the current visit's timing.
            from app.services.neoscorpion_submission import state as submission_state
            row.update(fueling_on_trusted=False,fueling_on_active=False,fueling_effective_on_utc=None,
                manual_on_confirmed=False,on_time="-",outstanding_segments=[],pending_segment_fuelers=[],
                physical_segment_resources=[],truck_start_reason="PRIOR VISIT TIMING UNKNOWN",
                truck_travel_origin="",truck_travel_until="",off_ready=False,
                off_reason="No confirmed physical ON exists for this prior visit.")
            row.update(submission_state(assignment,row['mission'],work,list(work.tank_states),on_trusted=False))
            row['normal_completion_ready']=False
            row['normal_completion_reason']=row['fueler_completion_reason']
            continue
        if record is None:raise ValueError('Current physical visit timing identity is missing; refresh/review required.')
        state = dict(states[record_key])
        fob_check = bool(assignment.assigned_fueler_user_id is not None and assignment.assigned_truck_id is None
                         and row.get("fob_likely"))
        confirmed_segment_on = (work.confirmed_on_at_utc if work and work.confirmed_on_at_utc
                                and (work.truck_segment_started_at_utc is None
                                     or work.confirmed_on_at_utc >= work.truck_segment_started_at_utc)
                                else None)
        if work and (record.get("manual_on") or confirmed_segment_on) and (row.get("is_off") or row.get("administratively_complete")):
            # A historical OFF is preserved, but does not authenticate an
            # uncertain legacy ON for timing or performance calculations.
            state.update(on_trusted=True, on_active=True,
                         effective_on=record.get("manual_on") or confirmed_segment_on)
        row.update(fueling_on_trusted=state["on_trusted"], fueling_on_active=state["on_active"], fueling_effective_on_utc=state["effective_on"],
                   truck_start_reason=state["reason"], truck_travel_origin=state["origin"],
                   truck_travel_until=state["arrival"].isoformat()+"Z" if state["arrival"] else "")
        row["manual_on_confirmed"] = bool(record.get("manual_on"))
        row["outstanding_segments"] = [item for item in all_work(work_states)
            if item.fuel_assignment_id == assignment.id and (item.id != assignment.active_work_state_id or item.tail_number != row["tail_number"])
            and item.physical_sequence_number is not None and item.physical_finalized_at_utc is None]
        # Preparation records also have visit IDs. An older ID is not proof
        # that another aircraft was fueled; retain exact recovery identities.
        row["outstanding_segment_details"] = [dict(
            work=item,
            original_aircraft=_normalize_tail(item.tail_number) != _normalize_tail(row["tail_number"]),
            physical_work=bool(item.manual_on_at_utc or item.confirmed_on_at_utc or item.on_at_utc
                or item.off_at_utc or item.ended_early_at_utc or item.physical_transfer_gallons
                or item.physical_transfer_review),
        ) for item in row["outstanding_segments"]]
        row["pending_segment_fuelers"] = [item.physical_fueler_user_id for item in row["outstanding_segments"]
            if item.off_at_utc is not None and item.ended_early_at_utc is None]
        row["physical_segment_resources"] = [
            (item["fueler_id"],item["truck_id"]) for item in records
            if item.get("historical_segment") and item.get("assignment_id") == assignment.id
            and item["release"] is None and states[item["id"]]["on_trusted"]]
        row["on_time"] = (format_local_hhmm(state["effective_on"], row["mission"].timezone)
                          if state["effective_on"] else "-")
        occupants = [record for record in records if record["truck_id"] == assignment.assigned_truck_id
                     and record["truck_id"] is not None and record["release"] is None
                     and states[record["id"]]["on_trusted"]]
        activity = ""
        if len(occupants) == 1:
            position = occupants[0]["parking"]
            activity = (f"CURRENTLY FUELING ON {position}" if position and position != "-"
                        else "CURRENTLY FUELING · LOCATION UNKNOWN")
        elif occupants or state["reason"] == "TRUCK STILL FUELING":
            activity = "CURRENT STATUS UNKNOWN"
        elif state["arrival"]:
            activity = state["reason"]
        row["truck_current_activity"] = activity
        row["on_time"] = format_local_hhmm(state["effective_on"], row["mission"].timezone) if state["effective_on"] else "-"
        if assignment.assigned_truck_id is not None and not state["on_trusted"] and not row.get("is_off"):
            row["off_ready"] = False
            row["off_reason"] = "Confirm physical TRUCK ON before TRUCK OFF."
        from app.services.neoscorpion_submission import state as submission_state
        row.update(submission_state(assignment,row["mission"],work,list(work.tank_states) if work else [],on_trusted=state["on_trusted"]))
        if not row.get("administratively_complete") and not row.get("fuel_on_board_ready"):
            row["normal_completion_ready"] = bool(row["normal_completion_ready"] and row["fueler_submitted"])
            if not row["fueler_submitted"]:
                row["normal_completion_reason"] = row["fueler_completion_reason"] or "Fueler COMPLETE ASSIGNMENT required."
        row.update(fueling_status(row, now=now, threshold=getattr(settings, "red_assignment_alert_threshold_minutes", 30)))


def format_display_thousands(value):
    converted = lbs_to_display_thousands(value)
    if converted is None:
        return ""
    return f"{converted:.1f}"


def _hanzo_planning_status(row):
    """Return the concise authoritative-state reason for a Hanzo plan gap."""
    if row["fuel_configuration_message"]:
        return row["fuel_configuration_message"]
    if row["mission"].planned_fuel_load is None:
        return "Fuel Load needed"
    if not row["planned_ready"]:
        return "Awaiting fuel readings"
    if row["apu_running"] is None or not row["apu_source_valid"]:
        return "Awaiting APU verification"
    if any(tank["planned_lbs"] is None for tank in row["tank_rows"]):
        return "Awaiting fuel readings"
    return ""


def _hanzo_fuel_short(required_fuel_lbs, neo_fuel_lbs):
    return (required_fuel_lbs is not None and neo_fuel_lbs is not None
            and neo_fuel_lbs < required_fuel_lbs)


def _dispatch_truck_visuals(nightly_trucks, rows, *, timezone_name=None):
    """Decorate existing dispatch projections for the read-only truck visual aid."""
    projections_by_truck_id = {}
    for row in rows:
        assignment = row["assignment"]
        if assignment is None or assignment.assigned_truck_id is None:
            continue
        if row["projected_truck_gallons"] is not None:
            projections_by_truck_id[assignment.assigned_truck_id] = row
        elif row["projected_truck_display"] == "INCOMPLETE":
            projections_by_truck_id[assignment.assigned_truck_id] = row

    visuals = []
    for nightly in nightly_trucks:
        truck = nightly["truck"]
        selection = nightly["selection"]
        projection_row = projections_by_truck_id.get(truck.id)
        current_gallons = selection.current_gallons if selection.inventory_verified else None
        projected_gallons = (
            projection_row["projected_truck_gallons"]
            if projection_row is not None
            else None
        )
        visuals.append(
            {
                "truck_id": truck.id,
                "truck_number": truck.truck_number,
                "status": selection.status,
                "status_label": _dispatch_truck_status_label(selection.status),
                "top_off_sent_time": (
                    format_local_hhmm(selection.top_off_sent_at_utc, timezone_name)
                    if selection.status == "topping_off" else ""
                ),
                "capacity_gallons": truck.capacity_gallons,
                "capacity_display": _gallons_display(truck.capacity_gallons),
                "current_gallons": current_gallons,
                "last_recorded_gallons": selection.current_gallons,
                "current_display": (_gallons_display(current_gallons) if selection.inventory_verified
                                    else "UNVERIFIED · LAST " + _gallons_display(selection.current_gallons)),
                "inventory_verified": selection.inventory_verified,
                # Normal OFF awaits canonical T/F posting, not a new reading.
                "inventory_reconciliation_required": bool(not selection.inventory_verified
                    and selection.inventory_pending_epoch != selection.inventory_epoch),
                "current_percent": _capacity_percent(current_gallons, truck.capacity_gallons),
                "current_gauge_percent": _gauge_percent(
                    _capacity_percent(current_gallons, truck.capacity_gallons)
                ),
                "projected_gallons": projected_gallons,
                "projected_display": _gallons_display(projected_gallons),
                "projected_percent": _capacity_percent(
                    projected_gallons,
                    truck.capacity_gallons,
                ),
                "projected_gauge_percent": _gauge_percent(
                    _capacity_percent(projected_gallons, truck.capacity_gallons)
                ),
                "projected_short": bool(
                    projection_row and projection_row["projected_truck_short"]
                ),
                "projected_incomplete": bool(
                    projection_row
                    and projection_row["projected_truck_display"] == "INCOMPLETE"
                ),
                "projected_usage_gallons": (
                    current_gallons - projected_gallons
                    if current_gallons is not None and projected_gallons is not None
                    else None
                ),
            }
        )
    return visuals


def _attach_dispatch_truck_activity(
    visuals, assignments, evidence, selections, settings,
    *, now_utc=None,
):
    """Expose current-sort physical activity without treating future work as location."""
    if not visuals:
        return
    assignments = list(assignments)
    records, releases = evidence.get("records", ()), evidence.get("releases", ())
    eligible_ids = {
        assignment.id for assignment in assignments
        if assignment.operational_status == "active"
        and assignment.review_status != "complete"
        and assignment.sort_date_mission.fuel_status != "complete"
    }
    by_truck = {}
    for record in records:
        if (record.get("assignment_id",record["id"]) in eligible_ids or record.get("historical_segment")
                or record.get("manual_on") or record.get("confirmed_on")) and record["release"] is None and record["truck_id"] is not None:
            by_truck.setdefault(record["truck_id"], []).append(record)
    released_by_truck = {}
    for record in (*records, *releases):
        if record["truck_id"] is not None and record["release"] is not None:
            released_by_truck.setdefault(record["truck_id"], []).append(record)
    now = now_utc or datetime.utcnow()
    travel_settings = effective_spear_settings(settings)
    for visual in visuals:
        truck_id = visual["truck_id"]
        selection = selections.get(truck_id)
        current = by_truck.get(truck_id, ())
        visual.update(activity_label="STATUS UNKNOWN", activity_travel_until="",
                      activity_travel_origin="")
        physically_active = [record for record in current if record.get("manual_on") is not None
                             or record["confirmed_on"] is not None or record["on"] is not None]
        if physically_active:
            if len(physically_active) != 1:
                continue
            record = physically_active[0]
            state = fueling_start_state(record, records, releases, travel_settings, now=now)
            if state["on_trusted"]:
                visual["activity_label"] = (
                    f"FUELING · {record['parking']}" if record["parking"] != "-"
                    else "FUELING · LOCATION UNKNOWN"
                )
                if not selection or selection.status != "available":
                    # Physical occupation and serviceability are independent.
                    # Do not erase the service restriction or invent a RETURN.
                    issue = {"topping_off": "RETURN", "needs_sump": "NEEDS SUMP",
                             "unavailable_oos": "OOS"}.get(selection.status if selection else None, "ASSET")
                    visual["activity_label"] += f" · {issue} REVIEW"
            continue
        if len(current) > 1:
            continue
        record = current[0] if current else None
        if selection is None:
            continue
        if selection.status == "topping_off":
            visual["activity_label"] = "TOPPING OFF"
        elif selection.status == "needs_sump":
            visual["activity_label"] = "NEEDS SUMP"
        elif selection.status == "unavailable_oos":
            visual["activity_label"] = "OOS"
        elif selection.status == "available" and record:
            state = fueling_start_state(record, records, releases, travel_settings, now=now)
            if state["arrival"] and state["origin"]:
                visual["activity_label"] = state["reason"]
                visual["activity_travel_until"] = state["arrival"].isoformat() + "Z"
                visual["activity_travel_origin"] = state["origin"]
            elif not state["reason"] and record["parking"] != "-":
                visual["activity_label"] = f"PREASSIGNED · {record['parking']}"
        elif selection.status == "available":
            past = released_by_truck.get(truck_id, ())
            if not past:
                visual["activity_label"] = "IDLE"
                continue
            latest = max(item["release"] for item in past)
            locations = {item["parking"] for item in past if item["release"] == latest}
            if len(locations) == 1 and "-" not in locations:
                visual["activity_label"] = f"IDLE · LAST {next(iter(locations))}"


def _attach_dispatch_mission_truck_visuals(rows, truck_visuals):
    visuals_by_truck_id = {visual["truck_id"]: visual for visual in truck_visuals}
    for row in rows:
        assignment = row["assignment"]
        truck_id = assignment.assigned_truck_id if assignment else None
        visual = visuals_by_truck_id.get(truck_id)
        if visual is None:
            row["truck_visual"] = None
            continue
        row["truck_visual"] = {
            "truck_number": visual["truck_number"],
            "current_display": visual["current_display"],
            "current_percent": visual["current_percent"],
            "current_gauge_percent": visual["current_gauge_percent"],
            "projected_display": row["projected_truck_display"],
            "projected_percent": _capacity_percent(
                row["projected_truck_gallons"],
                visual["capacity_gallons"],
            ),
            "projected_gauge_percent": _gauge_percent(
                _capacity_percent(
                    row["projected_truck_gallons"],
                    visual["capacity_gallons"],
                )
            ),
            "projected_short": row["projected_truck_short"],
            "projected_incomplete": row["projected_truck_display"] == "INCOMPLETE",
        }


def _dispatch_truck_status_label(status):
    return {
        "available": "Available",
        "unavailable_oos": "Unavailable / OOS",
        "topping_off": "Topping Off",
        "needs_sump": "Needs Sump",
    }.get(status, "Unknown")


def _gallons_display(value):
    return f"{value:,} gal" if value is not None else "—"


def _capacity_percent(gallons, capacity):
    if gallons is None or capacity is None or capacity <= 0:
        return None
    return int((Decimal(gallons) * Decimal("100") / Decimal(capacity)).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    ))


def _gauge_percent(percent):
    return min(100, max(0, percent)) if percent is not None else None


def _attach_dispatch_load_planners(rows):
    """Use Rain's canonical identity/eligibility with already-loaded relationships."""
    if not rows:
        return
    eligible_ids = {person.id for person in eligible_neorain_load_planners()}
    for row in rows:
        planner = effective_neorain_load_planner(
            row["mission"], eligible_person_ids=eligible_ids
        )
        row["load_planner_name"] = planner.full_name if planner else "UNASSIGNED"


def _departure_missions(operation, *, include_load_planners=False):
    query = SortDateMission.query
    if include_load_planners:
        query = query.options(
            joinedload(SortDateMission.load_planner_person),
            joinedload(SortDateMission.master_flight_schedule).joinedload(
                MasterFlightSchedule.load_planner_person
            ),
        )
    return (
        query.filter(
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
        display = (
            format_dispatch_parking_position(
                assignment.ramp_code,
                assignment.position_code,
            )
            + lane_suffix
        ).strip() or "-"
        positions[tail_number] = (display, bool(assignment.ramp_code and assignment.position_code))
    return positions


def format_dispatch_parking_position(ramp_code, position_code):
    """Return a parking code without duplicating a single-letter ramp prefix."""
    ramp = str(ramp_code or "").strip().upper()
    position = str(position_code or "").strip().upper()
    if not position:
        return ramp
    if len(ramp) == 1:
        matched = re.fullmatch(rf"{re.escape(ramp)}+(\d{{1,2}})", position)
        if matched:
            return f"{ramp}{matched.group(1)}"
    return f"{ramp}{position}" if ramp else position


def _assignments_by_mission(operation):
    return {
        assignment.sort_date_mission_id: assignment
        for assignment in NeoScorpionFuelAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        )
        .options(
            joinedload(NeoScorpionFuelAssignment.sort_date_mission),
            joinedload(NeoScorpionFuelAssignment.assigned_fueler),
            joinedload(NeoScorpionFuelAssignment.assigned_truck),
        )
        .populate_existing()
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
    if status == "needs_sump":
        raise ValueError("That truck requires sumping before assignment.")
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


def _effective_aircraft_fuel_configuration(gateway_id):
    rates = {
        aircraft_type: DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
        for aircraft_type in NEOSCORPION_APU_AIRCRAFT_TYPES
    }
    lateral_limits = {}
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
        if override.max_lateral_imbalance_lbs is not None:
            lateral_limits[override.aircraft_type] = Decimal(
                override.max_lateral_imbalance_lbs
            )
    return rates, lateral_limits


def _effective_apu_rates(gateway_id):
    rates, _limits = _effective_aircraft_fuel_configuration(gateway_id)
    return rates


def _effective_lateral_imbalance_limit(gateway_id, aircraft_type):
    setting = NeoScorpionAircraftFuelSetting.query.filter_by(
        gateway_id=gateway_id,
        aircraft_type=aircraft_type,
    ).first()
    if setting is None or setting.max_lateral_imbalance_lbs is None:
        return None
    return Decimal(setting.max_lateral_imbalance_lbs)


def _apu_rate_field_name(aircraft_type):
    return f"apu_rate_{aircraft_type.lower().replace('-', '_')}"


def _assignment_pump_rate_field_name(aircraft_type):
    return f"assignment_pump_rate_{aircraft_type.lower().replace('-', '_')}"


def _max_lateral_imbalance_field_name(aircraft_type):
    return f"max_lateral_imbalance_{aircraft_type.lower().replace('-', '_')}_lbs"


def _parse_optional_lateral_imbalance(value, aircraft_type):
    submitted = (value or "").strip()
    if not submitted:
        return None
    if not re.fullmatch(r"\d+", submitted):
        raise ValueError(
            f"{aircraft_type} lateral imbalance must be a whole number of pounds."
        )
    return int(submitted)


def _parse_optional_minutes(value, label):
    submitted = (value or "").strip()
    if not submitted:
        return None
    amount = _parse_setting_decimal(submitted, label)
    if amount < 0:
        raise ValueError(f"{label} cannot be negative.")
    return amount


def _parse_optional_pump_rate(value, aircraft_type):
    submitted = (value or "").strip()
    if not submitted:
        return None
    amount = _parse_setting_decimal(submitted, f"{aircraft_type} pump rate")
    if amount <= 0:
        raise ValueError(f"{aircraft_type} pump rate must be greater than zero.")
    return amount


def _parse_setting_decimal(value, label):
    if not re.fullmatch(r"-?\d+(?:\.\d{1,2})?", value):
        raise ValueError(f"{label} must be a nonnegative number with up to two decimals.")
    return Decimal(value)


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
