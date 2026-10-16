"""Bounded whole-sort SPEAR schedules. Forecasts never mutate physical resources.

Earlier authoritative departures are reserved first. A beam of equal-priority
calendars retains alternate resource/top-off sequences; later jobs may fill gaps
only if all earlier reservations, travel and inventory constraints still hold.
This is a bounded search, not a claim of an exhaustive global optimum.
"""
from dataclasses import dataclass, field, replace, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from types import SimpleNamespace
from time import monotonic
from functools import lru_cache
import hashlib
import json

from app.services import neoscorpion_spear as legacy
from app.services.neoscorpion_resource_timing import fueler_work_end, stamp
from app.services.neoscorpion_spear_calibration import calibrated_planning_settings

VERSION = 'spear-whole-sort/v2'
MAX_JOBS = 80
BEAM = 8
MAX_EVALUATIONS = 16000
MAX_SECONDS = 0.8
INFINITY = datetime(2099, 1, 1)


def minutes(value):
    return timedelta(minutes=float(value))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        separators=(',', ':')).encode()).hexdigest()


def top_off_policy(pairs, truck_id, settings):
    """Exact linked, verified SENT/RETURN evidence only; never reconstruct history."""
    samples = []
    for sent, returned in pairs:
        start, end = stamp(sent.occurred_at_utc), stamp(returned.occurred_at_utc)
        if (sent.kind != 'sent' or returned.kind != 'returned'
                or returned.sent_event_id != sent.id
                or sent.fuel_truck_id != returned.fuel_truck_id
                or sent.sort_date_operation_id != returned.sort_date_operation_id
                or not start or not end or not returned.inventory_verified
                or returned.gallons is None or returned.gallons < 0
                or not sent.actor_user_id or not returned.actor_user_id):
            continue
        duration = (end-start).total_seconds()/60
        if 2 <= duration <= 240:
            samples.append((sent.fuel_truck_id, sent.sort_date_operation_id, duration))
    specific = [s for s in samples if s[0] == truck_id]
    qualified = lambda rows: len(rows) >= 10 and len({r[1] for r in rows}) >= 2
    evidence = specific if qualified(specific) else samples
    use = settings.use_learned_top_off and qualified(evidence)
    duration = sorted(s[2] for s in evidence)[ceil(.8*len(evidence))-1] if use else settings.top_off_roundtrip_minutes
    return dict(minutes=duration, source=('truck p80' if evidence is specific else 'gateway p80') if use else 'manual',
        sample_count=len(evidence), sort_count=len({s[1] for s in evidence}),
        confidence='qualified' if use else 'manual fallback' if settings.use_learned_top_off else 'manual',
        minimum_samples=10, percentile=80)


@dataclass(frozen=True)
class Block:
    key: str
    start: datetime
    end: datetime
    ramp: str
    demand: Decimal = Decimal(0)
    reset: bool = False
    conditional: bool = False


@dataclass
class Schedule:
    trucks: dict = field(default_factory=dict)
    fuelers: dict = field(default_factory=dict)
    jobs: dict = field(default_factory=dict)
    objective: tuple = ()
    topoffs: int = 0
    changes: int = 0

    def clone(self):
        return Schedule({k:list(v) for k,v in self.trucks.items()},
            {k:list(v) for k,v in self.fuelers.items()},dict(self.jobs),
            self.objective,self.topoffs,self.changes)


@lru_cache(maxsize=512)
def _travel(origin, destination, settings, fueler=False):
    return minutes(legacy._travel_minutes(origin, destination,
        settings.truck_minutes_per_ramp_move) / (2 if fueler else 1))


def _gaps(blocks, base, location, ramp, duration, earliest, settings, fueler=False):
    # Unknown physical locations intentionally block this resource. Do not pass
    # them to ramp conversion, guess Remote, or abort the entire fleet plan.
    if base>=INFINITY or location not in legacy.SPEAR_RAMP_ORDER:
        return
    previous_end, previous_ramp = base, location
    for block in sorted(blocks, key=lambda b:(b.start, b.key)):
        start = max(earliest, previous_end + _travel(previous_ramp, ramp, settings, fueler))
        end = block.start - (timedelta(0) if block.reset else _travel(ramp, block.ramp, settings, fueler))
        if start+duration <= end:
            yield start, end
        previous_end, previous_ramp = max(previous_end, block.end), block.ramp
    start = max(earliest, previous_end + _travel(previous_ramp, ramp, settings, fueler))
    if start < INFINITY:
        yield start, INFINITY


