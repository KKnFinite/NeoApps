from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user

from app.extensions import db
from app.models import PermissionRule
from app.models.user import ROLE_LEVELS
from app.services.access_control import get_current_gateway, get_user_node_role


DEFAULT_PERMISSION_RULES = (
    (
        "neomotherbrain.dashboard.view",
        "operator",
        "View NeoMotherBrain dashboard screens.",
    ),
    (
        "neomotherbrain.manage_sort.view",
        "operator",
        "View NeoMotherBrain Manage Sort screens.",
    ),
    (
        "neomotherbrain.master_schedule.view",
        "operator",
        "View NeoMotherBrain Master Schedule screens.",
    ),
    (
        "neomotherbrain.gateway_matrix.view",
        "operator",
        "View NeoMotherBrain Gateway Matrix screens.",
    ),
    (
        "neoermac.building_lineup.view",
        "operator",
        "View NeoErmac Building Lineup screens.",
    ),
    (
        "neoermac.building_lineup.edit",
        "simulator",
        "Edit NeoErmac Building Lineup screens.",
    ),
    (
        "neoermac.door_view.enter_actual_pulls",
        "operator",
        "Enter actual pull information in NeoErmac Door View.",
    ),
    (
        "neoermac.tug_assignments.edit",
        "master",
        "Edit NeoErmac Tug Assignments.",
    ),
)

PERMISSION_RULE_GROUPS = (
    ("system", "NeoGateway / System", ("neogateway.", "neoapps.", "system.")),
    ("motherbrain", "NeoMotherBrain", ("neomotherbrain.", "motherbrain.")),
    ("sektor", "NeoSektor", ("neosektor.", "sektor.")),
    ("ermac", "NeoErmac", ("neoermac.", "ermac.")),
    ("scorpion", "NeoScorpion", ("neoscorpion.", "scorpion.")),
    ("reptile", "NeoReptile", ("neoreptile.", "reptile.")),
    ("subzero", "NeoSub-Zero", ("neosubzero.", "subzero.", "neosub-zero.", "sub-zero.")),
    ("rain", "NeoRain", ("neorain.", "rain.")),
)


def ensure_default_permission_rules():
    for permission_key, minimum_role, description in DEFAULT_PERMISSION_RULES:
        rule = get_permission_rule(permission_key)
        if not rule:
            db.session.add(
                PermissionRule(
                    permission_key=permission_key,
                    minimum_role=minimum_role,
                    description=description,
                )
            )
            continue

        if not rule.minimum_role:
            rule.minimum_role = minimum_role
        if not rule.description:
            rule.description = description

    db.session.flush()


def get_permission_rule(permission_key):
    normalized_key = normalize_permission_key(permission_key)
    if not normalized_key:
        return None

    return PermissionRule.query.filter_by(permission_key=normalized_key).first()


def grouped_permission_rules(rules):
    grouped = {
        group_key: {"key": group_key, "label": label, "rules": []}
        for group_key, label, _prefixes in PERMISSION_RULE_GROUPS
    }
    fallback_key = "system"

    for rule in rules:
        group_key = _permission_rule_group_key(rule.permission_key)
        grouped.get(group_key, grouped[fallback_key])["rules"].append(rule)

    return [grouped[group_key] for group_key, _label, _prefixes in PERMISSION_RULE_GROUPS]


def user_can(permission_key, user=None):
    user = user or current_user
    if not _is_authenticated_user(user):
        return False

    node_code = _node_code_from_permission_key(permission_key)
    if not node_code:
        return False

    gateway = get_current_gateway()
    node_role = get_user_node_role(user, gateway.code, node_code)
    if node_role is None:
        return False

    if node_role == "grandmaster":
        return True

    rule = get_permission_rule(permission_key)
    if not rule:
        return False

    return ROLE_LEVELS.get(node_role, 0) >= ROLE_LEVELS.get(rule.minimum_role, 0)


def permission_access(view_permission_key, edit_permission_key=None, user=None):
    can_edit = bool(edit_permission_key and user_can(edit_permission_key, user))
    can_view = can_edit or user_can(view_permission_key, user)

    return {
        "can_view": can_view,
        "can_edit": can_edit,
    }


def require_permission(permission_key):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped_view(*args, **kwargs):
            if user_can(permission_key):
                return view_func(*args, **kwargs)

            flash("Access denied.", "error")
            return redirect(url_for("neomotherbrain.rfd_hub"))

        return wrapped_view

    return decorator


def normalize_permission_key(permission_key):
    return str(permission_key or "").strip().lower()


def _permission_rule_group_key(permission_key):
    normalized_key = normalize_permission_key(permission_key)
    for group_key, _label, prefixes in PERMISSION_RULE_GROUPS:
        if normalized_key.startswith(prefixes):
            return group_key
    return "system"


def _node_code_from_permission_key(permission_key):
    normalized_key = normalize_permission_key(permission_key)
    parts = normalized_key.split(".")
    if len(parts) < 3 or not all(parts):
        return None

    node_code = parts[0]
    if node_code.startswith("neo") and len(node_code) > 3:
        node_code = node_code[3:]
    return node_code


def _is_authenticated_user(user):
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "id", None))
