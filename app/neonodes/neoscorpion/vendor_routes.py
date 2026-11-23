"""Separate vendor surface; normal Dispatch remains employee-only."""
from functools import wraps
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, abort, jsonify, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import (User, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    NeoScorpionVendorAudit, NeoScorpionTruckDriver, NeoScorpionSortTruck, NeoScorpionFuelTruck)
from app.services import neoscorpion_vendors as vendors
from app.services import neoscorpion_inventory as inventory
from app.services.access_control import get_current_gateway
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change
from app.services.neoscorpion_driver_missions import group_missions, sort_missions_revision

bp = Blueprint('scorpion_vendor', __name__)


def vendor_refresh_windows(gateway):
    """Publish upcoming *physical* Sort windows, not planning/Ops windows.

    Old tabs fail closed after the bounded horizon; an explicit page reload can
    obtain the latest schedule. A timer that runs outside these windows does no I/O.
    """
    from app.services.gateway_matrix import screen_poll_windows
    return [[datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() for ms in period]
            for period in screen_poll_windows(gateway)]


def vendor_revision_in_sort(gateway):
    """Reject expensive revision projection outside real configured Sort hours."""
    from app.services.gateway_matrix import current_gateway_local_datetime, gateway_timezone, screen_poll_windows
    from zoneinfo import ZoneInfo
    periods = screen_poll_windows(gateway)
    if not periods:
        return False
    local_now = current_gateway_local_datetime(gateway)
    instant = local_now.replace(tzinfo=ZoneInfo(gateway_timezone(gateway))).timestamp() * 1000
    return any(start <= instant < end for start, end in periods)


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
            return redirect(url_for('auth.login'))
        account = vendors.vendor_account(current_user)
        if not account: abort(403)
        return view(account, *args, **kwargs)
    return wrapped


@bp.route('/login', methods=['GET', 'POST'])
def login():
    return redirect(url_for("auth.login"), code=307 if request.method == "POST" else 302)


@bp.post('/logout')
@vendor_only
def logout(account):
    return redirect(url_for("auth.logout"), code=307)


@bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    return redirect(url_for("auth.forgot_password"), code=307 if request.method == "POST" else 302)


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    return redirect(url_for("auth.reset_password", token=token), code=307 if request.method == "POST" else 302)


@bp.route('/change-password', methods=['GET', 'POST'])
@vendor_only
def change_password(account):
    return redirect(url_for("auth.change_password"), code=307 if request.method == "POST" else 302)


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
    if request.endpoint in {'scorpion_vendor.revision','scorpion_vendor.supervisor_revision'} or request.form.get('dispatch_assets_workspace') == '1':
        return jsonify(ok=False,error=str(error)),403
    profile = vendors.vendor_account(current_user)
    if profile:
        return render_template('neonodes/neoscorpion/vendor_truck.html', driver_error=str(error)), 403
    return str(error), 403


def workspace_result(gateway, *, account_id=None):
    """Read the existing revision after commit; never create a parallel state."""
    from app.services.neoscorpion import fuel_assignments_live_revision
    payload = dict(ok=True, changed=True, **fuel_assignments_live_revision(gateway))
    if account_id is not None:
        profile = NeoScorpionVendorAccount.query.filter_by(user_id=account_id, gateway_id=gateway.id).first()
        if profile:
            operation_id = payload['operation_id']
            night = db.session.get(NeoScorpionVendorNight, (operation_id, profile.user_id)) if operation_id else None
            payload['account_html'] = render_template('neonodes/neoscorpion/_asset_driver_account.html',
                profile=profile, night=night, operation_id=operation_id)
    return jsonify(payload)


@bp.route('/invitation/<token>', methods=['GET','POST'])
def invitation(token):
    # Old links reference already-created identities. Verify them through the
    # shared email flow; never accept a password through the retired form.
    return redirect(url_for('auth.verify_email', token=token), code=303)


@bp.get('/account')
@vendor_only
def account(account):
    return redirect(url_for('scorpion_vendor.my_truck'))


def my_truck_context(account, operation, user):
    from app.services.neoscorpion_drivers import driver_context
    context = driver_context(account.gateway,user)
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


