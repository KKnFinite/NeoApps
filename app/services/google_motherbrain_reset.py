"""Plan, dry-run, and future explicit execution for MotherBrain nightly reset."""

from collections.abc import Mapping
import re

from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
    _clear_google_motherbrain_reset_ranges,
    read_google_motherbrain_reset_parking_formulas,
)


INBOUND_RESET_RANGES = (
    "Inbound!A4:G13",
    "Inbound!A15:G100",
    "Inbound!P4:P100",
)
OUTBOUND_RESET_RANGES = (
    "Outbound!A4:G13",
    "Outbound!A15:G100",
    "Outbound!P4:P100",
    "Outbound!Y4:Y100",
)
PARKING_HELPER_FIRST_COLUMN = "BG"
PARKING_HELPER_LAST_COLUMN = "BK"
MIN_PARKING_ASSIGNMENT_ROW = 4
_SAFE_SINGLE_CELL_FORMULA = re.compile(
    r"^\s*=\s*\$?([A-Z]{1,3})\$?([1-9]\d*)\s*$",
    re.IGNORECASE,
)


def build_google_motherbrain_reset_plan(parking_formula_rows):
    """Build the exact workbook ranges/cells that a nightly reset may clear."""
    parking_cells = tuple(_parking_cells_from_formulas(parking_formula_rows))
    clear_ranges = (
        *INBOUND_RESET_RANGES,
        *OUTBOUND_RESET_RANGES,
        *(f"Parking Plan!{cell}" for cell in parking_cells),
    )
    return {
        "spreadsheet_id": GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
        "parking_formula_range": GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
        "inbound_ranges": INBOUND_RESET_RANGES,
        "outbound_ranges": OUTBOUND_RESET_RANGES,
        "parking_cells": parking_cells,
        "clear_ranges": clear_ranges,
    }


def dry_run_google_motherbrain_reset(*, formula_reader=None):
    """Return a reset plan after reading formulas; never write to Google."""
    formula_rows = (formula_reader or read_google_motherbrain_reset_parking_formulas)()
    return build_google_motherbrain_reset_plan(formula_rows)


def execute_google_motherbrain_reset_plan(plan, *, writer=None):
    """Execute a validated plan only when a future approved caller opts in."""
    clear_ranges = _validated_clear_ranges(plan)
    (writer or _clear_google_motherbrain_reset_ranges)(clear_ranges)
    return clear_ranges


def _parking_cells_from_formulas(formula_rows):
    seen = set()
    for formula in _formula_values(formula_rows):
        match = _SAFE_SINGLE_CELL_FORMULA.fullmatch(formula)
        if not match:
            continue
        column = match.group(1).upper()
        row = int(match.group(2))
        if row < MIN_PARKING_ASSIGNMENT_ROW or _is_helper_column(column):
            continue
        cell = f"{column}{row}"
        if cell not in seen:
            seen.add(cell)
            yield cell


def _formula_values(formula_rows):
    for row in formula_rows or ():
        if isinstance(row, (list, tuple)):
            yield str(row[0] if row else "")
        else:
            yield str(row if row is not None else "")


def _is_helper_column(column):
    column_number = _column_number(column)
    return _column_number(PARKING_HELPER_FIRST_COLUMN) <= column_number <= _column_number(
        PARKING_HELPER_LAST_COLUMN
    )


def _column_number(column):
    value = 0
    for letter in column:
        value = (value * 26) + ord(letter) - ord("A") + 1
    return value


def _validated_clear_ranges(plan):
    if not isinstance(plan, Mapping):
        raise ValueError("Google MotherBrain reset plan is invalid.")
    if plan.get("spreadsheet_id") != GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID:
        raise ValueError("Google MotherBrain reset plan targets an unauthorized workbook.")

    parking_cells = tuple(plan.get("parking_cells") or ())
    expected_cells = tuple(
        _parking_cells_from_formulas(f"={cell}" for cell in parking_cells)
    )
    if expected_cells != parking_cells:
        raise ValueError("Google MotherBrain reset plan contains unsafe parking cells.")

    expected_ranges = (
        *INBOUND_RESET_RANGES,
        *OUTBOUND_RESET_RANGES,
        *(f"Parking Plan!{cell}" for cell in parking_cells),
    )
    if tuple(plan.get("clear_ranges") or ()) != expected_ranges:
        raise ValueError("Google MotherBrain reset plan contains unexpected ranges.")
    return expected_ranges
