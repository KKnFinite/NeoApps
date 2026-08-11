import re

from app.models import SortDateMission


TAIL_PRESENCE_TBD = "tail_tbd"
TAIL_PRESENCE_ASSUMED_HERE = "assumed_here"
TAIL_PRESENCE_ARRIVED = "arrived"
TAIL_PRESENCE_NOT_ARRIVED = "not_arrived"

_STATUS_OVERRIDE_ALLOWED = {"", "scheduled"}


def arrival_presence_by_tail(operation):
    """Build current-operation arrival presence without caching tail assignments."""
    if not operation:
        return {}

    arrivals = SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type="arrival",
    ).all()
    presence = {}
    for arrival in arrivals:
        tail = normalize_tail_number(arrival.assigned_tail_number)
        if not tail:
            continue
        state = presence.setdefault(
            tail,
            {
                "has_matching_arrival": True,
                "has_actual_block_in": False,
            },
        )
        if arrival.actual_block_in_datetime_utc is not None:
            state["has_actual_block_in"] = True
    return presence


def departure_tail_presence(mission, arrivals_by_tail=None):
    tail = normalize_tail_number(getattr(mission, "assigned_tail_number", None))
    if not tail:
        return _presence_payload(
            TAIL_PRESENCE_TBD,
            tail="",
            is_present=False,
            has_matching_arrival=False,
            has_actual_block_in=False,
        )

    arrival = (arrivals_by_tail or {}).get(tail)
    if arrival is None:
        return _presence_payload(
            TAIL_PRESENCE_ASSUMED_HERE,
            tail=tail,
            is_present=True,
            has_matching_arrival=False,
            has_actual_block_in=False,
        )

    has_actual_block_in = bool(arrival.get("has_actual_block_in"))
    return _presence_payload(
        TAIL_PRESENCE_ARRIVED if has_actual_block_in else TAIL_PRESENCE_NOT_ARRIVED,
        tail=tail,
        is_present=has_actual_block_in,
        has_matching_arrival=True,
        has_actual_block_in=has_actual_block_in,
    )


def tail_presence_status_override(mission, presence):
    if not presence:
        return None
    status = str(getattr(mission, "departure_status", "") or "").strip().lower()
    if status not in _STATUS_OVERRIDE_ALLOWED:
        return None
    if presence["state"] == TAIL_PRESENCE_TBD:
        return "TAIL TBD"
    if presence["state"] == TAIL_PRESENCE_NOT_ARRIVED:
        return "TAIL NOT ARRIVED"
    return None


def normalize_tail_number(value):
    return re.sub(r"\s+", "", str(value or "")).upper()


def _presence_payload(
    state,
    *,
    tail,
    is_present,
    has_matching_arrival,
    has_actual_block_in,
):
    return {
        "state": state,
        "tail": tail,
        "is_present": is_present,
        "has_matching_arrival": has_matching_arrival,
        "has_actual_block_in": has_actual_block_in,
        "show_door_parking": is_present,
    }
