"""Apply supplied Google MotherBrain live rows to a current sort operation."""

from __future__ import annotations

from datetime import datetime, time, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services.departure_progress import (
    recompute_departure_status_after_external_clear,
    repair_orphaned_external_departed_status,
)
from app.models import (
    SortDateGoogleMissionLink,
    SortDateMission,
    SortDateParkingAssignment,
    SortDateTailState,
)
from app.services.alp_import import alp_flight_key, normalize_alp_flight_number
from app.services.flight_rules import derive_aircraft_type_from_tail_number
from app.services.gateway_matrix import gateway_timezone
from app.services.google_motherbrain_parking import apply_google_motherbrain_parking
from app.services.night_sorting import sort_datetime_for_local_time
from app.services.parking_plan import (
    ParkingPlanError,
    TAIL_STATUS_NORMAL,
    TAIL_STATUS_SPARE,
    mark_arrival_tail_spare,
    set_tail_hot,
)
from app.services.sort_date_operations import (
    _planned_datetime_utc,
    create_default_crew_assignments_for_mission,
    ensure_tail_state_for_mission,
)


GOOGLE_MOTHERBRAIN_MISSION_SOURCE = "google_motherbrain"
GOOGLE_INBOUND_SHEET = "Inbound"
GOOGLE_OUTBOUND_SHEET = "Outbound"
GOOGLE_ARRIVAL_STATUS_RANK = {
    "scheduled": 0,
    "en_route": 1,
    "on_ground": 2,
    "arrived": 3,
    "unloaded": 4,
}
GOOGLE_ARRIVAL_STATUS_MAP = {
    "": "scheduled",
    "DEP": "en_route",
    "ON": "on_ground",
    "ARR": "arrived",
    "ARRIVED": "arrived",
    "CNL": "cancelled",
    "CANCELLED": "cancelled",
}
GOOGLE_SPECIAL_ARRIVAL_STATUSES = {"HERE", "HOT", "SPARE"}
GOOGLE_NATIVE_BLOCK_OUT_SOURCES = {
    "alp",
    "api",
    "manual",
    "neo",
    "neorain",
    "neo_rain",
    "rain",
    "google_rain",
}
_TAIL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")
_HHMM_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_MONTH_DAY_HHMM_PATTERN = re.compile(
    r"^(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})$"
)


class GoogleMotherBrainMissionError(ValueError):
    """A row-scoped live mission application error."""


def apply_google_motherbrain_live_rows(
    operation,
    *,
    inbound_rows=(),
    outbound_rows=(),
    user=None,
    now=None,
):
    """Apply supplied live rows without reading Google or committing the session."""
    _validate_operation(operation)
    applied_at = _utc_naive(now or datetime.utcnow())
    inbound = apply_google_motherbrain_live_mission_batch(
        operation,
        "arrival",
        inbound_rows,
        user=user,
        now=applied_at,
    )
    outbound = apply_google_motherbrain_live_mission_batch(
        operation,
        "departure",
        outbound_rows,
        user=user,
        now=applied_at,
    )
    return {
        "operation_id": operation.id,
        "inbound": inbound,
        "outbound": outbound,
        "applied_count": inbound["applied_count"] + outbound["applied_count"],
        "skipped_count": inbound["skipped_count"] + outbound["skipped_count"],
    }


