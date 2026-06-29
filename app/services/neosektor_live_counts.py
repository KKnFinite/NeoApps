from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount,
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorOperationalSetting,
    NeoSektorSortState,
    NeoSektorWaveState,
)
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    current_operations_for_gateway,
    operation_is_active_at,
    sort_lookup_window_for_operation,
)


STATUS_LABELS = ("Empty", "Light", "Moderate", "Full", "Overflowing")
STATUS_RANKS = {label: index for index, label in enumerate(STATUS_LABELS)}
DEFAULT_SORT_NAME = "night"
DEFAULT_ACTIVE_WAVE = "1ST WAVE"
DEFAULT_WAVES = (
    ("first", "1ST WAVE"),
    ("second", "2ND WAVE"),
)
DEFAULT_BALLMAT_SIDES = (
    ("east", "EAST", "EBM"),
    ("west", "WEST", "WBM"),
)
DEFAULT_BAYS = (
    ("EAST", "Bay 1"),
    ("EAST", "Bay 2"),
    ("EAST", "Bay 3"),
    ("WEST", "Bay 4"),
    ("WEST", "Bay 5"),
)
DRIVER_ROUTE_FIRST_WAVE_NAME = "1ST WAVE ROUTE"
DRIVER_ROUTE_SECOND_WAVE_NAME = "2ND WAVE ROUTE"
DRIVER_ROUTE_WEST_OFFSET_NAME = "WEST OFFSET"
DEFAULT_DRIVER_ROUTES = (
    DRIVER_ROUTE_FIRST_WAVE_NAME,
    DRIVER_ROUTE_SECOND_WAVE_NAME,
    DRIVER_ROUTE_WEST_OFFSET_NAME,
)
DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER = 45
DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER = 37
DEFAULT_ALL_UP_TO_DOWN_MINUTES = 15
UNLOAD_MODIFIER_MAX = 999
ALL_UP_TO_DOWN_MINUTES_MAX = 120
MAIN_BALLMAT_COUNT_MAX = 99
LEFT_TO_ARRIVE_MAX = 999
DRIVER_OFFSET_MAX = 20
TUNNEL_CONDUCTOR_VIEW_PERMISSION = "neosektor.conductor.view"
TUNNEL_CONDUCTOR_EDIT_PERMISSION = "neosektor.tunnel_conductor.edit"


def live_counts_context(gateway, sort_date=None, sort_name=None):
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    refresh_status = neosektor_refresh_status(gateway)

    ballmat_wave_counts = _get_or_create_ballmat_wave_counts(sort_state)
    waves = _get_or_create_waves(sort_state)
    ballmats = _get_or_create_ballmats(sort_state)
    open_bays = _get_or_create_open_bays(sort_state)
    bay_statuses = _get_or_create_bay_statuses(sort_state)
    driver_routes = _get_or_create_driver_routes(sort_state)
    operational_settings = get_or_create_operational_settings(gateway)
    _sync_ballmat_rollups(sort_state, ballmat_wave_counts, waves, ballmats)
    side_views = _side_state_views(ballmat_wave_counts, ballmats, open_bays, bay_statuses)
    wave_views = _wave_views(waves, side_views, operational_settings)
    routing = _driver_routing_calculation(sort_state, side_views, driver_routes)
    _sync_driver_route_values(driver_routes, routing)
    db.session.flush()

    planned_total = max(0, sort_state.planned_total or 0)
    unloaded_total = max(0, sort_state.unloaded_total or 0)
    left_to_unload = max(planned_total - unloaded_total, 0)

    return {
        "sort_state": sort_state,
        "status_labels": STATUS_LABELS,
        "summary": {
            "sort_date": sort_state.sort_date,
            "sort_name": sort_state.sort_name.upper(),
            "active_wave": sort_state.active_wave,
            "planned_total": planned_total,
            "unloaded_total": unloaded_total,
            "left_to_unload": left_to_unload,
            "completion_percent": _completion_percent(planned_total, unloaded_total),
        },
        "waves": wave_views,
        "sides": side_views,
        "ballmats": [_ballmat_view(row) for row in ballmats],
        "open_bays": [_open_bay_view(row) for row in open_bays],
        "bay_statuses": [_bay_status_view(row) for row in bay_statuses],
        "driver_routes": [_driver_route_view(row) for row in driver_routes],
        "operational_settings": _operational_settings_view(operational_settings),
        "refresh_status": refresh_status,
    }


