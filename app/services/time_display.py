from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context


DEFAULT_TIMEZONE = "America/Chicago"


def configured_timezone_name(timezone_name=None):
    if timezone_name:
        return timezone_name
    if has_app_context():
        return current_app.config.get("DEFAULT_GATEWAY_TIMEZONE", DEFAULT_TIMEZONE)
    return DEFAULT_TIMEZONE


def utc_to_local_naive(value, timezone_name=None):
    if not value:
        return None

    timezone_name = configured_timezone_name(timezone_name)
    utc_value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == DEFAULT_TIMEZONE:
            return _fallback_chicago_datetime(utc_value.replace(tzinfo=None))
        return value.replace(tzinfo=None)

    return utc_value.astimezone(local_timezone).replace(tzinfo=None)


def format_local_hhmm(value, timezone_name=None):
    local_value = utc_to_local_naive(value, timezone_name)
    return local_value.strftime("%H:%M") if local_value else ""


def format_local_ymd_hm(value, timezone_name=None):
    local_value = utc_to_local_naive(value, timezone_name)
    return local_value.strftime("%Y-%m-%d %H:%M") if local_value else ""


def _fallback_chicago_datetime(utc_datetime):
    standard_local = utc_datetime - timedelta(hours=6)
    if _is_us_central_daylight_time(standard_local):
        return utc_datetime - timedelta(hours=5)
    return standard_local


def _is_us_central_daylight_time(local_datetime):
    year = local_datetime.year
    dst_start = _second_sunday(year, 3).replace(hour=2)
    dst_end = _first_sunday(year, 11).replace(hour=2)
    return dst_start <= local_datetime < dst_end


def _first_sunday(year, month):
    current = datetime(year, month, 1)
    while current.weekday() != 6:
        current += timedelta(days=1)
    return current


def _second_sunday(year, month):
    return _first_sunday(year, month) + timedelta(days=7)