def apply_google_motherbrain_live_mission_batch(
    operation,
    mission_type,
    rows,
    *,
    user=None,
    now=None,
):
    """Apply one direction in sheet-row order with per-row savepoint isolation."""
    _validate_operation(operation)
    mission_type = _normalize_mission_type(mission_type)
    applied_at = _utc_naive(now or datetime.utcnow())
    ordered_rows = sorted(
        list(rows or ()),
        key=lambda row: (_row_number((row or {}).get("sheet_row") or (row or {}).get("row_number")) or 10**9),
    )
    seen_flight_keys = set()
    results = []

    for batch_index, supplied_row in enumerate(ordered_rows, start=1):
        try:
            row = _normalize_live_row(supplied_row, mission_type, batch_index)
        except GoogleMotherBrainMissionError as error:
            row = _raw_row_context(supplied_row, mission_type, batch_index)
            results.append(_skipped_result(row, str(error)))
            continue
        flight_key = alp_flight_key(row["flight_number"])
        if flight_key and flight_key in seen_flight_keys:
            results.append(
                _skipped_result(row, "Duplicate Google flight row; first row wins.")
            )
            continue
        if flight_key:
            seen_flight_keys.add(flight_key)

        try:
            with db.session.begin_nested():
                result = _apply_live_row(
                    operation,
                    mission_type,
                    row,
                    user=user,
                    applied_at=applied_at,
                )
                db.session.flush()
        except (GoogleMotherBrainMissionError, ParkingPlanError, SQLAlchemyError) as error:
            result = _skipped_result(row, str(error) or "Live mission application failed.")
        results.append(result)

    applied_count = sum(result["status"] in {"applied", "preserved"} for result in results)
    return {
        "mission_type": mission_type,
        "results": results,
        "applied_count": applied_count,
        "skipped_count": len(results) - applied_count,
    }


def _apply_live_row(operation, mission_type, row, *, user, applied_at):
    if not row["sheet_row"]:
        raise GoogleMotherBrainMissionError("Google sheet row must be a positive integer.")
    if not row["effective_tail"]:
        raise GoogleMotherBrainMissionError("Effective tail is required.")

    link = _link_for_row(operation, mission_type, row)
    mission = _linked_mission(operation, mission_type, link)
    if mission is None and row["flight_number"]:
        matches = _matching_missions(operation, mission_type, row["flight_number"])
        if len(matches) > 1:
            raise GoogleMotherBrainMissionError(
                "Multiple current-sort missions share this flight number."
            )
        mission = matches[0] if matches else None

    if mission_type == "departure" and row["destination_mode"] == "spare":
        return _apply_spare_row(
            operation,
            row,
            link=link,
            mission=mission,
            user=user,
            applied_at=applied_at,
        )

    if not row["flight_number"]:
        raise GoogleMotherBrainMissionError("Flight number is required.")

    planned_local, planned_utc = _parse_optional_live_datetime(
        row["planned_time"], operation, "planned time"
    )
    operational_local, operational_utc = _parse_optional_live_datetime(
        row["operational_time"], operation, "operational time"
    )
    del operational_local

    created = mission is None
    if created:
        mission = _create_google_mission(
            operation,
            mission_type,
            row,
            planned_local,
            planned_utc,
        )
    else:
        mission.flight_number = row["flight_number"]

    link = link or _new_link(operation, mission_type, row)
    link.sort_date_mission = mission
    _update_link_snapshot(link, row, applied_at)

    if planned_local is not None:
        mission.planned_datetime_local = planned_local
        mission.planned_datetime_utc = planned_utc
        mission.planned_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE

    if mission_type == "arrival":
        warnings = _apply_inbound_state(
            operation,
            mission,
            link,
            row,
            operational_utc,
            user=user,
            applied_at=applied_at,
        )
    else:
        warnings = _apply_outbound_state(
            operation,
            mission,
            link,
            row,
            operational_utc,
            user=user,
            applied_at=applied_at,
        )

    db.session.flush()
    parking_result = _apply_row_parking(operation, row, user=user)
    return {
        "status": "applied",
        "reason": "",
        "mission_id": mission.id,
        "correlation_id": link.id,
        "created": created,
        "sheet": row["source_sheet"],
        "sheet_row": row["sheet_row"],
        "flight_number": mission.flight_number,
        "warnings": warnings,
        "parking": parking_result,
        "pending_tail_number": link.pending_tail_number,
    }


