"""Separate vendor surface; normal Dispatch remains employee-only."""
from functools import wraps
from datetime import datetime
from flask import Blueprint, current_app, render_template, request, redirect, url_for, abort, jsonify, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.auth import routes as auth
from app.extensions import db
from app.models import (NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    NeoScorpionVendorAudit, NeoScorpionTruckDriver, NeoScorpionSortTruck, NeoScorpionFuelTruck)
from app.services import neoscorpion_vendors as vendors
from app.services import neoscorpion_inventory as inventory
from app.services.access_control import get_current_gateway
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change
from app.services.neoscorpion_driver_missions import group_missions, sort_missions_revision

bp = Blueprint('scorpion_vendor', __name__)


@bp.context_processor
def navigation_context():
    from app.neonodes.neoscorpion.routes import inject_neoscorpion_navigation
    return inject_neoscorpion_navigation()


@bp.after_request
def private_response(response):
    response.headers['Cache-Control']='no-store'
    response.headers['Referrer-Policy']='no-referrer'
    return response


def vendor_only(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('scorpion_vendor.login'))
        account = vendors.vendor_account(current_user)
        if not account: abort(403)
        return view(account, *args, **kwargs)
    return wrapped


@bp.route('/login', methods=['GET', 'POST'])
def login():
    return auth.login_response(vendor_driver=True)


@bp.post('/logout')
@vendor_only
def logout(account):
    return auth.logout_response(vendor_driver=True)


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    return auth.forgot_password_response(vendor_driver=True)


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    return auth.reset_password_response(token, vendor_driver=True)


@bp.route('/change-password', methods=['GET', 'POST'])
@vendor_only
def change_password(account):
    return auth.change_password_response(vendor_driver=True)


def driver_access(account):
    from app.services.neoscorpion import current_sort_operation
    operation = current_sort_operation(account.gateway) if account.gateway.is_active else None
    if not operation or operation.archived_at_utc:
        return None, 'NO ACTIVE SORT'
    night = db.session.get(NeoScorpionVendorNight, (operation.id, account.user_id))
    status = night.status if night else 'pending'
    message = {
        'approved': None,
        'denied': 'DISPATCHER APPROVAL DENIED',
        'revoked': 'DISPATCHER APPROVAL REVOKED',
        'pending': 'AWAITING DISPATCHER APPROVAL',
    }[status]
    return operation, message


def operational(account):
    if not account.enabled:
        raise PermissionError('Your NeoScorpion driver account is disabled. Contact Dispatch for help.')
    expected_id = (request.form if request.method == 'POST' else request.args).get('operation_id')
    try:
        operation = vendors.current_operation(account.gateway, expected_id)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    vendors.require_approved(account, operation)
    return operation


@bp.errorhandler(PermissionError)
def denied(error):
    if request.endpoint == 'scorpion_vendor.revision' or request.form.get('dispatch_assets_workspace') == '1':
        return jsonify(ok=False,error=str(error)),403
    profile = vendors.vendor_account(current_user)
    if profile:
        return render_template('neonodes/neoscorpion/vendor_truck.html', driver_error=str(error)), 403
    return str(error), 403


def workspace_result(gateway, *, message=None):
    """Read the existing revision after commit; never create a parallel state."""
    from app.services.neoscorpion import fuel_assignments_live_revision
    payload = dict(ok=True, changed=True, **fuel_assignments_live_revision(gateway))
    if message:
        payload['message'] = message
    return jsonify(payload)


@bp.route('/invitation/<token>', methods=['GET','POST'])
def invitation(token):
    error = None
    valid = False
    try:
        if request.method == 'POST':
            vendors.accept_invitation(token,request.form.get('password',''),request.form.get('confirm_password',''))
            db.session.commit()
            flash('Registration complete. Sign in with your email and password.', 'success')
            return redirect(url_for('scorpion_vendor.login'))
        vendors.invitation_record(token)
        valid = True
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        error = str(exc) if isinstance(exc, ValueError) else 'Registration changed. Try opening the link again.'
        if request.method == 'POST':
            try:
                vendors.invitation_record(token)
                valid = True
            except ValueError as unavailable:
                error = str(unavailable)
    return render_template('neonodes/neoscorpion/vendor_invitation.html', error=error, valid=valid), 400 if error else 200


