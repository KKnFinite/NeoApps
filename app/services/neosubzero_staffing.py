"""Shared Deice qualifications and current-sort NeoSubZero staffing pools."""

from datetime import datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoSubZeroCalloutAssignment,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingPersonQualification,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.services.live_collaboration import entity_version


DEICE_QUALIFICATION_KEY = "deice"
PERMANENT_DEICE_OPERATION = "aviation services"
PERMANENT_DEICE_WORK_AREA = "deice"


class NeoSubZeroStaffingError(ValueError):
    """Safe operator-facing qualification/callout validation error."""


def neosubzero_qualification_people(search="", *, limit=250):
    """Return a bounded active-person projection for qualification management."""
    normalized_search = str(search or "").strip().casefold()
    query = StaffingPerson.query.filter(StaffingPerson.active.is_(True))
    if normalized_search:
        pattern = f"%{normalized_search}%"
        query = query.filter(
            or_(
                func.lower(StaffingPerson.employee_id).like(pattern),
                func.lower(StaffingPerson.first_name).like(pattern),
                func.lower(StaffingPerson.last_name).like(pattern),
                func.lower(
                    StaffingPerson.first_name + " " + StaffingPerson.last_name
                ).like(pattern),
            )
        )
    people = (
        query.order_by(
            StaffingPerson.last_name,
            StaffingPerson.first_name,
            StaffingPerson.employee_id,
            StaffingPerson.id,
        )
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    person_ids = [person.id for person in people]
    assignments = _assignments_by_person(person_ids)
    qualifications = _qualification_rows_by_person(person_ids)
    units_by_id = _staffing_units_by_id()
    return tuple(
        {
            "person": person,
            "work_area": _work_area_for_person(person.id, assignments, units_by_id),
            "work_area_path": _unit_path(
                _work_area_for_person(person.id, assignments, units_by_id),
                units_by_id,
            ),
            "qualified": bool(
                qualifications.get(person.id)
                and qualifications[person.id].active
            ),
            "qualification": qualifications.get(person.id),
            "version": entity_version(qualifications.get(person.id)),
        }
        for person in people
    )


def set_staffing_person_qualification(
    person,
    qualification_key,
    qualified,
    *,
    user_id=None,
    qualification=None,
):
    """Stage one reusable qualification grant/revocation without committing."""
    key = _qualification_key(qualification_key)
    if not isinstance(qualified, bool):
        raise NeoSubZeroStaffingError("Qualification state must be true or false.")
    if person is None or person.id is None:
        raise NeoSubZeroStaffingError("Choose an existing employee.")
    if qualified and not person.active:
        raise NeoSubZeroStaffingError("Only active employees may be qualified.")
    row = qualification
    if row is not None and (
        row.person_id != person.id or row.qualification_key != key
    ):
        raise NeoSubZeroStaffingError("Qualification does not belong to this employee.")
    if row is None:
        row = StaffingPersonQualification.query.filter_by(
            person_id=person.id,
            qualification_key=key,
        ).one_or_none()
    now = datetime.utcnow()
    if row is None:
        if not qualified:
            return None
        row = StaffingPersonQualification(
            person_id=person.id,
            qualification_key=key,
        )
        db.session.add(row)
    row.active = qualified
    if qualified:
        row.granted_at = now
        row.granted_by_user_id = user_id
        row.revoked_at = None
        row.revoked_by_user_id = None
    else:
        row.revoked_at = now
        row.revoked_by_user_id = user_id
        if key == DEICE_QUALIFICATION_KEY:
            active_callouts = (
                NeoSubZeroCalloutAssignment.query.filter_by(
                    person_id=person.id,
                    active=True,
                )
                .with_for_update()
                .all()
            )
            for callout in active_callouts:
                callout.active = False
                callout.removed_at = now
                callout.removed_by_user_id = user_id
                callout.removal_reason = "qualification"
    return row


def neosubzero_callout_context(operation):
    """Return current permanent staff, callouts, and eligible candidates."""
    if operation is None:
        return {
            "operation": None,
            "permanent": (),
            "callouts": (),
            "candidates": (),
            "staffing_pool": (),
        }
    snapshot = _subzero_staffing_snapshot(operation)
    return {
        "operation": operation,
        "permanent": snapshot["permanent"],
        "callouts": snapshot["callouts"],
        "candidates": snapshot["candidates"],
        "staffing_pool": snapshot["staffing_pool"],
    }


def current_subzero_staffing_pool(operation):
    """Return the bounded canonical permanent + selected callout pool for UCC."""
    if operation is None:
        return ()
    return _subzero_staffing_snapshot(operation)["staffing_pool"]


def set_neosubzero_callout_membership(
    operation,
    person,
    selected,
    *,
    user_id=None,
    assignment=None,
):
    """Stage an explicit current-sort callout add/remove without committing."""
    if operation is None:
        raise NeoSubZeroStaffingError("No current sort is available.")
    if not isinstance(selected, bool):
        raise NeoSubZeroStaffingError("Callout membership must be true or false.")
    if person is None or person.id is None:
        raise NeoSubZeroStaffingError("Choose an existing employee.")
    row = assignment
    if row is not None and (
        row.sort_date_operation_id != operation.id or row.person_id != person.id
    ):
        raise NeoSubZeroStaffingError("Callout assignment does not match this employee.")
    if row is None:
        row = NeoSubZeroCalloutAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            person_id=person.id,
        ).one_or_none()
    now = datetime.utcnow()
    if selected:
        candidate_ids = {
            item["person"].id
            for item in _subzero_staffing_snapshot(operation)["candidates"]
            if item["available"]
        }
        if person.id not in candidate_ids:
            raise NeoSubZeroStaffingError(
                "Choose an available Deice-qualified callout employee."
            )
        if row is None:
            row = NeoSubZeroCalloutAssignment(
                sort_date_operation_id=operation.id,
                person_id=person.id,
            )
            db.session.add(row)
        row.active = True
        row.selected_at = now
        row.selected_by_user_id = user_id
        row.removed_at = None
        row.removed_by_user_id = None
        row.removal_reason = None
        return row
    if row is None:
        return None
    row.active = False
    row.removed_at = now
    row.removed_by_user_id = user_id
    row.removal_reason = "manual"
    return row


