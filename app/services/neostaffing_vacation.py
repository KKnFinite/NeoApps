from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import calendar as calendar_module
import math

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingVacationManagementCapacity,
    StaffingVacationManagementChangeRequest,
    StaffingVacationManagementSelection,
    StaffingVacationManagementTurnResolution,
    StaffingVacationManagementTurnState,
    StaffingVacationManagementWeekOverride,
    StaffingVacationUnionCalendar,
    StaffingVacationUnionCalendarScope,
    StaffingVacationUnionCalendarShare,
    StaffingVacationUnionSelection,
    StaffingVacationWeekConversion,
    StaffingVacationDaySelection,
    StaffingVacationDayEntitlement,
    StaffingVacationHolidayRule,
    StaffingVacationQualifyingHoliday,
    StaffingWorkAssignment,
    User,
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
HOLIDAY_RULE_TYPES = frozenset({"fixed_date", "nth_weekday", "last_weekday"})
HOLIDAY_MONTH_CHOICES = tuple(
    (month, calendar_module.month_name[month]) for month in range(1, 13)
)
HOLIDAY_WEEKDAY_CHOICES = tuple(enumerate(calendar_module.day_name))
HOLIDAY_OCCURRENCE_CHOICES = (
    (1, "First"),
    (2, "Second"),
    (3, "Third"),
    (4, "Fourth"),
    (5, "Fifth"),
)
VACATION_UNION_ACTIVE_SELECTION_STATUSES = frozenset({"pending", "approved"})
VACATION_UNION_PENDING_ENTRY_CLASSIFICATIONS = frozenset({"part_time_supervisor"})
VACATION_UNION_DIRECT_ENTRY_CLASSIFICATIONS = frozenset(
    {"full_time_supervisor", "manager", "division_manager"}
)
VACATION_OFFICIAL_CALENDAR_OWNER_CLASSIFICATIONS = frozenset(
    {"full_time_supervisor", "manager", "division_manager"}
)
VACATION_VIEW_CALENDAR_OWNER_CLASSIFICATIONS = frozenset(
    {
        "part_time_supervisor",
        "full_time_supervisor",
        "full_time_specialist",
        "manager",
        "division_manager",
    }
)
VACATION_VIEW_CALENDAR_LIMIT = 5
VACATION_CALENDAR_TYPES = frozenset({"official", "view_only"})
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
VACATION_SPLIT_MANAGER_CLASSIFICATIONS = frozenset({"manager", "division_manager"})
VACATION_SPLIT_ADMIN_CLASSIFICATIONS = frozenset(
    {"full_time_supervisor", "manager", "division_manager"}
)
VACATION_DAY_ITEM_TYPES = frozenset(
    {"d_day", "optional_day", "anniversary_day", "floating_holiday"}
)
VACATION_AVAILABILITY_ITEM_TYPES = frozenset(
    {"special_assignment", "corporate_class"}
)
VACATION_PINNED_RECIPIENT_CLASSIFICATIONS = frozenset(
    {"full_time_supervisor", "manager", "division_manager"}
)
D_DAY_ENTITLEMENT = 5
OPTIONAL_DAY_ENTITLEMENT = 4
VACATION_MINIMUM_WRITABLE_APP_ROLE = "operator"


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
class UnionVacationEntitlement:
    regular_weeks: int
    optional_weeks: int = 1


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


def union_vacation_entitlement(seniority_date, vacation_year):
    """Return regular plus unsplit Optional Week entitlement for a Union person."""
    year = normalize_vacation_year(vacation_year)
    if not isinstance(seniority_date, date):
        raise ValueError("A valid seniority date is required.")
    completed_years = _completed_service_years(seniority_date, date(year, 12, 31))
    thresholds = ((30, 7), (25, 6), (20, 5), (15, 4), (8, 3), (3, 2), (1, 1))
    regular = next(
        (weeks for service_years, weeks in thresholds if completed_years >= service_years),
        0,
    )
    return UnionVacationEntitlement(regular_weeks=regular)


def d_day_cycle(value):
    """Return the non-carrying January 1 through December 31 D-Day cycle."""
    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    return date(day.year, 1, 1), date(day.year, 12, 31)


def optional_day_cycle(value):
    """Return the August 1 through July 31 Union Optional Day cycle."""
    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    start_year = day.year if day.month >= 8 else day.year - 1
    return date(start_year, 8, 1), date(start_year + 1, 7, 31)


def employee_anniversary_date(seniority_date, year):
    """Resolve the actual annual seniority anniversary, including leap-day service."""
    if not isinstance(seniority_date, date):
        raise ValueError("A valid seniority date is required.")
    year = normalize_vacation_year(year)
    try:
        return seniority_date.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def management_vacation_entitlement(seniority_date, vacation_year):
    """Return whole-week entitlement earned by the end of the vacation year."""
    year = normalize_vacation_year(vacation_year)
    if not isinstance(seniority_date, date):
        raise ValueError("A valid seniority date is required.")
    service_date = date(year, 12, 31)
    completed_years = _completed_service_years(seniority_date, service_date)
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


def require_vacation_mutation_access(user):
    """Reject read-only app roles before evaluating workflow-specific authority."""
    app_role = get_user_app_role(user, "neostaffing") or "watcher"
    if (
        getattr(user, "role", None) != "grandmaster"
        and ROLE_LEVELS.get(app_role, 0)
        < ROLE_LEVELS[VACATION_MINIMUM_WRITABLE_APP_ROLE]
    ):
        raise ValueError("NeoStaffing Watcher access is read-only.")
    return app_role


def _actor_has_vacation_mutation_access(actor):
    return bool(
        actor.is_grandmaster
        or ROLE_LEVELS.get(actor.app_role, 0)
        >= ROLE_LEVELS[VACATION_MINIMUM_WRITABLE_APP_ROLE]
    )


def can_edit_union_scope(actor, scope_ids):
    if not _actor_has_vacation_mutation_access(actor):
        return False
    requested = set(scope_ids)
    allowed = (
        actor.sideways_scope_ids
        if ROLE_LEVELS.get(actor.app_role, 0) >= ROLE_LEVELS["master"]
        else actor.normal_scope_ids
    )
    return bool(requested) and requested.issubset(allowed)


def can_edit_management_capacity(actor, area_id):
    return bool(
        _actor_has_vacation_mutation_access(actor)
        and (
            actor.is_grandmaster
            or int(area_id) in actor.management_capacity_ids
        )
    )


