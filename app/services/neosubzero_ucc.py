"""Current-sort NeoSubZero UCC ramp and staffing services."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.services.neosubzero_radio import radio_pending, radio_complete
from app.models import (
    NeoSubZeroCalloutAssignment,
    NeoSubZeroPretreatState,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingPersonQualification,
    StaffingWorkAssignment,
    SortDateMission,
    SortDateParkingAssignment,
    SortDateTailState,
    MotherBrainParkingSettings,
)
from app.services.live_collaboration import entity_version
from app.services.memory_diagnostics import memory_diagnostics
from app.services.neosubzero_constants import RAMP_ORDER
from app.services.neosubzero_staffing import current_subzero_staffing_pool


UCC_REFRESH_KEY = "neosubzero.ucc"
UCC_POSITIONS = (1, 2, 3, 4)
UCC_ROLES = ("driver", "flyer")
UCC_RAMP_ORDER = ("Alpha", "Bravo", "Charlie", "Delta", "Echo")
UCC_DISPLAY_RAMPS = (*UCC_RAMP_ORDER, "Remote")
UCC_WAITING_CAPACITY = 4
UCC_WAITING_LABELS = ('POSITION 10', 'MIDDLE 1', 'MIDDLE 2', 'POSITION 9')


def _parking_9_10_enabled(gateway):
    # Consume MotherBrain's existing gateway setting without creating/updating it.
    settings = MotherBrainParkingSettings.query.filter_by(gateway_id=gateway.id).first()
    return bool(settings and settings.include_throat_default)


class NeoSubZeroUccError(ValueError):
    """Safe operator-facing UCC validation error."""


@memory_diagnostics("neosubzero_ucc_context")
def neosubzero_ucc_context(gateway, operation):
    """Return a bounded UCC projection from canonical current-sort state."""
    from app.services.neosubzero_departure_deice import (
        departure_deice_context,
    )

    departure_context = departure_deice_context(gateway, operation)
    from app.services.neosubzero_trucks import registered_trucks
    truck_options = registered_trucks(gateway, active_only=True)
    fluid_settings = departure_context["fluid_settings"]
    parking_9_10_enabled = _parking_9_10_enabled(gateway)
    if operation is None:
        return {
            "operation": None,
            "ramps": tuple({
                "name": name, "aircraft": (), "queue": (), "throat": None, "pad": None,
                "waiting_queue": (), "slots": (), "exceptions": (),
                "location_exceptions": (), "parking_exceptions": (), "active_conflicts": (),
                "parking_positions": _parking_projection(name, (), parking_9_10_enabled),
            } for name in UCC_DISPLAY_RAMPS),
            "unlocated_aircraft": (),
            "staffing_pool": (),
            "truck_options": truck_options,
            "fluid_settings": fluid_settings,
            "assignment_index": {},
            "detached_units": (),
            "staffing_version": "",
        }

    departure_rows = _physical_aircraft_rows(operation, departure_context)
    assignment_rows = (
        NeoSubZeroUccAssignment.query.options(
            joinedload(NeoSubZeroUccAssignment.person)
        )
        .filter_by(sort_date_operation_id=operation.id)
        .all()
    )
    assignment_by_slot = {
        (row.ramp, row.position_number, row.team_role): row
        for row in assignment_rows
    }
    truck_by_position = {
        (row.ramp, row.position_number): row
        for row in NeoSubZeroUccTruckAssignment.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }
    staffing_pool = current_subzero_staffing_pool(operation)
    available_people = {
        item["person"].id: item for item in staffing_pool
    }
    rows_by_ramp = {ramp: [] for ramp in (*UCC_DISPLAY_RAMPS, "Unlocated")}
    for row in departure_rows:
        ramp = row.get("ramp")
        ramp = ramp if ramp in rows_by_ramp else "Unlocated"
        item = dict(row)
        item["visual_state"] = _visual_state(item)
        item["visual_label"] = _visual_label(item["visual_state"])
        rows_by_ramp[ramp].append(item)

    ramps = []
    for ramp in UCC_DISPLAY_RAMPS:
        aircraft = rows_by_ramp[ramp]
        aircraft.sort(key=_aircraft_sort_key)
        queue = _physical_queue(aircraft) if ramp in UCC_RAMP_ORDER else []
        active = [row for row in queue if _is_active_throat(row)]
        pad_reviews = [row for row in aircraft if row.get("pad_evidence_review")]
        pad = active[0] if len(active) == 1 and not pad_reviews else None
        waiting = [row for row in queue if not _is_active_throat(row)]
        waiting_rows = waiting[:UCC_WAITING_CAPACITY]
        throat = waiting_rows[0] if waiting_rows else None
        for queue_position, row in enumerate(waiting_rows, start=1):
            row["queue_position"] = queue_position
            row['queue_label'] = UCC_WAITING_LABELS[queue_position - 1]
        waiting_queue = tuple(waiting_rows)
        parked = [row for row in aircraft if row.get("block_out_at_utc") is None
                  and not row.get('retained_radio_throat') and row not in queue and row not in pad_reviews]
        prefix = 'ABCDE'[UCC_RAMP_ORDER.index(ramp)] if ramp in UCC_RAMP_ORDER else ''
        occupied = ({prefix+'10'} if throat else set()) | ({prefix+'09'} if len(waiting_queue) == UCC_WAITING_CAPACITY else set())
        parking = _parking_projection(ramp, [row for row in parked if row['parking'] not in occupied], parking_9_10_enabled)
        visible_codes = {place["code"] for place in parking}
        slots = []
        for position in UCC_POSITIONS if ramp in RAMP_ORDER else ():
            roles = {}
            for role in UCC_ROLES:
                assignment = assignment_by_slot.get((ramp, position, role))
                person = assignment.person if assignment else None
                roles[role] = {
                    "assignment": assignment,
                    "person": person,
                    "version": entity_version(assignment),
                    "available": bool(person and person.id in available_people),
                }
            truck = truck_by_position.get((ramp, position))
            slots.append(
                {
                    "position": position,
                    "roles": roles,
                    "truck": truck,
                    "truck_number": getattr(truck, "truck_number", None) or "",
                    "truck_version": entity_version(truck),
                }
            )
        ramps.append(
            {
                "name": ramp,
                "aircraft": tuple(aircraft),
                "throat": throat,
                "pad": pad,
                "waiting_queue": waiting_queue,
                "queue": tuple(queue),
                "queue_version": _queue_version(aircraft),
                "exceptions": tuple(waiting[UCC_WAITING_CAPACITY:]),
                "location_exceptions": tuple(row for row in aircraft if row.get("block_out_at_utc") and ramp not in UCC_RAMP_ORDER),
                "parking_positions": parking,
                "parking_exceptions": tuple(row for row in parked if row["parking"] not in visible_codes or row['parking'] in occupied),
                "active_conflicts": tuple((active if pad is None else []) + pad_reviews),
                "slots": tuple(slots),
            }
        )
    return {
        "operation": operation,
        "ramps": tuple(ramps),
        "unlocated_aircraft": tuple(sorted(rows_by_ramp["Unlocated"], key=_aircraft_sort_key)),
        "staffing_pool": staffing_pool,
        "truck_options": truck_options,
        "fluid_settings": fluid_settings,
        "staffing_version": ucc_staffing_version(operation),
        "assignment_index": {
            str(row.person_id): {"assignmentId": str(row.id), "assignmentVersion": entity_version(row)}
            for row in assignment_rows if row.person_id is not None
        },
        "detached_units": (),  # Remote teams are now always in the visible roster.
    }


def _parking_projection(ramp, parked, parking_9_10_enabled):
    from app.services.parking_plan import PARKING_RAMP_GROUPS
    positions_by_ramp = {name: positions for name, _code, positions in PARKING_RAMP_GROUPS}
    parking_codes = list(positions_by_ramp.get(ramp, ()))
    if ramp in UCC_RAMP_ORDER and not parking_9_10_enabled:
        parking_codes = [code for code in parking_codes if _position_number(code) not in (9, 10)]
    parking = []
    for code in dict.fromkeys(parking_codes):
        number = _position_number(code)
        grid_row, grid_column = None, None
        if ramp in UCC_RAMP_ORDER:
            if 1 <= number <= 4:
                grid_row, grid_column = 5 - number, 1
            elif 5 <= number <= 8:
                grid_row, grid_column = 9 - number, 3
            else:
                grid_row, grid_column = (1 if number == 10 else 4), 2
        parking.append({
            "code": code,
            "aircraft": tuple(row for row in parked if row["parking"] == code),
            "grid_row": grid_row,
            "grid_column": grid_column,
        })
    return tuple(parking)


def _tail_parking_location(state):
    """Read canonical named areas as well as numbered stands; never guess one."""
    from app.services.parking_rules import normalize_parking_position_code
    recorded = str(getattr(state, 'parking_position', '') or '').strip()
    stand = normalize_parking_position_code(recorded)
    if stand:
        return stand, stand[0]
    return {'REMOTE': ('REMOTE', 'R'), 'MOTHERBRAIN': ('MotherBrain', 'MB')}.get(recorded.upper())


def _physical_aircraft_rows(operation, departure_context):
    """Read-only, same-sort aircraft projection; never infer a parking assignment."""
    from app.services.neosubzero_departure_deice import _departure_row, _ramp_name
    from app.services.time_display import format_local_hhmm
    from app.services.parking_plan import tail_operational_status, tail_operational_status_label

    parking = {str(row.tail_number).strip().upper(): row for row in SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id).all()}
    rows = list(departure_context["rows"])
    departure_tails = {row["tail"] for row in rows}
    departures = {}
    for row in rows:
        if row.get("block_out_at_utc"):
            departures[row["tail"]] = max(departures.get(row["tail"], datetime.min), row["block_out_at_utc"])
    arrival_missions = SortDateMission.query.filter_by(sort_date_operation_id=operation.id, mission_type="arrival").order_by(SortDateMission.actual_block_in_datetime_utc.desc(), SortDateMission.id).all()
    arrivals = {}
    for mission in arrival_missions:
        if mission.actual_block_in_datetime_utc:
            tail = str(mission.assigned_tail_number or "").strip().upper()
            arrivals[tail] = max(arrivals.get(tail, datetime.min), mission.actual_block_in_datetime_utc)
    states = {str(row.tail_number).strip().upper(): row for row in SortDateTailState.query.filter_by(sort_date=operation.sort_date, gateway_code=operation.gateway_code, sort_name=operation.sort_name).all()}
    pretreat = {str(row.tail_number).strip().upper(): row for row in NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id).all()}
    # A tail reassignment cannot release an unconfirmed physical aircraft. Keep
    # its existing event at its canonical stand/throat with an identity warning;
    # do not transfer its spray evidence or radio authority to the new tail.
    detached_radio = departure_context.get("detached_radio", ())
    detached = (*detached_radio, *departure_context.get("detached_spraying", ()))
    sources={mission.id:mission for mission in SortDateMission.query.filter(
        SortDateMission.sort_date_operation_id==operation.id,
        SortDateMission.id.in_([event.sort_date_mission_id for event in detached])).all()} if detached else {}
    for event in detached:
        mission=sources.get(event.sort_date_mission_id)
        if mission is None: continue
        tail=str(event.tail_number).strip().upper()
        row=_departure_row(mission,operation,parking.get(tail),states.get(tail),pretreat.get(tail),event,departure_context['fluid_settings'],datetime.utcnow())
        row.update(tail=tail, flight=event.flight_number_snapshot or '', script=None)
        if event in detached_radio:
            row.update(identity_key=f'radio-{event.id}',
                       identity_note='RADIO PENDING · Mission/tail identity changed. Confirm the physical aircraft and resolve canonical identity before radio confirmation.')
        else:
            row.update(identity_key=f'deice-{event.id}', block_out_at_utc=None,
                       identity_note='ACTIVE DEICE · Mission/tail identity changed. Confirm the physical aircraft and resolve canonical identity.')
        recorded_ramps={evidence.get('ramp') for evidence in (event.pass_evidence or {}).values()
                        if evidence.get('parking')=='DEICE PAD' and evidence.get('ramp') in UCC_RAMP_ORDER}
        if len(recorded_ramps)==1:
            row.update(ramp=recorded_ramps.pop(),retained_radio_throat=True)
        rows.append(row);departure_tails.add(tail)
    for mission in arrival_missions:
        tail = str(mission.assigned_tail_number or "").strip().upper()
        if not tail or tail in departure_tails or (tail not in parking and not _tail_parking_location(states.get(tail))) or getattr(mission, "arrival_status", None) == "cancelled":
            continue
        row = _departure_row(mission, operation, parking.get(tail), states.get(tail), pretreat.get(tail), None, departure_context["fluid_settings"], datetime.utcnow())
        row["departure_time_label"] = "ATA" if mission.actual_block_in_datetime_utc else "ETA"
        row["destination"] = str(mission.origin or "").strip()
        row["departure_time"] = format_local_hhmm(mission.actual_block_in_datetime_utc or mission.eta_datetime_utc or mission.planned_datetime_utc, mission.timezone or None)
        row["tv_destination"] = mission.destination
        row["tv_etd"] = ""  # The shared row builder uses the arrival estimate for this inbound-only row.
        rows.append(row)
        departure_tails.add(tail)
    located_tails = dict.fromkeys((*parking, *(tail for tail, state in states.items() if _tail_parking_location(state))))
    for tail in located_tails:
        if not tail or tail in departure_tails:
            continue
        assignment = parking.get(tail)
        state, treatment = states.get(tail), pretreat.get(tail)
        rows.append({
            "mission_id": None, "identity_key": f"parking-{assignment.id}" if assignment else f"tail-state-{state.id}",
            "flight": "", "tail": tail, "destination": "", "parking": getattr(assignment, 'position_code', None) or "TBD",
            "sort_time": None, "block_out_at_utc": None, "departure_time": "", "departure_time_label": "",
            "tail_status": tail_operational_status_label(tail_operational_status(state, assignment=assignment)),
            "pretreat_complete": bool(state and state.pretreat_status),
            "pretreat_configured": bool(treatment and treatment.configured_at_utc),
            "pretreat_status": "PRETREATED" if state and state.pretreat_status else "PRETREAT PLANNED" if treatment and treatment.pretreat_planned else "—",
            "deice_status": str(getattr(state, "deice_status", "unknown")).upper(), "event": None,
        })
    spray_starts = departure_context.get("spray_starts", {})
    for row in rows:
        _project_spray_start(row, spray_starts)

    def identity_order(row):
        # A different mission may reference this same tail. Keep the active
        # event on its own mission when that mission still owns the tail.
        event = row.get("event")
        linked_active = _is_active_throat(row) and row["mission_id"] == event.sort_date_mission_id
        return (not linked_active, not bool(row.get("block_out_at_utc")), _aircraft_sort_key(row))

    unique = {}
    for row in sorted(rows, key=identity_order):
        tail = row["tail"]
        # Multiple missions can reference one aircraft; it cannot occupy two stands.
        if tail == "-":
            continue
        if tail in unique:
            unique[tail].setdefault("identity_note", "Multiple current-sort missions reference this tail; inspect canonical mission details.")
            continue
        item = dict(row)
        item["identity_key"] = row.get("identity_key") or str(row["mission_id"])
        if (not _is_active_throat(item) and not item.get("pad_evidence_review")
                and not item.get("block_out_at_utc") and departures.get(tail)
                and arrivals.get(tail, datetime.min) <= departures[tail]):
            continue  # A later preassignment is not evidence of a physical return.
        assignment = parking.get(tail)
        state = states.get(tail)
        code = str(getattr(assignment, "ramp_code", "") or "").strip()
        item["parking_source"] = "Current-sort parking assignment"
        if not item.get("parking") or item["parking"] == "TBD":
            # MotherBrain's stored tail parking is additional canonical evidence,
            # never a guessed stand or a write back to the parking plan.
            location = _tail_parking_location(state)
            if location:
                item["parking"], code = location
                item["parking_source"] = "MotherBrain tail parking"
        item["ramp"] = row['ramp'] if row.get('retained_radio_throat') else _ramp_name(code) or "Unlocated"
        item["lane"] = getattr(assignment, "lane_number", None)
        item["canonical_departure_required"] = bool(state and state.deice_status in {"required", "configured"})
        # Treatment evidence belonging to a former tail must never move aircraft.
        event = item.get("event")
        if event and str(event.tail_number or "").strip().upper() != tail:
            item["identity_note"] = "Departure deice evidence belongs to a different tail. Coordinator review required."
            item["event"] = None
            item["pad_evidence_review"] = True
            item["deice_status"] = "TAIL IDENTITY REVIEW"
        if radio_complete(item.get('event')):
            continue  # Confirmed departure must not reappear at its recorded stand.
        if (item.get("block_out_at_utc") and _departure_event_status(item) not in {"deice_planned", "configured", "finished"}
                and not _is_active_throat(item) and not item.get("pad_evidence_review")):
            continue
        unique[tail] = item
    return list(unique.values())


def _project_spray_start(row, spray_starts):
    event = row.get("event")
    if event is None:
        if row.get("ownership_review"):
            row["identity_note"] = row["ownership_review"]
        if row.get("event_ownership_conflict"):
            row["pad_evidence_review"] = True
        return
    if radio_complete(event):
        return
    tail = row["tail"]
    mission_ids = {event.sort_date_mission_id, row["mission_id"]}
    frozen_starts = []
    conflicting = False
    for evidence in (event.pass_evidence or {}).values():
        recorded_tail = str(evidence.get("tail") or "").strip().upper()
        recorded_operation = evidence.get("operation_id")
        if evidence.get("start") and (recorded_tail not in ("", tail)
                                      or recorded_operation not in (None, event.sort_date_operation_id)):
            conflicting = True
        if recorded_tail == tail and recorded_operation == event.sort_date_operation_id:
            mission_ids.add(evidence.get("mission_id"))
            try:
                started = datetime.fromisoformat(evidence.get("start") or "")
            except (TypeError, ValueError):
                continue
            if started.tzinfo is not None:
                started = started.astimezone(timezone.utc).replace(tzinfo=None)
            frozen_starts.append(started)
    records = spray_starts.get(event.id, ())
    matched = [record for record in records if str(record.tail_number_snapshot or "").strip().upper() == tail
               and record.sort_date_mission_id in mission_ids]
    conflicting = conflicting or any(record.tail_number_snapshot and record not in matched for record in records)
    starts = frozen_starts + [record.started_at_utc for record in matched]
    started = _spray_started_at(row) or min(starts, default=None)
    if conflicting or (records and not started) or (started and event.status in {"negative", "not_sprayed"}):
        row.update(pad_evidence_review=True, identity_note="Recorded spray conflicts with the current tail, mission, or treatment state. Coordinator review required.")
    else:
        row["spray_started_at_utc"] = started
        if _holds_for_radio(row) and not radio_pending(event):
            row["identity_note"] = "Spray and CLEAR are recorded. Radio confirmation is unverified; coordinator review required."


def validate_ucc_truck_unique(operation, truck_number, assignment=None):
    number = str(truck_number or "").strip()
    if not number:
        return
    duplicate = NeoSubZeroUccTruckAssignment.query.filter(
        NeoSubZeroUccTruckAssignment.sort_date_operation_id == operation.id,
        func.lower(func.trim(NeoSubZeroUccTruckAssignment.truck_number)) == number.lower(),
        NeoSubZeroUccTruckAssignment.id != (getattr(assignment, "id", None) or -1),
    ).first()
    if duplicate:
        raise NeoSubZeroUccError(f"Truck {number} is already at {duplicate.ramp} position {duplicate.position_number}. Move that unit or clear its truck first.")


def _physical_queue(rows):
    def order(row):
        if _is_active_throat(row):
            return (0, _throat_sort_key(row))
        rank = getattr(row["event"], "ucc_queue_order", None)
        return (1, (rank is None, rank or 0, _queue_sort_key(row)))

    return sorted((row for row in rows if not row.get("pad_evidence_review") and (_is_active_throat(row) or
        (row.get("block_out_at_utc") and _departure_event_status(row) in {"deice_planned", "configured", "finished"}))), key=order)


def _queue_version(rows):
    values = [(row["mission_id"], entity_version(row.get("event")), str(row.get("block_out_at_utc")),
               row["parking"], row["tail"], str(_spray_started_at(row)), bool(row.get("pad_evidence_review")))
              for row in rows]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def ucc_staffing_version(operation):
    values = [(model.__tablename__, row.id, entity_version(row)) for model in (NeoSubZeroUccAssignment, NeoSubZeroUccTruckAssignment) for row in model.query.filter_by(sort_date_operation_id=operation.id).order_by(model.id).all()]
    return hashlib.sha256(json.dumps([operation.id, values]).encode()).hexdigest()


def move_ucc_unit(
    operation, source_ramp, source_position, destination_ramp, destination_position,
    *, expected_version, swap=False, user_id=None,
):
    """Move/swap canonical slots under the caller's operation lock, never spray snapshots."""
    from app.services.neosubzero_spray import set_neosubzero_ucc_truck
    source_ramp, source_position, _ = _validated_slot(operation, source_ramp, source_position, "driver", None)
    destination_ramp, destination_position, _ = _validated_slot(operation, destination_ramp, destination_position, "driver", None)
    if expected_version != ucc_staffing_version(operation):
        raise NeoSubZeroUccError("Staffing or trucks changed. Your draft is preserved; open current UCC in a new tab and review before retrying.")
    if (source_ramp, source_position) == (destination_ramp, destination_position):
        raise NeoSubZeroUccError("Choose a different treatment position.")
    people = {
        (row.ramp, row.position_number, row.team_role): row
        for row in NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=operation.id).with_for_update().all()
    }
    trucks = {
        (row.ramp, row.position_number): row
        for row in NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=operation.id).with_for_update().all()
    }
    source, destination = (source_ramp, source_position), (destination_ramp, destination_position)
    units = {key: {role: getattr(people.get((*key, role)), "person", None) for role in UCC_ROLES} for key in (source, destination)}
    numbers = {key: getattr(trucks.get(key), "truck_number", None) or "" for key in (source, destination)}
    for key in (source, destination):
        validate_ucc_truck_unique(operation, numbers[key], trucks.get(key))
    if not any(units[source].values()) and not numbers[source]:
        raise NeoSubZeroUccError("The source unit is empty.")
    if not swap and (any(units[destination].values()) or numbers[destination]):
        raise NeoSubZeroUccError("The destination is occupied. Choose SWAP UNITS or an empty position.")
    for unit in units.values():
        for person in unit.values():
            if person:
                _validate_available_person(operation, person)
    for key in (source, destination):
        for role in UCC_ROLES:
            row = people.get((*key, role))
            if row:
                _stage_slot_person(row, None, user_id=None)
    db.session.flush()
    # Release both truck numbers before a transactional move/swap, just as
    # personnel are released above. Per-pass historical snapshots are untouched.
    for key in (source,destination):
        if trucks.get(key): trucks[key].truck_number=None
    db.session.flush()
    for key, baseline in ((destination, source), (source, destination if swap else None)):
        for role in UCC_ROLES:
            set_neosubzero_ucc_assignment(
                operation, *key, role, units[baseline][role] if baseline else None,
                user_id=user_id, assignment=people.get((*key, role)),
            )
        set_neosubzero_ucc_truck(
            operation, *key, numbers[baseline] if baseline else "",
            user_id=user_id, assignment=trucks.get(key),
            retained_number=numbers[baseline] if baseline else None,
        )


