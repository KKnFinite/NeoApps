"""Per-pass participation on canonical spray records, independent of gallons."""
from datetime import datetime
from sqlalchemy import func
from app.extensions import db
from app.models import (NeoSubZeroSprayRecord, NeoSubZeroUccTruckAssignment,
    NeoSubZeroUccAssignment, StaffingPerson, SortDateParkingAssignment)
from app.services.neosubzero_evidence import (application_context, audit_change,
    evidence_values, normalized_tail, NeoSubZeroEvidenceError)
from app.neonodes.neosubzero.services import DEFAULT_PRETREAT_REASON


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
    if kind == 'departure':
        _crew(record, *truck_team(operation, truck))
    db.session.add(record)
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
    if kind=='departure' and mission.actual_block_out_datetime_utc and ramp in ('Alpha','Bravo','Charlie','Delta','Echo'):
        # Consume the existing canonical queue. Parking position 10 is never a pad.
        from app.services.neosubzero_departure_deice import departure_deice_context
        from app.services.neosubzero_ucc import _physical_queue
        from app.models import Gateway
        gateway=db.session.get(Gateway,operation.gateway_id)
        rows=departure_deice_context(gateway,operation)['rows']
        queue=_physical_queue([row for row in rows if row.get('ramp')==ramp])
        if queue and queue[0]['event'] and queue[0]['event'].id==parent.id:
            parking='DEICE PAD'
    return dict(tail=normalized_tail(parent.tail_number),mission_id=mission.id,flight=mission.flight_number,
        original_mission_id=getattr(parent,'sort_date_mission_id',None) or getattr(parent,'original_mission_id',None),
        kind=kind,pass_type='type_iv' if type_iv else 'type_i',operation_id=operation.id,sort_date=operation.sort_date.isoformat(),sort_name=operation.sort_name,
        ramp=ramp or None,parking=parking,fluid=settings.type_iv_fluid_name if type_iv else settings.type_i_fluid_name,
        concentration=100 if type_iv else (settings.type_i_concentration_percent if kind=='departure' else None),
        reason=DEFAULT_PRETREAT_REASON if kind=='pretreat' else parent.reason_for_application or context['reason_for_application'] or None,
        active_precipitation=context['active_precipitation'] or None,ambient_temperature=context['ambient_temperature'] or None,
        dew_point=context['dew_point'] or None,notes=context['notes'] or None)


def _apply_official(record, evidence, parent, number):
    if evidence.get('pass_type'):
        record.pass_type=evidence['pass_type']
    if evidence.get('mission_id'):
        record.sort_date_mission_id=evidence['mission_id']
    record.surface_area=evidence.get('surface') or getattr(parent,f'pass{number}_surface_area')
    record.started_at_utc=getattr(parent,f'pass{number}_started_at_utc')
    record.ended_at_utc=getattr(parent,f'pass{number}_ended_at_utc')
    for column,key in (('tail_number_snapshot','tail'),('flight_number_snapshot','flight'),('ramp_snapshot','ramp'),('parking_snapshot','parking'),('fluid_name_snapshot','fluid'),('concentration_percent_snapshot','concentration'),('reason_for_application','reason'),('active_precipitation','active_precipitation'),('ambient_temperature','ambient_temperature'),('dew_point','dew_point'),('notes','notes')):
        setattr(record,column,evidence.get(key))


def snapshot_treatment_passes(operation, mission, parent, settings, *, kind='departure', actor_id=None, previous=None, correction_reason=None):
    """Freeze pass context at START and departure crew at END."""
    db.session.flush()
    evidence=dict(parent.pass_evidence or {})
    for number,_type in enumerate(pass_types(parent,kind),1):
        if (previous or {}).get(f'pass{number}_ended_at_utc'):
            after=evidence_values(parent)
            changed=any((previous or {}).get(f'pass{number}_{field}')!=after.get(f'pass{number}_{field}')
                        for field in ('surface_area','started_at_utc','ended_at_utc'))
            if kind == 'pretreat' and not changed:
                continue
        start=getattr(parent,f'pass{number}_started_at_utc')
        end=getattr(parent,f'pass{number}_ended_at_utc')
        if not start: continue
        old=evidence.get(str(number))
        # A pre-upgrade completed pass is not proof of its former staffing or
        # fluid. Require an explicit historical correction; never backfill now.
        legacy_ended = not old and (previous or {}).get(f'pass{number}_ended_at_utc')
        if legacy_ended and kind != 'pretreat':
            continue
        if legacy_ended:
            _correct_pretreat_times(operation, mission, parent, number, actor_id, correction_reason)
            continue
        captured=dict(old or _pass_evidence(operation,mission,parent,kind,number,settings))
        if not old:
            captured['context_capture']='with_end' if end else 'with_start'
            captured['context_captured_at_utc']=datetime.utcnow().isoformat()
            # A retrospectively entered END does not establish pass-start weather.
            if (previous or {}).get(f'pass{number}_started_at_utc'):
                captured['context_capture']='start_not_snapshotted'
        captured.update(start=start.isoformat(),end=end.isoformat() if end else None)
        if kind != 'pretreat' or 'surface' not in captured:
            captured['surface'] = getattr(parent, f'pass{number}_surface_area')
        evidence[str(number)]=captured
        if kind == 'pretreat':
            if (previous or {}).get(f'pass{number}_ended_at_utc'):
                _correct_pretreat_times(operation, mission, parent, number, actor_id, correction_reason)
            continue
        if not end: continue  # Departure crew becomes official only at END.
        records=ensure_planned_participants(operation,mission,parent,kind,number,settings)
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


