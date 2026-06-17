import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.extensions import db
from app.models import (
    SortTimelineMonthlyAdjustment,
    SortTimelineSettings,
    SortTimelineSortSetting,
    SortTimelineSpecialPollTime,
    SortTimelineUsageCounter,
)
from app.services.gateway_matrix import DAY_OPTIONS, SORT_OPTIONS, gateway_timezone


DEFAULT_MONTHLY_API_UNITS = 600
DEFAULT_UNITS_PER_POLL = 2
DEFAULT_OPERATING_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
DAY_VALUES = {day for day, _label in DAY_OPTIONS}
SORT_VALUES = {sort_name for sort_name, _label in SORT_OPTIONS}


def ensure_sort_timeline_settings(gateway):
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        settings = SortTimelineSettings(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            monthly_api_units=DEFAULT_MONTHLY_API_UNITS,
            units_per_poll=DEFAULT_UNITS_PER_POLL,
            operating_weekdays=_weekday_csv(DEFAULT_OPERATING_WEEKDAYS),
        )
        db.session.add(settings)
        db.session.flush()
    else:
        settings.gateway_code = gateway.code
        if settings.monthly_api_units is None:
            settings.monthly_api_units = DEFAULT_MONTHLY_API_UNITS
        if not settings.units_per_poll:
            settings.units_per_poll = DEFAULT_UNITS_PER_POLL
        if not settings.operating_weekdays:
            settings.operating_weekdays = _weekday_csv(DEFAULT_OPERATING_WEEKDAYS)

    existing_sorts = {
        sort_setting.sort_name: sort_setting
        for sort_setting in settings.sort_settings
    }
    for sort_name, _sort_label in SORT_OPTIONS:
        sort_setting = existing_sorts.get(sort_name)
        if not sort_setting:
            sort_setting = SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                sort_name=sort_name,
            )
            db.session.add(sort_setting)
            continue

        sort_setting.gateway_id = gateway.id
        sort_setting.gateway_code = gateway.code

    db.session.flush()
    return settings


def sort_timeline_context(gateway, month_key=None, now=None):
    settings = ensure_sort_timeline_settings(gateway)
    month_key = normalize_month_key(month_key) or month_key_for_gateway_datetime(now, gateway)
    selected_month = month_start_from_key(month_key)
    adjustments = monthly_adjustments_for_gateway(gateway, month_key)
    previews = sort_timeline_previews(settings, adjustments, selected_month, gateway, now=now)
    return {
        "settings": settings,
        "month_key": month_key,
        "selected_month": selected_month,
        "operating_weekdays": operating_weekday_set(settings.operating_weekdays),
        "adjustments": adjustments,
        "previews": previews,
        "preview_by_sort": {
            preview["sort_name"]: preview
            for preview in previews
        },
        "summary": aggregate_preview(previews),
        "usage_count": usage_count_for_month(gateway, month_key),
    }


def save_sort_timeline_from_form(gateway, form):
    settings = ensure_sort_timeline_settings(gateway)
    month_key = normalize_month_key(form.get("month_key")) or month_key_for_gateway_datetime(
        None,
        gateway,
    )

    settings.monthly_api_units = _nonnegative_int(
        form.get("monthly_api_units", form.get("monthly_api_limit")),
        default=DEFAULT_MONTHLY_API_UNITS,
    )
    settings.units_per_poll = _positive_int(
        form.get("units_per_poll"),
        default=DEFAULT_UNITS_PER_POLL,
    )
    settings.provider_enabled = form.get("provider_enabled") == "1"
    settings.provider_name = _clean_text(form.get("provider_name"), max_length=120)
    settings.api_key_env_var_name = _clean_env_var_name(form.get("api_key_env_var_name"))
    settings.operating_weekdays = _weekday_csv(form.getlist("operating_weekdays"))

    _replace_monthly_adjustments(
        gateway,
        month_key,
        form.get("added_operating_days", ""),
        form.get("removed_operating_days", ""),
    )
    _save_sort_settings(settings, gateway, form)
    db.session.flush()
    return settings, month_key


