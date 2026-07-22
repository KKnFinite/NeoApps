from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ortools.sat.python import cp_model

from app.extensions import db
from app.models import MotherBrainParkingRule, MotherBrainParkingSettings, SortDateParkingAssignment
from app.services.gateway_matrix import gateway_timezone
from app.services.parking_physical_validator import (
    NORMAL_767_FOOTPRINT_RAMP_CODES,
    NORMAL_BANKS,
    NORMAL_RAMP_CODES,
    REMOTE_ORDER,
    VALID_767_NORMAL_ANCHORS,
    parking_767_footprint_positions,
    parking_configurable_rule_flags,
    validate_parking_physical_rules,
)
from app.services.parking_aircraft import (
    UNKNOWN_PARKING_AIRCRAFT_TYPE,
    normalize_parking_aircraft_type,
    resolve_parking_aircraft_type_from_tail,
)
from app.services.parking_plan import parking_position_options, tail_rows_for_operation
from app.services.parking_rules import (
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    AIRCRAFT_TYPE_RAMP_PREFERENCE,
    ARRIVAL_PARKING_PREFERENCE,
    ARRIVAL_PARKING_REQUIREMENT,
    BLOCKED_PARKING_POSITION,
    DEPARTURE_PARKING_PREFERENCE,
    DEPARTURE_PARKING_REQUIREMENT,
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
    DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
    DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES,
    active_blocked_parking_positions,
    normalize_parking_position_code,
    parking_schedule_rule_key,
    parking_schedule_rule_label,
)
from app.services.building_lineup_parking_preferences import (
    active_belt_pair_preference_map,
    belt_pair_ramp_label,
    building_lineup_destination_belt_pair_map,
    building_lineup_destination_conflicts,
    normalize_destination,
)


logger = logging.getLogger(__name__)

PARKING_OPTIMIZER_TIME_LIMIT_SECONDS = 8.0
PARKING_OPTIMIZER_SEARCH_WORKERS = 1
PARKING_OPTIMIZER_MAX_MEMORY_MB = 256
PARKING_OPTIMIZER_MAX_CANDIDATE_TAILS = 64
PARKING_OPTIMIZER_MAX_STAGE_PLACEMENTS = 1800
PARKING_OPTIMIZER_MAX_STAGE_ETA_RELATIONS = 150000
SUCCESS_SOLVER_STATUSES = {"OPTIMAL", "FEASIBLE"}
PREFERRED_RAMP_SCORE = 600
AVOID_RAMP_PENALTY = 350
DEICE_CLOSE_PAIR_PENALTY = 400
DEICE_MAX_CANDIDATE_ENTRIES = 1400
DEICE_MAX_PAIR_SCAN_COUNT = 300000
DEICE_MAX_CLUSTER_COUNT = 240
DEICE_MAX_CLUSTER_LITERAL_COUNT = 20000
INBOUND_CLOSE_ETA_RAMP_PENALTY = 900
INBOUND_SPACING_MAX_CANDIDATE_ENTRIES = 1400
INBOUND_SPACING_MAX_PAIR_SCAN_COUNT = 300000
INBOUND_SPACING_MAX_CLUSTER_COUNT = 240
INBOUND_SPACING_MAX_CLUSTER_LITERAL_COUNT = 20000
PARKING_WINDOW_PRIORITY_SCORE = 2000
RAMP_BALANCE_PAIR_PENALTY = 260
RAMP_SIDE_BALANCE_PENALTY = 60
PREFERRED_MAX_PER_RAMP_PENALTY = 1200
FOUR_EIGHT_AVOID_PENALTY = 280
FOUR_EIGHT_757_PREFERENCE_SCORE = 420
FOUR_EIGHT_BLOCKED_POSITION_RELIEF = 110
BELT_PAIR_PREFERENCE_SCORE = 300


@dataclass(frozen=True)
class ParkingPlacement:
    tail: str
    ramp: str
    position: str
    lane: int
    cost: int
    aircraft_type: str
    eta: datetime | None = None
    departure: datetime | None = None
    soft_score: int = 0
    preference_reasons: tuple[str, ...] = ()
    belt_pair: str = ""
    belt_preferred_ramps: tuple[str, ...] = ()
    belt_preference_applied: bool = False
    footprint_positions: tuple[str, ...] = ()

    @property
    def label(self):
        return f"{self.position} Slot {self.lane}"


def parking_optimizer_default_options(gateway):
    settings = MotherBrainParkingSettings.query.filter_by(gateway_id=gateway.id).first()
    return {
        "include_remote": bool(settings and settings.include_remote_default),
        "include_throat": bool(settings and settings.include_throat_default),
        "deice_spacing_threshold_minutes": (
            settings.deice_spacing_threshold_minutes
            if settings and settings.deice_spacing_threshold_minutes is not None
            else DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES
        ),
        "preferred_max_per_ramp": (
            settings.preferred_max_per_ramp
            if settings and settings.preferred_max_per_ramp is not None
            else None
        ),
        "inbound_same_ramp_spacing_minutes": (
            settings.inbound_same_ramp_spacing_minutes
            if settings and settings.inbound_same_ramp_spacing_minutes is not None
            else DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES
        ),
    }


def parking_optimizer_preview(
    gateway,
    operation,
    include_remote=None,
    include_throat=None,
    tail_rows=None,
):
    defaults = parking_optimizer_default_options(gateway)
    include_remote = defaults["include_remote"] if include_remote is None else bool(include_remote)
    include_throat = defaults["include_throat"] if include_throat is None else bool(include_throat)
    deice_threshold = _safe_int(
        defaults.get("deice_spacing_threshold_minutes"),
        DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
    )
    inbound_spacing_threshold = _safe_int(
        defaults.get("inbound_same_ramp_spacing_minutes"),
        DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES,
    )
    preferred_max_per_ramp = _safe_optional_int(defaults.get("preferred_max_per_ramp"))
    tail_rows = tail_rows if tail_rows is not None else tail_rows_for_operation(gateway, operation)
    rules = _active_rule_sets(gateway)
    configurable_rule_flags = parking_configurable_rule_flags(gateway.id)
    belt_preference_context = _belt_preference_context(gateway)
    assignments = _active_assignments(operation)
    timezone_name = gateway_timezone(gateway)

    locked_assignments = _locked_assignment_rows(assignments, tail_rows)
    locked_tails = {row["tail"] for row in locked_assignments}
    active_rows = [
        row
        for row in tail_rows
        if row.get("has_active_mission") and _normalize_tail(row.get("tail"))
    ]
    rows_by_tail = {_normalize_tail(row.get("tail")): row for row in active_rows}
    candidate_rows = [
        row for row in active_rows if _normalize_tail(row.get("tail")) not in locked_tails
    ]

    locked_conflicts = [
        conflict.__dict__
        for conflict in validate_parking_physical_rules(
            operation,
            tail_rows=tail_rows,
            include_order_conflicts=True,
        )
    ]
    candidate_guard_reason = _candidate_tail_guard_reason(candidate_rows)
    if candidate_guard_reason:
        candidate_positions = _candidate_positions(include_remote, include_throat)
        candidate_diagnostics = {
            _normalize_tail(row.get("tail")): {
                "candidate_positions_before_filters": len(candidate_positions),
                "candidate_positions_after_hard_filters": 0,
                "candidate_positions_before_sample": _position_sample(
                    [position for position, _ramp in candidate_positions]
                ),
                "candidate_positions_after_sample": "",
                "top_rejection_reason": candidate_guard_reason,
            }
            for row in candidate_rows
            if _normalize_tail(row.get("tail"))
        }
        unassigned = _unassigned_rows(
            candidate_rows,
            {},
            [],
            solver_status="GUARDED",
            include_remote=include_remote,
            include_throat=include_throat,
            locked_conflicts=locked_conflicts,
            hard_rules=rules["hard"],
            candidate_diagnostics=candidate_diagnostics,
        )
        model_diagnostic = _stage_model_diagnostic(
            "candidate_generation",
            0,
            0,
            "GUARDED",
            candidate_guard_reason,
        )
        result = _preview_result(
            "GUARDED",
            include_remote,
            include_throat,
            deice_threshold,
            inbound_spacing_threshold,
            preferred_max_per_ramp,
            _deice_report(
                "skipped",
                "Deice scoring skipped because candidate guard stopped optimizer preview.",
            ),
            _inbound_spacing_report(
                "skipped",
                "Inbound ETA spacing skipped because candidate guard stopped optimizer preview.",
            ),
            len(candidate_rows),
            locked_assignments,
            [],
            unassigned,
            locked_conflicts,
            candidate_guard_reason,
            wall_time=0,
            solver_diagnostic=candidate_guard_reason,
            model_diagnostics=[model_diagnostic],
        )
        logger.warning(
            "Parking optimizer candidate guard stopped preview: operation=%s candidates=%s limit=%s",
            getattr(operation, "id", None),
            len(candidate_rows),
            PARKING_OPTIMIZER_MAX_CANDIDATE_TAILS,
        )
        return result

    locked_normal_ramp_counts = _normal_ramp_counts_for_assignments(assignments)
    locked_normal_side_counts = _normal_side_counts_for_assignments(assignments)
    candidate_positions = _candidate_positions(include_remote, include_throat)
    locked_lane_keys = {
        (_normalize_position(assignment.position_code), int(assignment.lane_number or 1))
        for assignment in assignments
        if _normalize_position(assignment.position_code)
    }
    locked_filled_positions = {
        _normalize_position(assignment.position_code)
        for assignment in assignments
        if _normalize_position(assignment.position_code)
    }
    locked_blocked_positions = _locked_blocked_positions(
        assignments,
        tail_rows,
        configurable_rule_flags,
    )
    locked_a300_positions, locked_767_positions = _locked_configurable_positions(
        assignments,
        tail_rows,
        configurable_rule_flags,
    )
    rule_blocked_positions = active_blocked_parking_positions(gateway)
    locked_eta_by_position = _locked_eta_by_position(
        assignments,
        tail_rows,
        configurable_rule_flags,
    )
    slot_1_placements, slot_1_diagnostics = _build_candidate_placements(
        candidate_rows,
        candidate_positions,
        locked_lane_keys,
        locked_filled_positions | rule_blocked_positions,
        locked_blocked_positions,
        rules["hard"],
        rules["soft"],
        belt_preference_context,
        timezone_name,
        allowed_lanes=(1,),
        configurable_rule_flags=configurable_rule_flags,
        locked_a300_positions=locked_a300_positions,
        locked_767_positions=locked_767_positions,
    )

    slot_1_result = (
        _solve_optimizer_stage(
            slot_1_placements,
            assignments,
            tail_rows,
            timezone_name,
            deice_threshold,
            inbound_spacing_threshold,
            preferred_max_per_ramp,
            locked_filled_positions | locked_blocked_positions,
            rule_blocked_positions,
            locked_eta_by_position,
            locked_normal_ramp_counts,
            locked_normal_side_counts,
            configurable_rule_flags=configurable_rule_flags,
            stage_name="slot_1",
        )
        if slot_1_placements
        else {
            "status": None,
            "status_name": "OPTIMAL",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_no_placement_report(deice_threshold),
            "inbound_spacing_report": _inbound_spacing_no_placement_report(
                inbound_spacing_threshold
            ),
            "model_diagnostics": [
                _stage_model_diagnostic(
                    "slot_1",
                    0,
                    0,
                    "OPTIMAL",
                    "No candidate placements were available for Slot 1.",
                )
            ],
        }
    )

    selected_by_tail = dict(slot_1_result["selected_by_tail"])
    reason_placements = slot_1_placements
    candidate_diagnostics = dict(slot_1_diagnostics)
    status_name = slot_1_result["status_name"]
    wall_time = slot_1_result["wall_time"]
    deice_reports = [slot_1_result.get("deice_report")]
    inbound_spacing_reports = [slot_1_result.get("inbound_spacing_report")]
    model_diagnostics = list(slot_1_result.get("model_diagnostics") or [])

    if status_name in SUCCESS_SOLVER_STATUSES:
        unresolved_rows = [
            row
            for row in candidate_rows
            if _normalize_tail(row.get("tail")) not in selected_by_tail
        ]
        if unresolved_rows:
            stage_filled_positions = locked_filled_positions | locked_blocked_positions
            stage_filled_positions |= _filled_positions_for_selected(selected_by_tail.values())
            stage_filled_positions |= _blocked_positions_for_selected(selected_by_tail.values())
            stage_blocked_positions = locked_blocked_positions | _blocked_positions_for_selected(
                selected_by_tail.values()
            )
            stage_locked_a300_positions = locked_a300_positions | _a300_positions_for_selected(
                selected_by_tail.values()
            )
            stage_locked_767_positions = locked_767_positions | _767_positions_for_selected(
                selected_by_tail.values()
            )
            stage_eta_by_position = _merge_eta_by_position(
                locked_eta_by_position,
                _eta_by_position_for_placements(selected_by_tail.values()),
            )
            stage_normal_ramp_counts = _merge_ramp_counts(
                locked_normal_ramp_counts,
                _normal_ramp_counts_for_placements(selected_by_tail.values()),
            )
            stage_normal_side_counts = _merge_side_counts(
                locked_normal_side_counts,
                _normal_side_counts_for_placements(selected_by_tail.values()),
            )
            slot_1_timing = _slot_1_timing_by_position(
                assignments,
                tail_rows,
                selected_by_tail.values(),
                rows_by_tail,
                timezone_name,
            )
            slot_2_placements, slot_2_diagnostics = _build_candidate_placements(
                unresolved_rows,
                candidate_positions,
                locked_lane_keys,
                stage_filled_positions | rule_blocked_positions,
                stage_blocked_positions,
                rules["hard"],
                rules["soft"],
                belt_preference_context,
                timezone_name,
                allowed_lanes=(2,),
                slot_1_timing_by_position=slot_1_timing,
                configurable_rule_flags=configurable_rule_flags,
                locked_a300_positions=stage_locked_a300_positions,
                locked_767_positions=stage_locked_767_positions,
            )
            reason_placements = slot_2_placements
            candidate_diagnostics.update(slot_2_diagnostics)
            if slot_2_placements:
                slot_2_result = _solve_optimizer_stage(
                    slot_2_placements,
                    assignments,
                    tail_rows,
                    timezone_name,
                    deice_threshold,
                    inbound_spacing_threshold,
                    preferred_max_per_ramp,
                    stage_filled_positions,
                    rule_blocked_positions,
                    stage_eta_by_position,
                    stage_normal_ramp_counts,
                    stage_normal_side_counts,
                    configurable_rule_flags=configurable_rule_flags,
                    stage_name="slot_2",
                )
                wall_time += slot_2_result["wall_time"]
                deice_reports.append(slot_2_result.get("deice_report"))
                inbound_spacing_reports.append(slot_2_result.get("inbound_spacing_report"))
                model_diagnostics.extend(slot_2_result.get("model_diagnostics") or [])
                if slot_2_result["status_name"] in SUCCESS_SOLVER_STATUSES:
                    selected_by_tail.update(slot_2_result["selected_by_tail"])
                else:
                    status_name = slot_2_result["status_name"]

    deice_report = _merge_deice_reports(deice_reports, deice_threshold)
    inbound_spacing_report = _merge_inbound_spacing_reports(
        inbound_spacing_reports,
        inbound_spacing_threshold,
    )

    unassigned = _unassigned_rows(
        candidate_rows,
        selected_by_tail,
        reason_placements,
        solver_status=status_name,
        include_remote=include_remote,
        include_throat=include_throat,
        locked_conflicts=locked_conflicts,
        hard_rules=rules["hard"],
        candidate_diagnostics=candidate_diagnostics,
    )
    selected_deice_reasons = _selected_deice_reasons(
        selected_by_tail,
        assignments,
        tail_rows,
        timezone_name,
        deice_threshold,
    )
    suggestions = []
    for placement in sorted(selected_by_tail.values(), key=lambda item: (item.tail, item.cost, item.label)):
        row = rows_by_tail.get(placement.tail, {})
        suggestions.append(
            _suggestion_row(
                placement,
                row,
                deice_threshold,
                inbound_spacing_threshold,
                preferred_max_per_ramp,
                deice_report.get("status"),
                inbound_spacing_report.get("status"),
                selected_deice_reasons.get(placement.tail, ()),
            )
        )

    summary = (
        "Preview generated. Saved assignments were not changed."
        if suggestions or locked_assignments
        else "No parking suggestions were generated."
    )
    solver_diagnostic = _solver_diagnostic(status_name, suggestions, candidate_rows)
    if solver_diagnostic:
        summary = solver_diagnostic

    result = _preview_result(
        status_name,
        include_remote,
        include_throat,
        deice_threshold,
        inbound_spacing_threshold,
        preferred_max_per_ramp,
        deice_report,
        inbound_spacing_report,
        len(candidate_rows),
        locked_assignments,
        suggestions,
        unassigned,
        locked_conflicts,
        summary,
        wall_time=wall_time,
        solver_diagnostic=solver_diagnostic,
        model_diagnostics=model_diagnostics,
    )
    logger.info(
        "Parking optimizer preview complete: operation=%s status=%s candidates=%s suggestions=%s unresolved=%s model=%s",
        getattr(operation, "id", None),
        result["solver_status"],
        result["candidate_tail_count"],
        len(result["suggested_assignments"]),
        len(result["unassigned_tails"]),
        result["model_diagnostic_summary"],
    )
    return result


