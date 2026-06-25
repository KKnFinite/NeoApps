from dataclasses import dataclass

from app.extensions import db
from app.models import MotherBrainAlert, SortDateParkingAssignment
from app.services.motherbrain_alerts import (
    MOTHERBRAIN_ALERT_SCOPE,
    PARKING_CONFLICT_ALERT_PERMISSION,
)


NORMAL_RAMP_CODES = ("A", "B", "C", "D", "E")
NORMAL_BANKS = ((1, 2, 3, 4), (5, 6, 7, 8))
REMOTE_ORDER = ("R01", "R02", "R03", "R04")
VALID_767_NORMAL_ANCHORS = {1: 2, 3: 4, 5: 6, 7: 8}
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


def parking_physical_validation_context(operation, tail_rows=None):
    conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
    return {
        "conflicts": [conflict.__dict__ for conflict in conflicts],
        "conflict_count": len(conflicts),
        "has_conflicts": bool(conflicts),
    }


def validate_parking_physical_rules(operation, tail_rows=None):
    if not operation:
        return []

    assignments = _active_assignments(operation)
    aircraft_type_by_tail = _aircraft_type_by_tail(tail_rows)
    occupancy = _occupancy_by_position(assignments)
    blocked = _blocked_positions(assignments, aircraft_type_by_tail)
    conflicts = []

    conflicts.extend(_normal_fill_order_conflicts(occupancy, blocked))
    conflicts.extend(_remote_fill_order_conflicts(occupancy, blocked))
    conflicts.extend(_normal_767_conflicts(assignments, aircraft_type_by_tail, occupancy))
    conflicts.extend(_throat_conflicts(occupancy, blocked))
    return _dedupe_conflicts(conflicts)


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
                gateway_code=gateway.code,
                scope=MOTHERBRAIN_ALERT_SCOPE,
                alert_key=alert_key,
            )
            db.session.add(alert)
            changed = True

        next_values = {
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
        _normalize_tail(row.get("tail")): _normalize_aircraft_type(row.get("aircraft_type"))
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
        if ramp not in NORMAL_RAMP_CODES or number is None:
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
        if ramp not in NORMAL_RAMP_CODES:
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


def _normalize_aircraft_type(value):
    text = str(value or "").strip().upper()
    if "767" in text:
        return "767"
    if "757" in text:
        return "757"
    if "A300" in text or "A-300" in text:
        return "A300"
    return text
