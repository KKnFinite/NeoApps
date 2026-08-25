from datetime import datetime, timezone
import re

from app.models import SortDateGoogleMissionLink, SortDateMission


TAIL_PRESENCE_TBD = "tail_tbd"
TAIL_PRESENCE_ASSUMED_HERE = "assumed_here"
TAIL_PRESENCE_ARRIVED = "arrived"
TAIL_PRESENCE_NOT_ARRIVED = "not_arrived"

_STATUS_OVERRIDE_ALLOWED = {"", "scheduled"}

PRESENCE_EVIDENCE_ACTUAL_BLOCK_IN = "actual_block_in"
PRESENCE_EVIDENCE_GOOGLE_HERE = "google_here"
PRESENCE_EVIDENCE_API_ASSUMED_ARRIVED = "api_assumed_arrived"

_EVIDENCE_PRIORITY = {
    None: 0,
    PRESENCE_EVIDENCE_API_ASSUMED_ARRIVED: 1,
    PRESENCE_EVIDENCE_GOOGLE_HERE: 2,
    PRESENCE_EVIDENCE_ACTUAL_BLOCK_IN: 3,
}


def arrival_presence_by_tail(
    operation,
    now=None,
    *,
    arrivals=None,
    google_links=None,
):
    """Build current-operation arrival presence without caching tail assignments."""
    if not operation:
        return {}

    now_utc = _utc_naive(now) or datetime.now(timezone.utc).replace(tzinfo=None)
    if arrivals is None:
        arrivals = SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="arrival",
        ).all()
    links_by_tail = _google_arrival_links_by_tail(
        operation,
        google_links=google_links,
    )
    presence = {}
    for arrival in arrivals:
        tail = normalize_tail_number(arrival.assigned_tail_number)
        if not tail:
            continue
        arrival_evidence = _arrival_presence_evidence(
            arrival,
            tail,
            links_by_tail.get(tail, ()),
            now_utc,
        )
        evidence = arrival_evidence["presence_evidence"]
        state = presence.setdefault(
            tail,
            {
                "has_matching_arrival": True,
                "has_actual_block_in": False,
                "has_google_here": False,
                "has_api_assumed_arrived": False,
                "presence_evidence": None,
            },
        )
        state["has_actual_block_in"] |= arrival_evidence["has_actual_block_in"]
        state["has_google_here"] |= arrival_evidence["has_google_here"]
        state["has_api_assumed_arrived"] |= arrival_evidence[
            "has_api_assumed_arrived"
        ]
        current_evidence = state["presence_evidence"]
        if _EVIDENCE_PRIORITY[evidence] > _EVIDENCE_PRIORITY[current_evidence]:
            state["presence_evidence"] = evidence
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
    presence_evidence = arrival.get("presence_evidence")
    is_present = presence_evidence is not None
    return _presence_payload(
        TAIL_PRESENCE_ARRIVED if is_present else TAIL_PRESENCE_NOT_ARRIVED,
        tail=tail,
        is_present=is_present,
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


def _arrival_presence_evidence(arrival, tail, google_links, now_utc):
    has_actual_block_in = arrival.actual_block_in_datetime_utc is not None
    has_google_here = any(
        _link_confirms_google_here(link, arrival, tail) for link in google_links
    )
    assumed_arrived_at = _utc_naive(arrival.api_assumed_arrived_time_utc)
    has_api_assumed_arrived = (
        assumed_arrived_at is not None and assumed_arrived_at <= now_utc
    )
    if has_actual_block_in:
        presence_evidence = PRESENCE_EVIDENCE_ACTUAL_BLOCK_IN
    elif has_google_here:
        presence_evidence = PRESENCE_EVIDENCE_GOOGLE_HERE
    elif has_api_assumed_arrived:
        presence_evidence = PRESENCE_EVIDENCE_API_ASSUMED_ARRIVED
    else:
        presence_evidence = None
    return {
        "has_actual_block_in": has_actual_block_in,
        "has_google_here": has_google_here,
        "has_api_assumed_arrived": has_api_assumed_arrived,
        "presence_evidence": presence_evidence,
    }


def _google_arrival_links_by_tail(operation, *, google_links=None):
    links = google_links
    if links is None:
        links = SortDateGoogleMissionLink.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="arrival",
        ).all()
    by_tail = {}
    for link in links:
        # Current-operation tail identity is the Motherbrain authority. Import
        # reconciliation can replace an arrival row while retaining its HERE
        # evidence on the prior link row, so an exact mission-id match is too
        # strict. This remains bounded and only considers the same tail.
        tail = normalize_tail_number(link.last_tail_number)
        if tail:
            by_tail.setdefault(tail, []).append(link)
    return by_tail


def _link_confirms_google_here(link, arrival, tail):
    return (
        str(link.last_status_raw or "").strip().upper() == "HERE"
        and normalize_tail_number(link.last_tail_number) == tail
        and normalize_tail_number(arrival.assigned_tail_number) == tail
    )


def _utc_naive(value):
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


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
