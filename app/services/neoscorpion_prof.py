"""Shared, snapshot-based physical fueling performance and report mathematics.

Historical configuration is read from the immutable event, never today's settings.
ON -> OFF is service duration, not a measured mechanical pumping rate.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import SimpleNamespace

from app.extensions import db
from app.models import (NeoScorpionFuelingEvent, NeoScorpionFuelAssignment,
                        NeoScorpionFuelTruck, SortDateMission, SortDateOperation, User)


def number(value):
    try:
        result = Decimal(str(value)) if value is not None else None
        return result if result is not None and result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def timestamp(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed=datetime.fromisoformat(value) if value else None
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed and parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def iso(value):
    return value.isoformat() if value else None


def minutes(start, end):
    start, end = timestamp(start), timestamp(end)
    return Decimal(str((end-start).total_seconds())) / 60 if start and end else None


def setup_allowance(fueler_at, truck_at, window, setup):
    """Assignment lead time controls zero/full policy credit, not preparation."""
    setup, window = number(setup), number(window)
    lead = minutes(fueler_at, truck_at)
    if setup is None or setup < 0:
        return None, lead, "Setup Time unavailable"
    if window is None:
        return setup, lead, "Advance window not configured; full Setup Time"
    if lead is None:
        return None, None, "Assignment timestamps unknown"
    if lead >= window:
        return Decimal(0), lead, "Fueler assigned at least the advance window before truck"
    return setup, lead, "Advance window not met; full Setup Time"


def policy_snapshot(assignment, settings, planning, aircraft_type, *, resources=None):
    allowance, lead, reason = setup_allowance(
        assignment.fueler_assigned_at_utc, assignment.truck_assigned_at_utc,
        getattr(settings, "advance_setup_window_minutes", None), planning.setup_minutes)
    if resources is not None:
        allowance=number(resources.get('setup_allowance'))
        lead=number(resources.get('lead_minutes'))
        reason=resources.get('setup_reason')
    return {
        "version": 4 if resources is not None else 3, "aircraft_type": aircraft_type,
        "fueler_id": assignment.assigned_fueler_user_id,
        "truck_id": assignment.assigned_truck_id,
        "cycle": int(assignment.current_cycle_number or 1),
        "fueler_assigned_at": iso(assignment.fueler_assigned_at_utc),
        "truck_assigned_at": iso(assignment.truck_assigned_at_utc),
        "assignment_actor_id": assignment.resource_assignment_by_user_id,
        "advance_window": getattr(settings, "advance_setup_window_minutes", None),
        "setup_minutes": str(planning.setup_minutes) if planning.setup_minutes is not None else None,
        "setup_allowance": str(allowance) if allowance is not None else None,
        "lead_minutes": str(lead) if lead is not None else None,
        "setup_reason": reason,
        "pump_rate": str(planning.pump_rate_for(aircraft_type)) if planning.pump_rate_for(aircraft_type) is not None else None,
        "forecasts": [], "exclusions": [],
        **({'resource_timing':resources} if resources is not None else {}),
    }


def event_math(event):
    facts = event.performance_snapshot or {}
    shared = list(facts.get("exclusions", ()))
    if facts.get("version") not in {3,4}:
        shared.append("Historical settings/timestamp provenance unavailable")
    if facts.get("on_source") not in {"manual", "trusted automatic"}:
        shared.append("ON not verified")
    actual = minutes(event.started_at_utc, event.ended_at_utc)
    if actual is None or actual <= 0:
        shared.append("Missing or invalid physical ON/OFF interval")
    rate, gallons = number(facts.get("pump_rate")), number(event.transfer_fuel_gallons)
    if rate is None or rate <= 0:
        shared.append("Pump rate unavailable")
    if gallons is None or gallons <= 0 or event.event_type not in {"fuel", "uplift"}:
        shared.append("No qualifying positive FUEL/UPLIFT transfer")
    if facts.get("fueler_id") != event.fueler_user_id or facts.get("truck_id") != event.fuel_truck_id:
        shared.append("Resource identity uncertain")
    pump = gallons / rate if gallons is not None and rate is not None and rate > 0 else None
    allowance = number(facts.get("setup_allowance"))
    fueler_reasons = list(shared)
    truck_reasons=list(shared)
    if allowance is None or not facts.get("fueler_assigned_at") or not facts.get("truck_assigned_at"):
        fueler_reasons.append("Assignment lead time/setup allowance unknown")
    actual_at = timestamp(facts.get("actuals_received_at"))
    ready_at = timestamp(facts.get("completion_ready_at"))
    completed_at = timestamp(facts.get("completed_at"))
    wrap = max(Decimal(0), minutes(event.ended_at_utc, actual_at)) if actual_at and event.ended_at_utc else None
    admin_anchor = max(event.ended_at_utc, actual_at) if actual_at and event.ended_at_utc else None
    if facts.get('version') == 4 and timestamp(facts.get('fueler_work_end_at')) is not None and event.ended_at_utc:
        admin_anchor=max(event.ended_at_utc,timestamp(facts['fueler_work_end_at']))
    admin = max(Decimal(0), minutes(admin_anchor, completed_at)) if admin_anchor and completed_at else None
    expected_fueler = pump + allowance if pump is not None and allowance is not None else None
    truck_actual=actual
    expected_truck=pump
    interval=dict(truck_elapsed_minutes=actual,excluded_wait_minutes=Decimal(0),excluded_wait_intervals=[])
    resources=facts.get('resource_timing') or {}
    if facts.get('version') == 4:
        from app.services.neoscorpion_resource_timing import truck_interval
        interval=truck_interval(resources,event.started_at_utc,event.ended_at_utc)
        truck_actual=interval['truck_actual_minutes']
        travel=number(resources.get('expected_travel_minutes'))
        expected_truck=pump+travel if pump is not None and travel is not None and travel >= 0 else None
        truck_reasons.extend(interval['truck_timing_reasons'])
        if resources.get('availability_continuity') != 'verified' or not resources.get('availability_evidence'):
            truck_reasons.append('Continuous truck availability evidence unavailable')
        fueler_reasons.extend(resources.get('fueler_reasons',()))
        if not resources or resources.get('work_id') != event.fuel_work_state_id:
            fueler_reasons.append('Physical resource evidence identity unavailable')
            truck_reasons.append('Physical resource evidence identity unavailable')
        if timestamp(resources.get('on_at')) != timestamp(event.started_at_utc):
            fueler_reasons.append('Frozen resource ON does not match the physical event')
            truck_reasons.append('Frozen resource ON does not match the physical event')
    def score(expected, denominator, reasons):
        return int((100 * expected / denominator).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if not reasons and expected is not None and denominator is not None and denominator > 0 else None
    return dict(facts=facts, actual_minutes=actual, pump_minutes=pump,
                setup_allowance=allowance, expected_fueler=expected_fueler, expected_truck=expected_truck,
                fueler_actual_minutes=actual,truck_actual_minutes=truck_actual,**{k:v for k,v in interval.items() if k != 'truck_actual_minutes'},
                fueler_prof=score(expected_fueler, actual, fueler_reasons), truck_prof=score(expected_truck, truck_actual, truck_reasons),
                fueler_reasons=tuple(dict.fromkeys(fueler_reasons)), truck_reasons=tuple(dict.fromkeys(truck_reasons)),
                wrap_minutes=wrap, admin_minutes=admin, actuals_at=actual_at, ready_at=ready_at,
                completed_at=completed_at)


def load_jobs(gateway_id, *, operation_id=None, limit=2000, offset=0):
    """One bounded joined query, including excluded jobs; no per-row lookups."""
    query = db.session.query(NeoScorpionFuelingEvent, SortDateMission, SortDateOperation, User, NeoScorpionFuelTruck).join(
        NeoScorpionFuelAssignment, NeoScorpionFuelAssignment.id == NeoScorpionFuelingEvent.fuel_assignment_id).join(
        SortDateMission, SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id).join(
        SortDateOperation, SortDateOperation.id == NeoScorpionFuelingEvent.sort_date_operation_id).outerjoin(
        User, User.id == NeoScorpionFuelingEvent.fueler_user_id).join(
        NeoScorpionFuelTruck, NeoScorpionFuelTruck.id == NeoScorpionFuelingEvent.fuel_truck_id).filter(
        SortDateOperation.gateway_id == gateway_id)
    if operation_id is not None:
        query = query.filter(NeoScorpionFuelingEvent.sort_date_operation_id == operation_id)
    else:
        query = query.filter(NeoScorpionFuelingEvent.ended_at_utc >= datetime.utcnow()-timedelta(days=90))
    rows = query.order_by(NeoScorpionFuelingEvent.ended_at_utc.desc(), NeoScorpionFuelingEvent.id.desc()).limit(limit).offset(offset).all()
    return [dict(event=e, mission=m, operation=o, fueler=u, truck=t, **event_math(e)) for e,m,o,u,t in rows]


def load_observation_jobs(gateway_id, *, operation_id=None, limit=2000):
    """Compact live-read projection; excludes archived forecast arrays and names."""
    model = NeoScorpionFuelingEvent
    keys = ("version", "aircraft_type", "fueler_id", "truck_id", "pump_rate", "setup_allowance",
            "fueler_assigned_at", "truck_assigned_at", "on_source", "exclusions", "resource_timing")
    columns = [model.id, model.sort_date_operation_id, model.fuel_assignment_id, model.cycle_number,
               model.fueler_user_id, model.fuel_truck_id, model.started_at_utc, model.ended_at_utc,
               model.transfer_fuel_gallons, model.event_type,model.fuel_work_state_id]
    event_columns=tuple(columns)
    columns.extend((model.performance_snapshot[key].as_json() if key in {"exclusions","resource_timing"} else
                    model.performance_snapshot[key].as_integer() if key in {"version","fueler_id","truck_id"} else
                    model.performance_snapshot[key].as_string()).label("fact_"+key) for key in keys)
    query = db.session.query(*columns).join(SortDateOperation,SortDateOperation.id == model.sort_date_operation_id).filter(
        SortDateOperation.gateway_id == gateway_id)
    if operation_id is not None:
        query = query.filter(model.sort_date_operation_id == operation_id)
    else:
        query = query.filter(model.ended_at_utc >= datetime.utcnow()-timedelta(days=90))
    rows = query.order_by(model.ended_at_utc.desc(),model.id.desc()).limit(limit).all()
    result = []
    for row in rows:
        facts = {key:getattr(row,"fact_"+key) for key in keys}
        facts["exclusions"] = facts["exclusions"] or []
        e = SimpleNamespace(**{column.key:getattr(row,column.key) for column in event_columns}, performance_snapshot=facts)
        result.append(dict(event=e,**event_math(e)))
    return result


def totals(jobs, resource, identifier=None):
    expected, actual, count = Decimal(0), Decimal(0), 0
    for job in jobs:
        event = job["event"]
        if identifier is not None and getattr(event, "fueler_user_id" if resource == "fueler" else "fuel_truck_id") != identifier:
            continue
        if job[resource+"_reasons"]:
            continue
        expected += job["expected_"+resource]
        actual += job.get(resource+'_actual_minutes',job['actual_minutes'])
        count += 1
    return dict(jobs=count, expected=expected, actual=actual,
                prof=int((100*expected/actual).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if actual > 0 else None)


def report_context(gateway, operation, *, fueler_id=None, truck_id=None, page=1):
    from app.services.gateway_matrix import gateway_timezone
    # Imports above follow the app's canonical timezone helpers; the route
    # supplies only an operation already checked against the current gateway.
    jobs = load_jobs(gateway.id, operation_id=operation.id, limit=5001) if operation else []
    truncated = len(jobs) > 5000
    jobs = jobs[:5000]
    fuelers = {j["event"].fueler_user_id: j["fueler"].display_name for j in jobs if j["fueler"]}
    trucks = {j["event"].fuel_truck_id: j["truck"].truck_number for j in jobs}
    selected = [j for j in jobs if (fueler_id is None or j["event"].fueler_user_id == fueler_id)
                and (truck_id is None or j["event"].fuel_truck_id == truck_id)]
    zone = gateway_timezone(gateway)
    def clock(value):
        value = timestamp(value)
        return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M:%S %Z") if value else "—"
    def display(value):
        return str(number(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if number(value) is not None else "—"
    for job in selected:
        e, facts = job["event"], job["facts"]
        job["columns"] = {
            "Policy version": f"v{facts.get('version','unknown')}" + (' · physical ON→OFF (legacy policy preserved)' if facts.get('version')==3 else ' · resource-aware'),
            "Flight": job["mission"].flight_number, "Tail": e.tail_number,
            "Aircraft": facts.get("aircraft_type", "UNKNOWN"),
            "Fueler": job["fueler"].display_name if job["fueler"] else "UNKNOWN",
            "Truck": job["truck"].truck_number, "Sort": job["operation"].sort_name,
            "Date": str(job["operation"].sort_date), "Cycle": e.cycle_number, "Segment": e.sequence_number,
            "Fueler assigned": clock(facts.get("fueler_assigned_at")), "Truck assigned": clock(facts.get("truck_assigned_at")),
            "Advance lead min": display(facts.get("lead_minutes")), "Advance window min": display(facts.get("advance_window")),
            "Setup Time min": display(facts.get("setup_minutes")), "Setup allowance min": display(job["setup_allowance"]),
            "Setup reason": facts.get("setup_reason", "Historical policy unavailable"),
            "Preparation ready": clock(facts.get("preparation_completed_at")),
            "T/F gallons": e.transfer_fuel_gallons, "Pump gal/min": display(facts.get("pump_rate")),
            "Pump calculation": f"{e.transfer_fuel_gallons or '—'} ÷ {display(facts.get('pump_rate'))} = {display(job['pump_minutes'])} min",
            "TRUCK ON": clock(e.started_at_utc), "ON source": facts.get("on_source", "legacy/unknown"),
            "TRUCK OFF": clock(e.ended_at_utc), "Physical elapsed sec": display(job["actual_minutes"]*60 if job["actual_minutes"] is not None else None),
            "Physical elapsed min": display(job["actual_minutes"]),
            "Fueler expected min": display(job["expected_fueler"]), "Truck expected min": display(job["expected_truck"]),
            "Fueler formula": f"100 × ({display(job['setup_allowance'])} + {display(job['pump_minutes'])}) ÷ {display(job['actual_minutes'])}",
            "Truck formula": (f"100 × ({display((facts.get('resource_timing') or {}).get('expected_travel_minutes'))} travel + {display(job['pump_minutes'])} pump) ÷ ({display(job['truck_elapsed_minutes'])} eligible − {display(job['excluded_wait_minutes'])} proven wait)"
                              if facts.get('version')==4 else f"100 × {display(job['pump_minutes'])} ÷ {display(job['actual_minutes'])}"),
            "Fueler PROF": job["fueler_prof"] if job["fueler_prof"] is not None else "—",
            "Truck PROF": job["truck_prof"] if job["truck_prof"] is not None else "—",
            "Actual readings received": clock(job["actuals_at"]), "Wrap-up min": display(job["wrap_minutes"]),
            "Completion ready": clock(job["ready_at"]), "Mission completed": clock(job["completed_at"]),
            "Other administrative delay min": display(job["admin_minutes"]),
            "Exclusions": "; ".join(dict.fromkeys([*job["fueler_reasons"], *job["truck_reasons"]])) or "QUALIFIED",
            "Forecasts known before OFF": "; ".join(f"At {clock(p.get('observed_at'))}: 100 PROF {clock(p.get('goal'))}, likely {clock(p.get('likely'))} ({p.get('source')})" for p in facts.get("forecasts", [])) or "Unavailable",
        }
        r=facts.get('resource_timing') or {}
        job['columns'].update({
            'Previous truck work ID':r.get('truck_previous_work_id') or '—',
            'Previous truck physical release':clock(r.get('truck_previous_release_at')),
            'Effective truck assignment':clock(r.get('truck_assigned_at')),
            'Eligible truck staging/start':clock(r.get('truck_eligible_start_at')),
            'Aircraft staging eligibility':clock(r.get('aircraft_staging_at')),
            'Aircraft preparation access':clock(r.get('aircraft_access_at')),
            'Aircraft access source':r.get('aircraft_access_source','v3 / unavailable'),
            'Previous / destination ramp':f"{r.get('previous_ramp') or '—'} / {r.get('destination_ramp') or '—'}",
            'Frozen expected ramp travel min':display(r.get('expected_travel_minutes')),
            'Pre-ON elapsed min (not measured travel)':display(minutes(r.get('truck_eligible_start_at'),e.started_at_utc)),
            'Truck eligible elapsed min':display(job['truck_elapsed_minutes']),
            'Proven Fueler-wait exclusion min':display(job['excluded_wait_minutes']),
            'Truck adjusted actual min':display(job['truck_actual_minutes']),
            'Excluded intervals and evidence':'; '.join(f"{clock(i['start'])} → {clock(i['end'])}: {i['minutes']} min; prior work {i['previous_work_id']}; {i['reason']}" for i in job['excluded_wait_intervals']) or 'None proven',
            'Waiting uncertainty':r.get('waiting_note','v3 physical-only policy'),
            'Previous Fueler work ID':r.get('fueler_previous_work_id') or '—',
            'Previous Fueler physical OFF':clock(r.get('fueler_previous_off_at')),
            'Previous Fueler genuine work-end':clock(r.get('fueler_previous_release_at')),
            'Fueler work-end source':r.get('fueler_release_source','v3 / unavailable'),
            'Fueler available preparation start':clock(r.get('fueler_available_preparation_at')),
            'Truck effective physical readiness':clock(r.get('truck_readiness_at')),
            'Advance preparation verified':str(r.get('preparation_qualified','v3 assignment-only rule')),
            'Evidence observed at ON':clock(r.get('observed_at')),
            'Later release evidence observed':clock(r.get('final_observed_at')),
            'Truck availability continuity':r.get('availability_continuity','Unavailable / legacy'),
            'Truck availability observations':'; '.join(f"{clock(i.get('at'))}: {i.get('scope')} {i.get('status', 'available' if i.get('available') else 'unavailable')}; actor {i.get('actor_id') or 'unknown'}; {i.get('source')}" for i in r.get('availability_evidence',[])) or 'Unavailable / legacy',
            'Fueler final required data saved':clock((facts.get('fueler_data_ready') or {}).get('at')),
            'Fueler genuine work-end':clock(facts.get('fueler_work_end_at')),
            'Other final Fueler data after Actuals min':display(max(Decimal(0),minutes(max(e.ended_at_utc,job['actuals_at']),facts.get('fueler_work_end_at'))))
                if e.ended_at_utc and job['actuals_at'] and timestamp(facts.get('fueler_work_end_at')) else '—',
        })
    start = (max(1, page)-1)*50
    summaries = {}
    for resource in ("fueler", "truck"):
        valid = [j for j in selected if not j[resource+"_reasons"]]
        summaries[resource] = {**totals(selected, resource),
            "expected_components": " + ".join(display(j["expected_"+resource]) for j in valid) or "0",
            "actual_components": " + ".join(display(j[resource+'_actual_minutes']) for j in valid) or "0"}
    copy_text = "\n".join(f"{resource.upper()} PROF: 100 × ({summary['expected_components']}) ÷ ({summary['actual_components']}) = {summary['prof'] if summary['prof'] is not None else '—'}" for resource,summary in summaries.items())+"\n\n"+"\n\n".join("\n".join(f"{k}: {v}" for k,v in j["columns"].items()) for j in selected)
    return dict(jobs=selected[start:start+50], all_jobs=selected, count=len(selected), page=max(1,page),
                has_next=start+50 < len(selected), summaries=summaries, fuelers=fuelers, trucks=trucks,
                copy_text=copy_text, truncated=truncated)
