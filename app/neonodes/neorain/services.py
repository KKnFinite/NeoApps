"""Read-only current-sort data for NeoRain operational screens."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import SortDateMission, SortDateParkingAssignment
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.node_refresh import node_auto_refresh_status
from app.services.operation_scope import current_operational_sort_operation
from app.services.sort_date_operations import mission_display_timing_data
from app.services.time_display import format_local_hhmm


NEORAIN_OUTBOUND_REFRESH_KEY = "neorain.outbound"
_OPERATION_UNSET = object()


def neorain_outbound_context(gateway, *, operation=_OPERATION_UNSET):
    """Build the current-sort Outbound board without mutating operational state."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    refresh = neorain_outbound_refresh_status(gateway, operation=operation)
    return {
        "operation": operation,
        "rows": _outbound_rows(operation),
        "refresh_status": refresh,
    }


def current_neorain_outbound_operation(gateway):
    """Return an existing lifecycle-current operation, never a historical fallback."""
    return current_operational_sort_operation(gateway)


def neorain_outbound_refresh_status(gateway, *, operation=None):
    """Apply the shared live-screen interval to the shared operational window policy."""
    status = dict(node_auto_refresh_status(gateway, operation=operation))
    setting = live_screen_refresh_value(gateway, NEORAIN_OUTBOUND_REFRESH_KEY)
    status["live_screen_refresh_interval_ms"] = setting.effective_interval_ms
    if not setting.enabled:
        status["auto_refresh_enabled"] = False
        status["reason"] = "disabled"
        status["message"] = "Live updates off"
        status["live_status_label"] = "Live updates off"
    return status


def neorain_outbound_revision(gateway, *, operation=_OPERATION_UNSET):
    """Compact revision for the persisted inputs rendered on the Outbound board."""
    if operation is _OPERATION_UNSET:
        operation = current_neorain_outbound_operation(gateway)
    operation_id = operation.id if operation else None
    criterion = (
        SortDateMission.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateMission.sort_date_operation_id.is_(None)
    )
    parking_criterion = (
        SortDateParkingAssignment.sort_date_operation_id == operation_id
        if operation_id is not None
        else SortDateParkingAssignment.sort_date_operation_id.is_(None)
    )
    aggregates = (
        _revision_aggregate(
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            criterion,
            SortDateMission.mission_type == "departure",
        ),
        _revision_aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            parking_criterion,
        ),
    )
    rows = sorted(
        db.session.execute(union_all(*aggregates)).all(),
        key=lambda row: row.source,
    )
    payload = {
        "gateway_id": gateway.id,
        "operation_id": operation_id,
        "operation_updated_at": _revision_value(getattr(operation, "updated_at", None)),
        "inputs": [
            {
                "source": row.source,
                "row_count": int(row.row_count or 0),
                "max_id": int(row.max_id or 0),
                "id_sum": int(row.id_sum or 0),
                "latest_updated_at": _revision_value(row.latest_updated_at),
            }
            for row in rows
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _outbound_rows(operation):
    if operation is None:
        return []
    parking_by_tail = {
        _tail_key(assignment.tail_number): _text(assignment.position_code)
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
        ).all()
        if _tail_key(assignment.tail_number)
    }
    missions = SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).all()
    rows = [_outbound_row(mission, operation, parking_by_tail) for mission in missions]
    return sorted(rows, key=_row_sort_key)


def _outbound_row(mission, operation, parking_by_tail):
    timing = mission_display_timing_data(mission, operation)
    planned = timing.get("adjusted_planned_departure_time") or mission.planned_datetime_local
    timezone_name = mission.timezone or None
    status = str(mission.departure_status or "scheduled").strip().lower()
    return {
        "wave": timing.get("wave") or "-",
        "flight_number": _text(mission.flight_number),
        "tail": _text(mission.assigned_tail_number),
        "destination": _text(mission.destination),
        "parking": parking_by_tail.get(_tail_key(mission.assigned_tail_number), ""),
        "planned_time": _time_value(planned),
        "status": status.replace("_", " ").upper(),
        "elmac": format_local_hhmm(mission.elmac_completed_at_utc, timezone_name),
        "ramp_load_complete": format_local_hhmm(
            mission.ramp_load_completed_at_utc, timezone_name
        ),
        "crew_load_complete": format_local_hhmm(
            mission.crew_load_completed_at_utc, timezone_name
        ),
        "official_block_out": format_local_hhmm(
            mission.actual_block_out_datetime_utc, timezone_name
        ),
        "no_return": "NO RETURN" if status == "departed" else "",
        "sort_time": planned,
        "mission_id": mission.id,
    }


def _revision_aggregate(source, model, timestamp_column, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _row_sort_key(row):
    return (
        row["sort_time"] is None,
        row["sort_time"] or datetime.max,
        row["flight_number"],
        row["mission_id"],
    )


def _tail_key(value):
    return _text(value)


def _text(value):
    return str(value or "").strip().upper()


def _time_value(value):
    return value.strftime("%H:%M") if value else ""


def _revision_value(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return str(value or "")
