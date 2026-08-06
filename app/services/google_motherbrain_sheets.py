"""Read-only Google Sheets adapter for the locked MotherBrain workbook."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
import re

from flask import current_app

try:
    import gspread
except ImportError:  # Production installs the declared dependency.
    gspread = None

from app.services.google_motherbrain_import import (
    GOOGLE_MOTHERBRAIN_GATEWAY_CODE,
    GOOGLE_MOTHERBRAIN_SCHEMA_VERSION,
    GOOGLE_MOTHERBRAIN_SORT_NAME,
    GOOGLE_MOTHERBRAIN_TIMEZONE,
)


GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID = (
    "10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg"
)
GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_TITLE = "RFD-N-sim: Mother Brain"
GOOGLE_MOTHERBRAIN_READONLY_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
)
GOOGLE_SHEETS_EPOCH = datetime(1899, 12, 30)

GOOGLE_MOTHERBRAIN_RANGE_SPECS = (
    ("sort_date", "Inbound!H2", 1, 1, 2),
    ("inbound_manual", "Inbound!A4:G13", 10, 7, 4),
    ("inbound_alp", "Inbound!A16:G100", 85, 7, 16),
    ("inbound_official_order", "Inbound!P4:P100", 97, 1, 4),
    ("outbound_manual", "Outbound!A4:G13", 10, 7, 4),
    ("outbound_alp", "Outbound!A16:G100", 85, 7, 16),
    ("outbound_official_order", "Outbound!P4:P100", 97, 1, 4),
    ("outbound_tail_swaps", "Outbound!W4:Z100", 97, 4, 4),
    ("parking_assignments", "Parking Plan!BG3:BH100", 98, 2, 3),
)
GOOGLE_MOTHERBRAIN_REQUIRED_TABS = frozenset(
    {"Inbound", "Outbound", "Parking Plan"}
)


class GoogleMotherBrainReaderError(RuntimeError):
    """A safe, user-facing Google reader failure."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def google_motherbrain_reader_status(config=None):
    """Return local configuration status without making a Google API request."""
    config = config or current_app.config
    raw_credentials, credential_source = _credential_json(config)
    credentials_configured = bool(raw_credentials)
    credentials_valid = False
    service_account_email = None
    if credentials_configured:
        try:
            info = _parse_service_account_json(raw_credentials)
        except GoogleMotherBrainReaderError:
            info = None
        if info is not None:
            credentials_valid = True
            service_account_email = str(info.get("client_email") or "").strip() or None

    spreadsheet_id = str(
        config.get("GOOGLE_MOTHERBRAIN_SPREADSHEET_ID") or ""
    ).strip()
    return {
        "enabled": _as_bool(config.get("GOOGLE_MOTHERBRAIN_READER_ENABLED", False)),
        "credentials_configured": credentials_configured,
        "credentials_valid": credentials_valid,
        "credential_source": credential_source,
        "spreadsheet_id_configured": bool(spreadsheet_id),
        "spreadsheet_id_valid": (
            spreadsheet_id == GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID
        ),
        "service_account_email": service_account_email,
    }


