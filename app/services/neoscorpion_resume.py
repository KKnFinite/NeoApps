"""Explicit, audited resumption of an unfinished visit; never allocate work."""
from app.extensions import db
from app.models import NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionSortTruck, NeoScorpionSettings


def resume_same_visit(gateway,user,operation,asset,assignment,mission,work,expected_off,now,*,administrative=False):
    # The caller holds the operation, assignment and current-work locks and has
    # checked the authenticated Fueler, cycle, tail, truck and work reference.
    from app.services import neoscorpion as fuel
    if (not work or not work.manual_on_at_utc or work.physical_finalized_at_utc
            or work.ended_early_at_utc or work.fueler_submitted_at_utc
            or work.physical_transfer_review or work.continuation_review_reason
            or work.physical_truck_id != assignment.assigned_truck_id
            or work.physical_fueler_user_id != assignment.assigned_fueler_user_id
            or (not administrative and work.physical_fueler_user_id != user.id)
            or work.manual_on_truck_id != assignment.assigned_truck_id
            or work.manual_on_cycle_number != int(assignment.current_cycle_number or 1)):
        raise ValueError("This visit cannot resume: verify its resource, submission or physical review with Dispatch.")
    if NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=work.id).first():
        raise ValueError("Posted physical work cannot be resumed; use a separate visit.")
    old_value=f"off_at_utc={expected_off};"
    if work.off_at_utc is None:
        retry=NeoScorpionFuelAuditEntry.query.filter_by(fuel_work_state_id=work.id,
            action='reopen_off',field_name='resume_fueling').order_by(NeoScorpionFuelAuditEntry.id.desc()).first()
        if retry and (retry.old_value or '').startswith(old_value):
            return False,int(asset.revision),work
        raise fuel.FuelerDataConflict()
    if not expected_off or work.off_at_utc.isoformat() != str(expected_off):
        raise fuel.FuelerDataConflict()
    if now < work.off_at_utc:
        raise ValueError("Resume time cannot precede recorded physical OFF.")
    fuel._validate_nightly_truck_assignment(gateway,operation,assignment.assigned_truck_id)
    fuel._lock_physical_truck_start(assignment.assigned_truck_id,work.id)
    assignments=fuel._assignments_by_mission(operation)
    works=fuel._fuel_work_states_by_assignment_tail(assignments.values())
    selections={s.fuel_truck_id:s for s in NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id).all()}
    records,releases=fuel._fuel_start_records(operation,assignments.values(),works,fuel._parking_by_tail(operation),selections)
    record=next(r for r in records if r['id']==assignment.id)
    settings=fuel.effective_spear_settings(NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first())
    gate=fuel.truck_start_gate(record,records,releases,settings,at=now)
    if gate['reason'] in {'TRUCK STILL FUELING','TRUCK UNAVAILABLE','DEFUEL RECOVERY REVIEW','Needs Fueler','Needs Truck'}:
        raise ValueError(gate['reason'])
    # A later actual job is a real separate visit, not an accidental OFF. Future
    # preassignments without physical ON do not prevent resumption.
    for other in records:
        start=other.get('manual_on') or other.get('confirmed_on') or other.get('on')
        if (other.get('work_id') != work.id and start is not None and start <= now
                and (other['truck_id']==work.physical_truck_id or other['fueler_id']==work.physical_fueler_user_id)
                and (other['release'] is None or other['release'] > work.off_at_utc)):
            raise ValueError("Another physical job used this truck or Fueler after OFF. Confirm a separate visit; do not resume this interval.")
    result=fuel.reopen_fueler_off(gateway,user,assignment.id,
        'Fueler resumed the same unfinished physical visit after premature OFF.',now_utc=now)
    audit=result.audit_entries[0]
    audit.field_name='resume_fueling'
    audit.new_value=f'resumed_at_utc={now.isoformat()}'
    work.performance_context={**(work.performance_context or {}),
        'fueling_resume_version':int((work.performance_context or {}).get('fueling_resume_version',0))+1}
    db.session.flush()
    return True,result.revision,work


