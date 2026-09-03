"""Deterministic NeoScorpion SPEAR fleet planning and settings."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from types import SimpleNamespace

from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionSpearAuditEntry
from app.services.neoscorpion_dispatch_planning import assignment_mission_timing


SPEAR_RAMP_ORDER = ("Remote", "Alpha", "Bravo", "Charlie", "Delta", "Echo")
SPEAR_PRIORITY_DEFINITIONS = (
    ("avoid_late", "Avoid completing after departure"),
    ("maximize_safe", "Maximize jobs complete by departure -20 minutes"),
    ("protect_reserve", "Protect minimum per-truck reserve"),
    ("minimize_travel", "Minimize ramp travel"),
    ("avoid_top_off", "Avoid unnecessary top-offs"),
    ("balance_workload", "Balance truck/fueler workload"),
)
SPEAR_DEFAULT_PRIORITY_ORDER = tuple(key for key, _label in SPEAR_PRIORITY_DEFINITIONS)
SPEAR_HARD_CONSTRAINTS = (
    "OOS and Needs Sump resources are unavailable",
    "Sent assignments remain locked unless a resource becomes invalid",
    "Trucks missing capacity or current gallons are excluded and flagged",
    "Truck fuel and empty-capacity feasibility",
    "Unavailable fuelers and trucks cannot be assigned",
    "DEFUEL is planned only for an existing dispatcher-created DEFUEL cycle",
)


@dataclass(frozen=True)
class SpearSettings:
    recommendations_enabled: bool = True
    automation_enabled: bool = False
    minimum_truck_reserve_gallons: int = 500
    do_not_top_off_above_percent: int = 70
    truck_minutes_per_ramp_move: Decimal = Decimal("2")
    fueler_begins_at: str = "Remote"
    truck_begins_at: str = "Remote"
    truck_after_top_off: str = "Remote"
    incoming_early_staging_minutes: int = 15
    recalculation_interval_minutes: int = 2
    automation_stability_delay_seconds: int = 5
    priority_order: tuple[str, ...] = SPEAR_DEFAULT_PRIORITY_ORDER


@dataclass(frozen=True)
class SpearPlanStep:
    action_type: str
    mission_id: int | None
    flight_number: str
    ramp: str
    truck_id: int | None
    truck_number: str
    fueler_id: int | None
    fueler_name: str
    projected_start_at_utc: datetime | None
    projected_complete_at_utc: datetime | None
    risk: str
    reason: str
    automatic_eligible: bool = True


@dataclass(frozen=True)
class SpearPlan:
    steps: tuple[SpearPlanStep, ...]
    risks_by_mission_id: dict[int, str]
    unavailable_by_mission_id: dict[int, str]
    covered_count: int
    at_risk_count: int
    late_count: int
    unplanned_count: int
    status_text: str
    token: str


@dataclass(frozen=True)
class SpearSettingsSaveResult:
    changed: bool
    automation_just_enabled: bool


def spear_dispatch_status(plan, settings):
    """Return the compact Fuel Dispatch presentation state for SPEAR."""
    if not settings.recommendations_enabled:
        return {
            "state": "off",
            "label": "SPEAR · OFF",
            "detail": "Recommendations disabled",
            "automation_enabled": False,
        }
    if plan is not None and plan.late_count:
        state = "late"
    elif plan is not None and (plan.at_risk_count or plan.unplanned_count):
        state = "at-risk"
    elif settings.automation_enabled:
        state = "auto"
    else:
        state = "ready"
    labels = {
        "ready": "SPEAR · READY",
        "auto": "SPEAR · AUTO",
        "at-risk": "SPEAR · AT RISK",
        "late": "SPEAR · LATE",
    }
    return {
        "state": state,
        "label": labels[state],
        "detail": "Automation ON" if settings.automation_enabled else "Automation OFF",
        "automation_enabled": settings.automation_enabled,
    }


@dataclass
class _ResourceState:
    available_at: datetime
    ramp: str
    workload: int = 0


def first_automatic_step(plan):
    return next((step for step in plan.steps if step.automatic_eligible), None)


def execute_spear_step(step, *, assign_action, top_off_action):
    """Route one verified recommendation through a caller's canonical workflow."""
    if step.action_type == "top_off":
        return top_off_action(step)
    if step.action_type == "assign":
        return assign_action(step)
    raise ValueError("Unsupported SPEAR action.")