def deactivate_neosubzero_callouts_for_attendance(
    operation,
    attendance_status_by_person_id,
    *,
    user_id=None,
):
    """Stage removal of callouts explicitly marked Not Here in attendance."""
    if operation is None:
        return 0
    unavailable_ids = {
        int(person_id)
        for person_id, status in (attendance_status_by_person_id or {}).items()
        if str(status or "").strip().casefold() not in {"", "here"}
    }
    if not unavailable_ids:
        return 0
    rows = (
        NeoSubZeroCalloutAssignment.query.filter(
            NeoSubZeroCalloutAssignment.sort_date_operation_id == operation.id,
            NeoSubZeroCalloutAssignment.person_id.in_(unavailable_ids),
            NeoSubZeroCalloutAssignment.active.is_(True),
        )
        .with_for_update()
        .all()
    )
    now = datetime.utcnow()
    for row in rows:
        row.active = False
        row.removed_at = now
        row.removed_by_user_id = user_id
        row.removal_reason = "attendance"
    return len(rows)


def permanent_deice_work_area_ids():
    units_by_id = _staffing_units_by_id(active_only=True)
    return {
        unit.id
        for unit in units_by_id.values()
        if unit.unit_type == "work_area"
        and _normalized(unit.name) == PERMANENT_DEICE_WORK_AREA
        and _has_named_ancestor(
            unit,
            units_by_id,
            "operation",
            PERMANENT_DEICE_OPERATION,
        )
        and _has_sort_ancestor(unit, units_by_id, "night")
    }


def _subzero_staffing_snapshot(operation):
    units_by_id = _staffing_units_by_id(active_only=True)
    permanent_area_ids = {
        unit.id
        for unit in units_by_id.values()
        if unit.unit_type == "work_area"
        and _normalized(unit.name) == PERMANENT_DEICE_WORK_AREA
        and _has_named_ancestor(
            unit,
            units_by_id,
            "operation",
            PERMANENT_DEICE_OPERATION,
        )
        and _has_sort_ancestor(unit, units_by_id, operation.sort_name)
    }
    qualification_join = and_(
        StaffingPersonQualification.person_id == StaffingPerson.id,
        StaffingPersonQualification.qualification_key == DEICE_QUALIFICATION_KEY,
        StaffingPersonQualification.active.is_(True),
    )
    records = (
        db.session.query(
            StaffingPerson,
            StaffingWorkAssignment,
            StaffingPersonQualification,
        )
        .outerjoin(
            StaffingWorkAssignment,
            and_(
                StaffingWorkAssignment.person_id == StaffingPerson.id,
                StaffingWorkAssignment.active.is_(True),
            ),
        )
        .outerjoin(StaffingPersonQualification, qualification_join)
        .filter(
            StaffingPerson.active.is_(True),
            or_(
                StaffingPersonQualification.id.is_not(None),
                StaffingWorkAssignment.work_area_unit_id.in_(
                    permanent_area_ids or {-1}
                ),
            ),
        )
        .order_by(
            StaffingPerson.last_name,
            StaffingPerson.first_name,
            StaffingPerson.id,
        )
        .all()
    )
    people_by_id = {person.id: person for person, _assignment, _qual in records}
    assignment_by_person = {
        person.id: assignment for person, assignment, _qual in records
    }
    qualified_ids = {
        person.id for person, _assignment, qualification in records if qualification
    }
    attendance = _attendance_records(
        operation,
        set(people_by_id),
        units_by_id,
    )
    callout_rows = (
        NeoSubZeroCalloutAssignment.query.options(
            joinedload(NeoSubZeroCalloutAssignment.person)
        )
        .filter_by(sort_date_operation_id=operation.id)
        .all()
    )
    callout_by_person = {row.person_id: row for row in callout_rows}
    permanent_ids = {
        person_id
        for person_id, assignment in assignment_by_person.items()
        if assignment
        and assignment.work_area_unit_id in permanent_area_ids
    }

    def row(person_id, source):
        person = people_by_id[person_id]
        assignment = assignment_by_person.get(person_id)
        work_area = units_by_id.get(
            getattr(assignment, "work_area_unit_id", None)
        )
        attendance_row = attendance.get(person_id)
        status = str(getattr(attendance_row, "status", "") or "").strip()
        return {
            "person": person,
            "source": source,
            "work_area": work_area,
            "work_area_path": _unit_path(work_area, units_by_id),
            "attendance": attendance_row,
            "attendance_status": status,
            "attendance_label": status.replace("_", " ").title() if status else "Unmarked",
            "available": not status or status == "here",
            "callout": callout_by_person.get(person_id),
            "version": entity_version(callout_by_person.get(person_id)),
        }

    permanent = tuple(row(person_id, "permanent") for person_id in permanent_ids)
    selected_ids = {
        person_id
        for person_id, callout in callout_by_person.items()
        if callout.active
        and person_id in qualified_ids
        and person_id not in permanent_ids
        and person_id in people_by_id
        and row(person_id, "callout")["available"]
    }
    callouts = tuple(row(person_id, "callout") for person_id in selected_ids)
    candidate_ids = qualified_ids - permanent_ids - selected_ids
    candidates = tuple(row(person_id, "callout") for person_id in candidate_ids)
    staffing_pool = tuple(
        item
        for item in (*permanent, *callouts)
        if item["available"]
    )
    sort_key = lambda item: (
        item["person"].last_name.casefold(),
        item["person"].first_name.casefold(),
        item["person"].id,
    )
    return {
        "permanent": tuple(sorted(permanent, key=sort_key)),
        "callouts": tuple(sorted(callouts, key=sort_key)),
        "candidates": tuple(sorted(candidates, key=sort_key)),
        "staffing_pool": tuple(sorted(staffing_pool, key=sort_key)),
    }


