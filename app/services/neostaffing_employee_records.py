"""Manual Employee Records with current-scope authorization and immutable history."""
import json
from datetime import datetime

from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import User, StaffingPerson, StaffingUnit, StaffingWorkAssignment, StaffingLeadershipAssignment
from app.models.staffing_employee_record import (
    StaffingEmployeeRecord as Record, StaffingEmployeeRecordEvent as Event,
    StaffingEmployeeRecordSetting as Setting,
)
from app.models.staffing_accountability import StaffingAccountabilityResolution
from app.services import neostaffing as staffing
from app.services.access_control import get_user_app_role, user_can_access_app
from app.services.neostaffing_attendance_authority import attendance_authority
from app.services import neostaffing_record_storage as storage
from app.services.neostaffing_write_authority import lock_staffing_write_authority

KINDS = {"talk_with": "Talk With", "verbal": "Verbal", "written_warning": "Written Warning"}
ACKNOWLEDGMENT = "This information was reviewed with me."


def is_grandmaster(user):
    return bool(user.is_active and (user.role == "grandmaster" or get_user_app_role(user, "neostaffing") == "grandmaster"))


def authority(user):
    # Do not reuse stale hierarchy ORM state across a lock wait.
    units = StaffingUnit.query.filter_by(active=True).order_by(StaffingUnit.id).populate_existing().all()
    hierarchy = staffing._staffing_hierarchy_from_units(units)
    return hierarchy, attendance_authority(user, hierarchy)


def chain(unit, units):
    result, seen = [], set()
    while unit and unit.active and unit.id not in seen:
        seen.add(unit.id)
        result.append(unit)
        unit = units.get(unit.parent_id)
    return result if unit is None else []


def person_units(person_id):
    return set(db.session.scalars(select(StaffingWorkAssignment.work_area_unit_id).where(
        StaffingWorkAssignment.person_id == person_id, StaffingWorkAssignment.active.is_(True)))) | set(
        db.session.scalars(select(StaffingLeadershipAssignment.unit_id).where(
            StaffingLeadershipAssignment.person_id == person_id, StaffingLeadershipAssignment.active.is_(True))))


def covered_units(hierarchy, auth):
    """Work areas use central authority; managerial units require complete scope."""
    units = hierarchy["by_id"]
    descendants = {unit.id: set() for unit in units.values()}
    for unit in units.values():
        if unit.unit_type == "work_area":
            for ancestor in chain(unit, units):
                descendants[ancestor.id].add(unit.id)
    return {key for key, areas in descendants.items() if areas and areas <= auth.work_area_ids}


def access(user, person_id, *, write=False, require_write=False):
    # Serialize target edits/transfers with the existing employee parent lock.
    # Fresh loads prevent a previously opened page granting historical access.
    if write:
        user = lock_staffing_write_authority(user, [])
        _, preliminary = authority(user)
        StaffingPerson.query.filter(StaffingPerson.id.in_({person_id, preliminary.person_id} - {None})).order_by(
            StaffingPerson.id).populate_existing().with_for_update().all()
        StaffingLeadershipAssignment.query.filter_by(person_id=preliminary.person_id, active=True).order_by(
            StaffingLeadershipAssignment.id).populate_existing().with_for_update(read=True).all()
    person = StaffingPerson.query.filter_by(id=person_id).populate_existing().first()
    if not person:
        raise ValueError("Employee not found.")
    fresh_user = db.session.get(User, user.id, populate_existing=True)
    hierarchy, auth = authority(fresh_user)
    unit_ids = person_units(person_id)
    if write:
        dependencies = set(unit_ids) | set(auth.work_area_ids)
        ancestor_ids = {unit.id for key in dependencies for unit in chain(hierarchy["by_id"].get(key), hierarchy["by_id"])}
        StaffingUnit.query.filter(StaffingUnit.id.in_(ancestor_ids)).order_by(StaffingUnit.id).populate_existing().with_for_update(read=True).all()
        # Hierarchy/role state is re-evaluated after any lock wait.
        hierarchy, auth = authority(fresh_user)
        unit_ids = person_units(person_id)
    scoped = person.active and bool(unit_ids & covered_units(hierarchy, auth))
    historical_fallback = (not person.active or not unit_ids) and is_grandmaster(fresh_user)
    if not scoped and not (not (write or require_write) and historical_fallback):
        raise ValueError("Current NeoStaffing management authority over this employee is required.")
    return person, hierarchy, unit_ids


