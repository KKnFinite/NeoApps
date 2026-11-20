"""Read-only projections for the NeoStaffing workspace."""
from collections import defaultdict
from datetime import date, timedelta
import json

from flask import request, session
from sqlalchemy import func, or_

from app.models import (
    SortDateOperation, StaffingDailyAttendance, StaffingPerson, StaffingTimecardSlice,
    StaffingUnit, StaffingWorkAssignment,
)
from app.services import neostaffing as staffing
from app.services.access_control import user_can_access_app
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services.neostaffing_attendance_authority import attendance_authority

SCOPE_KEYS = ("sort_id", "operation_id", "department_id", "work_area_id")


def organization_filters(scope, *, default_sort=True):
    values = {}
    if scope["sort"] and (default_sort or scope["selected"]):
        values["sort_id"] = str(scope["sort"].id)
    if scope["selected"]:
        values[scope["selected"].unit_type + "_id"] = str(scope["selected"].id)
    return values


def authorized_areas(scope):
    return {unit.id for unit in scope["options"] if unit.unit_type == "work_area"}


def workspace_scope(user):
    cache_key = "neoapps.staffing.workspace_scope"
    if cache_key in request.environ:
        return request.environ[cache_key]
    hierarchy = staffing._daily_attendance_hierarchy()
    units = hierarchy["by_id"]
    gateway = staffing._attendance_gateway()
    operations = staffing.current_operations_for_gateway(gateway) if gateway else []
    operations = {staffing._normalize_staffing_sort_name(item.sort_name): item for item in operations}
    authority = attendance_authority(user, hierarchy)
    master = user_can_access_app(user, "neostaffing", minimum_role="master")
    # Unlinked read-only viewers retain the ordinary NeoStaffing view policy.
    # A linked manager whose grants are removed has no managerial area scope.
    areas = {unit.id for unit in units.values() if unit.unit_type == "work_area"
             and (master or authority.person_id is None or unit.id in authority.work_area_ids)}
    descendants = {
        unit.id: staffing._daily_attendance_work_area_ids(unit, hierarchy) & areas
        for unit in hierarchy["units"]
    }
    sorts = [unit for unit in hierarchy["units"] if unit.unit_type == "sort" and descendants[unit.id]]
    current_sorts = [unit for unit in sorts if staffing._normalize_staffing_sort_name(unit.name) in operations]
    gateway_code = gateway.code if gateway else ""
    saved = session.get("staffing_scope", {})
    selected = units.get(saved.get("id"))
    if selected and not descendants.get(selected.id):
        selected = None
    from app.services.neostaffing_assignments import sort_of

    def sort_operation(unit):
        return operations.get(staffing._normalize_staffing_sort_name(unit.name)) if unit else None

    if selected:
        selected_operation = sort_operation(sort_of(selected, units))
        # Switch only when the selected Sort identity changes, not at midnight
        # or on rollover of a different configured window.
        stamp = [user.id, gateway_code, getattr(selected_operation, "id", None)]
        if saved.get("stamp") != stamp:
            selected = None
    default_sorts = current_sorts or sorts
    sort = sort_of(selected, units) if selected else next(
        (unit for unit in default_sorts if staffing._normalize_staffing_sort_name(unit.name) == "night"),
        next(iter(default_sorts), None),
    )
    operation = sort_operation(sort)
    selected_areas = descendants[selected.id] if selected else areas
    active_areas = selected_areas & descendants.get(sort.id, set()) if sort else set()
    options = [unit for unit in hierarchy["units"] if descendants[unit.id]]
    scope = {
        "selected": selected, "sort": sort, "operation": operation,
        "areas": selected_areas, "active_areas": active_areas,
        "options": options, "gateway_code": gateway_code, "authority": authority,
        "operations": operations, "descendants": descendants,
        "hierarchy": hierarchy, "master": master,
    }
    request.environ[cache_key] = scope
    return scope


