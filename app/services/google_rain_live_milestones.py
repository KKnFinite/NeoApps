"""Apply legacy Google Rain milestones to existing current-sort departures."""

from __future__ import annotations

from datetime import datetime

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import SortDateMission
from app.services.alp_import import alp_flight_key, normalize_alp_flight_number
from app.services.google_motherbrain_live_missions import (
    GoogleMotherBrainMissionError,
    _parse_optional_live_datetime,
)


GOOGLE_RAIN_SOURCE = "google_rain"
_UNOWNED_SOURCES = {"", "unknown"}
_MISSING = object()
_DEPARTURE_STATUS_RANK = {
    "scheduled": 0,
    "loading": 1,
    "last_uld_enroute": 2,
    "ramp_load_complete": 3,
    "crew_load_complete": 4,
    "blocked_out": 5,
    "departed": 6,
}
_MILESTONE_SPECS = (
    (
        "elmac",
        "elmac_completed_at_utc",
        "elmac_completed_source",
        None,
    ),
    (
        "ramp_load_complete",
        "ramp_load_completed_at_utc",
        "ramp_load_completed_source",
        "ramp_load_complete",
    ),
    (
        "crew_load_complete",
        "crew_load_completed_at_utc",
        "crew_load_completed_source",
        "crew_load_complete",
    ),
    (
        "block",
        "actual_block_out_datetime_utc",
        "actual_block_out_source",
        "departed",
    ),
)


def apply_google_rain_departure_milestones(operation, rows=(), now=None):
    """Apply Rain rows without creating missions, changing tails, or committing."""
    del now
    _validate_operation(operation)
    missions = SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).all()
    missions_by_flight = {}
    for mission in missions:
        flight_key = alp_flight_key(mission.flight_number)
        if flight_key:
            missions_by_flight.setdefault(flight_key, []).append(mission)

    results = []
    ordered_rows = sorted(
        list(rows or ()),
        key=lambda row: _positive_int((row or {}).get("sheet_row")) or 10**9,
    )
    for supplied_row in ordered_rows:
        row = _normalize_row(supplied_row)
        mission, match_error = _matching_departure(
            operation,
            row,
            missions_by_flight.get(row["flight_key"], ()),
        )
        if mission is None:
            result = _skipped_result(row, match_error)
            _log_skipped(operation, row, match_error)
            results.append(result)
            continue

        try:
            with db.session.begin_nested():
                result = _apply_row(operation, mission, row)
                db.session.flush()
        except SQLAlchemyError as error:
            reason = type(error).__name__
            result = _skipped_result(row, reason)
            _log_skipped(operation, row, reason)
        results.append(result)

    applied_count = sum(result["status"] == "applied" for result in results)
    return {
        "operation_id": operation.id,
        "results": results,
        "applied_count": applied_count,
        "skipped_count": len(results) - applied_count,
    }


def _apply_row(operation, mission, row):
    changed_fields = []
    warnings = []
    for row_key, timestamp_attr, source_attr, target_status in _MILESTONE_SPECS:
        raw_value = row[row_key]
        if raw_value is _MISSING:
            continue
        try:
            _local_value, timestamp_utc = _parse_optional_live_datetime(
                raw_value,
                operation,
                _field_label(row_key),
            )
        except GoogleMotherBrainMissionError as error:
            warnings.append(str(error))
            continue

        ownership = _apply_owned_timestamp(
            mission,
            timestamp_attr,
            source_attr,
            timestamp_utc,
        )
        if ownership == "protected":
            warnings.append(f"Protected {_field_label(row_key)} preserved.")
            continue
        if ownership == "changed":
            changed_fields.append(timestamp_attr)
        if timestamp_utc is not None and target_status:
            if _advance_departure_status(mission, target_status):
                changed_fields.append("departure_status")

    return {
        "status": "applied",
        "sheet_row": row["sheet_row"],
        "flight_number": row["flight_number"],
        "mission_id": mission.id,
        "changed_fields": sorted(set(changed_fields)),
        "warnings": warnings,
    }


