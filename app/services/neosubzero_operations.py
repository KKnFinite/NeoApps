"""Read-only presentation adapter; physical treatment state remains canonical."""
from datetime import datetime, timedelta
from app.models import SortDateMission, SortDateTailState
from app.neonodes.neosubzero.services import pretreat_context, SURFACE_LABELS
from app.services.neosubzero_departure_deice import departure_deice_context
from app.services.sort_date_operations import mission_display_timing_data
from app.services.time_display import format_local_hhmm

PRIMARY_RAMPS = ('Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')


def departure_presentation(row, tail_state):
    event = row.get('event')
    if event and event.tail_number.strip().upper() != row['tail']:
        return 'review', 'TAIL IDENTITY REVIEW'
    status = getattr(event, 'status', None)
    if status == 'cleared': return 'cleared', 'CLEARED'
    if status == 'finished': return 'fin', 'FIN'
    if status == 'negative': return 'negative', 'NEGATIVE DEICE'
    if status == 'not_sprayed': return 'not-sprayed', 'NOT SPRAYED'
    if event and event.pass1_started_at_utc: return 'deicing', 'DEICING'
    if event and event.configured_at_utc: return 'configured', 'CONFIGURED'
    if event: return 'planned', 'PLANNED'
    canonical = str(getattr(tail_state, 'deice_status', '') or '').lower()
    if canonical == 'cleared': return 'cleared', 'CLEARED'
    if canonical == 'negative': return 'negative', 'NEGATIVE DEICE'
    if canonical == 'configured': return 'configured', 'CONFIGURED'
    if canonical == 'required': return 'planned', 'PLANNED'
    if row.get('deice_status') == 'NOT SPRAYED': return 'not-sprayed', 'NOT SPRAYED'
    # Pretreat completion is not a departure-deice state.
    return 'untreated', 'UNTREATED'


def operations_context(gateway, operation, mode):
    context = departure_deice_context(gateway, operation)
    if operation is None:
        return {**context, 'ramps': PRIMARY_RAMPS}
    missions = {m.id: m for m in SortDateMission.query.filter_by(sort_date_operation_id=operation.id).all()}
    tail_states = {state.tail_number.strip().upper(): state for state in SortDateTailState.query.filter_by(
        sort_date=operation.sort_date,gateway_code=operation.gateway_code,sort_name=operation.sort_name).all()}
    departures = {row['mission_id']: row for row in context['rows']}
    rows = pretreat_context(gateway, operation)['rows'] if mode == 'pretreat' else context['rows']
    for row in rows:
        mission = missions[row['mission_id']]
        departure = departures.get(mission.id, {})
        row['flight'] = mission.flight_number or '—'
        row['ramp'] = departure.get('ramp') or 'Location Review'
        if row['ramp'] not in (*PRIMARY_RAMPS, 'Remote'):
            row['ramp'] = 'Location Review'
        timing = mission_display_timing_data(mission, operation)
        etd = mission.eta_datetime_utc or (mission.planned_datetime_utc + timedelta(minutes=timing.get('effective_window_minutes') or 0) if mission.planned_datetime_utc else None)
        row['etd'] = format_local_hhmm(etd, mission.timezone or None) or '—'
        row['sort_time'] = row.get('sort_eta') if mode == 'pretreat' else etd
        row['departure_status'] = departure_presentation(departure,tail_states.get(row['tail']))[1]
        row['pretreat_complete'] = bool(row.get('completed') if mode == 'pretreat' else row.get('pretreat_complete'))
        event = departure.get('event')
        row['identity_conflict'] = bool(mode == 'outbound' and event and event.tail_number.strip().upper() != row['tail'])
        row['history_event'] = event if row['identity_conflict'] else None
        row['primary_passes'] = []
        if mode == 'pretreat':
            state = row['state']
            planned = bool(state and (row['planned'] or state.configured_at_utc or state.pass1_surface_area or state.pass1_started_at_utc))
            types = ('Type I', 'Type IV') if planned else ()
            surfaces = [getattr(state, f'pass{i}_surface_area', None) for i in (1, 2)]
            row['visual_state'], row['visual_label'] = ('fin', 'PRETREATED') if row['completed'] else ('deicing', 'PRETREAT IN PROGRESS') if state and state.pass1_started_at_utc else ('configured', 'CONFIGURED') if row['configured'] else ('planned', 'PLANNED') if row['planned'] else ('untreated', 'NOT PLANNED')
        else:
            types = row['pass_types'] if not row['identity_conflict'] else ()
            surfaces = [row['pass1_surface'], row['pass2_surface']]
            row['visual_state'], row['visual_label'] = departure_presentation(row,tail_states.get(row['tail']))
        for number in (1, 2):
            row['primary_passes'].append({'number': number, 'type': types[number-1] if len(types) >= number else None,
                'surface': SURFACE_LABELS.get(surfaces[number-1]) if len(types) >= number else None})
        row['detail_key'] = f"{mode}:{mission.id}:{row['tail']}:{getattr(event if mode == 'outbound' else row.get('state'), 'id', 0)}"
    rows.sort(key=lambda row: (mode == 'outbound' and row['visual_state'] == 'cleared', row['sort_time'] is None,
                               row['sort_time'] or datetime.max, row['flight'], row['mission_id']))
    extras = tuple(ramp for ramp in ('Remote', 'Location Review') if any(row['ramp'] == ramp for row in rows))
    return {**context, 'rows': rows, 'ramps': (*PRIMARY_RAMPS, *extras)}