def _apply_spare_row(
    operation,
    row,
    *,
    link,
    mission,
    user,
    applied_at,
):
    link = link or _new_link(operation, "departure", row)
    _update_link_snapshot(link, row, applied_at)
    if mission is not None:
        link.sort_date_mission = mission
        parking_result = _apply_row_parking(operation, row, user=user)
        return {
            "status": "preserved",
            "reason": "Existing departure preserved; SPARE did not delete or transform it.",
            "mission_id": mission.id,
            "correlation_id": link.id,
            "created": False,
            "sheet": row["source_sheet"],
            "sheet_row": row["sheet_row"],
            "flight_number": mission.flight_number,
            "warnings": [],
            "parking": parking_result,
            "pending_tail_number": link.pending_tail_number,
        }

    tail_number = row["effective_tail"]
    departures = _missions_for_tail(operation, tail_number, "departure")
    if departures:
        raise GoogleMotherBrainMissionError(
            "A tail with a departure mission cannot be marked SPARE."
        )
    arrivals = _missions_for_tail(operation, tail_number, "arrival")
    if arrivals:
        mark_arrival_tail_spare(operation, tail_number, user=user)
    else:
        _ensure_standalone_google_spare(operation, tail_number)

    parking_result = _apply_row_parking(operation, row, user=user)
    return {
        "status": "applied",
        "reason": "",
        "mission_id": None,
        "correlation_id": link.id,
        "created": False,
        "sheet": row["source_sheet"],
        "sheet_row": row["sheet_row"],
        "flight_number": row["flight_number"],
        "warnings": [],
        "parking": parking_result,
        "pending_tail_number": None,
    }


def _create_google_mission(
    operation,
    mission_type,
    row,
    planned_local,
    planned_utc,
):
    if mission_type == "arrival":
        if not row["origin"]:
            raise GoogleMotherBrainMissionError("Inbound origin is required.")
        origin = row["origin"]
        destination = str(operation.gateway_code or "").strip().upper()
    else:
        if row["destination_mode"] == "normal" and not row["destination"]:
            raise GoogleMotherBrainMissionError("Outbound destination is required.")
        origin = str(operation.gateway_code or "").strip().upper()
        destination = "HOT" if row["destination_mode"] == "hot" else row["destination"]

    mission = SortDateMission(
        sort_date_operation=operation,
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        mission_type=mission_type,
        mission_source=GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
        flight_number=row["flight_number"],
        origin=origin,
        destination=destination,
        timezone=gateway_timezone(operation.gateway),
        planned_datetime_local=planned_local,
        planned_datetime_utc=planned_utc,
        planned_source=(
            GOOGLE_MOTHERBRAIN_MISSION_SOURCE if planned_local is not None else "unknown"
        ),
        assigned_tail_number=row["effective_tail"],
        tail_source=GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
        tail_updated_at=datetime.utcnow(),
        fuel_status="waiting",
        arrival_status="scheduled" if mission_type == "arrival" else None,
        departure_status="scheduled" if mission_type == "departure" else None,
    )
    db.session.add(mission)
    db.session.flush()
    tail_state = ensure_tail_state_for_mission(mission)
    create_default_crew_assignments_for_mission(
        mission,
        getattr(tail_state, "aircraft_type", None) or "unknown",
    )
    return mission


