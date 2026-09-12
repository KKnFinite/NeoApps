from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import or_

from app.extensions import db
from app.models import (
    Gateway,
    SortDateOperation,
    StaffingAttendanceSummary,
    StaffingDailyAttendance,
)
from app.services import neostaffing as staffing_service
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    current_operations_for_gateway,
    operation_is_active_at,
)


ATTENDANCE_SUMMARY_RETENTION_DAYS = 365
ATTENDANCE_SUMMARY_CLEANUP_BATCH_SIZE = 250
# Catch up one operation per request, within the retained-summary horizon.
# Older summary absence cannot distinguish missed work from expired history.
ATTENDANCE_ROLLOVER_LOOKBACK_DAYS = ATTENDANCE_SUMMARY_RETENTION_DAYS


@dataclass(frozen=True)
class AttendanceSummaryFinalizationResult:
    sort_date_operation_id: int
    attendance_date: date
    staffing_sort_unit_id: int
    summary_count: int


@dataclass(frozen=True)
class AttendanceRolloverResult:
    current_sort_date_operation_id: int | None
    prior_sort_date_operation_id: int | None
    finalized_summary_count: int = 0
    purged_detail_count: int = 0
    purged_expired_summary_count: int = 0
    status: str = "no_current_operation"
    purged_expired_occurrence_count: int = 0

    @property
    def changed(self):
        return bool(
            self.finalized_summary_count
            or self.purged_detail_count
            or self.purged_expired_summary_count
            or self.purged_expired_occurrence_count
        )


def finalize_attendance_summaries(operation, user=None, finalized_at=None):
    """Idempotently recalculate retained Operation/Department totals."""
    operation_id = _positive_int(
        getattr(operation, "id", operation),
        "Sort Date Operation",
    )
    locked_operation = (
        SortDateOperation.query.filter_by(id=operation_id)
        .with_for_update(key_share=True)
        .first()
    )
    if not locked_operation:
        raise ValueError("The staffing Sort Date Operation was not found.")

    calculated = staffing_service.attendance_operation_department_counts(
        locked_operation
    )
    calculated_scopes = calculated["scopes"]
    expected_keys = {
        (row["scope"].unit_type, row["scope"].id)
        for row in calculated_scopes
    }
    existing = {
        (summary.scope_type, summary.scope_unit_id): summary
        for summary in StaffingAttendanceSummary.query.filter_by(
            sort_date_operation_id=locked_operation.id
        ).all()
    }
    finalized_at = finalized_at or datetime.utcnow()
    user_id = getattr(user, "id", None)
    for row in calculated_scopes:
        scope = row["scope"]
        key = (scope.unit_type, scope.id)
        summary = existing.get(key)
        if summary is None:
            summary = StaffingAttendanceSummary(
                sort_date_operation_id=locked_operation.id,
                attendance_date=locked_operation.sort_date,
                scope_type=scope.unit_type,
                scope_unit_id=scope.id,
            )
            db.session.add(summary)
        summary.on_payroll_count = row["on_payroll"]
        summary.worked_count = row["working"]
        summary.finalized_at = finalized_at
        summary.finalized_by_user_id = user_id
        summary.updated_by_user_id = user_id

    db.session.flush()
    persisted_keys = {
        (summary.scope_type, summary.scope_unit_id)
        for summary in StaffingAttendanceSummary.query.filter_by(
            sort_date_operation_id=locked_operation.id
        ).all()
    }
    if not expected_keys.issubset(persisted_keys):
        raise RuntimeError("Attendance summary persistence verification failed.")
    # Complete frozen workdays only from this canonical finalization command.
    # Reads never manufacture finalization or treat a missing partner as Here.
    from app.models.staffing_accountability import StaffingAccountabilityWorkday as Workday, StaffingAccountabilitySource as Source
    from app.services.neostaffing_workday_identity import bind_workday_sources
    from sqlalchemy import func
    last_id = 0
    while True:
        pending = db.session.query(Workday.id, Workday.person_id).join(Source, Source.workday_id == Workday.id).filter(
            Workday.id > last_id, Workday.requires_pair.is_(True),
            Workday.workday_date.between(locked_operation.sort_date - timedelta(days=14), locked_operation.sort_date),
            or_(Workday.first_sort_id == calculated["staffing_sort"].id,
                Workday.second_sort_id == calculated["staffing_sort"].id),
        ).group_by(Workday.id, Workday.person_id).having(func.count(Source.id) == 1).order_by(Workday.id).limit(500).all()
        if not pending:
            break
        from app.models import StaffingPerson
        db.session.query(StaffingPerson.id).filter(StaffingPerson.id.in_(
            {row.person_id for row in pending})).order_by(StaffingPerson.id).with_for_update().all()
        bind_workday_sources(locked_operation, {row.person_id for row in pending},
                             user_id=user_id, qualifying_person_ids=set())
        db.session.flush()
        last_id = pending[-1].id
    return AttendanceSummaryFinalizationResult(
        sort_date_operation_id=locked_operation.id,
        attendance_date=locked_operation.sort_date,
        staffing_sort_unit_id=calculated["staffing_sort"].id,
        summary_count=len(expected_keys),
    )


