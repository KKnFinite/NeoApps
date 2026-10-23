"""Per-pass participation on canonical spray records, independent of gallons."""
from datetime import datetime
from sqlalchemy import func
from app.extensions import db
from app.models import (NeoSubZeroSprayRecord, NeoSubZeroUccTruckAssignment,
    NeoSubZeroUccAssignment, NeoSubZeroPretreatState, StaffingPerson, SortDateParkingAssignment)
from app.services.neosubzero_evidence import (application_context, audit_change,
    evidence_values, normalized_tail, NeoSubZeroEvidenceError)


def participation_query(parent, kind='departure'):
    field=NeoSubZeroSprayRecord.pretreat_state_id if kind=='pretreat' else NeoSubZeroSprayRecord.departure_deice_event_id
    return NeoSubZeroSprayRecord.query.filter(field==parent.id)


def pass_types(parent, kind):
    if kind=='pretreat': return ('Type I','Type IV')
    from app.services.neosubzero_departure_deice import PLAN_PASS_TYPES
    return PLAN_PASS_TYPES.get(parent.treatment_plan,())


def physical_location(operation, tail):
    from app.services.neosubzero_departure_deice import _ramp_name
    parking=SortDateParkingAssignment.query.filter(
        SortDateParkingAssignment.sort_date_operation_id==operation.id,
        func.upper(func.trim(SortDateParkingAssignment.tail_number))==normalized_tail(tail)).one_or_none()
    return _ramp_name(getattr(parking,'ramp_code',None)),getattr(parking,'position_code',None)


def truck_team(operation, number):
    slots=NeoSubZeroUccTruckAssignment.query.filter(
        NeoSubZeroUccTruckAssignment.sort_date_operation_id==operation.id,
        func.lower(func.trim(NeoSubZeroUccTruckAssignment.truck_number))==str(number).strip().lower()).all()
    if len(slots)>1: raise NeoSubZeroEvidenceError('Truck has conflicting DUCC assignments. Resolve the shared assignment first.')
    if not slots: return None,None
    slot=slots[0]
    people={row.team_role:row.person for row in NeoSubZeroUccAssignment.query.filter_by(
        sort_date_operation_id=operation.id,ramp=slot.ramp,position_number=slot.position_number).all()}
    return people.get('driver'),people.get('flyer')


def _crew(record, driver, flyer):
    for role,person in (('driver',driver),('flyer',flyer)):
        setattr(record,role+'_person_id',getattr(person,'id',None))
        setattr(record,role+'_name_snapshot',getattr(person,'full_name',None))


def _new_participant(operation, mission, parent, kind, number, slot, truck, settings):
    types=pass_types(parent,kind)
    if number not in range(1,len(types)+1): raise NeoSubZeroEvidenceError('Save the applicable treatment plan before selecting this pass.')
    ramp,parking=physical_location(operation,parent.tail_number)
    record=NeoSubZeroSprayRecord(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
        departure_deice_event_id=parent.id if kind=='departure' else None,
        pretreat_state_id=parent.id if kind=='pretreat' else None,pass_number=number,position_number=slot,
        tail_number_snapshot=normalized_tail(parent.tail_number),flight_number_snapshot=mission.flight_number,
        timezone_snapshot=mission.timezone,ramp_snapshot=ramp or None,parking_snapshot=parking,
        truck_number_snapshot=str(truck).strip(),pass_type='type_iv' if types[number-1]=='Type IV' else 'type_i',
        fluid_name_snapshot=settings.type_iv_fluid_name if types[number-1]=='Type IV' else settings.type_i_fluid_name,
        concentration_percent_snapshot=100 if types[number-1]=='Type IV' else settings.type_i_concentration_percent,
        surface_area=getattr(parent,f'pass{number}_surface_area'),participation_selected=True,crew_override=False)
    _crew(record,*truck_team(operation,truck));db.session.add(record)
    return record


def ensure_planned_participants(operation, mission, parent, kind, number, settings):
    db.session.flush()
    records=participation_query(parent,kind).filter_by(pass_number=number).order_by(NeoSubZeroSprayRecord.position_number).all()
    if records: return records
    ramp,_parking=physical_location(operation,parent.tail_number)
    for truck in NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=operation.id,ramp=ramp).order_by(NeoSubZeroUccTruckAssignment.position_number).all():
        if truck.truck_number:
            records.append(_new_participant(operation,mission,parent,kind,number,truck.position_number,truck.truck_number,settings))
    return records