def set_workspace_scope(user, selected_id):
    scope = workspace_scope(user)
    if selected_id is not None and selected_id not in {unit.id for unit in scope["options"]}:
        raise ValueError("Select an area within your staffing scope.")
    from app.services.neostaffing_assignments import sort_of

    units = scope["hierarchy"]["by_id"]
    sort = sort_of(units.get(selected_id), units)
    operation = scope["operations"].get(staffing._normalize_staffing_sort_name(sort.name)) if sort else None
    session["staffing_scope"] = {
        "id": selected_id, "stamp": [user.id, scope["gateway_code"], getattr(operation, "id", None)],
    }


def live_staffing(scope):
    operation, sort, hierarchy = (scope[key] for key in ("operation", "sort", "hierarchy"))
    areas = scope["active_areas"]
    assignments = staffing._daily_attendance_assignments(
        sort, sort, hierarchy, work_area_ids=areas,
    ) if areas else []
    records = staffing._daily_attendance_records(
        {row.person_id for row in assignments}, operation, sort,
    ) if operation and assignments else {}
    rows = staffing._daily_attendance_rows(assignments, records, hierarchy)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["work_area"].id].append(row)
    # Include empty authorized areas so gaps in planned coverage are visible.
    coverage = []
    for unit in hierarchy["units"]:
        if unit.id not in areas:
            continue
        members = list({row["person"].id: row for row in grouped[unit.id]}.values())
        coverage.append({
            "unit": unit, "rows": members, "assigned": len(members),
            "here": sum(row["status"] == "here" for row in members) if operation else None,
            "shift": staffing.assignment_service.is_shift(unit, hierarchy["by_id"]),
        })
    summary = staffing._daily_attendance_summary(
        {row["person"].id: row for row in rows}.values()
    )
    return coverage, summary


def overview_coverage(scope, coverage):
    if scope["selected"] and scope["selected"].unit_type in ("department", "work_area"):
        return coverage
    units = scope["hierarchy"]["by_id"]
    grouped = defaultdict(dict)
    for area in coverage:
        parent = units.get(area["unit"].parent_id) or area["unit"]
        grouped[parent.id].update((row["person"].id, row["status"]) for row in area["rows"])
    return [
        {"unit": units[unit_id], "assigned": len(statuses),
         "here": sum(status == "here" for status in statuses.values()) if scope["operation"] else None}
        for unit_id, statuses in grouped.items()
    ]


def needs_assignment(scope):
    # A permanent unassigned employee has no trustworthy local Sort/area
    # attribution. Only the app-wide directory scope can include these rows.
    if not scope["master"] or scope["selected"]:
        return []
    return StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        StaffingPerson.classification.in_(staffing.NON_MANAGEMENT_CLASSIFICATIONS),
        ~StaffingPerson.work_assignments.any(StaffingWorkAssignment.active.is_(True)),
    ).order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id).all()


def report_dates(start, end, fallback):
    dates = []
    for value in (start, end):
        try:
            dates.append(date.fromisoformat(value) if value else fallback)
        except ValueError:
            raise ValueError("Enter valid report dates.") from None
    if dates[0] > dates[1] or dates[1] - dates[0] > timedelta(days=366):
        raise ValueError("Select a date range of up to one year.")
    return tuple(dates)


def _retained_sources():
    return (
        (StaffingTimecardSlice, StaffingTimecardSlice.workday_date, StaffingTimecardSlice.attendance_status),
        (StaffingDailyAttendance, StaffingDailyAttendance.attendance_date, StaffingDailyAttendance.status),
    )


def _retained_query(scope, model, day, status):
    return model.query.join(
        SortDateOperation, SortDateOperation.id == model.sort_date_operation_id,
    ).filter(SortDateOperation.gateway_code == scope["gateway_code"], SortDateOperation.sort_date == day,
             model.work_area_unit_id.in_(scope["areas"]), status.in_(staffing.ATTENDANCE_STATUS_LABELS))


