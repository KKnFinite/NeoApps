from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import calendar as calendar_module
import math

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingVacationManagementCapacity,
    StaffingVacationManagementWeekOverride,
    StaffingVacationUnionCalendar,
    StaffingVacationUnionCalendarScope,
    StaffingWorkAssignment,
)
from app.models.user import ROLE_LEVELS
from app.services.access_control import get_user_app_role


VACATION_YEAR_MIN = 2000
VACATION_YEAR_MAX = 2200
VACATION_UNION_SCOPE_TYPES = frozenset({"operation", "department", "work_area"})
VACATION_MANAGEMENT_AREA_TYPES = frozenset({"sort", "operation", "department"})
VACATION_PT_CLASSIFICATIONS = frozenset({"part_time"})
# Non-domiciled FT Combo is intentionally outside the vacation capacity pool.
VACATION_FT_CLASSIFICATIONS = frozenset(
    {"full_time_combo", "domiciled_full_time_combo"}
)
VACATION_UNION_CLASSIFICATIONS = frozenset(
    VACATION_PT_CLASSIFICATIONS | VACATION_FT_CLASSIFICATIONS
)
VACATION_MANAGEMENT_CLASSIFICATIONS = frozenset(
    {
        "part_time_supervisor",
        "full_time_supervisor",
        "full_time_specialist",
        "manager",
        "division_manager",
    }
)


@dataclass(frozen=True)
class VacationWeek:
    vacation_year: int
    start_date: date
    end_date: date

    @property
    def week_ending(self):
        return self.end_date


@dataclass(frozen=True)
class UnionVacationCapacity:
    payroll_count: int
    percentage: int
    capacity: int
    seasonal: bool


@dataclass(frozen=True)
class VacationActor:
    app_role: str
    person: StaffingPerson | None
    is_grandmaster: bool
    normal_scope_ids: frozenset[int]
    sideways_scope_ids: frozenset[int]
    management_capacity_ids: frozenset[int]


def default_vacation_year(today=None):
    today = today or date.today()
    return today.year + 1 if today >= date(today.year, 11, 1) else today.year


def vacation_selection_opens_on(vacation_year):
    year = normalize_vacation_year(vacation_year)
    return date(year - 1, 11, 1)


def vacation_year_weeks(vacation_year):
    """Return every Sunday-Saturday week identified by a Saturday in the year."""
    year = normalize_vacation_year(vacation_year)
    first = date(year, 1, 1)
    first_saturday = first + timedelta(days=(5 - first.weekday()) % 7)
    rows = []
    week_ending = first_saturday
    while week_ending.year == year:
        rows.append(
            VacationWeek(
                vacation_year=year,
                start_date=week_ending - timedelta(days=6),
                end_date=week_ending,
            )
        )
        week_ending += timedelta(days=7)
    return tuple(rows)


def easter_sunday(year):
    """Gregorian Easter via the Anonymous Gregorian algorithm."""
    year = normalize_vacation_year(year)
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def labor_day(year):
    year = normalize_vacation_year(year)
    september_first = date(year, 9, 1)
    return september_first + timedelta(days=(7 - september_first.weekday()) % 7)


def union_week_is_seasonal(vacation_year, week_ending):
    year = normalize_vacation_year(vacation_year)
    week_ending = normalize_week_ending(year, week_ending)
    first_seasonal_week_ending = easter_sunday(year) + timedelta(days=6)
    last_seasonal_week_ending = labor_day(year) + timedelta(days=5)
    return first_seasonal_week_ending <= week_ending <= last_seasonal_week_ending


def union_whole_week_capacity(payroll_count, vacation_year, week_ending):
    payroll = _nonnegative_int(payroll_count, "Payroll count")
    seasonal = union_week_is_seasonal(vacation_year, week_ending)
    percentage = 17 if seasonal else 12
    return UnionVacationCapacity(
        payroll_count=payroll,
        percentage=percentage,
        capacity=max(1, math.floor(payroll * percentage / 100)),
        seasonal=seasonal,
    )


def union_single_day_capacity(payroll_count):
    payroll = _nonnegative_int(payroll_count, "Payroll count")
    return UnionVacationCapacity(
        payroll_count=payroll,
        percentage=5,
        capacity=max(1, math.floor(payroll * 5 / 100)),
        seasonal=False,
    )


