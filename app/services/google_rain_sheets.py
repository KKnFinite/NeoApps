"""Focused legacy Google Rain adapters for bounded outbound milestone I/O."""

from __future__ import annotations

from datetime import datetime

from flask import current_app

from app.services.alp_import import alp_flight_key, normalize_alp_flight_number
from app.services.google_motherbrain_sheets import (
    GoogleMotherBrainReaderError,
    _configured_reader_inputs,
    _create_gspread_client,
    _create_gspread_writer,
    _google_call,
    _padded_rows,
)
from app.services.google_motherbrain_live_missions import (
    GoogleMotherBrainMissionError,
    _parse_optional_live_datetime,
)
from app.services.time_display import format_local_hhmm


GOOGLE_RAIN_LOCKED_SPREADSHEET_ID = (
    "13ub-NCOHzUpgAvxzKHK_noRTficTSFqufkNNOKKfPcM"
)
GOOGLE_RAIN_OUTBOUND_SHEET = "Outbound"
GOOGLE_RAIN_FIRST_DATA_ROW = 3
GOOGLE_RAIN_LAST_DATA_ROW = 50
GOOGLE_RAIN_OUTBOUND_RANGE_SPECS = (
    ("flight_number", "Outbound!A3:A50", 1),
    ("destination", "Outbound!C3:C50", 1),
    ("std", "Outbound!E3:E50", 1),
    ("elmac", "Outbound!L3:L50", 1),
    ("ramp_load_complete", "Outbound!M3:M50", 1),
    ("crew_load_complete", "Outbound!N3:N50", 1),
    ("block", "Outbound!O3:O50", 1),
    ("no_return", "Outbound!S3:S50", 1),
)
GOOGLE_RAIN_OUTBOUND_IDENTITY_RANGE_SPECS = (
    ("flight_number", "Outbound!A3:A50", 1),
    ("destination", "Outbound!C3:C50", 1),
    ("std", "Outbound!E3:E50", 1),
)
GOOGLE_RAIN_MILESTONE_WRITE_COLUMNS = {
    "elmac": "L",
    "ramp_load_complete": "M",
    "crew_load_complete": "N",
    "official_block_out": "O",
    "no_return": "S",
}


class GoogleRainWriterError(RuntimeError):
    """Safe operator-facing failure from the bounded Rain writer."""

    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(message)


def read_google_rain_outbound_milestones(config=None, client_factory=None):
    """Read only Rain fields needed to update existing departure milestones."""
    config = config or current_app.config
    credentials, _motherbrain_spreadsheet_id = _configured_reader_inputs(config)
    client = (client_factory or _create_gspread_client)(credentials)
    spreadsheet = _google_call(
        "open_rain_spreadsheet",
        lambda: client.open_by_key(GOOGLE_RAIN_LOCKED_SPREADSHEET_ID),
    )
    ranges = [spec[1] for spec in GOOGLE_RAIN_OUTBOUND_RANGE_SPECS]
    response = _google_call(
        "read_rain_outbound_milestones",
        lambda: spreadsheet.values_batch_get(
            ranges,
            params={
                "valueRenderOption": "FORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
                "majorDimension": "ROWS",
            },
        ),
    )
    values = _range_values(response)
    row_count = GOOGLE_RAIN_LAST_DATA_ROW - GOOGLE_RAIN_FIRST_DATA_ROW + 1
    flight_rows = _padded_rows(values[0], row_count, 1)
    destination_rows = _padded_rows(values[1], row_count, 1)
    std_rows = _padded_rows(values[2], row_count, 1)
    elmac_rows = _padded_rows(values[3], row_count, 1)
    ramp_load_complete_rows = _padded_rows(values[4], row_count, 1)
    crew_load_complete_rows = _padded_rows(values[5], row_count, 1)
    block_rows = _padded_rows(values[6], row_count, 1)
    no_return_rows = _padded_rows(values[7], row_count, 1)

    rows = []
    for offset in range(row_count):
        flight_number = _cell(flight_rows[offset], 0)
        destination = _cell(destination_rows[offset], 0)
        std = _cell(std_rows[offset], 0)
        elmac = _cell(elmac_rows[offset], 0)
        ramp_load_complete = _cell(ramp_load_complete_rows[offset], 0)
        crew_load_complete = _cell(crew_load_complete_rows[offset], 0)
        block = _cell(block_rows[offset], 0)
        no_return = _cell(no_return_rows[offset], 0)
        if not _rain_outbound_row_has_content(
            flight_number,
            destination,
            std,
            elmac,
            ramp_load_complete,
            crew_load_complete,
            block,
            no_return,
        ):
            continue
        rows.append(
            {
                "source_sheet": GOOGLE_RAIN_OUTBOUND_SHEET,
                "sheet_row": GOOGLE_RAIN_FIRST_DATA_ROW + offset,
                "flight_number": flight_number,
                "destination": destination,
                "std": std,
                "elmac": elmac,
                "ramp_load_complete": ramp_load_complete,
                "crew_load_complete": crew_load_complete,
                "block": block,
                "no_return": no_return,
            }
        )
    return rows


