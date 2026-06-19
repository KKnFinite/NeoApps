from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from app.extensions import db
from app.models import GatewaySortMatrix, SortDateOperation, SortTimelineSettings
from app.services.sort_date_operations import generate_sort_date_operation_from_master


DAY_OPTIONS = (
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
)

SORT_OPTIONS = (
    ("sunrise", "Sunrise"),
    ("day", "Day"),
    ("twilight", "Twilight"),
    ("night", "Night"),
)

DAY_VALUES = {value for value, _label in DAY_OPTIONS}
SORT_VALUES = {value for value, _label in SORT_OPTIONS}
SORT_ORDER = {value: index for index, (value, _label) in enumerate(SORT_OPTIONS)}


def gateway_timezone(gateway=None):
    return current_app.config.get("DEFAULT_GATEWAY_TIMEZONE", "America/Chicago")


def current_gateway_local_datetime(gateway=None, now=None):
    if now is None:
        now = current_app.config.get("CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE")
    timezone = gateway_timezone(gateway)
    if now is not None:
        if now.tzinfo:
            try:
                return now.astimezone(ZoneInfo(timezone)).replace(tzinfo=None)
            except ZoneInfoNotFoundError:
                return now.replace(tzinfo=None)
        return now
    try:
        return datetime.now(ZoneInfo(timezone)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return _fallback_gateway_now(timezone)


def current_gateway_local_date(gateway=None, now=None):
    return current_gateway_local_datetime(gateway, now=now).date()


def matrix_entries_for_gateway(gateway):
    entries = GatewaySortMatrix.query.filter_by(gateway_id=gateway.id).all()
    return {
        (entry.day_of_week, entry.sort_name): entry
        for entry in entries
    }


def matrix_state_for_gateway(gateway):
    entries = matrix_entries_for_gateway(gateway)
    return {
        day: {
            sort_name: entries.get((day, sort_name)).is_active
            if entries.get((day, sort_name))
            else False
            for sort_name, _label in SORT_OPTIONS
        }
        for day, _label in DAY_OPTIONS
    }


def save_gateway_matrix(gateway, active_cells):
    existing_entries = matrix_entries_for_gateway(gateway)
    active_cells = {
        (_normalize_day(day), _normalize_sort(sort_name))
        for day, sort_name in active_cells
        if _normalize_day(day) in DAY_VALUES and _normalize_sort(sort_name) in SORT_VALUES
    }

    for day, _day_label in DAY_OPTIONS:
        for sort_name, _sort_label in SORT_OPTIONS:
            key = (day, sort_name)
            entry = existing_entries.get(key)
            if not entry:
                entry = GatewaySortMatrix(
                    gateway_id=gateway.id,
                    gateway_code=gateway.code,
                    day_of_week=day,
                    sort_name=sort_name,
                )
                db.session.add(entry)

            entry.gateway_code = gateway.code
            entry.is_active = key in active_cells

    db.session.commit()


def active_sorts_for_gateway_date(gateway, sort_date):
    day = sort_date.strftime("%A").lower()
    entries = (
        GatewaySortMatrix.query.filter_by(
            gateway_id=gateway.id,
            day_of_week=day,
            is_active=True,
        )
        .order_by(GatewaySortMatrix.sort_name.asc())
        .all()
    )
    active_sort_names = {entry.sort_name for entry in entries}
    return [
        sort_name
        for sort_name, _label in SORT_OPTIONS
        if sort_name in active_sort_names
    ]


def ensure_sort_operations_for_gateway_date(
    gateway,
    sort_date=None,
    generated_by_user_id=None,
    now=None,
):
    local_now = current_gateway_local_datetime(gateway, now=now)
    sort_date = sort_date or local_now.date()
    active_previous_sort_names = previous_day_active_sort_names(gateway, sort_date, local_now)
    created_operations = []
    existing_operations = []
    errors = []

    for sort_name in active_sorts_for_gateway_date(gateway, sort_date):
        if sort_name in active_previous_sort_names:
            continue
        existing_operation = SortDateOperation.query.filter_by(
            sort_date=sort_date,
            gateway_code=gateway.code,
            sort_name=sort_name,
        ).first()
        if existing_operation:
            existing_operations.append(existing_operation)
            continue

        try:
            operation = generate_sort_date_operation_from_master(
                sort_date=sort_date,
                gateway_code=gateway.code,
                sort_name=sort_name,
                generated_by_user_id=generated_by_user_id,
            )
            created_operations.append(operation)
        except ValueError as error:
            db.session.rollback()
            errors.append(f"{sort_name}: {error}")

    return {
        "sort_date": sort_date,
        "created": created_operations,
        "existing": existing_operations,
        "errors": errors,
    }


def current_operations_for_gateway(gateway, now=None):
    local_now = current_gateway_local_datetime(gateway, now=now)
    current_date = local_now.date()
    previous_date = current_date - timedelta(days=1)
    operations = (
        SortDateOperation.query.filter(
            SortDateOperation.gateway_code == gateway.code,
            SortDateOperation.archived_at_utc.is_(None),
            SortDateOperation.sort_date.in_((current_date, previous_date)),
        )
        .all()
    )
    visible_operations = [
        operation
        for operation in operations
        if operation.sort_date == current_date
        or operation_is_active_at(operation, local_now, gateway)
    ]
    return sorted(
        visible_operations,
        key=lambda operation: (
            0 if operation_is_active_at(operation, local_now, gateway) else 1,
            operation.sort_date,
            SORT_ORDER.get(operation.sort_name, len(SORT_ORDER)),
        ),
    )


def previous_day_active_sort_names(gateway, sort_date, local_now):
    previous_date = sort_date - timedelta(days=1)
    previous_operations = (
        SortDateOperation.query.filter_by(
            gateway_code=gateway.code,
            sort_date=previous_date,
        )
        .filter(SortDateOperation.archived_at_utc.is_(None))
        .all()
    )
    return {
        operation.sort_name
        for operation in previous_operations
        if operation_is_active_at(operation, local_now, gateway)
    }


def operation_is_active_at(operation, local_now, gateway=None):
    if not operation or not local_now:
        return False
    start_local, end_local = sort_lookup_window_for_operation(operation, gateway)
    return bool(start_local and end_local and start_local <= local_now < end_local)


def sort_lookup_window_for_operation(operation, gateway=None):
    sort_setting = _sort_timeline_sort_setting(gateway or operation.gateway, operation.sort_name)
    start_time = sort_setting.sort_window_start_local if sort_setting else None
    end_time = sort_setting.sort_window_end_local if sort_setting else None
    start_time = start_time or time(0, 0)
    end_time = end_time or time(23, 59)
    start_local = datetime.combine(operation.sort_date, start_time)
    end_local = datetime.combine(operation.sort_date, end_time)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local, end_local


def _sort_timeline_sort_setting(gateway, sort_name):
    if not gateway:
        return None
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        return None
    sort_name = str(sort_name or "").strip().lower()
    return next(
        (
            sort_setting
            for sort_setting in settings.sort_settings
            if sort_setting.sort_name == sort_name
        ),
        None,
    )


def operations_for_gateway_date(gateway, sort_date):
    operations = (
        SortDateOperation.query.filter_by(
            gateway_code=gateway.code,
            sort_date=sort_date,
        )
        .all()
    )
    return sorted(
        operations,
        key=lambda operation: SORT_ORDER.get(operation.sort_name, len(SORT_ORDER)),
    )


def _normalize_day(day):
    return str(day or "").strip().lower()


def _normalize_sort(sort_name):
    return str(sort_name or "").strip().lower()


def _fallback_gateway_now(timezone):
    now_utc = datetime.utcnow()
    if timezone != "America/Chicago":
        return now_utc

    standard_local = now_utc - timedelta(hours=6)
    if _is_us_central_daylight_time(standard_local):
        return now_utc - timedelta(hours=5)
    return standard_local


def _is_us_central_daylight_time(local_datetime):
    year = local_datetime.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= local_datetime < dst_end


def _nth_weekday_of_month(year, month, weekday, occurrence):
    candidate = datetime(year, month, 1)
    days_until_weekday = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=days_until_weekday + (occurrence - 1) * 7)