def parking_optimizer_error_preview(
    gateway,
    operation,
    include_remote=None,
    include_throat=None,
    tail_rows=None,
    message=None,
):
    defaults = parking_optimizer_default_options(gateway)
    include_remote = defaults["include_remote"] if include_remote is None else bool(include_remote)
    include_throat = defaults["include_throat"] if include_throat is None else bool(include_throat)
    deice_threshold = _safe_int(
        defaults.get("deice_spacing_threshold_minutes"),
        DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
    )
    inbound_spacing_threshold = _safe_int(
        defaults.get("inbound_same_ramp_spacing_minutes"),
        DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES,
    )
    preferred_max_per_ramp = _safe_optional_int(defaults.get("preferred_max_per_ramp"))
    tail_rows = tail_rows if tail_rows is not None else tail_rows_for_operation(gateway, operation)
    assignments = _active_assignments(operation)
    locked_assignments = _locked_assignment_rows(assignments, tail_rows)
    locked_tails = {row["tail"] for row in locked_assignments}
    candidate_rows = [
        row
        for row in tail_rows
        if row.get("has_active_mission")
        and _normalize_tail(row.get("tail"))
        and _normalize_tail(row.get("tail")) not in locked_tails
    ]
    diagnostic = message or (
        "Optimizer failed before solver completed. Existing assignments were preserved."
    )
    unassigned = [
        {
            "tail": _normalize_tail(row.get("tail")),
            "origin": row.get("arrival_origin") or "-",
            "aircraft_type": _parking_aircraft_type_for_row(row),
            "parking_window": _parking_window_label(row),
            "candidate_positions_before_filters": 0,
            "candidate_positions_after_hard_filters": 0,
            "candidate_positions_before_sample": "",
            "candidate_positions_after_sample": "",
            "top_rejection_reason": "",
            "reason": diagnostic,
        }
        for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"]))
    ]
    return _preview_result(
        "ERROR",
        include_remote,
        include_throat,
        deice_threshold,
        inbound_spacing_threshold,
        preferred_max_per_ramp,
        _deice_report("skipped", "Deice scoring skipped because optimizer preview failed."),
        _inbound_spacing_report(
            "skipped",
            "Inbound ETA spacing skipped because optimizer preview failed.",
        ),
        len(candidate_rows),
        locked_assignments,
        [],
        unassigned,
        [],
        diagnostic,
        wall_time=0,
        solver_diagnostic=diagnostic,
        model_diagnostics=[
            _stage_model_diagnostic("preview", 0, 0, "ERROR", diagnostic)
        ],
    )


def apply_parking_optimizer_plan(
    gateway,
    operation,
    include_remote=None,
    include_throat=None,
    user=None,
):
    preview = parking_optimizer_preview(
        gateway,
        operation,
        include_remote=include_remote,
        include_throat=include_throat,
    )
    result = {
        "preview": preview,
        "applied_count": 0,
        "skipped": [],
        "ok": False,
        "message": "",
    }
    eta_conflicts = _eta_order_conflicts_from_preview(preview)
    if eta_conflicts:
        result["message"] = "Resolve ETA order conflicts before applying optimizer suggestions."
        return result

    if preview["solver_status"] not in SUCCESS_SOLVER_STATUSES:
        result["message"] = preview.get("summary") or (
            f"Optimizer returned {preview['solver_status']}; no assignments were applied."
        )
        return result

    suggestions = list(preview.get("suggested_assignments") or [])
    if not suggestions:
        result["message"] = "No suggested assignments were available to apply."
        return result

    assignment_by_tail = {
        _normalize_tail(assignment.tail_number): assignment
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
        if _normalize_tail(assignment.tail_number)
    }
    occupied_lanes = {
        (
            _normalize_position(assignment.position_code),
            int(assignment.lane_number or 1),
        )
        for assignment in assignment_by_tail.values()
        if _normalize_position(assignment.position_code) and assignment.lane_number
    }
    blocked_positions = active_blocked_parking_positions(gateway)

    for suggestion in suggestions:
        tail = _normalize_tail(suggestion.get("tail"))
        position = _normalize_position(suggestion.get("position"))
        lane = _normalize_lane(suggestion.get("lane"))
        if not tail or not position or lane not in (1, 2):
            result["skipped"].append(
                {"tail": tail or "-", "reason": "Suggestion was incomplete."}
            )
            continue

        if position in blocked_positions:
            result["skipped"].append(
                {"tail": tail, "reason": f"{position} is blocked by Parking Rules."}
            )
            continue

        current_assignment = assignment_by_tail.get(tail)
        if current_assignment and _normalize_position(current_assignment.position_code):
            result["skipped"].append(
                {
                    "tail": tail,
                    "reason": f"Current assignment preserved at {current_assignment.position_code} Slot {current_assignment.lane_number}.",
                }
            )
            continue

        if (position, lane) in occupied_lanes:
            result["skipped"].append(
                {"tail": tail, "reason": f"{position} Slot {lane} is no longer open."}
            )
            continue

        assignment = current_assignment or SortDateParkingAssignment(
            sort_date_operation_id=operation.id,
            tail_number=tail,
        )
        if not current_assignment:
            db.session.add(assignment)
            assignment_by_tail[tail] = assignment

        assignment.ramp_code = _ramp_from_position(position)
        assignment.position_code = position
        assignment.lane_number = lane
        assignment.assigned_by_user_id = getattr(user, "id", None)
        assignment.assigned_at = datetime.utcnow()
        occupied_lanes.add((position, lane))
        result["applied_count"] += 1

    db.session.flush()
    unresolved_count = len(preview.get("unassigned_tails") or [])
    result["ok"] = result["applied_count"] > 0
    result["message"] = (
        f"Applied {result['applied_count']} optimizer assignment"
        f"{'' if result['applied_count'] == 1 else 's'}."
    )
    if unresolved_count:
        result["message"] += f" {unresolved_count} unresolved tail{'s' if unresolved_count != 1 else ''} remain."
    if result["skipped"]:
        result["message"] += f" {len(result['skipped'])} suggestion{'s' if len(result['skipped']) != 1 else ''} skipped."
    if result["applied_count"] == 0 and result["skipped"]:
        result["message"] = "No assignments were applied; current manual assignments were preserved."
    return result


def _preview_result(
    solver_status,
    include_remote,
    include_throat,
    deice_threshold,
    inbound_spacing_threshold,
    preferred_max_per_ramp,
    deice_report,
    inbound_spacing_report,
    candidate_tail_count,
    locked_assignments,
    suggestions,
    unassigned_tails,
    conflicts,
    summary,
    wall_time=None,
    solver_diagnostic=None,
    model_diagnostics=None,
):
    model_diagnostics = model_diagnostics or []
    return {
        "solver_status": solver_status,
        "summary": summary,
        "solver_diagnostic": solver_diagnostic or "",
        "suggested_assignments": suggestions,
        "locked_assignments": locked_assignments,
        "unassigned_tails": unassigned_tails,
        "candidate_tail_count": candidate_tail_count,
        "conflicts": conflicts,
        "has_conflicts": bool(conflicts or unassigned_tails),
        "runtime_toggles": {
            "include_remote": bool(include_remote),
            "include_throat": bool(include_throat),
            "deice_spacing_threshold_minutes": deice_threshold,
            "deice_scoring_status": (deice_report or {}).get("status", "disabled"),
            "deice_scoring_detail": (deice_report or {}).get("detail", ""),
            "inbound_same_ramp_spacing_minutes": inbound_spacing_threshold,
            "inbound_spacing_status": (inbound_spacing_report or {}).get("status", "disabled"),
            "inbound_spacing_detail": (inbound_spacing_report or {}).get("detail", ""),
            "preferred_max_per_ramp": preferred_max_per_ramp,
        },
        "wall_time_seconds": wall_time,
        "model_diagnostics": model_diagnostics,
        "model_diagnostic_summary": " ".join(
            str(item.get("summary") or "").strip()
            for item in model_diagnostics
            if str(item.get("summary") or "").strip()
        ),
        "can_apply_preview": solver_status in SUCCESS_SOLVER_STATUSES,
        "preview_only": True,
    }


def _active_rule_sets(gateway):
    rules = MotherBrainParkingRule.query.filter_by(gateway_id=gateway.id, active=True).all()
    hard_rules = {"forbidden": [], "required": []}
    soft_rules = {"preferred": [], "avoid": []}
    for rule in rules:
        category = str(rule.rule_category or "").strip().lower()
        behavior = _normalize_rule_behavior(rule.rule_behavior)
        if category == ORIGIN_RAMP_RESTRICTION:
            if behavior not in hard_rules:
                continue
            hard_rules[behavior].append(rule)
        elif category == ORIGIN_RAMP_PREFERENCE:
            if behavior in {"required", "preferred"}:
                hard_rules["required"].append(rule)
            elif behavior in soft_rules:
                soft_rules[behavior].append(rule)
        elif category in (ARRIVAL_PARKING_REQUIREMENT, DEPARTURE_PARKING_REQUIREMENT):
            hard_rules["required"].append(rule)
        elif category in (ARRIVAL_PARKING_PREFERENCE, DEPARTURE_PARKING_PREFERENCE):
            soft_rules["preferred"].append(rule)
        elif category == AIRCRAFT_TYPE_RAMP_RESTRICTION:
            if behavior not in hard_rules:
                continue
            hard_rules[behavior].append(rule)
        elif category == AIRCRAFT_TYPE_RAMP_PREFERENCE:
            if behavior not in soft_rules:
                continue
            soft_rules[behavior].append(rule)
        elif category == BLOCKED_PARKING_POSITION:
            hard_rules["forbidden"].append(rule)
    return {"hard": hard_rules, "soft": soft_rules}


