"""Canonical Work Assignment selection and shared Shift lifecycle.

All ORM writers (including bulk/change-request writers) share this flush guard.
Database triggers installed by explicit bootstrap additionally protect direct SQL.
No work is performed on read-only requests.
"""
from collections import defaultdict

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, joinedload

from app.extensions import db
from app.models import StaffingPerson, StaffingShiftFlowPlan, StaffingUnit, StaffingWorkAssignment

FT_COMBO = frozenset({"full_time_combo", "domiciled_full_time_combo", "non_domiciled_full_time_combo"})


def sort_of(area, units=None):
    seen = set()
    while area and area.id not in seen:
        if area.unit_type == "sort":
            return area
        seen.add(area.id)
        area = units.get(area.parent_id) if units is not None else area.parent
    return None


def is_shift(area, units=None):
    if not area or area.unit_type != "work_area":
        return False
    parent = (lambda node: units.get(node.parent_id)) if units is not None else (lambda node: node.parent)
    department = parent(area)
    operation = parent(department) if department else None
    sort = parent(operation) if operation else None
    return bool(department and operation and sort
                and department.unit_type == "department" and department.name.strip().casefold() == "shift"
                and operation.unit_type == "operation" and operation.name.strip().casefold() == "ramp"
                and sort.unit_type == "sort" and sort.name.strip().casefold() == "night")


def shift_home(person):
    """Resolve only Night/Ramp/Shift; never choose an arbitrary FT assignment."""
    rows = StaffingWorkAssignment.query.options(
        joinedload(StaffingWorkAssignment.work_area).joinedload(StaffingUnit.parent)
        .joinedload(StaffingUnit.parent).joinedload(StaffingUnit.parent)
    ).filter_by(person_id=person.id, active=True).populate_existing().all()
    return next((row for row in rows if is_shift(row.work_area)), None)


def assignment_for_target(person, area, rows):
    active = [row for row in rows if row.active]
    if person.classification in FT_COMBO:
        target_sort = sort_of(area)
        if not target_sort:
            raise ValueError("A Work Area must belong to a Sort.")
        matching = [row for row in rows if getattr(sort_of(row.work_area), "id", None) == target_sort.id]
        return next((row for row in matching if row.active), matching[0] if matching else None)
    if len(active) > 1:
        raise ValueError("Resolve multiple Work Assignments before changing this employee's classification.")
    return active[0] if active else rows[0] if rows else None


def version(person):
    return str(person.shift_flow_version or 0)


def _changed(obj, fields):
    state = inspect(obj)
    return state.pending or any(state.attrs[field].history.has_changes() for field in fields)


@event.listens_for(Session, "before_flush")
def enforce_work_assignment_lifecycle(session, _context, _instances):
    changed = list(session.new) + list(session.dirty) + list(session.deleted)
    assignments = [obj for obj in changed if isinstance(obj, StaffingWorkAssignment) and
                   (obj in session.deleted or _changed(obj, ("work_area_unit_id", "work_area", "active", "person_id")))]
    plans = [obj for obj in changed if isinstance(obj, StaffingShiftFlowPlan) and
             (obj in session.deleted or _changed(obj, ("setup_work_area_id", "setup_work_area", "sort_start_work_area_id", "sort_start_work_area", "ballmat_transition", "final_door_work_area_id", "final_door_work_area")))]
    people = [obj for obj in changed if isinstance(obj, StaffingPerson)
              and _changed(obj, ("classification",))]
    units_changed = [obj for obj in session.dirty if isinstance(obj, StaffingUnit)
                     and _changed(obj, ("parent_id", "parent", "name", "active"))]
    if not (assignments or plans or people or units_changed):
        return
    units = {unit.id: unit for unit in session.scalars(select(StaffingUnit)).all()}
    for unit in units_changed:
        if inspect(unit).attrs.parent.history.has_changes():
            unit.parent_id = getattr(unit.parent, "id", None)
    ids = {obj.person_id for obj in assignments if obj.person_id}
    ids.update(obj.staffing_person_id for obj in plans if obj.staffing_person_id)
    ids.update(obj.id for obj in people if obj.id)
    for obj in assignments + plans:
        foreign_id = obj.person_id if isinstance(obj, StaffingWorkAssignment) else obj.staffing_person_id
        if not foreign_id and obj.person and obj.person.id:
            ids.add(obj.person.id)
    # Hierarchy changes are rare: validate affected assignments as a bounded
    # set, not one query per employee. No ancestry value is duplicated in rows.
    if units_changed:
        changed_unit_ids = {unit.id for unit in units_changed}
        affected_areas = set()
        for area in units.values():
            if area.unit_type != "work_area":
                continue
            ancestor, seen = area, set()
            while ancestor and ancestor.id not in seen:
                if ancestor.id in changed_unit_ids:
                    affected_areas.add(area.id)
                    break
                seen.add(ancestor.id)
                ancestor = units.get(ancestor.parent_id)
        ids.update(session.scalars(select(StaffingWorkAssignment.person_id).where(
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(affected_areas))))
    if not ids:
        return
    locked = session.scalars(select(StaffingPerson).where(StaffingPerson.id.in_(ids))
                             .order_by(StaffingPerson.id).with_for_update()).all()
    snapshot = session.info.get("staffing_assignment_snapshot")
    rows = snapshot if snapshot is not None else session.scalars(select(StaffingWorkAssignment).where(StaffingWorkAssignment.person_id.in_(ids))).all()
    rows = list({id(row): row for row in rows + assignments if row not in session.deleted}.values())
    by_person = defaultdict(list)
    for row in rows:
        if inspect(row).attrs.work_area.history.has_changes():
            row.work_area_unit_id = row.work_area.id
        person_id = row.person_id or getattr(row.person, "id", None)
        if row.active is not False:
            by_person[person_id].append(row)
    existing_plans = {row.staffing_person_id: row for row in session.scalars(
        select(StaffingShiftFlowPlan).options(joinedload(StaffingShiftFlowPlan.setup_work_area),
            joinedload(StaffingShiftFlowPlan.final_door_work_area))
        .where(StaffingShiftFlowPlan.staffing_person_id.in_(ids))).all()}
    existing_plans.update({row.staffing_person_id or getattr(row.person, "id", None): row for row in plans
                           if row not in session.deleted})
    for person in locked:
        active = by_person[person.id]
        sorts = [getattr(sort_of(units.get(row.work_area_unit_id), units), "id", None) for row in active]
        if len(active) > 1 and (person.classification not in FT_COMBO or len(set(sorts)) != len(sorts)):
            raise ValueError("Only FT Combo may have multiple Work Assignments, with at most one per Sort.")
        home = next((row for row in active if is_shift(units.get(row.work_area_unit_id), units)), None)
        plan = existing_plans.get(person.id)
        if plan and plan not in session.deleted:
            if not home:
                if plan in session.new:
                    raise ValueError("Shift Flow requires a Night / Ramp / Shift Home assignment.")
                session.delete(plan)
            else:
                plan.sort_start_work_area = units[home.work_area_unit_id]
                department_id = units[home.work_area_unit_id].parent_id
                for field in ("setup_work_area", "final_door_work_area"):
                    area = getattr(plan, field)
                    if area and (not area.active or area.parent_id != department_id):
                        setattr(plan, field, None)
                if "ballmat" not in units[home.work_area_unit_id].name.casefold():
                    plan.ballmat_transition = None
        # The aggregate revision covers Home-only changes and absent plans too.
        person.shift_flow_version = (person.shift_flow_version or 0) + 1