def sort_timeline_previews(settings, adjustments, selected_month, gateway, now=None):
    operating_days = operating_days_for_month(
        selected_month,
        operating_weekday_set(settings.operating_weekdays),
        adjustments["added_dates"],
        adjustments["removed_dates"],
    )
    monthly_poll_count = monthly_poll_limit(settings.monthly_api_units, settings.units_per_poll)
    daily_cap = daily_poll_cap(monthly_poll_count, operating_days)
    previews = []
    sort_settings = {
        sort_setting.sort_name: sort_setting
        for sort_setting in settings.sort_settings
    }

    for sort_name, sort_label in SORT_OPTIONS:
        sort_setting = sort_settings.get(sort_name)
        special_count = len(sort_setting.special_poll_times) if sort_setting else 0
        auto_count = auto_interval_poll_count(daily_cap, special_count)
        scheduled_times = scheduled_poll_times(sort_setting, auto_count)
        previews.append(
            {
                "sort_name": sort_name,
                "sort_label": sort_label,
                "sort_setting": sort_setting,
                "operating_days": operating_days,
                "monthly_api_units": settings.monthly_api_units,
                "units_per_poll": settings.units_per_poll,
                "monthly_poll_limit": monthly_poll_count,
                "daily_poll_cap": daily_cap,
                "special_poll_count": special_count,
                "auto_interval_poll_count": auto_count,
                "total_scheduled_polls": special_count + auto_count,
                "next_poll_time": next_poll_time(scheduled_times, selected_month, gateway, now=now),
            }
        )

    return previews


def aggregate_preview(previews):
    if not previews:
        return {
            "operating_days": 0,
            "monthly_api_units": 0,
            "units_per_poll": DEFAULT_UNITS_PER_POLL,
            "monthly_poll_limit": 0,
            "daily_poll_cap": 0,
            "special_poll_count": 0,
            "auto_interval_poll_count": 0,
            "total_scheduled_polls": 0,
            "next_poll_time": None,
        }

    next_times = [preview["next_poll_time"] for preview in previews if preview["next_poll_time"]]
    return {
        "operating_days": previews[0]["operating_days"],
        "monthly_api_units": previews[0]["monthly_api_units"],
        "units_per_poll": previews[0]["units_per_poll"],
        "monthly_poll_limit": previews[0]["monthly_poll_limit"],
        "daily_poll_cap": previews[0]["daily_poll_cap"],
        "special_poll_count": sum(preview["special_poll_count"] for preview in previews),
        "auto_interval_poll_count": sum(preview["auto_interval_poll_count"] for preview in previews),
        "total_scheduled_polls": sum(preview["total_scheduled_polls"] for preview in previews),
        "next_poll_time": min(next_times) if next_times else None,
    }


def monthly_adjustments_for_gateway(gateway, month_key):
    rows = (
        SortTimelineMonthlyAdjustment.query.filter_by(
            gateway_id=gateway.id,
            month_key=month_key,
        )
        .order_by(
            SortTimelineMonthlyAdjustment.local_date.asc(),
            SortTimelineMonthlyAdjustment.adjustment_type.asc(),
        )
        .all()
    )
    added_dates = [
        adjustment.local_date
        for adjustment in rows
        if adjustment.adjustment_type == "add"
    ]
    removed_dates = [
        adjustment.local_date
        for adjustment in rows
        if adjustment.adjustment_type == "remove"
    ]
    return {
        "added_dates": added_dates,
        "removed_dates": removed_dates,
        "added_text": "\n".join(day.isoformat() for day in added_dates),
        "removed_text": "\n".join(day.isoformat() for day in removed_dates),
    }


def operating_days_for_month(month_start, weekday_values, added_dates=(), removed_dates=()):
    weekday_values = set(weekday_values or ())
    _, day_count = calendar.monthrange(month_start.year, month_start.month)
    enabled_weekday_count = 0
    for day_number in range(1, day_count + 1):
        candidate = date(month_start.year, month_start.month, day_number)
        if candidate.strftime("%A").lower() in weekday_values:
            enabled_weekday_count += 1

    added_count = len(set(added_dates or ()))
    removed_count = len(set(removed_dates or ()))
    return max(0, enabled_weekday_count + added_count - removed_count)