def _normalize_rule_behavior(value):
    behavior = str(value or "").strip().lower()
    if behavior in {"forbidden", "restricted", "restriction"}:
        return "forbidden"
    if behavior in {"required", "require"}:
        return "required"
    if behavior in {"preferred", "prefer", "preference"}:
        return "preferred"
    if behavior in {"avoid", "avoided"}:
        return "avoid"
    return behavior


def _safe_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value):
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _active_assignments(operation):
    return [
        assignment
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
        if _normalize_tail(assignment.tail_number)
        and _normalize_position(assignment.position_code)
        and assignment.lane_number
    ]


def _locked_assignment_rows(assignments, tail_rows):
    row_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    locked = []
    for assignment in sorted(
        assignments,
        key=lambda item: (
            _normalize_position(item.position_code),
            int(item.lane_number or 1),
            _normalize_tail(item.tail_number),
        ),
    ):
        tail = _normalize_tail(assignment.tail_number)
        row = row_by_tail.get(tail, {})
        status_label = str(row.get("operational_status_label") or "").strip().upper()
        reason = (
            f"{status_label} parked tail fixed."
            if status_label in {"HOT", "SPARE", "QT", "OOS"}
            else "Existing manual assignment preserved."
        )
        locked.append(
            {
                "tail": tail,
                "position": _normalize_position(assignment.position_code),
                "lane": int(assignment.lane_number or 1),
                "label": f"{_normalize_position(assignment.position_code)} Slot {int(assignment.lane_number or 1)}",
                "origin": row.get("arrival_origin") or "-",
                "aircraft_type": _parking_aircraft_type_for_row(row),
                "parking_window": _parking_window_label(row),
                "reason": reason,
            }
        )
    return locked


def _candidate_positions(include_remote, include_throat):
    positions = []
    for _name, ramp, ramp_positions in parking_position_options():
        if ramp == "R" and not include_remote:
            continue
        for position in ramp_positions:
            number = _position_number(position)
            if number in (9, 10) and not include_throat:
                continue
            positions.append((_normalize_position(position), ramp))
    return positions


def _build_candidate_placements(
    candidate_rows,
    candidate_positions,
    locked_lane_keys,
    locked_filled_positions,
    locked_blocked_positions,
    hard_rules,
    soft_rules,
    belt_preference_context,
    timezone_name,
    allowed_lanes=(1, 2),
    slot_1_timing_by_position=None,
    configurable_rule_flags=None,
    locked_a300_positions=None,
    locked_767_positions=None,
):
    placements = []
    diagnostics = {}
    before_positions = sorted({_normalize_position(position) for position, _ramp in candidate_positions})
    allowed_lanes = tuple(lane for lane in allowed_lanes if lane in (1, 2))
    slot_1_timing_by_position = slot_1_timing_by_position or {}
    blocked_position_counts = _blocked_position_counts_by_ramp(hard_rules)
    ordered_rows = sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"]))
    row_count = len(ordered_rows)
    for row_index, row in enumerate(ordered_rows):
        parking_window_priority = (row_count - row_index) * PARKING_WINDOW_PRIORITY_SCORE
        tail = _normalize_tail(row.get("tail"))
        aircraft_type = _parking_aircraft_type_for_row(row)
        after_positions = set()
        rejection_counts = {}
        for order, (position, ramp) in enumerate(candidate_positions):
            rejection_reason = ""
            footprint_positions = _candidate_footprint_positions(
                aircraft_type,
                position,
                configurable_rule_flags,
            )
            if position in locked_blocked_positions:
                rejection_reason = "Blocked by locked/manual assignments."
            elif _rule_blocks_row(row, aircraft_type, position, ramp, hard_rules):
                rejection_reason = _rule_block_reason(row, aircraft_type, position, ramp, hard_rules)
            elif _configurable_rule_blocks_candidate(
                aircraft_type,
                position,
                footprint_positions,
                locked_filled_positions,
                locked_a300_positions,
                locked_767_positions,
                configurable_rule_flags,
            ):
                rejection_reason = _configurable_rule_block_reason(
                    aircraft_type,
                    position,
                    footprint_positions,
                    locked_filled_positions,
                    locked_a300_positions,
                    locked_767_positions,
                    configurable_rule_flags,
                )
            elif not _placement_allows_aircraft(
                aircraft_type,
                position,
                locked_filled_positions,
                configurable_rule_flags,
            ):
                rejection_reason = (
                    "767 footprint cannot fit in available slots."
                    if aircraft_type == "767"
                    else "Aircraft cannot use this position."
                )
            else:
                slot_2_rejection_reason = ""
                open_lanes = [
                    lane
                    for lane in allowed_lanes
                    if (position, lane) not in locked_lane_keys
                ]
                if 2 in open_lanes:
                    slot_2_allowed, slot_2_reason = _slot_2_timing_allows(
                        row,
                        position,
                        slot_1_timing_by_position,
                    )
                    if not slot_2_allowed:
                        open_lanes = [lane for lane in open_lanes if lane != 2]
                        slot_2_rejection_reason = slot_2_reason
                if open_lanes:
                    after_positions.add(position)
                else:
                    rejection_reason = slot_2_rejection_reason or "Blocked by locked/manual assignments."

            if rejection_reason:
                rejection_counts[rejection_reason] = rejection_counts.get(rejection_reason, 0) + 1
                continue
            soft_score, preference_reasons = _soft_rule_score(
                row,
                aircraft_type,
                position,
                ramp,
                soft_rules,
            )
            belt_score, belt_reasons, belt_metadata = _belt_pair_preference_score(
                row,
                ramp,
                belt_preference_context,
            )
            for lane in allowed_lanes:
                if (position, lane) in locked_lane_keys:
                    continue
                if lane == 2:
                    slot_2_allowed, _slot_2_reason = _slot_2_timing_allows(
                        row,
                        position,
                        slot_1_timing_by_position,
                    )
                    if not slot_2_allowed:
                        continue
                slot_policy_score, slot_policy_reasons = _four_eight_slot_score(
                    aircraft_type,
                    position,
                    ramp,
                    lane,
                    blocked_position_counts.get(ramp, 0),
                )
                placements.append(
                    ParkingPlacement(
                        tail=tail,
                        ramp=ramp,
                        position=position,
                        lane=lane,
                        cost=(order * 4) + lane,
                        aircraft_type=aircraft_type,
                        eta=row.get("arrival_block_in_local"),
                        departure=_departure_time_for_deice(row, timezone_name),
                        soft_score=(
                            soft_score
                            + belt_score
                            + slot_policy_score
                            + parking_window_priority
                        ),
                        preference_reasons=tuple(
                            preference_reasons + belt_reasons + slot_policy_reasons
                        ),
                        belt_pair=belt_metadata.get("belt_pair", ""),
                        belt_preferred_ramps=tuple(belt_metadata.get("preferred_ramps", ())),
                        belt_preference_applied=bool(belt_metadata.get("applied")),
                        footprint_positions=tuple(footprint_positions),
                    )
                )
        diagnostics[tail] = {
            "candidate_positions_before_filters": len(before_positions),
            "candidate_positions_after_hard_filters": len(after_positions),
            "candidate_positions_before_sample": _position_sample(before_positions),
            "candidate_positions_after_sample": _position_sample(after_positions),
            "top_rejection_reason": _top_rejection_reason(rejection_counts),
            "building_lineup_belt_pair": _belt_pair_for_row(
                row,
                belt_preference_context,
            ),
            "building_lineup_preferred_ramps": _belt_preferred_ramps_for_row(
                row,
                belt_preference_context,
            ),
        }
    return placements, diagnostics


def _candidate_tail_guard_reason(candidate_rows):
    candidate_count = len(candidate_rows or [])
    if candidate_count <= PARKING_OPTIMIZER_MAX_CANDIDATE_TAILS:
        return ""
    return (
        "Optimizer candidate guard skipped solving to protect memory "
        f"({candidate_count} active unparked tails exceeds "
        f"{PARKING_OPTIMIZER_MAX_CANDIDATE_TAILS}). Existing assignments were preserved."
    )


def _stage_guard_reason(placements, eta_relation_count):
    placement_count = len(placements)
    if placement_count > PARKING_OPTIMIZER_MAX_STAGE_PLACEMENTS:
        return (
            "Optimizer model guard skipped solving to protect memory "
            f"({placement_count} candidate placements exceeds "
            f"{PARKING_OPTIMIZER_MAX_STAGE_PLACEMENTS})."
        )
    if eta_relation_count > PARKING_OPTIMIZER_MAX_STAGE_ETA_RELATIONS:
        return (
            "Optimizer model guard skipped solving to protect memory "
            f"({eta_relation_count} ETA relation checks exceeds "
            f"{PARKING_OPTIMIZER_MAX_STAGE_ETA_RELATIONS})."
        )
    return ""


def _stage_model_diagnostic(
    stage,
    placement_count,
    eta_relation_count,
    status,
    summary,
    wall_time=None,
    deice_status="",
    deice_candidate_count=0,
    deice_pair_scan_count=0,
    search_workers=None,
    memory_limit_mb=None,
):
    return {
        "stage": stage,
        "placement_count": placement_count,
        "eta_relation_count": eta_relation_count,
        "deice_candidate_count": deice_candidate_count,
        "deice_pair_scan_count": deice_pair_scan_count,
        "search_workers": search_workers or PARKING_OPTIMIZER_SEARCH_WORKERS,
        "memory_limit_mb": memory_limit_mb or PARKING_OPTIMIZER_MAX_MEMORY_MB,
        "status": status,
        "summary": summary,
        "wall_time_seconds": wall_time,
        "deice_status": deice_status,
    }


def _eta_relation_count_for_placements(placements, locked_eta_by_position):
    contributors_by_position = {}
    for placement in placements:
        for position in _filled_positions_for_placement(placement):
            contributors_by_position.setdefault(position, 0)
            contributors_by_position[position] += 1

    total = 0
    locked_eta_by_position = locked_eta_by_position or {}
    for sequence in _eta_order_sequences():
        for index, higher_position in enumerate(sequence):
            higher_candidate_count = contributors_by_position.get(higher_position, 0)
            higher_locked_count = len(locked_eta_by_position.get(higher_position, []) or [])
            if not higher_candidate_count and not higher_locked_count:
                continue
            for lower_position in sequence[:index]:
                lower_candidate_count = contributors_by_position.get(lower_position, 0)
                lower_locked_count = len(locked_eta_by_position.get(lower_position, []) or [])
                total += higher_candidate_count * lower_locked_count
                total += higher_candidate_count * lower_candidate_count
                total += higher_locked_count * lower_candidate_count
    return total


def _deice_complexity_for_placements(placements, threshold_minutes):
    if _safe_int(threshold_minutes, 0) <= 0:
        return 0, 0
    by_ramp = {}
    for placement in placements:
        if not _is_deice_position(placement.position) or not placement.departure:
            continue
        by_ramp[placement.ramp] = by_ramp.get(placement.ramp, 0) + 1
    candidate_count = sum(by_ramp.values())
    pair_scan_count = sum(count * count for count in by_ramp.values())
    return candidate_count, pair_scan_count


def _configure_cp_solver(solver):
    solver.parameters.max_time_in_seconds = PARKING_OPTIMIZER_TIME_LIMIT_SECONDS
    solver.parameters.num_search_workers = PARKING_OPTIMIZER_SEARCH_WORKERS
    try:
        solver.parameters.max_memory_in_mb = PARKING_OPTIMIZER_MAX_MEMORY_MB
    except (AttributeError, ValueError, TypeError):
        logger.debug("CP-SAT max_memory_in_mb parameter is unavailable in this runtime.")
    return solver


