"""Read-only projections for the NeoStaffing workspace."""
from collections import defaultdict
from datetime import date, timedelta
import json

from flask import request, session
from sqlalchemy import and_, func, or_, select

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingAttendanceOutcome as Outcome, StaffingDailyAttendance, StaffingPerson,
    StaffingRosterClose as Close, StaffingRosterPlacement as Placement, StaffingTimecardSlice,
    StaffingUnit, StaffingWorkAssignment,
)
from app.services import neostaffing as staffing
from app.services.access_control import user_can_access_app
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services.neostaffing_attendance_authority import attendance_authority
from app.services.neostaffing_historical_records import enabled as history_enabled
from app.services.neostaffing_history_retention import history_cutoff

SCOPE_KEYS = ("sort_id", "operation_id", "department_id", "work_area_id")
BOARD_VIEW_PERMISSION = "neostaffing.board.view"


def organization_filters(scope, *, default_sort=True):
    values = {}
    if scope["sort"] and (default_sort or scope["selected"]):
        values["sort_id"] = str(scope["sort"].id)
    if scope["selected"]:
        values[scope["selected"].unit_type + "_id"] = str(scope["selected"].id)
    return values


def authorized_areas(scope):
    return {unit.id for unit in scope["options"] if unit.unit_type == "work_area"}


def shift_board_area_ids(scope, area_ids):
    units = scope["hierarchy"]["by_id"]
    return {area_id for area_id in area_ids if staffing.assignment_service.is_shift(units[area_id], units)}


def general_board_area_ids(scope):
    areas = scope["active_areas"]
    return areas - shift_board_area_ids(scope, areas)


