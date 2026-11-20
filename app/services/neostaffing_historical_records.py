"""Forward, operation-scoped staffing and individual attendance evidence."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.extensions import db
from app.models import (
    Gateway, SortDateOperation, SortTimelineSortSetting, StaffingAttendanceOutcome as Outcome,
    StaffingDailyAttendance, StaffingPerson, StaffingRosterClose as Close, StaffingTimecardEdit,
    StaffingRosterPlacement as Placement, StaffingRosterRevision as Revision, StaffingTimecardSlice, StaffingUnit,
)
from app.services import neostaffing as staffing
from app.services.gateway_matrix import current_gateway_local_datetime, gateway_timezone
from app.services.neostaffing_history_legacy import timecard_labels
from app.services.neostaffing_history_retention import history_cutoff, retention_cutoff
from app.services.neostaffing_history_schema import ROSTER_HISTORY_LOCK


DISCOVERY_DAYS = 7
DISCOVERY_LIMIT = 32


def enabled():
    return current_app.config.get("NEOSTAFFING_HISTORY_ENABLED", False) is True


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _insert(model):
    return (pg_insert if db.session.get_bind().dialect.name == "postgresql" else sqlite_insert)(model)


def _utc_boundary(local, zone):
    matches = {value for fold in (0, 1)
               if (value := local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc))
               .astimezone(zone).replace(tzinfo=None) == local}
    return next(iter(matches)).replace(tzinfo=None) if len(matches) == 1 else None


def _window(operation, setting, zone):
    if not setting or not setting.sort_window_start_local or not setting.sort_window_end_local:
        return None
    opens = datetime.combine(operation.sort_date, setting.sort_window_start_local)
    closes = datetime.combine(operation.sort_date, setting.sort_window_end_local)
    if closes <= opens:
        closes += timedelta(days=1)
    start, end = _utc_boundary(opens, zone), _utc_boundary(closes, zone)
    return (start, end) if start and end and start < end else None


def _roster_as_of(closes_at):
    # A transaction that spans the boundary belongs entirely after the close.
    late_changes = select(Revision.change_set_id).where(
        Revision.changed_at >= closes_at, Revision.change_set_id.is_not(None),
    )
    latest = select(
        Revision.kind, Revision.entity_id, Revision.state,
        func.row_number().over(partition_by=(Revision.kind, Revision.entity_id),
                               order_by=(Revision.changed_at.desc(), Revision.id.desc())).label("rank"),
    ).where(Revision.kind != "baseline", Revision.changed_at < closes_at,
            or_(Revision.change_set_id.is_(None), Revision.change_set_id.not_in(late_changes))).subquery()
    states = {"assignment": {}, "person": {}, "unit": {}}
    for kind, entity_id, state in db.session.execute(
        select(latest.c.kind, latest.c.entity_id, latest.c.state).where(latest.c.rank == 1, latest.c.state.is_not(None))
    ):
        if state["active"]:
            states[kind][entity_id] = state
    return states


def _area_paths(units, sort_id):
    paths = {}
    for area_id, area in units.items():
        if area["unit_type"] != "work_area":
            continue
        names, seen, current_id = [], set(), area_id
        while current_id in units and current_id not in seen:
            seen.add(current_id)
            unit = units[current_id]
            names.append(unit["name"])
            if unit["unit_type"] == "sort":
                if current_id == sort_id:
                    paths[area_id] = " / ".join(reversed(names))
                break
            current_id = unit["parent_id"]
    return paths


def seal_roster(operation_id, at):
    """Seal once from committed, dated revisions. Never read today's roster."""
    if db.session.get_bind().dialect.name == "postgresql" and not db.session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": ROSTER_HISTORY_LOCK}
    ):
        return False
    close = db.session.scalar(select(Close).where(
        Close.sort_date_operation_id == operation_id, Close.sealed_at.is_(None),
        Close.unavailable_at.is_(None), Close.closes_at <= at,
    ).with_for_update())
    if close is None:
        return False
    states = _roster_as_of(close.closes_at)
    paths = _area_paths(states["unit"], close.sort_unit_id)
    # A removed/replaced Sort definition means the capture cannot establish coverage.
    if close.sort_unit_id not in states["unit"]:
        close.unavailable_at = at
        return False
    rows = {}
    for assignment in states["assignment"].values():
        person_id, area_id = assignment["person_id"], assignment["work_area_unit_id"]
        person = states["person"].get(person_id)
        if area_id not in paths or not person or person["classification"] not in staffing.NON_MANAGEMENT_CLASSIFICATIONS:
            continue
        rows[person_id, area_id] = dict(
            sort_date_operation_id=operation_id, person_id=person_id, work_area_id=area_id, area_path=paths[area_id],
            employee_id=person["employee_id"], employee_name=f"{person['first_name']} {person['last_name']}".strip(),
            classification=person["classification"],
        )
    if rows:
        db.session.execute(Placement.__table__.insert(), list(rows.values()))
    close.sealed_at = at
    db.session.flush()
    return True


