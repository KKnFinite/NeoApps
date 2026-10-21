"""Read-only truck history assembled from canonical physical and audit records.

No inventory simulation: balances are shown only when a record actually stored
them. A physical transfer already covered by a later reading has zero additional
ledger movement. Missing snapshots never become a guessed running balance.
"""
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import (NeoScorpionFuelAssignment as Assignment, NeoScorpionFuelWorkState as Work,
    NeoScorpionFuelingEvent as FuelEvent, NeoScorpionFuelTruck as Truck,
    NeoScorpionSortTruck as Selection, NeoScorpionTruckInventoryAudit as InventoryAudit,
    NeoScorpionFuelAuditEntry as FuelAudit, NeoScorpionTruckDriverAudit as DriverAudit,
    NeoScorpionVendorAudit as VendorAudit, NeoScorpionTopOffEvent as TopOff, User)
from app.services.gateway_matrix import gateway_timezone
from app.services.neoscorpion_prof import timestamp

LIMIT = 10000
UNKNOWN = 'Unavailable'


def _truck_id(value):
    return int(value) if str(value or '').isdigit() and int(value) > 0 else None


def _sent_truck_receipt(audit):
    return bool(audit.field_name == 'assigned_truck_id' and audit.changed_by_user_id and (
        (audit.action == 'swap_truck' and audit.reason in {
            'Dispatcher recorded the truck assignment.', 'Dispatcher changed the assigned fuel truck.'})
        or (audit.action == 'segment_transfer' and audit.reason.startswith(
            ('Dispatcher assigned resources to a new ', 'Activated Dispatcher-planned truck from validated source submission.')))))


def _truck_change_evidence(audits, works, posted):
    """Read-only proof at the instant of a change, never today's assignment.

    ASSIGN/UPDATE receipts are emitted only by the committed Dispatcher action;
    dropdown selection, fuel autosave and pending-plan saves are not receipts.
    A later ON cannot retroactively turn an earlier planning edit into a swap.
    """
    previous = {}
    physical = defaultdict(list)
    work_by_id = {w.id:w for w in works}
    for work in works:
        facts = work.performance_context or {}
        at = work.manual_on_at_utc or ((work.confirmed_on_at_utc or work.on_at_utc)
            if facts.get('on_source') in {'manual','trusted automatic'} else None)
        if at and work.physical_truck_id:
            physical[work.fuel_assignment_id].append((at,work.physical_truck_id,work.physical_cycle_number,
                work.off_at_utc or work.ended_early_at_utc))
    for event in posted:
        work = work_by_id.get(event.fuel_work_state_id)
        proven = ((event.performance_snapshot or {}).get('on_source') in {'manual','trusted automatic'}
            or (work and work.manual_on_at_utc == event.started_at_utc and work.physical_truck_id == event.fuel_truck_id))
        if event.started_at_utc and proven:
            physical[event.fuel_assignment_id].append((event.started_at_utc,event.fuel_truck_id,event.cycle_number,event.ended_at_utc))

    result = {}
    for row in sorted(audits,key=lambda a:(a.created_at,a.id)):
        if row.field_name != 'assigned_truck_id' and row.action not in {'swap_truck','unassign_truck'}:
            continue
        old,new = _truck_id(row.old_value),_truck_id(row.new_value)
        prior = previous.get(row.fuel_assignment_id)
        work = work_by_id.get(row.fuel_work_state_id)
        cycle = work.physical_cycle_number if work else None
        boundary = prior.created_at if prior else datetime.min
        candidates = [p for p in physical[row.fuel_assignment_id]
                      if p[0] <= row.created_at and (not cycle or p[2] == cycle)
                      and (boundary <= p[0] or p[3] is None or p[3] >= row.created_at)]
        last_at = max((p[0] for p in candidates),default=None)
        physical_proof = bool(old and {p[1] for p in candidates if p[0] == last_at} == {old})
        prior_work = work_by_id.get(prior.fuel_work_state_id) if prior else None
        receipt_proof = bool(prior and _sent_truck_receipt(prior) and _truck_id(prior.new_value) == old
            and prior.created_at < row.created_at and (not work or not prior_work
                or work.physical_cycle_number == prior_work.physical_cycle_number))
        if row.action == 'swap_truck':
            result[row.id] = (('Physical ON before change' if physical_proof else
                'Prior committed Dispatcher assignment' if _sent_truck_receipt(row) and receipt_proof else None)
                if old and old != new else None)
        # Even an unknown/unverified edit breaks a previous resource episode.
        # Never borrow a historical A receipt for a later unsent A→B change.
        previous[row.fuel_assignment_id] = row
    return result


