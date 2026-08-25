from dataclasses import dataclass
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

    @property
    def changed(self):
        return bool(
            self.finalized_summary_count
            or self.purged_detail_count
            or self.purged_expired_summary_count
        )


def finalize_attendance_summaries(operation, user=None, finalized_at=None):
    """Idempotently recalculate retained Operation/Department totals."""
    operation_id = _positive_int(
        getattr(operation, "id", operation),
        "Sort Date Operation",
    )
    locked_operation = (
        SortDateOperation.query.filter_by(id=operation_id)
        .with_for_update()
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
    return AttendanceSummaryFinalizationResult(
        sort_date_operation_id=locked_operation.id,
        attendance_date=locked_operation.sort_date,
        staffing_sort_unit_id=calculated["staffing_sort"].id,
        summary_count=len(expected_keys),
    )


def process_attendance_rollover(current_operation, user=None, *, now_local=None):
    """Finalize then purge one prior Night detail set in one caller transaction."""
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

    prior_operation = (
        SortDateOperation.query.filter(
            SortDateOperation.gateway_code == current_operation.gateway_code,
            SortDateOperation.sort_name == current_operation.sort_name,
            SortDateOperation.sort_date < current_operation.sort_date,
        )
        .order_by(SortDateOperation.sort_date.desc(), SortDateOperation.id.desc())
        .with_for_update()
        .first()
    )
    if not prior_operation:
        return AttendanceRolloverResult(
            current_operation.id,
            None,
            status="no_prior_operation",
        )

    linked_detail_count = StaffingDailyAttendance.query.filter_by(
        sort_date_operation_id=prior_operation.id
    ).count()
    legacy_detail_count = StaffingDailyAttendance.query.filter(
        StaffingDailyAttendance.attendance_date == prior_operation.sort_date,
        StaffingDailyAttendance.sort_date_operation_id.is_(None),
    ).count()
    existing_summary_count = StaffingAttendanceSummary.query.filter_by(
        sort_date_operation_id=prior_operation.id
    ).count()
    if existing_summary_count and not linked_detail_count and not legacy_detail_count:
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
        StaffingDailyAttendance.attendance_date == prior_operation.sort_date,
        StaffingDailyAttendance.sort_unit_id == finalization.staffing_sort_unit_id,
        or_(
            StaffingDailyAttendance.sort_date_operation_id == prior_operation.id,
            StaffingDailyAttendance.sort_date_operation_id.is_(None),
        ),
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


def maintain_current_attendance_rollover(user=None, *, now_local=None):
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
            == staffing_service.ATTENDANCE_OPERATION_SORT_NAME
            and operation_is_active_at(operation, local_now, gateway)
        ),
        None,
    )
    result = process_attendance_rollover(
        current_operation,
        user,
        now_local=local_now,
    )
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