def _attendance_detail_scope(operation, staffing_sort_id):
    """Identical date/sort/identity contract for detection and deletion."""
    return (
        StaffingDailyAttendance.attendance_date == operation.sort_date,
        StaffingDailyAttendance.sort_unit_id == staffing_sort_id,
        or_(
            StaffingDailyAttendance.sort_date_operation_id == operation.id,
            StaffingDailyAttendance.sort_date_operation_id.is_(None),
        ),
    )


def process_attendance_rollover(current_operation, user=None, *, now_local=None):
    """Finalize then purge at most one outstanding Night in a bounded date window."""
    if not current_operation:
        return AttendanceRolloverResult(None, None)
    gateway = current_operation.gateway or _gateway_for_operation(current_operation)
    local_now = current_gateway_local_datetime(gateway, now=now_local)
    if not operation_is_active_at(current_operation, local_now, gateway):
        return AttendanceRolloverResult(
            current_operation.id,
            None,
            status="current_operation_not_active",
        )

    staffing_sort = staffing_service._staffing_sort_for_operation(
        current_operation, staffing_service._daily_attendance_hierarchy()
    )
    prior_candidates = SortDateOperation.query.filter(
        SortDateOperation.gateway_code == current_operation.gateway_code,
        SortDateOperation.sort_name == current_operation.sort_name,
        SortDateOperation.sort_date < current_operation.sort_date,
        SortDateOperation.sort_date >= (
            local_now.date() - timedelta(days=ATTENDANCE_ROLLOVER_LOOKBACK_DAYS)
        ),
    )
    outstanding_details = db.session.query(StaffingDailyAttendance.id).filter(
        *_attendance_detail_scope(SortDateOperation, staffing_sort.id)
    ).exists()
    has_summary = db.session.query(StaffingAttendanceSummary.id).filter(
        StaffingAttendanceSummary.sort_date_operation_id == SortDateOperation.id
    ).exists()
    prior_operation = (
        prior_candidates.filter(or_(outstanding_details, ~has_summary))
        .order_by(SortDateOperation.sort_date, SortDateOperation.id)
        .with_for_update(key_share=True)
        .first()
    )
    if not prior_operation:
        # Preserve the existing already-processed result without walking history.
        latest = prior_candidates.order_by(
            SortDateOperation.sort_date.desc(), SortDateOperation.id.desc()
        ).first()
        return AttendanceRolloverResult(
            current_operation.id,
            latest.id if latest else None,
            status="already_processed" if latest else "no_prior_operation",
        )

    detail_count = StaffingDailyAttendance.query.filter(
        *_attendance_detail_scope(prior_operation, staffing_sort.id)
    ).count()
    existing_summary_count = StaffingAttendanceSummary.query.filter_by(
        sort_date_operation_id=prior_operation.id
    ).count()
    if existing_summary_count and not detail_count:
        return AttendanceRolloverResult(
            current_operation.id,
            prior_operation.id,
            status="already_processed",
        )

    finalization = finalize_attendance_summaries(
        prior_operation,
        user,
    )
    legacy_or_linked = StaffingDailyAttendance.query.filter(
        *_attendance_detail_scope(prior_operation, finalization.staffing_sort_unit_id)
    )
    purged_detail_count = legacy_or_linked.delete(synchronize_session=False)
    db.session.flush()
    return AttendanceRolloverResult(
        current_operation.id,
        prior_operation.id,
        finalized_summary_count=finalization.summary_count,
        purged_detail_count=purged_detail_count,
        status="processed",
    )