def ballmat_operations_context(gateway, selected_side, sort_date=None, sort_name=None):
    selected_side = normalize_ballmat_side(selected_side) or "east"
    state = ballmat_state_payload(gateway, sort_date, sort_name)

    return {
        "selected_side": selected_side,
        "selected_side_label": side_display_label(selected_side),
        "selected_manager_label": side_manager_label(selected_side),
        "state": state,
        "status_labels": STATUS_LABELS,
    }


def ballmat_state_payload(gateway, sort_date=None, sort_name=None):
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    refresh_status = neosektor_refresh_status(gateway)

    ballmat_wave_counts = _get_or_create_ballmat_wave_counts(sort_state)
    waves = _get_or_create_waves(sort_state)
    ballmats = _get_or_create_ballmats(sort_state)
    open_bays = _get_or_create_open_bays(sort_state)
    bay_statuses = _get_or_create_bay_statuses(sort_state)
    operational_settings = get_or_create_operational_settings(gateway)
    _sync_ballmat_rollups(sort_state, ballmat_wave_counts, waves, ballmats)
    side_views = _side_state_views(
        ballmat_wave_counts,
        ballmats,
        open_bays,
        bay_statuses,
    )
    wave_views = _wave_views(waves, side_views, operational_settings)
    db.session.flush()

    planned_total = max(0, sort_state.planned_total or 0)
    unloaded_total = max(0, sort_state.unloaded_total or 0)

    return {
        "summary": {
            "sort_date": sort_state.sort_date.isoformat(),
            "sort_name": sort_state.sort_name.upper(),
            "active_wave": sort_state.active_wave,
            "planned_total": planned_total,
            "unloaded_total": unloaded_total,
            "left_to_unload": max(planned_total - unloaded_total, 0),
            "completion_percent": _completion_percent(planned_total, unloaded_total),
        },
        "sides": side_views,
        "waves": wave_views,
        "operational_settings": _operational_settings_view(operational_settings),
        "refresh": refresh_status,
    }


def neosektor_refresh_status(gateway, now=None):
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
            message="Auto-refresh active for current operation window.",
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
            message="Auto-refresh paused until the current operation window opens.",
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
        message="Auto-refresh paused outside the current operation window.",
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
    next_check_seconds = None
    if not active and start_local and local_now < start_local:
        next_check_seconds = max(int((start_local - local_now).total_seconds()), 1)

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
        "next_check_seconds": next_check_seconds,
    }


def update_ballmat_side(gateway, selected_side, payload, sort_date=None, sort_name=None):
    selected_side = normalize_ballmat_side(selected_side)
    target_side = normalize_ballmat_side((payload or {}).get("side"))
    if not selected_side or not target_side or selected_side != target_side:
        raise ValueError("Selected side does not match update side.")

    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)

    ballmat_wave_counts = _get_or_create_ballmat_wave_counts(sort_state)
    waves = _get_or_create_waves(sort_state)
    ballmats = _get_or_create_ballmats(sort_state)
    open_bays = _get_or_create_open_bays(sort_state)
    bay_statuses = _get_or_create_bay_statuses(sort_state)

    side_label = side_display_label(selected_side)
    wave_payload = (payload or {}).get("waves") or {}
    rows_by_wave = {
        row.wave_name: row
        for row in ballmat_wave_counts
        if row.side == side_label
    }
    for wave_key, wave_name in DEFAULT_WAVES:
        row = rows_by_wave[wave_name]
        row.count = _clean_count(
            (wave_payload.get(wave_key) or {}).get("count"),
            default=row.count,
            maximum=MAIN_BALLMAT_COUNT_MAX,
        )
        row.status = _status((wave_payload.get(wave_key) or {}).get("status") or row.status)

    open_bay_row = next(row for row in open_bays if row.side == side_label)
    open_bay_row.open_count = _clean_count(
        (payload or {}).get("open_bays"),
        default=open_bay_row.open_count,
        maximum=MAIN_BALLMAT_COUNT_MAX,
    )

    bay_payload = (payload or {}).get("bay_statuses") or {}
    for bay in bay_statuses:
        if bay.side == side_label and bay.bay_name in bay_payload:
            bay.status = _status(bay_payload[bay.bay_name])

    _sync_ballmat_rollups(sort_state, ballmat_wave_counts, waves, ballmats)
    db.session.flush()
    return ballmat_state_payload(gateway, sort_date, sort_name)


