"""One administrator decision boundary for registration and account conversion."""
from flask_login import current_user

from app.extensions import db
from app.models import AccountAccessRequest,AccountSecurityEvent,FuelDriverIdentity,PortalAppAccess,User
from app.services.account_foundation import atomic_account_change
from app.services.account_security import lock_administration,platform_grandmaster,record_security_change
from app.services.accounts_workspace import close_open_attempt,verify_review
from app.services.auth_session_security import rotate_user_session_version

TYPES={'full':'Full NeoApps','fuel_driver':'Fuel Truck Driver','vendor_supervisor':'Vendor Supervisor'}


def administrator(user):
    if not platform_grandmaster(user) or not user.email_verified_at:return False
    from app.services.account_scope_runtime import scoped_active,is_platform_admin
    return not scoped_active() or is_platform_admin(user)


def current_type(user):
    from app.services.fuel_driver_accounts import supervisor_membership
    return ('vendor_supervisor' if supervisor_membership(user.scorpion_vendor) else 'fuel_driver') if user.scorpion_vendor else 'full'


def requested_type(user):
    identity=db.session.get(FuelDriverIdentity,user.id)
    return identity.account_type if identity and identity.account_type in TYPES else 'fuel_driver' if identity and identity.requested_driver else 'full'


@atomic_account_change
def decide(user_id,token,selected,outcome,employee_id,confirmed_identity,person_id,reason=''):
    from app.auth import routes as legacy
    from app.services.access_control import get_current_gateway
    from app.services.fuel_driver_accounts import verify_ups_id,identity_verified
    from app.services.neoscorpion_account_types import convert_account
    if not administrator(current_user):raise PermissionError('Verified NeoApps platform administration is required.')
    if selected not in TYPES or outcome not in {'approved','denied'}:raise ValueError('Choose an account type and approval or denial.')
    reason=str(reason or '').strip()
    if len(reason)>2000:raise ValueError('Decision reason must be at most 2000 characters.')

    target=db.session.get(User,user_id)
    if not target:raise ValueError('Account not found.')
    before=current_type(target);original=requested_type(target)
    requested=AccountSecurityEvent.query.filter_by(subject_user_id=user_id,action='account_type_requested').order_by(AccountSecurityEvent.id.desc()).first()
    if requested:original=requested.details['account_type']

    def authorize(user):
        actor=lock_administration()
        if not administrator(actor):raise PermissionError('Account administration changed. Sign in again.')
        if actor.id==user.id or user.role=='grandmaster':raise ValueError('Self-conversion and ordinary changes to protected Grandmasters are not permitted.')
        verify_review(user,token)
        if outcome=='approved':
            if not user.is_active or not user.email_verified_at:raise ValueError('An active account and verified email are required before approval.')
            value=str(employee_id or '').strip()
            if selected!='full' or value!=str(user.employee_id or '').strip() or confirmed_identity=='yes' or person_id:
                import re
                if not re.fullmatch(r'(?:[0-9]{7}|[0-9]{10})',value):raise ValueError('Employee / UPS ID must contain exactly 7 or 10 digits, numbers only.')
                if value!=user.employee_id or not identity_verified(user) or person_id:
                    if confirmed_identity!='yes':raise ValueError('Verify and confirm this Employee / UPS ID before approval.')
                    verify_ups_id(user.id,token,value,reason or 'Identity verified during account approval.',person_id)
        return actor

    if outcome=='denied':
        lock_administration()
        target=User.query.filter_by(id=user_id).populate_existing().with_for_update().one()
        authorize(target)
        identity = db.session.get(FuelDriverIdentity, user_id)
        if not identity or not identity.requested_driver:
            raise ValueError('Deny individual application requests in Application access.')
        access = PortalAppAccess.query.filter_by(user_id=user_id, app_code='neogateway').with_for_update().first()
        attempt = AccountAccessRequest.query.filter_by(user_id=user_id, app_code='neogateway', state='pending').first()
        if access and access.status == 'approved' and access.is_active:
            raise ValueError('This account already has approved NeoGateway access.')
        if not attempt and not (access and access.is_active and access.status == 'pending'):
            raise ValueError('No pending restricted account request exists to deny.')
        if access:
            legacy._deny_portal_app_access(access, reason)
        close_open_attempt(target, 'neogateway', 'denied', reason)
        rotate_user_session_version(target)
    else:
        converted = convert_account(get_current_gateway(),current_user,target,selected,authorize=authorize,
            can_change_role=lambda role:platform_grandmaster(current_user) and role in legacy.ROLE_CHOICES)
        identity=db.session.get(FuelDriverIdentity,target.id)
        if identity is None:
            identity=FuelDriverIdentity(user_id=target.id);db.session.add(identity)
        identity.account_type=selected;identity.requested_driver=False;identity.version=int(identity.version or 1)+1
        db.session.flush()
        if converted and selected != 'full':
            for attempt in AccountAccessRequest.query.filter_by(user_id=user_id,state='pending').all():
                close_open_attempt(target,attempt.app_code,'approved' if attempt.app_code=='neogateway' else 'withdrawn',reason)
        rotate_user_session_version(target)
    record_security_change('account_type_decided',target,original_request=original,before=before,
        selected=selected,outcome=outcome,reason=reason,
        supervisor_read_only=selected=='vendor_supervisor',identity_preserved=True)
    db.session.flush()
    return target