def waiting_context(account, operation, status):
    from app.services.fuel_driver_accounts import supervisor_membership
    is_supervisor = account.user_id is not None and bool(supervisor_membership(account))
    claim = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        user_id=account.user_id, kind='claim').order_by(NeoScorpionVendorRequest.id.desc()).first() if operation and account.user_id is not None else None
    trucks = NeoScorpionFuelTruck.query.filter_by(gateway_id=account.gateway_id, is_active=True,
        is_out_of_service=False).order_by(NeoScorpionFuelTruck.truck_number).all() if operation and not is_supervisor and status not in {'DISPATCHER APPROVAL DENIED','DISPATCHER APPROVAL REVOKED'} else []
    selections = {s.fuel_truck_id:s for s in NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id).all()} if operation else {}
    trucks = [t for t in trucks if t.id not in selections or selections[t.id].status == 'available']
    fingerprint = dict(status=status, operation_id=operation.id if operation else None,
        claim=(claim.id,claim.status,claim.version) if claim else None, trucks=[t.id for t in trucks], supervisor=is_supervisor)
    return dict(waiting_operation=operation, waiting_status=status, available_trucks=trucks,
        latest_claim=claim, is_supervisor=is_supervisor, waiting_fingerprint=fingerprint)


def driver_screen_context(account, user):
    """Render Driver waiting or approved data for an already authorized account."""
    operation, waiting_status = driver_access(account)
    from app.services.fuel_driver_accounts import supervisor_membership
    if supervisor_membership(account):
        waiting_status = 'VENDOR SUPERVISOR · READ ONLY'
    if waiting_status:
        return dict(account=account, **waiting_context(account, operation, waiting_status))
    vendors.require_approved(account, operation)
    fingerprint = vendor_fingerprint(account, operation, include_sort_missions=True)
    context = my_truck_context(account, operation, user)
    if fingerprint['sort_missions_revision'] is not None and context['selection'] is not None:
        from app.services.neoscorpion import fuel_dispatch_context
        dispatch = fuel_dispatch_context(account.gateway, include_spear=False, display_nicknames=False)
        require_same_operation(dispatch['operation'], operation)
        context['sort_missions'] = group_missions(dispatch['rows'])
    return dict(context, fingerprint=fingerprint)


@bp.route('/my-truck', methods=['GET','POST'])
@vendor_only
def my_truck(account):
    if request.method == 'POST':
        try:
            if request.form.get('action') == 'disconnect':
                operation = operational(account)
                operation,state=lock_nightly_asset_scope_for_mutation(operation)
                vendors.current_operation(account.gateway, operation.id)
                vendors.require_approved(account, operation)
                vendors.disconnect(account.gateway,operation,current_user,user_id=current_user.id)
                vendors.expire_user_requests(account.gateway, operation, current_user, account.user_id, 'driver_disconnected')
                vendors.audit(account.gateway,current_user,'driver_disconnected',operation=operation,subject=current_user)
                record_nightly_operational_change(state,operation.id)
            else:
                if not request.form.get('operation_id'):
                    raise ValueError('Select the current Sort before requesting a truck.')
                operation = vendors.current_operation(account.gateway, request.form.get('operation_id'))
                vendors.request_claim(account,operation,request.form.get('truck_id'),request.form.get('gallons'))
            db.session.commit()
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback(); flash(str(exc) if isinstance(exc,ValueError) else 'Request changed. Refresh.','error')
        return redirect(url_for('scorpion_vendor.my_truck'))
    return render_template('neonodes/neoscorpion/vendor_truck.html',
        refresh_windows=vendor_refresh_windows(account.gateway),
        **driver_screen_context(account, current_user))


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
        raise PermissionError('The active sort changed or ended. Return to Fuel Truck Driver for your status.')


def vendor_fingerprint(account, operation, *, include_sort_missions=False):
    from app.services.neoscorpion import neoscorpion_live_revision
    from app.services.neoscorpion_assets import fueler_on_break
    fingerprint = neoscorpion_live_revision(account.gateway)
    fingerprint['fueler_on_break'] = fueler_on_break(fingerprint['operation_id'], account.user_id)
    if fingerprint['operation_id'] != operation.id:
        raise PermissionError('The active sort changed or ended. Return to Fuel Truck Driver for your status.')
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
    return render_template('neonodes/neoscorpion/vendor_live.html', account=account,
        fingerprint=fingerprint, refresh_windows=vendor_refresh_windows(account.gateway), **context)


@bp.get('/revision')
@vendor_only
def revision(account):
    if not vendor_revision_in_sort(account.gateway):
        return '', 204
    if request.args.get('waiting') == '1':
        operation, status = driver_access(account)
        from app.services.fuel_driver_accounts import supervisor_membership
        if supervisor_membership(account):
            status = 'VENDOR SUPERVISOR · READ ONLY'
        return jsonify(waiting_context(account, operation, status)['waiting_fingerprint'])
    operation = operational(account)
    if request.args.get('view') == 'live':
        require_connected_truck(account, operation)
        return jsonify(vendor_fingerprint(account, operation))
    return jsonify(vendor_fingerprint(account, operation, include_sort_missions=True))


