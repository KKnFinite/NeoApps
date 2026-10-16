"""Physical resource evidence for the existing PROF/SPEAR system, not a scheduler.

Opportunity is not measured travel. ETA/staging policy is kept separately from
confirmed aircraft access. No dispatcher completion timestamp releases a Fueler.
"""
from datetime import timedelta
from decimal import Decimal
from app.services.neoscorpion_fueling_status import utc_naive


def stamp(value):
    from app.services.neoscorpion_prof import timestamp
    return utc_naive(timestamp(value))


def iso(value):
    return value.isoformat() if value else None


def record_master_availability(truck,actor,source,at):
    """Caller holds the master truck lock. No old state/time is backfilled."""
    raw=truck.availability_history
    if raw is not None and (not isinstance(raw,list) or any(not isinstance(item,dict) for item in raw)):
        return  # Preserve malformed legacy evidence; qualification fails closed.
    history=list(raw or [])
    flags=dict(is_active=bool(truck.is_active),is_out_of_service=bool(truck.is_out_of_service))
    if history and all(history[-1].get(key)==value for key,value in flags.items()):return
    truck.availability_history=[*history,dict(flags,at=iso(at),available=flags['is_active'] and not flags['is_out_of_service'],
        actor_id=actor.id if actor else None,source=source)]


def record_truck_availability(selection,truck,actor,source,at):
    """Audit metadata only, under existing sort→master lock ordering."""
    from app.models import NeoScorpionFuelTruck
    truck=NeoScorpionFuelTruck.query.filter_by(id=truck.id).with_for_update().populate_existing().one()
    record_master_availability(truck,actor,'first_confirmed_observation',at)
    raw=selection.availability_history
    if raw is not None and (not isinstance(raw,list) or any(not isinstance(item,dict) for item in raw)):
        return
    history=list(raw or [])
    value=dict(at=iso(at),available=selection.status=='available',status=selection.status,
        actor_id=actor.id if actor else None,source=source)
    if not history or history[-1] != value:
        selection.availability_history=[*history,value]


def availability_window(start,on,nightly_history,master_history):
    """Prove continuity from actual status observations; never infer an old state."""
    reasons=[];evidence=[];states={};events=[]
    for kind,history in (('nightly',nightly_history),('master',master_history)):
        if not isinstance(history,list) or len(history)>5000:
            reasons.append(f'{kind} availability history unavailable or exceeds evidence bound');continue
        valid=[]
        for index,event in enumerate(history):
            at=stamp(event.get('at')) if isinstance(event,dict) else None
            if at is None or type(event.get('available')) is not bool:
                reasons.append(f'{kind} availability history malformed');continue
            if at <= on:valid.append((at,index,event))
        valid.sort(key=lambda item:(item[0],item[1]))
        for first,second in zip(valid,valid[1:]):
            if (first[0]==second[0] and first[2]['available'] != second[2]['available']
                    and not (first[2].get('source') and second[2].get('source'))):
                reasons.append(f'{kind} availability has conflicting simultaneous observations')
        baseline=[item for item in valid if item[0] <= start]
        if not baseline:
            reasons.append(f'{kind} availability was not recorded at the first opportunity');continue
        states[kind]=baseline[-1][2]['available']
        evidence.append(dict(baseline[-1][2],scope=kind))
        for at,index,event in valid:
            if start < at <= on:events.append((at,kind,index,event))
    eligible=start if len(states)==2 and all(states.values()) else None
    for at,kind,index,event in sorted(events,key=lambda item:(item[0],item[1],item[2])):
        states[kind]=event['available'];evidence.append(dict(event,scope=kind))
        if len(states)==2 and all(states.values()) and eligible is None:
            eligible=at
        elif eligible is not None and not all(states.values()):
            reasons.append('Intervening resource unavailability after the first opportunity')
    if eligible is None or len(states)!=2 or not all(states.values()):
        reasons.append('Truck availability opportunity is not proven')
    return eligible,evidence,list(dict.fromkeys(reasons))