def _apply_inbound_state(
    operation,
    mission,
    link,
    row,
    operational_utc,
    *,
    user,
    applied_at,
):
    warnings = []
    if row["origin"]:
        mission.origin = row["origin"]
    _apply_effective_tail(mission, row["effective_tail"], applied_at)
    ensure_tail_state_for_mission(mission)

    raw_status = row["status_raw"]
    if raw_status in GOOGLE_SPECIAL_ARRIVAL_STATUSES:
        if raw_status == "HOT":
            set_tail_hot(operation, row["effective_tail"], True, user=user)
        elif raw_status == "SPARE":
            try:
                mark_arrival_tail_spare(operation, row["effective_tail"], user=user)
            except ParkingPlanError as error:
                warnings.append(str(error))
        return warnings

    proposed_status = GOOGLE_ARRIVAL_STATUS_MAP.get(raw_status)
    if proposed_status is None:
        warnings.append(f"Unsupported inbound status preserved: {raw_status}.")
        return warnings

    current_status = str(mission.arrival_status or "scheduled").strip().lower()
    mission.arrival_status = _arrival_status_after_update(current_status, proposed_status)

    if proposed_status == "cancelled":
        return warnings
    if proposed_status in {"en_route", "on_ground"} and operational_utc is not None:
        link.google_eta_datetime_utc = operational_utc
        if str(mission.eta_source or "").strip().lower() != "api":
            mission.eta_datetime_utc = operational_utc
            mission.eta_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
    elif proposed_status == "arrived" and operational_utc is not None:
        mission.actual_block_in_datetime_utc = operational_utc
        mission.actual_block_in_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
    return warnings


def _apply_outbound_state(
    operation,
    mission,
    link,
    row,
    operational_utc,
    *,
    user,
    applied_at,
):
    warnings = []
    old_tail = _normalize_tail(mission.assigned_tail_number)
    new_tail = row["effective_tail"]
    if old_tail and old_tail != new_tail:
        try:
            set_tail_hot(operation, old_tail, False, user=user)
        except ParkingPlanError:
            pass

    _apply_effective_tail(mission, new_tail, applied_at)
    ensure_tail_state_for_mission(mission)
    db.session.flush()

    if row["destination_mode"] == "hot":
        mission.destination = "HOT"
        set_tail_hot(operation, new_tail, True, user=user)
    else:
        mission.destination = row["destination"] or mission.destination
        set_tail_hot(operation, new_tail, False, user=user)

    if mission.departure_status is None:
        mission.departure_status = "scheduled"

    if _clear_future_google_block_out(mission, applied_at):
        warnings.append("Future Google block-out state cleared.")

    if operational_utc is None:
        if _clear_google_block_out(mission):
            warnings.append("Cleared Google block-out no longer present in the sheet.")
        elif repair_orphaned_external_departed_status(mission):
            warnings.append("Stale Google departure status repaired.")

    if operational_utc is not None:
        if operational_utc > applied_at:
            warnings.append("Future Google block-out ignored.")
        else:
            source = str(mission.actual_block_out_source or "unknown").strip().lower()
            has_native_authority = bool(
                mission.actual_block_out_datetime_utc
                and source in GOOGLE_NATIVE_BLOCK_OUT_SOURCES
            )
            if has_native_authority:
                warnings.append("Native Neo block-out preserved over Google block-out.")
            else:
                mission.actual_block_out_datetime_utc = operational_utc
                mission.actual_block_out_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
                mission.departure_status = "departed"

    _apply_pending_tail_swap(link, row)
    return warnings


def _clear_future_google_block_out(mission, applied_at):
    source = str(mission.actual_block_out_source or "unknown").strip().lower()
    block_out_utc = mission.actual_block_out_datetime_utc
    if (
        source != GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        or block_out_utc is None
        or _utc_naive(block_out_utc) <= applied_at
    ):
        return False

    mission.actual_block_out_datetime_utc = None
    mission.actual_block_out_source = "unknown"
    recompute_departure_status_after_external_clear(mission, "departed")
    return True


def _clear_google_block_out(mission):
    source = str(mission.actual_block_out_source or "unknown").strip().lower()
    if (
        source != GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        or mission.actual_block_out_datetime_utc is None
    ):
        return False

    mission.actual_block_out_datetime_utc = None
    mission.actual_block_out_source = "unknown"
    recompute_departure_status_after_external_clear(mission, "departed")
    return True


