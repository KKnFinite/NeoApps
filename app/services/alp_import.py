from collections import Counter, defaultdict
from datetime import datetime
import re

from sqlalchemy import func

from app.extensions import db
from app.models import SortDateMission
from app.services.flight_api import _utc_to_local_naive as utc_to_local_naive
from app.services.sort_date_operations import ensure_tail_state_for_mission


ALP_MISSION_TYPES = {"arrival", "departure"}
ALP_TIMEZONE = "America/Chicago"


def normalize_alp_flight_number(value):
    key = alp_flight_key(value)
    return f"UPS{key}" if key else None


def alp_flight_key(value):
    text = str(value or "").strip().upper()
    if not text:
        return None
    text = re.sub(r"\s+", "", text)
    for prefix in ("UPS", "5X"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    try:
        return f"{int(digits):04d}"
    except ValueError:
        return None


def preview_alp_paste(operation, mission_type, paste_text, timezone_name=ALP_TIMEZONE):
    mission_type = _validate_mission_type(mission_type)
    parsed_rows = _parse_alp_rows(paste_text or "", timezone_name=timezone_name)
    valid_rows = [row for row in parsed_rows if not row.get("error")]
    key_counts = Counter(row["flight_key"] for row in valid_rows if row.get("flight_key"))
    duplicate_keys = {key for key, count in key_counts.items() if count > 1}

    missions = _missions_for_operation(operation, mission_type)
    missions_by_key = _missions_by_key(missions)
    matched_rows = []
    unmatched_rows = []
    duplicate_rows = []
    suppressed_hot_rows = []
    invalid_rows = [row for row in parsed_rows if row.get("error")]
    present_keys = {row["flight_key"] for row in valid_rows if row.get("flight_key")}

    for row in valid_rows:
        if row["flight_key"] in duplicate_keys:
            duplicate_rows.append({**row, "reason": "Duplicate ALP flight in paste."})
            continue

        matches = missions_by_key.get(row["flight_key"], [])
        if len(matches) == 1:
            mission = matches[0]
            matched_rows.append(_matched_row(row, mission))
        elif len(matches) > 1:
            unmatched_rows.append(
                {**row, "reason": "Multiple current operation missions share this flight."}
            )
        else:
            if _should_suppress_hot_positioning_row(operation, row):
                suppressed_hot_rows.append(
                    {
                        **row,
                        "reason": "9xxx HOT positioning row linked to normal outbound mission.",
                    }
                )
                continue
            unmatched_rows.append({**row, "reason": "No current operation mission match."})

    missing_missions = [
        _missing_mission_row(mission)
        for mission in missions
        if alp_flight_key(mission.flight_number) not in present_keys
    ]

    return {
        "mission_type": mission_type,
        "label": "Arrival" if mission_type == "arrival" else "Departure",
        "paste_text": paste_text or "",
        "matched_rows": matched_rows,
        "unmatched_rows": unmatched_rows,
        "missing_missions": missing_missions,
        "duplicate_rows": duplicate_rows,
        "invalid_rows": invalid_rows,
        "suppressed_hot_rows": suppressed_hot_rows,
        "summary": {
            "matched": len(matched_rows),
            "unmatched": len(unmatched_rows),
            "missing": len(missing_missions),
            "duplicates": len(duplicate_rows),
            "invalid": len(invalid_rows),
            "suppressed_hot": len(suppressed_hot_rows),
        },
    }


def apply_alp_paste(operation, mission_type, paste_text, user=None, timezone_name=ALP_TIMEZONE):
    preview = preview_alp_paste(
        operation,
        mission_type,
        paste_text,
        timezone_name=timezone_name,
    )
    now_utc = datetime.utcnow()
    applied_rows = []

    for row in preview["matched_rows"]:
        mission = db.session.get(SortDateMission, row["mission_id"])
        if not mission or mission.sort_date_operation_id != operation.id:
            continue
        if mission.mission_type != preview["mission_type"]:
            continue

        mission.assigned_tail_number = row["tail_number"]
        mission.tail_source = "alp"
        mission.tail_updated_at = now_utc

        if mission.mission_type == "arrival":
            mission.eta_datetime_utc = row["utc_datetime"]
            mission.eta_source = "alp"
        else:
            mission.actual_block_out_datetime_utc = row["utc_datetime"]
            mission.actual_block_out_source = "alp"

        ensure_tail_state_for_mission(mission)
        applied_rows.append(row)

    db.session.commit()
    preview["applied_rows"] = applied_rows
    preview["applied_count"] = len(applied_rows)
    preview["applied_by"] = getattr(user, "username", None)
    return preview


def _validate_mission_type(mission_type):
    mission_type = str(mission_type or "").strip().lower()
    if mission_type not in ALP_MISSION_TYPES:
        raise ValueError("ALP import mission type must be arrival or departure.")
    return mission_type


def _parse_alp_rows(paste_text, timezone_name=ALP_TIMEZONE):
    rows = []
    for line_number, raw_line in enumerate((paste_text or "").splitlines(), start=1):
        if not raw_line.strip():
            continue
        columns = _split_alp_line(raw_line)
        if _is_header_row(columns):
            continue
        if len(columns) < 7:
            rows.append(
                {
                    "line_number": line_number,
                    "raw": raw_line,
                    "error": "Expected 7 ALP columns.",
                }
            )
            continue

        rows.append(_parse_alp_row(columns, line_number, raw_line, timezone_name))
    return rows


def _split_alp_line(line):
    if "\t" in line:
        return [column.strip() for column in line.split("\t")]
    return [column.strip() for column in re.split(r"\s{2,}", line.strip())]


def _is_header_row(columns):
    if len(columns) < 2:
        return False
    return columns[0].strip().lower() == "date" and columns[1].strip().lower() == "flight"


def _parse_alp_row(columns, line_number, raw_line, timezone_name):
    date_text = columns[0].strip()
    flight_text = columns[1].strip()
    airport = columns[2].strip().upper()
    tail_number = columns[3].strip().upper()
    time_text = columns[6].strip()
    flight_key = alp_flight_key(flight_text)

    base = {
        "line_number": line_number,
        "raw": raw_line,
        "date_text": date_text,
        "flight_number": flight_text,
        "normalized_flight_number": normalize_alp_flight_number(flight_text),
        "flight_key": flight_key,
        "airport": airport,
        "tail_number": tail_number,
        "time_text": time_text,
        "stripped_time": _strip_alp_time_marker(time_text),
    }

    if not flight_key:
        return {**base, "error": "Flight number is required."}
    if not tail_number:
        return {**base, "error": "Tail is required."}

    try:
        utc_datetime = _alp_utc_datetime(date_text, time_text)
    except ValueError as exc:
        return {**base, "error": str(exc)}

    local_datetime = utc_to_local_naive(utc_datetime, timezone_name)
    return {
        **base,
        "utc_datetime": utc_datetime,
        "local_datetime": local_datetime,
        "local_display": _format_alp_local(local_datetime),
        "utc_display": utc_datetime.strftime("%Y-%m-%d %H:%M UTC"),
    }


def _strip_alp_time_marker(value):
    return re.sub(r"\s*\([A-Za-z]\)\s*$", "", str(value or "").strip())


def _alp_utc_datetime(date_text, time_text):
    normalized_date = str(date_text or "").strip().upper()
    stripped_time = _strip_alp_time_marker(time_text)
    if not re.fullmatch(r"([0-2]?[0-9]|3[01])-[A-Z]{3}-[0-9]{4}", normalized_date):
        raise ValueError("Date must use DD-MON-YYYY.")
    if not re.fullmatch(r"([01]?[0-9]|2[0-3]):[0-5][0-9]", stripped_time):
        raise ValueError("Time must use HH:MM Zulu format.")

    hour, minute = [int(part) for part in stripped_time.split(":", 1)]
    try:
        alp_date = datetime.strptime(normalized_date, "%d-%b-%Y").date()
    except ValueError:
        raise ValueError("Date must use DD-MON-YYYY.") from None
    return datetime(alp_date.year, alp_date.month, alp_date.day, hour, minute)


def _missions_for_operation(operation, mission_type):
    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )


def _missions_by_key(missions):
    grouped = defaultdict(list)
    for mission in missions:
        key = alp_flight_key(mission.flight_number)
        if key:
            grouped[key].append(mission)
    return grouped


def _should_suppress_hot_positioning_row(operation, row):
    if not _is_9xxx_flight(row.get("flight_number")):
        return False
    tail = str(row.get("tail_number") or "").strip().upper()
    if not tail:
        return False
    row_key = row.get("flight_key") or alp_flight_key(row.get("flight_number"))
    return bool(_normal_outbound_for_tail(operation, tail, excluded_flight_key=row_key))


def _normal_outbound_for_tail(operation, tail_number, excluded_flight_key=None):
    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .filter(func.upper(SortDateMission.assigned_tail_number) == tail_number)
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )
    for mission in missions:
        mission_key = alp_flight_key(mission.flight_number)
        if mission_key and mission_key != excluded_flight_key and not mission_key.startswith("9"):
            return mission
    return None


def _is_9xxx_flight(value):
    key = alp_flight_key(value)
    return bool(key and key.startswith("9"))


def _matched_row(row, mission):
    current_tail = (mission.assigned_tail_number or "").strip().upper()
    tail_number = row["tail_number"]
    warning = ""
    if current_tail and current_tail != tail_number:
        if mission.tail_source == "api":
            warning = "API tail differs; ALP will replace API tail."
        else:
            warning = "ALP tail differs from current tail and will become authoritative."

    current_time_utc = _current_alp_comparable_time_utc(mission)
    time_change = _time_change_display(current_time_utc, row["utc_datetime"])

    return {
        **row,
        "mission_id": mission.id,
        "current_flight_number": mission.flight_number,
        "current_tail": current_tail or "-",
        "tail_change": (
            "No change" if current_tail == tail_number else f"{current_tail or '-'} -> {tail_number}"
        ),
        "time_change": time_change,
        "eta_change": time_change,
        "time_changed": not _same_alp_minute(current_time_utc, row["utc_datetime"]),
        "warning": warning,
    }