def fueler_work_end(work):
    """Only a saved, validated Fueler-data milestone plus physical OFF proves release."""
    if work is None or getattr(work, 'ended_early_at_utc', None):
        return None
    facts = getattr(work, 'performance_context', None) or {}
    ready = facts.get('fueler_data_ready') or {}
    off = stamp(getattr(work, 'off_at_utc', None))
    at = stamp(ready.get('at'))
    actuals = stamp(getattr(work, 'actuals_received_at_utc', None))
    if (not off or not at or not actuals or ready.get('fueler_id') != work.physical_fueler_user_id
            or ready.get('tail') != work.tail_number):
        return None
    return max(off, at, actuals)


def aircraft_evidence(arrival, preparation, early_minutes):
    """Freeze the current early-staging policy and a distinct confirmed access time.

For an arrived aircraft the actual arrival anchors the configured staging
window, avoiding stale/early ETAs punishing work on a late aircraft. Otherwise
ETA permits prospective staging only; it does not prove preparation access.
"""
    actual = stamp(getattr(arrival, 'actual_block_in_datetime_utc', None))
    eta = stamp(getattr(arrival, 'eta_datetime_utc', None) or getattr(arrival, 'planned_datetime_utc', None))
    prepared = stamp((preparation or {}).get('at'))
    access = actual or prepared
    stage = (actual or eta)-timedelta(minutes=early_minutes) if actual or eta else prepared
    return dict(aircraft_access_at=iso(access), aircraft_access_source='actual_block_in' if actual else
                'documented_preparation' if prepared else 'unknown',
                aircraft_staging_at=iso(stage), staging_source='actual_arrival_minus_configured_window' if actual else
                'eta_minus_configured_window' if eta else 'documented_preparation' if prepared else 'unknown',
                incoming_early_staging_minutes=early_minutes)


def previous_visit(visits, kind, identifier, current_id, on):
    candidates = [v for v in visits if v['id'] != current_id and v.get(kind+'_id') == identifier
                  and stamp(v.get('on')) and stamp(v['on']) <= on]
    candidates.sort(key=lambda v:(stamp(v['on']),v['id']))
    if not candidates:
        return None, []
    reasons=[]
    latest=candidates[-1]
    for previous in candidates[:-1]:
        end=stamp(previous.get('off'))
        if end is None or end > stamp(latest['on']):
            reasons.append(f'Overlapping or unresolved previous {kind} visits')
        if kind == 'fueler' and previous.get('fueler_release') is None and not previous.get('finalized'):
            reasons.append('Older unresolved Fueler final work')
    previous=candidates[-1]
    if previous.get('on_source') not in {'manual','trusted automatic'}:
        reasons.append(f'Previous {kind} physical timing is untrusted legacy evidence')
    if previous.get('interrupted'):
        reasons.append(f'Previous {kind} segment interrupted; availability restoration uncertain')
    if kind == 'truck' and previous.get('cycle_type') == 'defuel':
        reasons.append('Post-DEFUEL/sump physical origin not verified')
    physical_off=stamp(previous.get('off'))
    if physical_off is None:
        reasons.append(f'Previous {kind} physical work not released')
    elif physical_off < stamp(previous['on']) or physical_off > on:
        reasons.append(f'Conflicting previous {kind} physical ON/OFF')
    return previous, reasons


