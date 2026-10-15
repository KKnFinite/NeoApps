"""Gateway/vendor security and sort-locked driver participation. Caller commits."""
import re
import secrets
from datetime import datetime
from flask import g, request, redirect, url_for, abort
from flask_login import current_user
from sqlalchemy import func
from app.extensions import db
from app.models import (User, UserToken, Gateway, NeoScorpionFuelTruck, NeoScorpionSortTruck,
    NeoScorpionTruckDriver, NeoScorpionVendorAccount, NeoScorpionVendorNight,
    NeoScorpionVendorRequest, NeoScorpionVendorAudit)
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change


def audit(gateway, actor, action, *, operation=None, subject=None, truck_id=None, request_id=None, **facts):
    db.session.add(NeoScorpionVendorAudit(gateway_id=gateway.id, actor_user_id=actor.id,
        sort_date_operation_id=operation.id if operation else None,
        subject_user_id=subject.id if subject else None, fuel_truck_id=truck_id,
        request_id=request_id, action=action, facts=facts))


def vendor_account(user):
    return db.session.get(NeoScorpionVendorAccount, user.id) if user and user.is_authenticated else None


def dispatcher(gateway, actor):
    from app.services.neoscorpion_assets import eligible_nightly_fueler_users
    # The existing bounded asset resolver enforces this gateway's membership,
    # app approval and node role without lazy authentication backfill writes.
    if (not actor or not actor.is_active or vendor_account(actor) or
            not eligible_nightly_fueler_users(gateway,active_users=[actor],permission_key='neoscorpion.fuel_dispatch.edit')):
        raise PermissionError('Dispatcher edit authority is required.')


def current_operation(gateway, expected_id=None):
    from app.services.neoscorpion import current_sort_operation
    operation = current_sort_operation(gateway)
    if not operation or operation.archived_at_utc or (expected_id is not None and str(operation.id) != str(expected_id)):
        raise ValueError('The active sort changed or ended. Refresh before continuing.')
    return operation


def expire_user_requests(gateway, operation, actor, user_id, reason):
    for row in NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
            user_id=user_id, status='pending').all():
        row.status = 'expired'; row.version += 1
        row.decided_at = datetime.utcnow(); row.decided_by_user_id = actor.id
        audit(gateway, actor, 'request_expired', operation=operation, request_id=row.id, reason=reason)


def require_approved(account, operation):
    if account is None:
        raise PermissionError('Verified vendor account required.')
    # A request may wait on the sort lock after its authentication guard ran.
    # Never authorize that mutation from pre-lock identity-map snapshots.
    account = NeoScorpionVendorAccount.query.filter_by(user_id=account.user_id).populate_existing().one()
    user = User.query.filter_by(id=account.user_id).populate_existing().one()
    approval = NeoScorpionVendorNight.query.filter_by(sort_date_operation_id=operation.id,
        user_id=user.id).populate_existing().first()
    if (not account.enabled or not account.gateway.is_active or not user.is_active or not user.email_verified_at or
            operation.gateway_id != account.gateway_id or operation.archived_at_utc or
            not approval or approval.status != 'approved'):
        raise PermissionError('Dispatcher approval for this active sort is required.')
    return approval


def vendor_request_boundary():
    """Global default-deny, including other blueprints, future routes and APIs."""
    if request.endpoint == 'static':
        return None  # Public assets do not need a user/profile database lookup.
    if not current_user.is_authenticated:
        return None
    account = vendor_account(current_user)
    if not account:
        return None
    g.scorpion_vendor_account = account
    endpoint = request.endpoint or ''
    if endpoint in {'auth.portal_dashboard', 'neonodes.index'} and request.method == 'GET':
        return redirect(url_for('scorpion_vendor.account'))
    allowed_auth = {'auth.login', 'auth.logout', 'auth.change_password', 'auth.forgot_password',
        'auth.reset_password', 'auth.verify_email'}
    if endpoint == 'static' or endpoint in allowed_auth or endpoint == 'healthz':
        return None
    if endpoint in {'scorpion_vendor.account','scorpion_vendor.my_truck','scorpion_vendor.live',
            'scorpion_vendor.revision','scorpion_vendor.fuel_reading','scorpion_vendor.invitation'}:
        return None  # Each operational handler independently checks live approval.
    abort(403)


