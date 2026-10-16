"""Existing live refresh drives bounded, revision-aware SPEAR reconciliation."""
from collections import OrderedDict
from dataclasses import asdict,replace
from datetime import datetime,timedelta
from threading import RLock,Thread
from types import SimpleNamespace
import json
from flask import current_app
from sqlalchemy.orm import aliased, Session
from sqlalchemy import select,text
from app.extensions import db
from app.models import NeoScorpionTopOffEvent as Event, SortDateOperation
from app.models.neoscorpion_spear_plan import NeoScorpionSpearPlanSnapshot as Snapshot
from app.services.neoscorpion_spear_optimizer import optimize, digest, VERSION

_lock=RLock()
_cache=OrderedDict()
_history=OrderedDict()
_snapshots=OrderedDict()
_snapshot_worker=False


def inputs(context,calibrations):
    """Include external mission changes even when fueling revision did not move."""
    rows=[]
    for row in context['rows']:
        objects=[]
        for key in ('mission','arrival_mission','assignment','fuel_work_state','pending_uplift'):
            obj=row.get(key)
            # Mapped scalar columns only, never lazy relationships / per-row reads.
            objects.append({c.name:getattr(obj,c.name) for c in obj.__table__.columns
                if c.name not in ('notes','updated_at','created_at','comment','remarks','load_planner_person_id')}
                if obj is not None and hasattr(obj,'__table__') else None)
        rows.append([row['mission'].id,objects,*[row.get(k) for k in (
            'parking_position','arrival_status','required_fuel_lbs','inbound_fuel_lbs',
            'planning_demand_gallons','actual_total_lbs','actual_complete','cycle_type',
            'fueling_on_trusted','fueling_effective_on_utc','is_off','detailed_aircraft_type')]])
    trucks=[]
    for item in context['nightly_trucks']:
        t,s=item['truck'],item['selection']
        trucks.append([t.id,t.capacity_gallons,t.is_active,t.is_out_of_service,s.status,
            s.current_gallons,s.inventory_verified,s.inventory_epoch,s.top_off_sent_at_utc])
    return digest([sorted(rows,key=lambda r:r[0]),sorted(trucks),
        sorted(f.id for f in context['nightly_assignment_fuelers']),asdict(context['spear_settings']),calibrations,
        next((r['spear_physical_visits'] for r in context['rows'] if 'spear_physical_visits' in r),[])])


def load_top_off_history(operation):
    sent,returned=aliased(Event),aliased(Event)
    pairs=db.session.query(sent,returned).join(returned,returned.sent_event_id==sent.id).join(
        SortDateOperation,SortDateOperation.id==sent.sort_date_operation_id).filter(
        SortDateOperation.gateway_id==operation.gateway_id,returned.occurred_at_utc<=datetime.utcnow()).order_by(returned.occurred_at_utc.desc()).limit(1000).all()
    events=Event.query.filter_by(sort_date_operation_id=operation.id).order_by(Event.occurred_at_utc).limit(1001).all()
    if len(events)>1000:raise ValueError('Current TOP OFF history bound exceeded')
    return pairs,events


def freeze_sent_forecast(operation,truck_id,sent_at):
    """Optional immutable prediction prepared before inserting the SENT event."""
    from app.models import NeoScorpionSettings
    from app.services.neoscorpion_spear import effective_spear_settings
    from app.services.neoscorpion_spear_optimizer import top_off_policy
    try:
        with db.session.begin_nested():
            settings=effective_spear_settings(NeoScorpionSettings.query.filter_by(gateway_id=operation.gateway_id).first())
            pairs,_=load_top_off_history(operation)
            policy=top_off_policy(pairs,truck_id,settings)
            return sent_at+timedelta(minutes=float(policy['minutes'])),policy
    except Exception:
        current_app.logger.warning('SPEAR SENT prediction unavailable; physical action preserved',exc_info=True)
        return None,None


def _summary(plan):
    return {str(s.mission_id):[s.truck_id,s.fueler_id] for s in plan.steps if s.action_type=='assign'}


def _material_changes(previous,plan):
    if previous is None:return []
    old={s.mission_id:s for s in previous.steps}
    changes=[]
    for step in plan.steps:
        before=old.get(step.mission_id)
        if (before is None or (before.truck_id,before.fueler_id,before.risk,before.action_type) !=
                (step.truck_id,step.fueler_id,step.risk,step.action_type)):
            changes.append(f'{step.flight_number}: operational inputs changed; {step.reason}')
        elif before.projected_complete_at_utc and step.projected_complete_at_utc and abs(
                (before.projected_complete_at_utc-step.projected_complete_at_utc).total_seconds())>=180:
            changes.append(f'{step.flight_number}: projected physical OFF changed; protecting authoritative departure order')
    return changes[:8]


def _snapshot(context,plan,fingerprint,changes):
    """Bounded background evidence; never wait on an operational transaction."""
    global _snapshot_worker
    payload=json.loads(json.dumps(dict(schema_version='spear-plan/v2',plan=asdict(plan),changes=changes),default=str))
    if len(json.dumps(payload))>512000:
        return
    values=dict(gateway_id=context['operation'].gateway_id,operation_id=context['operation'].id,
        mode=context['spear_settings'].whole_sort_mode,fingerprint=fingerprint,algorithm_version=VERSION,
        token=plan.token,payload=payload)
    if current_app.config.get('TESTING'):
        return  # Test persistence explicitly; never leak fixture worker threads.
    app=current_app._get_current_object()
    with _lock:
        _snapshots[(id(app),values['operation_id'],fingerprint)]=(app,values)
        while len(_snapshots)>32:_snapshots.popitem(last=False)
        if _snapshot_worker:return
        _snapshot_worker=True
    Thread(target=_drain_snapshots,daemon=True,name='spear-plan-evidence').start()


