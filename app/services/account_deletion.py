"""Delete login/access while retaining identities required by immutable history.

The immutable deletion receipt is the permanent tombstone. No operational row
or identity FK is deleted; no new schema or parallel authorization engine.
"""
from datetime import datetime

from app.extensions import db
from app.models import (AccountAccessRequest, AccountScopedRole, AccountSecurityEvent,
    FuelVendorMembership, Gateway, GatewayMembership, GatewayNodeRole,
    NeoScorpionFuelAssignment, NeoScorpionSortFueler, NeoScorpionTruckDriver,
    NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    PortalAppAccess, User)
from app.services.account_foundation import AccountConflict, atomic_account_change
from app.services.account_security import lock_administration, platform_grandmaster
from app.services.auth_session_security import rotate_user_session_version

ACTION = 'account_deleted'


def receipt(user_id):
    return AccountSecurityEvent.query.filter_by(subject_user_id=user_id, action=ACTION).first()


def visible_accounts():
    return ~AccountSecurityEvent.query.filter(
        AccountSecurityEvent.subject_user_id == User.id,
        AccountSecurityEvent.action == ACTION).exists()


def can_delete(actor):
    if not platform_grandmaster(actor) or not actor.email_verified_at:
        return False
    from app.services.account_scope_runtime import scoped_active, is_platform_admin
    return not scoped_active() or is_platform_admin(actor)


def _operations(gateways):
    from app.services.neoscorpion import current_sort_operation
    return {op.id: op for gateway in gateways
            if (op := current_sort_operation(gateway)) and not op.archived_at_utc}


@atomic_account_change
def delete_account(user_id, token, confirmation, confirmed_user_id):
    from flask_login import current_user
    from app.services.accounts_workspace import close_open_attempt, verify_review
    from app.services.neoscorpion_account_types import _guard_last_grandmaster
    from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation
    from app.services.user_tokens import revoke_unused_password_reset_tokens, revoke_unused_email_verification_tokens
    if not can_delete(current_user):
        raise PermissionError('Only an active, verified NeoApps platform administrator may delete accounts.')
    # Match live resource/conversion lock order: Sort scopes, administration,
    # actor, target. No assignment can race the live responsibility check.
    gateways=Gateway.query.filter_by(is_active=True).order_by(Gateway.id).all()
    operations=_operations(gateways)
    for operation_id in sorted(operations):
        lock_nightly_asset_scope_for_mutation(operations[operation_id])
    actor=lock_administration()
    if not can_delete(actor):
        raise PermissionError('Platform administration changed. Sign in again.')
    target=User.query.filter_by(id=user_id).populate_existing().with_for_update().one()
    if target.id == actor.id:
        raise ValueError('You cannot delete your own account.')
    if target.role == 'grandmaster' or AccountScopedRole.query.filter_by(
            user_id=target.id,kind='neoapps',role='grandmaster',is_active=True).first():
        raise ValueError('Protected NeoApps Grandmaster accounts cannot be deleted.')
    if receipt(target.id):
        raise AccountConflict('This account has already been deleted.')
    verify_review(target,token)
    if confirmation != 'yes' or str(confirmed_user_id).strip() != str(target.id):
        raise ValueError('Confirm DELETE ACCOUNT and enter the target NeoApps account number.')
    if operations.keys() != _operations(gateways).keys():
        raise AccountConflict('The current Sort changed. Reload before deleting this account.')
    ids=list(operations)
    if ids and (NeoScorpionSortFueler.query.filter(
            NeoScorpionSortFueler.sort_date_operation_id.in_(ids),NeoScorpionSortFueler.user_id==target.id).first()
        or NeoScorpionFuelAssignment.query.filter(
            NeoScorpionFuelAssignment.sort_date_operation_id.in_(ids),
            NeoScorpionFuelAssignment.assigned_fueler_user_id==target.id,
            NeoScorpionFuelAssignment.review_status!='complete').first()
        or NeoScorpionTruckDriver.query.filter(
            NeoScorpionTruckDriver.sort_date_operation_id.in_(ids),NeoScorpionTruckDriver.driver_user_id==target.id).first()
        or NeoScorpionVendorRequest.query.filter(
            NeoScorpionVendorRequest.sort_date_operation_id.in_(ids),
            NeoScorpionVendorRequest.user_id==target.id,NeoScorpionVendorRequest.status=='pending').first()):
        raise ValueError('Remove this account from current nightly assets and resolve its assignments, truck claims and pending requests before deletion. No operational records were changed.')
    _guard_last_grandmaster(target)
    memberships=GatewayMembership.query.filter_by(user_id=target.id).all()
    nodes=GatewayNodeRole.query.filter(GatewayNodeRole.gateway_membership_id.in_([m.id for m in memberships])).all()
    apps=PortalAppAccess.query.filter_by(user_id=target.id).all()
    scopes=AccountScopedRole.query.filter_by(user_id=target.id).all()
    before=dict(active=target.is_active,session_version=target.auth_session_version,
        memberships=[m.id for m in memberships if m.is_active],nodes=[n.id for n in nodes if n.is_active],
        applications=[a.id for a in apps if a.is_active],scopes=[s.id for s in scopes if s.is_active],
        scope_receipts=[dict(id=s.id,scope=s.scope_key,role=s.role,version=s.version,
            security_event_id=s.security_event_id,active=s.is_active) for s in scopes])
    at=datetime.utcnow()
    event=AccountSecurityEvent(actor_user_id=actor.id,subject_user_id=target.id,action=ACTION,
        details=dict(before=before,after=dict(active=False,session_version=int(target.auth_session_version or 1)+1,
            memberships=[],nodes=[],applications=[],scopes=[]),
            reason='Platform administrator explicitly confirmed account deletion.',identity_retained_for_history=True))
    db.session.add(event);db.session.flush()
    for grant in memberships+nodes+apps:
        grant.is_active=False
    for scope in scopes:
        scope.is_active=False;scope.version+=1;scope.security_event_id=event.id
    for attempt in AccountAccessRequest.query.filter_by(user_id=target.id,state='pending').all():
        close_open_attempt(target,attempt.app_code,'withdrawn','Account deleted by platform administrator.')
    vendor= db.session.get(NeoScorpionVendorAccount,target.id)
    if vendor:vendor.enabled=False
    for member in FuelVendorMembership.query.filter_by(user_id=target.id,ended_at=None).all():
        member.ended_at=at
    for night in NeoScorpionVendorNight.query.filter_by(user_id=target.id).filter(
            NeoScorpionVendorNight.sort_date_operation_id.in_(ids),NeoScorpionVendorNight.status.in_(['pending','approved'])).all():
        night.status='revoked';night.version+=1;night.changed_by_user_id=actor.id;night.updated_at=at
    revoke_unused_password_reset_tokens(target)
    revoke_unused_email_verification_tokens(target)
    target.is_active=False
    target.password_hash='!'  # Deliberately invalid credential, never a new password.
    rotate_user_session_version(target)
    db.session.flush()
    return target
