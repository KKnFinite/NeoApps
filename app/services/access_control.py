from datetime import datetime
from types import SimpleNamespace

from flask import current_app, g, has_request_context, request
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import set_committed_value

from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    PortalAppAccess,
    SortDateOperation,
    SortTimelineSortSetting,
)
from app.models.user import ROLE_LEVELS
from app.services.operation_scope import (
    OPERATION_BY_ID_NAMESPACE,
)
from app.services.request_cache import (
    MISSING,
    clear_request_cache,
    get_request_cached,
    request_cached,
    set_request_cached,
)


PORTAL_APPS = (
    {
        "code": "neogateway",
        "name": "NeoGateway",
        "description": "Gateway operations and NeoNode systems.",
        "endpoint": "neomotherbrain.rfd_hub",
        "icon_folder": "neogateway",
        "portal_icon_src": "/static/images/icons/neogateway/inapp/neogateway-inapp-128.png",
        "coming_soon": False,
    },
    {
        "code": "neostaffing",
        "name": "NeoStaffing",
        "description": "Staffing operations and workforce planning.",
        "endpoint": "neostaffing.index",
        "icon_folder": "neostaffing",
        "portal_icon_src": "/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png",
        "coming_soon": False,
    },
    {
        "code": "neobid",
        "name": "NeoBid",
        "description": "Bid tools placeholder for future buildout.",
        "endpoint": "auth.neobid_placeholder",
        "icon_folder": "neobid",
        "coming_soon": True,
    },
)
PORTAL_APP_CODES = {app["code"] for app in PORTAL_APPS}
_ACCESS_INITIALIZATION_CHANGED_ATTRIBUTE = "_neoapps_access_initialization_changed"

DEFAULT_NEONODES = (
    ("motherbrain", "NeoMotherBrain", 10),
    ("sektor", "NeoSektor", 20),
    ("scorpion", "NeoScorpion", 30),
    ("reptile", "NeoReptile", 40),
    ("ermac", "NeoErmac", 50),
    ("subzero", "NeoSub-Zero", 60),
    ("rain", "NeoRain", 70),
)

PWA_INSTALL_TARGETS = (
    {
        "kind": "app",
        "code": "neogateway",
        "manifest_key": "neogateway",
        "icon_folder": "neogateway",
        "install_icon_src": "/static/images/icons/neogateway/inapp/neogateway-inapp-128.png",
        "name": "NeoGateway",
        "description": "Gateway operations and NeoNode systems.",
        "start_url": "/rfd",
    },
    {
        "kind": "app",
        "code": "neostaffing",
        "manifest_key": "neostaffing",
        "icon_folder": "neostaffing",
        "install_icon_src": "/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png",
        "name": "NeoStaffing",
        "description": "Staffing operations and workforce planning.",
        "start_url": "/neostaffing",
    },
    {
        "kind": "app",
        "code": "neobid",
        "manifest_key": "neobid",
        "icon_folder": "neobid",
        "name": "NeoBid",
        "description": "Bid tools placeholder for future buildout.",
        "start_url": "/neobid",
    },
    {
        "kind": "node",
        "code": "motherbrain",
        "manifest_key": "neomotherbrain",
        "icon_folder": "motherbrain",
        "install_icon_src": "/static/images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png",
        "name": "NeoMotherBrain",
        "description": "Sort planning, schedules, and operation control.",
        "start_url": "/motherbrain",
        "minimum_role": "simulator",
    },
    {
        "kind": "node",
        "code": "sektor",
        "manifest_key": "neosektor",
        "icon_folder": "sektor",
        "install_icon_src": "/static/images/icons/neosektor/inapp/neosektor-icon-128x128.png",
        "name": "NeoSektor",
        "description": "Ballmat counts, routing, and discharge operations.",
        "start_url": "/neosektor",
        "minimum_role": "watcher",
    },
    {
        "kind": "node",
        "code": "ermac",
        "manifest_key": "neoermac",
        "icon_folder": "ermac",
        "install_icon_src": "/static/images/icons/neoermac/inapp/neoermac-inapp-128.png",
        "name": "NeoErmac",
        "description": "Outbound door, lineup, and pull visibility.",
        "start_url": "/neoermac",
        "minimum_role": "watcher",
    },
    {
        "kind": "node",
        "code": "scorpion",
        "manifest_key": "neoscorpion",
        "icon_folder": "scorpion",
        "install_icon_src": "/static/images/icons/neoscorpion/inapp/neoscorpion-128x128.png",
        "name": "NeoScorpion",
        "description": "Fueling dispatch, trucks, and fueler operations.",
        "start_url": "/neoscorpion",
        "minimum_role": "watcher",
    },
    {
        "kind": "node",
        "code": "reptile",
        "manifest_key": "reptile",
        "icon_folder": "reptile",
        "name": "NeoReptile",
        "description": "Future NeoReptile workspace.",
        "start_url": "/nodes/",
        "minimum_role": "watcher",
    },
    {
        "kind": "node",
        "code": "subzero",
        "manifest_key": "subzero",
        "icon_folder": "subzero",
        "name": "NeoSub-Zero",
        "description": "Future NeoSub-Zero workspace.",
        "start_url": "/nodes/",
        "minimum_role": "watcher",
    },
    {
        "kind": "node",
        "code": "rain",
        "manifest_key": "rain",
        "icon_folder": "rain",
        "name": "NeoRain",
        "description": "NeoRain load planning workspace.",
        "start_url": "/neorain",
        "minimum_role": "watcher",
    },
)


