"""One transaction/authority contract behind platform, app and node editors."""
from hashlib import sha256
import json
from flask import current_app
from flask_login import current_user
from itsdangerous import URLSafeTimedSerializer,BadSignature

from app.extensions import db
from app.models import (User,Gateway,GatewayMembership,GatewayNodeRole,PortalAppAccess,NeoNode,AccountScopedRole,PermissionRule,AccountScopedPolicy)
from app.models.user import ROLE_LEVELS
from app.services.account_authority import Scope
from app.services.account_foundation import atomic_account_change,AccountConflict,FoundationNotActivated
from app.services.account_scope_runtime import scoped_active,authority,can_administer,can_configure,owner_scope,protected_rule
from app.services.account_security import lock_administration,platform_grandmaster,record_security_change
from app.services.accounts_workspace import review_token,verify_review
from app.services.auth_session_security import rotate_user_session_version


def require_active():
    if not scoped_active():raise FoundationNotActivated('Scoped administration is awaiting the complete protected cutover.')


def _target(user_id):
    target=User.query.filter_by(id=user_id).populate_existing().with_for_update().one_or_none()
    if not target:raise ValueError('Unknown NeoApps account.')
    return target


def validate_scope(scope):
    if not isinstance(scope,Scope):raise ValueError('An explicit scope is required.')
    if scope.kind in {'gateway','node'} and not Gateway.query.filter_by(id=scope.gateway_id,is_active=True).first():
        raise ValueError('Unknown or inactive gateway.')
    if scope.kind=='node' and not NeoNode.query.filter_by(id=scope.node_id,is_active=True).first():
        raise ValueError('Unknown or inactive node.')


def role_guard(actor,target,scope,existing,selected,*,granting=True,commissioning=False):
    if selected not in ROLE_LEVELS and selected!='disabled':raise ValueError('Choose a valid scoped role.')
    evidence=authority(actor)
    if not evidence.can_administer(scope):raise ValueError('No administration authority in this scope.')
    prior=existing.role if existing else None
    active=bool(existing and getattr(existing,'is_active',True) and getattr(existing,'status','approved')=='approved')
    if target.id==actor.id and not (active and granting and selected==prior):raise ValueError('Self role changes require a separate protected procedure.')
    platform=platform_grandmaster(actor)
    if target.role=='grandmaster' and not platform:raise ValueError('Protected platform administrator.')
    if scope.kind=='neoapps' and (prior=='grandmaster' or selected=='grandmaster'):
        raise ValueError('Platform Grandmaster commissioning/removal requires the protected procedure.')
    if scope.kind=='node' and selected=='grandmaster':raise ValueError('NeoNode Grandmaster is retired. Select Master or lower.')
    parent=platform or (scope.kind=='node' and (
        evidence.role(Scope('application','neogateway'))=='grandmaster'
        or evidence.role(Scope('gateway','neogateway',scope.gateway_id))=='grandmaster'))
    rank=ROLE_LEVELS.get(evidence.role(scope),0)
    if not parent and (ROLE_LEVELS.get(prior,0)>=rank or selected!='disabled' and ROLE_LEVELS[selected]>=rank):
        raise ValueError('Both existing and proposed roles must be below your authority.')
    if selected=='grandmaster' and not (active and prior=='grandmaster') and not commissioning:
        raise ValueError('New application/gateway Grandmasters require protected parent commissioning.')


