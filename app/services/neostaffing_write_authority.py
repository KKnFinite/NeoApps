"""Transaction-bound authority refresh for critical Staffing writes.

Call before mutations, with the caller's organizational dependencies locked.
Locks remain until caller commit/rollback. This changes no permission rules.
"""
from app.models import User, PortalAppAccess, PermissionRule
from app.services.request_cache import clear_request_cache, set_request_cached


def lock_staffing_write_authority(user, permission_keys):
    keys = sorted(set(permission_keys))
    if any(not key.startswith('neostaffing.') for key in keys):
        raise ValueError('Only Staffing permission keys are supported.')
    # SHARE permits other readers/critical writes, but prevents a revocation,
    # role change, deactivation or rule edit passing this decision until commit.
    # populate_existing discards both stale ORM fields and pre-lock snapshots.
    session_version = user.auth_session_version
    actor = User.query.filter_by(id=user.id).populate_existing().with_for_update(read=True).first()
    if not actor or not actor.is_active or actor.auth_session_version != session_version:
        raise ValueError('You do not have permission to use Bulk Change.')
    access = PortalAppAccess.query.filter_by(
        user_id=actor.id, app_code='neostaffing'
    ).populate_existing().with_for_update(read=True).first()
    rules = PermissionRule.query.filter(PermissionRule.permission_key.in_(keys)).order_by(
        PermissionRule.id
    ).populate_existing().with_for_update(read=True).all()
    clear_request_cache()
    set_request_cached('access.app_access', (actor.id, 'neostaffing'), access)
    by_key = {rule.permission_key: rule for rule in rules}
    for key in keys:
        set_request_cached('permission.rule', key, by_key.get(key))
    return actor
