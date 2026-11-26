from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from flask import abort, current_app, jsonify, make_response, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.orm import load_only
from app.extensions import db

from app.models import Gateway, SortDateOperation
from app.services.operation_lifecycle import current_existing_operational_sort_operations
from app.services.gateway_matrix import current_gateway_local_datetime, gateway_timezone, physical_poll_windows
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.live_screen_registry import RAIDEN_REFRESH_KEY
from . import bp
from .services import (
    arrival_sort_window, authorized_gateways, board_sources, board_weather, gateway_operations, postgres_board_versions,
    snapshot, usable_pull_lineup,
)


def _selection():
    allowed = authorized_gateways(current_user)
    if not allowed:
        abort(403)
    query = Gateway.query.filter(Gateway.id.in_(allowed))
    code = request.args.get("gateway")
    if request.endpoint == "neoraiden.state" and code is not None:
        query = query.filter_by(code=code)
    gateways = query.order_by(Gateway.code).all()
    if code is None:
        code = current_app.config["DEFAULT_GATEWAY_CODE"]
        gateway = next((g for g in gateways if g.code == code), gateways[0])
    else:
        gateway = next((g for g in gateways if g.code == code),None)
    if gateway is None:
        abort(403)
    if "operation_id" in request.args:
        operation = gateway_operations(gateway).filter_by(id=request.args.get("operation_id",type=int)).first_or_404()
    else:
        active = current_existing_operational_sort_operations(gateway)
        operation = next((op for op in active if op.gateway_code == gateway.code and op.gateway_id in (None,gateway.id)), None)
    return gateways, gateway, operation


def _revision(board):
    return sha256(json.dumps(board, separators=(",", ":")).encode()).hexdigest()


def _board(gateway, operation, *, conditional=False):
    if operation is not None and db.engine.dialect.name == "postgresql":
        # Reauthorize every source and recalculate time-dependent wave states on every read.
        sources = board_sources(current_user, gateway, operation)
        access, uld = sources
        window = arrival_sort_window(gateway, operation)
        weather = board_weather(gateway, access)
        versions, weather["precipitation"] = postgres_board_versions(gateway, operation, access)
        revision = _revision(("board-v10", current_app.config.get("STATIC_ASSET_VERSION"),
            current_user.id, gateway.id, gateway.code, operation.id, str(operation.sort_date), operation.sort_name,
            operation.window_minutes, operation.first_wave_window_minutes, operation.second_wave_window_minutes,
            tuple(map(str, window)), "ermac" in access and usable_pull_lineup(gateway, operation, window),
            sorted(access), uld, gateway_timezone(gateway), weather, versions))
        if conditional and request.if_none_match.contains(revision):
            return None, revision
        return snapshot(current_user, gateway, operation, sources=sources, weather=weather), revision
    board = snapshot(current_user, gateway, operation)
    revision = _revision(board)
    return (None if conditional and request.if_none_match.contains(revision) else board), revision


def _poll_windows(gateway, operation):
    live = operation and not operation.archived_at_utc
    return physical_poll_windows(gateway, operation.sort_date if live else None,
                                 operation.sort_name if live else None,
                                 follow_matrix="operation_id" not in request.args)


@bp.get("")
@login_required
def index():
    gateways, gateway, operation = _selection()
    sorts = gateway_operations(gateway).options(load_only(SortDateOperation.id, SortDateOperation.sort_date,
        SortDateOperation.sort_name)).order_by(SortDateOperation.sort_date.desc(),SortDateOperation.id.desc()).all()
    board, revision = _board(gateway, operation)
    return render_template("neonodes/neoraiden/index.html", gateways=gateways, gateway=gateway, sorts=sorts,
        selected_id=request.args.get("operation_id",type=int), board=board, revision=revision,
        poll_windows=_poll_windows(gateway, operation),
        refresh_interval_ms=live_screen_refresh_value(gateway, RAIDEN_REFRESH_KEY).effective_interval_ms)


@bp.get("/state")
@login_required
def state():
    _, gateway, operation = _selection()
    windows = _poll_windows(gateway, operation)
    interval_ms = live_screen_refresh_value(gateway, RAIDEN_REFRESH_KEY).effective_interval_ms
    if request.headers.get("X-Raiden-Automatic") == "1":
        active = False
        if windows:
            now = current_gateway_local_datetime(gateway).replace(tzinfo=ZoneInfo(gateway_timezone(gateway)))
            active = any(start <= now.timestamp() * 1000 < end for start, end in windows)
        if not active or not interval_ms:
            response = make_response("", 204)
            response.headers["X-Raiden-Windows"] = json.dumps(windows)
            response.headers["X-Raiden-Interval-Ms"] = str(interval_ms)
            response.cache_control.no_store = True
            return response
    board, revision = _board(gateway, operation, conditional=True)
    if board is None:
        response = make_response("",304)
    else:
        # A refresh fragment has no shell, account menu or alert-tray dependencies.
        template = current_app.jinja_env.get_template("neonodes/neoraiden/_board.html")
        response = jsonify(html=template.render(board=board))
    response.set_etag(revision)
    response.headers["X-Raiden-Windows"] = json.dumps(windows)
    response.headers["X-Raiden-Interval-Ms"] = str(interval_ms)
    response.cache_control.no_store = True
    return response