def scoped_roles(scope):
    """Minimal subject directory; no email, UPS ID, HR profile or foreign grants."""
    query=User.query.filter(User.is_active.is_(True),~User.scorpion_vendor.has())
    if scope.kind=='application':
        from sqlalchemy import or_,select
        from app.models import AccountAccessRequest
        query=query.filter(or_(User.portal_app_accesses.any(app_code=scope.app_code),
            User.id.in_(select(AccountAccessRequest.user_id).where(AccountAccessRequest.app_code==scope.app_code,AccountAccessRequest.state=='pending'))))
    elif scope.kind in {'gateway','node'}:
        query=query.join(GatewayMembership,GatewayMembership.user_id==User.id).join(PortalAppAccess,PortalAppAccess.user_id==User.id).filter(
            GatewayMembership.gateway_id==scope.gateway_id,GatewayMembership.status=='approved',GatewayMembership.is_active.is_(True),
            PortalAppAccess.app_code=='neogateway',PortalAppAccess.status=='approved',PortalAppAccess.is_active.is_(True))
    return query.order_by(User.last_name,User.first_name,User.id)


def existing_role(user,scope):
    if scope.kind=='neoapps':return user
    if scope.kind=='application':return PortalAppAccess.query.filter_by(user_id=user.id,app_code=scope.app_code).first()
    if scope.kind=='gateway':return AccountScopedRole.query.filter_by(user_id=user.id,kind='gateway',gateway_id=scope.gateway_id).first()
    member=GatewayMembership.query.filter_by(user_id=user.id,gateway_id=scope.gateway_id).first()
    return GatewayNodeRole.query.filter_by(gateway_membership_id=member.id,node_id=scope.node_id).first() if member else None


@atomic_account_change
def set_role(user_id,scope,selected,token,reason):
    require_active();actor=lock_administration();require_active()
    validate_scope(scope)
    target=_target(user_id)
    verify_review(target,token)
    if target.scorpion_vendor or not target.is_active:raise ValueError('An active full NeoApps account is required.')
    if not 3<=len(str(reason or '').strip())<=2000:raise ValueError('Enter a reason of 3–2000 characters.')
    existing=existing_role(target,scope)
    role_guard(actor,target,scope,existing,selected)
    if scope.kind=='application':raise ValueError('Application roles use the application approval/revocation action.')
    before=None if existing is None else dict(role=existing.role,active=bool(getattr(existing,'is_active',True)))
    if scope.kind=='neoapps':
        if selected=='disabled':raise ValueError('Account suspension is separate from its platform role.')
        target.role=selected
    else:
        member=GatewayMembership.query.filter_by(user_id=target.id,gateway_id=scope.gateway_id,status='approved',is_active=True).first()
        app=PortalAppAccess.query.filter_by(user_id=target.id,app_code='neogateway',status='approved',is_active=True).first()
        if not member or not app:raise ValueError('Approved app and gateway access must precede a scoped role.')
        if scope.kind=='node':
            if not db.session.get(NeoNode,scope.node_id).is_active:raise ValueError('Inactive node.')
            if existing is None:
                existing=GatewayNodeRole(gateway_membership_id=member.id,node_id=scope.node_id,role='watcher');db.session.add(existing)
        elif existing is None:
            # The immutable event is linked after it is flushed below.
            existing=AccountScopedRole(user_id=target.id,kind='gateway',scope_key=scope.key,app_code='neogateway',gateway_id=scope.gateway_id,role='watcher')
            db.session.add(existing)
        existing.is_active=selected!='disabled'
        if selected!='disabled':existing.role=selected
    from app.models import AccountSecurityEvent
    receipt=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=target.id,action='scoped_role_changed',
        details=dict(scope=scope.key,before=before,after=dict(role=selected,active=selected!='disabled'),reason=reason.strip()))
    # Avoid premature autoflush of a new gateway rank lacking its receipt FK.
    with db.session.no_autoflush:
        db.session.add(receipt)
        if scope.kind=='gateway' and existing.id:
            existing.version+=1
    db.session.flush([receipt])
    if scope.kind=='gateway':existing.security_event_id=receipt.id
    rotate_user_session_version(target);db.session.flush()


