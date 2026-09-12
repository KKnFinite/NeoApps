"""Attendance-owned occurrence maintenance; no policy, counters or prompts."""
from calendar import monthrange
from datetime import date

from app.extensions import db
from app.models import StaffingAttendanceOccurrence, StaffingPerson
from sqlalchemy import and_


OCCURRENCE_CLEANUP_BATCH_SIZE = 250
QUALIFYING_STATUSES = frozenset(("call_in", "no_call"))


def occurrence_cutoff(as_of):
    """Nine calendar months, clamping end-of-month dates (not 270 days)."""
    month = as_of.year * 12 + as_of.month - 1 - 9
    year, month = divmod(month, 12)
    month += 1
    return date(year, month, min(as_of.day, monthrange(year, month)[1]))


def purge_expired_occurrences(as_of, *, skip_empty=False):
    """One fixed batch; no commit, worker, cache or poll. Caller owns transaction."""
    ids = db.session.query(StaffingAttendanceOccurrence.id).filter(
        StaffingAttendanceOccurrence.attendance_date < occurrence_cutoff(as_of),
    ).order_by(
        StaffingAttendanceOccurrence.attendance_date, StaffingAttendanceOccurrence.id,
    ).limit(OCCURRENCE_CLEANUP_BATCH_SIZE)
    # Passive maintenance must remain entirely read-only when nothing expired.
    # Mutation callers already write and use the single-statement fast path.
    if skip_empty and ids.first() is None:
        return 0
    # A bounded subquery avoids a separate ID SELECT/round trip. Zero matches
    # changes zero rows; no ORM hydration or per-occurrence deletion is needed.
    return StaffingAttendanceOccurrence.query.filter(
        StaffingAttendanceOccurrence.id.in_(ids),
    ).delete(synchronize_session=False)


def retained_occurrences_query(as_of):
    """Logical retention stays exact even while bounded physical cleanup catches up.

    Internal Staffing query, not an exposed endpoint. Future authorized consumers
    must add their employee scope/pagination before materializing results.
    """
    return StaffingAttendanceOccurrence.query.filter(
        StaffingAttendanceOccurrence.attendance_date >= occurrence_cutoff(as_of),
    )


def sync_attendance_occurrences(operation, statuses, *, user_id, as_of, workday_people=None):
    """Called only after validated current-sort writes under ordered person locks.

    One row per employee/operation; switches update it and corrections remove it.
    No tracker setting gates collection. Source hints remain unchanged; the
    employee-wide reconciliation record separately establishes completeness.
    The attendance and occurrence changes commit/rollback together.
    """
    if not statuses:
        return
    missing_groups = []
    if workday_people is not None:
        from app.models.staffing_accountability import StaffingAccountabilityWorkday as Workday, StaffingAccountabilitySource as Source
        from app.services.neostaffing_assignments import FT_COMBO
        loaded = db.session.query(StaffingPerson.id, StaffingAttendanceOccurrence, Source.workday_id).outerjoin(
            StaffingAttendanceOccurrence, and_(StaffingAttendanceOccurrence.person_id == StaffingPerson.id,
                StaffingAttendanceOccurrence.sort_date_operation_id == operation.id),
        ).outerjoin(Source, and_(Source.person_id == StaffingPerson.id, Source.operation_id == operation.id)
        ).filter(StaffingPerson.id.in_(statuses)).populate_existing().all()
        existing = {person_id: row for person_id, row, _ in loaded if row is not None}
        for person_id, _, group_id in loaded:
            if (group_id is None and statuses[person_id] in QUALIFYING_STATUSES
                    and workday_people[person_id].classification not in FT_COMBO):
                group = Workday(person_id=person_id, workday_date=operation.sort_date,
                    requires_pair=False, created_by_user_id=user_id)
                db.session.add(group)
                missing_groups.append(group)
    else:
        existing = {row.person_id: row for row in StaffingAttendanceOccurrence.query.filter(
            StaffingAttendanceOccurrence.sort_date_operation_id == operation.id,
            StaffingAttendanceOccurrence.person_id.in_(statuses),
        ).populate_existing().all()}
    cutoff = occurrence_cutoff(as_of)
    for person_id, status in statuses.items():
        row = existing.get(person_id)
        if status not in QUALIFYING_STATUSES or operation.sort_date < cutoff:
            if row is not None:
                db.session.delete(row)
        elif row is None:
            db.session.add(StaffingAttendanceOccurrence(
                person_id=person_id, sort_date_operation_id=operation.id,
                attendance_date=operation.sort_date, status=status,
                reconciliation_needed=True, updated_by_user_id=user_id,
            ))
        elif row.status != status:
            row.status = status
            row.updated_by_user_id = user_id
    if missing_groups:
        db.session.flush()
        db.session.add_all(Source(person_id=group.person_id, workday_id=group.id,
            operation_id=operation.id, position=1) for group in missing_groups)
    purge_expired_occurrences(as_of)
