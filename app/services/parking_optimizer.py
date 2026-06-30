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
    BLOCKED_PARKING_POSITION,
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
    DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
    active_blocked_parking_positions,
    normalize_parking_position_code,
)


logger = logging.getLogger(__name__)

PARKING_OPTIMIZER_TIME_LIMIT_SECONDS = 8.0
PARKING_OPTIMIZER_MAX_STAGE_PLACEMENTS = 3500
PARKING_OPTIMIZER_MAX_STAGE_ETA_RELATIONS = 300000
SUCCESS_SOLVER_STATUSES = {"OPTIMAL", "FEASIBLE"}
PREFERRED_RAMP_SCORE = 600
AVOID_RAMP_PENALTY = 350
DEICE_CLOSE_PAIR_PENALTY = 400
DEICE_MAX_CLUSTER_COUNT = 240
DEICE_MAX_CLUSTER_LITERAL_COUNT = 20000
PARKING_WINDOW_PRIORITY_SCORE = 2000
RAMP_BALANCE_PAIR_PENALTY = 260
PREFERRED_MAX_PER_RAMP_PENALTY = 1200
FOUR_EIGHT_AVOID_PENALTY = 280
FOUR_EIGHT_757_PREFERENCE_SCORE = 420
FOUR_EIGHT_BLOCKED_POSITION_RELIEF = 110


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
    preferred_max_per_ramp = _safe_optional_int(defaults.get("preferred_max_per_ramp"))
    tail_rows = tail_rows if tail_rows is not None else tail_rows_for_operation(gateway, operation)
    rules = _active_rule_sets(gateway)
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
    locked_normal_ramp_counts = _normal_ramp_counts_for_assignments(assignments)
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
    locked_blocked_positions = _locked_blocked_positions(assignments, tail_rows)
    locked_eta_by_position = _locked_eta_by_position(assignments, tail_rows)
    slot_1_placements, slot_1_diagnostics = _build_candidate_placements(
        candidate_rows,
        candidate_positions,
        locked_lane_keys,
        locked_filled_positions,
        locked_blocked_positions,
        rules["hard"],
        rules["soft"],
        timezone_name,
        allowed_lanes=(1,),
    )

    slot_1_result = (
        _solve_optimizer_stage(
            slot_1_placements,
            assignments,
            tail_rows,
            timezone_name,
            deice_threshold,
            preferred_max_per_ramp,
            locked_filled_positions | locked_blocked_positions,
            locked_eta_by_position,
            locked_normal_ramp_counts,
            stage_name="slot_1",
        )
        if slot_1_placements
        else {
            "status": None,
            "status_name": "OPTIMAL",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_no_placement_report(deice_threshold),
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
            stage_eta_by_position = _merge_eta_by_position(
                locked_eta_by_position,
                _eta_by_position_for_placements(selected_by_tail.values()),
            )
            stage_normal_ramp_counts = _merge_ramp_counts(
                locked_normal_ramp_counts,
                _normal_ramp_counts_for_placements(selected_by_tail.values()),
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
                stage_filled_positions,
                stage_blocked_positions,
                rules["hard"],
                rules["soft"],
                timezone_name,
                allowed_lanes=(2,),
                slot_1_timing_by_position=slot_1_timing,
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
                    preferred_max_per_ramp,
                    stage_filled_positions,
                    stage_eta_by_position,
                    stage_normal_ramp_counts,
                    stage_name="slot_2",
                )
                wall_time += slot_2_result["wall_time"]
                deice_reports.append(slot_2_result.get("deice_report"))
                model_diagnostics.extend(slot_2_result.get("model_diagnostics") or [])
                if slot_2_result["status_name"] in SUCCESS_SOLVER_STATUSES:
                    selected_by_tail.update(slot_2_result["selected_by_tail"])
                else:
                    status_name = slot_2_result["status_name"]

    deice_report = _merge_deice_reports(deice_reports, deice_threshold)

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
                preferred_max_per_ramp,
                deice_report.get("status"),
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
        preferred_max_per_ramp,
        deice_report,
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
        preferred_max_per_ramp,
        _deice_report("skipped", "Deice scoring skipped because optimizer preview failed."),
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
    preferred_max_per_ramp,
    deice_report,
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
        locked.append(
            {
                "tail": tail,
                "position": _normalize_position(assignment.position_code),
                "lane": int(assignment.lane_number or 1),
                "label": f"{_normalize_position(assignment.position_code)} Slot {int(assignment.lane_number or 1)}",
                "origin": row.get("arrival_origin") or "-",
                "aircraft_type": _parking_aircraft_type_for_row(row),
                "parking_window": _parking_window_label(row),
                "reason": "Existing manual assignment preserved.",
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
    timezone_name,
    allowed_lanes=(1, 2),
    slot_1_timing_by_position=None,
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
            if position in locked_blocked_positions:
                rejection_reason = "Blocked by locked/manual assignments."
            elif _rule_blocks_row(row, aircraft_type, position, ramp, hard_rules):
                rejection_reason = _rule_block_reason(row, aircraft_type, position, ramp, hard_rules)
            elif not _placement_allows_aircraft(aircraft_type, position, locked_filled_positions):
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
                        soft_score=soft_score + slot_policy_score + parking_window_priority,
                        preference_reasons=tuple(preference_reasons + slot_policy_reasons),
                    )
                )
        diagnostics[tail] = {
            "candidate_positions_before_filters": len(before_positions),
            "candidate_positions_after_hard_filters": len(after_positions),
            "candidate_positions_before_sample": _position_sample(before_positions),
            "candidate_positions_after_sample": _position_sample(after_positions),
            "top_rejection_reason": _top_rejection_reason(rejection_counts),
        }
    return placements, diagnostics


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
):
    return {
        "stage": stage,
        "placement_count": placement_count,
        "eta_relation_count": eta_relation_count,
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


def _solve_optimizer_stage(
    placements,
    assignments,
    tail_rows,
    timezone_name,
    deice_threshold,
    preferred_max_per_ramp,
    filled_positions,
    eta_by_position,
    locked_normal_ramp_counts=None,
    stage_name="stage",
):
    if not placements:
        return {
            "status": None,
            "status_name": "NO_CANDIDATES",
            "selected_by_tail": {},
            "wall_time": 0,
            "deice_report": _deice_no_placement_report(deice_threshold),
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
            "model_diagnostics": [
                _stage_model_diagnostic(
                    stage_name,
                    len(placements),
                    eta_relation_count,
                    "GUARDED",
                    guard_reason,
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
        _add_fill_order_constraints(model, variables, fill_exprs)
        _add_throat_constraints(model, variables, fill_exprs)
        _add_767_block_constraints(model, variables, fill_exprs)
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
        objective_terms.extend(
            _ramp_balance_penalty_terms(
                model,
                variables,
                locked_normal_ramp_counts or {},
                preferred_max_per_ramp,
            )
        )
        model.Maximize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = PARKING_OPTIMIZER_TIME_LIMIT_SECONDS
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
            "model_diagnostics": [
                _stage_model_diagnostic(
                    stage_name,
                    len(placements),
                    eta_relation_count,
                    "ERROR",
                    detail,
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


def _add_fill_order_constraints(model, variables, fill_exprs):
    for placement, variable in variables.items():
        number = _position_number(placement.position)
        if placement.ramp in NORMAL_RAMP_CODES:
            for bank in NORMAL_BANKS:
                if number not in bank:
                    continue
                for lower in bank:
                    if lower >= number:
                        continue
                    model.Add(variable <= _fill_expr(fill_exprs, f"{placement.ramp}{lower:02d}"))
        elif placement.ramp == "R":
            try:
                index = REMOTE_ORDER.index(placement.position)
            except ValueError:
                continue
            for lower_position in REMOTE_ORDER[:index]:
                model.Add(variable <= _fill_expr(fill_exprs, lower_position))


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
    preferred_max_per_ramp=None,
    deice_status="disabled",
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
    }


def _rule_blocks_row(row, aircraft_type, position, ramp, rules):
    if any(
        _rule_matches_position(rule, position, ramp)
        for rule in _blocked_position_rules(rules.get("forbidden", []))
    ):
        return True
    required_rules = _rules_for_row(row, aircraft_type, rules.get("required", []))
    if required_rules and not any(_rule_matches_position(rule, position, ramp) for rule in required_rules):
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
    if required_rules and not any(_rule_matches_position(rule, position, ramp) for rule in required_rules):
        if any(str(rule.subject_type or "").strip().lower() == "aircraft_type" for rule in required_rules):
            return "Aircraft type required on another ramp."
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


def _soft_rule_reason(rule, verb):
    subject_type = str(rule.subject_type or "").strip().lower()
    subject = _normalize_subject(rule.subject_value)
    ramp = str(rule.ramp_code or "").strip().upper()
    ramp_label = "9/10 throat" if ramp == "THROAT" else f"ramp {ramp}"
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

    clusters = []
    seen_clusters = set()
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

    literal_count = sum(len(cluster_entries) for _ramp, cluster_entries in clusters)
    if not clusters:
        return [], _deice_report(
            "skipped",
            "Deice scoring skipped because no close same-ramp candidate departure clusters were found.",
        )
    if len(clusters) > DEICE_MAX_CLUSTER_COUNT or literal_count > DEICE_MAX_CLUSTER_LITERAL_COUNT:
        return [], _deice_report(
            "skipped",
            (
                "Deice scoring skipped to keep optimizer solve time bounded "
                f"({len(clusters)} clusters / {literal_count} placement references)."
            ),
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


def _deice_entries_are_close(first, second, threshold_minutes):
    first_ramp = first.ramp if isinstance(first, ParkingPlacement) else first["ramp"]
    second_ramp = second.ramp if isinstance(second, ParkingPlacement) else second["ramp"]
    if first_ramp != second_ramp:
        return False
    first_departure = first.departure if isinstance(first, ParkingPlacement) else first["departure"]
    second_departure = second.departure if isinstance(second, ParkingPlacement) else second["departure"]
    diff = _minutes_apart(first_departure, second_departure)
    return diff is not None and diff < threshold_minutes


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
        "final_mix_pull_time_local",
        "first_mix_pull_time_local",
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
    matching = []
    for rule in rules:
        subject_type = str(rule.subject_type or "").strip().lower()
        subject = _normalize_subject(rule.subject_value)
        if subject_type == "origin" and subject == origin:
            matching.append(rule)
        elif subject_type == "aircraft_type" and subject == aircraft_type:
            matching.append(rule)
    return matching


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
    if rule_ramp == "THROAT":
        return _position_number(position) in (9, 10)
    return rule_ramp == ramp


def _placement_allows_aircraft(aircraft_type, position, locked_filled_positions):
    number = _position_number(position)
    ramp = _ramp_from_position(position)
    if aircraft_type != "767":
        return True
    if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES or number in (9, 10):
        return True
    blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
    if not blocked_number:
        return False
    return f"{ramp}{blocked_number:02d}" not in locked_filled_positions


def _locked_blocked_positions(assignments, tail_rows):
    aircraft_type_by_tail = {
        _normalize_tail(row.get("tail")): resolve_parking_aircraft_type_from_tail(row.get("tail"))
        for row in tail_rows
    }
    blocked = set()
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        position = _normalize_position(assignment.position_code)
        ramp = _ramp_from_position(position)
        number = _position_number(position)
        if aircraft_type_by_tail.get(tail) != "767":
            continue
        if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES:
            continue
        blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
        if blocked_number:
            blocked.add(f"{ramp}{blocked_number:02d}")
    return blocked


def _locked_eta_by_position(assignments, tail_rows):
    rows_by_tail = {
        _normalize_tail(row.get("tail")): row
        for row in tail_rows
        if _normalize_tail(row.get("tail"))
    }
    locked_eta = {}
    for assignment in assignments:
        tail = _normalize_tail(assignment.tail_number)
        row = rows_by_tail.get(tail, {})
        eta = row.get("arrival_block_in_local")
        position = _normalize_position(assignment.position_code)
        if not eta or not position:
            continue
        locked_eta.setdefault(position, []).append(eta)

        aircraft_type = _parking_aircraft_type_for_row(row)
        if aircraft_type != "767":
            continue
        blocked_position = _blocked_position_for_values(aircraft_type, position)
        if blocked_position:
            locked_eta.setdefault(blocked_position, []).append(eta)
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


def _normal_ramp_counts_for_placements(placements):
    counts = {}
    for placement in placements:
        if placement.ramp not in NORMAL_RAMP_CODES:
            continue
        counts[placement.ramp] = counts.get(placement.ramp, 0) + 1
    return counts


def _merge_ramp_counts(*sources):
    merged = {}
    for source in sources:
        for ramp, count in (source or {}).items():
            merged[ramp] = merged.get(ramp, 0) + max(0, int(count or 0))
    return merged


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


def _is_constant_one(value):
    return isinstance(value, int) and value == 1


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
    if ramp == "R":
        return "Remote"
    if ramp == "THROAT":
        return "9/10 throat parking"
    return ramp


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