def _joint_slot(state, truck_id, fueler_id, truck, fueler, ramp, duration, finishing,
                earliest, settings):
    tg = list(_gaps(state.trucks[truck_id], truck['base'], truck['ramp'], ramp,
        duration, earliest, settings))
    fg = list(_gaps(state.fuelers[fueler_id], fueler['base'], fueler['ramp'], ramp,
        duration+finishing, earliest, settings, True))
    choices=[]
    for ta, tb in tg:
        for fa, fb in fg:
            start=max(ta,fa)
            if start+duration <= tb and start+duration+finishing <= fb:
                choices.append(start)
    return sorted(set(choices))


def _inventory(blocks, truck, reserve):
    gallons = Decimal(truck['gallons'])
    capacity = Decimal(truck['capacity'])
    for block in sorted(blocks, key=lambda b:(b.end, not b.reset, b.key)):
        gallons = capacity if block.reset else gallons-block.demand
        if gallons < 0 or gallons > capacity or (block.demand > 0 and gallons < reserve):
            return False
    return True


def _top_off_branches(state, truck_id, truck, earliest, ramp, settings, policy):
    """Try every bounded truck gap, including immediate and post-mission TOP OFF."""
    yield state, False
    if not truck.get('may_need_topoff',True):
        return
    duration=minutes(policy['minutes'])
    # Roundtrip includes the trip to the farm and return to Remote. Only later
    # Remote-to-aircraft travel is added, never another trip inside the roundtrip.
    blocks=sorted(state.trucks[truck_id],key=lambda b:(b.start,b.key))
    previous=truck['base']
    for index, block in enumerate([*blocks, None]):
        # Both immediate and latest safe departures matter: the latter leaves
        # room for productive work before a protected earlier aircraft arrives.
        latest=max(previous,earliest-duration-_travel('Remote',ramp,settings))
        for begin in sorted({previous,latest}):
            end=begin+duration
            gallons=Decimal(truck['gallons'])
            for earlier in sorted((b for b in blocks if b.end<=begin),key=lambda b:(b.end,not b.reset,b.key)):
                gallons=Decimal(truck['capacity']) if earlier.reset else gallons-earlier.demand
            if gallons>=truck['capacity']:
                continue
            if (end < INFINITY and (block is None or
                    end+_travel('Remote',block.ramp,settings) <= block.start)):
                candidate=state.clone()
                candidate.trucks[truck_id].append(Block('topoff:'+str(index)+':'+str(end),
                    begin,end,'Remote',reset=True,conditional=True))
                candidate.topoffs+=1
                yield candidate, True
        if block is not None:
            previous=max(previous,block.end)


