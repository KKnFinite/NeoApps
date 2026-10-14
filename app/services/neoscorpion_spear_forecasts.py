"""SPEAR's physical OFF forecasts. No writes, wall-clock sliding, or pump training.

LIKELY uses a baseline-weighted service-duration ratio. Each historical event is
included once at its most specific resource match (pair, Fueler, Truck, aircraft).
OBSERVE preserves baseline. APPLY needs three qualified outcomes. This does not
multiply resource PROF ratings or label effective service throughput a pump rate.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.neoscorpion_prof import number, timestamp, setup_allowance


def physical_off_forecast(row, planning, *, settings=None, jobs=(), mode="observe"):
    assignment, work = row.get("assignment"), row.get("fuel_work_state")
    result = dict(goal=None, likely=None, difference=None, source="TIMING UNKNOWN", samples=0)
    if assignment is None or (work and (work.off_at_utc or work.ended_early_at_utc)) or row.get("administratively_complete") or row.get("effective_hold"):
        return result
    if assignment.assigned_fueler_user_id is None or assignment.assigned_truck_id is None:
        return result
    if row.get("fueling_on_trusted") is False and row.get("fueling_effective_on_utc") is not None:
        return result
    facts = getattr(work, "performance_context", None) or {}
    if facts.get("exclusions"):
        return result  # Swapped/interrupted identities cannot define a PROF goal.
    aircraft = row.get("detailed_aircraft_type")
    rate = number(facts.get("pump_rate")) if facts else number(planning.pump_rate_for(aircraft))
    allowance = number(facts.get("setup_allowance")) if facts else setup_allowance(
        getattr(assignment, "fueler_assigned_at_utc", None), getattr(assignment, "truck_assigned_at_utc", None),
        getattr(settings, "advance_setup_window_minutes", None), planning.setup_minutes)[0]
    gallons = number(getattr(assignment, "transfer_fuel_gallons", None) if getattr(assignment, "transfer_fuel_gallons", None) is not None
                     else row.get("planning_demand_gallons"))
    if rate is None or rate <= 0 or allowance is None or gallons is None or gallons <= 0:
        return result
    start = row.get("fueling_effective_on_utc")
    if start is None:
        # A preassignment is not physical arrival. Use recorded readiness and
        # actual release/travel evidence, never the time this page was rendered.
        if row.get("truck_start_reason") in {"TRUCK UNAVAILABLE", "TRUCK LOCATION UNKNOWN"} or (
                row.get("truck_start_reason") == "TRUCK STILL FUELING" and row.get("forecast_truck_available_at") is None):
            return result
        arrival = row.get("arrival_mission")
        ready = getattr(arrival, "actual_block_in_datetime_utc", None)
        if ready is None:
            eta = getattr(arrival, "eta_datetime_utc", None)
            if eta is None or planning.eta_safety_buffer_minutes is None:
                return result
            ready = eta+timedelta(minutes=float(planning.eta_safety_buffer_minutes))
        assigned = getattr(assignment, "truck_assigned_at_utc", None)
        if assigned is None:
            return result
        start = max(ready, assigned, timestamp(row.get("truck_travel_until", "").replace("Z", "")) or assigned,
                    row.get("forecast_truck_available_at") or assigned, row.get("forecast_fueler_available_at") or assigned)
    expected = allowance + gallons/rate
    result["goal"] = start + timedelta(seconds=float(expected*60))
    weighted, weight_total, sample_count = Decimal(3), Decimal(3), 0
    for job in jobs:
        if job["fueler_reasons"] or job["expected_fueler"] is None or job["expected_fueler"] <= 0:
            continue
        e = job["event"]
        if e.fuel_assignment_id == assignment.id and e.cycle_number == getattr(assignment, "current_cycle_number", 1):
            # Repeated visits in the same cycle are independent outcomes. Only
            # the current segment is self-evidence; prior sealed visits remain
            # eligible. Legacy rows without identity cannot prove independence.
            if getattr(work,"id",None) is None or getattr(e,"fuel_work_state_id",None)==work.id:
                continue
        if job["facts"].get("aircraft_type") != aircraft:
            continue
        f, t = e.fueler_user_id == assignment.assigned_fueler_user_id, e.fuel_truck_id == assignment.assigned_truck_id
        weight = Decimal(4 if f and t else 3 if f else 2 if t else 1)
        weighted += weight*job["actual_minutes"]/job["expected_fueler"]
        weight_total += weight
        sample_count += 1
    factor = weighted/weight_total if mode == "apply" and sample_count >= 3 else Decimal(1)
    # Bound accepted observations to avoid a single abnormal sample inventing a
    # precise estimate. The report retains that sample and its actual facts.
    factor = max(Decimal("0.5"), min(Decimal(2), factor))
    result.update(likely=start+timedelta(seconds=float(expected*factor*60)),
                  difference=int((expected*(factor-1)).quantize(Decimal(1), rounding=ROUND_HALF_UP)),
                  source="QUALIFIED SERVICE HISTORY" if mode == "apply" and sample_count >= 3 else "BASELINE",
                  samples=sample_count)
    return result
