from dataclasses import dataclass

from app.extensions import db
from app.models import MotherBrainAlert, MotherBrainParkingRule, SortDateParkingAssignment
from app.services.motherbrain_alerts import (
    MOTHERBRAIN_ALERT_SCOPE,
    PARKING_CONFLICT_ALERT_PERMISSION,
)
from app.services.parking_aircraft import resolve_parking_aircraft_type_from_tail
from app.services.parking_rules import (
    BLOCKED_PARKING_POSITION,
    normalize_parking_position_code,
)


NORMAL_RAMP_CODES = ("A", "B", "C", "D", "E")
NORMAL_767_FOOTPRINT_RAMP_CODES = ("A", "B", "C", "D")
NORMAL_BANKS = ((1, 2, 3, 4), (5, 6, 7, 8))
REMOTE_ORDER = ("R01", "R02", "R03", "R04")
VALID_767_NORMAL_ANCHORS = {1: 2, 2: 3, 3: 4, 5: 6, 6: 7, 7: 8}
PARKING_ALERT_KEY_PREFIX = "parking-physical"


@dataclass(frozen=True)
class ParkingPhysicalConflict:
    conflict_key: str
    severity: str
    title: str
    message: str
    reason: str
    position: str = ""
    tail: str = ""
    blocking_position: str = ""
    blocking_tail: str = ""
    eta: str = ""
    blocking_eta: str = ""


def parking_physical_validation_context(operation, tail_rows=None):
    conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
    return {
        "conflicts": [conflict.__dict__ for conflict in conflicts],
        "conflict_count": len(conflicts),
        "has_conflicts": bool(conflicts),
    }


def validate_parking_physical_rules(operation, tail_rows=None, include_order_conflicts=False):
    if not operation:
        return []

    assignments = _active_assignments(operation)
    aircraft_type_by_tail = _aircraft_type_by_tail(tail_rows)
    occupancy = _occupancy_by_position(assignments)
    blocked = _blocked_positions(assignments, aircraft_type_by_tail)
    conflicts = []

    conflicts.extend(_parking_rule_blocked_position_conflicts(assignments, operation))
    if include_order_conflicts:
        conflicts.extend(_normal_fill_order_conflicts(occupancy, blocked))
        conflicts.extend(_remote_fill_order_conflicts(occupancy, blocked))
    conflicts.extend(_normal_767_conflicts(assignments, aircraft_type_by_tail, occupancy))
    conflicts.extend(_throat_conflicts(occupancy, blocked))
    conflicts.extend(_slot_2_overflow_conflicts(assignments, tail_rows))
    if include_order_conflicts:
        conflicts.extend(_eta_order_conflicts(assignments, aircraft_type_by_tail, tail_rows))
    return _dedupe_conflicts(conflicts)


def _parking_rule_blocked_position_conflicts(assignments, operation):
    blocked_positions = _active_rule_blocked_positions(operation)
    if not blocked_positions:
        return []

    conflicts = []
    for assignment in assignments:
        position = _normalize_position(getattr(assignment, "position_code", ""))
        if position not in blocked_positions:
            continue
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        conflicts.append(
            _conflict(
                "critical",
                "Blocked parking position",
                position,
                tail,
                f"{position} is blocked by Parking Rules.",
                "parking_rules_blocked_position",
            )
        )
    return conflicts


def _active_rule_blocked_positions(operation):
    if not operation:
        return set()
    query = MotherBrainParkingRule.query.filter_by(
        rule_category=BLOCKED_PARKING_POSITION,
        subject_type="position",
        active=True,
    )
    if getattr(operation, "gateway_id", None):
        query = query.filter(MotherBrainParkingRule.gateway_id == operation.gateway_id)
    else:
        query = query.filter(MotherBrainParkingRule.gateway_code == operation.gateway_code)
    return {
        normalize_parking_position_code(rule.subject_value)
        for rule in query.all()
        if normalize_parking_position_code(rule.subject_value)
    }