def context_for(hierarchy, ids):
    units = hierarchy["by_id"]
    return [[{"id": unit.id, "type": unit.unit_type, "name": unit.name}
             for unit in reversed(chain(units.get(key), units))]
            for key in sorted(ids) if chain(units.get(key), units)]


def enabled(hierarchy, ids):
    setting_ids = {unit.id for key in ids for unit in chain(hierarchy["by_id"].get(key), hierarchy["by_id"])
                   if unit.unit_type in ("operation", "department")}
    return bool(setting_ids and db.session.scalar(select(Setting.unit_id).where(
        Setting.unit_id.in_(setting_ids), Setting.enabled.is_(True)).limit(1)))


def text_value(value):
    value = str(value or "").strip()
    if not value or len(value) > 12000:
        raise ValueError("Enter between 1 and 12,000 characters.")
    return value


def add_event(record, kind, body, user):
    sequence = (db.session.scalar(select(func.max(Event.sequence)).where(Event.record_id == record.id)) or 0) + 1
    db.session.add(Event(record_id=record.id, sequence=sequence, kind=kind, body=body, actor_id=user.id))


def create(user, person_id, kind, body, resolution_id=None):
    person, hierarchy, ids = access(user, person_id, write=True)
    if not enabled(hierarchy, ids):
        raise ValueError("Employee Records is OFF for this employee. Existing history remains available.")
    if kind not in KINDS:
        raise ValueError("Choose Talk With, Verbal or Written Warning.")
    resolution = None
    if resolution_id:
        resolution = db.session.get(StaffingAccountabilityResolution, int(resolution_id))
        if not resolution or resolution.person_id != person.id:
            raise ValueError("Discipline source does not belong to this employee.")
    record = Record(person_id=person.id, kind=kind, body=text_value(body),
        context_json=json.dumps(context_for(hierarchy, ids)), created_by=user.id,
        discipline_resolution_id=resolution.id if resolution else None,
        discipline_snapshot_json=json.dumps({"id": resolution.id, "action": resolution.action,
            "kind": resolution.kind, "actor_id": resolution.actor_id, "resolved_at": resolution.resolved_at.isoformat(),
            "trigger_workday_id": resolution.trigger_workday_id}) if resolution else None)
    db.session.add(record)
    db.session.flush()
    add_event(record, "created", json.dumps({"kind": kind, "body": record.body}), user)
    return record


def locked_record(user, record_id, version=None):
    identity = db.session.get(Record, record_id)
    if not identity:
        raise ValueError("Record not found.")
    access(user, identity.person_id, write=True)
    record = Record.query.filter_by(id=record_id).populate_existing().with_for_update().one()
    if version is not None and str(record.version) != str(version):
        raise ValueError("This record changed. Reload before saving.")
    return record


def edit(user, record_id, version, kind, body):
    if version is None:
        raise ValueError("Record version is required. Reload before saving.")
    record = locked_record(user, record_id, version)
    if record.finalized_at:
        raise ValueError("Finalized originals cannot be edited. Add dated context instead.")
    if kind not in KINDS:
        raise ValueError("Invalid record type.")
    record.kind, record.body = kind, text_value(body)
    record.version += 1
    record.updated_at = datetime.utcnow()
    add_event(record, "edited", json.dumps({"kind": kind, "body": record.body}), user)
    return record


def finalize(user, record_id, version, method, acknowledged, raw=None):
    if version is None:
        raise ValueError("Record version is required. Reload before reviewing.")
    record = locked_record(user, record_id, version)
    if record.finalized_at:
        raise ValueError("Record is already finalized.")
    if method not in ("signature", "rts") or acknowledged != "yes":
        raise ValueError("Confirm in-person review and choose acknowledgment or Refuse to Sign.")
    if method == "signature":
        record.signature_key, record.signature_sha256, record.signature_size = storage.store(record.id, raw)
    record.acknowledgment = method
    record.acknowledgment_text = ACKNOWLEDGMENT
    record.finalized_at = record.updated_at = datetime.utcnow()
    record.finalized_by = user.id
    record.version += 1
    add_event(record, "finalized", method, user)
    return record