def quantity(value):
    return f'{value:,}' if value is not None else UNKNOWN


def report_context(gateway, operation, *, truck_id=None):
    if operation is not None and operation.gateway_id != gateway.id:
        raise ValueError('Sort belongs to another gateway.')
    if operation is None:
        return dict(trucks={}, summaries=[], fleet={}, events=[], copy_text='No sort available.', truncated=False, export_rows=[])
    truncated = False

    def load(query):
        nonlocal truncated
        rows = query.limit(LIMIT+1).all()
        truncated |= len(rows) > LIMIT
        return rows[:LIMIT]

    assignments = {a.id:a for a in load(Assignment.query.options(joinedload(Assignment.sort_date_mission)).filter_by(
        sort_date_operation_id=operation.id).order_by(Assignment.id))}
    selections = {s.fuel_truck_id:s for s in load(Selection.query.filter_by(sort_date_operation_id=operation.id).order_by(Selection.id))}
    posted = load(FuelEvent.query.filter_by(sort_date_operation_id=operation.id).order_by(FuelEvent.id))
    works = load(Work.query.join(Assignment, Assignment.id == Work.fuel_assignment_id).filter(
        Assignment.sort_date_operation_id == operation.id,
        Work.performance_context['duplicate_of_work_id'].as_integer().is_(None)).order_by(Work.id))
    work_by_id = {work.id:work for work in works}
    inventory = load(InventoryAudit.query.filter_by(sort_date_operation_id=operation.id).order_by(InventoryAudit.id))
    fuel_audits = load(FuelAudit.query.filter_by(sort_date_operation_id=operation.id).order_by(FuelAudit.id))
    drivers = load(DriverAudit.query.filter_by(sort_date_operation_id=operation.id).order_by(DriverAudit.id))
    vendor = load(VendorAudit.query.filter_by(sort_date_operation_id=operation.id).order_by(VendorAudit.id))
    trips = load(TopOff.query.filter_by(sort_date_operation_id=operation.id).order_by(TopOff.id))
    ids = set(selections) | {e.fuel_truck_id for e in posted+inventory+drivers+trips}
    ids |= {w.physical_truck_id for w in works if w.physical_truck_id}
    ids |= {v.fuel_truck_id for v in vendor if v.fuel_truck_id}
    ids |= {a.assigned_truck_id for a in assignments.values() if a.assigned_truck_id}
    ids |= {_truck_id(value) for a in fuel_audits if a.field_name == 'assigned_truck_id'
            for value in (a.old_value,a.new_value) if _truck_id(value)}
    trucks = {t.id:t.truck_number for t in Truck.query.filter(Truck.gateway_id == gateway.id, Truck.id.in_(ids)).order_by(Truck.truck_number,Truck.id).all()}
    if truck_id is not None and truck_id not in trucks:
        raise ValueError('Truck not found in this sort.')
    user_ids = set()
    for rows, fields in ((posted,('fueler_user_id',)), (works,('physical_fueler_user_id','manual_on_by_user_id','off_by_user_id','ended_early_by_user_id')),
            (inventory,('actor_user_id',)), (fuel_audits,('changed_by_user_id',)),
            (drivers,('driver_user_id','previous_driver_user_id','changed_by_user_id')), (vendor,('actor_user_id',)),
            (trips,('actor_user_id',)), (list(assignments.values()),('resource_assignment_by_user_id','completed_by_user_id'))):
        user_ids.update(getattr(row, field) for row in rows for field in fields if getattr(row, field))
    names = {u.id:u.display_name for u in User.query.filter(User.id.in_(user_ids)).all()}
    zone = ZoneInfo(gateway_timezone(gateway))

    def utc(value):
        value = timestamp(value)
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value

    def clock(value):
        value = utc(value)
        return value.replace(tzinfo=timezone.utc).astimezone(zone).strftime('%Y-%m-%d %H:%M:%S %Z') if value else UNKNOWN

    driver_history = defaultdict(list)
    for row in drivers:
        driver_history[row.fuel_truck_id].append((row.created_at, 0, row.id, names.get(row.driver_user_id,'UNKNOWN')))
    for row in vendor:
        if row.action == 'manual_driver':
            driver_history[row.fuel_truck_id].append((row.created_at, 1, row.id, (row.facts or {}).get('name') or 'UNKNOWN'))
    for history in driver_history.values():
        history.sort()

    def driver_at(identifier, at):
        history = [h for h in driver_history[identifier] if at and utc(h[0]) <= utc(at)]
        return history[-1][3] if history else 'UNKNOWN / historical identity unavailable'

    events = []
    assignment_audits = []

    def add(identifier, at, label, source, *, actor=None, work=None, event=None, facts=None,
            change=None, before=None, after=None, note='', duration=None, driver=None, audit_only=False, assignment_id=None):
        if identifier not in trucks or (truck_id is not None and truck_id != identifier):
            return
        facts = facts or {}
        assignment = assignments.get(event.fuel_assignment_id if event else work.fuel_assignment_id if work else assignment_id)
        mission = assignment.sort_date_mission if assignment else None
        tail = event.tail_number if event else work.tail_number if work else ''
        columns = {
            'Time':clock(at), 'UTC':utc(at).isoformat()+'Z' if at else UNKNOWN,
            'Sort':f'{operation.sort_date} · {operation.sort_name} · #{operation.id}',
            'Truck':trucks[identifier], 'Event':label, 'Source ID':source,
            'Mission':mission.id if mission else '', 'Flight':mission.flight_number if mission else '',
            'Destination':mission.destination if mission else '', 'Tail':tail,
            'Cycle':event.cycle_number if event else work.physical_cycle_number if work else '',
            'Work ID':event.fuel_work_state_id if event else work.id if work else facts.get('audit_work_id',''),
            'Parking':facts.get('parking') or UNKNOWN,
            'Fueler':names.get(event.fueler_user_id if event else work.physical_fueler_user_id if work else None,'UNKNOWN'),
            'Driver':driver or driver_at(identifier, at), 'Actor':names.get(actor,'UNKNOWN'),
            'ON':clock(event.started_at_utc if event else work.manual_on_at_utc or work.confirmed_on_at_utc or work.on_at_utc if work else None),
            'OFF':clock(event.ended_at_utc if event else work.off_at_utc or work.ended_early_at_utc if work else None),
            'T/F gal':quantity(event.transfer_fuel_gallons if event else work.physical_transfer_gallons if work else None),
            'Duration min':f'{duration:.2f}' if duration is not None else UNKNOWN,
            'Change gal':f'{change:+,}' if change is not None else UNKNOWN,
            'Before gal':quantity(before), 'Balance gal':quantity(after), 'Notes':note,
        }
        (assignment_audits if audit_only else events).append(
            dict(at=utc(at), source=source, truck_id=identifier, columns=columns))

    summary = {}
    for identifier, number in trucks.items():
        if truck_id is not None and truck_id != identifier:
            continue
        selection = selections.get(identifier)
        summary[identifier] = dict(truck=number, starting=selection.starting_gallons if selection else None,
            ending=selection.current_gallons if selection else None, verified=bool(selection and selection.inventory_verified),
            visits=0, physical_minutes=0, duration_unknown=False, uplift=0, defuel=0, transfer_unknown=False,
            top_off_visits=0, top_off_added=0, top_off_unknown=False, adjustments=0, adjustment_unknown=False,
            oos_minutes=None, driver=driver_at(identifier,datetime.max), status=selection.status if selection else UNKNOWN)
        if selection:
            add(identifier, selection.created_at, 'ADDED / STARTING FUEL', f'asset:{selection.id}',
                after=selection.starting_gallons, note='Opening snapshot. Current balance is shown separately; no inferred historical running balance.')
            if selection.top_off_sent_at_utc and not any(t.kind == 'sent' and t.fuel_truck_id == identifier
                    and t.occurred_at_utc == selection.top_off_sent_at_utc for t in trips):
                summary[identifier]['top_off_visits'] += 1
                summary[identifier]['top_off_unknown'] = True
                add(identifier,selection.top_off_sent_at_utc,'TOP OFF SENT (LEGACY)',f'asset:{selection.id}:topoff',
                    note='Last retained sent timestamp only. Departure fuel, actor and complete prior roundtrips were not snapshotted.')

    by_work = defaultdict(list)
    event_audit = defaultdict(list)
    for row in inventory:
        if row.fueling_event_id:
            event_audit[row.fueling_event_id].append(row)
    for event in posted:
        by_work[event.fuel_work_state_id].append(event)
    assigned_keys = set()
    for work in works:
        history = by_work.get(work.id) or [None]
        for event in history:
            identifier = event.fuel_truck_id if event else work.physical_truck_id
            if identifier not in summary:
                continue
            facts = (event.performance_snapshot if event else work.performance_context) or {}
            owns_work = event is None or (event.sequence_number == work.physical_sequence_number
                and event.fuel_truck_id == work.physical_truck_id)
            on = event.started_at_utc if event else work.manual_on_at_utc or work.confirmed_on_at_utc or work.on_at_utc
            off = event.ended_at_utc if event else work.off_at_utc or work.ended_early_at_utc
            source = f'fuel:{event.id}' if event else f'work:{work.id}'
            lead = utc(facts.get('truck_assigned_at'))
            key = (identifier,work.fuel_assignment_id,lead)
            if lead and key not in assigned_keys:
                assigned_keys.add(key)
                add(identifier,lead,'MISSION ASSIGNED / PREASSIGNED',source+':assigned',work=work,event=event,facts=facts,
                    note='Assignment is not proof of physical arrival.')
            if event is not None or on:
                summary[identifier]['visits'] += 1
            if on:
                add(identifier,on,'TRUCK ON',source+':on',actor=work.manual_on_by_user_id if owns_work else None,work=work,event=event,facts=facts,
                    note='ON provenance: '+str(facts.get('on_source') or ('manual' if event is None and work.manual_on_at_utc else 'legacy / unverified')))
            duration = (utc(off)-utc(on)).total_seconds()/60 if on and off and utc(off) >= utc(on) else None
            if off:
                trusted = (event is None and work.manual_on_at_utc is not None) or facts.get('on_source') in {'manual', 'trusted automatic'}
                if duration is None or not trusted:
                    summary[identifier]['duration_unknown'] = True
                else:
                    summary[identifier]['physical_minutes'] += duration
                add(identifier,off,'TRUCK OFF / RELEASED' if not work.ended_early_at_utc else 'END EARLY / RELEASED',source+':off',
                    actor=(work.off_by_user_id or work.ended_early_by_user_id) if owns_work else None,work=work,event=event,facts=facts,duration=duration,
                    note='Physical release, NOT Dispatcher COMPLETE.' + (' Inventory posting pending.' if event is None else ''))
            if event:
                audits = event_audit[event.id]
                covered = next((a for a in audits if a.action == 'verified' and a.transfer_fuel_gallons is not None),None)
                discrepancy = next((a for a in audits if a.action == 'discrepancy'),None)
                gallons = event.transfer_fuel_gallons
                if gallons is None:
                    summary[identifier]['transfer_unknown'] = True
                else:
                    summary[identifier]['defuel' if event.event_type == 'defuel' else 'uplift'] += gallons
                change = None if discrepancy or gallons is None else 0 if covered else gallons if event.event_type == 'defuel' else -gallons
                add(identifier,event.created_at,event.event_type.upper()+' POSTED',source,work=work,event=event,facts=facts,
                    actor=covered.actor_user_id if covered else discrepancy.actor_user_id if discrepancy else work.physical_finalized_by_user_id if owns_work else None,
                    change=change, before=covered.prior_recorded_gallons if covered else discrepancy.prior_recorded_gallons if discrepancy else None,
                    after=covered.confirmed_gallons if covered else None,
                    note='UNVERIFIED: '+discrepancy.reason if discrepancy else 'Already covered by verified inventory reading; no second deduction.' if covered else 'Canonical posted transfer. Historical balance snapshot not recorded.')
                if event.event_type == 'defuel':
                    add(identifier,event.created_at,'NEEDS SUMP',source+':sump',work=work,event=event)
            completed = utc(facts.get('completed_at'))
            assignment = assignments.get(work.fuel_assignment_id)
            if not completed and assignment and assignment.active_work_state_id == work.id:
                completed = assignment.completed_at_utc
            if completed:
                current_completion = assignment and assignment.active_work_state_id == work.id and assignment.completed_at_utc == completed
                add(identifier,completed,'DISPATCHER COMPLETE' if current_completion else 'SEGMENT FINALIZED (DISPATCH)',source+':complete',work=work,event=event,
                    actor=work.physical_finalized_by_user_id if owns_work else None,
                    note='Administrative finalization; physical OFF is separate. Segment finalization alone does not prove replacement-mission completion.')

    for assignment in assignments.values():
        key=(assignment.assigned_truck_id,assignment.id,assignment.truck_assigned_at_utc)
        if assignment.truck_assigned_at_utc and key not in assigned_keys:
            assigned_keys.add(key)
            work=work_by_id.get(assignment.active_work_state_id)
            add(assignment.assigned_truck_id,assignment.truck_assigned_at_utc,'MISSION ASSIGNED / PREASSIGNED',
                f'assignment:{assignment.id}',actor=assignment.resource_assignment_by_user_id,work=work,
                note=f'Mission {assignment.sort_date_mission.flight_number}; assignment is not physical arrival.')
    for row in drivers:
        add(row.fuel_truck_id,row.created_at,'DRIVER CHANGE',f'driver:{row.id}',actor=row.changed_by_user_id,
            note=f'{names.get(row.previous_driver_user_id,"UNKNOWN")} → {names.get(row.driver_user_id,"UNKNOWN")} · {row.reason}')
    for row in vendor:
        if row.action == 'manual_driver':
            add(row.fuel_truck_id,row.created_at,'MANUAL DRIVER',f'vendor:{row.id}',actor=row.actor_user_id,
                driver=(row.facts or {}).get('name') or 'UNKNOWN')
    # One applied movement per canonical inventory audit; request records are not movements.
    returned = {(t.fuel_truck_id,t.occurred_at_utc) for t in trips if t.kind == 'returned'}
    for row in inventory:
        if row.fueling_event_id or row.action != 'verified':
            continue
        if (row.fuel_truck_id,row.created_at) in returned and row.reason.startswith('Top Off Complete'):
            continue
        delta = row.confirmed_gallons-row.prior_recorded_gallons if row.confirmed_gallons is not None and row.prior_recorded_gallons is not None else None
        label = 'TOP OFF RETURN (LEGACY)' if row.reason.startswith('Top Off Complete') else 'INVENTORY VERIFIED / CORRECTED'
        if row.fuel_truck_id in summary:
            target=summary[row.fuel_truck_id]
            if label.startswith('TOP OFF'):
                target['top_off_unknown']=True
            elif delta is None:
                target['adjustment_unknown']=True
            else:
                target['adjustments'] += delta
        add(row.fuel_truck_id,row.created_at,label,f'inventory:{row.id}',actor=row.actor_user_id,
            before=row.prior_recorded_gallons,after=row.confirmed_gallons,change=delta,
            note=row.reason+' Change is relative to the recorded baseline, not an invented physical balance.')
    trip_map={t.id:t for t in trips}
    for row in trips:
        if row.fuel_truck_id not in summary:
            continue
        sent=trip_map.get(row.sent_event_id)
        delta=(row.gallons-sent.gallons) if sent and sent.inventory_verified and sent.gallons is not None else None
        duration=(row.occurred_at_utc-sent.occurred_at_utc).total_seconds()/60 if sent else None
        target=summary[row.fuel_truck_id]
        if row.kind == 'sent':
            target['top_off_visits'] += 1
        elif delta is None:
            target['top_off_unknown']=True
        else:
            target['top_off_added'] += delta
        add(row.fuel_truck_id,row.occurred_at_utc,'TOP OFF '+row.kind.upper(),f'topoff:{row.id}',actor=row.actor_user_id,
            driver=row.driver_name,change=delta if row.kind == 'returned' else None,
            before=sent.gallons if sent and sent.inventory_verified else None,
            after=row.gallons if row.inventory_verified else None,duration=duration,
            note=f'Inventory epoch {row.inventory_epoch}.'+(' Sent record unavailable; historical trip boundaries unknown.' if row.kind == 'returned' and not sent else ''))
    change_evidence = _truck_change_evidence(fuel_audits, works, posted)
    for row in fuel_audits:
        if row.action not in {'swap_truck','unassign_truck','auto_hold','resume_hold','reopen_off'} and row.field_name != 'duplicate_visit_reconciliation':
            continue
        work=work_by_id.get(row.fuel_work_state_id)
        # Initial committed assignments are assignments, not truck swaps. Keep
        # their history even if today's resource/receipt has since changed.
        initial_truck = _truck_id(row.new_value)
        if (row.action == 'swap_truck' and not _truck_id(row.old_value) and initial_truck
                and _sent_truck_receipt(row) and row.field_name == 'assigned_truck_id'):
            key=(initial_truck,row.fuel_assignment_id,row.created_at)
            if key not in assigned_keys:
                assigned_keys.add(key)
                add(initial_truck,row.created_at,'MISSION ASSIGNED / PREASSIGNED',f'audit:{row.id}:assigned',
                    actor=row.changed_by_user_id,assignment_id=row.fuel_assignment_id,
                    note='Committed Dispatcher assignment; not proof of physical arrival.')
        identifiers=set()
        if work and work.physical_truck_id and row.action not in {'swap_truck','unassign_truck'}:
            identifiers.add(work.physical_truck_id)
        if row.action in {'swap_truck','unassign_truck'}:
            for value in (row.old_value,row.new_value):
                if str(value or '').isdigit():identifiers.add(int(value))
        for identifier in identifiers:
            proof = change_evidence.get(row.id)
            audit_only = row.action == 'swap_truck' and not proof
            label = ('UNASSIGN TRUCK' if row.action == 'swap_truck' and not _truck_id(row.new_value)
                     else row.action.replace('_',' ').upper())
            add(identifier,row.created_at,'ASSIGNMENT AUDIT (NOT OPERATIONAL)' if audit_only else label,f'audit:{row.id}',
                actor=row.changed_by_user_id,work=None if row.action in {'swap_truck','unassign_truck'} else work,
                assignment_id=row.fuel_assignment_id,audit_only=audit_only,facts={'audit_work_id':row.fuel_work_state_id},
                note=(f'{row.new_value}. {row.reason}' if row.field_name=='duplicate_visit_reconciliation'
                    else f'{row.old_value or "—"} → {row.new_value or "—"}. {row.reason}')
                    + (' Prior sent assignment / physical change not established; initial or planning evidence only.' if audit_only
                       else f' Evidence: {proof}.' if proof else ''))
    events.sort(key=lambda e:(e['at'] or datetime.min,e['source']))
    summaries=list(summary.values())
    fleet={key:sum(s[key] for s in summaries) if all(s[key] is not None for s in summaries) else None
           for key in ('starting','ending','visits','physical_minutes','uplift','defuel','top_off_visits','top_off_added','adjustments')}
    fleet.update(verified=all(s['verified'] for s in summaries), duration_unknown=any(s['duration_unknown'] for s in summaries),
        transfer_unknown=any(s['transfer_unknown'] for s in summaries), top_off_unknown=any(s['top_off_unknown'] for s in summaries),
        adjustment_unknown=any(s['adjustment_unknown'] for s in summaries),oos_minutes=None)
    copy_text=f'TRUCK ROLLDOWN · {gateway.code} · {operation.sort_date} · {operation.sort_name}\n'
    copy_text+='Historical gaps: no inferred balances, travel, OOS durations or driver identities. Flight/destination identify the linked mission; parking is shown only if snapshotted.\n'
    summary_rows=[]
    for s in ([dict(fleet,truck='ALL TRUCKS')] if truck_id is None and summaries else [])+summaries:
        details=(f"START {quantity(s['starting'])}; CURRENT {quantity(s['ending'])} {'VERIFIED' if s['verified'] else 'UNVERIFIED'}; "
            f"VISITS {s['visits']}; PHYSICAL MIN {s['physical_minutes']:.2f}{' PARTIAL' if s['duration_unknown'] else ''}; "
            f"UPLIFT {s['uplift']:,}; DEFUEL {s['defuel']:,}{' PARTIAL' if s['transfer_unknown'] else ''}; "
            f"TOP OFF SENT {s['top_off_visits']}; KNOWN ADDED {s['top_off_added']:,}{' PARTIAL' if s['top_off_unknown'] else ''}; "
            f"SIGNED ADJUSTMENTS {s['adjustments']:,}{' PARTIAL' if s['adjustment_unknown'] else ''}; "
            f"OOS TIME Unavailable; LATEST DRIVER {s.get('driver','—')}")
        copy_text+=f"\nTRUCK {s['truck']} · {details}\n"
        if events:
            summary_rows.append({**dict.fromkeys(events[0]['columns'],''), 'Truck':s['truck'],'Event':'SUMMARY',
                                 'Balance gal':quantity(s['ending']),'Notes':details})
    copy_text+='\n'+'\n\n'.join('\n'.join(f'{key}: {value}' for key,value in e['columns'].items()) for e in events)
    assignment_audits.sort(key=lambda e:(e['at'] or datetime.min,e['source'],e['truck_id']))
    return dict(trucks=trucks,summaries=summaries,fleet=fleet,events=events,assignment_audits=assignment_audits,copy_text=copy_text,truncated=truncated,
                export_rows=summary_rows+[e['columns'] for e in events])