@bp.get('/account')
@vendor_only
def account(account):
    return redirect(url_for('scorpion_vendor.my_truck'))


def my_truck_context(account, operation):
    from app.services.neoscorpion_drivers import driver_context
    context = driver_context(account.gateway,current_user)
    require_same_operation(context['operation'], operation)
    claims = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        user_id=account.user_id, kind='claim')
    context.update(account=account, operation=operation,
        available_trucks=NeoScorpionFuelTruck.query.filter_by(gateway_id=account.gateway_id,is_active=True,is_out_of_service=False).order_by(NeoScorpionFuelTruck.truck_number).all(),
        latest_claim=claims.order_by(NeoScorpionVendorRequest.id.desc()).first(),
        pending_readings=NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
            user_id=account.user_id, kind='fuel', status='pending').all())
    selected = context['selected_driver_truck']
    context['selection'] = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=selected.id).first() if selected else None
    context['verification_available']=bool(selected and inventory.verification_available(operation,selected.id))
    return context


@bp.route('/my-truck', methods=['GET','POST'])
@vendor_only
def my_truck(account):
    if request.method == 'POST':
        try:
            operation = operational(account)
            if request.form.get('action') == 'disconnect':
                operation,state=lock_nightly_asset_scope_for_mutation(operation)
                vendors.current_operation(account.gateway, operation.id)
                vendors.require_approved(account, operation)
                vendors.disconnect(account.gateway,operation,current_user,user_id=current_user.id)
                vendors.expire_user_requests(account.gateway, operation, current_user, account.user_id, 'driver_disconnected')
                vendors.audit(account.gateway,current_user,'driver_disconnected',operation=operation,subject=current_user)
                record_nightly_operational_change(state,operation.id)
            else:
                vendors.request_claim(account,operation,request.form.get('truck_id'),request.form.get('gallons'))
            db.session.commit()
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback(); flash(str(exc) if isinstance(exc,ValueError) else 'Request changed. Refresh.','error')
        return redirect(url_for('scorpion_vendor.my_truck'))
    operation, waiting_status = driver_access(account)
    if waiting_status:
        return render_template('neonodes/neoscorpion/vendor_truck.html', waiting_status=waiting_status)
    vendors.require_approved(account, operation)
    fingerprint = vendor_fingerprint(account, operation, include_sort_missions=True)
    context = my_truck_context(account, operation)
    if fingerprint['sort_missions_revision'] is not None and context['selection'] is not None:
        from app.services.neoscorpion import fuel_dispatch_context
        dispatch = fuel_dispatch_context(account.gateway, include_spear=False, display_nicknames=False)
        require_same_operation(dispatch['operation'], operation)
        context['sort_missions'] = group_missions(dispatch['rows'])
    return render_template('neonodes/neoscorpion/vendor_truck.html', fingerprint=fingerprint,
        **context)


def connected_truck(account, operation):
    return db.session.query(NeoScorpionTruckDriver.fuel_truck_id).join(
        NeoScorpionSortTruck,
        (NeoScorpionSortTruck.sort_date_operation_id == NeoScorpionTruckDriver.sort_date_operation_id)
        & (NeoScorpionSortTruck.fuel_truck_id == NeoScorpionTruckDriver.fuel_truck_id),
    ).filter(
        NeoScorpionTruckDriver.sort_date_operation_id == operation.id,
        NeoScorpionTruckDriver.driver_user_id == account.user_id,
    ).first()


def require_connected_truck(account, operation):
    if not connected_truck(account, operation):
        raise PermissionError('An approved truck connection is required to view the live operation.')