def earliest_retained_evidence(scope, person_id=None):
    """The first linked record still present in this scope, without asserting continuity."""
    if not scope["gateway_code"] or not scope["areas"]:
        return None
    minima = []
    for model, day, status in _retained_sources():
        query = _retained_query(scope, model, day, status)
        if person_id is not None:
            query = query.filter(model.person_id == person_id)
        minima.append(query.with_entities(func.min(day)).scalar())
    return min((value for value in minima if value is not None), default=None)


def recorded_attendance(scope, start, end, *, person_id=None, classification="", details=False):
    """Return actual marks with original area attribution, including retained timecards."""
    gateway = scope["gateway_code"]
    areas = scope["areas"]
    if not gateway or not areas:
        return []
    records = {}
    snapshots = {}
    for model, day, status in _retained_sources():
        query = _retained_query(scope, model, day, status).filter(day.between(start, end))
        if person_id is not None:
            query = query.filter(model.person_id == person_id)
        for row in query.all():
            record_day = row.workday_date if model is StaffingTimecardSlice else row.attendance_date
            record_status = row.attendance_status if model is StaffingTimecardSlice else row.status
            key = (row.sort_date_operation_id, row.person_id)
            if model is StaffingTimecardSlice and (classification or details):
                snapshots[key] = json.loads(row.context_json)
            records[key] = (record_day, row.work_area_unit_id, row.person_id, record_status)
    retained = sorted(
        (value, key) for key, value in records.items()
        if not classification or snapshots.get(key, {}).get("classification_at_recording") == classification
    )
    if details:
        return [(value, snapshots.get(key, {})) for value, key in retained]
    return [value for value, _ in retained]


def attendance_history_people(scope, query, classification, start, end):
    if not query.strip() or not scope["areas"]:
        return []
    people = StaffingPerson.query
    if classification:
        people = people.filter_by(classification=classification)
    for word in query.strip().split()[:8]:
        people = people.filter(or_(
            StaffingPerson.first_name.icontains(word, autoescape=True),
            StaffingPerson.last_name.icontains(word, autoescape=True),
            StaffingPerson.employee_id.icontains(word, autoescape=True),
        ))
    current_assignment = StaffingPerson.work_assignments.any(
        StaffingWorkAssignment.active.is_(True)
        & StaffingWorkAssignment.work_area_unit_id.in_(scope["areas"])
    )
    retained_ids = []
    for model, day, status in _retained_sources():
        retained_ids.append(_retained_query(scope, model, day, status)
                            .filter(day.between(start, end)).with_entities(model.person_id).statement)
    return people.filter(or_(current_assignment, *(StaffingPerson.id.in_(ids) for ids in retained_ids))).order_by(
        StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id,
    ).limit(30).all()


