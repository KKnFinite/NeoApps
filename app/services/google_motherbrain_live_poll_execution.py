"""Server-side execution of the enabled locked MotherBrain live Google poll."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models import SortDateOperation
from app.services.gateway_matrix import (
    active_sorts_for_gateway_date,
    current_gateway_local_datetime,
)
from app.services.google_motherbrain_import import (
    GOOGLE_MOTHERBRAIN_GATEWAY_CODE,
    GOOGLE_MOTHERBRAIN_SORT_NAME,
)
from app.services.google_motherbrain_live_missions import (
    apply_google_motherbrain_live_rows,
)
from app.services.google_motherbrain_live_poll_lease import (
    acquire_google_motherbrain_live_poll_lease,
    complete_google_motherbrain_live_poll_failure,
    complete_google_motherbrain_live_poll_success,
)
from app.services.google_motherbrain_sheets import read_google_motherbrain_live_rows
from app.services.google_rain_live_milestones import (
    apply_google_rain_departure_milestones,
)
from app.services.google_rain_sheets import read_google_rain_outbound_milestones
from app.services.operation_lifecycle import ensure_operational_sort_operations
from app.services.sort_timeline import ensure_sort_timeline_settings, sort_settings_by_name


def execute_google_motherbrain_live_poll(
    gateway,
    now=None,
    *,
    reader=None,
    applier=None,
    rain_reader=None,
    rain_applier=None,
):
    """Run one server-resolved live poll without accepting client scope input."""
    lifecycle = ensure_operational_sort_operations(gateway, now=now)
    if lifecycle["errors"]:
        db.session.rollback()
        return {"status": "lifecycle_error"}

    # Lifecycle-created operations must be durable before another worker can lease them.
    db.session.commit()
    operation = _polling_window_operation(gateway, lifecycle, now=now)
    if operation is None:
        return {"status": "outside_window"}

    acquired = acquire_google_motherbrain_live_poll_lease(operation, now=now)
    if not acquired.acquired:
        return {"status": acquired.status, "operation_id": operation.id}

    reader = reader or read_google_motherbrain_live_rows
    applier = applier or apply_google_motherbrain_live_rows
    try:
        live_rows = reader()
        application = applier(
            operation,
            inbound_rows=live_rows.get("inbound_rows", ()),
            outbound_rows=live_rows.get("outbound_rows", ()),
            now=now,
        )
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning(
            "Google MotherBrain live poll failed safely: operation_id=%s error=%s",
            operation.id,
            type(error).__name__,
        )
        complete_google_motherbrain_live_poll_failure(acquired.lease, error, now=now)
        return {"status": "failed", "operation_id": operation.id}

    if not complete_google_motherbrain_live_poll_success(acquired.lease, now=now):
        current_app.logger.warning(
            "Google MotherBrain live poll completed after its lease expired: operation_id=%s",
            operation.id,
        )
        return {"status": "lease_lost", "operation_id": operation.id}
    rain_result = _run_google_rain_best_effort(
        operation,
        now=now,
        reader=rain_reader,
        applier=rain_applier,
    )
    return {
        "status": "success",
        "operation_id": operation.id,
        "applied_count": application.get("applied_count", 0),
        "skipped_count": application.get("skipped_count", 0),
        "rain_status": rain_result["status"],
        "rain_applied_count": rain_result.get("applied_count", 0),
        "rain_skipped_count": rain_result.get("skipped_count", 0),
    }


def _run_google_rain_best_effort(operation, *, now=None, reader=None, applier=None):
    """Run Rain after the primary poll is durable; never undo that success."""
    if current_app.config.get("TESTING") and reader is None and applier is None:
        return {"status": "not_run"}

    reader = reader or read_google_rain_outbound_milestones
    applier = applier or apply_google_rain_departure_milestones
    try:
        rows = reader()
        application = applier(operation, rows=rows, now=now)
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning(
            "Google Rain milestone poll failed safely: operation_id=%s error=%s",
            operation.id,
            type(error).__name__,
        )
        return {"status": "failed"}

    return {
        "status": "success",
        "applied_count": application.get("applied_count", 0),
        "skipped_count": application.get("skipped_count", 0),
    }


def _polling_window_operation(gateway, lifecycle, now=None):
    """Return the locked workbook operation while its physical Sort Window is live."""
    if str(gateway.code or "").strip().upper() != GOOGLE_MOTHERBRAIN_GATEWAY_CODE:
        return None

    local_now = lifecycle.get("local_now") or current_gateway_local_datetime(gateway, now=now)
    settings = ensure_sort_timeline_settings(gateway)
    candidate_dates = (local_now.date() - timedelta(days=1), local_now.date())
    operations = (
        SortDateOperation.query.filter(
            SortDateOperation.gateway_code == gateway.code,
            SortDateOperation.sort_name == GOOGLE_MOTHERBRAIN_SORT_NAME,
            SortDateOperation.sort_date.in_(candidate_dates),
            SortDateOperation.archived_at_utc.is_(None),
        )
        .order_by(SortDateOperation.sort_date.desc(), SortDateOperation.id.desc())
        .all()
    )
    for operation in operations:
        if operation.sort_name not in active_sorts_for_gateway_date(
            gateway,
            operation.sort_date,
        ):
            continue
        start_local, end_local = google_polling_window_for_operation(operation, settings)
        if start_local and end_local and start_local <= local_now < end_local:
            return operation
    return None


def google_polling_window_for_operation(operation, settings):
    """Resolve the physical Sort Window used by both Google read adapters."""
    sort_name = str(operation.sort_name or "").strip().lower()
    sort_setting = sort_settings_by_name(settings).get(sort_name)
    start_time = getattr(sort_setting, "sort_window_start_local", None)
    end_time = getattr(sort_setting, "sort_window_end_local", None)
    if not start_time or not end_time:
        return None, None

    start_local = datetime.combine(operation.sort_date, start_time)
    end_local = datetime.combine(operation.sort_date, end_time)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local, end_local
