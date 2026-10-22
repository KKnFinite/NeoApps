"""Section-scoped settings baselines, serialized by the existing gateway row.

No schema/version writes on GET, and no automatic rebasing of stale forms.
HTTP mutations must validate a signed baseline inside their write transaction.
"""
from decimal import Decimal
import hashlib
import json
from flask import current_app, request, get_flashed_messages
from itsdangerous import BadSignature, URLSafeSerializer
from app.extensions import db
from app.models import Gateway, NeoScorpionSettings, NeoScorpionAircraftFuelSetting

SCOPES = ('save_settings', 'save_assignment_planning_settings', 'save_apu_rates', 'spear')
MAIN_FIELDS = ('fuel_density_lbs_per_gallon', 'red_assignment_alert_threshold_minutes',
    'ready_assignment_pulse_enabled', 'mission_row_height_percent', 'neo_fuel_excess_alert_gallons',
    'truck_fuel_discrepancy_threshold_gallons', 'planning_inbound_fuel_fallback_lbs',
    'fob_difference_threshold_lbs', 'tf_vs_estimated_threshold_lbs')
PLANNING_FIELDS = ('assignment_setup_minutes', 'advance_setup_window_minutes',
    'assignment_finishing_minutes', 'assignment_eta_safety_buffer_minutes')


class SettingsConflict(ValueError):
    pass


def _normal(value):
    if isinstance(value, (Decimal, float, int)) and not isinstance(value, bool):
        return str(Decimal(str(value)).normalize())
    return value


def _snapshot(gateway, scope):
    if scope not in SCOPES:
        raise SettingsConflict('Unknown Settings section. Reload Settings before saving.')
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).populate_existing().first()
    fields = (MAIN_FIELDS if scope == 'save_settings' else PLANNING_FIELDS
        if scope == 'save_assignment_planning_settings' else
        tuple(c.name for c in NeoScorpionSettings.__table__.columns if c.name.startswith('spear_'))
        if scope == 'spear' else ())
    values = {}
    for name in fields:
        column = NeoScorpionSettings.__table__.columns[name]
        default = column.default.arg if column.default is not None and not column.default.is_callable else None
        values[name] = _normal(getattr(settings, name) if settings else default)
    if scope in ('save_apu_rates', 'save_assignment_planning_settings'):
        from app.services.neoscorpion import NEOSCORPION_APU_AIRCRAFT_TYPES, NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES, DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
        rows = {r.aircraft_type:r for r in NeoScorpionAircraftFuelSetting.query.filter_by(
            gateway_id=gateway.id).populate_existing().all()}
        types = NEOSCORPION_APU_AIRCRAFT_TYPES if scope == 'save_apu_rates' else NEOSCORPION_ASSIGNMENT_PLANNING_AIRCRAFT_TYPES
        attrs = ('apu_rate_thousand_lbs_per_hour',) if scope == 'save_apu_rates' else (
            'assignment_pump_rate_gallons_per_minute', 'max_lateral_imbalance_lbs')
        for aircraft_type in types:
            for attr in attrs:
                default = DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR if scope == 'save_apu_rates' else None
                values[aircraft_type+':'+attr] = _normal(getattr(rows[aircraft_type], attr) if aircraft_type in rows else default)
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _signer():
    return URLSafeSerializer(current_app.secret_key, salt='neoscorpion-admin-settings-v1')


def settings_baseline(gateway, scope):
    return _signer().dumps({'gateway':gateway.id, 'scope':scope, 'snapshot':_snapshot(gateway, scope)})


def guard_settings(gateway, scope, form):
    # Stable lock even before a gateway has its first Settings/aircraft row.
    # Every Settings route holds it through validation, service flush and commit.
    # PostgreSQL NO KEY UPDATE serializes administrators without conflicting
    # with the KEY SHARE locks used by unrelated operational FK inserts.
    db.session.query(Gateway).filter_by(id=gateway.id).with_for_update(key_share=True).one()
    try:
        baseline = _signer().loads(form.get('settings_baseline', ''))
    except BadSignature as exc:
        raise SettingsConflict('This Settings form has no valid baseline. Nothing was saved. Open current values before saving again.') from exc
    if baseline != {'gateway':gateway.id, 'scope':scope, 'snapshot':_snapshot(gateway, scope)}:
        raise SettingsConflict('This Settings section changed since you opened it. Nothing was saved. Your unsaved entries are retained; open current values to compare and resolve the conflict.')


def admin_form_context(gateway, scopes, status_code, *, can_edit):
    # Generate tokens BEFORE rendering values: a concurrent edit after this
    # snapshot can cause a safe rejection, never a new token on stale values.
    baselines = {scope:settings_baseline(gateway, scope) for scope in scopes}
    draft = None
    if can_edit and request.method == 'POST' and status_code in (400, 409):
        scope = ('spear' if request.endpoint == 'neoscorpion.spear_settings' else
            ('truck-'+str(request.form.get('truck_id') or 'new') if request.endpoint == 'neoscorpion.truck_manager'
             else request.form.get('action') or 'save_settings'))
        draft = {'scope':scope, 'values':{k:v for k,v in request.form.items() if k != 'csrf_token'}}
        if scope in baselines:
            baselines[scope] = request.form.get('settings_baseline', '')
    errors = get_flashed_messages(category_filter=['error']) if draft else []
    return {'settings_baselines':baselines, 'admin_form_draft':draft,
            'admin_form_error':errors[-1] if errors else 'Nothing was saved. Your unsaved entries are retained below.'}


def truck_integrity_message(exc):
    constraint = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
    if constraint == 'uq_neoscorpion_fuel_truck_gateway_number' or (
        'UNIQUE constraint failed: neoscorpion_fuel_trucks.gateway_id, neoscorpion_fuel_trucks.truck_number' in str(exc.orig)):
        return 'A truck with this number already exists at this gateway. Use its existing record or enter a different truck number.'
    return 'The truck could not be saved because its records changed or failed validation. Nothing was saved; reload current values and try again.'