def require_same_operation(current, expected):
    if not current or current.id != expected.id:
        raise PermissionError('The active sort changed or ended. Return to Truck Driver for your status.')


def vendor_fingerprint(account, operation, *, include_sort_missions=False):
    from app.services.neoscorpion import fuel_assignments_live_revision
    fingerprint = fuel_assignments_live_revision(account.gateway)
    if fingerprint['operation_id'] != operation.id:
        raise PermissionError('The active sort changed or ended. Return to Truck Driver for your status.')
    if include_sort_missions:
        fingerprint['sort_missions_revision'] = (
            sort_missions_revision(operation) if connected_truck(account, operation) else None
        )
    return fingerprint


@bp.get('/live')
@vendor_only
def live(account):
    operation = operational(account)
    fingerprint = vendor_fingerprint(account, operation)
    require_connected_truck(account, operation)
    from app.neonodes.neoscorpion.routes import _dispatch_presentation_context
    context = _dispatch_presentation_context(account.gateway)
    require_same_operation(context['operation'], operation)
    return render_template('neonodes/neoscorpion/vendor_live.html', account=account, fingerprint=fingerprint, **context)


@bp.get('/revision')
@vendor_only
def revision(account):
    if request.args.get('waiting') == '1':
        _, status = driver_access(account)
        return jsonify(status=status)
    operation = operational(account)
    if request.args.get('view') == 'live':
        require_connected_truck(account, operation)
        return jsonify(vendor_fingerprint(account, operation))
    return jsonify(vendor_fingerprint(account, operation, include_sort_missions=True))


@bp.post('/fuel-reading')
@login_required
def fuel_reading():
    # An older vendor tab can still submit its existing form.
    account = vendors.vendor_account(current_user)
    if account:
        return submit_fuel_reading(account.gateway, 'scorpion_vendor.my_truck')
    return submit_fuel_reading(get_current_gateway(), 'neoscorpion.fueler')


@bp.post('/my-truck/fuel-reading')
@vendor_only
def driver_fuel_reading(account):
    return submit_fuel_reading(account.gateway, 'scorpion_vendor.my_truck')


def submit_fuel_reading(gateway, destination):
    try:
        operation=vendors.current_operation(gateway,request.form.get('operation_id'))
        inventory.submit_verification(gateway,operation,current_user,request.form.get('truck_id'),request.form.get('gallons'))
        db.session.commit();flash('Fuel reading recorded. High discrepancies await Dispatch approval.','success')
    except (ValueError,TypeError,IntegrityError) as exc:
        db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else 'Reading changed. Refresh.','error')
    return redirect(url_for(destination))


@bp.post('/inventory')
@login_required
def manage_inventory():
    gateway=get_current_gateway()
    vendors.dispatcher(gateway,current_user)
    try:
        operation=vendors.current_operation(gateway,request.form.get('operation_id'))
        if request.form.get('action') == 'correct':
            inventory.correct_baseline(gateway,operation,current_user,request.form.get('truck_id'),
                request.form.get('gallons'),request.form.get('expected_epoch'),request.form.get('expected_posting_id'))
        else:
            inventory.decide_verification(gateway,operation,current_user,request.form.get('request_id'),
                request.form.get('action')=='approve',request.form.get('version'))
        db.session.commit()
    except (ValueError,TypeError,IntegrityError) as exc:
        db.session.rollback()
        message=str(exc) if isinstance(exc,ValueError) else 'Inventory changed. Refresh.'
        if request.form.get('dispatch_assets_workspace')=='1':
            return jsonify(ok=False,error=message),400
        flash(message,'error')
    if request.form.get('dispatch_assets_workspace')=='1':
        return workspace_result(gateway)
    return redirect(url_for('neoscorpion.fuel_dispatch'))


