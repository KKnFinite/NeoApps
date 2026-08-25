from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    StaffingVacationManagementSelection,
    StaffingVacationManagementTurnResolution,
    StaffingVacationManagementTurnState,
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
VACATION_MANAGEMENT_PASS_ADMIN_CLASSIFICATIONS = frozenset(
    {
        "full_time_supervisor",
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


@dataclass(frozen=True)
class ManagementTurnSnapshot:
    status: str
    current_person_id: int | None
    resolved_person_ids: frozenset[int]
    completed: bool


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


def management_vacation_entitlement(seniority_date, vacation_year):
    """Return whole-week entitlement earned by the end of the vacation year."""
    year = normalize_vacation_year(vacation_year)
    if not isinstance(seniority_date, date):
        raise ValueError("A valid seniority date is required.")
    service_date = date(year, 12, 31)
    completed_years = service_date.year - seniority_date.year - (
        (service_date.month, service_date.day)
        < (seniority_date.month, seniority_date.day)
    )
    if completed_years >= 25:
        return 6
    if completed_years >= 20:
        return 5
    if completed_years >= 10:
        return 4
    if completed_years >= 5:
        return 3
    return 2


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


def management_vacation_context(vacation_year, user, today=None):
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    hierarchy = vacation_hierarchy()
    leadership = _management_leadership_rows()
    actor_employee_id = str(getattr(user, "employee_id", "") or "").casefold()
    actor_rows = [
        row
        for row in leadership
        if str(getattr(row.person, "employee_id", "") or "").casefold()
        == actor_employee_id
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
    selections = (
        StaffingVacationManagementSelection.query.filter_by(vacation_year=year)
        .filter(StaffingVacationManagementSelection.cancelled_at.is_(None))
        .order_by(
            StaffingVacationManagementSelection.week_ending,
            StaffingVacationManagementSelection.id,
        )
        .all()
    )
    states = (
        StaffingVacationManagementTurnState.query.options(
            joinedload(StaffingVacationManagementTurnState.current_person),
            selectinload(StaffingVacationManagementTurnState.resolutions),
        )
        .filter_by(vacation_year=year)
        .all()
    )
    state_by_area = {state.area_unit_id: state for state in states}
    selections_by_person = {}
    for selection in selections:
        selections_by_person.setdefault(selection.staffing_person_id, []).append(selection)

    primary_by_person, secondary_by_person = _primary_and_secondary_assignments(
        leadership
    )
    people_by_area = {}
    secondary_by_area = {}
    primary_area_by_person = {}
    for person_id, assignment in primary_by_person.items():
        area = management_area_for_assignment(
            assignment.person,
            hierarchy["by_id"].get(assignment.unit_id),
            hierarchy,
        )
        if area:
            primary_area_by_person[person_id] = area
            people_by_area.setdefault(area.id, []).append(assignment.person)
        for secondary in secondary_by_person.get(person_id, ()):
            secondary_area = management_area_for_assignment(
                secondary.person,
                hierarchy["by_id"].get(secondary.unit_id),
                hierarchy,
            )
            if secondary_area and (not area or secondary_area.id != area.id):
                secondary_by_area.setdefault(secondary_area.id, []).append(secondary.person)

    used_by_area_week = {}
    for selection in selections:
        area = primary_area_by_person.get(selection.staffing_person_id)
        if area:
            key = (area.id, selection.week_ending)
            used_by_area_week[key] = used_by_area_week.get(key, 0) + 1

    weeks = vacation_year_weeks(year)
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
        turn = management_turn_snapshot(
            year,
            primary_people,
            selections_by_person,
            state_by_area.get(area.id),
            today=today,
        )
        capacity = capacity_by_area.get(area.id)
        week_rows = []
        for week in weeks:
            used = used_by_area_week.get((area.id, week.week_ending), 0)
            limit = capacity.normal_limit if capacity else None
            week_rows.append(
                {
                    "week": week,
                    "used": used,
                    "limit": limit,
                    "full": limit is None or used >= limit,
                }
            )
        person_rows = []
        current_person = next(
            (person for person in primary_people if person.id == turn.current_person_id),
            None,
        )
        for person in primary_people:
            selected = selections_by_person.get(person.id, [])
            entitlement = management_vacation_entitlement(
                person.seniority_date,
                year,
            )
            remaining = max(0, entitlement - len(selected))
            owner_can_write = bool(
                actor.is_grandmaster
                or (actor.person and actor.person.id == person.id)
            )
            person_rows.append(
                {
                    "person": person,
                    "entitlement": entitlement,
                    "remaining": remaining,
                    "selections": selected,
                    "is_active_turn": person.id == turn.current_person_id,
                    "can_select": owner_can_write
                    and _turn_allows_person(turn, person, current_person)
                    and remaining > 0,
                    "can_pass": bool(
                        actor.person
                        and actor.person.id == person.id
                        and person.id == turn.current_person_id
                    ),
                }
            )
        area_rows.append(
            {
                "area": area,
                "path": unit_path(area, hierarchy),
                "people": primary_people,
                "person_rows": person_rows,
                "secondary_people": secondary_people,
                "capacity": capacity,
                "week_rows": week_rows,
                "turn": turn,
                "can_admin_pass": bool(
                    turn.current_person_id
                    and _can_administer_management_turn(actor, area.id)
                ),
                "off_week_endings": off_weeks_by_area.get(area.id, set()),
                "can_edit": can_edit_management_capacity(actor, area.id),
            }
        )
    area_rows.sort(key=lambda row: _unit_sort_key(row["area"], hierarchy))
    return {
        "vacation_year": year,
        "weeks": weeks,
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


def management_primary_area(person, hierarchy=None):
    """Resolve current Management ownership from the first active assignment."""
    if (
        not person
        or not person.active
        or person.employee_status != "active"
        or person.classification not in VACATION_MANAGEMENT_CLASSIFICATIONS
    ):
        return None
    hierarchy = hierarchy or vacation_hierarchy()
    assignment = (
        StaffingLeadershipAssignment.query.filter_by(
            person_id=person.id,
            active=True,
        )
        .order_by(StaffingLeadershipAssignment.id)
        .first()
    )
    if not assignment:
        return None
    return management_area_for_assignment(
        person,
        hierarchy["by_id"].get(assignment.unit_id),
        hierarchy,
    )


def management_turn_snapshot(
    vacation_year,
    people,
    selections_by_person,
    state=None,
    *,
    today=None,
):
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    resolved_ids = frozenset(
        resolution.staffing_person_id
        for resolution in (state.resolutions if state else ())
    )
    if today < vacation_selection_opens_on(year):
        return ManagementTurnSnapshot("not_open", None, resolved_ids, False)
    if state and state.completed_at:
        return ManagementTurnSnapshot("completed", None, resolved_ids, True)

    ordered = _seniority_order(people)
    unresolved_with_bank = [
        person
        for person in ordered
        if person.id not in resolved_ids
        and _management_remaining_for_person(
            person,
            year,
            selections_by_person.get(person.id, ()),
        )
        > 0
    ]
    current_id = state.current_person_id if state else None
    if current_id and any(person.id == current_id for person in unresolved_with_bank):
        return ManagementTurnSnapshot("active", current_id, resolved_ids, False)
    if state and state.current_person:
        anchor = _person_sort_key(state.current_person)
        unresolved_with_bank = [
            person
            for person in unresolved_with_bank
            if _person_sort_key(person) > anchor
        ]
    if unresolved_with_bank:
        return ManagementTurnSnapshot(
            "active",
            unresolved_with_bank[0].id,
            resolved_ids,
            False,
        )
    return ManagementTurnSnapshot("completed", None, resolved_ids, True)


def add_management_weeks(
    person,
    vacation_year,
    week_endings,
    user,
    *,
    today=None,
):
    """Atomically reserve one or more whole weeks against bank and capacity."""
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    if today < vacation_selection_opens_on(year):
        raise ValueError("Initial Management vacation selection has not opened yet.")
    normalized_weeks = sorted(
        {normalize_week_ending(year, value) for value in week_endings}
    )
    if not normalized_weeks:
        raise ValueError("Select at least one vacation week.")
    person = _management_person(person)
    hierarchy = vacation_hierarchy()
    area = management_primary_area(person, hierarchy)
    if not area:
        raise ValueError("The selected person does not have a primary Management area.")
    actor = vacation_actor(user, hierarchy)
    if not (
        actor.is_grandmaster
        or (actor.person and actor.person.id == person.id)
    ):
        raise ValueError("You may only select your own Management vacation weeks.")

    _lock_management_area(area.id)
    capacity = (
        StaffingVacationManagementCapacity.query.filter_by(
            vacation_year=year,
            area_unit_id=area.id,
        )
        .with_for_update()
        .first()
    )
    if not capacity:
        raise ValueError("Management capacity is not configured for this area and year.")
    people = _management_people_for_area(area.id, hierarchy)
    all_person_selections = _management_active_selections_by_person(year)
    state = _locked_management_turn_state(
        year,
        area,
        people,
        all_person_selections,
        today,
    )
    turn = management_turn_snapshot(
        year,
        people,
        all_person_selections,
        state,
        today=today,
    )
    current_person = next(
        (row for row in people if row.id == turn.current_person_id),
        state.current_person if state else None,
    )
    if not _turn_allows_person(turn, person, current_person):
        raise ValueError("Initial selection has not reached this supervisor yet.")

    existing_rows = (
        StaffingVacationManagementSelection.query.filter(
            StaffingVacationManagementSelection.staffing_person_id == person.id,
            StaffingVacationManagementSelection.vacation_year == year,
            StaffingVacationManagementSelection.week_ending.in_(normalized_weeks),
        )
        .with_for_update()
        .all()
    )
    existing_by_week = {row.week_ending: row for row in existing_rows}
    if any(row.cancelled_at is None for row in existing_rows):
        raise ValueError("One of the selected weeks is already in this vacation bank.")
    current_selections = all_person_selections.get(person.id, [])
    remaining = _management_remaining_for_person(person, year, current_selections)
    if len(normalized_weeks) > remaining:
        raise ValueError("The selected weeks exceed the remaining vacation bank.")

    usage = _management_week_usage(year, normalized_weeks, hierarchy)
    for week in normalized_weeks:
        if usage.get((area.id, week), 0) >= capacity.normal_limit:
            raise ValueError(
                f"Management capacity is full for WE {week.strftime('%b %d, %Y')}."
            )

    now = datetime.utcnow()
    saved = []
    for week in normalized_weeks:
        row = existing_by_week.get(week)
        if row:
            row.cancelled_at = None
            row.cancelled_by_user_id = None
            row.cancellation_reason = None
            row.selected_by_user_id = getattr(user, "id", None)
            row.updated_at = now
        else:
            row = StaffingVacationManagementSelection(
                staffing_person_id=person.id,
                vacation_year=year,
                week_ending=week,
                selected_by_user_id=getattr(user, "id", None),
                created_at=now,
                updated_at=now,
            )
            db.session.add(row)
        saved.append(row)
    db.session.flush()

    new_remaining = remaining - len(normalized_weeks)
    if state.current_person_id == person.id and new_remaining == 0:
        _advance_management_turn(
            state,
            people,
            _management_active_selections_by_person(year),
            person,
            "completed",
            user,
            now,
        )
    db.session.flush()
    return saved


def add_management_week(person, vacation_year, week_ending, user, *, today=None):
    return add_management_weeks(
        person,
        vacation_year,
        [week_ending],
        user,
        today=today,
    )[0]


def pass_management_turn(
    vacation_year,
    area_id,
    person,
    user,
    *,
    administrative=False,
    today=None,
):
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    if today < vacation_selection_opens_on(year):
        raise ValueError("Initial Management vacation selection has not opened yet.")
    person = _management_person(person)
    hierarchy = vacation_hierarchy()
    area = hierarchy["by_id"].get(_positive_int(area_id, "Management area"))
    if not area or area.unit_type not in VACATION_MANAGEMENT_AREA_TYPES:
        raise ValueError("Select a valid Management vacation area.")
    actor = vacation_actor(user, hierarchy)
    if administrative:
        if not _can_administer_management_turn(actor, area.id):
            raise ValueError("You do not have authority to advance this Management turn.")
    elif not actor.person or actor.person.id != person.id:
        raise ValueError("Only the active supervisor may voluntarily pass their turn.")

    _lock_management_area(area.id)
    people = _management_people_for_area(area.id, hierarchy)
    selections_by_person = _management_active_selections_by_person(year)
    state = _locked_management_turn_state(
        year,
        area,
        people,
        selections_by_person,
        today,
    )
    turn = management_turn_snapshot(
        year,
        people,
        selections_by_person,
        state,
        today=today,
    )
    if turn.current_person_id != person.id:
        raise ValueError("This supervisor is no longer the active Management turn.")
    now = datetime.utcnow()
    _advance_management_turn(
        state,
        people,
        selections_by_person,
        person,
        "admin_passed" if administrative else "passed",
        user,
        now,
    )
    db.session.flush()
    return state


def reconcile_management_person_state(person, user=None, *, today=None):
    """Reconcile active turns and future picks after a management roster change."""
    person = _management_person(person, require_active=False)
    today = today or date.today()
    hierarchy = vacation_hierarchy()
    current_area = management_primary_area(person, hierarchy)
    now = datetime.utcnow()
    states = (
        StaffingVacationManagementTurnState.query.filter(
            StaffingVacationManagementTurnState.current_person_id == person.id,
            StaffingVacationManagementTurnState.completed_at.is_(None),
        )
        .with_for_update()
        .all()
    )
    for state in states:
        if current_area and current_area.id == state.area_unit_id:
            continue
        area = hierarchy["by_id"].get(state.area_unit_id)
        if not area:
            continue
        people = _management_people_for_area(area.id, hierarchy)
        selections_by_person = _management_active_selections_by_person(
            state.vacation_year
        )
        _advance_management_turn(
            state,
            people,
            selections_by_person,
            person,
            "transferred" if current_area else "departed",
            user,
            now,
        )

    cancelled = []
    if not current_area:
        cancelled = (
            StaffingVacationManagementSelection.query.filter(
                StaffingVacationManagementSelection.staffing_person_id == person.id,
                StaffingVacationManagementSelection.cancelled_at.is_(None),
                StaffingVacationManagementSelection.week_ending >= today,
            )
            .with_for_update()
            .all()
        )
        for row in cancelled:
            row.cancelled_at = now
            row.cancelled_by_user_id = getattr(user, "id", None)
            row.cancellation_reason = "left_management"
            row.updated_at = now
    db.session.flush()
    return {"advanced_turns": len(states), "cancelled_future_picks": len(cancelled)}


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


def _management_leadership_rows():
    return (
        StaffingLeadershipAssignment.query.options(
            joinedload(StaffingLeadershipAssignment.person),
        )
        .join(StaffingPerson)
        .filter(
            StaffingLeadershipAssignment.active.is_(True),
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(VACATION_MANAGEMENT_CLASSIFICATIONS),
        )
        .order_by(StaffingLeadershipAssignment.id)
        .all()
    )


def _primary_and_secondary_assignments(leadership_rows):
    primary = {}
    secondary = {}
    for assignment in leadership_rows:
        if assignment.person_id not in primary:
            primary[assignment.person_id] = assignment
        else:
            secondary.setdefault(assignment.person_id, []).append(assignment)
    return primary, secondary


def _management_people_for_area(area_id, hierarchy):
    primary, _secondary = _primary_and_secondary_assignments(
        _management_leadership_rows()
    )
    people = []
    for assignment in primary.values():
        area = management_area_for_assignment(
            assignment.person,
            hierarchy["by_id"].get(assignment.unit_id),
            hierarchy,
        )
        if area and area.id == area_id:
            people.append(assignment.person)
    return _seniority_order(people)


def _management_active_selections_by_person(vacation_year):
    rows = (
        StaffingVacationManagementSelection.query.filter_by(
            vacation_year=normalize_vacation_year(vacation_year)
        )
        .filter(StaffingVacationManagementSelection.cancelled_at.is_(None))
        .order_by(
            StaffingVacationManagementSelection.week_ending,
            StaffingVacationManagementSelection.id,
        )
        .all()
    )
    result = {}
    for row in rows:
        result.setdefault(row.staffing_person_id, []).append(row)
    return result


def _management_remaining_for_person(person, vacation_year, selections):
    return max(
        0,
        management_vacation_entitlement(person.seniority_date, vacation_year)
        - len(tuple(selections)),
    )


def _turn_allows_person(turn, person, current_person):
    if turn.status == "not_open":
        return False
    if turn.completed:
        return True
    if person.id == turn.current_person_id:
        return True
    if person.id in turn.resolved_person_ids:
        return True
    return bool(
        current_person
        and _person_sort_key(person) < _person_sort_key(current_person)
    )


def _can_administer_management_turn(actor, area_id):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification
            in VACATION_MANAGEMENT_PASS_ADMIN_CLASSIFICATIONS
            and can_edit_management_capacity(actor, area_id)
        )
    )


def _management_person(person, require_active=True):
    if isinstance(person, StaffingPerson):
        row = person
    else:
        row = db.session.get(StaffingPerson, _positive_int(person, "person"))
    if not row:
        raise ValueError("The selected Management supervisor was not found.")
    if require_active and (
        not row.active
        or row.employee_status != "active"
        or row.classification not in VACATION_MANAGEMENT_CLASSIFICATIONS
    ):
        raise ValueError("The selected person is not an active Management supervisor.")
    return row


def _lock_management_area(area_id):
    area = (
        StaffingUnit.query.filter_by(id=area_id, active=True)
        .with_for_update()
        .first()
    )
    if not area:
        raise ValueError("The Management vacation area is no longer available.")
    return area


def _locked_management_turn_state(
    vacation_year,
    area,
    people,
    selections_by_person,
    today,
):
    state = (
        StaffingVacationManagementTurnState.query.options(
            joinedload(StaffingVacationManagementTurnState.current_person),
            selectinload(StaffingVacationManagementTurnState.resolutions),
        )
        .filter_by(vacation_year=vacation_year, area_unit_id=area.id)
        .with_for_update()
        .first()
    )
    if state:
        snapshot = management_turn_snapshot(
            vacation_year,
            people,
            selections_by_person,
            state,
            today=today,
        )
        if state.current_person_id != snapshot.current_person_id:
            state.current_person_id = snapshot.current_person_id
        if snapshot.completed and not state.completed_at:
            state.completed_at = datetime.utcnow()
        db.session.flush()
        return state

    snapshot = management_turn_snapshot(
        vacation_year,
        people,
        selections_by_person,
        None,
        today=today,
    )
    now = datetime.utcnow()
    state = StaffingVacationManagementTurnState(
        vacation_year=vacation_year,
        area_unit_id=area.id,
        current_person_id=snapshot.current_person_id,
        started_at=now,
        completed_at=now if snapshot.completed else None,
        updated_at=now,
    )
    db.session.add(state)
    db.session.flush()
    return state


def _advance_management_turn(
    state,
    people,
    selections_by_person,
    person,
    outcome,
    user,
    now,
):
    existing = next(
        (
            resolution
            for resolution in state.resolutions
            if resolution.staffing_person_id == person.id
        ),
        None,
    )
    if not existing:
        existing = StaffingVacationManagementTurnResolution(
            staffing_person_id=person.id,
            outcome=outcome,
            resolved_by_user_id=getattr(user, "id", None),
            resolved_at=now,
        )
        state.resolutions.append(existing)
    resolved_ids = {
        resolution.staffing_person_id for resolution in state.resolutions
    }
    anchor = _person_sort_key(person)
    candidates = [
        candidate
        for candidate in _seniority_order(people)
        if candidate.id not in resolved_ids
        and _person_sort_key(candidate) > anchor
        and _management_remaining_for_person(
            candidate,
            state.vacation_year,
            selections_by_person.get(candidate.id, ()),
        )
        > 0
    ]
    state.current_person_id = candidates[0].id if candidates else None
    state.completed_at = None if candidates else now
    state.updated_at = now
    db.session.flush()
    return state


def _management_week_usage(vacation_year, week_endings, hierarchy):
    weeks = set(week_endings)
    selections = (
        StaffingVacationManagementSelection.query.filter(
            StaffingVacationManagementSelection.vacation_year == vacation_year,
            StaffingVacationManagementSelection.week_ending.in_(weeks),
            StaffingVacationManagementSelection.cancelled_at.is_(None),
        )
        .all()
    )
    primary, _secondary = _primary_and_secondary_assignments(
        _management_leadership_rows()
    )
    area_by_person = {}
    for person_id, assignment in primary.items():
        area = management_area_for_assignment(
            assignment.person,
            hierarchy["by_id"].get(assignment.unit_id),
            hierarchy,
        )
        if area:
            area_by_person[person_id] = area.id
    usage = {}
    for selection in selections:
        area_id = area_by_person.get(selection.staffing_person_id)
        if area_id:
            key = (area_id, selection.week_ending)
            usage[key] = usage.get(key, 0) + 1
    return usage


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
        key=_person_sort_key,
    )


def _person_sort_key(person):
    return (
        person.seniority_date,
        person.last_name.casefold(),
        person.first_name.casefold(),
        person.id,
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
