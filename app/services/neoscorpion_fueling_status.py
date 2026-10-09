"""Read-only, shared fueling progress. Urgency never changes workflow state."""
from datetime import datetime, timedelta, timezone


STAGE_COLORS = {
    "pending": "gray", "ready": "amber", "assigned": "orange",
    "fueling": "yellow", "off": "teal", "fob-ready": "teal",
    "complete": "green", "fob": "green", "review": "red",
}
DEPARTURE_ALERT_STAGES = frozenset({"pending", "ready", "assigned", "off", "fob-ready"})


def utc_naive(value):
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def status_color(stage, departure, *, now=None, threshold=30, predicted_finish=None):
    now = utc_naive(now or datetime.utcnow())
    departure = utc_naive(departure)
    if stage in DEPARTURE_ALERT_STAGES and departure is not None:
        if departure <= now + timedelta(minutes=threshold):
            return "red"
    if stage == "fueling" and predicted_finish is not None and departure is not None:
        # Match SPEAR's AT RISK / LATE boundary. An overdue estimate cannot
        # predict finishing in the past while work is still active.
        if max(utc_naive(predicted_finish), now) > departure - timedelta(minutes=20):
            return "red"
    return STAGE_COLORS[stage]


def fueling_status(row, *, now=None, threshold=30):
    assignment = row.get("assignment")
    work = row.get("fuel_work_state")
    fueler = bool(assignment and assignment.assigned_fueler_user_id is not None)
    truck = bool(assignment and assignment.assigned_truck_id is not None)
    detail = ""
    warnings = []
    if row.get("fuel_on_board_complete"):
        stage = "fob"
    elif row.get("administratively_complete"):
        stage = "complete"
    elif row.get("effective_hold") or row.get("work_ended_early"):
        stage = "review"
        detail = row.get("hold_reason_display") or "Dispatcher review required"
    elif row.get("fuel_on_board_ready"):
        stage = "fob-ready"
    elif work and work.off_at_utc:
        stage = "off"
    elif work and work.on_at_utc:
        stage = "fueling"
    else:
        missing = []
        if str(row.get("arrival_status", "")).casefold() not in {"arrived", "on ground"}:
            missing.append("Arrival")
        if row.get("inbound_fuel_lbs") is None and row.get("measured_inbound_fuel_lbs") is None:
            missing.append("Inbound")
        if row.get("required_fuel_lbs") is None:
            missing.append("Required")
        if not row.get("parking_valid"):
            missing.append("Parking")
        if missing:
            stage, detail = "pending", "Needs " + ", ".join(missing)
        elif fueler and (truck or row.get("fob_likely")):
            stage = "assigned"
        else:
            stage = "ready"
            detail = "Needs Fueler" if not fueler else "Needs Truck"
            if not fueler and not truck and not row.get("fob_likely"):
                detail = "Needs Fueler / Truck"
    fob_check = bool(row.get("fob_likely") and fueler and not truck and stage in {"assigned", "fueling"})
    if row.get("fob_likely") and stage not in {"fob-ready", "fob", "complete"}:
        warnings.append("FOB CHECK" if fob_check else "FOB LIKELY")
    if row.get("tail_mismatch"):
        warnings.append("TAIL SWAP · advisory")
    if row.get("direction_mismatch"):
        warnings.append("Fuel direction discrepancy")
    if assignment and getattr(assignment, "review_status", None) == "review" and stage != "review":
        warnings.append("Dispatcher review requested")
    departure = utc_naive(row["mission"].eta_datetime_utc or row["mission"].planned_datetime_utc)
    predicted_finish = row.get("fueling_predicted_finish_utc")
    if stage == "fueling" and (predicted_finish is None or departure is None):
        warnings.append("TIMING UNKNOWN")
    return {
        "dispatch_status_key": stage,
        "dispatch_status_label": stage.upper().replace("-", " "),
        "dispatch_status_detail": detail,
        "fuel_status_warnings": tuple(warnings),
        "fuel_status_color": status_color(stage, departure, now=now, threshold=threshold,
                                          predicted_finish=predicted_finish),
        "fuel_status_etd_utc": departure.isoformat() + "Z" if departure else "",
        "fuel_status_threshold": threshold,
        "fuel_status_predicted_finish_utc": utc_naive(predicted_finish).isoformat() + "Z" if predicted_finish else "",
    }


def fob_assessment(*, required, inbound, remaining, actual, apu_allowance,
                   apu_confirmed, apu_source_valid, inherited=None, transfer=None,
                   cycle_type="fuel"):
    """Estimates identify checks; only complete/trusted measurements verify FOB."""
    verified = inherited if inherited is not None else actual if apu_confirmed and apu_source_valid else None
    available = verified if verified is not None else remaining if remaining is not None else inbound
    projected = available - apu_allowance if available is not None and apu_allowance is not None else None
    eligible = cycle_type != "defuel" and transfer in (None, 0)
    sufficient = bool(eligible and required is not None and projected is not None and projected >= required)
    return {"fob_likely": sufficient, "fob_verified": sufficient and verified is not None,
            "fob_available_lbs": available, "fob_projected_neo_lbs": projected,
            "fob_apu_allowance_lbs": apu_allowance}
