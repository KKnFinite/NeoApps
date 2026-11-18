"""Bounded repairs to the current account model, not a second role system."""
from flask_login import current_user

from app.extensions import db
from app.models import AccountSecurityEvent, NeoNode, User
from app.models.user import ROLE_LEVELS
from app.services.request_cache import clear_request_cache


PUBLIC_ACCOUNT_PERMISSIONS = frozenset({
    "neoapps.portal.view", "neoapps.portal.request_access.edit",
})


def platform_security_permission(key):
    return key.startswith(("neoapps.", "system.")) and key not in PUBLIC_ACCOUNT_PERMISSIONS


def protected_policy_threshold(key):
    return platform_security_permission(key) or key.startswith((
        "neomotherbrain.permission_rules.", "motherbrain.permission_rules.",
    ))


def platform_grandmaster(user):
    return bool(user and user.is_authenticated and user.is_active
                and user.role == "grandmaster" and not getattr(user, "scorpion_vendor", None))


def lock_administration():
    """Match account conversion's lock order; reauthorize after any wait.

    This is the existing account-grant serialization row, held only for the
    mutation transaction. Token recovery only takes its subject account lock.
    """
    actor_id = current_user.id
    session_version = current_user.auth_session_version
    NeoNode.query.filter_by(code="motherbrain").with_for_update().first()
    db.session.expire_all()
    actor = User.query.filter_by(id=actor_id).populate_existing().with_for_update().first()
    clear_request_cache()
    if (not actor or not actor.is_active or actor.scorpion_vendor
            or actor.auth_session_version != session_version):
        raise ValueError("Administrative authority changed. Sign in again.")
    return actor


def may_manage_permission(user, key):
    from app.services.account_scope_runtime import scoped_active, owner_scope, can_configure
    if scoped_active():
        scope=owner_scope(key)
        return bool(scope and can_configure(user,scope,key))
    from app.services.access_control import get_current_gateway, get_user_app_role, get_user_node_role, prime_user_node_roles_for_request
    if platform_grandmaster(user):
        return True
    if protected_policy_threshold(key):
        return False
    prefix = key.split(".", 1)[0]
    if prefix in {"neogateway", "neostaffing", "neobid"}:
        return (user.role == "master" and get_user_app_role(user, prefix) == "grandmaster")
    node_code = prefix[3:] if prefix.startswith("neo") else prefix
    prime_user_node_roles_for_request(user, get_current_gateway().code, [node_code])
    return get_user_node_role(user, get_current_gateway().code, node_code) == "grandmaster"


def node_authority(user, node):
    """A platform parent may oversee nodes; unrelated node roles never combine."""
    if platform_grandmaster(user):
        return ROLE_LEVELS["grandmaster"]
    from app.services.account_scope_runtime import scoped_active,authority
    if scoped_active():
        from app.services.account_authority import Scope
        from app.services.access_control import get_current_gateway
        evidence=authority(user);gateway=get_current_gateway()
        if evidence.can_administer(Scope('gateway','neogateway',gateway.id)):
            return ROLE_LEVELS['grandmaster']
    from app.services.access_control import get_current_gateway, get_user_node_role, prime_user_node_roles_for_request
    prime_user_node_roles_for_request(user, get_current_gateway().code, [node.code])
    return ROLE_LEVELS.get(get_user_node_role(user, get_current_gateway().code, node.code), 0)


def guard_node_change(actor, target, node, existing, selected):
    from app.services.account_scope_runtime import scoped_active
    if scoped_active():
        from app.services.account_authority import Scope
        from app.services.access_control import get_current_gateway
        from app.services.scoped_access_management import role_guard
        return role_guard(actor,target,Scope('node','neogateway',get_current_gateway().id,node.id),existing,selected)
    authority = node_authority(actor, node)
    if authority < ROLE_LEVELS["master"]:
        raise ValueError("You cannot manage roles outside your authorized node.")
    prior = existing.role if existing and existing.is_active else "watcher"
    active = bool(existing and existing.is_active)
    if selected == prior and active:
        return
    if (target.id == actor.id and selected != "disabled"
            and (not active or ROLE_LEVELS.get(selected, 0) > ROLE_LEVELS[prior])):
        raise ValueError("You cannot promote your own role.")
    if not platform_grandmaster(actor) and (
            existing and ROLE_LEVELS.get(existing.role, 0) >= authority
            or selected != "disabled" and ROLE_LEVELS.get(selected, 100) >= authority):
        raise ValueError("You cannot assign a role equal to or higher than your authority, or change an existing role at that level.")


def record_security_change(action, subject, **details):
    db.session.add(AccountSecurityEvent(
        actor_user_id=current_user.id, subject_user_id=subject.id,
        action=action, details=details,
    ))


def guard_app_role_change(target, access, selected, *, granting=True):
    from app.services.account_scope_runtime import scoped_active
    if scoped_active():
        from app.services.account_authority import Scope
        from app.services.scoped_access_management import role_guard
        if not access:raise ValueError('An explicit application entitlement is required.')
        return role_guard(current_user,target,Scope('application',access.app_code),access,selected,granting=granting)
    # Phase 1 retains platform oversight; it does not introduce the future
    # delegated application-account administration model.
    if not platform_grandmaster(current_user):
        raise ValueError("Platform authority is required to change application roles.")
    active = bool(access and access.is_active and access.status == "approved")
    prior = access.role if active else "watcher"
    if (target.id == current_user.id
            and ((granting and not active) or ROLE_LEVELS.get(selected, 100) > ROLE_LEVELS.get(prior, 0))):
        raise ValueError("You cannot promote your own application role.")