def _solve_optimizer_stage(
    placements,
    assignments,
    tail_rows,
    timezone_name,
    deice_threshold,
    inbound_spacing_threshold,
    preferred_max_per_ramp,
    filled_positions,
    fill_order_satisfied_positions,
    eta_by_position,
    locked_normal_ramp_counts=None,
    locked_normal_side_counts=None,
    configurable_rule_flags=None,
    stage_name="stage",
):
    if not placements:
        return {
            "status": None,
            "status_name": "NO_CANDIDATES",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_no_placement_report(deice_threshold),
            "inbound_spacing_report": _inbound_spacing_no_placement_report(
                inbound_spacing_threshold
            ),
            "model_diagnostics": [
                _stage_model_diagnostic(
                    stage_name,
                    0,
                    0,
                    "NO_CANDIDATES",
                    "No candidate placements were available for this optimizer stage.",
                )
            ],
        }

    eta_relation_count = _eta_relation_count_for_placements(placements, eta_by_position)
    deice_candidate_count, deice_pair_scan_count = _deice_complexity_for_placements(
        placements,
        deice_threshold,
    )
    logger.info(
        "Parking optimizer %s prepared: placements=%s eta_relations=%s deice_candidates=%s deice_pair_scans=%s workers=%s memory_mb=%s",
        stage_name,
        len(placements),
        eta_relation_count,
        deice_candidate_count,
        deice_pair_scan_count,
        PARKING_OPTIMIZER_SEARCH_WORKERS,
        PARKING_OPTIMIZER_MAX_MEMORY_MB,
    )
    guard_reason = _stage_guard_reason(placements, eta_relation_count)
    if guard_reason:
        logger.warning(
            "Parking optimizer guarded %s for operation model: placements=%s eta_relations=%s reason=%s",
            stage_name,
            len(placements),
            eta_relation_count,
            guard_reason,
        )
        return {
            "status": None,
            "status_name": "GUARDED",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_report(
                "skipped",
                "Deice scoring skipped because the optimizer model guard stopped this stage.",
            ),
            "inbound_spacing_report": _inbound_spacing_report(
                "skipped",
                "Inbound ETA spacing skipped because the optimizer model guard stopped this stage.",
            ),
            "model_diagnostics": [
                _stage_model_diagnostic(
                    stage_name,
                    len(placements),
                    eta_relation_count,
                    "GUARDED",
                    guard_reason,
                    deice_candidate_count=deice_candidate_count,
                    deice_pair_scan_count=deice_pair_scan_count,
                )
            ],
        }

    model = cp_model.CpModel()
    try:
        variables = {
            placement: model.NewBoolVar(
                f"assign_{_var_key(placement.tail)}_{placement.position}_{placement.lane}"
            )
            for placement in placements
        }

        _add_tail_constraints(model, variables)
        _add_lane_constraints(model, variables)
        fill_exprs = _add_position_fill_constraints(model, variables, filled_positions)
        _add_fill_order_constraints(
            model,
            variables,
            fill_exprs,
            fill_order_satisfied_positions=fill_order_satisfied_positions,
        )
        _add_throat_constraints(model, variables, fill_exprs)
        _add_767_block_constraints(model, variables, fill_exprs)
        _add_configurable_parking_rule_constraints(
            model,
            variables,
            fill_exprs,
            configurable_rule_flags,
        )
        _add_eta_order_constraints(model, variables, eta_by_position)

        objective_terms = [
            variable * (100000 + placement.soft_score - placement.cost)
            for placement, variable in variables.items()
        ]
        deice_terms, deice_report = _deice_spacing_penalty_terms(
            model,
            variables,
            assignments,
            tail_rows,
            timezone_name,
            deice_threshold,
        )
        objective_terms.extend(deice_terms)
        inbound_spacing_terms, inbound_spacing_report = _inbound_eta_spacing_penalty_terms(
            model,
            variables,
            assignments,
            tail_rows,
            inbound_spacing_threshold,
        )
        objective_terms.extend(inbound_spacing_terms)
        objective_terms.extend(
            _ramp_balance_penalty_terms(
                model,
                variables,
                locked_normal_ramp_counts or {},
                preferred_max_per_ramp,
            )
        )
        objective_terms.extend(
            _ramp_side_balance_penalty_terms(
                model,
                variables,
                locked_normal_side_counts or {},
            )
        )
        model.Maximize(sum(objective_terms))

        solver = _configure_cp_solver(cp_model.CpSolver())
        status = solver.Solve(model)
        status_name = solver.StatusName(status)
    except Exception as exc:  # pragma: no cover - exercised through patched solver tests
        logger.exception(
            "Parking optimizer failed in %s: placements=%s eta_relations=%s",
            stage_name,
            len(placements),
            eta_relation_count,
        )
        detail = f"Optimizer solver failed safely: {exc}"
        return {
            "status": None,
            "status_name": "ERROR",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_report(
                "skipped",
                "Deice scoring skipped because optimizer solver failed.",
            ),
            "inbound_spacing_report": _inbound_spacing_report(
                "skipped",
                "Inbound ETA spacing skipped because optimizer solver failed.",
            ),
            "model_diagnostics": [
                _stage_model_diagnostic(
                    stage_name,
                    len(placements),
                    eta_relation_count,
                    "ERROR",
                    detail,
                    deice_candidate_count=deice_candidate_count,
                    deice_pair_scan_count=deice_pair_scan_count,
                )
            ],
        }

    logger.info(
        "Parking optimizer %s solved: status=%s placements=%s eta_relations=%s wall_time=%.3f deice=%s",
        stage_name,
        status_name,
        len(placements),
        eta_relation_count,
        solver.WallTime() if status else 0,
        (deice_report or {}).get("status"),
    )
    model_diagnostics = [
        _stage_model_diagnostic(
            stage_name,
            len(placements),
            eta_relation_count,
            status_name,
            (
                f"{stage_name}: {len(placements)} placements, "
                f"{eta_relation_count} ETA relation checks, solver {status_name}."
            ),
            wall_time=solver.WallTime() if status else 0,
            deice_status=(deice_report or {}).get("status", ""),
            deice_candidate_count=deice_candidate_count,
            deice_pair_scan_count=deice_pair_scan_count,
        )
    ]
    selected_by_tail = {}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for placement in sorted(placements, key=lambda item: (item.tail, item.cost, item.label)):
            if solver.Value(variables[placement]) != 1:
                continue
            selected_by_tail[placement.tail] = placement
    return {
        "status": status,
        "status_name": status_name,
        "selected_by_tail": selected_by_tail,
        "wall_time": solver.WallTime() if status else 0,
        "deice_report": deice_report,
        "inbound_spacing_report": inbound_spacing_report,
        "model_diagnostics": model_diagnostics,
    }


def _add_tail_constraints(model, variables):
    by_tail = {}
    for placement, variable in variables.items():
        by_tail.setdefault(placement.tail, []).append(variable)
    for tail_variables in by_tail.values():
        model.Add(sum(tail_variables) <= 1)


def _add_lane_constraints(model, variables):
    by_lane = {}
    for placement, variable in variables.items():
        by_lane.setdefault((placement.position, placement.lane), []).append(variable)
    for lane_variables in by_lane.values():
        model.Add(sum(lane_variables) <= 1)


def _add_position_fill_constraints(model, variables, locked_filled_positions):
    all_positions = {placement.position for placement in variables}
    for placement in variables:
        blocked = _blocked_position_for_placement(placement)
        if blocked:
            all_positions.add(blocked)
    fill_exprs = {}
    for position in sorted(all_positions | set(locked_filled_positions)):
        if position in locked_filled_positions:
            fill_exprs[position] = 1
            continue
        contributors = [
            variable
            for placement, variable in variables.items()
            if placement.position == position or _blocked_position_for_placement(placement) == position
        ]
        fill = model.NewBoolVar(f"filled_{position}")
        if contributors:
            model.AddMaxEquality(fill, contributors)
        else:
            model.Add(fill == 0)
        fill_exprs[position] = fill
    return fill_exprs


def _add_fill_order_constraints(
    model,
    variables,
    fill_exprs,
    fill_order_satisfied_positions=None,
):
    fill_order_satisfied_positions = set(fill_order_satisfied_positions or ())
    for placement, variable in variables.items():
        number = _position_number(placement.position)
        if placement.ramp in NORMAL_RAMP_CODES:
            for bank in NORMAL_BANKS:
                if number not in bank:
                    continue
                for lower in bank:
                    if lower >= number:
                        continue
                    model.Add(
                        variable
                        <= _fill_order_expr(
                            fill_exprs,
                            f"{placement.ramp}{lower:02d}",
                            fill_order_satisfied_positions,
                        )
                    )
        elif placement.ramp == "R":
            try:
                index = REMOTE_ORDER.index(placement.position)
            except ValueError:
                continue
            for lower_position in REMOTE_ORDER[:index]:
                model.Add(
                    variable
                    <= _fill_order_expr(
                        fill_exprs,
                        lower_position,
                        fill_order_satisfied_positions,
                    )
                )


def _add_throat_constraints(model, variables, fill_exprs):
    for placement, variable in variables.items():
        number = _position_number(placement.position)
        if placement.ramp not in NORMAL_RAMP_CODES or number not in (9, 10):
            continue
        if number == 9:
            model.Add(variable <= _fill_expr(fill_exprs, f"{placement.ramp}10"))
            _add_clear_bank_or_constraint(model, variable, placement.ramp, fill_exprs, NORMAL_BANKS)
        elif number == 10:
            _add_clear_bank_or_constraint(
                model,
                variable,
                placement.ramp,
                fill_exprs,
                ((2, 3, 4), (6, 7, 8)),
            )


def _add_clear_bank_or_constraint(model, variable, ramp, fill_exprs, banks):
    clear_variables = []
    for index, bank in enumerate(banks):
        clear = model.NewBoolVar(f"clear_{ramp}_{index}_{variable.Name()}")
        fills = [_fill_expr(fill_exprs, f"{ramp}{number:02d}") for number in bank]
        for fill in fills:
            model.Add(clear <= 1 - fill)
        model.Add(clear >= 1 - sum(fills))
        clear_variables.append(clear)
    model.Add(variable <= sum(clear_variables))


def _add_767_block_constraints(model, variables, fill_exprs):
    variables_by_position = {}
    for placement, variable in variables.items():
        variables_by_position.setdefault(placement.position, []).append((placement, variable))

    for placement, variable in variables.items():
        blocked = _blocked_position_for_placement(placement)
        if not blocked:
            continue
        if _is_constant_one(_fill_expr(fill_exprs, blocked)):
            model.Add(variable == 0)
            continue
        for blocked_placement, blocked_variable in variables_by_position.get(blocked, []):
            if blocked_placement.tail == placement.tail:
                continue
            model.Add(variable + blocked_variable <= 1)


def _add_configurable_parking_rule_constraints(
    model,
    variables,
    fill_exprs,
    configurable_rule_flags,
):
    if _configurable_rule_enabled(configurable_rule_flags, "force_767_to_position_4_8"):
        for placement, variable in variables.items():
            if placement.aircraft_type != "767":
                continue
            number = _position_number(placement.position)
            if placement.ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES:
                continue
            if number in (3, 7):
                lower_pair = (1, 2) if number == 3 else (5, 6)
                _add_767_anchor_block_when_pair_filled(
                    model,
                    variable,
                    placement.ramp,
                    lower_pair,
                    fill_exprs,
                )
            elif number in (4, 8):
                lower_pair = (1, 2) if number == 4 else (5, 6)
                for lower_number in lower_pair:
                    model.Add(
                        variable
                        <= _fill_expr(
                            fill_exprs,
                            f"{placement.ramp}{lower_number:02d}",
                        )
                    )

    if not _configurable_rule_enabled(
        configurable_rule_flags,
        "prevent_767_adjacent_to_a300",
    ):
        return

    a300_placements = [
        (placement, variable)
        for placement, variable in variables.items()
        if placement.aircraft_type == "A300"
    ]
    for placement, variable in variables.items():
        if placement.aircraft_type != "767":
            continue
        for a300_placement, a300_variable in a300_placements:
            if placement.ramp != a300_placement.ramp:
                continue
            if not _positions_are_directly_adjacent(placement.position, a300_placement.position):
                continue
            model.Add(variable + a300_variable <= 1)


def _add_767_anchor_block_when_pair_filled(
    model,
    variable,
    ramp,
    lower_pair,
    fill_exprs,
):
    first = _fill_expr(fill_exprs, f"{ramp}{lower_pair[0]:02d}")
    second = _fill_expr(fill_exprs, f"{ramp}{lower_pair[1]:02d}")
    if _is_constant_zero(first) or _is_constant_zero(second):
        return
    if _is_constant_one(first) and _is_constant_one(second):
        model.Add(variable == 0)
        return
    if _is_constant_one(first):
        model.Add(variable + second <= 1)
        return
    if _is_constant_one(second):
        model.Add(variable + first <= 1)
        return

    both_filled = model.NewBoolVar(f"filled_pair_{ramp}_{lower_pair[0]}_{lower_pair[1]}_{variable.Name()}")
    model.AddBoolAnd([first, second]).OnlyEnforceIf(both_filled)
    model.AddBoolOr([first.Not(), second.Not(), both_filled])
    model.Add(variable + both_filled <= 1)


def _add_eta_order_constraints(model, variables, locked_eta_by_position):
    contributors_by_position = {}
    for placement, variable in variables.items():
        for position in _filled_positions_for_placement(placement):
            contributors_by_position.setdefault(position, []).append((placement, variable))

    for sequence in _eta_order_sequences():
        for index, higher_position in enumerate(sequence):
            lower_positions = sequence[:index]
            if not lower_positions:
                continue

            higher_candidates = contributors_by_position.get(higher_position, [])
            higher_locked = locked_eta_by_position.get(higher_position, [])
            for lower_position in lower_positions:
                lower_candidates = contributors_by_position.get(lower_position, [])
                lower_locked = locked_eta_by_position.get(lower_position, [])

                for higher_placement, higher_variable in higher_candidates:
                    for lower_eta in lower_locked:
                        if _eta_before(higher_placement.eta, lower_eta):
                            model.Add(higher_variable == 0)
                    for lower_placement, lower_variable in lower_candidates:
                        if higher_placement == lower_placement:
                            continue
                        if _eta_before(higher_placement.eta, lower_placement.eta):
                            model.Add(higher_variable + lower_variable <= 1)

                for higher_eta in higher_locked:
                    for lower_placement, lower_variable in lower_candidates:
                        if _eta_before(higher_eta, lower_placement.eta):
                            model.Add(lower_variable == 0)


