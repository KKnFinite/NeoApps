"""Neo-owned, sort-specific briefing inputs and canonical flight performance.

No Google reads or writes. Existing recap manual facts are reused; new narrative
fields live alongside them in the existing versioned report-entry table.
"""
from decimal import Decimal

from app.extensions import db
from app.models import NeoRainReportEntry, SortDateOperation
from app.services.mission_identity import flight_identity
from app.services.neorain_reports import _mission_rows, _report_gateway_id, normalize_report_value
from app.services.google_rain_integration_mode import rain_operation_is_native
from app.services.neorain_performance import completed_actual, completed_variance_minutes, eligible_for_performance, mission_cancelled


STAFFING_GROUPS = tuple(
    (f"{label} staffing", (
        (f"{side}_planned_payroll", f"{label} Payroll Planned", "number"),
        (f"{side}_actual_payroll", f"{label} Payroll Actual", "number"),
        (f"briefing_{side}_staffing_planned", f"{label} Worked Planned", "number"),
        (f"briefing_{side}_staffing_actual", f"{label} Worked Actual", "number"),
    )) for side, label in (("ramp", "Ramp"), ("hub", "Hub"))
)
VOLUME_INPUTS = (
    ("planned_volume", "HPS Volume", "number"), ("actual_volume", "Actual Volume", "number"),
    ("smalls_percent", "Smalls Processed", "percent"), ("planned_fph", "Planned FPH", "number"),
    ("actual_fph", "Actual FPH", "number"),
)