def vacation_hierarchy(include_inactive=False):
    query = StaffingUnit.query
    if not include_inactive:
        query = query.filter(StaffingUnit.active.is_(True))
    units = query.order_by(
        StaffingUnit.display_order,
        func.lower(StaffingUnit.name),
        StaffingUnit.id,
    ).all()
    by_id = {unit.id: unit for unit in units}
    children = {}
    for unit in units:
        children.setdefault(unit.parent_id, []).append(unit)
    return {"units": units, "by_id": by_id, "children": children}


def vacation_actor(user, hierarchy=None, leadership_rows=None):
    hierarchy = hierarchy or vacation_hierarchy()
    app_role = get_user_app_role(user, "neostaffing") or "watcher"
    is_grandmaster = bool(
        app_role == "grandmaster" or getattr(user, "role", None) == "grandmaster"
    )
    if is_grandmaster:
        all_ids = frozenset(hierarchy["by_id"])
        return VacationActor(
            app_role=app_role,
            person=None,
            is_grandmaster=True,
            normal_scope_ids=all_ids,
            sideways_scope_ids=all_ids,
            management_capacity_ids=all_ids,
        )

    employee_id = str(getattr(user, "employee_id", "") or "").strip()
    person = None
    if employee_id:
        person = StaffingPerson.query.filter(
            StaffingPerson.active.is_(True),
            func.lower(StaffingPerson.employee_id) == employee_id.casefold(),
        ).first()
    if not person:
        return VacationActor(
            app_role=app_role,
            person=None,
            is_grandmaster=False,
            normal_scope_ids=frozenset(),
            sideways_scope_ids=frozenset(),
            management_capacity_ids=frozenset(),
        )

    if leadership_rows is None:
        leadership_rows = StaffingLeadershipAssignment.query.filter_by(
            person_id=person.id,
            active=True,
        ).all()
    roots = {
        row.unit_id
        for row in leadership_rows
        if row.unit_id in hierarchy["by_id"]
    }
    normal = _descendant_ids(roots, hierarchy)
    sideways = set(normal)
    if ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]:
        sibling_roots = {
            sibling.id
            for root_id in roots
            for sibling in hierarchy["children"].get(
                hierarchy["by_id"][root_id].parent_id,
                (),
            )
        }
        sideways.update(_descendant_ids(sibling_roots, hierarchy))

    management_ids = set()
    for assignment in leadership_rows:
        unit = hierarchy["by_id"].get(assignment.unit_id)
        if not unit:
            continue
        management_ids.update(_management_capacity_ids_for_assignment(person, unit, hierarchy))
        if ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]:
            for sibling in hierarchy["children"].get(unit.parent_id, ()):
                management_ids.update(
                    _management_capacity_ids_for_assignment(person, sibling, hierarchy)
                )
    return VacationActor(
        app_role=app_role,
        person=person,
        is_grandmaster=False,
        normal_scope_ids=frozenset(normal),
        sideways_scope_ids=frozenset(sideways),
        management_capacity_ids=frozenset(management_ids),
    )


def can_edit_union_scope(actor, scope_ids):
    requested = set(scope_ids)
    allowed = (
        actor.sideways_scope_ids
        if ROLE_LEVELS.get(actor.app_role, 0) >= ROLE_LEVELS["master"]
        else actor.normal_scope_ids
    )
    return bool(requested) and requested.issubset(allowed)


def can_edit_management_capacity(actor, area_id):
    return bool(
        actor.is_grandmaster
        or int(area_id) in actor.management_capacity_ids
    )


def operation_has_editable_union_scope(actor, operation_id, hierarchy):
    allowed = (
        actor.sideways_scope_ids
        if ROLE_LEVELS.get(actor.app_role, 0) >= ROLE_LEVELS["master"]
        else actor.normal_scope_ids
    )
    return any(
        _is_descendant_or_self(unit_id, operation_id, hierarchy)
        for unit_id in allowed
    )