def effective_spear_settings(settings):
    if settings is None:
        return SpearSettings()
    return SpearSettings(
        recommendations_enabled=bool(settings.spear_recommendations_enabled),
        automation_enabled=bool(settings.spear_automation_enabled),
        minimum_truck_reserve_gallons=int(
            settings.spear_minimum_truck_reserve_gallons or 500
        ),
        do_not_top_off_above_percent=int(
            settings.spear_do_not_top_off_above_percent or 70
        ),
        truck_minutes_per_ramp_move=Decimal(
            settings.spear_truck_minutes_per_ramp_move or 2
        ),
        fueler_begins_at=_ramp(settings.spear_fueler_begins_at),
        truck_begins_at=_ramp(settings.spear_truck_begins_at),
        truck_after_top_off=_ramp(settings.spear_truck_after_top_off),
        incoming_early_staging_minutes=int(
            settings.spear_incoming_early_staging_minutes or 15
        ),
        recalculation_interval_minutes=int(
            settings.spear_recalculation_interval_minutes or 2
        ),
        automation_stability_delay_seconds=int(
            settings.spear_automation_stability_delay_seconds or 5
        ),
        priority_order=_priority_order(settings.spear_priority_order_json),
    )


def save_spear_settings(gateway, user, form):
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).with_for_update().first()
    if settings is None:
        settings = NeoScorpionSettings(gateway_id=gateway.id)
        db.session.add(settings)
        db.session.flush()

    previous_automation = bool(settings.spear_automation_enabled)
    enabled = _form_bool(form, "recommendations_enabled")
    automation = _form_bool(form, "automation_enabled")
    if automation and not enabled:
        raise ValueError("Enable SPEAR Recommendations before enabling Automation.")
    values = {
        "spear_recommendations_enabled": enabled,
        "spear_automation_enabled": automation,
        "spear_minimum_truck_reserve_gallons": _whole_number(
            form.get("minimum_truck_reserve_gallons"), "Minimum Truck Reserve Gallons", 0
        ),
        "spear_do_not_top_off_above_percent": _whole_number(
            form.get("do_not_top_off_above_percent"), "Do Not Top Off Above", 1, 100
        ),
        "spear_truck_minutes_per_ramp_move": _positive_decimal(
            form.get("truck_minutes_per_ramp_move"), "Truck Minutes Per Ramp Move"
        ),
        "spear_fueler_begins_at": _ramp(form.get("fueler_begins_at")),
        "spear_truck_begins_at": _ramp(form.get("truck_begins_at")),
        "spear_truck_after_top_off": _ramp(form.get("truck_after_top_off")),
        "spear_incoming_early_staging_minutes": _whole_number(
            form.get("incoming_early_staging_minutes"), "Incoming Aircraft Early Staging", 0
        ),
        "spear_recalculation_interval_minutes": _whole_number(
            form.get("recalculation_interval_minutes"), "Recalculation Interval", 1
        ),
        "spear_automation_stability_delay_seconds": _whole_number(
            form.get("automation_stability_delay_seconds"), "Automation Stability Delay", 1
        ),
        "spear_priority_order_json": json.dumps(
            list(_priority_order(form.get("priority_order")))
        ),
    }
    changed = False
    for field, value in values.items():
        if getattr(settings, field) != value:
            setattr(settings, field, value)
            changed = True
    if changed:
        settings.updated_by_user_id = getattr(user, "id", None)
        db.session.flush()
    return SpearSettingsSaveResult(
        changed=changed,
        automation_just_enabled=automation and not previous_automation,
    )


