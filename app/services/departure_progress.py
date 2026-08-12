"""Departure status helpers shared by external operational adapters."""


DEPARTURE_STATUS_RANK = {
    "scheduled": 0,
    "loading": 1,
    "last_uld_enroute": 2,
    "ramp_load_complete": 3,
    "crew_load_complete": 4,
    "blocked_out": 5,
    "departed": 6,
}


def recompute_departure_status_after_external_clear(mission, cleared_status):
    """Recompute status after an externally owned milestone is removed.

    A stronger status than the cleared event is preserved because it represents
    later operational progress. Otherwise, durable timestamps determine the
    strongest remaining status. Intentional loading is also preserved.
    """
    current_status = _normalized_status(mission.departure_status)
    if current_status == "cancelled":
        return False

    current_rank = DEPARTURE_STATUS_RANK.get(current_status, -1)
    cleared_rank = DEPARTURE_STATUS_RANK.get(cleared_status, -1)
    if current_rank > cleared_rank:
        return False

    next_status = _strongest_factual_departure_status(mission, current_status)
    if current_status == next_status:
        return False

    mission.departure_status = next_status
    return True


def repair_orphaned_external_departed_status(mission):
    """Repair old external departed state whose owned timestamp was already cleared.

    Before this repair shipped, Google Rain could clear its timestamp/source but
    leave ``departed`` behind. Manual missions are excluded because their status
    can be an intentional operator decision without a timestamp.
    """
    if (
        _normalized_status(mission.departure_status) != "departed"
        or mission.actual_block_out_datetime_utc is not None
        or _normalized_status(mission.mission_source) == "manual"
    ):
        return False
    return recompute_departure_status_after_external_clear(mission, "departed")


def _strongest_factual_departure_status(mission, current_status):
    if mission.actual_block_out_datetime_utc is not None:
        return "blocked_out" if current_status == "blocked_out" else "departed"
    if mission.crew_load_completed_at_utc is not None:
        return "crew_load_complete"
    if mission.ramp_load_completed_at_utc is not None:
        return "ramp_load_complete"
    if mission.last_uld_enroute_at_utc is not None:
        return "last_uld_enroute"
    if current_status == "loading":
        return "loading"
    return "scheduled"


def _normalized_status(value):
    return str(value or "").strip().lower()
