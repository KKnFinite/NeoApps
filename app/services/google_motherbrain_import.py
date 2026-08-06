"""Preview-only normalization for the locked RFD Google MotherBrain workbook."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import re
from types import SimpleNamespace

from sqlalchemy import func

from app.models import (
    Gateway,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
)
from app.services.alp_import import (
    _alp_utc_datetime,
    alp_flight_key,
    normalize_alp_flight_number,
)
from app.services.parking_physical_validator import validate_parking_physical_rules
from app.services.parking_plan import tail_rows_for_operation
from app.services.parking_rules import (
    VALID_PARKING_POSITIONS,
    normalize_parking_position_code,
)


GOOGLE_MOTHERBRAIN_SCHEMA_VERSION = 1
GOOGLE_MOTHERBRAIN_GATEWAY_CODE = "RFD"
GOOGLE_MOTHERBRAIN_SORT_NAME = "night"
GOOGLE_MOTHERBRAIN_TIMEZONE = "America/Chicago"

ARRIVAL_STATUS_ALIASES = {
    "SCH": "scheduled",
    "SCHEDULED": "scheduled",
    "EST": "en_route",
    "ESTIMATED": "en_route",
    "ENR": "en_route",
    "ARR": "arrived",
    "ARRIVED": "arrived",
    "CNL": "cancelled",
    "CANCELLED": "cancelled",
    "UNLOADED": "unloaded",
}
ARRIVAL_MARKER_STATUSES = {
    "S": "scheduled",
    "E": "en_route",
    "A": "arrived",
}
ARRIVAL_STATUS_RANK = {
    "scheduled": 0,
    "en_route": 1,
    "arrived": 2,
    "unloaded": 3,
}
TAIL_ACTIONS = {
    "HERE": "would_mark_here",
    "SPARE": "would_mark_spare",
    "HOT": "would_mark_hot",
}
# Deliberately explicit. Unrecognized nonblank values remain pending.
TAIL_SWAP_ACKNOWLEDGMENTS = frozenset(
    {
        "1",
        "ACK",
        "ACKNOWLEDGED",
        "APPROVED",
        "READY",
        "UNLOCK",
        "UNLOCKED",
        "TRUE",
        "X",
        "Y",
        "YES",
    }
)


class GoogleMotherBrainPayloadError(ValueError):
    """A stable, client-safe validation error."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


class GoogleMotherBrainOperationError(LookupError):
    """A stable operation-resolution error."""

    def __init__(self, code, message, status_code):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def validate_google_motherbrain_envelope(payload, configured_spreadsheet_id):
    if not isinstance(payload, dict):
        raise GoogleMotherBrainPayloadError(
            "invalid_payload",
            "The request body must be a JSON object.",
        )

    required_keys = {
        "schema_version",
        "spreadsheet_id",
        "spreadsheet_title",
        "gateway_code",
        "sort_name",
        "sort_date",
        "timezone",
        "submitted_at",
        "snapshot",
    }
    missing = sorted(required_keys - set(payload))
    if missing:
        raise GoogleMotherBrainPayloadError(
            "invalid_payload",
            f"Missing required field: {missing[0]}.",
        )

    if payload.get("schema_version") != GOOGLE_MOTHERBRAIN_SCHEMA_VERSION:
        raise GoogleMotherBrainPayloadError(
            "unsupported_schema_version",
            "Unsupported schema version.",
        )

    if str(payload.get("spreadsheet_id") or "") != str(configured_spreadsheet_id or ""):
        raise GoogleMotherBrainPayloadError(
            "invalid_spreadsheet",
            "The spreadsheet is not authorized for this integration.",
        )

    spreadsheet_title = str(payload.get("spreadsheet_title") or "").strip()
    if not spreadsheet_title:
        raise GoogleMotherBrainPayloadError(
            "invalid_payload",
            "spreadsheet_title is required.",
        )

    gateway_code = str(payload.get("gateway_code") or "").strip().upper()
    if gateway_code != GOOGLE_MOTHERBRAIN_GATEWAY_CODE:
        raise GoogleMotherBrainPayloadError(
            "invalid_gateway",
            "This integration accepts only the configured gateway.",
        )

    sort_name = str(payload.get("sort_name") or "").strip().lower()
    if sort_name != GOOGLE_MOTHERBRAIN_SORT_NAME:
        raise GoogleMotherBrainPayloadError(
            "invalid_sort",
            "This integration accepts only the configured sort.",
        )

    sort_date = _parse_iso_date(payload.get("sort_date"))
    timezone_name = str(payload.get("timezone") or "").strip()
    if timezone_name != GOOGLE_MOTHERBRAIN_TIMEZONE:
        raise GoogleMotherBrainPayloadError(
            "invalid_timezone",
            "This integration accepts only the configured timezone.",
        )

    submitted_at = _parse_submitted_at(payload.get("submitted_at"))
    snapshot = payload.get("snapshot")
    _validate_snapshot_structure(snapshot)

    return {
        "schema_version": GOOGLE_MOTHERBRAIN_SCHEMA_VERSION,
        "spreadsheet_id": str(configured_spreadsheet_id),
        "spreadsheet_title": spreadsheet_title,
        "gateway_code": gateway_code,
        "sort_name": sort_name,
        "sort_date": sort_date,
        "timezone": timezone_name,
        "submitted_at": submitted_at,
        "snapshot": snapshot,
    }


