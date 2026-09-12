"""Shared, employee-wide accountability calculations and authorized resolutions.

Attendance facts remain owned by neostaffing_accountability. This module never
creates attendance, finalizes a Sort, commits, or writes during a read.
"""
import json
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment
from app.models.staffing_accountability import (
    StaffingTrackerSetting as Setting, StaffingComboWorkday as Combo,
    StaffingAccountabilityWorkday as Workday, StaffingAccountabilitySource as Source,
    StaffingAccountabilityResolution as Resolution, StaffingAccountabilityCoverage as Coverage,
    StaffingAccountabilityReconciliation as Reconciliation,
)
from app.models import StaffingAttendanceOccurrence, StaffingAttendanceSummary
from app.services.neostaffing_accountability import occurrence_cutoff
from app.services.neostaffing_assignments import FT_COMBO, sort_of
from app.services.neostaffing_attendance_authority import attendance_authority
from app.services.access_control import user_can_access_app


ACTIONS = ("Verbal", "Written Warning", "Warning Letter", "Suspension", "Termination")
INFORMAL = ACTIONS[:2]
FORMAL = ACTIONS[2:]
DEFAULT_POLICY = ((1, "Verbal"), (2, "Written Warning"), (3, "Verbal"),
                  (4, "Warning Letter"), (5, "Verbal"), (6, "Suspension"),
                  (7, "Verbal"), (8, "Termination"))
CLEANUP_BATCH = 250


def normalized_policy(value):
    if value in (None, "", []):
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise ValueError("Policy requires 1–32 positive thresholds.")
    result = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError("Each policy step requires a threshold and action.")
        threshold, action = entry
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1 or action not in ACTIONS:
            raise ValueError("Invalid policy threshold or action.")
        result.append((threshold, action))
    if len({threshold for threshold, _ in result}) != len(result):
        raise ValueError("Policy thresholds must be unique.")
    result.sort()
    return None if tuple(result) == DEFAULT_POLICY else result


def policy_action(count, policy=None):
    return next((action for threshold, action in reversed(policy or DEFAULT_POLICY)
                 if count >= threshold), None)


def sequence_action(raw, last_formal):
    if raw not in FORMAL:
        return raw
    allowed = min(FORMAL.index(last_formal) + 1, 2) if last_formal in FORMAL else 0
    return FORMAL[min(FORMAL.index(raw), allowed)]


def finalized_facts(as_of, person_ids=None):
    """SQL projection: one qualifying, finalized fact per frozen workday.

    A missing partner/finalization is never treated as a nonqualifying attendance.
    Imported dated history is explicitly reconciled, not a fake operation.
    """
    retained_scope = [Workday.workday_date >= occurrence_cutoff(as_of), Workday.workday_date <= as_of]
    if person_ids is not None:
        retained_scope.append(Workday.person_id.in_(person_ids))
    operation_ids = select(Source.operation_id).join(Workday, Workday.id == Source.workday_id).where(*retained_scope)
    finalized = select(StaffingAttendanceSummary.sort_date_operation_id,
        func.max(StaffingAttendanceSummary.finalized_at).label("finalized_at")
    ).where(StaffingAttendanceSummary.sort_date_operation_id.in_(operation_ids)
    ).group_by(StaffingAttendanceSummary.sort_date_operation_id).subquery()
    native = select(
        Source.workday_id.label("workday_id"),
        func.count(Source.id).label("sources"),
        func.count(finalized.c.sort_date_operation_id).label("finalized"),
        func.max(finalized.c.finalized_at).label("finalized_at"),
        func.max(case((StaffingAttendanceOccurrence.status == "no_call", 2),
                      (StaffingAttendanceOccurrence.status == "call_in", 1), else_=0)).label("severity"),
    ).outerjoin(finalized, finalized.c.sort_date_operation_id == Source.operation_id).outerjoin(
        StaffingAttendanceOccurrence, and_(
            StaffingAttendanceOccurrence.person_id == Source.person_id,
            StaffingAttendanceOccurrence.sort_date_operation_id == Source.operation_id,
        ),
    ).join(Workday, Workday.id == Source.workday_id).where(*retained_scope).group_by(Source.workday_id).subquery()
    severity = case((Workday.imported_status == "no_call", 2),
                    (Workday.imported_status == "call_in", 1), else_=native.c.severity)
    return select(Workday.id.label("id"), Workday.person_id.label("person_id"),
                  Workday.workday_date.label("workday_date"), severity.label("severity"),
                  Workday.imported_status.is_(None).label("native_source"),
                  case((Workday.imported_status.is_not(None), Workday.created_at),
                       else_=native.c.finalized_at).label("finalized_at")).outerjoin(
        native, native.c.workday_id == Workday.id,
    ).where(
        *retained_scope,
        severity > 0,
        or_(Workday.imported_status.is_not(None), and_(
            native.c.sources == case((Workday.requires_pair.is_(True), 2), else_=1),
            native.c.sources == native.c.finalized,
        )),
    )