def get_default_gateway():
    gateway_code = _default_gateway_code()

    def resolve():
        gateway = Gateway.query.filter_by(code=gateway_code).first()
        if gateway:
            return gateway

        return ensure_default_gateway_and_nodes()

    return request_cached("access.default_gateway", gateway_code, resolve)


def get_current_gateway():
    return get_default_gateway()


def prime_lightweight_live_request_scope(
    user,
    node_code,
    *,
    operation_id=None,
    include_current_ermac_operation=False,
    include_sektor_routing_signal=False,
):
    """Resolve and seed shared live-request access facts in one projection."""
    if not _is_authenticated_user(user):
        return None

    gateway_code = _default_gateway_code()
    node_code = _normalize_node_code(node_code)
    normalized_operation_id = None
    if operation_id is not None:
        try:
            normalized_operation_id = int(operation_id)
        except (TypeError, ValueError):
            normalized_operation_id = None

    query = (
        db.session.query(
            Gateway,
            GatewayMembership,
            PortalAppAccess,
            NeoNode,
            GatewayNodeRole,
        )
        .outerjoin(
            GatewayMembership,
            and_(
                GatewayMembership.gateway_id == Gateway.id,
                GatewayMembership.user_id == user.id,
                Gateway.is_active.is_(True),
            ),
        )
        .outerjoin(
            PortalAppAccess,
            and_(
                PortalAppAccess.user_id == user.id,
                PortalAppAccess.app_code == "neogateway",
            ),
        )
        .outerjoin(
            NeoNode,
            and_(
                NeoNode.code == node_code,
                NeoNode.is_active.is_(True),
            ),
        )
        .outerjoin(
            GatewayNodeRole,
            and_(
                GatewayNodeRole.gateway_membership_id == GatewayMembership.id,
                GatewayNodeRole.node_id == NeoNode.id,
            ),
        )
    )

    include_operation = normalized_operation_id is not None
    if include_operation:
        operation_join = SortDateOperation.id == normalized_operation_id

        query = (
            query.add_entity(SortDateOperation)
            .outerjoin(SortDateOperation, operation_join)
            .add_entity(SortTimelineSortSetting)
            .outerjoin(
                SortTimelineSortSetting,
                and_(
                    SortTimelineSortSetting.gateway_id == Gateway.id,
                    SortTimelineSortSetting.sort_name
                    == SortDateOperation.sort_name,
                ),
            )
        )

    signal_scope = None
    if include_sektor_routing_signal and node_code == "sektor" and has_request_context():
        from app.services.neosektor_routing_signal import (
            ROUTING_SIGNAL_NAMESPACE, routing_signal_columns, routing_signal_scope,
        )
        try:
            signal_scope = routing_signal_scope(request.args)
        except ValueError:
            pass  # Route retains its existing validation/error response, after auth.
        if signal_scope is not None:
            # Correlate to the same authorized Gateway; no extra entity hydration.
            query = query.add_columns(*routing_signal_columns(Gateway.id, *signal_scope))

    row = query.filter(Gateway.code == gateway_code).first()
    if row is None:
        set_request_cached("access.default_gateway", gateway_code, None)
        return SimpleNamespace(
            gateway=None,
            membership=None,
            app_access=None,
            node=None,
            node_role=None,
            operation=None,
            sort_setting=None,
        )

    gateway, membership, app_access, node, node_role = row[:5]
    if signal_scope is not None:
        set_request_cached(ROUTING_SIGNAL_NAMESPACE, (gateway.id, *signal_scope), tuple(row[-2:]))
    operation = row[5] if include_operation else None
    sort_setting = row[6] if include_operation else None

    if membership is not None:
        set_committed_value(membership, "gateway", gateway)
    if operation is not None and operation.gateway_id == gateway.id:
        set_committed_value(operation, "gateway", gateway)

    if app_access is None:
        app_access = _legacy_neogateway_app_access(user, membership)

    resolved_node_role = None
    if (
        _membership_is_approved_active(membership)
        and _app_access_is_approved_active(app_access)
        and node is not None
    ):
        if node_role is not None:
            resolved_node_role = node_role.role if node_role.is_active else None
        elif app_access.role in ROLE_LEVELS:
            resolved_node_role = app_access.role
        else:
            resolved_node_role = "watcher"

    set_request_cached("access.default_gateway", gateway_code, gateway)
    set_request_cached(
        "access.gateway_membership",
        (user.id, gateway_code),
        membership,
    )
    set_request_cached(
        "access.app_access",
        (user.id, "neogateway"),
        app_access,
    )
    set_request_cached("access.active_node", node_code, node)
    set_request_cached(
        "access.node_role",
        (user.id, gateway_code, node_code),
        resolved_node_role,
    )

    if include_operation:
        set_request_cached(
            OPERATION_BY_ID_NAMESPACE,
            normalized_operation_id,
            operation,
        )
        if operation is not None:
            set_request_cached(
                "sort_timeline_sort_setting",
                (gateway.id, operation.sort_name.strip().lower()),
                sort_setting,
            )

    if include_current_ermac_operation and gateway is not None:
        from app.services.operation_scope import current_operational_sort_operation

        operation = current_operational_sort_operation(gateway)
        sort_setting = None

    scope = SimpleNamespace(
        gateway=gateway,
        membership=membership,
        app_access=app_access,
        node=node,
        node_role=resolved_node_role,
        operation=operation,
        sort_setting=sort_setting,
    )
    return scope


