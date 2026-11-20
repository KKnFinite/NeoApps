"""Remember canonical full-page visits. Recheck access before every restoration."""
import time

from flask import current_app, request, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature

from app.extensions import db
from app.models import Gateway
from app.models.account_preference import AccountOperationalScreen
from app.services.access_control import user_can_access_node, user_has_app_access
from app.services.permission_rules import user_can
from app.neonodes.neoraiden.services import authorized_gateways, gateway_operations


# Routes with an extra resource or edit boundary must opt in explicitly.
SCREENS = {
    "neostaffing.index": "neostaffing.board.view",
    "neostaffing.board": "neostaffing.board.view",
    "neostaffing.attendance": "neostaffing.people.view",
    "neostaffing.shift_flow": "neostaffing.people.view",
    "neomotherbrain.motherbrain": "neomotherbrain.dashboard.view",
    "neomotherbrain.manage_sort": "neomotherbrain.manage_sort.view",
    "neomotherbrain.operations": "neomotherbrain.manage_sort.view",
    "neomotherbrain.parking_plan": "motherbrain.parking_plan.view",
    "neosektor.index": "neosektor.dashboard.view",
    "neosektor.ebm": "neosektor.ebm.view",
    "neosektor.wbm": "neosektor.wbm.view",
    "neosektor.live_counts": "neosektor.live_counts.view",
    "neosektor.driver_routing": "neosektor.driver_routing.view",
    "neosektor.discharge": "neosektor.discharge.view",
    "neosektor.tunnel_conductor": "neosektor.conductor.view",
    "neoermac.index": "neoermac.dashboard.view",
    "neoermac.door_view": "neoermac.door_view.view",
    "neoermac.building_lineup": "neoermac.building_lineup.view",
    "neoermac.upcoming_pulls": "neoermac.upcoming_pulls.view",
    "neoermac.view_outbound": "neoermac.view_outbound.view",
    "neoscorpion.index": "neoscorpion.dashboard.view",
    "neoscorpion.fuel_dispatch": "neoscorpion.fuel_dispatch.view",
    "neoscorpion.fueler": "neoscorpion.fueler.view",
    "neoscorpion.hanzo": "neoscorpion.hanzo.view",
    "neoscorpion.truck_manager": "neoscorpion.truck_manager.view",
    "neoscorpion.fueling_board": "neoscorpion.fueling_board.view",
    "neorain.rainrock": "neorain.rainrock.view",
    "neorain.inbound": "neorain.inbound.view",
    "neorain.outbound": "neorain.outbound.view",
    "neorain.load_planner_lineup": "neorain.load_planner_lineup.view",
    "neorain.daily_briefing": "neorain.daily_briefing.view",
    "neosubzero.ucc": "neosubzero.ucc.view",
    "neosubzero.operations": None,
    "neoraiden.index": None,
}
HISTORY_SCREENS = {"neoraiden.index", "neorain.rainrock", "neorain.daily_briefing"}


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="operational-screen-v1")


def visit_token(user, gateway=None):
    endpoint = request.endpoint
    if (endpoint not in SCREENS or request.method != "GET" or not user.is_authenticated
            or any(key in request.args for key in ("tv", "fragment", "draft", "presentation"))
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"):
        return None
    code = getattr(gateway, "code", None) or current_app.config["DEFAULT_GATEWAY_CODE"]
    op_id = request.args.get("operation_id", type=int)
    mode = request.args.get("mode") if endpoint == "neosubzero.operations" else None
    return _signer().dumps([user.id, endpoint, code, op_id, mode, time.time_ns() // 1000])


def _authorized_url(user, endpoint, code, operation_id, mode):
    if endpoint not in SCREENS or endpoint not in current_app.view_functions:
        return None
    if endpoint.startswith("neostaffing."):
        if user_has_app_access(user, "neostaffing") and user_can(SCREENS[endpoint], user=user):
            return url_for(endpoint)
        return None
    gateway = Gateway.query.filter_by(code=code, is_active=True).first()
    if not gateway or gateway.id not in authorized_gateways(user):
        return None
    args = {}
    if endpoint == "neoraiden.index":
        args["gateway"] = code
    else:
        if code != current_app.config["DEFAULT_GATEWAY_CODE"]:
            return None
        node = endpoint.split(".")[0].removeprefix("neo")
        if not user_can_access_node(user, code, node):
            return None
        if endpoint == "neosubzero.operations":
            from app.neonodes.neosubzero.operations_routes import available_modes
            modes = available_modes()
            if not modes or (mode and mode not in modes):
                return None
            if mode:
                args["mode"] = mode
        elif not user_can(SCREENS[endpoint], user=user):
            return None
    if operation_id is not None:
        if not gateway_operations(gateway).filter_by(id=operation_id).first():
            return None
        if endpoint not in HISTORY_SCREENS:
            from app.services.operation_lifecycle import current_existing_operational_sort_operations
            if not any(op.id == operation_id for op in current_existing_operational_sort_operations(gateway)):
                return None
        args["operation_id"] = operation_id
    return url_for(endpoint, **args)


def store_visit(user, token):
    try:
        user_id, endpoint, code, op_id, mode, stamp = _signer().loads(token, max_age=300)
    except (BadSignature, TypeError, ValueError):
        return False
    if user_id != user.id or not _authorized_url(user, endpoint, code, op_id, mode):
        return False
    if db.engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    values = dict(endpoint=endpoint, gateway_code=code, operation_id=op_id, mode=mode, visited_us=stamp)
    statement = insert(AccountOperationalScreen).values(user_id=user_id, **values)
    db.session.execute(statement.on_conflict_do_update(index_elements=["user_id"], set_=values,
        where=AccountOperationalScreen.visited_us < stamp))
    db.session.commit()
    return True


def restored_url(user):
    if not user.is_active or user.scorpion_vendor:
        return None
    row = db.session.get(AccountOperationalScreen, user.id)
    if row:
        return _authorized_url(user, row.endpoint, row.gateway_code, row.operation_id, row.mode)
    return None