def tunnel_conductor_context(gateway, sort_date=None, sort_name=None):
    return {
        "state": driver_routing_state_payload(gateway, sort_date, sort_name),
        "status_labels": STATUS_LABELS,
    }


def driver_routing_context(gateway, sort_date=None, sort_name=None):
    return {
        "state": driver_routing_state_payload(gateway, sort_date, sort_name),
    }


def driver_routing_state_payload(gateway, sort_date=None, sort_name=None):
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    state = ballmat_state_payload(gateway, sort_date, sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    driver_routes = _get_or_create_driver_routes(sort_state)
    routing = _driver_routing_calculation(sort_state, state["sides"], driver_routes)
    _sync_driver_route_values(driver_routes, routing)
    db.session.flush()

    state["routing"] = routing
    state["driver_routes"] = [_driver_route_view(row) for row in driver_routes]
    return state


def update_driver_routing_settings(gateway, payload, sort_date=None, sort_name=None):
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    driver_routes = _get_or_create_driver_routes(sort_state)
    offset_row = _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME)
    offset_row.route_value = str(_clean_offset((payload or {}).get("west_offset")))
    db.session.flush()
    return driver_routing_state_payload(gateway, sort_date, sort_name)


def update_tunnel_driver_offset(gateway, payload, sort_date=None, sort_name=None):
    return update_driver_routing_settings(gateway, payload, sort_date, sort_name)


def update_neosektor_operational_settings(gateway, payload, sort_date=None, sort_name=None):
    settings = get_or_create_operational_settings(gateway)
    settings.first_wave_unload_modifier = _clean_count(
        (payload or {}).get("first_modifier"),
        default=settings.first_wave_unload_modifier,
        maximum=UNLOAD_MODIFIER_MAX,
    )
    settings.second_wave_unload_modifier = _clean_count(
        (payload or {}).get("second_modifier"),
        default=settings.second_wave_unload_modifier,
        maximum=UNLOAD_MODIFIER_MAX,
    )
    settings.all_up_to_down_minutes = _clean_count(
        (payload or {}).get("down_timer_minutes"),
        default=settings.all_up_to_down_minutes,
        minimum=1,
        maximum=ALL_UP_TO_DOWN_MINUTES_MAX,
    )
    db.session.flush()
    return driver_routing_state_payload(gateway, sort_date, sort_name)


def adjust_tunnel_wave_arrivals(gateway, wave, delta=None, value=None, sort_date=None, sort_name=None):
    _wave_key, wave_name = normalize_wave_key(wave)
    if not wave_name:
        raise ValueError("Invalid wave.")

    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)
    waves = _get_or_create_waves(sort_state)

    target_row = next(row for row in waves if row.wave_name == wave_name)
    if value is not None:
        target_row.planned_count = _clean_count(
            value,
            default=target_row.planned_count,
            maximum=LEFT_TO_ARRIVE_MAX,
        )
    else:
        target_row.planned_count = min(
            max((target_row.planned_count or 0) + _clean_delta(delta), 0),
            LEFT_TO_ARRIVE_MAX,
        )
    db.session.flush()
    return driver_routing_state_payload(gateway, sort_date, sort_name)


def get_or_create_sort_state(gateway, sort_date, sort_name):
    sort_state = NeoSektorSortState.query.filter_by(
        gateway_id=gateway.id,
        sort_date=sort_date,
        sort_name=sort_name,
    ).first()
    if sort_state:
        return sort_state

    sort_state = NeoSektorSortState(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        sort_date=sort_date,
        sort_name=sort_name,
        active_wave=DEFAULT_ACTIVE_WAVE,
    )
    db.session.add(sort_state)
    db.session.flush()
    return sort_state


def get_or_create_operational_settings(gateway):
    settings = NeoSektorOperationalSetting.query.filter_by(
        gateway_id=gateway.id,
    ).first()
    if settings:
        return settings

    settings = NeoSektorOperationalSetting(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        first_wave_unload_modifier=DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER,
        second_wave_unload_modifier=DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER,
        all_up_to_down_minutes=DEFAULT_ALL_UP_TO_DOWN_MINUTES,
    )
    db.session.add(settings)
    db.session.flush()
    return settings


def normalize_sort_name(sort_name):
    value = str(sort_name or "").strip().lower()
    return value or DEFAULT_SORT_NAME


