"""Queued uplift planning and guarded, savepoint-isolated canonical rollover."""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import (NeoScorpionPendingUplift,NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,
    NeoScorpionFuelingEvent,NeoScorpionSortTruck,NeoScorpionFuelTruck,NeoScorpionSettings)

OUTSTANDING=('pending','review')
CONTINUATIONS=('continuation','increase')


def plan_continuation(gateway,user,form,*,now_utc=None):
    """Plan a resource, never a physical occupation or approved transfer."""
    from app.services import neoscorpion as fuel
    op=fuel.current_sort_operation(gateway)
    if op is None:raise ValueError('No current sort operation.')
    op,asset=fuel.lock_nightly_asset_scope_for_mutation(op)
    a,m=fuel._locked_current_fuel_assignment(op,form.get('assignment_id'),action_label='PLAN ANOTHER TRUCK')
    if form.get('expected_cycle') is None or form.get('expected_tail') is None:
        raise PendingUpliftConflict('Refresh the current aircraft and cycle before planning a truck.')
    fuel._validate_cycle_request(a,m,form.get('expected_cycle'),form.get('expected_tail'))
    if a.completed_at_utc or a.fuel_on_board_at_utc:raise ValueError('Use START UPLIFT for an already completed aircraft.')
    if a.current_cycle_type=='defuel':raise ValueError('Complete/review DEFUEL independently; do not plan an uplift continuation.')
    now=now_utc or datetime.utcnow()
    action=form.get('pending_action','create')
    queue=outstanding_plans(a.id,lock=True)
    if any(p.plan_kind not in CONTINUATIONS for p in queue):raise ValueError('Review the existing legacy pending uplift separately.')
    p=next((p for p in queue if str(p.id)==str(form.get('pending_id'))),None)
    if action=='create' and not form.get('pending_id'):
        if any(p.tail_number!=m.assigned_tail_number or p.parent_cycle_number!=a.current_cycle_number for p in queue):
            raise PendingUpliftConflict('Review/cancel the earlier aircraft or cycle plans before adding another truck.')
        # Compare the displayed queue tail under the operation lock. A repeated
        # or concurrent stale click cannot manufacture another planned visit.
        if str(form.get('expected_last_plan_ref') or '')!=str(queue[-1].id if queue else ''):
            raise PendingUpliftConflict('Planned truck queue changed; refresh before adding another truck.')
    elif p is None:
        raise PendingUpliftConflict('Planned truck changed; refresh before editing it.')
    if p and (str(form.get('pending_id'))!=str(p.id) or str(form.get('expected_version'))!=str(p.version)):
        raise PendingUpliftConflict('Planned truck changed; refresh before editing it.')
    if p and action!='cancel' and (p.tail_number!=m.assigned_tail_number or p.parent_cycle_number!=a.current_cycle_number):
        raise ValueError('Physical aircraft/visit changed. Cancel the old plan and explicitly plan the replacement; no readings will be transferred.')
    if p is None:
        if action!='create':raise PendingUpliftConflict('Planned truck changed; refresh.')
        if str(form.get('expected_work_ref'))!=str(a.active_work_state_id or ''):
            raise PendingUpliftConflict('Active physical visit changed; refresh before planning its next truck.')
        work=fuel.current_work(a,fuel._normalize_tail(m.assigned_tail_number),lock=True)
        if work is None:
            work=fuel.new_work(a,fuel._normalize_tail(m.assigned_tail_number),user,now)
            work.physical_required_fuel_lbs=m.planned_fuel_load
        if not m.planned_fuel_load:raise ValueError('Enter Required Fuel first.')
        p=NeoScorpionPendingUplift(sort_date_operation_id=op.id,mission_id=m.id,fuel_assignment_id=a.id,
            parent_cycle_number=int(a.current_cycle_number or 1),parent_work_state_id=work.id,
            tail_number=m.assigned_tail_number,required_fuel_lbs=m.planned_fuel_load,
            plan_kind='continuation',created_by_user_id=user.id,updated_by_user_id=user.id,
            created_at_utc=now,updated_at_utc=now)
        db.session.add(p);db.session.flush()
    if action=='cancel':
        p.status='cancelled';p.review_reason=None
    elif action in {'create','save','activate'}:
        truck=fuel._int_or_none(form.get('proposed_truck_id'))
        _planning_resources(gateway,op,a.assigned_fueler_user_id,truck)
        if truck is None:raise ValueError('Select the next truck.')
        p.proposed_truck_id=truck;p.proposed_fueler_user_id=a.assigned_fueler_user_id
        p.required_fuel_lbs=m.planned_fuel_load
        if p.status!='review':p.status='pending'
    else:raise ValueError('Select a valid planned-truck action.')
    p.version+=1;p.updated_at_utc=now;p.updated_by_user_id=user.id
    _audit(op,a,p,user,'Dispatcher '+action+' planned truck continuation.',now)
    activate_after_submission(gateway,user,op,a,m,now)
    reconcile_completion(op,a,m,user,now)
    asset=fuel.record_nightly_operational_change(asset,op.id);db.session.flush()
    return SimpleNamespace(changed=True,revision=int(asset.revision),pending=p)