def send_invitation(gateway, actor, action):
    from app.services.email_service import send_vendor_invitation

    if action == 'invite':
        user, token = vendors.invite(gateway, actor, request.form.get('email'), request.form.get('name'))
    else:
        user, token = vendors.renew_invitation(gateway, actor, request.form.get('user_id'))

    delivery = send_vendor_invitation(user, token)
    if not delivery['sent']:
        reason = delivery.get('reason')
        if reason == 'missing_config':
            failure = 'Brevo mail configuration is incomplete.'
        elif reason == 'provider_rejected':
            failure = 'Brevo rejected the invitation email. Check Brevo transactional email settings.'
        else:
            failure = 'Brevo acceptance could not be confirmed.'
        state = ('No account was created. Retry Send Invitation.' if action == 'invite'
                 else 'The prior invitation is unchanged. Retry Resend Invitation.')
        raise ValueError(f'{failure} {state}')

    vendors.audit(gateway, actor, 'invitation_accepted_by_brevo', subject=user,
        message_id=delivery.get('message_id'), invitation_action=action)
    return 'Brevo accepted the invitation email. Inbox delivery is pending verification.'


@bp.route('/manage',methods=['GET','POST'])
@login_required
def manage():
    gateway=get_current_gateway(); vendors.dispatcher(gateway,current_user)
    from app.services.neoscorpion import current_sort_operation
    operation=current_sort_operation(gateway)
    if request.method=='POST':
        action = request.form.get('action')
        message = None
        try:
            if action not in {'invite','resend','disable','enable'}:
                operation=vendors.current_operation(gateway,request.form.get('operation_id'))
            if action in {'invite','resend'}:
                message = send_invitation(gateway, current_user, action)
            elif action in {'approved','denied','revoked'}:
                vendors.set_participation(gateway,operation,current_user,request.form.get('user_id'),action,request.form.get('version'))
            elif action in {'claim_approve','claim_deny'}:
                vendors.decide_claim(gateway,operation,current_user,request.form.get('request_id'),action=='claim_approve',
                    request.form.get('version'),override=request.form.get('override')=='1')
            elif action in {'disable','enable'}:
                state=None
                if operation:
                    operation,state=lock_nightly_asset_scope_for_mutation(operation)
                profile = NeoScorpionVendorAccount.query.filter_by(
                    user_id=int(request.form.get('user_id')), gateway_id=gateway.id,
                ).with_for_update().populate_existing().one_or_none()
                if not profile:
                    raise ValueError('Select a driver account at this gateway.')
                if action == 'disable' and profile.enabled:
                    from app.services.auth_session_security import rotate_user_session_version
                    rotate_user_session_version(profile.user)
                profile.enabled=action=='enable'
                if not profile.enabled and operation:
                    vendors.disconnect(gateway,operation,current_user,user_id=profile.user_id)
                    night=db.session.get(NeoScorpionVendorNight,(operation.id,profile.user_id))
                    if night:
                        night.status='revoked';night.version+=1
                        night.changed_by_user_id=current_user.id;night.updated_at=datetime.utcnow()
                    vendors.expire_user_requests(gateway,operation,current_user,profile.user_id,'account_disabled')
                vendors.audit(gateway,current_user,'account_'+action,operation=operation,subject=profile.user)
                if operation:record_nightly_operational_change(state,operation.id)
            elif action=='assign':
                from app.services.neoscorpion_drivers import change_driver
                operation,state=lock_nightly_asset_scope_for_mutation(operation)
                vendors.current_operation(gateway,operation.id)
                change_driver(gateway,operation,current_user,request.form['truck_id'],request.form.get('user_id'),
                    dispatcher=True,expected_driver_id=request.form.get('expected_driver_id'),
                    expected_driver_truck_id=request.form.get('expected_driver_truck_id'),
                    override=request.form.get('override')=='1',reason=request.form.get('reason',''))
            elif action=='manual':
                operation,state=lock_nightly_asset_scope_for_mutation(operation)
                vendors.current_operation(gateway,operation.id)
                truck = NeoScorpionFuelTruck.query.filter_by(
                    id=int(request.form.get('truck_id')), gateway_id=gateway.id,
                ).one_or_none()
                if not truck:
                    raise ValueError('Select a truck at this gateway.')
                name=request.form.get('name','').strip()
                if len(name)>120:raise ValueError('Driver name must be at most 120 characters.')
                existing=NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=truck.id).first()
                previous_driver = existing.driver_user_id if existing else None
                if str(previous_driver or '') != request.form.get('expected_driver_id',''):
                    raise ValueError('Driver assignment changed. Refresh before replacing it.')
                vendors.disconnect(gateway,operation,current_user,truck_id=truck.id)
                row=NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=truck.id).first()
                if not row:
                    row=NeoScorpionTruckDriver(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,changed_by_user_id=current_user.id)
                    db.session.add(row)
                row.manual_name=name
                vendors.audit(gateway,current_user,'manual_driver',operation=operation,truck_id=truck.id,name=name)
                record_nightly_operational_change(state,operation.id)
            else:raise ValueError('Unsupported driver action.')
            try:
                db.session.commit()
            except SQLAlchemyError:
                if not message:
                    raise
                current_app.logger.warning('Vendor invitation was accepted by Brevo but its save could not be confirmed.')
                raise ValueError('Brevo accepted the email, but the registration link could not be confirmed. Refresh the account list before retrying.') from None
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback()
            message=str(exc) if isinstance(exc,ValueError) else 'Driver state changed. Refresh.'
            if request.form.get('dispatch_assets_workspace')=='1':
                return jsonify(ok=False,error=message),400
            flash(message,'error')
            return redirect(url_for('scorpion_vendor.manage'))
        if message:
            # Release account and token locks before taking the nightly lock.
            # Account management remains committed even if this refresh fails.
            try:
                operation = current_sort_operation(gateway)
                if operation:
                    operation, state = lock_nightly_asset_scope_for_mutation(operation)
                    record_nightly_operational_change(state, operation.id)
                    db.session.commit()
            except (ValueError, SQLAlchemyError):
                db.session.rollback()
                current_app.logger.warning('Could not publish the vendor invitation refresh.')
                message += ' Refresh Nightly Trucks to update the account list.'
        if request.form.get('dispatch_assets_workspace')=='1':
            return workspace_result(gateway, message=message)
        if message:
            flash(message, 'success')
        return redirect(url_for('scorpion_vendor.manage'))
    return render_template('neonodes/neoscorpion/vendor_manage.html',gateway=gateway,operation=operation,
        **driver_management_context(gateway,operation))