def management_vacation_context(vacation_year, user):
    year = normalize_vacation_year(vacation_year)
    hierarchy = vacation_hierarchy()
    leadership = (
        StaffingLeadershipAssignment.query.options(
            joinedload(StaffingLeadershipAssignment.person),
        )
        .join(StaffingPerson)
        .filter(
            StaffingLeadershipAssignment.active.is_(True),
            StaffingPerson.active.is_(True),
            StaffingPerson.classification.in_(VACATION_MANAGEMENT_CLASSIFICATIONS),
        )
        .order_by(StaffingLeadershipAssignment.id)
        .all()
    )
    actor_rows = [
        row
        for row in leadership
        if getattr(row.person, "employee_id", "")
        == str(getattr(user, "employee_id", "") or "")
    ]
    actor = vacation_actor(user, hierarchy, actor_rows)
    capacity_by_area = {
        row.area_unit_id: row
        for row in StaffingVacationManagementCapacity.query.filter_by(
            vacation_year=year
        ).all()
    }
    off_weeks_by_area = {}
    for row in StaffingVacationManagementWeekOverride.query.filter_by(
        vacation_year=year
    ).all():
        off_weeks_by_area.setdefault(row.area_unit_id, set()).add(row.week_ending)

    primary_by_person = {}
    secondary_by_person = {}
    for assignment in leadership:
        if assignment.person_id not in primary_by_person:
            primary_by_person[assignment.person_id] = assignment
        else:
            secondary_by_person.setdefault(assignment.person_id, []).append(assignment)

    people_by_area = {}
    secondary_by_area = {}
    for person_id, assignment in primary_by_person.items():
        area = management_area_for_assignment(
            assignment.person,
            hierarchy["by_id"].get(assignment.unit_id),
            hierarchy,
        )
        if area:
            people_by_area.setdefault(area.id, []).append(assignment.person)
        for secondary in secondary_by_person.get(person_id, ()):
            secondary_area = management_area_for_assignment(
                secondary.person,
                hierarchy["by_id"].get(secondary.unit_id),
                hierarchy,
            )
            if secondary_area and (not area or secondary_area.id != area.id):
                secondary_by_area.setdefault(secondary_area.id, []).append(secondary.person)

    area_rows = []
    for area in hierarchy["units"]:
        if area.unit_type not in VACATION_MANAGEMENT_AREA_TYPES:
            continue
        primary_people = _seniority_order(people_by_area.get(area.id, ()))
        secondary_people = _seniority_order(secondary_by_area.get(area.id, ()))
        if (
            not primary_people
            and not secondary_people
            and area.id not in capacity_by_area
            and not can_edit_management_capacity(actor, area.id)
        ):
            continue
        area_rows.append(
            {
                "area": area,
                "path": unit_path(area, hierarchy),
                "people": primary_people,
                "secondary_people": secondary_people,
                "capacity": capacity_by_area.get(area.id),
                "off_week_endings": off_weeks_by_area.get(area.id, set()),
                "can_edit": can_edit_management_capacity(actor, area.id),
            }
        )
    area_rows.sort(key=lambda row: _unit_sort_key(row["area"], hierarchy))
    return {
        "vacation_year": year,
        "weeks": vacation_year_weeks(year),
        "selection_opens_on": vacation_selection_opens_on(year),
        "areas": area_rows,
        "actor": actor,
        "is_dynamic": True,
    }


def management_area_for_assignment(person, unit, hierarchy):
    if not person or not unit:
        return None
    classification = person.classification
    if classification == "part_time_supervisor":
        return _ancestor_of_type(unit, "department", hierarchy)
    if classification in {"full_time_supervisor", "full_time_specialist"}:
        return _ancestor_of_type(unit, "operation", hierarchy)
    if classification in {"manager", "division_manager"}:
        return _ancestor_of_type(unit, "sort", hierarchy) or (
            unit if unit.unit_type == "sort" else None
        )
    return None


def save_management_capacity(vacation_year, area_id, values, user):
    year = normalize_vacation_year(vacation_year)
    hierarchy = vacation_hierarchy()
    area = hierarchy["by_id"].get(_positive_int(area_id, "Management area"))
    if not area or area.unit_type not in VACATION_MANAGEMENT_AREA_TYPES:
        raise ValueError("Select a valid Management vacation area.")
    actor = vacation_actor(user, hierarchy)
    if not can_edit_management_capacity(actor, area.id):
        raise ValueError("You do not have authority to configure this Management area.")
    limits = {
        "normal_limit": _nonnegative_int(values.get("normal_limit"), "Normal limit"),
        "one_pinned_limit": _nonnegative_int(
            values.get("one_pinned_limit"), "One-pinned limit"
        ),
        "two_plus_pinned_limit": _nonnegative_int(
            values.get("two_plus_pinned_limit"), "Two-plus-pinned limit"
        ),
    }
    row = StaffingVacationManagementCapacity.query.filter_by(
        vacation_year=year,
        area_unit_id=area.id,
    ).with_for_update().first()
    if not row:
        row = StaffingVacationManagementCapacity(
            vacation_year=year,
            area_unit_id=area.id,
            created_by_user_id=getattr(user, "id", None),
        )
        db.session.add(row)
    for key, value in limits.items():
        setattr(row, key, value)
    row.updated_by_user_id = getattr(user, "id", None)
    db.session.flush()
    return row


