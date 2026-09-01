"""Apply legacy Google Rain milestones to existing current-sort departures."""

from __future__ import annotations

from datetime import datetime

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import SortDateMission
from app.services.alp_import import alp_flight_key, normalize_alp_flight_number
from app.services.departure_progress import (
    recompute_departure_status_after_external_clear,
)
from app.services.google_motherbrain_live_missions import (
    GoogleMotherBrainMissionError,
    _parse_optional_live_datetime,
)


GOOGLE_RAIN_SOURCE = "google_rain"
GOOGLE_TO_NEO_AUTHORITY_HANDOFF = "google_to_neo"
NEO_TO_GOOGLE_AUTHORITY_HANDOFF = "neo_to_google"
_AUTHORITY_HANDOFFS = {
    GOOGLE_TO_NEO_AUTHORITY_HANDOFF,
    NEO_TO_GOOGLE_AUTHORITY_HANDOFF,
}
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
        "blocked_out",
    ),
)


def apply_google_rain_departure_milestones(
    operation,
    rows=(),
    now=None,
    *,
    authority_handoff=None,
):
    """Apply Rain rows without creating missions, changing tails, or committing."""
    del now
    _validate_operation(operation)
    if authority_handoff not in {None, *_AUTHORITY_HANDOFFS}:
        raise ValueError("Choose a valid Google Rain authority handoff.")
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
            missions,
        )
        if mission is None:
            result = _skipped_result(row, match_error)
            _log_skipped(operation, row, match_error)
            results.append(result)
            continue

        try:
            with db.session.begin_nested():
                result = _apply_row(
                    operation,
                    mission,
                    row,
                    authority_handoff=authority_handoff,
                )
                db.session.flush()
        except SQLAlchemyError as error:
            if authority_handoff is not None:
                raise
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


def _apply_row(operation, mission, row, *, authority_handoff=None):
    changed_fields = []
    warnings = []
    if authority_handoff is None and _relinquish_legacy_google_rain_elmac(mission):
        changed_fields.extend(("elmac_completed_at_utc", "elmac_completed_source"))
    replace_neorain_owned = authority_handoff == NEO_TO_GOOGLE_AUTHORITY_HANDOFF
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
            if authority_handoff is not None:
                raise
            warnings.append(str(error))
            continue

        previous_timestamp = getattr(mission, timestamp_attr)
        previous_source = _normalized_source(getattr(mission, source_attr))
        ownership = _apply_owned_timestamp(
            mission,
            timestamp_attr,
            source_attr,
            timestamp_utc,
            replace_neorain_owned=replace_neorain_owned,
        )
        if ownership == "protected":
            warnings.append(f"Protected {_field_label(row_key)} preserved.")
            continue
        if ownership == "changed":
            changed_fields.append(timestamp_attr)
            if (
                target_status
                and timestamp_utc is None
                and previous_timestamp is not None
                and _rain_handoff_owned_source(
                    previous_source,
                    include_neorain=replace_neorain_owned,
                )
                and recompute_departure_status_after_external_clear(
                    mission,
                    target_status,
                )
            ):
                changed_fields.append("departure_status")
        if timestamp_utc is not None and target_status:
            if _advance_departure_status(mission, target_status):
                changed_fields.append("departure_status")

    if row["no_return"] is not _MISSING:
        no_return = _google_checkbox_state(row["no_return"])
        if no_return is None:
            if authority_handoff is not None:
                raise ValueError("Rain No Return value is invalid.")
            warnings.append("Rain No Return value is invalid and was ignored.")
        elif no_return:
            missing = _missing_no_return_prerequisites(mission)
            if missing:
                if authority_handoff is not None:
                    raise ValueError(
                        "Rain No Return requires " + ", ".join(missing) + "."
                    )
                warnings.append(
                    "Rain No Return ignored: requires " + ", ".join(missing) + "."
                )
                if _clear_google_rain_no_return(
                    mission,
                    include_neorain=replace_neorain_owned,
                ):
                    changed_fields.append("departure_status")
            elif (
                replace_neorain_owned
                and _normalized_status(mission.departure_status) == "departed"
            ):
                departure_source = _normalized_source(
                    mission.departure_status_source
                )
                if _rain_handoff_owned_source(
                    departure_source,
                    include_neorain=True,
                ):
                    if departure_source != GOOGLE_RAIN_SOURCE:
                        mission.departure_status_source = GOOGLE_RAIN_SOURCE
                        changed_fields.append("departure_status")
                else:
                    warnings.append("Protected No Return preserved.")
            elif _advance_departure_status(
                mission,
                "departed",
                source=GOOGLE_RAIN_SOURCE,
            ):
                changed_fields.append("departure_status")
        elif _clear_google_rain_no_return(
            mission,
            include_neorain=replace_neorain_owned,
        ):
            changed_fields.append("departure_status")

    return {
        "status": "applied",
        "sheet_row": row["sheet_row"],
        "flight_number": row["flight_number"],
        "mission_id": mission.id,
        "changed_fields": sorted(set(changed_fields)),
        "warnings": warnings,
    }


def _apply_owned_timestamp(
    mission,
    timestamp_attr,
    source_attr,
    incoming_utc,
    *,
    replace_neorain_owned=False,
):
    current_timestamp = getattr(mission, timestamp_attr)
    current_source = _normalized_source(getattr(mission, source_attr))
    rain_owned = _rain_handoff_owned_source(
        current_source,
        include_neorain=replace_neorain_owned,
    )
    unowned_empty = current_timestamp is None and current_source in _UNOWNED_SOURCES
    if not rain_owned and not unowned_empty:
        return "protected"

    next_source = GOOGLE_RAIN_SOURCE if incoming_utc is not None else "unknown"
    if current_timestamp == incoming_utc and current_source == next_source:
        return "unchanged"
    setattr(mission, timestamp_attr, incoming_utc)
    setattr(mission, source_attr, next_source)
    return "changed"