def _rain_outbound_row_has_content(
    flight_number,
    destination,
    std,
    elmac,
    ramp_load_complete,
    crew_load_complete,
    block,
    no_return,
):
    """Return whether a bounded Rain row contains meaningful sheet data.

    An unchecked Google checkbox is returned as the literal string ``FALSE``.
    That value by itself is formatting residue, not an operational Rain row.
    Keep any other supplied identity or milestone value, including an invalid
    checkbox value, so the existing row-level validation can safely diagnose it.
    """
    if any(
        _has_rendered_value(value)
        for value in (
            flight_number,
            destination,
            std,
            elmac,
            ramp_load_complete,
            crew_load_complete,
            block,
        )
    ):
        return True
    return _has_rendered_value(no_return) and not _unchecked_rain_checkbox(no_return)


def _has_rendered_value(value):
    return value is not None and str(value).strip() != ""


def _unchecked_rain_checkbox(value):
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() in {
        "false",
        "0",
        "no",
        "off",
        "unchecked",
    }


def write_google_rain_departure_milestone(
    mission,
    field,
    value,
    *,
    operation=None,
    config=None,
    client_factory=None,
):
    """Write one Neo-authoritative Rain value to its existing Google row only.

    This service intentionally performs no Neo mission mutation, database write,
    or integration-mode check. It reads only A/C/E identity cells before issuing
    a single-cell Google update.
    """
    _validate_departure_mission(mission)
    normalized_field = str(field or "").strip().lower()
    column = GOOGLE_RAIN_MILESTONE_WRITE_COLUMNS.get(normalized_field)
    if column is None:
        raise GoogleRainWriterError(
            "unsupported_field",
            "Choose a valid NeoRain milestone for Google mirroring.",
        )
    cell_value = _google_write_value(normalized_field, value, mission)
    config = config or current_app.config

    try:
        credentials, _motherbrain_spreadsheet_id = _configured_reader_inputs(config)
        client = (client_factory or _create_gspread_writer)(credentials)
        spreadsheet = _google_call(
            "open_rain_spreadsheet",
            lambda: client.open_by_key(GOOGLE_RAIN_LOCKED_SPREADSHEET_ID),
        )
        identity_rows = _read_google_rain_outbound_identities(spreadsheet)
        row = _matching_google_rain_outbound_row(
            mission,
            identity_rows,
            operation=operation,
        )
        worksheet = _google_call(
            "open_rain_outbound_sheet",
            lambda: spreadsheet.worksheet(GOOGLE_RAIN_OUTBOUND_SHEET),
        )
        cell = f"{column}{row['sheet_row']}"
        _google_call(
            "write_rain_outbound_milestone",
            lambda: worksheet.update_acell(cell, cell_value),
        )
    except GoogleRainWriterError:
        raise
    except GoogleMotherBrainReaderError as error:
        raise GoogleRainWriterError(
            "google_failure",
            "NeoRain could not update the Google Rain workbook.",
        ) from error

    return {
        "field": normalized_field,
        "sheet_row": row["sheet_row"],
        "cell": cell,
        "value": cell_value,
    }


