from dataclasses import dataclass
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    StaffingOperationSchedule,
    StaffingUnit,
)
from app.models.staffing_operation_schedule import (
    STAFFING_OPERATION_SCHEDULE_WEEKDAYS,
)
from app.services.access_control import user_can_access_app
from app.services.gateway_matrix import SORT_VALUES


EXPLICIT_OPERATION_SCHEDULE_SOURCE = "explicit"
GATEWAY_SORT_MATRIX_SOURCE = "gateway_sort_matrix"
UNCONFIGURED_OPERATION_SCHEDULE_SOURCE = "unconfigured"


@dataclass(frozen=True)
class NormalOperationalDateResolution:
    operation_id: int
    target_date: date
    is_operational: bool
    source: str
    schedule_id: int | None = None


@dataclass(frozen=True)
class NormalOperationalDaysResult:
    operation_id: int
    start_date: date
    end_date: date
    resolutions: tuple[NormalOperationalDateResolution, ...]

    @property
    def operational_dates(self):
        return tuple(
            resolution.target_date
            for resolution in self.resolutions
            if resolution.is_operational
        )


def can_manage_operation_schedules(user):
    return user_can_access_app(
        user,
        "neostaffing",
        minimum_role="master",
    )


def create_operation_schedule(
    operation_id,
    effective_from,
    effective_through,
    active_weekdays,
    user,
):
    """Create one immutable historical schedule period for a Staffing Operation."""
    if not can_manage_operation_schedules(user):
        raise ValueError("Operation Schedule changes require Master access.")

    normalized_operation_id = _positive_int(operation_id, "Operation")
    start_date = _date_value(effective_from, "Effective From")
    end_date = (
        _date_value(effective_through, "Effective Through")
        if effective_through not in (None, "")
        else None
    )
    if end_date is not None and end_date < start_date:
        raise ValueError("Effective Through cannot be before Effective From.")
    weekdays = _weekday_set(active_weekdays)

    operation = (
        StaffingUnit.query.filter_by(id=normalized_operation_id)
        .with_for_update()
        .first()
    )
    if not operation or operation.unit_type != "operation":
        raise ValueError("Select a valid NeoStaffing Operation.")

    overlap_query = StaffingOperationSchedule.query.filter(
        StaffingOperationSchedule.operation_unit_id == operation.id,
        or_(
            StaffingOperationSchedule.effective_through.is_(None),
            StaffingOperationSchedule.effective_through >= start_date,
        ),
    )
    if end_date is not None:
        overlap_query = overlap_query.filter(
            StaffingOperationSchedule.effective_from <= end_date
        )
    if overlap_query.with_for_update().first():
        raise ValueError(
            "Operation Schedule dates overlap an existing effective schedule."
        )

    schedule = StaffingOperationSchedule(
        operation_unit_id=operation.id,
        effective_from=start_date,
        effective_through=end_date,
        created_by_user_id=getattr(user, "id", None),
        updated_by_user_id=getattr(user, "id", None),
    )
    for weekday in STAFFING_OPERATION_SCHEDULE_WEEKDAYS:
        setattr(schedule, weekday, weekday in weekdays)
    db.session.add(schedule)
    db.session.flush()
    return schedule