def target_changed(gateway,user,op,a,m,old,target,now):
    """Called under the common mutation lock by every required-fuel editor."""
    from app.services import neoscorpion as fuel
    from app.services.neoscorpion_submission import invalidate
    work=fuel.current_work(a,fuel._normalize_tail(m.assigned_tail_number),lock=True) if a else None
    queue=outstanding_plans(a.id,lock=True) if a else []
    p=queue[0] if queue else None
    measured=bool(work and any(t.actual_lbs is not None for t in work.tank_states))
    if (p or (measured and target is not None and old is not None and target>old)) and not getattr(user,'id',None):
        raise ValueError('An authenticated Dispatcher is required to plan changed measured work.')
    if target is not None and old is not None and target>old and measured:
        if a.current_cycle_type=='defuel':raise ValueError('Review the DEFUEL target/direction separately before increasing fuel.')
        if p and p.plan_kind not in CONTINUATIONS:raise ValueError('Resolve the existing pending uplift before increasing this measured visit target.')
        work.physical_required_fuel_lbs=work.physical_required_fuel_lbs if work.target_frozen else old
        work.target_frozen=True
        if p is None:
            p=NeoScorpionPendingUplift(sort_date_operation_id=op.id,mission_id=m.id,fuel_assignment_id=a.id,
                parent_cycle_number=int(a.current_cycle_number or 1),parent_work_state_id=work.id,tail_number=work.tail_number,
                required_fuel_lbs=target,plan_kind='increase',proposed_fueler_user_id=a.assigned_fueler_user_id,
                created_by_user_id=user.id,updated_by_user_id=user.id,created_at_utc=now,updated_at_utc=now)
            db.session.add(p);db.session.flush()
    elif work and not work.target_frozen:
        invalidate(work);work.physical_required_fuel_lbs=target
    m.planned_fuel_load=target;m.planned_fuel_updated_at=now
    if work and not p and target is not None and old is not None and target<old:
        handoff=NeoScorpionPendingUplift.query.filter_by(promoted_work_state_id=work.id,status='promoted').filter(
            NeoScorpionPendingUplift.plan_kind.in_(CONTINUATIONS)).with_for_update().one_or_none()
        if handoff and (handoff.handoff_snapshot or {}).get('neo') is not None and handoff.handoff_snapshot['neo']>=target:
            if not getattr(user,'id',None):raise ValueError('An authenticated Dispatcher is required to review a changed handoff.')
            work.continuation_review_reason='NEXT TRUCK NOT REQUIRED — DISPATCHER REVIEW: reduced departure target is met by the submitted source.'
            handoff.version+=1;handoff.updated_at_utc=now;handoff.updated_by_user_id=user.id
            _audit(op,a,handoff,user,'Reduced target requires explicit next-truck review; no visit was cancelled.',now)
    if p and p not in queue:queue.append(p)
    for p in queue:
        if p.plan_kind not in CONTINUATIONS:continue
        if target is None or target<=0:raise ValueError('An outstanding truck plan requires a positive departure target.')
        p.required_fuel_lbs=target;p.version+=1;p.updated_at_utc=now;p.updated_by_user_id=user.id
        _audit(op,a,p,user,'Reconciled changed requirement with the existing planned visit.',now)
        if work:
            net=fuel._fuel_work_calculation(fuel.tank_layout_for_tail(work.tail_number),
                {t.tank_code:t for t in work.tank_states},work.apu_running,fuel._effective_apu_allowance_lbs(work))[4]
            if net is not None and net>=target:
                p.status='review';p.review_reason='PLANNED TRUCK NOT REQUIRED — DISPATCHER REVIEW'
    if queue:activate_after_submission(gateway,user,op,a,m,now)