def _advance_departure_status(mission, target_status, *, source=None):
    current_status = str(mission.departure_status or "").strip().lower()
    if current_status == "cancelled":
        return False
    current_rank = _DEPARTURE_STATUS_RANK.get(current_status, -1)
    target_rank = _DEPARTURE_STATUS_RANK[target_status]
    if current_rank >= target_rank:
        return False
    mission.departure_status = target_status
    if source is not None:
        mission.departure_status_source = source
    return True


def _clear_google_rain_no_return(mission, *, include_neorain=False):
    if (
        _normalized_status(mission.departure_status) != "departed"
        or not _rain_handoff_owned_source(
            _normalized_source(mission.departure_status_source),
            include_neorain=include_neorain,
        )
    ):
        return False
    if not recompute_departure_status_after_external_clear(mission, "departed"):
        return False
    mission.departure_status_source = "unknown"
    return True


def _rain_handoff_owned_source(source, *, include_neorain=False):
    return source == GOOGLE_RAIN_SOURCE or (include_neorain and source == "neorain")


def _relinquish_legacy_google_rain_elmac(mission):
    if _normalized_source(mission.elmac_completed_source) != GOOGLE_RAIN_SOURCE:
        return False
    mission.elmac_completed_at_utc = None
    mission.elmac_completed_source = "unknown"
    return True


def _missing_no_return_prerequisites(mission):
    required = (
        ("Ramp Load Complete", mission.ramp_load_completed_at_utc),
        ("Crew Load Complete", mission.crew_load_completed_at_utc),
        ("Official Block-Out", mission.actual_block_out_datetime_utc),
    )
    return tuple(label for label, value in required if value is None)


def _matching_departure(operation, row, candidates, all_departures):
    candidates = list(candidates)
    if not candidates:
        if not row["raw_flight_number"]:
            return None, "Rain flight number is missing or invalid."
        fallback_candidates, fallback_error = _destination_std_candidates(
            operation,
            row,
            all_departures,
        )
        if len(fallback_candidates) == 1:
            return fallback_candidates[0], None
        if len(fallback_candidates) > 1:
            return (
                None,
                "Rain flight format does not match canonical mission and "
                "destination/STD matches multiple current-sort departures.",
            )
        if fallback_error:
            return None, fallback_error
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


def _destination_std_candidates(operation, row, departures):
    """Return safe fallback candidates when Rain's flight label is noncanonical.

    Google Rain has no shared mission identifier. Its C/E values can only be used
    as a fallback when both identify exactly one existing current-sort departure;
    neither tail nor an inferred schedule value is used.
    """
    destination = row["destination"]
    std = row["std"]
    if not destination or std is _MISSING or str(std or "").strip() in {"", "-"}:
        return (), None
    try:
        std_local, _std_utc = _parse_optional_live_datetime(
            std,
            operation,
            "Rain STD",
        )
    except GoogleMotherBrainMissionError:
        return (), "Rain STD is invalid for destination/STD matching."
    if std_local is None:
        return (), None
    return (
        tuple(
            mission
            for mission in departures
            if str(mission.destination or "").strip().upper() == destination
            and _same_minute(mission.planned_datetime_local, std_local)
        ),
        None,
    )


def _normalize_row(supplied_row):
    supplied_row = dict(supplied_row or {})
    raw_flight_number = _first_value(
        supplied_row,
        "flight_number",
        "FLIGHT",
        "A",
    )
    flight_number = normalize_alp_flight_number(raw_flight_number)
    return {
        "sheet_row": _positive_int(supplied_row.get("sheet_row")),
        "flight_number": flight_number,
        "flight_key": alp_flight_key(flight_number),
        "raw_flight_number": str(raw_flight_number or "").strip(),
        "destination": str(
            _first_value(supplied_row, "destination", "DEST", "C") or ""
        ).strip().upper(),
        "std": _first_supplied(supplied_row, "std", "STD", "E"),
        "ramp_load_complete": _first_supplied(
            supplied_row, "ramp_load_complete", "r_lc", "R-LC", "M"
        ),
        "crew_load_complete": _first_supplied(
            supplied_row, "crew_load_complete", "c_lc", "C-LC", "N"
        ),
        "block": _first_supplied(supplied_row, "block", "BLOCK", "O"),
        "no_return": _first_supplied(
            supplied_row,
            "no_return",
            "NO_RETURN",
            "No Return",
            "S",
        ),
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
        "ramp_load_complete": "Rain Ramp Load Complete",
        "crew_load_complete": "Rain C-LC",
        "block": "Rain Official Block-Out",
        "no_return": "Rain No Return",
    }[row_key]


def _same_minute(left, right):
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    return left.replace(second=0, microsecond=0) == right.replace(
        second=0, microsecond=0
    )


def _normalized_source(value):
    return str(value or "unknown").strip().lower()


def _normalized_status(value):
    return str(value or "").strip().lower()


def _google_checkbox_state(value):
    if isinstance(value, bool):
        return value
    normalized = str(value if value is not None else "").strip().lower()
    if normalized in {"1", "true", "yes", "on", "checked"}:
        return True
    if normalized in {"", "-", "0", "false", "no", "off", "unchecked"}:
        return False
    return None


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