def _unassigned_rows(
    candidate_rows,
    selected_by_tail,
    placements,
    solver_status=None,
    include_remote=False,
    include_throat=False,
    locked_conflicts=None,
    hard_rules=None,
    candidate_diagnostics=None,
):
    placement_tails = {placement.tail for placement in placements}
    rows = []
    for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"])):
        tail = _normalize_tail(row.get("tail"))
        if tail in selected_by_tail:
            continue
        aircraft_type = _parking_aircraft_type_for_row(row)
        diagnostics = (candidate_diagnostics or {}).get(tail, {})
        rows.append(
            {
                "tail": tail,
                "origin": row.get("arrival_origin") or "-",
                "aircraft_type": aircraft_type,
                "parking_window": _parking_window_label(row),
                "candidate_positions_before_filters": diagnostics.get(
                    "candidate_positions_before_filters",
                    0,
                ),
                "candidate_positions_after_hard_filters": diagnostics.get(
                    "candidate_positions_after_hard_filters",
                    0,
                ),
                "candidate_positions_before_sample": diagnostics.get(
                    "candidate_positions_before_sample",
                    "",
                ),
                "candidate_positions_after_sample": diagnostics.get(
                    "candidate_positions_after_sample",
                    "",
                ),
                "top_rejection_reason": diagnostics.get("top_rejection_reason", ""),
                "building_lineup_belt_pair": diagnostics.get(
                    "building_lineup_belt_pair",
                    "",
                ),
                "building_lineup_preferred_ramps": diagnostics.get(
                    "building_lineup_preferred_ramps",
                    (),
                ),
                "reason": _unresolved_reason(
                    row,
                    aircraft_type,
                    has_candidate=tail in placement_tails,
                    solver_status=solver_status,
                    include_remote=include_remote,
                    include_throat=include_throat,
                    locked_conflicts=locked_conflicts or [],
                    hard_rules=hard_rules or {},
                    diagnostics=diagnostics,
                ),
            }
        )
    return rows


def _suggestion_row(
    placement,
    row,
    deice_threshold=0,
    inbound_spacing_threshold=0,
    preferred_max_per_ramp=None,
    deice_status="disabled",
    inbound_spacing_status="disabled",
    deice_reasons=(),
):
    reasons = ["Suggested by optimizer preview."]
    if placement.lane == 2:
        reasons.append(
            "Slot 2 used because no valid Slot 1 position remained and Slot 1 departs before this tail arrives."
        )
    if placement.ramp in NORMAL_RAMP_CODES:
        reasons.append(
            "Ramp balancing considered across Alpha, Bravo, Charlie, Delta, and Echo."
        )
        reasons.append(
            "01-04 / 05-08 side balance considered within this ramp."
        )
        if inbound_spacing_threshold and inbound_spacing_status == "applied":
            reasons.append(
                f"Inbound ETA spacing checked: same-ramp arrivals under {inbound_spacing_threshold} min are penalized."
            )
        if preferred_max_per_ramp is not None:
            reasons.append(
                f"Preferred Max Per Ramp {preferred_max_per_ramp} considered as a soft limit."
            )
    reasons.extend(placement.preference_reasons)
    if deice_threshold and placement.departure and deice_status == "applied":
        reasons.append(
            f"Deice spacing checked: same-ramp departures under {deice_threshold} min are penalized."
        )
    reasons.extend(deice_reasons or ())
    return {
        "tail": placement.tail,
        "position": placement.position,
        "lane": placement.lane,
        "label": placement.label,
        "origin": row.get("arrival_origin") or "-",
        "aircraft_type": placement.aircraft_type,
        "parking_window": _parking_window_label(row),
        "reason": " ".join(reasons),
        "building_lineup_belt_pair": placement.belt_pair,
        "building_lineup_preferred_ramps": placement.belt_preferred_ramps,
        "building_lineup_belt_preference_applied": placement.belt_preference_applied,
    }


def _rule_blocks_row(row, aircraft_type, position, ramp, rules):
    if any(
        _rule_matches_position(rule, position, ramp)
        for rule in _blocked_position_rules(rules.get("forbidden", []))
    ):
        return True
    required_rules = _rules_for_row(row, aircraft_type, rules.get("required", []))
    if required_rules and not _required_rules_allow(required_rules, position, ramp):
        return True
    return any(
        _rule_matches_position(rule, position, ramp)
        for rule in _rules_for_row(row, aircraft_type, rules.get("forbidden", []))
    )


def _rule_block_reason(row, aircraft_type, position, ramp, rules):
    if any(
        _rule_matches_position(rule, position, ramp)
        for rule in _blocked_position_rules(rules.get("forbidden", []))
    ):
        return f"{position} is blocked by Parking Rules."

    required_rules = _rules_for_row(row, aircraft_type, rules.get("required", []))
    if required_rules and not _required_rules_allow(required_rules, position, ramp):
        failing_rules = _failing_required_rules(required_rules, position, ramp)
        required_subject_types = {
            str(rule.subject_type or "").strip().lower() for rule in failing_rules
        }
        if "aircraft_type" in required_subject_types:
            return "Aircraft type required on another ramp."
        if "arrival_plan" in required_subject_types:
            return "Arrival required parking rule requires another ramp or position."
        if "departure_plan" in required_subject_types:
            return "Departure required parking rule requires another ramp or position."
        return "Origin required on another ramp."

    matching_forbidden = [
        rule
        for rule in _rules_for_row(row, aircraft_type, rules.get("forbidden", []))
        if _rule_matches_position(rule, position, ramp)
    ]
    if any(str(rule.subject_type or "").strip().lower() == "aircraft_type" for rule in matching_forbidden):
        return "Aircraft type restricted from available ramps."
    if any(str(rule.subject_type or "").strip().lower() == "origin" for rule in matching_forbidden):
        return "Origin restricted from available ramps."
    return "Blocked by hard parking rules."


def _soft_rule_score(row, aircraft_type, position, ramp, soft_rules):
    score = 0
    reasons = []
    for rule in _rules_for_row(row, aircraft_type, soft_rules.get("preferred", [])):
        if not _rule_matches_position(rule, position, ramp):
            continue
        score += PREFERRED_RAMP_SCORE
        reasons.append(_soft_rule_reason(rule, "prefers"))
    for rule in _rules_for_row(row, aircraft_type, soft_rules.get("avoid", [])):
        reasons.append(_soft_rule_reason(rule, "avoids"))
        if not _rule_matches_position(rule, position, ramp):
            continue
        score -= AVOID_RAMP_PENALTY
    return score, reasons


def _belt_preference_context(gateway):
    return {
        "destination_map": building_lineup_destination_belt_pair_map(gateway),
        "destination_conflicts": building_lineup_destination_conflicts(gateway),
        "preferences": active_belt_pair_preference_map(gateway),
    }


def _belt_pair_preference_score(row, ramp, context):
    belt_pair = _belt_pair_for_row(row, context)
    preferred_ramps = _belt_preferred_ramps_for_row(row, context)
    if not belt_pair or not preferred_ramps:
        return 0, [], {"belt_pair": belt_pair, "preferred_ramps": preferred_ramps}

    metadata = {
        "belt_pair": belt_pair,
        "preferred_ramps": preferred_ramps,
        "applied": ramp in preferred_ramps,
    }
    if ramp not in preferred_ramps:
        return 0, [], metadata

    destination = _departure_destination_for_row(row)
    ramp_labels = ", ".join(belt_pair_ramp_label(item) for item in preferred_ramps)
    reason = (
        f"Building Lineup {belt_pair} belt preference for {destination} "
        f"prefers {ramp_labels}; selected ramp {belt_pair_ramp_label(ramp)} received soft score."
    )
    return BELT_PAIR_PREFERENCE_SCORE, [reason], metadata


def _belt_pair_for_row(row, context):
    destination = _departure_destination_for_row(row)
    if not destination:
        return ""
    if destination in (context or {}).get("destination_conflicts", {}):
        return ""
    return (context or {}).get("destination_map", {}).get(destination, "")


def _belt_preferred_ramps_for_row(row, context):
    belt_pair = _belt_pair_for_row(row, context)
    if not belt_pair:
        return ()
    return tuple((context or {}).get("preferences", {}).get(belt_pair, ()))


def _departure_destination_for_row(row):
    destination = row.get("departure_destination")
    if not destination and row.get("departure"):
        destination = getattr(row["departure"], "destination", "")
    return normalize_destination(destination)


def _required_rules_allow(required_rules, position, ramp):
    return not _failing_required_rules(required_rules, position, ramp)


def _failing_required_rules(required_rules, position, ramp):
    failing = []
    for group in _required_rule_groups(required_rules):
        if not any(_rule_matches_position(rule, position, ramp) for rule in group):
            failing.extend(group)
    return failing


def _required_rule_groups(required_rules):
    groups = {}
    for rule in required_rules:
        key = (
            str(rule.rule_category or "").strip().lower(),
            str(rule.subject_type or "").strip().lower(),
            str(rule.subject_value or "").strip().upper(),
        )
        groups.setdefault(key, []).append(rule)
    return tuple(groups.values())


def _soft_rule_reason(rule, verb):
    subject_type = str(rule.subject_type or "").strip().lower()
    subject = _normalize_subject(rule.subject_value)
    ramp = str(rule.ramp_code or "").strip().upper()
    ramp_label = _rule_target_label(rule)
    if subject_type == "arrival_plan":
        return f"Arrival {parking_schedule_rule_label(rule.subject_value)} {verb} {ramp_label}."
    if subject_type == "departure_plan":
        return f"Departure {parking_schedule_rule_label(rule.subject_value)} {verb} {ramp_label}."
    if subject_type == "origin":
        return f"Origin {subject} {verb} {ramp_label}."
    return f"Aircraft {subject} {verb} {ramp_label}."


def _four_eight_slot_score(aircraft_type, position, ramp, lane, blocked_position_count=0):
    if lane != 1 or ramp not in NORMAL_RAMP_CODES or _position_number(position) not in (4, 8):
        return 0, []

    relief = min(
        FOUR_EIGHT_AVOID_PENALTY,
        max(0, int(blocked_position_count or 0)) * FOUR_EIGHT_BLOCKED_POSITION_RELIEF,
    )
    score = -(FOUR_EIGHT_AVOID_PENALTY - relief)
    reasons = []
    if aircraft_type == "757":
        score += FOUR_EIGHT_757_PREFERENCE_SCORE
        reasons.append("757 preferred on 04/08 position.")
    if relief:
        reasons.append("04/08 used because this ramp has blocked positions.")
    return score, reasons


def _ramp_balance_penalty_terms(
    model,
    variables,
    locked_normal_ramp_counts,
    preferred_max_per_ramp,
):
    terms = []
    normal_entries = [
        (placement, variable)
        for placement, variable in variables.items()
        if placement.ramp in NORMAL_RAMP_CODES
    ]
    if not normal_entries:
        return terms

    by_ramp = {ramp: [] for ramp in sorted(NORMAL_RAMP_CODES)}
    for placement, variable in normal_entries:
        by_ramp.setdefault(placement.ramp, []).append((placement, variable))

    for ramp, entries in by_ramp.items():
        locked_count = max(0, int(locked_normal_ramp_counts.get(ramp, 0) or 0))
        candidate_variables = [variable for _placement, variable in entries]
        if not candidate_variables:
            continue
        max_total_count = locked_count + len(candidate_variables)
        ramp_count = model.NewIntVar(
            locked_count,
            max_total_count,
            f"ramp_count_{ramp}",
        )
        model.Add(ramp_count == sum(candidate_variables) + locked_count)

        for threshold in range(1, max_total_count):
            over_threshold = model.NewBoolVar(f"ramp_balance_{ramp}_over_{threshold}")
            model.Add(ramp_count >= threshold + 1).OnlyEnforceIf(over_threshold)
            model.Add(ramp_count <= threshold).OnlyEnforceIf(over_threshold.Not())
            terms.append(over_threshold * -(RAMP_BALANCE_PAIR_PENALTY * threshold))

        if preferred_max_per_ramp is None:
            continue
        preferred_max = max(0, int(preferred_max_per_ramp))
        max_possible_excess = max(0, locked_count + len(candidate_variables) - preferred_max)
        if max_possible_excess <= 0:
            continue
        excess = model.NewIntVar(0, max_possible_excess, f"preferred_max_excess_{ramp}")
        model.Add(excess >= ramp_count - preferred_max)
        terms.append(excess * -PREFERRED_MAX_PER_RAMP_PENALTY)

    return terms


