"""Queued uplift planning and guarded, savepoint-isolated canonical rollover."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import (NeoScorpionPendingUplift,NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,
    NeoScorpionFuelingEvent,NeoScorpionSortTruck,NeoScorpionFuelTruck,NeoScorpionSettings)

OUTSTANDING=('pending','review')

class PendingUpliftConflict(ValueError):
    pass


def outstanding(assignment_id, *, lock=False):
    query=NeoScorpionPendingUplift.query.filter_by(fuel_assignment_id=assignment_id).filter(NeoScorpionPendingUplift.status.in_(OUTSTANDING))
    if lock:query=query.with_for_update().populate_existing()
    return query.first()


def _audit(op,a,p,user,action,now,old=None):
    from app.services import neoscorpion as fuel
    import json
    fuel._new_fuel_audit(op,a,user,'segment_transfer','pending_uplift',old,
        json.dumps(dict(id=p.id,identity=p.pending_cycle_identity,status=p.status,version=p.version,
            parent_cycle=p.parent_cycle_number,parent_work=p.parent_work_state_id,tail=p.tail_number,
            required=p.required_fuel_lbs,fueler=p.proposed_fueler_user_id,truck=p.proposed_truck_id,
            promoted_cycle=p.promoted_cycle_number,promoted_work=p.promoted_work_state_id,reason=p.review_reason),sort_keys=True),
        action,now_utc=now)


def _planning_resources(gateway,operation,fueler_id,truck_id):
    from app.services import neoscorpion as fuel
    if fueler_id is not None:fuel._validate_nightly_fueler_assignment(gateway,operation,fueler_id)
    if truck_id is not None:
        selected=db.session.query(NeoScorpionSortTruck.id).join(NeoScorpionFuelTruck,
            NeoScorpionFuelTruck.id==NeoScorpionSortTruck.fuel_truck_id).filter(
            NeoScorpionSortTruck.sort_date_operation_id==operation.id,NeoScorpionSortTruck.fuel_truck_id==truck_id,
            NeoScorpionFuelTruck.gateway_id==gateway.id).first()
        if not selected:raise ValueError('Select a nightly truck for this gateway.')


def save_pending_uplift(gateway,user,form,*,now_utc=None):
    from app.services import neoscorpion as fuel
    operation=fuel.current_sort_operation(gateway)
    if operation is None:raise ValueError('No current sort operation.')
    operation,asset=fuel.lock_nightly_asset_scope_for_mutation(operation)
    action=form.get('pending_action','save')
    if action=='create' and not form.get('assignment_id'):
        mission=fuel._departure_mission_for_operation(operation,fuel._int_or_none(form.get('mission_id')),for_update=True)
        if mission is None:raise ValueError('Current departure mission not found.')
        if mission.fuel_status=='complete':raise ValueError('This mission is already complete.')
        assignment=fuel.ensure_fuel_assignment(operation,mission)
    else:
        assignment,mission=fuel._locked_current_fuel_assignment(operation,form.get('assignment_id'),action_label='PENDING UPLIFT')
    now=now_utc or datetime.utcnow()
    p=outstanding(assignment.id,lock=True)
    if action=='create':
        fuel._validate_cycle_request(assignment,mission,form.get('expected_cycle'),form.get('expected_tail'))
        if form.get('expected_cycle') is None or form.get('expected_tail') is None:raise PendingUpliftConflict('Refresh the current cycle first.')
        if assignment.completed_at_utc or assignment.fuel_on_board_at_utc:raise ValueError('Add pending uplift while the current cycle is unfinished.')
        if not fuel._normalize_tail(mission.assigned_tail_number):raise ValueError('Confirm the aircraft tail first.')
        if p:
            if p.parent_cycle_number!=int(assignment.current_cycle_number or 1) or p.tail_number!=fuel._normalize_tail(mission.assigned_tail_number):
                raise PendingUpliftConflict('Resolve the existing pending uplift before creating another.')
            return SimpleNamespace(changed=False,revision=int(asset.revision),pending=p)
        if not mission.planned_fuel_load or mission.planned_fuel_load<=0:raise ValueError('Enter valid current Required Fuel before adding pending uplift.')
        p=NeoScorpionPendingUplift(sort_date_operation_id=operation.id,mission_id=mission.id,fuel_assignment_id=assignment.id,
            parent_cycle_number=int(assignment.current_cycle_number or 1),parent_work_state_id=assignment.active_work_state_id,
            tail_number=fuel._normalize_tail(mission.assigned_tail_number),required_fuel_lbs=mission.planned_fuel_load,
            proposed_fueler_user_id=assignment.assigned_fueler_user_id,proposed_truck_id=assignment.assigned_truck_id,
            created_by_user_id=user.id,updated_by_user_id=user.id,created_at_utc=now,updated_at_utc=now,version=1,status='pending')
        db.session.add(p);db.session.flush()
    else:
        if not p or str(p.id)!=str(form.get('pending_id')) or str(p.version)!=str(form.get('expected_version')):
            raise PendingUpliftConflict('Pending uplift changed. Refresh and review its current values.')
        if action=='cancel':
            p.status='cancelled';p.review_reason=None
        elif action=='save':
            target=fuel.display_thousands_to_lbs(form.get('required_fuel'))
            if target is None or target<=0:raise ValueError('Enter a positive TOTAL Required Fuel target.')
            fueler_id=fuel._int_or_none(form.get('proposed_fueler_user_id'));truck_id=fuel._int_or_none(form.get('proposed_truck_id'))
            _planning_resources(gateway,operation,fueler_id,truck_id)
            p.required_fuel_lbs=target;p.proposed_fueler_user_id=fueler_id;p.proposed_truck_id=truck_id
        elif action=='retry':
            if assignment.completed_at_utc is None:raise ValueError('Dispatcher must COMPLETE the parent cycle first.')
            _attempt_promotion(gateway,user,operation,assignment,mission,p,now)
            asset=fuel.record_nightly_operational_change(asset,operation.id);db.session.flush()
            return SimpleNamespace(changed=True,revision=int(asset.revision),pending=p)
        else:raise ValueError('Select a valid pending uplift action.')
        p.version+=1;p.updated_by_user_id=user.id;p.updated_at_utc=now
    _audit(operation,assignment,p,user,'Dispatcher '+action+' pending uplift planning only.',now)
    asset=fuel.record_nightly_operational_change(asset,operation.id);db.session.flush()
    return SimpleNamespace(changed=True,revision=int(asset.revision),pending=p)


def _promote(gateway,user,operation,assignment,mission,p,now):
    from app.services import neoscorpion as fuel
    if p.sort_date_operation_id!=operation.id or p.mission_id!=mission.id or p.fuel_assignment_id!=assignment.id:
        raise ValueError('Pending uplift mission/operation identity changed.')
    if p.parent_cycle_number!=int(assignment.current_cycle_number or 1):raise ValueError('Parent fuel cycle changed; review the saved pending uplift.')
    if p.tail_number!=fuel._normalize_tail(mission.assigned_tail_number) or p.tail_number!=fuel._normalize_tail(assignment.confirmed_tail_number):
        raise ValueError('Aircraft tail changed. Old-tail measurements cannot seed this uplift; cancel and prepare against verified current-tail work.')
    if assignment.completed_at_utc is None or assignment.fuel_on_board_at_utc or mission.fuel_status!='complete':
        raise ValueError('The parent physical cycle is not Dispatcher COMPLETE.')
    if assignment.operational_status=='hold_review':raise ValueError('Resolve the operational hold first.')
    work=fuel.current_work(assignment,p.tail_number,lock=True)
    if not work or not work.physical_finalized_at_utc or not work.off_at_utc or work.physical_transfer_review or work.ended_early_at_utc:
        raise ValueError('Trusted finalized physical work and TRUCK OFF are required.')
    if p.parent_work_state_id is not None and p.parent_work_state_id!=work.id:
        raise ValueError('The parent physical visit changed; review the saved pending uplift identity.')
    layout=fuel.tank_layout_for_tail(p.tail_number)
    tanks=fuel._locked_tank_states(work);actual={t.tank_code:t.actual_lbs for t in tanks}
    if not layout or any(code not in actual or actual[code] is None for code,_ in layout):
        raise ValueError('Verified final Actual readings are missing for this aircraft layout.')
    actual={code:actual[code] for code,_ in layout}
    if p.required_fuel_lbs<=sum(actual.values()):raise ValueError('Verified final fuel already meets or exceeds the pending TOTAL target; review required.')
    parent_events=NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=work.id,cycle_number=p.parent_cycle_number).all()
    if not parent_events:raise ValueError('A completed physical fueling event is required before uplift.')
    if NeoScorpionFuelingEvent.query.filter(NeoScorpionFuelingEvent.sort_date_operation_id==operation.id,
            NeoScorpionFuelingEvent.tail_number==p.tail_number,NeoScorpionFuelingEvent.fuel_work_state_id!=work.id,
            NeoScorpionFuelingEvent.ended_at_utc>work.off_at_utc).first():
        raise ValueError('Newer physical work exists for this tail; verify current measurements before uplift.')
    other=NeoScorpionFuelWorkState.query.filter(NeoScorpionFuelWorkState.fuel_assignment_id==assignment.id,
        NeoScorpionFuelWorkState.id!=work.id,NeoScorpionFuelWorkState.physical_finalized_at_utc.is_(None)).all()
    if any(w.manual_on_at_utc or w.confirmed_on_at_utc or w.off_at_utc or w.physical_transfer_review for w in other):
        raise ValueError('Outstanding prior physical segment requires review before uplift.')
    _planning_resources(gateway,operation,p.proposed_fueler_user_id,p.proposed_truck_id)
    if p.proposed_truck_id is not None:
        fuel._validate_nightly_truck_assignment(gateway,operation,p.proposed_truck_id)
        selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=p.proposed_truck_id).with_for_update().one()
        if not selection.inventory_verified or selection.current_gallons is None:
            raise ValueError('Proposed truck inventory is not verified.')
        from app.services.neoscorpion_dispatch_planning import estimate_fuel_demand_gallons
        settings=NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
        density=settings.fuel_density_lbs_per_gallon if settings else fuel.DEFAULT_FUEL_DENSITY_LBS_PER_GALLON
        demand=estimate_fuel_demand_gallons(p.required_fuel_lbs,None,density,measured_fob_lbs=sum(actual.values())).gallons
        reserve=fuel.effective_spear_settings(settings).minimum_truck_reserve_gallons
        if demand is None or selection.current_gallons-demand<reserve:
            raise ValueError('Proposed truck lacks the estimated uplift fuel and configured reserve; top off or select another truck.')

    assignments=fuel._assignments_by_mission(operation)
    rows=fuel._fuel_rows(operation,fuel._departure_missions(operation),assignments_by_mission=assignments)
    owners_fueler,owners_truck=fuel._current_resource_owners(rows)
    if p.proposed_fueler_user_id is not None and owners_fueler.get(p.proposed_fueler_user_id):
        raise ValueError('Proposed Fueler is assigned to other active work.')
    if p.proposed_truck_id is not None and owners_truck.get(p.proposed_truck_id):
        raise ValueError('Proposed Truck is assigned to other active work.')
    result=fuel.start_follow_up_fuel_cycle(gateway,user,assignment.id,'uplift',str(Decimal(p.required_fuel_lbs)/Decimal(1000)),
        None,None,expected_cycle=p.parent_cycle_number,expected_tail=p.tail_number,now_utc=now,pending_uplift_id=p.id)
    # Replace generic latest-tail carry-forward with the exact verified parent visit.
    fresh=result.fuel_work_state;by_code={t.tank_code:t for t in fresh.tank_states}
    from app.models import NeoScorpionFuelTankState
    for code,value in actual.items():
        tank=by_code.get(code)
        if tank is None:tank=NeoScorpionFuelTankState(tank_code=code);fresh.tank_states.append(tank)
        tank.remaining_lbs=value;tank.actual_lbs=None
    assignment.assigned_fueler_user_id=p.proposed_fueler_user_id
    assignment.assigned_truck_id=p.proposed_truck_id
    assignment.fueler_assigned_at_utc=now if p.proposed_fueler_user_id else None
    assignment.truck_assigned_at_utc=now if p.proposed_truck_id else None
    assignment.resource_assignment_by_user_id=user.id
    fresh.physical_fueler_user_id=p.proposed_fueler_user_id;fresh.physical_truck_id=p.proposed_truck_id
    fresh.notes=None
    tail=fuel._tail_fuel_states_by_tail(operation).get(p.tail_number)
    if tail is not None:tail.fob_lbs=sum(actual.values())
    p.parent_work_state_id=work.id;p.status='promoted';p.promoted_cycle_number=int(assignment.current_cycle_number)
    p.promoted_work_state_id=fresh.id
    p.review_reason=None;p.version+=1;p.updated_by_user_id=user.id;p.updated_at_utc=now
    _audit(operation,assignment,p,user,'Promoted pending uplift after Dispatcher COMPLETE.',now)
    db.session.flush()


def _attempt_promotion(gateway,user,operation,assignment,mission,p,now):
    # Flush the genuine completed event/inventory before the follow-up savepoint.
    # Failure here can only roll back the proposed cycle, never its parent event.
    db.session.flush()
    try:
        with db.session.begin_nested():_promote(gateway,user,operation,assignment,mission,p,now)
    except Exception as exc:
        from flask import current_app
        if not isinstance(exc,ValueError):current_app.logger.exception('Pending uplift %s promotion failed',p.id)
        p.status='review';p.review_reason=str(exc) if isinstance(exc,ValueError) else 'Promotion failed ('+type(exc).__name__+'); Dispatcher review required.'
        p.version+=1;p.updated_by_user_id=user.id;p.updated_at_utc=now
        _audit(operation,assignment,p,user,'Pending uplift retained for review; completed parent preserved.',now)
        db.session.flush()


def promote_after_completion(gateway,user,operation,assignment,mission,now):
    p=outstanding(assignment.id,lock=True)
    if p is not None:_attempt_promotion(gateway,user,operation,assignment,mission,p,now)


def attach_pending(context):
    from app.services import neoscorpion as fuel
    operation=context.get('operation')
    if operation is None:return context
    pending=NeoScorpionPendingUplift.query.filter_by(sort_date_operation_id=operation.id).filter(
        NeoScorpionPendingUplift.status.in_(OUTSTANDING)).options(joinedload(NeoScorpionPendingUplift.proposed_truck),
        joinedload(NeoScorpionPendingUplift.proposed_fueler)).all()
    by_mission={p.mission_id:p for p in pending}
    selections={r['selection'].fuel_truck_id:r['selection'] for r in context.get('nightly_trucks',[])}
    fuelers,trucks=fuel._current_resource_owners(context['rows'])
    for row in context['rows']:
        p=by_mission.get(row['mission'].id);row['pending_uplift']=p;row['pending_uplift_warning']=''
        if p:
            row['pending_uplift_target_input']=format(Decimal(p.required_fuel_lbs)/Decimal(1000),'.3f').rstrip('0').rstrip('.')
            messages=[]
            if fuelers.get(p.proposed_fueler_user_id):messages.append('Fueler currently committed; rechecked at promotion')
            if trucks.get(p.proposed_truck_id):messages.append('Truck currently committed; rechecked at promotion')
            if p.proposed_fueler_user_id and p.proposed_fueler_user_id not in context.get('nightly_assignment_fueler_ids',()):messages.append('Review Fueler nightly eligibility')
            selection=selections.get(p.proposed_truck_id)
            if p.proposed_truck_id and selection is None:messages.append('Truck no longer selected for this sort')
            if p.proposed_truck_id and selection and selection.status!='available':messages.append('Truck '+selection.status.replace('_',' '))
            if p.proposed_truck and (not p.proposed_truck.is_active or p.proposed_truck.is_out_of_service):messages.append('Truck inactive or OOS')
            row['pending_uplift_warning']='; '.join(messages)
    return context
