"""Safe display status for the server-resolved Google MotherBrain live poll."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import MotherBrainGoogleLivePollState
from app.services.gateway_matrix import gateway_timezone
from app.services.google_motherbrain_import import (
    GOOGLE_MOTHERBRAIN_SORT_NAME,
)
from app.services.google_motherbrain_live_poll_execution import (
    _polling_window_operation,
)
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_status,
)
from app.services.operation_lifecycle import ensure_operational_sort_operations


GOOGLE_LIVE_POLL_STALE_AFTER = timedelta(minutes=3)


def google_motherbrain_live_poll_health(gateway, now=None, *, lifecycle=None):
    """Return a safe current-sort sync summary without reading Google.

    The selected operation-detail URL is deliberately not accepted here.  The
    current lifecycle scope and active Polling Window determine which durable
    poll state, if any, is safe to display.
    """
    polling = google_motherbrain_live_polling_status(
        gateway,
        GOOGLE_MOTHERBRAIN_SORT_NAME,
    )
    health = {
        "enabled": polling["enabled"],
        "status": "off",
        "label": "OFF",
        "polling_window_active": False,
        "operation_id": None,
        "sort_date": None,
        "last_success_label": "Never",
    }
    if not polling["enabled"]:
        return health

    lifecycle = lifecycle or ensure_operational_sort_operations(gateway, now=now)
    operation = _polling_window_operation(gateway, lifecycle, now=now)
    if operation is None:
        health.update(
            {
                "status": "outside_window",
                "label": "Outside Polling Window",
            }
        )
        return health

    state = MotherBrainGoogleLivePollState.query.filter_by(
        gateway_id=gateway.id,
        sort_name=GOOGLE_MOTHERBRAIN_SORT_NAME,
        sort_date=operation.sort_date,
    ).first()
    health.update(
        {
            "polling_window_active": True,
            "operation_id": operation.id,
            "sort_date": operation.sort_date,
            "last_success_label": _last_success_label(
                state.last_success_at_utc if state else None,
                gateway,
            ),
        }
    )

    if state and state.last_error:
        health.update({"status": "error", "label": "Error"})
    elif _is_current(state.last_success_at_utc if state else None, now):
        health.update({"status": "current", "label": "Current"})
    else:
        health.update({"status": "stale", "label": "Stale"})
    return health


def _is_current(last_success_at_utc, now):
    if not last_success_at_utc:
        return False
    return _utc_naive(now) - _utc_naive(last_success_at_utc) < GOOGLE_LIVE_POLL_STALE_AFTER


def _last_success_label(last_success_at_utc, gateway):
    if not last_success_at_utc:
        return "Never"
    try:
        local_time = _utc_naive(last_success_at_utc).replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo(gateway_timezone(gateway))
        )
    except ZoneInfoNotFoundError:
        local_time = _utc_naive(last_success_at_utc).replace(tzinfo=timezone.utc)
    return local_time.strftime("%H:%M")


def _utc_naive(value=None):
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