def operation_has_editable_union_scope(actor, operation_id, hierarchy):
    if not _actor_has_vacation_mutation_access(actor):
        return False
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
        StaffingVacationManagementSelection.query.options(
            joinedload(StaffingVacationManagementSelection.change_requests)
        )
        .filter_by(vacation_year=year)
        .filter(StaffingVacationManagementSelection.cancelled_at.is_(None))
        .order_by(
            StaffingVacationManagementSelection.week_ending,
            StaffingVacationManagementSelection.id,
        )
        .all()
    )
    pending_request_by_selection = {
        request.selection_id: request
        for selection in selections
        for request in selection.change_requests
        if request.status == "pending"
    }
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
    conversions = (
        StaffingVacationWeekConversion.query.options(
            selectinload(StaffingVacationWeekConversion.days)
        )
        .filter_by(vacation_year=year, program="management", recombined_at=None)
        .order_by(StaffingVacationWeekConversion.id)
        .all()
    )
    conversions_by_person = {}
    bank_usage_by_person = {
        person_id: list(rows) for person_id, rows in selections_by_person.items()
    }
    for conversion in conversions:
        conversions_by_person.setdefault(conversion.staffing_person_id, []).append(
            conversion
        )
        bank_usage_by_person.setdefault(conversion.staffing_person_id, []).append(
            conversion
        )
    day_rows_by_person, floating_by_person = _vacation_day_rows_for_year(year)

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
            # Division Managers are the pinned one-level-up availability for
            # Manager pools, not members of the lower-level capacity pool.
            if assignment.person.classification != "division_manager":
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
        assignment = primary_by_person.get(selection.staffing_person_id)
        if area and assignment.person.classification != "division_manager":
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
            bank_usage_by_person,
            state_by_area.get(area.id),
            today=today,
        )
        capacity = capacity_by_area.get(area.id)
        pinned_people = _management_pinned_people_for_area(
            area, primary_by_person, hierarchy
        )
        pinned_week_status = _pinned_week_statuses(
            pinned_people,
            weeks,
            selections_by_person,
            day_rows_by_person,
        )
        week_rows = []
        for week in weeks:
            used = used_by_area_week.get((area.id, week.week_ending), 0)
            pinned_statuses = pinned_week_status.get(week.week_ending, {})
            unavailable_count = len(pinned_statuses)
            reduced_on = week.week_ending not in off_weeks_by_area.get(area.id, set())
            limit = management_capacity_limit(
                capacity,
                unavailable_count,
                reduced_capacity_on=reduced_on,
            )
            week_rows.append(
                {
                    "week": week,
                    "used": used,
                    "limit": limit,
                    "full": limit is None or used >= limit,
                    "over": limit is not None and used > limit,
                    "pinned_unavailable_count": unavailable_count,
                }
            )
        person_rows = []
        can_manage_week_changes = _can_administer_management_week_changes(
            actor, area.id
        )
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
            person_conversions = conversions_by_person.get(person.id, [])
            remaining = max(0, remaining - len(person_conversions))
            owner_can_write = bool(
                _actor_has_vacation_mutation_access(actor)
                and (
                    actor.is_grandmaster
                    or (actor.person and actor.person.id == person.id)
                )
            )
            person_rows.append(
                {
                    "person": person,
                    "entitlement": entitlement,
                    "remaining": remaining,
                    "selections": selected,
                    "pending_request_by_selection": {
                        selection.id: pending_request_by_selection[selection.id]
                        for selection in selected
                        if selection.id in pending_request_by_selection
                    },
                    "future_selection_ids": {
                        selection.id
                        for selection in selected
                        if not _management_week_has_started(
                            selection.week_ending, today
                        )
                    },
                    "can_request_week_changes": bool(
                        _actor_has_vacation_mutation_access(actor)
                        and actor.person
                        and actor.person.id == person.id
                    ),
                    "can_manage_week_changes": can_manage_week_changes,
                    "split_conversions": [
                        {
                            "conversion": conversion,
                            "scheduled_days": [
                                day for day in conversion.days if day.status == "scheduled"
                            ],
                            "remaining_days": 5
                            - sum(1 for day in conversion.days if day.status == "scheduled"),
                        }
                        for conversion in person_conversions
                    ],
                    "split_day_balance": sum(
                        5 - sum(1 for day in conversion.days if day.status == "scheduled")
                        for conversion in person_conversions
                    ),
                    "day_items": day_rows_by_person.get(person.id, []),
                    "d_days_remaining": max(
                        0,
                        D_DAY_ENTITLEMENT
                        - sum(
                            row.item_type == "d_day"
                            for row in day_rows_by_person.get(person.id, ())
                        ),
                    ),
                    "anniversary_available": max(
                        0,
                        1
                        - sum(
                            row.item_type == "anniversary_day"
                            for row in day_rows_by_person.get(person.id, ())
                        ),
                    ),
                    "floating_available": _available_floating_entitlements(
                        floating_by_person.get(person.id, ()),
                        day_rows_by_person.get(person.id, ()),
                    ),
                    "can_split": _can_manage_split_days_for_area(
                        actor, area.id, manager_only=True
                    ),
                    "can_manage_split_days": _can_manage_split_days_for_area(
                        actor, area.id
                    ),
                    "can_manage_days": _can_manage_management_days_for_area(
                        actor, area.id
                    ),
                    "is_active_turn": person.id == turn.current_person_id,
                    "can_select": owner_can_write
                    and _turn_allows_person(turn, person, current_person)
                    and remaining > 0,
                    "can_pass": bool(
                        _actor_has_vacation_mutation_access(actor)
                        and actor.person
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
                "pinned_rows": [
                    {
                        "person": person,
                        "availability": _pinned_person_availability(
                            person,
                            selections_by_person,
                            day_rows_by_person,
                            today=today,
                        ),
                        "can_manage": _can_manage_management_days_for_area(
                            actor,
                            management_area_for_assignment(
                                person,
                                hierarchy["by_id"].get(
                                    primary_by_person[person.id].unit_id
                                ),
                                hierarchy,
                            ).id,
                        ),
                        "remaining": _management_remaining_for_person(
                            person,
                            year,
                            bank_usage_by_person.get(person.id, ()),
                        ),
                        "can_manage_vacation": bool(
                            person.classification == "division_manager"
                            and _can_manage_division_manager_vacation(
                                actor, area.id
                            )
                        ),
                        "can_add_vacation": bool(
                            person.classification == "division_manager"
                            and today >= vacation_selection_opens_on(year)
                            and _can_manage_division_manager_vacation(
                                actor, area.id
                            )
                        ),
                    }
                    for person in pinned_people
                ],
                "capacity": capacity,
                "week_rows": week_rows,
                "turn": turn,
                "can_admin_pass": bool(
                    turn.current_person_id
                    and _can_administer_management_turn(actor, area.id)
                ),
                "off_week_endings": off_weeks_by_area.get(area.id, set()),
                "can_edit": can_edit_management_capacity(actor, area.id),
                "over": any(row["over"] for row in week_rows),
            }
        )
    area_rows.sort(key=lambda row: _unit_sort_key(row["area"], hierarchy))
    default_area = (
        primary_area_by_person.get(actor.person.id) if actor.person else None
    )
    default_area_id = default_area.id if default_area else None
    if default_area_id:
        area_rows.sort(
            key=lambda row: (
                row["area"].id != default_area_id,
                _unit_sort_key(row["area"], hierarchy),
            )
        )
    return {
        "vacation_year": year,
        "weeks": weeks,
        "selection_opens_on": vacation_selection_opens_on(year),
        "areas": area_rows,
        "actor": actor,
        "today": today,
        "default_area_id": default_area_id,
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
    require_vacation_mutation_access(user)
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
    leadership_rows = _management_leadership_rows()
    primary_assignments, _secondary = _primary_and_secondary_assignments(
        leadership_rows
    )
    people = _management_people_for_area(
        area.id, hierarchy, leadership_rows=leadership_rows
    )
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
    _ensure_weeks_have_no_split_days(person.id, normalized_weeks)
    current_selections = all_person_selections.get(person.id, [])
    remaining = _management_remaining_for_person(person, year, current_selections)
    if len(normalized_weeks) > remaining:
        raise ValueError("The selected weeks exceed the remaining vacation bank.")

    usage = _management_week_usage(year, normalized_weeks, hierarchy)
    pinned_people = _management_pinned_people_for_area(
        area, primary_assignments, hierarchy
    )
    pinned_ids = [person.id for person in pinned_people]
    pinned_day_rows = {}
    if pinned_ids:
        for row in StaffingVacationDaySelection.query.filter(
            StaffingVacationDaySelection.staffing_person_id.in_(pinned_ids),
            StaffingVacationDaySelection.vacation_year == year,
            StaffingVacationDaySelection.status == "scheduled",
        ).all():
            pinned_day_rows.setdefault(row.staffing_person_id, []).append(row)
    pinned_statuses = _pinned_week_statuses(
        pinned_people,
        [
            VacationWeek(year, week - timedelta(days=6), week)
            for week in normalized_weeks
        ],
        all_person_selections,
        pinned_day_rows,
    )
    reduced_off = {
        row.week_ending
        for row in StaffingVacationManagementWeekOverride.query.filter(
            StaffingVacationManagementWeekOverride.vacation_year == year,
            StaffingVacationManagementWeekOverride.area_unit_id == area.id,
            StaffingVacationManagementWeekOverride.week_ending.in_(normalized_weeks),
        ).all()
    }
    for week in normalized_weeks:
        effective_limit = management_capacity_limit(
            capacity,
            len(pinned_statuses.get(week, {})),
            reduced_capacity_on=week not in reduced_off,
        )
        if usage.get((area.id, week), 0) >= effective_limit:
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

    _award_configured_floating_holidays(saved, "management")

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


def add_division_manager_weeks(
    person,
    vacation_year,
    week_endings,
    user,
    *,
    today=None,
):
    """Directly add Division Manager weeks without consuming a Manager pool slot."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    if today < vacation_selection_opens_on(year):
        raise ValueError("Management vacation selection has not opened yet.")
    normalized_weeks = sorted(
        {normalize_week_ending(year, value) for value in week_endings}
    )
    if not normalized_weeks:
        raise ValueError("Select at least one vacation week.")
    person = _management_person(person)
    if person.classification != "division_manager":
        raise ValueError("Select an active Division Manager.")
    hierarchy = vacation_hierarchy()
    area, actor = _authorize_management_week_change(person, user, hierarchy)
    if not _can_manage_division_manager_vacation(actor, area.id):
        raise ValueError(
            "Only an authorized Manager may change Division Manager vacation."
        )
    _lock_management_area(area.id)
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
    _ensure_weeks_have_no_split_days(person.id, normalized_weeks)
    current_selections = _management_active_selections_by_person(year).get(
        person.id, ()
    )
    remaining = _management_remaining_for_person(person, year, current_selections)
    if len(normalized_weeks) > remaining:
        raise ValueError("The selected weeks exceed the remaining vacation bank.")

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
    _award_configured_floating_holidays(saved, "management")
    return saved


def request_management_selection_change(
    selection,
    request_type,
    user,
    *,
    requested_week_ending=None,
    today=None,
):
    """Create one employee-owned pending move/cancel request without releasing capacity."""
    require_vacation_mutation_access(user)
    today = today or date.today()
    selection = _locked_management_selection(selection)
    person = _locked_management_person(selection.staffing_person_id)
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    if not actor.person or actor.person.id != person.id:
        raise ValueError("You may only request changes to your own vacation week.")
    _ensure_management_selection_is_active(selection)
    _ensure_management_week_not_started(selection.week_ending, today)
    request_type = str(request_type or "").strip().lower()
    if request_type not in {"move", "cancel"}:
        raise ValueError("Choose move or cancellation.")
    destination = None
    if request_type == "move":
        destination = normalize_week_ending(
            selection.vacation_year, requested_week_ending
        )
        _ensure_management_week_not_started(destination, today)
        if destination == selection.week_ending:
            raise ValueError("Choose a different destination week.")
        _ensure_person_has_no_active_management_week(
            person.id,
            selection.vacation_year,
            destination,
            exclude_selection_id=selection.id,
        )
    existing = (
        StaffingVacationManagementChangeRequest.query.filter_by(
            selection_id=selection.id,
            status="pending",
        )
        .with_for_update()
        .first()
    )
    if existing:
        raise ValueError("A change request is already pending for this vacation week.")
    now = datetime.utcnow()
    row = StaffingVacationManagementChangeRequest(
        selection_id=selection.id,
        request_type=request_type,
        requested_week_ending=destination,
        status="pending",
        requested_by_user_id=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def cancel_management_selection_change_request(change_request, user):
    require_vacation_mutation_access(user)
    row = _locked_management_change_request(change_request)
    if row.status != "pending":
        raise ValueError("This vacation change request is no longer pending.")
    selection = _locked_management_selection(row.selection_id)
    person = _locked_management_person(selection.staffing_person_id)
    actor = vacation_actor(user)
    if not actor.person or actor.person.id != person.id:
        raise ValueError("Only the requesting employee may cancel this request.")
    now = datetime.utcnow()
    _resolve_management_change_request(row, "cancelled", user, now)
    db.session.flush()
    return row


def review_management_selection_change_request(
    change_request,
    decision,
    user,
    *,
    capacity_override=False,
    today=None,
):
    require_vacation_mutation_access(user)
    today = today or date.today()
    row = _locked_management_change_request(change_request)
    if row.status != "pending":
        raise ValueError("This vacation change request is no longer pending.")
    selection = _locked_management_selection(row.selection_id)
    person = _locked_management_person(selection.staffing_person_id)
    hierarchy = vacation_hierarchy()
    area, actor = _authorize_management_week_change(person, user, hierarchy)
    _lock_management_area(area.id)
    _ensure_management_selection_is_active(selection)
    decision = str(decision or "").strip().lower()
    if decision not in {"approve", "deny"}:
        raise ValueError("Choose approve or deny.")
    now = datetime.utcnow()
    if decision == "deny":
        _resolve_management_change_request(row, "denied", user, now)
    else:
        _ensure_management_week_not_started(selection.week_ending, today)
    if decision == "approve" and row.request_type == "cancel":
        _cancel_management_selection_row(
            selection, user, now, reason="request_approved"
        )
        _resolve_management_change_request(row, "approved", user, now)
    elif decision == "approve":
        destination = normalize_week_ending(
            selection.vacation_year, row.requested_week_ending
        )
        _ensure_management_week_not_started(destination, today)
        _move_management_selection_row(
            selection,
            person,
            area,
            destination,
            actor,
            user,
            hierarchy,
            capacity_override=capacity_override,
            cancellation_reason="request_moved",
        )
        _resolve_management_change_request(row, "approved", user, now)
    db.session.flush()
    return row


def move_management_selection(
    selection,
    requested_week_ending,
    user,
    *,
    capacity_override=False,
    today=None,
):
    require_vacation_mutation_access(user)
    today = today or date.today()
    selection = _locked_management_selection(selection)
    person = _locked_management_person(selection.staffing_person_id)
    hierarchy = vacation_hierarchy()
    area, actor = _authorize_management_week_change(person, user, hierarchy)
    _lock_management_area(area.id)
    _ensure_management_selection_is_active(selection)
    _ensure_management_week_not_started(selection.week_ending, today)
    destination = normalize_week_ending(
        selection.vacation_year, requested_week_ending
    )
    _ensure_management_week_not_started(destination, today)
    if destination == selection.week_ending:
        raise ValueError("Choose a different destination week.")
    result = _move_management_selection_row(
        selection,
        person,
        area,
        destination,
        actor,
        user,
        hierarchy,
        capacity_override=capacity_override,
        cancellation_reason="direct_moved",
    )
    _cancel_pending_management_change_requests(selection.id, user)
    db.session.flush()
    return result


def cancel_management_selection(
    selection,
    user,
    *,
    correction=False,
    today=None,
):
    require_vacation_mutation_access(user)
    today = today or date.today()
    selection = _locked_management_selection(selection)
    person = _locked_management_person(selection.staffing_person_id)
    hierarchy = vacation_hierarchy()
    area, _actor = _authorize_management_week_change(person, user, hierarchy)
    _lock_management_area(area.id)
    _ensure_management_selection_is_active(selection)
    started = _management_week_has_started(selection.week_ending, today)
    if started and not _boolean(correction):
        raise ValueError("Past or already-started weeks require correction removal.")
    now = datetime.utcnow()
    _cancel_management_selection_row(
        selection,
        user,
        now,
        reason="past_correction" if started else "direct_cancelled",
    )
    _cancel_pending_management_change_requests(selection.id, user, now=now)
    db.session.flush()
    return selection


def pass_management_turn(
    vacation_year,
    area_id,
    person,
    user,
    *,
    administrative=False,
    today=None,
):
    require_vacation_mutation_access(user)
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
    if cancelled:
        _reconcile_selection_floating_holidays(cancelled, "management")
    return {"advanced_turns": len(states), "cancelled_future_picks": len(cancelled)}


def save_management_capacity(vacation_year, area_id, values, user):
    require_vacation_mutation_access(user)
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
    require_vacation_mutation_access(user)
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
    require_vacation_mutation_access(user)
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


def management_capacity_limit(
    capacity,
    pinned_unavailable_count,
    *,
    reduced_capacity_on=True,
):
    """Resolve one week's limit from yearly settings and derived availability."""
    if not capacity:
        return None
    unavailable = _nonnegative_int(
        pinned_unavailable_count, "Pinned unavailable count"
    )
    if not reduced_capacity_on or unavailable == 0:
        return capacity.normal_limit
    if unavailable == 1:
        return capacity.one_pinned_limit
    return capacity.two_plus_pinned_limit


def create_union_calendar(values, user):
    return _save_union_calendar(None, values, user)


def update_union_calendar(calendar, values, user):
    if not calendar:
        raise ValueError("The selected Union vacation calendar was not found.")
    return _save_union_calendar(calendar, values, user)


def _save_union_calendar(calendar, values, user):
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(values.get("vacation_year"))
    calendar_type = str(
        values.get("calendar_type")
        or getattr(calendar, "calendar_type", None)
        or "official"
    ).strip().casefold()
    if calendar_type not in VACATION_CALENDAR_TYPES:
        raise ValueError("Choose Official or View Only calendar type.")
    if calendar and calendar.calendar_type != calendar_type:
        raise ValueError("Calendar type cannot be changed after creation.")
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
    scope_ids = _highest_union_scope_ids(scope_ids, operation.id, hierarchy)
    actor = vacation_actor(user, hierarchy)
    if calendar:
        resolve_union_calendar_owner(calendar, persist=True)
    if calendar_type == "official":
        if not _can_manage_official_calendar(actor, scope_ids):
            raise ValueError(
                "Only an authorized FT Supervisor or Manager may configure an Official calendar."
            )
        StaffingUnit.query.filter_by(id=operation.id).with_for_update().first()
        conflicts = official_calendar_overlap_conflicts(
            year,
            scope_ids,
            include_pt,
            include_ft,
            exclude_calendar_id=getattr(calendar, "id", None),
            hierarchy=hierarchy,
        )
        if conflicts:
            summary = "; ".join(
                f"{row['calendar_name']} [{row['scope_label']}]: "
                f"{', '.join(row['employee_labels'][:5])}; areas: "
                f"{', '.join(row['area_labels']) or 'configured scope'}"
                for row in conflicts
            )
            raise ValueError(
                "Employees may belong to only one Official editable calendar per year. "
                f"Conflicts: {summary}"
            )
        name = generated_official_calendar_name(
            scope_ids, include_pt, include_ft, hierarchy
        )
    else:
        if calendar:
            if not _can_edit_view_calendar(calendar, user):
                raise ValueError("Only the owner may edit this View Only calendar.")
        else:
            if not _can_own_view_calendar(actor):
                raise ValueError("An active NeoStaffing management user is required.")
            owned_count = StaffingVacationUnionCalendar.query.filter_by(
                owner_user_id=getattr(user, "id", None),
                calendar_type="view_only",
            ).count()
            if owned_count >= VACATION_VIEW_CALENDAR_LIMIT:
                raise ValueError("You may create up to 5 personal View Only calendars.")
        if not actor.is_grandmaster and not can_edit_union_scope(actor, scope_ids):
            raise ValueError("You do not have authority to configure this View Only scope.")
        name = str(values.get("name") or "").strip()
        if not name:
            raise ValueError("A custom View Only calendar name is required.")
        if len(name) > 140:
            raise ValueError("View Only calendar name must be 140 characters or fewer.")

    if not calendar:
        calendar = StaffingVacationUnionCalendar(
            created_by_user_id=getattr(user, "id", None),
            owner_user_id=getattr(user, "id", None),
            calendar_type=calendar_type,
        )
        db.session.add(calendar)
    elif calendar.owner_user_id is None:
        calendar.owner_user_id = getattr(calendar, "created_by_user_id", None)
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


def delete_union_calendar(calendar, user):
    """Delete one definition without touching employee/year selections."""
    require_vacation_mutation_access(user)
    row = _locked_union_calendar_definition(calendar)
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    scope_ids = {scope.staffing_unit_id for scope in row.scopes}
    if row.calendar_type == "official":
        if not _can_manage_official_calendar(actor, scope_ids):
            raise ValueError("You do not have authority to delete this Official calendar.")
    elif not _can_edit_view_calendar(row, user):
        raise ValueError("Only the owner may delete this View Only calendar.")
    db.session.delete(row)
    db.session.flush()


def can_edit_union_calendar(calendar, user):
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    if calendar.calendar_type == "official":
        return _can_manage_official_calendar(
            actor, {scope.staffing_unit_id for scope in calendar.scopes}
        )
    return _can_edit_view_calendar(calendar, user)


def can_create_union_calendar_type(calendar_type, user):
    actor = vacation_actor(user)
    if calendar_type == "official":
        return bool(
            actor.is_grandmaster
            or (
                actor.person
                and actor.person.classification
                in VACATION_OFFICIAL_CALENDAR_OWNER_CLASSIFICATIONS
                and actor.sideways_scope_ids
            )
        )
    if calendar_type == "view_only":
        return bool(
            _can_own_view_calendar(actor)
            and StaffingVacationUnionCalendar.query.filter_by(
                owner_user_id=getattr(user, "id", None),
                calendar_type="view_only",
            ).count()
            < VACATION_VIEW_CALENDAR_LIMIT
        )
    return False


def generated_official_calendar_name(scope_ids, include_pt, include_ft, hierarchy=None):
    hierarchy = hierarchy or vacation_hierarchy()
    units = _ordered_scope_units(scope_ids, hierarchy)
    labels = [unit.name for unit in units]
    visible = labels[:3]
    if len(labels) > 3:
        visible.append(f"+{len(labels) - 3} more")
    scope_label = ", ".join(visible) or "Unscoped"
    classification = "PT" if include_pt and not include_ft else "FT" if include_ft and not include_pt else None
    prefix = "Hourly Vacation Calendar"
    if classification:
        prefix += f" - {classification}"
    return f"{prefix} - {scope_label}"[:140]


def official_calendar_overlap_conflicts(
    vacation_year,
    scope_ids,
    include_pt,
    include_ft,
    *,
    exclude_calendar_id=None,
    hierarchy=None,
):
    """Return bounded, employee-specific Official overlap details."""
    year = normalize_vacation_year(vacation_year)
    hierarchy = hierarchy or vacation_hierarchy()
    calendars = StaffingVacationUnionCalendar.query.options(
        selectinload(StaffingVacationUnionCalendar.scopes)
    ).filter_by(vacation_year=year, calendar_type="official").all()
    calendars = [row for row in calendars if row.id != exclude_calendar_id]
    proposed = _membership_definition(scope_ids, include_pt, include_ft, hierarchy)
    definitions = {
        row.id: _membership_definition(
            {scope.staffing_unit_id for scope in row.scopes},
            row.include_part_time,
            row.include_full_time,
            hierarchy,
        )
        for row in calendars
    }
    people = _active_union_people_with_assignments()
    proposed_people = {
        person.id: person
        for person, assignment in people
        if _membership_matches(person, assignment, proposed)
    }
    conflicts = []
    for row in calendars:
        member_ids = {
            person.id
            for person, assignment in people
            if _membership_matches(person, assignment, definitions[row.id])
        }
        overlapping_person_ids = proposed_people.keys() & member_ids
        overlapping = _seniority_order(
            [proposed_people[person_id] for person_id in overlapping_person_ids]
        )
        if not overlapping:
            continue
        conflicting_area_ids = {
            assignment.work_area_unit_id
            for person, assignment in people
            if person.id in overlapping_person_ids
            and (
                _membership_matches(person, assignment, proposed)
                or _membership_matches(person, assignment, definitions[row.id])
            )
        }
        conflicts.append(
            {
                "calendar_id": row.id,
                "calendar_name": row.name,
                "scope_label": union_calendar_scope_label(row, hierarchy),
                "area_labels": [
                    hierarchy["by_id"][area_id].name
                    for area_id in sorted(conflicting_area_ids)
                    if area_id in hierarchy["by_id"]
                ],
                "employee_ids": [person.id for person in overlapping],
                "employee_labels": [
                    f"{person.employee_id} {person.last_name}, {person.first_name}"
                    for person in overlapping
                ],
            }
        )
    return conflicts


def update_view_calendar_shares(calendar, recipient_user_ids, user):
    require_vacation_mutation_access(user)
    row = _locked_union_calendar_definition(calendar)
    if row.calendar_type != "view_only":
        raise ValueError("Only View Only calendars can be shared.")
    if not _can_edit_view_calendar(row, user):
        raise ValueError("Only the owner may share this View Only calendar.")
    recipient_ids = {
        _positive_int(value, "share recipient") for value in recipient_user_ids or ()
    }
    recipient_ids.discard(row.owner_user_id)
    eligible = {item["user"].id: item for item in management_calendar_users()}
    invalid = recipient_ids - eligible.keys()
    if invalid:
        raise ValueError("Every share recipient must be an active management user.")
    existing = {share.recipient_user_id: share for share in row.shares}
    for recipient_id, share in existing.items():
        if recipient_id not in recipient_ids:
            db.session.delete(share)
    for recipient_id in sorted(recipient_ids - existing.keys()):
        row.shares.append(
            StaffingVacationUnionCalendarShare(
                recipient_user_id=recipient_id,
                shared_by_user_id=getattr(user, "id", None),
            )
        )
    db.session.flush()
    return row


def search_management_calendar_users(search, *, exclude_user_id=None, limit=30):
    term = str(search or "").strip().casefold()
    rows = management_calendar_users()
    if term:
        rows = [
            row
            for row in rows
            if term
            in str(
                getattr(row["person"], "employee_id", None)
                or row["user"].employee_id
                or ""
            ).casefold()
            or term
            in str(
                getattr(row["person"], "first_name", None)
                or row["user"].first_name
                or ""
            ).casefold()
            or term
            in str(
                getattr(row["person"], "last_name", None)
                or row["user"].last_name
                or ""
            ).casefold()
        ]
    return [
        row for row in rows if row["user"].id != exclude_user_id
    ][: max(1, min(int(limit), 50))]


def copy_shared_view_calendar(calendar, name, user):
    require_vacation_mutation_access(user)
    source = _locked_union_calendar_definition(calendar)
    if source.calendar_type != "view_only" or not can_view_union_calendar(source, user):
        raise ValueError("The shared View Only calendar is not available.")
    if source.owner_user_id == getattr(user, "id", None):
        raise ValueError("Copy is intended for a calendar shared with you.")
    values = {
        "vacation_year": source.vacation_year,
        "calendar_type": "view_only",
        "name": name,
        "operation_unit_id": source.operation_unit_id,
        "include_part_time": source.include_part_time,
        "include_full_time": source.include_full_time,
        "staffing_unit_ids": [scope.staffing_unit_id for scope in source.scopes],
        "active": source.active,
    }
    return create_union_calendar(values, user)


def can_view_union_calendar(calendar, user):
    if calendar.calendar_type == "official":
        return True
    user_id = getattr(user, "id", None)
    return bool(
        getattr(user, "role", None) == "grandmaster"
        or calendar.owner_user_id == user_id
        or any(share.recipient_user_id == user_id for share in calendar.shares)
    )


def view_union_calendar_context(calendar, user):
    row = StaffingVacationUnionCalendar.query.options(
        selectinload(StaffingVacationUnionCalendar.scopes),
        selectinload(StaffingVacationUnionCalendar.shares),
        joinedload(StaffingVacationUnionCalendar.owner),
    ).filter_by(id=_positive_int(getattr(calendar, "id", calendar), "calendar")).first()
    owner = resolve_union_calendar_owner(row) if row else None
    if not row or row.calendar_type != "view_only" or not (
        can_view_union_calendar(row, user)
        or getattr(owner, "id", None) == getattr(user, "id", None)
    ):
        raise ValueError("The View Only calendar is not available.")
    hierarchy = vacation_hierarchy()
    members = _members_by_calendar([row], hierarchy).get(row.id, [])
    return {
        "calendar": row,
        "owner": owner,
        "scope_label": union_calendar_scope_label(row, hierarchy),
        "members": members,
        "can_edit": _can_edit_view_calendar(row, user),
    }


def resolve_union_calendar_owner(
    calendar, *, persist=False, candidates=None, hierarchy=None
):
    """Resolve deterministic ownership fallback without requiring a GET write."""
    hierarchy = hierarchy or vacation_hierarchy()
    scope_ids = {scope.staffing_unit_id for scope in calendar.scopes}
    if candidates is None:
        candidates = management_calendar_users(hierarchy=hierarchy)
    by_user_id = {row["user"].id: row for row in candidates}
    current = by_user_id.get(
        calendar.owner_user_id or calendar.created_by_user_id
    )
    if current and _calendar_owner_candidate_allowed(calendar, current, scope_ids):
        return current["user"]
    scoped = [
        row
        for row in candidates
        if not row["actor"].is_grandmaster
        and _calendar_owner_candidate_allowed(calendar, row, scope_ids)
    ]
    grandmasters = [row for row in candidates if row["actor"].is_grandmaster]
    ordered = sorted(scoped, key=_calendar_owner_priority) or sorted(
        grandmasters, key=_grandmaster_owner_priority
    )
    owner = ordered[0]["user"] if ordered else None
    if persist and calendar.owner_user_id != getattr(owner, "id", None):
        calendar.owner_user_id = getattr(owner, "id", None)
        calendar.updated_at = datetime.utcnow()
        db.session.flush()
    return owner


def reconcile_union_calendar_owners(vacation_year=None):
    """Persist deterministic replacement owners in one bounded request-driven pass."""
    hierarchy = vacation_hierarchy()
    query = StaffingVacationUnionCalendar.query.options(
        selectinload(StaffingVacationUnionCalendar.scopes),
    )
    if vacation_year is not None:
        query = query.filter_by(vacation_year=normalize_vacation_year(vacation_year))
    calendars = query.order_by(StaffingVacationUnionCalendar.id).all()
    candidates = management_calendar_users(hierarchy=hierarchy) if calendars else []
    changed = []
    for calendar in calendars:
        previous_owner_id = calendar.owner_user_id
        owner = resolve_union_calendar_owner(
            calendar,
            persist=True,
            candidates=candidates,
            hierarchy=hierarchy,
        )
        if previous_owner_id != getattr(owner, "id", None):
            changed.append(calendar)
    return changed


def official_calendar_carry_forward_candidates(user, today=None):
    """Return owned current-year Official calendars eligible for a Nov 1 copy."""
    today = _as_date(today or date.today())
    if today < date(today.year, 11, 1):
        return []
    source_year = today.year
    target_year = source_year + 1
    hierarchy = vacation_hierarchy()
    sources = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
        )
        .filter_by(
            vacation_year=source_year,
            calendar_type="official",
            active=True,
        )
        .order_by(StaffingVacationUnionCalendar.id)
        .all()
    )
    targets = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
        )
        .filter_by(vacation_year=target_year, calendar_type="official")
        .order_by(StaffingVacationUnionCalendar.id)
        .all()
    )
    candidates = management_calendar_users(hierarchy=hierarchy) if sources else []
    actor = vacation_actor(user, hierarchy)
    user_id = getattr(user, "id", None)
    target_keys = {_official_calendar_definition_key(row) for row in targets}
    result = []
    for source in sources:
        owner = resolve_union_calendar_owner(
            source,
            candidates=candidates,
            hierarchy=hierarchy,
        )
        if not actor.is_grandmaster and getattr(owner, "id", None) != user_id:
            continue
        if _official_calendar_definition_key(source) in target_keys:
            continue
        result.append(
            {
                "calendar": source,
                "owner": owner,
                "target_year": target_year,
                "display_name": generated_official_calendar_name(
                    {scope.staffing_unit_id for scope in source.scopes},
                    source.include_part_time,
                    source.include_full_time,
                    hierarchy,
                ),
                "scope_label": union_calendar_scope_label(source, hierarchy),
            }
        )
    return result