def _current_alp_comparable_time_utc(mission):
    if not mission:
        return None
    if mission.mission_type == "arrival":
        return mission.eta_datetime_utc or mission.planned_datetime_utc
    if mission.mission_type == "departure":
        return mission.actual_block_out_datetime_utc or mission.planned_datetime_utc
    return mission.planned_datetime_utc


def _time_change_display(current_utc, alp_utc):
    if _same_alp_minute(current_utc, alp_utc):
        return "No change"
    current_display = _alp_utc_display(current_utc)
    alp_display = _alp_utc_display(alp_utc)
    return f"{current_display} -> {alp_display}"


def _same_alp_minute(left, right):
    if not left or not right:
        return False
    return _minute_precision(left) == _minute_precision(right)


def _minute_precision(value):
    if not value:
        return None
    return value.replace(second=0, microsecond=0)


def _alp_utc_display(value):
    if not value:
        return "-"
    return _format_alp_local(utc_to_local_naive(value, ALP_TIMEZONE))


def _missing_mission_row(mission):
    return {
        "mission_id": mission.id,
        "flight_number": mission.flight_number,
        "flight_key": alp_flight_key(mission.flight_number) or "-",
        "airport": mission.origin if mission.mission_type == "arrival" else mission.destination,
        "tail_number": mission.assigned_tail_number or "-",
    }


def _format_alp_local(value):
    return f"{value.strftime('%H:%M Local %b')} {value.day}" if hasattr(value, "strftime") else ""
