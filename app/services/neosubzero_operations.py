"""Read-only presentation adapter; physical treatment state remains canonical."""
from datetime import datetime, timedelta
from app.models import NeoSubZeroSprayRecord, SortDateMission, SortDateTailState
from app.services.live_collaboration import entity_version
from app.neonodes.neosubzero.services import PRETREAT_SURFACE, pretreat_context, SURFACE_LABELS
from app.services.neosubzero_departure_deice import departure_deice_context, mobile_departure_evidence
from app.services.neosubzero_participation import confirmed_participant
from app.services.neosubzero_spray import _decimal_display
from app.services.sort_date_operations import mission_display_timing_data
from app.services.time_display import format_local_hhmm

PRIMARY_RAMPS = ('Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo')


def departure_presentation(row, tail_state):
    event = row.get('event')
    if row.get('ownership_review'):
        return 'review', 'TREATMENT OWNERSHIP REVIEW'
    if event and event.tail_number.strip().upper() != row['tail']:
        return 'review', 'TAIL IDENTITY REVIEW'
    status = getattr(event, 'status', None)
    if row.get('radio_pending'): return 'radio-pending', 'CLEARED · RADIO PENDING'
    if row.get('radio_complete'): return 'gone', 'GONE · RADIO COMPLETE'
    if status == 'cleared': return 'gone', 'GONE · CLEARED'
    if status == 'finished': return 'fin', 'FIN'
    if status == 'negative': return 'negative', 'NEGATIVE DEICE'
    if status == 'not_sprayed': return 'not-sprayed', 'NOT SPRAYED'
    if event and event.pass1_started_at_utc: return 'deicing', 'DEICING'
    if event and event.configured_at_utc: return 'configured', 'CONFIGURED'
    if event: return 'planned', 'PLANNED'
    canonical = str(getattr(tail_state, 'deice_status', '') or '').lower()
    if canonical == 'cleared': return 'gone', 'GONE · CLEARED'
    if canonical == 'negative': return 'negative', 'NEGATIVE DEICE'
    if canonical == 'configured': return 'configured', 'CONFIGURED'
    if canonical == 'required': return 'planned', 'PLANNED'
    if row.get('gone_without_deice'): return 'gone', 'GONE · NO DEICE PLANNED'
    # Pretreat completion is not a departure-deice state.
    return 'untreated', 'UNTREATED'


