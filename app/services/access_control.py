from flask import current_app

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode
from app.models.user import ROLE_LEVELS


DEFAULT_NEONODES = (
    ("motherbrain", "NeoMotherBrain", 10),
    ("sektor", "NeoSektor", 20),
    ("scorpion", "NeoScorpion", 30),
    ("reptile", "NeoReptile", 40),
    ("ermac", "NeoErmac", 50),
    ("subzero", "NeoSub-Zero", 60),
    ("rain", "NeoRain", 70),
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
            name=current_app.config.get("DEFAULT_GATEWAY_NAME", "NeoRFD"),
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
    return _membership_is_approved_active(membership)


def get_user_node_role(user, gateway_code, node_code):
    membership = get_user_gateway_membership(user, gateway_code)
    if not _membership_is_approved_active(membership):
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

    return "watcher"


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

    active_nodes = NeoNode.query.filter_by(is_active=True).all()
    for node in active_nodes:
        node_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
        ).first()
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

        node_role.role = role
        node_role.is_active = True

    db.session.flush()
    return membership


def _default_gateway_code():
    return current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper()


def _normalize_gateway_code(gateway_code):
    return str(gateway_code or "").strip().upper()


def _normalize_node_code(node_code):
    return str(node_code or "").strip().lower()


def _is_authenticated_user(user):
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "id", None))


def _membership_is_approved_active(membership):
    return bool(
        membership
        and membership.is_active
        and membership.status == "approved"
        and membership.gateway
        and membership.gateway.is_active
    )
