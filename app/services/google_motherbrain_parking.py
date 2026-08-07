"""Apply Google MotherBrain parking values through Neo parking services."""

import re

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services.parking_physical_validator import (
    parking_physical_validation_context,
    sync_parking_physical_alerts,
)
from app.services.parking_plan import (
    ParkingPlanError,
    assign_tail_to_lane,
    tail_rows_for_operation,
)
from app.services.parking_rules import normalize_parking_position_code


_TAIL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,31}$")


def normalize_google_motherbrain_parking(value):
    """Return Neo's physical position and slot for a Google parking value."""
    raw_value = str(value or "").strip().upper().replace(" ", "")
    lane_number = 2 if raw_value.endswith("-B") else 1
    position_value = raw_value[:-2] if lane_number == 2 else raw_value
    position_code = normalize_parking_position_code(position_value)
    if not position_code:
        return None
    return {
        "ramp_code": position_code[0],
        "position_code": position_code,
        "lane_number": lane_number,
    }


def apply_google_motherbrain_parking(
    operation,
    effective_tail,
    google_parking_value,
    *,
    user=None,
    source_sheet="",
    source_row=None,
):
    """Apply one Google parking value; the caller owns the transaction commit."""
    batch_result = apply_google_motherbrain_parking_batch(
        operation,
        [
            {
                "effective_tail": effective_tail,
                "parking_value": google_parking_value,
                "source_sheet": source_sheet,
                "source_row": source_row,
            }
        ],
        user=user,
    )
    result = dict(batch_result["results"][0])
    result["physical_validation"] = batch_result["physical_validation"]
    result["alert_sync"] = batch_result["alert_sync"]
    return result


def apply_google_motherbrain_parking_batch(operation, rows, *, user=None):
    """Apply valid Google parking rows while isolating invalid rows.

    Rows are dictionaries containing ``effective_tail`` and ``parking_value``.
    Optional ``source_sheet`` and ``source_row`` values are preserved in each
    result and in skip logging. The caller commits or rolls back the batch.
    """
    if not operation or not getattr(operation, "gateway", None):
        raise ValueError("A current sort operation with a gateway is required.")

    results = []
    applied_count = 0
    for index, row in enumerate(rows or (), start=1):
        row = row or {}
        source_sheet = str(row.get("source_sheet") or "").strip()
        source_row = row.get("source_row")
        raw_tail = row.get("effective_tail")
        raw_parking = row.get("parking_value")
        tail_number = _normalize_effective_tail(raw_tail)
        location = normalize_google_motherbrain_parking(raw_parking)

        if not tail_number:
            results.append(
                _skipped_result(
                    index,
                    raw_tail,
                    raw_parking,
                    source_sheet,
                    source_row,
                    "Missing or unusable effective tail.",
                )
            )
            continue
        if not location:
            results.append(
                _skipped_result(
                    index,
                    tail_number,
                    raw_parking,
                    source_sheet,
                    source_row,
                    "Missing or unusable parking value.",
                )
            )
            continue

        try:
            with db.session.begin_nested():
                assignment = assign_tail_to_lane(
                    operation,
                    tail_number,
                    location["ramp_code"],
                    location["position_code"],
                    location["lane_number"],
                    user=user,
                    replace_occupied=True,
                    confirm_rule_override=True,
                )
                db.session.flush()
        except (ParkingPlanError, SQLAlchemyError) as error:
            results.append(
                _skipped_result(
                    index,
                    tail_number,
                    raw_parking,
                    source_sheet,
                    source_row,
                    str(error) or "Parking application failed.",
                )
            )
            continue

        applied_count += 1
        results.append(
            {
                "index": index,
                "status": "applied",
                "tail_number": assignment.tail_number,
                "parking_value": str(raw_parking or "").strip(),
                "position_code": assignment.position_code,
                "lane_number": assignment.lane_number,
                "source_sheet": source_sheet,
                "source_row": source_row,
                "reason": "",
            }
        )

    physical_validation = parking_physical_validation_context(
        operation,
        tail_rows=tail_rows_for_operation(operation.gateway, operation),
    )
    alert_sync = sync_parking_physical_alerts(
        operation.gateway,
        operation,
        physical_validation,
    )
    db.session.flush()
    return {
        "results": results,
        "applied_count": applied_count,
        "skipped_count": len(results) - applied_count,
        "physical_validation": physical_validation,
        "alert_sync": alert_sync,
    }


def _normalize_effective_tail(value):
    tail_number = str(value or "").strip().upper()
    if not _TAIL_PATTERN.fullmatch(tail_number):
        return ""
    return tail_number


def _skipped_result(
    index,
    tail_number,
    parking_value,
    source_sheet,
    source_row,
    reason,
):
    result = {
        "index": index,
        "status": "skipped",
        "tail_number": str(tail_number or "").strip().upper(),
        "parking_value": str(parking_value or "").strip(),
        "position_code": "",
        "lane_number": None,
        "source_sheet": source_sheet,
        "source_row": source_row,
        "reason": reason,
    }
    current_app.logger.warning(
        "Skipped Google MotherBrain parking row sheet=%s row=%s "
        "tail=%s parking=%r reason=%s",
        source_sheet or "unknown",
        source_row if source_row is not None else index,
        result["tail_number"] or "missing",
        result["parking_value"],
        reason,
    )
    return result