def sync_parking_physical_alerts(gateway, operation, validation_context):
    if not gateway or not operation:
        return {"changed": False, "active_keys": []}

    conflicts = validation_context.get("conflicts", [])
    alert_prefix = f"{PARKING_ALERT_KEY_PREFIX}:{operation.id}:"
    active_keys = {f"{alert_prefix}{conflict['conflict_key']}" for conflict in conflicts}
    changed = False

    existing_alerts = MotherBrainAlert.query.filter(
        MotherBrainAlert.gateway_id == gateway.id,
        MotherBrainAlert.scope == MOTHERBRAIN_ALERT_SCOPE,
        MotherBrainAlert.alert_key.like(f"{alert_prefix}%"),
    ).all()
    existing_by_key = {alert.alert_key: alert for alert in existing_alerts}

    related_url = f"/motherbrain/parking-plan/{operation.id}"
    for conflict in conflicts:
        alert_key = f"{alert_prefix}{conflict['conflict_key']}"
        alert = existing_by_key.get(alert_key)
        if not alert:
            alert = MotherBrainAlert(
                gateway_id=gateway.id,
                sort_date_operation_id=operation.id,
                gateway_code=gateway.code,
                scope=MOTHERBRAIN_ALERT_SCOPE,
                alert_key=alert_key,
            )
            db.session.add(alert)
            changed = True

        next_values = {
            "sort_date_operation_id": operation.id,
            "gateway_code": gateway.code,
            "severity": conflict["severity"],
            "title": conflict["title"],
            "message": conflict["message"],
            "related_url": related_url,
            "related_label": "VIEW PARKING PLAN",
            "permission_key": PARKING_CONFLICT_ALERT_PERMISSION,
            "active": True,
            "acknowledged": False,
        }
        for field, value in next_values.items():
            if getattr(alert, field) != value:
                setattr(alert, field, value)
                changed = True

    for alert in existing_alerts:
        if alert.alert_key in active_keys:
            continue
        if alert.active:
            alert.active = False
            changed = True

    db.session.flush()
    return {"changed": changed, "active_keys": sorted(active_keys)}


def _active_assignments(operation):
    return [
        assignment
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
        if _position_number(getattr(assignment, "position_code", "")) is not None
        and _normalize_ramp(getattr(assignment, "ramp_code", ""))
    ]


def _aircraft_type_by_tail(tail_rows):
    return {
        _normalize_tail(row.get("tail")): resolve_parking_aircraft_type_from_tail(row.get("tail"))
        for row in (tail_rows or [])
        if _normalize_tail(row.get("tail"))
    }


def _occupancy_by_position(assignments):
    occupancy = {}
    for assignment in assignments:
        position = _normalize_position(getattr(assignment, "position_code", ""))
        if not position:
            continue
        occupancy.setdefault(position, []).append(assignment)
    return occupancy


def _blocked_positions(assignments, aircraft_type_by_tail):
    blocked = {}
    for assignment in assignments:
        position = _normalize_position(getattr(assignment, "position_code", ""))
        ramp = _normalize_ramp(getattr(assignment, "ramp_code", ""))
        number = _position_number(position)
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES or number is None:
            continue
        if aircraft_type_by_tail.get(tail) != "767":
            continue
        blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
        if not blocked_number:
            continue
        blocked.setdefault(f"{ramp}{blocked_number:02d}", []).append(assignment)
    return blocked


def _normal_fill_order_conflicts(occupancy, blocked):
    conflicts = []
    for ramp in NORMAL_RAMP_CODES:
        for bank in NORMAL_BANKS:
            for number in bank:
                position = f"{ramp}{number:02d}"
                if not _is_position_filled(position, occupancy, blocked):
                    continue
                missing = [
                    f"{ramp}{lower:02d}"
                    for lower in bank
                    if lower < number
                    and not _is_position_filled(f"{ramp}{lower:02d}", occupancy, blocked)
                ]
                if not missing:
                    continue
                conflicts.append(
                    _conflict(
                        "warning",
                        "Parking fill order conflict",
                        position,
                        _first_tail_at(position, occupancy),
                        f"{position} cannot be used until {', '.join(missing)} are filled.",
                        "normal_bank_fill_order",
                    )
                )
    return conflicts


def _remote_fill_order_conflicts(occupancy, blocked):
    conflicts = []
    for index, position in enumerate(REMOTE_ORDER):
        if not _is_position_filled(position, occupancy, blocked):
            continue
        missing = [
            prior
            for prior in REMOTE_ORDER[:index]
            if not _is_position_filled(prior, occupancy, blocked)
        ]
        if not missing:
            continue
        conflicts.append(
            _conflict(
                "warning",
                "Remote fill order conflict",
                position,
                _first_tail_at(position, occupancy),
                f"{position} cannot be used until {', '.join(missing)} are filled.",
                "remote_fill_order",
            )
        )
    return conflicts


