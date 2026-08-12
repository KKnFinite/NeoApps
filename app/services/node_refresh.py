from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    current_operations_for_gateway,
    ops_window_for_operation,
    sort_lookup_window_for_operation,
)


def node_auto_refresh_status(
    gateway,
    operation=None,
    now=None,
    active_message="Live updates on",
    before_message="Live updates off - outside Ops window",
    outside_message="Live updates off - outside Ops window",
):
    """Resolve live-screen eligibility independently from Flight API polling."""
    return _auto_refresh_status(
        gateway,
        operation=operation,
        now=now,
        window_resolver=ops_window_for_operation,
        before_reason="before_ops_window",
        outside_reason="outside_ops_window",
        active_message=active_message,
        before_message=before_message,
        outside_message=outside_message,
    )


def sort_window_auto_refresh_status(
    gateway,
    operation=None,
    now=None,
    active_message="Live updates on",
    before_message="Live updates off - outside Sort window",
    outside_message="Live updates off - outside Sort window",
):
    """Resolve live-screen eligibility against the physical Sort window."""
    return _auto_refresh_status(
        gateway,
        operation=operation,
        now=now,
        window_resolver=sort_lookup_window_for_operation,
        before_reason="before_sort_window",
        outside_reason="outside_sort_window",
        active_message=active_message,
        before_message=before_message,
        outside_message=outside_message,
    )


def _auto_refresh_status(
    gateway,
    *,
    operation,
    now,
    window_resolver,
    before_reason,
    outside_reason,
    active_message,
    before_message,
    outside_message,
):
    local_now = current_gateway_local_datetime(gateway, now=now)

    if operation is not None:
        return _status_for_selected_operation(
            gateway,
            operation,
            local_now,
            window_resolver=window_resolver,
            before_reason=before_reason,
            outside_reason=outside_reason,
            active_message=active_message,
            before_message=before_message,
            outside_message=outside_message,
        )

    operations = current_operations_for_gateway(gateway, now=local_now)
    active_operation = next(
        (
            candidate
            for candidate in operations
            if _operation_is_current_context(candidate, gateway, local_now)
            and _operation_is_inside_window(
                candidate,
                gateway,
                local_now,
                window_resolver,
            )
        ),
        None,
    )

    if active_operation:
        start_local, end_local = window_resolver(active_operation, gateway)
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
    for candidate in operations:
        if not _operation_is_current_context(candidate, gateway, local_now):
            continue
        start_local, end_local = window_resolver(candidate, gateway)
        if start_local and local_now < start_local:
            if not next_window[0] or start_local < next_window[0]:
                next_operation = candidate
                next_window = (start_local, end_local)

    if next_operation:
        return _refresh_status_payload(
            next_operation,
            next_window[0],
            next_window[1],
            local_now,
            active=False,
            reason=before_reason,
            message=before_message,
        )

    candidate = next(
        (
            row
            for row in operations
            if _operation_is_current_context(row, gateway, local_now)
        ),
        operations[0] if operations else None,
    )
    start_local, end_local = (
        window_resolver(candidate, gateway) if candidate else (None, None)
    )
    return _refresh_status_payload(
        candidate,
        start_local,
        end_local,
        local_now,
        active=False,
        reason=outside_reason,
        message=outside_message,
    )


def _status_for_selected_operation(
    gateway,
    operation,
    local_now,
    *,
    window_resolver,
    before_reason,
    outside_reason,
    active_message,
    before_message,
    outside_message,
):
    start_local, end_local = window_resolver(operation, gateway)
    if not _operation_is_current_context(operation, gateway, local_now):
        return _refresh_status_payload(
            operation,
            start_local,
            end_local,
            local_now,
            active=False,
            reason="historical_sort",
            message="Live updates off - historical sort",
        )

    active = bool(start_local <= local_now < end_local)
    reason = (
        "active"
        if active
        else _outside_window_reason(
            local_now,
            start_local,
            before_reason,
            outside_reason,
        )
    )
    return _refresh_status_payload(
        operation,
        start_local,
        end_local,
        local_now,
        active=active,
        reason=reason,
        message=(
            active_message
            if active
            else before_message if reason == before_reason else outside_message
        ),
    )


def _operation_is_inside_window(operation, gateway, local_now, window_resolver):
    start_local, end_local = window_resolver(operation, gateway)
    return bool(start_local and end_local and start_local <= local_now < end_local)


def _operation_is_current_context(operation, gateway, local_now):
    if not operation or operation.archived_at_utc is not None:
        return False
    if operation.gateway_code != gateway.code:
        return False

    # Sort lookup identifies whether this is the current operational context.
    # The caller-selected window resolver controls live refresh eligibility.
    start_local, end_local = sort_lookup_window_for_operation(operation, gateway)
    return bool(start_local <= local_now < end_local)


def _outside_window_reason(local_now, start_local, before_reason, outside_reason):
    return before_reason if local_now < start_local else outside_reason


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
        "live_status_label": message,
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