def reorder_ucc_queue(gateway, operation, ramp_name, mission_id, direction, *, expected_version, user_id):
    """Correct order on existing departure events; no new aircraft or spray state."""
    context = neosubzero_ucc_context(gateway, operation)
    ramp = next((row for row in context["ramps"] if row["name"] == ramp_name), None)
    if not ramp or expected_version != ramp["queue_version"]:
        raise NeoSubZeroUccError("Aircraft or queue changed. Review current UCC before correcting the queue.")
    queue = list(ramp["queue"])
    index = next((i for i, row in enumerate(queue) if row["mission_id"] == mission_id), None)
    if index is None or direction not in {"forward", "back"}:
        raise NeoSubZeroUccError("Choose an aircraft in this ramp's departure queue.")
    target = index + (-1 if direction == "forward" else 1)
    if target < 0 or target >= len(queue) or any(_is_active_throat(queue[i]) for i in (index, target)):
        raise NeoSubZeroUccError("The active deice position cannot be moved. Correct only waiting aircraft.")
    before = [row["mission_id"] for row in queue]
    queue[index], queue[target] = queue[target], queue[index]
    after = [row["mission_id"] for row in queue]
    for rank, row in enumerate(queue, 1):
        event = row["event"]
        if event is None:
            raise NeoSubZeroUccError("Record Initial Contact in Deice Operations before manually reordering this aircraft.")
        history = json.loads(event.ucc_queue_history or "[]")
        history.append({"actor": user_id, "at_utc": datetime.utcnow().isoformat(), "before": before, "after": after})
        event.ucc_queue_order = rank
        event.ucc_queue_history = json.dumps(history)