def discover_and_seal(gateway_id, *, at=None):
    """Existing safety heartbeat enrolls recent real operations and seals one."""
    at = at or utcnow()
    if not enabled():
        return False
    gateway = db.session.get(Gateway, gateway_id)
    if not gateway or gateway.code != current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper():
        return False
    baseline = db.session.scalar(select(func.min(Revision.changed_at)).where(Revision.kind == "baseline"))
    if baseline is None:
        return False
    zone = ZoneInfo(gateway_timezone(gateway))
    local_date = current_gateway_local_datetime(gateway, now=at.replace(tzinfo=timezone.utc)).date()
    candidates = db.session.execute(
        select(SortDateOperation).outerjoin(Close, Close.sort_date_operation_id == SortDateOperation.id)
        .where(SortDateOperation.gateway_code == gateway.code,
               or_(SortDateOperation.gateway_id == gateway.id, SortDateOperation.gateway_id.is_(None)),
               SortDateOperation.sort_date.between(local_date - timedelta(days=DISCOVERY_DAYS), local_date),
               Close.sort_date_operation_id.is_(None)).order_by(SortDateOperation.sort_date.desc())
        .limit(DISCOVERY_LIMIT)
    ).scalars().all()
    if candidates:
        settings = {staffing._normalize_staffing_sort_name(row.sort_name): row for row in
                    SortTimelineSortSetting.query.filter_by(gateway_id=gateway.id).all()}
        hierarchy = staffing._daily_attendance_hierarchy()
        rows = []
        for operation in candidates:
            setting = settings.get(staffing._normalize_staffing_sort_name(operation.sort_name))
            window = _window(operation, setting, zone)
            if not window:
                continue
            opens, closes = window
            if baseline > opens or at < opens or operation.created_at >= closes:
                continue
            if setting.updated_at > opens:
                # If this operation was never enrolled, a mid-Sort schedule edit
                # cannot establish which boundary governed its original roster.
                continue
            try:
                sort = staffing._staffing_sort_for_operation(operation, hierarchy)
            except ValueError:
                continue
            rows.append(dict(sort_date_operation_id=operation.id, gateway_code=gateway.code, sort_name=operation.sort_name,
                             sort_date=operation.sort_date, sort_unit_id=sort.id, opens_at=opens, closes_at=closes))
        if rows:
            db.session.execute(_insert(Close).on_conflict_do_nothing(index_elements=["sort_date_operation_id"]), rows)
    due_id = db.session.scalar(select(Close.sort_date_operation_id).where(
        Close.gateway_code == gateway.code, Close.sealed_at.is_(None),
        Close.unavailable_at.is_(None), Close.closes_at <= at,
        Close.sort_date >= local_date - timedelta(days=DISCOVERY_DAYS),
    ).order_by(Close.closes_at).limit(1))
    sealed = due_id is not None and seal_roster(due_id, at)
    db.session.commit()
    return sealed