@atomic_account_change
def decide_application(user_id,scope,selected,outcome,token,reason):
    require_active();actor=lock_administration();require_active()
    if scope.kind!='application':raise ValueError('An application scope is required.')
    from app.services.accounts_workspace import require_available_app,close_open_attempt,application_decision_reason
    require_available_app(scope.app_code)
    target=_target(user_id)
    verify_review(target,token)
    if target.id==actor.id or not target.is_active or target.scorpion_vendor:raise ValueError('Review another active full account.')
    reason=application_decision_reason(outcome,reason)
    access=existing_role(target,scope)
    role_guard(actor,target,scope,access,selected,granting=outcome=='approved')
    from app.models import AccountAccessRequest
    from app.auth import routes as legacy
    pending=AccountAccessRequest.query.filter_by(user_id=target.id,app_code=scope.app_code,state='pending').first()
    if not access:
        if not pending:raise ValueError('An application request is required before approval.')
        access=PortalAppAccess(user_id=target.id,app_code=scope.app_code,role='watcher',status='pending',is_active=True)
        db.session.add(access);db.session.flush()
    before=dict(status=access.status,role=access.role,active=access.is_active)
    if outcome=='approved':legacy._approve_portal_app_access(access,selected,reason)
    elif outcome=='denied':legacy._deny_portal_app_access(access,reason)
    elif outcome=='revoked':
        access.is_active=False
        if scope.app_code=='neogateway':
            member=GatewayMembership.query.filter_by(user_id=target.id,gateway_id=legacy.get_current_gateway().id).first()
            if member:member.is_active=False
            from app.services.account_scope_runtime import revoke_gateway_ranks
            revoke_gateway_ranks(target.id,reason)
        close_open_attempt(target,scope.app_code,'denied',reason)
    else:raise ValueError('Invalid application decision.')
    record_security_change('scoped_application_access_changed',target,scope=scope.key,before=before,
        after=dict(status=access.status,role=access.role,active=access.is_active),reason=reason)
    rotate_user_session_version(target);db.session.flush()


def rules_for(scope):
    return [r for r in PermissionRule.query.order_by(PermissionRule.permission_key).all() if owner_scope(r.permission_key,scope.gateway_id)==scope]


def effective_minimum(scope,rule):
    from app.services.account_scope_runtime import scoped_minimum
    return scoped_minimum(scope,rule.permission_key,rule.minimum_role)


def policy_review(scope):
    payload=dict(actor=current_user.id,session=current_user.auth_session_version,scope=scope.key,
        authority=authority(current_user).roles,rules=[(r.id,r.permission_key,effective_minimum(scope,r),r.description) for r in rules_for(scope)],
        overrides=[(r.id,r.version,r.security_event_id) for r in AccountScopedPolicy.query.filter_by(scope_key=scope.key).order_by(AccountScopedPolicy.id)])
    return URLSafeTimedSerializer(current_app.secret_key,salt='scoped-policy-v1').dumps(sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest())


@atomic_account_change
def set_policy(scope,token,updates):
    require_active();actor=lock_administration();require_active()
    validate_scope(scope)
    try:
        serializer=URLSafeTimedSerializer(current_app.secret_key,salt='scoped-policy-v1')
        reviewed=serializer.loads(token,max_age=1200)
        if reviewed!=serializer.loads(policy_review(scope)):raise AccountConflict('Policy or authority changed. Reload before editing.')
    except BadSignature:raise AccountConflict('Policy review expired.') from None
    from app.services.permission_rules import permission_is_configurable
    from app.services.account_scope_runtime import policy_floor
    for rule_id,selected in updates:
        rule=PermissionRule.query.filter_by(id=rule_id).populate_existing().with_for_update().one_or_none()
        if not rule or not permission_is_configurable(rule.permission_key) or not can_configure(actor,scope,rule.permission_key):
            raise ValueError('This rule is outside your permitted policy scope.')
        floor=policy_floor(rule.permission_key)
        if selected not in ROLE_LEVELS or ROLE_LEVELS[selected]<ROLE_LEVELS.get(floor,50):raise ValueError('The operational safety floor cannot be lowered.')
        if scope.kind=='node' and selected=='grandmaster':raise ValueError('Node policies cannot require a retired node role.')
        from app.services.account_security import protected_policy_threshold
        if protected_policy_threshold(rule.permission_key) and selected!='grandmaster':raise ValueError('Platform security requires protected Grandmaster authority.')
        before=effective_minimum(scope,rule)
        if before!=selected:
            from app.models import AccountSecurityEvent
            receipt=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=actor.id,action='scoped_permission_changed',
                details=dict(scope=scope.key,permission=rule.permission_key,before=before,after=selected))
            db.session.add(receipt);db.session.flush([receipt])
            override=AccountScopedPolicy.query.filter_by(scope_key=scope.key,permission_key=rule.permission_key).with_for_update().one_or_none()
            if override is None:
                override=AccountScopedPolicy(scope_key=scope.key,permission_key=rule.permission_key,minimum_role=selected,security_event_id=receipt.id)
                db.session.add(override)
            else:
                override.minimum_role=selected;override.version+=1;override.security_event_id=receipt.id
    db.session.flush()