def activate_after_submission(gateway,user,op,a,m,now):
    """One submitted baseline → one new preparing Work; no fuel posting."""
    from app.services import neoscorpion as fuel
    from app.services.neoscorpion_submission import evidence
    from app.models import NeoScorpionFuelTankState
    p=outstanding(a.id,lock=True)
    if p is None or p.plan_kind not in CONTINUATIONS:return
    work=db.session.get(NeoScorpionFuelWorkState,p.parent_work_state_id)
    if not work or not work.fueler_submitted_at_utc:return
    reason=None
    if (a.active_work_state_id!=work.id or p.tail_number!=m.assigned_tail_number
            or p.parent_cycle_number!=a.current_cycle_number):reason='Aircraft or physical visit changed; review the planned truck identity.'
    view,mission=fuel._segment_views(a,m,work)
    tanks=fuel._locked_tank_states(work)
    if work.fueler_submission_evidence!=evidence(view,mission,work,tanks):return
    neo=fuel._fuel_work_calculation(fuel.tank_layout_for_tail(work.tail_number),{t.tank_code:t for t in tanks},
        work.apu_running,fuel._effective_apu_allowance_lbs(work))[4]
    if reason is None and (neo is None or neo>=p.required_fuel_lbs):reason='PLANNED TRUCK NOT REQUIRED — DISPATCHER REVIEW'
    if reason is None and not p.proposed_truck_id:reason='SELECT NEXT TRUCK — DISPATCHER REVIEW'
    if reason:
        p.status='review';p.review_reason=reason;p.version+=1
        _audit(op,a,p,user,'Submitted visit retained; planned truck requires Dispatcher review.',now)
        return
    try:
        _planning_resources(gateway,op,view.assigned_fueler_user_id,p.proposed_truck_id)
    except ValueError as exc:
        p.status='review';p.review_reason='NEXT TRUCK NEEDS REVIEW — '+str(exc);p.version+=1
        _audit(op,a,p,user,'Valid Fueler submission retained; planned resources need review.',now)
        return
    work.target_frozen=True
    snapshot=dict(parent_work=work.id,submission_version=work.fueler_submission_version,
        submission_evidence=work.fueler_submission_evidence,submitted_at=work.fueler_submitted_at_utc.isoformat(),
        required=work.physical_required_fuel_lbs,actual={t.tank_code:t.actual_lbs for t in tanks},neo=neo)
    previous_truck=a.assigned_truck_id
    a.assigned_truck_id=p.proposed_truck_id;a.assigned_fueler_user_id=view.assigned_fueler_user_id
    a.truck_assigned_at_utc=now;a.fueler_assigned_at_utc=now;a.resource_assignment_by_user_id=user.id
    fresh=fuel.new_work(a,work.tail_number,user,now);fresh.physical_required_fuel_lbs=p.required_fuel_lbs
    fuel._new_fuel_audit(op,a,user,'segment_transfer','assigned_truck_id',previous_truck,p.proposed_truck_id,
        'Activated Dispatcher-planned truck from validated source submission.',fuel_work_state=fresh,now_utc=now)
    for code,value in snapshot['actual'].items():fresh.tank_states.append(NeoScorpionFuelTankState(tank_code=code,remaining_lbs=value))
    a.completed_at_utc=None;a.completed_by_user_id=None;a.review_status='pending';m.fuel_status='assigned';m.fuel_completed_at_utc=None
    p.handoff_snapshot=snapshot;p.status='promoted';p.promoted_work_state_id=fresh.id
    p.promoted_cycle_number=a.current_cycle_number;p.review_reason=None;p.version+=1;p.updated_at_utc=now;p.updated_by_user_id=user.id
    _audit(op,a,p,user,'Activated next physical visit from Fueler submission; prior Dispatcher verification remains outstanding.',now)
    # Only the immediate successor is activated. Every later plan now waits on
    # this fresh visit's own validated receipt, never the original receipt.
    for later in outstanding_plans(a.id,lock=True):
        if later.plan_kind in CONTINUATIONS and later.tail_number==work.tail_number and later.parent_cycle_number==a.current_cycle_number:
            later.parent_work_state_id=fresh.id;later.version+=1;later.updated_at_utc=now
            _audit(op,a,later,user,'Queued truck now awaits the preceding physical visit submission.',now)
    truck=db.session.get(NeoScorpionFuelTruck,p.proposed_truck_id)
    fuel._record_assigned_fueler_update(a,(f'NEXT TRUCK {truck.truck_number} ACTIVATED · fresh ON/OFF/APU/Actual/T/F required.',),now_utc=now)
    db.session.flush()


