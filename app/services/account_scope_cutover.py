"""Reviewed all-or-nothing scope migration and bounded rollback.

No startup or GET invokes a migration. Existing explicit tables remain the
canonical adapters; the Phase 2 gateway scope supplies the missing gateway rank.
"""
from datetime import datetime
from hashlib import sha256
import json
from flask import current_app
from flask_login import current_user
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import text

from app.extensions import db
from app.models import (User,Gateway,NeoNode,GatewayMembership,GatewayNodeRole,PortalAppAccess,
    PermissionRule,NeoScorpionVendorAccount,AccountScopedRole,AccountSecurityEvent,AccountAuthorizationState,AccountScopedPolicy)
from app.models.user import ROLE_LEVELS
from app.services.account_security import lock_administration,platform_grandmaster
from app.services.account_foundation import atomic_account_change,AccountConflict
from app.services.grandmaster_commissioning import _ready,ReauthenticationFailed
from app.services.request_cache import clear_request_cache
from app.services.account_scope_runtime import GATEWAY_PROTECTED


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def inventory():
    """Stable authorization metadata only; no contact, credential or HR payload."""
    def rows(model,columns):
        return [dict(zip(columns,row)) for row in db.session.query(*(getattr(model,c) for c in columns)).order_by(model.id if hasattr(model,'id') else model.user_id).all()]
    return dict(users=rows(User,['id','role','is_active','auth_session_version']),
        apps=rows(PortalAppAccess,['id','user_id','app_code','role','status','is_active']),
        memberships=rows(GatewayMembership,['id','user_id','gateway_id','status','is_active']),
        nodes=rows(NeoNode,['id','code','is_active']),gateways=rows(Gateway,['id','code','is_active']),
        grants=rows(GatewayNodeRole,['id','gateway_membership_id','node_id','role','is_active']),
        rules=rows(PermissionRule,['id','permission_key','minimum_role']),
        restricted=[r[0] for r in db.session.query(NeoScorpionVendorAccount.user_id).order_by(NeoScorpionVendorAccount.user_id)],
        gateway_roles=rows(AccountScopedRole,['id','user_id','kind','scope_key','app_code','gateway_id','node_id','role','is_active','version']),
        policies=rows(AccountScopedPolicy,['id','scope_key','permission_key','minimum_role','version']))


def plan(data):
    users={u['id']:u for u in data['users']};apps={(a['user_id'],a['app_code']):a for a in data['apps']}
    grants={(g['gateway_membership_id'],g['node_id']):g for g in data['grants']}
    memberships={m['id']:m for m in data['memberships']}
    retired=[];inherited=[];gateway_roles=[];blockers=[]
    if data['gateway_roles'] or data.get('policies'):
        blockers.append('Existing staged scope records require independent review before cutover.')
    for grant in data['grants']:
        if grant['role']!='grandmaster':continue
        member=memberships[grant['gateway_membership_id']];user=users[member['user_id']]
        if user['role']!='grandmaster':
            blockers.append(f"User {user['id']} has node Grandmaster capabilities without platform oversight; do not guess replacement authority.")
        retired.append(dict(grant_id=grant['id'],user_id=user['id'],gateway_id=member['gateway_id'],node_id=grant['node_id'],
            before='grandmaster',after='master',active=grant['is_active']))
    for member in data['memberships']:
        user=users[member['user_id']];app=apps.get((user['id'],'neogateway'))
        if member['status']=='approved' and member['is_active'] and not app:
            blockers.append(f"User {user['id']} has legacy gateway access without an explicit application entitlement; mapping requires review.")
        eligible=bool(user['id'] not in data['restricted'] and user['is_active'] and member['status']=='approved' and member['is_active']
            and app and app['status']=='approved' and app['is_active'] and any(g['id']==member['gateway_id'] and g['is_active'] for g in data['gateways']))
        gateway_roles.append(dict(user_id=user['id'],gateway_id=member['gateway_id'],role='grandmaster' if app and app['role']=='grandmaster' else 'watcher',active=eligible))
        if not eligible:continue
        for node in data['nodes']:
            if node['is_active'] and (member['id'],node['id']) not in grants:
                role=app['role']
                if role=='grandmaster':
                    if user['role']!='grandmaster':blockers.append(f"User {user['id']} has inherited node Grandmaster authority requiring explicit review.")
                    role='master'
                inherited.append(dict(user_id=user['id'],membership_id=member['id'],gateway_id=member['gateway_id'],node_id=node['id'],role=role))
    return dict(fingerprint=digest(data),retired=retired,inherited=inherited,gateway_roles=gateway_roles,blockers=blockers)


