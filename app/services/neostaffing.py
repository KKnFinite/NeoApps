from datetime import date, datetime, timedelta
import re

from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    StaffingDailyAttendance,
    StaffingChangeRequest,
    Gateway,
    StaffingGroup,
    StaffingGroupMembership,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    SortDateOperation,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.models.staffing_leadership_assignment import STAFFING_LEADERSHIP_LEVELS
from app.models.staffing_daily_attendance import STAFFING_DAILY_ATTENDANCE_STATUSES
from app.models.staffing_person import STAFFING_CLASSIFICATIONS, STAFFING_EMPLOYEE_STATUSES
from app.models.staffing_unit import STAFFING_UNIT_TYPES
from app.services.gateway_matrix import current_operations_for_gateway


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
    "work_area": "Work Area",
    "department": "Department",
    "operation": "Operation",
    "sort": "Sort",
}

EMPLOYEE_STATUS_LABELS = {
    "active": "Active",
    "disability": "Disability",
    "comp": "Comp",
    "military": "Military",
    "fmla": "FMLA",
}

ATTENDANCE_STATUS_LABELS = {
    "here": "Here",
    "call_in": "Call In",
    "no_call": "No Call",
    "vacation": "Vacation",
    "optional_day": "Optional Day",
    "anniversary_day": "Anniversary Day",
    "funeral": "Funeral",
    "jury": "Jury",
    "int_fmla": "Int FMLA",
    "disability": "Disability",
    "comp": "Comp",
    "military": "Military",
    "cleared": "Cleared",
}

NON_MANAGEMENT_CLASSIFICATIONS = {"part_time", "full_time_combo"}
MANAGEMENT_CLASSIFICATIONS = set(STAFFING_CLASSIFICATIONS) - NON_MANAGEMENT_CLASSIFICATIONS
SUPERVISOR_CLASSIFICATIONS = {
    "part_time_supervisor",
    "full_time_supervisor",
    "full_time_specialist",
}
MANAGER_CLASSIFICATIONS = {"manager", "division_manager"}
PARENT_TYPE_BY_UNIT_TYPE = {
    "sort": None,
    "operation": "sort",
    "department": "operation",
    "work_area": ("department", "operation"),
}
STAFFING_NEAR_TARGET_THRESHOLD = 0.8
PEOPLE_DEFAULT_PAGE_SIZE = 100
PEOPLE_MAX_PAGE_SIZE = 250
ATTENDANCE_OPERATION_SORT_NAME = "night"
ATTENDANCE_OPERATION_MISSING_MESSAGE = "NIGHT SORT HAS NOT BEEN CREATED YET."
REPORTING_TARGET_CLASSIFICATION = {
    "part_time_supervisor": "full_time_supervisor",
    "full_time_specialist": "full_time_supervisor",
    "full_time_supervisor": "manager",
    "manager": "division_manager",
}
REPORTING_HISTORY_RETENTION_DAYS = 30
DIRECT_REPORTING_EDITOR_CLASSIFICATIONS = {
    "full_time_supervisor",
    "manager",
    "division_manager",
}
MANAGEMENT_TREE_CLASSIFICATION_ORDER = {
    "division_manager": 0,
    "manager": 1,
    "full_time_supervisor": 2,
    "part_time_supervisor": 3,
    "full_time_specialist": 4,
}


def classification_choices():
    return [(value, CLASSIFICATION_LABELS[value]) for value in STAFFING_CLASSIFICATIONS]


def employee_status_choices():
    return [(value, EMPLOYEE_STATUS_LABELS[value]) for value in STAFFING_EMPLOYEE_STATUSES]


def attendance_status_choices():
    return [
        (value, ATTENDANCE_STATUS_LABELS[value])
        for value in STAFFING_DAILY_ATTENDANCE_STATUSES
    ]


def unit_type_choices():
    return [(value, UNIT_TYPE_LABELS[value]) for value in STAFFING_UNIT_TYPES]


def leadership_level_choices():
    return [(value, LEADERSHIP_LEVEL_LABELS[value]) for value in STAFFING_LEADERSHIP_LEVELS]


def landing_context():
    active_people = StaffingPerson.query.filter_by(active=True)
    active_people_count = active_people.count()
    active_work_assignments = StaffingWorkAssignment.query.filter_by(active=True).count()
    active_work_area_count = StaffingUnit.query.filter_by(unit_type="work_area", active=True).count()
    today = date.today()
    today_attendance = StaffingDailyAttendance.query.filter_by(attendance_date=today).count()
    active_non_management = active_people.filter(
        StaffingPerson.classification.in_(NON_MANAGEMENT_CLASSIFICATIONS)
    ).count()
    unassigned = max(active_non_management - active_work_assignments, 0)
    return {
        "summary": {
            "total_people": StaffingPerson.query.count(),
            "active_roster": active_people_count,
            "assigned": active_work_assignments,
            "unassigned": unassigned,
            "work_areas": active_work_area_count,
            "today_attendance": today_attendance,
        },
        "today": today,
    }


def create_person(values):
    person_values = _person_values(values)
    person = StaffingPerson()
    _apply_person_values(person, person_values)
    with db.session.no_autoflush:
        existing = StaffingPerson.query.filter_by(
            employee_id=person_values["employee_id"]
        ).first()
        if existing:
            raise ValueError("Employee ID already exists.")
    db.session.add(person)
    db.session.flush()
    return person


def update_person(person, values, is_new=False):
    person_values = _person_values(values)
    with db.session.no_autoflush:
        existing = StaffingPerson.query.filter_by(
            employee_id=person_values["employee_id"]
        ).first()
        if existing and existing.id != getattr(person, "id", None):
            raise ValueError("Employee ID already exists.")

    old_classification = None if is_new else person.classification
    old_active = None if is_new else person.active
    _apply_person_values(person, person_values)

    if old_classification and old_classification != person_values["classification"]:
        remove_invalid_assignments_for_person(person)
    if person.id and (
        old_classification != person_values["classification"]
        or old_active != person_values["active"]
    ):
        end_invalid_reporting_relationships_for_person(person)

    return person


def create_people_batch(rows, work_area):
    if not work_area or work_area.unit_type != "work_area" or not work_area.active:
        raise ValueError("Select an active Work Area.")
    if not rows:
        raise ValueError("Enter at least one employee row.")

    normalized_rows = [_person_values(row) for row in rows]
    employee_ids = [row["employee_id"].lower() for row in normalized_rows]
    if len(set(employee_ids)) != len(employee_ids):
        raise ValueError("Employee ID is duplicated in this batch.")

    existing_ids = {
        employee_id.lower()
        for (employee_id,) in (
            db.session.query(StaffingPerson.employee_id)
            .filter(func.lower(StaffingPerson.employee_id).in_(employee_ids))
            .all()
        )
    }
    if existing_ids:
        raise ValueError("Employee ID already exists.")

    people = []
    for person_values in normalized_rows:
        person = StaffingPerson()
        _apply_person_values(person, person_values)
        _validate_work_assignment(person, work_area)
        people.append(person)
    db.session.add_all(people)
    db.session.flush()
    db.session.add_all(
        [
            StaffingWorkAssignment(person=person, work_area=work_area, active=True)
            for person in people
        ]
    )
    db.session.flush()
    return people


def _person_values(values):
    employee_id = _required_text(values.get("employee_id"), "Employee ID")
    first_name = _normalize_person_name(values.get("first_name"), "First name")
    last_name = _normalize_person_name(values.get("last_name"), "Last name")
    seniority_date = _parse_date(values.get("seniority_date"), "Seniority date")
    classification = _normalize_choice(
        values.get("classification"),
        STAFFING_CLASSIFICATIONS,
        "classification",
    )
    employee_status = _normalize_choice(
        values.get("employee_status") or "active",
        STAFFING_EMPLOYEE_STATUSES,
        "Employee Status",
    )
    phone_number = _normalize_phone_number(values.get("phone_number"))
    active = _parse_bool(values.get("active"), default=True)

    return {
        "employee_id": employee_id,
        "first_name": first_name,
        "last_name": last_name,
        "seniority_date": seniority_date,
        "classification": classification,
        "employee_status": employee_status,
        "phone_number": phone_number,
        "active": active,
    }


def _apply_person_values(person, person_values):
    person.employee_id = person_values["employee_id"]
    person.first_name = person_values["first_name"]
    person.last_name = person_values["last_name"]
    person.seniority_date = person_values["seniority_date"]
    person.phone_number = person_values["phone_number"]
    person.classification = person_values["classification"]
    person.employee_status = person_values["employee_status"]
    person.active = person_values["active"]
    return person


def delete_person(person):
    reporting_count = StaffingReportingRelationship.query.filter(
        or_(
            StaffingReportingRelationship.person_id == person.id,
            StaffingReportingRelationship.reports_to_person_id == person.id,
        )
    ).count()
    if reporting_count:
        raise ValueError(
            "This person has Reports To history and cannot be deleted. Deactivate the person instead."
        )
    change_request_count = StaffingChangeRequest.query.filter(
        or_(
            StaffingChangeRequest.person_id == person.id,
            StaffingChangeRequest.submitted_by_person_id == person.id,
        )
    ).count()
    if change_request_count:
        raise ValueError(
            "This person has employee change-request history and cannot be deleted. "
            "Deactivate the person instead."
        )
    StaffingWorkAssignment.query.filter_by(person_id=person.id).delete()
    StaffingLeadershipAssignment.query.filter_by(person_id=person.id).delete()
    db.session.delete(person)
    db.session.flush()


def toggle_person_active(person):
    person.active = not person.active
    end_invalid_reporting_relationships_for_person(person)
    return person


def create_unit(values):
    unit = StaffingUnit()
    update_unit(unit, values, is_new=True)
    db.session.add(unit)
    db.session.flush()
    return unit


def update_unit(unit, values, is_new=False):
    normalized = validated_unit_update_values(unit, values, is_new=is_new)

    unit.unit_type = normalized["unit_type"]
    unit.name = normalized["name"]
    unit.parent = normalized["parent"]
    unit.display_order = normalized["display_order"]
    unit.active = normalized["active"]
    unit.required_headcount = normalized["required_headcount"]
    return unit


def validated_unit_update_values(unit, values, is_new=False):
    unit_type = _normalize_choice(values.get("unit_type"), STAFFING_UNIT_TYPES, "unit type")
    name = _required_text(values.get("name"), "Unit name")
    parent = _resolve_parent(values.get("parent_id"), unit_type)
    display_order = _parse_int(values.get("display_order"), default=0)
    active = _parse_bool(values.get("active"), default=True)
    required_headcount = None
    if unit_type == "work_area":
        required_headcount = _parse_optional_int(
            values.get("required_headcount"),
            minimum=0,
            label="Planned staffing",
        )

    if not is_new and parent and parent.id == unit.id:
        raise ValueError("A unit cannot be its own parent.")
    if not is_new and parent and _unit_is_descendant(parent, unit):
        raise ValueError("A unit cannot move under one of its descendants.")

    return {
        "unit_type": unit_type,
        "name": name,
        "parent": parent,
        "parent_id": parent.id if parent else None,
        "display_order": display_order,
        "active": active,
        "required_headcount": required_headcount,
    }


def delete_unit(unit):
    if unit.children:
        raise ValueError("Remove child units before deleting this unit.")
    if any(assignment.active for assignment in unit.work_assignments):
        raise ValueError("Remove work assignments before deleting this work area.")
    if any(assignment.active for assignment in unit.leadership_assignments):
        raise ValueError("Remove leadership assignments before deleting this unit.")
    parent = unit.parent
    db.session.delete(unit)
    db.session.flush()
    if parent in db.session:
        db.session.expire(parent, ["children"])


def assign_work_area(person, work_area, effective_date=None):
    _validate_work_assignment(person, work_area)
    parsed_effective_date = _parse_optional_date(effective_date)
    assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id).first()
    if assignment:
        assignment.work_area = work_area
        assignment.active = True
    else:
        assignment = StaffingWorkAssignment(person=person, work_area=work_area, active=True)
        db.session.add(assignment)
    assignment.effective_date = parsed_effective_date
    db.session.flush()
    return assignment


def clear_work_assignment(person):
    assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id).first()
    if assignment and assignment.active:
        work_area = assignment.work_area
        assignment.active = False
        db.session.flush()
        if person in db.session:
            db.session.expire(person, ["work_assignment"])
        if work_area in db.session:
            db.session.expire(work_area, ["work_assignments"])
    return None