def _ramp_side_balance_penalty_terms(
    model,
    variables,
    locked_normal_side_counts,
):
    terms = []
    by_ramp = {ramp: {"lower": [], "upper": []} for ramp in sorted(NORMAL_RAMP_CODES)}
    for placement, variable in variables.items():
        side = _normal_ramp_side(placement.position)
        if placement.ramp not in NORMAL_RAMP_CODES or not side:
            continue
        by_ramp.setdefault(placement.ramp, {"lower": [], "upper": []})[side].append(variable)

    for ramp, side_variables in by_ramp.items():
        lower_variables = side_variables.get("lower", [])
        upper_variables = side_variables.get("upper", [])
        if not lower_variables and not upper_variables:
            continue
        lower_locked = max(0, int((locked_normal_side_counts or {}).get((ramp, "lower"), 0) or 0))
        upper_locked = max(0, int((locked_normal_side_counts or {}).get((ramp, "upper"), 0) or 0))
        lower_count = model.NewIntVar(
            lower_locked,
            lower_locked + len(lower_variables),
            f"ramp_side_{ramp}_lower_count",
        )
        upper_count = model.NewIntVar(
            upper_locked,
            upper_locked + len(upper_variables),
            f"ramp_side_{ramp}_upper_count",
        )
        model.Add(lower_count == sum(lower_variables) + lower_locked)
        model.Add(upper_count == sum(upper_variables) + upper_locked)
        max_delta = lower_locked + upper_locked + len(lower_variables) + len(upper_variables)
        if max_delta < 3:
            continue
        total_count = model.NewIntVar(
            lower_locked + upper_locked,
            max_delta,
            f"ramp_side_{ramp}_total_count",
        )
        model.Add(total_count == lower_count + upper_count)
        delta = model.NewIntVar(-max_delta, max_delta, f"ramp_side_{ramp}_delta")
        imbalance = model.NewIntVar(0, max_delta, f"ramp_side_{ramp}_imbalance")
        model.Add(delta == lower_count - upper_count)
        model.AddAbsEquality(imbalance, delta)
        excess_imbalance = model.NewIntVar(0, max_delta, f"ramp_side_{ramp}_excess")
        model.Add(excess_imbalance >= imbalance - 1)
        if lower_locked + upper_locked >= 3:
            terms.append(excess_imbalance * -RAMP_SIDE_BALANCE_PENALTY)
            continue
        balance_applies = model.NewBoolVar(f"ramp_side_{ramp}_applies")
        model.Add(total_count >= 3).OnlyEnforceIf(balance_applies)
        model.Add(total_count <= 2).OnlyEnforceIf(balance_applies.Not())
        active_excess = model.NewIntVar(0, max_delta, f"ramp_side_{ramp}_active_excess")
        model.Add(active_excess <= excess_imbalance)
        model.Add(active_excess <= max_delta * balance_applies)
        model.Add(active_excess >= excess_imbalance - (max_delta * (1 - balance_applies)))
        terms.append(active_excess * -RAMP_SIDE_BALANCE_PENALTY)

    return terms


def _inbound_eta_spacing_penalty_terms(
    model,
    variables,
    assignments,
    tail_rows,
    threshold_minutes,
):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return [], _inbound_spacing_report(
            "disabled",
            "Inbound ETA spacing disabled because the threshold is 0 minutes.",
        )

    terms = []
    candidate_entries = [
        (placement, variable)
        for placement, variable in variables.items()
        if placement.ramp in NORMAL_RAMP_CODES and placement.eta
    ]
    locked_entries = _locked_inbound_entries(assignments, tail_rows)
    cluster_terms, cluster_report = _inbound_eta_cluster_penalty_terms(
        model,
        candidate_entries,
        threshold_minutes,
    )

    for placement, variable in candidate_entries:
        for locked in locked_entries:
            if not _inbound_entries_are_close(placement, locked, threshold_minutes):
                continue
            terms.append(variable * -_inbound_eta_penalty(placement.eta, locked["eta"], threshold_minutes))

    terms.extend(cluster_terms)
    if terms:
        detail = (
            f"Inbound ETA spacing applied as bounded same-ramp arrival clusters under {threshold_minutes} min."
        )
        if cluster_report.get("detail"):
            detail += f" {cluster_report['detail']}"
        return terms, _inbound_spacing_report("applied", detail)
    if cluster_report.get("status") == "skipped":
        return terms, cluster_report
    return terms, _inbound_spacing_report(
        "skipped",
        "Inbound ETA spacing skipped because no close same-ramp candidate arrival clusters were found.",
    )


def _inbound_eta_cluster_penalty_terms(model, candidate_entries, threshold_minutes):
    if not candidate_entries:
        return [], _inbound_spacing_report(
            "skipped",
            "Inbound ETA spacing skipped because no candidate normal-ramp arrival times were available.",
        )

    by_ramp = {}
    for index, (placement, variable) in enumerate(candidate_entries):
        by_ramp.setdefault(placement.ramp, []).append((index, placement, variable))

    pair_scan_count = sum(len(entries) * len(entries) for entries in by_ramp.values())
    if len(candidate_entries) > INBOUND_SPACING_MAX_CANDIDATE_ENTRIES:
        return [], _inbound_spacing_report(
            "skipped",
            (
                "Inbound ETA spacing skipped to keep optimizer memory bounded "
                f"({len(candidate_entries)} candidate arrival placements exceeds "
                f"{INBOUND_SPACING_MAX_CANDIDATE_ENTRIES})."
            ),
        )
    if pair_scan_count > INBOUND_SPACING_MAX_PAIR_SCAN_COUNT:
        return [], _inbound_spacing_report(
            "skipped",
            (
                "Inbound ETA spacing skipped to keep optimizer solve time bounded "
                f"({pair_scan_count} same-ramp pair scans exceeds "
                f"{INBOUND_SPACING_MAX_PAIR_SCAN_COUNT})."
            ),
        )

    clusters = []
    seen_clusters = set()
    literal_count = 0
    for ramp, entries in by_ramp.items():
        for _center_index, center_placement, _center_variable in entries:
            cluster = []
            for index, placement, _variable in entries:
                diff = _minutes_apart(center_placement.eta, placement.eta)
                if diff is not None and diff < threshold_minutes:
                    cluster.append(index)
            cluster = tuple(cluster)
            if cluster in seen_clusters:
                continue
            seen_clusters.add(cluster)
            cluster_entries = [
                entry for entry in entries if entry[0] in cluster
            ]
            if len({placement.tail for _index, placement, _variable in cluster_entries}) <= 1:
                continue
            clusters.append((ramp, cluster_entries))
            literal_count += len(cluster_entries)
            if (
                len(clusters) > INBOUND_SPACING_MAX_CLUSTER_COUNT
                or literal_count > INBOUND_SPACING_MAX_CLUSTER_LITERAL_COUNT
            ):
                return [], _inbound_spacing_report(
                    "skipped",
                    (
                        "Inbound ETA spacing skipped to keep optimizer solve time bounded "
                        f"({len(clusters)} clusters / {literal_count} placement references)."
                    ),
                )

    if not clusters:
        return [], _inbound_spacing_report(
            "skipped",
            "Inbound ETA spacing skipped because no close same-ramp candidate arrival clusters were found.",
        )

    terms = []
    for cluster_index, (ramp, cluster_entries) in enumerate(clusters):
        variables_in_cluster = [variable for _index, _placement, variable in cluster_entries]
        max_tail_count = len({placement.tail for _index, placement, _variable in cluster_entries})
        excess = model.NewIntVar(
            0,
            max(0, max_tail_count - 1),
            f"inbound_eta_cluster_{ramp}_{cluster_index}",
        )
        model.Add(excess >= sum(variables_in_cluster) - 1)
        terms.append(excess * -INBOUND_CLOSE_ETA_RAMP_PENALTY)

    return terms, _inbound_spacing_report(
        "applied",
        f"Built {len(clusters)} bounded ramp/time inbound ETA cluster penalties.",
    )


def _inbound_spacing_report(status, detail):
    return {"status": status, "detail": detail}


def _inbound_spacing_no_placement_report(threshold_minutes):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return _inbound_spacing_report(
            "disabled",
            "Inbound ETA spacing disabled because the threshold is 0 minutes.",
        )
    return _inbound_spacing_report(
        "skipped",
        "Inbound ETA spacing skipped because no candidate placements were available.",
    )


def _merge_inbound_spacing_reports(reports, threshold_minutes):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return _inbound_spacing_report(
            "disabled",
            "Inbound ETA spacing disabled because the threshold is 0 minutes.",
        )
    filtered = [report for report in reports if report]
    if not filtered:
        return _inbound_spacing_no_placement_report(threshold_minutes)
    for status in ("applied", "skipped", "disabled"):
        matching = [report for report in filtered if report.get("status") == status]
        if matching:
            details = " ".join(
                str(report.get("detail") or "").strip()
                for report in matching
                if str(report.get("detail") or "").strip()
            )
            return _inbound_spacing_report(status, details)
    return _inbound_spacing_report(
        "skipped",
        "Inbound ETA spacing skipped because no applicable arrival placements were available.",
    )


def _deice_spacing_penalty_terms(
    model,
    variables,
    assignments,
    tail_rows,
    timezone_name,
    threshold_minutes,
):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return [], _deice_report(
            "disabled",
            "Deice scoring disabled because the threshold is 0 minutes.",
        )

    terms = []
    candidate_entries = [
        (placement, variable)
        for placement, variable in variables.items()
        if _is_deice_position(placement.position) and placement.departure
    ]
    locked_entries = _locked_deice_entries(assignments, tail_rows, timezone_name)
    cluster_terms, cluster_report = _deice_cluster_penalty_terms(
        model,
        candidate_entries,
        threshold_minutes,
    )

    for index, (placement, variable) in enumerate(candidate_entries):
        for locked in locked_entries:
            if not _deice_entries_are_close(placement, locked, threshold_minutes):
                continue
            terms.append(variable * -_deice_penalty(placement.departure, locked["departure"], threshold_minutes))

    terms.extend(cluster_terms)
    if terms:
        detail = (
            f"Deice scoring applied as bounded same-ramp departure clusters under {threshold_minutes} min."
        )
        if cluster_report.get("detail"):
            detail += f" {cluster_report['detail']}"
        return terms, _deice_report("applied", detail)
    if cluster_report.get("status") == "skipped":
        return terms, cluster_report
    return terms, _deice_report(
        "skipped",
        "Deice scoring skipped because no candidate normal-ramp departure times were available.",
    )


def _deice_cluster_penalty_terms(model, candidate_entries, threshold_minutes):
    if not candidate_entries:
        return [], _deice_report(
            "skipped",
            "Deice scoring skipped because no candidate normal-ramp departure times were available.",
        )

    by_ramp = {}
    for index, (placement, variable) in enumerate(candidate_entries):
        by_ramp.setdefault(placement.ramp, []).append((index, placement, variable))

    pair_scan_count = sum(len(entries) * len(entries) for entries in by_ramp.values())
    if len(candidate_entries) > DEICE_MAX_CANDIDATE_ENTRIES:
        return [], _deice_report(
            "skipped",
            (
                "Deice scoring skipped to keep optimizer memory bounded "
                f"({len(candidate_entries)} candidate departure placements exceeds "
                f"{DEICE_MAX_CANDIDATE_ENTRIES})."
            ),
        )
    if pair_scan_count > DEICE_MAX_PAIR_SCAN_COUNT:
        return [], _deice_report(
            "skipped",
            (
                "Deice scoring skipped to keep optimizer solve time bounded "
                f"({pair_scan_count} same-ramp pair scans exceeds "
                f"{DEICE_MAX_PAIR_SCAN_COUNT})."
            ),
        )

    clusters = []
    seen_clusters = set()
    literal_count = 0
    for ramp, entries in by_ramp.items():
        for _center_index, center_placement, _center_variable in entries:
            cluster = []
            for index, placement, _variable in entries:
                diff = _minutes_apart(center_placement.departure, placement.departure)
                if diff is not None and diff < threshold_minutes:
                    cluster.append(index)
            cluster = tuple(cluster)
            if cluster in seen_clusters:
                continue
            seen_clusters.add(cluster)
            cluster_entries = [
                entry for entry in entries if entry[0] in cluster
            ]
            if len({placement.tail for _index, placement, _variable in cluster_entries}) <= 1:
                continue
            clusters.append((ramp, cluster_entries))
            literal_count += len(cluster_entries)
            if len(clusters) > DEICE_MAX_CLUSTER_COUNT or literal_count > DEICE_MAX_CLUSTER_LITERAL_COUNT:
                return [], _deice_report(
                    "skipped",
                    (
                        "Deice scoring skipped to keep optimizer solve time bounded "
                        f"({len(clusters)} clusters / {literal_count} placement references)."
                    ),
                )

    if not clusters:
        return [], _deice_report(
            "skipped",
            "Deice scoring skipped because no close same-ramp candidate departure clusters were found.",
        )

    terms = []
    for cluster_index, (ramp, cluster_entries) in enumerate(clusters):
        variables_in_cluster = [variable for _index, _placement, variable in cluster_entries]
        max_tail_count = len({placement.tail for _index, placement, _variable in cluster_entries})
        excess = model.NewIntVar(
            0,
            max(0, max_tail_count - 1),
            f"deice_cluster_{ramp}_{cluster_index}",
        )
        model.Add(excess >= sum(variables_in_cluster) - 1)
        terms.append(excess * -DEICE_CLOSE_PAIR_PENALTY)

    return terms, _deice_report(
        "applied",
        f"Built {len(clusters)} bounded ramp/time deice cluster penalties.",
    )


def _deice_report(status, detail):
    return {"status": status, "detail": detail}


def _deice_no_placement_report(threshold_minutes):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return _deice_report(
            "disabled",
            "Deice scoring disabled because the threshold is 0 minutes.",
        )
    return _deice_report(
        "skipped",
        "Deice scoring skipped because no candidate placements were available.",
    )


