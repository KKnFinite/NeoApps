"""Neo-owned, sort-specific briefing inputs and canonical flight performance.

No Google reads or writes. Existing recap manual facts are reused; new narrative
fields live alongside them in the existing versioned report-entry table.
"""
from decimal import Decimal

from app.extensions import db
from app.models import NeoRainReportEntry, SortDateOperation
from app.services.mission_identity import flight_identity
from app.services.neorain_reports import _mission_rows, _report_gateway_id, normalize_report_value
from app.services.time_display import format_local_hhmm


INPUT_GROUPS = (
    ('Safety / Incident', (
        ('briefing_safety', 'Safety', 'note'),
        ('briefing_incident', 'Incident', 'note'),
    )),
    ('Volume Plan / Actual', (
        ('planned_volume', 'Volume plan', 'number'),
        ('actual_volume', 'Volume actual', 'number'),
    )),
    ('Ramp staffing — Actual / Planned', (
        ('briefing_ramp_staffing_actual', 'Ramp staffing actual', 'number'),
        ('briefing_ramp_staffing_planned', 'Ramp staffing planned', 'number'),
    )),
    ('Hub staffing — Actual / Planned', (
        ('briefing_hub_staffing_actual', 'Hub staffing actual', 'number'),
        ('briefing_hub_staffing_planned', 'Hub staffing planned', 'number'),
    )),
    ('Service issues', (
        ('briefing_inbound_missorts', 'Inbound missorts', 'number'),
        ('briefing_libs', 'LIBs', 'number'),
    )),
    ('Service / Operational notes', (
        ('briefing_service', 'Service failures and details', 'note'),
        ('briefing_operational_notes', 'Operational notes', 'note'),
    )),
)
INPUTS = {key: (label, kind) for _, fields in INPUT_GROUPS for key, label, kind in fields}


class BriefingConflict(ValueError):
    pass


def _normalize(key, raw):
    if not isinstance(raw, str):
        raise ValueError('Enter text or a whole-number count.')
    if INPUTS[key][1] == 'number':
        if len(raw) > 32:
            raise ValueError('Enter a whole-number count of at most 999,999,999.')
        # All briefing counts use the established count validation; field names
        # remain separate from unrelated Rainrock metrics.
        return normalize_report_value('planned_volume', raw)
    text = raw.replace('\r\n', '\n').replace('\r', '\n').strip()
    if '\x00' in text or len(text) > 2000:
        raise ValueError('Each note must contain at most 2,000 characters and no null characters.')
    return text or None


def save_inputs(operation, values, versions, user_id, *, current=False):
    """Validate every changed field before an atomic, version-checked update."""
    if not isinstance(values, dict) or not values or set(values) - INPUTS.keys():
        raise ValueError('Choose valid briefing inputs.')
    if not isinstance(versions, dict) or set(versions) != set(values):
        raise ValueError('Provide the saved version of each changed input.')
    normalized = {key: _normalize(key, raw) for key, raw in values.items()}
    if any(type(value) is not int or value < 0 for value in versions.values()):
        raise ValueError('Input versions must be nonnegative integers.')
    # Serialize first inserts as well as edits, without changing the sort itself.
    db.session.execute(db.select(SortDateOperation.id).where(
        SortDateOperation.id == operation.id).with_for_update()).all()
    rows = {row.field_name: row for row in NeoRainReportEntry.query.filter_by(
        sort_date_operation_id=operation.id, data_source='manual')
        .filter(NeoRainReportEntry.field_name.in_(values)).populate_existing().with_for_update().all()}
    for key in values:
        row = rows.get(key)
        if (row.version if row else 0) != versions[key]:
            raise BriefingConflict(f'{INPUTS[key][0]} changed. Reload the saved inputs before retrying.')
    gateway_id = _report_gateway_id(operation)
    if gateway_id is None:
        raise ValueError('This sort does not have a valid gateway.')
    for key, value in normalized.items():
        row = rows.get(key)
        value = value or ''  # Numeric zero is normalized to the nonempty string "0".
        if row is None:
            if not value:
                continue
            db.session.add(NeoRainReportEntry(gateway_id=gateway_id,
                sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                sort_name=operation.sort_name, field_name=key, data_source='manual',
                value=value, updated_by_user_id=user_id))
        elif row.value != value:
            row.value = value
            row.version += 1
            row.updated_by_user_id = user_id
    db.session.flush()


def _performance(missions, *, arrival):
    from app.neonodes.neorain.services import (
        _inbound_late_summary, _outbound_late_summary, neorain_late_metrics_inclusion,
    )
    eligible = [m for m in missions if neorain_late_metrics_inclusion(m)['included']
                and m.planned_datetime_utc is not None]
    summary = (_inbound_late_summary if arrival else _outbound_late_summary)(missions)['total']
    actual_attr = 'actual_block_in_datetime_utc' if arrival else 'actual_block_out_datetime_utc'
    recorded = [m for m in eligible if getattr(m, actual_attr) is not None]
    details = []
    for mission in eligible:
        actual = getattr(mission, actual_attr)
        minutes = int((actual - mission.planned_datetime_utc).total_seconds() / 60) if actual else None
        if minutes is None or minutes <= 0:
            continue
        details.append(dict(mission_id=mission.id, flight=flight_identity(mission.flight_number),
            origin=mission.origin, destination=mission.destination, tail=mission.assigned_tail_number,
            actual=format_local_hhmm(actual, mission.timezone), late_minutes=max(0, minutes) if minutes is not None else None,
            delays=[dict(code=row.code, minutes=row.minutes, notes=row.notes or '')
                    for row in sorted(mission.delay_info_rows, key=lambda row: row.id)]))
    return dict(eligible=len(eligible) if missions else None, recorded=len(recorded),
        late_count=summary['aircraft_late'] if recorded else None,
        late_minutes=summary['late_minutes'] if recorded else None,
        average=summary['average'] if recorded else None,
        incomplete=len(recorded) < len(eligible), details=details)