def _drain_snapshots():
    global _snapshot_worker
    try:
        while True:
            with _lock:
                if not _snapshots:
                    _snapshot_worker=False
                    return
                _,(app,values)=_snapshots.popitem(last=False)
            with app.app_context():
                _write_snapshot(values)
    except Exception:
        with _lock:
            _snapshot_worker=False


def _previous(operation_id,mode):
    """Reuse durable plan stability across worker restarts, with one bounded read."""
    try:
        with db.session.begin_nested():
            previous=Snapshot.query.filter_by(operation_id=operation_id,mode=mode).order_by(
                Snapshot.created_at.desc(),Snapshot.id.desc()).first()
            if previous is None:return None
            from app.services.neoscorpion_spear import SpearPlanStep
            steps=[]
            for raw in previous.payload.get('plan',{}).get('steps',[])[:160]:
                values=dict(raw)
                for key in ('projected_start_at_utc','projected_complete_at_utc'):
                    if values.get(key):values[key]=datetime.fromisoformat(values[key])
                steps.append(SpearPlanStep(**values))
            return SimpleNamespace(steps=steps)
    except Exception:
        current_app.logger.warning('SPEAR prior snapshot unavailable; deterministic tie-breakers retained')
        return None


def _write_snapshot(values):
    try:
        with Session(db.engine) as session:
            if db.engine.dialect.name=='postgresql':
                session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                session.execute(text("SET LOCAL statement_timeout = '2s'"))
            exists=session.scalar(select(Snapshot.id).where(Snapshot.operation_id==values['operation_id'],
                Snapshot.mode==values['mode'],Snapshot.fingerprint==values['fingerprint']))
            if exists is None:
                session.add(Snapshot(**values))
                session.commit()
    except Exception:
        current_app.logger.warning('SPEAR optional plan snapshot unavailable',exc_info=True)


def enrich(context,baseline,planning,calibrations,now=None):
    """One visible engine; SHADOW compares silently and ACTIVE has safe fallback."""
    settings=context['spear_settings']
    if not settings.recommendations_enabled:return baseline
    now=now or datetime.utcnow()
    fingerprint=inputs(context,calibrations)
    key=(id(current_app._get_current_object()),context['operation'].id)
    # Periodic reconciliation piggybacks on the existing Dispatch refresh; no
    # new browser timer/API. Exact time is deliberately excluded from tokens.
    bucket=int(now.timestamp()//(60*settings.recalculation_interval_minutes))
    cachekey=(*key,fingerprint,bucket)
    try:
        with _lock:
            cached=_cache.get(cachekey)
            previous=_history.get(key)
        if cached is None:
            if previous is None:previous=_previous(context['operation'].id,settings.whole_sort_mode)
            pairs,events=load_top_off_history(context['operation'])
            candidate=optimize(context['rows'],operation=context['operation'],planning_settings=planning,
                spear_settings=settings,nightly_fuelers=context['nightly_assignment_fuelers'],
                nightly_trucks=context['nightly_trucks'],now_utc=now,calibrations=calibrations,
                top_off_pairs=pairs,top_events=events,previous=_summary(previous) if previous else {},fingerprint=digest([fingerprint,[(s.id,r.id,r.occurred_at_utc) for s,r in pairs]]))
            changes=_material_changes(previous,candidate)
            candidate=replace(candidate,optimizer={**candidate.optimizer,'mode':settings.whole_sort_mode,
                'changes':changes,'input_fingerprint':fingerprint,'baseline_token':baseline.token})
            _snapshot(context,candidate,digest([fingerprint,candidate.token,candidate.optimizer.get('schedules')]),changes)
            with _lock:
                _cache[cachekey]=candidate
                _history[key]=candidate
                while len(_cache)>64:_cache.popitem(last=False)
                while len(_history)>32:_history.popitem(last=False)
        else:
            candidate=cached
        context['spear_whole_sort']=candidate.optimizer
        future={d['pending_id']:d for d in candidate.optimizer.get('future_demands',[])}
        for row in context['rows']:
            pending=row.get('pending_uplift')
            row['pending_spear_forecast']=future.get(pending.id) if pending else None
        from app.services.time_display import format_local_hhmm
        from app.services.neoscorpion import gateway_timezone
        for visual in context.get('truck_visuals',[]):
            status=candidate.optimizer.get('top_off_status',{}).get(str(visual['truck_id']))
            visual['spear_top_off_status']=''
            if status:
                predicted=status.get('return_at')
                visual['spear_top_off_status']=('TOP OFF TIMING UNKNOWN' if predicted is None else 'TOP OFF OVERDUE' if status['overdue'] else
                    'EST RETURN '+format_local_hhmm(predicted,gateway_timezone(context['operation'].gateway)))
                visual['spear_top_off_confidence']=candidate.optimizer['policies'].get(visual['truck_id'],{}).get('confidence','manual')
        if settings.whole_sort_mode=='active':
            return candidate
    except Exception:
        current_app.logger.warning('SPEAR whole-sort evaluation unavailable; safe SPEAR retained',exc_info=True)
        context['spear_whole_sort']={'mode':settings.whole_sort_mode,'fallback':True,
            'reason':'Whole-sort evaluation unavailable; existing safe SPEAR retained'}
    return replace(baseline,token=digest([baseline.token,fingerprint,'safe-spear'])[:24])
