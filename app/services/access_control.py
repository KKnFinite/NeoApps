from datetime import datetime

from flask import current_app

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess
from app.models.user import ROLE_LEVELS


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
        "description": "Future NeoRain workspace.",
        "start_url": "/nodes/",
        "minimum_role": "watcher",
    },
)


def get_default_gateway():
    gateway = Gateway.query.filter_by(code=_default_gateway_code()).first()
    if gateway:
        return gateway

    return ensure_default_gateway_and_nodes()


def get_current_gateway():
    return get_default_gateway()


def ensure_default_gateway_and_nodes():
    gateway_code = _default_gateway_code()
    gateway = Gateway.query.filter_by(code=gateway_code).first()
    if not gateway:
        gateway = Gateway(
            code=gateway_code,
            name=current_app.config.get("DEFAULT_GATEWAY_NAME", "NeoGateway"),
            is_active=True,
        )
        db.session.add(gateway)
        db.session.flush()
    else:
        gateway.name = current_app.config.get("DEFAULT_GATEWAY_NAME", gateway.name)
        gateway.is_active = True

    for code, name, sort_order in DEFAULT_NEONODES:
        node = NeoNode.query.filter_by(code=code).first()
        if not node:
            db.session.add(
                NeoNode(
                    code=code,
                    name=name,
                    sort_order=sort_order,
                    is_active=True,
                )
            )
            continue

        node.name = name
        node.sort_order = sort_order
        node.is_active = True

    db.session.flush()
    return gateway


def get_user_gateway_membership(user, gateway_code):
    if not _is_authenticated_user(user):
        return None

    gateway_code = _normalize_gateway_code(gateway_code)
    return (
        GatewayMembership.query.join(Gateway)
        .filter(
            GatewayMembership.user_id == user.id,
            Gateway.code == gateway_code,
            Gateway.is_active.is_(True),
        )
        .first()
    )


def user_has_gateway_access(user, gateway_code):
    membership = get_user_gateway_membership(user, gateway_code)
    if not _membership_is_approved_active(membership):
        return False

    if _normalize_gateway_code(gateway_code) != _default_gateway_code():
        return True

    return user_has_app_access(user, "neogateway")


def get_user_node_role(user, gateway_code, node_code):
    membership = get_user_gateway_membership(user, gateway_code)
    if not _membership_is_approved_active(membership):
        return None
    if _normalize_gateway_code(gateway_code) == _default_gateway_code() and not user_has_app_access(
        user,
        "neogateway",
    ):
        return None

    node = NeoNode.query.filter_by(
        code=_normalize_node_code(node_code),
        is_active=True,
    ).first()
    if not node:
        return None

    node_role = GatewayNodeRole.query.filter_by(
        gateway_membership_id=membership.id,
        node_id=node.id,
        is_active=True,
    ).first()
    if node_role:
        return node_role.role

    seed_role = _neogateway_seed_role_for_user(user)
    seed_gateway_node_roles(membership, seed_role, overwrite_existing=False)
    node_role = GatewayNodeRole.query.filter_by(
        gateway_membership_id=membership.id,
        node_id=node.id,
        is_active=True,
    ).first()
    return node_role.role if node_role else "watcher"


def user_can_access_node(user, gateway_code, node_code, minimum_role="watcher"):
    role = get_user_node_role(user, gateway_code, node_code)
    if role is None:
        return False

    return ROLE_LEVELS.get(role, 0) >= ROLE_LEVELS.get(minimum_role, 0)


def request_default_gateway_access_for_user(user):
    gateway = ensure_default_gateway_and_nodes()
    membership = GatewayMembership.query.filter_by(
        user_id=user.id,
        gateway_id=gateway.id,
    ).first()
    if not membership:
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="pending",
            is_active=True,
        )
        db.session.add(membership)
    else:
        membership.is_active = True
        if membership.status not in {"approved", "denied"}:
            membership.status = "pending"

    db.session.flush()
    request_app_access_for_user(user, "neogateway")
    return membership