def carry_forward_official_calendar(calendar, user, today=None):
    """Create one next-year Official definition without copying transactional state."""
    require_vacation_mutation_access(user)
    today = _as_date(today or date.today())
    if today < date(today.year, 11, 1):
        raise ValueError("Official calendar carry-forward opens November 1.")
    source = _locked_union_calendar_definition(calendar)
    if (
        source.calendar_type != "official"
        or not source.active
        or source.vacation_year != today.year
    ):
        raise ValueError("Choose an active current-year Official calendar.")
    hierarchy = vacation_hierarchy()
    owner = resolve_union_calendar_owner(source, persist=True, hierarchy=hierarchy)
    actor = vacation_actor(user, hierarchy)
    if not actor.is_grandmaster and getattr(owner, "id", None) != getattr(user, "id", None):
        raise ValueError("Only the calendar owner may carry this calendar forward.")
    target_year = today.year + 1
    target_rows = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
        )
        .filter_by(vacation_year=target_year, calendar_type="official")
        .with_for_update()
        .all()
    )
    source_key = _official_calendar_definition_key(source)
    if any(_official_calendar_definition_key(row) == source_key for row in target_rows):
        raise ValueError("This Official calendar was already carried forward.")
    return create_union_calendar(
        {
            "vacation_year": target_year,
            "calendar_type": "official",
            "operation_unit_id": source.operation_unit_id,
            "include_part_time": source.include_part_time,
            "include_full_time": source.include_full_time,
            "staffing_unit_ids": [
                scope.staffing_unit_id for scope in source.scopes
            ],
            "active": True,
        },
        user,
    )


def management_calendar_users(*, hierarchy=None):
    """Load eligible management accounts, people, access, and authority in bounded reads."""
    hierarchy = hierarchy or vacation_hierarchy()
    users = User.query.filter_by(is_active=True).all()
    people = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        StaffingPerson.employee_status == "active",
        StaffingPerson.classification.in_(VACATION_VIEW_CALENDAR_OWNER_CLASSIFICATIONS),
    ).all()
    access_rows = PortalAppAccess.query.filter_by(
        app_code="neostaffing", status="approved", is_active=True
    ).all()
    leadership = StaffingLeadershipAssignment.query.filter_by(active=True).all()
    person_by_employee = {
        str(person.employee_id or "").casefold(): person for person in people
    }
    access_by_user = {row.user_id: row for row in access_rows}
    leadership_by_person = {}
    for row in leadership:
        leadership_by_person.setdefault(row.person_id, []).append(row)
    result = []
    for user in users:
        access = access_by_user.get(user.id)
        app_role = access.role if access else None
        is_grandmaster = user.role == "grandmaster" or app_role == "grandmaster"
        person = person_by_employee.get(str(user.employee_id or "").casefold())
        if not is_grandmaster and (not access or not person):
            continue
        actor = _loaded_vacation_actor(
            user,
            app_role or "watcher",
            person,
            leadership_by_person.get(getattr(person, "id", None), []),
            hierarchy,
            is_grandmaster=is_grandmaster,
        )
        result.append({"user": user, "person": person, "actor": actor})
    return result


def union_calendar_admin_context(user):
    actor = vacation_actor(user)
    if not actor.is_grandmaster:
        raise ValueError("Grandmaster access is required for calendar administration.")
    calendars = StaffingVacationUnionCalendar.query.options(
        selectinload(StaffingVacationUnionCalendar.scopes),
        selectinload(StaffingVacationUnionCalendar.shares),
    ).order_by(
        StaffingVacationUnionCalendar.vacation_year.desc(),
        StaffingVacationUnionCalendar.calendar_type,
        func.lower(StaffingVacationUnionCalendar.name),
    ).all()
    hierarchy = vacation_hierarchy()
    owner_candidates = management_calendar_users(hierarchy=hierarchy)
    return {
        "calendars": [
            {
                "calendar": calendar,
                "display_name": (
                    generated_official_calendar_name(
                        {scope.staffing_unit_id for scope in calendar.scopes},
                        calendar.include_part_time,
                        calendar.include_full_time,
                        hierarchy,
                    )
                    if calendar.calendar_type == "official"
                    else calendar.name
                ),
                "owner": resolve_union_calendar_owner(
                    calendar,
                    candidates=owner_candidates,
                    hierarchy=hierarchy,
                ),
                "scope_label": union_calendar_scope_label(calendar, hierarchy),
            }
            for calendar in calendars
        ]
    }


def reset_union_vacation_calendar(calendar, vacation_year, user):
    """Reset one Official pool/year while preserving its durable definition."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    actor = vacation_actor(user)
    if not actor.is_grandmaster:
        raise ValueError("Grandmaster access is required to reset a calendar.")
    row = _locked_union_calendar_definition(calendar)
    if row.calendar_type != "official" or row.vacation_year != year:
        raise ValueError("Choose an Official calendar for the selected year.")
    person_ids = {person.id for person in union_calendar_members(row)}
    return _reset_vacation_person_year_state(
        person_ids,
        year,
        program="union",
    )


def reset_management_vacation_area(area, vacation_year, user):
    """Reset one dynamic Management area/year to its derived fresh state."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    actor = vacation_actor(user)
    if not actor.is_grandmaster:
        raise ValueError("Grandmaster access is required to reset a calendar.")
    area_id = _positive_int(getattr(area, "id", area), "Management area")
    hierarchy = vacation_hierarchy()
    area_row = (
        StaffingUnit.query.filter(
            StaffingUnit.id == area_id,
            StaffingUnit.unit_type.in_(VACATION_MANAGEMENT_AREA_TYPES),
        )
        .with_for_update()
        .first()
    )
    if not area_row:
        raise ValueError("The selected Management vacation area was not found.")
    leadership_rows = _management_leadership_rows()
    person_ids = {
        person.id
        for person in _management_people_for_area(
            area_row.id,
            hierarchy,
            leadership_rows=leadership_rows,
        )
    }
    result = _reset_vacation_person_year_state(
        person_ids,
        year,
        program="management",
    )
    state_ids = [
        state_id
        for (state_id,) in db.session.query(StaffingVacationManagementTurnState.id)
        .filter_by(vacation_year=year, area_unit_id=area_row.id)
        .all()
    ]
    result["turn_resolutions"] = _bulk_delete(
        StaffingVacationManagementTurnResolution,
        StaffingVacationManagementTurnResolution.turn_state_id.in_(
            state_ids or {-1}
        ),
    )
    result["turn_states"] = _bulk_delete(
        StaffingVacationManagementTurnState,
        StaffingVacationManagementTurnState.id.in_(state_ids or {-1}),
    )
    result["week_overrides"] = _bulk_delete(
        StaffingVacationManagementWeekOverride,
        StaffingVacationManagementWeekOverride.vacation_year == year,
        StaffingVacationManagementWeekOverride.area_unit_id == area_row.id,
    )
    db.session.flush()
    return result


def _reset_vacation_person_year_state(
    person_ids,
    vacation_year,
    *,
    program,
):
    """Target durable transaction rows for one resolved pool; caller owns commit."""
    person_ids = set(person_ids)
    ids = person_ids or {-1}
    result = {}
    conversion_ids = [
        conversion_id
        for (conversion_id,) in db.session.query(StaffingVacationWeekConversion.id)
        .filter(
            StaffingVacationWeekConversion.vacation_year == vacation_year,
            StaffingVacationWeekConversion.staffing_person_id.in_(ids),
            StaffingVacationWeekConversion.program == program,
        )
        .all()
    ]
    entitlement_ids = [
        entitlement_id
        for (entitlement_id,) in db.session.query(StaffingVacationDayEntitlement.id)
        .filter(
            StaffingVacationDayEntitlement.vacation_year == vacation_year,
            StaffingVacationDayEntitlement.staffing_person_id.in_(ids),
            StaffingVacationDayEntitlement.source_program == program,
        )
        .all()
    ]
    direct_item_types = (
        {"optional_day", "anniversary_day"}
        if program == "union"
        else {
            "d_day",
            "anniversary_day",
            "special_assignment",
            "corporate_class",
        }
    )
    result["day_selections"] = _bulk_delete(
        StaffingVacationDaySelection,
        StaffingVacationDaySelection.vacation_year == vacation_year,
        StaffingVacationDaySelection.staffing_person_id.in_(ids),
        or_(
            StaffingVacationDaySelection.conversion_id.in_(conversion_ids or {-1}),
            StaffingVacationDaySelection.entitlement_id.in_(entitlement_ids or {-1}),
            StaffingVacationDaySelection.item_type.in_(direct_item_types),
        ),
    )
    result["day_entitlements"] = _bulk_delete(
        StaffingVacationDayEntitlement,
        StaffingVacationDayEntitlement.vacation_year == vacation_year,
        StaffingVacationDayEntitlement.staffing_person_id.in_(ids),
        StaffingVacationDayEntitlement.source_program == program,
    )
    result["week_conversions"] = _bulk_delete(
        StaffingVacationWeekConversion,
        StaffingVacationWeekConversion.id.in_(conversion_ids or {-1}),
    )
    if program == "union":
        result["union_selections"] = _bulk_delete(
            StaffingVacationUnionSelection,
            StaffingVacationUnionSelection.vacation_year == vacation_year,
            StaffingVacationUnionSelection.staffing_person_id.in_(ids),
        )
    else:
        selection_ids = [
            selection_id
            for (selection_id,) in db.session.query(
                StaffingVacationManagementSelection.id
            )
            .filter(
                StaffingVacationManagementSelection.vacation_year
                == vacation_year,
                StaffingVacationManagementSelection.staffing_person_id.in_(ids),
            )
            .all()
        ]
        result["change_requests"] = _bulk_delete(
            StaffingVacationManagementChangeRequest,
            StaffingVacationManagementChangeRequest.selection_id.in_(
                selection_ids or {-1}
            ),
        )
        result["management_selections"] = _bulk_delete(
            StaffingVacationManagementSelection,
            StaffingVacationManagementSelection.id.in_(selection_ids or {-1}),
        )
    db.session.flush()
    return result


def _bulk_delete(model, *criteria):
    return model.query.filter(*criteria).delete(synchronize_session=False)


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