def maintain_current_attendance_rollover(user=None, *, now_local=None, sort_name=None):
    """User-driven rollover hook; it never generates an operation or commits."""
    gateway = _default_gateway()
    if not gateway:
        return AttendanceRolloverResult(None, None)
    local_now = current_gateway_local_datetime(gateway, now=now_local)
    current_operation = next(
        (
            operation
            for operation in current_operations_for_gateway(gateway, now=local_now)
            if staffing_service._normalize_staffing_sort_name(operation.sort_name)
            == staffing_service._normalize_staffing_sort_name(sort_name or staffing_service.ATTENDANCE_OPERATION_SORT_NAME)
            and operation_is_active_at(operation, local_now, gateway)
        ),
        None,
    )
    result = process_attendance_rollover(
        current_operation,
        user,
        now_local=local_now,
    )
    from app.services.neostaffing_accountability import purge_expired_occurrences

    result = replace(result, purged_expired_occurrence_count=purge_expired_occurrences(local_now.date(), skip_empty=True))
    from app.services.neostaffing_discipline import purge_expired_discipline
    result = replace(result, purged_expired_occurrence_count=(
        result.purged_expired_occurrence_count + purge_expired_discipline(local_now.date())))
    if result.status != "processed":
        return result
    expired_count = purge_expired_attendance_summaries(
        as_of=local_now.date(),
    )
    return AttendanceRolloverResult(
        result.current_sort_date_operation_id,
        result.prior_sort_date_operation_id,
        finalized_summary_count=result.finalized_summary_count,
        purged_detail_count=result.purged_detail_count,
        purged_expired_summary_count=expired_count,
        status=result.status,
        purged_expired_occurrence_count=result.purged_expired_occurrence_count,
    )


def purge_expired_attendance_summaries(
    *,
    as_of=None,
    retention_days=ATTENDANCE_SUMMARY_RETENTION_DAYS,
    batch_size=ATTENDANCE_SUMMARY_CLEANUP_BATCH_SIZE,
):
    """Delete at most one bounded batch older than the one-year retention."""
    as_of = as_of or date.today()
    retention_days = _positive_int(retention_days, "Retention Days")
    batch_size = _positive_int(batch_size, "Cleanup Batch Size")
    cutoff = as_of - timedelta(days=retention_days)
    expired_ids = [
        summary_id
        for (summary_id,) in (
            db.session.query(StaffingAttendanceSummary.id)
            .filter(StaffingAttendanceSummary.attendance_date < cutoff)
            .order_by(
                StaffingAttendanceSummary.attendance_date,
                StaffingAttendanceSummary.id,
            )
            .limit(batch_size)
            .all()
        )
    ]
    if not expired_ids:
        return 0
    deleted = StaffingAttendanceSummary.query.filter(
        StaffingAttendanceSummary.id.in_(expired_ids)
    ).delete(synchronize_session=False)
    db.session.flush()
    return deleted


def _default_gateway():
    gateway_code = str(
        current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD") or "RFD"
    ).strip().upper()
    return Gateway.query.filter_by(code=gateway_code, is_active=True).first()


def _gateway_for_operation(operation):
    return Gateway.query.filter_by(
        code=operation.gateway_code,
        is_active=True,
    ).first()


def _positive_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a positive integer.") from error
    if normalized <= 0:
        raise ValueError(f"{label} must be a positive integer.")
    return normalized