def read_google_motherbrain_envelope(config=None, client_factory=None, now=None):
    """Read the locked workbook and build the existing schema-version-1 envelope."""
    config = config or current_app.config
    status = google_motherbrain_reader_status(config)
    if not status["enabled"]:
        raise GoogleMotherBrainReaderError(
            "reader_disabled",
            "Google reader is disabled.",
        )
    if not status["credentials_configured"]:
        raise GoogleMotherBrainReaderError(
            "missing_credentials",
            "Google service-account credentials are not configured.",
        )
    if not status["credentials_valid"]:
        raise GoogleMotherBrainReaderError(
            "invalid_credentials",
            "Google service-account credentials are invalid.",
        )
    if not status["spreadsheet_id_configured"]:
        raise GoogleMotherBrainReaderError(
            "missing_spreadsheet_id",
            "The Google MotherBrain spreadsheet ID is not configured.",
        )
    if not status["spreadsheet_id_valid"]:
        raise GoogleMotherBrainReaderError(
            "invalid_spreadsheet_id",
            "The configured spreadsheet ID does not match the locked MotherBrain workbook.",
        )

    raw_credentials, _credential_source = _credential_json(config)
    credentials = _parse_service_account_json(raw_credentials)
    spreadsheet_id = str(config["GOOGLE_MOTHERBRAIN_SPREADSHEET_ID"]).strip()
    client = (client_factory or _create_gspread_client)(credentials)
    spreadsheet = _google_call(
        "open_spreadsheet",
        lambda: client.open_by_key(spreadsheet_id),
    )
    metadata = _google_call(
        "read_metadata",
        lambda: spreadsheet.fetch_sheet_metadata(
            params={"includeGridData": False}
        ),
    )
    title, timezone_name = _validate_workbook_metadata(metadata)

    ranges = [spec[1] for spec in GOOGLE_MOTHERBRAIN_RANGE_SPECS]
    raw_response = _google_call(
        "read_raw_ranges",
        lambda: spreadsheet.values_batch_get(
            ranges,
            params={
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "SERIAL_NUMBER",
                "majorDimension": "ROWS",
            },
        ),
    )
    formatted_response = _google_call(
        "read_formatted_ranges",
        lambda: spreadsheet.values_batch_get(
            ranges,
            params={
                "valueRenderOption": "FORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
                "majorDimension": "ROWS",
            },
        ),
    )
    raw_ranges = _range_values(raw_response)
    formatted_ranges = _range_values(formatted_response)

    raw_by_key = {}
    formatted_by_key = {}
    for index, (key, _a1, row_count, column_count, _start_row) in enumerate(
        GOOGLE_MOTHERBRAIN_RANGE_SPECS
    ):
        raw_by_key[key] = _padded_rows(
            raw_ranges[index], row_count, column_count
        )
        formatted_by_key[key] = _padded_rows(
            formatted_ranges[index], row_count, column_count
        )

    sort_date = _required_google_date(
        raw_by_key["sort_date"][0][0],
        formatted_by_key["sort_date"][0][0],
        "Inbound H2 sort date",
    )
    snapshot = {
        "inbound": {
            "manual_rows": _flight_rows(
                raw_by_key["inbound_manual"],
                formatted_by_key["inbound_manual"],
                start_row=4,
                airport_key="origin",
                row_type="manual",
            ),
            "alp_rows": _flight_rows(
                raw_by_key["inbound_alp"],
                formatted_by_key["inbound_alp"],
                start_row=16,
                airport_key="origin",
                row_type="alp",
            ),
            "official_order": _official_order(
                formatted_by_key["inbound_official_order"]
            ),
        },
        "outbound": {
            "manual_rows": _flight_rows(
                raw_by_key["outbound_manual"],
                formatted_by_key["outbound_manual"],
                start_row=4,
                airport_key="destination",
                row_type="manual",
            ),
            "alp_rows": _flight_rows(
                raw_by_key["outbound_alp"],
                formatted_by_key["outbound_alp"],
                start_row=16,
                airport_key="destination",
                row_type="alp",
            ),
            "official_order": _official_order(
                formatted_by_key["outbound_official_order"]
            ),
            "tail_swaps": _tail_swaps(
                formatted_by_key["outbound_tail_swaps"], start_row=4
            ),
        },
        "parking": {
            "assignments": _parking_assignments(
                formatted_by_key["parking_assignments"]
            ),
        },
    }
    submitted_at = now or datetime.now(timezone.utc)
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    return {
        "schema_version": GOOGLE_MOTHERBRAIN_SCHEMA_VERSION,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": title,
        "gateway_code": GOOGLE_MOTHERBRAIN_GATEWAY_CODE,
        "sort_name": GOOGLE_MOTHERBRAIN_SORT_NAME,
        "sort_date": sort_date,
        "timezone": timezone_name,
        "submitted_at": submitted_at.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "snapshot": snapshot,
    }


