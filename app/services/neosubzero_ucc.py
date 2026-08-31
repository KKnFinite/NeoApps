"""Current-sort NeoSubZero UCC ramp and staffing services."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoSubZeroCalloutAssignment,
    NeoSubZeroUccAssignment,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingPersonQualification,
    StaffingWorkAssignment,
)
from app.services.live_collaboration import entity_version
from app.services.neosubzero_constants import RAMP_ORDER
from app.services.neosubzero_staffing import current_subzero_staffing_pool


UCC_REFRESH_KEY = "neosubzero.ucc"
UCC_POSITIONS = (1, 2, 3, 4)
UCC_ROLES = ("driver", "flyer")


class NeoSubZeroUccError(ValueError):
    """Safe operator-facing UCC validation error."""


def neosubzero_ucc_context(gateway, operation):
    """Return a bounded UCC projection from canonical current-sort state."""
    from app.services.neosubzero_departure_deice import (
        departure_deice_context,
    )

    departure_context = departure_deice_context(gateway, operation)
    fluid_settings = departure_context["fluid_settings"]
    if operation is None:
        return {
            "operation": None,
            "ramps": (),
            "staffing_pool": (),
            "fluid_settings": fluid_settings,
        }

    departure_rows = departure_context["rows"]
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
    staffing_pool = current_subzero_staffing_pool(operation)
    available_people = {
        item["person"].id: item for item in staffing_pool
    }
    rows_by_ramp = {ramp: [] for ramp in RAMP_ORDER}
    for row in departure_rows:
        ramp = row.get("ramp")
        if ramp not in rows_by_ramp:
            continue
        item = dict(row)
        item["visual_state"] = _visual_state(item)
        item["visual_label"] = _visual_label(item["visual_state"])
        rows_by_ramp[ramp].append(item)

    ramps = []
    for ramp in RAMP_ORDER:
        aircraft = rows_by_ramp[ramp]
        if not aircraft:
            continue
        aircraft.sort(key=_aircraft_sort_key)
        active_throat_rows = [row for row in aircraft if _is_active_throat(row)]
        active_throat_rows.sort(key=_throat_sort_key)
        throat = active_throat_rows[0] if active_throat_rows else None
        waiting_rows = [
            row
            for row in aircraft
            if row is not throat and _is_waiting_for_deice(row)
        ]
        waiting_rows.sort(key=_queue_sort_key)
        for queue_position, row in enumerate(waiting_rows, start=1):
            row["queue_position"] = queue_position
        waiting_queue = tuple(waiting_rows)
        slots = []
        for position in UCC_POSITIONS:
            roles = {}
            for role in UCC_ROLES:
                assignment = assignment_by_slot.get((ramp, position, role))
                person = (
                    assignment.person
                    if assignment and assignment.person_id in available_people
                    else None
                )
                roles[role] = {
                    "assignment": assignment,
                    "person": person,
                    "version": entity_version(assignment),
                }
            slots.append({"position": position, "roles": roles})
        ramps.append(
            {
                "name": ramp,
                "aircraft": tuple(aircraft),
                "throat": throat,
                "waiting_queue": waiting_queue,
                "slots": tuple(slots),
            }
        )
    return {
        "operation": operation,
        "ramps": tuple(ramps),
        "staffing_pool": staffing_pool,
        "fluid_settings": fluid_settings,
    }


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

    if person is not None:
        eligible_ids = {
            item["person"].id for item in current_subzero_staffing_pool(operation)
        }
        if person.id not in eligible_ids:
            raise NeoSubZeroUccError(
                "Choose an available Deice-qualified employee."
            )
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


def neosubzero_ucc_revision(gateway, operation):
    """Revision covering exactly UCC-visible aircraft, staffing, and fluid inputs."""
    from app.services.neosubzero_departure_deice import departure_deice_revision

    operation_id = getattr(operation, "id", None)
    payload = [("departure", departure_deice_revision(gateway, operation))]
    models = (
        ("ucc", NeoSubZeroUccAssignment, NeoSubZeroUccAssignment.sort_date_operation_id == operation_id),
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
    status = str(getattr(event, "status", "") or "").strip().lower()
    if status == "cleared":
        return "cleared"
    if status == "negative" or row.get("collapse_state") == "negative":
        return "negative"
    if status == "finished":
        return "finished"
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
        "finished": "FINISHED / AWAITING CLEARANCE",
        "negative": "NEGATIVE DEICE",
        "cleared": "CLEARED",
    }.get(state, "ACTIVE")


def _position_number(position_code):
    text = str(position_code or "").strip().upper()
    digits = "".join(character for character in text if character.isdigit())
    return int(digits) if digits else None


def _departure_event_status(row):
    return str(getattr(row.get("event"), "status", "") or "").strip().lower()


def _is_active_throat(row):
    event = row.get("event")
    status = _departure_event_status(row)
    return status == "finished" or (
        status == "configured"
        and getattr(event, "configured_at_utc", None) is not None
    )


def _is_waiting_for_deice(row):
    return (
        _departure_event_status(row) == "deice_planned"
        and row.get("block_out_at_utc") is not None
    )


def _throat_sort_key(row):
    event = row.get("event")
    status = _departure_event_status(row)
    active_at = (
        getattr(event, "pass1_started_at_utc", None)
        or getattr(event, "configured_at_utc", None)
        or row.get("sort_time")
    )
    return (
        0 if status == "finished" else 1,
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