@bp.get('/supervisor')
@vendor_only
def supervisor(account):
    from app.services.fuel_driver_accounts import supervisor_context
    return render_template('neonodes/neoscorpion/vendor_supervisor.html',
        refresh_windows=vendor_refresh_windows(account.gateway), **supervisor_context(account))


@bp.get('/supervisor/revision')
@vendor_only
def supervisor_revision(account):
    if not vendor_revision_in_sort(account.gateway):
        return '', 204
    from app.services.fuel_driver_accounts import supervisor_context
    return jsonify(supervisor_context(account)['fingerprint'])


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


@bp.route('/manage',methods=['GET','POST'])
@login_required
def manage():
    gateway=get_current_gateway(); vendors.dispatcher(gateway,current_user)
    from app.services.neoscorpion import current_sort_operation
    operation=current_sort_operation(gateway)
    if request.method=='POST':
        action = request.form.get('action')
        try:
            if action == 'signup_invite':
                from app.services.fuel_driver_invitations import send_signup_invitation
                from app.services.auth_rate_limits import client_ip_for_request
                sent = send_signup_invitation(request.form.get('email'), client_ip_for_request(request), gateway)
                flash('NeoApps signup invitation sent.' if sent else 'Invitation delivery could not be confirmed. No account was created.', 'info' if sent else 'error')
                return redirect(url_for('scorpion_vendor.manage'))
            if action not in {'invite','resend','disable','enable'}:
                operation=vendors.current_operation(gateway,request.form.get('operation_id'))
            if action in {'invite','resend'}:
                raise ValueError('Driver invitations are retired. Use NeoApps Create Account and User Management.')
            elif action in {'approved','denied','revoked'}:
                vendors.set_participation(gateway,operation,current_user,request.form.get('user_id'),action,request.form.get('version'))
            elif action in {'claim_approve','claim_deny'}:
                vendors.decide_claim(gateway,operation,current_user,request.form.get('request_id'),action=='claim_approve',
                    request.form.get('version'),override=request.form.get('override')=='1', expected_night_version=request.form.get('night_version'))
            elif action in {'disable','enable'}:
                state=None
                if operation:
                    operation,state=lock_nightly_asset_scope_for_mutation(operation)
                user_id = int(request.form.get('user_id'))
                User.query.with_entities(User.id).filter_by(id=user_id).with_for_update(key_share=True).first()
                profile = NeoScorpionVendorAccount.query.filter_by(
                    user_id=user_id, gateway_id=gateway.id,
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
            db.session.commit()
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback()
            message=str(exc) if isinstance(exc,ValueError) else 'Driver state changed. Refresh.'
            if request.form.get('dispatch_assets_workspace')=='1':
                return jsonify(ok=False,error=message),400
            flash(message,'error')
            return redirect(url_for('scorpion_vendor.manage'))
        if request.form.get('dispatch_assets_workspace')=='1':
            account_id = (int(request.form['user_id'])
                          if action in {'approved', 'denied', 'revoked', 'enable', 'disable'} else None)
            return workspace_result(gateway, account_id=account_id)
        return redirect(url_for('scorpion_vendor.manage'))
    return render_template('neonodes/neoscorpion/vendor_manage.html',gateway=gateway,operation=operation,
        **driver_management_context(gateway,operation))


def driver_management_context(gateway, operation, *, active_users=None):
    """Existing gateway-scoped administration data, shared by Dispatch and fallback."""
    profiles=NeoScorpionVendorAccount.query.filter_by(gateway_id=gateway.id).all()
    nights={r.user_id:r for r in NeoScorpionVendorNight.query.filter_by(sort_date_operation_id=operation.id).all()} if operation else {}
    from app.services.neoscorpion_drivers import driver_roster
    roster=driver_roster(gateway, operation, include_choices=True, active_users=active_users)
    connections=list(roster['drivers_by_truck_id'].values())
    employee_choices=roster['eligible_drivers']
    approved_vendors=[p.user for p in profiles if p.enabled and p.user.is_active and p.user.email_verified_at and nights.get(p.user_id) and nights[p.user_id].status=='approved']
    return dict(profiles=profiles,nights=nights,driver_choices=employee_choices+approved_vendors,
        connections={r.fuel_truck_id:r for r in connections}, trucks_by_driver={r.driver_user_id:r.fuel_truck_id for r in connections if r.driver_user_id},
        pending=NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,kind='claim',status='pending').all() if operation else [],
        driver_trucks=roster['driver_trucks'],
        audits=NeoScorpionVendorAudit.query.filter_by(gateway_id=gateway.id).order_by(NeoScorpionVendorAudit.id.desc()).limit(100).all())
