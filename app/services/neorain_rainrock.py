"""Compact Rainrock projection over existing, versioned sort facts. No Google writes."""
from decimal import Decimal

from app.extensions import db
from app.models import Gateway, NeoRainOperationalSetting, NeoRainReportEntry
from app.services.neorain_daily_briefing import STAFFING_GROUPS, VOLUME_INPUTS, briefing_context, save_inputs
from app.services.neorain_reports import _mission_rows, report_context, normalize_report_value
from app.services.neorain_ground_time_settings import default_planned_fph


INPUT_GROUPS = (
    ('Volume & Performance', VOLUME_INPUTS),
    ('Staffing', tuple(field for _, group in STAFFING_GROUPS for field in group)),
    ('Additional Notes', (('note_1', 'Additional Notes', 'note'),)),
)
INPUTS = {key: (label, kind) for _, group in INPUT_GROUPS for key, label, kind in group}


def set_default_planned_fph(gateway, raw):
    value = normalize_report_value('planned_fph', raw)
    if value is None or int(value) <= 0:
        raise ValueError('Default Planned FPH must be a positive whole number.')
    db.session.execute(db.select(Gateway.id).where(Gateway.id == gateway.id).with_for_update()).all()
    setting = NeoRainOperationalSetting.query.filter_by(gateway_id=gateway.id).with_for_update().one_or_none()
    if setting is None:
        setting = NeoRainOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code)
        db.session.add(setting)
    setting.default_planned_fph = int(value)


def dashboard_context(operation, *, current=False):
    return report_projections(operation, current=current)[0]


def report_projections(operation, *, current=False):
    missions = _mission_rows(operation)
    report = report_context(operation, current=current, missions=missions)
    briefing = briefing_context(operation, current=current, missions=missions)
    shared = briefing['fields']
    # Only scheduled, included flights participate in the lateness denominator.
    # Still display every flight and its actual/delays in the tables below.
    for side in ('inbound', 'outbound'):
        report['recorded_' + side] = briefing[side]['recorded']
    manual = {row.field_name: row for row in NeoRainReportEntry.query.filter_by(
        sort_date_operation_id=operation.id, data_source='manual').all()}
    inputs = {}
    for key, (label, kind) in INPUTS.items():
        item = dict(shared.get(key) or report['fields'][key])
        # Actual FPH is an operator fact, never a Google/computed estimate.
        if key == 'actual_fph':
            row = manual.get(key)
            item.update(value=row.value if row and row.value else None, source='NeoRain entry' if row and row.value else 'Unavailable')
        raw = item.get('value')
        try:
            display = (f'{int(Decimal(raw)):,}' if kind == 'number' else raw + '%' if kind == 'percent' else raw) if raw is not None else '—'
        except (ValueError, ArithmeticError):
            raw = None; display = '—'
        item.update(label=label, kind=kind, value=raw, display=display, editable=True,
                    version=manual[key].version if key in manual else 0,
                    input_value=raw if raw is not None else '')
        if key == 'planned_fph' and key in manual and manual[key].updated_by_user_id is None:
            item['source'] = 'Saved sort value'
        inputs[key] = item
    report['inputs'] = inputs
    report['closeout'] = [shared[key] for key in (
        'briefing_safety', 'briefing_incident', 'briefing_inbound_missorts',
        'briefing_libs', 'briefing_service', 'briefing_operational_notes',
    ) if shared[key]['value'] is not None]
    report['notes'] = [inputs['note_1']['value']] if inputs['note_1']['value'] else []
    report['notes'] += [report['fields'][f'note_{n}']['value'] for n in range(2, 6) if report['fields'][f'note_{n}']['value']]
    return report, briefing


def save_dashboard_inputs(operation, values, versions, user_id):
    return save_inputs(operation, values, versions, user_id, specs=INPUTS)
