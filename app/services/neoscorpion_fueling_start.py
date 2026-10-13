"""Physical-start eligibility from committed current-sort facts. No writes/timers."""
from datetime import timedelta
from math import ceil

from app.services.neoscorpion_fueling_status import utc_naive
from app.services.neoscorpion_spear import _parking_ramp, _travel_minutes


def truck_start_gate(record, records, releases, settings, *, at):
    truck_id = record["truck_id"]
    result = dict(can_start=False, reason="Needs Truck", origin="", arrival=None)
    if truck_id is None:
        return result
    if record["fueler_id"] is None:
        return dict(result, reason="Needs Fueler")
    if not record["truck_available"]:
        return dict(result, reason="TRUCK UNAVAILABLE")
    # An ON from another mission is a commitment until its recorded release;
    # future resource selections without ON are not physical activity.
    for other in records:
        start = other.get("manual_on") or other["confirmed_on"] or other["on"]
        if (other["id"] == record["id"] or other["truck_id"] != truck_id
                or start is None):
            continue
        assigned_at = other["assigned_at"]
        if assigned_at and assigned_at > start + timedelta(seconds=1):
            continue  # Legacy ON predates this truck's actual assignment.
        if start <= at and (other["release"] is None or other["release"] > at):
            return dict(result, reason="TRUCK STILL FUELING")
    past = [other for other in records if other["id"] != record["id"]
            and other["truck_id"] == truck_id and other["release"] is not None
            and other["release"] <= at and (other["on"] or other["confirmed_on"] or other.get("manual_on"))]
    past.extend(event for event in releases if event["id"] != record["id"]
                and event["truck_id"] == truck_id and event["release"] <= at)
    if past:
        previous = max(past, key=lambda item: item["release"])
        ramps = {_parking_ramp(item["parking"]) for item in past if item["release"] == previous["release"]}
        if None in ramps or len(ramps) != 1:
            return dict(result, reason="TRUCK LOCATION UNKNOWN")
        origin, destination = _parking_ramp(previous["parking"]), _parking_ramp(record["parking"])
        if origin is None or destination is None:
            return dict(result, reason="TRUCK LOCATION UNKNOWN")
        departure = max(previous["release"], record["assigned_at"] or previous["release"])
        arrival = departure + timedelta(minutes=float(_travel_minutes(
            origin, destination, settings.truck_minutes_per_ramp_move)))
        if arrival > at:
            return dict(result, reason=f"COMING FROM {origin.upper()} · ~{ceil((arrival-at).total_seconds()/60)} MIN",
                        origin=origin.upper(), arrival=arrival)
        return dict(result, can_start=True, reason="ARRIVAL UNCONFIRMED", origin=origin.upper(), arrival=arrival)
    return dict(result, can_start=True, reason="")


def fueling_start_state(record, records, releases, settings, *, now):
    now = utc_naive(now)
    gate = truck_start_gate(record, records, releases, settings, at=now)
    manual = record.get("manual_on")
    start = manual or record["confirmed_on"] or record["on"]
    trusted = False
    premature = False
    if manual is not None and manual <= now:
        # A Fueler's explicit physical confirmation outranks predicted travel,
        # but only while attached to this exact current truck/cycle segment.
        trusted = True
    elif start is not None and start <= now:
        provenance = record["assigned_at"] is None or record["assigned_at"] <= start + timedelta(seconds=1)
        historical = truck_start_gate({**record, "truck_available": True}, records, releases, settings, at=start)
        premature = bool(not provenance or (record["confirmed_on"] is None and record.get("premature_readings", False))
                         or historical["reason"] == "TRUCK STILL FUELING"
                         or historical["reason"].startswith("COMING FROM"))
        # A persisted confirmation is evidence from the actual save-time gate.
        # Later parking/status edits must not turn that legitimate ON into a new
        # start. Uncertain legacy timing stays unknown instead of sliding forward.
        trusted = bool(provenance and (record["confirmed_on"] is not None
                       or (not premature and record["remaining_at_on"] and historical["can_start"])))
    if trusted:
        gate.update(reason="", origin="", arrival=None)
    elif start is not None and gate["can_start"] and gate["arrival"] is None:
        gate["reason"] = "TIMING UNKNOWN"
    if start is not None and start > now:
        premature = True
    active = bool(trusted or (start is not None and not premature and record["truck_id"] is not None
                             and record["fueler_id"] is not None))
    return {**gate, "on_trusted": trusted, "on_active": active,
            "requires_confirmation": start is None or (premature and not trusted),
            "effective_on": start if trusted else None}