def bulk_update_work_area_assignments(person_ids, action, work_area=None):
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"assign", "move", "clear"}:
        raise ValueError("Choose a valid bulk action.")

    ids = _normalized_person_ids(person_ids)
    if not ids:
        raise ValueError("Select at least one person.")

    if normalized_action in {"assign", "move"}:
        if not work_area or work_area.unit_type != "work_area":
            raise ValueError("Select a valid Work Area.")

    people = (
        StaffingPerson.query.filter(StaffingPerson.id.in_(ids))
        .order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id)
        .all()
    )
    people_by_id = {person.id: person for person in people}
    result = {"updated": 0, "skipped": [], "missing": []}

    for person_id in ids:
        person = people_by_id.get(person_id)
        if not person:
            result["missing"].append(str(person_id))
            continue

        if person.classification not in NON_MANAGEMENT_CLASSIFICATIONS:
            result["skipped"].append(person.full_name or person.employee_id)
            continue

        if normalized_action == "clear":
            clear_work_assignment(person)
        else:
            assign_work_area(person, work_area)
        result["updated"] += 1

    db.session.flush()
    return result


def create_leadership_assignment(person, unit, leadership_level=None):
    level = leadership_level or default_leadership_level_for(person, unit)
    validate_leadership_assignment(person, unit, level)

    existing = StaffingLeadershipAssignment.query.filter_by(
        person_id=person.id,
        unit_id=unit.id,
        leadership_level=level,
    ).first()
    if existing and existing.active:
        raise ValueError("This leadership assignment already exists.")
    if existing:
        existing.active = True
        db.session.flush()
        return existing

    assignment = StaffingLeadershipAssignment(
        person=person,
        unit=unit,
        leadership_level=level,
        active=True,
    )
    db.session.add(assignment)
    db.session.flush()
    return assignment


def validate_leadership_assignment(person, unit, leadership_level=None):
    level = leadership_level or default_leadership_level_for(person, unit)
    _validate_leadership_assignment(person, unit, level)
    return level


def delete_leadership_assignment(assignment):
    person = assignment.person
    unit = assignment.unit
    assignment.active = False
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
            assignment.active = False
    db.session.flush()
    if person in db.session:
        db.session.expire(person, ["leadership_assignments"])


def reporting_relationship_revision(relationship):
    if not relationship:
        return "none"
    updated_at = relationship.updated_at or relationship.created_at
    timestamp = updated_at.isoformat(timespec="microseconds") if updated_at else ""
    return f"{relationship.id}:{timestamp}"


def validate_reporting_relationship(person, reports_to_person):
    if not person or not person.active:
        raise ValueError("The selected management person is not active.")
    if person.classification == "division_manager":
        raise ValueError("Division Managers do not have a Reports To assignment.")
    required_classification = REPORTING_TARGET_CLASSIFICATION.get(person.classification)
    if not required_classification:
        raise ValueError("This classification does not have a management Reports To assignment.")
    if not reports_to_person or not reports_to_person.active:
        raise ValueError("Select an active Reports To person.")
    if person.id == reports_to_person.id:
        raise ValueError("A person cannot report to themselves.")
    if reports_to_person.classification != required_classification:
        required_label = CLASSIFICATION_LABELS[required_classification]
        raise ValueError(
            f"{CLASSIFICATION_LABELS[person.classification]} must report to a {required_label}."
        )
    return True


def update_reporting_relationship(
    person_id,
    reports_to_person_id,
    expected_revision,
    effective_date=None,
):
    try:
        subject_id = int(person_id)
        target_id = int(reports_to_person_id)
    except (TypeError, ValueError):
        raise ValueError("Select a valid Reports To person.")
    if not str(expected_revision or "").strip():
        raise ValueError("Reporting relationship version is required. Reload Management View.")

    people = (
        StaffingPerson.query.filter(StaffingPerson.id.in_({subject_id, target_id}))
        .order_by(StaffingPerson.id)
        .with_for_update()
        .all()
    )
    people_by_id = {person.id: person for person in people}
    person = people_by_id.get(subject_id)
    reports_to_person = people_by_id.get(target_id)
    if not person:
        raise ValueError("The selected management person was not found.")
    validate_reporting_relationship(person, reports_to_person)

    active_relationships = (
        StaffingReportingRelationship.query.filter_by(
            person_id=person.id,
            active=True,
        )
        .order_by(StaffingReportingRelationship.id)
        .with_for_update()
        .all()
    )
    if len(active_relationships) > 1:
        raise ValueError("Multiple active Reports To records require configuration repair.")
    current_relationship = active_relationships[0] if active_relationships else None
    current_revision = reporting_relationship_revision(current_relationship)
    if str(expected_revision).strip() != current_revision:
        raise ValueError(
            "Reports To changed while you were editing. Latest Management View has been loaded."
        )

    as_of = effective_date or date.today()
    purged = purge_expired_reporting_relationship_history(as_of)
    if (
        current_relationship
        and current_relationship.reports_to_person_id == reports_to_person.id
    ):
        return {
            "relationship": current_relationship,
            "changed": bool(purged),
            "purged": purged,
        }

    now = datetime.utcnow()
    if current_relationship:
        current_relationship.active = False
        current_relationship.effective_end = as_of
        current_relationship.updated_at = now

    relationship = StaffingReportingRelationship(
        person_id=person.id,
        reports_to_person_id=reports_to_person.id,
        active=True,
        effective_start=as_of,
        effective_end=None,
        created_at=now,
        updated_at=now,
    )
    db.session.add(relationship)
    db.session.flush()
    return {
        "relationship": relationship,
        "changed": True,
        "purged": purged,
    }


def purge_expired_reporting_relationship_history(as_of=None):
    cutoff = (as_of or date.today()) - timedelta(
        days=REPORTING_HISTORY_RETENTION_DAYS
    )
    expired_rows = StaffingReportingRelationship.query.filter(
        StaffingReportingRelationship.active.is_(False),
        StaffingReportingRelationship.effective_end < cutoff,
    ).all()
    for relationship in expired_rows:
        db.session.delete(relationship)
    return len(expired_rows)


def end_invalid_reporting_relationships_for_person(person, as_of=None):
    if not person.id:
        return 0
    ended_on = as_of or date.today()
    relationships = StaffingReportingRelationship.query.filter(
        StaffingReportingRelationship.active.is_(True),
        or_(
            StaffingReportingRelationship.person_id == person.id,
            StaffingReportingRelationship.reports_to_person_id == person.id,
        ),
    ).all()
    related_ids = {
        related_id
        for relationship in relationships
        for related_id in (
            relationship.person_id,
            relationship.reports_to_person_id,
        )
    }
    people_by_id = {
        row.id: row
        for row in StaffingPerson.query.filter(StaffingPerson.id.in_(related_ids or {-1})).all()
    }
    changed = 0
    now = datetime.utcnow()
    for relationship in relationships:
        subject = people_by_id.get(relationship.person_id)
        supervisor = people_by_id.get(relationship.reports_to_person_id)
        if _reporting_tiers_are_valid(subject, supervisor):
            continue
        relationship.active = False
        relationship.effective_end = ended_on
        relationship.updated_at = now
        changed += 1
    purge_expired_reporting_relationship_history(ended_on)
    if changed:
        db.session.flush()
    return changed


def can_user_directly_edit_reporting_relationship(user, app_role):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", None) == "grandmaster" or app_role == "grandmaster":
        return True
    if app_role == "watcher":
        return False
    employee_id = str(getattr(user, "employee_id", "") or "").strip()
    if not employee_id:
        return False
    person = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        func.lower(StaffingPerson.employee_id) == employee_id.lower(),
    ).first()
    return bool(
        person
        and person.classification in DIRECT_REPORTING_EDITOR_CLASSIFICATIONS
    )


def default_leadership_level_for(person, unit):
    classification = person.classification
    if classification == "part_time_supervisor" and unit.unit_type == "work_area":
        return "work_area"
    if classification == "full_time_supervisor" and unit.unit_type == "department":
        return "department"
    if classification == "manager" and unit.unit_type == "operation":
        return "operation"
    if classification == "division_manager" and unit.unit_type == "sort":
        return "sort"
    if classification == "full_time_specialist" and unit.unit_type in {"department", "operation"}:
        return unit.unit_type
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


def dashboard_context(filters=None):
    filters = filters or {}
    selected_sort = _resolve_optional_unit(filters.get("sort_id"), "sort")
    selected_operation = _resolve_optional_unit(filters.get("operation_id"), "operation")
    selected_department = _resolve_optional_unit(filters.get("department_id"), "department")
    selected_work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if selected_work_area:
        area_department, area_operation, area_sort = parent_chain_for_work_area(selected_work_area)
        selected_department = selected_department or area_department
        selected_operation = selected_operation or area_operation
        selected_sort = selected_sort or area_sort
    if selected_department and selected_operation is None:
        selected_operation = selected_department.parent
    if selected_operation and selected_sort is None:
        selected_sort = selected_operation.parent

    operations = _board_operations(selected_sort)
    departments = _board_departments(selected_operation, operations)
    assigned_by_work_area = _board_assigned_counts()
    leadership_index = _board_leadership_index()
    search = str(filters.get("search") or "").strip().lower()
    understaffed_only = _parse_bool(filters.get("understaffed_only"), default=False)
    missing_leadership_only = _parse_bool(filters.get("missing_leadership_only"), default=False)

    cards = []
    for work_area in StaffingUnit.query.filter_by(unit_type="work_area", active=True).all():
        department, operation, sort = _board_parent_chain(work_area)
        if selected_sort and (not sort or sort.id != selected_sort.id):
            continue
        if selected_operation and (not operation or operation.id != selected_operation.id):
            continue
        if selected_department and (not department or department.id != selected_department.id):
            continue
        if selected_work_area and work_area.id != selected_work_area.id:
            continue
        path = unit_path(work_area)
        if search and search not in f"{work_area.name} {path}".lower():
            continue

        assigned = int(assigned_by_work_area.get(work_area.id, 0) or 0)
        staffing_gap = staffing_gap_for_work_area(work_area, assigned)
        planned = staffing_gap["planned"]
        open_positions = staffing_gap["open_positions"]
        extra_staffing = staffing_gap["extra_staffing"]
        coverage = staffing_gap["coverage"]
        required_configured = staffing_gap["planned_configured"]
        status, status_color = _coverage_status(assigned, planned, open_positions)
        leadership = _board_work_area_leadership_counts(
            leadership_index,
            sort,
            operation,
            department,
            work_area,
        )
        missing_leadership = _board_missing_leadership(leadership)
        if understaffed_only and open_positions <= 0:
            continue
        if missing_leadership_only and not missing_leadership:
            continue

        cards.append(
            {
                "unit": work_area,
                "path": path,
                "sort": sort,
                "operation": operation,
                "department": department,
                "assigned": assigned,
                "required": planned,
                "planned": planned,
                "required_configured": required_configured,
                "planned_configured": required_configured,
                "open": open_positions,
                "open_positions": open_positions,
                "extra": extra_staffing,
                "extra_staffing": extra_staffing,
                "gap": staffing_gap["gap"],
                "coverage": coverage,
                "coverage_bar": min(coverage, 100),
                "status": status,
                "status_color": status_color,
                "leadership": leadership,
                "missing_leadership": missing_leadership,
                "has_missing_leadership": bool(missing_leadership),
            }
        )

    cards.sort(
        key=lambda row: (
            row["sort"].display_order if row["sort"] else 0,
            row["sort"].name.lower() if row["sort"] else "",
            row["operation"].display_order if row["operation"] else 0,
            row["operation"].name.lower() if row["operation"] else "",
            row["department"].display_order if row["department"] else 0,
            row["department"].name.lower() if row["department"] else "",
            row["unit"].display_order,
            row["unit"].name.lower(),
            row["unit"].id,
        )
    )

    rollups = {
        "sorts": _board_rollups(cards, "sort"),
        "operations": _board_rollups(cards, "operation"),
        "departments": _board_rollups(cards, "department"),
    }
    gap_analysis = staffing_gap_analysis(cards)
    summary = {
        "total_employees": sum(card["assigned"] for card in cards),
        "total_assigned": sum(card["assigned"] for card in cards),
        "total_required": sum(card["planned"] for card in cards),
        "total_planned": sum(card["planned"] for card in cards),
        "total_open": sum(card["open_positions"] for card in cards),
        "total_extra": sum(card["extra_staffing"] for card in cards),
        "understaffed_work_areas": sum(1 for card in cards if card["open"] > 0),
        "missing_leadership_work_areas": sum(1 for card in cards if card["has_missing_leadership"]),
        "default_required_work_areas": sum(1 for card in cards if not card["required_configured"]),
        "default_planned_work_areas": sum(1 for card in cards if not card["planned_configured"]),
        "most_understaffed": gap_analysis["most_understaffed"],
        "most_overstaffed": gap_analysis["most_overstaffed"],
        "missing_leadership": gap_analysis["missing_leadership"],
        "default_required": [card for card in cards if not card["required_configured"]][:3],
        "default_planned": [card for card in cards if not card["planned_configured"]][:3],
    }

    return {
        "summary": summary,
        "hierarchy": staffing_hierarchy_tree(),
        "work_area_cards": cards,
        "selected_work_area": cards[0] if cards else None,
        "rollups": rollups,
        "sorts": units_by_type("sort"),
        "operations": operations,
        "departments": departments,
        "work_areas": _required_headcount_work_areas(selected_department, selected_operation),
        "filters": {
            "sort_id": str(selected_sort.id) if selected_sort else "",
            "operation_id": str(selected_operation.id) if selected_operation else "",
            "department_id": str(selected_department.id) if selected_department else "",
            "work_area_id": str(selected_work_area.id) if selected_work_area else "",
            "search": filters.get("search", ""),
            "understaffed_only": "1" if understaffed_only else "",
            "missing_leadership_only": "1" if missing_leadership_only else "",
        },
    }