def ensure_default_gateway_and_nodes():
    gateway_code = _default_gateway_code()
    cache_key = gateway_code

    def ensure():
        gateway = get_request_cached("access.default_gateway", gateway_code)
        if gateway is MISSING:
            gateway = Gateway.query.filter_by(code=gateway_code).first()
        if not gateway:
            gateway, _created = _get_or_create_grant(
                Gateway, {"code": gateway_code},
                {"name": current_app.config.get("DEFAULT_GATEWAY_NAME", "NeoGateway"),
                 "is_active": True},
            )
        else:
            gateway.name = current_app.config.get("DEFAULT_GATEWAY_NAME", gateway.name)

        set_request_cached("access.default_gateway", gateway_code, gateway)

        for code, name, sort_order in DEFAULT_NEONODES:
            node = NeoNode.query.filter_by(code=code).first()
            if not node:
                node, _created = _get_or_create_grant(
                    NeoNode, {"code": code},
                    {"name": name, "sort_order": sort_order, "is_active": True},
                )
            else:
                node.name = name
                node.sort_order = sort_order

            set_request_cached("access.active_node", code, node if node.is_active else None)

        db.session.flush()
        return gateway

    return request_cached("access.default_gateway_ensured", cache_key, ensure)


def get_user_gateway_membership(user, gateway_code):
    if not _is_authenticated_user(user):
        return None

    gateway_code = _normalize_gateway_code(gateway_code)
    cache_key = (user.id, gateway_code)

    def resolve():
        return (
            GatewayMembership.query.join(Gateway)
            .filter(
                GatewayMembership.user_id == user.id,
                Gateway.code == gateway_code,
                Gateway.is_active.is_(True),
            )
            .first()
        )

    return request_cached("access.gateway_membership", cache_key, resolve)