def resource_policy(*, work_id, truck_id, fueler_id, on, truck_assigned, fueler_assigned,
                    preparation, aircraft, parking, parking_at, visits, travel_per_move, window, setup):
    """Pure v4 opportunity reconstruction. Inputs are frozen at physical ON."""
    from app.services.neoscorpion_prof import number, minutes
    from app.services.neoscorpion_spear import _parking_ramp, _travel_minutes
    on=stamp(on); truck_assigned=stamp(truck_assigned); fueler_assigned=stamp(fueler_assigned)
    truck, truck_reasons=previous_visit(visits,'truck',truck_id,work_id,on)
    fueler, fueler_reasons=previous_visit(visits,'fueler',fueler_id,work_id,on)
    previous_off=stamp(truck.get('off')) if truck else None
    previous_fueler_off=stamp(fueler.get('off')) if fueler else None
    fueler_release=stamp(fueler.get('fueler_release')) if fueler else fueler_assigned
    access=stamp(aircraft.get('aircraft_access_at')); staging=stamp(aircraft.get('aircraft_staging_at'))
    destination=_parking_ramp(parking); origin=_parking_ramp(truck.get('parking')) if truck else None
    if not truck_assigned or truck_assigned > on:
        truck_reasons.append('Effective truck assignment unknown or after ON')
    if not staging or staging > on:
        truck_reasons.append('Aircraft staging eligibility unknown or after ON')
    if aircraft.get('staging_source') == 'documented_preparation':
        truck_reasons.append('First aircraft staging opportunity unproven; preparation shows latest access only')
    if access and access > on and aircraft.get('aircraft_access_source') == 'actual_block_in':
        truck_reasons.append('Aircraft access conflicts with physical ON')
    if stamp(parking_at) is None:
        truck_reasons.append('Effective destination parking assignment timestamp unknown')
    if previous_off is None:
        truck_reasons.append('Previous physical truck release unknown')
    if origin is None or destination is None:
        truck_reasons.append('Previous/destination physical ramp unknown')
    travel_rate=number(travel_per_move)
    travel=_travel_minutes(origin,destination,travel_rate) if origin is not None and destination is not None and travel_rate is not None and travel_rate >= 0 else None
    if travel is None:
        truck_reasons.append('Configured ramp travel allowance unavailable')
    eligible=max(previous_off,truck_assigned,staging,stamp(parking_at) or staging) if previous_off and truck_assigned and staging else None
    if eligible and eligible > on:
        truck_reasons.append('Eligible truck start is after physical ON')
    if not fueler_assigned or fueler_assigned > on:
        fueler_reasons.append('Effective Fueler assignment unknown or after ON')
    if fueler and fueler_release is None:
        fueler_reasons.append('Previous Fueler final-work milestone unknown')
    if not access or access > on:
        fueler_reasons.append('Aircraft preparation access unknown or after ON')
    available=max(fueler_assigned,fueler_release,access) if fueler_assigned and fueler_release and access else None
    if available and available > on:
        fueler_reasons.append('Fueler not genuinely free at current physical ON')
    prepared=stamp((preparation or {}).get('at'))
    valid_preparation=bool(prepared and prepared <= on and available and prepared >= available
        and (preparation or {}).get('fueler_id') == fueler_id
        and stamp((preparation or {}).get('assigned_at')) == fueler_assigned)
    setup=number(setup); window=number(window)
    lead=minutes(available,on)
    allowance=setup
    reason='Full configured setup; qualifying advance preparation not proven'
    if setup is None or setup < 0:
        allowance=None;fueler_reasons.append('Setup Time unavailable')
    elif window is None:
        reason='Advance window not configured; full Setup Time'
    elif window < 0:
        allowance=None;fueler_reasons.append('Invalid advance setup window')
    elif not fueler_reasons and valid_preparation and lead is not None and lead >= window:
        allowance=Decimal(0);reason='Available, accessible and documented preparation meets advance window'
    return {**aircraft, 'work_id':work_id, 'observed_at':iso(on), 'on_at':iso(on),
        'truck_previous_work_id':truck['id'] if truck else None,
        'truck_previous_release_at':iso(previous_off), 'truck_assigned_at':iso(truck_assigned),
        'truck_eligible_start_at':iso(eligible), 'previous_ramp':origin,'destination_ramp':destination,
        'parking':parking, 'parking_observed_at':iso(on), 'parking_assignment_at':iso(stamp(parking_at)),
        'travel_per_ramp_move':str(travel_rate) if travel_rate is not None else None,
        'expected_travel_minutes':str(travel) if travel is not None else None,
        'fueler_previous_work_id':fueler['id'] if fueler else None,
        'fueler_previous_off_at':iso(previous_fueler_off), 'fueler_previous_release_at':iso(fueler_release) if fueler else None,
        'fueler_release_source':'validated_final_data_and_physical_off' if fueler and fueler_release else
            'no_previous_recorded_physical_commitment' if not fueler else 'unknown',
        'fueler_available_preparation_at':iso(available), 'preparation_ready_at':iso(prepared),
        'preparation_qualified':valid_preparation, 'truck_readiness_at':iso(on),
        'lead_minutes':str(lead) if lead is not None else None,
        'setup_allowance':str(allowance) if allowance is not None else None, 'setup_reason':reason,
        'truck_reasons':list(dict.fromkeys(truck_reasons)), 'fueler_reasons':list(dict.fromkeys(fueler_reasons)),
        'waiting_note':'No pre-ON waiting is excluded without a confirmed truck-presence boundary.'}


