"""Compact Rainrock projection over existing, versioned sort facts. No Google writes."""
from decimal import Decimal

from app.extensions import db
from app.models import Gateway, NeoRainOperationalSetting, NeoRainReportEntry
from app.services.neorain_daily_briefing import briefing_context, save_inputs
from app.services.neorain_reports import report_context, normalize_report_value
from app.services.neorain_ground_time_settings import default_planned_fph


INPUT_GROUPS = (
    ('Volume & Performance', (
        ('planned_volume', 'HPS Volume', 'number'), ('actual_volume', 'Actual Volume', 'number'),
        ('smalls_percent', 'Smalls Processed', 'percent'), ('planned_fph', 'Planned FPH', 'number'),
        ('actual_fph', 'Actual FPH', 'number'))),
    ('Staffing', (
        ('briefing_ramp_staffing_planned', 'Ramp Payroll', 'number'),
        ('briefing_ramp_staffing_actual', 'Ramp Worked', 'number'),
        ('briefing_hub_staffing_planned', 'Hub Payroll', 'number'),
        ('briefing_hub_staffing_actual', 'Hub Worked', 'number'))),
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
    report = report_context(operation, current=current)
    briefing = briefing_context(operation, current=current)
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
    report['notes'] = [inputs['note_1']['value']] if inputs['note_1']['value'] else []
    report['notes'] += [report['fields'][f'note_{n}']['value'] for n in range(2, 6) if report['fields'][f'note_{n}']['value']]
    return report


def save_dashboard_inputs(operation, values, versions, user_id):
    return save_inputs(operation, values, versions, user_id, specs=INPUTS)