def initialize_management_capacity_year(vacation_year, area_ids, user):
    year = normalize_vacation_year(vacation_year)
    normalized_ids = {_positive_int(value, "Management area") for value in area_ids}
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    areas = {
        unit_id: hierarchy["by_id"].get(unit_id)
        for unit_id in normalized_ids
    }
    if any(
        not area or area.unit_type not in VACATION_MANAGEMENT_AREA_TYPES
        for area in areas.values()
    ):
        raise ValueError("Select valid Management vacation areas.")
    if any(not can_edit_management_capacity(actor, area_id) for area_id in normalized_ids):
        raise ValueError("You do not have authority to initialize every selected area.")

    existing_ids = {
        row.area_unit_id
        for row in StaffingVacationManagementCapacity.query.filter(
            StaffingVacationManagementCapacity.vacation_year == year,
            StaffingVacationManagementCapacity.area_unit_id.in_(normalized_ids or {-1}),
        ).all()
    }
    prior_by_area = {
        row.area_unit_id: row
        for row in StaffingVacationManagementCapacity.query.filter(
            StaffingVacationManagementCapacity.vacation_year == year - 1,
            StaffingVacationManagementCapacity.area_unit_id.in_(normalized_ids or {-1}),
        ).all()
    }
    created = []
    for area_id in sorted(normalized_ids - existing_ids):
        prior = prior_by_area.get(area_id)
        if not prior:
            continue
        row = StaffingVacationManagementCapacity(
            vacation_year=year,
            area_unit_id=area_id,
            normal_limit=prior.normal_limit,
            one_pinned_limit=prior.one_pinned_limit,
            two_plus_pinned_limit=prior.two_plus_pinned_limit,
            created_by_user_id=getattr(user, "id", None),
            updated_by_user_id=getattr(user, "id", None),
        )
        db.session.add(row)
        created.append(row)
    db.session.flush()
    return created


def reduced_capacity_enabled(vacation_year, area_id, week_ending):
    year = normalize_vacation_year(vacation_year)
    week = normalize_week_ending(year, week_ending)
    return not StaffingVacationManagementWeekOverride.query.filter_by(
        vacation_year=year,
        area_unit_id=_positive_int(area_id, "Management area"),
        week_ending=week,
    ).first()


def set_reduced_capacity_enabled(vacation_year, area_id, week_ending, enabled, user):
    year = normalize_vacation_year(vacation_year)
    week = normalize_week_ending(year, week_ending)
    hierarchy = vacation_hierarchy()
    area_id = _positive_int(area_id, "Management area")
    area = hierarchy["by_id"].get(area_id)
    if not area or area.unit_type not in VACATION_MANAGEMENT_AREA_TYPES:
        raise ValueError("Select a valid Management vacation area.")
    actor = vacation_actor(user, hierarchy)
    if not can_edit_management_capacity(actor, area.id):
        raise ValueError("You do not have authority to configure this Management area.")
    row = StaffingVacationManagementWeekOverride.query.filter_by(
        vacation_year=year,
        area_unit_id=area.id,
        week_ending=week,
    ).with_for_update().first()
    if _boolean(enabled):
        if row:
            db.session.delete(row)
        db.session.flush()
        return None
    if not row:
        row = StaffingVacationManagementWeekOverride(
            vacation_year=year,
            area_unit_id=area.id,
            week_ending=week,
            created_by_user_id=getattr(user, "id", None),
        )
        db.session.add(row)
    db.session.flush()
    return row


def create_union_calendar(values, user):
    return _save_union_calendar(None, values, user)


def update_union_calendar(calendar, values, user):
    if not calendar:
        raise ValueError("The selected Union vacation calendar was not found.")
    return _save_union_calendar(calendar, values, user)