def _board_operations(selected_sort):
    all_operations = units_by_type("operation")
    if selected_sort:
        return [operation for operation in all_operations if operation.parent_id == selected_sort.id]
    return all_operations


def _board_departments(selected_operation, operations):
    if selected_operation:
        return _departments_under(selected_operation)
    operation_ids = {operation.id for operation in operations}
    return (
        StaffingUnit.query.filter(
            StaffingUnit.unit_type == "department",
            StaffingUnit.parent_id.in_(operation_ids or {-1}),
        )
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def required_headcount_context(filters=None):
    filters = filters or {}
    selected_sort = _resolve_optional_unit(filters.get("sort_id"), "sort")
    selected_operation = _resolve_optional_unit(filters.get("operation_id"), "operation")
    selected_department = _resolve_optional_unit(filters.get("department_id"), "department")
    selected_work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if selected_work_area:
        area_department, area_operation, area_sort = parent_chain_for_work_area(selected_work_area)
        selected_department = selected_department or area_department
        selected_operation = selected_operation or area_operation
        selected_sort = selected_sort or area_sort
    if selected_department and selected_operation is None:
        selected_operation = selected_department.parent
    if selected_operation and selected_sort is None:
        selected_sort = selected_operation.parent

    operations = _board_operations(selected_sort)
    departments = _board_departments(selected_operation, operations)
    assigned_by_work_area = _board_assigned_counts()
    rows = []
    for work_area in StaffingUnit.query.filter_by(unit_type="work_area", active=True).all():
        department, operation, sort = _board_parent_chain(work_area)
        if selected_sort and (not sort or sort.id != selected_sort.id):
            continue
        if selected_operation and (not operation or operation.id != selected_operation.id):
            continue
        if selected_department and (not department or department.id != selected_department.id):
            continue
        if selected_work_area and work_area.id != selected_work_area.id:
            continue
        assigned = int(assigned_by_work_area.get(work_area.id, 0) or 0)
        staffing_gap = staffing_gap_for_work_area(work_area, assigned)
        configured = staffing_gap["planned_configured"]
        planned = staffing_gap["planned"]
        rows.append(
            {
                "unit": work_area,
                "sort": sort,
                "operation": operation,
                "department": department,
                "path": unit_path(work_area),
                "configured": configured,
                "required": planned,
                "planned": planned,
                "assigned": assigned,
                "difference": assigned - planned,
                "gap": staffing_gap["gap"],
                "open_positions": staffing_gap["open_positions"],
                "extra_staffing": staffing_gap["extra_staffing"],
            }
        )
    rows.sort(
        key=lambda row: (
            row["sort"].display_order if row["sort"] else 0,
            row["sort"].name.lower() if row["sort"] else "",
            row["operation"].display_order if row["operation"] else 0,
            row["operation"].name.lower() if row["operation"] else "",
            row["department"].display_order if row["department"] else 0,
            row["department"].name.lower() if row["department"] else "",
            row["unit"].display_order,
            row["unit"].name.lower(),
            row["unit"].id,
        )
    )
    return {
        "rows": rows,
        "sorts": units_by_type("sort"),
        "operations": operations,
        "departments": departments,
        "work_areas": _required_headcount_work_areas(selected_department, selected_operation),
        "filters": {
            "sort_id": str(selected_sort.id) if selected_sort else "",
            "operation_id": str(selected_operation.id) if selected_operation else "",
            "department_id": str(selected_department.id) if selected_department else "",
            "work_area_id": str(selected_work_area.id) if selected_work_area else "",
        },
    }


def update_required_headcount(work_area, raw_required_headcount):
    if work_area.unit_type != "work_area":
        raise ValueError("Planned staffing can only be set for Work Areas.")
    work_area.required_headcount = _parse_optional_int(
        raw_required_headcount,
        minimum=0,
        label="Planned staffing",
    )
    db.session.flush()
    return work_area


def staffing_gap_for_work_area(work_area, assigned_count):
    assigned = int(assigned_count or 0)
    planned_configured = work_area.required_headcount is not None
    planned = int(work_area.required_headcount if planned_configured else assigned)
    gap = assigned - planned
    open_positions = max(0, planned - assigned)
    extra_staffing = max(0, assigned - planned)
    return {
        "work_area": work_area,
        "assigned": assigned,
        "assigned_staffing": assigned,
        "planned": planned,
        "planned_staffing": planned,
        "planned_configured": planned_configured,
        "open_positions": open_positions,
        "extra_staffing": extra_staffing,
        "gap": gap,
        "coverage": _coverage_percent(assigned, planned),
    }


def staffing_gap_analysis(cards, limit=3):
    understaffed = sorted(
        [card for card in cards if card["open_positions"] > 0],
        key=lambda row: (-row["open_positions"], row["unit"].name.lower(), row["unit"].id),
    )[:limit]
    overstaffed = sorted(
        [card for card in cards if card["extra_staffing"] > 0],
        key=lambda row: (-row["extra_staffing"], row["unit"].name.lower(), row["unit"].id),
    )[:limit]
    missing_leadership = [card for card in cards if card["has_missing_leadership"]][:limit]
    return {
        "most_understaffed": understaffed,
        "most_overstaffed": overstaffed,
        "missing_leadership": missing_leadership,
    }


def _required_headcount_work_areas(selected_department, selected_operation):
    query = StaffingUnit.query.filter_by(unit_type="work_area", active=True)
    if selected_department:
        query = query.filter(StaffingUnit.parent_id == selected_department.id)
    elif selected_operation:
        query = query.filter(StaffingUnit.id.in_(work_area_ids_under(selected_operation) or {-1}))
    return query.order_by(StaffingUnit.display_order, StaffingUnit.name).all()


def _board_assigned_counts():
    return {
        work_area_id: int(count or 0)
        for work_area_id, count in (
            db.session.query(
                StaffingWorkAssignment.work_area_unit_id,
                func.count(StaffingWorkAssignment.id),
            )
            .join(StaffingPerson)
            .filter(
                StaffingPerson.active.is_(True),
                StaffingPerson.classification.in_(NON_MANAGEMENT_CLASSIFICATIONS),
                StaffingWorkAssignment.active.is_(True),
            )
            .group_by(StaffingWorkAssignment.work_area_unit_id)
            .all()
        )
    }


def _board_leadership_index():
    index = {}
    assignments = (
        StaffingLeadershipAssignment.query.join(StaffingPerson)
        .filter(
            StaffingLeadershipAssignment.active.is_(True),
            StaffingPerson.active.is_(True),
        )
        .all()
    )
    for assignment in assignments:
        index.setdefault(assignment.unit_id, {}).setdefault(assignment.person.classification, 0)
        index[assignment.unit_id][assignment.person.classification] += 1
    return index


def _board_parent_chain(work_area):
    return parent_chain_for_work_area(work_area)


def parent_chain_for_work_area(work_area):
    department = None
    operation = None
    sort = None
    if not work_area:
        return department, operation, sort
    parent = work_area.parent
    if parent and parent.unit_type == "department":
        department = parent
        operation = parent.parent if parent.parent and parent.parent.unit_type == "operation" else None
    elif parent and parent.unit_type == "operation":
        operation = parent
    if operation and operation.parent and operation.parent.unit_type == "sort":
        sort = operation.parent
    return department, operation, sort


def _board_work_area_leadership_counts(index, sort, operation, department, work_area):
    return {
        "pt_supervisors": int(index.get(work_area.id if work_area else None, {}).get("part_time_supervisor", 0)),
        "ft_supervisors": int(index.get(department.id if department else None, {}).get("full_time_supervisor", 0)),
        "managers": int(index.get(operation.id if operation else None, {}).get("manager", 0)),
        "division_managers": int(index.get(sort.id if sort else None, {}).get("division_manager", 0)),
    }


def _board_missing_leadership(leadership):
    missing = []
    if leadership["pt_supervisors"] <= 0:
        missing.append("PT Supervisor")
    if leadership["ft_supervisors"] <= 0:
        missing.append("FT Supervisor")
    if leadership["managers"] <= 0:
        missing.append("Manager")
    if leadership["division_managers"] <= 0:
        missing.append("Division Manager")
    return missing


def _coverage_percent(assigned, required):
    if required <= 0:
        return 100 if assigned > 0 else 100
    return int(round((assigned / required) * 100))


def _coverage_status(assigned, required, open_positions):
    if required <= 0 or open_positions <= 0 or assigned >= required:
        return "On Track", "green"
    coverage = assigned / required
    if coverage >= STAFFING_NEAR_TARGET_THRESHOLD:
        return "Near Target", "yellow"
    return "Understaffed", "red"


def _board_rollups(cards, key):
    buckets = {}
    for card in cards:
        unit = card.get(key)
        if not unit:
            continue
        bucket = buckets.setdefault(
            unit.id,
            {
                "unit": unit,
                "path": unit_path(unit),
                "assigned": 0,
                "required": 0,
                "planned": 0,
                "open": 0,
                "extra": 0,
                "extra_staffing": 0,
                "coverage": 100,
                "work_area_count": 0,
            },
        )
        bucket["assigned"] += card["assigned"]
        bucket["required"] += card["planned"]
        bucket["planned"] += card["planned"]
        bucket["open"] += card["open_positions"]
        bucket["extra"] += card["extra_staffing"]
        bucket["extra_staffing"] += card["extra_staffing"]
        bucket["work_area_count"] += 1
    for bucket in buckets.values():
        bucket["coverage"] = _coverage_percent(bucket["assigned"], bucket["required"])
    return sorted(
        buckets.values(),
        key=lambda row: (
            row["unit"].display_order,
            row["unit"].name.lower(),
            row["unit"].id,
        ),
    )


def seniority_context(filters=None):
    filters = filters or {}
    sorts = units_by_type("sort")
    all_operations = units_by_type("operation")
    selected_sort = _resolve_optional_unit(filters.get("sort_id"), "sort")
    operations = [
        operation
        for operation in all_operations
        if selected_sort is None or operation.parent_id == selected_sort.id
    ]
    selected_operation = _resolve_selected_operation(filters.get("operation_id"), operations, all_operations)
    if selected_operation and selected_sort is None:
        selected_sort = selected_operation.parent

    include_management = _parse_bool(filters.get("include_management"), default=False)
    rows = []
    if selected_operation:
        allowed_work_area_ids = work_area_ids_under(selected_operation)
        rows.extend(
            _seniority_work_assignment_rows(
                selected_operation,
                allowed_work_area_ids,
                filters,
            )
        )
        if include_management:
            rows.extend(_seniority_management_rows(selected_operation, filters))

    rows.sort(
        key=lambda row: (
            row["person"].seniority_date,
            str(row["person"].employee_id or ""),
            row["person"].id,
            row["scope_name"],
        )
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    counts = {
        "total": len(rows),
        "part_time": sum(1 for row in rows if row["person"].classification == "part_time"),
        "combo": sum(1 for row in rows if row["person"].classification == "full_time_combo"),
        "supervisors": sum(1 for row in rows if row["person"].classification in SUPERVISOR_CLASSIFICATIONS),
        "managers": sum(1 for row in rows if row["person"].classification in MANAGER_CLASSIFICATIONS),
    }

    selected_department = _resolve_optional_unit(filters.get("department_id"), "department")
    selected_work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    return {
        "sorts": sorts,
        "operations": operations,
        "departments": _departments_under(selected_operation),
        "work_areas": _work_areas_under(selected_operation),
        "selected_sort": selected_sort,
        "selected_operation": selected_operation,
        "selected_department": selected_department,
        "selected_work_area": selected_work_area,
        "rows": rows,
        "counts": counts,
        "include_management": include_management,
        "filters": {
            "sort_id": str(selected_sort.id) if selected_sort else "",
            "operation_id": str(selected_operation.id) if selected_operation else "",
            "classification": filters.get("classification", ""),
            "employee_status": filters.get("employee_status", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "search": filters.get("search", ""),
            "active": filters.get("active", "active") or "active",
            "include_management": "1" if include_management else "",
        },
        "hierarchy": staffing_hierarchy_tree(),
    }


def people_context(filters=None, user=None):
    filters = filters or {}
    filters = _with_default_management_scope(filters, user)
    sorts = units_by_type("sort")
    all_operations = units_by_type("operation")
    selected_sort = _resolve_optional_unit(filters.get("sort_id"), "sort")
    operations = [
        operation
        for operation in all_operations
        if selected_sort is None or operation.parent_id == selected_sort.id
    ]
    selected_operation = _resolve_optional_unit(filters.get("operation_id"), "operation")
    if selected_operation and selected_sort and selected_operation.parent_id != selected_sort.id:
        selected_operation = None
    if selected_operation and selected_sort is None:
        selected_sort = selected_operation.parent
    selected_department = _resolve_optional_unit(filters.get("department_id"), "department")
    selected_work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if selected_work_area:
        area_department, area_operation, area_sort = parent_chain_for_work_area(selected_work_area)
        selected_department = selected_department or area_department
        selected_operation = selected_operation or area_operation
        selected_sort = selected_sort or area_sort
    if selected_department and selected_operation is None:
        selected_operation = selected_department.parent
    if selected_operation and selected_sort is None:
        selected_sort = selected_operation.parent
    if selected_sort:
        operations = [
            operation
            for operation in all_operations
            if operation.parent_id == selected_sort.id
        ]

    rows = _people_rows()
    rows = _filter_people_rows(
        rows,
        {
            **filters,
            "selected_sort": selected_sort,
            "selected_operation": selected_operation,
            "selected_department": selected_department,
            "selected_work_area": selected_work_area,
        },
    )
    total_matches = len(rows)
    rows.sort(
        key=lambda row: (
            row["person"].last_name.lower(),
            row["person"].first_name.lower(),
            str(row["person"].employee_id or ""),
            row["person"].id,
        )
    )

    page, per_page = _pagination_from_filters(filters)
    if per_page:
        total_pages = max((total_matches + per_page - 1) // per_page, 1)
        page = min(page, total_pages)
        start = (page - 1) * per_page
        paginated_rows = rows[start : start + per_page]
    else:
        total_pages = 1
        paginated_rows = rows

    selected_person = _resolve_people_detail(filters.get("person_id"), rows)
    if selected_person is None and paginated_rows:
        selected_person = paginated_rows[0]

    counts = {
        "total": total_matches,
        "shown": len(paginated_rows),
        "active": sum(1 for row in rows if row["person"].active),
        "inactive": sum(1 for row in rows if not row["person"].active),
        "supervisors": sum(1 for row in rows if row["person"].classification in SUPERVISOR_CLASSIFICATIONS),
        "managers": sum(1 for row in rows if row["person"].classification in MANAGER_CLASSIFICATIONS),
        "assigned": sum(1 for row in rows if row["work_assignment"] and row["work_assignment"].active),
        "unassigned": sum(
            1
            for row in rows
            if row["person"].classification in NON_MANAGEMENT_CLASSIFICATIONS
            and not (row["work_assignment"] and row["work_assignment"].active)
        ),
    }

    selected_unit = selected_work_area or selected_department or selected_operation or selected_sort

    return {
        "sorts": sorts,
        "operations": operations,
        "departments": _departments_under(selected_operation),
        "direct_work_areas": _direct_child_work_areas(selected_operation),
        "department_work_areas": _direct_child_work_areas(selected_department),
        "work_areas": _work_areas_under(selected_operation),
        "selected_sort": selected_sort,
        "selected_operation": selected_operation,
        "selected_department": selected_department,
        "selected_work_area": selected_work_area,
        "selected_unit": selected_unit,
        "selected_unit_leadership": _leadership_assignments_for_unit(selected_unit),
        "management_candidates": management_candidates_for_unit(selected_unit),
        "rows": paginated_rows,
        "all_rows": rows,
        "counts": counts,
        "selected_person": selected_person,
        "leadership_only": _parse_bool(filters.get("leadership_only"), default=False),
        "assignment_status": str(filters.get("assignment_status") or "").strip(),
        "pagination": {
            "page": page,
            "per_page": per_page or total_matches or PEOPLE_DEFAULT_PAGE_SIZE,
            "total": total_matches,
            "total_pages": total_pages,
            "has_previous": bool(per_page and page > 1),
            "has_next": bool(per_page and page < total_pages),
        },
        "filters": {
            "sort_id": str(selected_sort.id) if selected_sort else "",
            "operation_id": str(selected_operation.id) if selected_operation else "",
            "classification": filters.get("classification", ""),
            "employee_status": filters.get("employee_status", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "search": filters.get("search", ""),
            "active": filters.get("active", "active") or "active",
            "assignment_status": filters.get("assignment_status", ""),
            "page": str(page),
            "per_page": str(per_page or "all"),
            "leadership_only": "1" if _parse_bool(filters.get("leadership_only"), default=False) else "",
            "person_id": str(selected_person["person"].id) if selected_person else "",
        },
        "hierarchy": staffing_hierarchy_tree(),
    }


def selectable_parent_units(unit_type):
    expected_parent_type = PARENT_TYPE_BY_UNIT_TYPE.get(unit_type)
    if expected_parent_type is None:
        return []
    if isinstance(expected_parent_type, tuple):
        return (
            StaffingUnit.query.filter(StaffingUnit.unit_type.in_(expected_parent_type))
            .order_by(StaffingUnit.unit_type, StaffingUnit.display_order, StaffingUnit.name)
            .all()
        )
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


def management_candidates_for_unit(unit):
    if not unit:
        return []
    candidates = []
    people = (
        StaffingPerson.query.filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.classification.in_(MANAGEMENT_CLASSIFICATIONS),
        )
        .order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.employee_id)
        .all()
    )
    for person in people:
        try:
            leadership_level = default_leadership_level_for(person, unit)
        except ValueError:
            continue
        linked_user = linked_user_for_person(person)
        if not linked_user:
            continue
        candidates.append(
            {
                "person": person,
                "leadership_level": leadership_level,
                "linked_user": linked_user,
            }
        )
    return candidates


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


def org_chart_context(selected_unit_id=None):
    selected_unit = None
    if selected_unit_id:
        try:
            selected_unit = db.session.get(StaffingUnit, int(selected_unit_id))
        except (TypeError, ValueError):
            selected_unit = None
    root_units = units_by_type("sort")
    current_children = []
    if selected_unit:
        current_children = sorted(
            selected_unit.children,
            key=lambda row: (row.display_order, row.unit_type, row.name.lower(), row.id),
        )
    else:
        current_children = root_units
    unit_card_meta = _org_chart_unit_meta()
    selected_detail = unit_card_meta.get(selected_unit.id) if selected_unit else None
    work_area_detail = None
    if selected_unit and selected_unit.unit_type == "work_area":
        assigned_count = StaffingWorkAssignment.query.filter_by(
            work_area_unit_id=selected_unit.id,
            active=True,
        ).count()
        pt_supervisors = [
            assignment
            for assignment in selected_unit.leadership_assignments
            if assignment.active
            and assignment.person.classification == "part_time_supervisor"
        ]
        work_area_detail = {
            "unit": selected_unit,
            "path": unit_path(selected_unit),
            "assigned_count": assigned_count,
            "pt_supervisors": pt_supervisors,
            "required_headcount": selected_unit.required_headcount,
        }
    return {
        "tree": staffing_hierarchy_tree(),
        "selected_unit": selected_unit,
        "breadcrumb": unit_breadcrumb(selected_unit),
        "current_children": current_children,
        "unit_card_meta": unit_card_meta,
        "selected_detail": selected_detail,
        "work_area_detail": work_area_detail,
        "units": StaffingUnit.query.order_by(
            StaffingUnit.unit_type,
            StaffingUnit.display_order,
            StaffingUnit.name,
        ).all(),
        "sorts": units_by_type("sort"),
        "operations": units_by_type("operation"),
        "departments": units_by_type("department"),
        "parent_units": selectable_parent_units("work_area"),
    }


def management_org_chart_context(selected_person_id=None):
    people = (
        StaffingPerson.query.filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.classification.in_(MANAGEMENT_CLASSIFICATIONS),
        )
        .order_by(
            StaffingPerson.last_name,
            StaffingPerson.first_name,
            StaffingPerson.employee_id,
        )
        .all()
    )
    person_ids = {person.id for person in people}
    people_by_id = {person.id: person for person in people}
    relationships = StaffingReportingRelationship.query.filter(
        StaffingReportingRelationship.active.is_(True),
        StaffingReportingRelationship.person_id.in_(person_ids or {-1}),
    ).all()
    leadership_assignments = StaffingLeadershipAssignment.query.filter(
        StaffingLeadershipAssignment.active.is_(True),
        StaffingLeadershipAssignment.person_id.in_(person_ids or {-1}),
    ).all()
    units = StaffingUnit.query.order_by(
        StaffingUnit.display_order,
        StaffingUnit.name,
        StaffingUnit.id,
    ).all()
    units_by_id = {unit.id: unit for unit in units}
    unit_paths = {
        unit.id: _unit_path_from_map(unit, units_by_id)
        for unit in units
    }

    assignments_by_person = {}
    for assignment in leadership_assignments:
        unit = units_by_id.get(assignment.unit_id)
        if not unit:
            continue
        assignments_by_person.setdefault(assignment.person_id, []).append(assignment)
    for assignments in assignments_by_person.values():
        assignments.sort(
            key=lambda row: (
                unit_paths.get(row.unit_id, "").lower(),
                row.leadership_level,
                row.id,
            )
        )

    relationship_by_person = {}
    for relationship in relationships:
        person = people_by_id.get(relationship.person_id)
        reports_to_person = people_by_id.get(relationship.reports_to_person_id)
        if not _reporting_tiers_are_valid(person, reports_to_person):
            continue
        relationship_by_person[person.id] = relationship

    children_by_supervisor = {}
    for person_id, relationship in relationship_by_person.items():
        children_by_supervisor.setdefault(
            relationship.reports_to_person_id,
            [],
        ).append(person_id)
    for child_ids in children_by_supervisor.values():
        child_ids.sort(key=lambda row_id: _management_person_sort_key(people_by_id[row_id]))

    def build_tree(person, ancestors=None):
        ancestors = set(ancestors or ())
        if person.id in ancestors:
            return {"person": person, "children": [], "mismatch": True}
        next_ancestors = ancestors | {person.id}
        relationship = relationship_by_person.get(person.id)
        alignment = None
        if relationship:
            alignment = _operational_reporting_alignment(
                person.id,
                relationship.reports_to_person_id,
                people_by_id,
                assignments_by_person,
                units_by_id,
            )
        return {
            "person": person,
            "children": [
                build_tree(people_by_id[child_id], next_ancestors)
                for child_id in children_by_supervisor.get(person.id, [])
            ],
            "mismatch": alignment is False,
        }

    division_managers = sorted(
        (
            person
            for person in people
            if person.classification == "division_manager"
        ),
        key=_management_person_sort_key,
    )
    unassigned_people = sorted(
        (
            person
            for person in people
            if person.classification in REPORTING_TARGET_CLASSIFICATION
            and person.id not in relationship_by_person
        ),
        key=_management_person_sort_key,
    )
    tree = [build_tree(person) for person in division_managers]
    unassigned_tree = [build_tree(person) for person in unassigned_people]

    selected_person = None
    try:
        selected_person = people_by_id.get(int(selected_person_id))
    except (TypeError, ValueError):
        selected_person = None

    selected_detail = None
    if selected_person:
        relationship = relationship_by_person.get(selected_person.id)
        reports_to_person = (
            people_by_id.get(relationship.reports_to_person_id)
            if relationship
            else None
        )
        alignment = None
        if reports_to_person:
            alignment = _operational_reporting_alignment(
                selected_person.id,
                reports_to_person.id,
                people_by_id,
                assignments_by_person,
                units_by_id,
            )
        target_classification = REPORTING_TARGET_CLASSIFICATION.get(
            selected_person.classification
        )
        candidates = []
        if target_classification:
            for candidate in people:
                if candidate.classification != target_classification:
                    continue
                suggested = _operational_reporting_alignment(
                    selected_person.id,
                    candidate.id,
                    people_by_id,
                    assignments_by_person,
                    units_by_id,
                )
                candidates.append(
                    {
                        "person": candidate,
                        "suggested": suggested is True,
                    }
                )
            candidates.sort(
                key=lambda row: (
                    not row["suggested"],
                    _management_person_sort_key(row["person"]),
                )
            )

        selected_detail = {
            "person": selected_person,
            "relationship": relationship,
            "relationship_revision": reporting_relationship_revision(relationship),
            "reports_to_person": reports_to_person,
            "operational_assignments": [
                {
                    "assignment": assignment,
                    "unit": units_by_id[assignment.unit_id],
                    "path": unit_paths[assignment.unit_id],
                }
                for assignment in assignments_by_person.get(selected_person.id, [])
                if assignment.unit_id in units_by_id
            ],
            "mismatch": alignment is False,
            "comparison_available": alignment is not None,
            "target_classification": target_classification,
            "candidates": candidates,
        }

    return {
        "tree": tree,
        "unassigned_tree": unassigned_tree,
        "unassigned_count": len(unassigned_people),
        "selected_person": selected_person,
        "selected_detail": selected_detail,
        "people_count": len(people),
    }


def _reporting_tiers_are_valid(person, reports_to_person):
    if not person or not reports_to_person or not person.active or not reports_to_person.active:
        return False
    return (
        REPORTING_TARGET_CLASSIFICATION.get(person.classification)
        == reports_to_person.classification
        and person.id != reports_to_person.id
    )


def _management_person_sort_key(person):
    return (
        MANAGEMENT_TREE_CLASSIFICATION_ORDER.get(person.classification, 99),
        person.last_name.lower(),
        person.first_name.lower(),
        person.employee_id.lower(),
        person.id,
    )


def _unit_path_from_map(unit, units_by_id):
    names = []
    visited = set()
    current = unit
    while current and current.id not in visited:
        visited.add(current.id)
        names.append(current.name)
        current = units_by_id.get(current.parent_id)
    return " / ".join(reversed(names))


def _operational_reporting_alignment(
    person_id,
    reports_to_person_id,
    people_by_id,
    assignments_by_person,
    units_by_id,
):
    person = people_by_id.get(person_id)
    reports_to_person = people_by_id.get(reports_to_person_id)
    if not _reporting_tiers_are_valid(person, reports_to_person):
        return False
    person_assignments = assignments_by_person.get(person_id, [])
    supervisor_assignments = assignments_by_person.get(reports_to_person_id, [])
    if not person_assignments or not supervisor_assignments:
        return None

    person_units = [
        units_by_id.get(assignment.unit_id)
        for assignment in person_assignments
        if units_by_id.get(assignment.unit_id)
    ]
    supervisor_units = [
        units_by_id.get(assignment.unit_id)
        for assignment in supervisor_assignments
        if units_by_id.get(assignment.unit_id)
    ]
    if person.classification == "part_time_supervisor":
        expected_department_ids = {
            unit.parent_id
            for unit in person_units
            if unit.unit_type == "work_area"
            and units_by_id.get(unit.parent_id)
            and units_by_id[unit.parent_id].unit_type == "department"
        }
        return any(
            unit.unit_type == "department" and unit.id in expected_department_ids
            for unit in supervisor_units
        )
    if person.classification == "full_time_specialist":
        for specialist_unit in person_units:
            for supervisor_unit in supervisor_units:
                if supervisor_unit.unit_type != "department":
                    continue
                if specialist_unit.unit_type == "department" and specialist_unit.id == supervisor_unit.id:
                    return True
                if specialist_unit.unit_type == "operation" and supervisor_unit.parent_id == specialist_unit.id:
                    return True
        return False
    if person.classification == "full_time_supervisor":
        expected_operation_ids = {
            unit.parent_id
            for unit in person_units
            if unit.unit_type == "department"
        }
        return any(
            unit.unit_type == "operation" and unit.id in expected_operation_ids
            for unit in supervisor_units
        )
    if person.classification == "manager":
        expected_sort_ids = {
            unit.parent_id
            for unit in person_units
            if unit.unit_type == "operation"
        }
        return any(
            unit.unit_type == "sort" and unit.id in expected_sort_ids
            for unit in supervisor_units
        )
    return None


def unit_breadcrumb(unit):
    if not unit:
        return []
    breadcrumb = []
    current = unit
    while current:
        breadcrumb.append(current)
        current = current.parent
    return list(reversed(breadcrumb))


def management_attendance_context_for_user(user):
    account_is_management = bool(getattr(user, "is_management", False))
    employee_id = str(getattr(user, "employee_id", "") or "").strip()
    if not employee_id:
        if not account_is_management:
            return {"is_management": False, "person": None, "assignments": [], "message": ""}
        return {
            "is_management": True,
            "person": None,
            "assignments": [],
            "message": "Add an Employee ID to your NeoApps account before assigned staffing scope can resolve.",
        }
    person = StaffingPerson.query.filter(
        func.lower(StaffingPerson.employee_id) == employee_id.lower()
    ).first()
    staffing_is_management = bool(
        person
        and person.active
        and person.classification in MANAGEMENT_CLASSIFICATIONS
    )
    is_management = account_is_management or staffing_is_management
    if not is_management:
        return {"is_management": False, "person": person, "assignments": [], "message": ""}
    if not person:
        return {
            "is_management": True,
            "person": None,
            "assignments": [],
            "message": "Create a matching PEOPLE record before assigned staffing scope can resolve.",
        }
    assignments = [
        assignment
        for assignment in person.leadership_assignments
        if assignment.active and assignment.unit and assignment.unit.active
    ]
    cards = [
        {
            "assignment": assignment,
            "unit": assignment.unit,
            "path": unit_path(assignment.unit),
            "label": _attendance_scope_label(assignment),
            "scope_key": _attendance_scope_key(assignment.unit),
        }
        for assignment in assignments
    ]
    return {
        "is_management": True,
        "person": person,
        "assignments": cards,
        "message": "" if cards else "No leadership assignment is linked to your PEOPLE record yet.",
    }


def _attendance_scope_label(assignment):
    if assignment.leadership_level == "work_area":
        return "Work Area Attendance"
    if assignment.leadership_level == "department":
        return "Department Attendance"
    if assignment.leadership_level == "operation":
        return "Operation Attendance"
    if assignment.leadership_level == "sort":
        return "Sort Attendance"
    return "Attendance"


def _attendance_scope_key(unit):
    return f"{unit.unit_type}_id"


def create_staffing_group(values):
    name = _validated_staffing_group_name(values.get("name"))
    group = StaffingGroup(
        name=name,
        active=_parse_bool(values.get("active"), default=True),
    )
    db.session.add(group)
    db.session.flush()
    _replace_staffing_group_memberships(
        group,
        _submitted_staffing_group_unit_ids(values),
    )
    return group


def update_staffing_group(group, values):
    if not group:
        raise ValueError("The selected Staffing Group was not found.")
    group.name = _validated_staffing_group_name(values.get("name"), group.id)
    group.active = _parse_bool(values.get("active"), default=False)
    _replace_staffing_group_memberships(
        group,
        _submitted_staffing_group_unit_ids(values),
    )
    db.session.flush()
    return group


def staffing_groups_context():
    all_hierarchy = _daily_attendance_hierarchy(include_inactive=True)
    active_hierarchy = _staffing_hierarchy_from_units(
        unit for unit in all_hierarchy["units"] if unit.active
    )
    definitions = _staffing_group_definitions(
        all_hierarchy,
        active_only=False,
    )
    operation = current_night_attendance_operation()
    ready = False
    message = ATTENDANCE_OPERATION_MISSING_MESSAGE
    staffing_sort = None
    roster_rows = []
    if operation:
        try:
            staffing_sort = _staffing_sort_for_operation(operation, active_hierarchy)
        except ValueError as error:
            message = str(error)
        else:
            assignments = _daily_attendance_assignments(
                staffing_sort,
                staffing_sort,
                active_hierarchy,
            )
            existing = _daily_attendance_records(
                [assignment.person_id for assignment in assignments],
                operation,
                staffing_sort,
            )
            roster_rows = _daily_attendance_rows(
                assignments,
                existing,
                active_hierarchy,
            )
            ready = True
            message = ""

    group_rows = _daily_staffing_group_rollups(
        definitions,
        roster_rows,
        active_hierarchy,
    )
    membership_options = [
        {
            "unit": unit,
            "label": _daily_attendance_unit_path(unit, all_hierarchy),
        }
        for unit in all_hierarchy["units"]
        if unit.unit_type in {"department", "operation"}
    ]
    return {
        "ready": ready,
        "message": message,
        "sort_date_operation": operation,
        "attendance_date": operation.sort_date if operation else None,
        "selected_sort": staffing_sort,
        "groups": group_rows,
        "membership_options": membership_options,
    }


def attendance_context(filters=None, user=None, include_staffing_groups=False):
    filters = dict(filters or {})
    gateway = _attendance_gateway()
    operation = current_night_attendance_operation(gateway)
    if not operation:
        return _empty_daily_attendance_context(
            filters,
            ATTENDANCE_OPERATION_MISSING_MESSAGE,
        )

    hierarchy = _daily_attendance_hierarchy()
    try:
        staffing_sort = _staffing_sort_for_operation(operation, hierarchy)
        selected_scope = _daily_attendance_scope(
            filters,
            hierarchy,
            staffing_sort,
            user=user,
        )
    except ValueError as error:
        return _empty_daily_attendance_context(
            filters,
            str(error),
            operation=operation,
            hierarchy=hierarchy,
        )

    assignment_scope = staffing_sort if include_staffing_groups else selected_scope
    loaded_assignments = _daily_attendance_assignments(
        assignment_scope,
        staffing_sort,
        hierarchy,
    )
    existing = _daily_attendance_records(
        [assignment.person_id for assignment in loaded_assignments],
        operation,
        staffing_sort,
    )
    loaded_rows = _daily_attendance_rows(loaded_assignments, existing, hierarchy)
    if include_staffing_groups and selected_scope.id != staffing_sort.id:
        selected_work_area_ids = _daily_attendance_work_area_ids(
            selected_scope,
            hierarchy,
        )
        rows = [
            row
            for row in loaded_rows
            if row["work_area"] and row["work_area"].id in selected_work_area_ids
        ]
    else:
        rows = loaded_rows

    staffing_groups = []
    if include_staffing_groups:
        group_definitions = _staffing_group_definitions(
            hierarchy,
            active_only=True,
        )
        staffing_groups = _daily_staffing_group_rollups(
            group_definitions,
            loaded_rows,
            hierarchy,
        )

    summary = _daily_attendance_summary(rows)
    filters = _daily_attendance_filters(filters, staffing_sort, selected_scope)
    return {
        "ready": True,
        "message": "",
        "sort_date_operation": operation,
        "attendance_date": operation.sort_date,
        "selected_sort": staffing_sort,
        "selected_scope": selected_scope,
        "rows": rows,
        "counts": summary["status_counts"],
        "summary": summary,
        "total_loaded": len(rows),
        "rollups": _daily_attendance_rollups(rows),
        "staffing_groups": staffing_groups,
        "scope_options": _daily_attendance_scope_options(hierarchy, staffing_sort),
        "status_choices": attendance_status_choices(),
        "filters": filters,
    }


def save_attendance(values, user):
    gateway = _attendance_gateway()
    current_operation = current_night_attendance_operation(gateway)
    operation = _submitted_attendance_operation(values.get("sort_date_operation_id"))
    if not current_operation or not operation or operation.id != current_operation.id:
        raise ValueError("The selected Night Sort is no longer current. Reload Attendance.")

    hierarchy = _daily_attendance_hierarchy()
    staffing_sort = _staffing_sort_for_operation(operation, hierarchy)
    selected_scope = _daily_attendance_scope(
        values,
        hierarchy,
        staffing_sort,
        strict=True,
    )
    assignments = _daily_attendance_assignments(
        selected_scope,
        staffing_sort,
        hierarchy,
    )
    assignments_by_person = {assignment.person_id: assignment for assignment in assignments}
    eligible_person_ids = set(assignments_by_person)
    submitted_person_ids = _submitted_attendance_person_ids(values)
    outside_scope_ids = submitted_person_ids - eligible_person_ids
    if outside_scope_ids:
        raise ValueError("Attendance includes an employee outside the selected scope.")

    bulk_status = str(values.get("bulk_status") or "").strip()
    person_ids = eligible_person_ids if bulk_status else submitted_person_ids
    if bulk_status and bulk_status != "here":
        raise ValueError("The attendance bulk action is invalid.")

    existing = {}
    if person_ids:
        records = StaffingDailyAttendance.query.filter(
            StaffingDailyAttendance.person_id.in_(person_ids),
            StaffingDailyAttendance.attendance_date == operation.sort_date,
            StaffingDailyAttendance.sort_unit_id == staffing_sort.id,
            or_(
                StaffingDailyAttendance.sort_date_operation_id == operation.id,
                StaffingDailyAttendance.sort_date_operation_id.is_(None),
            ),
        ).all()
        existing = {record.person_id: record for record in records}

    saved = 0
    user_id = getattr(user, "id", None)
    for person_id in sorted(person_ids):
        assignment = assignments_by_person[person_id]
        status_value = "here" if bulk_status else str(
            values.get(f"status_{person_id}") or ""
        ).strip()
        record = existing.get(person_id)
        if not status_value:
            if record:
                db.session.delete(record)
                saved += 1
            continue

        status = _normalize_choice(
            status_value,
            STAFFING_DAILY_ATTENDANCE_STATUSES,
            "attendance status",
        )
        note = _optional_text(values.get(f"note_{person_id}"))
        work_area = hierarchy["by_id"].get(assignment.work_area_unit_id)
        department, operation_unit, _row_sort = _daily_attendance_placement(
            work_area,
            hierarchy,
        )
        if not record:
            record = StaffingDailyAttendance(
                person=assignment.person,
                attendance_date=operation.sort_date,
                sort_unit_id=staffing_sort.id,
                sort_date_operation_id=operation.id,
                work_area_unit_id=work_area.id if work_area else None,
                department_unit_id=department.id if department else None,
                operation_unit_id=operation_unit.id if operation_unit else None,
                recorded_by_user_id=user_id,
            )
            db.session.add(record)
        else:
            # Existing placement snapshots are historical facts. Only fill values
            # absent from legacy rows; ordinary status edits never rewrite them.
            legacy_record = record.sort_date_operation_id is None
            if legacy_record:
                record.sort_date_operation_id = operation.id
                if record.work_area_unit_id is None and work_area:
                    record.work_area_unit_id = work_area.id
                if record.department_unit_id is None and department:
                    record.department_unit_id = department.id
                if record.operation_unit_id is None and operation_unit:
                    record.operation_unit_id = operation_unit.id
        record.status = status
        record.note = note
        record.updated_by_user_id = user_id
        saved += 1
    db.session.flush()
    return saved


def current_night_attendance_operation(gateway=None):
    """Resolve the existing current Night operation without generating one."""
    gateway = gateway or _attendance_gateway()
    if not gateway:
        return None
    return next(
        (
            operation
            for operation in current_operations_for_gateway(gateway)
            if _normalize_staffing_sort_name(operation.sort_name)
            == ATTENDANCE_OPERATION_SORT_NAME
        ),
        None,
    )


def _attendance_gateway():
    gateway_code = current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper()
    return Gateway.query.filter_by(code=gateway_code, is_active=True).first()


def _submitted_attendance_operation(operation_id):
    try:
        normalized_id = int(operation_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(SortDateOperation, normalized_id)


def _daily_attendance_hierarchy(include_inactive=False):
    query = StaffingUnit.query
    if not include_inactive:
        query = query.filter_by(active=True)
    units = (
        query
        .order_by(
            StaffingUnit.display_order,
            StaffingUnit.unit_type,
            StaffingUnit.name,
            StaffingUnit.id,
        )
        .all()
    )
    return _staffing_hierarchy_from_units(units)


def _staffing_hierarchy_from_units(units):
    units = list(units)
    by_id = {unit.id: unit for unit in units}
    children_by_parent = {}
    for unit in units:
        children_by_parent.setdefault(unit.parent_id, []).append(unit)
    return {
        "units": units,
        "by_id": by_id,
        "children_by_parent": children_by_parent,
    }


def _validated_staffing_group_name(value, current_group_id=None):
    name = _required_text(value, "Staffing Group name")
    if len(name) > 140:
        raise ValueError("Staffing Group name must be 140 characters or fewer.")
    duplicate_query = StaffingGroup.query.filter(
        func.lower(StaffingGroup.name) == name.lower()
    )
    if current_group_id is not None:
        duplicate_query = duplicate_query.filter(StaffingGroup.id != current_group_id)
    if duplicate_query.first():
        raise ValueError("A Staffing Group with that name already exists.")
    return name


def _submitted_staffing_group_unit_ids(values):
    raw_values = (
        values.getlist("staffing_unit_ids")
        if hasattr(values, "getlist")
        else values.get("staffing_unit_ids", [])
    )
    if isinstance(raw_values, (str, int)):
        raw_values = [raw_values]
    unit_ids = set()
    for raw_value in raw_values or []:
        try:
            unit_ids.add(int(raw_value))
        except (TypeError, ValueError):
            raise ValueError("Select valid Department or Operation members.")
    return unit_ids


def _replace_staffing_group_memberships(group, staffing_unit_ids):
    units = []
    if staffing_unit_ids:
        units = StaffingUnit.query.filter(StaffingUnit.id.in_(staffing_unit_ids)).all()
    units_by_id = {unit.id: unit for unit in units}
    if set(units_by_id) != set(staffing_unit_ids):
        raise ValueError("A selected Staffing Group unit is unavailable.")
    if any(unit.unit_type not in {"department", "operation"} for unit in units):
        raise ValueError("Staffing Groups may contain only Departments and Operations.")

    existing = StaffingGroupMembership.query.filter_by(group_id=group.id).all()
    existing_by_unit_id = {
        membership.staffing_unit_id: membership for membership in existing
    }
    for unit_id, membership in existing_by_unit_id.items():
        if unit_id not in staffing_unit_ids:
            db.session.delete(membership)
    for unit_id in sorted(staffing_unit_ids - set(existing_by_unit_id)):
        db.session.add(
            StaffingGroupMembership(
                group_id=group.id,
                staffing_unit_id=unit_id,
            )
        )
    db.session.flush()


def _staffing_group_definitions(hierarchy, active_only):
    query = (
        db.session.query(StaffingGroup, StaffingGroupMembership)
        .outerjoin(
            StaffingGroupMembership,
            StaffingGroupMembership.group_id == StaffingGroup.id,
        )
    )
    if active_only:
        query = query.filter(StaffingGroup.active.is_(True))
    records = query.order_by(
        StaffingGroup.active.desc(),
        func.lower(StaffingGroup.name),
        StaffingGroup.id,
        StaffingGroupMembership.id,
    ).all()

    definitions_by_id = {}
    for group, membership in records:
        definition = definitions_by_id.setdefault(
            group.id,
            {
                "group": group,
                "memberships": [],
                "member_unit_ids": set(),
            },
        )
        if not membership:
            continue
        unit = hierarchy["by_id"].get(membership.staffing_unit_id)
        if not unit:
            continue
        definition["member_unit_ids"].add(unit.id)
        definition["memberships"].append(
            {
                "membership": membership,
                "unit": unit,
                "label": _daily_attendance_unit_path(unit, hierarchy),
            }
        )
    return list(definitions_by_id.values())


def _daily_staffing_group_rollups(definitions, roster_rows, hierarchy):
    rows_by_work_area_id = {}
    for row in roster_rows:
        work_area = row.get("work_area")
        if work_area:
            rows_by_work_area_id.setdefault(work_area.id, []).append(row)

    rows_by_member_unit_id = {}
    member_unit_ids = {
        unit_id
        for definition in definitions
        for unit_id in definition["member_unit_ids"]
    }
    for unit_id in member_unit_ids:
        unit = hierarchy["by_id"].get(unit_id)
        if not unit or unit.unit_type not in {"department", "operation"}:
            rows_by_member_unit_id[unit_id] = []
            continue
        rows_by_member_unit_id[unit_id] = [
            row
            for work_area_id in _daily_attendance_work_area_ids(unit, hierarchy)
            for row in rows_by_work_area_id.get(work_area_id, [])
        ]

    rollups = []
    for definition in definitions:
        rows_by_person_id = {}
        for unit_id in definition["member_unit_ids"]:
            for row in rows_by_member_unit_id.get(unit_id, []):
                rows_by_person_id[row["person"].id] = row
        summary = _daily_attendance_summary(list(rows_by_person_id.values()))
        rollups.append({**definition, **summary})
    return rollups


def _daily_attendance_records(person_ids, operation, staffing_sort):
    if not person_ids:
        return {}
    records = StaffingDailyAttendance.query.filter(
        StaffingDailyAttendance.person_id.in_(set(person_ids)),
        StaffingDailyAttendance.attendance_date == operation.sort_date,
        StaffingDailyAttendance.sort_unit_id == staffing_sort.id,
        or_(
            StaffingDailyAttendance.sort_date_operation_id == operation.id,
            StaffingDailyAttendance.sort_date_operation_id.is_(None),
        ),
    ).all()
    return {record.person_id: record for record in records}


def _daily_attendance_rows(assignments, existing, hierarchy):
    rows = []
    for assignment in assignments:
        work_area = hierarchy["by_id"].get(assignment.work_area_unit_id)
        department, operation_unit, row_sort = _daily_attendance_placement(
            work_area,
            hierarchy,
        )
        record = existing.get(assignment.person_id)
        rows.append(
            {
                "person": assignment.person,
                "work_area": work_area,
                "department": department,
                "operation": operation_unit,
                "sort": row_sort,
                "attendance": record,
                "status": record.status if record else "",
                "note": (record.note or "") if record else "",
            }
        )
    return rows


def _staffing_sort_for_operation(operation, hierarchy):
    operation_name = _normalize_staffing_sort_name(operation.sort_name)
    matches = [
        unit
        for unit in hierarchy["units"]
        if unit.unit_type == "sort"
        and _normalize_staffing_sort_name(unit.name) == operation_name
    ]
    if not matches:
        raise ValueError(
            f'No active NeoStaffing Sort matches "{operation.sort_name}".'
        )
    if len(matches) > 1:
        raise ValueError(
            f'Multiple active NeoStaffing Sorts match "{operation.sort_name}".'
        )
    return matches[0]


def _normalize_staffing_sort_name(value):
    normalized = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if normalized.endswith(" sort"):
        normalized = normalized[:-5].strip()
    return normalized


def _daily_attendance_scope(
    filters,
    hierarchy,
    staffing_sort,
    *,
    user=None,
    strict=False,
):
    selected = None
    selected_key = None
    for key, unit_type in (
        ("work_area_id", "work_area"),
        ("department_id", "department"),
        ("operation_id", "operation"),
        ("sort_id", "sort"),
    ):
        raw_value = str(filters.get(key) or "").strip()
        if not raw_value:
            continue
        selected_key = key
        try:
            unit_id = int(raw_value)
        except (TypeError, ValueError):
            unit_id = None
        candidate = hierarchy["by_id"].get(unit_id)
        if not candidate or candidate.unit_type != unit_type:
            raise ValueError("The selected attendance area is unavailable.")
        selected = candidate
        break

    if selected is None and user is not None:
        selected = _default_daily_attendance_scope(user, hierarchy, staffing_sort)
    selected = selected or staffing_sort
    if not _unit_belongs_to_staffing_sort(selected, staffing_sort, hierarchy):
        message = "The selected attendance area does not belong to the current Night Sort."
        if strict or selected_key:
            raise ValueError(message)
        selected = staffing_sort
    return selected


def _default_daily_attendance_scope(user, hierarchy, staffing_sort):
    context = management_attendance_context_for_user(user)
    for card in context.get("assignments") or []:
        unit = hierarchy["by_id"].get(card["unit"].id)
        if unit and _unit_belongs_to_staffing_sort(unit, staffing_sort, hierarchy):
            return unit
    return None


def _unit_belongs_to_staffing_sort(unit, staffing_sort, hierarchy):
    current = unit
    visited = set()
    while current and current.id not in visited:
        if current.id == staffing_sort.id:
            return True
        visited.add(current.id)
        current = hierarchy["by_id"].get(current.parent_id)
    return False


def _daily_attendance_work_area_ids(scope, hierarchy):
    if scope.unit_type == "work_area":
        return {scope.id}
    work_area_ids = set()
    pending = [scope.id]
    while pending:
        parent_id = pending.pop()
        for child in hierarchy["children_by_parent"].get(parent_id, []):
            if child.unit_type == "work_area":
                work_area_ids.add(child.id)
            else:
                pending.append(child.id)
    return work_area_ids


def _daily_attendance_assignments(selected_scope, staffing_sort, hierarchy):
    work_area_ids = _daily_attendance_work_area_ids(
        selected_scope or staffing_sort,
        hierarchy,
    )
    if not work_area_ids:
        return []
    return (
        StaffingWorkAssignment.query.options(
            joinedload(StaffingWorkAssignment.person),
            joinedload(StaffingWorkAssignment.work_area),
        )
        .join(StaffingPerson)
        .filter(
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids),
            StaffingPerson.active.is_(True),
            StaffingPerson.classification.in_(NON_MANAGEMENT_CLASSIFICATIONS),
        )
        .order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id)
        .all()
    )


def _daily_attendance_placement(work_area, hierarchy):
    department = None
    operation = None
    staffing_sort = None
    current = hierarchy["by_id"].get(work_area.parent_id) if work_area else None
    if current and current.unit_type == "department":
        department = current
        current = hierarchy["by_id"].get(current.parent_id)
    if current and current.unit_type == "operation":
        operation = current
        current = hierarchy["by_id"].get(current.parent_id)
    if current and current.unit_type == "sort":
        staffing_sort = current
    return department, operation, staffing_sort


def _submitted_attendance_person_ids(values):
    person_ids = set()
    for key in values.keys():
        key = str(key)
        if not key.startswith("status_"):
            continue
        try:
            person_ids.add(int(key.split("_", 1)[1]))
        except (TypeError, ValueError):
            continue
    return person_ids


def _daily_attendance_summary(rows):
    status_counts = {}
    for row in rows:
        if row["status"]:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    here = status_counts.get("here", 0)
    absent = sum(
        count for status, count in status_counts.items() if status != "here"
    )
    unmarked = len(rows) - here - absent
    return {
        "total_roster": len(rows),
        "here": here,
        "absent": absent,
        "unmarked": unmarked,
        "status_counts": status_counts,
        "absence_breakdown": [
            {
                "status": status,
                "label": ATTENDANCE_STATUS_LABELS[status],
                "count": status_counts[status],
            }
            for status in STAFFING_DAILY_ATTENDANCE_STATUSES
            if status != "here" and status_counts.get(status, 0)
        ],
    }


def _daily_attendance_rollups(rows):
    rollups = {"work_area": {}, "department": {}, "operation": {}}
    for row in rows:
        for key in rollups:
            unit = row.get(key)
            if not unit:
                continue
            metrics = rollups[key].setdefault(
                unit.id,
                {
                    "unit": unit,
                    "total_roster": 0,
                    "here": 0,
                    "absent": 0,
                    "unmarked": 0,
                },
            )
            metrics["total_roster"] += 1
            if row["status"] == "here":
                metrics["here"] += 1
            elif row["status"]:
                metrics["absent"] += 1
            else:
                metrics["unmarked"] += 1
    return {
        key: sorted(
            values.values(),
            key=lambda item: (
                item["unit"].display_order,
                item["unit"].name.lower(),
                item["unit"].id,
            ),
        )
        for key, values in rollups.items()
    }


def _daily_attendance_scope_options(hierarchy, staffing_sort):
    options = {"sorts": [], "operations": [], "departments": [], "work_areas": []}
    option_key_by_type = {
        "sort": "sorts",
        "operation": "operations",
        "department": "departments",
        "work_area": "work_areas",
    }
    for unit in hierarchy["units"]:
        if not _unit_belongs_to_staffing_sort(unit, staffing_sort, hierarchy):
            continue
        options[option_key_by_type[unit.unit_type]].append(
            {"id": unit.id, "label": _daily_attendance_unit_path(unit, hierarchy)}
        )
    return options


def _daily_attendance_unit_path(unit, hierarchy):
    names = []
    current = unit
    visited = set()
    while current and current.id not in visited:
        visited.add(current.id)
        names.append(current.name)
        current = hierarchy["by_id"].get(current.parent_id)
    return " / ".join(reversed(names))


def _daily_attendance_filters(filters, staffing_sort, selected_scope):
    selected = {
        "sort_id": "",
        "operation_id": "",
        "department_id": "",
        "work_area_id": "",
    }
    key = _attendance_scope_key(selected_scope)
    selected[key] = str(selected_scope.id)
    selected["sort_id"] = str(staffing_sort.id)
    return selected


def _empty_daily_attendance_context(filters, message, operation=None, hierarchy=None):
    empty_options = {"sorts": [], "operations": [], "departments": [], "work_areas": []}
    return {
        "ready": False,
        "message": message,
        "sort_date_operation": operation,
        "attendance_date": operation.sort_date if operation else None,
        "selected_sort": None,
        "selected_scope": None,
        "rows": [],
        "counts": {},
        "summary": {
            "total_roster": 0,
            "here": 0,
            "absent": 0,
            "unmarked": 0,
            "status_counts": {},
            "absence_breakdown": [],
        },
        "total_loaded": 0,
        "rollups": {"work_area": [], "department": [], "operation": []},
        "staffing_groups": [],
        "scope_options": empty_options,
        "status_choices": attendance_status_choices(),
        "filters": {
            "sort_id": str(filters.get("sort_id") or ""),
            "operation_id": str(filters.get("operation_id") or ""),
            "department_id": str(filters.get("department_id") or ""),
            "work_area_id": str(filters.get("work_area_id") or ""),
        },
    }


def _attendance_assignments_for_scope(selected_scope, selected_sort):
    work_area_ids = set()
    if selected_scope:
        work_area_ids = work_area_ids_under(selected_scope)
    elif selected_sort:
        work_area_ids = work_area_ids_under(selected_sort)
    if (selected_scope or selected_sort) and not work_area_ids:
        return []
    query = (
        StaffingWorkAssignment.query.join(StaffingPerson)
        .join(StaffingUnit)
        .filter(
            StaffingWorkAssignment.active.is_(True),
            StaffingPerson.active.is_(True),
            StaffingPerson.classification.in_(NON_MANAGEMENT_CLASSIFICATIONS),
        )
    )
    if work_area_ids:
        query = query.filter(StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids))
    return query.order_by(StaffingPerson.last_name, StaffingPerson.first_name).all()


