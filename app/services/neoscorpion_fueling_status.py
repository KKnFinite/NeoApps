"""Read-only, shared fueling progress. Urgency never changes workflow state."""
from datetime import datetime, timedelta, timezone


STAGE_COLORS = {
    "pending": "gray", "ready": "amber", "assigned": "orange",
    "fueling": "yellow", "off": "teal", "fob-ready": "teal",
    "complete": "green", "fob": "green", "review": "red",
}
DEPARTURE_ALERT_STAGES = frozenset({"pending", "ready", "assigned", "off", "fob-ready"})


def status_secondary(stage, detail, warnings):
    """Pick one compact line; full reasons/warnings remain in canonical data."""
    if stage == "review" and detail:
        return detail
    for critical in ("Fuel direction discrepancy", "Dispatcher review requested"):
        if critical in warnings:
            return critical
    if detail == "MISSION DATA MISSING":
        return detail
    for critical in ("TIMING UNKNOWN", "FOB CHECK", "FOB LIKELY"):
        if critical in warnings:
            return critical
    if detail:
        return detail
    return next(iter(warnings), "")


def mission_missing_fields(row):
    """Data readiness only. Arrival and resource availability are separate facts.

    Only complete measured/inherited evidence may replace Inbound. Zero is a
    valid fuel reading, not a missing value. This never gates assignment writes.
    """
    mission = row["mission"]
    missing = []
    for label, value in (("Tail", row.get("tail_number", getattr(mission, "assigned_tail_number", None))),
                         ("Flight", getattr(mission, "flight_number", None)),
                         ("Destination", getattr(mission, "destination", None))):
        if not str(value or "").strip() or str(value).strip() in {"-", "—"}:
            missing.append(label)
    if not (mission.eta_datetime_utc or mission.planned_datetime_utc):
        missing.append("Departure time")
    if row.get("required_fuel_lbs") is None:
        missing.append("Required Fuel Load")
    if all(row.get(key) is None for key in
           ("inbound_fuel_lbs", "measured_inbound_fuel_lbs", "tail_swap_inherited_fuel_lbs")):
        missing.append("Inbound Fuel / verified measurement")
    if row.get("parking_required", True) and not row.get("parking_valid"):
        missing.append("Parking")
    return tuple(missing)


def mission_data_summary(missing):
    """One actionable note; the complete list stays in DETAILS and the title."""
    if not missing:
        return ""
    labels = {"Required Fuel Load": "FUEL LOAD", "Inbound Fuel / verified measurement": "INBOUND",
              "Departure time": "ETD"}
    first = labels.get(missing[0], missing[0].upper())
    return f"NEEDS {first}" + (f" +{len(missing)-1}" if len(missing) > 1 else "")


def review_reasons(row):
    """Current operational evidence, never arrival, forecasts or advisory swaps."""
    work = row.get("fuel_work_state")
    reasons = []
    if row.get("effective_hold"):
        reasons.append(row.get("hold_reason_display") or "Dispatcher hold requires review")
    if getattr(row.get("assignment"), "review_status", None) == "review" and not row.get("effective_hold"):
        reasons.append("Dispatcher review requested")
    if row.get("work_ended_early"):
        reasons.append("Fuel work ended early; Dispatcher review required")
    if row.get("physical_transfer_review") or getattr(work, "physical_transfer_review", False):
        reasons.append("Verify this physical visit's T/F ownership")
    if getattr(work, "continuation_review_reason", None):
        reasons.append(work.continuation_review_reason)
    if row.get("direction_mismatch"):
        reasons.append("Fuel direction discrepancy")
    if (work and not work.off_at_utc and row.get("fueling_on_trusted") is False
            and (getattr(work, "manual_on_at_utc", None) or getattr(work, "confirmed_on_at_utc", None) or work.on_at_utc)):
        reasons.append("Physical ON evidence requires verification")
    return tuple(dict.fromkeys(reasons))


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
    missing = mission_missing_fields(row)
    reviews = review_reasons(row)
    detail = ""
    warnings = []
    if row.get("fuel_on_board_complete"):
        stage = "fob"
    elif row.get("administratively_complete"):
        stage = "complete"
    elif reviews:
        stage = "review"
        detail = reviews[0]
    elif row.get("fuel_on_board_ready"):
        stage = "fob-ready"
    elif work and work.off_at_utc:
        stage = "off"
    elif work and (getattr(work, "manual_on_at_utc", None) or getattr(work, "confirmed_on_at_utc", None) or work.on_at_utc) and row.get("fueling_on_trusted", row.get("fueling_on_active", False)):
        stage = "fueling"
    elif fueler and truck:
        # Assignment is not physical readiness. Missing operational evidence
        # remains a warning/blocker, never a hidden third assignment gate.
        stage = "assigned"
        if missing:
            detail = "MISSION DATA MISSING"
    elif missing:
        stage, detail = "pending", "MISSION DATA MISSING"
    else:
        stage = "ready"
        detail = "FOB CHECK" if fueler and row.get("fob_likely") else (
            "Needs Fueler" if truck else "Needs Truck" if fueler else "Needs Fueler / Truck")
    movement = row.get("truck_start_reason")
    if stage in {"pending", "assigned", "ready"} and truck and movement:
        movement_note = "WAITING FOR TRUCK · TRUCK STILL FUELING" if movement == "TRUCK STILL FUELING" else movement
        # Availability does not make missing mission data disappear. Retain
        # movement separately for DETAILS/Fueler truck information, then show it
        # on the primary status only when no essential data is missing.
        if missing or movement == "TRUCK STILL FUELING":
            warnings.append(movement_note)
        if stage in {"assigned", "ready"} and not missing:
            detail = movement_note
    fob_check = bool(row.get("fob_likely") and fueler and not truck)
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
    if stage in {"complete", "fob"}:
        detail = ""
        # Drop stale advisory decoration, not genuine discrepancies on retained
        # historical evidence. Completed does not mean warnings may be erased.
        warnings = [warning for warning in warnings if warning != "TAIL SWAP · advisory"]
    return {
        "mission_data_missing": missing,
        "mission_data_summary": mission_data_summary(missing),
        "status_review_reasons": reviews if stage not in {"complete", "fob"} else (),
        "dispatch_status_key": stage,
        "dispatch_status_label": stage.upper().replace("-", " "),
        "dispatch_status_detail": detail,
        "fuel_status_warnings": tuple(warnings),
        "fuel_status_secondary": status_secondary(stage, detail, warnings),
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
    verified = (inherited if inherited is not None else actual) if apu_confirmed and apu_source_valid else None
    available = verified if verified is not None else remaining if remaining is not None else inbound
    projected = available - apu_allowance if available is not None and apu_allowance is not None else None
    eligible = cycle_type != "defuel" and transfer in (None, 0)
    sufficient = bool(eligible and required is not None and projected is not None and projected >= required)
    return {"fob_likely": sufficient, "fob_verified": sufficient and verified is not None,
            "fob_available_lbs": available, "fob_projected_neo_lbs": projected,
            "fob_apu_allowance_lbs": apu_allowance}