def invite(gateway, actor, email, name):
    dispatcher(gateway, actor)
    email = str(email or '').strip().lower()
    name = str(name or '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 255 or not 1 <= len(name) <= 120:
        raise ValueError('Enter a valid email and driver name (120 characters maximum).')
    if User.query.filter(func.lower(User.email) == email).first():
        raise ValueError('This email already has an account. Do not convert an employee account to a vendor.')
    from app.services.password_policy import set_user_password
    from app.services.user_tokens import create_user_token, EMAIL_VERIFICATION
    user = User(username='vendor-' + secrets.token_hex(16), email=email, full_name=name,
        role='watcher', is_active=True, is_management=False, employee_id=None)
    set_user_password(user, secrets.token_urlsafe(48) + 'aA1!')
    db.session.add(user); db.session.flush()
    db.session.add(NeoScorpionVendorAccount(user_id=user.id, gateway_id=gateway.id, invited_by_user_id=actor.id))
    token, _record = create_user_token(user, EMAIL_VERIFICATION, expires_in_hours=24)
    audit(gateway, actor, 'vendor_invited', subject=user)
    db.session.flush()
    return user, token


def accept_invitation(raw_token, password, confirmation):
    from app.services.user_tokens import hash_user_token, EMAIL_VERIFICATION, mark_token_used
    from app.services.password_policy import set_user_password
    token = UserToken.query.filter_by(token_hash=hash_user_token(raw_token), token_type=EMAIL_VERIFICATION).with_for_update().populate_existing().first()
    if not token or token.is_used or token.is_expired():
        raise ValueError('Invitation expired or already used.')
    account = db.session.get(NeoScorpionVendorAccount, token.user_id)
    if not account or not account.enabled or account.user.email_verified_at:
        raise ValueError('Invitation unavailable.')
    if password != confirmation:
        raise ValueError('Passwords do not match.')
    set_user_password(account.user, password)
    account.user.email_verified_at = datetime.utcnow()
    mark_token_used(token)
    audit(account.gateway, account.user, 'vendor_registered', subject=account.user)
    db.session.flush()
    return account.user


def renew_invitation(gateway, actor, user_id):
    dispatcher(gateway,actor)
    account=NeoScorpionVendorAccount.query.filter_by(user_id=int(user_id),gateway_id=gateway.id).with_for_update().populate_existing().one_or_none()
    if not account or not account.enabled or account.user.email_verified_at:
        raise ValueError('Only enabled, unregistered vendor invitations can be renewed.')
    from app.services.user_tokens import revoke_unused_email_verification_tokens,create_user_token,EMAIL_VERIFICATION
    revoke_unused_email_verification_tokens(account.user)
    raw,_record=create_user_token(account.user,EMAIL_VERIFICATION,expires_in_hours=24)
    audit(gateway,actor,'invitation_renewed',subject=account.user)
    return account.user,raw


def set_participation(gateway, operation, actor, user_id, status, expected_version):
    dispatcher(gateway, actor)
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(gateway, operation.id)
    account = NeoScorpionVendorAccount.query.filter_by(user_id=int(user_id), gateway_id=gateway.id).with_for_update().populate_existing().one_or_none()
    if not account or not account.enabled or not account.user.is_active or not account.user.email_verified_at:
        raise ValueError('Select a registered, verified, enabled vendor at this gateway.')
    if status not in {'approved', 'denied', 'revoked'}:
        raise ValueError('Invalid participation decision.')
    night = db.session.get(NeoScorpionVendorNight, (operation.id, account.user_id))
    if str(night.version if night else 0) != str(expected_version):
        raise ValueError('Approval changed. Refresh first.')
    if not night:
        night = NeoScorpionVendorNight(sort_date_operation_id=operation.id, user_id=account.user_id)
        db.session.add(night)
    night.status = status; night.version = int(night.version or 0) + 1
    night.changed_by_user_id = actor.id; night.updated_at = datetime.utcnow()
    if status != 'approved':
        disconnect(gateway, operation, actor, user_id=account.user_id)
        expire_user_requests(gateway, operation, actor, account.user_id, 'participation_' + status)
    audit(gateway, actor, 'vendor_' + status, operation=operation, subject=account.user, version=night.version)
    record_nightly_operational_change(state, operation.id); db.session.flush()


def disconnect(gateway, operation, actor, *, user_id=None, truck_id=None):
    from app.services.neoscorpion_drivers import change_driver
    rows = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id)
    rows = rows.filter_by(driver_user_id=user_id) if user_id else rows.filter_by(fuel_truck_id=truck_id)
    for row in rows.all():
        if row.driver_user_id:
            change_driver(gateway, operation, actor, row.fuel_truck_id, None, dispatcher=True,
                expected_driver_id=row.driver_user_id, expected_driver_truck_id=None)
        row.manual_name = ''