def build_spear_plan(
    rows,
    *,
    operation,
    planning_settings,
    spear_settings,
    nightly_fuelers,
    nightly_trucks,
    now_utc,
):
    """Plan the remaining sort from already-bounded canonical dispatch data."""
    if not spear_settings.recommendations_enabled:
        return _plan((), {}, {}, status_text="SPEAR: RECOMMENDATIONS OFF")

    fuelers = {
        item.id: item for item in sorted(nightly_fuelers, key=_fueler_sort_key)
    }
    fueler_state = {
        identifier: _ResourceState(now_utc, spear_settings.fueler_begins_at)
        for identifier in fuelers
    }
    truck_rows = {
        item["truck"].id: item
        for item in nightly_trucks
        if item.get("truck") is not None and item.get("selection") is not None
    }
    usable_trucks = {
        identifier: item
        for identifier, item in truck_rows.items()
        if item["selection"].status == "available"
        and item["truck"].is_active
        and not item["truck"].is_out_of_service
        and item["truck"].capacity_gallons is not None
        and item["selection"].current_gallons is not None
    }
    truck_state = {
        identifier: _ResourceState(now_utc, spear_settings.truck_begins_at)
        for identifier in usable_trucks
    }
    truck_gallons = {
        identifier: int(item["selection"].current_gallons)
        for identifier, item in usable_trucks.items()
    }
    _adopt_completed_locations(rows, fueler_state, truck_state)

    steps = []
    risks = {}
    unavailable = {}
    ordered_rows = sorted(
        (row for row in rows if not row.get("administratively_complete")),
        key=lambda row: (
            _departure_at(row) or datetime.max,
            row["mission"].id,
        ),
    )
    for row in ordered_rows:
        mission = row["mission"]
        mission_id = mission.id
        demand = _decimal_or_none(row.get("planning_demand_gallons"))
        if demand is None or demand == 0:
            unavailable[mission_id] = "FUEL DATA INCOMPLETE"
            continue
        ramp = _ramp(row.get("parking_position"), allow_unknown=True)
        if ramp not in SPEAR_RAMP_ORDER:
            unavailable[mission_id] = "PARKING / RAMP REQUIRED"
            continue
        timing = _spear_timing(row, operation, planning_settings)
        if timing is None:
            unavailable[mission_id] = "TIMING DATA INCOMPLETE"
            continue

        assignment = row.get("assignment")
        assigned_fueler = getattr(assignment, "assigned_fueler_user_id", None)
        assigned_truck = getattr(assignment, "assigned_truck_id", None)
        fueler_valid = assigned_fueler in fuelers
        truck_valid = assigned_truck in usable_trucks
        locked = bool(
            assignment
            and assigned_fueler is not None
            and assigned_truck is not None
            and fueler_valid
            and truck_valid
            and getattr(assignment, "operational_status", "active") == "active"
        )
        if locked:
            start, finish = _schedule_pair(
                ramp,
                timing,
                fueler_state[assigned_fueler],
                truck_state[assigned_truck],
                spear_settings,
                staging_allowed_at_utc=_staging_allowed_at(
                    row, spear_settings, now_utc
                ),
            )
            risk = _risk(finish, _departure_at(row))
            risks[mission_id] = risk
            _advance_resources(
                fueler_state[assigned_fueler], truck_state[assigned_truck], ramp, finish
            )
            _apply_demand(truck_gallons, assigned_truck, demand)
            continue

        fueler_ids = (assigned_fueler,) if fueler_valid else tuple(fuelers)
        truck_ids = (assigned_truck,) if truck_valid else tuple(usable_trucks)
        candidates = []
        top_off_candidates = []
        for fueler_id in fueler_ids:
            for truck_id in truck_ids:
                truck = usable_trucks[truck_id]["truck"]
                remaining = truck_gallons[truck_id]
                capacity = int(truck.capacity_gallons)
                feasible, projected = _fuel_feasibility(remaining, capacity, demand)
                if (
                    demand > 0
                    and capacity >= int(demand) + spear_settings.minimum_truck_reserve_gallons
                    and remaining - int(demand) < spear_settings.minimum_truck_reserve_gallons
                ):
                    percent = int(remaining * 100 / capacity) if capacity else 100
                    top_off_candidates.append(
                        (percent > spear_settings.do_not_top_off_above_percent, percent, str(truck.truck_number), truck_id)
                    )
                if not feasible:
                    continue
                start, finish = _schedule_pair(
                    ramp,
                    timing,
                    fueler_state[fueler_id],
                    truck_state[truck_id],
                    spear_settings,
                    staging_allowed_at_utc=_staging_allowed_at(
                        row, spear_settings, now_utc
                    ),
                )
                risk = _risk(finish, _departure_at(row))
                travel = _travel_minutes(
                    truck_state[truck_id].ramp,
                    ramp,
                    spear_settings.truck_minutes_per_ramp_move,
                )
                reserve_short = demand > 0 and projected < spear_settings.minimum_truck_reserve_gallons
                score = _candidate_score(
                    spear_settings.priority_order,
                    risk=risk,
                    reserve_short=reserve_short,
                    travel=travel,
                    workload=fueler_state[fueler_id].workload + truck_state[truck_id].workload,
                    fueler_id=fueler_id,
                    truck_id=truck_id,
                )
                candidates.append((score, fueler_id, truck_id, start, finish, risk, projected))

        if top_off_candidates and all(
            candidate[6] < spear_settings.minimum_truck_reserve_gallons
            for candidate in candidates
        ):
            _above, _percent, _number, truck_id = min(top_off_candidates)
            truck = usable_trucks[truck_id]["truck"]
            steps.append(
                SpearPlanStep(
                    "top_off", mission_id, mission.flight_number or "-", ramp,
                    truck_id, str(truck.truck_number), None, "", None, None,
                    "AT RISK", "Protect reserve before future assignment"
                )
            )
            risks[mission_id] = "AT RISK"
            continue
        if not candidates:
            unavailable[mission_id] = "NO FEASIBLE RESOURCE"
            continue

        _score, fueler_id, truck_id, start, finish, risk, projected = min(candidates)
        fueler = fuelers[fueler_id]
        truck = usable_trucks[truck_id]["truck"]
        staging_allowed_at = _staging_allowed_at(row, spear_settings, now_utc)
        invalid_sent_resource = bool(
            assignment
            and ((assigned_fueler is not None and not fueler_valid) or (assigned_truck is not None and not truck_valid))
        )
        steps.append(
            SpearPlanStep(
                "assign", mission_id, mission.flight_number or "-", ramp,
                truck_id, str(truck.truck_number), fueler_id,
                getattr(fueler, "display_name", None) or getattr(fueler, "full_name", None) or str(fueler_id),
                start, finish, risk,
                "Replace invalid sent resource" if invalid_sent_resource else "Minimum-delay deterministic assignment",
                automatic_eligible=(
                    not bool(row.get("work_has_begun"))
                    and fueler_state[fueler_id].workload == 0
                    and truck_state[truck_id].workload == 0
                    and now_utc >= staging_allowed_at
                ),
            )
        )
        risks[mission_id] = risk
        _advance_resources(fueler_state[fueler_id], truck_state[truck_id], ramp, finish)
        truck_gallons[truck_id] = projected

    return _plan(tuple(steps), risks, unavailable)