def employee_context(person, units, assignments, combo, settings):
    """Current authoritative assignments determine participation and ownership.

    FT combinations never select an arbitrary assignment when unconfigured.
    """
    rows = [row for row in assignments if row.active and row.person_id == person.id]
    if person.classification in FT_COMBO:
        allowed = {combo.first_sort_id, combo.second_sort_id} if combo else set()
        rows = [row for row in rows if getattr(sort_of(units.get(row.work_area_unit_id), units), "id", None) in allowed]
    scopes, policies, enabled = [], [], False
    for row in rows:
        chain, seen, unit = [], set(), units.get(row.work_area_unit_id)
        while unit and unit.id not in seen and unit.active:
            seen.add(unit.id)
            chain.append(unit)
            unit = units.get(unit.parent_id)
        if unit is not None or not chain or chain[-1].unit_type != "sort":
            continue
        operation = next((item for item in chain if item.unit_type == "operation"), None)
        department = next((item for item in chain if item.unit_type == "department"), None)
        if not operation:
            continue
        op_setting = settings.get(operation.id)
        dept_setting = settings.get(department.id) if department else None
        enabled |= bool((op_setting and op_setting.enabled) or (dept_setting and dept_setting.enabled))
        policies.append(json.loads(op_setting.policy_json) if op_setting and op_setting.policy_json else None)
        scopes.append({"work_area_id": row.work_area_unit_id, "operation": operation, "department": department})
    return {"scopes": scopes, "policies": policies, "enabled": enabled}


def lock_authorized_employee(user, person_id, *, formal=False, configure=False, lock=True):
    """Employee parent lock serializes both sides' delivery/trigger consumption."""
    from app.services import neostaffing as staffing
    query = StaffingPerson.query.filter_by(id=person_id, active=True).populate_existing()
    person = (query.with_for_update() if lock else query).first()
    if not person:
        raise ValueError("Active employee not found.")
    hierarchy = staffing._daily_attendance_hierarchy()
    authority = attendance_authority(user, hierarchy)
    assignments = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).populate_existing().all()
    combo = db.session.get(Combo, person.id, populate_existing=True)
    context = employee_context(person, hierarchy["by_id"], assignments, combo, {})
    allowed = any(scope["work_area_id"] in authority.work_area_ids for scope in context["scopes"])
    if configure and not combo:
        allowed = any(row.work_area_unit_id in authority.work_area_ids for row in assignments)
    if not allowed:
        raise ValueError("Employee is outside your active NeoStaffing leadership scope.")
    if formal and not user_can_access_app(user, "neostaffing", minimum_role="master"):
        raise ValueError("Master authority is required for formal discipline.")
    return person, hierarchy, assignments, combo