def normalize_ballmat_side(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"e", "east", "ebm"}:
        return "east"
    if normalized in {"w", "west", "wbm"}:
        return "west"
    return None


def side_display_label(side):
    normalized = normalize_ballmat_side(side) or "east"
    return "EAST" if normalized == "east" else "WEST"


def side_manager_label(side):
    normalized = normalize_ballmat_side(side) or "east"
    return "EBM" if normalized == "east" else "WBM"


def normalize_wave_key(value):
    normalized = str(value or "").strip().lower()
    for wave_key, wave_name in DEFAULT_WAVES:
        wave_aliases = {
            wave_key,
            wave_name.lower(),
            wave_name.lower().replace(" ", "_"),
            wave_name.lower().replace(" ", "-"),
        }
        if normalized in wave_aliases:
            return wave_key, wave_name
    if normalized in {"1", "first", "1st", "1st_wave", "1st-wave"}:
        return "first", "1ST WAVE"
    if normalized in {"2", "second", "2nd", "2nd_wave", "2nd-wave"}:
        return "second", "2ND WAVE"
    return None, None


def _get_or_create_waves(sort_state):
    existing = {
        row.wave_name: row
        for row in NeoSektorWaveState.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for index, (_wave_key, wave_name) in enumerate(DEFAULT_WAVES, start=1):
        row = existing.get(wave_name)
        if row is None:
            row = NeoSektorWaveState(
                sort_state_id=sort_state.id,
                wave_name=wave_name,
                display_order=index,
            )
            db.session.add(row)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_ballmats(sort_state):
    existing = {
        row.side: row
        for row in NeoSektorBallmatCount.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side_label)
        if row is None:
            row = NeoSektorBallmatCount(sort_state_id=sort_state.id, side=side_label)
            db.session.add(row)
        rows.append(row)
    return rows


def _get_or_create_ballmat_wave_counts(sort_state):
    existing = {
        (row.side, row.wave_name): row
        for row in NeoSektorBallmatWaveCount.query.filter_by(
            sort_state_id=sort_state.id
        ).all()
    }
    rows = []
    display_order = 0
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        for _wave_key, wave_name in DEFAULT_WAVES:
            display_order += 1
            row = existing.get((side_label, wave_name))
            if row is None:
                row = NeoSektorBallmatWaveCount(
                    sort_state_id=sort_state.id,
                    side=side_label,
                    wave_name=wave_name,
                    display_order=display_order,
                )
                db.session.add(row)
            rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_open_bays(sort_state):
    existing = {
        row.side: row
        for row in NeoSektorOpenBayState.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side_label)
        if row is None:
            row = NeoSektorOpenBayState(sort_state_id=sort_state.id, side=side_label)
            db.session.add(row)
        rows.append(row)
    return rows


def _get_or_create_bay_statuses(sort_state):
    existing = {
        row.bay_name: row
        for row in NeoSektorBayStatus.query.filter_by(sort_state_id=sort_state.id).all()
    }
    rows = []
    for index, (side, bay_name) in enumerate(DEFAULT_BAYS, start=1):
        row = existing.get(bay_name)
        if row is None:
            row = NeoSektorBayStatus(
                sort_state_id=sort_state.id,
                side=side,
                bay_name=bay_name,
                display_order=index,
            )
            db.session.add(row)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _get_or_create_driver_routes(sort_state):
    existing = {
        row.route_name: row
        for row in NeoSektorDriverRouteSetting.query.filter_by(
            sort_state_id=sort_state.id
        ).all()
    }
    rows = []
    for index, route_name in enumerate(DEFAULT_DRIVER_ROUTES, start=1):
        row = existing.get(route_name)
        if row is None:
            row = NeoSektorDriverRouteSetting(
                sort_state_id=sort_state.id,
                route_name=route_name,
                route_value=_driver_route_default_value(route_name),
                display_order=index,
            )
            db.session.add(row)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _completion_percent(planned_total, unloaded_total):
    if planned_total <= 0:
        return 0
    return min(round((unloaded_total / planned_total) * 100), 100)


def _wave_view(row, left_to_unload=None):
    planned = max(row.planned_count or 0, 0)
    unloaded = max(row.unloaded_count or 0, 0)
    left = max(planned - unloaded, 0) if left_to_unload is None else left_to_unload
    return {
        "name": row.wave_name,
        "planned": planned,
        "left_to_arrive": _wave_left_to_arrive_display(planned),
        "unloaded": unloaded,
        "left": left,
        "left_to_unload": left,
        "status": _status(row.status),
    }


def _wave_views(waves, sides, operational_settings, now=None):
    rows_by_name = {row.wave_name: row for row in waves}
    first_row = rows_by_name["1ST WAVE"]
    second_row = rows_by_name["2ND WAVE"]
    east = sides["east"]
    west = sides["west"]

    east_open_bays = max(east["open_bays"], 0)
    west_open_bays = max(west["open_bays"], 0)

    first_left_to_arrive = max(first_row.planned_count or 0, 0)
    second_left_to_arrive = max(second_row.planned_count or 0, 0)

    first_remaining = _remaining_wave_load(
        first_left_to_arrive,
        _side_wave_count(east, "first"),
        _side_wave_count(west, "first"),
        east_open_bays,
        west_open_bays,
    )
    first_is_all_up = first_left_to_arrive == 0 and first_remaining == 0
    first_timer_done = _sync_wave_all_up_timer(
        first_row,
        first_is_all_up,
        operational_settings,
        now,
    )
    if first_is_all_up and first_timer_done:
        first_left_to_unload = "DOWN"
    elif first_is_all_up:
        first_left_to_unload = "ALL UP"
    else:
        first_left_to_unload = (
            first_remaining
            + _settings_first_modifier(operational_settings)
        )

    second_waiting_on_first_wave = first_is_all_up and not first_timer_done
    second_base_remaining = _wave_load_without_open_bays(
        second_left_to_arrive,
        _side_wave_count(east, "second"),
        _side_wave_count(west, "second"),
    )
    second_open_bay_remaining = _remaining_wave_load(
        second_left_to_arrive,
        _side_wave_count(east, "second"),
        _side_wave_count(west, "second"),
        east_open_bays,
        west_open_bays,
    )
    second_can_use_open_bays = (
        first_left_to_arrive == 0
        and first_left_to_unload in {0, "DOWN"}
    )
    second_remaining = (
        second_open_bay_remaining
        if second_can_use_open_bays
        else second_base_remaining
    )
    second_is_all_up = second_left_to_arrive == 0 and second_remaining == 0
    second_timer_done = _sync_wave_all_up_timer(
        second_row,
        second_is_all_up,
        operational_settings,
        now,
    )

    if second_is_all_up and second_timer_done:
        second_left_to_unload = "DOWN"
    elif second_is_all_up:
        second_left_to_unload = "ALL UP"
    elif second_waiting_on_first_wave:
        second_left_to_unload = "-"
    elif not first_is_all_up:
        second_left_to_unload = second_remaining
    else:
        second_left_to_unload = (
            second_remaining
            + _settings_second_modifier(operational_settings)
        )

    return [
        _wave_view(first_row, first_left_to_unload),
        _wave_view(second_row, second_left_to_unload),
    ]


def _wave_left_to_arrive_display(value):
    return "ALL IN" if max(value or 0, 0) == 0 else max(value or 0, 0)


def _remaining_wave_load(left_to_arrive, east_wave, west_wave, east_open_bays, west_open_bays):
    open_bays_total = east_open_bays + west_open_bays
    return max(0, left_to_arrive + east_wave + west_wave - open_bays_total)


def _wave_load_without_open_bays(left_to_arrive, east_wave, west_wave):
    return max(0, left_to_arrive + east_wave + west_wave)


def _sync_wave_all_up_timer(row, is_timer_active, operational_settings=None, now=None):
    if now is None:
        now = datetime.utcnow()
    elif now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    if is_timer_active:
        if row.all_up_started_at is None:
            row.all_up_started_at = now
            return False
        started_at = row.all_up_started_at
        if started_at.tzinfo is not None:
            started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
        delay = timedelta(minutes=_settings_down_timer_minutes(operational_settings))
        return now - started_at >= delay

    if row.all_up_started_at is not None:
        row.all_up_started_at = None
    return False


def _ballmat_view(row):
    return {
        "side": row.side,
        "count": max(row.count or 0, 0),
        "status": _status(row.status),
    }


def _open_bay_view(row):
    return {
        "side": row.side,
        "open_count": max(row.open_count or 0, 0),
    }


def _bay_status_view(row):
    return {
        "side": row.side,
        "bay_name": row.bay_name,
        "status": _status(row.status),
    }


def _driver_route_view(row):
    return {
        "route_name": row.route_name,
        "route_value": row.route_value or "-",
    }


def _operational_settings_view(settings):
    return {
        "first_modifier": _settings_first_modifier(settings),
        "second_modifier": _settings_second_modifier(settings),
        "down_timer_minutes": _settings_down_timer_minutes(settings),
    }


def _settings_first_modifier(settings):
    return _clean_count(
        getattr(settings, "first_wave_unload_modifier", None),
        default=DEFAULT_FIRST_WAVE_UNLOAD_MODIFIER,
        maximum=UNLOAD_MODIFIER_MAX,
    )


def _settings_second_modifier(settings):
    return _clean_count(
        getattr(settings, "second_wave_unload_modifier", None),
        default=DEFAULT_SECOND_WAVE_UNLOAD_MODIFIER,
        maximum=UNLOAD_MODIFIER_MAX,
    )


def _settings_down_timer_minutes(settings):
    return _clean_count(
        getattr(settings, "all_up_to_down_minutes", None),
        default=DEFAULT_ALL_UP_TO_DOWN_MINUTES,
        minimum=1,
        maximum=ALL_UP_TO_DOWN_MINUTES_MAX,
    )


def _driver_route_default_value(route_name):
    return "0" if route_name == DRIVER_ROUTE_WEST_OFFSET_NAME else ""


def _driver_route_by_name(driver_routes, route_name):
    return next(row for row in driver_routes if row.route_name == route_name)


def _driver_routing_calculation(sort_state, sides, driver_routes):
    east = sides["east"]
    west = sides["west"]
    west_offset = _driver_route_offset(driver_routes)
    east_open_bays = max(east["open_bays"], 0)
    west_open_bays = max(west["open_bays"], 0)
    first_route = _driver_wave_route(
        _side_wave_count(east, "first"),
        _side_wave_count(west, "first"),
        east_open_bays,
        west_open_bays,
        west_offset,
    )
    second_route = _driver_wave_route(
        _side_wave_count(east, "second"),
        _side_wave_count(west, "second"),
        east_open_bays,
        west_open_bays,
        west_offset,
    )

    return {
        "sort_name": sort_state.sort_name.upper(),
        "active_wave": sort_state.active_wave,
        "west_offset": west_offset,
        "routes": {
            "first": {
                "wave_key": "first",
                "wave_label": "1ST WAVE",
                "east_count": _side_wave_count(east, "first"),
                "west_count": _side_wave_count(west, "first"),
                **first_route,
            },
            "second": {
                "wave_key": "second",
                "wave_label": "2ND WAVE",
                "east_count": _side_wave_count(east, "second"),
                "west_count": _side_wave_count(west, "second"),
                **second_route,
            },
        },
        "bay_priority": _driver_bay_priority(sides),
    }


def _driver_wave_route(east_value, west_value, east_open_bays, west_open_bays, west_offset):
    if east_value == 0 and west_value == 0:
        if east_open_bays >= west_open_bays:
            return {
                "target": "East Ballmat Stay Right",
                "direction": "east",
                "arrow": "right",
            }
        return {
            "target": "West Ballmat Stay Left",
            "direction": "west",
            "arrow": "left",
        }

    if east_value <= west_value + west_offset:
        return {
            "target": "East Ballmat Stay Right",
            "direction": "east",
            "arrow": "right",
        }

    return {
        "target": "West Ballmat Stay Left",
        "direction": "west",
        "arrow": "left",
    }


def _side_wave_count(side, wave_key):
    wave = next((row for row in side["waves"] if row["key"] == wave_key), None)
    return max((wave or {}).get("count") or 0, 0)


def _driver_bay_priority(sides):
    priority = [
        {
            **bay,
            "side": side["label"],
            "rank_label": "",
            "status_rank": STATUS_RANKS[_status(bay["status"])],
        }
        for side in sides.values()
        for bay in side["bays"]
    ]
    priority.sort(
        key=lambda bay: (bay["status_rank"], _bay_number(bay["bay_name"])),
        reverse=True,
    )
    for index, bay in enumerate(priority, start=1):
        bay["rank"] = index
        bay["rank_label"] = _ordinal(index)
    return priority


def _bay_number(value):
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return int(digits or 0)


def _ordinal(number):
    if number == 1:
        suffix = "st"
    elif number == 2:
        suffix = "nd"
    elif number == 3:
        suffix = "rd"
    else:
        suffix = "th"
    return f"{number}{suffix}"


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


def _driver_route_offset(driver_routes):
    offset_row = _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME)
    return _clean_offset(offset_row.route_value)