def monthly_poll_limit(monthly_api_units, units_per_poll):
    units_per_poll = max(1, int(units_per_poll or DEFAULT_UNITS_PER_POLL))
    return max(0, int(monthly_api_units or 0) // units_per_poll)


def daily_poll_cap(monthly_poll_count, operating_days):
    if operating_days <= 0:
        return 0
    return max(0, int(monthly_poll_count or 0) // operating_days)


def auto_interval_poll_count(daily_cap, special_poll_count):
    return max(0, int(daily_cap or 0) - int(special_poll_count or 0))


def scheduled_poll_times(sort_setting, auto_count):
    if not sort_setting:
        return []

    special_times = [
        special.poll_time_local
        for special in sort_setting.special_poll_times
    ]
    auto_times = evenly_spread_times(
        sort_setting.polling_start_local,
        sort_setting.polling_end_local,
        auto_count,
    )
    return sorted(set(special_times + auto_times))


def evenly_spread_times(start_time, end_time, count):
    if not start_time or not end_time or count <= 0:
        return []

    start_minutes = start_time.hour * 60 + start_time.minute
    end_minutes = end_time.hour * 60 + end_time.minute
    if end_minutes < start_minutes:
        end_minutes += 24 * 60
    if end_minutes == start_minutes:
        return [start_time] if count == 1 else []

    if count == 1:
        return [start_time]

    step = (end_minutes - start_minutes) / (count - 1)
    times = []
    for index in range(count):
        total_minutes = round(start_minutes + (step * index)) % (24 * 60)
        times.append(time(total_minutes // 60, total_minutes % 60))
    return times


def next_poll_time(scheduled_times, selected_month, gateway, now=None):
    if not scheduled_times:
        return None

    local_now = gateway_local_datetime(gateway, now)
    if selected_month.year != local_now.year or selected_month.month != local_now.month:
        return scheduled_times[0]

    for poll_time in sorted(scheduled_times):
        if poll_time > local_now.time().replace(second=0, microsecond=0):
            return poll_time
    return None


def usage_count_for_month(gateway, month_key):
    counter = SortTimelineUsageCounter.query.filter_by(
        gateway_id=gateway.id,
        month_key=month_key,
    ).first()
    return counter.attempted_call_count if counter else 0


def record_sort_timeline_api_attempt(gateway, attempted_at_utc=None):
    month_key = month_key_for_gateway_datetime(attempted_at_utc, gateway)
    counter = SortTimelineUsageCounter.query.filter_by(
        gateway_id=gateway.id,
        month_key=month_key,
    ).first()
    if not counter:
        counter = SortTimelineUsageCounter(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            month_key=month_key,
            attempted_call_count=0,
        )
        db.session.add(counter)

    counter.gateway_code = gateway.code
    counter.attempted_call_count += 1
    db.session.flush()
    return counter


def month_key_for_gateway_datetime(moment=None, gateway=None):
    return gateway_local_datetime(gateway, moment).strftime("%Y-%m")


def gateway_local_datetime(gateway=None, moment=None):
    tz_name = gateway_timezone(gateway)
    try:
        gateway_tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        if tz_name == "America/Chicago":
            return _fallback_central_datetime(moment)
        gateway_tz = timezone.utc

    if moment is None:
        return datetime.now(gateway_tz)

    if isinstance(moment, date) and not isinstance(moment, datetime):
        return datetime.combine(moment, time.min, tzinfo=gateway_tz)

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(gateway_tz)


def _fallback_central_datetime(moment=None):
    if moment is None:
        utc_datetime = datetime.now(timezone.utc)
    elif isinstance(moment, date) and not isinstance(moment, datetime):
        return datetime.combine(moment, time.min)
    else:
        utc_datetime = moment
        if utc_datetime.tzinfo is None:
            utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
        utc_datetime = utc_datetime.astimezone(timezone.utc)

    standard_local = utc_datetime.replace(tzinfo=None) - timedelta(hours=6)
    if _is_us_central_daylight_time(standard_local):
        return utc_datetime.replace(tzinfo=None) - timedelta(hours=5)
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


def normalize_month_key(value):
    value = str(value or "").strip()
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m")


def month_start_from_key(month_key):
    parsed = datetime.strptime(month_key, "%Y-%m")
    return date(parsed.year, parsed.month, 1)


def operating_weekday_set(raw_value):
    if isinstance(raw_value, str):
        values = raw_value.split(",")
    else:
        values = raw_value or ()

    normalized = [
        day.strip().lower()
        for day in values
        if day.strip().lower() in DAY_VALUES
    ]
    return set(normalized) or set(DEFAULT_OPERATING_WEEKDAYS)


def time_value(value):
    if isinstance(value, time):
        return value
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def format_time(value):
    if not value:
        return "--"
    return value.strftime("%H:%M")


def _replace_monthly_adjustments(gateway, month_key, added_text, removed_text):
    SortTimelineMonthlyAdjustment.query.filter_by(
        gateway_id=gateway.id,
        month_key=month_key,
    ).delete(synchronize_session=False)

    selected_month = month_start_from_key(month_key)
    for adjustment_type, raw_text in (("add", added_text), ("remove", removed_text)):
        for local_date in _parse_adjustment_dates(raw_text, selected_month):
            db.session.add(
                SortTimelineMonthlyAdjustment(
                    gateway_id=gateway.id,
                    gateway_code=gateway.code,
                    month_key=month_key,
                    local_date=local_date,
                    adjustment_type=adjustment_type,
                )
            )


def _parse_adjustment_dates(raw_text, selected_month):
    dates = set()
    for token in str(raw_text or "").replace(",", "\n").splitlines():
        token = token.strip()
        if not token:
            continue
        try:
            parsed = datetime.strptime(token, "%Y-%m-%d").date()
        except ValueError:
            continue
        if parsed.year == selected_month.year and parsed.month == selected_month.month:
            dates.add(parsed)
    return sorted(dates)


def _save_sort_settings(settings, gateway, form):
    sort_settings = {
        sort_setting.sort_name: sort_setting
        for sort_setting in settings.sort_settings
    }
    for sort_name, _sort_label in SORT_OPTIONS:
        sort_setting = sort_settings.get(sort_name)
        if not sort_setting:
            sort_setting = SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                sort_name=sort_name,
            )
            db.session.add(sort_setting)
            db.session.flush()

        sort_setting.gateway_id = gateway.id
        sort_setting.gateway_code = gateway.code
        sort_setting.sort_window_start_local = time_value(form.get(f"{sort_name}_sort_start"))
        sort_setting.sort_window_end_local = time_value(form.get(f"{sort_name}_sort_end"))
        sort_setting.ops_window_start_local = time_value(form.get(f"{sort_name}_ops_start"))
        sort_setting.ops_window_end_local = time_value(form.get(f"{sort_name}_ops_end"))
        sort_setting.polling_start_local = time_value(form.get(f"{sort_name}_polling_start"))
        sort_setting.polling_end_local = time_value(form.get(f"{sort_name}_polling_end"))

        _replace_special_poll_times(sort_setting, gateway, form.getlist(f"{sort_name}_special_poll_time"), form.getlist(f"{sort_name}_delete_special_poll_time"))


def _replace_special_poll_times(sort_setting, gateway, raw_times, raw_deletions):
    deleted_times = {time_value(value) for value in raw_deletions}
    deleted_times.discard(None)
    poll_times = {
        time_value(value)
        for value in raw_times
        if time_value(value) and time_value(value) not in deleted_times
    }

    SortTimelineSpecialPollTime.query.filter_by(
        sort_setting_id=sort_setting.id,
    ).delete(synchronize_session=False)
    for poll_time in sorted(poll_times):
        db.session.add(
            SortTimelineSpecialPollTime(
                sort_setting_id=sort_setting.id,
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                sort_name=sort_setting.sort_name,
                poll_time_local=poll_time,
            )
        )


def _weekday_csv(values):
    weekdays = [
        day
        for day, _label in DAY_OPTIONS
        if str(day).lower() in {str(value).strip().lower() for value in values or ()}
    ]
    if not weekdays:
        weekdays = list(DEFAULT_OPERATING_WEEKDAYS)
    return ",".join(weekdays)


def _clean_text(value, max_length=120):
    return str(value or "").strip()[:max_length]


def _clean_env_var_name(value):
    value = _clean_text(value, max_length=120).upper()
    return "".join(character for character in value if character.isalnum() or character == "_")


def _nonnegative_int(value, default=0):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, number)


def _positive_int(value, default=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)