def reconcile_duplicate_visit(gateway,user,assignment_id,original_id,duplicate_id,*,reason,now_utc=None):
    """Explicit administrative repair only; never called by polling or Fueler ON.

    Preserve the duplicate as sealed, linked raw evidence rather than deleting
    it or treating its administrative retirement as a physical OFF. This narrow
    repair rejects transfers, Actuals, submissions, plans and resource changes.
    """
    from datetime import datetime
    from app.models import NeoScorpionFuelWorkState,NeoScorpionPendingUplift
    from app.services import neoscorpion as fuel
    if not reason or not reason.strip():raise ValueError('An explicit repair reason is required.')
    operation=fuel.current_sort_operation(gateway)
    if not operation:raise ValueError('No current operation.')
    operation,asset=fuel.lock_nightly_asset_scope_for_mutation(operation)
    assignment,mission=fuel._locked_current_fuel_assignment(operation,assignment_id,action_label='RECONCILE DUPLICATE VISIT')
    works=NeoScorpionFuelWorkState.query.filter(NeoScorpionFuelWorkState.fuel_assignment_id==assignment.id,
        NeoScorpionFuelWorkState.id.in_((original_id,duplicate_id))).order_by(NeoScorpionFuelWorkState.id).with_for_update().populate_existing().all()
    by_id={w.id:w for w in works};original=by_id.get(original_id);duplicate=by_id.get(duplicate_id)
    if original is None or duplicate is None or original_id==duplicate_id:raise ValueError('Exact original and duplicate records are required.')
    if (duplicate.performance_context or {}).get('duplicate_of_work_id')==original_id and assignment.active_work_state_id==original_id:
        return original  # One approved repair, never replay physical actions.
    if (assignment.active_work_state_id!=duplicate_id or assignment.completed_at_utc
            or assignment.fuel_on_board_at_utc or assignment.current_cycle_type!='fuel'
            or not original.off_at_utc or not original.manual_on_at_utc or not duplicate.manual_on_at_utc
            or not 0 <= (duplicate.manual_on_at_utc-original.off_at_utc).total_seconds() <= 60):
        raise ValueError('This is not the approved short accidental-OFF duplicate sequence.')
    for work in works:
        if (work.tail_number!=mission.assigned_tail_number or work.physical_cycle_number!=assignment.current_cycle_number
                or work.physical_truck_id!=assignment.assigned_truck_id or work.physical_fueler_user_id!=assignment.assigned_fueler_user_id
                or work.fueler_submitted_at_utc or work.physical_finalized_at_utc or work.ended_early_at_utc
                or work.physical_transfer_gallons not in (None,0) or work.physical_transfer_review or work.continuation_review_reason
                or any(t.actual_lbs is not None for t in work.tank_states)):
            raise ValueError('New fuel evidence, review, completion or resource conflict requires separate review; nothing was merged.')
    if assignment.transfer_fuel_gallons not in (None,0) or NeoScorpionFuelingEvent.query.filter(NeoScorpionFuelingEvent.fuel_work_state_id.in_((original_id,duplicate_id))).first():
        raise ValueError('A transfer exists; inventory must not be reconciled through this repair.')
    if NeoScorpionPendingUplift.query.filter(NeoScorpionPendingUplift.fuel_assignment_id==assignment.id,
            NeoScorpionPendingUplift.status.in_(('pending','review','promoted'))).first():
        raise ValueError('An independent planned/continuation job requires review.')
    link=NeoScorpionFuelAuditEntry.query.filter_by(fuel_assignment_id=assignment.id,field_name='physical_visit',
        old_value=str(original_id),new_value=str(duplicate_id)).first()
    if not link:raise ValueError('No authoritative creation link proves this duplicate relationship.')
    if NeoScorpionFuelAuditEntry.query.filter(NeoScorpionFuelAuditEntry.fuel_assignment_id==assignment.id,
            NeoScorpionFuelAuditEntry.action.in_(('swap_truck','swap_fueler')),
            NeoScorpionFuelAuditEntry.created_at>original.manual_on_at_utc).first():
        raise ValueError('Resource changes after physical ON require separate review.')
    now=now_utc or datetime.utcnow()
    fields=('apu_running','apu_confirmed_at_utc','apu_allowance_lbs','automatic_apu_allowance_lbs',
            'apu_override_allowance_lbs','applied_apu_rate_thousand_lbs_per_hour','apu_source_tank_code','notes')
    for name in fields:
        old,new=getattr(original,name),getattr(duplicate,name)
        if old is not None and new is not None and old!=new:
            raise ValueError('Conflicting '+name+'; nothing was merged.')
    if original.apu_override_enabled!=duplicate.apu_override_enabled:raise ValueError('Conflicting APU override evidence.')
    tanks={t.tank_code:t for t in original.tank_states}
    for tank in duplicate.tank_states:
        old=tanks.get(tank.tank_code)
        if old and old.remaining_lbs is not None and tank.remaining_lbs is not None and old.remaining_lbs!=tank.remaining_lbs:
            raise ValueError('Conflicting Remaining '+tank.tank_code+'; nothing was merged.')
    snapshot=lambda w: dict(id=w.id,on=w.manual_on_at_utc.isoformat(),off=w.off_at_utc.isoformat() if w.off_at_utc else None,
        tanks=[dict(code=t.tank_code,remaining=t.remaining_lbs,actual=t.actual_lbs) for t in w.tank_states],
        fields={name:str(getattr(w,name)) if getattr(w,name) is not None else None for name in fields})
    import json
    before=json.dumps([snapshot(original),snapshot(duplicate)],sort_keys=True)
    for name in fields:
        if getattr(original,name) is None and getattr(duplicate,name) is not None:setattr(original,name,getattr(duplicate,name))
    from app.models import NeoScorpionFuelTankState
    for tank in duplicate.tank_states:
        old=tanks.get(tank.tank_code)
        if old is None:
            old=NeoScorpionFuelTankState(tank_code=tank.tank_code);original.tank_states.append(old)
        if old.remaining_lbs is None:old.remaining_lbs=tank.remaining_lbs
    duplicate.performance_context={**(duplicate.performance_context or {}),'duplicate_of_work_id':original_id}
    duplicate.ended_early_at_utc=now;duplicate.ended_early_by_user_id=user.id
    duplicate.ended_early_reason=f'Administrative duplicate reconciliation to visit {original_id}; not a separate physical OFF. '+reason
    duplicate.physical_finalized_at_utc=now;duplicate.physical_finalized_by_user_id=user.id
    assignment.active_work_state_id=original_id
    assignment.transfer_fuel_gallons=original.physical_transfer_gallons
    fuel._new_fuel_audit(operation,assignment,user,'segment_transfer','duplicate_visit_reconciliation',before,
        json.dumps(dict(original_id=original_id,duplicate_id=duplicate_id)),reason,fuel_work_state=original,now_utc=now)
    db.session.flush()
    # The approved repair re-establishes the already-recorded continuous ON;
    # it never claims a new physical arrival or replaces the original ON.
    stamp=original.off_at_utc.isoformat()
    resume_same_visit(gateway,user,operation,asset,assignment,mission,original,stamp,now,administrative=True)
    return original