def load_visits(operation):
    """One bounded gateway-history query. Never infer identities from current labels."""
    from app.models import NeoScorpionFuelWorkState as Work, NeoScorpionFuelAssignment as Assignment, SortDateOperation, NeoScorpionFuelingEvent as Event
    from datetime import datetime, time
    from types import SimpleNamespace
    from app.extensions import db
    fields=(Work.id,Work.fuel_assignment_id,Work.tail_number,Work.physical_truck_id,Work.physical_fueler_user_id,
            Work.manual_on_at_utc,Work.confirmed_on_at_utc,Work.on_at_utc,Work.off_at_utc,Work.ended_early_at_utc,
            Work.actuals_received_at_utc,Work.physical_finalized_at_utc,Work.physical_cycle_type)
    query=db.session.query(*fields,
        Work.performance_context['on_source'].as_string().label('on_source'),
        Work.performance_context['resource_timing'].as_json().label('resource_timing'),
        Work.performance_context['fueler_data_ready'].as_json().label('fueler_data_ready'),
        Event.id.label('event_id'),Event.started_at_utc.label('event_on'),Event.ended_at_utc.label('event_off'),
        Event.performance_snapshot['resource_timing'].as_json().label('event_resources'),
        Event.performance_snapshot['on_source'].as_string().label('event_on_source'),
        Event.performance_snapshot['fueler_work_end_at'].as_string().label('event_fueler_release')).outerjoin(
            Event,db.and_(Event.fuel_work_state_id==Work.id,Event.sequence_number==Work.physical_sequence_number)).join(Assignment,Assignment.id==Work.fuel_assignment_id).join(
        SortDateOperation,SortDateOperation.id==Assignment.sort_date_operation_id).filter(
        SortDateOperation.gateway_id==operation.gateway_id,
        Work.physical_sequence_number.is_not(None),
        db.or_(Work.created_at >= datetime.combine(operation.sort_date,time.min)-timedelta(days=7),
               Work.physical_finalized_at_utc.is_(None))).order_by(Work.id.desc())
    works=query.limit(5001).all()
    visits=[]
    for row in works[:5000]:
        work=SimpleNamespace(**{field.key:getattr(row,field.key) for field in fields},
            performance_context=dict(on_source=row.on_source,resource_timing=row.resource_timing,fueler_data_ready=row.fueler_data_ready))
        facts=work.performance_context
        on=row.event_on if row.event_id else work.manual_on_at_utc or (work.confirmed_on_at_utc if facts.get('on_source')=='trusted automatic' else None)
        # Preserve an untrusted legacy occupation as uncertainty, not free time.
        if not on and not row.event_id:on=work.on_at_utc
        resources=row.event_resources if row.event_id else facts.get('resource_timing')
        visits.append(dict(id=work.id,assignment_id=work.fuel_assignment_id,truck_id=work.physical_truck_id,
            fueler_id=work.physical_fueler_user_id,on=iso(on),off=iso(row.event_off if row.event_id else work.off_at_utc or work.ended_early_at_utc),
            fueler_release=row.event_fueler_release if row.event_id else iso(fueler_work_end(work)),parking=(resources or {}).get('parking'),
            on_source=row.event_on_source if row.event_id else facts.get('on_source'),tail=work.tail_number,
            finalized=bool(row.event_id or work.physical_finalized_at_utc),interrupted=bool(work.ended_early_at_utc),cycle_type=work.physical_cycle_type))
    return visits, len(works)>5000