def user_has_gateway_access(user, gateway_code):
    membership = get_user_gateway_membership(user, gateway_code)
    if not _membership_is_approved_active(membership):
        return False

    if _normalize_gateway_code(gateway_code) != _default_gateway_code():
        return True

    return user_has_app_access(user, "neogateway")


def get_user_node_role(user, gateway_code, node_code):
    if not _is_authenticated_user(user):
        return None

    gateway_code = _normalize_gateway_code(gateway_code)
    node_code = _normalize_node_code(node_code)
    cache_key = (user.id, gateway_code, node_code)

    def resolve():
        membership = get_user_gateway_membership(user, gateway_code)
        if not _membership_is_approved_active(membership):
            return None
        if gateway_code == _default_gateway_code() and not user_has_app_access(
            user,
            "neogateway",
        ):
            return None

        node = _get_active_node(node_code)
        if not node:
            return None

        node_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
        ).first()
        if node_role is not None:
            return node_role.role if node_role.is_active else None

        seed_role = _neogateway_seed_role_for_user(user)
        if _is_lightweight_live_state_request():
            return seed_role
        seed_gateway_node_roles(membership, seed_role, overwrite_existing=False)
        node_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
            is_active=True,
        ).first()
        return node_role.role if node_role and node_role.is_active else None

    return request_cached("access.node_role", cache_key, resolve)


def user_can_access_node(user, gateway_code, node_code, minimum_role="watcher"):
    role = get_user_node_role(user, gateway_code, node_code)
    if role is None:
        return False

    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(minimum_role, 0)


def prime_user_node_roles_for_request(user, gateway_code, node_codes):
    """Seed the existing per-node access cache with one bounded projection."""
    if not _is_authenticated_user(user):
        return {}

    gateway_code = _normalize_gateway_code(gateway_code)
    normalized_codes = tuple(
        dict.fromkeys(
            normalized
            for node_code in node_codes
            if (normalized := _normalize_node_code(node_code))
        )
    )
    unresolved_codes = tuple(
        node_code
        for node_code in normalized_codes
        if get_request_cached(
            "access.node_role",
            (user.id, gateway_code, node_code),
        )
        is MISSING
    )
    if unresolved_codes:
        for node_code in unresolved_codes:
            set_request_cached("access.active_node", node_code, None)
            set_request_cached(
                "access.node_role",
                (user.id, gateway_code, node_code),
                None,
            )

        rows = (
            db.session.query(
                Gateway,
                GatewayMembership,
                PortalAppAccess,
                NeoNode,
                GatewayNodeRole,
            )
            .select_from(Gateway)
            .join(NeoNode, NeoNode.code.in_(unresolved_codes))
            .outerjoin(
                GatewayMembership,
                and_(
                    GatewayMembership.gateway_id == Gateway.id,
                    GatewayMembership.user_id == user.id,
                    Gateway.is_active.is_(True),
                ),
            )
            .outerjoin(
                PortalAppAccess,
                and_(
                    PortalAppAccess.user_id == user.id,
                    PortalAppAccess.app_code == "neogateway",
                ),
            )
            .outerjoin(
                GatewayNodeRole,
                and_(
                    GatewayNodeRole.gateway_membership_id == GatewayMembership.id,
                    GatewayNodeRole.node_id == NeoNode.id,
                ),
            )
            .filter(Gateway.code == gateway_code)
            .all()
        )
        for gateway, membership, app_access, node, node_role in rows:
            if membership is not None:
                set_committed_value(membership, "gateway", gateway)
            if app_access is None:
                app_access = _legacy_neogateway_app_access(user, membership)

            resolved_role = None
            active_node = node if node.is_active else None
            if (
                _membership_is_approved_active(membership)
                and _app_access_is_approved_active(app_access)
                and active_node is not None
            ):
                if node_role is not None:
                    resolved_role = node_role.role if node_role.is_active else None
                elif app_access.role in ROLE_LEVELS:
                    resolved_role = app_access.role
                else:
                    resolved_role = "watcher"

            set_request_cached("access.default_gateway", gateway_code, gateway)
            set_request_cached(
                "access.gateway_membership",
                (user.id, gateway_code),
                membership,
            )
            set_request_cached(
                "access.app_access",
                (user.id, "neogateway"),
                app_access,
            )
            set_request_cached("access.active_node", node.code, active_node)
            set_request_cached(
                "access.node_role",
                (user.id, gateway_code, node.code),
                resolved_role,
            )

    return {
        node_code: get_request_cached(
            "access.node_role",
            (user.id, gateway_code, node_code),
        )
        for node_code in normalized_codes
    }