INPUT_GROUPS = (
    ('Safety / Incident', (
        ('briefing_safety', 'Safety', 'note'),
        ('briefing_incident', 'Incident', 'note'),
    )),
    ('Volume & Performance', VOLUME_INPUTS),
    *STAFFING_GROUPS,
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


def _normalize(key, raw, specs=None):
    specs = specs or INPUTS
    if not isinstance(raw, str):
        raise ValueError('Enter text or a whole-number count.')
    if specs[key][1] in ('number', 'percent'):
        if len(raw) > 32:
            raise ValueError('Enter a whole-number count of at most 999,999,999.')
        # All briefing counts use the established count validation; field names
        # remain separate from unrelated Rainrock metrics.
        return normalize_report_value(key if key in ('smalls_percent',) else 'planned_volume', raw)
    text = raw.replace('\r\n', '\n').replace('\r', '\n').strip()
    if '\x00' in text or len(text) > 2000:
        raise ValueError('Each note must contain at most 2,000 characters and no null characters.')
    return text or None


def save_inputs(operation, values, versions, user_id, *, current=False, specs=None):
    """Validate every changed field before an atomic, version-checked update."""
    specs = specs or INPUTS
    if not isinstance(values, dict) or not values or set(values) - specs.keys():
        raise ValueError('Choose valid briefing inputs.')
    if not isinstance(versions, dict) or set(versions) != set(values):
        raise ValueError('Provide the saved version of each changed input.')
    normalized = {key: _normalize(key, raw, specs) for key, raw in values.items()}
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
            raise BriefingConflict(f'{specs[key][0]} changed. Reload the saved inputs before retrying.')
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
    from app.neonodes.neorain.services import _inbound_late_summary, _outbound_late_summary
    eligible = [mission for mission in missions if eligible_for_performance(mission)]
    eligible_ids = {mission.id for mission in eligible}
    summary = (_inbound_late_summary if arrival else _outbound_late_summary)(eligible)['total']
    recorded = sum(completed_actual(mission) is not None for mission in eligible)
    details, other, cancelled = [], [], []
    for mission in missions:
        row = dict(mission_id=mission.id, flight=flight_identity(mission.flight_number),
                   notes=[note.text for note in mission.neorain_mission_notes])
        if mission_cancelled(mission):
            cancelled.append({**row, "flight": mission.flight_number})
            continue
        minutes = completed_variance_minutes(mission) if mission.id in eligible_ids else None
        row['delays'] = [dict(code=delay.code, minutes=delay.minutes, notes=delay.notes or '')
                         for delay in sorted(mission.delay_info_rows, key=lambda item: item.id)]
        if minutes is not None and minutes > 0:
            details.append(dict(row, origin=mission.origin, destination=mission.destination,
                tail=mission.assigned_tail_number, late_minutes=minutes))
        elif row['notes'] or row['delays']:
            other.append(row)
    return dict(eligible=len(eligible) if missions else None, recorded=recorded,
        late_count=summary['aircraft_late'] if recorded else None,
        late_minutes=summary['late_minutes'] if recorded else None,
        average=summary['average'] if recorded else None, incomplete=recorded < len(eligible),
        details=details, other=other, cancelled=cancelled)


def briefing_context(operation, *, current=False, missions=None):
    entries = NeoRainReportEntry.query.filter_by(sort_date_operation_id=operation.id).all()
    rows = {row.field_name: row for row in entries if row.data_source == 'manual'}
    gateway_id = _report_gateway_id(operation)
    native = rain_operation_is_native(operation)
    google = {row.field_name: row for row in entries if row.data_source == 'google'
              and row.source_report_date == operation.sort_date and row.value
              and row.sort_name.casefold() == operation.sort_name.casefold() and row.gateway_id == gateway_id
              and operation.gateway_code.upper() == 'RFD' and operation.sort_name.casefold() == 'night'
              and not native}
    fields = {}
    legacy_staffing = {
        f'briefing_{side}_staffing_{period}': f'{side}_{period}_working'
        for side in ('ramp', 'hub') for period in ('planned', 'actual')
    }
    for key, (label, kind) in INPUTS.items():
        row = rows.get(key)
        fallback_key = legacy_staffing.get(key, key)
        fallback = rows.get(fallback_key) if fallback_key != key else None
        if not (fallback and fallback.value):
            fallback = google.get(fallback_key) if key != 'actual_fph' else None
        raw = row.value if row and row.value else (fallback.value if fallback else None)
        if raw is not None and kind in ('number', 'percent'):
            try:
                raw = normalize_report_value('smalls_percent' if kind == 'percent' else 'planned_volume', raw)
            except ValueError:
                raw = None
        fields[key] = dict(label=label, kind=kind, version=row.version if row else 0,
            manual_value=row.value if row else '', editable=True, value=raw,
            source=('Rainrock snapshot' if fallback.data_source == 'google' else 'Previous Rainrock input')
                if fallback and not (row and row.value) else 'NeoRain entry')
    inbound, outbound = missions if missions is not None else _mission_rows(operation)
    return dict(operation=operation, fields=fields,
        inbound=_performance(inbound, arrival=True), outbound=_performance(outbound, arrival=False),
        legacy_notes=[rows[f'note_{n}'].value for n in range(1, 6)
                      if f'note_{n}' in rows and rows[f'note_{n}'].value])


def briefing_payload(context):
    return dict(briefing=format_briefing(context),
        briefing_detailed=format_briefing(context, include_delay_details=True), fields=context['fields'])


def format_briefing(context, *, include_delay_details=False):
    """Plain-text report projection; the default preserves the concise format."""
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
        def delay_details(row):
            entries = [' '.join(str(value) for value in (delay['minutes'], delay['code'])
                       if value is not None and value != '') for delay in row['delays']]
            notes = '; '.join(delay['notes'] for delay in row['delays'] if delay['notes'])
            text = ', '.join(entry for entry in entries if entry)
            return text + ((' - ' if text else '') + notes if notes else '')

        def mission_notes(row):
            return ' — Mission Notes: ' + '; '.join(row['notes']) if row['notes'] else ''

        for row in data['details']:
            codes = '/'.join(dict.fromkeys(delay['code'] for delay in row['delays'] if delay['code']))
            line = f"{row['origin'] or 'UNAVAILABLE'}-{row['destination'] or 'UNAVAILABLE'} {row['flight'] or 'UNAVAILABLE'} {row['tail'] or 'UNAVAILABLE'} +{row['late_minutes']}"
            if include_delay_details:
                detail = delay_details(row)
                result.append(line + (' — ' + detail if detail else '') + mission_notes(row))
            else:
                result.append(line + (' ' + codes if codes else ''))
        for row in data.get('cancelled', []):
            suffix = ' — ' + '; '.join(row['notes']) if include_delay_details and row['notes'] else ''
            result.append(f"{row['flight']} — CANCELLED" + suffix)
        if include_delay_details:
            for row in data.get('other', []):
                detail = delay_details(row)
                result.append(row['flight'] + (' — Delay Info: ' + detail if detail else '') + mission_notes(row))
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
    if include_delay_details:
        closeout_keys = (
            "smalls_percent", "planned_fph", "actual_fph",
            "ramp_planned_payroll", "ramp_actual_payroll",
            "hub_planned_payroll", "hub_actual_payroll",
        )
        closeout = []
        for key in closeout_keys:
            if fields[key]['value'] is not None:
                suffix = '%' if key == 'smalls_percent' else ''
                closeout.append(f"{fields[key]['label']}: {value(key)}{suffix}")
        if closeout:
            sections.insert(1, closeout)
    return '\n\n'.join('\n'.join(section) for section in sections)