def _attendance_work_area_for_person(person):
    assignment = person.work_assignment if person.work_assignment and person.work_assignment.active else None
    return assignment.work_area if assignment else None


def _resolve_attendance_scope(filters):
    for key, unit_type in (
        ("work_area_id", "work_area"),
        ("department_id", "department"),
        ("operation_id", "operation"),
        ("sort_id", "sort"),
    ):
        unit = _resolve_optional_unit(filters.get(key), unit_type)
        if unit:
            return unit
    return None


def _resolve_attendance_sort(filters, selected_scope):
    explicit_sort = _resolve_optional_unit(filters.get("sort_id"), "sort")
    if explicit_sort:
        return explicit_sort
    current = selected_scope
    while current:
        if current.unit_type == "sort":
            return current
        current = current.parent
    sorts = units_by_type("sort")
    return sorts[0] if len(sorts) == 1 else None


def reports_context(filters=None, user=None):
    filters = filters or {}
    filters = _with_default_management_scope(filters, user)
    report_type = str(filters.get("report_type") or "staffing").strip().lower()
    if report_type not in {"staffing", "seniority", "attendance"}:
        report_type = "staffing"
    staffing = people_context(
        {
            "sort_id": filters.get("sort_id", ""),
            "operation_id": filters.get("operation_id", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "classification": filters.get("classification", ""),
            "employee_status": filters.get("employee_status", ""),
            "active": filters.get("active", "active"),
            "search": filters.get("search", ""),
            "assignment_status": filters.get("assignment_status", ""),
            "per_page": "all",
        }
    )
    seniority = seniority_context(
        {
            "sort_id": filters.get("sort_id", ""),
            "operation_id": filters.get("operation_id", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "classification": filters.get("classification", ""),
            "employee_status": filters.get("employee_status", ""),
            "active": filters.get("active", "active"),
        }
    )
    attendance_date = _parse_optional_date(filters.get("attendance_date"))
    attendance_query = StaffingDailyAttendance.query.join(StaffingPerson)
    if attendance_date:
        attendance_query = attendance_query.filter(
            StaffingDailyAttendance.attendance_date == attendance_date
        )
    attendance_status = str(filters.get("attendance_status") or "").strip()
    if attendance_status in STAFFING_DAILY_ATTENDANCE_STATUSES:
        attendance_query = attendance_query.filter(
            StaffingDailyAttendance.status == attendance_status
        )
    selected_scope = _resolve_attendance_scope(filters)
    if selected_scope:
        work_area_ids = work_area_ids_under(selected_scope)
        attendance_query = attendance_query.filter(
            StaffingDailyAttendance.work_area_unit_id.in_(work_area_ids or {-1})
        )
    attendance_rows = attendance_query.order_by(
        StaffingDailyAttendance.attendance_date.desc(),
        StaffingPerson.last_name,
        StaffingPerson.first_name,
    ).all()
    attendance_counts = {}
    for record in attendance_rows:
        attendance_counts[record.status] = attendance_counts.get(record.status, 0) + 1
    return {
        "report_type": report_type,
        "staffing": staffing,
        "seniority": seniority,
        "attendance_rows": attendance_rows,
        "attendance_counts": attendance_counts,
        "staffing_classification_counts": _people_count_by(staffing["all_rows"], "classification"),
        "staffing_employee_status_counts": _people_count_by(staffing["all_rows"], "employee_status"),
        "attendance_status_choices": attendance_status_choices(),
        "classification_choices": classification_choices(),
        "employee_status_choices": employee_status_choices(),
        "filters": {
            "report_type": report_type,
            "sort_id": filters.get("sort_id", ""),
            "operation_id": filters.get("operation_id", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "classification": filters.get("classification", ""),
            "employee_status": filters.get("employee_status", ""),
            "assignment_status": filters.get("assignment_status", ""),
            "attendance_date": filters.get("attendance_date", ""),
            "attendance_status": filters.get("attendance_status", ""),
        },
    }


def linked_user_for_person(person):
    if not person or not person.employee_id:
        return None
    return User.query.filter(
        func.lower(User.employee_id) == person.employee_id.lower()
    ).first()


def people_query(search=None, classification=None, active=None, employee_status=None):
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
    if employee_status:
        query = query.filter_by(employee_status=employee_status)
    if active in {"active", "inactive"}:
        query = query.filter_by(active=(active == "active"))
    return query.order_by(StaffingPerson.seniority_date, StaffingPerson.last_name, StaffingPerson.first_name)


def _people_rows():
    active_work_assignments = {
        assignment.person_id: assignment
        for assignment in (
            StaffingWorkAssignment.query.filter_by(active=True)
            .join(StaffingUnit)
            .all()
        )
    }
    active_leadership = {}
    for assignment in (
        StaffingLeadershipAssignment.query.filter_by(active=True)
        .join(StaffingUnit)
        .all()
    ):
        active_leadership.setdefault(assignment.person_id, []).append(assignment)

    rows = []
    for person in StaffingPerson.query.order_by(StaffingPerson.last_name, StaffingPerson.first_name).all():
        work_assignment = active_work_assignments.get(person.id)
        work_area = work_assignment.work_area if work_assignment else None
        department, operation, sort = parent_chain_for_work_area(work_area)
        leadership_assignments = sorted(
            active_leadership.get(person.id, []),
            key=lambda row: (row.unit.unit_type, unit_path(row.unit), row.id),
        )
        rows.append(
            {
                "person": person,
                "work_assignment": work_assignment,
                "work_area": work_area,
                "department": department,
                "operation": operation,
                "sort": sort,
                "leadership_assignments": leadership_assignments,
                "leadership_labels": _leadership_labels(person, leadership_assignments),
                "seniority_operation": _people_seniority_operation(work_area, leadership_assignments),
            }
        )
    return rows


def _filter_people_rows(rows, filters):
    active = filters.get("active", "active")
    classification = str(filters.get("classification") or "").strip()
    employee_status = str(filters.get("employee_status") or "").strip()
    search = str(filters.get("search") or "").strip().lower()
    leadership_only = _parse_bool(filters.get("leadership_only"), default=False)
    assignment_status = str(filters.get("assignment_status") or "").strip()
    selected_scope = (
        filters.get("selected_work_area")
        or filters.get("selected_department")
        or filters.get("selected_operation")
        or filters.get("selected_sort")
    )
    allowed_unit_ids = unit_ids_under(selected_scope) if selected_scope else None

    filtered = []
    for row in rows:
        person = row["person"]
        if active in {"active", "inactive"} and person.active != (active == "active"):
            continue
        if classification in STAFFING_CLASSIFICATIONS and person.classification != classification:
            continue
        if employee_status in STAFFING_EMPLOYEE_STATUSES and person.employee_status != employee_status:
            continue
        if leadership_only and not row["leadership_assignments"]:
            continue
        has_work_assignment = bool(row["work_assignment"] and row["work_assignment"].active)
        if assignment_status == "assigned" and not has_work_assignment:
            continue
        if assignment_status == "unassigned" and (
            has_work_assignment or person.classification not in NON_MANAGEMENT_CLASSIFICATIONS
        ):
            continue
        if search:
            searchable = " ".join(
                [
                    person.employee_id or "",
                    person.first_name or "",
                    person.last_name or "",
                    person.full_name or "",
                ]
            ).lower()
            if search not in searchable:
                continue
        if allowed_unit_ids is not None and not _people_row_matches_scope(row, allowed_unit_ids):
            continue
        filtered.append(row)
    return filtered


def _people_row_matches_scope(row, allowed_unit_ids):
    scoped_ids = set()
    for unit in (row.get("work_area"), row.get("department"), row.get("operation"), row.get("sort")):
        if unit:
            scoped_ids.add(unit.id)
    scoped_ids.update(assignment.unit_id for assignment in row.get("leadership_assignments", []))
    return bool(scoped_ids & allowed_unit_ids)


def _leadership_labels(person, assignments):
    labels = []
    for assignment in assignments:
        if person.classification == "part_time_supervisor" and assignment.unit.unit_type == "work_area":
            label = "Work Area Supervisor"
        elif person.classification == "full_time_supervisor" and assignment.unit.unit_type == "department":
            label = "Department Supervisor"
        elif person.classification == "manager" and assignment.unit.unit_type == "operation":
            label = "Manager"
        elif person.classification == "division_manager" and assignment.unit.unit_type == "sort":
            label = "Division Manager"
        elif person.classification == "full_time_specialist":
            label = "Specialist Assignment"
        else:
            label = LEADERSHIP_LEVEL_LABELS.get(assignment.leadership_level, "Leadership")
        labels.append(
            {
                "label": label,
                "unit": assignment.unit,
                "path": unit_path(assignment.unit),
            }
        )
    return labels


def _people_seniority_operation(work_area, leadership_assignments):
    _department, operation, _sort = parent_chain_for_work_area(work_area)
    if operation:
        return operation
    for assignment in leadership_assignments:
        unit = assignment.unit
        if unit.unit_type == "operation":
            return unit
        if unit.unit_type == "department" and unit.parent:
            return unit.parent
        if unit.unit_type == "work_area":
            _department, operation, _sort = parent_chain_for_work_area(unit)
            if operation:
                return operation
    return None


def _resolve_people_detail(person_id, rows):
    if not person_id:
        return None
    try:
        selected_id = int(person_id)
    except (TypeError, ValueError):
        return None
    for row in rows:
        if row["person"].id == selected_id:
            return row
    return None


def _pagination_from_filters(filters):
    page = _parse_positive_int(filters.get("page"), default=1)
    per_page_value = str(filters.get("per_page") or "").strip().lower()
    if per_page_value == "all":
        return page, None
    per_page = _parse_positive_int(per_page_value, default=PEOPLE_DEFAULT_PAGE_SIZE)
    per_page = min(per_page, PEOPLE_MAX_PAGE_SIZE)
    return page, per_page


def _parse_positive_int(value, default=1):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _with_default_management_scope(filters, user):
    if not user or _has_explicit_scope(filters):
        return filters
    context = management_attendance_context_for_user(user)
    assignments = context.get("assignments") or []
    if not assignments:
        return filters
    first = assignments[0]
    scoped = dict(filters)
    scoped[first["scope_key"]] = str(first["unit"].id)
    return scoped


def _has_explicit_scope(filters):
    return any(str(filters.get(key) or "").strip() for key in ("work_area_id", "department_id", "operation_id", "sort_id"))


def _org_chart_unit_meta():
    assigned_counts = _board_assigned_counts()
    active_leadership = {}
    for assignment in StaffingLeadershipAssignment.query.filter_by(active=True).all():
        if assignment.unit_id:
            active_leadership.setdefault(assignment.unit_id, []).append(assignment)
    meta = {}
    for unit in StaffingUnit.query.all():
        child_count = len([child for child in unit.children if child.active])
        work_area_ids = work_area_ids_under(unit)
        assigned_count = sum(int(assigned_counts.get(work_area_id, 0) or 0) for work_area_id in work_area_ids)
        leadership = sorted(
            active_leadership.get(unit.id, []),
            key=lambda row: (row.person.last_name.lower(), row.person.first_name.lower(), row.id),
        )
        meta[unit.id] = {
            "child_count": child_count,
            "assigned_count": assigned_count,
            "leadership": leadership,
            "leadership_names": [assignment.person.full_name for assignment in leadership],
            "required_headcount": unit.required_headcount,
        }
    return meta


def _people_count_by(rows, field):
    counts = {}
    for row in rows:
        person = row["person"]
        key = getattr(person, field, None)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _unit_is_descendant(candidate_parent, unit):
    current = candidate_parent
    while current:
        if current.id == unit.id:
            return True
        current = current.parent
    return False


def _seniority_work_assignment_rows(operation, allowed_work_area_ids, filters):
    query = (
        StaffingWorkAssignment.query.join(StaffingPerson)
        .join(StaffingUnit, StaffingWorkAssignment.work_area)
        .filter(
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(allowed_work_area_ids or {-1}),
        )
    )
    query = _apply_seniority_person_filters(query, filters)
    department = _resolve_optional_unit(filters.get("department_id"), "department")
    if department:
        query = query.filter(StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids_under(department) or {-1}))
    work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if work_area:
        query = query.filter(StaffingWorkAssignment.work_area_unit_id == work_area.id)

    rows = []
    for assignment in query.all():
        work_area = assignment.work_area
        rows.append(
            {
                "person": assignment.person,
                "work_area": work_area,
                "scope": work_area,
                "scope_name": work_area.name,
                "scope_path": unit_path(work_area),
                "source": "work_assignment",
            }
        )
    return rows


def _seniority_management_rows(operation, filters):
    allowed_unit_ids = unit_ids_under(operation)
    query = (
        StaffingLeadershipAssignment.query.join(StaffingPerson)
        .join(StaffingUnit)
        .filter(
            StaffingLeadershipAssignment.active.is_(True),
            StaffingLeadershipAssignment.unit_id.in_(allowed_unit_ids or {-1}),
        )
    )
    query = _apply_seniority_person_filters(query, filters, management_only=True)
    department = _resolve_optional_unit(filters.get("department_id"), "department")
    if department:
        query = query.filter(StaffingLeadershipAssignment.unit_id.in_(unit_ids_under(department) or {-1}))
    work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if work_area:
        query = query.filter(StaffingLeadershipAssignment.unit_id == work_area.id)

    rows = []
    seen = set()
    for assignment in query.all():
        key = (assignment.person_id, assignment.unit_id, assignment.leadership_level)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "person": assignment.person,
                "work_area": assignment.unit if assignment.unit.unit_type == "work_area" else None,
                "scope": assignment.unit,
                "scope_name": assignment.unit.name,
                "scope_path": unit_path(assignment.unit),
                "source": "leadership_assignment",
            }
        )
    return rows


