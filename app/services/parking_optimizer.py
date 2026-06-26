from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ortools.sat.python import cp_model

from app.extensions import db
from app.models import MotherBrainParkingRule, MotherBrainParkingSettings, SortDateParkingAssignment
from app.services.gateway_matrix import gateway_timezone
from app.services.parking_physical_validator import (
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
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
    DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
)


PARKING_OPTIMIZER_TIME_LIMIT_SECONDS = 3.0
SUCCESS_SOLVER_STATUSES = {"OPTIMAL", "FEASIBLE"}
PREFERRED_RAMP_SCORE = 600
AVOID_RAMP_PENALTY = 350
DEICE_CLOSE_PAIR_PENALTY = 400


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
        for conflict in validate_parking_physical_rules(operation, tail_rows=tail_rows)
    ]
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
    placements = _build_candidate_placements(
        candidate_rows,
        candidate_positions,
        locked_lane_keys,
        locked_filled_positions,
        locked_blocked_positions,
        rules["hard"],
        rules["soft"],
        timezone_name,
    )

    if not placements:
        return _preview_result(
            "NO_CANDIDATES",
            include_remote,
            include_throat,
            deice_threshold,
            len(candidate_rows),
            locked_assignments,
            [],
            _unassigned_rows(
                candidate_rows,
                {},
                placements,
                include_remote=include_remote,
                include_throat=include_throat,
                locked_conflicts=locked_conflicts,
                hard_rules=rules["hard"],
            ),
            locked_conflicts,
            "No candidate parking positions are available under the selected toggles and hard rules.",
        )

    model = cp_model.CpModel()
    variables = {
        placement: model.NewBoolVar(
            f"assign_{_var_key(placement.tail)}_{placement.position}_{placement.lane}"
        )
        for placement in placements
    }

    _add_tail_constraints(model, variables)
    _add_lane_constraints(model, variables)
    fill_exprs = _add_position_fill_constraints(
        model,
        variables,
        locked_filled_positions | locked_blocked_positions,
    )
    _add_fill_order_constraints(model, variables, fill_exprs)
    _add_throat_constraints(model, variables, fill_exprs)
    _add_767_block_constraints(model, variables, fill_exprs)
    _add_eta_order_constraints(model, variables, locked_eta_by_position)

    objective_terms = [
        variable * (100000 + placement.soft_score - placement.cost)
        for placement, variable in variables.items()
    ]
    objective_terms.extend(
        _deice_spacing_penalty_terms(
            model,
            variables,
            assignments,
            tail_rows,
            timezone_name,
            deice_threshold,
        )
    )
    model.Maximize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = PARKING_OPTIMIZER_TIME_LIMIT_SECONDS
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    selected_by_tail = {}
    suggestions = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for placement in sorted(placements, key=lambda item: (item.tail, item.cost, item.label)):
            if solver.Value(variables[placement]) != 1:
                continue
            selected_by_tail[placement.tail] = placement
        selected_deice_reasons = _selected_deice_reasons(
            selected_by_tail,
            assignments,
            tail_rows,
            timezone_name,
            deice_threshold,
        )
        for placement in sorted(selected_by_tail.values(), key=lambda item: (item.tail, item.cost, item.label)):
            row = rows_by_tail.get(placement.tail, {})
            suggestions.append(
                _suggestion_row(
                    placement,
                    row,
                    deice_threshold,
                    selected_deice_reasons.get(placement.tail, ()),
                )
            )

    unassigned = _unassigned_rows(
        candidate_rows,
        selected_by_tail,
        placements,
        include_remote=include_remote,
        include_throat=include_throat,
        locked_conflicts=locked_conflicts,
        hard_rules=rules["hard"],
    )
    summary = (
        "Preview generated. Saved assignments were not changed."
        if suggestions or locked_assignments
        else "No parking suggestions were generated."
    )
    return _preview_result(
        status_name,
        include_remote,
        include_throat,
        deice_threshold,
        len(candidate_rows),
        locked_assignments,
        suggestions,
        unassigned,
        locked_conflicts,
        summary,
        wall_time=solver.WallTime() if status else None,
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

    for suggestion in suggestions:
        tail = _normalize_tail(suggestion.get("tail"))
        position = _normalize_position(suggestion.get("position"))
        lane = _normalize_lane(suggestion.get("lane"))
        if not tail or not position or lane not in (1, 2):
            result["skipped"].append(
                {"tail": tail or "-", "reason": "Suggestion was incomplete."}
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
    candidate_tail_count,
    locked_assignments,
    suggestions,
    unassigned_tails,
    conflicts,
    summary,
    wall_time=None,
):
    return {
        "solver_status": solver_status,
        "summary": summary,
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
        },
        "wall_time_seconds": wall_time,
        "preview_only": True,
    }


