"""Current-sort NeoRain Crew Admin assignments."""
import json

from sqlalchemy.orm import aliased, joinedload

from app.extensions import db
from app.models import NeoRainCrewAdminAssignment, StaffingPerson, StaffingUnit, StaffingWorkAssignment

NEORAIN_CREW_ADMIN_RAMPS = ("Remote", "Alpha", "Bravo", "Charlie", "Delta", "Echo")


class NeoRainCrewAdminError(ValueError):
    pass


def eligible_neorain_crew_admins():
    ids = _crew_admin_work_area_ids()
    if not ids:
        return []
    return StaffingPerson.query.join(StaffingWorkAssignment).filter(
        StaffingPerson.active.is_(True), StaffingWorkAssignment.active.is_(True),
        StaffingWorkAssignment.work_area_unit_id.in_(ids),
    ).order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id).all()


def neorain_crew_admin_assignments(operation):
    if operation is None:
        return []
    eligible = {person.id for person in eligible_neorain_crew_admins()}
    rows = NeoRainCrewAdminAssignment.query.options(joinedload(NeoRainCrewAdminAssignment.person)).filter_by(sort_date_operation_id=operation.id).order_by(NeoRainCrewAdminAssignment.id).all()
    return [{"assignment": row, "person": row.person if row.person_id in eligible else None, "ramps": tuple(json.loads(row.ramps_json or "[]"))} for row in rows]


def add_neorain_crew_admin_assignment(operation, person, ramps, printer_number="", van_number=""):
    _validate_person(person)
    row = NeoRainCrewAdminAssignment(sort_date_operation_id=operation.id, person_id=person.id)
    db.session.add(row)
    return update_neorain_crew_admin_assignment(row, person, ramps, printer_number, van_number)


def update_neorain_crew_admin_assignment(row, person, ramps, printer_number="", van_number=""):
    _validate_person(person)
    row.person_id = person.id
    row.ramps_json = json.dumps(_validated_ramps(ramps))
    row.printer_number = _short_text(printer_number)
    row.van_number = _short_text(van_number)
    return row


def remove_neorain_crew_admin_assignment(row):
    db.session.delete(row)


def _validate_person(person):
    if person is None or person.id not in {value.id for value in eligible_neorain_crew_admins()}:
        raise NeoRainCrewAdminError("Choose an active eligible Crew Admin.")


def _validated_ramps(ramps):
    values = tuple(dict.fromkeys(str(value).strip() for value in (ramps or ()) if str(value).strip()))
    if any(value not in NEORAIN_CREW_ADMIN_RAMPS for value in values):
        raise NeoRainCrewAdminError("Choose only valid Crew Admin ramps.")
    return values


def _short_text(value):
    value = str(value or "").strip()
    if len(value) > 64:
        raise NeoRainCrewAdminError("Printer and Van values must be 64 characters or fewer.")
    return value or None


def _crew_admin_work_area_ids():
    area, dept, operation, sort = aliased(StaffingUnit), aliased(StaffingUnit), aliased(StaffingUnit), aliased(StaffingUnit)
    return [row[0] for row in db.session.query(area.id).join(dept, area.parent_id == dept.id).join(operation, dept.parent_id == operation.id).join(sort, operation.parent_id == sort.id).filter(area.unit_type == "work_area", area.name == "Crew Admin", dept.name == "Load Planning", operation.name == "Ramp", sort.name == "Night").all()]