def add_union_week(
    calendar,
    person,
    vacation_year,
    week_ending,
    bank_type,
    user,
    *,
    capacity_override=False,
):
    """Atomically reserve one Official-pool whole week from a Union bank."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    week = normalize_week_ending(year, week_ending)
    bank_type = str(bank_type or "").strip().casefold()
    if bank_type not in {"regular", "optional"}:
        raise ValueError("Choose Regular or Optional Week bank.")
    calendar_id = getattr(calendar, "id", calendar)
    locked_calendar = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes)
        )
        .filter_by(id=_positive_int(calendar_id, "Union calendar"), active=True)
        .with_for_update()
        .first()
    )
    if not locked_calendar or locked_calendar.vacation_year != year:
        raise ValueError("The selected Official Union calendar is not available.")
    person = _locked_union_person(person)
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    entry_status = _union_actor_entry_status(actor, locked_calendar)
    if not entry_status:
        raise ValueError("You do not have authority to enter this Union vacation pick.")
    if _boolean(capacity_override) and entry_status != "approved":
        raise ValueError("PT Supervisors cannot override Union vacation capacity.")

    pool = _union_pool_data(year, hierarchy=hierarchy)
    if pool["official_calendar_by_person"].get(person.id) != locked_calendar.id:
        raise ValueError("This is not the employee's Official Union vacation calendar.")
    active_selections = pool["active_selections_by_person"].get(person.id, ())
    entitlement = union_vacation_entitlement(person.seniority_date, year)
    used_bank = sum(1 for row in active_selections if row.bank_type == bank_type)
    if bank_type == "optional":
        used_bank += _active_conversion_count(person.id, year, "union")
    available_bank = (
        entitlement.regular_weeks
        if bank_type == "regular"
        else entitlement.optional_weeks
    )
    if used_bank >= available_bank:
        label = "regular-week" if bank_type == "regular" else "Optional Week"
        raise ValueError(f"The employee has no remaining {label} bank.")

    existing = (
        StaffingVacationUnionSelection.query.filter_by(
            staffing_person_id=person.id,
            vacation_year=year,
            week_ending=week,
        )
        .with_for_update()
        .first()
    )
    if existing and existing.status in VACATION_UNION_ACTIVE_SELECTION_STATUSES:
        raise ValueError("The employee already has a selection for this week.")
    _ensure_weeks_have_no_split_days(person.id, [week])

    capacity = union_whole_week_capacity(
        len(pool["official_members_by_calendar"].get(locked_calendar.id, ())),
        year,
        week,
    )
    used = pool["usage_by_calendar_week"].get((locked_calendar.id, week), 0)
    if used >= capacity.capacity and not _boolean(capacity_override):
        raise ValueError("Union vacation capacity is full; an authorized one-time override is required.")

    now = datetime.utcnow()
    if existing:
        selection = existing
        selection.bank_type = bank_type
        selection.status = entry_status
        selection.entered_by_user_id = getattr(user, "id", None)
        selection.reviewed_by_user_id = (
            getattr(user, "id", None) if entry_status == "approved" else None
        )
        selection.reviewed_at = now if entry_status == "approved" else None
        selection.cancelled_by_user_id = None
        selection.cancelled_at = None
        selection.updated_at = now
    else:
        selection = StaffingVacationUnionSelection(
            staffing_person_id=person.id,
            vacation_year=year,
            week_ending=week,
            bank_type=bank_type,
            status=entry_status,
            entered_by_user_id=getattr(user, "id", None),
            reviewed_by_user_id=(
                getattr(user, "id", None) if entry_status == "approved" else None
            ),
            reviewed_at=now if entry_status == "approved" else None,
            created_at=now,
            updated_at=now,
        )
        db.session.add(selection)
    db.session.flush()
    if entry_status == "approved":
        _award_configured_floating_holidays([selection], "union")
    return selection


def split_management_week(
    person,
    vacation_year,
    user,
    *,
    selection=None,
    today=None,
):
    """Convert one Management week (selected future or unused bank) into five days."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    person = _locked_management_person(person)
    hierarchy = vacation_hierarchy()
    area = management_primary_area(person, hierarchy)
    if not area:
        raise ValueError("The selected person does not have a primary Management area.")
    actor = vacation_actor(user, hierarchy)
    if not _can_manage_split_days(actor, person, "management", manager_only=True):
        raise ValueError("Only an authorized Manager may split Management vacation weeks.")
    _lock_management_area(area.id)
    source = None
    if selection:
        source = (
            StaffingVacationManagementSelection.query.filter_by(
                id=_positive_int(getattr(selection, "id", selection), "selection"),
                staffing_person_id=person.id,
                vacation_year=year,
                cancelled_at=None,
            )
            .with_for_update()
            .first()
        )
        if not source:
            raise ValueError("The Management vacation week is no longer available to split.")
        if source.week_ending - timedelta(days=6) <= today:
            raise ValueError("A started or past Management vacation week cannot be split.")
    else:
        bank_usage = _management_active_selections_by_person(year).get(person.id, ())
        if _management_remaining_for_person(person, year, bank_usage) <= 0:
            raise ValueError("The employee has no unused Management vacation week to split.")

    now = datetime.utcnow()
    if source:
        source.cancelled_at = now
        source.cancelled_by_user_id = getattr(user, "id", None)
        source.cancellation_reason = "split"
        source.updated_at = now
    conversion = StaffingVacationWeekConversion(
        staffing_person_id=person.id,
        vacation_year=year,
        program="management",
        source_management_selection_id=source.id if source else None,
        converted_by_user_id=getattr(user, "id", None),
        converted_at=now,
        created_at=now,
        updated_at=now,
    )
    db.session.add(conversion)
    db.session.flush()
    if source:
        _reconcile_selection_floating_holidays([source], "management")
    return conversion


def split_union_optional_week(
    calendar,
    person,
    vacation_year,
    user,
    *,
    selection=None,
    today=None,
):
    """Convert only one Union Optional Week into five split vacation days."""
    require_vacation_mutation_access(user)
    year = normalize_vacation_year(vacation_year)
    today = today or date.today()
    calendar = _locked_union_calendar(calendar, year)
    person = _locked_union_person(person)
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    if _union_actor_entry_status(actor, calendar) != "approved":
        raise ValueError("Only an authorized FT Supervisor or Manager may split the Optional Week.")
    pool = _union_pool_data(year, hierarchy=hierarchy)
    if pool["official_calendar_by_person"].get(person.id) != calendar.id:
        raise ValueError("This is not the employee's Official Union vacation calendar.")
    source = None
    if selection:
        source = (
            StaffingVacationUnionSelection.query.filter(
                StaffingVacationUnionSelection.id
                == _positive_int(getattr(selection, "id", selection), "selection"),
                StaffingVacationUnionSelection.staffing_person_id == person.id,
                StaffingVacationUnionSelection.vacation_year == year,
                StaffingVacationUnionSelection.status.in_(
                    VACATION_UNION_ACTIVE_SELECTION_STATUSES
                ),
            )
            .with_for_update()
            .first()
        )
        if not source:
            raise ValueError("The Union vacation week is no longer available to split.")
        if source.bank_type != "optional":
            raise ValueError("Regular Union vacation weeks cannot be split.")
        if source.week_ending - timedelta(days=6) <= today:
            raise ValueError("A started or past Optional Week cannot be split.")
    else:
        optional_used = sum(
            1
            for row in pool["active_selections_by_person"].get(person.id, ())
            if row.bank_type == "optional"
        ) + _active_conversion_count(person.id, year, "union")
        if optional_used >= 1:
            raise ValueError("The employee has no unused Optional Week to split.")

    now = datetime.utcnow()
    if source:
        source.status = "cancelled"
        source.cancelled_by_user_id = getattr(user, "id", None)
        source.cancelled_at = now
        source.updated_at = now
    conversion = StaffingVacationWeekConversion(
        staffing_person_id=person.id,
        vacation_year=year,
        program="union",
        source_union_selection_id=source.id if source else None,
        converted_by_user_id=getattr(user, "id", None),
        converted_at=now,
        created_at=now,
        updated_at=now,
    )
    db.session.add(conversion)
    db.session.flush()
    if source:
        _reconcile_selection_floating_holidays([source], "union")
    return conversion


