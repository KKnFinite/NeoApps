from datetime import datetime, timedelta

from app.models import SortDateOperation


def create_sort_date_operation(
    sort_date,
    gateway_code,
    sort_name,
    window_minutes=0,
    generated_by_user_id=None,
):
    return SortDateOperation(
        sort_date=sort_date,
        gateway_code=gateway_code,
        sort_name=sort_name,
        window_minutes=normalize_window_minutes(window_minutes),
        generated_by_user_id=generated_by_user_id,
    )


def normalize_window_minutes(window_minutes):
    if window_minutes is None:
        return 0

    window_minutes = int(window_minutes)
    if window_minutes < 0:
        raise ValueError("window_minutes cannot be negative.")

    return window_minutes


def apply_window_minutes(value, window_minutes):
    if value is None:
        return None

    window_minutes = normalize_window_minutes(window_minutes)
    if window_minutes == 0:
        return value

    if isinstance(value, datetime):
        return value + timedelta(minutes=window_minutes)

    anchor = datetime.combine(datetime.min.date(), value)
    return (anchor + timedelta(minutes=window_minutes)).time()


def mission_display_timing_data(mission, operation=None):
    window_minutes = normalize_window_minutes(
        getattr(operation, "window_minutes", 0) if operation else 0
    )

    if mission.mission_type == "departure":
        return {
            "base_planned_departure_time": mission.planned_datetime_local,
            "adjusted_planned_departure_time": apply_window_minutes(
                mission.planned_datetime_local,
                window_minutes,
            ),
            "base_pure_pull_time": mission.pure_pull_time_local,
            "adjusted_pure_pull_time": apply_window_minutes(
                mission.pure_pull_time_local,
                window_minutes,
            ),
            "base_first_mix_pull_time": mission.first_mix_pull_time_local,
            "adjusted_first_mix_pull_time": apply_window_minutes(
                mission.first_mix_pull_time_local,
                window_minutes,
            ),
            "base_final_mix_pull_time": mission.final_mix_pull_time_local,
            "adjusted_final_mix_pull_time": apply_window_minutes(
                mission.final_mix_pull_time_local,
                window_minutes,
            ),
        }

    return {
        "base_planned_arrival_time": mission.planned_datetime_local,
        "adjusted_planned_arrival_time": mission.planned_datetime_local,
        "base_eta_time_utc": mission.eta_datetime_utc,
        "adjusted_eta_time_utc": mission.eta_datetime_utc,
        "base_actual_block_in_time_utc": mission.actual_block_in_datetime_utc,
        "adjusted_actual_block_in_time_utc": mission.actual_block_in_datetime_utc,
    }
