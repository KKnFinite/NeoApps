"""Read-only current-sort data for NeoRain operational screens."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import SortDateMission, SortDateParkingAssignment
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.live_collaboration import entity_version
from app.services.node_refresh import node_auto_refresh_status
from app.services.operation_scope import current_operational_sort_operation
from app.services.sort_date_operations import mission_display_timing_data
from app.services.time_display import format_local_hhmm
from app.services.departure_progress import (
    DEPARTURE_STATUS_RANK,
    recompute_departure_status_after_external_clear,
)
from app.services.google_motherbrain_live_missions import (
    _parse_optional_live_datetime,
)


NEORAIN_OUTBOUND_REFRESH_KEY = "neorain.outbound"
_OPERATION_UNSET = object()
NEORAIN_MILESTONE_SOURCE = "neorain"
_UNOWNED_SOURCES = {"", "unknown"}
_HHMM_INPUT_PATTERN = re.compile(r"^\d{4}$")
_RAIN_MILESTONES = {
    "ramp_load_complete": {
        "label": "Ramp Load Complete",
        "timestamp_attr": "ramp_load_completed_at_utc",
        "source_attr": "ramp_load_completed_source",
        "status": "ramp_load_complete",
    },
    "crew_load_complete": {
        "label": "Crew Load Complete",
        "timestamp_attr": "crew_load_completed_at_utc",
        "source_attr": "crew_load_completed_source",
        "status": "crew_load_complete",
    },
    "official_block_out": {
        "label": "Official Block-Out",
        "timestamp_attr": "actual_block_out_datetime_utc",
        "source_attr": "actual_block_out_source",
        "status": "blocked_out",
    },
}
NEORAIN_MUTABLE_MILESTONE_FIELDS = frozenset(
    (*_RAIN_MILESTONES, "no_return")
)


class NeoRainMilestoneError(ValueError):
    """Safe validation/conflict error for a later NeoRain mutation route."""


def neorain_outbound_context(gateway, *, operation=_OPERATION_UNSET):
    """Build the current-sort Outbound board without mutating operational state."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    refresh = neorain_outbound_refresh_status(gateway, operation=operation)
    return {
        "operation": operation,
        "rows": _outbound_rows(operation),
        "refresh_status": refresh,
    }


def current_neorain_outbound_operation(gateway):
    """Return an existing lifecycle-current operation, never a historical fallback."""
    return current_operational_sort_operation(gateway)


def mutate_neorain_departure_milestone(mission, operation, field, value):
    """Mutate one Rain-owned departure fact without committing the transaction.

    Timestamp inputs are strict operational HHMM values; an empty string clears
    only a NeoRain-owned timestamp. No Return is a separate explicit boolean
    action so changing a factual timestamp never silently reverses departure.
    """
    _validate_departure_mission(mission, operation)
    normalized_field = str(field or "").strip().lower()
    if normalized_field == "no_return":
        return _mutate_no_return(mission, value)
    specification = _RAIN_MILESTONES.get(normalized_field)
    if specification is None:
        raise NeoRainMilestoneError("Choose a valid NeoRain milestone.")

    timestamp_utc = _parse_operational_hhmm(value, operation, specification["label"])
    timestamp_attr = specification["timestamp_attr"]
    source_attr = specification["source_attr"]
    current_timestamp = getattr(mission, timestamp_attr)
    current_source = _normalized_source(getattr(mission, source_attr))
    if not _neorain_may_mutate_timestamp(current_timestamp, current_source):
        raise NeoRainMilestoneError(
            f"{specification['label']} is owned by {current_source} and cannot be changed in NeoRain."
        )

    next_source = NEORAIN_MILESTONE_SOURCE if timestamp_utc else "unknown"
    changed = (
        current_timestamp != timestamp_utc or current_source != next_source
    )
    if changed:
        setattr(mission, timestamp_attr, timestamp_utc)
        setattr(mission, source_attr, next_source)

    status_changed = False
    if timestamp_utc is not None:
        status_changed = _advance_departure_status(
            mission,
            specification["status"],
        )
    elif current_timestamp is not None:
        status_changed = recompute_departure_status_after_external_clear(
            mission,
            specification["status"],
        )

    return _mutation_result(
        mission,
        normalized_field,
        changed=changed or status_changed,
        value=getattr(mission, timestamp_attr),
        source=getattr(mission, source_attr),
    )


