"""Canonical completion and lateness rules for NeoRain boards and reports."""
from app.services.sort_date_operations import normalize_wave

ARRIVAL_COMPLETE = {"arrived", "unloaded"}


def mission_status(mission):
    value = mission.arrival_status if mission.mission_type == "arrival" else mission.departure_status
    return str(value or "").strip().lower()


def mission_cancelled(mission):
    return mission_status(mission) == "cancelled"


def late_inclusion(mission):
    if mission_cancelled(mission):
        return {"included": False, "source": "cancelled"}
    override = mission.late_metrics_included_override
    if override is not None:
        return {"included": bool(override), "source": "override"}
    return {"included": normalize_wave(mission.wave) in {"1", "2"}, "source": "default"}


def eligible_for_performance(mission):
    """An unfinished flight remains in coverage unless cancelled or excluded."""
    return mission.planned_datetime_utc is not None and late_inclusion(mission)["included"]


def completed_actual(mission):
    status = mission_status(mission)
    if mission.mission_type == "arrival" and status in ARRIVAL_COMPLETE:
        return mission.actual_block_in_datetime_utc
    if mission.mission_type == "departure" and status == "departed":
        return mission.actual_block_out_datetime_utc
    return None


def completed_variance_minutes(mission):
    actual, scheduled = completed_actual(mission), mission.planned_datetime_utc
    return int((actual - scheduled).total_seconds() / 60) if actual and scheduled else None


def average_lateness(missions):
    """Average positive completed variances, first overall and then with exclusions applied."""
    completed = [(mission, minutes) for mission in missions if not mission_cancelled(mission)
                 if (minutes := completed_variance_minutes(mission)) is not None]

    def average(variances):
        if not variances:
            return None
        late = [minutes for minutes in variances if minutes > 0]
        return sum(late) / len(late) if late else 0.0

    return {
        "overall": average([minutes for _, minutes in completed]),
        "included": average([minutes for mission, minutes in completed if eligible_for_performance(mission)]),
    }