def schedule_split_vacation_day(
    conversion,
    vacation_date,
    user,
    *,
    capacity_override=False,
):
    """Schedule one split day with reusable person/day exclusivity."""
    require_vacation_mutation_access(user)
    conversion = _locked_active_conversion(conversion)
    day = _normalize_vacation_date(conversion.vacation_year, vacation_date)
    person = _locked_person(conversion.staffing_person_id)
    actor, calendar = _authorize_split_day_write(conversion, person, user)
    active_days = _locked_conversion_days(conversion.id)
    if len(active_days) >= 5:
        raise ValueError("No split vacation days remain available.")
    _ensure_time_off_day_available(person.id, day)

    if conversion.program == "union":
        calendar = _locked_union_calendar(calendar.id, conversion.vacation_year)
        pool = _union_pool_data(conversion.vacation_year)
        calendar_id = pool["official_calendar_by_person"].get(person.id)
        if not calendar_id or not calendar or calendar.id != calendar_id:
            raise ValueError("The employee does not currently have this Official Union calendar.")
        capacity = union_single_day_capacity(
            len(pool["official_members_by_calendar"].get(calendar.id, ()))
        )
        used = _union_day_usage(conversion.vacation_year, pool).get(
            (calendar.id, day), 0
        )
        if used >= capacity.capacity and not _boolean(capacity_override):
            raise ValueError("Union single-day capacity is full; confirm a one-time override.")
        if _boolean(capacity_override) and not _can_override_split_capacity(actor):
            raise ValueError("PT Supervisors cannot override Union vacation capacity.")

    now = datetime.utcnow()
    row = StaffingVacationDaySelection(
        conversion_id=conversion.id,
        staffing_person_id=person.id,
        vacation_year=conversion.vacation_year,
        vacation_date=day,
        item_type="split_vacation",
        status="scheduled",
        entered_by_user_id=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def cancel_split_vacation_day(day_selection, user):
    """Cancel or correct one split day, including an erroneous past entry."""
    require_vacation_mutation_access(user)
    row = (
        StaffingVacationDaySelection.query.filter_by(
            id=_positive_int(getattr(day_selection, "id", day_selection), "split day"),
            status="scheduled",
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The split vacation day is no longer scheduled.")
    conversion = _locked_active_conversion(row.conversion_id)
    person = _locked_person(row.staffing_person_id)
    _authorize_split_day_write(conversion, person, user)
    now = datetime.utcnow()
    row.status = "cancelled"
    row.cancelled_by_user_id = getattr(user, "id", None)
    row.cancelled_at = now
    row.updated_at = now
    db.session.flush()
    return row


def award_floating_holidays_for_selection(
    selection,
    program,
    qualifying_holidays=None,
):
    """Idempotently preserve holidays supplied by NeoApps' holiday authority.

    Normal application behavior reads NeoStaffing's configured qualifying dates.
    An explicit mapping remains available for deterministic service tests.
    """
    program = str(program or "").strip().casefold()
    if program == "management":
        row = StaffingVacationManagementSelection.query.filter_by(
            id=_positive_int(getattr(selection, "id", selection), "selection"),
            cancelled_at=None,
        ).with_for_update().first()
    elif program == "union":
        row = StaffingVacationUnionSelection.query.filter(
            StaffingVacationUnionSelection.id
            == _positive_int(getattr(selection, "id", selection), "selection"),
            StaffingVacationUnionSelection.status == "approved",
        ).with_for_update().first()
    else:
        raise ValueError("Choose a valid vacation program.")
    if not row:
        raise ValueError("Only an approved active whole vacation week can earn holidays.")
    if qualifying_holidays is None:
        holiday_values = _configured_holiday_values(
            row.week_ending - timedelta(days=6), row.week_ending
        )
    else:
        holiday_values = list(dict(qualifying_holidays).items())
    return _award_floating_entitlements([row], program, holiday_values)


def _award_configured_floating_holidays(selections, program):
    """Award configured dates for a bounded set of newly approved weeks."""
    selections = list(selections)
    if not selections:
        return []
    first_day = min(row.week_ending - timedelta(days=6) for row in selections)
    last_day = max(row.week_ending for row in selections)
    return _award_floating_entitlements(
        selections,
        program,
        _configured_holiday_values(first_day, last_day),
    )


def _reconcile_selection_floating_holidays(selections, program):
    """Reconcile durable Floating Holidays after whole-week state changes.

    An award remains durable once it has been consumed by a scheduled day. An
    unused award is removed when its source selection is no longer active or
    its qualifying date is no longer inside the source week. Cancelled day
    history is retained, but detached from an award that is no longer valid.
    """
    selections = list(selections)
    if not selections:
        return []
    program = str(program or "").strip().casefold()
    if program not in {"management", "union"}:
        raise ValueError("Choose a valid vacation program.")
    selection_ids = {row.id for row in selections}
    entitlements = (
        StaffingVacationDayEntitlement.query.filter(
            StaffingVacationDayEntitlement.entitlement_type == "floating_holiday",
            StaffingVacationDayEntitlement.source_program == program,
            StaffingVacationDayEntitlement.source_selection_id.in_(selection_ids),
        )
        .with_for_update()
        .all()
    )
    active_selections = [
        row
        for row in selections
        if (
            row.cancelled_at is None
            if program == "management"
            else row.status == "approved"
        )
    ]
    holiday_values = []
    valid_sources = set()
    if active_selections:
        first_day = min(
            row.week_ending - timedelta(days=6) for row in active_selections
        )
        last_day = max(row.week_ending for row in active_selections)
        holiday_values = _configured_holiday_values(first_day, last_day)
        valid_sources = {
            (selection.id, holiday_day)
            for selection in active_selections
            for holiday_day, _holiday_name in holiday_values
            if selection.week_ending - timedelta(days=6)
            <= holiday_day
            <= selection.week_ending
        }

    invalid = [
        row
        for row in entitlements
        if (row.source_selection_id, row.source_holiday_date) not in valid_sources
    ]
    invalid_ids = {row.id for row in invalid}
    if invalid_ids:
        consumed_ids = {
            entitlement_id
            for (entitlement_id,) in db.session.query(
                StaffingVacationDaySelection.entitlement_id
            ).filter(
                StaffingVacationDaySelection.entitlement_id.in_(invalid_ids),
                StaffingVacationDaySelection.status == "scheduled",
            ).all()
        }
        revocable_ids = invalid_ids - consumed_ids
        if revocable_ids:
            StaffingVacationDaySelection.query.filter(
                StaffingVacationDaySelection.entitlement_id.in_(revocable_ids),
                StaffingVacationDaySelection.status == "cancelled",
            ).update(
                {StaffingVacationDaySelection.entitlement_id: None},
                synchronize_session=False,
            )
            for entitlement in invalid:
                if entitlement.id in revocable_ids:
                    db.session.delete(entitlement)
    db.session.flush()
    return _award_floating_entitlements(
        active_selections,
        program,
        holiday_values,
    )


def resolve_holiday_rule_date(rule, vacation_year):
    """Resolve one recurring holiday rule without materializing yearly rows."""
    year = normalize_vacation_year(vacation_year)
    month = int(rule.month)
    if rule.rule_type == "fixed_date":
        try:
            return date(year, month, int(rule.day_of_month))
        except (TypeError, ValueError):
            return None
    weekday = int(rule.weekday)
    if rule.rule_type == "nth_weekday":
        first = date(year, month, 1)
        day_number = 1 + ((weekday - first.weekday()) % 7) + 7 * (
            int(rule.occurrence) - 1
        )
        try:
            resolved = date(year, month, day_number)
        except ValueError:
            return None
        return resolved if resolved.month == month else None
    if rule.rule_type == "last_weekday":
        last_day = calendar_module.monthrange(year, month)[1]
        resolved = date(year, month, last_day)
        return resolved - timedelta(days=(resolved.weekday() - weekday) % 7)
    raise ValueError("Choose a valid recurring holiday rule.")


def holiday_rule_label(rule):
    month_name = calendar_module.month_name[int(rule.month)]
    if rule.rule_type == "fixed_date":
        return f"{month_name} {int(rule.day_of_month)}"
    weekday_name = calendar_module.day_name[int(rule.weekday)]
    if rule.rule_type == "last_weekday":
        return f"Last {weekday_name} in {month_name}"
    occurrence = dict(HOLIDAY_OCCURRENCE_CHOICES).get(
        int(rule.occurrence), f"#{int(rule.occurrence)}"
    )
    return f"{occurrence} {weekday_name} in {month_name}"


def _holiday_rule_key(rule_type, month, day_of_month, weekday, occurrence):
    if rule_type == "fixed_date":
        return f"fixed_date:{month}:{day_of_month}"
    if rule_type == "last_weekday":
        return f"last_weekday:{month}:{weekday}"
    return f"nth_weekday:{month}:{weekday}:{occurrence}"


def _configured_holiday_values(first_day, last_day):
    """Return deduplicated actual dates from recurring and legacy definitions."""
    rules = StaffingVacationHolidayRule.query.order_by(
        StaffingVacationHolidayRule.id
    ).all()
    values = {}
    for rule in rules:
        for year in range(first_day.year, last_day.year + 1):
            holiday_day = resolve_holiday_rule_date(rule, year)
            if holiday_day and first_day <= holiday_day <= last_day:
                values.setdefault(holiday_day, rule.name)
    # Legacy rows remain readable during deployment and preserve old explicit
    # dates that cannot be inferred safely. Recurring rules win on duplicates.
    legacy = StaffingVacationQualifyingHoliday.query.filter(
        StaffingVacationQualifyingHoliday.holiday_date.between(first_day, last_day)
    ).order_by(
        StaffingVacationQualifyingHoliday.holiday_date,
        StaffingVacationQualifyingHoliday.id,
    ).all()
    for holiday in legacy:
        values.setdefault(holiday.holiday_date, holiday.name)
    return sorted(values.items())


def _delete_matching_legacy_holiday_dates(rule_type, month, day_of_month):
    """Remove migrated fixed-date definitions without touching earned awards."""
    if rule_type != "fixed_date":
        return
    for legacy in StaffingVacationQualifyingHoliday.query.all():
        if (
            legacy.holiday_date.month == month
            and legacy.holiday_date.day == day_of_month
        ):
            db.session.delete(legacy)


def qualifying_holiday_settings(user):
    """Return the compact Settings contract and authorization state."""
    return {
        "holidays": StaffingVacationHolidayRule.query.order_by(
            StaffingVacationHolidayRule.month,
            func.lower(StaffingVacationHolidayRule.name),
            StaffingVacationHolidayRule.id,
        ).all(),
        "can_edit": can_manage_vacation_settings(user),
        "month_choices": HOLIDAY_MONTH_CHOICES,
        "weekday_choices": HOLIDAY_WEEKDAY_CHOICES,
        "occurrence_choices": HOLIDAY_OCCURRENCE_CHOICES,
        "rule_label": holiday_rule_label,
    }


def can_manage_vacation_settings(user):
    app_role = get_user_app_role(user, "neostaffing") or "watcher"
    return bool(
        getattr(user, "role", None) == "grandmaster"
        or (
            ROLE_LEVELS.get(app_role, 0)
            >= ROLE_LEVELS[VACATION_MINIMUM_WRITABLE_APP_ROLE]
            and ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]
        )
    )


def save_qualifying_holiday(
    holiday,
    holiday_date=None,
    name=None,
    user=None,
    *,
    rule_type=None,
    month=None,
    day_of_month=None,
    weekday=None,
    occurrence=None,
):
    """Create/edit one recurring rule and reconcile approved weeks."""
    require_vacation_mutation_access(user)
    if not can_manage_vacation_settings(user):
        raise ValueError("NeoStaffing Master access is required to edit holidays.")
    if rule_type is None and holiday_date is not None:
        try:
            legacy_date = (
                holiday_date
                if isinstance(holiday_date, date)
                else date.fromisoformat(str(holiday_date))
            )
        except (TypeError, ValueError) as error:
            raise ValueError("Select a valid qualifying holiday date.") from error
        rule_type = "fixed_date"
        month = legacy_date.month
        day_of_month = legacy_date.day
    normalized_rule = str(rule_type or "").strip().casefold()
    if normalized_rule not in HOLIDAY_RULE_TYPES:
        raise ValueError("Choose a valid recurring holiday rule.")
    try:
        normalized_month = int(month)
        normalized_day = (
            int(day_of_month)
            if day_of_month is not None and str(day_of_month).strip()
            else None
        )
        normalized_weekday = (
            int(weekday)
            if weekday is not None and str(weekday).strip()
            else None
        )
        normalized_occurrence = (
            int(occurrence)
            if occurrence is not None and str(occurrence).strip()
            else None
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Complete the recurring holiday rule.") from error
    if not 1 <= normalized_month <= 12:
        raise ValueError("Choose a valid holiday month.")
    if normalized_rule == "fixed_date":
        if normalized_day is None:
            raise ValueError("Choose a day of month.")
        try:
            date(2000, normalized_month, normalized_day)
        except ValueError as error:
            raise ValueError("Choose a valid month and day.") from error
        normalized_weekday = normalized_occurrence = None
    else:
        if normalized_weekday is None or not 0 <= normalized_weekday <= 6:
            raise ValueError("Choose a valid weekday.")
        normalized_day = None
        if normalized_rule == "nth_weekday":
            if normalized_occurrence is None or not 1 <= normalized_occurrence <= 5:
                raise ValueError("Choose a valid weekday occurrence.")
        else:
            normalized_occurrence = None
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Holiday name is required.")
    if len(normalized_name) > 80:
        raise ValueError("Holiday name must be 80 characters or fewer.")
    holiday_id = getattr(holiday, "id", holiday)
    row = None
    matching_awards = []
    old_definition = None
    if holiday_id:
        row = StaffingVacationHolidayRule.query.filter_by(
            id=_positive_int(holiday_id, "holiday")
        ).with_for_update().first()
        if not row:
            raise ValueError("The qualifying holiday was not found.")
        old_definition = (
            row.rule_type,
            row.month,
            row.day_of_month,
            row.weekday,
            row.occurrence,
        )
        matching_awards = [
            award
            for award in StaffingVacationDayEntitlement.query.filter_by(
                entitlement_type="floating_holiday"
            ).all()
            if award.source_holiday_date
            == resolve_holiday_rule_date(row, award.source_holiday_date.year)
        ]
    definition_key = _holiday_rule_key(
        normalized_rule,
        normalized_month,
        normalized_day,
        normalized_weekday,
        normalized_occurrence,
    )
    duplicate = StaffingVacationHolidayRule.query.filter(
        StaffingVacationHolidayRule.definition_key == definition_key,
        StaffingVacationHolidayRule.id != (row.id if row else 0),
    ).first()
    if duplicate:
        raise ValueError("An equivalent recurring holiday already exists.")
    now = datetime.utcnow()
    if not row:
        row = StaffingVacationHolidayRule(
            created_by_user_id=getattr(user, "id", None),
            created_at=now,
        )
        db.session.add(row)
    row.name = normalized_name
    row.rule_type = normalized_rule
    row.month = normalized_month
    row.day_of_month = normalized_day
    row.weekday = normalized_weekday
    row.occurrence = normalized_occurrence
    row.definition_key = definition_key
    row.updated_by_user_id = getattr(user, "id", None)
    row.updated_at = now
    db.session.flush()
    if old_definition and old_definition != (
        normalized_rule,
        normalized_month,
        normalized_day,
        normalized_weekday,
        normalized_occurrence,
    ):
        _delete_matching_legacy_holiday_dates(*old_definition[:3])
    for award in matching_awards:
        if award.source_holiday_date == resolve_holiday_rule_date(
            row, award.source_holiday_date.year
        ):
            award.source_holiday_name = normalized_name
    reconcile_floating_holiday_entitlements()
    return row


def delete_qualifying_holiday(holiday, user):
    """Remove configuration while conservatively preserving durable awards."""
    require_vacation_mutation_access(user)
    if not can_manage_vacation_settings(user):
        raise ValueError("NeoStaffing Master access is required to edit holidays.")
    row = StaffingVacationHolidayRule.query.filter_by(
        id=_positive_int(getattr(holiday, "id", holiday), "holiday")
    ).with_for_update().first()
    if not row:
        raise ValueError("The qualifying holiday was not found.")
    _delete_matching_legacy_holiday_dates(
        row.rule_type, row.month, row.day_of_month
    )
    db.session.delete(row)
    db.session.flush()
    return row


def reconcile_floating_holiday_entitlements(holiday_dates=None):
    """Bounded reconciliation of configured dates against approved whole weeks."""
    management = StaffingVacationManagementSelection.query.filter(
        StaffingVacationManagementSelection.cancelled_at.is_(None),
    ).all()
    union = StaffingVacationUnionSelection.query.filter(
        StaffingVacationUnionSelection.status == "approved",
    ).all()
    selections = management + union
    if not selections:
        return []
    first_day = min(row.week_ending - timedelta(days=6) for row in selections)
    last_day = max(row.week_ending for row in selections)
    holiday_values = _configured_holiday_values(first_day, last_day)
    if holiday_dates is not None:
        normalized = {
            value if isinstance(value, date) else date.fromisoformat(str(value))
            for value in holiday_dates
        }
        holiday_values = [item for item in holiday_values if item[0] in normalized]
    if not holiday_values:
        return []
    awarded = _award_floating_entitlements(management, "management", holiday_values)
    awarded.extend(_award_floating_entitlements(union, "union", holiday_values))
    return awarded


def _award_floating_entitlements(selections, program, holiday_values):
    selections = list(selections)
    normalized_holidays = []
    for raw_day, raw_name in holiday_values:
        holiday_day = (
            raw_day if isinstance(raw_day, date) else date.fromisoformat(str(raw_day))
        )
        holiday_name = str(raw_name or "").strip()
        if not holiday_name:
            raise ValueError("A qualifying holiday must have a name.")
        normalized_holidays.append((holiday_day, holiday_name))
    matches = [
        (selection, holiday_day, holiday_name)
        for selection in selections
        for holiday_day, holiday_name in normalized_holidays
        if selection.week_ending - timedelta(days=6)
        <= holiday_day
        <= selection.week_ending
    ]
    if not matches:
        return []
    selection_ids = {selection.id for selection, _day, _name in matches}
    holiday_dates = {day for _selection, day, _name in matches}
    existing = {
        (row.source_selection_id, row.source_holiday_date): row
        for row in StaffingVacationDayEntitlement.query.filter(
            StaffingVacationDayEntitlement.source_program == program,
            StaffingVacationDayEntitlement.source_selection_id.in_(selection_ids),
            StaffingVacationDayEntitlement.source_holiday_date.in_(holiday_dates),
        ).all()
    }
    result = []
    for selection, holiday_day, holiday_name in matches:
        key = (selection.id, holiday_day)
        entitlement = existing.get(key)
        if not entitlement:
            entitlement = StaffingVacationDayEntitlement(
                staffing_person_id=selection.staffing_person_id,
                vacation_year=selection.vacation_year,
                entitlement_type="floating_holiday",
                source_program=program,
                source_selection_id=selection.id,
                source_holiday_date=holiday_day,
                source_holiday_name=holiday_name,
            )
            db.session.add(entitlement)
            existing[key] = entitlement
        result.append(entitlement)
    db.session.flush()
    return result


def schedule_vacation_entitlement_day(
    person,
    vacation_date,
    item_type,
    user,
    *,
    program,
    entitlement_id=None,
    capacity_override=False,
    today=None,
):
    """Consume one derived or durable day entitlement transactionally."""
    require_vacation_mutation_access(user)
    day = vacation_date if isinstance(vacation_date, date) else date.fromisoformat(str(vacation_date))
    item_type = str(item_type or "").strip().casefold()
    program = str(program or "").strip().casefold()
    if item_type not in VACATION_DAY_ITEM_TYPES:
        raise ValueError("Choose a valid vacation day type.")
    if program not in {"management", "union"}:
        raise ValueError("Choose a valid vacation program.")
    person = _locked_person(person)
    actor, calendar = _authorize_vacation_day_write(
        person, program, user, vacation_year=day.year
    )
    _ensure_time_off_day_available(person.id, day)
    active_rows = (
        StaffingVacationDaySelection.query.filter_by(
            staffing_person_id=person.id,
            item_type=item_type,
            status="scheduled",
        )
        .with_for_update()
        .all()
    )
    entitlement = None
    if item_type == "d_day":
        if program != "management" or person.classification not in VACATION_MANAGEMENT_CLASSIFICATIONS:
            raise ValueError("D-Days apply only to Management employees.")
        cycle_start, cycle_end = d_day_cycle(day)
        if sum(cycle_start <= row.vacation_date <= cycle_end for row in active_rows) >= D_DAY_ENTITLEMENT:
            raise ValueError("No D-Days remain in this calendar year.")
    elif item_type == "optional_day":
        if program != "union":
            raise ValueError("Optional Days apply only to Union employees.")
        cycle_start, cycle_end = optional_day_cycle(day)
        if sum(cycle_start <= row.vacation_date <= cycle_end for row in active_rows) >= OPTIONAL_DAY_ENTITLEMENT:
            raise ValueError("No Optional Days remain in this August-July cycle.")
    elif item_type == "anniversary_day":
        anniversary = employee_anniversary_date(person.seniority_date, day.year)
        if day != anniversary:
            raise ValueError("Anniversary Day may be used only on the actual anniversary date.")
        if (today or date.today()) < anniversary:
            raise ValueError("Anniversary Day is not available before the anniversary date.")
        if any(row.vacation_date.year == day.year for row in active_rows):
            raise ValueError("Anniversary Day is already used for this year.")
    else:
        entitlement = StaffingVacationDayEntitlement.query.filter_by(
            id=_positive_int(entitlement_id, "Floating Holiday entitlement"),
            staffing_person_id=person.id,
            entitlement_type="floating_holiday",
            source_program=program,
        ).with_for_update().first()
        if not entitlement:
            raise ValueError("The Floating Holiday entitlement is not available.")
        if day.year != entitlement.vacation_year:
            raise ValueError("The Floating Holiday must be used in its vacation year.")
        if any(row.entitlement_id == entitlement.id for row in active_rows):
            raise ValueError("This Floating Holiday entitlement is already used.")

    if program == "union":
        _enforce_union_day_capacity(
            person,
            day,
            actor,
            calendar,
            capacity_override=capacity_override,
        )
    now = datetime.utcnow()
    row = StaffingVacationDaySelection(
        conversion_id=None,
        entitlement_id=entitlement.id if entitlement else None,
        staffing_person_id=person.id,
        vacation_year=day.year,
        vacation_date=day,
        item_type=item_type,
        status="scheduled",
        entered_by_user_id=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def cancel_vacation_entitlement_day(day_selection, user, *, today=None):
    """Correct a day entry and restore its derived or durable entitlement."""
    require_vacation_mutation_access(user)
    row = StaffingVacationDaySelection.query.filter(
        StaffingVacationDaySelection.id
        == _positive_int(getattr(day_selection, "id", day_selection), "vacation day"),
        StaffingVacationDaySelection.item_type.in_(VACATION_DAY_ITEM_TYPES),
        StaffingVacationDaySelection.status == "scheduled",
    ).with_for_update().first()
    if not row:
        raise ValueError("The vacation day is no longer scheduled.")
    person = _locked_person(row.staffing_person_id)
    program = _day_selection_program(row, person)
    actor, _calendar = _authorize_vacation_day_write(
        person, program, user, vacation_year=row.vacation_year
    )
    if row.vacation_date < (today or date.today()) and not _can_correct_past_day(actor):
        raise ValueError("Only an FT Supervisor or Manager may correct a past vacation day.")
    now = datetime.utcnow()
    row.status = "cancelled"
    row.cancelled_by_user_id = getattr(user, "id", None)
    row.cancelled_at = now
    row.updated_at = now
    db.session.flush()
    if row.entitlement_id:
        entitlement = StaffingVacationDayEntitlement.query.filter_by(
            id=row.entitlement_id,
            entitlement_type="floating_holiday",
        ).first()
        if entitlement:
            selection_model = (
                StaffingVacationManagementSelection
                if entitlement.source_program == "management"
                else StaffingVacationUnionSelection
            )
            source = selection_model.query.filter_by(
                id=entitlement.source_selection_id
            ).first()
            if source:
                _reconcile_selection_floating_holidays(
                    [source], entitlement.source_program
                )
    return row


def schedule_management_availability_day(
    person,
    availability_date,
    item_type,
    user,
):
    """Persist one exclusive Special Assignment or Corporate Class day."""
    require_vacation_mutation_access(user)
    day = (
        availability_date
        if isinstance(availability_date, date)
        else date.fromisoformat(str(availability_date))
    )
    item_type = str(item_type or "").strip().casefold()
    if item_type not in VACATION_AVAILABILITY_ITEM_TYPES:
        raise ValueError("Choose Special Assignment or Corporate Class.")
    person = _locked_person(person)
    actor = _authorize_management_availability_write(person, user)
    _ensure_time_off_day_available(person.id, day)
    now = datetime.utcnow()
    row = StaffingVacationDaySelection(
        staffing_person_id=person.id,
        vacation_year=day.year,
        vacation_date=day,
        item_type=item_type,
        status="scheduled",
        entered_by_user_id=getattr(user, "id", None),
        created_at=now,
        updated_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row


def cancel_management_availability_day(day_selection, user):
    """Remove an availability entry, including a past correction."""
    require_vacation_mutation_access(user)
    row = StaffingVacationDaySelection.query.filter(
        StaffingVacationDaySelection.id
        == _positive_int(getattr(day_selection, "id", day_selection), "availability day"),
        StaffingVacationDaySelection.item_type.in_(
            VACATION_AVAILABILITY_ITEM_TYPES
        ),
        StaffingVacationDaySelection.status == "scheduled",
    ).with_for_update().first()
    if not row:
        raise ValueError("The availability entry is no longer active.")
    person = _locked_person(row.staffing_person_id)
    _authorize_management_availability_write(person, user)
    now = datetime.utcnow()
    row.status = "cancelled"
    row.cancelled_by_user_id = getattr(user, "id", None)
    row.cancelled_at = now
    row.updated_at = now
    db.session.flush()
    return row


def recombine_split_vacation_week(conversion, user):
    """Return an untouched five-day conversion to a generic unused week bank."""
    require_vacation_mutation_access(user)
    conversion = _locked_active_conversion(conversion)
    person = _locked_person(conversion.staffing_person_id)
    actor, _calendar = _authorize_split_day_write(
        conversion, person, user, recombine=True
    )
    if conversion.program == "management" and not _can_manage_split_days(
        actor, person, "management", manager_only=True
    ):
        raise ValueError("Only an authorized Manager may recombine Management split days.")
    if _locked_conversion_days(conversion.id):
        raise ValueError("Cancel all scheduled split days before recombining the week.")
    now = datetime.utcnow()
    conversion.recombined_by_user_id = getattr(user, "id", None)
    conversion.recombined_at = now
    conversion.updated_at = now
    db.session.flush()
    return conversion


def review_union_selection(selection, approve, user, *, capacity_override=False):
    """Approve or deny one PT-entered pending selection in its current Official pool."""
    require_vacation_mutation_access(user)
    selection_id = getattr(selection, "id", selection)
    row = (
        StaffingVacationUnionSelection.query.filter_by(
            id=_positive_int(selection_id, "Union vacation selection")
        )
        .with_for_update()
        .first()
    )
    if not row or row.status != "pending":
        raise ValueError("The Union vacation selection is no longer pending.")
    person = _locked_union_person(row.staffing_person_id)
    hierarchy = vacation_hierarchy()
    pool = _union_pool_data(row.vacation_year, hierarchy=hierarchy)
    calendar_id = pool["official_calendar_by_person"].get(person.id)
    if not calendar_id:
        raise ValueError("The employee does not currently have one Official Union calendar.")
    calendar = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes)
        )
        .filter_by(id=calendar_id, active=True)
        .with_for_update()
        .first()
    )
    pool = _union_pool_data(row.vacation_year, hierarchy=hierarchy)
    if pool["official_calendar_by_person"].get(person.id) != calendar.id:
        raise ValueError("The employee's Official Union calendar changed; reload and retry.")
    actor = vacation_actor(user, hierarchy)
    if _union_actor_entry_status(actor, calendar) != "approved":
        raise ValueError("Only an authorized FT Supervisor or Manager may review this pick.")

    approve = _boolean(approve)
    if approve:
        capacity = union_whole_week_capacity(
            len(pool["official_members_by_calendar"].get(calendar.id, ())),
            row.vacation_year,
            row.week_ending,
        )
        used = pool["usage_by_calendar_week"].get(
            (calendar.id, row.week_ending),
            0,
        )
        if used > capacity.capacity and not _boolean(capacity_override):
            raise ValueError("Approval exceeds Union capacity; confirm a one-time override.")
        row.status = "approved"
    else:
        row.status = "denied"
    now = datetime.utcnow()
    row.reviewed_by_user_id = getattr(user, "id", None)
    row.reviewed_at = now
    row.updated_at = now
    db.session.flush()
    if approve:
        _award_configured_floating_holidays([row], "union")
    return row


def move_union_selection(
    selection,
    requested_week_ending,
    user,
    *,
    capacity_override=False,
    today=None,
):
    """Atomically move one approved future Union week within its Official pool."""
    require_vacation_mutation_access(user)
    today = _as_date(today or date.today())
    selection_id = getattr(selection, "id", selection)
    row = (
        StaffingVacationUnionSelection.query.filter_by(
            id=_positive_int(selection_id, "Union vacation selection"),
            status="approved",
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The approved Union vacation selection is no longer active.")
    if _union_week_has_started(row.week_ending, today):
        raise ValueError("Past or already-started Union weeks cannot be moved.")
    destination = normalize_week_ending(row.vacation_year, requested_week_ending)
    if destination == row.week_ending:
        raise ValueError("Choose a different destination week.")
    if _union_week_has_started(destination, today):
        raise ValueError("Choose a future destination week.")
    person = _locked_union_person(row.staffing_person_id)
    hierarchy = vacation_hierarchy()
    pool = _union_pool_data(row.vacation_year, hierarchy=hierarchy)
    calendar_id = pool["official_calendar_by_person"].get(person.id)
    calendar = _locked_union_calendar(calendar_id, row.vacation_year)
    pool = _union_pool_data(row.vacation_year, hierarchy=hierarchy)
    actor = vacation_actor(user, hierarchy)
    if _union_actor_entry_status(actor, calendar) != "approved":
        raise ValueError("Only an authorized FT Supervisor or Manager may move this pick.")
    if pool["official_calendar_by_person"].get(person.id) != calendar.id:
        raise ValueError("The employee's Official Union calendar changed; reload and retry.")
    _ensure_weeks_have_no_split_days(person.id, [destination])
    capacity = union_whole_week_capacity(
        len(pool["official_members_by_calendar"].get(calendar.id, ())),
        row.vacation_year,
        destination,
    )
    used = pool["usage_by_calendar_week"].get((calendar.id, destination), 0)
    override = _boolean(capacity_override)
    if used >= capacity.capacity and not override:
        raise ValueError(
            "Union vacation capacity is full; confirm a one-time override for this move."
        )
    if override and not _can_override_split_capacity(actor):
        raise ValueError("PT Supervisors cannot override Union vacation capacity.")
    target = (
        StaffingVacationUnionSelection.query.filter(
            StaffingVacationUnionSelection.staffing_person_id == person.id,
            StaffingVacationUnionSelection.vacation_year == row.vacation_year,
            StaffingVacationUnionSelection.week_ending == destination,
            StaffingVacationUnionSelection.id != row.id,
        )
        .with_for_update()
        .first()
    )
    if target and target.status in VACATION_UNION_ACTIVE_SELECTION_STATUSES:
        raise ValueError("The employee already has this Union vacation week selected.")
    now = datetime.utcnow()
    if target:
        row.status = "cancelled"
        row.cancelled_by_user_id = getattr(user, "id", None)
        row.cancelled_at = now
        row.updated_at = now
        target.bank_type = row.bank_type
        target.status = "approved"
        target.entered_by_user_id = getattr(user, "id", None)
        target.reviewed_by_user_id = getattr(user, "id", None)
        target.reviewed_at = now
        target.cancelled_by_user_id = None
        target.cancelled_at = None
        target.updated_at = now
        result = target
    else:
        row.week_ending = destination
        row.entered_by_user_id = getattr(user, "id", None)
        row.reviewed_by_user_id = getattr(user, "id", None)
        row.reviewed_at = now
        row.updated_at = now
        result = row
    db.session.flush()
    _reconcile_selection_floating_holidays(
        [row] if result is row else [row, result],
        "union",
    )
    return result


def cancel_union_selection(selection, user, *, correction=False, today=None):
    """Cancel an active Union selection without deleting its durable history."""
    require_vacation_mutation_access(user)
    today = _as_date(today or date.today())
    selection_id = getattr(selection, "id", selection)
    row = (
        StaffingVacationUnionSelection.query.filter_by(
            id=_positive_int(selection_id, "Union vacation selection")
        )
        .with_for_update()
        .first()
    )
    if not row or row.status not in VACATION_UNION_ACTIVE_SELECTION_STATUSES:
        raise ValueError("The Union vacation selection is no longer active.")
    person = _locked_union_person(row.staffing_person_id)
    hierarchy = vacation_hierarchy()
    pool = _union_pool_data(row.vacation_year, hierarchy=hierarchy)
    calendar_id = pool["official_calendar_by_person"].get(person.id)
    actor = vacation_actor(user, hierarchy)
    entry_status = None
    if calendar_id:
        calendar = pool["calendar_by_id"].get(calendar_id)
        entry_status = _union_actor_entry_status(actor, calendar)
    allowed = bool(
        actor.is_grandmaster
        or entry_status == "approved"
        or (
            entry_status == "pending"
            and row.status == "pending"
            and row.entered_by_user_id == getattr(user, "id", None)
        )
    )
    if not allowed:
        raise ValueError("You do not have authority to cancel this Union vacation pick.")
    started = _union_week_has_started(row.week_ending, today)
    if started and row.status == "approved" and not _boolean(correction):
        raise ValueError("Past or already-started Union weeks require correction removal.")
    if _boolean(correction) and entry_status != "approved" and not actor.is_grandmaster:
        raise ValueError("Only an authorized FT Supervisor or Manager may correct a past pick.")
    now = datetime.utcnow()
    row.status = "cancelled"
    row.cancelled_by_user_id = getattr(user, "id", None)
    row.cancelled_at = now
    row.updated_at = now
    db.session.flush()
    _reconcile_selection_floating_holidays([row], "union")
    return row


def _union_pool_data(vacation_year, *, hierarchy=None, calendars=None):
    """Resolve each Union employee into exactly one Official calendar in memory."""
    year = normalize_vacation_year(vacation_year)
    hierarchy = hierarchy or vacation_hierarchy()
    if calendars is None:
        calendars = (
            StaffingVacationUnionCalendar.query.options(
                selectinload(StaffingVacationUnionCalendar.scopes)
            )
            .filter_by(vacation_year=year, calendar_type="official")
            .order_by(StaffingVacationUnionCalendar.id.asc())
            .all()
        )
    calendars = [
        calendar for calendar in calendars if calendar.calendar_type == "official"
    ]
    calendar_by_id = {calendar.id: calendar for calendar in calendars}
    scope_ids_by_calendar = {
        calendar.id: {scope.staffing_unit_id for scope in calendar.scopes}
        for calendar in calendars
    }
    calendar_ids_by_membership = {}
    for calendar in calendars:
        if not calendar.active:
            continue
        classifications = set()
        if calendar.include_part_time:
            classifications.update(VACATION_PT_CLASSIFICATIONS)
        if calendar.include_full_time:
            classifications.update(VACATION_FT_CLASSIFICATIONS)
        work_area_ids = _scope_work_area_ids(
            scope_ids_by_calendar[calendar.id], hierarchy
        )
        for work_area_id in work_area_ids:
            for classification in classifications:
                calendar_ids_by_membership.setdefault(
                    (work_area_id, classification), set()
                ).add(calendar.id)

    union_rows = (
        db.session.query(
            StaffingPerson,
            StaffingWorkAssignment,
            StaffingVacationUnionSelection,
        )
        .join(
            StaffingWorkAssignment,
            StaffingWorkAssignment.person_id == StaffingPerson.id,
        )
        .outerjoin(
            StaffingVacationUnionSelection,
            and_(
                StaffingVacationUnionSelection.staffing_person_id
                == StaffingPerson.id,
                StaffingVacationUnionSelection.vacation_year == year,
                StaffingVacationUnionSelection.status.in_(
                    VACATION_UNION_ACTIVE_SELECTION_STATUSES
                ),
            ),
        )
        .filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(VACATION_UNION_CLASSIFICATIONS),
            StaffingWorkAssignment.active.is_(True),
        )
        .all()
    )
    official_calendar_by_person = {}
    official_members_by_calendar = {calendar.id: [] for calendar in calendars}
    people_by_id = {}
    active_selections_by_person = {}
    for person, assignment, selection in union_rows:
        people_by_id[person.id] = (person, assignment)
        if selection:
            active_selections_by_person.setdefault(person.id, []).append(selection)
    for person, assignment in people_by_id.values():
        matching = calendar_ids_by_membership.get(
            (assignment.work_area_unit_id, person.classification), set()
        )
        if len(matching) != 1:
            continue
        calendar_id = next(iter(matching))
        official_calendar_by_person[person.id] = calendar_id
        official_members_by_calendar[calendar_id].append(person)
    for calendar_id, members in official_members_by_calendar.items():
        official_members_by_calendar[calendar_id] = _seniority_order(members)

    usage_by_calendar_week = {}
    for person_id, selections in active_selections_by_person.items():
        selections.sort(key=lambda selection: (selection.week_ending, selection.id))
        calendar_id = official_calendar_by_person.get(person_id)
        if calendar_id:
            for selection in selections:
                key = (calendar_id, selection.week_ending)
                usage_by_calendar_week[key] = usage_by_calendar_week.get(key, 0) + 1
    return {
        "calendar_by_id": calendar_by_id,
        "scope_ids_by_calendar": scope_ids_by_calendar,
        "official_calendar_by_person": official_calendar_by_person,
        "official_members_by_calendar": official_members_by_calendar,
        "active_selections_by_person": active_selections_by_person,
        "usage_by_calendar_week": usage_by_calendar_week,
    }


def _union_day_usage(vacation_year, pool):
    rows = (
        db.session.query(StaffingVacationDaySelection)
        .filter(
            StaffingVacationDaySelection.vacation_year == vacation_year,
            StaffingVacationDaySelection.status == "scheduled",
        )
        .all()
    )
    usage = {}
    for row in rows:
        calendar_id = pool["official_calendar_by_person"].get(row.staffing_person_id)
        if calendar_id:
            key = (calendar_id, row.vacation_date)
            usage[key] = usage.get(key, 0) + 1
    return usage


def _authorize_vacation_day_write(person, program, user, *, vacation_year):
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    if program == "management":
        area = management_primary_area(person, hierarchy)
        allowed = bool(
            area
            and (
                actor.is_grandmaster
                or (
                    actor.person
                    and actor.person.classification in VACATION_MANAGEMENT_CLASSIFICATIONS
                    and can_edit_management_capacity(actor, area.id)
                )
            )
        )
        if not allowed:
            raise ValueError("You do not have authority to manage these vacation days.")
        return actor, None
    pool = _union_pool_data(vacation_year, hierarchy=hierarchy)
    calendar_id = pool["official_calendar_by_person"].get(person.id)
    calendar = pool["calendar_by_id"].get(calendar_id)
    if not calendar or _union_actor_entry_status(actor, calendar) != "approved":
        raise ValueError(
            "Only an authorized FT Supervisor or Manager may manage these Union vacation days."
        )
    return actor, calendar


def _authorize_management_availability_write(person, user):
    if person.classification not in VACATION_PINNED_RECIPIENT_CLASSIFICATIONS:
        raise ValueError(
            "Only an FT Supervisor or Manager/Division Manager may receive "
            "this availability status."
        )
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    area = management_primary_area(person, hierarchy)
    if not area:
        raise ValueError("The selected person has no active primary Management area.")
    if actor.is_grandmaster:
        return actor
    if person.classification == "division_manager":
        allowed = _can_manage_division_manager_vacation(actor, area.id)
    else:
        allowed = bool(
            actor.person
            and actor.person.classification
            in {"full_time_supervisor", "manager"}
            and can_edit_management_capacity(actor, area.id)
        )
    if not allowed:
        raise ValueError("You do not have authority to manage this person's availability.")
    return actor


def _enforce_union_day_capacity(
    person,
    vacation_date,
    actor,
    calendar,
    *,
    capacity_override=False,
):
    calendar = _locked_union_calendar(calendar.id, calendar.vacation_year)
    pool = _union_pool_data(calendar.vacation_year)
    if pool["official_calendar_by_person"].get(person.id) != calendar.id:
        raise ValueError("The employee's Official Union calendar changed; reload and retry.")
    capacity = union_single_day_capacity(
        len(pool["official_members_by_calendar"].get(calendar.id, ()))
    )
    used = _union_day_usage(calendar.vacation_year, pool).get(
        (calendar.id, vacation_date), 0
    )
    override = _boolean(capacity_override)
    if used >= capacity.capacity and not override:
        raise ValueError("Union single-day capacity is full; confirm a one-time override.")
    if override and not _can_override_split_capacity(actor):
        raise ValueError("PT Supervisors cannot override Union vacation capacity.")


def _day_selection_program(row, person):
    if row.item_type == "d_day":
        return "management"
    if row.item_type == "optional_day":
        return "union"
    if row.item_type == "floating_holiday" and row.entitlement:
        return row.entitlement.source_program
    return (
        "management"
        if person.classification in VACATION_MANAGEMENT_CLASSIFICATIONS
        else "union"
    )


def _can_correct_past_day(actor):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification in VACATION_SPLIT_ADMIN_CLASSIFICATIONS
        )
    )


def _locked_union_calendar(calendar, vacation_year):
    calendar_id = getattr(calendar, "id", calendar)
    row = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes)
        )
        .filter_by(
            id=_positive_int(calendar_id, "Union calendar"),
            vacation_year=vacation_year,
            active=True,
            calendar_type="official",
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The selected Official Union calendar is not available.")
    return row


def _locked_person(person):
    person_id = getattr(person, "id", person)
    row = (
        StaffingPerson.query.filter_by(id=_positive_int(person_id, "employee"))
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The selected employee was not found.")
    return row


def _locked_management_person(person):
    row = _locked_person(person)
    return _management_person(row)


def _locked_active_conversion(conversion):
    conversion_id = getattr(conversion, "id", conversion)
    row = (
        StaffingVacationWeekConversion.query.filter_by(
            id=_positive_int(conversion_id, "split vacation conversion"),
            recombined_at=None,
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The split vacation week is no longer active.")
    return row


def _locked_conversion_days(conversion_id):
    return (
        StaffingVacationDaySelection.query.filter_by(
            conversion_id=conversion_id,
            status="scheduled",
        )
        .order_by(StaffingVacationDaySelection.vacation_date)
        .with_for_update()
        .all()
    )


def _active_conversion_count(person_id, vacation_year, program):
    return StaffingVacationWeekConversion.query.filter_by(
        staffing_person_id=person_id,
        vacation_year=vacation_year,
        program=program,
        recombined_at=None,
    ).count()


def _can_manage_split_days(actor, person, program, *, manager_only=False):
    if actor.is_grandmaster:
        return True
    if not actor.person:
        return False
    required = (
        VACATION_SPLIT_MANAGER_CLASSIFICATIONS
        if manager_only
        else VACATION_SPLIT_ADMIN_CLASSIFICATIONS
    )
    if actor.person.classification not in required:
        return False
    if program != "management":
        return False
    area = management_primary_area(person)
    return bool(area and can_edit_management_capacity(actor, area.id))


def _can_manage_split_days_for_area(actor, area_id, *, manager_only=False):
    if actor.is_grandmaster:
        return True
    if not actor.person:
        return False
    required = (
        VACATION_SPLIT_MANAGER_CLASSIFICATIONS
        if manager_only
        else VACATION_SPLIT_ADMIN_CLASSIFICATIONS
    )
    return bool(
        actor.person.classification in required
        and can_edit_management_capacity(actor, area_id)
    )


def _can_manage_management_days_for_area(actor, area_id):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification in VACATION_MANAGEMENT_CLASSIFICATIONS
            and can_edit_management_capacity(actor, area_id)
        )
    )


def _can_administer_management_week_changes(actor, area_id):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification
            in VACATION_MANAGEMENT_PASS_ADMIN_CLASSIFICATIONS
            and can_edit_management_capacity(actor, area_id)
        )
    )