def _release_resources(rows, fuelers, trucks, now, settings, top_events, policies, operation, planning):
    """Freeze current occupancy first, independent of mission sort/completion."""
    current_ids={getattr(r.get('fuel_work_state'),'id',None) for r in rows}
    current_ids.update(getattr(s,'id',None) for r in rows for s in (r.get('outstanding_segments') or []))
    visits=next((r['spear_physical_visits'] for r in rows if 'spear_physical_visits' in r),[])
    if any(r.get('spear_visits_truncated') for r in rows):
        raise ValueError('Physical history bound reached; existing safe SPEAR required.')
    for visit in visits:
        if visit['id'] in current_ids or not stamp(visit.get('on')):continue
        ramp=legacy._parking_ramp(visit.get('parking'))
        for pool,identifier,end in ((trucks,visit.get('truck_id'),stamp(visit.get('off'))),
                (fuelers,visit.get('fueler_id'),stamp(visit.get('fueler_release')))):
            if identifier not in pool:continue
            resource=pool[identifier]
            if end:
                if end >= resource.get('location_at',datetime.min):
                    resource.update(ramp=ramp,location_at=end)
            elif not visit.get('finalized'):
                resource.update(base=INFINITY,conditional=True)
    for row in rows:
        ramp=legacy._parking_ramp(row.get('parking_position'))
        assignment=row.get('assignment')
        if assignment is None:
            continue
        work=row.get('fuel_work_state')
        segments=list(row.get('outstanding_segments') or [])
        if work is not None and all(getattr(s,'id',None) != getattr(work,'id',None) for s in segments):
            segments.append(work)
        for segment in segments:
            on=stamp(getattr(segment,'on_at_utc',None))
            if not on:
                continue
            off=stamp(getattr(segment,'off_at_utc',None))
            release=fueler_work_end(segment)
            forecast=row.get('physical_off_forecast') or {}
            predicted=stamp(forecast.get('likely'))
            recorded_parking=((getattr(segment,'performance_context',None) or {}).get('resource_timing') or {}).get('parking')
            segment_ramp=legacy._parking_ramp(recorded_parking) if recorded_parking else ramp
            # An expired forecast never fabricates release or slides forward.
            for pool,identifier,end,is_truck in (
                (trucks,getattr(segment,'physical_truck_id',None),off,True),
                (fuelers,getattr(segment,'physical_fueler_user_id',None),release,False)):
                if identifier not in pool:
                    continue
                resource=pool[identifier]
                if end:
                    if end >= resource.get('location_at',datetime.min):
                        resource.update(ramp=segment_ramp,location_at=end)
                else:
                    future=predicted if row.get('fueling_on_trusted') and predicted and predicted > now and segment_ramp else INFINITY
                    if not is_truck and future != INFINITY:
                        finishing=legacy._spear_timing(row,operation,planning).finishing_minutes
                        future=future+minutes(finishing) if finishing is not None else INFINITY
                    resource.update(base=max(resource['base'],future),conditional=True,
                        ramp=segment_ramp)
                    if is_truck and not resource.get('active_demand_reserved'):
                        demand=legacy._decimal_or_none(row.get('planning_demand_gallons'))
                        if demand is None:
                            resource['base']=INFINITY
                        else:
                            resource['gallons']=int(Decimal(resource['gallons'])-demand)
                            resource['active_demand_reserved']=True
        # Legacy unproven physical starts and holds reserve resources indefinitely.
        if (row.get('work_has_begun') and not any(stamp(getattr(s,'on_at_utc',None)) for s in segments)
                or getattr(assignment,'operational_status','active') != 'active'):
            for pool,identifier in ((trucks,getattr(assignment,'assigned_truck_id',None)),
                    (fuelers,getattr(assignment,'assigned_fueler_user_id',None))):
                if identifier in pool:
                    pool[identifier].update(base=INFINITY,conditional=True)
    # Actual RETURN locations are authoritative, never arbitrary history text.
    latest={}
    for event in top_events:
        if event.kind == 'returned' and event.inventory_verified:
            if event.fuel_truck_id not in latest or event.occurred_at_utc > latest[event.fuel_truck_id].occurred_at_utc:
                latest[event.fuel_truck_id]=event
    for identifier,truck in trucks.items():
        event=latest.get(identifier)
        if event and event.occurred_at_utc > truck.get('location_at',datetime.min):
            truck['ramp']='Remote'
        if truck['ramp'] is None:truck['base']=INFINITY
        sent=truck.get('sent')
        if truck['status'] in ('top_off','topping_off') or sent:
            recorded=next((e for e in top_events if e.kind=='sent' and e.fuel_truck_id==identifier and stamp(e.occurred_at_utc)==sent),None)
            predicted=(stamp(recorded.spear_predicted_return_at_utc) if recorded is not None else
                sent+minutes(policies[identifier]['minutes']) if sent else None)
            truck.update(base=predicted if predicted and predicted > now else INFINITY,
                ramp='Remote',conditional=True,return_at=predicted,overdue=bool(predicted and predicted <= now))
            # Forecast only, never write selection.current_gallons.
            truck['gallons']=truck['capacity']
    for resource in fuelers.values():
        if resource['ramp'] is None:resource['base']=INFINITY


