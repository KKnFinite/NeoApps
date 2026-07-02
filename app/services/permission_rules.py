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
        "neomotherbrain.manage_sort.edit",
        "simulator",
        "Edit NeoMotherBrain sort operation windows, missions, and generated sort data.",
    ),
    (
        "neomotherbrain.arrival_planning.view",
        "operator",
        "View NeoMotherBrain Arrival Planning screens.",
    ),
    (
        "neomotherbrain.arrival_planning.edit",
        "master",
        "Edit NeoMotherBrain Arrival Planning mission rows.",
    ),
    (
        "neomotherbrain.arrival_planning.run",
        "master",
        "Import, process, add, hot, or ignore Arrival Planning review rows.",
    ),
    (
        "neomotherbrain.departure_planning.view",
        "operator",
        "View NeoMotherBrain Departure Planning screens.",
    ),
    (
        "neomotherbrain.departure_planning.edit",
        "master",
        "Edit NeoMotherBrain Departure Planning mission rows.",
    ),
    (
        "neomotherbrain.departure_planning.run",
        "master",
        "Import, process, add, hot, or ignore Departure Planning review rows.",
    ),
    (
        "neomotherbrain.master_schedule.view",
        "operator",
        "View NeoMotherBrain Master Schedule screens.",
    ),
    (
        "neomotherbrain.master_schedule.edit",
        "simulator",
        "Edit NeoMotherBrain Master Schedule rows and active status.",
    ),
    (
        "neomotherbrain.gateway_matrix.view",
        "operator",
        "View NeoMotherBrain Gateway Matrix screens.",
    ),
    (
        "neomotherbrain.gateway_matrix.edit",
        "simulator",
        "Edit NeoMotherBrain Gateway Matrix active sort days.",
    ),
    (
        "neomotherbrain.sort_timeline.view",
        "grandmaster",
        "View NeoMotherBrain Sort Timeline API planning settings.",
    ),
    (
        "neomotherbrain.sort_timeline.edit",
        "grandmaster",
        "Edit NeoMotherBrain Sort Timeline API planning settings.",
    ),
    (
        "neomotherbrain.manage_api.view",
        "grandmaster",
        "View NeoMotherBrain Manage API diagnostics and manual polling tools.",
    ),
    (
        "neomotherbrain.manage_api.run",
        "grandmaster",
        "Run manual Flight API poll and replay actions.",
    ),
    (
        "neomotherbrain.flight_api_review.view",
        "simulator",
        "View unmatched UPS Flight API review queue.",
    ),
    (
        "neomotherbrain.flight_api_review.edit",
        "simulator",
        "Add or ignore unmatched UPS Flight API review items.",
    ),
    (
        "neomotherbrain.flight_api_auto_poll.trigger",
        "simulator",
        "Trigger one passive Flight API auto-poll eligibility check.",
    ),
    (
        "motherbrain.parking_rules.view",
        "simulator",
        "View NeoMotherBrain Parking Rules settings.",
    ),
    (
        "motherbrain.parking_rules.edit",
        "simulator",
        "Edit NeoMotherBrain Parking Rules settings.",
    ),
    (
        "motherbrain.parking_plan.view",
        "operator",
        "View NeoMotherBrain Parking Plan screens.",
    ),
    (
        "motherbrain.parking_plan.edit",
        "simulator",
        "Edit NeoMotherBrain Parking Plan assignments and tail state controls.",
    ),
    (
        "motherbrain.parking_optimizer.run",
        "master",
        "Run future NeoMotherBrain Parking Plan optimizer previews.",
    ),
    (
        "motherbrain.parking_optimizer.apply",
        "master",
        "Apply future NeoMotherBrain Parking Plan optimizer results.",
    ),
    (
        "motherbrain.parking_conflicts.view",
        "operator",
        "View NeoMotherBrain Parking Plan conflict alerts.",
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
        "neoermac.door_view.view",
        "operator",
        "View NeoErmac Door View screens.",
    ),
    (
        "neoermac.door_view.edit",
        "operator",
        "Edit NeoErmac Door View pulls and ULD requests.",
    ),
    (
        "neoermac.view_outbound.view",
        "watcher",
        "View NeoErmac outbound destination summary screens.",
    ),
    (
        "neoermac.tug_assignments.edit",
        "master",
        "Edit NeoErmac Tug Assignments.",
    ),
    (
        "neosektor.live_counts.view",
        "watcher",
        "View NeoSektor Live Counts screens.",
    ),
    (
        "neosektor.ebm.view",
        "operator",
        "View NeoSektor EBM Ballmat Operations screens.",
    ),
    (
        "neosektor.ebm.edit",
        "operator",
        "Edit NeoSektor EBM Ballmat Operations counts and bay status.",
    ),
    (
        "neosektor.wbm.view",
        "operator",
        "View NeoSektor WBM Ballmat Operations screens.",
    ),
    (
        "neosektor.wbm.edit",
        "operator",
        "Edit NeoSektor WBM Ballmat Operations counts and bay status.",
    ),
    (
        "neosektor.conductor.view",
        "simulator",
        "View NeoSektor Tunnel Conductor screens.",
    ),
    (
        "neosektor.tunnel_conductor.edit",
        "simulator",
        "Edit NeoSektor Tunnel Conductor screens.",
    ),
    (
        "neosektor.discharge.view",
        "operator",
        "View NeoSektor Discharge screens.",
    ),
    (
        "neosektor.discharge.edit",
        "operator",
        "Edit NeoSektor Discharge screens.",
    ),
    (
        "neosektor.driver_routing.view",
        "watcher",
        "View NeoSektor Driver Routing screens.",
    ),
    (
        "neoscorpion.fuel_dispatch.view",
        "operator",
        "View NeoScorpion Fuel Dispatch screens.",
    ),
    (
        "neoscorpion.fuel_dispatch.edit",
        "simulator",
        "Edit NeoScorpion dispatcher fuel assignments and mission fuel requirements.",
    ),
    (
        "neoscorpion.fueler.view",
        "watcher",
        "View assigned NeoScorpion fueler work.",
    ),
    (
        "neoscorpion.fueler.edit",
        "operator",
        "Enter NeoScorpion fueler FOB, APU, transfer, and actual fuel data.",
    ),
    (
        "neoscorpion.truck_manager.view",
        "operator",
        "View NeoScorpion fuel truck state.",
    ),
    (
        "neoscorpion.truck_manager.edit",
        "simulator",
        "Edit NeoScorpion fuel trucks and vendor driver assignments.",
    ),
    (
        "neoscorpion.settings.view",
        "simulator",
        "View NeoScorpion fuel settings.",
    ),
    (
        "neoscorpion.settings.edit",
        "master",
        "Edit NeoScorpion fuel settings.",
    ),
    (
        "neoscorpion.history.view",
        "operator",
        "View NeoScorpion completed fuel history.",
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

PERMISSION_ACTION_LABELS = {
    "view": "View",
    "edit": "Edit",
    "trigger": "Trigger / Run",
}

PERMISSION_ACTION_ORDER = ("view", "edit", "trigger")

PERMISSION_RULE_ITEMS = (
    (
        "motherbrain",
        "neomotherbrain.manage_sort",
        "Manage Sort",
        "Sort summary, selected sort windows, and mission maintenance.",
        {
            "view": "neomotherbrain.manage_sort.view",
            "edit": "neomotherbrain.manage_sort.edit",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.arrival_planning",
        "Arrival Planning",
        "Arrival ALP/API planning rows and current arrival mission list.",
        {
            "view": "neomotherbrain.arrival_planning.view",
            "edit": "neomotherbrain.arrival_planning.edit",
            "trigger": "neomotherbrain.arrival_planning.run",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.departure_planning",
        "Departure Planning",
        "Departure ALP/API planning rows, tail swaps, and current departure mission list.",
        {
            "view": "neomotherbrain.departure_planning.view",
            "edit": "neomotherbrain.departure_planning.edit",
            "trigger": "neomotherbrain.departure_planning.run",
        },
    ),
    (
        "motherbrain",
        "motherbrain.parking_plan",
        "Parking Plan",
        "Parking Plan board, manual assignment controls, and tail state updates.",
        {
            "view": "motherbrain.parking_plan.view",
            "edit": "motherbrain.parking_plan.edit",
        },
    ),
    (
        "motherbrain",
        "motherbrain.parking_rules",
        "Parking Rules",
        "Parking Plan rules and optimizer settings.",
        {
            "view": "motherbrain.parking_rules.view",
            "edit": "motherbrain.parking_rules.edit",
        },
    ),
    (
        "motherbrain",
        "motherbrain.parking_optimizer",
        "Parking Optimizer",
        "Suggest Parking Plan optimizer preview.",
        {
            "trigger": "motherbrain.parking_optimizer.run",
        },
    ),
    (
        "motherbrain",
        "motherbrain.parking_optimizer_apply",
        "Parking Optimizer Apply",
        "Apply generated Parking Plan optimizer recommendations.",
        {
            "trigger": "motherbrain.parking_optimizer.apply",
        },
    ),
    (
        "motherbrain",
        "motherbrain.parking_conflicts",
        "Parking Conflict Alerts",
        "Parking Plan validation and conflict alert visibility.",
        {
            "view": "motherbrain.parking_conflicts.view",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.gateway_matrix",
        "Gateway Matrix",
        "Gateway Matrix sort day configuration.",
        {
            "view": "neomotherbrain.gateway_matrix.view",
            "edit": "neomotherbrain.gateway_matrix.edit",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.master_schedule",
        "Master Schedule",
        "Master Arrival/Departure Plan schedule rows.",
        {
            "view": "neomotherbrain.master_schedule.view",
            "edit": "neomotherbrain.master_schedule.edit",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.sort_timeline",
        "Sort Timeline",
        "API planning settings, polling windows, and monthly usage planning.",
        {
            "view": "neomotherbrain.sort_timeline.view",
            "edit": "neomotherbrain.sort_timeline.edit",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.manage_api",
        "Manage API",
        "Flight API diagnostics, manual poll, and replay tools.",
        {
            "view": "neomotherbrain.manage_api.view",
            "trigger": "neomotherbrain.manage_api.run",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.flight_api_auto_poll",
        "Flight API Auto Poll",
        "Passive auto-poll eligibility checks and provider polling trigger.",
        {
            "trigger": "neomotherbrain.flight_api_auto_poll.trigger",
        },
    ),
    (
        "motherbrain",
        "neomotherbrain.flight_api_review",
        "Unmatched Queue",
        "UPS Flight API review queue and accepted/ignored review items.",
        {
            "view": "neomotherbrain.flight_api_review.view",
            "edit": "neomotherbrain.flight_api_review.edit",
        },
    ),
    (
        "sektor",
        "neosektor.live_counts",
        "Live Counts",
        "NeoSektor Live Counts screen.",
        {
            "view": "neosektor.live_counts.view",
        },
    ),
    (
        "sektor",
        "neosektor.ebm",
        "East Ballmat",
        "NeoSektor East Ballmat Operations screen.",
        {
            "view": "neosektor.ebm.view",
            "edit": "neosektor.ebm.edit",
        },
    ),
    (
        "sektor",
        "neosektor.wbm",
        "West Ballmat",
        "NeoSektor West Ballmat Operations screen.",
        {
            "view": "neosektor.wbm.view",
            "edit": "neosektor.wbm.edit",
        },
    ),
    (
        "sektor",
        "neosektor.tunnel_conductor",
        "Tunnel Conductor",
        "NeoSektor Tunnel Conductor screen.",
        {
            "view": "neosektor.conductor.view",
            "edit": "neosektor.tunnel_conductor.edit",
        },
    ),
    (
        "sektor",
        "neosektor.discharge",
        "Discharge",
        "NeoSektor Discharge screen.",
        {
            "view": "neosektor.discharge.view",
            "edit": "neosektor.discharge.edit",
        },
    ),
    (
        "sektor",
        "neosektor.driver_routing",
        "Driver Routing",
        "NeoSektor Driver Routing screen.",
        {
            "view": "neosektor.driver_routing.view",
        },
    ),
    (
        "ermac",
        "neoermac.building_lineup",
        "Building Lineup",
        "NeoErmac Building Lineup screen.",
        {
            "view": "neoermac.building_lineup.view",
            "edit": "neoermac.building_lineup.edit",
        },
    ),
    (
        "ermac",
        "neoermac.door_view",
        "Door View",
        "NeoErmac Door View screen.",
        {
            "view": "neoermac.door_view.view",
            "edit": "neoermac.door_view.edit",
        },
    ),
    (
        "ermac",
        "neoermac.view_outbound",
        "View Outbound",
        "NeoErmac outbound destination summary screen.",
        {
            "view": "neoermac.view_outbound.view",
        },
    ),
    (
        "ermac",
        "neoermac.tug_assignments",
        "Tug Assignments",
        "NeoErmac Tug Assignments placeholder and future edit access.",
        {
            "edit": "neoermac.tug_assignments.edit",
        },
    ),
    (
        "scorpion",
        "neoscorpion.fuel_dispatch",
        "Fuel Dispatch",
        "NeoScorpion Fuel Dispatch screen.",
        {
            "view": "neoscorpion.fuel_dispatch.view",
            "edit": "neoscorpion.fuel_dispatch.edit",
        },
    ),
    (
        "scorpion",
        "neoscorpion.fueler",
        "Fueler",
        "NeoScorpion Fueler screen.",
        {
            "view": "neoscorpion.fueler.view",
            "edit": "neoscorpion.fueler.edit",
        },
    ),
    (
        "scorpion",
        "neoscorpion.truck_manager",
        "Truck Manager",
        "NeoScorpion Truck Manager screen.",
        {
            "view": "neoscorpion.truck_manager.view",
            "edit": "neoscorpion.truck_manager.edit",
        },
    ),
    (
        "scorpion",
        "neoscorpion.settings",
        "Settings",
        "NeoScorpion fuel settings.",
        {
            "view": "neoscorpion.settings.view",
            "edit": "neoscorpion.settings.edit",
        },
    ),
    (
        "scorpion",
        "neoscorpion.history",
        "Fuel History",
        "NeoScorpion completed fuel history.",
        {
            "view": "neoscorpion.history.view",
        },
    ),
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


def default_minimum_role(permission_key):
    normalized_key = normalize_permission_key(permission_key)
    for default_key, minimum_role, _description in DEFAULT_PERMISSION_RULES:
        if normalize_permission_key(default_key) == normalized_key:
            return minimum_role
    return None


def grouped_permission_rules(rules):
    grouped = {
        group_key: {"key": group_key, "label": label, "rules": [], "items": []}
        for group_key, label, _prefixes in PERMISSION_RULE_GROUPS
    }
    fallback_key = "system"
    rule_by_key = {normalize_permission_key(rule.permission_key): rule for rule in rules}
    assigned_rule_ids = set()

    for group_key, item_key, label, description, action_keys in PERMISSION_RULE_ITEMS:
        actions = []
        for action_type in PERMISSION_ACTION_ORDER:
            permission_key = action_keys.get(action_type)
            rule = rule_by_key.get(normalize_permission_key(permission_key))
            if not rule:
                continue
            assigned_rule_ids.add(rule.id)
            actions.append(_permission_rule_action(action_type, rule))
        if actions:
            grouped.get(group_key, grouped[fallback_key])["items"].append(
                {
                    "key": item_key,
                    "label": label,
                    "description": description,
                    "actions": actions,
                }
            )

    for rule in rules:
        group_key = _permission_rule_group_key(rule.permission_key)
        grouped.get(group_key, grouped[fallback_key])["rules"].append(rule)
        if rule.id in assigned_rule_ids:
            continue
        fallback_item = _fallback_permission_rule_item(rule)
        grouped.get(group_key, grouped[fallback_key])["items"].append(fallback_item)

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
    minimum_role = rule.minimum_role if rule else default_minimum_role(permission_key)
    if not minimum_role:
        return False

    return ROLE_LEVELS.get(node_role, 0) >= ROLE_LEVELS.get(minimum_role, 0)


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


def permission_rule_action_type(permission_key):
    suffix = normalize_permission_key(permission_key).split(".")[-1]
    if suffix == "view":
        return "view"
    if suffix == "edit":
        return "edit"
    if suffix in {"apply", "run", "trigger"}:
        return "trigger"
    return "edit"


def _permission_rule_action(action_type, rule):
    return {
        "type": action_type,
        "label": PERMISSION_ACTION_LABELS[action_type],
        "rule": rule,
    }


def _fallback_permission_rule_item(rule):
    action_type = permission_rule_action_type(rule.permission_key)
    item_key = _fallback_permission_item_key(rule.permission_key)
    return {
        "key": item_key,
        "label": _permission_item_label(item_key),
        "description": rule.description or rule.permission_key,
        "actions": [_permission_rule_action(action_type, rule)],
    }


def _fallback_permission_item_key(permission_key):
    parts = normalize_permission_key(permission_key).split(".")
    if len(parts) > 1 and parts[-1] in {"view", "edit", "apply", "run", "trigger"}:
        return ".".join(parts[:-1])
    return normalize_permission_key(permission_key)


def _permission_item_label(item_key):
    parts = normalize_permission_key(item_key).split(".")
    raw_label = parts[-1] if parts else item_key
    return raw_label.replace("_", " ").replace("-", " ").title()


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
