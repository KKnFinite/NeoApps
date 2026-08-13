import hashlib
import json
from datetime import date, datetime, time

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import (
    MotherBrainParkingRule,
    MotherBrainParkingSettings,
    SortDateMission,
    SortDateParkingAssignment,
    SortDateTailState,
    SortTimelineSettings,
)
from app.services.live_collaboration import entity_version
from app.services.parking_plan import PARKING_LANES, PARKING_RAMP_GROUPS


EMPTY_ENTITY_VERSION = "missing"
EMPTY_SLOT_VERSION = "empty"


class ParkingStateConflict(Exception):
    def __init__(self, message, *, reason, latest=None):
        self.conflict = {
            "type": "parking_state_changed",
            "reason": reason,
            "message": message,
            "can_overwrite": False,
            "refresh_required": True,
            "latest": latest or {},
        }
        super().__init__(message)


def parking_plan_live_state(
    operation,
    *,
    tail_rows,
    summary,
    parking_status,
    revision=None,
):
    """Build compact, stable parking state for browser reconciliation."""
    assignments = (
        SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        )
        .order_by(SortDateParkingAssignment.id.asc())
        .all()
    )
    assignments_by_tail = {
        _normalize_tail(row.tail_number): row
        for row in assignments
        if _normalize_tail(row.tail_number)
    }
    tail_states = {
        _normalize_tail(row.tail_number): row
        for row in SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
        if _normalize_tail(row.tail_number)
    }
    missions_by_tail = {}
    missions = (
        SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
        .filter(SortDateMission.assigned_tail_number.isnot(None))
        .order_by(SortDateMission.id.asc())
        .all()
    )
    for mission in missions:
        tail = _normalize_tail(mission.assigned_tail_number)
        if tail:
            missions_by_tail.setdefault(tail, []).append(mission)

    tails = []
    tails_by_number = {}
    for row in tail_rows:
        tail = _normalize_tail(row.get("tail"))
        assignment = assignments_by_tail.get(tail)
        tail_state = tail_states.get(tail)
        source = assignment_snapshot(assignment)
        version = _digest(
            {
                "assignment": _assignment_revision_values(assignment),
                "tail_state": _tail_state_revision_values(tail_state),
                "missions": [
                    _mission_revision_values(mission)
                    for mission in missions_by_tail.get(tail, ())
                ],
                "display": {
                    "arrival_origin": row.get("arrival_origin"),
                    "arrival_time": row.get("arrival_time"),
                    "departure_destination": row.get("departure_destination"),
                    "departure_time": row.get("departure_time"),
                    "ground_time": row.get("ground_time"),
                    "aircraft_type": row.get("aircraft_type"),
                    "operational_status": row.get("operational_status"),
                    "mission_lines": row.get("active_mission_lines"),
                    "departure_order": row.get("departure_order"),
                },
            }
        )
        state = {
            "id": f"tail:{tail}",
            "tail_number": tail,
            "version": version,
            "source": source,
            "tail_state_version": (
                entity_version(tail_state) if tail_state else EMPTY_ENTITY_VERSION
            ),
            "operational_status": row.get("operational_status") or "normal",
            "is_out_of_service": bool(row.get("is_out_of_service")),
        }
        tails.append(state)
        tails_by_number[tail] = state

    slots = []
    slots_by_id = {}
    occupied_by_slot = {
        _slot_id(row.ramp_code, row.position_code, row.lane_number): row
        for row in assignments
        if row.ramp_code and row.position_code and row.lane_number in PARKING_LANES
    }
    for _ramp_name, ramp_code, position_codes in PARKING_RAMP_GROUPS:
        for position_code in position_codes:
            for lane_number in PARKING_LANES:
                slot_id = _slot_id(ramp_code, position_code, lane_number)
                occupant = occupied_by_slot.get(slot_id)
                slot = {
                    "id": slot_id,
                    "ramp_code": ramp_code,
                    "position_code": position_code,
                    "lane_number": lane_number,
                    "occupant_tail": (
                        _normalize_tail(occupant.tail_number) if occupant else ""
                    ),
                    "version": (
                        entity_version(occupant) if occupant else EMPTY_SLOT_VERSION
                    ),
                }
                slots.append(slot)
                slots_by_id[slot_id] = slot

    conflicts = _safe_conflicts(parking_status)
    revision = revision if revision is not None else parking_plan_revision(operation)
    return {
        "revision": revision,
        "operation": {
            "id": operation.id,
            "version": entity_version(operation),
            "gateway_code": operation.gateway_code,
            "sort_name": operation.sort_name,
            "sort_date": operation.sort_date.isoformat(),
        },
        "summary": dict(summary or {}),
        "conflicts": conflicts,
        "tails": tails,
        "tails_by_number": tails_by_number,
        "slots": slots,
        "slots_by_id": slots_by_id,
    }


