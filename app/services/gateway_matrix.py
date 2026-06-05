from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app

from app.extensions import db
from app.models import GatewaySortMatrix, SortDateOperation
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
    ("twilight", "Twilight"),
    ("night", "Night"),
    ("sunrise", "Sunrise"),
    ("day", "Day"),
)

DAY_VALUES = {value for value, _label in DAY_OPTIONS}
SORT_VALUES = {value for value, _label in SORT_OPTIONS}


def gateway_timezone(gateway=None):
    return current_app.config.get("DEFAULT_GATEWAY_TIMEZONE", "America/Chicago")


def current_gateway_local_date(gateway=None):
    timezone = gateway_timezone(gateway)
    try:
        return datetime.now(ZoneInfo(timezone)).date()
    except ZoneInfoNotFoundError:
        return _fallback_gateway_now(timezone).date()


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
):
    sort_date = sort_date or current_gateway_local_date(gateway)
    created_operations = []
    existing_operations = []
    errors = []

    for sort_name in active_sorts_for_gateway_date(gateway, sort_date):
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


def operations_for_gateway_date(gateway, sort_date):
    return (
        SortDateOperation.query.filter_by(
            gateway_code=gateway.code,
            sort_date=sort_date,
        )
        .order_by(SortDateOperation.sort_name.asc())
        .all()
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