def _correct_pretreat_times(operation, mission, parent, number, actor_id, reason):
    records = participation_query(parent, 'pretreat').filter_by(pass_number=number).with_for_update().all()
    for record in records:
        if not confirmed_participant(record):
            continue
        before = evidence_values(record)
        record.started_at_utc = getattr(parent, f'pass{number}_started_at_utc')
        record.ended_at_utc = getattr(parent, f'pass{number}_ended_at_utc')
        if before != evidence_values(record):
            audit_change(operation, 'pass_evidence_correction', actor_id=actor_id, mission=mission,
                         record=record, reason=reason, before=before, after=evidence_values(record))


def confirmed_participant(record):
    return bool(record.official_at_utc or record.gallons is not None or
                (record.started_at_utc and record.ended_at_utc))


class PretreatCorrectionRequired(NeoSubZeroEvidenceError):
    pass


def save_pretreat_participant(operation, mission, parent, values, settings, *, actor_id):
    """Save one canonical PRETREAT row. A planned row requires explicit actual confirmation."""
    from app.services.live_collaboration import version_conflict
    from app.services.neosubzero_spray import spray_gallons_amount
    from app.services.neosubzero_staffing import pretreat_crew_choices
    from app.services.neosubzero_trucks import validate_registered_truck

    number = int(values.get('pass_number') or 0)
    if number not in (1, 2):
        raise NeoSubZeroEvidenceError('Choose an applicable Pretreat pass.')
    ended = bool(getattr(parent, f'pass{number}_ended_at_utc'))
    records = participation_query(parent, 'pretreat').filter_by(pass_number=number).with_for_update().all()
    record_id = int(values.get('record_id') or 0)
    record = next((item for item in records if item.id == record_id), None)
    if record_id and (record is None or not values.get('record_version') or
                      version_conflict(record, values.get('record_version'))):
        raise NeoSubZeroEvidenceError('Participation changed. Your draft is kept; reload current values.')
    existing = [item for item in records if item.participation_selected or confirmed_participant(item)]
    if record and (record not in existing or (record.excluded_at_utc and record.gallons is None)):
        raise NeoSubZeroEvidenceError('This truck is excluded from the pass.')
    raw_truck = str(values.get('truck_number') or '').strip()
    truck = raw_truck if record and raw_truck == record.truck_number_snapshot else validate_registered_truck(
        operation, raw_truck, allow_inactive=ended)
    if not truck:
        raise NeoSubZeroEvidenceError('Select a truck.')
    if any(item is not record and item.truck_number_snapshot.casefold() == truck.casefold() for item in existing):
        raise NeoSubZeroEvidenceError('This truck already participates in this pass.')

    before = evidence_values(record)
    old_gallons = record.gallons if record else None
    gallons = spray_gallons_amount(values.get('gallons'), recorded=old_gallons) if 'gallons' in values else old_gallons
    actual = bool(record and confirmed_participant(record))
    confirm = values.get('confirm_actual') == 'yes'
    if not ended and (gallons != old_gallons or confirm):
        raise NeoSubZeroEvidenceError('Save PASS END before recording actual participation or gallons.')
    if not actual and gallons is not None and not confirm:
        raise NeoSubZeroEvidenceError('Select Record actual to save this planned truck with actual gallons.')

    concentration = record.concentration_percent_snapshot if record else None
    if number == 2 and not actual:
        concentration = 100
    if 'concentration_percent' in values:
        raw = str(values.get('concentration_percent') or '').strip()
        if number == 2 and raw != '100':
            raise NeoSubZeroEvidenceError('Type IV concentration is fixed at 100%.')
        if raw and (not raw.isascii() or not raw.isdecimal() or len(raw) > 3 or not 1 <= int(raw) <= 100):
            raise NeoSubZeroEvidenceError('Enter a whole concentration percentage from 1 to 100.')
        concentration = int(raw) if raw else None

    changes = dict(truck_number_snapshot=truck, concentration_percent_snapshot=concentration, gallons=gallons)
    crew_ids = []
    crew_choices = None
    for role in ('driver', 'flyer'):
        id_field, name_field = f'{role}_person_id', f'{role}_name_snapshot'
        old_id, old_name = getattr(record, id_field, None), getattr(record, name_field, None)
        raw_id = values.get(id_field)
        keep_recorded_name = raw_id == 'recorded' and old_id is None and old_name
        person_id = old_id if raw_id is None or keep_recorded_name else int(raw_id or 0) or None
        crew_ids.append(person_id)
        if person_id != old_id:
            if person_id and crew_choices is None:
                crew_choices = {person.id: person for person in pretreat_crew_choices(operation)}
            person = crew_choices.get(person_id) if person_id else None
            if person_id and person is None:
                raise NeoSubZeroEvidenceError(f'Select a registered deicer for {role.title()}.')
            changes.update({id_field: person_id, name_field: person.full_name if person else None})
        elif raw_id == '' and old_name and old_id is None:
            changes[name_field] = None
    if crew_ids[0] and crew_ids[0] == crew_ids[1] and {'driver_person_id', 'flyer_person_id'} & changes.keys():
        raise NeoSubZeroEvidenceError('Driver and Flyer must be different people.')

    reason = str(values.get('correction_reason') or '').strip()
    edits = {key: value for key, value in changes.items() if getattr(record, key, None) != value}
    correction = actual and any(getattr(record, key) is not None for key in edits)
    if correction and not reason and values.get('confirm_correction') != '1':
        raise PretreatCorrectionRequired('Confirm the change to recorded actual participation.')
    if record is None:
        occupied = {item.position_number for item in existing}
        slot = next((index for index in range(1, 5) if index not in occupied), None)
        if slot is None:
            raise NeoSubZeroEvidenceError('A pass supports at most four participating trucks.')
        record = next((item for item in records if item.position_number == slot), None)
        if record:
            before = evidence_values(record)
            _crew(record, None, None)
            record.excluded_at_utc = None
            record.participation_selected = True
            record.started_at_utc = record.ended_at_utc = None
        else:
            record = _new_participant(operation, mission, parent, 'pretreat', number, slot, truck, settings)
    elif not edits and not (confirm and not actual):
        return record

    if confirm and not actual:
        captured = (parent.pass_evidence or {}).get(str(number))
        if not captured:
            # The operator is supplying participation, with no inference about legacy pass context.
            captured = dict(tail=parent.tail_number, mission_id=parent.original_mission_id or mission.id,
                            flight=parent.flight_number_snapshot)
        _apply_official(record, {**captured, 'fluid': captured.get('fluid') or ''}, parent, number)
        record.pass_type = 'type_iv' if number == 2 else 'type_i'
        record.official_at_utc = datetime.utcnow()
    for key, value in changes.items():
        setattr(record, key, value)
    if not actual:
        record.reason_for_application = DEFAULT_PRETREAT_REASON
    record.crew_override = True
    record.recorded_by_user_id = actor_id
    parent.updated_at = datetime.utcnow()
    db.session.flush()
    action = ('pretreat_participation_correction' if correction else
              'pretreat_participation_actual' if confirmed_participant(record) else 'pretreat_participation_plan')
    audit_change(operation, action, actor_id=actor_id, mission=mission, record=record,
                 reason=reason, before=before, after=evidence_values(record))
    return record


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
    if kind == 'departure' and getattr(parent, 'reset_at_utc', None):
        raise NeoSubZeroEvidenceError('This initiation was reset. Reload current aircraft before changing participation.')
    if kind == 'pretreat':
        if action != 'correct_participant':
            raise NeoSubZeroEvidenceError('Use the Pretreat truck editor to record participation.')
        return save_pretreat_participant(operation, mission, parent, values, settings, actor_id=actor_id)
    from app.services.live_collaboration import entity_version,version_conflict
    number=int(values.get('pass_number') or 0)
    if number not in range(1,len(pass_types(parent,kind))+1): raise NeoSubZeroEvidenceError('Choose an applicable pass.')
    complete=bool(getattr(parent,f'pass{number}_ended_at_utc'))
    reason=str(values.get('correction_reason') or '').strip()
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