def _attendance_records(operation, person_ids, units_by_id):
    if not person_ids:
        return {}
    sort_ids = {
        unit.id
        for unit in units_by_id.values()
        if unit.unit_type == "sort"
        and _normalized_sort(unit.name) == _normalized_sort(operation.sort_name)
    }
    records = StaffingDailyAttendance.query.filter(
        StaffingDailyAttendance.person_id.in_(person_ids),
        StaffingDailyAttendance.attendance_date == operation.sort_date,
        or_(
            StaffingDailyAttendance.sort_date_operation_id == operation.id,
            and_(
                StaffingDailyAttendance.sort_date_operation_id.is_(None),
                StaffingDailyAttendance.sort_unit_id.in_(sort_ids or {-1}),
            ),
        ),
    ).all()
    records.sort(
        key=lambda item: item.sort_date_operation_id == operation.id,
        reverse=True,
    )
    return {record.person_id: record for record in records}


def _assignments_by_person(person_ids):
    if not person_ids:
        return {}
    return {
        assignment.person_id: assignment
        for assignment in StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(person_ids),
            StaffingWorkAssignment.active.is_(True),
        ).all()
    }


def _qualification_rows_by_person(person_ids):
    if not person_ids:
        return {}
    return {
        row.person_id: row
        for row in StaffingPersonQualification.query.filter(
            StaffingPersonQualification.person_id.in_(person_ids),
            StaffingPersonQualification.qualification_key
            == DEICE_QUALIFICATION_KEY,
        ).all()
    }


def _staffing_units_by_id(*, active_only=False):
    query = StaffingUnit.query
    if active_only:
        query = query.filter(StaffingUnit.active.is_(True))
    return {unit.id: unit for unit in query.all()}


def _work_area_for_person(person_id, assignments, units_by_id):
    assignment = assignments.get(person_id)
    return units_by_id.get(getattr(assignment, "work_area_unit_id", None))


def _unit_path(unit, units_by_id):
    names = []
    visited = set()
    while unit and unit.id not in visited:
        visited.add(unit.id)
        names.append(unit.name)
        unit = units_by_id.get(unit.parent_id)
    return " / ".join(reversed(names)) or "Unassigned"


def _has_named_ancestor(unit, units_by_id, unit_type, name):
    current = units_by_id.get(unit.parent_id)
    while current:
        if current.unit_type == unit_type and _normalized(current.name) == name:
            return True
        current = units_by_id.get(current.parent_id)
    return False


def _has_sort_ancestor(unit, units_by_id, sort_name):
    expected = _normalized_sort(sort_name)
    current = units_by_id.get(unit.parent_id)
    while current:
        if (
            current.unit_type == "sort"
            and _normalized_sort(current.name) == expected
        ):
            return True
        current = units_by_id.get(current.parent_id)
    return False


def _qualification_key(value):
    key = str(value or "").strip().casefold().replace(" ", "_")
    if not key or len(key) > 64 or not all(
        character.isalnum() or character == "_" for character in key
    ):
        raise NeoSubZeroStaffingError("Choose a valid qualification key.")
    return key


def _normalized(value):
    return " ".join(str(value or "").strip().casefold().split())


def _normalized_sort(value):
    normalized = _normalized(value)
    return normalized[:-5].strip() if normalized.endswith(" sort") else normalized