@atomic_account_change
def commission_scope(user_id,scope,token,password,confirmation,reason):
    """A parent commissioning action, never a Grandmaster dropdown option."""
    from app.services.grandmaster_commissioning import _ready,ReauthenticationFailed
    require_active();actor=lock_administration();require_active()
    validate_scope(scope)
    from app.services.accounts_workspace import require_available_app
    require_available_app(scope.app_code)
    if not platform_grandmaster(actor) or not _ready(actor):raise ValueError('Verified platform Grandmaster commissioning authority required.')
    if not isinstance(password,str) or len(password)>1024 or not actor.check_password(password):raise ReauthenticationFailed('Current password could not be verified.')
    if scope.kind not in {'application','gateway'}:raise ValueError('Only application or gateway Grandmaster commissioning is available here.')
    target=_target(user_id);verify_review(target,token)
    if not target.is_active or not target.email_verified_at or target.scorpion_vendor or target.id==actor.id:raise ValueError('A different active verified full account is required.')
    if confirmation!=f'COMMISSION {target.id} {scope.key}':raise ValueError('Explicit target and scope confirmation required.')
    if not 3<=len(str(reason or '').strip())<=2000:raise ValueError('Enter a commissioning reason.')
    existing=existing_role(target,scope)
    role_guard(actor,target,scope,existing,'grandmaster',commissioning=True)
    if existing and existing.is_active and existing.role=='grandmaster':raise AccountConflict('This scope already has Grandmaster authority.')
    app=PortalAppAccess.query.filter_by(user_id=target.id,app_code=scope.app_code,status='approved',is_active=True).one_or_none()
    if not app:raise ValueError('An approved active application entitlement must precede commissioning.')
    if scope.kind=='gateway' and not GatewayMembership.query.filter_by(user_id=target.id,gateway_id=scope.gateway_id,status='approved',is_active=True).first():
        raise ValueError('An approved active gateway membership is required.')
    from app.models import AccountSecurityEvent
    receipt=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=target.id,action='scoped_grandmaster_commissioned',details=dict(
        scope=scope.key,before=existing.role if existing else None,after='grandmaster',reason=reason.strip(),reauthentication='current_password',
        platform_role_unchanged=target.role,application_administration_requires='NeoApps Master plus application Grandmaster'))
    db.session.add(receipt);db.session.flush([receipt])
    if existing is None:
        existing=AccountScopedRole(user_id=target.id,kind='gateway',scope_key=scope.key,app_code='neogateway',gateway_id=scope.gateway_id,
            role='grandmaster',is_active=True,security_event_id=receipt.id)
        db.session.add(existing)
    else:
        existing.role='grandmaster';existing.is_active=True
        if scope.kind=='gateway':existing.version+=1;existing.security_event_id=receipt.id
    rotate_user_session_version(target);db.session.flush()