def addendum(user, record_id, body, expected_sequence):
    record = locked_record(user, record_id)
    if not record.finalized_at:
        raise ValueError("Edit the draft before review; addenda apply to finalized records.")
    actual = db.session.scalar(select(func.max(Event.sequence)).where(Event.record_id == record.id)) or 0
    if str(actual) != str(expected_sequence):
        raise ValueError("Record history changed. Reload before adding context.")
    add_event(record, "addendum", text_value(body), user)


def configure(user, unit_id, on, version):
    user = lock_staffing_write_authority(user, [])
    hierarchy, auth = authority(user)
    if auth.person_id:
        StaffingPerson.query.filter_by(id=auth.person_id).populate_existing().with_for_update(read=True).one()
        StaffingLeadershipAssignment.query.filter_by(person_id=auth.person_id, active=True).order_by(
            StaffingLeadershipAssignment.id).populate_existing().with_for_update(read=True).all()
        hierarchy, auth = authority(user)
    if not user_can_access_app(user, "neostaffing", minimum_role="master"):
        raise ValueError("Staffing Master authority is required to configure Employee Records.")
    unit = hierarchy["by_id"].get(unit_id)
    if not unit or unit.unit_type not in ("operation", "department") or unit_id not in covered_units(hierarchy, auth):
        raise ValueError("Configuration is outside your current management scope.")
    # Operation then Department ordering serializes inherited ON and child edits.
    ids = sorted(u.id for u in chain(unit, hierarchy["by_id"]) if u.unit_type in ("operation", "department"))
    StaffingUnit.query.filter(StaffingUnit.id.in_(ids)).order_by(StaffingUnit.id).populate_existing().with_for_update().all()
    hierarchy, auth = authority(user)
    if unit_id not in covered_units(hierarchy, auth):
        raise ValueError("Configuration authority changed. Reload before saving.")
    if unit.unit_type == "department":
        parent = db.session.get(Setting, unit.parent_id, populate_existing=True)
        if parent and parent.enabled:
            raise ValueError("This Department is ON BY OPERATION.")
    row = db.session.get(Setting, unit.id, populate_existing=True)
    if str(row.version if row else 0) != str(version):
        raise ValueError("Configuration changed. Reload before saving.")
    if not row:
        row = Setting(unit_id=unit.id, version=0, actor_id=user.id)
        db.session.add(row)
    row.enabled, row.actor_id, row.updated_at = bool(on), user.id, datetime.utcnow()
    row.version += 1


def directory(user, search, page):
    hierarchy, auth = authority(user)
    units = covered_units(hierarchy, auth)
    if not units and not is_grandmaster(user):
        raise ValueError("Current NeoStaffing management authority is required.")
    workers = select(StaffingWorkAssignment.person_id).where(StaffingWorkAssignment.active.is_(True), StaffingWorkAssignment.work_area_unit_id.in_(units))
    leaders = select(StaffingLeadershipAssignment.person_id).where(StaffingLeadershipAssignment.active.is_(True), StaffingLeadershipAssignment.unit_id.in_(units))
    scope = StaffingPerson.active.is_(True) & or_(StaffingPerson.id.in_(workers), StaffingPerson.id.in_(leaders))
    if is_grandmaster(user):
        all_workers = select(StaffingWorkAssignment.person_id).where(StaffingWorkAssignment.active.is_(True))
        all_leaders = select(StaffingLeadershipAssignment.person_id).where(StaffingLeadershipAssignment.active.is_(True))
        scope = or_(scope, StaffingPerson.active.is_(False), (~StaffingPerson.id.in_(all_workers) & ~StaffingPerson.id.in_(all_leaders)))
    query = StaffingPerson.query.filter(scope)
    if search:
        pattern = "%" + str(search)[:100] + "%"
        query = query.filter(or_(StaffingPerson.first_name.ilike(pattern), StaffingPerson.last_name.ilike(pattern), StaffingPerson.employee_id.ilike(pattern)))
    people = query.order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingPerson.id).paginate(page=page, per_page=25, error_out=False)
    settings = {s.unit_id: s for s in Setting.query.all()}
    configurable = [u for u in hierarchy["units"] if u.id in units and u.unit_type in ("operation", "department")]
    return people, configurable, settings
