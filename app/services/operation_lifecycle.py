from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import SortDateOperation, SortTimelineSettings
from app.services.gateway_matrix import (
    SORT_ORDER,
    active_sorts_for_gateway_date,
    current_gateway_local_datetime,
)
from app.services.request_cache import request_cached
from app.services.sort_date_operations import generate_sort_date_operation_from_master


MANUAL_CURRENT_SORT_NAME = "night"


class ManualSortCreationError(ValueError):
    pass


def ensure_operational_sort_operations(
    gateway,
    now=None,
    *,
    local_now=None,
    sort_settings=None,
    active_sorts_by_date=None,
):
    """Ensure operations whose configured planning lifecycle windows are active."""
    local_now = local_now or current_gateway_local_datetime(gateway, now=now)
    if not gateway or not gateway.is_active:
        return _empty_result(local_now)

    eligible_windows = _eligible_operation_windows(
        gateway,
        local_now,
        sort_settings=sort_settings,
        active_sorts_by_date=active_sorts_by_date,
    )
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


def current_existing_operational_sort_operations(
    gateway,
    now=None,
    *,
    local_now=None,
    sort_settings=None,
    active_sorts_by_date=None,
):
    """Read-only resolution of existing operations in active lifecycle windows."""
    local_now = local_now or current_gateway_local_datetime(gateway, now=now)
    if not gateway or not gateway.is_active:
        return []

    def resolve():
        resolved_sort_settings = (
            sort_settings
            if sort_settings is not None
            else _sort_settings_for_gateway(gateway)
        )
        windows = _eligible_operation_windows(
            gateway,
            local_now,
            sort_settings=resolved_sort_settings,
            active_sorts_by_date=active_sorts_by_date,
        )
        keys = {
            (window["sort_date"], str(window["sort_name"]).strip().lower())
            for window in windows
        }
        candidate_dates = {local_now.date() - timedelta(days=1), local_now.date()}
        operations = (
            SortDateOperation.query.filter(
                SortDateOperation.archived_at_utc.is_(None),
                db.or_(
                    SortDateOperation.gateway_id == gateway.id,
                    SortDateOperation.gateway_code == gateway.code,
                ),
                SortDateOperation.sort_date.in_(candidate_dates),
            )
            .all()
        )
        by_key = {
            (operation.sort_date, str(operation.sort_name).strip().lower()): operation
            for operation in operations
        }
        scheduled_operations = [
            by_key[key]
            for key in (
                (window["sort_date"], str(window["sort_name"]).strip().lower())
                for window in windows
            )
            if key in by_key
        ]
        if scheduled_operations:
            return scheduled_operations

        manual_operations = []
        for operation in operations:
            if operation.generated_by_user_id is None:
                continue
            sort_name = str(operation.sort_name or "").strip().lower()
            configured_window = _configured_lifecycle_window(
                resolved_sort_settings.get(sort_name),
                operation.sort_date,
            )
            inside_window = bool(
                configured_window
                and configured_window[0] <= local_now < configured_window[1]
            )
            if operation.sort_date == local_now.date() or inside_window:
                manual_operations.append(operation)
        return sorted(
            manual_operations,
            key=lambda operation: (
                operation.sort_date,
                SORT_ORDER.get(
                    str(operation.sort_name or "").strip().lower(),
                    len(SORT_ORDER),
                ),
                operation.id,
            ),
        )

    return request_cached(
        "operation_lifecycle.current_existing_operations",
        (gateway.id, gateway.code, local_now),
        resolve,
    )


def manual_current_sort_creation_status(gateway, now=None, *, local_now=None):
    """Return read-only eligibility and existing-operation state for tonight."""
    local_now = local_now or current_gateway_local_datetime(gateway, now=now)
    sort_settings = _sort_settings_for_gateway(gateway) if gateway else {}
    sort_date = _manual_current_sort_date(local_now, sort_settings)
    current_operations = current_existing_operational_sort_operations(
        gateway,
        local_now=local_now,
        sort_settings=sort_settings,
    ) if gateway and gateway.is_active else []
    existing_operation = _operation_for_key(
        gateway,
        sort_date,
        MANUAL_CURRENT_SORT_NAME,
    ) if gateway else None
    return {
        "local_now": local_now,
        "sort_date": sort_date,
        "sort_name": MANUAL_CURRENT_SORT_NAME,
        "scheduled": bool(
            gateway
            and MANUAL_CURRENT_SORT_NAME
            in active_sorts_for_gateway_date(gateway, sort_date)
        ),
        "current_operation": current_operations[0] if current_operations else None,
        "existing_operation": existing_operation,
        "operation_exists": bool(current_operations or existing_operation),
    }


def create_manual_current_sort_operation(
    gateway,
    generated_by_user_id,
    *,
    allow_unscheduled=False,
    now=None,
    local_now=None,
):
    """Create tonight's canonical operation, returning the concurrent winner."""
    status = manual_current_sort_creation_status(
        gateway,
        now=now,
        local_now=local_now,
    )
    if status["current_operation"]:
        raise ManualSortCreationError("A current sort operation already exists.")
    if status["existing_operation"]:
        return {
            "operation": status["existing_operation"],
            "created": False,
            "status": status,
        }
    if not status["scheduled"] and not allow_unscheduled:
        raise ManualSortCreationError("Tonight is not a scheduled sort day.")

    try:
        operation = generate_sort_date_operation_from_master(
            sort_date=status["sort_date"],
            gateway_code=gateway.code,
            sort_name=status["sort_name"],
            generated_by_user_id=generated_by_user_id,
        )
    except (IntegrityError, ValueError) as error:
        db.session.rollback()
        operation = _operation_for_key(
            gateway,
            status["sort_date"],
            status["sort_name"],
        )
        if not operation:
            raise ManualSortCreationError(str(error)) from error
        return {"operation": operation, "created": False, "status": status}
    return {"operation": operation, "created": True, "status": status}


def _eligible_operation_windows(
    gateway,
    local_now,
    *,
    sort_settings=None,
    active_sorts_by_date=None,
):
    sort_settings = (
        sort_settings
        if sort_settings is not None
        else _sort_settings_for_gateway(gateway)
    )
    active_sorts_by_date = active_sorts_by_date or {}
    eligible = []

    for sort_date in (local_now.date() - timedelta(days=1), local_now.date()):
        active_sorts = active_sorts_by_date.get(sort_date)
        if active_sorts is None:
            active_sorts = active_sorts_for_gateway_date(gateway, sort_date)
        for sort_name in active_sorts:
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


def _manual_current_sort_date(local_now, sort_settings):
    previous_date = local_now.date() - timedelta(days=1)
    previous_window = _configured_lifecycle_window(
        sort_settings.get(MANUAL_CURRENT_SORT_NAME),
        previous_date,
    )
    if previous_window and previous_window[0] <= local_now < previous_window[1]:
        return previous_date
    return local_now.date()


def _operation_for_key(gateway, sort_date, sort_name):
    return SortDateOperation.query.filter_by(
        sort_date=sort_date,
        gateway_code=gateway.code,
        sort_name=sort_name,
    ).first()


def _operation_for_window(gateway, window):
    return _operation_for_key(
        gateway,
        window["sort_date"],
        window["sort_name"],
    )