def _normal_767_conflicts(assignments, aircraft_type_by_tail, occupancy):
    conflicts = []
    for assignment in assignments:
        position = _normalize_position(getattr(assignment, "position_code", ""))
        ramp = _normalize_ramp(getattr(assignment, "ramp_code", ""))
        number = _position_number(position)
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        if aircraft_type_by_tail.get(tail) != "767":
            continue
        if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES:
            continue
        if number not in {1, 2, 3, 4, 5, 6, 7, 8}:
            continue

        blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
        if not blocked_number:
            conflicts.append(
                _conflict(
                    "critical",
                    "Invalid 767 parking anchor",
                    position,
                    tail,
                    f"767 at {position} is invalid because 767 aircraft cannot anchor at {number:02d}.",
                    "invalid_767_anchor",
                )
            )
            continue

        blocked_position = f"{ramp}{blocked_number:02d}"
        blocked_occupants = [
            occupant
            for occupant in occupancy.get(blocked_position, [])
            if _normalize_tail(getattr(occupant, "tail_number", "")) != tail
        ]
        for occupant in blocked_occupants:
            occupant_tail = _normalize_tail(getattr(occupant, "tail_number", ""))
            conflicts.append(
                _conflict(
                    "critical",
                    "Blocked slot occupied",
                    blocked_position,
                    occupant_tail,
                    f"{blocked_position} is blocked by 767 parked at {position}.",
                    f"blocked_by_767_{position}_{tail}",
                )
            )
    return conflicts


def _throat_conflicts(occupancy, blocked):
    conflicts = []
    for ramp in NORMAL_RAMP_CODES:
        pos09 = f"{ramp}09"
        pos10 = f"{ramp}10"
        if _is_position_filled(pos09, occupancy, blocked):
            if not _is_position_filled(pos10, occupancy, blocked):
                conflicts.append(
                    _conflict(
                        "warning",
                        "Throat parking order conflict",
                        pos09,
                        _first_tail_at(pos09, occupancy),
                        f"{pos09} cannot be used unless {pos10} is also parked.",
                        "throat_09_without_10",
                    )
                )
            if not _has_clear_full_bank(ramp, occupancy, blocked):
                conflicts.append(
                    _conflict(
                        "warning",
                        "Throat parking clearance conflict",
                        pos09,
                        _first_tail_at(pos09, occupancy),
                        f"{pos09} can only be used when a full {ramp}01-{ramp}04 or {ramp}05-{ramp}08 bank is clear.",
                        "throat_09_bank_clearance",
                    )
                )
        if _is_position_filled(pos10, occupancy, blocked) and not _has_clear_partial_bank(
            ramp,
            occupancy,
            blocked,
        ):
            conflicts.append(
                _conflict(
                    "warning",
                    "Throat parking clearance conflict",
                    pos10,
                    _first_tail_at(pos10, occupancy),
                    f"{pos10} can only be used when {ramp}02-{ramp}04 or {ramp}06-{ramp}08 are clear.",
                    "throat_10_clearance",
                )
            )
    return conflicts


def _eta_order_conflicts(assignments, aircraft_type_by_tail, tail_rows):
    fill_items = _eta_fill_items_by_position(assignments, aircraft_type_by_tail, tail_rows)
    conflicts = []
    for ramp in NORMAL_RAMP_CODES:
        for bank in NORMAL_BANKS:
            sequence = [f"{ramp}{number:02d}" for number in bank]
            conflicts.extend(_eta_sequence_conflicts(sequence, fill_items, "normal_bank_eta_order"))

        conflicts.extend(
            _eta_sequence_conflicts(
                [f"{ramp}10", f"{ramp}09"],
                fill_items,
                "throat_eta_order",
                throat=True,
            )
        )

    conflicts.extend(
        _eta_sequence_conflicts(
            list(REMOTE_ORDER),
            fill_items,
            "remote_eta_order",
            remote=True,
        )
    )
    return conflicts


