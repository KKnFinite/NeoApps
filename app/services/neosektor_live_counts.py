from datetime import date

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorSortState,
    NeoSektorWaveState,
)


STATUS_LABELS = ("Empty", "Light", "Moderate", "Full", "Overflowing")
DEFAULT_SORT_NAME = "night"
DEFAULT_ACTIVE_WAVE = "1ST WAVE"
DEFAULT_WAVES = ("1ST WAVE", "2ND WAVE")
DEFAULT_BALLMAT_SIDES = ("EAST", "WEST")
DEFAULT_BAYS = (
    ("EAST", "EAST 1"),
    ("EAST", "EAST 2"),
    ("EAST", "EAST 3"),
    ("WEST", "WEST 1"),
    ("WEST", "WEST 2"),
    ("WEST", "WEST 3"),
)
DEFAULT_DRIVER_ROUTES = ("EAST ROUTE", "WEST ROUTE")


def live_counts_context(gateway, sort_date=None, sort_name=None):
    sort_date = sort_date or date.today()
    sort_name = normalize_sort_name(sort_name)
    sort_state = get_or_create_sort_state(gateway, sort_date, sort_name)

    waves = _get_or_create_waves(sort_state)
    ballmats = _get_or_create_ballmats(sort_state)
    open_bays = _get_or_create_open_bays(sort_state)
    bay_statuses = _get_or_create_bay_statuses(sort_state)
    driver_routes = _get_or_create_driver_routes(sort_state)
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
        "waves": [_wave_view(row) for row in waves],
        "ballmats": [_ballmat_view(row) for row in ballmats],
        "open_bays": [_open_bay_view(row) for row in open_bays],
        "bay_statuses": [_bay_status_view(row) for row in bay_statuses],
        "driver_routes": [_driver_route_view(row) for row in driver_routes],
    }


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


def normalize_sort_name(sort_name):
    value = str(sort_name or "").strip().lower()
    return value or DEFAULT_SORT_NAME


def _get_or_create_waves(sort_state):
    existing = {row.wave_name: row for row in sort_state.wave_states}
    rows = []
    for index, wave_name in enumerate(DEFAULT_WAVES, start=1):
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
    existing = {row.side: row for row in sort_state.ballmat_counts}
    rows = []
    for side in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side)
        if row is None:
            row = NeoSektorBallmatCount(sort_state_id=sort_state.id, side=side)
            db.session.add(row)
        rows.append(row)
    return rows


def _get_or_create_open_bays(sort_state):
    existing = {row.side: row for row in sort_state.open_bay_states}
    rows = []
    for side in DEFAULT_BALLMAT_SIDES:
        row = existing.get(side)
        if row is None:
            row = NeoSektorOpenBayState(sort_state_id=sort_state.id, side=side)
            db.session.add(row)
        rows.append(row)
    return rows


def _get_or_create_bay_statuses(sort_state):
    existing = {row.bay_name: row for row in sort_state.bay_statuses}
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
    existing = {row.route_name: row for row in sort_state.driver_route_settings}
    rows = []
    for index, route_name in enumerate(DEFAULT_DRIVER_ROUTES, start=1):
        row = existing.get(route_name)
        if row is None:
            row = NeoSektorDriverRouteSetting(
                sort_state_id=sort_state.id,
                route_name=route_name,
                display_order=index,
            )
            db.session.add(row)
        rows.append(row)
    return sorted(rows, key=lambda row: row.display_order)


def _completion_percent(planned_total, unloaded_total):
    if planned_total <= 0:
        return 0
    return min(round((unloaded_total / planned_total) * 100), 100)


def _wave_view(row):
    planned = max(row.planned_count or 0, 0)
    unloaded = max(row.unloaded_count or 0, 0)
    return {
        "name": row.wave_name,
        "planned": planned,
        "unloaded": unloaded,
        "left": max(planned - unloaded, 0),
        "status": _status(row.status),
    }


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


def _status(value):
    value = str(value or "").strip().title()
    return value if value in STATUS_LABELS else "Empty"