def normal_operational_days_for_operation(
    operation_id,
    start_date,
    end_date=None,
):
    """Resolve normal recurring operational dates with bounded collection queries."""
    normalized_operation_id = _positive_int(operation_id, "Operation")
    normalized_start = _date_value(start_date, "Start Date")
    normalized_end = (
        _date_value(end_date, "End Date")
        if end_date not in (None, "")
        else normalized_start
    )
    if normalized_end < normalized_start:
        raise ValueError("End Date cannot be before Start Date.")

    operation = (
        StaffingUnit.query.options(joinedload(StaffingUnit.parent))
        .filter_by(id=normalized_operation_id)
        .first()
    )
    if not operation or operation.unit_type != "operation":
        raise ValueError("Select a valid NeoStaffing Operation.")

    schedules = (
        StaffingOperationSchedule.query.filter(
            StaffingOperationSchedule.operation_unit_id == operation.id,
            StaffingOperationSchedule.effective_from <= normalized_end,
            or_(
                StaffingOperationSchedule.effective_through.is_(None),
                StaffingOperationSchedule.effective_through >= normalized_start,
            ),
        )
        .order_by(
            StaffingOperationSchedule.effective_from,
            StaffingOperationSchedule.id,
        )
        .all()
    )
    _assert_non_overlapping_schedules(schedules)

    fallback_weekdays = None
    fallback_source = None
    resolutions = []
    target_date = normalized_start
    while target_date <= normalized_end:
        schedule = _schedule_for_date(schedules, target_date)
        if schedule is not None:
            weekday = target_date.strftime("%A").casefold()
            resolutions.append(
                NormalOperationalDateResolution(
                    operation_id=operation.id,
                    target_date=target_date,
                    is_operational=weekday in schedule.active_weekdays,
                    source=EXPLICIT_OPERATION_SCHEDULE_SOURCE,
                    schedule_id=schedule.id,
                )
            )
        else:
            if fallback_weekdays is None:
                fallback_weekdays, fallback_source = _fallback_weekdays(operation)
            weekday = target_date.strftime("%A").casefold()
            resolutions.append(
                NormalOperationalDateResolution(
                    operation_id=operation.id,
                    target_date=target_date,
                    is_operational=weekday in fallback_weekdays,
                    source=fallback_source,
                )
            )
        target_date += timedelta(days=1)

    return NormalOperationalDaysResult(
        operation_id=operation.id,
        start_date=normalized_start,
        end_date=normalized_end,
        resolutions=tuple(resolutions),
    )


def normal_operational_date_resolution(operation_id, target_date):
    return normal_operational_days_for_operation(
        operation_id,
        target_date,
    ).resolutions[0]


def is_normal_operational_date(operation_id, target_date):
    return normal_operational_date_resolution(
        operation_id,
        target_date,
    ).is_operational


def _fallback_weekdays(operation):
    staffing_sort = operation.parent
    sort_name = str(getattr(staffing_sort, "name", "") or "").strip().casefold()
    if not staffing_sort or staffing_sort.unit_type != "sort" or sort_name not in SORT_VALUES:
        return frozenset(), UNCONFIGURED_OPERATION_SCHEDULE_SOURCE

    gateway_code = str(
        current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD") or "RFD"
    ).strip().upper()
    gateway = Gateway.query.filter_by(code=gateway_code, is_active=True).first()
    if not gateway:
        return frozenset(), UNCONFIGURED_OPERATION_SCHEDULE_SOURCE

    weekdays = frozenset(
        day_of_week
        for (day_of_week,) in (
            db.session.query(GatewaySortMatrix.day_of_week)
            .filter_by(
                gateway_id=gateway.id,
                sort_name=sort_name,
                is_active=True,
            )
            .all()
        )
        if day_of_week in STAFFING_OPERATION_SCHEDULE_WEEKDAYS
    )
    return weekdays, GATEWAY_SORT_MATRIX_SOURCE


def _schedule_for_date(schedules, target_date):
    matches = [
        schedule
        for schedule in schedules
        if schedule.effective_from <= target_date
        and (
            schedule.effective_through is None
            or target_date <= schedule.effective_through
        )
    ]
    if len(matches) > 1:
        raise RuntimeError("Overlapping Operation Schedule data requires repair.")
    return matches[0] if matches else None


def _assert_non_overlapping_schedules(schedules):
    previous = None
    for schedule in schedules:
        if previous and (
            previous.effective_through is None
            or schedule.effective_from <= previous.effective_through
        ):
            raise RuntimeError("Overlapping Operation Schedule data requires repair.")
        previous = schedule


def _weekday_set(active_weekdays):
    if isinstance(active_weekdays, str):
        values = active_weekdays.split(",")
    else:
        values = active_weekdays or ()
    normalized = {
        str(value or "").strip().casefold()
        for value in values
        if str(value or "").strip()
    }
    invalid = normalized - set(STAFFING_OPERATION_SCHEDULE_WEEKDAYS)
    if invalid:
        raise ValueError("Choose valid recurring weekdays.")
    return normalized


def _date_value(value, label):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "").strip())
    except ValueError as error:
        raise ValueError(f"{label} must be a valid date.") from error


def _positive_int(value, label):
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Select a valid {label}.") from error
    if normalized <= 0:
        raise ValueError(f"Select a valid {label}.")
    return normalized
