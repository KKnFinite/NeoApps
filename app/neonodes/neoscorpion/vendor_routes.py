"""Separate vendor surface; normal Dispatch remains employee-only."""
from functools import wraps
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, abort, jsonify, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import (NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    NeoScorpionVendorAudit, NeoScorpionTruckDriver, NeoScorpionSortTruck, NeoScorpionFuelTruck,
    NeoScorpionSettings)
from app.services import neoscorpion_vendors as vendors
from app.services import neoscorpion_inventory as inventory
from app.services.access_control import get_current_gateway
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change

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
    @login_required
    def wrapped(*args, **kwargs):
        account = vendors.vendor_account(current_user)
        if not account: abort(403)
        return view(account, *args, **kwargs)
    return wrapped


def operational(account):
    try:
        operation = vendors.current_operation(account.gateway, request.form.get('operation_id') if request.method == 'POST' else None)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    vendors.require_approved(account, operation)
    return operation


@bp.errorhandler(PermissionError)
def denied(error):
    if request.form.get('dispatch_assets_workspace') == '1':
        return jsonify(ok=False,error=str(error)),403
    return str(error), 403


def workspace_result(gateway):
    """Read the existing revision after commit; never create a parallel state."""
    from app.services.neoscorpion import fuel_assignments_live_revision
    return jsonify(ok=True,changed=True,**fuel_assignments_live_revision(gateway))


@bp.route('/invitation/<token>', methods=['GET','POST'])
def invitation(token):
    error = None
    if request.method == 'POST':
        try:
            vendors.accept_invitation(token,request.form.get('password',''),request.form.get('confirm_password',''))
            db.session.commit()
            return redirect(url_for('auth.login'))
        except (ValueError, IntegrityError) as exc:
            db.session.rollback(); error = str(exc) if isinstance(exc,ValueError) else 'Invitation already used.'
    return render_template('neonodes/neoscorpion/vendor_invitation.html', error=error), 400 if error else 200


@bp.get('/account')
@vendor_only
def account(account):
    from app.services.neoscorpion import current_sort_operation
    operation = current_sort_operation(account.gateway)
    night = db.session.get(NeoScorpionVendorNight,(operation.id,account.user_id)) if operation else None
    return render_template('neonodes/neoscorpion/vendor_account.html', account=account, operation=operation, night=night)


def my_truck_context(account):
    from app.services.neoscorpion_drivers import driver_context
    operation = operational(account)
    context = driver_context(account.gateway,current_user)
    context.update(account=account, operation=operation,
        available_trucks=NeoScorpionFuelTruck.query.filter_by(gateway_id=account.gateway_id,is_active=True,is_out_of_service=False).order_by(NeoScorpionFuelTruck.truck_number).all(),
        driver_requests=NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,user_id=account.user_id,status='pending').all())
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
                vendors.disconnect(account.gateway,operation,current_user,user_id=current_user.id)
                vendors.audit(account.gateway,current_user,'driver_disconnected',operation=operation,subject=current_user)
                record_nightly_operational_change(state,operation.id)
            else:
                vendors.request_claim(account,operation,request.form.get('truck_id'),request.form.get('gallons'))
            db.session.commit()
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback(); flash(str(exc) if isinstance(exc,ValueError) else 'Request changed. Refresh.','error')
        return redirect(url_for('scorpion_vendor.my_truck'))
    return render_template('neonodes/neoscorpion/vendor_truck.html',**my_truck_context(account))


@bp.get('/live')
@vendor_only
def live(account):
    operational(account)
    from app.neonodes.neoscorpion.routes import _dispatch_presentation_context
    return render_template('neonodes/neoscorpion/vendor_live.html',account=account,
        **_dispatch_presentation_context(account.gateway))


@bp.get('/revision')
@vendor_only
def revision(account):
    operational(account)
    from app.services.neoscorpion import fuel_assignments_live_revision
    response=jsonify(fuel_assignments_live_revision(account.gateway));response.headers['Cache-Control']='no-store'
    return response