def _sync_driver_route_values(driver_routes, routing):
    _driver_route_by_name(driver_routes, DRIVER_ROUTE_FIRST_WAVE_NAME).route_value = (
        routing["routes"]["first"]["target"]
    )
    _driver_route_by_name(driver_routes, DRIVER_ROUTE_SECOND_WAVE_NAME).route_value = (
        routing["routes"]["second"]["target"]
    )
    _driver_route_by_name(driver_routes, DRIVER_ROUTE_WEST_OFFSET_NAME).route_value = str(
        routing["west_offset"]
    )


def _side_state_views(ballmat_wave_counts, ballmats, open_bays, bay_statuses):
    ballmat_by_side = {row.side: row for row in ballmats}
    open_bay_by_side = {row.side: row for row in open_bays}
    wave_counts_by_side = {
        side_label: [
            row
            for row in ballmat_wave_counts
            if row.side == side_label
        ]
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    }
    bay_statuses_by_side = {
        side_label: [
            row
            for row in bay_statuses
            if row.side == side_label
        ]
        for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES
    }

    sides = {}
    for side_key, side_label, manager_label in DEFAULT_BALLMAT_SIDES:
        sides[side_key] = {
            "key": side_key,
            "label": side_label,
            "manager_label": manager_label,
            "total_count": max(ballmat_by_side[side_label].count or 0, 0),
            "status": _status(ballmat_by_side[side_label].status),
            "open_bays": max(open_bay_by_side[side_label].open_count or 0, 0),
            "waves": [
                _ballmat_wave_view(row)
                for row in sorted(
                    wave_counts_by_side[side_label],
                    key=lambda row: row.display_order,
                )
            ],
            "bays": [
                _bay_status_view(row)
                for row in sorted(
                    bay_statuses_by_side[side_label],
                    key=lambda row: row.display_order,
                )
            ],
        }
    return sides