def reconcile_completion(op,a,m,user,now):
    """A verified last visit cannot conceal unverified source work/plans."""
    plans=NeoScorpionPendingUplift.query.filter_by(fuel_assignment_id=a.id).filter(NeoScorpionPendingUplift.plan_kind.in_(CONTINUATIONS)).all()
    if not plans:return
    works=NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=a.id).all()
    current=next((w for w in works if w.id==a.active_work_state_id),None)
    pending=any(p.status in OUTSTANDING for p in plans)
    unresolved=any(not w.physical_finalized_at_utc and (w.manual_on_at_utc or w.fueler_submitted_at_utc or w.continuation_review_reason) for w in works)
    complete=bool(current and current.physical_finalized_at_utc and not pending and not unresolved)
    completed_at=(a.completed_at_utc or now) if complete else None
    a.completed_at_utc=completed_at;a.completed_by_user_id=(a.completed_by_user_id or user.id) if complete else None
    a.review_status='complete' if complete else 'pending';m.fuel_status='complete' if complete else 'assigned';m.fuel_completed_at_utc=completed_at


def review_handoff(gateway,user,form,*,now_utc=None):
    from app.services import neoscorpion as fuel
    from app.services.neoscorpion_submission import evidence,invalidate
    op=fuel.current_sort_operation(gateway)
    if not op:raise ValueError('No current sort operation.')
    op,asset=fuel.lock_nightly_asset_scope_for_mutation(op)
    a,m=fuel._locked_current_fuel_assignment(op,form.get('assignment_id'),action_label='REVIEW HANDOFF')
    p=NeoScorpionPendingUplift.query.filter_by(id=fuel._int_or_none(form.get('pending_id')),
        fuel_assignment_id=a.id,sort_date_operation_id=op.id,status='promoted').with_for_update().one_or_none()
    if not p or p.plan_kind not in CONTINUATIONS or str(p.version)!=str(form.get('expected_version')):
        raise PendingUpliftConflict('Handoff review changed; refresh.')
    reason=(form.get('reason') or '').strip()
    if not reason:raise ValueError('Enter the physical evidence/review reason.')
    source=db.session.get(NeoScorpionFuelWorkState,p.parent_work_state_id)
    child=db.session.get(NeoScorpionFuelWorkState,p.promoted_work_state_id)
    now=now_utc or datetime.utcnow()
    if form.get('pending_action')=='review_source':
        if source.physical_finalized_at_utc:raise ValueError('Verified historical work requires the authorized historical correction workflow.')
        invalidate(source)
    elif form.get('pending_action') in {'resolve_handoff','cancel_unstarted_handoff'}:
        view,mission=fuel._segment_views(a,m,source)
        if (not source.fueler_submitted_at_utc or source.fueler_submission_evidence!=evidence(view,mission,source,list(source.tank_states))):
            raise ValueError('Source Fueler must correct/save and resubmit this exact visit first.')
        if child.physical_finalized_at_utc:raise ValueError('Finalized descendant requires historical review, not a silent baseline change.')
        if form.get('pending_action')=='cancel_unstarted_handoff':
            if (a.active_work_state_id!=child.id or m.assigned_tail_number!=child.tail_number
                    or a.current_cycle_number!=child.physical_cycle_number):
                raise PendingUpliftConflict('The active aircraft/visit changed; review it before cancelling.')
            if outstanding(a.id,lock=True):raise ValueError('Cancel/review the later planned truck before retiring this visit.')
            if child.manual_on_at_utc or child.confirmed_on_at_utc or child.on_at_utc or child.off_at_utc:
                raise ValueError('This truck physically started; preserve its explicit ON/OFF and complete/review the visit.')
            fuel._retire_visit(gateway,op,a,m,child,fuel._locked_tank_states(child),user,now,'segment_transfer')
            a.active_work_state_id=source.id;a.assigned_truck_id=source.physical_truck_id
            a.assigned_fueler_user_id=source.physical_fueler_user_id;a.transfer_fuel_gallons=source.physical_transfer_gallons
            p.status='cancelled';p.review_reason='Unstarted continuation cancelled after explicit Dispatcher review: '+reason
            reconcile_completion(op,a,m,user,now)
        else:
            if form.get('confirm_remaining_verified')!='1':raise ValueError('Explicitly confirm the next visit Remaining readings were physically reviewed.')
            layout=fuel.tank_layout_for_tail(child.tail_number);values={t.tank_code:t.remaining_lbs for t in child.tank_states}
            if not layout or any(values.get(code) is None for code,_ in layout):raise ValueError('Save all measured Remaining readings on the affected visit first.')
            # An explicit attestation accepts the shown physical readings. Never
            # replace a started child's baseline from a later source correction.
            p.handoff_snapshot={**(p.handoff_snapshot or {}),'review':dict(actor=user.id,at=now.isoformat(),reason=reason,
                corrected_source_evidence=source.fueler_submission_evidence,accepted_remaining=values)}
            child.continuation_review_reason=None
    else:raise ValueError('Select a valid handoff review action.')
    p.version+=1;p.updated_by_user_id=user.id;p.updated_at_utc=now
    _audit(op,a,p,user,'Explicit handoff review: '+reason,now)
    asset=fuel.record_nightly_operational_change(asset,op.id);db.session.flush()
    return SimpleNamespace(changed=True,revision=int(asset.revision),pending=p)