def neorain_departure_milestone_value(mission, field):
    """Return the canonical value accepted by the focused Google writer."""
    normalized_field = str(field or "").strip().lower()
    if normalized_field == "no_return":
        return _normalized_status(mission.departure_status) == "departed"
    specification = _RAIN_MILESTONES.get(normalized_field)
    if specification is None:
        raise NeoRainMilestoneError("Choose a valid NeoRain milestone.")
    return getattr(mission, specification["timestamp_attr"])


def _mutate_no_return(mission, value):
    desired = _parse_no_return(value)
    current_status = _normalized_status(mission.departure_status)
    current_source = _normalized_source(mission.departure_status_source)

    if desired:
        missing = _missing_no_return_prerequisites(mission)
        if missing:
            raise NeoRainMilestoneError(
                "No Return requires " + ", ".join(missing) + "."
            )
        if current_status == "departed":
            return _mutation_result(
                mission,
                "no_return",
                changed=False,
                value=True,
                source=mission.departure_status_source,
            )
        mission.departure_status = "departed"
        mission.departure_status_source = NEORAIN_MILESTONE_SOURCE
        return _mutation_result(
            mission,
            "no_return",
            changed=True,
            value=True,
            source=NEORAIN_MILESTONE_SOURCE,
        )

    if current_status != "departed":
        return _mutation_result(
            mission,
            "no_return",
            changed=False,
            value=False,
            source=mission.departure_status_source,
        )
    if current_source != NEORAIN_MILESTONE_SOURCE:
        raise NeoRainMilestoneError("No Return is not owned by NeoRain.")
    changed = recompute_departure_status_after_external_clear(mission, "departed")
    return _mutation_result(
        mission,
        "no_return",
        changed=changed,
        value=False,
        source=mission.departure_status_source,
    )


def _parse_operational_hhmm(value, operation, field_label):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    raw_value = str(value).strip()
    if not _HHMM_INPUT_PATTERN.fullmatch(raw_value):
        raise NeoRainMilestoneError(
            f"{field_label} must use four-digit HHMM time."
        )
    hour = int(raw_value[:2])
    minute = int(raw_value[2:])
    if hour > 23 or minute > 59:
        raise NeoRainMilestoneError(f"{field_label} is not a valid time.")
    _local_value, timestamp_utc = _parse_optional_live_datetime(
        time(hour, minute),
        operation,
        field_label,
    )
    return timestamp_utc


def _parse_no_return(value):
    if value is True or str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "set",
    }:
        return True
    if value is False or str(value or "").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
        "clear",
        "",
    }:
        return False
    raise NeoRainMilestoneError("No Return must be set or cleared.")


def _validate_departure_mission(mission, operation):
    if mission is None or operation is None:
        raise NeoRainMilestoneError("A current departure mission is required.")
    if mission.mission_type != "departure":
        raise NeoRainMilestoneError("NeoRain milestones apply only to departures.")
    if mission.sort_date_operation_id != operation.id:
        raise NeoRainMilestoneError("Departure mission is outside the current sort.")


def _neorain_may_mutate_timestamp(current_timestamp, current_source):
    return current_source == NEORAIN_MILESTONE_SOURCE or (
        current_timestamp is None and current_source in _UNOWNED_SOURCES
    )


def _advance_departure_status(mission, target_status):
    current_status = _normalized_status(mission.departure_status)
    if current_status == "cancelled":
        return False
    if DEPARTURE_STATUS_RANK.get(current_status, -1) >= DEPARTURE_STATUS_RANK[
        target_status
    ]:
        return False
    mission.departure_status = target_status
    mission.departure_status_source = NEORAIN_MILESTONE_SOURCE
    return True


def _missing_no_return_prerequisites(mission):
    required = (
        ("Ramp Load Complete", mission.ramp_load_completed_at_utc),
        ("Crew Load Complete", mission.crew_load_completed_at_utc),
        ("Official Block-Out", mission.actual_block_out_datetime_utc),
    )
    return tuple(label for label, value in required if value is None)


def _mutation_result(mission, field, *, changed, value, source):
    return {
        "changed": bool(changed),
        "field": field,
        "departure_status": _normalized_status(mission.departure_status),
        "value": value,
        "source": _normalized_source(source),
    }


def _normalized_status(value):
    return str(value or "").strip().lower()


def _normalized_source(value):
    return str(value or "").strip().lower()