def snapshot_resources(gateway,assignment,mission,work,settings,planning,now):
    from app.models import SortDateOperation,SortDateParkingAssignment, NeoScorpionTopOffEvent, NeoScorpionTruckInventoryAudit, NeoScorpionFuelAuditEntry,NeoScorpionSortTruck,NeoScorpionFuelTruck
    from app.extensions import db
    from app.services.neoscorpion import _arrivals_by_tail, format_dispatch_parking_position
    from app.services.neoscorpion_spear import effective_spear_settings
    operation=db.session.get(SortDateOperation,assignment.sort_date_operation_id)
    visits,truncated=load_visits(operation)
    spear=effective_spear_settings(settings)
    preparation=(work.performance_context or {}).get('preparation_evidence') or {}
    if preparation.get('tail') != work.tail_number:preparation={}
    arrival=_arrivals_by_tail(operation).get(work.tail_number)
    aircraft=aircraft_evidence(arrival,preparation,spear.incoming_early_staging_minutes)
    if not aircraft['aircraft_access_at']:
        aircraft.update(aircraft_access_at=iso(now),aircraft_access_source='confirmed_physical_truck_on')
    parking=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id,tail_number=work.tail_number).one_or_none()
    evidence=resource_policy(work_id=work.id,truck_id=work.physical_truck_id,fueler_id=work.physical_fueler_user_id,
        on=now,truck_assigned=assignment.truck_assigned_at_utc,fueler_assigned=assignment.fueler_assigned_at_utc,
        preparation=preparation,aircraft=aircraft,
        parking=format_dispatch_parking_position(parking.ramp_code,parking.position_code) if parking else None,
        parking_at=parking.assigned_at if parking else None,visits=visits,
        travel_per_move=spear.truck_minutes_per_ramp_move,window=getattr(settings,'advance_setup_window_minutes',None),setup=planning.setup_minutes)
    previous_off=stamp(evidence.get('truck_previous_release_at'))
    base_start=stamp(evidence.get('truck_eligible_start_at'))
    if base_start:
        selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=work.physical_truck_id).first()
        truck=db.session.get(NeoScorpionFuelTruck,work.physical_truck_id)
        eligible,history,reasons=availability_window(base_start,now,
            selection.availability_history if selection else None,truck.availability_history if truck else None)
        evidence.update(availability_evidence=history,availability_base_start_at=iso(base_start),
            availability_continuity='verified' if not reasons else 'uncertain')
        evidence['truck_reasons'].extend(reasons)
        if eligible:evidence['truck_eligible_start_at']=iso(eligible)
    if previous_off:
        topoff=NeoScorpionTopOffEvent.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=work.physical_truck_id).filter(NeoScorpionTopOffEvent.occurred_at_utc > previous_off,
                NeoScorpionTopOffEvent.occurred_at_utc <= now).order_by(NeoScorpionTopOffEvent.occurred_at_utc.desc()).first()
        if topoff:
            evidence['truck_reasons'].append('TOP OFF intervened; next physical origin not independently verified')
            evidence['intervening_top_off_event_id']=topoff.id
            if topoff.kind == 'returned' and evidence.get('truck_eligible_start_at'):
                evidence['truck_eligible_start_at']=iso(max(stamp(evidence['truck_eligible_start_at']),topoff.occurred_at_utc))
        sumped=NeoScorpionTruckInventoryAudit.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=work.physical_truck_id).filter(NeoScorpionTruckInventoryAudit.created_at > previous_off,
                NeoScorpionTruckInventoryAudit.created_at <= now,
                NeoScorpionTruckInventoryAudit.reason.startswith('MARK SUMPED')).order_by(NeoScorpionTruckInventoryAudit.created_at.desc()).first()
        if sumped and evidence.get('truck_eligible_start_at'):
            evidence['truck_eligible_start_at']=iso(max(stamp(evidence['truck_eligible_start_at']),sumped.created_at))
            evidence['sump_release_audit_id']=sumped.id
    if truncated:
        evidence['truck_reasons'].append('Resource history exceeds bounded evidence limit')
        evidence['fueler_reasons'].append('Resource history exceeds bounded evidence limit')
    if NeoScorpionFuelAuditEntry.query.filter_by(fuel_assignment_id=assignment.id,action='auto_hold').filter(
            NeoScorpionFuelAuditEntry.created_at <= now,
            NeoScorpionFuelAuditEntry.created_at >= (assignment.truck_assigned_at_utc or now)).first():
        evidence['truck_reasons'].append('Hold/review affected this resource opportunity')
        evidence['fueler_reasons'].append('Hold/review affected preparation opportunity')
    return evidence