def operations_context(gateway, operation, mode):
    context = departure_deice_context(gateway, operation)
    if operation is None:
        return {**context, 'ramps': (*PRIMARY_RAMPS, 'Remote')}
    missions = {m.id: m for m in SortDateMission.query.filter_by(sort_date_operation_id=operation.id).all()}
    tail_states = {state.tail_number.strip().upper(): state for state in SortDateTailState.query.filter_by(
        sort_date=operation.sort_date,gateway_code=operation.gateway_code,sort_name=operation.sort_name).all()}
    departures = {row['mission_id']: row for row in context['rows']}
    rows = pretreat_context(gateway, operation)['rows'] if mode == 'pretreat' else context['rows']
    evidence = mobile_departure_evidence(operation) if mode == 'outbound' else {}
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
        row['identity_conflict'] = bool(mode == 'outbound' and (departure.get('ownership_review') or (event and event.tail_number.strip().upper() != row['tail'])))
        row['ownership_review'] = departure.get('ownership_review')
        row['history_event'] = event if row['identity_conflict'] else None
        row['primary_passes'] = []
        if mode == 'pretreat':
            row['parking'] = departure.get('parking') or row['parking']
            row['destination'] = mission.destination or '—'
            types = ('Type I', 'Type IV')
            surfaces = [PRETREAT_SURFACE, PRETREAT_SURFACE]
            row['visual_state'], row['visual_label'] = (
                ('fin', 'PRETREATED') if row['completed'] else
                ('configured', 'CONFIGURED') if row['configured'] else
                ('planned', 'PLANNED') if row['planned'] else ('untreated', 'NOT PLANNED')
            )
        else:
            row['mission_departure_status'] = mission.departure_status
            row['mobile_evidence'] = evidence.get(getattr(event, 'id', None), {})
            row['mobile_open'] = (not row['identity_conflict'] and row['tail'] != '-' and
                                  mission.departure_status != 'cancelled' and
                                  (mission.departure_status != 'departed' or row.get('radio_pending')) and not row['terminal'])
            types = row['pass_types'] if not row['identity_conflict'] else ()
            surfaces = [row['pass1_surface'], row['pass2_surface']]
            row['visual_state'], row['visual_label'] = departure_presentation(row,tail_states.get(row['tail']))
            # Departure closes entry to new treatment, while FIN still needs
            # its own explicit CLEAR and pilot readback in desktop Dispatch.
            row['desktop_clear'] = (mission.departure_status == 'departed' and row['visual_state'] == 'fin'
                                    and not row['identity_conflict'] and row['tail'] != '-')
            if row['visual_state'] == 'untreated':
                row['visual_state'], row['visual_label'] = (
                    ('pretreated', 'PRETREATED') if row.get('pretreat_milestones_complete') else ('untreated', 'NO STATUS'))
            row['quick_actions'], row['quick_clock'] = outbound_quick_actions(row)
        for number in (1, 2):
            row['primary_passes'].append({'number': number, 'type': types[number-1] if len(types) >= number else None,
                'surface': SURFACE_LABELS.get(surfaces[number-1]) if len(types) >= number else None})
        row['detail_key'] = f"{mode}:{mission.id}:{row['tail']}:{getattr(event if mode == 'outbound' else row.get('state'), 'id', 0)}"
    def order(row):
        group=(2 if row['visual_state']=='gone' else 1 if row['visual_state']=='radio-pending' else 0) if mode=='outbound' else 0
        event=row.get('event')
        timestamp=getattr(event,'radio_completed_at_utc',None) or getattr(event,'cleared_at_utc',None) or row.get('block_out_at_utc')
        return (group, datetime.max-(timestamp or datetime.min) if group==2 else timedelta(0),
                row['sort_time'] is None,row['sort_time'] or datetime.max,row['flight'],row['mission_id'])
    rows.sort(key=order)
    if mode=='pretreat':
        participants = {}
        occupied = {}
        records = NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id).filter(
            NeoSubZeroSprayRecord.pretreat_state_id.is_not(None)).order_by(NeoSubZeroSprayRecord.position_number).all()
        for record in records:
            actual = confirmed_participant(record)
            key = (record.pretreat_state_id, record.pass_number)
            if record.participation_selected or actual:
                occupied[key] = occupied.get(key, 0) + 1
            if record.gallons is not None or (not record.excluded_at_utc and (record.participation_selected or actual)):
                participants.setdefault(key, []).append(dict(
                    record=record, actual=actual, version=entity_version(record), gallons=_decimal_display(record.gallons)))
        for row in rows:
            for treatment in row['primary_passes']:
                key = (getattr(row['state'], 'id', None), treatment['number'])
                treatment['participants'] = participants.get(key, ())
                treatment['can_add_truck'] = occupied.get(key, 0) < 4
    extras = ('Remote', 'Location Review') if any(row['ramp'] == 'Location Review' for row in rows) else ('Remote',)
    return {**context, 'rows': rows, 'ramps': (*PRIMARY_RAMPS, *extras)}


def outbound_quick_actions(row):
    """Presentation eligibility; canonical mutations still enforce all guards."""
    event = row.get('event')
    if row.get('identity_conflict') or not (row.get('mobile_open') or row.get('desktop_clear')):
        return (), None
    status = getattr(event, 'status', None)
    if row.get('radio_pending'):
        return (('radio', 'RADIO'),), None
    if status == 'finished':
        return (('clear', 'CLEAR'),), None
    if status in ('negative', 'not_sprayed'):
        return (('move_to_planned', 'INITIAL CONTACT'), ('clear', 'CLEAR')), None
    if event is None:
        return (('initial_contact', 'INITIAL CONTACT'), ('set_negative', 'NEGATIVE DEICE')), None
    if not row.get('has_actual_spray'):
        actions = [('toggle_configured', 'CLEAR CONFIGURED' if row.get('configured') else 'CONFIGURED'),
                   ('reset', 'RESET'), ('set_negative', 'NEGATIVE DEICE')]
        if row.get('configured'):
            actions.append(('clear', 'CLEAR'))
    else:
        actions = []
    clock = None
    for number, _fluid in enumerate(row.get('pass_types', ()), 1):
        if not row.get(f'pass{number}_surface'):
            break
        if not row.get(f'pass{number}_start'):
            if row.get('configured') or row.get('has_actual_spray'):
                clock = (f'pass{number}_start', f'START PASS {number}')
            break
        if not row.get(f'pass{number}_end'):
            clock = (f'pass{number}_end', f'END PASS {number}')
            break
    return tuple(actions), clock