def ensure_user_gateway_membership(user, gateway):
    """Create a pending request only; existing decisions always win."""
    membership, created = _get_or_create_grant(
        GatewayMembership, {"user_id": user.id, "gateway_id": gateway.id},
        {"status": "pending", "is_active": True},
    )
    if created:
        _mark_access_initialization_changed()
        clear_request_cache()
    return membership


def request_default_gateway_access_for_user(user):
    gateway = ensure_default_gateway_and_nodes()
    membership = ensure_user_gateway_membership(user, gateway)
    membership = GatewayMembership.query.filter_by(id=membership.id).with_for_update().populate_existing().one()
    _request_reinstatement(membership)
    db.session.flush()
    request_app_access_for_user(user, "neogateway")
    clear_request_cache()
    return membership


def backfill_default_gateway_node_roles(user, role="grandmaster"):
    if role not in ROLE_LEVELS:
        raise ValueError("Unsupported node role.")

    gateway = ensure_default_gateway_and_nodes()
    # Bootstrap/legacy provisioning creates missing grants only. It is not an
    # administrator reactivation action, including for the bootstrap account.
    membership, created = _get_or_create_grant(
        GatewayMembership, {"user_id": user.id, "gateway_id": gateway.id},
        {"status": "approved", "is_active": True},
    )
    app_access, _app_created = _get_or_create_grant(
        PortalAppAccess, {"user_id": user.id, "app_code": "neogateway"},
        {"status": membership.status, "is_active": membership.is_active,
         "role": role,
         "approved_by_user_id": membership.approved_by_user_id,
         "approved_at": membership.approved_at or (datetime.utcnow() if created else None),
         "approval_notes": membership.approval_notes,
         "denied_by_user_id": membership.denied_by_user_id,
         "denied_at": membership.denied_at, "denial_notes": membership.denial_notes},
    )
    if _membership_is_approved_active(membership) and _app_access_is_approved_active(app_access):
        seed_gateway_node_roles(membership, app_access.role, overwrite_existing=False)
    clear_request_cache()
    return membership


def seed_gateway_node_roles(membership, role="watcher", overwrite_existing=False):
    if not membership:
        raise ValueError("Gateway membership request was not found.")
    if role not in ROLE_LEVELS:
        raise ValueError("Unsupported node role.")

    if overwrite_existing:
        raise ValueError("Existing node grants require an explicit administrator role edit.")
    if not _membership_is_approved_active(membership):
        return

    active_nodes = NeoNode.query.filter_by(is_active=True).all()
    existing_ids = {
        row.node_id for row in GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
        ).all()
    }
    changed = False
    for node in active_nodes:
        if node.id not in existing_ids:
            _row, created = _get_or_create_grant(
                GatewayNodeRole,
                {"gateway_membership_id": membership.id, "node_id": node.id},
                {"role": role, "is_active": True},
            )
            changed = changed or created
    if changed:
        _mark_access_initialization_changed()
        clear_request_cache()


def portal_app_definitions():
    return PORTAL_APPS


def portal_app_definition(app_code):
    normalized = _normalize_app_code(app_code)
    for app in PORTAL_APPS:
        if app["code"] == normalized:
            return app
    return None


