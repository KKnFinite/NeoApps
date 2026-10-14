"""Current-sort Rain controls delegating to canonical MotherBrain services."""
from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, SortDateTailState
from app.neonodes.neorain import bp
from app.neonodes.neorain.services import current_neorain_outbound_operation
from app.services.access_control import get_current_gateway
from app.services.live_collaboration import entity_version, version_conflict
from app.services.mission_identity import reject_duplicate_flight
from app.services.permission_rules import user_can
from app.services.parking_plan import (ParkingPlanError, assign_tail_to_lane,
    set_tail_hot, mark_arrival_tail_spare, clear_tail_spare, create_standalone_spare)


def scope(board, *, write=False):
    if board not in ('inbound', 'outbound'):
        abort(404)
    if not user_can(f'neorain.{board}.edit' if write else f'neorain.{board}.view'):
        abort(403)
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if not operation or operation.gateway_id != gateway.id:
        abort(409, 'No current operational sort.')
    if operation.archived_at_utc or operation.locked_at_utc:
        abort(409, 'The operational sort is not editable.')
    if write:
        if request.form.get('operation_id', type=int) != operation.id:
            abort(409, 'The operational sort changed. Reload before editing.')
        operation = SortDateOperation.query.filter_by(id=operation.id).populate_existing().with_for_update().one()
        if operation.archived_at_utc or operation.locked_at_utc:
            abort(409, 'The operational sort is not editable.')
    return operation


@bp.route('/<board>/missions/new', methods=['GET', 'POST'])
@gateway_node_required('rain')
def mission_new(board):
    return mission_form(board)


@bp.route('/<board>/missions/<int:mission_id>/edit', methods=['GET', 'POST'])
@gateway_node_required('rain')
def mission_edit(board, mission_id):
    return mission_form(board, mission_id)


def mission_form(board, mission_id=None):
    from app.neomotherbrain.routes import (_mission_form_from_request,
        _mission_form_from_model, _apply_mission_form, _render_mission_form,
        _sync_tail_state_and_crew_slots, _aircraft_type_for_tail)
    operation = scope(board, write=request.method == 'POST')
    direction = 'arrival' if board == 'inbound' else 'departure'
    mission = None
    if mission_id is not None:
        query = SortDateMission.query.filter_by(id=mission_id,
            sort_date_operation_id=operation.id, mission_type=direction,
            mission_source='manual', master_flight_schedule_id=None)
        mission = query.populate_existing().with_for_update().one_or_none() if request.method == 'POST' else query.one_or_none()
        if mission is None:
            abort(404)
    form = _mission_form_from_model(mission) if mission and request.method == 'GET' else _mission_form_from_request(operation)
    form['mission_type'] = direction
    if request.method == 'GET' and not mission:
        form['destination' if direction == 'arrival' else 'origin'] = operation.gateway_code
    if request.method == 'POST':
        if mission and (not request.form.get('expected_version') or version_conflict(mission, request.form.get('expected_version'))):
            db.session.rollback()
            return 'Mission changed. Reload before editing.', 409
        old_tail = mission.assigned_tail_number if mission else None
        old_type = _aircraft_type_for_tail(operation, old_tail) if old_tail else 'unknown'
        candidate = mission or SortDateMission(sort_date_operation=operation)
        try:
            _apply_mission_form(candidate, operation, form)
            reject_duplicate_flight(operation, candidate)
            db.session.add(candidate)
            db.session.flush()
            _sync_tail_state_and_crew_slots(candidate, old_tail_number=old_tail, old_aircraft_type=old_type)
            parking = request.form.get('parking_position', '').strip().upper()
            if parking:
                if not candidate.assigned_tail_number:
                    raise ValueError('Choose a tail before assigning parking.')
                assign_tail_to_lane(operation, candidate.assigned_tail_number,
                    parking[0], parking, 1, user=current_user)
            db.session.commit()
        except (ValueError, ParkingPlanError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc) if not isinstance(exc, IntegrityError) else 'Flight or tail identity conflict. Reload and try again.', 'error')
            return _render_mission_form(operation, form, 'edit' if mission else 'new', mission), 400
        flash('One-off flight saved.', 'success')
        return redirect(url_for(f'neorain.{board}'))
    return _render_mission_form(operation, form, 'edit' if mission else 'new', mission)


def tail_tools_context(operation):
    if operation is None:
        return []
    return [{'tail': row, 'version': entity_version(row)} for row in
        SortDateTailState.query.filter_by(sort_date=operation.sort_date,
            gateway_code=operation.gateway_code, sort_name=operation.sort_name).order_by(SortDateTailState.tail_number).all()]


@bp.route('/outbound/tails', methods=['POST'])
@gateway_node_required('rain')
def tail_action():
    operation = scope('outbound', write=True)
    action, tail_number = request.form.get('action'), request.form.get('tail_number', '').strip().upper()
    try:
        if action == 'add_spare':
            existing = SortDateTailState.query.filter_by(sort_date=operation.sort_date,
                gateway_code=operation.gateway_code, sort_name=operation.sort_name, tail_number=tail_number).first()
            if existing:
                raise ValueError('This tail already exists. Use its current tail controls.')
            parking = request.form.get('parking_position', '').strip().upper()
            create_standalone_spare(operation, tail_number, request.form.get('aircraft_type'),
                ramp_code=parking[0] if parking else None, position_code=parking or None, user=current_user)
        else:
            row = SortDateTailState.query.filter_by(sort_date=operation.sort_date,
                gateway_code=operation.gateway_code, sort_name=operation.sort_name,
                tail_number=tail_number).populate_existing().with_for_update().one_or_none()
            if not row or not request.form.get('expected_version') or version_conflict(row, request.form.get('expected_version')):
                raise ValueError('Tail changed. Reload before editing.')
            if action in ('hot', 'clear_hot'):
                set_tail_hot(operation, tail_number, action == 'hot', user=current_user)
            elif action == 'spare':
                mark_arrival_tail_spare(operation, tail_number, user=current_user)
            elif action == 'clear_spare':
                clear_tail_spare(operation, tail_number, user=current_user)
            else:
                raise ValueError('Choose a valid tail action.')
        db.session.commit()
        flash('Tail state saved.', 'success')
    except (ValueError, ParkingPlanError, IntegrityError) as exc:
        db.session.rollback()
        return 'Tail update rejected. ' + str(exc if not isinstance(exc, IntegrityError) else 'Identity conflict.'), 409
    return redirect(url_for('neorain.outbound'))