def resolve_google_motherbrain_operation(validated_envelope):
    operations = (
        SortDateOperation.query.filter(
            SortDateOperation.sort_date == validated_envelope["sort_date"],
            func.upper(SortDateOperation.gateway_code)
            == validated_envelope["gateway_code"],
            func.lower(SortDateOperation.sort_name) == validated_envelope["sort_name"],
            SortDateOperation.archived_at_utc.is_(None),
        )
        .order_by(SortDateOperation.id.asc())
        .all()
    )
    if not operations:
        raise GoogleMotherBrainOperationError(
            "operation_not_found",
            "No matching current-sort operation exists.",
            404,
        )
    if len(operations) != 1:
        raise GoogleMotherBrainOperationError(
            "operation_ambiguous",
            "More than one matching current-sort operation exists.",
            409,
        )
    return operations[0]


def build_google_motherbrain_preview(validated_envelope, operation):
    snapshot = validated_envelope["snapshot"]
    missions = (
        SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )
    arrivals = [mission for mission in missions if mission.mission_type == "arrival"]
    departures = [mission for mission in missions if mission.mission_type == "departure"]

    inbound = _preview_mission_section(
        "arrival",
        snapshot["inbound"],
        arrivals,
    )
    outbound = _preview_mission_section(
        "departure",
        snapshot["outbound"],
        departures,
    )
    tail_swaps = _preview_tail_swaps(
        snapshot["outbound"]["tail_swaps"],
        departures,
    )
    gateway = operation.gateway or Gateway.query.filter(
        func.upper(Gateway.code) == validated_envelope["gateway_code"]
    ).first()
    parking = _preview_parking(
        operation,
        gateway,
        snapshot["parking"]["assignments"],
    )

    warnings = []
    for section_name, section in (
        ("inbound", inbound),
        ("outbound", outbound),
        ("tail_swaps", tail_swaps),
        ("parking", parking),
    ):
        for warning in section.get("warnings", []):
            warnings.append({"section": section_name, **warning})

    return {
        "ok": True,
        "preview_only": True,
        "schema_version": GOOGLE_MOTHERBRAIN_SCHEMA_VERSION,
        "fingerprint": google_motherbrain_snapshot_fingerprint(snapshot),
        "operation": {
            "id": operation.id,
            "sort_date": operation.sort_date.isoformat(),
            "gateway_code": str(operation.gateway_code or "").upper(),
            "sort_name": str(operation.sort_name or "").lower(),
        },
        "summary": {
            "inbound": inbound["summary"],
            "outbound": outbound["summary"],
            "tail_swaps": tail_swaps["summary"],
            "parking": parking["summary"],
        },
        "sections": {
            "inbound": inbound,
            "outbound": outbound,
            "tail_swaps": tail_swaps,
            "parking": parking,
        },
        "warnings": warnings,
        "errors": [],
    }