def attach_resource_evidence(rows,operation,settings,planning,now):
    """Shared read-only prospective availability; unresolved future order is unknown."""
    from app.services.neoscorpion_spear import effective_spear_settings
    from app.models import NeoScorpionSortTruck,NeoScorpionFuelTruck,SortDateParkingAssignment
    from app.extensions import db
    spear=effective_spear_settings(settings)
    visits,truncated=load_visits(operation)
    histories={row[0]:(row[1],row[2]) for row in db.session.query(NeoScorpionSortTruck.fuel_truck_id,
        NeoScorpionSortTruck.availability_history,NeoScorpionFuelTruck.availability_history).join(NeoScorpionFuelTruck,
        NeoScorpionFuelTruck.id==NeoScorpionSortTruck.fuel_truck_id).filter(NeoScorpionSortTruck.sort_date_operation_id==operation.id).all()}
    parking_dates={row.tail_number:row.assigned_at for row in SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id).all()}
    future=[row for row in rows if row.get('assignment') and not row.get('is_source_segment')
        and not row.get('administratively_complete') and not row.get('fueling_effective_on_utc')
        and not row.get('is_off')]
    for row in rows:
        # Reuse the already-loaded authoritative visit evidence for whole-sort
        # calendars. This adds no query or second physical-release formula.
        row['spear_physical_visits']=visits
        row['spear_visits_truncated']=truncated
        work=row.get('fuel_work_state');assignment=row.get('assignment')
        facts=getattr(work,'performance_context',None) or {}
        if not assignment:continue
        if facts.get('version') == 4:
            row['resource_timing']=facts.get('resource_timing') or {}
            continue
        if row.get('fueling_effective_on_utc') or row.get('is_off') or row.get('administratively_complete'):
            continue  # Existing v3 live cycles retain their original policy.
        preparation=facts.get('preparation_evidence') or {}
        aircraft=aircraft_evidence(row.get('arrival_mission'),preparation,spear.incoming_early_staging_minutes)
        evidence=resource_policy(work_id=getattr(work,'id',None),truck_id=assignment.assigned_truck_id,
            fueler_id=assignment.assigned_fueler_user_id,on=now,truck_assigned=assignment.truck_assigned_at_utc,
            fueler_assigned=assignment.fueler_assigned_at_utc,preparation=preparation,aircraft=aircraft,
            parking=row.get('parking_position'),parking_at=parking_dates.get(row.get('tail_number') or row['mission'].assigned_tail_number),visits=visits,
            travel_per_move=spear.truck_minutes_per_ramp_move,window=getattr(settings,'advance_setup_window_minutes',None),setup=planning.setup_minutes)
        evidence['prospective']=True
        base_start=stamp(evidence.get('truck_eligible_start_at'))
        if base_start:
            eligible,history,reasons=availability_window(base_start,now,*histories.get(assignment.assigned_truck_id,(None,None)))
            evidence['truck_reasons'].extend(reasons)
            if eligible:evidence['truck_eligible_start_at']=iso(eligible)
        conflicts=[other for other in future if other is not row and (
            assignment.assigned_truck_id is not None and other['assignment'].assigned_truck_id == assignment.assigned_truck_id
            or assignment.assigned_fueler_user_id is not None and other['assignment'].assigned_fueler_user_id == assignment.assigned_fueler_user_id)]
        if conflicts or truncated:
            evidence['forecast_uncertainty']='Future resource order unproven' if conflicts else 'Bounded resource history incomplete'
        row['resource_timing']=evidence


