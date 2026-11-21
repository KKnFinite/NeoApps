"""Shared choices and validation for permission thresholds, separate from assigning roles."""

from app.models.user import ROLE_LEVELS
from app.services.account_scope_runtime import GATEWAY_PROTECTED, policy_floor
from app.services.account_security import protected_policy_threshold
from app.services.permission_rules import RETIRED_PERMISSION_KEYS, permission_is_configurable


THRESHOLD_ROLES = ("watcher", "operator", "simulator", "master")


def threshold_floor(key, *, scoped):
    if protected_policy_threshold(key):
        return "grandmaster"
    if scoped:
        floor = policy_floor(key)
        return floor if floor in ROLE_LEVELS else "grandmaster"
    return "watcher"


def threshold_choices(key, *, scoped):
    if not permission_is_configurable(key) or (scoped and key in GATEWAY_PROTECTED):
        return ()
    floor = threshold_floor(key, scoped=scoped)
    return tuple(role for role in THRESHOLD_ROLES if ROLE_LEVELS[role] >= ROLE_LEVELS[floor])


def threshold_field(key, minimum, *, scoped, may_edit=True):
    configurable = permission_is_configurable(key)
    choices = threshold_choices(key, scoped=scoped)
    floor = threshold_floor(key, scoped=scoped)
    if protected_policy_threshold(key):
        reason = "Platform security requires protected Grandmaster authority."
    elif scoped and key in GATEWAY_PROTECTED:
        reason = "This action requires protected gateway administration. A role threshold cannot grant it."
    elif not configurable:
        if key.endswith(".view") and key not in RETIRED_PERMISSION_KEYS:
            reason = "Viewing follows existing access rules for this application or node."
        else:
            reason = "This permission is no longer configurable."
    elif not may_edit:
        reason = "Your access to this setting is read-only."
    else:
        reason = None
    return {
        "configurable": configurable,
        "minimum": "grandmaster" if protected_policy_threshold(key) else minimum,
        "choices": choices,
        "editable": bool(choices and may_edit),
        "floor": floor if floor != "watcher" and not reason else None,
        "reason": reason,
    }


def validate_threshold(key, current, selected, *, scoped):
    choices = threshold_choices(key, scoped=scoped)
    if not choices:
        raise ValueError("This permission threshold is protected or read-only.")
    # Browsers omit a select when its saved value is outside the choices and
    # the administrator leaves the disabled current-value option selected.
    if selected is None and current not in choices:
        return current
    if selected not in THRESHOLD_ROLES:
        raise ValueError("Unsupported minimum role selected.")
    if selected not in choices:
        raise ValueError("The reviewed safety floor cannot be lowered.")
    return selected