@bp.post('/fuel-reading')
@login_required
def fuel_reading():
    account=vendors.vendor_account(current_user)
    gateway=account.gateway if account else get_current_gateway()
    try:
        operation=vendors.current_operation(gateway,request.form.get('operation_id'))
        inventory.submit_verification(gateway,operation,current_user,request.form.get('truck_id'),request.form.get('gallons'))
        db.session.commit();flash('Fuel reading recorded. High discrepancies await Dispatch approval.','success')
    except (ValueError,TypeError,IntegrityError) as exc:
        db.session.rollback();flash(str(exc) if isinstance(exc,ValueError) else 'Reading changed. Refresh.','error')
    return redirect(url_for('scorpion_vendor.my_truck' if account else 'neoscorpion.fueler'))


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
        try:
            action=request.form.get('action')
            if action not in {'invite','resend','disable','enable'}:
                operation=vendors.current_operation(gateway,request.form.get('operation_id'))
            if action in {'invite','resend'}:
                from app.services.email_service import send_vendor_invitation
                user,token=(vendors.invite(gateway,current_user,request.form.get('email'),request.form.get('name')) if action=='invite'
                    else vendors.renew_invitation(gateway,current_user,request.form.get('user_id')))
                delivery=send_vendor_invitation(user,token)
                if not delivery['sent']:
                    raise ValueError('Invitation email could not be sent. Account was not created; check mail delivery.')
            elif action in {'approved','denied','revoked'}:
                vendors.set_participation(gateway,operation,current_user,request.form.get('user_id'),action,request.form.get('version'))
            elif action in {'claim_approve','claim_deny'}:
                vendors.decide_claim(gateway,operation,current_user,request.form.get('request_id'),action=='claim_approve',
                    request.form.get('version'),override=request.form.get('override')=='1')
            elif action in {'disable','enable'}:
                state=None
                if operation:
                    operation,state=lock_nightly_asset_scope_for_mutation(operation)
                profile=NeoScorpionVendorAccount.query.filter_by(user_id=int(request.form['user_id']),gateway_id=gateway.id).with_for_update().one()
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
                truck=NeoScorpionFuelTruck.query.filter_by(id=int(request.form['truck_id']),gateway_id=gateway.id).one()
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
            if operation and action in {'invite','resend'}:
                operation,state=lock_nightly_asset_scope_for_mutation(operation)
                record_nightly_operational_change(state,operation.id)
            db.session.commit()
        except (ValueError,TypeError,IntegrityError) as exc:
            db.session.rollback()
            message=str(exc) if isinstance(exc,ValueError) else 'Driver state changed. Refresh.'
            if request.form.get('dispatch_assets_workspace')=='1':
                return jsonify(ok=False,error=message),400
            flash(message,'error')
        if request.form.get('dispatch_assets_workspace')=='1':
            return workspace_result(gateway)
        return redirect(url_for('scorpion_vendor.manage'))
    return render_template('neonodes/neoscorpion/vendor_manage.html',gateway=gateway,operation=operation,
        **driver_management_context(gateway,operation))


def driver_management_context(gateway,operation):
    """Existing gateway-scoped administration data, shared by Dispatch and fallback."""
    profiles=NeoScorpionVendorAccount.query.filter_by(gateway_id=gateway.id).all()
    nights={r.user_id:r for r in NeoScorpionVendorNight.query.filter_by(sort_date_operation_id=operation.id).all()} if operation else {}
    connections=NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id).all() if operation else []
    from app.services.neoscorpion_drivers import driver_roster
    employee_choices=driver_roster(gateway,operation,include_choices=True)['eligible_drivers']
    approved_vendors=[p.user for p in profiles if p.enabled and p.user.is_active and p.user.email_verified_at and nights.get(p.user_id) and nights[p.user_id].status=='approved']
    return dict(profiles=profiles,nights=nights,driver_choices=employee_choices+approved_vendors,
        connections={r.fuel_truck_id:r for r in connections}, trucks_by_driver={r.driver_user_id:r.fuel_truck_id for r in connections if r.driver_user_id},
        pending=NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,kind='claim',status='pending').all() if operation else [],
        driver_trucks=NeoScorpionFuelTruck.query.join(NeoScorpionSortTruck,NeoScorpionSortTruck.fuel_truck_id==NeoScorpionFuelTruck.id).filter(NeoScorpionSortTruck.sort_date_operation_id==operation.id).all() if operation else [],
        audits=NeoScorpionVendorAudit.query.filter_by(gateway_id=gateway.id).order_by(NeoScorpionVendorAudit.id.desc()).limit(100).all())