def _slot_2_overflow_conflicts(assignments, tail_rows):
    assignments_by_lane = {}
    for assignment in assignments:
        position = _normalize_position(getattr(assignment, "position_code", ""))
        try:
            lane = int(getattr(assignment, "lane_number", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not position or lane not in (1, 2):
            continue
        assignments_by_lane.setdefault((position, lane), assignment)

    timing_by_tail = _parking_window_timing_by_tail(tail_rows)
    conflicts = []
    for (position, lane), slot_2_assignment in sorted(assignments_by_lane.items()):
        if lane != 2:
            continue
        tail = _normalize_tail(getattr(slot_2_assignment, "tail_number", ""))
        slot_1_assignment = assignments_by_lane.get((position, 1))
        if not slot_1_assignment:
            conflicts.append(
                _slot_2_conflict(
                    position,
                    tail,
                    "",
                    f"{position} Slot 2 cannot be used while {position} Slot 1 is empty.",
                    "slot_2_empty_slot_1",
                )
            )
            continue

        slot_1_tail = _normalize_tail(getattr(slot_1_assignment, "tail_number", ""))
        slot_1_timing = timing_by_tail.get(slot_1_tail, {})
        slot_2_timing = timing_by_tail.get(tail, {})
        slot_1_departure = slot_1_timing.get("departure")
        slot_2_arrival = slot_2_timing.get("arrival")
        if not slot_1_departure:
            conflicts.append(
                _slot_2_conflict(
                    position,
                    tail,
                    slot_1_tail,
                    f"{position} Slot 2 cannot be used because {position} Slot 1 has no known departure time.",
                    "slot_2_unknown_slot_1_departure",
                )
            )
            continue
        if not slot_2_arrival:
            conflicts.append(
                _slot_2_conflict(
                    position,
                    tail,
                    slot_1_tail,
                    f"{position} Slot 2 cannot be used because Slot 2 arrival time is unknown.",
                    "slot_2_unknown_arrival",
                )
            )
            continue
        if slot_1_departure > slot_2_arrival:
            conflicts.append(
                _slot_2_conflict(
                    position,
                    tail,
                    slot_1_tail,
                    (
                        f"{position} Slot 2 cannot be used because Slot 1 departure "
                        f"{_format_local_time(slot_1_departure)} is after Slot 2 arrival "
                        f"{_format_local_time(slot_2_arrival)}."
                    ),
                    "slot_2_timing_overlap",
                    blocking_eta=_format_local_time(slot_1_departure),
                    eta=_format_local_time(slot_2_arrival),
                )
            )
    return conflicts


def _slot_2_conflict(
    position,
    tail,
    blocking_tail,
    message,
    reason,
    eta="",
    blocking_eta="",
):
    return ParkingPhysicalConflict(
        conflict_key=_stable_conflict_key(reason, f"{position}-slot-2", tail, message),
        severity="warning",
        title="Slot 2 overflow conflict",
        message=message,
        reason=reason,
        position=f"{position} Slot 2",
        tail=tail,
        blocking_position=f"{position} Slot 1",
        blocking_tail=blocking_tail,
        eta=eta,
        blocking_eta=blocking_eta,
    )


def _eta_fill_items_by_position(assignments, aircraft_type_by_tail, tail_rows):
    timing_by_tail = _eta_timing_by_tail(tail_rows)
    fill_items = {}
    for assignment in assignments:
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        position = _normalize_position(getattr(assignment, "position_code", ""))
        ramp = _normalize_ramp(getattr(assignment, "ramp_code", ""))
        number = _position_number(position)
        timing = timing_by_tail.get(tail)
        if not tail or not position or not timing:
            continue

        item = {
            "tail": tail,
            "position": position,
            "assignment_position": position,
            "eta": timing["eta"],
            "eta_label": timing["eta_label"],
        }
        fill_items.setdefault(position, []).append(item)

        if ramp not in NORMAL_767_FOOTPRINT_RAMP_CODES or aircraft_type_by_tail.get(tail) != "767":
            continue
        blocked_number = VALID_767_NORMAL_ANCHORS.get(number)
        if not blocked_number:
            continue
        blocked_position = f"{ramp}{blocked_number:02d}"
        fill_items.setdefault(blocked_position, []).append(
            {
                **item,
                "position": blocked_position,
            }
        )
    return fill_items


def _eta_timing_by_tail(tail_rows):
    timing = {}
    for row in tail_rows or []:
        tail = _normalize_tail(row.get("tail"))
        eta = row.get("arrival_block_in_local")
        if not tail or not eta:
            continue
        timing[tail] = {
            "eta": eta,
            "eta_label": _format_local_time(eta),
        }
    return timing


def _parking_window_timing_by_tail(tail_rows):
    timing = {}
    for row in tail_rows or []:
        tail = _normalize_tail(row.get("tail"))
        if not tail:
            continue
        timing[tail] = {
            "arrival": row.get("arrival_block_in_local"),
            "departure": row.get("departure_datetime_local"),
        }
    return timing


def _eta_sequence_conflicts(sequence, fill_items, reason, remote=False, throat=False):
    conflicts = []
    for index, position in enumerate(sequence):
        lower_positions = sequence[:index]
        if not lower_positions:
            continue
        for item in fill_items.get(position, []):
            for lower_position in lower_positions:
                for lower_item in fill_items.get(lower_position, []):
                    if _same_eta_fill_item(item, lower_item):
                        continue
                    if not item["eta"] or not lower_item["eta"]:
                        continue
                    if item["eta"] >= lower_item["eta"]:
                        continue
                    conflicts.append(
                        _eta_conflict(
                            position,
                            item,
                            lower_position,
                            lower_item,
                            lower_positions,
                            reason,
                            remote=remote,
                            throat=throat,
                        )
                    )
    return conflicts


def _same_eta_fill_item(item, other):
    return (
        item.get("tail") == other.get("tail")
        and item.get("assignment_position") == other.get("assignment_position")
    )


def _eta_conflict(
    position,
    item,
    blocking_position,
    blocking_item,
    required_prior_positions,
    reason,
    remote=False,
    throat=False,
):
    prior_label = "/".join(required_prior_positions)
    tail = item.get("tail") or ""
    blocking_tail = blocking_item.get("tail") or ""
    eta = item.get("eta_label") or "-"
    blocking_eta = blocking_item.get("eta_label") or "-"
    assignment_position = item.get("assignment_position") or position
    blocking_assignment_position = blocking_item.get("assignment_position") or blocking_position

    if throat:
        message = (
            f"{position} ETA order conflict: {position} cannot arrive before {blocking_position}. "
            f"{tail} at {assignment_position} ETA {eta} is before "
            f"{blocking_tail} at {blocking_assignment_position} ETA {blocking_eta}."
        )
    elif remote:
        message = (
            f"{position} ETA order conflict: {position} arrives before {prior_label}. "
            f"{tail} at {assignment_position} ETA {eta} is before "
            f"{blocking_tail} at {blocking_assignment_position} ETA {blocking_eta}."
        )
    else:
        message = (
            f"{position} cannot arrive before {prior_label} are parked. "
            f"{tail} at {assignment_position} ETA {eta} is before "
            f"{blocking_tail} at {blocking_assignment_position} ETA {blocking_eta}."
        )

    conflict_key = _stable_conflict_key(
        reason,
        position,
        tail,
        f"{blocking_position}|{blocking_tail}",
    )
    return ParkingPhysicalConflict(
        conflict_key=conflict_key,
        severity="warning",
        title="Parking ETA order conflict",
        message=message,
        reason=reason,
        position=position,
        tail=tail,
        blocking_position=blocking_position,
        blocking_tail=blocking_tail,
        eta=eta,
        blocking_eta=blocking_eta,
    )


def _has_clear_full_bank(ramp, occupancy, blocked):
    return any(
        all(_is_position_clear(f"{ramp}{number:02d}", occupancy, blocked) for number in bank)
        for bank in NORMAL_BANKS
    )


def _has_clear_partial_bank(ramp, occupancy, blocked):
    partial_banks = ((2, 3, 4), (6, 7, 8))
    return any(
        all(_is_position_clear(f"{ramp}{number:02d}", occupancy, blocked) for number in bank)
        for bank in partial_banks
    )


def _is_position_filled(position, occupancy, blocked):
    return bool(occupancy.get(position) or blocked.get(position))


def _is_position_clear(position, occupancy, blocked):
    return not _is_position_filled(position, occupancy, blocked)


def _first_tail_at(position, occupancy):
    for assignment in occupancy.get(position, []):
        tail = _normalize_tail(getattr(assignment, "tail_number", ""))
        if tail:
            return tail
    return ""


def _conflict(severity, title, position, tail, message, reason):
    return ParkingPhysicalConflict(
        conflict_key=_stable_conflict_key(reason, position, tail, message),
        severity=severity,
        title=title,
        message=message,
        reason=reason,
        position=position,
        tail=tail,
    )


def _stable_conflict_key(reason, position, tail, message):
    raw = "|".join(str(value or "") for value in (reason, position, tail, message))
    cleaned = "".join(character.lower() if character.isalnum() else "-" for character in raw)
    return "-".join(part for part in cleaned.split("-") if part)[:120]


def _dedupe_conflicts(conflicts):
    deduped = {}
    for conflict in conflicts:
        deduped.setdefault(conflict.conflict_key, conflict)
    return sorted(
        deduped.values(),
        key=lambda conflict: (
            conflict.position,
            conflict.tail,
            conflict.reason,
            conflict.message,
        ),
    )


def _normalize_tail(value):
    return str(value or "").strip().upper()


def _normalize_ramp(value):
    return str(value or "").strip().upper()


def _normalize_position(value):
    return str(value or "").strip().upper()


def _position_number(position):
    try:
        return int(str(position or "")[1:])
    except (TypeError, ValueError):
        return None


def _format_local_time(value):
    if not value:
        return "-"
    return value.strftime("%H:%M")