def _apply_pending_tail_swap(link, row):
    proposed = row["proposed_tail"]
    effective = row["effective_tail"]
    if proposed and proposed != effective:
        link.pending_tail_number = proposed
        link.pending_swap_flight_number = row["swap_flight_number"] or row["flight_number"]
        link.pending_swap_destination = row["swap_destination"] or row["destination"]
        link.pending_swap_acknowledgment = row["swap_acknowledgment"]
        return
    link.pending_tail_number = None
    link.pending_swap_flight_number = None
    link.pending_swap_destination = None
    link.pending_swap_acknowledgment = None


def _arrival_status_after_update(current_status, proposed_status):
    if current_status == "unloaded":
        return current_status
    if current_status == "cancelled":
        if proposed_status in {"en_route", "on_ground", "arrived"}:
            return proposed_status
        return current_status
    if proposed_status == "cancelled":
        return proposed_status
    current_rank = GOOGLE_ARRIVAL_STATUS_RANK.get(current_status, 0)
    proposed_rank = GOOGLE_ARRIVAL_STATUS_RANK.get(proposed_status, 0)
    return proposed_status if proposed_rank >= current_rank else current_status


def _apply_effective_tail(mission, tail_number, applied_at):
    mission.assigned_tail_number = tail_number
    mission.tail_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
    mission.tail_updated_at = applied_at


def _apply_row_parking(operation, row, *, user):
    if not str(row["parking_value"] or "").strip():
        return None
    return apply_google_motherbrain_parking(
        operation,
        row["effective_tail"],
        row["parking_value"],
        user=user,
        source_sheet=row["source_sheet"],
        source_row=row["sheet_row"],
    )


def _ensure_standalone_google_spare(operation, tail_number):
    state = SortDateTailState.query.filter_by(
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        tail_number=tail_number,
    ).first()
    if state is None:
        derived_type = derive_aircraft_type_from_tail_number(tail_number)
        state = SortDateTailState(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            tail_number=tail_number,
            aircraft_type=(None if derived_type == "unknown" else derived_type),
            aircraft_type_source=("unknown" if derived_type == "unknown" else "derived"),
        )
        db.session.add(state)
    state.operational_status = TAIL_STATUS_SPARE
    state.is_out_of_service = False
    assignment = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
        tail_number=tail_number,
    ).first()
    if assignment:
        assignment.is_hot = False
    db.session.flush()
    return state


def _link_for_row(operation, mission_type, row):
    return SortDateGoogleMissionLink.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
        source_sheet=row["source_sheet"],
        source_row=row["sheet_row"],
    ).first()


def _new_link(operation, mission_type, row):
    link = SortDateGoogleMissionLink(
        sort_date_operation=operation,
        mission_type=mission_type,
        source_sheet=row["source_sheet"],
        source_row=row["sheet_row"],
    )
    db.session.add(link)
    return link


def _linked_mission(operation, mission_type, link):
    if link is None or link.sort_date_mission_id is None:
        return None
    mission = db.session.get(SortDateMission, link.sort_date_mission_id)
    if (
        mission is None
        or mission.sort_date_operation_id != operation.id
        or mission.mission_type != mission_type
    ):
        link.sort_date_mission = None
        return None
    return mission


def _matching_missions(operation, mission_type, flight_number):
    target_key = alp_flight_key(flight_number)
    if not target_key:
        return []
    return [
        mission
        for mission in SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        ).all()
        if alp_flight_key(mission.flight_number) == target_key
    ]


def _missions_for_tail(operation, tail_number, mission_type):
    return SortDateMission.query.filter(
        SortDateMission.sort_date_operation_id == operation.id,
        SortDateMission.mission_type == mission_type,
        func.upper(SortDateMission.assigned_tail_number) == tail_number,
    ).all()


def _update_link_snapshot(link, row, applied_at):
    link.last_flight_number = row["flight_number"] or None
    link.last_tail_number = row["effective_tail"] or None
    link.last_status_raw = row["status_raw"] or None
    link.last_applied_at_utc = applied_at