def finalized_resources(evidence,work,now):
    """Resolve only the already-referenced previous work, never reorder history.

Late final data is an outcome observed later, not a retroactive forecast input.
The original ON snapshot is retained verbatim.
"""
    from app.extensions import db
    from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
    result=dict(evidence)
    previous_id=evidence.get('fueler_previous_work_id')
    previous=db.session.get(NeoScorpionFuelWorkState,previous_id) if previous_id else None
    if previous:
        event=NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=previous.id,
            sequence_number=previous.physical_sequence_number).first()
        released=stamp((event.performance_snapshot or {}).get('fueler_work_end_at')) if event else fueler_work_end(previous)
        result['final_observed_at']=iso(now)
        result['at_on_fueler_release_at']=evidence.get('fueler_previous_release_at')
        result['fueler_previous_release_at']=iso(released)
    return result


def truck_interval(evidence,on,off):
    """Adjusted opportunity interval, with proof-carrying, nonoverlapping waiting.

    ON begins physical service, not a waiting interval. Before ON a configured
    travel allowance is not an observed arrival. Current canonical records have
    no independent confirmed staging/arrival milestone, so those waits remain
    uncertain and are never subtracted. Contradictory ON/work-end overlap is not
    converted into an apparently high Truck score.
"""
    from app.services.neoscorpion_prof import number, minutes
    reasons=list(evidence.get('truck_reasons',()))
    start=stamp(evidence.get('truck_eligible_start_at'));on=stamp(on);off=stamp(off)
    fueler_end=stamp(evidence.get('fueler_previous_release_at'))
    fueler_off=stamp(evidence.get('fueler_previous_off_at'))
    travel=number(evidence.get('expected_travel_minutes'))
    elapsed=minutes(start,off)
    intervals=[];excluded=Decimal(0)
    if not start or not on or not off or start > on or off <= on:
        reasons.append('Invalid eligible-start/physical ON/OFF ordering')
    if evidence.get('fueler_previous_work_id') and fueler_end is None:
        reasons.append('Fueler waiting uncertain: previous final-work timestamp unavailable')
    if fueler_end and start and on and travel is not None:
        travel_ready=start+timedelta(minutes=float(travel))
        if fueler_end > travel_ready and travel_ready < on:
            reasons.append('Pre-ON Fueler waiting uncertain: no confirmed truck arrival milestone')
        if fueler_end > on:
            reasons.append('Current physical service overlaps previous Fueler final work; waiting is not proven')
    adjusted=elapsed-excluded if elapsed is not None else None
    if adjusted is None or adjusted <= 0:
        reasons.append('Nonpositive adjusted Truck interval')
    return dict(truck_actual_minutes=adjusted,truck_elapsed_minutes=elapsed,
        excluded_wait_minutes=excluded,excluded_wait_intervals=intervals,
        truck_timing_reasons=list(dict.fromkeys(reasons)))
