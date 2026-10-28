"""Authorized current-sort transports for participation and canonical DUCC teams."""
from flask import request,flash,jsonify,redirect,url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import (SortDateOperation,SortDateMission,NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState,NeoSubZeroSprayRecord,NeoSubZeroUccAssignment,NeoSubZeroUccTruckAssignment,StaffingPerson)
from app.neonodes.neosubzero import bp
from app.neonodes.neosubzero import routes as legacy
from app.services.permission_rules import user_can
from app.services.live_collaboration import version_conflict
from app.services.neosubzero_evidence import (NeoSubZeroEvidenceError,normalized_tail,
    departure_event_for_tail,evidence_values,audit_change)
from app.services.neosubzero_participation import change_participation


def can_manage_operations():
    return user_can('neosubzero.coordinator.edit') or user_can('neosubzero.outbound.edit')


def locked_operation(gateway):
    current=legacy.current_neosubzero_operation(gateway)
    if current is None or request.form.get('operation_id',type=int)!=current.id:
        raise NeoSubZeroEvidenceError('The current sort changed. Your draft is kept; reload current operations.')
    return SortDateOperation.query.filter_by(id=current.id,gateway_code=gateway.code).with_for_update().one()


def locked_mission(operation):
    mission=SortDateMission.query.filter_by(id=request.form.get('mission_id',type=int),sort_date_operation_id=operation.id,mission_type='departure').populate_existing().with_for_update().one_or_none()
    if mission is None or not normalized_tail(request.form.get('physical_tail')) or normalized_tail(request.form.get('physical_tail'))!=normalized_tail(mission.assigned_tail_number):
        raise NeoSubZeroEvidenceError('Aircraft identity changed. Your draft is kept; reload the effective tail.')
    return mission


def require_version(entity, token, label):
    expected=str(token or '').strip()
    if (entity and (not expected or version_conflict(entity,expected))) or (entity is None and expected):
        raise NeoSubZeroEvidenceError(label+' changed. Your draft is kept; reload current values.')


def _result(message, *, error=False):
    if request.path.endswith('/operations/mutate'):
        flash(message,'error' if error else 'success')
        return redirect(url_for('neosubzero.operations'))
    if request.is_json or request.headers.get('X-Requested-With')=='XMLHttpRequest' or 'application/json' in request.headers.get('Accept',''):
        return jsonify(ok=not error,**({'error':message} if error else {'message':message})),409 if error else 200
    flash(message,'error' if error else 'success')
    if request.form.get('return_to')=='log': return redirect(url_for('neosubzero.deice_log'))
    return redirect(url_for('neosubzero.operations',mode=request.form.get('mode','outbound'),mission=request.form.get('mission_id')))


@bp.route('/frost-inspection',methods=['POST'])
@gateway_node_required('subzero')
def frost_inspection_mutate():
    if not can_manage_operations() or request.form.get('board')=='deicer': return 'Access denied.',403
    from app.services.frost_inspections import record
    try:
        operation=locked_operation(legacy.get_current_gateway())
        mission=locked_mission(operation)
        record(operation,mission,request.form,actor_id=current_user.id)
        db.session.commit()
        return _result('INSPECTION SAVED. Physical evidence recorded; treatment, FIN and CLEAR unchanged.')
    except (ValueError,IntegrityError) as exc:
        db.session.rollback()
        return _result(str(exc) if isinstance(exc,ValueError) else 'Inspection conflicted. Keep your draft and reload current values.',error=True)