def _merge_deice_reports(reports, threshold_minutes):
    reports = [report for report in reports if report]
    if _safe_int(threshold_minutes, 0) <= 0:
        return _deice_report(
            "disabled",
            "Deice scoring disabled because the threshold is 0 minutes.",
        )
    if not reports:
        return _deice_no_placement_report(threshold_minutes)
    for status in ("applied", "skipped"):
        matching = [report for report in reports if report.get("status") == status]
        if matching:
            details = " ".join(
                str(report.get("detail") or "").strip()
                for report in matching
                if str(report.get("detail") or "").strip()
            )
            return _deice_report(status, details)
    return _deice_report(
        "disabled",
        "Deice scoring disabled because the threshold is 0 minutes.",
    )


def _selected_deice_reasons(
    selected_by_tail,
    assignments,
    tail_rows,
    timezone_name,
    threshold_minutes,
):
    threshold_minutes = _safe_int(threshold_minutes, 0)
    if threshold_minutes <= 0:
        return {}

    selected = list(selected_by_tail.values())
    locked = _locked_deice_entries(assignments, tail_rows, timezone_name)
    reasons = {placement.tail: [] for placement in selected}
    for placement in selected:
        if not _is_deice_position(placement.position) or not placement.departure:
            continue
        for other in selected:
            if other.tail == placement.tail:
                continue
            if not _deice_entries_are_close(placement, other, threshold_minutes):
                continue
            diff = _minutes_apart(placement.departure, other.departure)
            reasons[placement.tail].append(
                f"Deice warning: {placement.ramp} has {other.tail} within {diff} min."
            )
        for other in locked:
            if not _deice_entries_are_close(placement, other, threshold_minutes):
                continue
            diff = _minutes_apart(placement.departure, other["departure"])
            reasons[placement.tail].append(
                f"Deice warning: {placement.ramp} has locked {other['tail']} within {diff} min."
            )
    return {tail: tuple(values) for tail, values in reasons.items() if values}


def _locked_deice_entries(assignments, tail_rows, timezone_name):
    rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    entries = []
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        position = _normalize_position(assignment.position_code)
        if not _is_deice_position(position):
            continue
        row = rows_by_tail.get(tail, {})
        if row.get("suppress_departure_movement"):
            continue
        departure = _departure_time_for_deice(row, timezone_name)
        if not departure:
            continue
        entries.append(
            {
                "tail": tail,
                "ramp": _ramp_from_position(position),
                "position": position,
                "departure": departure,
            }
        )
    return entries


def _locked_inbound_entries(assignments, tail_rows):
    rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    entries = []
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        position = _normalize_position(assignment.position_code)
        ramp = _ramp_from_position(position)
        if ramp not in NORMAL_RAMP_CODES:
            continue
        row = rows_by_tail.get(tail, {})
        if row.get("suppress_arrival_movement"):
            continue
        eta = row.get("arrival_block_in_local")
        if not eta:
            continue
        entries.append(
            {
                "tail": tail,
                "ramp": ramp,
                "position": position,
                "eta": eta,
            }
        )
    return entries


def _deice_entries_are_close(first, second, threshold_minutes):
    first_ramp = first.ramp if isinstance(first, ParkingPlacement) else first["ramp"]
    second_ramp = second.ramp if isinstance(second, ParkingPlacement) else second["ramp"]
    if first_ramp != second_ramp:
        return False
    first_departure = first.departure if isinstance(first, ParkingPlacement) else first["departure"]
    second_departure = second.departure if isinstance(second, ParkingPlacement) else second["departure"]
    diff = _minutes_apart(first_departure, second_departure)
    return diff is not None and diff < threshold_minutes


def _inbound_entries_are_close(first, second, threshold_minutes):
    first_ramp = first.ramp if isinstance(first, ParkingPlacement) else first["ramp"]
    second_ramp = second.ramp if isinstance(second, ParkingPlacement) else second["ramp"]
    if first_ramp != second_ramp:
        return False
    first_eta = first.eta if isinstance(first, ParkingPlacement) else first["eta"]
    second_eta = second.eta if isinstance(second, ParkingPlacement) else second["eta"]
    diff = _minutes_apart(first_eta, second_eta)
    return diff is not None and diff < threshold_minutes


def _inbound_eta_penalty(first_eta, second_eta, threshold_minutes):
    diff = _minutes_apart(first_eta, second_eta)
    if diff is None:
        return 0
    return INBOUND_CLOSE_ETA_RAMP_PENALTY + max(0, threshold_minutes - diff)


def _deice_penalty(first_departure, second_departure, threshold_minutes):
    diff = _minutes_apart(first_departure, second_departure)
    if diff is None:
        return 0
    return DEICE_CLOSE_PAIR_PENALTY + max(0, threshold_minutes - diff)


def _minutes_apart(first, second):
    if not first or not second:
        return None
    minutes = int(abs((first - second).total_seconds()) // 60)
    return min(minutes, abs(1440 - minutes)) if minutes > 720 else minutes


def _solver_diagnostic(status_name, suggestions, candidate_rows):
    if status_name == "GUARDED" and candidate_rows:
        return (
            "Optimizer model guard stopped the solver before it could exceed safe memory bounds. "
            "Existing assignments were preserved; review candidate diagnostics or reduce the open candidate set."
        )
    if status_name == "ERROR" and candidate_rows:
        return (
            "Optimizer failed before proving a parking plan. Existing assignments were preserved."
        )
    if status_name != "UNKNOWN" or suggestions or not candidate_rows:
        return ""
    return (
        "Optimizer solver returned UNKNOWN before proving a parking plan. "
        "This is a solver/model time diagnostic, not proof that no parking is possible."
    )


def _position_sample(positions, limit=8):
    values = sorted(positions)
    if not values:
        return ""
    sample = ", ".join(values[:limit])
    if len(values) > limit:
        sample += f", +{len(values) - limit} more"
    return sample


def _top_rejection_reason(rejection_counts):
    if not rejection_counts:
        return ""
    return sorted(
        rejection_counts.items(),
        key=lambda item: (0 if str(item[0]).startswith("Slot ") else 1, -item[1], item[0]),
    )[0][0]


def _is_deice_position(position):
    ramp = _ramp_from_position(position)
    number = _position_number(position)
    return ramp in NORMAL_RAMP_CODES and number is not None and 1 <= number <= 8


def _departure_time_for_deice(row, timezone_name):
    departure = row.get("departure")
    if departure and getattr(departure, "actual_block_out_datetime_utc", None):
        return _utc_to_local(departure.actual_block_out_datetime_utc, timezone_name)
    if row.get("departure_datetime_local"):
        return row.get("departure_datetime_local")
    for field_name in (
        "mix_pull_time_local",
        "pure_pull_time_local",
    ):
        pull_time = getattr(departure, field_name, None) if departure else None
        if pull_time:
            return _datetime_from_local_time(row, pull_time)
    return None


def _utc_to_local(value, timezone_name):
    if not value:
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("America/Chicago")
    if value.tzinfo:
        return value.astimezone(zone).replace(tzinfo=None)
    return value.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)


def _datetime_from_local_time(row, value):
    base = row.get("departure_datetime_local") or row.get("arrival_block_in_local")
    if not base:
        return None
    candidate = datetime.combine(base.date(), value)
    if row.get("arrival_block_in_local") and candidate < row["arrival_block_in_local"]:
        candidate += timedelta(days=1)
    return candidate


def _rules_for_row(row, aircraft_type, rules):
    origin = _normalize_subject(row.get("arrival_origin"))
    aircraft_type = _normalize_aircraft_type(aircraft_type)
    arrival_key = _schedule_rule_key_for_row(row, "arrival_plan")
    departure_key = _schedule_rule_key_for_row(row, "departure_plan")
    matching = []
    for rule in rules:
        subject_type = str(rule.subject_type or "").strip().lower()
        subject = _normalize_subject(rule.subject_value)
        if subject_type == "origin" and subject == origin:
            matching.append(rule)
        elif subject_type == "aircraft_type" and subject == aircraft_type:
            matching.append(rule)
        elif subject_type == "arrival_plan" and str(rule.subject_value or "").strip().upper() == arrival_key:
            matching.append(rule)
        elif subject_type == "departure_plan" and str(rule.subject_value or "").strip().upper() == departure_key:
            matching.append(rule)
    return matching


def _schedule_rule_key_for_row(row, subject_type):
    mission_type = "arrival" if subject_type == "arrival_plan" else "departure"
    mission = row.get(mission_type)
    if not mission:
        return ""
    station = getattr(mission, "origin", "") if mission_type == "arrival" else getattr(mission, "destination", "")
    return parking_schedule_rule_key(
        mission_type,
        getattr(mission, "flight_number", ""),
        station,
    )


def _blocked_position_counts_by_ramp(rules):
    counts = {}
    for rule in _blocked_position_rules(rules.get("forbidden", [])):
        position = normalize_parking_position_code(rule.subject_value)
        ramp = _ramp_from_position(position)
        if ramp in NORMAL_RAMP_CODES:
            counts[ramp] = counts.get(ramp, 0) + 1
    return counts


def _blocked_position_rules(rules):
    return [
        rule
        for rule in rules
        if str(rule.subject_type or "").strip().lower() == "position"
    ]


def _rule_matches_position(rule, position, ramp):
    subject_type = str(rule.subject_type or "").strip().lower()
    if subject_type == "position":
        return normalize_parking_position_code(rule.subject_value) == _normalize_position(position)
    rule_ramp = str(rule.ramp_code or "").strip().upper()
    rule_position = normalize_parking_position_code(rule_ramp)
    if rule_position:
        return rule_position == _normalize_position(position)
    if rule_ramp == "THROAT":
        return _position_number(position) in (9, 10)
    return rule_ramp == ramp


def _candidate_footprint_positions(aircraft_type, position, configurable_rule_flags=None):
    position = _normalize_position(position)
    if aircraft_type != "767":
        return (position,)
    ramp = _ramp_from_position(position)
    number = _position_number(position)
    if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES or number is None:
        return (position,)
    blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
    if blocked_number:
        return (position, f"{ramp}{blocked_number:02d}")
    if _configurable_rule_enabled(configurable_rule_flags, "force_767_to_position_4_8"):
        if number == 4:
            return (f"{ramp}03", position)
        if number == 8:
            return (f"{ramp}07", position)
    return ()


def _placement_allows_aircraft(
    aircraft_type,
    position,
    locked_filled_positions,
    configurable_rule_flags=None,
):
    number = _position_number(position)
    ramp = _ramp_from_position(position)
    if aircraft_type != "767":
        return True
    if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES or number in (9, 10):
        return True
    footprint = _candidate_footprint_positions(
        aircraft_type,
        position,
        configurable_rule_flags,
    )
    if len(footprint) != 2:
        return False
    return all(item == _normalize_position(position) or item not in locked_filled_positions for item in footprint)


def _configurable_rule_blocks_candidate(
    aircraft_type,
    position,
    footprint_positions,
    locked_filled_positions,
    locked_a300_positions,
    locked_767_positions,
    configurable_rule_flags,
):
    if (
        aircraft_type == "A300"
        and _configurable_rule_enabled(configurable_rule_flags, "prevent_a300_in_position_5")
        and _position_number(position) == 5
    ):
        return True
    if aircraft_type == "767":
        if not footprint_positions:
            return True
        if _configurable_rule_enabled(configurable_rule_flags, "force_767_to_position_4_8"):
            ramp = _ramp_from_position(position)
            number = _position_number(position)
            lower_pair = (1, 2) if number == 3 else (5, 6) if number == 7 else ()
            if lower_pair and all(
                f"{ramp}{item:02d}" in (locked_filled_positions or set())
                for item in lower_pair
            ):
                return True
        if _configurable_rule_enabled(configurable_rule_flags, "prevent_767_adjacent_to_a300"):
            return any(
                _positions_are_directly_adjacent(position, a300_position)
                for a300_position in (locked_a300_positions or set())
            )
    elif (
        aircraft_type == "A300"
        and _configurable_rule_enabled(configurable_rule_flags, "prevent_767_adjacent_to_a300")
    ):
        return any(
            _positions_are_directly_adjacent(locked_767_position, position)
            for locked_767_position in (locked_767_positions or ())
        )
    return False


def _configurable_rule_block_reason(
    aircraft_type,
    position,
    footprint_positions,
    locked_filled_positions,
    locked_a300_positions,
    locked_767_positions,
    configurable_rule_flags,
):
    if aircraft_type == "A300" and _position_number(position) == 5:
        return f"A300 cannot use {position} while the Position 5 restriction is enabled."
    if aircraft_type == "767":
        ramp = _ramp_from_position(position)
        number = _position_number(position)
        lower_pair = (1, 2) if number == 3 else (5, 6) if number == 7 else ()
        if lower_pair and all(
            f"{ramp}{item:02d}" in (locked_filled_positions or set())
            for item in lower_pair
        ):
            return (
                f"767 cannot use {position} while {ramp}{lower_pair[0]:02d} and "
                f"{ramp}{lower_pair[1]:02d} are occupied."
            )
        for a300_position in locked_a300_positions or ():
            if _positions_are_directly_adjacent(position, a300_position):
                return f"767 cannot be adjacent to A300 at {a300_position}."
    else:
        for locked_767_position in locked_767_positions or ():
            if _positions_are_directly_adjacent(locked_767_position, position):
                return f"A300 cannot be adjacent to an existing 767 at {locked_767_position}."
    return "Blocked by configurable hard parking rules."


