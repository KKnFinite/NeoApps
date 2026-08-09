"""Safely dry-run or explicitly execute the MotherBrain reset on a test copy.

Usage:
    python scripts/test_google_motherbrain_reset.py --spreadsheet-id <test-copy-id>
    python scripts/test_google_motherbrain_reset.py --spreadsheet-id <test-copy-id> --execute
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.google_motherbrain_reset import (  # noqa: E402
    build_google_motherbrain_reset_plan,
)
from app.services.google_motherbrain_sheets import (  # noqa: E402
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
    GOOGLE_MOTHERBRAIN_REQUIRED_TABS,
    GoogleMotherBrainReaderError,
    _create_gspread_client,
    _create_gspread_writer,
    _credential_json,
    _parse_service_account_json,
)


RESET_TEST_COPY_TITLE_MARKER = "RESET TEST COPY"


class GoogleMotherBrainResetTestHarnessError(RuntimeError):
    """Raised when a requested workbook is not a safe reset-test target."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Dry-run the MotherBrain reset plan against a disposable test copy."
    )
    parser.add_argument(
        "--spreadsheet-id",
        required=True,
        help="Explicit ID of the disposable Google Sheets reset test copy.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly clear the approved reset ranges in the validated test copy.",
    )
    return parser.parse_args(argv)


def run_reset_test_harness(
    spreadsheet_id,
    *,
    execute=False,
    config=None,
    client_factory=None,
):
    """Build or explicitly execute the reset plan for a validated test workbook."""
    target_id = _validated_test_spreadsheet_id(spreadsheet_id)
    credentials = _harness_credentials(config or _environment_config())
    client = (client_factory or _client_factory_for(execute))(credentials)
    spreadsheet = client.open_by_key(target_id)
    title = _validate_test_copy_metadata(
        spreadsheet.fetch_sheet_metadata(params={"includeGridData": False})
    )
    formula_rows = _read_parking_formula_rows(spreadsheet)
    reset_plan = build_google_motherbrain_reset_plan(formula_rows)

    if execute:
        spreadsheet.values_batch_clear(list(reset_plan["clear_ranges"]))

    return {
        "spreadsheet_id": target_id,
        "spreadsheet_title": title,
        "executed": bool(execute),
        "clear_ranges": reset_plan["clear_ranges"],
        "parking_cells": reset_plan["parking_cells"],
    }


def _validated_test_spreadsheet_id(spreadsheet_id):
    target_id = str(spreadsheet_id or "").strip()
    if not target_id:
        raise GoogleMotherBrainResetTestHarnessError(
            "An explicit test spreadsheet ID is required."
        )
    if target_id == GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID:
        raise GoogleMotherBrainResetTestHarnessError(
            "The locked production MotherBrain workbook cannot be used for reset testing."
        )
    return target_id


def _harness_credentials(config):
    raw_credentials, _credential_source = _credential_json(config)
    if not raw_credentials:
        raise GoogleMotherBrainResetTestHarnessError(
            "Google service-account credentials are not configured."
        )
    try:
        return _parse_service_account_json(raw_credentials)
    except GoogleMotherBrainReaderError as exc:
        raise GoogleMotherBrainResetTestHarnessError(
            "Google service-account credentials are invalid."
        ) from exc


def _client_factory_for(execute):
    return _create_gspread_writer if execute else _create_gspread_client


def _validate_test_copy_metadata(metadata):
    properties = metadata.get("properties") if isinstance(metadata, dict) else None
    if not isinstance(properties, dict):
        raise GoogleMotherBrainResetTestHarnessError(
            "Google did not return valid workbook metadata."
        )

    title = str(properties.get("title") or "").strip()
    if RESET_TEST_COPY_TITLE_MARKER not in title.upper():
        raise GoogleMotherBrainResetTestHarnessError(
            "The target workbook title must contain RESET TEST COPY."
        )

    tab_names = {
        str((sheet.get("properties") or {}).get("title") or "").strip()
        for sheet in metadata.get("sheets") or []
        if isinstance(sheet, dict)
    }
    missing_tabs = sorted(GOOGLE_MOTHERBRAIN_REQUIRED_TABS - tab_names)
    if missing_tabs:
        raise GoogleMotherBrainResetTestHarnessError(
            f"The target workbook is missing the required {missing_tabs[0]} tab."
        )
    return title


def _read_parking_formula_rows(spreadsheet):
    response = spreadsheet.values_batch_get(
        [GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE],
        params={
            "valueRenderOption": "FORMULA",
            "dateTimeRenderOption": "FORMATTED_STRING",
            "majorDimension": "ROWS",
        },
    )
    value_ranges = response.get("valueRanges") if isinstance(response, dict) else None
    if not isinstance(value_ranges, list) or len(value_ranges) != 1:
        raise GoogleMotherBrainResetTestHarnessError(
            "Google did not return the Parking Plan reset formulas."
        )
    values = value_ranges[0].get("values") if isinstance(value_ranges[0], dict) else None
    if not isinstance(values, list):
        raise GoogleMotherBrainResetTestHarnessError(
            "Google returned invalid Parking Plan reset formulas."
        )
    return values


def _environment_config():
    return {
        "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": os.getenv(
            "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON"
        ),
        "GOOGLE_SERVICE_ACCOUNT_JSON": os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
    }


def main(argv=None):
    args = parse_args(argv)
    try:
        result = run_reset_test_harness(
            args.spreadsheet_id,
            execute=args.execute,
        )
    except GoogleMotherBrainResetTestHarnessError as exc:
        print(f"Google MotherBrain reset test harness refused: {exc}", file=sys.stderr)
        return 2

    mode = "EXECUTED" if result["executed"] else "DRY RUN"
    print(f"Google MotherBrain reset test harness: {mode}")
    print(f"Workbook title: {result['spreadsheet_title']}")
    print("Approved clear ranges:")
    for clear_range in result["clear_ranges"]:
        print(f"- {clear_range}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