def google_motherbrain_snapshot_fingerprint(snapshot):
    canonical = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_snapshot_structure(snapshot):
    if not isinstance(snapshot, dict):
        raise GoogleMotherBrainPayloadError(
            "invalid_payload",
            "snapshot must be an object.",
        )

    section_contracts = {
        "inbound": ("manual_rows", "alp_rows", "official_order"),
        "outbound": ("manual_rows", "alp_rows", "official_order", "tail_swaps"),
        "parking": ("assignments",),
    }
    for section_name, collection_names in section_contracts.items():
        section = snapshot.get(section_name)
        if not isinstance(section, dict):
            raise GoogleMotherBrainPayloadError(
                "invalid_payload",
                f"snapshot.{section_name} must be an object.",
            )
        for collection_name in collection_names:
            collection = section.get(collection_name)
            if not isinstance(collection, list):
                raise GoogleMotherBrainPayloadError(
                    "invalid_payload",
                    f"snapshot.{section_name}.{collection_name} must be a list.",
                )
            if collection_name == "official_order":
                if any(not isinstance(value, str) for value in collection):
                    raise GoogleMotherBrainPayloadError(
                        "invalid_payload",
                        f"snapshot.{section_name}.official_order must contain strings.",
                    )
            elif any(not isinstance(value, dict) for value in collection):
                raise GoogleMotherBrainPayloadError(
                    "invalid_payload",
                    f"snapshot.{section_name}.{collection_name} must contain objects.",
                )


def _parse_iso_date(value):
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise GoogleMotherBrainPayloadError(
            "invalid_sort_date",
            "sort_date must use YYYY-MM-DD.",
        ) from None
    if parsed.isoformat() != text:
        raise GoogleMotherBrainPayloadError(
            "invalid_sort_date",
            "sort_date must use YYYY-MM-DD.",
        )
    return parsed