def _positions_are_directly_adjacent(first_position, second_position):
    first_position = _normalize_position(first_position)
    second_position = _normalize_position(second_position)
    first_number = _position_number(first_position)
    second_number = _position_number(second_position)
    return (
        first_number is not None
        and second_number is not None
        and _ramp_from_position(first_position) == _ramp_from_position(second_position)
        and abs(first_number - second_number) == 1
    )


def _configurable_rule_enabled(configurable_rule_flags, key):
    value = (configurable_rule_flags or {}).get(key)
    return True if value is None else bool(value)


def _locked_blocked_positions(assignments, tail_rows, configurable_rule_flags=None):
    aircraft_type_by_tail = {
        _normalize_tail(row.get("tail")): _parking_aircraft_type_for_row(row)
        for row in tail_rows
    }
    occupancy = {
        _normalize_position(assignment.position_code): assignment
        for assignment in assignments
        if _normalize_position(assignment.position_code)
    }
    blocked = set()
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        position = _normalize_position(assignment.position_code)
        if aircraft_type_by_tail.get(tail) != "767":
            continue
        footprint = parking_767_footprint_positions(
            position,
            occupancy,
            configurable_rule_flags,
        )
        blocked.update(item for item in footprint if item != position)
    return blocked


def _locked_configurable_positions(assignments, tail_rows, configurable_rule_flags):
    rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    occupancy = {
        _normalize_position(assignment.position_code): assignment
        for assignment in assignments
        if _normalize_position(assignment.position_code)
    }
    a300_positions = set()
    positions = set()
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        position = _normalize_position(assignment.position_code)
        aircraft_type = _parking_aircraft_type_for_row(rows_by_tail.get(tail, {}))
        if aircraft_type == "A300":
            a300_positions.add(position)
        elif aircraft_type == "767":
            if parking_767_footprint_positions(position, occupancy, configurable_rule_flags):
                positions.add(position)
    return a300_positions, positions


def _a300_positions_for_selected(placements):
    return {
        placement.position
        for placement in placements
        if placement.aircraft_type == "A300"
    }


def _767_positions_for_selected(placements):
    return {
        placement.position
        for placement in placements
        if placement.aircraft_type == "767"
    }


def _locked_eta_by_position(assignments, tail_rows, configurable_rule_flags=None):
    rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    locked_eta = {}
    occupancy = {
        _normalize_position(assignment.position_code): assignment
        for assignment in assignments
        if _normalize_position(assignment.position_code)
    }
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        row = rows_by_tail.get(tail, {})
        if row.get("suppress_arrival_movement"):
            continue
        eta = row.get("arrival_block_in_local")
        position = _normalize_position(assignment.position_code)
        if not eta or not position:
            continue
        locked_eta.setdefault(position, []).append(eta)

        aircraft_type = _parking_aircraft_type_for_row(row)
        if aircraft_type != "767":
            continue
        footprint = parking_767_footprint_positions(
            position,
            occupancy,
            configurable_rule_flags,
        )
        for footprint_position in footprint:
            if footprint_position != position:
                locked_eta.setdefault(footprint_position, []).append(eta)
    return locked_eta


def _filled_positions_for_selected(placements):
    filled = set()
    for placement in placements:
        filled.update(_filled_positions_for_placement(placement))
    return filled


def _blocked_positions_for_selected(placements):
    blocked = set()
    for placement in placements:
        blocked_position = _blocked_position_for_placement(placement)
        if blocked_position:
            blocked.add(blocked_position)
    return blocked


def _eta_by_position_for_placements(placements):
    eta_by_position = {}
    for placement in placements:
        if not placement.eta:
            continue
        for position in _filled_positions_for_placement(placement):
            eta_by_position.setdefault(position, []).append(placement.eta)
    return eta_by_position


def _merge_eta_by_position(*sources):
    merged = {}
    for source in sources:
        for position, values in (source or {}).items():
            merged.setdefault(position, []).extend(values)
    return merged


def _normal_ramp_counts_for_assignments(assignments):
    counts = {}
    for assignment in assignments:
        position = _normalize_position(assignment.position_code)
        ramp = _ramp_from_position(position)
        if ramp not in NORMAL_RAMP_CODES:
            continue
        counts[ramp] = counts.get(ramp, 0) + 1
    return counts


def _normal_side_counts_for_assignments(assignments):
    counts = {}
    for assignment in assignments:
        position = _normalize_position(assignment.position_code)
        ramp = _ramp_from_position(position)
        side = _normal_ramp_side(position)
        if ramp not in NORMAL_RAMP_CODES or not side:
            continue
        key = (ramp, side)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _normal_ramp_counts_for_placements(placements):
    counts = {}
    for placement in placements:
        if placement.ramp not in NORMAL_RAMP_CODES:
            continue
        counts[placement.ramp] = counts.get(placement.ramp, 0) + 1
    return counts


def _normal_side_counts_for_placements(placements):
    counts = {}
    for placement in placements:
        side = _normal_ramp_side(placement.position)
        if placement.ramp not in NORMAL_RAMP_CODES or not side:
            continue
        key = (placement.ramp, side)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _merge_ramp_counts(*sources):
    merged = {}
    for source in sources:
        for ramp, count in (source or {}).items():
            merged[ramp] = merged.get(ramp, 0) + max(0, int(count or 0))
    return merged


def _merge_side_counts(*sources):
    merged = {}
    for source in sources:
        for key, count in (source or {}).items():
            merged[key] = merged.get(key, 0) + max(0, int(count or 0))
    return merged


def _normal_ramp_side(position):
    number = _position_number(position)
    if number in (1, 2, 3, 4):
        return "lower"
    if number in (5, 6, 7, 8):
        return "upper"
    return ""


def _slot_1_timing_by_position(assignments, tail_rows, selected_placements, rows_by_tail, timezone_name):
    rows_by_tail = rows_by_tail or {}
    timing = {}
    locked_rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    for assignment in assignments:
        if int(assignment.lane_number or 0) != 1:
            continue
        position = _normalize_position(assignment.position_code)
        tail = _normalize_tail(assignment.tail_number)
        if not position or not tail:
            continue
        row = locked_rows_by_tail.get(tail, {})
        timing[position] = {
            "tail": tail,
            "arrival": row.get("arrival_block_in_local"),
            "departure": _parking_window_end_for_row(row, timezone_name),
        }

    for placement in selected_placements:
        if placement.lane != 1:
            continue
        row = rows_by_tail.get(placement.tail, {})
        timing[placement.position] = {
            "tail": placement.tail,
            "arrival": placement.eta,
            "departure": _parking_window_end_for_row(row, timezone_name) or placement.departure,
        }
    return timing


def _parking_window_end_for_row(row, timezone_name):
    if not row:
        return None
    return _departure_time_for_deice(row, timezone_name) or row.get("departure_datetime_local")


def _slot_2_timing_allows(row, position, slot_1_timing_by_position):
    slot_1 = (slot_1_timing_by_position or {}).get(position)
    if not slot_1:
        return False, "Slot 2 cannot be used because Slot 1 is empty."
    slot_1_departure = slot_1.get("departure")
    if not slot_1_departure:
        return False, "Slot 1 departure time unknown."
    slot_2_arrival = row.get("arrival_block_in_local")
    if not slot_2_arrival:
        return False, "Slot 2 arrival time unknown."
    if slot_1_departure > slot_2_arrival:
        return False, "Slot 2 timing conflict."
    return True, ""


def _blocked_position_for_placement(placement):
    if len(placement.footprint_positions) == 2:
        return next(
            item
            for item in placement.footprint_positions
            if item != placement.position
        )
    return _blocked_position_for_values(placement.aircraft_type, placement.position)


def _blocked_position_for_values(aircraft_type, position):
    if aircraft_type != "767":
        return ""
    ramp = _ramp_from_position(position)
    number = _position_number(position)
    if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES:
        return ""
    blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
    return f"{ramp}{blocked_number:02d}" if blocked_number else ""


def _filled_positions_for_placement(placement):
    if placement.footprint_positions:
        return list(placement.footprint_positions)
    positions = [placement.position]
    blocked = _blocked_position_for_placement(placement)
    if blocked:
        positions.append(blocked)
    return positions


def _eta_order_sequences():
    sequences = []
    for ramp in NORMAL_RAMP_CODES:
        sequences.extend(
            [f"{ramp}{number:02d}" for number in bank]
            for bank in NORMAL_BANKS
        )
        sequences.append([f"{ramp}10", f"{ramp}09"])
    sequences.append(list(REMOTE_ORDER))
    return sequences


def _eta_before(first, second):
    return bool(first and second and first < second)


def _eta_order_conflicts_from_preview(preview):
    return [
        conflict
        for conflict in (preview.get("conflicts") or [])
        if str(conflict.get("reason") or "").startswith(
            ("normal_bank_eta_order", "remote_eta_order", "throat_eta_order")
        )
    ]


def _fill_expr(fill_exprs, position):
    return fill_exprs.get(position, 0)


def _fill_order_expr(fill_exprs, position, fill_order_satisfied_positions):
    return 1 if position in fill_order_satisfied_positions else _fill_expr(fill_exprs, position)


def _is_constant_one(value):
    return isinstance(value, int) and value == 1


def _is_constant_zero(value):
    return isinstance(value, int) and value == 0


def _parking_window_label(row):
    arrival = row.get("arrival_time") or "-"
    departure = row.get("departure_time") or "-"
    return f"{arrival} -> {departure}"


def _parking_window_sort_key(row):
    return (
        str(row.get("arrival_time") or ""),
        str(row.get("departure_time") or ""),
        _normalize_tail(row.get("tail")),
    )


def _var_key(value):
    return "".join(character if character.isalnum() else "_" for character in str(value or ""))


def _normalize_tail(value):
    return str(value or "").strip().upper()


def _normalize_position(value):
    return str(value or "").strip().upper()


def _normalize_subject(value):
    return "".join(character for character in str(value or "").strip().upper() if character.isalnum())


def _normalize_aircraft_type(value):
    normalized = normalize_parking_aircraft_type(value, allow_unknown=True)
    return normalized or str(value or "").strip().upper()


def _parking_aircraft_type_for_row(row):
    tail = row.get("tail") if row else ""
    return resolve_parking_aircraft_type_from_tail(tail)


def _unresolved_reason(
    row,
    aircraft_type,
    has_candidate,
    solver_status,
    include_remote,
    include_throat,
    locked_conflicts,
    hard_rules,
    diagnostics=None,
):
    diagnostics = diagnostics or {}
    if has_candidate:
        if solver_status == "GUARDED":
            return (
                "Optimizer model guard skipped solving for this tail; "
                "candidate positions remain after hard filters."
            )
        if solver_status == "ERROR":
            return (
                "Optimizer solver failed before proving a plan for this tail; "
                "existing assignments were preserved."
            )
        if solver_status == "UNKNOWN":
            return (
                "Solver returned UNKNOWN before proving a plan for this tail; "
                "candidate positions remain after hard filters."
            )
        return (
            "No selected assignment after hard rules and ETA-order optimization; "
            "other tails used the available feasible positions."
        )

    reasons = []
    if locked_conflicts:
        reasons.append("Current Parking Plan conflicts must be resolved first.")
    if aircraft_type == UNKNOWN_PARKING_AIRCRAFT_TYPE:
        reasons.append("Unknown aircraft type.")

    matching_required = _rules_for_row(row, aircraft_type, hard_rules.get("required", []))
    if matching_required:
        required_ramps = {_rule_ramp_label(rule) for rule in matching_required}
        if not include_remote and "Remote" in required_ramps:
            reasons.append("Remote disabled.")
        if not include_throat and "9/10 throat parking" in required_ramps:
            reasons.append("9/10 throat parking disabled.")

    matching_forbidden = _rules_for_row(row, aircraft_type, hard_rules.get("forbidden", []))
    if any(str(rule.subject_type or "").strip().lower() == "aircraft_type" for rule in matching_forbidden):
        reasons.append("Aircraft type restricted from available ramps.")
    if any(str(rule.subject_type or "").strip().lower() == "origin" for rule in matching_forbidden):
        reasons.append("Origin restricted from available ramps.")
    if aircraft_type == "767":
        reasons.append("767 footprint cannot fit in available slots.")
    top_rejection = diagnostics.get("top_rejection_reason")
    if top_rejection:
        reasons.append(top_rejection)
    if not include_remote:
        reasons.append("Remote disabled.")
    if not include_throat:
        reasons.append("9/10 throat parking disabled.")
    if not reasons:
        reasons.append("No valid parking position found.")
    return " ".join(dict.fromkeys(reasons))


def _rule_ramp_label(rule):
    ramp = str(rule.ramp_code or "").strip().upper()
    position = normalize_parking_position_code(ramp)
    if position:
        if position.startswith("R"):
            return "Remote"
        if _position_number(position) in (9, 10):
            return "9/10 throat parking"
        return position[:1]
    if ramp == "R":
        return "Remote"
    if ramp == "THROAT":
        return "9/10 throat parking"
    return ramp


def _rule_target_label(rule):
    target = str(rule.ramp_code or "").strip().upper()
    position = normalize_parking_position_code(target)
    if position:
        return f"position {position}"
    if target == "THROAT":
        return "9/10 throat parking"
    return f"ramp {target}"


def _ramp_from_position(position):
    position = _normalize_position(position)
    return "R" if position.startswith("R") else position[:1]


def _position_number(position):
    try:
        return int(str(position or "")[1:])
    except (TypeError, ValueError):
        return None


def _normalize_lane(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
