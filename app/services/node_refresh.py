from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    current_operations_for_gateway,
    operation_is_active_at,
    sort_lookup_window_for_operation,
)


def node_auto_refresh_status(
    gateway,
    now=None,
    active_message="Auto-refresh active for current operation window.",
    before_message="Auto-refresh paused until the current operation window opens.",
    outside_message="Auto-refresh paused outside the current operation window.",
):
    local_now = current_gateway_local_datetime(gateway, now=now)
    operations = current_operations_for_gateway(gateway, now=local_now)
    active_operation = next(
        (
            operation
            for operation in operations
            if operation_is_active_at(operation, local_now, gateway)
        ),
        None,
    )

    if active_operation:
        start_local, end_local = sort_lookup_window_for_operation(active_operation, gateway)
        return _refresh_status_payload(
            active_operation,
            start_local,
            end_local,
            local_now,
            active=True,
            reason="active",
            message=active_message,
        )

    next_operation = None
    next_window = (None, None)
    for operation in operations:
        start_local, end_local = sort_lookup_window_for_operation(operation, gateway)
        if start_local and local_now < start_local:
            if not next_window[0] or start_local < next_window[0]:
                next_operation = operation
                next_window = (start_local, end_local)

    if next_operation:
        return _refresh_status_payload(
            next_operation,
            next_window[0],
            next_window[1],
            local_now,
            active=False,
            reason="before_operation_window",
            message=before_message,
        )

    operation = operations[0] if operations else None
    start_local, end_local = (
        sort_lookup_window_for_operation(operation, gateway) if operation else (None, None)
    )
    return _refresh_status_payload(
        operation,
        start_local,
        end_local,
        local_now,
        active=False,
        reason="outside_operation_window",
        message=outside_message,
    )


def _refresh_status_payload(
    operation,
    start_local,
    end_local,
    local_now,
    active=False,
    reason="",
    message="",
):
    return {
        "auto_refresh_enabled": bool(active),
        "is_operation_active": bool(active),
        "reason": reason,
        "message": message,
        "operation_id": operation.id if operation else None,
        "operation_label": _operation_label(operation) if operation else "",
        "sort_date": operation.sort_date.isoformat() if operation else "",
        "sort_name": operation.sort_name.upper() if operation else "",
        "local_now": _time_label(local_now),
        "window_start_local": _time_label(start_local),
        "window_end_local": _time_label(end_local),
        "window_label": _window_label(start_local, end_local),
        "next_check_seconds": None,
    }


def _operation_label(operation):
    if not operation:
        return ""
    sort_date = operation.sort_date
    date_label = f"{sort_date.month}/{sort_date.day}/{str(sort_date.year)[-2:]}"
    return f"{operation.gateway_code} {operation.sort_name.upper()} {date_label}"


def _time_label(value):
    if not value:
        return ""
    return value.strftime("%H:%M")


def _window_label(start_local, end_local):
    if not start_local or not end_local:
        return ""
    return f"{_time_label(start_local)}-{_time_label(end_local)}"