def _create_gspread_client(credentials):
    if gspread is None:
        raise GoogleMotherBrainReaderError(
            "reader_unavailable",
            "The Google Sheets reader is unavailable.",
        )
    try:
        return gspread.service_account_from_dict(
            credentials,
            scopes=GOOGLE_MOTHERBRAIN_READONLY_SCOPES,
        )
    except Exception as exc:
        raise GoogleMotherBrainReaderError(
            "invalid_credentials",
            "Google service-account credentials are invalid.",
        ) from exc


def _credential_json(config):
    dedicated = config.get("GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON")
    if dedicated is None:
        dedicated = os.environ.get("GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON")
    if isinstance(dedicated, str) and dedicated.strip():
        return dedicated.strip(), "dedicated"

    fallback = config.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if fallback is None:
        fallback = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip(), "fallback"
    return None, None


def _parse_service_account_json(raw_credentials):
    try:
        parsed = json.loads(raw_credentials)
    except (TypeError, json.JSONDecodeError):
        raise GoogleMotherBrainReaderError(
            "invalid_credentials",
            "Google service-account credentials are invalid.",
        ) from None
    if not isinstance(parsed, dict):
        raise GoogleMotherBrainReaderError(
            "invalid_credentials",
            "Google service-account credentials are invalid.",
        )
    required = ("client_email", "private_key", "token_uri")
    if any(
        not isinstance(parsed.get(key), str) or not parsed[key].strip()
        for key in required
    ):
        raise GoogleMotherBrainReaderError(
            "invalid_credentials",
            "Google service-account credentials are invalid.",
        )
    parsed = dict(parsed)
    parsed["private_key"] = parsed["private_key"].replace("\\n", "\n")
    return parsed


def _validate_workbook_metadata(metadata):
    properties = metadata.get("properties") if isinstance(metadata, dict) else None
    if not isinstance(properties, dict):
        raise GoogleMotherBrainReaderError(
            "invalid_workbook",
            "Google did not return valid workbook metadata.",
        )
    title = str(properties.get("title") or "").strip()
    if title != GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_TITLE:
        raise GoogleMotherBrainReaderError(
            "wrong_spreadsheet_title",
            "The Google workbook title does not match the locked MotherBrain workbook.",
        )
    timezone_name = str(properties.get("timeZone") or "").strip()
    if timezone_name != GOOGLE_MOTHERBRAIN_TIMEZONE:
        raise GoogleMotherBrainReaderError(
            "wrong_spreadsheet_timezone",
            "The Google workbook timezone must be America/Chicago.",
        )
    sheets = metadata.get("sheets")
    tab_names = {
        str((sheet.get("properties") or {}).get("title") or "").strip()
        for sheet in sheets or []
        if isinstance(sheet, dict)
    }
    missing_tabs = sorted(GOOGLE_MOTHERBRAIN_REQUIRED_TABS - tab_names)
    if missing_tabs:
        raise GoogleMotherBrainReaderError(
            "missing_sheet_tab",
            f"The Google workbook is missing the required {missing_tabs[0]} tab.",
        )
    return title, timezone_name


def _range_values(response):
    value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
    if not isinstance(value_ranges, list) or len(value_ranges) != len(
        GOOGLE_MOTHERBRAIN_RANGE_SPECS
    ):
        raise GoogleMotherBrainReaderError(
            "missing_sheet_range",
            "Google did not return every required MotherBrain range.",
        )
    values = []
    for value_range in value_ranges:
        if not isinstance(value_range, dict):
            raise GoogleMotherBrainReaderError(
                "missing_sheet_range",
                "Google did not return every required MotherBrain range.",
            )
        rows = value_range.get("values", [])
        if not isinstance(rows, list):
            raise GoogleMotherBrainReaderError(
                "missing_sheet_range",
                "Google returned an invalid MotherBrain range.",
            )
        values.append(rows)
    return values