def employee_state(person, hierarchy, assignments, combo, as_of):
    settings = {row.unit_id: row for row in Setting.query.all()}
    context = employee_context(person, hierarchy["by_id"], assignments, combo, settings)
    facts = db.session.execute(finalized_facts(as_of, [person.id]).order_by(
        Workday.workday_date, Workday.id)).mappings().all()
    resolutions = Resolution.query.filter(Resolution.person_id == person.id,
        Resolution.resolved_on >= occurrence_cutoff(as_of)
    ).order_by(Resolution.resolved_on.desc(), Resolution.resolved_at.desc(), Resolution.id.desc()).all()
    last_formal = next((row.action for row in resolutions if row.action in FORMAL), None)
    consumed = {row.trigger_workday_id for row in resolutions if row.trigger_workday_id}
    covered = set(db.session.scalars(select(Coverage.workday_id).join(
        Workday, Workday.id == Coverage.workday_id).where(Workday.person_id == person.id)))
    raw = max((policy_action(len(facts), policy) for policy in context["policies"]),
              key=lambda action: ACTIONS.index(action) if action in ACTIONS else -1, default=None)
    recommendation = sequence_action(raw, last_formal)
    reconciliation = db.session.get(Reconciliation, person.id)
    unknown = bool(facts and (not reconciliation or reconciliation.complete_since > occurrence_cutoff(as_of)))
    unresolved = [row for row in facts if row["id"] not in covered]
    latest_fact = max((row for row in facts if row["native_source"]),
                      key=lambda row: (row["finalized_at"], row["id"]), default=None)
    trigger = (latest_fact if latest_fact and latest_fact["id"] not in consumed
               and recommendation in FORMAL else None)
    due = bool(context["enabled"] and not unknown and (
        (recommendation in INFORMAL and unresolved) or trigger))
    return {**context, "person": person, "facts": facts, "count": len(facts),
            "last_formal": last_formal, "recommendation": recommendation,
            "reconciliation_needed": unknown, "unresolved": unresolved, "trigger": trigger,
            "due": due, "status": "TRACKER OFF" if not context["enabled"] else
            "RECONCILIATION NEEDED" if unknown else "ACTION REQUIRED" if due else "CURRENT"}


def resolve_obligation(user, person_id, values, as_of):
    kind = values.get("kind")
    if kind not in ("informal", "issue", "override", "no_discipline"):
        raise ValueError("Invalid resolution.")
    person, hierarchy, assignments, combo = lock_authorized_employee(user, person_id, formal=kind != "informal")
    state = employee_state(person, hierarchy, assignments, combo, as_of)
    if not state["due"]:
        raise ValueError("No current unresolved obligation. Reload Accountability.")
    expected = ",".join(str(row["id"]) for row in state["facts"])
    if values.get("expected_facts") != expected or values.get("recommendation") != state["recommendation"]:
        raise ValueError("Accountability changed. Reload before resolving.")
    recommendation = state["recommendation"]
    if (kind == "informal") != (recommendation in INFORMAL):
        raise ValueError("Resolution does not match the current obligation.")
    action = recommendation
    if kind == "override":
        action = values.get("action")
        if action not in FORMAL or not str(values.get("note", "")).strip():
            raise ValueError("An override requires a formal action and reason.")
        if sequence_action(action, state["last_formal"]) != action:
            raise ValueError("Formal progression cannot skip Warning Letter, Suspension, then Termination.")
    elif kind == "no_discipline":
        action = None
    resolution = Resolution(person_id=person.id, kind=kind, recommendation=recommendation,
        action=action, note=str(values.get("note", "")).strip()[:4000], actor_id=user.id, resolved_on=as_of,
        trigger_workday_id=state["trigger"]["id"] if kind != "informal" else None)
    db.session.add(resolution)
    db.session.flush()
    if kind == "informal":
        db.session.add_all(Coverage(workday_id=row["id"], resolution_id=resolution.id) for row in state["unresolved"])
    return resolution


def configure_workday(user, person_id, first_sort_id, second_sort_id, expected_version):
    person, hierarchy, assignments, combo = lock_authorized_employee(user, person_id, configure=True)
    if person.classification not in FT_COMBO:
        raise ValueError("Two-Sort workdays apply only to FT Combo employees.")
    if first_sort_id == second_sort_id or any(
        value not in hierarchy["by_id"] or hierarchy["by_id"][value].unit_type != "sort"
        for value in (first_sort_id, second_sort_id)
    ):
        raise ValueError("Choose two distinct configured active Sorts in workday order.")
    if int(expected_version) != (combo.version if combo else 0):
        raise ValueError("Workday configuration changed. Reload before saving.")
    if not combo:
        combo = Combo(person_id=person.id)
        db.session.add(combo)
    if (combo.first_sort_id, combo.second_sort_id) != (first_sort_id, second_sort_id):
        combo.first_sort_id, combo.second_sort_id = first_sort_id, second_sort_id
        combo.version = (combo.version or 0) + 1
        combo.updated_by_user_id = user.id
    return combo