def ensure_user_app_access(user, app_code):
    if not _is_real_user(user):
        return None

    normalized = _normalize_app_code(app_code)
    if normalized not in PORTAL_APP_CODES:
        raise ValueError("Unsupported app access request.")

    if normalized == "neogateway":
        synced = _sync_neogateway_app_access_from_gateway_membership(user)
        if synced:
            return synced

    access = PortalAppAccess.query.filter_by(
        user_id=user.id,
        app_code=normalized,
    ).first()
    if access:
        return access

    access, created = _get_or_create_grant(
        PortalAppAccess, {"user_id": user.id, "app_code": normalized},
        {"status": "pending", "role": "watcher", "is_active": True},
    )
    if created:
        _mark_access_initialization_changed()
        clear_request_cache()
    return access


def get_user_app_access(user, app_code):
    if not _is_authenticated_user(user) and not _is_real_user(user):
        return None

    normalized = _normalize_app_code(app_code)
    cache_key = (user.id, normalized)

    def resolve():
        access = PortalAppAccess.query.filter_by(
            user_id=user.id,
            app_code=normalized,
        ).first()
        if access or normalized != "neogateway":
            return access

        if _is_lightweight_live_state_request():
            membership = get_user_gateway_membership(user, _default_gateway_code())
            return _legacy_neogateway_app_access(user, membership)

        return _sync_neogateway_app_access_from_gateway_membership(user)

    return request_cached("access.app_access", cache_key, resolve)


def user_has_app_access(user, app_code):
    access = get_user_app_access(user, app_code)
    return _app_access_is_approved_active(access)


def get_user_app_role(user, app_code):
    access = get_user_app_access(user, app_code)
    if not _app_access_is_approved_active(access):
        return None
    return access.role


def user_can_access_app(user, app_code, minimum_role="watcher"):
    role = get_user_app_role(user, app_code)
    if role is None:
        return False

    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(minimum_role, 0)


def request_app_access_for_user(user, app_code):
    access = ensure_user_app_access(user, app_code)
    access = PortalAppAccess.query.filter_by(id=access.id).with_for_update().populate_existing().one()
    _request_reinstatement(access)
    db.session.flush()
    clear_request_cache()
    return access


def portal_dashboard_rows_for_user(user):
    return [
        {
            "app": app,
            "access": get_user_app_access(user, app["code"]),
            "icon_src": app.get(
                "portal_icon_src",
                f"/static/images/icons/{app['icon_folder']}/icon_192.png",
            ),
        }
        for app in PORTAL_APPS
    ]


def portal_install_rows_for_user(user):
    gateway_code = _default_gateway_code()
    prime_user_node_roles_for_request(
        user,
        gateway_code,
        (
            target["code"]
            for target in PWA_INSTALL_TARGETS
            if target["kind"] == "node"
        ),
    )
    rows = []
    for target in PWA_INSTALL_TARGETS:
        if target["kind"] == "app":
            if not user_has_app_access(user, target["code"]):
                continue
        elif not user_can_access_node(
            user,
            gateway_code,
            target["code"],
            minimum_role=target.get("minimum_role", "watcher"),
        ):
            continue

        rows.append(
            {
                **target,
                "icon_src": target.get(
                    "install_icon_src",
                    f"/static/images/icons/{target['icon_folder']}/icon_192.png",
                ),
                "manifest_url": f"/manifest/{target['manifest_key']}.webmanifest",
            }
        )
    return rows


def _sync_neogateway_app_access_from_gateway_membership(user):
    if not _is_real_user(user):
        return None

    gateway = get_default_gateway()
    membership = get_user_gateway_membership(user, gateway.code)
    if not membership:
        return None

    access = PortalAppAccess.query.filter_by(
        user_id=user.id,
        app_code="neogateway",
    ).first()
    if access:
        return access

    access, created = _get_or_create_grant(
        PortalAppAccess, {"user_id": user.id, "app_code": "neogateway"},
        {"status": membership.status,
         "role": _role_from_gateway_membership(user, membership),
         "is_active": membership.is_active,
         "approved_by_user_id": membership.approved_by_user_id,
         "approved_at": membership.approved_at,
         "approval_notes": membership.approval_notes,
         "denied_by_user_id": membership.denied_by_user_id,
         "denied_at": membership.denied_at,
         "denial_notes": membership.denial_notes},
    )
    if created:
        _mark_access_initialization_changed()
        clear_request_cache()
    return access