def decisions(data,proposed=False):
    """Finite existing-permission comparison; resource predicates stay in domains."""
    from app.services.permission_rules import permission_is_configurable
    users={u['id']:u for u in data['users']};nodes={n['code']:n for n in data['nodes']}
    apps={(a['user_id'],a['app_code']):a for a in data['apps']}
    memberships={(m['user_id'],m['gateway_id']):m for m in data['memberships']}
    grants={(g['gateway_membership_id'],g['node_id']):g for g in data['grants']}
    result={}
    for user in users.values():
        for gateway in data['gateways']:
            member=memberships.get((user['id'],gateway['id']))
            app=apps.get((user['id'],'neogateway'))
            eligible=bool(gateway['is_active'] and member and member['status']=='approved' and member['is_active'] and app and app['status']=='approved' and app['is_active'])
            for rule in data['rules']:
                key=rule['permission_key'];prefix=key.split('.')[0];role=None;allowed=False
                if user['is_active'] and user['id'] not in data['restricted']:
                    if prefix in {'neoapps','system'}:
                        role=user['role']
                        if key not in {'neoapps.portal.view','neoapps.portal.request_access.edit'} and role!='grandmaster':role=None
                    elif prefix in {'neogateway','neostaffing','neobid'}:
                        own=apps.get((user['id'],prefix))
                        role=own['role'] if own and own['status']=='approved' and own['is_active'] else None
                    elif eligible:
                        node=nodes.get(prefix[3:] if prefix.startswith('neo') else prefix)
                        grant=grants.get((member['id'],node['id'])) if node else None
                        if node and node['is_active']:
                            role=grant['role'] if grant and grant['is_active'] else None if grant else app['role']
                            if proposed and role=='grandmaster':role='master'
                    if role:
                        allowed=(key.endswith('.view') and not permission_is_configurable(key)
                            or role=='grandmaster' or ROLE_LEVELS.get(role,0)>=ROLE_LEVELS.get(rule['minimum_role'],100))
                    if proposed and key in GATEWAY_PROTECTED:
                        allowed=eligible and (user['role']=='grandmaster' or app['role']=='grandmaster')
                    elif proposed and role=='master' and prefix not in {'neoapps','system','neogateway','neostaffing','neobid'} and user['role']=='grandmaster':
                        allowed=True
                result[(user['id'],gateway['id'],key)]=bool(allowed)
    return result


def review():
    data=inventory();result=plan(data)
    before,after=decisions(data),decisions(data,True)
    result['differences']=[dict(user_id=k[0],gateway_id=k[1],permission=k[2],before=v,after=after[k]) for k,v in before.items() if v!=after[k]]
    result['compared_decisions']=len(before)
    if result['differences']:result['blockers'].append('Unresolved effective-permission differences require owner-approved mapping.')
    result['protected_admin_ids']=[u.id for u in User.query.filter_by(role='grandmaster',is_active=True).all() if _ready(u)]
    if len(result['protected_admin_ids'])<2:result['blockers'].append('Two verified recoverable platform administrators are required.')
    token=URLSafeTimedSerializer(current_app.secret_key,salt='scoped-cutover-v1').dumps(dict(actor=current_user.id,
        session=current_user.auth_session_version,fingerprint=result['fingerprint']))
    result['token']=token
    return result


def _lock_inventory():
    if db.session.connection().dialect.name=='postgresql':
        db.session.execute(text("SET LOCAL lock_timeout='5s'"))
        db.session.execute(text('LOCK TABLE users,portal_app_accesses,gateway_memberships,gateway_node_roles,account_scoped_roles,account_scoped_policies,permission_rules,neo_nodes,gateways IN SHARE ROW EXCLUSIVE MODE'))


def _step_up(password):
    actor=lock_administration()
    if not platform_grandmaster(actor) or not _ready(actor):raise ValueError('Verified recoverable platform Grandmaster required.')
    if not isinstance(password,str) or len(password)>1024 or not actor.check_password(password):raise ReauthenticationFailed('Current password could not be verified.')
    _lock_inventory()
    return actor