def set_neosubzero_ucc_assignment(
    operation,
    ramp,
    position_number,
    team_role,
    person,
    *,
    user_id=None,
    assignment=None,
):
    """Stage one Driver/Flyer slot change without committing."""
    if operation is None:
        raise NeoSubZeroUccError("No current sort is available.")
    ramp, position, role = _validated_slot(
        operation, ramp, position_number, team_role, assignment
    )

    if person is not None:
        _validate_available_person(operation, person)
        duplicate = NeoSubZeroUccAssignment.query.filter(
            NeoSubZeroUccAssignment.sort_date_operation_id == operation.id,
            NeoSubZeroUccAssignment.person_id == person.id,
            NeoSubZeroUccAssignment.id
            != (getattr(assignment, "id", None) or -1),
        ).one_or_none()
        if duplicate is not None:
            raise NeoSubZeroUccError(
                "This employee is already assigned to another UCC slot."
            )

    if assignment is None:
        assignment = NeoSubZeroUccAssignment(
            sort_date_operation_id=operation.id,
            ramp=ramp,
            position_number=position,
            team_role=role,
        )
        db.session.add(assignment)
    assignment.person_id = getattr(person, "id", None)
    assignment.assigned_by_user_id = user_id if person is not None else None
    assignment.assigned_at = datetime.utcnow() if person is not None else None
    return assignment