def driver_management_context(gateway,operation):
    """Existing gateway-scoped administration data, shared by Dispatch and fallback."""
    profiles=NeoScorpionVendorAccount.query.filter_by(gateway_id=gateway.id).all()
    nights={r.user_id:r for r in NeoScorpionVendorNight.query.filter_by(sort_date_operation_id=operation.id).all()} if operation else {}
    from app.services.neoscorpion_drivers import driver_roster
    roster=driver_roster(gateway,operation,include_choices=True)
    connections=list(roster['drivers_by_truck_id'].values())
    employee_choices=roster['eligible_drivers']
    approved_vendors=[p.user for p in profiles if p.enabled and p.user.is_active and p.user.email_verified_at and nights.get(p.user_id) and nights[p.user_id].status=='approved']
    return dict(profiles=profiles,nights=nights,driver_choices=employee_choices+approved_vendors,
        connections={r.fuel_truck_id:r for r in connections}, trucks_by_driver={r.driver_user_id:r.fuel_truck_id for r in connections if r.driver_user_id},
        pending=NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,kind='claim',status='pending').all() if operation else [],
        driver_trucks=roster['driver_trucks'],
        audits=NeoScorpionVendorAudit.query.filter_by(gateway_id=gateway.id).order_by(NeoScorpionVendorAudit.id.desc()).limit(100).all())