def attach_console(context):
    """Read-only visit actions; source checks share canonical row validation."""
    from app.services import neoscorpion as fuel
    op=context.get('operation')
    if not op:return context
    owners={r['mission'].id:getattr(r['assignment'],'_parent_assignment',r['assignment']) for r in context['rows'] if r.get('assignment')}
    index=fuel._fuel_work_states_by_assignment_tail(list(owners.values()))
    by_owner={a.id:a for a in owners.values()}
    sources=[]
    for work in fuel.all_work(index):
        a=by_owner[work.fuel_assignment_id]
        if work.id==a.active_work_state_id or work.physical_finalized_at_utc or not work.physical_sequence_number:continue
        view,mission=fuel._segment_views(a,a.sort_date_mission,work)
        sources.append((mission,view,work))
    rates,limits=fuel._effective_aircraft_fuel_configuration(op.gateway_id)
    settings=context.get('settings')
    selections={entry['selection'].fuel_truck_id:entry['selection'] for entry in context.get('nightly_trucks',[])}
    source_rows=fuel._fuel_rows(op,[],projection_sources=sources,assignments_by_mission=owners,
        fuel_work_states_by_assignment_tail=index,apu_rates_by_aircraft_type=rates,
        nightly_truck_states_by_truck_id=selections,
        lateral_imbalance_limits_by_aircraft_type=limits,status_settings=settings,
        fuel_density_lbs_per_gallon=settings.fuel_density_lbs_per_gallon if settings else None) if sources else []
    by_assignment={}
    for source in source_rows:by_assignment.setdefault(source['assignment'].id,[]).append(source)
    dependencies=NeoScorpionPendingUplift.query.filter_by(sort_date_operation_id=op.id,status='promoted').filter(
        NeoScorpionPendingUplift.plan_kind.in_(CONTINUATIONS)).all()
    works={w.id:w for w in fuel.all_work(index)}
    for source in source_rows:
        source['continuation_plan']=next((p for p in dependencies if p.parent_work_state_id==source['physical_work_id']),None)
    for plan in dependencies:plan.console_child=works.get(plan.promoted_work_state_id)
    for row in context['rows']:
        a=row.get('assignment');row['console_sources']=by_assignment.get(a.id,[]) if a else []
        row['console_dependencies']=[p for p in dependencies if a and p.fuel_assignment_id==a.id
            and works.get(p.promoted_work_state_id) and works[p.promoted_work_state_id].continuation_review_reason]
        row['action_count']=sum(1 for s in row['console_sources'] if s['normal_completion_ready']
            or s['fuel_work_state'].physical_transfer_review or s['fuel_work_state'].manual_on_at_utc
            or s['fuel_work_state'].confirmed_on_at_utc or s['fuel_work_state'].on_at_utc or s['fuel_work_state'].off_at_utc)
        row['action_count']+=len(row['console_dependencies'])+sum(p.console_review for p in row.get('pending_truck_plans',[]))+bool(row.get('fueler_work_blocked'))
        # Read-only status explanations must not enable verification or mutate
        # the workflow. Ordinary Fueler data entry is not a Dispatcher review.
        row['mission_data_action'] = bool(row.get('mission_data_missing') and not row.get('administratively_complete'))
        row['status_review_action'] = bool(row.get('status_review_reasons') and not row.get('fueler_work_blocked')
            and not (row.get('fuel_work_state') and row['fuel_work_state'].continuation_review_reason))
        row['action_count'] += int(row['mission_data_action']) + int(row['status_review_action'])
        row['completion_progress'] = ''
        if not row.get('administratively_complete') and row.get('is_off'):
            row['completion_progress'] = ('Fueler submitted — ready for Dispatcher COMPLETE.' if row['normal_completion_ready'] else
                row.get('normal_completion_reason') or row.get('fueler_completion_reason') or 'Fueler COMPLETE ASSIGNMENT required.')
        row['console_visits']=[w for w in fuel.all_work(index) if a and w.fuel_assignment_id==a.id]
        row['console_visits'].sort(key=lambda w:(w.physical_cycle_number or 0,w.physical_sequence_number or 0,w.id))
        row['unverified_visit_count']=sum(1 for w in row['console_visits']
            if w.id!=row.get('physical_work_id') and not w.physical_finalized_at_utc and not w.ended_early_at_utc
            and (w.manual_on_at_utc or w.off_at_utc or w.fueler_submitted_at_utc))
        if row.get('fuel_work_state') and row['fuel_work_state'].physical_finalized_at_utc and row['unverified_visit_count']:
            row['fuel_status_secondary']=f"VERIFY {row['unverified_visit_count']} EARLIER VISIT(S)"
        valid={s['physical_work_id']:s['fueler_submitted'] for s in row['console_sources']}
        trusted_on={s['physical_work_id']:s.get('fueling_on_trusted',False) for s in row['console_sources']}
        if row.get('physical_work_id'):valid[row['physical_work_id']]=row.get('fueler_submitted',False)
        if row.get('physical_work_id'):trusted_on[row['physical_work_id']]=row.get('fueling_on_trusted',False)
        for visit in row['console_visits']:
            visit.console_submission_valid=valid.get(visit.id,False)
            visit.console_on_trusted=trusted_on.get(visit.id,False)
    from app.services.neoscorpion_assignment_times import attach_assignment_times
    return attach_assignment_times(context)