def move_neosubzero_ucc_assignment(
    operation,
    ramp,
    position_number,
    team_role,
    person,
    *,
    resolution="remove",
    user_id=None,
    destination_assignment=None,
    source_assignment=None,
):
    """Atomically stage an assignment, move, replacement, or slot swap."""
    if operation is None:
        raise NeoSubZeroUccError("No current sort is available.")
    ramp, position, role = _validated_slot(
        operation,
        ramp,
        position_number,
        team_role,
        destination_assignment,
    )
    resolution = str(resolution or "").strip().casefold()
    if resolution == "cancel":
        return {
            "destination": destination_assignment,
            "source": source_assignment,
            "changed": False,
        }
    if person is None:
        destination = set_neosubzero_ucc_assignment(
            operation,
            ramp,
            position,
            role,
            None,
            user_id=user_id,
            assignment=destination_assignment,
        )
        return {"destination": destination, "source": None, "changed": True}

    _validate_available_person(operation, person)
    if (
        destination_assignment is not None
        and destination_assignment.person_id == person.id
    ):
        return {
            "destination": destination_assignment,
            "source": destination_assignment,
            "changed": False,
        }

    if source_assignment is None:
        source_assignment = NeoSubZeroUccAssignment.query.filter(
            NeoSubZeroUccAssignment.sort_date_operation_id == operation.id,
            NeoSubZeroUccAssignment.person_id == person.id,
            NeoSubZeroUccAssignment.id
            != (getattr(destination_assignment, "id", None) or -1),
        ).one_or_none()
    elif (
        source_assignment.sort_date_operation_id != operation.id
        or source_assignment.person_id != person.id
        or source_assignment.id == getattr(destination_assignment, "id", None)
    ):
        raise NeoSubZeroUccError(
            "The employee's current UCC assignment changed. Reload UCC."
        )

    displaced_person = (
        destination_assignment.person
        if destination_assignment and destination_assignment.person_id
        else None
    )
    if displaced_person is not None:
        if resolution not in {"remove", "swap"}:
            raise NeoSubZeroUccError(
                "Choose Remove, Swap, or Cancel for the occupied destination."
            )
        if resolution == "swap" and source_assignment is None:
            raise NeoSubZeroUccError(
                "Swap requires the selected employee to have a current UCC slot."
            )

    destination = destination_assignment
    if destination is None:
        destination = NeoSubZeroUccAssignment(
            sort_date_operation_id=operation.id,
            ramp=ramp,
            position_number=position,
            team_role=role,
        )
        db.session.add(destination)

    if source_assignment is not None:
        _stage_slot_person(source_assignment, None, user_id=None)
    if displaced_person is not None:
        _stage_slot_person(destination, None, user_id=None)
    if source_assignment is not None or displaced_person is not None:
        db.session.flush()

    _stage_slot_person(destination, person, user_id=user_id)
    if resolution == "swap" and source_assignment is not None:
        _stage_slot_person(source_assignment, displaced_person, user_id=user_id)
    return {
        "destination": destination,
        "source": source_assignment,
        "changed": True,
    }


