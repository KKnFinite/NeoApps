"""Confirmed personnel placement changes; history and operational evidence stay put."""
from datetime import date

from sqlalchemy import select, text

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment
from app.services import neostaffing as staffing
from app.services import neostaffing_assignments as assignments
from app.services.neostaffing_write_authority import lock_staffing_write_authority
from app.services.permission_rules import user_can
from app.services.access_control import user_has_app_access

PERMISSION = 'neostaffing.people.edit'


class TransferConflict(ValueError):
    pass


def destinations():
    units = {row.id: row for row in StaffingUnit.query.order_by(StaffingUnit.id).all()}
    result = []
    for area in units.values():
        if area.unit_type != 'work_area':
            continue
        node, seen = area, set()
        while node and node.active and node.id not in seen:
            seen.add(node.id)
            if node.unit_type == 'sort':
                if not assignments.is_shift(area, units) or staffing.shift_work_area_type(area) in {'Door','Ballmat','Discharge'}:
                    result.append(area)
                break
            node = units.get(node.parent_id)
    return sorted(result, key=lambda area: (staffing.unit_path(area.parent).casefold(),area.name.casefold(),area.id))


def transfer_employee(person_id, actor, values):
    """Caller commits once. Person lock fences profile, Home and flow changes."""
    try:
        actor = lock_staffing_write_authority(actor, [PERMISSION])
    except ValueError:
        raise PermissionError('Session or permissions changed. Sign in again before transferring.') from None
    if not user_has_app_access(actor, 'neostaffing') or not user_can(PERMISSION, actor):
        raise PermissionError('You no longer have permission to transfer employees.')
    if values.get('confirm_transfer') != 'yes':
        raise ValueError('Confirm the employee transfer before continuing.')
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text("SET LOCAL lock_timeout='5s'"))
    person = db.session.scalar(select(StaffingPerson).where(StaffingPerson.id == person_id)
        .execution_options(populate_existing=True).with_for_update())
    if not person or not person.active:
        raise TransferConflict('Employee is no longer active. Reopen the editor.')
    if (values.get('expected_person_version') != staffing.entity_version(person)
            or str(values.get('expected_assignment_version', '')) != assignments.version(person)):
        raise TransferConflict('Employee or Work Area changed. Reopen the editor before transferring.')
    rows = assignments.flow_assignments(person, active_only=True)
    source = next((row for row in rows if str(row.id) == str(values.get('source_assignment_id'))), None)
    if not source:
        raise TransferConflict('The selected current assignment changed. Reopen the editor.')
    allowed = {row.id: row for row in destinations()}
    try:
        destination = allowed[int(values.get('destination_work_area_id', ''))]
    except (ValueError, TypeError, KeyError):
        raise ValueError('Select an active destination Work Area.') from None
    staffing._validate_work_assignment(person, destination)
    # A concurrent hierarchy edit must not turn the selected destination into
    # a different/inactive location while this transfer waits for its locks.
    chain, node = [], destination
    while node:
        chain.append((node.id,node.parent_id,node.active,node.unit_type))
        node = node.parent
    db.session.scalars(select(StaffingUnit).where(StaffingUnit.id.in_([row[0] for row in chain]))
        .order_by(StaffingUnit.id).execution_options(populate_existing=True).with_for_update(read=True)).all()
    if any((db.session.get(StaffingUnit,i).parent_id,db.session.get(StaffingUnit,i).active,
            db.session.get(StaffingUnit,i).unit_type)!=(parent,active,kind) for i,parent,active,kind in chain):
        raise TransferConflict('Destination changed. Reopen the editor and choose it again.')
    if source.work_area_unit_id == destination.id:
        return person, False
    target_sort = assignments.sort_of(destination)
    if any(row.id != source.id and getattr(assignments.sort_of(row.work_area),'id',None) == target_sort.id for row in rows):
        raise ValueError('This employee already has an assignment in the destination Sort.')
    # Keep the prior placement and dates as history; do not rewrite its area.
    source.active = False
    db.session.add(StaffingWorkAssignment(person=person, work_area=destination,
        active=True, effective_date=date.today()))
    db.session.flush()  # canonical lifecycle removes a dormant Shift plan
    if assignments.is_shift(destination) and person.shift_flow_plan is None:
        staffing.create_shift_flow_plan(person,
            {'shift_flow_sort_start_work_area_id':destination.id}, destination)
        db.session.flush()
    db.session.expire(person, ['work_assignments','shift_flow_plan'])
    return person, True