def request_claim(account, operation, truck_id, gallons=None):
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(account.gateway, operation.id); require_approved(account, operation)
    truck = NeoScorpionFuelTruck.query.filter_by(id=int(truck_id), gateway_id=account.gateway_id,
        is_active=True, is_out_of_service=False).one_or_none()
    if not truck:
        raise ValueError('Choose an eligible gateway truck.')
    selected = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id, fuel_truck_id=truck.id).first()
    if selected and selected.status != 'available':
        raise ValueError('This truck is not operationally available.')
    value = None
    if selected is None:
        from app.services.neoscorpion_inventory import gallons_value
        value = gallons_value(truck, gallons)
    pending = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        user_id=account.user_id, kind='claim', status='pending').all()
    for old in pending:
        if old.fuel_truck_id == truck.id and old.gallons == value:
            return old
        old.status = 'superseded'
    row = NeoScorpionVendorRequest(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
        user_id=account.user_id, kind='claim', gallons=value)
    db.session.add(row); db.session.flush()
    audit(account.gateway, account.user, 'truck_claimed', operation=operation, truck_id=truck.id, request_id=row.id)
    record_nightly_operational_change(state, operation.id)
    return row


def decide_claim(gateway, operation, actor, request_id, approve, expected_version, *, override=False):
    dispatcher(gateway, actor)
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(gateway, operation.id)
    row = NeoScorpionVendorRequest.query.filter_by(id=int(request_id), sort_date_operation_id=operation.id,
        kind='claim').with_for_update().populate_existing().one_or_none()
    if not row or row.status != 'pending' or str(row.version) != str(expected_version):
        raise ValueError('This request changed or was already decided.')
    account = db.session.get(NeoScorpionVendorAccount, row.user_id)
    require_approved(account, operation)
    if approve:
        from app.services.neoscorpion_assets import select_nightly_truck
        from app.services.neoscorpion_drivers import change_driver
        truck = row.truck
        if truck.gateway_id != gateway.id or not truck.is_active or truck.is_out_of_service:
            raise ValueError('Truck no longer eligible.')
        selected = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id, fuel_truck_id=truck.id).first()
        if selected is None:
            select_nightly_truck(operation, truck, status='available', starting_gallons=row.gallons,
                current_gallons=row.gallons, changed_by_user=actor)
        elif selected.status != 'available':
            raise ValueError('Truck no longer available.')
        target = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=truck.id).first()
        own = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,driver_user_id=row.user_id).first()
        change_driver(gateway, operation, actor, truck.id, row.user_id, dispatcher=True,
            expected_driver_id=target.driver_user_id if target else None,
            expected_driver_truck_id=own.fuel_truck_id if own else None,
            override=override, reason='Dispatcher approved verified vendor truck claim.')
    row.status = 'approved' if approve else 'denied'; row.version += 1
    row.decided_at = datetime.utcnow(); row.decided_by_user_id = actor.id
    audit(gateway, actor, 'claim_' + row.status, operation=operation, subject=row.user, truck_id=row.fuel_truck_id, request_id=row.id)
    record_nightly_operational_change(state, operation.id); db.session.flush()


def driver_name(gateway, operation, truck_id, rows=None):
    row = (rows or {}).get(truck_id) if rows is not None else NeoScorpionTruckDriver.query.filter_by(
        sort_date_operation_id=operation.id, fuel_truck_id=truck_id).first()
    if row and row.driver:
        if not row.driver.is_active:
            return 'UNKNOWN'
        account = vendor_account(row.driver)
        if account:
            try: require_approved(account, operation)
            except PermissionError: return 'UNKNOWN'
        return row.driver.display_name
    return row.manual_name if row and row.manual_name else 'UNKNOWN'