def clear_neosubzero_ucc_assignments_for_people(
    operation,
    person_ids,
    *,
    lock=True,
):
    """Stage bounded slot clears for unavailable employees."""
    if operation is None:
        return 0
    normalized_ids = {int(person_id) for person_id in person_ids or ()}
    if not normalized_ids:
        return 0
    query = NeoSubZeroUccAssignment.query.filter(
        NeoSubZeroUccAssignment.sort_date_operation_id == operation.id,
        NeoSubZeroUccAssignment.person_id.in_(normalized_ids),
    )
    if lock:
        query = query.with_for_update()
    rows = query.all()
    for row in rows:
        row.person_id = None
        row.assigned_by_user_id = None
        row.assigned_at = None
    return len(rows)


def _validated_slot(operation, ramp, position_number, team_role, assignment):
    ramp = str(ramp or "").strip().title()
    role = str(team_role or "").strip().casefold()
    try:
        position = int(position_number)
    except (TypeError, ValueError) as exc:
        raise NeoSubZeroUccError("Choose a valid treatment position.") from exc
    if ramp not in RAMP_ORDER or position not in UCC_POSITIONS or role not in UCC_ROLES:
        raise NeoSubZeroUccError("Choose a valid UCC staffing slot.")
    if assignment is not None and (
        assignment.sort_date_operation_id != operation.id
        or assignment.ramp != ramp
        or assignment.position_number != position
        or assignment.team_role != role
    ):
        raise NeoSubZeroUccError("UCC assignment does not match this slot.")
    return ramp, position, role