class PendingUpliftConflict(ValueError):
    pass


def outstanding(assignment_id, *, lock=False):
    return next(iter(outstanding_plans(assignment_id,lock=lock)),None)


def outstanding_plans(assignment_id, *, lock=False):
    query=NeoScorpionPendingUplift.query.filter_by(fuel_assignment_id=assignment_id).filter(NeoScorpionPendingUplift.status.in_(OUTSTANDING))
    if lock:query=query.with_for_update().populate_existing()
    return query.order_by(NeoScorpionPendingUplift.id).all()


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
    # Required Fuel is a net Neo Fuel target. Raw Actual includes the completed
    # visit's APU allowance and can exceed the target while Neo Fuel is short.
    verified_neo_fuel_lbs=fuel._fuel_work_calculation(layout,{t.tank_code:t for t in tanks},
        work.apu_running,fuel._effective_apu_allowance_lbs(work))[4]
    if verified_neo_fuel_lbs is None or not fuel._apu_source_is_valid(work,layout):
        raise ValueError('Verified final Neo Fuel and applicable APU data are required before uplift.')
    if p.required_fuel_lbs<=verified_neo_fuel_lbs:
        raise ValueError('Verified final Neo Fuel already meets or exceeds the pending TOTAL target; review required.')
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
        demand=estimate_fuel_demand_gallons(p.required_fuel_lbs,None,density,measured_fob_lbs=verified_neo_fuel_lbs).gallons
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
    if p is not None and p.plan_kind=='uplift':_attempt_promotion(gateway,user,operation,assignment,mission,p,now)


