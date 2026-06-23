from datetime import date, datetime

from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.models.staffing_leadership_assignment import STAFFING_LEADERSHIP_LEVELS
from app.models.staffing_person import STAFFING_CLASSIFICATIONS
from app.models.staffing_unit import STAFFING_UNIT_TYPES


CLASSIFICATION_LABELS = {
    "part_time": "Part Time",
    "full_time_combo": "Full Time Combo",
    "part_time_supervisor": "Part Time Supervisor",
    "full_time_supervisor": "Full Time Supervisor",
    "full_time_specialist": "Full Time Specialist",
    "manager": "Manager",
    "division_manager": "Division Manager",
}

UNIT_TYPE_LABELS = {
    "sort": "Sort",
    "operation": "Operation",
    "department": "Department",
    "work_area": "Work Area",
}

LEADERSHIP_LEVEL_LABELS = {
    "work_area_lead": "Work Area Lead",
    "department_lead": "Department Lead",
    "operation_lead": "Operation Lead",
    "sort_lead": "Sort Lead",
    "specialist_support": "Specialist Support",
}

NON_MANAGEMENT_CLASSIFICATIONS = {"part_time", "full_time_combo"}
PARENT_TYPE_BY_UNIT_TYPE = {
    "sort": None,
    "operation": "sort",
    "department": "operation",
    "work_area": "department",
}


def classification_choices():
    return [(value, CLASSIFICATION_LABELS[value]) for value in STAFFING_CLASSIFICATIONS]


def unit_type_choices():
    return [(value, UNIT_TYPE_LABELS[value]) for value in STAFFING_UNIT_TYPES]


def leadership_level_choices():
    return [(value, LEADERSHIP_LEVEL_LABELS[value]) for value in STAFFING_LEADERSHIP_LEVELS]


def create_person(values):
    person = StaffingPerson()
    update_person(person, values, is_new=True)
    db.session.add(person)
    db.session.flush()
    return person


def update_person(person, values, is_new=False):
    employee_id = _required_text(values.get("employee_id"), "Employee ID")
    first_name = _required_text(values.get("first_name"), "First name")
    last_name = _required_text(values.get("last_name"), "Last name")
    seniority_date = _parse_date(values.get("seniority_date"), "Seniority date")
    classification = _normalize_choice(
        values.get("classification"),
        STAFFING_CLASSIFICATIONS,
        "classification",
    )
    phone_number = _optional_text(values.get("phone_number"))
    active = _parse_bool(values.get("active"), default=True)

    with db.session.no_autoflush:
        existing = StaffingPerson.query.filter_by(employee_id=employee_id).first()
        if existing and existing.id != getattr(person, "id", None):
            raise ValueError("Employee ID already exists.")

    old_classification = None if is_new else person.classification
    person.employee_id = employee_id
    person.first_name = first_name
    person.last_name = last_name
    person.seniority_date = seniority_date
    person.phone_number = phone_number
    person.classification = classification
    person.active = active

    if old_classification and old_classification != classification:
        remove_invalid_assignments_for_person(person)

    return person


def delete_person(person):
    StaffingWorkAssignment.query.filter_by(person_id=person.id).delete()
    StaffingLeadershipAssignment.query.filter_by(person_id=person.id).delete()
    db.session.delete(person)
    db.session.flush()


def create_unit(values):
    unit = StaffingUnit()
    update_unit(unit, values, is_new=True)
    db.session.add(unit)
    db.session.flush()
    return unit


def update_unit(unit, values, is_new=False):
    unit_type = _normalize_choice(values.get("unit_type"), STAFFING_UNIT_TYPES, "unit type")
    name = _required_text(values.get("name"), "Unit name")
    parent = _resolve_parent(values.get("parent_id"), unit_type)
    display_order = _parse_int(values.get("display_order"), default=0)
    active = _parse_bool(values.get("active"), default=True)
    required_headcount = None
    if unit_type == "work_area":
        required_headcount = _parse_optional_int(values.get("required_headcount"), minimum=0)

    if not is_new and parent and parent.id == unit.id:
        raise ValueError("A unit cannot be its own parent.")

    unit.unit_type = unit_type
    unit.name = name
    unit.parent = parent
    unit.display_order = display_order
    unit.active = active
    unit.required_headcount = required_headcount
    return unit


def delete_unit(unit):
    if unit.children:
        raise ValueError("Remove child units before deleting this unit.")
    if unit.work_assignments:
        raise ValueError("Remove work assignments before deleting this work area.")
    if unit.leadership_assignments:
        raise ValueError("Remove leadership assignments before deleting this unit.")
    parent = unit.parent
    db.session.delete(unit)
    db.session.flush()
    if parent in db.session:
        db.session.expire(parent, ["children"])


def assign_work_area(person, work_area):
    _validate_work_assignment(person, work_area)
    assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id).first()
    if assignment:
        assignment.work_area = work_area
    else:
        assignment = StaffingWorkAssignment(person=person, work_area=work_area)
        db.session.add(assignment)
    db.session.flush()
    return assignment


def clear_work_assignment(person):
    assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id).first()
    if assignment:
        work_area = assignment.work_area
        db.session.delete(assignment)
        db.session.flush()
        if person in db.session:
            db.session.expire(person, ["work_assignment"])
        if work_area in db.session:
            db.session.expire(work_area, ["work_assignments"])
    return None


