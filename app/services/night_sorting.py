from datetime import time


NIGHT_SORT_MORNING_CUTOFF = time(12, 0)


def night_sort_time_key(time_value, sort_name):
    if time_value is None:
        return (2, 0)

    minutes = time_value.hour * 60 + time_value.minute
    if str(sort_name or "").strip().lower() == "night" and time_value < NIGHT_SORT_MORNING_CUTOFF:
        minutes += 24 * 60

    return (0, minutes)


def mission_board_sort_key(mission):
    return (
        mission.mission_type or "",
        night_sort_time_key(_mission_display_time(mission), mission.sort_name),
        mission.flight_number or "",
    )


def master_schedule_sort_key(master_schedule):
    return (
        master_schedule.gateway_code or "",
        master_schedule.mission_type or "",
        night_sort_time_key(master_schedule.planned_time_local, master_schedule.sort_name),
        master_schedule.sort_name or "",
        master_schedule.flight_number or "",
    )


def _mission_display_time(mission):
    value = None
    if mission.mission_type == "arrival" and mission.eta_datetime_utc:
        value = mission.eta_datetime_utc
    else:
        value = mission.planned_datetime_local

    if hasattr(value, "time"):
        return value.time()
    return value