def _normalize_live_row(supplied_row, mission_type, batch_index):
    row = dict(supplied_row or {})
    default_sheet = GOOGLE_INBOUND_SHEET if mission_type == "arrival" else GOOGLE_OUTBOUND_SHEET
    source_sheet = str(row.get("source_sheet") or row.get("sheet") or default_sheet).strip()
    source_sheet = default_sheet if source_sheet.lower() == default_sheet.lower() else source_sheet
    sheet_row = _row_number(row.get("sheet_row") or row.get("row_number"))
    raw_flight = row.get("flight_number", row.get("P", ""))
    raw_tail = row.get("effective_tail", row.get("tail_number", row.get("Q", "")))
    raw_airport = row.get(
        "origin" if mission_type == "arrival" else "destination",
        row.get("R", ""),
    )
    raw_parking = row.get("parking", row.get("current_parking", row.get("S", "")))
    raw_planned = row.get("planned_time", row.get("scheduled_time", row.get("T", "")))
    raw_operational = row.get("operational_time", row.get("U", ""))
    raw_status = row.get("status", row.get("W", "")) if mission_type == "arrival" else ""
    airport_text = str(raw_airport or "").strip().upper()
    destination_mode = "normal"
    if mission_type == "departure":
        if airport_text == "HOT":
            destination_mode = "hot"
        elif airport_text == "SPARE":
            destination_mode = "spare"
        elif airport_text in {"CANX", "CANCELLED", "CNL"}:
            raise GoogleMotherBrainMissionError(
                "Outbound cancellation is unsupported until its Google column is verified."
            )

    airport = _normalize_airport(airport_text)
    if airport_text and destination_mode == "normal" and not airport:
        raise GoogleMotherBrainMissionError(
            f"{'Origin' if mission_type == 'arrival' else 'Destination'} must be three letters."
        )

    return {
        "batch_index": batch_index,
        "source_sheet": source_sheet,
        "sheet_row": sheet_row,
        "flight_number": normalize_alp_flight_number(raw_flight),
        "effective_tail": _normalize_tail(raw_tail),
        "origin": airport if mission_type == "arrival" else "",
        "destination": airport if mission_type == "departure" and destination_mode == "normal" else "",
        "destination_mode": destination_mode,
        "parking_value": str(raw_parking or "").strip(),
        "planned_time": raw_planned,
        "operational_time": raw_operational,
        "status_raw": str(raw_status or "").strip().upper(),
        "swap_flight_number": normalize_alp_flight_number(
            row.get("swap_flight_number", row.get("W", ""))
        ) if mission_type == "departure" else None,
        "swap_destination": _normalize_airport(
            row.get("swap_destination", row.get("X", ""))
        ) if mission_type == "departure" else None,
        "proposed_tail": _normalize_tail(
            row.get("proposed_tail", row.get("new_tail", row.get("Y", "")))
        ) if mission_type == "departure" else None,
        "swap_acknowledgment": str(
            row.get("swap_acknowledgment", row.get("Z", "")) or ""
        ).strip().upper() if mission_type == "departure" else "",
    }


def _raw_row_context(supplied_row, mission_type, batch_index):
    row = dict(supplied_row or {})
    default_sheet = GOOGLE_INBOUND_SHEET if mission_type == "arrival" else GOOGLE_OUTBOUND_SHEET
    return {
        "batch_index": batch_index,
        "source_sheet": str(row.get("source_sheet") or row.get("sheet") or default_sheet).strip(),
        "sheet_row": _row_number(row.get("sheet_row") or row.get("row_number")),
        "flight_number": normalize_alp_flight_number(
            row.get("flight_number", row.get("P", ""))
        ),
    }


