from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import SortDateOperation, SortTimelineSettings
from app.services.gateway_matrix import (
    SORT_ORDER,
    active_sorts_for_gateway_date,
    current_gateway_local_datetime,
)
from app.services.sort_date_operations import generate_sort_date_operation_from_master


def ensure_operational_sort_operations(gateway, now=None):
    """Ensure operations whose configured planning lifecycle windows are active."""
    local_now = current_gateway_local_datetime(gateway, now=now)
    if not gateway or not gateway.is_active:
        return _empty_result(local_now)

    eligible_windows = _eligible_operation_windows(gateway, local_now)
    created_operations = []
    existing_operations = []
    errors = []

    for window in eligible_windows:
        operation = _operation_for_window(gateway, window)
        if operation:
            existing_operations.append(operation)
            window["operation"] = operation
            continue

        try:
            operation = generate_sort_date_operation_from_master(
                sort_date=window["sort_date"],
                gateway_code=gateway.code,
                sort_name=window["sort_name"],
                generated_by_user_id=None,
            )
            created_operations.append(operation)
            window["operation"] = operation
        except (IntegrityError, ValueError) as error:
            db.session.rollback()
            operation = _operation_for_window(gateway, window)
            if operation:
                existing_operations.append(operation)
                window["operation"] = operation
            else:
                errors.append(f"{window['sort_name']}: {error}")

    return {
        "sort_date": (
            eligible_windows[0]["sort_date"]
            if eligible_windows
            else local_now.date()
        ),
        "local_now": local_now,
        "eligible": eligible_windows,
        "created": created_operations,
        "existing": existing_operations,
        "errors": errors,
    }


def _empty_result(local_now):
    return {
        "sort_date": local_now.date(),
        "local_now": local_now,
        "eligible": [],
        "created": [],
        "existing": [],
        "errors": [],
    }


def _eligible_operation_windows(gateway, local_now):
    sort_settings = _sort_settings_for_gateway(gateway)
    eligible = []

    for sort_date in (local_now.date() - timedelta(days=1), local_now.date()):
        for sort_name in active_sorts_for_gateway_date(gateway, sort_date):
            sort_setting = sort_settings.get(sort_name)
            window = _configured_lifecycle_window(sort_setting, sort_date)
            if not window:
                continue
            start_local, end_local, source = window
            if start_local <= local_now < end_local:
                eligible.append(
                    {
                        "sort_date": sort_date,
                        "sort_name": sort_name,
                        "window_source": source,
                        "window_start_local": start_local,
                        "window_end_local": end_local,
                        "operation": None,
                    }
                )

    return sorted(
        eligible,
        key=lambda window: (
            window["window_start_local"],
            SORT_ORDER.get(window["sort_name"], len(SORT_ORDER)),
        ),
    )


def _sort_settings_for_gateway(gateway):
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        return {}
    return {
        str(sort_setting.sort_name or "").strip().lower(): sort_setting
        for sort_setting in settings.sort_settings
    }


def _configured_lifecycle_window(sort_setting, sort_date):
    if not sort_setting or not sort_setting.sort_window_end_local:
        return None

    start_time = (
        sort_setting.planning_start_local
        or sort_setting.sort_window_start_local
    )
    if not start_time:
        return None

    end_time = sort_setting.sort_window_end_local
    source = "planning" if sort_setting.planning_start_local else "sort"

    start_local = datetime.combine(sort_date, start_time)
    end_local = datetime.combine(sort_date, end_time)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local, end_local, source


def _operation_for_window(gateway, window):
    return SortDateOperation.query.filter_by(
        sort_date=window["sort_date"],
        gateway_code=gateway.code,
        sort_name=window["sort_name"],
    ).first()
