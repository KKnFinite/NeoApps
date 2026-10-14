"""Neo commits first. A new edit may make one independently guarded Sheets call."""
from flask import jsonify, request
from flask_login import current_user
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateMission
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.services.live_collaboration import entity_version, version_conflict
from app.services.permission_rules import user_can
from app.services.neorain_overrides import FIELDS, edit_override, serialized_value, override_display

PERMISSIONS = {'sta': 'neorain.inbound.sta.edit', 'block_in': 'neorain.inbound.block_in.edit',
               'etd': 'neorain.outbound.etd.edit'}


def can_edit_field(field):
    return user_can(PERMISSIONS.get(field, 'neorain.outbound.milestones.edit'))


@bp.route('/override', methods=['POST'])
@gateway_node_required('rain')
def operational_override():
    return handle_override()


def handle_override(default_field=None):
    from app.neonodes.neorain import routes
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(ok=False, error='A JSON edit is required.'), 400
    if set(payload) - {'mission_id','field','value','expected_version','action','confirmed','included'}:
        return jsonify(ok=False, error='Unexpected edit fields.'), 400
    if not str(payload.get('expected_version') or '').strip():
        return jsonify(ok=False, error='Provide the current mission version.'), 400
    if type(payload.get('mission_id')) is not int or payload['mission_id'] <= 0:
        return jsonify(ok=False, error='Provide a valid mission identity.'), 400
    field = payload.get('field', default_field)
    if field not in FIELDS:
        return jsonify(ok=False, error='Choose a valid field.'), 400
    board = 'inbound' if FIELDS[field][0] == 'arrival' else 'outbound'
    if not user_can(f'neorain.{board}.view') or not can_edit_field(field):
        return jsonify(ok=False, error='Edit access denied.'), 403
    gateway = get_current_gateway()
    operation = routes.current_neorain_outbound_operation(gateway)
    if not operation or operation.gateway_id != gateway.id or operation.archived_at_utc or operation.locked_at_utc:
        return jsonify(ok=False, error='No editable current sort.'), 409
    mission = SortDateMission.query.filter_by(id=payload.get('mission_id'),
        sort_date_operation_id=operation.id, gateway_code=gateway.code,
        mission_type=FIELDS[field][0]).populate_existing().with_for_update().one_or_none()
    if not mission:
        return jsonify(ok=False, error='Mission is outside the current sort.'), 404
    conflict = version_conflict(mission, payload.get('expected_version'))
    if conflict:
        db.session.rollback()
        return jsonify(ok=False, code='stale_version', error=conflict['message']), 409
    action = payload.get('action', 'edit')
    if action not in ('edit', 'reset'):
        return jsonify(ok=False, error='Choose edit or reset.'), 400
    try:
        if mission.arrival_status == 'cancelled' or mission.departure_status == 'cancelled':
            raise ValueError('Cancelled missions must be restored through authorized mission management.')
        row, changed = edit_override(mission, operation, field, payload.get('value', payload.get('included')),
            current_user, reset=action == 'reset', confirmed=payload.get('confirmed') is True)
        row_id, generation = (row.id, row.generation) if row else (None, None)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify(ok=False, error=str(exc)), 400
    if changed and action == 'edit':
        from app.services.neorain_google_overrides import attempt_once
        attempt_once(row_id, generation)
    elif changed and action == 'reset':
        from app.services.neorain_google_overrides import refresh_reset
        refresh_reset(row_id, generation)
    db.session.refresh(mission)
    states = override_display(mission, field)
    formatted = routes.neorain_inbound_row(mission, operation) if board == 'inbound' else routes.neorain_outbound_row(mission, operation)
    return jsonify(ok=True, changed=changed, field=field, value=serialized_value(mission, field),
        version=entity_version(mission), row=formatted, **states)
