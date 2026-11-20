"""Audit and import verified, status-only legacy attendance evidence."""

import json
from collections import defaultdict
from dataclasses import dataclass, field

from flask import current_app
from sqlalchemy import func, select, tuple_, union
from sqlalchemy.orm import load_only

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingAttendanceOutcome as Outcome, StaffingDailyAttendance as Detail,
    StaffingPerson, StaffingTimecardEdit as Edit, StaffingTimecardSlice as Slice, StaffingUnit,
)
from app.services import neostaffing as staffing
from app.services.neostaffing_history_retention import retention_cutoff
from app.services.gateway_matrix import current_gateway_local_datetime


BATCH_SIZE = 250


@dataclass
class LegacyAudit:
    daily_attendance: int = 0
    timecard: int = 0
    explicit_clears: int = 0
    unknown_classification: int = 0
    earliest_mark: str | None = None
    inserted: int = 0
    withheld: dict = field(default_factory=dict)

    def withhold(self, reason):
        self.withheld[reason] = self.withheld.get(reason, 0) + 1


def json_mapping(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def timecard_labels(raw, gateway_code):
    context = json_mapping(raw)
    if context.get("gateway", gateway_code) != gateway_code:
        return {}
    identity_fields = (("employee_id", 80), ("employee", 161))
    if any(not isinstance(value := context.get(key), str) or not value.strip() or len(value) > limit
           for key, limit in identity_fields):
        return {}
    classification = context.get("classification_at_recording")
    if not isinstance(classification, str) or classification not in staffing.CLASSIFICATION_LABELS:
        context.pop("classification_at_recording", None)
    return context


def _linked(row, operation, day, units):
    sort = units.get(row.sort_unit_id)
    area = units.get(row.work_area_unit_id)
    return (day == operation.sort_date
            and sort and sort.unit_type == "sort" and area and area.unit_type == "work_area"
            and staffing._normalize_staffing_sort_name(sort.name)
            == staffing._normalize_staffing_sort_name(operation.sort_name))


def _timecard_proof(row, operation, units, edits):
    if not _linked(row, operation, row.workday_date, units):
        return None
    first = edits.get((row.id, 1), [])
    latest = edits.get((row.id, row.version), [])
    context = timecard_labels(row.context_json, operation.gateway_code)
    if len(first) != 1 or len(latest) != 1:
        return None
    first_status = json_mapping(first[0].snapshot_json).get("status")
    if (not isinstance(first_status, str) or first_status not in staffing.ATTENDANCE_STATUS_LABELS
            or row.attendance_status not in {*staffing.ATTENDANCE_STATUS_LABELS, ""}
            or json_mapping(latest[0].snapshot_json).get("status") != row.attendance_status
            or latest[0].recorded_at < first[0].recorded_at
            or not context):
        return None
    classification = context.get("classification_at_recording") if first[0].actor_id is not None else None
    return context, classification, first[0], latest[0]


def _legacy_entry(operation, person, details, slices, units, edits, audit):
    if len(details) > 1 or len(slices) > 1:
        audit.withhold("ambiguous_individual_records")
        return None
    detail = details[0] if details else None
    if detail and (
        not _linked(detail, operation, detail.attendance_date, units)
        or detail.status not in staffing.ATTENDANCE_STATUS_LABELS
        or detail.updated_at < detail.recorded_at
    ):
        audit.withhold("invalid_daily_attendance_attribution")
        return None
    slice_row = slices[0] if slices else None
    if detail and slice_row and (detail.sort_unit_id != slice_row.sort_unit_id
                                 or detail.work_area_unit_id != slice_row.work_area_unit_id):
        slice_row = None  # Daily detail establishes the mark. Ignore conflicting labels.
    proof = _timecard_proof(slice_row, operation, units, edits) if slice_row else None
    if not detail and not proof:
        audit.withhold("unverified_timecard")
        return None

    labels, classification, first, latest = proof or ({}, None, None, None)
    row = detail or slice_row
    kind = "daily_attendance" if detail else "timecard"
    status = detail.status if detail else slice_row.attendance_status or None
    setattr(audit, kind, getattr(audit, kind) + 1)
    audit.explicit_clears += status is None
    audit.unknown_classification += classification is None
    if status:
        day = operation.sort_date.isoformat()
        audit.earliest_mark = min(day, audit.earliest_mark or day)
    return dict(
        sort_date_operation_id=operation.id, person_id=person.id, gateway_code=operation.gateway_code,
        sort_name=operation.sort_name, sort_date=operation.sort_date, sort_unit_id=row.sort_unit_id,
        work_area_id=row.work_area_unit_id, employee_id=labels.get("employee_id") or person.employee_id,
        employee_name=labels.get("employee") or person.full_name, classification=classification, status=status,
        recorded_at=detail.recorded_at if detail else first.recorded_at,
        updated_at=detail.updated_at if detail else latest.recorded_at,
        source_kind=kind, source_record_id=row.id,
    )


def audit_or_import_legacy_marks(*, write=False, as_of=None):
    """Read-only by default. Explicit cutover copies only eligible individual marks."""
    as_of = as_of or current_gateway_local_datetime().date()
    cutoff = retention_cutoff(as_of)
    gateway_code = current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper()
    audit = LegacyAudit()
    audit.withheld["unlinked_daily_attendance"] = db.session.scalar(select(func.count()).select_from(Detail).where(
        Detail.sort_date_operation_id.is_(None), Detail.attendance_date.between(cutoff, as_of),
    ))
    # Batch by operation and employee so duplicate rows from the two sources
    # can be compared together. The read-only audit works before schema cutover.
    sources = union(
        select(Detail.sort_date_operation_id.label("operation_id"), Detail.person_id)
        .join(SortDateOperation, SortDateOperation.id == Detail.sort_date_operation_id)
        .where(SortDateOperation.gateway_code == gateway_code, SortDateOperation.sort_date.between(cutoff, as_of)),
        select(Slice.sort_date_operation_id.label("operation_id"), Slice.person_id)
        .join(SortDateOperation, SortDateOperation.id == Slice.sort_date_operation_id)
        .where(SortDateOperation.gateway_code == gateway_code, SortDateOperation.sort_date.between(cutoff, as_of)),
    ).subquery()
    units = {unit.id: unit for unit in StaffingUnit.query.all()}
    after = (0, 0)
    while keys := db.session.execute(select(sources.c.operation_id, sources.c.person_id)
            .where(tuple_(sources.c.operation_id, sources.c.person_id) > after)
            .order_by(sources.c.operation_id, sources.c.person_id).limit(BATCH_SIZE)).all():
        after = tuple(keys[-1])
        operations = {row.id: row for row in SortDateOperation.query.filter(
            SortDateOperation.id.in_({key.operation_id for key in keys}))}
        people = {person.id: person for person in StaffingPerson.query.filter(
            StaffingPerson.id.in_({key.person_id for key in keys}))}
        details = defaultdict(list)
        for row in Detail.query.options(load_only(
            Detail.id, Detail.person_id, Detail.sort_date_operation_id, Detail.sort_unit_id,
            Detail.work_area_unit_id, Detail.attendance_date, Detail.status, Detail.recorded_at, Detail.updated_at,
        )).filter(tuple_(Detail.sort_date_operation_id, Detail.person_id).in_(keys)):
            details[row.sort_date_operation_id, row.person_id].append(row)
        slices = defaultdict(list)
        rows = Slice.query.filter(tuple_(Slice.sort_date_operation_id, Slice.person_id).in_(keys)).all()
        for row in rows:
            slices[row.sort_date_operation_id, row.person_id].append(row)
        edits = defaultdict(list)
        versions = {(row.id, version) for row in rows for version in (1, row.version)}
        if versions:
            for edit in Edit.query.filter(tuple_(Edit.slice_id, Edit.version).in_(versions)):
                edits[edit.slice_id, edit.version].append(edit)

        entries = []
        for operation_id, person_id in keys:
            person = people.get(person_id)
            if person is None:
                audit.withhold("missing_employee_identity")
                continue
            key = operation_id, person_id
            entry = _legacy_entry(operations[operation_id], person, details[key], slices[key], units, edits, audit)
            if entry is not None:
                entries.append(entry)
        if write and entries:
            from app.services.neostaffing_historical_records import _insert

            statement = _insert(Outcome).on_conflict_do_nothing(
                index_elements=["sort_date_operation_id", "person_id"],
            ).returning(Outcome.person_id)
            audit.inserted += len(db.session.scalars(statement, entries).all())
    return audit