def board_scope_stamp(scope):
    return json.dumps([scope["gateway_code"], getattr(scope["sort"], "id", None),
                       getattr(scope["operation"], "id", None), getattr(scope["selected"], "id", None)],
                      separators=(",", ":"))


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
    picker_groups = defaultdict(list)
    for unit in options:
        picker_groups[unit.parent_id].append(unit)
    picker_levels = [
        {
            "id": unit.id,
            "parent_id": unit.parent_id,
            "name": unit.name,
            "path": staffing._daily_attendance_unit_path(unit, hierarchy),
            "children": picker_groups[unit.id],
        }
        for unit in options if unit.id in picker_groups
    ]
    scope = {
        "selected": selected, "sort": sort, "operation": operation,
        "selected_path": staffing._daily_attendance_unit_path(selected, hierarchy) if selected else "All My Areas",
        "picker_roots": picker_groups[None], "picker_levels": picker_levels,
        "picker_branch_ids": {level["id"] for level in picker_levels},
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
    if history_enabled():
        return ()  # Under the new policy, legacy sources must pass the import checks.
    return (
        (StaffingTimecardSlice, StaffingTimecardSlice.workday_date, StaffingTimecardSlice.attendance_status),
        (StaffingDailyAttendance, StaffingDailyAttendance.attendance_date, StaffingDailyAttendance.status),
    )


def _retained_query(scope, model, day, status):
    return model.query.join(
        SortDateOperation, SortDateOperation.id == model.sort_date_operation_id,
    ).filter(SortDateOperation.gateway_code == scope["gateway_code"], SortDateOperation.sort_date == day,
             model.work_area_unit_id.in_(scope["areas"]), status.in_(staffing.ATTENDANCE_STATUS_LABELS))


def _outcomes(scope):
    return Outcome.query.filter(Outcome.gateway_code == scope["gateway_code"],
                                Outcome.work_area_id.in_(scope["areas"]), Outcome.status.is_not(None),
                                Outcome.sort_date >= (scope.get("history_floor") or history_cutoff()))


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
    if history_enabled():
        outcomes = _outcomes(scope)
        if person_id is not None:
            outcomes = outcomes.filter(Outcome.person_id == person_id)
        minima.append(outcomes.with_entities(func.min(Outcome.sort_date)).scalar())
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
            if details:
                snapshots.setdefault(key, {})["operation_id"] = row.sort_date_operation_id
            records[key] = (record_day, row.work_area_unit_id, row.person_id, record_status)
    if history_enabled():
        query = _outcomes(scope).filter(Outcome.sort_date.between(start, end))
        if person_id is not None:
            query = query.filter(Outcome.person_id == person_id)
        if classification:
            query = query.filter(Outcome.classification == classification)
        for row in query:
            key = (row.sort_date_operation_id, row.person_id)
            records[key] = (row.sort_date, row.work_area_id, row.person_id, row.status)
            snapshots[key] = {"employee": row.employee_name, "classification_at_recording": row.classification,
                              "operation_id": row.sort_date_operation_id, "sort_name": row.sort_name}
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
    historical = history_enabled()
    if classification and not historical:
        people = people.filter_by(classification=classification)
    words = query.strip().split()[:8]
    outcomes = _outcomes(scope).filter(Outcome.sort_date.between(start, end)) if historical else None
    if historical and classification:
        outcomes = outcomes.filter_by(classification=classification)
    for word in words:
        people = people.filter(or_(
            StaffingPerson.first_name.icontains(word, autoescape=True),
            StaffingPerson.last_name.icontains(word, autoescape=True),
            StaffingPerson.employee_id.icontains(word, autoescape=True),
        ))
        if historical:
            outcomes = outcomes.filter(or_(Outcome.employee_name.icontains(word, autoescape=True),
                                           Outcome.employee_id.icontains(word, autoescape=True)))
    current_assignment = StaffingPerson.work_assignments.any(
        StaffingWorkAssignment.active.is_(True)
        & StaffingWorkAssignment.work_area_unit_id.in_(scope["areas"])
    )
    retained_ids = []
    for model, day, status in _retained_sources():
        retained = _retained_query(scope, model, day, status).filter(day.between(start, end))
        retained_ids.append(retained.with_entities(model.person_id).statement)
    authorized = [StaffingPerson.id.in_(ids) for ids in retained_ids]
    if not historical or not classification:
        authorized.append(current_assignment)
    live_matches = people.filter(or_(*authorized)).order_by(
        StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id,
    ).limit(30).all() if authorized else []
    if not historical:
        return live_matches
    # Pick one matching historical identity per employee, even after a name or ID edit.
    matches = outcomes.with_entities(
        Outcome.person_id.label("id"), Outcome.employee_id, Outcome.employee_name.label("full_name"),
        func.row_number().over(partition_by=Outcome.person_id, order_by=(
            Outcome.sort_date.desc(), Outcome.sort_date_operation_id.desc())).label("rank"),
    ).subquery()
    saved_matches = db.session.execute(
        select(matches.c.id, matches.c.employee_id, matches.c.full_name).where(matches.c.rank == 1)
        .order_by(matches.c.full_name, matches.c.id).limit(30)
    ).all()
    combined = {person.id: person for person in live_matches}
    combined.update((person.id, person) for person in saved_matches)
    return sorted(combined.values(), key=lambda person: (person.full_name.casefold(), person.id))[:30]


def _dated_staffing(scope, report_day, classification):
    """Authoritative close snapshots plus explicit coverage for missing operations."""
    if not scope["gateway_code"] or not scope["areas"] or report_day < scope["history_floor"]:
        return [], set(), set()
    units = scope["hierarchy"]["by_id"]
    from app.services.neostaffing_assignments import sort_of
    sorts = {sort.id: sort for area_id in scope["areas"] if (sort := sort_of(units.get(area_id), units))}
    normalize = staffing._normalize_staffing_sort_name
    sort_names = {normalize(sort.name) for sort in sorts.values()}
    expected = {op_id for op_id, name in SortDateOperation.query.with_entities(
        SortDateOperation.id, SortDateOperation.sort_name).filter_by(gateway_code=scope["gateway_code"], sort_date=report_day)
        if normalize(name) in sort_names}
    placement_scope = [Placement.sort_date_operation_id == Close.sort_date_operation_id,
                       Placement.work_area_id.in_(scope["areas"]), Close.sealed_at.is_not(None)]
    if classification:
        placement_scope.append(Placement.classification == classification)
    # Read metadata and children from a single statement snapshot so the report
    # cannot combine a pre-purge manifest with post-purge placements.
    captures = db.session.execute(select(Close, Placement).outerjoin(Placement, and_(*placement_scope)).where(
        Close.gateway_code == scope["gateway_code"], Close.sort_date == report_day, Close.sort_unit_id.in_(sorts),
    )).all()
    expected.update(close.sort_date_operation_id for close, _ in captures)
    sealed = {close.sort_date_operation_id for close, _ in captures if close.sealed_at}
    return [placement for _, placement in captures if placement is not None], expected, sealed


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
    if view != "seniority" and history_enabled():
        scope = {**scope, "history_floor": history_cutoff()}
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
        dated, expected, sealed = _dated_staffing(scope, report_day, classification) if history_enabled() else ([], set(), set())
        marks = {(snapshot["operation_id"], person_id, area_id): status
                 for (_, area_id, person_id, status), snapshot in retained}
        covered = set()
        for placement in dated:
            key = (placement.sort_date_operation_id, placement.person_id, placement.work_area_id)
            covered.add(key)
            groups[placement.work_area_id, placement.area_path][key] = {
                "status": marks.get(key), "person_id": placement.person_id, "assigned": True,
                "name": placement.employee_name if placement.work_area_id in named_areas else None,
                "classification": staffing.CLASSIFICATION_LABELS.get(placement.classification),
            }
        ids = {person_id for (_, area_id, person_id, _), snapshot in retained
               if area_id in named_areas and not snapshot.get("employee")}
        names = {person.id: person.full_name for person in StaffingPerson.query.filter(StaffingPerson.id.in_(ids)).all()} if ids else {}
        for (_, area_id, person_id, status), snapshot in retained:
            key = (snapshot["operation_id"], person_id, area_id)
            if key in covered:
                continue
            groups[area_id, staffing.unit_path(units[area_id])][key] = {
                "status": status, "person_id": person_id, "assigned": False,
                "name": (snapshot.get("employee") or names.get(person_id)) if area_id in named_areas else None,
                "classification": staffing.CLASSIFICATION_LABELS.get(snapshot.get("classification_at_recording")),
            }
        result["rows"] = [
            {"name": path, "recorded": sum(bool(row["status"]) for row in people.values()),
             "assigned": len({row["person_id"] for row in people.values() if row["assigned"]}),
             "here": sum(row["status"] == "here" for row in people.values()),
             "people": sorted((row for row in people.values() if row["name"]), key=lambda row: row["name"].casefold())}
            for (_, path), people in groups.items()
        ]
        result["earliest"] = earliest_retained_evidence(scope)
        result["has_roster"] = bool(sealed)
        result["assigned"] = len({placement.person_id for placement in dated})
        if sealed and expected == sealed:
            result["coverage"] = "Complete roster at the configured Sort close. Attendance shows recorded marks only."
        elif sealed:
            result["coverage"] = "Partial history. Some Sorts have no finalized roster."
        elif retained or not history_enabled():
            result["coverage"] = (
                "Totals include only records with classification captured at the time. Older classification detail may be unavailable."
                if classification else "Headcounts cover recorded attendance. The full historical assigned roster is unavailable."
            )
        else:
            result["coverage"] = "No verified roster or retained attendance is available for this scope and date."
        if sealed:
            relevant_names = Close.query.with_entities(Close.sort_unit_id).filter(
                Close.sort_date_operation_id.in_(sealed)).scalar_subquery()
            result["earliest_roster"] = Close.query.with_entities(func.min(Close.sort_date)).filter(
                Close.gateway_code == scope["gateway_code"], Close.sealed_at.is_not(None),
                Close.sort_unit_id.in_(relevant_names), Close.sort_date >= scope["history_floor"]).scalar()
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
        retained = recorded_attendance(scope, start, end, person_id=selected.id, classification=classification, details=True)
        missing = {snapshot["operation_id"] for _, snapshot in retained if "sort_name" not in snapshot}
        sort_names = dict(SortDateOperation.query.with_entities(SortDateOperation.id, SortDateOperation.sort_name)
                          .filter(SortDateOperation.id.in_(missing)).all()) if missing else {}
        result["rows"] = [{"day": day, "status": status,
                           "sort": snapshot.get("sort_name") or sort_names.get(snapshot["operation_id"], "")}
                          for (day, _, _, status), snapshot in retained]
        result["earliest"] = earliest_retained_evidence(scope, person_id=selected.id)
        result["coverage"] = (
            "Dates without a retained record may be outside the available attendance history."
            if start < today else "Attendance history shows recorded statuses only."
        )
    return result