def _pass_evidence(operation, mission, parent, kind, number, settings):
    context=application_context(operation)
    ramp,parking=physical_location(operation,parent.tail_number)
    type_iv=pass_types(parent,kind)[number-1]=='Type IV'
    return dict(tail=normalized_tail(parent.tail_number),mission_id=mission.id,flight=mission.flight_number,
        original_mission_id=getattr(parent,'sort_date_mission_id',None) or getattr(parent,'original_mission_id',None),
        kind=kind,pass_type='type_iv' if type_iv else 'type_i',operation_id=operation.id,sort_date=operation.sort_date.isoformat(),sort_name=operation.sort_name,
        ramp=ramp or None,parking=parking,fluid=settings.type_iv_fluid_name if type_iv else settings.type_i_fluid_name,
        concentration=100 if type_iv else settings.type_i_concentration_percent,
        reason=parent.reason_for_application or context['reason_for_application'] or ('Pretreat' if kind=='pretreat' else 'Frost'),
        active_precipitation=context['active_precipitation'] or None,ambient_temperature=context['ambient_temperature'] or None,
        dew_point=context['dew_point'] or None,notes=context['notes'] or None)


def _apply_official(record, evidence, parent, number):
    if evidence.get('pass_type'):
        record.pass_type=evidence['pass_type']
    if evidence.get('mission_id'):
        record.sort_date_mission_id=evidence['mission_id']
    record.surface_area=getattr(parent,f'pass{number}_surface_area')
    record.started_at_utc=getattr(parent,f'pass{number}_started_at_utc')
    record.ended_at_utc=getattr(parent,f'pass{number}_ended_at_utc')
    for column,key in (('tail_number_snapshot','tail'),('flight_number_snapshot','flight'),('ramp_snapshot','ramp'),('parking_snapshot','parking'),('fluid_name_snapshot','fluid'),('concentration_percent_snapshot','concentration'),('reason_for_application','reason'),('active_precipitation','active_precipitation'),('ambient_temperature','ambient_temperature'),('dew_point','dew_point'),('notes','notes')):
        setattr(record,column,evidence.get(key))


def snapshot_treatment_passes(operation, mission, parent, settings, *, kind='departure', actor_id=None, previous=None):
    """END freezes each pass independently, in the caller's locked transaction."""
    db.session.flush()
    evidence=dict(parent.pass_evidence or {})
    for number,_type in enumerate(pass_types(parent,kind),1):
        end=getattr(parent,f'pass{number}_ended_at_utc')
        if not end: continue
        old=evidence.get(str(number))
        # A pre-upgrade completed pass is not proof of its former staffing or
        # fluid. Require an explicit historical correction; never backfill now.
        if not old and (previous or {}).get(f'pass{number}_ended_at_utc'):
            continue
        captured=old or _pass_evidence(operation,mission,parent,kind,number,settings)
        evidence[str(number)]={**captured,'start':getattr(parent,f'pass{number}_started_at_utc').isoformat(),'end':end.isoformat(),'surface':getattr(parent,f'pass{number}_surface_area')}
        records=participation_query(parent,kind).filter_by(pass_number=number).all() if old else ensure_planned_participants(operation,mission,parent,kind,number,settings)
        for record in records:
            if not record.participation_selected and not record.gallons: continue
            before=evidence_values(record)
            if not record.official_at_utc and not record.gallons:
                if not record.crew_override: _crew(record,*truck_team(operation,record.truck_number_snapshot))
                record.sort_date_mission_id=mission.id
                record.official_at_utc=datetime.utcnow()
            _apply_official(record,captured,parent,number)
            record.recorded_by_user_id=actor_id
            if before != evidence_values(record):
                audit_change(operation,'pass_end_snapshot' if not old else 'pass_evidence_correction',actor_id=actor_id,mission=mission,
                    event=parent if kind=='departure' else None,record=record,before=before,after=evidence_values(record))
    parent.pass_evidence=evidence or None


def _historical_evidence(operation,mission,parent,kind,number,settings,values):
    evidence=(parent.pass_evidence or {}).get(str(number))
    if evidence: return evidence
    candidates=participation_query(parent,kind).filter_by(pass_number=number).all()
    candidates=[record for record in candidates if record.gallons and record.started_at_utc and record.ended_at_utc]
    if candidates and len({(r.pass_type,r.fluid_name_snapshot,r.concentration_percent_snapshot,r.reason_for_application,r.active_precipitation,r.ambient_temperature,r.dew_point) for r in candidates})==1:
        first=candidates[0]
        return dict(tail=parent.tail_number,mission_id=first.sort_date_mission_id,flight=first.flight_number_snapshot,
            kind=kind,pass_type=first.pass_type,ramp=first.ramp_snapshot,parking=first.parking_snapshot,fluid=first.fluid_name_snapshot,
            concentration=first.concentration_percent_snapshot,reason=first.reason_for_application,
            active_precipitation=first.active_precipitation,ambient_temperature=first.ambient_temperature,dew_point=first.dew_point,notes=first.notes)
    fluid=str(values.get('fluid_name') or '').strip()
    try: concentration=int(values.get('concentration_percent') or '')
    except (TypeError,ValueError): concentration=0
    type_iv=pass_types(parent,kind)[number-1]=='Type IV'
    if not fluid or len(fluid)>80 or not 1<=concentration<=100 or (type_iv and concentration!=100):
        raise NeoSubZeroEvidenceError('Historical fluid evidence is missing. Enter the actual product and concentration (Type IV: 100%) for this correction.')
    return dict(tail=parent.tail_number,mission_id=getattr(parent,'sort_date_mission_id',None) or getattr(parent,'original_mission_id',None) or mission.id,
        flight=getattr(parent,'flight_number_snapshot',None),kind=kind,pass_type='type_iv' if type_iv else 'type_i',
        ramp=None,parking=None,fluid=fluid,concentration=concentration,
        reason=parent.reason_for_application,active_precipitation=None,ambient_temperature=None,dew_point=None,notes=None)


