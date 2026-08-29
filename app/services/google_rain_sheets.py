"""Read-only adapter for the locked legacy Google Rain outbound milestones."""

from __future__ import annotations

from flask import current_app

from app.services.google_motherbrain_sheets import (
    GoogleMotherBrainReaderError,
    _configured_reader_inputs,
    _create_gspread_client,
    _google_call,
    _padded_rows,
)


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
    ("crew_load_complete", "Outbound!N3:N50", 1),
    ("block", "Outbound!O3:O50", 1),
    ("no_return", "Outbound!S3:S50", 1),
)


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
    crew_load_complete_rows = _padded_rows(values[4], row_count, 1)
    block_rows = _padded_rows(values[5], row_count, 1)
    no_return_rows = _padded_rows(values[6], row_count, 1)

    rows = []
    for offset in range(row_count):
        flight_number = _cell(flight_rows[offset], 0)
        destination = _cell(destination_rows[offset], 0)
        std = _cell(std_rows[offset], 0)
        elmac = _cell(elmac_rows[offset], 0)
        crew_load_complete = _cell(crew_load_complete_rows[offset], 0)
        block = _cell(block_rows[offset], 0)
        no_return = _cell(no_return_rows[offset], 0)
        if not any(
            (
                flight_number,
                destination,
                std,
                elmac,
                crew_load_complete,
                block,
                no_return,
            )
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
                "crew_load_complete": crew_load_complete,
                "block": block,
                "no_return": no_return,
            }
        )
    return rows


def _range_values(response):
    value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
    if not isinstance(value_ranges, list) or len(value_ranges) != len(
        GOOGLE_RAIN_OUTBOUND_RANGE_SPECS
    ):
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