def record_attendance_changes(operation, statuses, source_rows, assignments, timecards, units):
    """Run inside the authorized attendance transaction, including explicit clears."""
    if not enabled() or operation.sort_date < history_cutoff():
        return
    now = utcnow()
    rows = []
    for person_id, status in statuses.items():
        assignment = assignments[person_id]
        person = assignment.person
        source = source_rows.get(person_id)
        # A removed daily record still needs a tombstone for old timecard evidence.
        area_id = (source.work_area_unit_id if source else None) or assignment.work_area_unit_id
        sort_id = source.sort_unit_id if source else staffing.assignment_service.sort_of(
            units[assignment.work_area_unit_id], units).id
        slice_row = timecards.get(person_id)
        captured = (timecard_labels(slice_row.context_json, operation.gateway_code) if slice_row and
                    slice_row.work_area_unit_id == area_id else {})
        classification = None
        if source is not None and source.recorded_at is None:
            classification = person.classification  # Newly recorded canonical mark.
        rows.append(dict(
            sort_date_operation_id=operation.id, person_id=person_id, sort_date=operation.sort_date,
            gateway_code=operation.gateway_code, sort_name=operation.sort_name, sort_unit_id=sort_id,
            work_area_id=area_id, employee_id=captured.get("employee_id") or person.employee_id,
            employee_name=captured.get("employee") or person.full_name,
            classification=classification, status=status or None,
            recorded_at=(source.recorded_at if source else None) or now, updated_at=now,
            source_kind="live", source_record_id=source.id if source else None,
        ))
    if rows:
        insert = _insert(Outcome)
        remarked = Outcome.status.is_(None) & insert.excluded.status.is_not(None)
        capture_fields = ("work_area_id", "employee_id", "employee_name", "classification", "recorded_at",
                          "source_kind", "source_record_id")
        immutable = {key: case((remarked, getattr(insert.excluded, key)), else_=getattr(Outcome, key))
                     for key in capture_fields}
        db.session.execute(insert.on_conflict_do_update(
            index_elements=["sort_date_operation_id", "person_id"],
            set_={**immutable, "status": insert.excluded.status, "updated_at": insert.excluded.updated_at},
            where=Outcome.status.is_distinct_from(insert.excluded.status),
        ), rows)


def preserve_linked_details(operation, sort_id, *, as_of):
    """Save trusted linked detail before the existing cleanup. Never infer missing marks."""
    if not enabled() or operation.sort_date < retention_cutoff(as_of):
        return
    detail = StaffingDailyAttendance
    existing = select(Outcome.person_id).where(Outcome.sort_date_operation_id == operation.id)
    linked = db.session.execute(select(detail, StaffingPerson).join(StaffingPerson, StaffingPerson.id == detail.person_id)
        .join(StaffingUnit, StaffingUnit.id == detail.work_area_unit_id)
        .where(detail.sort_date_operation_id == operation.id, detail.attendance_date == operation.sort_date,
               detail.sort_unit_id == sort_id, StaffingUnit.unit_type == "work_area",
               detail.updated_at >= detail.recorded_at, detail.person_id.not_in(existing))).all()
    if not linked:
        return
    timecard = StaffingTimecardSlice
    contexts = {(person_id, area_id): timecard_labels(raw, operation.gateway_code)
                for person_id, area_id, raw in timecard.query.with_entities(
        timecard.person_id, timecard.work_area_unit_id, timecard.context_json
    ).join(StaffingTimecardEdit, StaffingTimecardEdit.slice_id == timecard.id).filter(
        timecard.sort_date_operation_id == operation.id, timecard.workday_date == operation.sort_date,
        timecard.sort_unit_id == sort_id, StaffingTimecardEdit.version == 1, StaffingTimecardEdit.actor_id.is_not(None),
    )}
    rows = []
    for source, person in linked:
        context = contexts.get((person.id, source.work_area_unit_id), {})
        rows.append(dict(
            sort_date_operation_id=operation.id, person_id=person.id, sort_date=operation.sort_date,
            gateway_code=operation.gateway_code, sort_name=operation.sort_name, sort_unit_id=sort_id,
            work_area_id=source.work_area_unit_id, employee_id=context.get("employee_id") or person.employee_id,
            employee_name=context.get("employee") or person.full_name, classification=context.get("classification_at_recording"),
            status=source.status, recorded_at=source.recorded_at, updated_at=source.updated_at,
            source_kind="daily_attendance", source_record_id=source.id,
        ))
    db.session.execute(_insert(Outcome).on_conflict_do_nothing(
        index_elements=["sort_date_operation_id", "person_id"]), rows)