@atomic_account_change
def activate(token,password,confirmation):
    actor=_step_up(password)
    if confirmation!='ACTIVATE SCOPED PERMISSIONS':raise ValueError('Type ACTIVATE SCOPED PERMISSIONS to confirm the complete cutover.')
    state=db.session.get(AccountAuthorizationState,1,with_for_update=True)
    if state:raise AccountConflict('An authorization cutover already exists; do not repeat it.')
    try:value=URLSafeTimedSerializer(current_app.secret_key,salt='scoped-cutover-v1').loads(token,max_age=1200)
    except BadSignature:raise AccountConflict('Cutover review expired. Review the complete mapping again.') from None
    current=review()
    if value!=dict(actor=actor.id,session=actor.auth_session_version,fingerprint=current['fingerprint']):raise AccountConflict('Authorization inventory changed after review.')
    if current['blockers']:raise ValueError('; '.join(current['blockers']))
    event=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=actor.id,action='scoped_authorization_activated',
        details=dict(before_fingerprint=current['fingerprint'],retired=current['retired'],inherited=current['inherited'],
                     compared_decisions=current['compared_decisions'],differences=[],admin_ids=current['protected_admin_ids'],reauthentication='current_password',
                     policy_floors={r.permission_key:r.minimum_role for r in PermissionRule.query.all()}))
    db.session.add(event);db.session.flush()
    for item in current['retired']:
        row=db.session.get(GatewayNodeRole,item['grant_id']);row.role='master'
    for item in current['inherited']:
        db.session.add(GatewayNodeRole(gateway_membership_id=item['membership_id'],node_id=item['node_id'],role=item['role'],is_active=True))
    for item in current['gateway_roles']:
        db.session.add(AccountScopedRole(user_id=item['user_id'],kind='gateway',scope_key=f"gateway:{item['gateway_id']}",
            app_code='neogateway',gateway_id=item['gateway_id'],role=item['role'],is_active=item['active'],security_event_id=event.id))
    db.session.flush()
    # This final row switches every adapter in the SAME commit as all mappings.
    db.session.add(AccountAuthorizationState(id=1,active=True,security_event_id=event.id,inventory_fingerprint=digest(inventory())))
    db.session.flush();clear_request_cache()
    return event.id


def rollback_review():
    state=db.session.get(AccountAuthorizationState,1)
    if not state or not state.active:return None
    fingerprint=digest(inventory())
    return dict(available=fingerprint==state.inventory_fingerprint,
        token=URLSafeTimedSerializer(current_app.secret_key,salt='scoped-rollback-v1').dumps(dict(
            actor=current_user.id,session=current_user.auth_session_version,version=state.version,fingerprint=fingerprint)))


@atomic_account_change
def rollback(token,password,confirmation):
    """Restore the reviewed legacy mode only if no subsequent grant/policy changed.

    Explicit Simulator materializations remain: they preserve the same historical
    effective access and prevent deletion of migration evidence.
    """
    actor=_step_up(password)
    if confirmation!='ROLL BACK SCOPED PERMISSIONS':raise ValueError('Type ROLL BACK SCOPED PERMISSIONS to confirm.')
    state=db.session.get(AccountAuthorizationState,1,with_for_update=True)
    if not state or not state.active:raise AccountConflict('Scoped authorization is not active.')
    try:value=URLSafeTimedSerializer(current_app.secret_key,salt='scoped-rollback-v1').loads(token,max_age=1200)
    except BadSignature:raise AccountConflict('Rollback review expired.') from None
    expected=dict(actor=actor.id,session=actor.auth_session_version,version=state.version,fingerprint=digest(inventory()))
    if value!=expected or expected['fingerprint']!=state.inventory_fingerprint:
        raise AccountConflict('Authorization changed after cutover. A separately reviewed rollback is required; no roles were overwritten.')
    activation=db.session.get(AccountSecurityEvent,state.security_event_id)
    receipt=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=actor.id,action='scoped_authorization_rolled_back',
        details=dict(activation_event_id=activation.id,restored=activation.details['retired'],preserved=activation.details['inherited'],reauthentication='current_password'))
    db.session.add(receipt);db.session.flush()
    state.active=False;state.version+=1;state.updated_at=datetime.utcnow()
    db.session.flush();clear_request_cache()
    for item in activation.details['retired']:
        db.session.get(GatewayNodeRole,item['grant_id']).role=item['before']
    for row in AccountScopedRole.query.filter_by(kind='gateway',security_event_id=activation.id).all():
        row.is_active=False;row.version+=1
    db.session.flush()
    return receipt.id
