"""Scope ownership for the existing permission engine and all local editors.

Canonical role adapters: User (platform), PortalAppAccess (application),
GatewayNodeRole (node), Phase 2 AccountScopedRole (gateway). Staged candidates
at other scopes never override or union with these sources. Operational
resource/Sort/HR/company checks remain mandatory after permission evaluation.
"""
from app.extensions import db
from app.models import AccountAuthorizationState
from app.services.request_cache import request_cached

NODE_PATHS={'motherbrain':'motherbrain','sektor':'neosektor','ermac':'neoermac',
            'scorpion':'neoscorpion','subzero':'neosubzero','rain':'neorain','reptile':'neoreptile'}
PATH_NODES={path:node for node,path in NODE_PATHS.items()}
GATEWAY_PROTECTED=frozenset({
    'neomotherbrain.integrations.edit','neomotherbrain.google_live_polling.edit',
    'neomotherbrain.flight_api_auto_poll.trigger','neomotherbrain.manage_api.run',
    'neomotherbrain.manage_api.view','neomotherbrain.node_refresh_timings.edit',
    'neoscorpion.refresh_settings.edit','neoermac.refresh_settings.edit','neorain.refresh_settings.edit',
})


def scoped_active():
    def read():
        with db.session.no_autoflush:
            row=db.session.get(AccountAuthorizationState,1)
            return bool(row and row.active)
    return request_cached('account.scoped_active',(),read)


def owner_scope(key,gateway_id=None):
    from app.services.account_authority import Scope
    from app.models import NeoNode
    from app.services.access_control import get_current_gateway
    prefix=key.split('.',1)[0]
    if prefix in {'neoapps','system'} or key.startswith(('neomotherbrain.permission_rules.','motherbrain.permission_rules.')):
        return Scope('neoapps')
    if prefix in {'neostaffing','neobid','neogateway'}:
        return Scope('application',prefix)
    gateway_id=gateway_id or get_current_gateway().id
    if key in GATEWAY_PROTECTED:
        return Scope('gateway','neogateway',gateway_id)
    code=prefix[3:] if prefix.startswith('neo') else prefix
    node_id=request_cached('account.policy_node',(code,),lambda:db.session.query(NeoNode.id).filter_by(code=code).scalar())
    return Scope('node','neogateway',gateway_id,node_id) if node_id else None


def authority(user):
    from app.services.account_authority import read_authority
    return request_cached('account.authority',(user.id,),lambda:read_authority(user.id))


def is_platform_admin(user):
    from app.services.account_authority import Scope
    return authority(user).role(Scope('neoapps'))=='grandmaster'


def can_administer(user,scope):
    if not getattr(user, 'is_authenticated', False) or not user.is_active or user.id is None:
        return False
    return authority(user).can_administer(scope)


def protected_rule(key):
    return (key.startswith(('neoapps.','system.','neostaffing.vacation','neostaffing.employee_records'))
            or key in GATEWAY_PROTECTED or '.permission' in key)


def can_configure(user,scope,key):
    from app.services.account_authority import Scope
    if owner_scope(key,scope.gateway_id)!=scope or not can_administer(user,scope):
        return False
    if protected_rule(key):
        if key.startswith('neostaffing.'):
            return is_platform_admin(user)
        if scope.kind=='gateway':
            return is_platform_admin(user) or authority(user).role(Scope('application','neogateway'))=='grandmaster'
        return is_platform_admin(user)
    return True


def scoped_minimum(scope, key, baseline):
    from app.models import AccountScopedPolicy
    def read():
        return {r.permission_key:r.minimum_role for r in AccountScopedPolicy.query.filter_by(scope_key=scope.key).all()}
    return request_cached('account.scoped_policies',scope.key,read).get(key,baseline)


def policy_floor(key):
    """The reviewed cutover floor is immutable; later edits cannot ratchet it down."""
    from app.models import AccountSecurityEvent
    from app.services.permission_rules import default_minimum_role
    def read():
        state=db.session.get(AccountAuthorizationState,1)
        event=db.session.get(AccountSecurityEvent,state.security_event_id) if state else None
        return (event.details or {}).get('policy_floors',{}) if event else {}
    return request_cached('account.policy_floors',(),read).get(key,default_minimum_role(key))


def revoke_gateway_ranks(user_id,reason,*,actor=None):
    """Keep explicit revocation tombstones; reapproval must not revive admin rank."""
    from flask_login import current_user
    from app.models import AccountScopedRole,AccountSecurityEvent
    for row in AccountScopedRole.query.filter_by(user_id=user_id,kind='gateway',is_active=True).with_for_update().all():
        receipt=AccountSecurityEvent(actor_user_id=(actor or current_user).id,subject_user_id=user_id,action='gateway_rank_revoked',
            details=dict(scope=row.scope_key,before=row.role,after='disabled',reason=reason))
        db.session.add(receipt);db.session.flush([receipt])
        row.is_active=False;row.version+=1;row.security_event_id=receipt.id