def _apply_seniority_person_filters(query, filters, management_only=False):
    active = filters.get("active", "active")
    if active in {"active", "inactive"}:
        query = query.filter(StaffingPerson.active.is_(active == "active"))
    classification = str(filters.get("classification") or "").strip()
    if classification in STAFFING_CLASSIFICATIONS:
        query = query.filter(StaffingPerson.classification == classification)
    elif management_only:
        query = query.filter(StaffingPerson.classification.in_(MANAGEMENT_CLASSIFICATIONS))
    else:
        query = query.filter(StaffingPerson.classification.in_(NON_MANAGEMENT_CLASSIFICATIONS))
    employee_status = str(filters.get("employee_status") or "").strip()
    if employee_status in STAFFING_EMPLOYEE_STATUSES:
        query = query.filter(StaffingPerson.employee_status == employee_status)

    search = str(filters.get("search") or "").strip()
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            db.or_(
                StaffingPerson.employee_id.ilike(pattern),
                StaffingPerson.first_name.ilike(pattern),
                StaffingPerson.last_name.ilike(pattern),
            )
        )
    return query


def _resolve_selected_operation(operation_id, operations, all_operations):
    selected_operation = _resolve_optional_unit(operation_id, "operation")
    if selected_operation and selected_operation in operations:
        return selected_operation
    if not operation_id and len(operations) == 1:
        return operations[0]
    if not operation_id and not operations and len(all_operations) == 1:
        return all_operations[0]
    return None