def attach_pending(context):
    from app.services import neoscorpion as fuel
    operation=context.get('operation')
    if operation is None:return context
    pending=NeoScorpionPendingUplift.query.filter_by(sort_date_operation_id=operation.id).filter(
        NeoScorpionPendingUplift.status.in_(OUTSTANDING)).options(joinedload(NeoScorpionPendingUplift.proposed_truck),
        joinedload(NeoScorpionPendingUplift.proposed_fueler)).order_by(NeoScorpionPendingUplift.id).all()
    by_mission={}
    for p in pending:by_mission.setdefault(p.mission_id,[]).append(p)
    selections={r['selection'].fuel_truck_id:r['selection'] for r in context.get('nightly_trucks',[])}
    fuelers,trucks=fuel._current_resource_owners(context['rows'])
    for row in context['rows']:
        queue=by_mission.get(row['mission'].id,[])
        row['pending_truck_plans']=queue
        row['last_plan_ref']=queue[-1].id if queue else ''
        p=queue[0] if queue else None;row['pending_uplift']=p;row['pending_uplift_warning']=''
        for planned in queue:
            planned.console_target=format(Decimal(planned.required_fuel_lbs)/Decimal(1000),'.3f').rstrip('0').rstrip('.')
            planned.console_review=bool(planned.status=='review' or planned.proposed_truck_id is None or planned.tail_number!=row['tail_number'])
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
            row['plan_requires_review']=any(planned.console_review for planned in queue)
        else:row['plan_requires_review']=False
    return context