def optimize(rows, *, operation, planning_settings, spear_settings, nightly_fuelers,
             nightly_trucks, now_utc, calibrations=None, top_off_pairs=(), top_events=(),
             previous=None, fingerprint='', max_evaluations=MAX_EVALUATIONS, max_seconds=MAX_SECONDS):
    now=legacy._utc_naive(now_utc)
    start_clock=monotonic()
    rows=tuple(rows)
    settings=spear_settings
    planning=calibrated_planning_settings(planning_settings,calibrations or {},mode=settings.live_calibration_mode)
    fuelers={f.id:dict(user=f,base=now,ramp=settings.fueler_begins_at,conditional=False)
        for f in sorted(nightly_fuelers,key=legacy._fueler_sort_key)}
    trucks={}
    policies={}
    for item in nightly_trucks:
        t,s=item.get('truck'),item.get('selection')
        if not t or not s or not t.is_active or t.is_out_of_service or not t.capacity_gallons:
            continue
        if s.status not in ('available','top_off','topping_off'):
            continue
        sent=stamp(getattr(s,'top_off_sent_at_utc',None))
        if s.status == 'available' and (s.current_gallons is None or not getattr(s,'inventory_verified',True)):
            continue
        if s.status != 'available' and not sent:
            continue
        policies[t.id]=dict(top_off_policy(top_off_pairs,t.id,settings),truck_number=str(t.truck_number))
        trucks[t.id]=dict(truck=t,base=now,ramp=settings.truck_begins_at,
            gallons=int(s.current_gallons or 0),capacity=int(t.capacity_gallons),status=s.status,
            sent=sent,conditional=False)
    _release_resources(rows,fuelers,trucks,now,settings,top_events,policies,operation,planning)
    occupied_f,occupied_t=legacy._current_resource_owners(rows)
    # Incomplete committed work blocks resources before any departure is planned.
    for row in rows:
        assignment=row.get('assignment')
        if assignment is None or row.get('administratively_complete'):continue
        if row.get('fob_likely') and not getattr(assignment,'assigned_truck_id',None):
            identifier=getattr(assignment,'assigned_fueler_user_id',None)
            if identifier in fuelers:fuelers[identifier]['base']=INFINITY
        missing=legacy._readiness_reasons(row,spear_settings=settings,now_utc=now)
        future=getattr(row.get('arrival_mission'),'eta_datetime_utc',None) or getattr(row.get('arrival_mission'),'planned_datetime_utc',None)
        if any(m != 'arrival_timing' or not future for m in missing):
            for pool,identifier in ((trucks,getattr(assignment,'assigned_truck_id',None)),
                    (fuelers,getattr(assignment,'assigned_fueler_user_id',None))):
                if identifier in pool:pool[identifier]['base']=INFINITY
    initial=Schedule(trucks={i:[] for i in trucks},fuelers={i:[] for i in fuelers})
    beam=[initial]
    waiting,unavailable,readiness={}, {}, {}
    timings={}
    row_by_id={r['mission'].id:r for r in rows}
    candidates=[r for r in rows if not r.get('administratively_complete') and not r.get('is_off')
        and not (r.get('fob_likely') and not getattr(r.get('assignment'),'assigned_truck_id',None))
        and not (r.get('fueling_on_trusted') or stamp(getattr(r.get('fuel_work_state'),'on_at_utc',None)))]
    future=[]
    # Grounded pending uplift participates in the same protected calendars, but
    # can never become an executable assignment before canonical promotion.
    for row in rows:
        pending=row.get('pending_uplift')
        if not pending:continue
        assignment=row.get('assignment')
        actual=row.get('actual_total_lbs')
        same_tail=str(getattr(row['mission'],'assigned_tail_number','') or '').strip().upper()==str(pending.tail_number).strip().upper()
        grounded=bool(actual is not None and row.get('actual_complete') and row.get('fuel_density')
            and same_tail and assignment and assignment.current_cycle_number==pending.parent_cycle_number)
        demand=(Decimal(pending.required_fuel_lbs)-Decimal(actual))/Decimal(str(row['fuel_density'])) if grounded else None
        forecast=dict(pending_id=pending.id,mission_id=row['mission'].id,identity=pending.pending_cycle_identity,
            version=pending.version,status=pending.status,proposed_truck=pending.proposed_truck_id,
            proposed_fueler=pending.proposed_fueler_user_id,demand_gallons=str(demand) if demand is not None and demand>0 else None,
            reason='Dispatcher completion and promotion required; forecast demand only' if demand is not None and demand>0 and pending.status=='pending'
                else 'REVIEW REQUIRED' if demand is not None else 'Final Actual / tail / cycle evidence incomplete',conditional=True)
        future.append(forecast)
        if demand is None or demand<=0 or pending.status!='pending':continue
        child=dict(row)
        mission=row['mission']
        child.update(mission=SimpleNamespace(id=-pending.id,flight_number=mission.flight_number,
            eta_datetime_utc=mission.eta_datetime_utc,planned_datetime_utc=mission.planned_datetime_utc,
            timezone=getattr(mission,'timezone',None)),planning_demand_gallons=demand,
            assignment=SimpleNamespace(assigned_truck_id=pending.proposed_truck_id,
                assigned_fueler_user_id=pending.proposed_fueler_user_id,operational_status='active'),
            _pending_parent_id=mission.id,_pending_id=pending.id,administratively_complete=False)
        row_by_id[-pending.id]=child
        candidates.append(child)
    candidates.sort(key=lambda r:(legacy._departure_at(r) or INFINITY,
        r.get('_pending_parent_id',r['mission'].id),bool(r.get('_pending_id'))))
    total_positive=sum(max(Decimal(0),legacy._decimal_or_none(r.get('planning_demand_gallons')) or Decimal(0)) for r in candidates)
    for truck in trucks.values():
        # If this truck alone can cover all known remaining demand plus reserve,
        # a replenishment cannot improve a protected schedule. Prune that branch.
        truck['may_need_topoff']=Decimal(truck['gallons'])-total_positive<settings.minimum_truck_reserve_gallons
    if len(candidates)>MAX_JOBS:
        raise ValueError('Whole-sort job bound exceeded; safe SPEAR fallback required.')
    evaluations=0
    protection_unknown=False
    for row in candidates:
        mid=row['mission'].id
        if protection_unknown:
            unavailable[mid]='Earlier departure data incomplete — protected schedule unknown'
            continue
        missing=legacy._readiness_reasons(row,spear_settings=settings,now_utc=now)
        # A known future arrival is schedulable, although never executable now.
        future_arrival=getattr(row.get('arrival_mission'),'eta_datetime_utc',None) or getattr(row.get('arrival_mission'),'planned_datetime_utc',None)
        missing=tuple(m for m in missing if m != 'arrival_timing' or not future_arrival)
        readiness[mid]=missing
        if missing:
            waiting[mid]=' · '.join(legacy.SPEAR_READINESS_REASON_LABELS[m] for m in missing)
            protection_unknown=mid>0
            # Unready but committed resources cannot be borrowed optimistically.
            assignment=row.get('assignment')
            for pool,identifier in ((trucks,getattr(assignment,'assigned_truck_id',None)),
                    (fuelers,getattr(assignment,'assigned_fueler_user_id',None))):
                if identifier in pool:pool[identifier]['base']=INFINITY
            continue
        timing=legacy._spear_timing(row,operation,planning)
        timings[mid]=timing
        ramp=legacy._parking_ramp(row.get('parking_position'))
        demand=legacy._decimal_or_none(row.get('planning_demand_gallons'))
        if not ramp or demand is None or timing.total_duration_minutes is None or timing.aircraft_ready_utc is None:
            unavailable[mid]='TIMING / FUEL / PARKING UNKNOWN — no invented schedule'
            protection_unknown=mid>0
            continue
        duration=minutes(timing.setup_minutes+timing.pump_minutes)
        finishing=minutes(timing.finishing_minutes)
        assignment=row.get('assignment')
        assigned_t=getattr(assignment,'assigned_truck_id',None)
        assigned_f=getattr(assignment,'assigned_fueler_user_id',None)
        tids=[assigned_t] if assigned_t else sorted(trucks)
        fids=[assigned_f] if assigned_f else sorted(fuelers)
        earliest=max(now, timing.aircraft_ready_utc)
        generated=[]
        ranked=[]
        for state in beam:
            parent=state.jobs.get(row.get('_pending_parent_id'))
            if row.get('_pending_id') and parent is None:
                original=row_by_id[row['_pending_parent_id']]
                off=stamp((original.get('physical_off_forecast') or {}).get('likely'))
                released=fueler_work_end(original.get('fuel_work_state'))
                if original.get('fueling_on_trusted') and off and off>now:
                    dependency=off+finishing
                elif original.get('is_off') and released:
                    dependency=max(now,released)
                else:
                    continue  # No invented parent release or admin completion.
                job_earliest=max(earliest,dependency)
            else:
                job_earliest=max(earliest,parent['fueler_end']) if parent else earliest
            for tid in tids:
                if tid not in trucks:continue
                truck=trucks[tid]
                # Include TOP OFF before a delayed aircraft, and immediately if
                # insufficient present fuel. Branches are constrained by every
                # earlier protected reservation, not just this mission's ETD.
                horizon=max(job_earliest,truck['base'])
                for branch,added_topoff in _top_off_branches(state,tid,truck,horizon,ramp,settings,policies[tid]):
                    for fid in fids:
                        if fid not in fuelers:continue
                        evaluations+=1
                        if evaluations>max_evaluations or monotonic()-start_clock>max_seconds:
                            raise ValueError('Whole-sort search budget exceeded; safe SPEAR fallback required.')
                        slots=_joint_slot(branch,tid,fid,truck,fuelers[fid],ramp,duration,finishing,job_earliest,settings)
                        at=next((at for at in slots if _inventory([*branch.trucks[tid],Block(str(mid),at,at+duration,ramp,demand)],
                            truck,settings.minimum_truck_reserve_gallons)),None)
                        if at is None:continue
                        off=at+duration
                        block=Block(str(mid),at,off,ramp,demand)
                        if not _inventory([*branch.trucks[tid],block],truck,settings.minimum_truck_reserve_gallons):continue
                        conditional=(bool(row.get('_pending_id')) or truck['conditional'] or fuelers[fid]['conditional'] or
                            any(b.conditional and b.end<=at for b in branch.trucks[tid]))
                        c=dict(mission_id=mid,truck_id=tid,fueler_id=fid,start=at,finish=off,
                            fueler_end=off+finishing,ramp=ramp,demand=demand,conditional=conditional,
                            topoff=added_topoff,committed=bool(assigned_t and assigned_f),pending_id=row.get('_pending_id'))
                        ranked.append(c)
                        new=branch.clone()
                        new.trucks[tid].append(block)
                        new.fuelers[fid].append(replace(block,end=off+finishing,demand=Decimal(0)))
                        new.jobs[mid]=c
                        new.objective=(*state.objective,off)
                        old=(previous or {}).get(str(mid))
                        new.changes+=int(old is not None and old != [tid,fid])
                        generated.append(new)
        if not generated:
            unavailable[mid]='NO FEASIBLE RESOURCE — occupancy / reserve / capacity / commitment'
            # Infeasibility is not permission to consume a committed resource.
            for pool,identifier in ((trucks,assigned_t),(fuelers,assigned_f)):
                if identifier in pool:pool[identifier]['base']=INFINITY
            continue
        generated.sort(key=lambda s:(s.objective,s.changes,s.topoffs,
            tuple((k,v['truck_id'],v['fueler_id']) for k,v in sorted(s.jobs.items()))))
        # Lexicographic completion vector is absolute priority. Retain alternative
        # equally best calendars, never sacrifice an earlier departure for later.
        best=generated[0].objective
        unique={}
        for state in generated:
            if state.objective != best:break
            identity=digest([[(b.key,b.start,b.end) for b in state.trucks[i]] for i in sorted(state.trucks)])+digest([(k,v['fueler_id']) for k,v in state.jobs.items()])
            unique.setdefault(identity,state)
            if len(unique)>=BEAM:break
        beam=list(unique.values())
        # Only genuinely evaluated choices, captured before later decisions.
        for state in beam:
            selected=state.jobs[mid]
            alternatives=sorted(ranked,key=lambda c:(c['finish'],c['truck_id'],c['fueler_id']))
            seen=set();selected['alternatives']=[]
            for alt in [selected,*alternatives]:
                pair=(alt['truck_id'],alt['fueler_id'])
                if pair in seen:continue
                seen.add(pair)
                selected['alternatives'].append({k:v for k,v in alt.items() if k != 'alternatives'})
                if len(seen)==4:break
    best=min(beam,key=lambda s:(s.objective,s.changes,s.topoffs))
    steps=[];risks={}
    for row in rows:
        if row.get('fueling_on_trusted') and not row.get('is_off') and not row.get('administratively_complete'):
            risks[row['mission'].id]=legacy._risk(stamp((row.get('physical_off_forecast') or {}).get('likely')),legacy._departure_at(row))
    for mid,c in best.jobs.items():
        row=row_by_id[mid];mission=row['mission'];timing=timings[mid]
        truck=trucks[c['truck_id']];fueler=fuelers[c['fueler_id']]
        risk=legacy._risk(c['finish'],legacy._departure_at(row))
        if c['conditional'] and risk == 'COVERED':risk='AT RISK'
        if c.get('pending_id'):
            forecast=next(f for f in future if f['pending_id']==c['pending_id'])
            forecast.update(projected_start=str(c['start']),projected_off=str(c['finish']),
                forecast_truck=c['truck_id'],forecast_fueler=c['fueler_id'])
            continue
        risks[mid]=risk
        if c['committed']:continue
        prior_t=[b for b in best.trucks[c['truck_id']] if b.end<=c['start']]
        prior_f=[b for b in best.fuelers[c['fueler_id']] if b.end<=c['start']]
        topoff=any(b.reset for b in prior_t)
        release=bool(prior_t or prior_f or truck['conditional'] or fueler['conditional']
            or occupied_t.get(c['truck_id'],set())-{mid} or occupied_f.get(c['fueler_id'],set())-{mid})
        reason=('Conditional TOP OFF RETURN required; ' if topoff or truck.get('sent') else '')+(
            'Future resource release required; ' if release else '')+'Protect earliest authoritative departure'
        alternatives=[dict(rank=i+1,truck_id=a['truck_id'],fueler_id=a['fueler_id'],
            truck_number=str(trucks[a['truck_id']]['truck'].truck_number),
            fueler_name=legacy._fueler_name(fuelers[a['fueler_id']]['user']),
            complete=legacy._display_time(a['finish'],mission),risk=legacy._risk(a['finish'],legacy._departure_at(row)),
            reason='Evaluated protected calendar') for i,a in enumerate(c['alternatives'])]
        before=Decimal(truck['gallons'])
        for b in sorted(prior_t,key=lambda b:(b.end,not b.reset,b.key)):
            before=Decimal(truck['capacity']) if b.reset else before-b.demand
        explanation=dict(kind='assignment',mission_ramp=row.get('parking_position'),
            departure=legacy._display_time(legacy._departure_at(row),mission),
            safe_completion_target=legacy._display_time(legacy._departure_at(row)-minutes(20),mission) if legacy._departure_at(row) else 'Unknown',
            aircraft_ready=legacy._display_time(timing.aircraft_ready_utc,mission),aircraft_ready_source=timing.aircraft_ready_source,
            truck_travel_minutes='Included in protected calendar',fueler_travel_minutes='Included in protected calendar',
            setup_minutes=legacy._minutes_display(timing.setup_minutes),pump_minutes=legacy._minutes_display(timing.pump_minutes),
            finishing_minutes=legacy._minutes_display(timing.finishing_minutes),projected_completion=legacy._display_time(c['finish'],mission),
            risk=risk,truck_gallons_before=int(before),estimated_gallons=int(abs(c['demand'])),
            truck_gallons_after=int(before-c['demand']),reserve_gallons=settings.minimum_truck_reserve_gallons,
            truck_location=dict(location=truck['ramp'],source='physical calendar',confidence='conditional' if release else 'verified inventory / configured start'),
            fueler_location=dict(ramp=fueler['ramp'],source='independent Fueler calendar'),early_staging_available=False,
            alternatives=alternatives,hard_constraint_notes=(reason,),live_calibration_mode=settings.live_calibration_mode,
            live_calibration=legacy._live_calibration_notes(calibrations or {},row.get('detailed_aircraft_type'),settings.live_calibration_mode),top_off_policy=policies[c['truck_id']])
        # A TOP OFF recommendation is an approval request, never an automatic
        # assignment backed by unverified forecast gallons.
        needs_send=topoff and truck['status']=='available' and not any(not b.reset for b in prior_t)
        steps.append(legacy.SpearPlanStep('top_off' if needs_send else 'assign',mid,mission.flight_number or '-',c['ramp'],
            c['truck_id'],str(truck['truck'].truck_number),None if needs_send else c['fueler_id'],
            '' if needs_send else legacy._fueler_name(fueler['user']),c['start'],c['finish'],risk,reason,
            automatic_eligible=not needs_send and not release and c['start']<=now and legacy._departure_at(row) is not None and not row.get('work_has_begun'),
            explanation=explanation,requires_resource_release=release))
    for forecast in future:
        mid=forecast['mission_id']
        if risks.get(mid) != 'LATE':risks[mid]='AT RISK'
    if future:
        pending_missions={f['mission_id'] for f in future}
        steps=[replace(s,risk='AT RISK',reason=s.reason+'; pending uplift is conditional')
            if s.mission_id in pending_missions and s.risk=='COVERED' else s for s in steps]
    metadata=dict(version=VERSION,evaluations=evaluations,elapsed_ms=round((monotonic()-start_clock)*1000,2),
        schedules=[{k:v for k,v in c.items() if k!='alternatives'} for c in best.jobs.values()],
        truck_calendars={str(i):[asdict(b) for b in sorted(bs,key=lambda b:b.start)] for i,bs in best.trucks.items()},
        fueler_calendars={str(i):[asdict(b) for b in sorted(bs,key=lambda b:b.start)] for i,bs in best.fuelers.items()},
        policies=policies,future_demands=future,
        top_off_status={str(i):dict(return_at=t.get('return_at'),overdue=t.get('overdue',False),verified_available=False)
            for i,t in trucks.items() if t.get('sent')})
    plan=legacy._plan(steps,risks,unavailable,waiting_for_data=waiting,readiness_by_mission_id=readiness,
        live_calibration_mode=settings.live_calibration_mode)
    return replace(plan,algorithm_version=VERSION,optimizer=metadata,
        token=digest([VERSION,fingerprint,[(s.action_type,s.mission_id,s.truck_id,s.fueler_id,s.risk,s.requires_resource_release) for s in steps]])[:24])


def validate_activation(gateway,user):
    """Explicit authorized settings action only; never flip during an ongoing sort."""
    from app.models import NeoScorpionFuelAssignment
    from app.services.neoscorpion import current_sort_operation
    if not getattr(user,'id',None):
        raise ValueError('Authorized Dispatcher selection is required.')
    operation=current_sort_operation(gateway)
    if operation:
        from app.models import SortDateOperation,NeoScorpionTopOffEvent
        from app.extensions import db
        operation=db.session.query(SortDateOperation).filter_by(id=operation.id).with_for_update().one()
        if NeoScorpionTopOffEvent.query.filter_by(sort_date_operation_id=operation.id).first():
            raise ValueError('Activate whole-sort planning before operational TOP OFF begins; use SHADOW for the ongoing sort.')
    if operation and NeoScorpionFuelAssignment.query.filter_by(sort_date_operation_id=operation.id).filter(
        (NeoScorpionFuelAssignment.assigned_fueler_user_id.isnot(None)) |
        (NeoScorpionFuelAssignment.assigned_truck_id.isnot(None))).first():
        raise ValueError('Activate whole-sort planning before sort assignments begin; use SHADOW for the ongoing sort.')
