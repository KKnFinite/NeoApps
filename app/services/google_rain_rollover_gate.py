"""Persistent new-sort fencing for Google-authoritative Rain values."""

from __future__ import annotations

import json

from app.extensions import db
from app.models import NeoRainGoogleRolloverState
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    ops_window_for_operation,
)
from app.services.google_motherbrain_live_missions import (
    GoogleMotherBrainMissionError,
    _parse_optional_live_datetime,
)


GOOGLE_RAIN_ROLLOVER_FIELDS = (
    "elmac",
    "ramp_load_complete",
    "crew_load_complete",
    "block",
    "no_return",
    "neo_fuel",
    "center_fuel",
)
_FIELD_LABELS = {
    "elmac": "Rain eLMAC",
    "ramp_load_complete": "Rain Ramp Load Complete",
    "crew_load_complete": "Rain C-LC",
    "block": "Rain Official Block-Out",
}


def gate_google_rain_rollover_rows(operation, rows=(), now=None):
    """Release Google Rain cells only after new-sort evidence is observed.

    The first observation of each sheet row becomes that operation's rollover
    baseline. A field remains fenced until its normalized value changes. The
    release is durable for the rest of the operation so later corrections and
    clears retain normal Google-primary behavior across web-worker restarts.
    """
    _validate_operation(operation)
    local_now = current_gateway_local_datetime(operation.gateway, now=now)
    window_start, window_end = ops_window_for_operation(
        operation,
        operation.gateway,
    )
    if not (
        window_start
        and window_end
        and window_start <= local_now < window_end
    ):
        return {
            "status": "outside_operational_window",
            "rows": (),
            "baseline_count": 0,
            "released_count": 0,
        }

    supplied_rows = [dict(row or {}) for row in rows or ()]
    sheet_rows = {
        _positive_int(row.get("sheet_row"))
        for row in supplied_rows
        if _positive_int(row.get("sheet_row")) is not None
    }
    states = (
        {
            state.sheet_row: state
            for state in NeoRainGoogleRolloverState.query.filter(
                NeoRainGoogleRolloverState.sort_date_operation_id == operation.id,
                NeoRainGoogleRolloverState.sheet_row.in_(sheet_rows),
            ).all()
        }
        if sheet_rows
        else {}
    )

    released_rows = []
    baseline_count = 0
    released_count = 0
    for row in supplied_rows:
        sheet_row = _positive_int(row.get("sheet_row"))
        if sheet_row is None:
            # The production adapter always supplies a sheet row. Without one,
            # there is no durable cell identity, so fail closed for Rain values.
            continue

        current_values = {
            field: _normalized_cell_value(field, row.get(field), operation)
            for field in GOOGLE_RAIN_ROLLOVER_FIELDS
            if field in row
        }
        state = states.get(sheet_row)
        if state is None:
            state = NeoRainGoogleRolloverState(
                sort_date_operation_id=operation.id,
                sheet_row=sheet_row,
                baseline_values_json=_dump_json(current_values),
                released_fields_json="[]",
            )
            db.session.add(state)
            states[sheet_row] = state
            baseline_count += 1
            continue

        baseline = _load_object(state.baseline_values_json)
        released = _load_field_set(state.released_fields_json)
        if baseline is None:
            # Corrupt fence metadata must fail closed rather than release live
            # Google values into a new canonical sort.
            state.baseline_values_json = _dump_json(current_values)
            state.released_fields_json = "[]"
            continue
        filtered = dict(row)
        row_has_released_value = False
        state_changed = False
        for field in GOOGLE_RAIN_ROLLOVER_FIELDS:
            if field not in row:
                continue
            if field not in baseline:
                baseline[field] = current_values[field]
                filtered.pop(field, None)
                state_changed = True
                continue
            if field not in released and current_values[field] != baseline.get(field):
                released.add(field)
                released_count += 1
                state_changed = True
            if field in released:
                row_has_released_value = True
            else:
                filtered.pop(field, None)

        if state_changed:
            state.baseline_values_json = _dump_json(baseline)
            state.released_fields_json = _dump_json(sorted(released))
        if row_has_released_value:
            released_rows.append(filtered)

    db.session.flush()
    return {
        "status": "active",
        "rows": tuple(released_rows),
        "baseline_count": baseline_count,
        "released_count": released_count,
    }


def _normalized_cell_value(field, value, operation):
    if field in {"neo_fuel", "center_fuel"}:
        return str(value or "").strip().casefold() or "blank"
    if field == "no_return":
        if isinstance(value, bool):
            return "true" if value else "false"
        normalized = str(value if value is not None else "").strip().lower()
        if normalized in {"1", "true", "yes", "on", "checked"}:
            return "true"
        if normalized in {"", "-", "0", "false", "no", "off", "unchecked"}:
            return "false"
        return f"invalid:{normalized}"

    try:
        _local_value, timestamp_utc = _parse_optional_live_datetime(
            value,
            operation,
            _FIELD_LABELS[field],
        )
    except GoogleMotherBrainMissionError:
        return f"invalid:{str(value if value is not None else '').strip().casefold()}"
    if timestamp_utc is None:
        return "blank"
    return f"utc:{timestamp_utc.replace(second=0, microsecond=0).isoformat()}"


def _load_object(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_field_set(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(parsed, list):
        return set()
    return {
        field
        for field in parsed
        if field in GOOGLE_RAIN_ROLLOVER_FIELDS
    }


def _dump_json(value):
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _validate_operation(operation):
    if operation is None or not getattr(operation, "id", None):
        raise ValueError("A persisted current sort operation is required.")
    if not getattr(operation, "gateway", None):
        raise ValueError("The current sort operation must belong to a gateway.")