def assignment_snapshot(assignment):
    if not assignment:
        return {
            "assignment_id": None,
            "version": EMPTY_ENTITY_VERSION,
            "location": "unassigned",
            "ramp_code": "",
            "position_code": "",
            "lane_number": None,
            "label": "unassigned",
        }
    position_code = _normalize_position(assignment.position_code)
    lane_number = assignment.lane_number if assignment.lane_number in PARKING_LANES else None
    location = _location_token(position_code, lane_number)
    return {
        "assignment_id": assignment.id,
        "version": entity_version(assignment) or EMPTY_ENTITY_VERSION,
        "location": location,
        "ramp_code": _normalize_ramp(assignment.ramp_code),
        "position_code": position_code,
        "lane_number": lane_number,
        "label": (
            f"{position_code} Slot {lane_number}"
            if position_code and lane_number
            else "unassigned"
        ),
    }


def validate_parking_move_snapshot(
    operation,
    *,
    tail_number,
    ramp_code,
    position_code,
    lane_number,
    expected,
):
    """Lock and validate the source and target for a short write transaction."""
    tail = _normalize_tail(tail_number)
    ramp = _normalize_ramp(ramp_code)
    position = _normalize_position(position_code)
    lane = _normalize_lane(lane_number)
    source = _assignment_for_tail_locked(operation, tail)
    target = _assignment_for_slot_locked(operation, ramp, position, lane)
    if not expected.get("enabled"):
        return source, target

    source_state = assignment_snapshot(source)
    if (
        expected.get("source_location") != source_state["location"]
        or expected.get("source_version") != source_state["version"]
    ):
        raise ParkingStateConflict(
            _source_conflict_message(tail, source_state),
            reason="source_changed",
            latest={"tail": tail, "source": source_state},
        )

    target_tail = _normalize_tail(target.tail_number) if target else ""
    target_version = entity_version(target) if target else EMPTY_SLOT_VERSION
    if (
        expected.get("target_tail", "") != target_tail
        or expected.get("target_version") != target_version
    ):
        target_label = f"{position} Slot {lane}"
        raise ParkingStateConflict(
            f"{target_label} is no longer available. Parking changed while you were editing.",
            reason="destination_changed",
            latest={
                "slot": _slot_id(ramp, position, lane),
                "occupant_tail": target_tail,
                "version": target_version,
            },
        )
    return source, target


def validate_parking_source_snapshot(operation, *, tail_number, expected):
    tail = _normalize_tail(tail_number)
    source = _assignment_for_tail_locked(operation, tail)
    if not expected.get("enabled"):
        return source
    source_state = assignment_snapshot(source)
    if (
        expected.get("source_location") != source_state["location"]
        or expected.get("source_version") != source_state["version"]
    ):
        raise ParkingStateConflict(
            _source_conflict_message(tail, source_state),
            reason="source_changed",
            latest={"tail": tail, "source": source_state},
        )
    return source


def parking_snapshot_from_form(form):
    enabled = str(form.get("parking_snapshot") or "").strip() == "1"
    return {
        "enabled": enabled,
        "source_location": str(form.get("expected_source_location") or "").strip(),
        "source_version": str(form.get("expected_source_version") or "").strip(),
        "target_tail": _normalize_tail(form.get("expected_target_tail")),
        "target_version": str(form.get("expected_target_version") or "").strip(),
    }


def optimizer_revision_conflict(operation, expected_revision):
    current = parking_plan_revision(operation)
    expected = str(expected_revision or "").strip()
    if expected and expected == current:
        return None
    return {
        "type": "parking_optimizer_stale",
        "reason": "parking_plan_changed",
        "message": "Parking changed after this optimizer preview. Generate a fresh preview before applying.",
        "can_overwrite": False,
        "refresh_required": True,
        "expected_revision": expected,
        "current_revision": current,
    }