def neorain_outbound_refresh_status(gateway, *, operation=None):
    """Apply the shared live-screen interval to the shared operational window policy."""
    status = dict(node_auto_refresh_status(gateway, operation=operation))
    setting = live_screen_refresh_value(gateway, NEORAIN_OUTBOUND_REFRESH_KEY)
    status["live_screen_refresh_interval_ms"] = setting.effective_interval_ms
    if not setting.enabled:
        status["auto_refresh_enabled"] = False
        status["reason"] = "disabled"
        status["message"] = "Live updates off"
        status["live_status_label"] = "Live updates off"
    return status


def neorain_outbound_revision(gateway, *, operation=_OPERATION_UNSET):
    """Compact revision for the persisted inputs rendered on the Outbound board."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    operation_id = operation.id if operation else None
    criterion = (
        SortDateMission.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateMission.sort_date_operation_id.is_(None)
    )
    parking_criterion = (
        SortDateParkingAssignment.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateParkingAssignment.sort_date_operation_id.is_(None)
    )
    aggregates = (
        _revision_aggregate(
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            criterion,
            SortDateMission.mission_type == "departure",
        ),
        _revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            parking_criterion,
        ),
    )
    rows = sorted(
        db.session.execute(union_all(*aggregates)).all(),
        key=lambda row: row.source,
    )
    payload = {
        "gateway_id": gateway.id,
        "operation_id": operation_id,
        "operation_updated_at": _revision_value(getattr(operation, "updated_at", None)),
        "inputs": [
            {
                "source": row.source,
                "row_count": int(row.row_count or 0),
                "max_id": int(row.max_id or 0),
                "id_sum": int(row.id_sum or 0),
                "latest_updated_at": _revision_value(row.latest_updated_at),
            }
            for row in rows
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _outbound_rows(operation):
    if operation is None:
        return []
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    missions = SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).all()
    rows = [_outbound_row(mission, operation, parking_by_tail) for mission in missions]
    return sorted(rows, key=_row_sort_key)


def neorain_outbound_row(mission, operation):
    """Format one updated mission with the same canonical Outbound presentation."""
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    row = _outbound_row(mission, operation, parking_by_tail)
    row.pop("sort_time", None)
    row["departure_status"] = _normalized_status(mission.departure_status)
    return row


def _outbound_row(mission, operation, parking_by_tail):
    timing = mission_display_timing_data(mission, operation)
    planned = timing.get("adjusted_planned_departure_time") or mission.planned_datetime_local
    timezone_name = mission.timezone or None
    status = str(mission.departure_status or "scheduled").strip().lower()
    return {
        "wave": timing.get("wave") or "-",
        "flight_number": _text(mission.flight_number),
        "tail": _text(mission.assigned_tail_number),
        "destination": _text(mission.destination),
        "parking": parking_by_tail.get(_tail_key(mission.assigned_tail_number), ""),
        "planned_time": _time_value(planned),
        "status": status.replace("_", " ").upper(),
        "elmac": format_local_hhmm(mission.elmac_completed_at_utc, timezone_name),
        "ramp_load_complete": format_local_hhmm(
            mission.ramp_load_completed_at_utc, timezone_name
        ),
        "crew_load_complete": format_local_hhmm(
            mission.crew_load_completed_at_utc, timezone_name
        ),
        "official_block_out": format_local_hhmm(
            mission.actual_block_out_datetime_utc, timezone_name
        ),
        "departure_variance": _departure_variance(mission),
        "no_return": "NO RETURN" if status == "departed" else "",
        "sort_time": planned,
        "mission_id": mission.id,
        "version": entity_version(mission),
    }


def _departure_variance(mission):
    """Return signed whole minutes from canonical STD to official Block-Out."""
    scheduled_departure = mission.planned_datetime_utc
    official_block_out = mission.actual_block_out_datetime_utc
    if scheduled_departure is None or official_block_out is None:
        return "-"
    minutes = int((official_block_out - scheduled_departure).total_seconds() / 60)
    return f"+{minutes}" if minutes > 0 else str(minutes)


def _revision_aggregate(source, model, timestamp_column, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _row_sort_key(row):
    return (
        row["sort_time"] is None,
        row["sort_time"] or datetime.max,
        row["flight_number"],
        row["mission_id"],
    )


def _tail_key(value):
    return _text(value)


def _text(value):
    return str(value or "").strip().upper()


def _time_value(value):
    return value.strftime("%H:%M") if value else ""


def _revision_value(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")