def record_spear_execution(operation, step, user, *, automatic, assignment_id=None):
    payload = asdict(step)
    for key in ("projected_start_at_utc", "projected_complete_at_utc"):
        value = payload.get(key)
        payload[key] = value.isoformat() if value else None
    entry = NeoScorpionSpearAuditEntry(
        sort_date_operation_id=operation.id,
        sort_date_mission_id=step.mission_id,
        fuel_assignment_id=assignment_id,
        action_type=step.action_type,
        execution_mode="automatic" if automatic else "dispatcher_approved",
        reason=step.reason,
        fuel_truck_id=step.truck_id,
        fueler_user_id=step.fueler_id,
        projected_start_at_utc=step.projected_start_at_utc,
        projected_complete_at_utc=step.projected_complete_at_utc,
        risk_classification=step.risk.lower().replace(" ", "_"),
        recommendation_json=json.dumps(payload, sort_keys=True),
        executed_by_user_id=getattr(user, "id", None),
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def record_spear_completion(operation, assignment, user):
    """Identify an automatic canonical COMPLETE as a SPEAR action."""
    entry = NeoScorpionSpearAuditEntry(
        sort_date_operation_id=operation.id,
        sort_date_mission_id=assignment.sort_date_mission_id,
        fuel_assignment_id=assignment.id,
        action_type="complete",
        execution_mode="automatic",
        reason="Fueler marked OFF with valid completed fuel data",
        fuel_truck_id=assignment.assigned_truck_id,
        fueler_user_id=assignment.assigned_fueler_user_id,
        risk_classification=None,
        recommendation_json="{}",
        executed_by_user_id=getattr(user, "id", None),
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def priority_rows(settings):
    labels = dict(SPEAR_PRIORITY_DEFINITIONS)
    return tuple((key, labels[key]) for key in settings.priority_order)


def _plan(steps, risks, unavailable, status_text=None):
    covered = sum(value == "COVERED" for value in risks.values())
    at_risk = sum(value == "AT RISK" for value in risks.values())
    late = sum(value == "LATE" for value in risks.values())
    unplanned = len(unavailable)
    if status_text is None:
        if late:
            status_text = f"SPEAR: {late} LATE"
        elif at_risk:
            status_text = f"SPEAR: {at_risk} LOAD{'S' if at_risk != 1 else ''} AT RISK"
        elif unplanned:
            status_text = f"SPEAR: {unplanned} LOAD{'S' if unplanned != 1 else ''} NEED DATA / RESOURCE"
        else:
            status_text = "SPEAR: ALL LOADS COVERED"
    token_payload = [
        (
            step.action_type, step.mission_id, step.truck_id, step.fueler_id,
            step.risk,
        )
        for step in steps
    ]
    token = hashlib.sha256(json.dumps(token_payload, sort_keys=True).encode()).hexdigest()[:24]
    return SpearPlan(
        tuple(steps), dict(risks), dict(unavailable), covered, at_risk, late,
        unplanned, status_text, token,
    )


def _spear_timing(row, operation, planning_settings):
    mission = row["mission"]
    arrival = row.get("arrival_mission")
    departure = _departure_at(row)
    if arrival is None or departure is None:
        return None
    timing_mission = SimpleNamespace(
        actual_block_in_datetime_utc=arrival.actual_block_in_datetime_utc,
        eta_datetime_utc=arrival.eta_datetime_utc or arrival.planned_datetime_utc,
        planned_datetime_utc=departure,
    )
    spear_planning_settings = SimpleNamespace(
        setup_minutes=planning_settings.setup_minutes,
        finishing_minutes=planning_settings.finishing_minutes,
        eta_safety_buffer_minutes=Decimal("5"),
        pump_rate_for=planning_settings.pump_rate_for,
        is_complete_for=lambda aircraft_type: (
            planning_settings.setup_minutes is not None
            and planning_settings.finishing_minutes is not None
            and planning_settings.pump_rate_for(aircraft_type) is not None
        ),
    )
    timing = assignment_mission_timing(
        mission=timing_mission,
        operation=SimpleNamespace(window_minutes=0),
        aircraft_type=row.get("detailed_aircraft_type"),
        planning_demand_gallons=row.get("planning_demand_gallons"),
        planning_settings=spear_planning_settings,
    )
    return timing if timing.available else None


def _departure_at(row):
    mission = row["mission"]
    return mission.eta_datetime_utc or mission.planned_datetime_utc


def _schedule_pair(
    ramp,
    timing,
    fueler,
    truck,
    settings,
    *,
    staging_allowed_at_utc,
):
    truck_arrival = truck.available_at + timedelta(
        minutes=float(_travel_minutes(truck.ramp, ramp, settings.truck_minutes_per_ramp_move))
    )
    fueler_arrival = fueler.available_at + timedelta(
        minutes=float(_travel_minutes(fueler.ramp, ramp, settings.truck_minutes_per_ramp_move) / 2)
    )
    truck_ready = max(truck_arrival, staging_allowed_at_utc)
    fueler_ready = max(fueler_arrival, staging_allowed_at_utc)
    start = max(timing.aircraft_ready_utc, truck_ready, fueler_ready)
    finish = start + timedelta(minutes=float(timing.total_duration_minutes))
    return start, finish


def _staging_allowed_at(row, settings, now_utc):
    arrival = row.get("arrival_mission")
    if arrival is None or arrival.actual_block_in_datetime_utc is not None:
        return now_utc
    eta = arrival.eta_datetime_utc or arrival.planned_datetime_utc
    if eta is None:
        return now_utc
    return eta - timedelta(minutes=settings.incoming_early_staging_minutes)


def _advance_resources(fueler, truck, ramp, finish):
    for state in (fueler, truck):
        state.available_at = finish
        state.ramp = ramp
        state.workload += 1


def _adopt_completed_locations(rows, fueler_state, truck_state):
    completed = sorted(
        (
            row for row in rows
            if row.get("administratively_complete") and row.get("assignment") is not None
        ),
        key=lambda row: (
            getattr(row["assignment"], "completed_at_utc", None) or datetime.min,
            row["mission"].id,
        ),
    )
    for row in completed:
        ramp = _ramp(row.get("parking_position"), allow_unknown=True)
        if ramp not in SPEAR_RAMP_ORDER:
            continue
        assignment = row["assignment"]
        for mapping, identifier in (
            (fueler_state, assignment.assigned_fueler_user_id),
            (truck_state, assignment.assigned_truck_id),
        ):
            if identifier in mapping:
                mapping[identifier].ramp = ramp


def _fuel_feasibility(current, capacity, demand):
    amount = int(abs(demand))
    if demand > 0:
        return current >= amount, current - amount
    return capacity - current >= amount, current + amount


def _apply_demand(gallons_by_truck, truck_id, demand):
    if truck_id not in gallons_by_truck:
        return
    gallons_by_truck[truck_id] += -int(abs(demand)) if demand > 0 else int(abs(demand))


def _risk(finish, departure):
    if departure is None or finish > departure:
        return "LATE"
    if finish > departure - timedelta(minutes=20):
        return "AT RISK"
    return "COVERED"


def _candidate_score(priority_order, *, risk, reserve_short, travel, workload, fueler_id, truck_id):
    factors = {
        "avoid_late": 1 if risk == "LATE" else 0,
        "maximize_safe": 0 if risk == "COVERED" else 1,
        "protect_reserve": 1 if reserve_short else 0,
        "minimize_travel": float(travel),
        "avoid_top_off": 0,
        "balance_workload": workload,
    }
    return tuple(factors[key] for key in priority_order) + (fueler_id, truck_id)


def _travel_minutes(origin, destination, minutes_per_move):
    return Decimal(abs(SPEAR_RAMP_ORDER.index(_ramp(origin)) - SPEAR_RAMP_ORDER.index(_ramp(destination)))) * Decimal(minutes_per_move)


def _fueler_sort_key(user):
    return (
        (getattr(user, "last_name", None) or "").casefold(),
        (getattr(user, "first_name", None) or "").casefold(),
        (getattr(user, "username", None) or "").casefold(),
        user.id,
    )


def _ramp(value, *, allow_unknown=False):
    text = str(value or "").strip()
    for ramp in SPEAR_RAMP_ORDER:
        if text.casefold().startswith(ramp.casefold()):
            return ramp
    if allow_unknown:
        return text or "-"
    raise ValueError("Choose a valid SPEAR ramp.")


def _priority_order(value):
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = [item.strip() for item in value.split(",") if item.strip()]
    elif value is None:
        parsed = []
    else:
        parsed = list(value)
    return tuple(parsed) if set(parsed) == set(SPEAR_DEFAULT_PRIORITY_ORDER) and len(parsed) == len(SPEAR_DEFAULT_PRIORITY_ORDER) else SPEAR_DEFAULT_PRIORITY_ORDER


def _form_bool(form, name):
    return str(form.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _whole_number(value, label, minimum, maximum=None):
    try:
        text = str(value).strip()
        if not text or "." in text:
            raise ValueError
        parsed = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number.") from None
    if parsed < minimum or maximum is not None and parsed > maximum:
        suffix = f" between {minimum} and {maximum}" if maximum is not None else f" at least {minimum}"
        raise ValueError(f"{label} must be{suffix}.")
    return parsed


def _positive_decimal(value, label):
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        parsed = None
    if parsed is None or not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{label} must be greater than zero.")
    return parsed


def _decimal_or_none(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None
