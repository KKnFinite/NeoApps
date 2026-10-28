"""Unified presentation, delegating every operational mutation to existing handlers."""
import hashlib
import json
from flask import jsonify, make_response, render_template, request, session, url_for
from flask_login import current_user
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState
from app.neonodes.neosubzero import bp
from app.neonodes.neosubzero import routes as legacy
from app.services.neosubzero_preferences import neosubzero_operations_preferences, set_neosubzero_operations_preferences
from app.services.permission_rules import user_can
from app.services.live_collaboration import version_conflict


def available_modes():
    return [mode for mode, permissions in (
        ('pretreat', ('neosubzero.pretreat.view',)),
        ('outbound', ('neosubzero.outbound.view', 'neosubzero.coordinator.view')),
    ) if any(user_can(permission) for permission in permissions)]


def operations_revision(gateway, operation, preferences):
    return hashlib.sha256(json.dumps([
        getattr(operation, 'id', None), preferences,
        legacy.pretreat_revision(gateway, operation),
        legacy.departure_deice_revision(gateway, operation),
    ], sort_keys=True).encode()).hexdigest()


def render_operations(forced_mode=None):
    from app.services.neosubzero_operations import operations_context
    modes = available_modes()
    if not modes:
        return 'Access denied.', 403
    gateway = legacy.get_current_gateway()
    operation = legacy.current_neosubzero_operation(gateway)
    preferences = neosubzero_operations_preferences(current_user, operation)
    mode = forced_mode or request.args.get('mode') or preferences['mode']
    if mode not in modes:
        if forced_mode or request.args.get('mode'):
            return 'Access denied.', 403
        mode = modes[0]
    board = 'coordinator' if user_can('neosubzero.coordinator.edit') else 'outbound'
    can_edit = user_can('neosubzero.pretreat.edit') if mode == 'pretreat' else user_can('neosubzero.' + board + '.edit')
    context = operations_context(gateway, operation, mode)
    from app.services.neosubzero_ucc import ucc_staffing_version
    from app.services.neosubzero_staffing import current_subzero_staffing_pool
    selected = next((row for row in context['rows'] if row['mission_id'] == request.args.get('mission', type=int)), None)
    session[legacy.LAST_PAGE_KEY] = 'neosubzero.operations'
    return render_template('neonodes/neosubzero/operations.html', gateway=gateway,
        mode=mode, modes=modes, preferences=preferences, board=board, can_edit=can_edit,
        can_manage_staff=user_can('neosubzero.ucc.view'), selected=selected,
        staffing_version=ucc_staffing_version(operation) if operation else '',
        staffing_pool=current_subzero_staffing_pool(operation) if operation else (),
        plan_labels=legacy.PLAN_LABELS, surface_labels=legacy.SURFACE_LABELS,
        gateway_timezone=legacy.gateway_timezone(gateway), application_context=legacy._application_context(operation),
        revision=operations_revision(gateway, operation, preferences),
        refresh_status=legacy.subzero_refresh_status(gateway, operation, legacy.PRETREAT_REFRESH_KEY if mode == 'pretreat' else legacy.OUTBOUND_REFRESH_KEY),
        **context)


@bp.route('/operations')
@gateway_node_required('subzero')
def operations():
    return render_operations()


@bp.route('/operations/revision')
@gateway_node_required('subzero')
def operations_revision_endpoint():
    if not available_modes():
        return jsonify(ok=False, error='Access denied.'), 403
    gateway = legacy.get_current_gateway()
    operation = legacy.current_neosubzero_operation(gateway)
    preferences = neosubzero_operations_preferences(current_user, operation)
    revision = operations_revision(gateway, operation, preferences)
    mode = request.args.get('mode') or preferences['mode']
    response = jsonify(ok=True, revision=revision, changed=revision != request.args.get('revision'),
        operation_id=getattr(operation, 'id', None), preferences=preferences,
        refresh=legacy.subzero_refresh_status(gateway, operation, legacy.PRETREAT_REFRESH_KEY if mode == 'pretreat' else legacy.OUTBOUND_REFRESH_KEY))
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/operations/preferences', methods=['POST'])
@gateway_node_required('subzero')
def operations_preferences_mutate():
    modes = available_modes()
    if not modes:
        return jsonify(ok=False, error='Access denied.'), 403
    operation = legacy.current_neosubzero_operation(legacy.get_current_gateway())
    values = request.get_json(silent=True) or {}
    if values.get('operation_id') != getattr(operation, 'id', None):
        return jsonify(ok=False, error='The current sort changed. Reload current operations before changing the ramp.'), 409
    changes = {key: values[key] for key in ('mode', 'layout', 'ramp') if key in values}
    if 'mode' in changes and changes['mode'] not in modes:
        return jsonify(ok=False, error='Access denied.'), 403
    try:
        set_neosubzero_operations_preferences(current_user, operation, changes)
        db.session.commit()
        return jsonify(ok=True, preferences=neosubzero_operations_preferences(current_user, operation))
    except ValueError as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 400