def _save_union_calendar(calendar, values, user):
    year = normalize_vacation_year(values.get("vacation_year"))
    name = str(values.get("name") or "").strip()
    if not name:
        raise ValueError("Union calendar name is required.")
    if len(name) > 140:
        raise ValueError("Union calendar name must be 140 characters or fewer.")
    operation_id = _positive_int(values.get("operation_unit_id"), "Operation")
    scope_ids = _submitted_scope_ids(values)
    include_pt = _boolean(values.get("include_part_time"))
    include_ft = _boolean(values.get("include_full_time"))
    if not include_pt and not include_ft:
        raise ValueError("Select PT Union, FT Union, or both.")

    hierarchy = vacation_hierarchy()
    operation = hierarchy["by_id"].get(operation_id)
    if not operation or operation.unit_type != "operation":
        raise ValueError("Select a valid Operation.")
    scope_units = [hierarchy["by_id"].get(unit_id) for unit_id in scope_ids]
    if not scope_units or any(
        not unit
        or unit.unit_type not in VACATION_UNION_SCOPE_TYPES
        or not _is_descendant_or_self(unit.id, operation.id, hierarchy)
        for unit in scope_units
    ):
        raise ValueError("Select valid organizational scope within the Operation.")
    scope_ids = _canonical_scope_ids(scope_ids, hierarchy)
    actor = vacation_actor(user, hierarchy)
    if not can_edit_union_scope(actor, scope_ids):
        raise ValueError("You do not have authority to configure this Union calendar scope.")

    duplicate = StaffingVacationUnionCalendar.query.filter(
        StaffingVacationUnionCalendar.vacation_year == year,
        StaffingVacationUnionCalendar.operation_unit_id == operation.id,
        func.lower(StaffingVacationUnionCalendar.name) == name.casefold(),
    )
    if calendar:
        duplicate = duplicate.filter(StaffingVacationUnionCalendar.id != calendar.id)
    if duplicate.first():
        raise ValueError("A Union calendar with this name already exists for the year.")

    if not calendar:
        calendar = StaffingVacationUnionCalendar(
            created_by_user_id=getattr(user, "id", None),
        )
        db.session.add(calendar)
    calendar.vacation_year = year
    calendar.operation_unit_id = operation.id
    calendar.name = name
    calendar.include_part_time = include_pt
    calendar.include_full_time = include_ft
    calendar.active = _boolean(values.get("active", True))
    calendar.updated_by_user_id = getattr(user, "id", None)
    _replace_union_scopes(calendar, scope_ids)
    db.session.flush()
    return calendar


def union_calendar_members(calendar):
    hierarchy = vacation_hierarchy()
    scope_ids = {scope.staffing_unit_id for scope in calendar.scopes}
    work_area_ids = _scope_work_area_ids(scope_ids, hierarchy)
    classifications = set()
    if calendar.include_part_time:
        classifications.update(VACATION_PT_CLASSIFICATIONS)
    if calendar.include_full_time:
        classifications.update(VACATION_FT_CLASSIFICATIONS)
    if not work_area_ids or not classifications:
        return []
    people = (
        StaffingPerson.query.join(StaffingWorkAssignment)
        .filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(classifications),
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids),
        )
        .order_by(
            StaffingPerson.seniority_date,
            func.lower(StaffingPerson.last_name),
            func.lower(StaffingPerson.first_name),
            StaffingPerson.id,
        )
        .all()
    )
    return people