def _active_rule_sets(gateway):
    rules = MotherBrainParkingRule.query.filter_by(gateway_id=gateway.id, active=True).all()
    hard_rules = {"forbidden": [], "required": []}
    soft_rules = {"preferred": [], "avoid": []}
    for rule in rules:
        category = str(rule.rule_category or "").strip().lower()
        behavior = _normalize_rule_behavior(rule.rule_behavior)
        if category in (ORIGIN_RAMP_RESTRICTION, AIRCRAFT_TYPE_RAMP_RESTRICTION):
            if behavior not in hard_rules:
                continue
            hard_rules[behavior].append(rule)
        elif category in (ORIGIN_RAMP_PREFERENCE, AIRCRAFT_TYPE_RAMP_PREFERENCE):
            if behavior not in soft_rules:
                continue
            soft_rules[behavior].append(rule)
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
):
    placements = []
    for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"])):
        tail = _normalize_tail(row.get("tail"))
        aircraft_type = _parking_aircraft_type_for_row(row)
        for order, (position, ramp) in enumerate(candidate_positions):
            if position in locked_blocked_positions:
                continue
            if _rule_blocks_row(row, aircraft_type, position, ramp, hard_rules):
                continue
            if not _placement_allows_aircraft(aircraft_type, position, locked_filled_positions):
                continue
            soft_score, preference_reasons = _soft_rule_score(
                row,
                aircraft_type,
                position,
                ramp,
                soft_rules,
            )
            for lane in (1, 2):
                if (position, lane) in locked_lane_keys:
                    continue
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
                        soft_score=soft_score,
                        preference_reasons=tuple(preference_reasons),
                    )
                )
    return placements


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
    include_remote=False,
    include_throat=False,
    locked_conflicts=None,
    hard_rules=None,
):
    placement_tails = {placement.tail for placement in placements}
    rows = []
    for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"])):
        tail = _normalize_tail(row.get("tail"))
        if tail in selected_by_tail:
            continue
        aircraft_type = _parking_aircraft_type_for_row(row)
        rows.append(
            {
                "tail": tail,
                "origin": row.get("arrival_origin") or "-",
                "aircraft_type": aircraft_type,
                "parking_window": _parking_window_label(row),
                "reason": _unresolved_reason(
                    row,
                    aircraft_type,
                    has_candidate=tail in placement_tails,
                    include_remote=include_remote,
                    include_throat=include_throat,
                    locked_conflicts=locked_conflicts or [],
                    hard_rules=hard_rules or {},
                ),
            }
        )
    return rows


def _suggestion_row(placement, row, deice_threshold=0, deice_reasons=()):
    reasons = ["Suggested by optimizer preview."]
    reasons.extend(placement.preference_reasons)
    if deice_threshold and placement.departure:
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
    required_rules = _rules_for_row(row, aircraft_type, rules.get("required", []))
    if required_rules and not any(_rule_matches_position(rule, position, ramp) for rule in required_rules):
        return True
    return any(
        _rule_matches_position(rule, position, ramp)
        for rule in _rules_for_row(row, aircraft_type, rules.get("forbidden", []))
    )


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
        return []

    terms = []
    candidate_entries = [
        (placement, variable)
        for placement, variable in variables.items()
        if _is_deice_position(placement.position) and placement.departure
    ]
    locked_entries = _locked_deice_entries(assignments, tail_rows, timezone_name)

    for index, (placement, variable) in enumerate(candidate_entries):
        for locked in locked_entries:
            if not _deice_entries_are_close(placement, locked, threshold_minutes):
                continue
            terms.append(variable * -_deice_penalty(placement.departure, locked["departure"], threshold_minutes))

        for other_index, (other_placement, other_variable) in enumerate(candidate_entries[index + 1 :], start=index + 1):
            if not _deice_entries_are_close(placement, other_placement, threshold_minutes):
                continue
            both = model.NewBoolVar(
                f"deice_{_var_key(placement.tail)}_{_var_key(other_placement.tail)}_{index}_{other_index}"
            )
            model.Add(both <= variable)
            model.Add(both <= other_variable)
            model.Add(both >= variable + other_variable - 1)
            terms.append(
                both
                * -_deice_penalty(
                    placement.departure,
                    other_placement.departure,
                    threshold_minutes,
                )
            )
    return terms


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


def _rule_matches_position(rule, position, ramp):
    rule_ramp = str(rule.ramp_code or "").strip().upper()
    if rule_ramp == "THROAT":
        return _position_number(position) in (9, 10)
    return rule_ramp == ramp


def _placement_allows_aircraft(aircraft_type, position, locked_filled_positions):
    number = _position_number(position)
    ramp = _ramp_from_position(position)
    if aircraft_type != "767":
        return True
    if ramp not in NORMAL_RAMP_CODES or number in (9, 10):
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
        if ramp not in NORMAL_RAMP_CODES:
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


def _blocked_position_for_placement(placement):
    return _blocked_position_for_values(placement.aircraft_type, placement.position)


def _blocked_position_for_values(aircraft_type, position):
    if aircraft_type != "767":
        return ""
    ramp = _ramp_from_position(position)
    number = _position_number(position)
    if ramp not in NORMAL_RAMP_CODES:
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
    include_remote,
    include_throat,
    locked_conflicts,
    hard_rules,
):
    if has_candidate:
        return "No feasible parking position under hard rules, ETA order, and scoring constraints."

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