def _can_manage_division_manager_vacation(actor, area_id):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification in {"manager", "division_manager"}
            and can_edit_management_capacity(actor, area_id)
        )
    )


def _authorize_management_week_change(person, user, hierarchy=None):
    hierarchy = hierarchy or vacation_hierarchy()
    area = management_primary_area(person, hierarchy)
    if not area:
        raise ValueError("The selected person has no active primary Management area.")
    actor = vacation_actor(user, hierarchy)
    if person.classification == "division_manager":
        if not _can_manage_division_manager_vacation(actor, area.id):
            raise ValueError(
                "Only an authorized Manager may change Division Manager vacation."
            )
        return area, actor
    if not _can_administer_management_week_changes(actor, area.id):
        raise ValueError(
            "Only an authorized FT Supervisor or Manager may change this vacation week."
        )
    return area, actor


def _locked_management_selection(selection):
    selection_id = getattr(selection, "id", selection)
    row = (
        StaffingVacationManagementSelection.query.filter_by(
            id=_positive_int(selection_id, "Management vacation selection")
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The Management vacation selection was not found.")
    return row


def _locked_management_change_request(change_request):
    request_id = getattr(change_request, "id", change_request)
    row = (
        StaffingVacationManagementChangeRequest.query.filter_by(
            id=_positive_int(request_id, "Management vacation change request")
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The Management vacation change request was not found.")
    return row


def _management_week_has_started(week_ending, today):
    return week_ending - timedelta(days=6) <= today


def _ensure_management_week_not_started(week_ending, today):
    if _management_week_has_started(week_ending, today):
        raise ValueError("Past or already-started vacation weeks cannot be changed.")


def _ensure_management_selection_is_active(selection):
    if selection.cancelled_at is not None:
        raise ValueError("This Management vacation week is no longer active.")


def _ensure_person_has_no_active_management_week(
    person_id,
    vacation_year,
    week_ending,
    *,
    exclude_selection_id=None,
):
    query = StaffingVacationManagementSelection.query.filter_by(
        staffing_person_id=person_id,
        vacation_year=vacation_year,
        week_ending=week_ending,
        cancelled_at=None,
    )
    if exclude_selection_id:
        query = query.filter(
            StaffingVacationManagementSelection.id != exclude_selection_id
        )
    if query.with_for_update().first():
        raise ValueError("The employee already has this vacation week selected.")


def _validate_management_move_capacity(
    person,
    vacation_year,
    week_ending,
    area,
    actor,
    hierarchy,
    *,
    capacity_override=False,
):
    capacity = (
        StaffingVacationManagementCapacity.query.filter_by(
            vacation_year=vacation_year,
            area_unit_id=area.id,
        )
        .with_for_update()
        .first()
    )
    if not capacity:
        raise ValueError("Management capacity is not configured for this area and year.")
    selections_by_person = _management_active_selections_by_person(vacation_year)
    usage = _management_week_usage(vacation_year, [week_ending], hierarchy)
    leadership_rows = _management_leadership_rows()
    primary_assignments, _secondary = _primary_and_secondary_assignments(
        leadership_rows
    )
    pinned_people = _management_pinned_people_for_area(
        area, primary_assignments, hierarchy
    )
    pinned_ids = [row.id for row in pinned_people]
    pinned_day_rows = {}
    if pinned_ids:
        for row in StaffingVacationDaySelection.query.filter(
            StaffingVacationDaySelection.staffing_person_id.in_(pinned_ids),
            StaffingVacationDaySelection.vacation_year == vacation_year,
            StaffingVacationDaySelection.status == "scheduled",
        ).all():
            pinned_day_rows.setdefault(row.staffing_person_id, []).append(row)
    pinned_statuses = _pinned_week_statuses(
        pinned_people,
        [VacationWeek(vacation_year, week_ending - timedelta(days=6), week_ending)],
        selections_by_person,
        pinned_day_rows,
    )
    reduced_off = (
        StaffingVacationManagementWeekOverride.query.filter_by(
            vacation_year=vacation_year,
            area_unit_id=area.id,
            week_ending=week_ending,
        ).first()
        is not None
    )
    limit = management_capacity_limit(
        capacity,
        len(pinned_statuses.get(week_ending, {})),
        reduced_capacity_on=not reduced_off,
    )
    used = usage.get((area.id, week_ending), 0)
    override = _boolean(capacity_override)
    if used >= limit and not override:
        raise ValueError(
            "Management capacity is full; confirm a one-time override for this move."
        )
    if override and not _can_administer_management_week_changes(actor, area.id):
        raise ValueError("You do not have authority to override Management capacity.")


def _move_management_selection_row(
    selection,
    person,
    area,
    destination,
    actor,
    user,
    hierarchy,
    *,
    capacity_override=False,
    cancellation_reason,
):
    _ensure_person_has_no_active_management_week(
        person.id,
        selection.vacation_year,
        destination,
        exclude_selection_id=selection.id,
    )
    _ensure_weeks_have_no_split_days(person.id, [destination])
    if person.classification != "division_manager":
        _validate_management_move_capacity(
            person,
            selection.vacation_year,
            destination,
            area,
            actor,
            hierarchy,
            capacity_override=capacity_override,
        )
    target = (
        StaffingVacationManagementSelection.query.filter(
            StaffingVacationManagementSelection.staffing_person_id == person.id,
            StaffingVacationManagementSelection.vacation_year
            == selection.vacation_year,
            StaffingVacationManagementSelection.week_ending == destination,
            StaffingVacationManagementSelection.id != selection.id,
        )
        .with_for_update()
        .first()
    )
    now = datetime.utcnow()
    if target:
        _cancel_management_selection_row(
            selection, user, now, reason=cancellation_reason
        )
        target.cancelled_at = None
        target.cancelled_by_user_id = None
        target.cancellation_reason = None
        target.selected_by_user_id = getattr(user, "id", None)
        target.updated_at = now
        result = target
    else:
        selection.week_ending = destination
        selection.selected_by_user_id = getattr(user, "id", None)
        selection.updated_at = now
        result = selection
    db.session.flush()
    _reconcile_selection_floating_holidays(
        [selection] if result is selection else [selection, result],
        "management",
    )
    return result


def _cancel_management_selection_row(selection, user, now, *, reason):
    selection.cancelled_at = now
    selection.cancelled_by_user_id = getattr(user, "id", None)
    selection.cancellation_reason = reason
    selection.updated_at = now
    db.session.flush()
    _reconcile_selection_floating_holidays([selection], "management")


def _resolve_management_change_request(row, status, user, now):
    row.status = status
    row.resolved_by_user_id = getattr(user, "id", None)
    row.resolved_at = now
    row.updated_at = now


def _cancel_pending_management_change_requests(selection_id, user, *, now=None):
    now = now or datetime.utcnow()
    rows = (
        StaffingVacationManagementChangeRequest.query.filter_by(
            selection_id=selection_id,
            status="pending",
        )
        .with_for_update()
        .all()
    )
    for row in rows:
        _resolve_management_change_request(row, "cancelled", user, now)
    return rows


def _authorize_split_day_write(conversion, person, user, *, recombine=False):
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    if conversion.program == "management":
        if not _can_manage_split_days(actor, person, "management"):
            raise ValueError("You do not have authority to manage these split vacation days.")
        return actor, None
    pool = _union_pool_data(conversion.vacation_year, hierarchy=hierarchy)
    calendar_id = pool["official_calendar_by_person"].get(person.id)
    calendar = pool["calendar_by_id"].get(calendar_id)
    if not calendar or _union_actor_entry_status(actor, calendar) != "approved":
        action = "recombine" if recombine else "manage"
        raise ValueError(
            f"Only an authorized FT Supervisor or Manager may {action} Union split days."
        )
    return actor, calendar


def _can_override_split_capacity(actor):
    return bool(
        actor.is_grandmaster
        or (
            actor.person
            and actor.person.classification in VACATION_SPLIT_ADMIN_CLASSIFICATIONS
        )
    )


def _normalize_vacation_date(vacation_year, value):
    try:
        day = value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("Select a valid vacation date.") from error
    if day.year != vacation_year:
        raise ValueError("The vacation date must be within the selected vacation year.")
    return day


def _ensure_time_off_day_available(person_id, vacation_date):
    if StaffingVacationDaySelection.query.filter_by(
        staffing_person_id=person_id,
        vacation_date=vacation_date,
        status="scheduled",
    ).first():
        raise ValueError("The employee already has a time-off item for this date.")
    latest_week_ending = vacation_date + timedelta(days=6)
    management_overlap = StaffingVacationManagementSelection.query.filter(
        StaffingVacationManagementSelection.staffing_person_id == person_id,
        StaffingVacationManagementSelection.cancelled_at.is_(None),
        StaffingVacationManagementSelection.week_ending.between(
            vacation_date, latest_week_ending
        ),
    ).first()
    union_overlap = StaffingVacationUnionSelection.query.filter(
        StaffingVacationUnionSelection.staffing_person_id == person_id,
        StaffingVacationUnionSelection.status.in_(
            VACATION_UNION_ACTIVE_SELECTION_STATUSES
        ),
        StaffingVacationUnionSelection.week_ending.between(
            vacation_date, latest_week_ending
        ),
    ).first()
    if management_overlap or union_overlap:
        raise ValueError("The employee already has a whole vacation week covering this date.")


def _ensure_weeks_have_no_split_days(person_id, week_endings):
    ranges = [
        StaffingVacationDaySelection.vacation_date.between(
            week_ending - timedelta(days=6), week_ending
        )
        for week_ending in week_endings
    ]
    if ranges and StaffingVacationDaySelection.query.filter(
        StaffingVacationDaySelection.staffing_person_id == person_id,
        StaffingVacationDaySelection.status == "scheduled",
        or_(*ranges),
    ).first():
        raise ValueError(
            "A day-level vacation or availability entry already exists within the selected week."
        )


def _union_actor_entry_status(actor, calendar):
    if not calendar or not calendar.active:
        return None
    scope_ids = {scope.staffing_unit_id for scope in calendar.scopes}
    if not can_edit_union_scope(actor, scope_ids):
        return None
    if actor.is_grandmaster:
        return "approved"
    classification = getattr(actor.person, "classification", None)
    if classification in VACATION_UNION_PENDING_ENTRY_CLASSIFICATIONS:
        return "pending"
    if classification in VACATION_UNION_DIRECT_ENTRY_CLASSIFICATIONS:
        return "approved"
    return None


def _union_week_has_started(week_ending, today):
    return week_ending - timedelta(days=6) <= today


def _union_can_cancel_selection(actor, entry_status, selection, user):
    return bool(
        actor.is_grandmaster
        or entry_status == "approved"
        or (
            entry_status == "pending"
            and selection.status == "pending"
            and selection.entered_by_user_id == getattr(user, "id", None)
        )
    )


def _locked_union_person(person):
    person_id = getattr(person, "id", person)
    row = (
        StaffingPerson.query.filter(
            StaffingPerson.id == _positive_int(person_id, "employee"),
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(VACATION_UNION_CLASSIFICATIONS),
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise ValueError("The selected employee is not an active eligible Union employee.")
    return row


def _completed_service_years(start_date, through_date):
    return through_date.year - start_date.year - (
        (through_date.month, through_date.day) < (start_date.month, start_date.day)
    )


def _vacation_day_rows_for_year(vacation_year):
    rows = StaffingVacationDaySelection.query.options(
        joinedload(StaffingVacationDaySelection.entitlement)
    ).filter_by(vacation_year=vacation_year, status="scheduled").order_by(
        StaffingVacationDaySelection.vacation_date,
        StaffingVacationDaySelection.id,
    ).all()
    entitlements = StaffingVacationDayEntitlement.query.filter_by(
        vacation_year=vacation_year,
        entitlement_type="floating_holiday",
    ).order_by(
        StaffingVacationDayEntitlement.source_holiday_date,
        StaffingVacationDayEntitlement.id,
    ).all()
    rows_by_person = {}
    entitlements_by_person = {}
    for row in rows:
        rows_by_person.setdefault(row.staffing_person_id, []).append(row)
    for entitlement in entitlements:
        entitlements_by_person.setdefault(entitlement.staffing_person_id, []).append(
            entitlement
        )
    return rows_by_person, entitlements_by_person


def _available_floating_entitlements(entitlements, day_rows):
    used = {
        row.entitlement_id
        for row in day_rows
        if row.status == "scheduled" and row.entitlement_id
    }
    return [row for row in entitlements if row.id not in used]


def union_calendars_context(vacation_year, user, today=None):
    year = normalize_vacation_year(vacation_year)
    today = _as_date(today or date.today())
    hierarchy = vacation_hierarchy()
    actor = vacation_actor(user, hierarchy)
    calendars = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
        )
        .filter_by(vacation_year=year)
        .order_by(
            StaffingVacationUnionCalendar.operation_unit_id,
            func.lower(StaffingVacationUnionCalendar.name),
            StaffingVacationUnionCalendar.id,
        )
        .all()
    )
    official_calendars = [
        calendar for calendar in calendars if calendar.calendar_type == "official"
    ]
    all_view_calendars = [
        calendar for calendar in calendars if calendar.calendar_type == "view_only"
    ]
    if all_view_calendars:
        shares = StaffingVacationUnionCalendarShare.query.options(
            joinedload(StaffingVacationUnionCalendarShare.recipient)
        ).filter(
            StaffingVacationUnionCalendarShare.calendar_id.in_(
                [calendar.id for calendar in all_view_calendars]
            )
        ).all()
        shares_by_calendar = {}
        for share in shares:
            shares_by_calendar.setdefault(share.calendar_id, []).append(share)
        for calendar in all_view_calendars:
            set_committed_value(
                calendar, "shares", shares_by_calendar.get(calendar.id, [])
            )
    owner_candidates = (
        management_calendar_users(hierarchy=hierarchy)
        if all_view_calendars
        else []
    )
    resolved_owner_by_calendar = {
        calendar.id: resolve_union_calendar_owner(
            calendar, candidates=owner_candidates, hierarchy=hierarchy
        )
        for calendar in all_view_calendars
    }
    visible_view_calendars = [
        calendar
        for calendar in all_view_calendars
        if can_view_union_calendar(calendar, user)
        or getattr(resolved_owner_by_calendar.get(calendar.id), "id", None)
        == getattr(user, "id", None)
    ]
    pool = _union_pool_data(
        year, hierarchy=hierarchy, calendars=official_calendars
    )
    union_conversions = []
    if pool["official_calendar_by_person"]:
        union_conversions = (
            StaffingVacationWeekConversion.query.options(
                selectinload(StaffingVacationWeekConversion.days)
            )
            .filter_by(vacation_year=year, program="union", recombined_at=None)
            .order_by(StaffingVacationWeekConversion.id)
            .all()
        )
    conversions_by_person = {}
    for conversion in union_conversions:
        conversions_by_person.setdefault(conversion.staffing_person_id, []).append(
            conversion
        )
    day_rows_by_person, floating_by_person = _vacation_day_rows_for_year(year)
    daily_usage = (
        _union_day_usage(year, pool)
        if pool["official_calendar_by_person"]
        else {}
    )
    weeks = vacation_year_weeks(year)

    calendar_rows = []
    for calendar in official_calendars:
        scope_ids = pool["scope_ids_by_calendar"].get(calendar.id, set())
        members = pool["official_members_by_calendar"].get(calendar.id, [])
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
        entry_status = _union_actor_entry_status(actor, calendar)
        daily_capacity = union_single_day_capacity(len(members))
        week_rows = []
        week_by_ending = {}
        for week in weeks:
            capacity = union_whole_week_capacity(
                len(members),
                year,
                week.week_ending,
            )
            used = pool["usage_by_calendar_week"].get(
                (calendar.id, week.week_ending),
                0,
            )
            week_row = {
                "week": week,
                "capacity": capacity,
                "used": used,
                "full": used >= capacity.capacity,
                "over": used > capacity.capacity,
            }
            week_rows.append(week_row)
            week_by_ending[week.week_ending] = week_row
        person_rows = []
        for person in members:
            selections = pool["active_selections_by_person"].get(person.id, [])
            entitlement = union_vacation_entitlement(person.seniority_date, year)
            regular_used = sum(
                1 for selection in selections if selection.bank_type == "regular"
            )
            optional_used = sum(
                1 for selection in selections if selection.bank_type == "optional"
            )
            person_conversions = conversions_by_person.get(person.id, [])
            optional_used += len(person_conversions)
            selection_rows = []
            for selection in selections:
                week_row = week_by_ending.get(selection.week_ending)
                started = _union_week_has_started(selection.week_ending, today)
                selection_rows.append(
                    {
                        "selection": selection,
                        "over": bool(week_row and week_row["over"]),
                        "started": started,
                        "can_review": bool(
                            selection.status == "pending"
                            and entry_status == "approved"
                        ),
                        "can_move": bool(
                            selection.status == "approved"
                            and entry_status == "approved"
                            and not started
                        ),
                        "can_correct": bool(
                            selection.status == "approved"
                            and entry_status == "approved"
                            and started
                        ),
                        "can_cancel": _union_can_cancel_selection(
                            actor,
                            entry_status,
                            selection,
                            user,
                        ),
                    }
                )
            person_rows.append(
                {
                    "person": person,
                    "entitlement": entitlement,
                    "regular_remaining": max(
                        0,
                        entitlement.regular_weeks - regular_used,
                    ),
                    "optional_remaining": max(
                        0,
                        entitlement.optional_weeks - optional_used,
                    ),
                    "selections": selection_rows,
                    "split_conversions": [
                        {
                            "conversion": conversion,
                            "scheduled_days": [
                                day for day in conversion.days if day.status == "scheduled"
                            ],
                            "remaining_days": 5
                            - sum(1 for day in conversion.days if day.status == "scheduled"),
                        }
                        for conversion in person_conversions
                    ],
                    "split_day_balance": sum(
                        5 - sum(1 for day in conversion.days if day.status == "scheduled")
                        for conversion in person_conversions
                    ),
                    "day_items": day_rows_by_person.get(person.id, []),
                    "optional_days_remaining": max(
                        0,
                        OPTIONAL_DAY_ENTITLEMENT
                        - sum(
                            row.item_type == "optional_day"
                            and optional_day_cycle(row.vacation_date)[0].year
                            == optional_day_cycle(date(year, 8, 1))[0].year
                            for row in day_rows_by_person.get(person.id, ())
                        ),
                    ),
                    "anniversary_available": max(
                        0,
                        1
                        - sum(
                            row.item_type == "anniversary_day"
                            for row in day_rows_by_person.get(person.id, ())
                        ),
                    ),
                    "floating_available": _available_floating_entitlements(
                        floating_by_person.get(person.id, ()),
                        day_rows_by_person.get(person.id, ()),
                    ),
                    "can_split_optional": entry_status == "approved",
                    "can_manage_split_days": entry_status == "approved",
                    "can_manage_days": entry_status == "approved",
                    "can_add": bool(
                        calendar.active
                        and entry_status
                        and (
                            regular_used < entitlement.regular_weeks
                            or optional_used < entitlement.optional_weeks
                        )
                    ),
                }
            )
        calendar_rows.append(
            {
                "calendar": calendar,
                "display_name": (
                    generated_official_calendar_name(
                        {scope.staffing_unit_id for scope in calendar.scopes},
                        calendar.include_part_time,
                        calendar.include_full_time,
                        hierarchy,
                    )
                    if calendar.calendar_type == "official"
                    else calendar.name
                ),
                "operation": hierarchy["by_id"].get(calendar.operation_unit_id),
                "department_label": department_label,
                "scope_ids": scope_ids,
                "scope_units": scope_units,
                "members": members,
                "person_rows": person_rows,
                "payroll_count": len(members),
                "single_day_capacity": union_single_day_capacity(len(members)),
                "daily_usage": {
                    day: used
                    for (calendar_id, day), used in daily_usage.items()
                    if calendar_id == calendar.id
                },
                "daily_over": any(
                    used > daily_capacity.capacity
                    for (calendar_id, _day), used in daily_usage.items()
                    if calendar_id == calendar.id
                ),
                "can_edit": _can_manage_official_calendar(actor, scope_ids),
                "entry_status": entry_status,
                "can_override": entry_status == "approved",
                "can_review": entry_status == "approved",
                "week_rows": week_rows,
                "over": any(row["over"] for row in week_rows),
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
    view_members = _members_by_calendar(visible_view_calendars, hierarchy)
    view_rows = []
    for calendar in visible_view_calendars:
        owner = resolved_owner_by_calendar.get(calendar.id)
        view_rows.append(
            {
                "calendar": calendar,
                "owner": owner,
                "scope_label": union_calendar_scope_label(calendar, hierarchy),
                "scope_summary": union_calendar_scope_label(
                    calendar, hierarchy, full=False
                ),
                "members": view_members.get(calendar.id, []),
                "payroll_count": len(view_members.get(calendar.id, [])),
                "can_edit": bool(
                    _actor_has_vacation_mutation_access(actor)
                    and (
                        actor.is_grandmaster
                        or getattr(owner, "id", None) == getattr(user, "id", None)
                    )
                ),
                "shared_recipients": [share.recipient for share in calendar.shares],
            }
        )
    user_id = getattr(user, "id", None)
    my_view_rows = [
        row for row in view_rows if row["calendar"].owner_user_id == user_id
    ]
    shared_view_rows = [
        row for row in view_rows if row["calendar"].owner_user_id != user_id
    ]
    owned_view_count = sum(
        calendar.owner_user_id == user_id for calendar in all_view_calendars
    )
    return {
        "vacation_year": year,
        "today": today,
        "weeks": weeks,
        "calendars": calendar_rows,
        "browser": browser,
        "operations": [
            unit for unit in hierarchy["units"] if unit.unit_type == "operation"
        ],
        "hierarchy": hierarchy,
        "actor": actor,
        "my_view_calendars": my_view_rows,
        "shared_view_calendars": shared_view_rows,
        "can_create_official": bool(
            _actor_has_vacation_mutation_access(actor)
            and (
                actor.is_grandmaster
                or (
                    actor.person
                    and actor.person.classification
                    in VACATION_OFFICIAL_CALENDAR_OWNER_CLASSIFICATIONS
                    and actor.sideways_scope_ids
                )
            )
        ),
        "can_create_view": bool(
            _can_own_view_calendar(actor)
            and owned_view_count < VACATION_VIEW_CALENDAR_LIMIT
        ),
        "owned_view_count": owned_view_count,
        "view_limit": VACATION_VIEW_CALENDAR_LIMIT,
        "is_grandmaster": actor.is_grandmaster,
        "can_create": bool(
            _actor_has_vacation_mutation_access(actor)
            and (actor.is_grandmaster or actor.sideways_scope_ids)
        ),
        "carry_forward_candidates": official_calendar_carry_forward_candidates(
            user, today=today
        ),
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


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError("Choose a valid lifecycle date.")


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


def _management_pinned_people_for_area(area, primary_assignments, hierarchy):
    """Return the established one-level-up people whose absence reduces a pool."""
    target_classification = {
        "department": "full_time_supervisor",
        "operation": "manager",
        "sort": "division_manager",
    }.get(area.unit_type)
    if not target_classification:
        return []
    people = []
    for assignment in primary_assignments.values():
        if assignment.person.classification != target_classification:
            continue
        assigned_unit = hierarchy["by_id"].get(assignment.unit_id)
        if not assigned_unit:
            continue
        if area.unit_type == "department":
            oversees = _is_descendant_or_self(
                area.id, assigned_unit.id, hierarchy
            )
        elif area.unit_type == "operation":
            oversees = bool(
                _ancestor_of_type(assigned_unit, "operation", hierarchy)
                and _ancestor_of_type(assigned_unit, "operation", hierarchy).id
                == area.id
            )
        else:
            assigned_sort = _ancestor_of_type(assigned_unit, "sort", hierarchy)
            oversees = bool(assigned_sort and assigned_sort.id == area.id)
        if oversees:
            people.append(assignment.person)
    return _seniority_order(people)


def _pinned_week_statuses(
    pinned_people,
    weeks,
    selections_by_person,
    day_rows_by_person,
):
    statuses = {week.week_ending: {} for week in weeks}
    week_by_date = {
        week.start_date + timedelta(days=offset): week.week_ending
        for week in weeks
        for offset in range(7)
    }
    pinned_ids = {person.id for person in pinned_people}
    for person in pinned_people:
        for selection in selections_by_person.get(person.id, ()):
            week_ending = getattr(selection, "week_ending", None)
            if week_ending in statuses:
                statuses[week_ending].setdefault(person.id, set()).add("vacation")
    for person_id in pinned_ids:
        for row in day_rows_by_person.get(person_id, ()):
            week_ending = week_by_date.get(row.vacation_date)
            if not week_ending:
                continue
            label = (
                row.item_type
                if row.item_type in VACATION_AVAILABILITY_ITEM_TYPES
                else "vacation"
            )
            statuses[week_ending].setdefault(person_id, set()).add(label)
    return statuses


def _pinned_person_availability(
    person,
    selections_by_person,
    day_rows_by_person,
    *,
    today=None,
):
    today = today or date.today()
    rows = []
    for selection in selections_by_person.get(person.id, ()):
        week_ending = getattr(selection, "week_ending", None)
        if week_ending:
            rows.append(
                {
                    "kind": "vacation",
                    "label": "VACATION",
                    "date": week_ending,
                    "selection": selection,
                    "started": _management_week_has_started(
                        week_ending, today
                    ),
                }
            )
    labels = {
        "special_assignment": "SPECIAL ASSIGNMENT",
        "corporate_class": "CORPORATE CLASS",
    }
    for row in day_rows_by_person.get(person.id, ()):
        if row.item_type in VACATION_AVAILABILITY_ITEM_TYPES:
            rows.append(
                {
                    "kind": row.item_type,
                    "label": labels[row.item_type],
                    "date": row.vacation_date,
                    "day": row,
                }
            )
        elif row.item_type in VACATION_DAY_ITEM_TYPES or row.item_type == "split_vacation":
            rows.append(
                {
                    "kind": "vacation",
                    "label": "VACATION",
                    "date": row.vacation_date,
                    "day": row,
                }
            )
    return sorted(rows, key=lambda item: (item["date"], item["label"]))


def _management_people_for_area(area_id, hierarchy, *, leadership_rows=None):
    primary, _secondary = _primary_and_secondary_assignments(
        leadership_rows or _management_leadership_rows()
    )
    people = []
    for assignment in primary.values():
        if assignment.person.classification == "division_manager":
            continue
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
    conversions = StaffingVacationWeekConversion.query.filter_by(
        vacation_year=normalize_vacation_year(vacation_year),
        program="management",
        recombined_at=None,
    ).all()
    for row in conversions:
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
        if assignment.person.classification == "division_manager":
            continue
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
            calendar.scopes.remove(scope)
    for unit_id in sorted(scope_ids - set(existing)):
        calendar.scopes.append(
            StaffingVacationUnionCalendarScope(staffing_unit_id=unit_id)
        )


def _official_calendar_definition_key(calendar):
    return (
        calendar.operation_unit_id,
        bool(calendar.include_part_time),
        bool(calendar.include_full_time),
        frozenset(scope.staffing_unit_id for scope in calendar.scopes),
    )


def _locked_union_calendar_definition(calendar):
    calendar_id = _positive_int(getattr(calendar, "id", calendar), "Union calendar")
    row = StaffingVacationUnionCalendar.query.options(
        selectinload(StaffingVacationUnionCalendar.scopes),
        selectinload(StaffingVacationUnionCalendar.shares),
    ).filter_by(id=calendar_id).with_for_update().first()
    if not row:
        raise ValueError("The selected Union vacation calendar was not found.")
    return row


def _can_manage_official_calendar(actor, scope_ids):
    return bool(
        _actor_has_vacation_mutation_access(actor)
        and (
            actor.is_grandmaster
            or (
                actor.person
                and actor.person.classification
                in VACATION_OFFICIAL_CALENDAR_OWNER_CLASSIFICATIONS
                and can_edit_union_scope(actor, scope_ids)
            )
        )
    )


def _can_own_view_calendar(actor):
    return bool(
        _actor_has_vacation_mutation_access(actor)
        and (
            actor.is_grandmaster
            or (
                actor.person
                and actor.person.classification
                in VACATION_VIEW_CALENDAR_OWNER_CLASSIFICATIONS
            )
        )
    )


def _can_edit_view_calendar(calendar, user):
    try:
        require_vacation_mutation_access(user)
    except ValueError:
        return False
    if getattr(user, "role", None) == "grandmaster" or get_user_app_role(
        user, "neostaffing"
    ) == "grandmaster":
        return True
    owner = resolve_union_calendar_owner(calendar)
    return getattr(owner, "id", None) == getattr(user, "id", None)


def _membership_definition(scope_ids, include_pt, include_ft, hierarchy):
    classifications = set()
    if include_pt:
        classifications.update(VACATION_PT_CLASSIFICATIONS)
    if include_ft:
        classifications.update(VACATION_FT_CLASSIFICATIONS)
    return {
        "work_area_ids": _scope_work_area_ids(set(scope_ids), hierarchy),
        "classifications": classifications,
    }


def _membership_matches(person, assignment, definition):
    return bool(
        person.classification in definition["classifications"]
        and assignment.work_area_unit_id in definition["work_area_ids"]
    )


def _active_union_people_with_assignments():
    rows = (
        db.session.query(StaffingPerson, StaffingWorkAssignment)
        .join(
            StaffingWorkAssignment,
            StaffingWorkAssignment.person_id == StaffingPerson.id,
        )
        .filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(VACATION_UNION_CLASSIFICATIONS),
            StaffingWorkAssignment.active.is_(True),
        )
        .all()
    )
    return rows


def _members_by_calendar(calendars, hierarchy):
    definitions = {
        calendar.id: _membership_definition(
            {scope.staffing_unit_id for scope in calendar.scopes},
            calendar.include_part_time,
            calendar.include_full_time,
            hierarchy,
        )
        for calendar in calendars
    }
    result = {calendar.id: [] for calendar in calendars}
    people = _active_union_people_with_assignments() if calendars else []
    for calendar in calendars:
        result[calendar.id] = _seniority_order(
            {
                person.id: person
                for person, assignment in people
                if _membership_matches(person, assignment, definitions[calendar.id])
            }.values()
        )
    return result


def _highest_union_scope_ids(scope_ids, operation_id, hierarchy):
    """Collapse selected coverage to the highest complete accurate Org nodes."""
    target_work_areas = _scope_work_area_ids(set(scope_ids), hierarchy)
    operation_work_areas = _scope_work_area_ids({operation_id}, hierarchy)
    if target_work_areas and target_work_areas == operation_work_areas:
        return {operation_id}
    result = set()
    remaining = set(target_work_areas)
    operation = hierarchy["by_id"][operation_id]
    departments = [
        unit
        for unit in hierarchy["units"]
        if unit.unit_type == "department"
        and _is_descendant_or_self(unit.id, operation.id, hierarchy)
    ]
    for department in departments:
        department_areas = _scope_work_area_ids({department.id}, hierarchy)
        if department_areas and department_areas.issubset(remaining):
            result.add(department.id)
            remaining.difference_update(department_areas)
    result.update(remaining)
    return result


def _ordered_scope_units(scope_ids, hierarchy):
    positions = {unit.id: index for index, unit in enumerate(hierarchy["units"])}
    return sorted(
        (hierarchy["by_id"][unit_id] for unit_id in scope_ids if unit_id in hierarchy["by_id"]),
        key=lambda unit: (positions.get(unit.id, 10**9), unit.id),
    )


def union_calendar_scope_label(calendar, hierarchy=None, *, full=True):
    hierarchy = hierarchy or vacation_hierarchy()
    labels = [
        unit.name
        for unit in _ordered_scope_units(
            {scope.staffing_unit_id for scope in calendar.scopes}, hierarchy
        )
    ]
    if full or len(labels) <= 3:
        return ", ".join(labels)
    return ", ".join(labels[:3] + [f"+{len(labels) - 3} more"])


def _loaded_vacation_actor(
    user,
    app_role,
    person,
    leadership_rows,
    hierarchy,
    *,
    is_grandmaster=False,
):
    if is_grandmaster:
        all_ids = frozenset(hierarchy["by_id"])
        return VacationActor(app_role, person, True, all_ids, all_ids, all_ids)
    if not person:
        return VacationActor(app_role, None, False, frozenset(), frozenset(), frozenset())
    roots = {row.unit_id for row in leadership_rows if row.unit_id in hierarchy["by_id"]}
    normal = _descendant_ids(roots, hierarchy)
    sideways = set(normal)
    if ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]:
        sibling_roots = {
            sibling.id
            for root_id in roots
            for sibling in hierarchy["children"].get(
                hierarchy["by_id"][root_id].parent_id, ()
            )
        }
        sideways.update(_descendant_ids(sibling_roots, hierarchy))
    management_ids = set()
    for assignment in leadership_rows:
        unit = hierarchy["by_id"].get(assignment.unit_id)
        if not unit:
            continue
        management_ids.update(
            _management_capacity_ids_for_assignment(person, unit, hierarchy)
        )
        if ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["master"]:
            for sibling in hierarchy["children"].get(unit.parent_id, ()):
                management_ids.update(
                    _management_capacity_ids_for_assignment(person, sibling, hierarchy)
                )
    return VacationActor(
        app_role,
        person,
        False,
        frozenset(normal),
        frozenset(sideways),
        frozenset(management_ids),
    )


def _calendar_owner_candidate_allowed(calendar, candidate, scope_ids):
    actor = candidate["actor"]
    if actor.is_grandmaster:
        return True
    classification = getattr(candidate["person"], "classification", None)
    required = (
        VACATION_OFFICIAL_CALENDAR_OWNER_CLASSIFICATIONS
        if calendar.calendar_type == "official"
        else VACATION_VIEW_CALENDAR_OWNER_CLASSIFICATIONS
    )
    return classification in required and can_edit_union_scope(actor, scope_ids)


def _calendar_owner_priority(candidate):
    person = candidate["person"]
    classification_rank = {
        "division_manager": 5,
        "manager": 4,
        "full_time_specialist": 3,
        "full_time_supervisor": 2,
        "part_time_supervisor": 1,
    }
    return (
        -classification_rank.get(getattr(person, "classification", None), 0),
        getattr(person, "seniority_date", None) or date.max,
        str(getattr(person, "last_name", "") or "").casefold(),
        str(getattr(person, "first_name", "") or "").casefold(),
        candidate["user"].id,
    )


def _grandmaster_owner_priority(candidate):
    person = candidate["person"]
    return (
        getattr(person, "seniority_date", None) or date.max,
        str(getattr(person, "last_name", "") or "").casefold(),
        str(getattr(person, "first_name", "") or "").casefold(),
        candidate["user"].id,
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