def _validate_available_person(operation, person):
    eligible_ids = {
        item["person"].id for item in current_subzero_staffing_pool(operation)
    }
    if person.id not in eligible_ids:
        raise NeoSubZeroUccError("Choose an available Deice-qualified employee.")


def _stage_slot_person(assignment, person, *, user_id):
    assignment.person = person
    assignment.person_id = getattr(person, "id", None)
    assignment.assigned_by_user_id = user_id if person is not None else None
    assignment.assigned_at = datetime.utcnow() if person is not None else None


def clear_neosubzero_ucc_assignments_for_person_all_sorts(person_id):
    """Stage clears across stored UCC slots after qualification revocation."""
    rows = (
        NeoSubZeroUccAssignment.query.filter_by(person_id=person_id)
        .with_for_update()
        .all()
    )
    for row in rows:
        row.person_id = None
        row.assigned_by_user_id = None
        row.assigned_at = None
    return len(rows)


def neosubzero_ucc_revision(gateway, operation, *, weather_revision=None):
    """Revision covering exactly UCC-visible aircraft, staffing, and fluid inputs."""
    from app.services.neosubzero_departure_deice import departure_deice_revision
    from app.models import NeoSubZeroTruck

    operation_id = getattr(operation, "id", None)
    payload = [("departure", departure_deice_revision(gateway, operation)),
               ("parking_9_10_enabled", _parking_9_10_enabled(gateway))]
    if weather_revision is not None:
        payload.append(("weather", weather_revision))
    models = (
        ("registered_trucks", NeoSubZeroTruck, NeoSubZeroTruck.gateway_id == gateway.id),
        ("missions", SortDateMission, SortDateMission.sort_date_operation_id == operation_id),
        # departure_deice_revision already covers both UCC assignment tables.
        ("callouts", NeoSubZeroCalloutAssignment, NeoSubZeroCalloutAssignment.sort_date_operation_id == operation_id),
        ("attendance", StaffingDailyAttendance, StaffingDailyAttendance.sort_date_operation_id == operation_id),
        ("qualifications", StaffingPersonQualification, StaffingPersonQualification.qualification_key == "deice"),
        ("people", StaffingPerson, StaffingPerson.active.is_(True)),
        ("work", StaffingWorkAssignment, StaffingWorkAssignment.active.is_(True)),
    )
    for label, model, criterion in models:
        count, latest, max_id = db.session.query(
            func.count(model.id),
            func.max(model.updated_at),
            func.max(model.id),
        ).filter(criterion).one()
        payload.append((label, int(count or 0), str(latest or ""), int(max_id or 0)))
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _visual_state(row):
    event = row.get("event")
    status = _departure_event_status(row)
    if _holds_for_radio(row): return 'radio-pending'
    if status == "cleared":
        return "gone"
    if status == "negative" or row.get("collapse_state") == "negative":
        return "negative"
    if status == "finished":
        return "finished"
    if _spray_started_at(row):
        return 'deicing'
    if status == "deice_planned":
        return "deice-planned"
    if status == "configured":
        return "configured"
    if row.get("pretreat_complete"):
        return "pretreated"
    if row.get("pretreat_configured"):
        return "configured"
    if row.get("pretreat_status") == "PRETREAT PLANNED":
        return "pretreat-planned"
    return "normal"