def _padded_rows(rows, row_count, column_count):
    padded = []
    for row_index in range(row_count):
        source = (
            rows[row_index]
            if row_index < len(rows) and isinstance(rows[row_index], list)
            else []
        )
        padded.append(
            [
                source[column] if column < len(source) else ""
                for column in range(column_count)
            ]
        )
    return padded


def _flight_rows(raw_rows, formatted_rows, start_row, airport_key, row_type):
    results = []
    for offset, displayed_row in enumerate(formatted_rows):
        displayed = [_trim(value) for value in displayed_row]
        flight_number = displayed[1]
        tail_number = displayed[3]
        status = displayed[5]
        cancelled = status.upper() in {"CNL", "CANCELLED"}
        include = (
            bool(tail_number or (cancelled and flight_number))
            if row_type == "manual"
            else any(displayed[1:7])
        )
        if not include:
            continue
        row = {
            "sheet_row": start_row + offset,
            "date": _optional_google_date(
                raw_rows[offset][0],
                displayed_row[0],
                f"Google row {start_row + offset} date",
            ),
            "flight_number": flight_number,
            airport_key: displayed[2],
            "tail_number": tail_number,
            "parking": displayed[4],
            "status": status,
            "time": displayed[6],
        }
        results.append(row)
    return results


def _official_order(rows):
    return [value for value in (_trim(row[0]) for row in rows) if value]


def _tail_swaps(rows, start_row):
    results = []
    for offset, row in enumerate(rows):
        values = [_trim(value) for value in row]
        if not values[2] and not values[3]:
            continue
        results.append(
            {
                "sheet_row": start_row + offset,
                "flight_number": values[0],
                "destination": values[1],
                "new_tail": values[2],
                "scorpion_unlock": values[3],
            }
        )
    return results


def _parking_assignments(rows):
    results = []
    for row in rows:
        tail_number = _trim(row[0])
        if tail_number:
            results.append(
                {
                    "tail_number": tail_number,
                    "position": _trim(row[1]),
                }
            )
    return results


def _required_google_date(raw_value, displayed_value, field_name):
    formatted = _optional_google_date(raw_value, displayed_value, field_name)
    if not formatted:
        raise GoogleMotherBrainReaderError(
            "invalid_date",
            f"{field_name} is required.",
        )
    return formatted


def _optional_google_date(raw_value, displayed_value, field_name):
    if isinstance(raw_value, datetime):
        return raw_value.date().isoformat()
    if isinstance(raw_value, date):
        return raw_value.isoformat()
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        try:
            return (
                GOOGLE_SHEETS_EPOCH + timedelta(days=float(raw_value))
            ).date().isoformat()
        except (OverflowError, TypeError, ValueError):
            pass
    raw_text = _trim(raw_value)
    displayed_text = _trim(displayed_value)
    if not raw_text and not displayed_text:
        return ""
    for candidate in (raw_text, displayed_text):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            try:
                return date.fromisoformat(candidate).isoformat()
            except ValueError:
                break
    raise GoogleMotherBrainReaderError(
        "invalid_date",
        f"{field_name} is invalid.",
    )


def _google_call(action, callback):
    try:
        return callback()
    except GoogleMotherBrainReaderError:
        raise
    except Exception as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        exception_name = type(exc).__name__.lower()
        if status_code in {401, 403} or exception_name == "spreadsheetnotfound":
            message = (
                "The locked Google workbook was not found or is not shared "
                "with the configured service account as Viewer."
            )
            code = "spreadsheet_access_denied"
        elif isinstance(exc, TimeoutError) or "timeout" in exception_name:
            message = "Google Sheets did not respond before the request timed out."
            code = "google_timeout"
        else:
            message = "Google Sheets could not be read. Please try again."
            code = "google_api_failure"
        raise GoogleMotherBrainReaderError(code, message) from exc


def _trim(value):
    return str(value if value is not None else "").strip()


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
