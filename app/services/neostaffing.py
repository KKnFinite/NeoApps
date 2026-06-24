from datetime import date, datetime

from sqlalchemy import func

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
    "work_area": "Work Area",
    "department": "Department",
    "operation": "Operation",
    "sort": "Sort",
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
    "work_area": "department",
}
STAFFING_NEAR_TARGET_THRESHOLD = 0.8


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
        required_headcount = _parse_optional_int(
            values.get("required_headcount"),
            minimum=0,
            label="Planned staffing",
        )

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


def create_leadership_assignment(person, unit, leadership_level=None):
    level = leadership_level or default_leadership_level_for(person, unit)
    _validate_leadership_assignment(person, unit, level)

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
    if selected_work_area and selected_department is None:
        selected_department = selected_work_area.parent
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
    if selected_work_area and selected_department is None:
        selected_department = selected_work_area.parent
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
    department = work_area.parent if work_area else None
    operation = department.parent if department and department.parent else None
    sort = operation.parent if operation and operation.parent else None
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
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "search": filters.get("search", ""),
            "active": filters.get("active", "active") or "active",
            "include_management": "1" if include_management else "",
        },
        "hierarchy": staffing_hierarchy_tree(),
    }


def people_context(filters=None):
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
    selected_department = _resolve_optional_unit(filters.get("department_id"), "department")
    selected_work_area = _resolve_optional_unit(filters.get("work_area_id"), "work_area")
    if selected_work_area and selected_department is None:
        selected_department = selected_work_area.parent
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
    rows.sort(
        key=lambda row: (
            row["person"].last_name.lower(),
            row["person"].first_name.lower(),
            str(row["person"].employee_id or ""),
            row["person"].id,
        )
    )

    selected_person = _resolve_people_detail(filters.get("person_id"), rows)
    if selected_person is None and rows:
        selected_person = rows[0]

    counts = {
        "total": len(rows),
        "active": sum(1 for row in rows if row["person"].active),
        "inactive": sum(1 for row in rows if not row["person"].active),
        "supervisors": sum(1 for row in rows if row["person"].classification in SUPERVISOR_CLASSIFICATIONS),
        "managers": sum(1 for row in rows if row["person"].classification in MANAGER_CLASSIFICATIONS),
    }

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
        "selected_person": selected_person,
        "leadership_only": _parse_bool(filters.get("leadership_only"), default=False),
        "filters": {
            "sort_id": str(selected_sort.id) if selected_sort else "",
            "operation_id": str(selected_operation.id) if selected_operation else "",
            "classification": filters.get("classification", ""),
            "department_id": filters.get("department_id", ""),
            "work_area_id": filters.get("work_area_id", ""),
            "search": filters.get("search", ""),
            "active": filters.get("active", "active") or "active",
            "leadership_only": "1" if _parse_bool(filters.get("leadership_only"), default=False) else "",
            "person_id": str(selected_person["person"].id) if selected_person else "",
        },
        "hierarchy": staffing_hierarchy_tree(),
    }


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
        department = work_area.parent if work_area and work_area.parent else None
        operation = department.parent if department and department.parent else None
        sort = operation.parent if operation and operation.parent else None
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
    search = str(filters.get("search") or "").strip().lower()
    leadership_only = _parse_bool(filters.get("leadership_only"), default=False)
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
        if leadership_only and not row["leadership_assignments"]:
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
    if work_area and work_area.parent and work_area.parent.parent:
        return work_area.parent.parent
    for assignment in leadership_assignments:
        unit = assignment.unit
        if unit.unit_type == "operation":
            return unit
        if unit.unit_type == "department" and unit.parent:
            return unit.parent
        if unit.unit_type == "work_area" and unit.parent and unit.parent.parent:
            return unit.parent.parent
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


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "on", "yes", "active"}