def _apply_owned_timestamp(mission, timestamp_attr, source_attr, incoming_utc):
    current_timestamp = getattr(mission, timestamp_attr)
    current_source = _normalized_source(getattr(mission, source_attr))
    rain_owned = current_source == GOOGLE_RAIN_SOURCE
    unowned_empty = current_timestamp is None and current_source in _UNOWNED_SOURCES
    if not rain_owned and not unowned_empty:
        return "protected"

    next_source = GOOGLE_RAIN_SOURCE if incoming_utc is not None else "unknown"
    if current_timestamp == incoming_utc and current_source == next_source:
        return "unchanged"
    setattr(mission, timestamp_attr, incoming_utc)
    setattr(mission, source_attr, next_source)
    return "changed"


def _advance_departure_status(mission, target_status):
    current_status = str(mission.departure_status or "").strip().lower()
    if current_status == "cancelled":
        return False
    current_rank = _DEPARTURE_STATUS_RANK.get(current_status, -1)
    target_rank = _DEPARTURE_STATUS_RANK[target_status]
    if current_rank >= target_rank:
        return False
    mission.departure_status = target_status
    return True


def _matching_departure(operation, row, candidates):
    if not row["flight_key"]:
        return None, "Rain flight number is missing or invalid."
    candidates = list(candidates)
    if not candidates:
        return None, "No current-sort departure matches the Rain flight."
    if len(candidates) == 1:
        return candidates[0], None

    destination = row["destination"]
    if destination:
        candidates = [
            mission
            for mission in candidates
            if str(mission.destination or "").strip().upper() == destination
        ]

    std = row["std"]
    if std is not _MISSING and str(std or "").strip() not in {"", "-"}:
        try:
            std_local, _std_utc = _parse_optional_live_datetime(
                std,
                operation,
                "Rain STD",
            )
        except GoogleMotherBrainMissionError:
            return None, "Rain STD is invalid for duplicate-flight matching."
        if std_local is not None:
            candidates = [
                mission
                for mission in candidates
                if _same_minute(mission.planned_datetime_local, std_local)
            ]

    if len(candidates) == 1:
        return candidates[0], None

    return None, "Rain flight matches multiple current-sort departures."


def _normalize_row(supplied_row):
    supplied_row = dict(supplied_row or {})
    flight_number = normalize_alp_flight_number(
        _first_value(supplied_row, "flight_number", "FLIGHT", "A")
    )
    return {
        "sheet_row": _positive_int(supplied_row.get("sheet_row")),
        "flight_number": flight_number,
        "flight_key": alp_flight_key(flight_number),
        "destination": str(
            _first_value(supplied_row, "destination", "DEST", "C") or ""
        ).strip().upper(),
        "std": _first_supplied(supplied_row, "std", "STD", "E"),
        "elmac": _first_supplied(supplied_row, "elmac", "eLMAC", "L"),
        "ramp_load_complete": _first_supplied(
            supplied_row, "ramp_load_complete", "r_lc", "R-LC", "M"
        ),
        "crew_load_complete": _first_supplied(
            supplied_row, "crew_load_complete", "c_lc", "C-LC", "N"
        ),
        "block": _first_supplied(supplied_row, "block", "BLOCK", "O"),
    }


def _first_value(row, *keys):
    value = _first_supplied(row, *keys)
    return None if value is _MISSING else value


def _first_supplied(row, *keys):
    for key in keys:
        if key in row:
            return row[key]
    return _MISSING


def _field_label(row_key):
    return {
        "elmac": "Rain eLMAC",
        "ramp_load_complete": "Rain R-LC",
        "crew_load_complete": "Rain C-LC",
        "block": "Rain BLOCK",
    }[row_key]


def _same_minute(left, right):
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    return left.replace(second=0, microsecond=0) == right.replace(
        second=0, microsecond=0
    )


def _normalized_source(value):
    return str(value or "unknown").strip().lower()


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _skipped_result(row, reason):
    return {
        "status": "skipped",
        "sheet_row": row["sheet_row"],
        "flight_number": row["flight_number"],
        "reason": reason,
    }


def _log_skipped(operation, row, reason):
    current_app.logger.warning(
        "Google Rain row skipped safely: operation_id=%s sheet_row=%s reason=%s",
        operation.id,
        row["sheet_row"],
        reason,
    )


def _validate_operation(operation):
    if operation is None or not getattr(operation, "id", None):
        raise ValueError("A persisted current sort operation is required.")
    if not getattr(operation, "gateway", None):
        raise ValueError("The current sort operation must belong to a gateway.")