@bp.route('/participation',methods=['POST'])
@gateway_node_required('subzero')
def participation_mutate():
    if not (can_manage_operations() or user_can('neosubzero.pretreat.edit')) or request.form.get('board')=='deicer': return 'Access denied.',403
    try:
        gateway=legacy.get_current_gateway();operation=locked_operation(gateway)
        record_id=request.form.get('record_id',type=int)
        record=NeoSubZeroSprayRecord.query.filter_by(id=record_id,sort_date_operation_id=operation.id).with_for_update().one_or_none() if record_id else None
        historical=request.form.get('historical_record')=='yes'
        if historical:
            if record is None or not record.ended_at_utc: raise NeoSubZeroEvidenceError('Choose an existing recorded physical application.')
            parent=record.pretreat_state or record.departure_deice_event
            if normalized_tail(request.form.get('physical_tail'))!=normalized_tail(record.tail_number_snapshot or parent.tail_number):
                raise NeoSubZeroEvidenceError('Historical physical-tail identity changed. Reload the record.')
            mission=db.session.get(SortDateMission,record.sort_date_mission_id)
            kind='pretreat' if record.pretreat_state_id else 'departure'
        else:
            mission=locked_mission(operation)
            kind=request.form.get('kind','departure')
            if kind=='pretreat':
                if not user_can('neosubzero.pretreat.edit'): return 'Access denied.',403
                parent=NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id,tail_number=normalized_tail(mission.assigned_tail_number)).with_for_update().one_or_none()
            elif kind=='departure':
                if not can_manage_operations(): return 'Access denied.',403
                parent_id=request.form.get('event_id',type=int)
                parent=NeoSubZeroDepartureDeiceEvent.query.filter_by(id=parent_id,sort_date_operation_id=operation.id,tail_number=normalized_tail(mission.assigned_tail_number)).with_for_update().one_or_none() if parent_id else departure_event_for_tail(operation,mission,lock=True)
            else: raise NeoSubZeroEvidenceError('Choose Pretreat or departure participation.')
        if kind=='departure' and not can_manage_operations(): return 'Access denied.',403
        if parent is None: raise NeoSubZeroEvidenceError('Save the treatment plan before selecting participants.')
        require_version(parent,request.form.get('expected_version'),'Treatment participation')
        change_participation(operation,mission,parent,kind,request.form.get('action'),request.form,
            legacy.neosubzero_fluid_settings(gateway),actor_id=current_user.id)
        db.session.commit();return _result('PARTICIPATION SAVED. Applied fuel evidence and FIN/CLEAR were preserved.')
    except (ValueError,IntegrityError) as exc:
        db.session.rollback();return _result(str(exc) if isinstance(exc,ValueError) else 'Participation conflicted. Reload current values; your draft is kept.',error=True)


@bp.route('/team',methods=['POST'])
@gateway_node_required('subzero')
def team_mutate():
    if not can_manage_operations() or request.form.get('board')=='deicer': return 'Access denied.',403
    from app.services.neosubzero_ucc import (ucc_staffing_version,move_neosubzero_ucc_assignment,validate_ucc_truck_unique)
    from app.services.neosubzero_spray import set_neosubzero_ucc_truck
    try:
        operation=locked_operation(legacy.get_current_gateway())
        # The aircraft is context only; staffing remains shared DUCC truth.
        if request.form.get('mission_id'): locked_mission(operation)
        if request.form.get('staffing_version')!=ucc_staffing_version(operation):
            raise NeoSubZeroEvidenceError('DUCC staffing changed. Your draft is kept; reload the current team before saving.')
        ramp=str(request.form.get('ramp') or '').strip().title();slot=request.form.get('position_number',type=int)
        truck=NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=operation.id,ramp=ramp,position_number=slot).with_for_update().one_or_none()
        if truck is None or not truck.truck_number: raise NeoSubZeroEvidenceError('Choose an existing DUCC truck/team position.')
        all_people=NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=operation.id).with_for_update().all()
        people=[p for p in all_people if p.ramp==ramp and p.position_number==slot]
        before=dict(truck=evidence_values(truck),people=[evidence_values(p) for p in all_people])
        action=request.form.get('action')
        if action=='change_truck':
            number=str(request.form.get('truck_number') or '').strip()
            if not number: raise NeoSubZeroEvidenceError('Enter the replacement truck number.')
            validate_ucc_truck_unique(operation,number,truck)
            set_neosubzero_ucc_truck(operation,ramp,slot,number,user_id=current_user.id,assignment=truck)
        elif action=='change_deicers':
            selected={role:request.form.get(role+'_person_id',type=int) for role in ('driver','flyer')}
            if selected['driver'] and selected['driver']==selected['flyer']: raise NeoSubZeroEvidenceError('Driver and Flyer must be different people.')
            for role in ('driver','flyer'):
                person=db.session.get(StaffingPerson,selected[role]) if selected[role] else None
                if selected[role] and person is None: raise NeoSubZeroEvidenceError('Choose an existing qualified '+role+'.')
                current=next((p for p in people if p.team_role==role),None)
                move_neosubzero_ucc_assignment(operation,ramp,slot,role,person,
                    resolution='swap' if request.form.get('swap_occupied')=='yes' else 'remove',user_id=current_user.id,destination_assignment=current)
        else: raise NeoSubZeroEvidenceError('Choose CHANGE DEICERS or CHANGE TRUCK.')
        audit_change(operation,action,actor_id=current_user.id,reason=request.form.get('correction_reason'),before=before,
            after=dict(truck=evidence_values(truck),people=[evidence_values(p) for p in NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=operation.id).all()]))
        db.session.commit();return _result('DUCC TEAM SAVED. Existing aircraft spray and historical snapshots were not changed.')
    except (ValueError,IntegrityError) as exc:
        db.session.rollback();return _result(str(exc) if isinstance(exc,ValueError) else 'Shared team changed. Reload current DUCC values.',error=True)