def reports(scope, args):
    view = args.get("report_type", "staffing")
    if view not in {"staffing", "attendance", "seniority"}:
        view = "staffing"
    from app.services.neostaffing_assignments import sort_of

    # Explicit links retain their scope throughout the report workflow.
    keys = {key: args[key] for key in SCOPE_KEYS if args.get(key)}
    units = scope["hierarchy"]["by_id"]
    for key in reversed(SCOPE_KEYS):
        if key not in keys:
            continue
        unit = units.get(args.get(key, type=int))
        if unit and key == unit.unit_type + "_id":
            ancestry = {}
            ancestor = unit
            while ancestor and ancestor.unit_type not in ancestry:
                ancestry[ancestor.unit_type] = str(ancestor.id)
                ancestor = units.get(ancestor.parent_id)
            sort = sort_of(unit, units)
            compatible = all(ancestry.get(name.removesuffix("_id")) == value for name, value in keys.items())
            scope = {**scope, "selected": unit, "sort": sort,
                     "operation": scope["operations"].get(staffing._normalize_staffing_sort_name(sort.name)) if sort else None,
                     "areas": scope["descendants"].get(unit.id, set()) if compatible else set()}
        else:
            scope = {**scope, "areas": set()}
        break
    classification = args.get("classification", "")
    if classification not in dict(staffing.classification_choices()):
        classification = ""
    operation = scope["operation"]
    today = operation.sort_date if operation else current_gateway_local_datetime().date()
    result = {"view": view, "classification": classification, "date": today, "scope_args": keys,
              "scope_label": staffing.unit_path(scope["selected"]) if scope["selected"] else "All My Areas",
              "from_date": today, "through_date": today, "rows": [], "query": "", "people": []}
    if view == "seniority":
        filters = {**organization_filters(scope), "classification": classification}
        filters["active"] = args.get("active", "active")
        filters["include_management"] = args.get("include_management", "")
        result["groups"] = []
        for unit in scope["options"]:
            if unit.unit_type != "operation" or (scope["sort"] and unit.parent_id != scope["sort"].id):
                continue
            if filters.get("operation_id") and int(filters["operation_id"]) != unit.id:
                continue
            ranked = staffing.seniority_context({**filters, "operation_id": unit.id})
            visible_rows = []
            for row in ranked["rows"]:
                row_unit = row["scope"]
                if row_unit.id in scope["areas"] or (
                    row["source"] == "leadership_assignment" and scope["descendants"].get(row_unit.id, set()) <= scope["areas"]
                    and scope["descendants"].get(row_unit.id)
                ):
                    visible_rows.append(row)
            if visible_rows:
                result["groups"].append((unit.name, visible_rows))
        return result
    if view == "staffing":
        try:
            report_day, _ = report_dates(args.get("attendance_date"), args.get("attendance_date"), today)
        except ValueError as error:
            result["error"] = str(error)
            return result
        result["date"] = report_day
        groups = defaultdict(dict)
        retained = recorded_attendance(scope, report_day, report_day, classification=classification, details=True)
        named_areas = scope["areas"] if scope["master"] else scope["authority"].work_area_ids
        ids = {person_id for (_, area_id, person_id, _), snapshot in retained
               if area_id in named_areas and not snapshot.get("employee")}
        names = {person.id: person.full_name for person in StaffingPerson.query.filter(StaffingPerson.id.in_(ids)).all()} if ids else {}
        for (_, area_id, person_id, status), snapshot in retained:
            groups[area_id][person_id] = {
                "status": status,
                "name": (snapshot.get("employee") or names.get(person_id)) if area_id in named_areas else None,
                "classification": staffing.CLASSIFICATION_LABELS.get(snapshot.get("classification_at_recording")),
            }
        result["rows"] = [
            {"name": staffing.unit_path(units[area_id]), "recorded": len(people),
             "here": sum(row["status"] == "here" for row in people.values()),
             "people": sorted((row for row in people.values() if row["name"]), key=lambda row: row["name"].casefold())}
            for area_id, people in groups.items()
        ]
        result["earliest"] = earliest_retained_evidence(scope)
        result["coverage"] = (
            "Totals include only records with classification captured at the time. Older classification detail may be unavailable."
            if classification else "Headcounts cover recorded attendance. The full historical assigned roster is unavailable."
        )
        return result
    result["query"] = args.get("search", "")[:140]
    if not scope["master"]:
        scope = {**scope, "areas": scope["areas"] & scope["authority"].work_area_ids}
        if not scope["areas"] and result["query"]:
            result["coverage"] = "An active management area is required to view individual attendance history."
            return result
    try:
        legacy_day = args.get("attendance_date")
        start, end = report_dates(args.get("from_date", legacy_day), args.get("through_date", legacy_day), today)
    except ValueError as error:
        result["error"] = str(error)
        return result
    result.update(from_date=start, through_date=end)
    result["people"] = attendance_history_people(scope, result["query"], classification, start, end)
    person_id = args.get("person_id", type=int)
    selected = next((person for person in result["people"] if person.id == person_id), None)
    if selected:
        result["person"] = selected
        result["rows"] = recorded_attendance(scope, start, end, person_id=selected.id)
        result["earliest"] = earliest_retained_evidence(scope, person_id=selected.id)
        result["coverage"] = (
            "Dates without a retained record may be outside the attendance and timecard retention windows."
            if start < today else "Attendance history shows recorded statuses only."
        )
    return result