def _visual_label(state):
    return {
        "pretreat-planned": "PRETREAT PLANNED",
        "configured": "CONFIGURED",
        "pretreated": "PRETREATED",
        "deice-planned": "DEICE PLANNED",
        "deicing": "DEICING",
        "finished": "FINISHED / AWAITING CLEARANCE",
        "negative": "NEGATIVE DEICE",
        "gone": "GONE / COMPLETED",
        "radio-pending": "CLEARED · RADIO PENDING",
    }.get(state, "NO DEICE RECORDED")


def _position_number(position_code):
    text = str(position_code or "").strip().upper()
    digits = "".join(character for character in text if character.isdigit())
    return int(digits) if digits else None


def _departure_event_status(row):
    status = str(getattr(row.get("event"), "status", "") or "").strip().lower()
    return status or ("deice_planned" if row.get("canonical_departure_required") else "")


def _spray_started_at(row):
    event = row.get("event")
    return (row.get("spray_started_at_utc") or getattr(event, "pass1_started_at_utc", None)
            or getattr(event, "pass2_started_at_utc", None))


def _holds_for_radio(row):
    event = row.get("event")
    return radio_pending(event) or (_departure_event_status(row) == "cleared"
                                    and not radio_complete(event)
                                    and bool(_spray_started_at(row) or getattr(event, "clear_outcome", None) == "sprayed"))


