"""Node-independent attendance authority; Reports To is never a security grant."""
from dataclasses import dataclass

from sqlalchemy import and_, func

from app.models import StaffingPerson, StaffingLeadershipAssignment


LEVELS = {
    "part_time_supervisor": "department",
    "full_time_supervisor": "department",
    "twenty_c_full_time_supervisor": "department",
    "full_time_specialist": "department",
    "manager": "operation",
    "division_manager": "sort",
}


@dataclass(frozen=True)
class AttendanceAuthority:
    person_id: int | None = None
    work_area_ids: frozenset = frozenset()


def attendance_authority(user, hierarchy=None):
    """Resolve active leadership once, then expand active ancestry in memory.

    Staffing currently links accounts by employee_id, not a user FK. Fail closed
    on ambiguous normalized IDs. No application role or Reports To fallback.
    """
    from app.services import neostaffing as staffing

    employee_id = str(getattr(user, "employee_id", "") or "").strip().lower()
    if not employee_id or not getattr(user, "is_active", False):
        return AttendanceAuthority()
    rows = StaffingPerson.query.add_columns(StaffingLeadershipAssignment.unit_id).outerjoin(
        StaffingLeadershipAssignment, and_(
            StaffingLeadershipAssignment.person_id == StaffingPerson.id,
            StaffingLeadershipAssignment.active.is_(True),
        ),
    ).filter(func.lower(func.trim(StaffingPerson.employee_id)) == employee_id).populate_existing().all()
    if len({person.id for person, _ in rows}) != 1 or not rows[0][0].active:
        return AttendanceAuthority()
    person = rows[0][0]
    level = LEVELS.get(person.classification)
    if not level:
        return AttendanceAuthority()
    hierarchy = hierarchy if hierarchy is not None else staffing._daily_attendance_hierarchy()
    units = hierarchy["by_id"]

    def ancestry(unit_id):
        chain, seen = [], set()
        while unit_id is not None:
            unit = units.get(unit_id)
            if unit is None or unit_id in seen or not unit.active:
                return []
            seen.add(unit_id)
            chain.append(unit)
            unit_id = unit.parent_id
        return chain

    scopes = {
        ancestor.id
        for _, unit_id in rows
        for ancestor in ancestry(unit_id)
        if ancestor.unit_type == level
    }
    # Specialists may legitimately lead an Operation in the existing model.
    # Its departments are covered, but the grant remains department-scoped.
    if person.classification == "full_time_specialist":
        operations = {unit_id for _, unit_id in rows if unit_id in units and units[unit_id].unit_type == "operation"}
        scopes.update(unit.id for unit in units.values() if unit.unit_type == "department"
                      and any(ancestor.id in operations for ancestor in ancestry(unit.id)))
    areas = frozenset(
        unit.id for unit in units.values()
        if unit.unit_type == "work_area"
        and any(ancestor.id in scopes for ancestor in ancestry(unit.id))
    )
    return AttendanceAuthority(person.id, areas)


def require_attendance_assignments(user, assignments, person_ids, hierarchy):
    """Recheck at the shared write boundary, irrespective of the caller's UI."""
    authority = attendance_authority(user, hierarchy)
    if not authority.work_area_ids:
        raise ValueError("Active NeoStaffing management attendance authority is required.")
    if any(
        person_id not in assignments
        or assignments[person_id].work_area_unit_id not in authority.work_area_ids
        for person_id in person_ids
    ):
        raise ValueError("Attendance includes an employee outside your leadership scope.")
    return authority


def inbound_attendance_areas(user, *, include_unscoped=False):
    """Use canonical Night/Ramp/Shift units, never later Flow destinations.

    StaffingUnit has no semantic area key. Reuse its existing area-name
    normalization at the boundary, then pass only authoritative unit IDs.
    include_unscoped is a viewing exception; the returned authority and all
    shared write authorization remain unchanged.
    """
    from app.services import neostaffing as staffing

    names = {"east ballmat": "ebm", "west ballmat": "wbm", "discharge": "dis"}
    areas = {key: [] for key in names.values()}
    if not getattr(user, "employee_id", None) and not include_unscoped:
        return AttendanceAuthority(), areas
    hierarchy = staffing._daily_attendance_hierarchy()
    authority = attendance_authority(user, hierarchy)
    for unit in hierarchy["units"]:
        if (not include_unscoped and unit.id not in authority.work_area_ids) or not staffing._is_shift_work_area(unit, hierarchy["by_id"]):
            continue
        key = names.get(staffing._attendance_work_area_name_key(unit.name))
        if key:
            areas[key].append(unit.id)
    return authority, areas


def node_attendance_authority(user, workspace, area=None):
    """Node writes: linked active management AND existing operational edit permission.

    Leadership and Reports To remain authoritative for central Staffing only.
    Callers cannot supply an arbitrary work-area grant.
    """
    from app.services.access_control import get_current_gateway, user_can_access_node
    from app.services.permission_rules import user_can
    from app.services import neostaffing as staffing

    if workspace not in {"sektor", "ermac"}:
        raise ValueError("Invalid employee workspace.")
    gateway = get_current_gateway()
    authority = attendance_authority(user)
    if not authority.person_id or not user_can_access_node(user, gateway.code, workspace):
        return AttendanceAuthority()
    if workspace == "sektor":
        _, areas = inbound_attendance_areas(user, include_unscoped=True)
        permissions = {"ebm": "neosektor.ebm.edit", "wbm": "neosektor.wbm.edit",
                       "dis": "neosektor.discharge.edit"}
        if area is not None and area not in permissions:
            raise ValueError("Invalid attendance area.")
        allowed = {unit_id for key, permission in permissions.items()
                   if (area is None or area == key) and user_can(permission, user)
                   for unit_id in areas[key]}
    else:
        from app.services.neoermac_door_supervision import supervised_doors_for_user
        from app.services.neoermac_building_lineup import get_outbound_door_options
        doors = supervised_doors_for_user(user, gateway, get_outbound_door_options())
        if area is not None:
            if area not in doors:
                raise ValueError("Door is outside your supervision scope.")
            doors = [area]
        allowed = set(staffing.attendance_deep_link_work_area_ids(doors, allow_persistent_roster=True)) \
            if user_can("neoermac.door_view.edit", user) else set()
    return AttendanceAuthority(authority.person_id, frozenset(allowed))