def _ballmat_wave_view(row):
    return {
        "key": _wave_key(row.wave_name),
        "name": row.wave_name,
        "count": max(row.count or 0, 0),
        "status": _status(row.status),
    }


def _sync_ballmat_rollups(sort_state, ballmat_wave_counts, waves, ballmats):
    wave_rows = {row.wave_name: row for row in waves}
    side_rows = {row.side: row for row in ballmats}
    total_unloaded = 0

    for _wave_key, wave_name in DEFAULT_WAVES:
        matching_rows = [
            row
            for row in ballmat_wave_counts
            if row.wave_name == wave_name
        ]
        wave_total = sum(max(row.count or 0, 0) for row in matching_rows)
        wave_row = wave_rows[wave_name]
        wave_row.unloaded_count = wave_total
        wave_row.status = _aggregate_status(matching_rows, wave_total)

    for _side_key, side_label, _manager_label in DEFAULT_BALLMAT_SIDES:
        matching_rows = [
            row
            for row in ballmat_wave_counts
            if row.side == side_label
        ]
        side_total = sum(max(row.count or 0, 0) for row in matching_rows)
        side_row = side_rows[side_label]
        side_row.count = side_total
        side_row.status = _aggregate_status(matching_rows, side_total)
        total_unloaded += side_total

    sort_state.unloaded_total = total_unloaded


def _aggregate_status(rows, total_count):
    statuses = [_status(row.status) for row in rows]
    if not statuses:
        return "Empty"

    strongest = max(statuses, key=lambda status: STATUS_RANKS[status])
    if total_count > 0 and strongest == "Empty":
        return "Light"
    return strongest


def _wave_key(wave_name):
    normalized = str(wave_name or "").strip().upper()
    for wave_key, configured_name in DEFAULT_WAVES:
        if normalized == configured_name:
            return wave_key
    return normalized.lower().replace(" ", "_")


def _clean_count(value, default=0, minimum=0, maximum=9999):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = default or 0
    return min(max(cleaned, minimum), maximum)


def _clean_delta(value):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = 0
    return min(max(cleaned, -1000), 1000)


def _clean_offset(value):
    try:
        cleaned = int(value)
    except (TypeError, ValueError):
        cleaned = 0
    return min(max(cleaned, 0), DRIVER_OFFSET_MAX)


def _status(value):
    value = str(value or "").strip().title()
    return value if value in STATUS_LABELS else "Empty"