def change_participation(operation, mission, parent, kind, action, values, settings, *, actor_id):
    from app.services.live_collaboration import entity_version,version_conflict
    number=int(values.get('pass_number') or 0)
    if number not in range(1,len(pass_types(parent,kind))+1): raise NeoSubZeroEvidenceError('Choose an applicable pass.')
    complete=bool(getattr(parent,f'pass{number}_ended_at_utc'))
    reason=str(values.get('correction_reason') or '').strip()
    if complete and not reason: raise NeoSubZeroEvidenceError('Enter a reason for this historical participation correction.')
    records=participation_query(parent,kind).filter_by(pass_number=number).with_for_update().all()
    materialized=False
    if not complete and not records and action in {'add_truck','remove_truck'}:
        records=ensure_planned_participants(operation,mission,parent,kind,number,settings)
        db.session.flush();materialized=True
    before=None
    if action=='add_truck':
        number_text=str(values.get('truck_number') or '').strip()
        if not number_text or len(number_text)>32: raise NeoSubZeroEvidenceError('Enter the actual participating truck number.')
        if any(r.truck_number_snapshot.casefold()==number_text.casefold() and (r.participation_selected or r.gallons) for r in records):
            raise NeoSubZeroEvidenceError('This truck already participates in the selected pass.')
        occupied={r.position_number for r in records if r.participation_selected or r.gallons}
        slot=next((slot for slot in range(1,5) if slot not in occupied),None)
        if slot is None: raise NeoSubZeroEvidenceError('A pass supports at most four participating trucks. Applied gallons must remain preserved.')
        record=next((r for r in records if r.position_number==slot),None)
        if record:
            before=evidence_values(record)
            record.truck_number_snapshot=number_text;record.excluded_at_utc=None;record.participation_selected=True;record.official_at_utc=None;record.crew_override=False
            _crew(record,*truck_team(operation,number_text))
        else: record=_new_participant(operation,mission,parent,kind,number,slot,number_text,settings)
    else:
        record=next((r for r in records if r.id==int(values.get('record_id') or 0) or (materialized and r.position_number==int(values.get('position_number') or 0))),None)
        if record is None: raise NeoSubZeroEvidenceError('Participation changed. Reload current values.')
        if not materialized and (not values.get('record_version') or version_conflict(record,values.get('record_version'))): raise NeoSubZeroEvidenceError('Participation changed. Your draft is kept; reload current values.')
        before=evidence_values(record)
        if action=='remove_truck':
            record.participation_selected=False
            if not record.gallons: record.excluded_at_utc=datetime.utcnow()
        elif action!='correct_participant': raise NeoSubZeroEvidenceError('Choose an existing participation action.')
    if action in {'add_truck','correct_participant'}:
        if complete and values.get('crew_confirmed')!='yes': raise NeoSubZeroEvidenceError('Explicitly confirm the actual historical Driver/Flyer for this correction; current DUCC staffing is not historical proof.')
        if action=='correct_participant' and values.get('truck_number'):
            truck=str(values.get('truck_number')).strip()
            if len(truck)>32: raise NeoSubZeroEvidenceError('Truck number is too long.')
            if any(r.id!=record.id and r.truck_number_snapshot.casefold()==truck.casefold() and (r.participation_selected or r.gallons) for r in records): raise NeoSubZeroEvidenceError('This truck already participates in this pass.')
            record.truck_number_snapshot=truck
        if values.get('crew_confirmed')=='yes':
            from app.services.neosubzero_ucc import _validate_available_person
            crew=[]
            for role in ('driver','flyer'):
                raw=str(values.get(role+'_person_id') or '').strip()
                person=db.session.get(StaffingPerson,int(raw)) if raw else None
                if person and not complete: _validate_available_person(operation,person)
                if raw and person is None: raise NeoSubZeroEvidenceError('Choose an existing qualified '+role+'.')
                crew.append(person)
            if crew[0] and crew[1] and crew[0].id==crew[1].id: raise NeoSubZeroEvidenceError('Driver and Flyer must be different people.')
            _crew(record,*crew);record.crew_override=True
        if complete:
            captured=_historical_evidence(operation,mission,parent,kind,number,settings,values)
            _apply_official(record,captured,parent,number);record.official_at_utc=record.official_at_utc or datetime.utcnow()
        record.recorded_by_user_id=actor_id
    # Bump the parent token so competing ADD operations cannot silently merge.
    parent.updated_at=datetime.utcnow()
    audit_change(operation,action,actor_id=actor_id,mission=mission,event=parent if kind=='departure' else None,
        record=record,reason=reason,before=before,after=evidence_values(record))
    return record