@bp.route('/operations/mutate', methods=['POST'])
@gateway_node_required('subzero')
def operations_mutate():
    """Scope/tail guard + JSON transport. Existing services still validate and commit."""
    if not available_modes():
        return jsonify(ok=False, error='Access denied.'), 403
    gateway = legacy.get_current_gateway()
    current = legacy.current_neosubzero_operation(gateway)
    mode = request.form.get('mode')
    target = request.form.get('target')
    if mode not in available_modes() or (target == url_for('neosubzero.pretreat_mutate') and mode != 'pretreat') or (target == url_for('neosubzero.departure_deice_mutate') and mode != 'outbound'):
        return jsonify(ok=False, error='Choose an authorized action for this operational mode.'), 403
    if current is None or request.form.get('operation_id', type=int) != current.id:
        return jsonify(ok=False, error='The current sort changed. Your draft was kept; open current operations.'), 409
    operation = SortDateOperation.query.filter_by(id=current.id, gateway_code=gateway.code).with_for_update().one()
    mission_id = request.form.get('mission_id', type=int)
    if mission_id:
        mission = SortDateMission.query.filter_by(id=mission_id, sort_date_operation_id=operation.id, mission_type='departure').with_for_update().one_or_none()
        expected_tail = str(request.form.get('physical_tail') or '').strip().upper()
        if mission is None or not expected_tail or expected_tail != str(mission.assigned_tail_number or '').strip().upper():
            db.session.rollback()
            return jsonify(ok=False, error='Aircraft identity changed. Your draft is preserved; review the physical tail before continuing.'), 409
        if target == url_for('neosubzero.pretreat_mutate'):
            state = NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id,tail_number=expected_tail).with_for_update().one_or_none()
            expected = str(request.form.get('expected_version') or '').strip()
            if (state and (not expected or version_conflict(state,expected))) or (state is None and expected):
                db.session.rollback()
                return jsonify(ok=False,error='Pretreat changed while you were editing. Your draft is kept; use RELOAD CURRENT VALUES to resolve the conflict.'),409
    handlers = {url_for('neosubzero.' + endpoint): getattr(legacy, endpoint) for endpoint in (
        'pretreat_mutate', 'departure_deice_mutate', 'truck_mutate', 'spray_gallons_mutate',
        'spray_reason_mutate', 'application_context_mutate')}
    from app.neonodes.neosubzero.release2_routes import participation_mutate,team_mutate,frost_inspection_mutate
    handlers.update({url_for('neosubzero.participation_mutate'):participation_mutate,url_for('neosubzero.team_mutate'):team_mutate})
    handlers[url_for('neosubzero.frost_inspection_mutate')]=frost_inspection_mutate
    handler = handlers.get(request.form.get('target'))
    if handler is None:
        db.session.rollback()
        return jsonify(ok=False, error='Choose an existing operational action.'), 400
    previous_messages = session.pop('_flashes', [])
    try:
        response = make_response(handler())
        messages = session.pop('_flashes', [])
    finally:
        if previous_messages:
            session['_flashes'] = previous_messages
    if response.status_code >= 400:
        db.session.rollback()
        return jsonify(ok=False, error=response.get_data(as_text=True)[:300]), response.status_code
    errors = [message for category, message in messages if category == 'error']
    if errors:
        return jsonify(ok=False, error=' '.join(errors)), 409
    return jsonify(ok=True, message=' '.join(message for _category, message in messages) or 'Saved.')
