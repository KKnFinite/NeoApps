from dataclasses import dataclass

from ortools.sat.python import cp_model

from app.models import MotherBrainParkingRule, MotherBrainParkingSettings, SortDateParkingAssignment
from app.services.parking_physical_validator import (
    NORMAL_BANKS,
    NORMAL_RAMP_CODES,
    REMOTE_ORDER,
    VALID_767_NORMAL_ANCHORS,
    validate_parking_physical_rules,
)
from app.services.parking_plan import parking_position_options, tail_rows_for_operation
from app.services.parking_rules import (
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    ORIGIN_RAMP_RESTRICTION,
    DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
)


PARKING_OPTIMIZER_TIME_LIMIT_SECONDS = 3.0


@dataclass(frozen=True)
class ParkingPlacement:
    tail: str
    ramp: str
    position: str
    lane: int
    cost: int
    aircraft_type: str

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
    tail_rows = tail_rows if tail_rows is not None else tail_rows_for_operation(gateway, operation)
    rules = _active_hard_rules(gateway)
    assignments = _active_assignments(operation)

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
    placements = _build_candidate_placements(
        candidate_rows,
        candidate_positions,
        locked_lane_keys,
        locked_filled_positions,
        locked_blocked_positions,
        rules,
    )

    if not placements:
        return _preview_result(
            "NO_CANDIDATES",
            include_remote,
            include_throat,
            locked_assignments,
            [],
            _unassigned_rows(candidate_rows, {}, placements),
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

    model.Maximize(
        sum(variable * 100000 for variable in variables.values())
        - sum(variable * placement.cost for placement, variable in variables.items())
    )

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
            row = rows_by_tail.get(placement.tail, {})
            selected_by_tail[placement.tail] = placement
            suggestions.append(_suggestion_row(placement, row))

    unassigned = _unassigned_rows(candidate_rows, selected_by_tail, placements)
    summary = (
        "Preview generated. Saved assignments were not changed."
        if suggestions or locked_assignments
        else "No parking suggestions were generated."
    )
    return _preview_result(
        status_name,
        include_remote,
        include_throat,
        locked_assignments,
        suggestions,
        unassigned,
        locked_conflicts,
        summary,
        wall_time=solver.WallTime() if status else None,
    )


def _preview_result(
    solver_status,
    include_remote,
    include_throat,
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
        "conflicts": conflicts,
        "has_conflicts": bool(conflicts or unassigned_tails),
        "runtime_toggles": {
            "include_remote": bool(include_remote),
            "include_throat": bool(include_throat),
        },
        "wall_time_seconds": wall_time,
        "preview_only": True,
    }


def _active_hard_rules(gateway):
    rules = MotherBrainParkingRule.query.filter_by(gateway_id=gateway.id, active=True).all()
    hard_rules = {"forbidden": [], "required": []}
    for rule in rules:
        if rule.rule_category not in (ORIGIN_RAMP_RESTRICTION, AIRCRAFT_TYPE_RAMP_RESTRICTION):
            continue
        behavior = str(rule.rule_behavior or "").strip().lower()
        if behavior not in hard_rules:
            continue
        hard_rules[behavior].append(rule)
    return hard_rules


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
                "aircraft_type": row.get("aircraft_type") or "-",
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
    rules,
):
    placements = []
    for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"])):
        tail = _normalize_tail(row.get("tail"))
        aircraft_type = _normalize_aircraft_type(row.get("aircraft_type"))
        for order, (position, ramp) in enumerate(candidate_positions):
            if position in locked_blocked_positions:
                continue
            if _rule_blocks_row(row, aircraft_type, position, ramp, rules):
                continue
            if not _placement_allows_aircraft(aircraft_type, position, locked_filled_positions):
                continue
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


def _unassigned_rows(candidate_rows, selected_by_tail, placements):
    placement_tails = {placement.tail for placement in placements}
    rows = []
    for row in sorted(candidate_rows, key=lambda item: (_parking_window_sort_key(item), item["tail"])):
        tail = _normalize_tail(row.get("tail"))
        if tail in selected_by_tail:
            continue
        rows.append(
            {
                "tail": tail,
                "origin": row.get("arrival_origin") or "-",
                "aircraft_type": row.get("aircraft_type") or "-",
                "parking_window": _parking_window_label(row),
                "reason": (
                    "No feasible parking position under hard rules."
                    if tail in placement_tails
                    else "No candidate position allowed by runtime toggles or hard restrictions."
                ),
            }
        )
    return rows


def _suggestion_row(placement, row):
    return {
        "tail": placement.tail,
        "position": placement.position,
        "lane": placement.lane,
        "label": placement.label,
        "origin": row.get("arrival_origin") or "-",
        "aircraft_type": row.get("aircraft_type") or "-",
        "parking_window": _parking_window_label(row),
        "reason": "Suggested by hard-rule optimizer preview.",
    }


def _rule_blocks_row(row, aircraft_type, position, ramp, rules):
    required_rules = _rules_for_row(row, aircraft_type, rules.get("required", []))
    if required_rules and not any(_rule_matches_position(rule, position, ramp) for rule in required_rules):
        return True
    return any(
        _rule_matches_position(rule, position, ramp)
        for rule in _rules_for_row(row, aircraft_type, rules.get("forbidden", []))
    )


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
        _normalize_tail(row.get("tail")): _normalize_aircraft_type(row.get("aircraft_type"))
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


def _blocked_position_for_placement(placement):
    if placement.aircraft_type != "767":
        return ""
    ramp = placement.ramp
    number = _position_number(placement.position)
    if ramp not in NORMAL_RAMP_CODES:
        return ""
    blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
    return f"{ramp}{blocked_number:02d}" if blocked_number else ""


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
    text = str(value or "").strip().upper()
    if "767" in text:
        return "767"
    if "757" in text:
        return "757"
    if "A300" in text or "A-300" in text:
        return "A300"
    return text


def _ramp_from_position(position):
    position = _normalize_position(position)
    return "R" if position.startswith("R") else position[:1]


def _position_number(position):
    try:
        return int(str(position or "")[1:])
    except (TypeError, ValueError):
        return None