def _resolve_optional_unit(unit_id, unit_type):
    if not unit_id:
        return None
    try:
        unit = db.session.get(StaffingUnit, int(unit_id))
    except (TypeError, ValueError):
        return None
    if not unit or unit.unit_type != unit_type:
        return None
    return unit


def _departments_under(operation):
    if not operation:
        return []
    return sorted(
        [child for child in operation.children if child.unit_type == "department"],
        key=lambda row: (row.display_order, row.name.lower(), row.id),
    )


def _work_areas_under(operation):
    if not operation:
        return []
    return (
        StaffingUnit.query.filter(
            StaffingUnit.unit_type == "work_area",
            StaffingUnit.id.in_(work_area_ids_under(operation) or {-1}),
        )
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def _direct_child_work_areas(parent):
    if not parent:
        return []
    return (
        StaffingUnit.query.filter_by(unit_type="work_area", parent_id=parent.id, active=True)
        .order_by(StaffingUnit.display_order, StaffingUnit.name)
        .all()
    )


def _leadership_assignments_for_unit(unit):
    if not unit:
        return []
    return (
        StaffingLeadershipAssignment.query.filter_by(unit_id=unit.id, active=True)
        .join(StaffingPerson)
        .order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.employee_id)
        .all()
    )


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
    if not linked_user_for_person(person):
        raise ValueError("Management assignments require a matching NeoApps user account.")
    if leadership_level != unit.unit_type:
        raise ValueError("Leadership level must match the selected unit scope.")
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
    if isinstance(expected_parent_type, tuple):
        if parent.unit_type not in expected_parent_type:
            allowed = " or ".join(UNIT_TYPE_LABELS[value] for value in expected_parent_type)
            raise ValueError(f"{UNIT_TYPE_LABELS[unit_type]} parent must be a {allowed}.")
        return parent
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


def _normalize_person_name(value, label):
    text = _required_text(value, label)
    return text[:1].upper() + text[1:].lower()


def _normalize_phone_number(value):
    text = _optional_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) != 10:
        raise ValueError("Phone number must contain exactly 10 digits.")
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


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


def _parse_optional_date(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("Effective date must be a valid date.") from exc


def _parse_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Display order must be a number.") from exc


def _parse_optional_int(value, minimum=None, label="Value"):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{label} cannot be negative.")
    return parsed


def _normalized_person_ids(values):
    normalized = []
    seen = set()
    for value in values or []:
        try:
            person_id = int(value)
        except (TypeError, ValueError):
            continue
        if person_id <= 0 or person_id in seen:
            continue
        normalized.append(person_id)
        seen.add(person_id)
    return normalized


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes", "active"}