def _read_google_rain_outbound_identities(spreadsheet):
    ranges = [spec[1] for spec in GOOGLE_RAIN_OUTBOUND_IDENTITY_RANGE_SPECS]
    response = _google_call(
        "read_rain_outbound_identities",
        lambda: spreadsheet.values_batch_get(
            ranges,
            params={
                "valueRenderOption": "FORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
                "majorDimension": "ROWS",
            },
        ),
    )
    values = _range_values(response, GOOGLE_RAIN_OUTBOUND_IDENTITY_RANGE_SPECS)
    row_count = GOOGLE_RAIN_LAST_DATA_ROW - GOOGLE_RAIN_FIRST_DATA_ROW + 1
    flight_rows = _padded_rows(values[0], row_count, 1)
    destination_rows = _padded_rows(values[1], row_count, 1)
    std_rows = _padded_rows(values[2], row_count, 1)
    rows = []
    for offset in range(row_count):
        flight_number = _cell(flight_rows[offset], 0)
        destination = _cell(destination_rows[offset], 0)
        std = _cell(std_rows[offset], 0)
        if not any((flight_number, destination, std)):
            continue
        rows.append(
            {
                "sheet_row": GOOGLE_RAIN_FIRST_DATA_ROW + offset,
                "flight_number": flight_number,
                "flight_key": alp_flight_key(
                    normalize_alp_flight_number(flight_number)
                ),
                "destination": destination.upper(),
                "std": std,
            }
        )
    return rows


def _matching_google_rain_outbound_row(mission, rows, *, operation=None):
    flight_key = alp_flight_key(normalize_alp_flight_number(mission.flight_number))
    if not flight_key:
        raise GoogleRainWriterError(
            "row_not_found",
            "The departure flight number cannot be matched in Google Rain.",
        )
    candidates = [row for row in rows if row["flight_key"] == flight_key]
    if not candidates:
        raise GoogleRainWriterError(
            "row_not_found",
            "No Google Rain outbound row matches this departure.",
        )
    if len(candidates) == 1:
        return candidates[0]

    destination = _text(mission.destination)
    if destination:
        candidates = [
            row for row in candidates if _text(row["destination"]) == destination
        ]
    if len(candidates) == 1:
        return candidates[0]

    operation = operation or mission.sort_date_operation
    if operation is not None and mission.planned_datetime_local is not None:
        candidates = [
            row
            for row in candidates
            if _matching_google_std(mission, operation, row["std"])
        ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise GoogleRainWriterError(
            "row_not_found",
            "No Google Rain outbound row matches this departure.",
        )
    raise GoogleRainWriterError(
        "ambiguous_row",
        "Google Rain has multiple matching outbound rows for this departure.",
    )


def _matching_google_std(mission, operation, value):
    if not str(value or "").strip() or str(value).strip() == "-":
        return False
    try:
        local_value, _utc_value = _parse_optional_live_datetime(
            value,
            operation,
            "Rain STD",
        )
    except GoogleMotherBrainMissionError:
        return False
    return _same_minute(local_value, mission.planned_datetime_local)


def _google_write_value(field, value, mission):
    if field == "no_return":
        if value is True or str(value or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "set",
        }:
            return "TRUE"
        if value is False or value is None or str(value or "").strip().lower() in {
            "0",
            "false",
            "no",
            "off",
            "clear",
            "",
        }:
            return "FALSE"
        raise GoogleRainWriterError(
            "invalid_value",
            "No Return must be set or cleared.",
        )
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, datetime):
        raise GoogleRainWriterError(
            "invalid_value",
            "NeoRain milestone time must be a canonical datetime.",
        )
    return format_local_hhmm(value, mission.timezone)


def _validate_departure_mission(mission):
    if mission is None or mission.mission_type != "departure":
        raise GoogleRainWriterError(
            "invalid_mission",
            "Google Rain milestones apply only to departure missions.",
        )


def _same_minute(left, right):
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return False
    return left.replace(second=0, microsecond=0) == right.replace(
        second=0,
        microsecond=0,
    )


def _text(value):
    return str(value or "").strip().upper()


def _range_values(response, range_specs=GOOGLE_RAIN_OUTBOUND_RANGE_SPECS):
    value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
    if not isinstance(value_ranges, list) or len(value_ranges) != len(range_specs):
        raise GoogleMotherBrainReaderError(
            "missing_rain_range",
            "Google did not return every required Rain range.",
        )

    values = []
    for value_range in value_ranges:
        rows = value_range.get("values", []) if isinstance(value_range, dict) else None
        if not isinstance(rows, list):
            raise GoogleMotherBrainReaderError(
                "invalid_rain_range",
                "Google returned an invalid Rain range.",
            )
        values.append(rows)
    return values


def _cell(row, index):
    value = row[index] if index < len(row) else ""
    return str(value if value is not None else "").strip()