def create_leadership_assignment(person, unit, leadership_level=None):
    level = leadership_level or default_leadership_level_for(person, unit)
    _validate_leadership_assignment(person, unit, level)

    existing = StaffingLeadershipAssignment.query.filter_by(
        person_id=person.id,
        unit_id=unit.id,
        leadership_level=level,
    ).first()
    if existing:
        raise ValueError("This leadership assignment already exists.")

    assignment = StaffingLeadershipAssignment(
        person=person,
        unit=unit,
        leadership_level=level,
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def delete_leadership_assignment(assignment):
    person = assignment.person
    unit = assignment.unit
    db.session.delete(assignment)
    db.session.flush()
    if person in db.session:
        db.session.expire(person, ["leadership_assignments"])
    if unit in db.session:
        db.session.expire(unit, ["leadership_assignments"])


def remove_invalid_assignments_for_person(person):
    if person.classification not in NON_MANAGEMENT_CLASSIFICATIONS:
        clear_work_assignment(person)

    for assignment in list(person.leadership_assignments):
        try:
            _validate_leadership_assignment(person, assignment.unit, assignment.leadership_level)
        except ValueError:
            db.session.delete(assignment)
    db.session.flush()
    if person in db.session:
        db.session.expire(person, ["leadership_assignments"])


def default_leadership_level_for(person, unit):
    classification = person.classification
    if classification == "part_time_supervisor" and unit.unit_type == "work_area":
        return "work_area_lead"
    if classification == "full_time_supervisor" and unit.unit_type == "department":
        return "department_lead"
    if classification == "manager" and unit.unit_type == "operation":
        return "operation_lead"
    if classification == "division_manager" and unit.unit_type == "sort":
        return "sort_lead"
    if classification == "full_time_specialist" and unit.unit_type in {"department", "operation"}:
        return "specialist_support"
    raise ValueError("This person classification cannot lead the selected unit.")


def staffing_hierarchy_tree():
    units = (
        StaffingUnit.query.order_by(
            StaffingUnit.unit_type,
            StaffingUnit.display_order,
            StaffingUnit.name,
        )
        .all()
    )
    children_by_parent = {}
    for unit in units:
        children_by_parent.setdefault(unit.parent_id, []).append(unit)
    for siblings in children_by_parent.values():
        siblings.sort(key=lambda row: (row.display_order, row.name.lower(), row.id))

    def build(parent_id):
        return [
            {
                "unit": unit,
                "children": build(unit.id),
            }
            for unit in children_by_parent.get(parent_id, [])
        ]

    return build(None)


def selectable_parent_units(unit_type):
    expected_parent_type = PARENT_TYPE_BY_UNIT_TYPE.get(unit_type)
    if expected_parent_type is None:
        return []
    return (
        StaffingUnit.query.filter_by(unit_type=expected_parent_type)
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def work_area_units():
    return (
        StaffingUnit.query.filter_by(unit_type="work_area")
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def units_by_type(unit_type):
    return (
        StaffingUnit.query.filter_by(unit_type=unit_type)
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def unit_ids_under(unit):
    ids = {unit.id}
    for child in unit.children:
        ids.update(unit_ids_under(child))
    return ids


def work_area_ids_under(unit):
    if unit.unit_type == "work_area":
        return {unit.id}
    ids = set()
    for child in unit.children:
        ids.update(work_area_ids_under(child))
    return ids


def people_query(search=None, classification=None, active=None):
    query = StaffingPerson.query
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            db.or_(
                StaffingPerson.employee_id.ilike(pattern),
                StaffingPerson.first_name.ilike(pattern),
                StaffingPerson.last_name.ilike(pattern),
            )
        )
    if classification:
        query = query.filter_by(classification=classification)
    if active in {"active", "inactive"}:
        query = query.filter_by(active=(active == "active"))
    return query.order_by(StaffingPerson.seniority_date, StaffingPerson.last_name, StaffingPerson.first_name)


def unit_path(unit):
    path = []
    current = unit
    while current:
        path.append(current.name)
        current = current.parent
    return " / ".join(reversed(path))


def _validate_work_assignment(person, work_area):
    if person.classification not in NON_MANAGEMENT_CLASSIFICATIONS:
        raise ValueError("Only part time and full time combo employees can be assigned to work areas.")
    if work_area.unit_type != "work_area":
        raise ValueError("Employees can only be assigned to Work Area units.")


def _validate_leadership_assignment(person, unit, leadership_level):
    _normalize_choice(leadership_level, STAFFING_LEADERSHIP_LEVELS, "leadership level")
    expected_level = default_leadership_level_for(person, unit)
    if leadership_level != expected_level:
        raise ValueError("Leadership level does not match this classification and unit scope.")


def _resolve_parent(parent_id, unit_type):
    expected_parent_type = PARENT_TYPE_BY_UNIT_TYPE.get(unit_type)
    if expected_parent_type is None:
        if parent_id:
            raise ValueError("Sort units cannot have a parent.")
        return None

    if not parent_id:
        raise ValueError(f"{UNIT_TYPE_LABELS[unit_type]} units require a parent.")
    parent = db.session.get(StaffingUnit, int(parent_id))
    if not parent:
        raise ValueError("Selected parent unit was not found.")
    if parent.unit_type != expected_parent_type:
        raise ValueError(
            f"{UNIT_TYPE_LABELS[unit_type]} parent must be a {UNIT_TYPE_LABELS[expected_parent_type]}."
        )
    return parent


def _required_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


def _normalize_choice(value, allowed, label):
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Unsupported {label}.")
    return normalized


def _parse_date(value, label):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid date.") from exc


def _parse_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Display order must be a number.") from exc


def _parse_optional_int(value, minimum=None):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Required headcount must be a number.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError("Required headcount cannot be negative.")
    return parsed


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes", "active"}