def _is_active_throat(row):
    status = _departure_event_status(row)
    # Actual treatment, FIN and pending readback own the pad even when the
    # independent MotherBrain Block-Out milestone has not yet been recorded.
    return not row.get("pad_evidence_review") and (_holds_for_radio(row) or status == "finished" or
        (status in {"deice_planned", "configured"} and _spray_started_at(row) is not None))


def _is_waiting_for_deice(row):
    return (
        _departure_event_status(row) == "deice_planned"
        and row.get("block_out_at_utc") is not None
    )


def _throat_sort_key(row):
    event = row.get("event")
    status = _departure_event_status(row)
    active_at = (
        _spray_started_at(row)
        or getattr(event, "configured_at_utc", None)
        or row.get("sort_time")
    )
    return (
        0 if radio_pending(event) else 1 if status == "finished" else 2,
        active_at is None,
        active_at or datetime.max,
        row.get("sort_time") or datetime.max,
        row.get("mission_id") or 0,
    )


def _queue_sort_key(row):
    return (
        row.get("block_out_at_utc") is None,
        row.get("block_out_at_utc") or datetime.max,
        row.get("flight") or "",
        row.get("mission_id") or 0,
    )


def _aircraft_sort_key(row):
    position = _position_number(row.get("parking"))
    return (
        position is None,
        position or 999,
        row.get("sort_time") or datetime.max,
        row.get("flight") or "",
        row.get("mission_id") or 0,
    )