def _get_or_create_grant(model, identity, defaults):
    """Only insert missing unique identities; a race never updates the winner.

    A savepoint contains the expected uniqueness conflict without rolling back
    the caller's transaction. Unexpected integrity failures still propagate.
    """
    existing = model.query.filter_by(**identity).first()
    if existing is not None:
        return existing, False
    connection = db.session.connection()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        # sqlite3 legacy transaction mode does not BEGIN for SELECT/SAVEPOINT.
        # Enclose the savepoint in the caller's real transaction so RELEASE
        # cannot independently commit a grant before the caller succeeds.
        connection.exec_driver_sql("BEGIN")
    try:
        with db.session.begin_nested():
            row = model(**identity, **defaults)
            db.session.add(row)
            db.session.flush()
        return row, True
    except IntegrityError:
        winner = model.query.filter_by(**identity).populate_existing().first()
        if winner is None:
            raise
        return winner, False


def _request_reinstatement(grant):
    # Denials remain denials. An inactive grant can rejoin the approval queue,
    # never the authorized population. Retain the previous decision metadata.
    if grant.status == "denied" or grant.is_active:
        return
    previous_status = grant.status
    grant.status = "pending"
    grant.is_active = True
    transition = (
        f"Reinstatement requested {datetime.utcnow().isoformat()} UTC; "
        f"previous status={previous_status}, active=False. Administrator approval required."
    )
    grant.approval_notes = "\n".join(filter(None, (grant.approval_notes, transition)))


def access_initialization_changed_this_request():
    if not has_request_context():
        return False
    state = getattr(g, _ACCESS_INITIALIZATION_CHANGED_ATTRIBUTE, None)
    return bool(state and state[0] is request._get_current_object() and state[1])


def _mark_access_initialization_changed():
    if has_request_context():
        setattr(
            g,
            _ACCESS_INITIALIZATION_CHANGED_ATTRIBUTE,
            (request._get_current_object(), True),
        )


def _role_from_gateway_membership(user, membership):
    if not _membership_is_approved_active(membership):
        return "watcher"

    # Legacy NeoNode role overrides stay scoped to their nodes. The app-level
    # role falls back to the user's existing global role so migration does not
    # accidentally promote every NeoGateway page because of one node override.
    return user.role if user.role in ROLE_LEVELS else "watcher"


def _legacy_neogateway_app_access(user, membership):
    if not _membership_is_approved_active(membership):
        return None
    return SimpleNamespace(
        user_id=user.id,
        app_code="neogateway",
        status=membership.status,
        role=_role_from_gateway_membership(user, membership),
        is_active=membership.is_active,
    )


def _neogateway_seed_role_for_user(user):
    access = get_user_app_access(user, "neogateway")
    if access and access.status == "approved" and access.is_active and access.role in ROLE_LEVELS:
        return access.role
    return "watcher"


def _get_active_node(node_code):
    normalized = _normalize_node_code(node_code)
    return request_cached(
        "access.active_node",
        normalized,
        lambda: NeoNode.query.filter_by(code=normalized, is_active=True).first(),
    )


def _default_gateway_code():
    return current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper()


def _normalize_gateway_code(gateway_code):
    return str(gateway_code or "").strip().upper()


def _normalize_node_code(node_code):
    return str(node_code or "").strip().lower()


def _normalize_app_code(app_code):
    return str(app_code or "").strip().lower()


def _is_authenticated_user(user):
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "id", None))


def _is_real_user(user):
    return bool(user and getattr(user, "id", None))


def _is_lightweight_live_state_request():
    from app.services.operational_request_policy import (
        current_request_is_lightweight_live_state,
    )

    return current_request_is_lightweight_live_state()


def _membership_is_approved_active(membership):
    return bool(
        membership
        and membership.is_active
        and membership.status == "approved"
        and membership.gateway
        and membership.gateway.is_active
    )


def _app_access_is_approved_active(access):
    return bool(
        access
        and access.is_active
        and access.status == "approved"
        and access.app_code in PORTAL_APP_CODES
    )
