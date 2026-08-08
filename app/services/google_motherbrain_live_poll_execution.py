"""Server-side execution of the enabled locked MotherBrain live Google poll."""

from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.services.flight_api import api_polling_window_for_operation
from app.services.gateway_matrix import current_gateway_local_datetime
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
from app.services.operation_lifecycle import ensure_operational_sort_operations
from app.services.sort_timeline import ensure_sort_timeline_settings


def execute_google_motherbrain_live_poll(gateway, now=None, *, reader=None, applier=None):
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
    return {
        "status": "success",
        "operation_id": operation.id,
        "applied_count": application.get("applied_count", 0),
        "skipped_count": application.get("skipped_count", 0),
    }


def _polling_window_operation(gateway, lifecycle, now=None):
    """Return the locked workbook operation only while its polling window is live."""
    if str(gateway.code or "").strip().upper() != GOOGLE_MOTHERBRAIN_GATEWAY_CODE:
        return None

    local_now = lifecycle.get("local_now") or current_gateway_local_datetime(gateway, now=now)
    settings = ensure_sort_timeline_settings(gateway)
    for candidate in lifecycle.get("eligible", ()):
        operation = candidate.get("operation")
        if operation is None:
            continue
        if str(operation.sort_name or "").strip().lower() != GOOGLE_MOTHERBRAIN_SORT_NAME:
            continue
        start_local, end_local = api_polling_window_for_operation(operation, settings)
        if start_local and end_local and start_local <= local_now < end_local:
            return operation
    return None