def configure_tracker(user, unit_id, enabled, policy, expected_version):
    from app.services import neostaffing as staffing
    if not user_can_access_app(user, "neostaffing", minimum_role="master"):
        raise ValueError("Master authority is required to configure discipline.")
    unit = StaffingUnit.query.filter_by(id=unit_id, active=True).populate_existing().with_for_update().first()
    if not unit or unit.unit_type not in ("operation", "department"):
        raise ValueError("Choose an active Operation or Department.")
    hierarchy = staffing._daily_attendance_hierarchy()
    authority = attendance_authority(user, hierarchy)
    descendants = set()
    for area_id, area in hierarchy["by_id"].items():
        if area.unit_type != "work_area":
            continue
        current, seen = hierarchy["by_id"].get(area_id), set()
        while current and current.id not in seen:
            seen.add(current.id)
            if current.id == unit.id:
                descendants.add(area_id)
            current = hierarchy["by_id"].get(current.parent_id)
    if not descendants or not descendants <= authority.work_area_ids:
        raise ValueError("Configuration is outside your leadership scope.")
    if unit.unit_type == "department":
        parent = db.session.get(Setting, unit.parent_id)
        if parent and parent.enabled:
            raise ValueError("This Department is ON BY OPERATION.")
        if policy not in (None, "", []):
            raise ValueError("Policy belongs to the Operation.")
    normalized = normalized_policy(policy) if unit.unit_type == "operation" else None
    row = db.session.get(Setting, unit.id, populate_existing=True)
    if int(expected_version) != (row.version if row else 0):
        raise ValueError("Tracker configuration changed. Reload before saving.")
    encoded = json.dumps(normalized, separators=(",", ":")) if normalized else None
    if not row and not enabled and encoded is None:
        return None
    if not row:
        row = Setting(unit_id=unit.id)
        db.session.add(row)
    if (row.enabled, row.policy_json) != (bool(enabled), encoded):
        row.enabled, row.policy_json = bool(enabled), encoded
        row.version = (row.version or 0) + 1
        row.updated_by_user_id = user.id
    return row