def union_calendars_context(vacation_year, user):
    year = normalize_vacation_year(vacation_year)
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    calendars = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes)
        )
        .filter_by(vacation_year=year)
        .order_by(
            StaffingVacationUnionCalendar.operation_unit_id,
            func.lower(StaffingVacationUnionCalendar.name),
            StaffingVacationUnionCalendar.id,
        )
        .all()
    )
    union_rows = (
        db.session.query(StaffingPerson, StaffingWorkAssignment)
        .join(StaffingWorkAssignment, StaffingWorkAssignment.person_id == StaffingPerson.id)
        .filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(VACATION_UNION_CLASSIFICATIONS),
            StaffingWorkAssignment.active.is_(True),
        )
        .all()
    )
    people_by_work_area = {}
    for person, assignment in union_rows:
        people_by_work_area.setdefault(assignment.work_area_unit_id, []).append(person)

    calendar_rows = []
    for calendar in calendars:
        scope_ids = {scope.staffing_unit_id for scope in calendar.scopes}
        work_area_ids = _scope_work_area_ids(scope_ids, hierarchy)
        classifications = set()
        if calendar.include_part_time:
            classifications.update(VACATION_PT_CLASSIFICATIONS)
        if calendar.include_full_time:
            classifications.update(VACATION_FT_CLASSIFICATIONS)
        members_by_id = {
            person.id: person
            for work_area_id in work_area_ids
            for person in people_by_work_area.get(work_area_id, ())
            if person.classification in classifications
        }
        members = _seniority_order(members_by_id.values())
        scope_units = [
            hierarchy["by_id"][unit_id]
            for unit_id in sorted(scope_ids)
            if unit_id in hierarchy["by_id"]
        ]
        department_ids = {
            department.id
            for unit in scope_units
            for department in [_ancestor_of_type(unit, "department", hierarchy)]
            if department
        }
        department_label = (
            hierarchy["by_id"][next(iter(department_ids))].name
            if len(department_ids) == 1
            else "ALL / MULTIPLE DEPARTMENTS"
        )
        calendar_rows.append(
            {
                "calendar": calendar,
                "operation": hierarchy["by_id"].get(calendar.operation_unit_id),
                "department_label": department_label,
                "scope_ids": scope_ids,
                "scope_units": scope_units,
                "members": members,
                "payroll_count": len(members),
                "single_day_capacity": union_single_day_capacity(len(members)),
                "can_edit": can_edit_union_scope(actor, scope_ids),
            }
        )

    browser = []
    for operation in (
        unit for unit in hierarchy["units"] if unit.unit_type == "operation"
    ):
        operation_rows = [
            row for row in calendar_rows if row["calendar"].operation_unit_id == operation.id
        ]
        if not operation_rows:
            continue
        department_groups = []
        for label in sorted({row["department_label"] for row in operation_rows}):
            department_groups.append(
                {
                    "label": label,
                    "calendars": [
                        row for row in operation_rows if row["department_label"] == label
                    ],
                }
            )
        browser.append({"operation": operation, "departments": department_groups})
    return {
        "vacation_year": year,
        "weeks": vacation_year_weeks(year),
        "calendars": calendar_rows,
        "browser": browser,
        "operations": [
            unit for unit in hierarchy["units"] if unit.unit_type == "operation"
        ],
        "hierarchy": hierarchy,
        "actor": actor,
        "can_create": bool(actor.is_grandmaster or actor.sideways_scope_ids),
    }


def union_scope_tree(operation_id, selected_ids=(), hierarchy=None):
    hierarchy = hierarchy or vacation_hierarchy()
    operation = hierarchy["by_id"].get(_positive_int(operation_id, "Operation"))
    if not operation or operation.unit_type != "operation":
        raise ValueError("Select a valid Operation.")
    selected = set(selected_ids)

    def build(unit, ancestor_selected=False):
        checked_here = ancestor_selected or unit.id in selected
        children = [
            build(child, checked_here)
            for child in hierarchy["children"].get(unit.id, ())
            if child.unit_type in {"department", "work_area"}
        ]
        child_checked = [child["checked"] or child["indeterminate"] for child in children]
        checked = checked_here or (bool(children) and all(child["checked"] for child in children))
        indeterminate = not checked and any(child_checked)
        return {
            "unit": unit,
            "checked": checked,
            "explicit": unit.id in selected,
            "indeterminate": indeterminate,
            "children": children,
        }

    return build(operation)


def normalize_vacation_year(value):
    try:
        year = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Vacation year must be valid.") from error
    if year < VACATION_YEAR_MIN or year > VACATION_YEAR_MAX:
        raise ValueError("Vacation year must be valid.")
    return year


def normalize_week_ending(vacation_year, value):
    year = normalize_vacation_year(vacation_year)
    if isinstance(value, date):
        week_ending = value
    else:
        try:
            week_ending = date.fromisoformat(str(value or "").strip())
        except ValueError as error:
            raise ValueError("Week Ending must be a valid Saturday.") from error
    if week_ending.weekday() != calendar_module.SATURDAY or week_ending.year != year:
        raise ValueError("Week Ending must be a Saturday in the vacation year.")
    return week_ending


def unit_path(unit, hierarchy):
    names = []
    current = unit
    while current:
        names.append(current.name)
        current = hierarchy["by_id"].get(current.parent_id)
    return " / ".join(reversed(names))


