import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.extensions import db
from app.models import (
    GatewaySortMatrix,
    SortTimelineApiParticipation,
    SortTimelineMonthVariance,
    SortTimelineSettings,
    SortTimelineSortSetting,
    SortTimelineSpecialPollTime,
    SortTimelineUsageCounter,
)
from app.services.gateway_matrix import DAY_OPTIONS, SORT_OPTIONS, gateway_timezone


DEFAULT_MONTHLY_API_UNITS = 600
DEFAULT_UNITS_PER_POLL = 2
DAY_VALUES = {day for day, _label in DAY_OPTIONS}
SORT_VALUES = {sort_name for sort_name, _label in SORT_OPTIONS}
MONTH_OPTIONS = tuple((month_number, calendar.month_name[month_number]) for month_number in range(1, 13))


def ensure_sort_timeline_settings(gateway):
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        settings = SortTimelineSettings(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            monthly_api_units=DEFAULT_MONTHLY_API_UNITS,
            units_per_poll=DEFAULT_UNITS_PER_POLL,
        )
        db.session.add(settings)
        db.session.flush()
    else:
        settings.gateway_code = gateway.code
        if settings.monthly_api_units is None:
            settings.monthly_api_units = DEFAULT_MONTHLY_API_UNITS
        if not settings.units_per_poll:
            settings.units_per_poll = DEFAULT_UNITS_PER_POLL

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
    current_month_key = normalize_month_key(month_key) or month_key_for_gateway_datetime(now, gateway)
    current_month = month_start_from_key(current_month_key)
    next_month = add_month(current_month)
    month_variances = month_variances_for_gateway(gateway)
    api_schedule = api_schedule_for_gateway(gateway)
    current_preview = month_budget_preview(
        settings,
        api_schedule,
        month_variances,
        current_month,
        gateway,
        now=now,
        include_usage=True,
    )
    next_preview = month_budget_preview(
        settings,
        api_schedule,
        month_variances,
        next_month,
        gateway,
        now=now,
        include_usage=False,
    )
    return {
        "settings": settings,
        "month_key": current_month_key,
        "current_month": current_month,
        "current_month_key": current_month_key,
        "current_month_label": current_month.strftime("%B %Y"),
        "next_month": next_month,
        "next_month_key": next_month.strftime("%Y-%m"),
        "next_month_label": next_month.strftime("%B %Y"),
        "month_options": MONTH_OPTIONS,
        "month_variances": month_variances,
        "api_schedule": api_schedule,
        "current_preview": current_preview,
        "next_preview": next_preview,
        "previews": current_preview["sort_previews"],
        "preview_by_sort": {
            preview["sort_name"]: preview
            for preview in current_preview["sort_previews"]
        },
        "summary": current_preview,
        "usage_count": current_preview["polls_used"],
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
    save_month_variances(gateway, form)
    save_api_participation(gateway, form)
    _save_sort_settings(settings, gateway, form)
    db.session.flush()
    return settings, month_key


def api_schedule_for_gateway(gateway):
    active_entries = (
        GatewaySortMatrix.query.filter_by(gateway_id=gateway.id, is_active=True)
        .order_by(GatewaySortMatrix.sort_name.asc(), GatewaySortMatrix.day_of_week.asc())
        .all()
    )
    active_cells = {
        (entry.day_of_week, entry.sort_name)
        for entry in active_entries
        if entry.day_of_week in DAY_VALUES and entry.sort_name in SORT_VALUES
    }
    ensure_api_participation(gateway, active_cells)
    participation_rows = {
        (row.day_of_week, row.sort_name): row
        for row in SortTimelineApiParticipation.query.filter_by(gateway_id=gateway.id).all()
    }
    configured_sorts = []
    enabled_cells = set()
    configured_cells = set()
    for sort_name, sort_label in SORT_OPTIONS:
        day_rows = []
        for day, day_label in DAY_OPTIONS:
            if (day, sort_name) not in active_cells:
                continue

            participation = participation_rows.get((day, sort_name))
            is_enabled = bool(participation.is_enabled) if participation else True
            configured_cells.add((day, sort_name))
            if is_enabled:
                enabled_cells.add((day, sort_name))
            day_rows.append(
                {
                    "day": day,
                    "day_label": day_label,
                    "sort_name": sort_name,
                    "sort_label": sort_label,
                    "is_enabled": is_enabled,
                    "field_name": api_participation_field_name(sort_name, day),
                }
            )
        if day_rows:
            configured_sorts.append(
                {
                    "sort_name": sort_name,
                    "sort_label": sort_label,
                    "days": day_rows,
                }
            )

    return {
        "configured_sorts": configured_sorts,
        "configured_cells": configured_cells,
        "enabled_cells": enabled_cells,
        "has_configured_cells": bool(configured_cells),
    }


def ensure_api_participation(gateway, active_cells):
    existing = {
        (row.day_of_week, row.sort_name): row
        for row in SortTimelineApiParticipation.query.filter_by(gateway_id=gateway.id).all()
    }
    for day, sort_name in active_cells:
        row = existing.get((day, sort_name))
        if row:
            row.gateway_code = gateway.code
            continue
        db.session.add(
            SortTimelineApiParticipation(
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                day_of_week=day,
                sort_name=sort_name,
                is_enabled=True,
            )
        )
    db.session.flush()


def save_api_participation(gateway, form):
    active_cells = {
        (entry.day_of_week, entry.sort_name)
        for entry in GatewaySortMatrix.query.filter_by(gateway_id=gateway.id, is_active=True).all()
        if entry.day_of_week in DAY_VALUES and entry.sort_name in SORT_VALUES
    }
    ensure_api_participation(gateway, active_cells)
    rows = {
        (row.day_of_week, row.sort_name): row
        for row in SortTimelineApiParticipation.query.filter_by(gateway_id=gateway.id).all()
    }
    for day, sort_name in active_cells:
        row = rows[(day, sort_name)]
        row.gateway_code = gateway.code
        row.is_enabled = form.get(api_participation_field_name(sort_name, day)) == "1"
    db.session.flush()


def api_participation_field_name(sort_name, day):
    return f"api_enabled_{sort_name}_{day}"


def month_budget_preview(settings, api_schedule, month_variances, month_start, gateway, now=None, include_usage=True):
    monthly_poll_count = monthly_poll_limit(settings.monthly_api_units, settings.units_per_poll)
    month_key = month_start.strftime("%Y-%m")
    month_variance = month_variances.get(month_start.month, 0)
    base_operating_days = api_operating_day_count(month_start, api_schedule["configured_cells"])
    operating_days = adjusted_operating_days(base_operating_days, month_variance)
    base_api_polling_days = api_operating_day_count(month_start, api_schedule["enabled_cells"])
    api_polling_days = adjusted_api_polling_days(base_api_polling_days, month_variance)
    provider_enabled = bool(settings.provider_enabled)
    original_daily_cap = daily_poll_cap(monthly_poll_count, api_polling_days) if provider_enabled else 0
    polls_used = usage_count_for_month(gateway, month_key) if include_usage else 0
    units_used = polls_used * _safe_units_per_poll(settings.units_per_poll)
    units_remaining = max(0, int(settings.monthly_api_units or 0) - units_used)
    polls_remaining = max(0, monthly_poll_count - polls_used)
    remaining_base_days = remaining_api_operating_day_count(
        month_start,
        api_schedule["configured_cells"],
        sort_settings_by_name(settings),
        gateway,
        now=now,
    ) if include_usage else base_operating_days
    remaining_operating_days = adjusted_operating_days(remaining_base_days, month_variance)
    remaining_base_api_polling_days = remaining_api_operating_day_count(
        month_start,
        api_schedule["enabled_cells"],
        sort_settings_by_name(settings),
        gateway,
        now=now,
    ) if include_usage else base_api_polling_days
    remaining_api_polling_days = adjusted_api_polling_days(
        remaining_base_api_polling_days,
        month_variance,
    )
    adjusted_daily_cap = daily_poll_cap(polls_remaining, remaining_api_polling_days) if provider_enabled else 0
    effective_daily_cap = min(original_daily_cap, adjusted_daily_cap) if provider_enabled else 0
    budget_exhausted = provider_enabled and monthly_poll_count > 0 and polls_remaining <= 0
    sort_previews = sort_timeline_previews(settings, api_schedule, month_start, effective_daily_cap)
    special_count = sum(preview["special_poll_count"] for preview in sort_previews)
    auto_count = max(0, effective_daily_cap - special_count) if provider_enabled and not budget_exhausted else 0
    total_scheduled_polls = special_count + auto_count if provider_enabled and not budget_exhausted else 0

    return {
        "month_key": month_key,
        "month_label": month_start.strftime("%B %Y"),
        "provider_enabled": provider_enabled,
        "provider_disabled": not provider_enabled,
        "budget_exhausted": budget_exhausted,
        "monthly_api_units": settings.monthly_api_units,
        "units_per_poll": settings.units_per_poll,
        "monthly_poll_limit": monthly_poll_count,
        "units_used": units_used,
        "units_remaining": units_remaining,
        "polls_used": polls_used,
        "polls_remaining": polls_remaining,
        "base_operating_days": base_operating_days,
        "month_variance": month_variance,
        "operating_days": operating_days,
        "base_api_polling_days": base_api_polling_days,
        "api_polling_days": api_polling_days,
        "remaining_operating_days": remaining_operating_days,
        "remaining_api_polling_days": remaining_api_polling_days,
        "original_daily_poll_cap": original_daily_cap,
        "adjusted_daily_poll_cap": adjusted_daily_cap,
        "effective_daily_poll_cap": effective_daily_cap,
        "daily_poll_cap": effective_daily_cap,
        "special_poll_count": special_count,
        "auto_interval_poll_count": auto_count,
        "total_scheduled_polls": total_scheduled_polls,
        "sort_previews": sort_previews,
    }


def sort_timeline_previews(settings, api_schedule, month_start, effective_daily_cap):
    previews = []
    sort_settings = sort_settings_by_name(settings)
    enabled_cells = api_schedule["enabled_cells"]
    for sort_info in api_schedule["configured_sorts"]:
        sort_name = sort_info["sort_name"]
        sort_setting = sort_settings.get(sort_name)
        enabled_days = [
            day_info["day"]
            for day_info in sort_info["days"]
            if (day_info["day"], sort_name) in enabled_cells
        ]
        special_count = len(sort_setting.special_poll_times) if sort_setting else 0
        scheduled_times = scheduled_poll_times(sort_setting, effective_daily_cap)
        previews.append(
            {
                "sort_name": sort_name,
                "sort_label": sort_info["sort_label"],
                "sort_setting": sort_setting,
                "configured_days": sort_info["days"],
                "enabled_days": enabled_days,
                "api_day_count": api_sort_day_count(month_start, sort_name, enabled_cells),
                "special_poll_count": special_count,
                "next_poll_time": scheduled_times[0] if scheduled_times else None,
            }
        )

    return previews


def sort_settings_by_name(settings):
    return {
        sort_setting.sort_name: sort_setting
        for sort_setting in settings.sort_settings
    }


def api_operating_day_count(month_start, enabled_cells):
    return len(api_operating_occurrences_for_month(month_start, enabled_cells))


def api_sort_day_count(month_start, sort_name, enabled_cells):
    _, day_count = calendar.monthrange(month_start.year, month_start.month)
    total = 0
    for day_number in range(1, day_count + 1):
        candidate = date(month_start.year, month_start.month, day_number)
        day_name = candidate.strftime("%A").lower()
        if (day_name, sort_name) in enabled_cells:
            total += 1
    return total


def api_operating_occurrences_for_month(month_start, enabled_cells):
    _, day_count = calendar.monthrange(month_start.year, month_start.month)
    operating_occurrences = []
    for day_number in range(1, day_count + 1):
        candidate = date(month_start.year, month_start.month, day_number)
        day_name = candidate.strftime("%A").lower()
        for enabled_day, sort_name in enabled_cells:
            if day_name == enabled_day:
                operating_occurrences.append((candidate, sort_name))
    return operating_occurrences


def remaining_api_operating_day_count(month_start, enabled_cells, sort_settings, gateway, now=None):
    local_now = gateway_local_datetime(gateway, now)
    selected_month = month_start_from_key(month_start.strftime("%Y-%m"))
    local_month = date(local_now.year, local_now.month, 1)
    operating_occurrences = api_operating_occurrences_for_month(month_start, enabled_cells)

    if selected_month < local_month:
        return 0
    if selected_month > local_month:
        return len(operating_occurrences)

    remaining = 0
    today = local_now.date()
    for operating_date, sort_name in operating_occurrences:
        if operating_date > today:
            remaining += 1
        elif operating_date == today and sort_api_window_still_active(sort_name, sort_settings, local_now):
            remaining += 1
    return remaining


def today_api_window_still_active(enabled_cells, sort_settings, local_now):
    day_name = local_now.strftime("%A").lower()
    current_time = local_now.time().replace(second=0, microsecond=0)
    for enabled_day, sort_name in enabled_cells:
        if enabled_day != day_name:
            continue
        sort_setting = sort_settings.get(sort_name)
        if not sort_setting or not sort_setting.polling_end_local:
            return True
        if _time_window_still_active(
            current_time,
            sort_setting.polling_start_local,
            sort_setting.polling_end_local,
        ):
            return True
    return False


def sort_api_window_still_active(sort_name, sort_settings, local_now):
    current_time = local_now.time().replace(second=0, microsecond=0)
    sort_setting = sort_settings.get(sort_name)
    if not sort_setting or not sort_setting.polling_end_local:
        return True
    return _time_window_still_active(
        current_time,
        sort_setting.polling_start_local,
        sort_setting.polling_end_local,
    )


def _time_window_still_active(current_time, start_time, end_time):
    if not end_time:
        return True
    if start_time and end_time < start_time:
        return current_time >= start_time or current_time <= end_time
    return current_time <= end_time


def month_variances_for_gateway(gateway):
    ensure_month_variances(gateway)
    rows = SortTimelineMonthVariance.query.filter_by(gateway_id=gateway.id).all()
    values = {month_number: 0 for month_number, _month_label in MONTH_OPTIONS}
    for row in rows:
        if 1 <= row.month_number <= 12:
            row.gateway_code = gateway.code
            values[row.month_number] = int(row.variance or 0)
    db.session.flush()
    return values


def ensure_month_variances(gateway):
    existing = {
        row.month_number: row
        for row in SortTimelineMonthVariance.query.filter_by(gateway_id=gateway.id).all()
    }
    for month_number, _month_label in MONTH_OPTIONS:
        row = existing.get(month_number)
        if row:
            row.gateway_code = gateway.code
            continue

        db.session.add(
            SortTimelineMonthVariance(
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                month_number=month_number,
                variance=0,
            )
        )
    db.session.flush()


def save_month_variances(gateway, form):
    ensure_month_variances(gateway)
    rows = {
        row.month_number: row
        for row in SortTimelineMonthVariance.query.filter_by(gateway_id=gateway.id).all()
    }
    for month_number, _month_label in MONTH_OPTIONS:
        row = rows[month_number]
        row.gateway_code = gateway.code
        row.variance = _integer(form.get(f"month_variance_{month_number}"), default=0)
    db.session.flush()


def adjusted_operating_days(base_operating_days, month_variance):
    return max(0, int(base_operating_days or 0) + int(month_variance or 0))


def adjusted_api_polling_days(base_api_polling_days, month_variance):
    if int(base_api_polling_days or 0) <= 0:
        return 0
    return adjusted_operating_days(base_api_polling_days, month_variance)


def monthly_poll_limit(monthly_api_units, units_per_poll):
    return max(0, int(monthly_api_units or 0) // _safe_units_per_poll(units_per_poll))


def daily_poll_cap(monthly_poll_count, operating_days):
    if operating_days <= 0:
        return 0
    return max(0, int(monthly_poll_count or 0) // operating_days)


def auto_interval_poll_count(daily_cap, special_poll_count):
    return max(0, int(daily_cap or 0) - int(special_poll_count or 0))


def scheduled_poll_times(sort_setting, max_count):
    if not sort_setting or max_count <= 0:
        return []

    special_times = [
        special.poll_time_local
        for special in sort_setting.special_poll_times
    ]
    auto_count = max(0, int(max_count or 0) - len(special_times))
    auto_times = evenly_spread_times(
        sort_setting.polling_start_local,
        sort_setting.polling_end_local,
        auto_count,
    )
    return sorted(set(special_times + auto_times))[:max_count]


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


def add_month(month_start):
    year = month_start.year + (1 if month_start.month == 12 else 0)
    month = 1 if month_start.month == 12 else month_start.month + 1
    return date(year, month, 1)


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


def _save_sort_settings(settings, gateway, form):
    sort_settings = sort_settings_by_name(settings)
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

        _replace_special_poll_times(
            sort_setting,
            gateway,
            form.getlist(f"{sort_name}_special_poll_time"),
            form.getlist(f"{sort_name}_delete_special_poll_time"),
        )


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


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value, default=1):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)


def _safe_units_per_poll(units_per_poll):
    return max(1, int(units_per_poll or DEFAULT_UNITS_PER_POLL))