def backfill_default_gateway_node_roles(user, role="grandmaster"):
    if role not in ROLE_LEVELS:
        raise ValueError("Unsupported node role.")

    gateway = ensure_default_gateway_and_nodes()
    membership = GatewayMembership.query.filter_by(
        user_id=user.id,
        gateway_id=gateway.id,
    ).first()
    if not membership:
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
        )
        db.session.add(membership)
        db.session.flush()

    membership.status = "approved"
    membership.is_active = True

    seed_gateway_node_roles(membership, role, overwrite_existing=True)

    db.session.flush()
    app_access = ensure_user_app_access(user, "neogateway")
    app_access.status = "approved"
    app_access.role = role
    app_access.is_active = True
    app_access.approved_at = app_access.approved_at or datetime.utcnow()
    return membership


def seed_gateway_node_roles(membership, role="watcher", overwrite_existing=False):
    if not membership:
        raise ValueError("Gateway membership request was not found.")
    if role not in ROLE_LEVELS:
        raise ValueError("Unsupported node role.")

    active_nodes = NeoNode.query.filter_by(is_active=True).all()
    existing_roles = {
        node_role.node_id: node_role
        for node_role in GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
        ).all()
    }

    for node in active_nodes:
        node_role = existing_roles.get(node.id)
        if not node_role:
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=node.id,
                    role=role,
                    is_active=True,
                )
            )
            continue

        if overwrite_existing or not node_role.is_active:
            node_role.role = role
            node_role.is_active = True

    db.session.flush()


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

    access = PortalAppAccess(
        user_id=user.id,
        app_code=normalized,
        status="pending",
        role="watcher",
        is_active=True,
    )
    db.session.add(access)
    db.session.flush()
    return access


def get_user_app_access(user, app_code):
    if not _is_authenticated_user(user) and not _is_real_user(user):
        return None

    normalized = _normalize_app_code(app_code)
    if normalized == "neogateway":
        synced = _sync_neogateway_app_access_from_gateway_membership(user)
        if synced:
            return synced

    return PortalAppAccess.query.filter_by(
        user_id=user.id,
        app_code=normalized,
    ).first()


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
    if access.status != "approved":
        access.status = "pending"
    access.is_active = True
    db.session.flush()
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

    gateway = ensure_default_gateway_and_nodes()
    membership = GatewayMembership.query.filter_by(
        user_id=user.id,
        gateway_id=gateway.id,
    ).first()
    if not membership:
        return None

    access = PortalAppAccess.query.filter_by(
        user_id=user.id,
        app_code="neogateway",
    ).first()
    if access:
        return access

    access = PortalAppAccess(
        user_id=user.id,
        app_code="neogateway",
        status=membership.status,
        role=_role_from_gateway_membership(user, membership),
        is_active=membership.is_active,
        approved_by_user_id=membership.approved_by_user_id,
        approved_at=membership.approved_at,
        approval_notes=membership.approval_notes,
        denied_by_user_id=membership.denied_by_user_id,
        denied_at=membership.denied_at,
        denial_notes=membership.denial_notes,
    )
    db.session.add(access)
    db.session.flush()
    return access


def _role_from_gateway_membership(user, membership):
    if not _membership_is_approved_active(membership):
        return "watcher"

    # Legacy NeoNode role overrides stay scoped to their nodes. The app-level
    # role falls back to the user's existing global role so migration does not
    # accidentally promote every NeoGateway page because of one node override.
    return user.role if user.role in ROLE_LEVELS else "watcher"


def _neogateway_seed_role_for_user(user):
    access = PortalAppAccess.query.filter_by(
        user_id=user.id,
        app_code="neogateway",
    ).first()
    if access and access.status == "approved" and access.is_active and access.role in ROLE_LEVELS:
        return access.role
    return "watcher"


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