def _replace_union_scopes(calendar, scope_ids):
    existing = {scope.staffing_unit_id: scope for scope in calendar.scopes}
    for unit_id, scope in existing.items():
        if unit_id not in scope_ids:
            db.session.delete(scope)
    for unit_id in sorted(scope_ids - set(existing)):
        calendar.scopes.append(
            StaffingVacationUnionCalendarScope(staffing_unit_id=unit_id)
        )


def _submitted_scope_ids(values):
    raw_values = (
        values.getlist("staffing_unit_ids")
        if hasattr(values, "getlist")
        else values.get("staffing_unit_ids", ())
    )
    if isinstance(raw_values, (str, int)):
        raw_values = [raw_values]
    return {_positive_int(value, "organizational scope") for value in raw_values or ()}


def _canonical_scope_ids(scope_ids, hierarchy):
    ordered = sorted(scope_ids, key=lambda unit_id: _unit_depth(unit_id, hierarchy))
    result = set()
    for unit_id in ordered:
        if any(_is_descendant_or_self(unit_id, selected_id, hierarchy) for selected_id in result):
            continue
        result.add(unit_id)
    return result


def _scope_work_area_ids(scope_ids, hierarchy):
    return {
        unit_id
        for scope_id in scope_ids
        for unit_id in _descendant_ids({scope_id}, hierarchy)
        if hierarchy["by_id"].get(unit_id)
        and hierarchy["by_id"][unit_id].unit_type == "work_area"
    }


def _management_capacity_ids_for_assignment(person, unit, hierarchy):
    result = set()
    if person.classification == "full_time_supervisor":
        if unit.unit_type == "department":
            result.add(unit.id)
            operation = _ancestor_of_type(unit, "operation", hierarchy)
            if operation:
                result.add(operation.id)
    elif person.classification == "manager":
        operation = _ancestor_of_type(unit, "operation", hierarchy)
        if operation:
            result.add(operation.id)
            result.update(
                child.id
                for child in hierarchy["children"].get(operation.id, ())
                if child.unit_type == "department"
            )
            staffing_sort = _ancestor_of_type(operation, "sort", hierarchy)
            if staffing_sort:
                result.add(staffing_sort.id)
    elif person.classification == "division_manager":
        staffing_sort = _ancestor_of_type(unit, "sort", hierarchy) or (
            unit if unit.unit_type == "sort" else None
        )
        if staffing_sort:
            result.update(_descendant_ids({staffing_sort.id}, hierarchy))
    return {unit_id for unit_id in result if hierarchy["by_id"][unit_id].unit_type in VACATION_MANAGEMENT_AREA_TYPES}


def _ancestor_of_type(unit, unit_type, hierarchy):
    current = unit
    while current:
        if current.unit_type == unit_type:
            return current
        current = hierarchy["by_id"].get(current.parent_id)
    return None


def _is_descendant_or_self(unit_id, ancestor_id, hierarchy):
    current = hierarchy["by_id"].get(unit_id)
    while current:
        if current.id == ancestor_id:
            return True
        current = hierarchy["by_id"].get(current.parent_id)
    return False


def _descendant_ids(root_ids, hierarchy):
    result = set()
    stack = list(root_ids)
    while stack:
        unit_id = stack.pop()
        if unit_id in result or unit_id not in hierarchy["by_id"]:
            continue
        result.add(unit_id)
        stack.extend(child.id for child in hierarchy["children"].get(unit_id, ()))
    return result


def _unit_depth(unit_id, hierarchy):
    depth = 0
    current = hierarchy["by_id"].get(unit_id)
    while current and current.parent_id in hierarchy["by_id"]:
        depth += 1
        current = hierarchy["by_id"].get(current.parent_id)
    return depth


def _unit_sort_key(unit, hierarchy):
    chain = []
    current = unit
    while current:
        chain.append((current.display_order, current.name.casefold(), current.id))
        current = hierarchy["by_id"].get(current.parent_id)
    return tuple(reversed(chain))


def _seniority_order(people):
    return sorted(
        people,
        key=lambda person: (
            person.seniority_date,
            person.last_name.casefold(),
            person.first_name.casefold(),
            person.id,
        ),
    )


def _positive_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Select a valid {label}.") from error
    if normalized <= 0:
        raise ValueError(f"Select a valid {label}.")
    return normalized


def _nonnegative_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be zero or greater.") from error
    if normalized < 0:
        raise ValueError(f"{label} must be zero or greater.")
    return normalized


def _boolean(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}