def briefing_context(operation, *, current=False):
    entries = NeoRainReportEntry.query.filter_by(sort_date_operation_id=operation.id).all()
    rows = {row.field_name: row for row in entries if row.data_source == 'manual'}
    # Reuse only already-saved, date-verified Rainrock volume snapshots. This
    # does not read Google or share either report's independent manual inputs.
    volume = {row.field_name: row for row in entries if row.data_source == 'google'
              and row.field_name in ('planned_volume', 'actual_volume')
              and row.source_report_date == operation.sort_date and row.value}
    fields = {}
    for key, (label, kind) in INPUTS.items():
        row = rows.get(key)
        fallback = volume.get(key)
        raw = row.value if row and row.value else (fallback.value if fallback else None)
        if raw is not None and kind == 'number':
            try:
                raw = normalize_report_value('planned_volume', raw)
            except ValueError:
                raw = None
        fields[key] = dict(label=label, kind=kind, version=row.version if row else 0,
            manual_value=row.value if row else '', editable=True, value=raw,
            source='Rainrock snapshot' if fallback and not (row and row.value) else 'NeoRain entry')
    inbound, outbound = _mission_rows(operation)
    return dict(operation=operation, fields=fields,
        inbound=_performance(inbound, arrival=True), outbound=_performance(outbound, arrival=False),
        legacy_notes=[rows[f'note_{n}'].value for n in range(1, 6)
                      if f'note_{n}' in rows and rows[f'note_{n}'].value])


def briefing_payload(context):
    return dict(briefing=format_briefing(context), fields=context['fields'])


def format_briefing(context):
    """The copy text is one plain-text projection, shared by render/save/refresh."""
    operation, fields = context['operation'], context['fields']
    def value(key):
        raw = fields[key]['value']
        if raw is None:
            return 'UNAVAILABLE'
        return f'{int(Decimal(raw)):,}' if fields[key]['kind'] == 'number' else raw

    def performance(label, data):
        count = data['late_count'] if data['late_count'] is not None else 'UNAVAILABLE'
        total = data['eligible'] if data['eligible'] is not None else 'UNAVAILABLE'
        average = data['average'] if data['average'] is not None else 'UNAVAILABLE'
        result = [label, f"{count} out of {total} late for {average}-minutes avg"]
        if data['incomplete']:
            result.append(f"IN PROGRESS — actual timings recorded for {data['recorded']} of {data['eligible']} eligible flights")
        for row in data['details']:
            codes = '/'.join(dict.fromkeys(delay['code'] for delay in row['delays'] if delay['code']))
            line = f"{row['origin'] or 'UNAVAILABLE'}-{row['destination'] or 'UNAVAILABLE'} {row['flight'] or 'UNAVAILABLE'} {row['tail'] or 'UNAVAILABLE'} +{row['late_minutes']}"
            result.append(line + (' ' + codes if codes else ''))
        return result

    counts = [fields[key]['value'] for key in ('briefing_inbound_missorts', 'briefing_libs')]
    issues = f'{sum(int(item) for item in counts):,}' if all(item is not None for item in counts) else 'UNAVAILABLE'
    notes = ([value('briefing_operational_notes')] if fields['briefing_operational_notes']['value'] is not None else []) + context['legacy_notes']
    notes = '\n'.join(notes) if notes else 'UNAVAILABLE'
    if not notes.casefold().startswith(operation.gateway_code.casefold() + ':'):
        notes = f'{operation.gateway_code}: {notes}'
    sections = [
        ['********************', operation.sort_date.strftime('%m/%d/%Y'),
         f'{operation.gateway_code} {operation.sort_name.title()} Update', '********************',
         f"Safety: {value('briefing_safety')}", f"Incident: {value('briefing_incident')}",
         f"Volume Plan: {value('planned_volume')}", f"Volume Actual: {value('actual_volume')}",
         f"Ramp Staffing: {value('briefing_ramp_staffing_actual')}/{value('briefing_ramp_staffing_planned')}",
         f"Hub Staffing: {value('briefing_hub_staffing_actual')}/{value('briefing_hub_staffing_planned')}"],
        performance('INBOUND PERFORMANCE', context['inbound']),
        [f'SERVICE ISSUES: {issues}', f"Inbound missorts: {value('briefing_inbound_missorts')}", f"LIB's: {value('briefing_libs')}"],
        performance('OUTBOUND PERFORMANCE', context['outbound']),
        ['SERVICE:', value('briefing_service')],
        [notes],
    ]
    return '\n\n'.join('\n'.join(section) for section in sections)
