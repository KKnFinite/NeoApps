"""Read-only submission evidence uses canonical tank, APU and movement rules."""
import hashlib
import json


def evidence(assignment, mission, work, tanks):
    def stamp(value):
        return value.isoformat() if value is not None else None
    data=dict(operation=assignment.sort_date_operation_id,mission=mission.id,
        cycle=int(assignment.current_cycle_number or 1),tail=mission.assigned_tail_number,
        work=work.id,visit=work.visit_number,sequence=work.physical_sequence_number,
        physical_cycle=work.physical_cycle_number,physical_truck=work.physical_truck_id,
        fueler=assignment.assigned_fueler_user_id,truck=assignment.assigned_truck_id,
        cycle_type=assignment.current_cycle_type,required=mission.planned_fuel_load,
        on=stamp(work.manual_on_at_utc or work.confirmed_on_at_utc or work.on_at_utc),
        off=stamp(work.off_at_utc),ended=stamp(work.ended_early_at_utc),
        hold=assignment.operational_status,review=work.physical_transfer_review,
        apu=work.apu_running,allowance=work.apu_allowance_lbs,
        automatic=work.automatic_apu_allowance_lbs,override=work.apu_override_enabled,
        override_lbs=work.apu_override_allowance_lbs,source=work.apu_source_tank_code,
        transfer=assignment.transfer_fuel_gallons,version=int(work.fueler_submission_version or 0),
        tanks=sorted((t.tank_code,t.remaining_lbs,t.actual_lbs) for t in tanks))
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def invalidate(work):
    # Keep the last actor/time/evidence and append-only audit; the version
    # revokes it even when a correction later restores the original quantities.
    if work is not None and work.fueler_submitted_at_utc is not None:
        work.fueler_submission_version=int(work.fueler_submission_version or 0)+1


def state(assignment, mission, work, tanks, *, on_trusted):
    from app.services import neoscorpion as fuel
    problem=''
    layout=fuel.tank_layout_for_tail(mission.assigned_tail_number)
    by_code={t.tank_code:t for t in tanks}
    if not assignment or not assignment.assigned_fueler_user_id:
        problem='Assign a Fueler first.'
    elif not work:
        problem='Confirm physical TRUCK ON for this assignment.'
    elif assignment.completed_at_utc or work.physical_finalized_at_utc:
        problem='This physical visit is already complete.'
    elif assignment.operational_status=='hold_review' or work.ended_early_at_utc or work.physical_transfer_review:
        problem='Resolve physical work review before COMPLETE ASSIGNMENT.'
    elif work.tail_number != mission.assigned_tail_number or (work.physical_cycle_number is not None and work.physical_cycle_number != int(assignment.current_cycle_number or 1)):
        problem='Physical visit identity changed; refresh.'
    elif not assignment.assigned_truck_id or not on_trusted:
        problem='Valid physical TRUCK ON required.'
    elif not work.off_at_utc:
        problem='Record physical TRUCK OFF first.'
    elif fuel.utc_naive(work.off_at_utc) <= fuel.utc_naive(work.manual_on_at_utc or work.confirmed_on_at_utc or work.on_at_utc):
        problem='REVIEW REQUIRED: TRUCK OFF must be after physical TRUCK ON.'
    elif mission.planned_fuel_load is None:
        problem='Required Fuel is missing.'
    elif not layout:
        problem='Aircraft tank configuration is unavailable; contact Dispatch.'
    elif any(c not in by_code or by_code[c].remaining_lbs is None for c,_ in layout):
        problem='Save Remaining: '+', '.join(label for c,label in layout if c not in by_code or by_code[c].remaining_lbs is None)+'.'
    elif any(c not in by_code or by_code[c].actual_lbs is None for c,_ in layout):
        problem='Save Actual: '+', '.join(label for c,label in layout if c not in by_code or by_code[c].actual_lbs is None)+'.'
    elif work.apu_running is None or fuel._effective_apu_allowance_lbs(work) is None or not fuel._apu_source_is_valid(work,layout):
        problem='Confirm APU status and valid source/allowance.'
    elif fuel._fuel_work_calculation(layout,by_code,work.apu_running,fuel._effective_apu_allowance_lbs(work))[-1] is None:
        problem='Complete all Actual readings and valid NEO fuel.'
    elif fuel._cycle_direction_evidence(assignment,work,tanks)=='opposite':
        problem='REVIEW REQUIRED: opposite fuel direction.'
    else:
        movement=fuel.classify_fuel_movement(assignment,work,tank_states=tanks)
        if movement=='unknown':problem='Fuel movement is unknown.'
        elif movement=='moved' and (assignment.transfer_fuel_gallons is None or assignment.transfer_fuel_gallons<=0):
            problem='Positive T/F required for fuel movement.'
        elif assignment.current_cycle_type in {'uplift','defuel'} and movement!='moved':
            problem='Confirm the required follow-up fuel movement.'
    fingerprint=evidence(assignment,mission,work,tanks) if work and assignment else None
    submitted=bool(not problem and work.fueler_submitted_at_utc and work.fueler_submission_evidence==fingerprint)
    return dict(fueler_completion_ready=not bool(problem),fueler_completion_reason=problem,
                fueler_submitted=submitted,fueler_submission_fingerprint=fingerprint)