def queue_context(user, filters, as_of):
    """Filter, count, order and paginate projections in SQL, not a roster scan."""
    from app.services import neostaffing as staffing
    hierarchy = staffing._daily_attendance_hierarchy()
    authority = attendance_authority(user, hierarchy)
    if not authority.work_area_ids:
        raise ValueError("Active NeoStaffing management leadership scope is required.")
    units = hierarchy["by_id"]
    settings = {row.unit_id: row for row in Setting.query.all()}
    area_sorts, area_operations, area_departments, area_enabled, area_policy = {}, {}, {}, {}, {}
    for unit in units.values():
        if unit.unit_type != "work_area":
            continue
        chain, current, seen = {}, unit, set()
        while current and current.id not in seen and current.active:
            seen.add(current.id)
            chain[current.unit_type] = current.id
            current = units.get(current.parent_id)
        if current is not None or not {"sort", "operation"} <= chain.keys():
            continue
        area_sorts[unit.id] = chain["sort"]
        area_operations[unit.id] = chain["operation"]
        area_departments[unit.id] = chain.get("department")
        op, dept = settings.get(chain["operation"]), settings.get(chain.get("department"))
        area_enabled[unit.id] = int(bool((op and op.enabled) or (dept and dept.enabled)))
        area_policy[unit.id] = json.loads(op.policy_json) if op and op.policy_json else DEFAULT_POLICY

    authorized_people = select(StaffingWorkAssignment.person_id).where(
        StaffingWorkAssignment.active.is_(True),
        StaffingWorkAssignment.work_area_unit_id.in_(authority.work_area_ids))
    retained = finalized_facts(as_of, authorized_people).subquery()
    facts = select(retained, func.row_number().over(partition_by=retained.c.person_id,
        order_by=(retained.c.native_source.desc(), retained.c.finalized_at.desc(), retained.c.id.desc())).label("latest_position")).subquery()
    stats = select(facts.c.person_id,
        func.count(facts.c.id).label("infractions"),
        func.min(facts.c.workday_date).label("first_date"),
        func.min(case((Coverage.workday_id.is_(None), facts.c.workday_date), else_=None)).label("informal_date"),
        func.max(case((Coverage.workday_id.is_(None), 1), else_=0)).label("informal_pending"),
        func.min(case((and_(Resolution.id.is_(None), facts.c.latest_position == 1,
                           facts.c.native_source.is_(True)), facts.c.workday_date), else_=None)).label("trigger_date"),
    ).outerjoin(Coverage, Coverage.workday_id == facts.c.id).outerjoin(
        Resolution, Resolution.trigger_workday_id == facts.c.id,
    ).group_by(facts.c.person_id).subquery()
    count = func.coalesce(stats.c.infractions, 0)
    work_area = StaffingWorkAssignment.work_area_unit_id
    sort_id = case(area_sorts, value=work_area, else_=None) if area_sorts else db.literal(None)
    eligible = or_(StaffingPerson.classification.notin_(FT_COMBO),
        and_(Combo.person_id.is_not(None), or_(sort_id == Combo.first_sort_id, sort_id == Combo.second_sort_id)))
    scores = {area: case(*[(count >= threshold, ACTIONS.index(action) + 1)
                          for threshold, action in reversed(policy)], else_=0)
              for area, policy in area_policy.items()}
    score = case(scores, value=work_area, else_=0) if scores else db.literal(0)
    enabled = case(area_enabled, value=work_area, else_=0) if area_enabled else db.literal(0)
    contexts = select(StaffingWorkAssignment.person_id,
        func.max(case((eligible, score), else_=0)).label("raw_score"),
        func.max(case((eligible, enabled), else_=0)).label("enabled"),
        func.min(case((eligible, work_area), else_=None)).label("first_area"),
        func.max(case((eligible, work_area), else_=None)).label("last_area"),
    ).join(StaffingPerson, StaffingPerson.id == StaffingWorkAssignment.person_id).outerjoin(
        Combo, Combo.person_id == StaffingPerson.id).outerjoin(stats, stats.c.person_id == StaffingPerson.id
    ).where(StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.person_id.in_(authorized_people)).group_by(StaffingWorkAssignment.person_id).subquery()

    visible_areas = set(authority.work_area_ids)
    for key, mapping in (("operation_id", area_operations), ("department_id", area_departments)):
        if filters.get(key):
            try:
                selected = int(filters[key])
            except (TypeError, ValueError):
                raise ValueError("Invalid organizational filter.")
            visible_areas &= {area for area, value in mapping.items() if value == selected}
    # Unconfigured FT staff remain visible for configuration, but have no guessed
    # policy/participation. Configured FT authority is either configured side.
    visible = select(StaffingWorkAssignment.person_id).join(
        StaffingPerson, StaffingPerson.id == StaffingWorkAssignment.person_id).outerjoin(
        Combo, Combo.person_id == StaffingPerson.id).where(
            StaffingWorkAssignment.active.is_(True), work_area.in_(visible_areas),
            or_(Combo.person_id.is_(None), eligible),
        )
    latest = select(Resolution.person_id, Resolution.action,
        func.row_number().over(partition_by=Resolution.person_id,
            order_by=(Resolution.resolved_on.desc(), Resolution.resolved_at.desc(), Resolution.id.desc())).label("position"),
    ).where(Resolution.action.in_(FORMAL),
        Resolution.person_id.in_(authorized_people),
        Resolution.resolved_on >= occurrence_cutoff(as_of)).subquery()
    last_score = case({name: i + 3 for i, name in enumerate(FORMAL)}, value=latest.c.action, else_=2)
    raw = func.coalesce(contexts.c.raw_score, 0)
    allowed = case((last_score >= 5, 5), else_=last_score + 1)
    score = case((and_(raw >= 3, raw > allowed), allowed), else_=raw)
    recommendation = case({i + 1: action for i, action in enumerate(ACTIONS)}, value=score, else_=None)
    unknown = and_(count > 0, or_(Reconciliation.person_id.is_(None), Reconciliation.complete_since > occurrence_cutoff(as_of)))
    due = or_(and_(score.in_((1, 2)), stats.c.informal_pending == 1), and_(score >= 3, stats.c.trigger_date.is_not(None)))
    status = case((func.coalesce(contexts.c.enabled, 0) == 0, "TRACKER OFF"),
                  (unknown, "RECONCILIATION NEEDED"), (due, "ACTION REQUIRED"), else_="CURRENT")
    query = select(StaffingPerson.id, StaffingPerson.first_name, StaffingPerson.last_name,
        count.label("infractions"), latest.c.action.label("last_formal"),
        recommendation.label("recommendation"),
        case((unknown, stats.c.first_date), (score.in_((1, 2)), stats.c.informal_date),
             else_=stats.c.trigger_date).label("trigger_date"), status.label("status"),
        contexts.c.first_area, contexts.c.last_area,
    ).outerjoin(stats, stats.c.person_id == StaffingPerson.id).outerjoin(
        contexts, contexts.c.person_id == StaffingPerson.id).outerjoin(
        latest, and_(latest.c.person_id == StaffingPerson.id, latest.c.position == 1)).outerjoin(
        Reconciliation, Reconciliation.person_id == StaffingPerson.id).where(
        StaffingPerson.active.is_(True), StaffingPerson.id.in_(visible))
    if filters.get("search"):
        term = str(filters["search"]).strip()[:140]
        query = query.where(or_(StaffingPerson.first_name.contains(term, autoescape=True),
                               StaffingPerson.last_name.contains(term, autoescape=True),
                               StaffingPerson.employee_id.contains(term, autoescape=True)))
    projection = query.subquery()
    summary = db.session.execute(select(projection.c.status, projection.c.recommendation,
        func.count()).group_by(projection.c.status, projection.c.recommendation)).all()
    selected = select(projection)
    if filters.get("tab", "needs_action") == "needs_action":
        selected = selected.where(projection.c.status.in_(("ACTION REQUIRED", "RECONCILIATION NEEDED")))
    for key in ("status", "recommendation"):
        if filters.get(key):
            selected = selected.where(projection.c[key] == filters[key])
    if filters.get("tab") == "formal_history":
        history_people = select(projection.c.id)
        if filters.get("status"):
            history_people = history_people.where(projection.c.status == filters["status"])
        selected = select(Resolution.id, Resolution.person_id, Resolution.kind,
            Resolution.action, Resolution.recommendation, Resolution.resolved_at, Resolution.resolved_on,
            Resolution.actor_id, Resolution.trigger_workday_id, Resolution.note,
            StaffingPerson.first_name, StaffingPerson.last_name,
        ).join(StaffingPerson, StaffingPerson.id == Resolution.person_id).where(
            Resolution.kind != "informal", Resolution.person_id.in_(history_people),
            Resolution.resolved_on >= occurrence_cutoff(as_of))
        if filters.get("recommendation"):
            selected = selected.where(Resolution.recommendation == filters["recommendation"])
    total = db.session.scalar(select(func.count()).select_from(selected.subquery()))
    try:
        page = max(1, int(filters.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    priority = case((and_(projection.c.status == "ACTION REQUIRED", projection.c.recommendation == "Termination"), 1),
                    (and_(projection.c.status == "ACTION REQUIRED", projection.c.recommendation == "Suspension"), 2),
                    (and_(projection.c.status == "ACTION REQUIRED", projection.c.recommendation == "Warning Letter"), 3),
                    (projection.c.status == "RECONCILIATION NEEDED", 4), else_=5)
    ordering = (Resolution.resolved_on.desc(), Resolution.resolved_at.desc(), Resolution.id.desc()) if filters.get("tab") == "formal_history" else (
        priority, projection.c.trigger_date, projection.c.last_name, projection.c.first_name, projection.c.id)
    rows = db.session.execute(selected.order_by(*ordering).offset((page - 1) * 25).limit(25)).mappings().all()
    rows = [dict(row) for row in rows]
    if filters.get("tab") != "formal_history":
        for row in rows:
            row["scope_label"] = " · ".join(
                units[area_operations[area]].name + " / " + (
                    units[area_departments[area]].name if area_departments.get(area) else "-")
                for area in dict.fromkeys((row["first_area"], row["last_area"])) if area in area_operations)
    settings_units = []
    for unit in units.values():
        mapping = area_operations if unit.unit_type == "operation" else area_departments
        areas = {area for area, parent in mapping.items() if parent == unit.id}
        if unit.unit_type in ("operation", "department") and areas and areas <= authority.work_area_ids:
            settings_units.append(unit)
    return {"rows": rows, "summary": summary, "total": total, "page": page,
            "units": units, "settings": settings, "settings_units": settings_units, "authority": authority}


def reconcile_history(user, person_id, decision, entries, as_of):
    person, hierarchy, assignments, combo = lock_authorized_employee(user, person_id)
    if decision == "skip":
        return None
    state = employee_state(person, hierarchy, assignments, combo, as_of)
    if not state["facts"] or not state["reconciliation_needed"]:
        raise ValueError("No finalized history reconciliation is needed.")
    if decision not in ("first", "history"):
        raise ValueError("Invalid reconciliation decision.")
    validated = []
    if decision == "history":
        from datetime import date
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
            raise ValueError("Enter 1–100 dated prior occurrences.")
        for entry in entries:
            day = date.fromisoformat(entry["date"])
            status = entry["status"]
            if status not in ("call_in", "no_call") or not occurrence_cutoff(as_of) <= day < state["facts"][0]["workday_date"]:
                raise ValueError("Prior history requires an exact qualifying date before the first retained occurrence.")
            validated.append((day, status))
        if len({day for day, _ in validated}) != len(validated):
            raise ValueError("Enter at most one occurrence per workday.")
    db.session.add_all(Workday(person_id=person.id, workday_date=day, imported_status=status,
                              created_by_user_id=user.id) for day, status in validated)
    row = db.session.get(Reconciliation, person.id)
    if row is None:
        row = Reconciliation(person_id=person.id)
        db.session.add(row)
    row.complete_since = occurrence_cutoff(as_of)
    row.actor_id = user.id
    row.reconciled_at = datetime.utcnow()
    return row


def backfill_legacy_workdays(as_of):
    """Explicit bootstrap only: preserve pre-feature per-operation fact identity.

    Existing collection was Night-only. Never retroactively apply a newly chosen
    Combo pairing to historical facts. Batch hydration/flushes, no per-row reads.
    """
    total = 0
    while True:
        rows = StaffingAttendanceOccurrence.query.outerjoin(Source, and_(
            Source.person_id == StaffingAttendanceOccurrence.person_id,
            Source.operation_id == StaffingAttendanceOccurrence.sort_date_operation_id,
        )).filter(Source.id.is_(None),
            StaffingAttendanceOccurrence.attendance_date >= occurrence_cutoff(as_of),
        ).order_by(StaffingAttendanceOccurrence.id).limit(500).all()
        if not rows:
            return total
        groups = [Workday(person_id=row.person_id, workday_date=row.attendance_date,
            requires_pair=False, created_by_user_id=row.updated_by_user_id) for row in rows]
        db.session.add_all(groups)
        db.session.flush()
        db.session.add_all(Source(workday_id=group.id, person_id=row.person_id,
            operation_id=row.sort_date_operation_id, position=1) for row, group in zip(rows, groups))
        db.session.flush()
        total += len(rows)


def reconcile_formal_history(user, person_id, action, issued_on, note, as_of):
    """Master records an actual retained prior action, never a fake trigger."""
    from datetime import date
    person, _, _, _ = lock_authorized_employee(user, person_id, formal=True)
    day = date.fromisoformat(issued_on)
    note = str(note or "").strip()
    if action not in FORMAL or not occurrence_cutoff(as_of) <= day < as_of or not note:
        raise ValueError("Prior formal history requires an exact retained date, formal action and note.")
    existing = Resolution.query.filter_by(person_id=person.id, kind="history", action=action, resolved_on=day).first()
    if existing:
        return existing
    row = Resolution(person_id=person.id, kind="history", action=action, resolved_on=day,
                     actor_id=user.id, note=note[:4000])
    db.session.add(row)
    return row


def purge_expired_discipline(as_of):
    """Fixed oldest-first batches, preserving retained formal trigger identities."""
    cutoff = occurrence_cutoff(as_of)
    expired = select(Resolution.id).where(
        Resolution.resolved_on < cutoff
    ).order_by(Resolution.resolved_at, Resolution.id).limit(CLEANUP_BATCH)
    ids = list(db.session.scalars(expired))
    deleted = 0
    if ids:
        Coverage.query.filter(Coverage.resolution_id.in_(ids)).delete(synchronize_session=False)
        deleted += Resolution.query.filter(Resolution.id.in_(ids)).delete(synchronize_session=False)
    retained_resolution = select(Resolution.id).where(Resolution.trigger_workday_id == Workday.id).exists()
    expired_days = select(Workday.id).where(Workday.workday_date < cutoff, ~retained_resolution).order_by(
        Workday.workday_date, Workday.id).limit(CLEANUP_BATCH)
    ids = list(db.session.scalars(expired_days))
    if ids:
        Coverage.query.filter(Coverage.workday_id.in_(ids)).delete(synchronize_session=False)
        Source.query.filter(Source.workday_id.in_(ids)).delete(synchronize_session=False)
        deleted += Workday.query.filter(Workday.id.in_(ids)).delete(synchronize_session=False)
    return deleted