def _parse_optional_live_datetime(value, operation, field_name):
    if value is None or (
        isinstance(value, str)
        and value.strip() in {"", "-"}
    ):
        return None, None
    timezone_name = gateway_timezone(operation.gateway)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            utc_value = value.astimezone(timezone.utc).replace(tzinfo=None)
            try:
                local_value = value.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
            except ZoneInfoNotFoundError:
                local_value = value.replace(tzinfo=None)
            return local_value, utc_value
        local_value = value
        return local_value, _planned_datetime_utc(local_value, timezone_name)
    if isinstance(value, time):
        local_value = sort_datetime_for_local_time(
            operation.sort_date, operation.sort_name, value
        )
        return local_value, _planned_datetime_utc(local_value, timezone_name)

    text = str(value).strip()
    dated_match = _MONTH_DAY_HHMM_PATTERN.fullmatch(text)
    if dated_match:
        month, day, hour, minute = (
            int(part) for part in dated_match.groups()
        )
        if hour > 23 or minute > 59:
            raise GoogleMotherBrainMissionError(f"Invalid {field_name}: {text}.")
        local_value = _formatted_live_datetime(
            operation.sort_date,
            month,
            day,
            hour,
            minute,
            field_name,
            text,
        )
        return local_value, _planned_datetime_utc(local_value, timezone_name)

    match = _HHMM_PATTERN.fullmatch(text)
    if match:
        hour, minute = (int(part) for part in match.groups())
        if hour > 23 or minute > 59:
            raise GoogleMotherBrainMissionError(f"Invalid {field_name}: {text}.")
        local_value = sort_datetime_for_local_time(
            operation.sort_date,
            operation.sort_name,
            time(hour, minute),
        )
        return local_value, _planned_datetime_utc(local_value, timezone_name)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise GoogleMotherBrainMissionError(f"Invalid {field_name}: {text}.") from None
    return _parse_optional_live_datetime(parsed, operation, field_name)


def _formatted_live_datetime(
    sort_date,
    month,
    day,
    hour,
    minute,
    field_name,
    text,
):
    candidates = []
    for year in (sort_date.year - 1, sort_date.year, sort_date.year + 1):
        try:
            candidates.append(datetime(year, month, day, hour, minute))
        except ValueError:
            continue
    if not candidates:
        raise GoogleMotherBrainMissionError(f"Invalid {field_name}: {text}.")

    return min(
        candidates,
        key=lambda candidate: (
            abs((candidate.date() - sort_date).days),
            candidate.year != sort_date.year,
        ),
    )


def _normalize_mission_type(value):
    mission_type = str(value or "").strip().lower()
    if mission_type not in {"arrival", "departure"}:
        raise ValueError("Google live mission type must be arrival or departure.")
    return mission_type


def _normalize_tail(value):
    tail = str(value or "").strip().upper()
    return tail if _TAIL_PATTERN.fullmatch(tail) else ""


def _normalize_airport(value):
    airport = str(value or "").strip().upper()
    return airport if re.fullmatch(r"[A-Z]{3}", airport) else ""


def _row_number(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _validate_operation(operation):
    if operation is None or not getattr(operation, "id", None):
        raise ValueError("A persisted current sort operation is required.")
    if not getattr(operation, "gateway", None):
        raise ValueError("The current sort operation must belong to a gateway.")


def _skipped_result(row, reason):
    current_app.logger.warning(
        "Skipped Google MotherBrain live mission row sheet=%s row=%s flight=%s reason=%s",
        row.get("source_sheet") or "unknown",
        row.get("sheet_row") or row.get("batch_index") or "unknown",
        row.get("flight_number") or "missing",
        reason,
    )
    return {
        "status": "skipped",
        "reason": reason,
        "mission_id": None,
        "correlation_id": None,
        "created": False,
        "sheet": row.get("source_sheet"),
        "sheet_row": row.get("sheet_row"),
        "flight_number": row.get("flight_number"),
        "warnings": [],
        "parking": None,
        "pending_tail_number": None,
    }


def _utc_naive(value):
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