def _parse_submitted_at(value):
    text = str(value or "").strip()
    if not text:
        raise GoogleMotherBrainPayloadError(
            "invalid_submitted_at",
            "submitted_at is required.",
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise GoogleMotherBrainPayloadError(
            "invalid_submitted_at",
            "submitted_at must be an ISO-8601 timestamp.",
        ) from None
    if parsed.tzinfo is None:
        raise GoogleMotherBrainPayloadError(
            "invalid_submitted_at",
            "submitted_at must include a timezone.",
        )
    return parsed


def _preview_mission_section(mission_type, section, missions):
    normalized_rows = [
        _normalize_alp_snapshot_row(row, mission_type)
        for row in section["alp_rows"]
    ]
    (
        manual_actions,
        manual_snapshot_rows,
        invalid_manual,
        manual_warnings,
    ) = _preview_manual_tail_rows(
        section["manual_rows"],
        mission_type,
    )
    valid_rows = [row for row in normalized_rows if not row.get("error")]
    invalid_rows = [_public_row(row) for row in normalized_rows if row.get("error")]
    invalid_rows.extend(invalid_manual)
    key_counts = Counter(row.get("flight_key") for row in valid_rows if row.get("flight_key"))
    duplicate_keys = {key for key, count in key_counts.items() if count > 1}
    missions_by_key = defaultdict(list)
    for mission in missions:
        key = alp_flight_key(mission.flight_number)
        if key:
            missions_by_key[key].append(mission)

    matched_rows = []
    unmatched_rows = []
    duplicate_rows = []
    present_keys = set()
    tail_changes = []
    timing_changes = []
    status_changes = []
    conflicts = []
    warnings = list(manual_warnings)

    for row in valid_rows:
        key = row["flight_key"]
        present_keys.add(key)
        if key in duplicate_keys:
            duplicate_rows.append(
                {**_public_row(row), "reason": "Duplicate Google ALP flight row."}
            )
            continue
        matches = missions_by_key.get(key, [])
        if not matches:
            unmatched_rows.append(
                {**_public_row(row), "reason": "No current operation mission match."}
            )
            continue
        if len(matches) > 1:
            unmatched_rows.append(
                {**_public_row(row), "reason": "Ambiguous current operation mission match."}
            )
            continue

        matched = _matched_snapshot_row(row, matches[0], mission_type)
        matched_rows.append(matched)
        if matched.get("tail_change"):
            tail_changes.append(matched["tail_change"])
        if matched.get("timing_change"):
            timing_changes.append(matched["timing_change"])
        if matched.get("status_change"):
            status_changes.append(matched["status_change"])
        if matched.get("conflict"):
            conflicts.append(matched["conflict"])
        for warning in matched.get("warnings", []):
            warnings.append(warning)

    missing_missions = [
        _mission_identity(mission)
        for mission in missions
        if alp_flight_key(mission.flight_number) not in present_keys
    ]
    official_order = [
        normalize_alp_flight_number(value) or str(value or "").strip().upper()
        for value in section["official_order"]
    ]

    return {
        "matched_rows": matched_rows,
        "unmatched_google_rows": unmatched_rows,
        "missing_current_missions": missing_missions,
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,
        "standalone_tail_actions": manual_actions,
        "manual_snapshot_rows": manual_snapshot_rows,
        "tail_changes": tail_changes,
        "timing_changes": timing_changes,
        "status_changes": status_changes,
        "conflicts": conflicts,
        "official_order": official_order,
        "warnings": warnings,
        "summary": {
            "received": len(section["alp_rows"]) + len(section["manual_rows"]),
            "matched": len(matched_rows),
            "unmatched": len(unmatched_rows),
            "missing": len(missing_missions),
            "duplicates": len(duplicate_rows),
            "invalid": len(invalid_rows),
            "standalone_tail_actions": len(manual_actions),
            "manual_snapshot_rows": len(manual_snapshot_rows),
            "tail_changes": len(tail_changes),
            "timing_changes": len(timing_changes),
            "status_changes": len(status_changes),
            "conflicts": len(conflicts),
        },
    }


def _normalize_alp_snapshot_row(row, mission_type):
    sheet_row = _sheet_row(row.get("sheet_row"))
    normalized_flight = normalize_alp_flight_number(row.get("flight_number"))
    flight_key = alp_flight_key(row.get("flight_number"))
    airport_field = "origin" if mission_type == "arrival" else "destination"
    airport = _normalize_code(row.get(airport_field))
    tail_number = _normalize_tail(row.get("tail_number"))
    status_raw = _clean_nullable(row.get("status"))
    time_raw = _clean_nullable(row.get("time"))
    marker = _time_marker(time_raw)
    cancelled = str(status_raw or "").upper() in {"CNL", "CANCELLED"}
    proposed_status = _proposed_status(mission_type, status_raw, marker)
    warnings = []

    normalized = {
        "sheet_row": sheet_row,
        "date": _clean_nullable(row.get("date")),
        "flight_number": normalized_flight,
        "flight_key": flight_key,
        airport_field: airport,
        "tail_number": tail_number,
        "parking": _normalize_optional_parking(row.get("parking")),
        "status_raw": status_raw,
        "time_raw": time_raw,
        "time_marker": marker,
        "proposed_status": proposed_status,
        "warnings": warnings,
    }
    if sheet_row is None:
        normalized["error"] = "sheet_row must be a positive integer."
        return normalized
    if not flight_key:
        normalized["error"] = "Flight number is required."
        return normalized
    if airport and not re.fullmatch(r"[A-Z]{3}", airport):
        normalized["error"] = f"{airport_field} must be exactly three letters."
        return normalized
    if not tail_number and not cancelled:
        normalized["error"] = "Tail is required unless the row is cancelled."
        return normalized
    if status_raw and proposed_status is None:
        warnings.append(
            {
                "sheet_row": sheet_row,
                "message": "Status was preserved as raw information and was not mapped.",
            }
        )

    if time_raw:
        try:
            utc_value = _row_utc_datetime(row.get("date"), time_raw)
        except ValueError as exc:
            normalized["error"] = str(exc)
            return normalized
        normalized["_utc_datetime_value"] = utc_value
        normalized["utc_datetime"] = _iso_utc(utc_value)
    elif not cancelled:
        normalized["error"] = "Time is required unless the row is cancelled."
        return normalized

    return normalized


def _matched_snapshot_row(row, mission, mission_type):
    public = _public_row(row)
    current_tail = _normalize_tail(mission.assigned_tail_number)
    proposed_tail = row.get("tail_number")
    tail_change = None
    if proposed_tail and proposed_tail != current_tail:
        tail_change = {
            "sheet_row": row["sheet_row"],
            "mission_id": mission.id,
            "flight_number": mission.flight_number,
            "current_tail": current_tail or None,
            "proposed_tail": proposed_tail,
        }

    timing_change = _mission_timing_change(row, mission, mission_type)
    status_change, conflict = _mission_status_change(row, mission, mission_type)
    return {
        **public,
        "mission": _mission_identity(mission),
        "tail_change": tail_change,
        "timing_change": timing_change,
        "status_change": status_change,
        "conflict": conflict,
        "warnings": public.get("warnings", []),
    }


def _mission_timing_change(row, mission, mission_type):
    proposed = row.get("_utc_datetime_value")
    if not proposed:
        return None
    if mission_type == "arrival":
        if row.get("proposed_status") in {"arrived", "unloaded"}:
            field = "actual_block_in_datetime_utc"
        else:
            field = "eta_datetime_utc"
    else:
        field = "actual_block_out_datetime_utc"
    current = getattr(mission, field, None)
    if _same_minute(current, proposed):
        return None
    return {
        "sheet_row": row["sheet_row"],
        "mission_id": mission.id,
        "flight_number": mission.flight_number,
        "field": field,
        "current": _iso_utc(current),
        "proposed": _iso_utc(proposed),
    }


def _mission_status_change(row, mission, mission_type):
    proposed = row.get("proposed_status")
    if not proposed:
        return None, None
    field = "arrival_status" if mission_type == "arrival" else "departure_status"
    current = getattr(mission, field, None)
    if mission_type == "arrival" and _arrival_status_is_downgrade(current, proposed):
        return None, {
            "sheet_row": row["sheet_row"],
            "mission_id": mission.id,
            "flight_number": mission.flight_number,
            "code": "arrival_status_downgrade",
            "message": f"Arrival status cannot be downgraded from {current} to {proposed}.",
        }
    if current == proposed:
        return None, None
    return {
        "sheet_row": row["sheet_row"],
        "mission_id": mission.id,
        "flight_number": mission.flight_number,
        "field": field,
        "current": current,
        "proposed": proposed,
    }, None


def _preview_manual_tail_rows(rows, mission_type):
    actions = []
    snapshot_rows = []
    invalid = []
    warnings = []
    for row in rows:
        sheet_row = _sheet_row(row.get("sheet_row"))
        tail = _normalize_tail(row.get("tail_number"))
        status = str(row.get("status") or "").strip().upper()
        airport_value = (
            row.get("origin") if mission_type == "arrival" else row.get("destination")
        )
        airport = _normalize_code(airport_value)
        action = TAIL_ACTIONS.get(status) or TAIL_ACTIONS.get(airport or "")
        public = {
            "sheet_row": sheet_row,
            "date": _clean_nullable(row.get("date")),
            "flight_number": normalize_alp_flight_number(row.get("flight_number")),
            "tail_number": tail,
            "parking": _normalize_optional_parking(row.get("parking")),
            "status_raw": _clean_nullable(row.get("status")),
            "time_raw": _clean_nullable(row.get("time")),
        }
        if mission_type == "arrival":
            public["origin"] = airport
        else:
            public["destination"] = airport
        if sheet_row is None:
            invalid.append({**public, "error": "sheet_row must be a positive integer."})
            continue
        if not tail:
            invalid.append({**public, "error": "Tail is required for a manual tail action."})
            continue
        if not action:
            if not public["flight_number"]:
                invalid.append(
                    {
                        **public,
                        "error": "Manual row requires a flight number or HERE/SPARE/HOT action.",
                    }
                )
                continue
            snapshot_rows.append(
                {
                    **public,
                    "action": None,
                    "creates_mission": False,
                }
            )
            continue
        actions.append(
            {
                **public,
                "action": action,
                "creates_mission": False,
            }
        )
    return actions, snapshot_rows, invalid, warnings


def _preview_tail_swaps(rows, departure_missions):
    missions_by_key = defaultdict(list)
    for mission in departure_missions:
        key = alp_flight_key(mission.flight_number)
        if key:
            missions_by_key[key].append(mission)

    items = []
    invalid = []
    warnings = []
    for row in rows:
        sheet_row = _sheet_row(row.get("sheet_row"))
        flight_number = normalize_alp_flight_number(row.get("flight_number"))
        flight_key = alp_flight_key(row.get("flight_number"))
        new_tail = _normalize_tail(row.get("new_tail"))
        destination = _normalize_code(row.get("destination"))
        raw_acknowledgment = _clean_nullable(row.get("scorpion_unlock"))
        acknowledgment = str(raw_acknowledgment or "").strip().upper()
        base = {
            "sheet_row": sheet_row,
            "flight_number": flight_number,
            "destination": destination,
            "proposed_new_tail": new_tail,
            "scorpion_unlock": raw_acknowledgment,
        }
        if sheet_row is None or not flight_key or not new_tail:
            invalid.append(
                {
                    **base,
                    "error": "sheet_row, flight_number, and new_tail are required.",
                }
            )
            continue
        matches = missions_by_key.get(flight_key, [])
        if len(matches) != 1:
            invalid.append(
                {
                    **base,
                    "error": (
                        "No current departure match."
                        if not matches
                        else "Ambiguous current departure match."
                    ),
                }
            )
            continue
        mission = matches[0]
        state = (
            "ready_to_finalize"
            if acknowledgment in TAIL_SWAP_ACKNOWLEDGMENTS
            else "pending"
        )
        item = {
            **base,
            "mission_id": mission.id,
            "current_tail": _normalize_tail(mission.assigned_tail_number) or None,
            "acknowledgment_state": state,
            "would_finalize": False,
        }
        items.append(item)
        if acknowledgment and state == "pending":
            warnings.append(
                {
                    "sheet_row": sheet_row,
                    "message": "Unrecognized Scorpion acknowledgment; tail swap remains pending.",
                }
            )

    return {
        "items": items,
        "invalid_rows": invalid,
        "warnings": warnings,
        "summary": {
            "received": len(rows),
            "pending": sum(item["acknowledgment_state"] == "pending" for item in items),
            "ready_to_finalize": sum(
                item["acknowledgment_state"] == "ready_to_finalize" for item in items
            ),
            "invalid": len(invalid),
        },
    }


def _preview_parking(operation, gateway, rows):
    normalized = [_normalize_parking_assignment(row) for row in rows]
    invalid_tails = [row for row in normalized if row.get("error_code") == "invalid_tail"]
    invalid_positions = [
        row for row in normalized if row.get("error_code") == "invalid_position"
    ]
    structurally_valid = [row for row in normalized if not row.get("error")]
    tail_counts = Counter(row["tail_number"] for row in structurally_valid)
    slot_counts = Counter(
        (row["position"], row["lane_number"]) for row in structurally_valid
    )
    duplicate_tail_placements = [
        row for row in structurally_valid if tail_counts[row["tail_number"]] > 1
    ]
    duplicate_position_occupancy = [
        row
        for row in structurally_valid
        if slot_counts[(row["position"], row["lane_number"])] > 1
    ]

    tail_rows = tail_rows_for_operation(gateway, operation) if gateway else []
    known_tails = {row["tail"] for row in tail_rows}
    unknown_tails = [
        row
        for row in structurally_valid
        if row["tail_number"] not in known_tails
    ]
    valid = [
        row
        for row in structurally_valid
        if tail_counts[row["tail_number"]] == 1
        and slot_counts[(row["position"], row["lane_number"])] == 1
        and row["tail_number"] in known_tails
    ]

    current_rows = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    current_by_tail = {
        _normalize_tail(row.tail_number): row
        for row in current_rows
        if row.position_code and row.lane_number
    }
    incoming_by_tail = {row["tail_number"]: row for row in valid}
    removed = []
    added = []
    moved = []
    unchanged = []
    for tail, current in current_by_tail.items():
        incoming = incoming_by_tail.get(tail)
        if not incoming:
            removed.append(_current_parking_identity(current))
            continue
        current_slot = (
            normalize_parking_position_code(current.position_code),
            int(current.lane_number),
        )
        incoming_slot = (incoming["position"], incoming["lane_number"])
        if current_slot == incoming_slot:
            unchanged.append(incoming)
        else:
            moved.append(
                {
                    "tail_number": tail,
                    "current_position": current_slot[0],
                    "current_lane_number": current_slot[1],
                    "proposed_position": incoming_slot[0],
                    "proposed_lane_number": incoming_slot[1],
                }
            )
    for tail, incoming in incoming_by_tail.items():
        if tail not in current_by_tail:
            added.append(incoming)

    transient_assignments = [
        SimpleNamespace(
            sort_date_operation_id=operation.id,
            tail_number=row["tail_number"],
            ramp_code=row["position"][0],
            position_code=row["position"],
            lane_number=row["lane_number"],
        )
        for row in valid
    ]
    physical_conflicts = [
        conflict.__dict__
        for conflict in validate_parking_physical_rules(
            operation,
            tail_rows=tail_rows,
            assignments=transient_assignments,
        )
    ]
    safe_to_apply_atomically = not any(
        (
            invalid_tails,
            invalid_positions,
            duplicate_tail_placements,
            duplicate_position_occupancy,
            unknown_tails,
            physical_conflicts,
        )
    )

    return {
        "received_assignments": normalized,
        "valid_normalized_assignments": valid,
        "duplicate_tail_placements": duplicate_tail_placements,
        "duplicate_position_lane_occupancy": duplicate_position_occupancy,
        "invalid_tails": invalid_tails,
        "invalid_positions": invalid_positions,
        "tails_not_known_to_current_sort": unknown_tails,
        "assignments_to_remove": removed,
        "assignments_to_add": added,
        "assignments_to_move": moved,
        "assignments_unchanged": unchanged,
        "physical_conflicts": physical_conflicts,
        "safe_to_apply_atomically": safe_to_apply_atomically,
        "warnings": [],
        "summary": {
            "received": len(rows),
            "valid": len(valid),
            "duplicate_tails": len(duplicate_tail_placements),
            "duplicate_slots": len(duplicate_position_occupancy),
            "invalid_tails": len(invalid_tails),
            "invalid_positions": len(invalid_positions),
            "unknown_tails": len(unknown_tails),
            "remove": len(removed),
            "add": len(added),
            "move": len(moved),
            "unchanged": len(unchanged),
            "physical_conflicts": len(physical_conflicts),
            "safe_to_apply_atomically": safe_to_apply_atomically,
        },
    }


def _normalize_parking_assignment(row):
    tail = _normalize_tail(row.get("tail_number"))
    raw_position = str(row.get("position") or "").strip().upper().replace(" ", "")
    lane_number = 2 if raw_position.endswith("-B") else 1
    position_source = raw_position[:-2] if lane_number == 2 else raw_position
    position = normalize_parking_position_code(position_source)
    result = {
        "tail_number": tail,
        "position": position or None,
        "lane_number": lane_number,
        "source_position": raw_position or None,
    }
    if not tail:
        return {**result, "error_code": "invalid_tail", "error": "Tail is required."}
    if position not in VALID_PARKING_POSITIONS:
        return {
            **result,
            "error_code": "invalid_position",
            "error": "Parking position is invalid.",
        }
    return result


def _current_parking_identity(assignment):
    return {
        "tail_number": _normalize_tail(assignment.tail_number),
        "position": normalize_parking_position_code(assignment.position_code),
        "lane_number": int(assignment.lane_number),
    }


def _proposed_status(mission_type, status_raw, marker):
    explicit = str(status_raw or "").strip().upper()
    if mission_type == "arrival":
        if explicit:
            return ARRIVAL_STATUS_ALIASES.get(explicit)
        return ARRIVAL_MARKER_STATUSES.get(marker)
    if explicit in {"CNL", "CANCELLED"}:
        return "cancelled"
    return None


def _arrival_status_is_downgrade(current, proposed):
    if proposed == "cancelled" or not current:
        return False
    if current not in ARRIVAL_STATUS_RANK or proposed not in ARRIVAL_STATUS_RANK:
        return False
    return ARRIVAL_STATUS_RANK[proposed] < ARRIVAL_STATUS_RANK[current]


def _row_utc_datetime(date_value, time_value):
    text = str(date_value or "").strip()
    try:
        row_date = date.fromisoformat(text)
    except ValueError:
        raise ValueError("Row date must use YYYY-MM-DD.") from None
    alp_date = row_date.strftime("%d-%b-%Y").upper()
    return _alp_utc_datetime(alp_date, time_value)


def _time_marker(value):
    match = re.search(r"\(([A-Za-z])\)\s*$", str(value or "").strip())
    return match.group(1).upper() if match else None


def _normalize_optional_parking(value):
    text = _clean_nullable(value)
    if not text:
        return None
    raw = str(text).upper().replace(" ", "")
    lane = 2 if raw.endswith("-B") else 1
    source = raw[:-2] if lane == 2 else raw
    position = normalize_parking_position_code(source)
    return {
        "position": position,
        "lane_number": lane,
        "source": raw,
    }


def _sheet_row(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_tail(value):
    return re.sub(r"\s+", "", str(value or "").upper()) or None


def _normalize_code(value):
    return re.sub(r"\s+", "", str(value or "").upper()) or None


def _clean_nullable(value):
    text = str(value or "").strip()
    return text or None


def _public_row(row):
    return {key: value for key, value in row.items() if not key.startswith("_")}


def _mission_identity(mission):
    return {
        "id": mission.id,
        "flight_number": mission.flight_number,
        "tail_number": _normalize_tail(mission.assigned_tail_number),
        "origin": _normalize_code(mission.origin),
        "destination": _normalize_code(mission.destination),
    }


def _same_minute(left, right):
    if not left or not right:
        return False
    return left.replace(second=0, microsecond=0) == right.replace(second=0, microsecond=0)


def _iso_utc(value):
    if not value:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"