def parking_plan_revision(operation):
    """Return a compact fingerprint for every persisted Parking Plan input."""
    aggregate_queries = (
        _revision_aggregate_query(
            "assignments",
            SortDateParkingAssignment,
            SortDateParkingAssignment.sort_date_operation_id == operation.id,
        ),
        _revision_aggregate_query(
            "tail_states",
            SortDateTailState,
            SortDateTailState.sort_date == operation.sort_date,
            SortDateTailState.gateway_code == operation.gateway_code,
            SortDateTailState.sort_name == operation.sort_name,
        ),
        _revision_aggregate_query(
            "missions",
            SortDateMission,
            SortDateMission.sort_date_operation_id == operation.id,
        ),
        _revision_aggregate_query(
            "rules",
            MotherBrainParkingRule,
            MotherBrainParkingRule.gateway_id == operation.gateway_id,
        ),
        _revision_aggregate_query(
            "parking_settings",
            MotherBrainParkingSettings,
            MotherBrainParkingSettings.gateway_id == operation.gateway_id,
        ),
        _revision_aggregate_query(
            "timeline_settings",
            SortTimelineSettings,
            SortTimelineSettings.gateway_id == operation.gateway_id,
        ),
    )
    aggregate_rows = sorted(
        db.session.execute(union_all(*aggregate_queries)).all(),
        key=lambda row: row.source,
    )
    return _digest(
        {
            "operation_id": operation.id,
            "operation_version": entity_version(operation),
            "inputs": [
                {
                    "source": row.source,
                    "row_count": int(row.row_count or 0),
                    "max_id": int(row.max_id or 0),
                    "id_sum": int(row.id_sum or 0),
                    "latest_updated_at": _value_token(row.latest_updated_at),
                }
                for row in aggregate_rows
            ],
        }
    )


def _revision_aggregate_query(source, model, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(model.updated_at).label("latest_updated_at"),
    ).where(*criteria)


def _assignment_for_tail_locked(operation, tail_number):
    return (
        SortDateParkingAssignment.query.filter(
            SortDateParkingAssignment.sort_date_operation_id == operation.id,
            func.upper(SortDateParkingAssignment.tail_number) == tail_number,
        )
        .with_for_update()
        .first()
    )


def _assignment_for_slot_locked(operation, ramp_code, position_code, lane_number):
    return (
        SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            ramp_code=ramp_code,
            position_code=position_code,
            lane_number=lane_number,
        )
        .with_for_update()
        .first()
    )


def _source_conflict_message(tail, source):
    if source["location"] != "unassigned":
        return f"{tail} was moved to {source['label']} by another user while you were working."
    return f"{tail} parking changed while you were working. Latest plan has been loaded."


def _assignment_revision_values(row):
    if not row:
        return None
    return {
        "id": row.id,
        "tail": _normalize_tail(row.tail_number),
        "ramp": _normalize_ramp(row.ramp_code),
        "position": _normalize_position(row.position_code),
        "lane": row.lane_number,
        "is_hot": bool(row.is_hot),
        "note": row.note or "",
        "assigned_by": row.assigned_by_user_id,
        "assigned_at": _value_token(row.assigned_at),
        "updated_at": _value_token(row.updated_at),
    }


def _tail_state_revision_values(row):
    if not row:
        return None
    return {
        "id": row.id,
        "tail": _normalize_tail(row.tail_number),
        "aircraft_type": row.aircraft_type or "",
        "parking_position": row.parking_position or "",
        "operational_status": row.operational_status or "normal",
        "is_out_of_service": bool(row.is_out_of_service),
        "deice_status": row.deice_status or "",
        "updated_at": _value_token(row.updated_at),
    }


def _mission_revision_values(row):
    return {
        "id": row.id,
        "tail": _normalize_tail(row.assigned_tail_number),
        "mission_type": row.mission_type,
        "planned": _value_token(row.planned_datetime_local),
        "eta": _value_token(row.eta_datetime_utc),
        "arrival_status": row.arrival_status or "",
        "departure_status": row.departure_status or "",
        "updated_at": _value_token(row.updated_at),
    }


def _safe_conflicts(parking_status):
    status = parking_status or {}
    return {
        "has_conflicts": bool(status.get("has_conflicts")),
        "has_warnings": bool(status.get("has_warnings")),
        "conflict_count": int(status.get("conflict_count") or 0),
        "physical": [
            {
                "position": row.get("position") or "",
                "tail": row.get("tail") or "",
                "message": row.get("message") or "",
            }
            for row in status.get("physical_conflicts", ())
        ],
    }


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _value_token(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def _slot_id(ramp_code, position_code, lane_number):
    return f"slot:{_normalize_ramp(ramp_code)}:{_normalize_position(position_code)}:{lane_number}"


def _location_token(position_code, lane_number):
    if not position_code or lane_number not in PARKING_LANES:
        return "unassigned"
    return f"{position_code}:{lane_number}"


def _normalize_tail(value):
    return str(value or "").strip().upper()


def _normalize_ramp(value):
    return str(value or "").strip().upper()


def _normalize_position(value):
    text = str(value or "").strip().upper().replace(" ", "")
    if len(text) >= 2 and text[0] in {"A", "B", "C", "D", "E", "R"}:
        digits = text[1:]
        if digits.isdigit():
            return f"{text[0]}{int(digits):02d}"
    return text


def _normalize_lane(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
