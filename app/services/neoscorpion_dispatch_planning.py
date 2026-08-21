"""Pure dispatcher fuel-demand and nightly truck projection calculations."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta


DEFAULT_PLANNING_INBOUND_FALLBACK_LBS = 12_000
FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES = {
    "B757": Decimal("12"),
    "A300": Decimal("13"),
    "B767ER": Decimal("13"),
    "B747-400": Decimal("12"),
    "B747-8": Decimal("12"),
}


@dataclass(frozen=True)
class EstimatedFuelDemand:
    gallons: int | None
    effective_inbound_lbs: int | None
    source: str
    source_label: str


@dataclass(frozen=True)
class TruckProjection:
    gallons: int | None
    short: bool


@dataclass(frozen=True)
class AssignmentMissionTiming:
    available: bool
    aircraft_type: str | None
    planning_demand_gallons: Decimal | None
    setup_minutes: Decimal | None
    pump_rate_gallons_per_minute: Decimal | None
    pump_minutes: Decimal | None
    finishing_minutes: Decimal | None
    total_duration_minutes: Decimal | None
    aircraft_ready_utc: datetime | None
    aircraft_ready_source: str | None
    fuel_complete_deadline_utc: datetime | None
    earliest_possible_finish_utc: datetime | None
    deadline_feasible: bool | None
    unavailable_reason: str | None


def assignment_mission_timing(
    *,
    mission,
    operation,
    aircraft_type,
    planning_demand_gallons,
    planning_settings,
):
    """Derive mission timing facts from already-loaded operational planning data."""
    demand = _decimal_or_none(planning_demand_gallons)
    setup_minutes = _decimal_or_none(getattr(planning_settings, "setup_minutes", None))
    finishing_minutes = _decimal_or_none(
        getattr(planning_settings, "finishing_minutes", None)
    )
    pump_rate = _decimal_or_none(
        planning_settings.pump_rate_for(aircraft_type)
        if aircraft_type in FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES
        else None
    )
    eta_safety_buffer_minutes = _decimal_or_none(
        getattr(planning_settings, "eta_safety_buffer_minutes", None)
    )
    base = {
        "aircraft_type": aircraft_type,
        "planning_demand_gallons": demand,
        "setup_minutes": setup_minutes,
        "pump_rate_gallons_per_minute": pump_rate,
        "finishing_minutes": finishing_minutes,
    }
    if aircraft_type not in FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES:
        return _timing_unavailable(base, "unsupported_aircraft")
    if (
        not planning_settings.is_complete_for(aircraft_type)
        or eta_safety_buffer_minutes is None
    ):
        return _timing_unavailable(base, "planning_settings_incomplete")
    if demand is None:
        return _timing_unavailable(base, "fuel_demand_unavailable")

    actual_block_in = getattr(mission, "actual_block_in_datetime_utc", None)
    eta = getattr(mission, "eta_datetime_utc", None)
    if actual_block_in is not None:
        aircraft_ready_utc = actual_block_in
        aircraft_ready_source = "actual_block_in"
    elif eta is not None:
        aircraft_ready_utc = eta + _decimal_timedelta(
            eta_safety_buffer_minutes
        )
        aircraft_ready_source = "eta_plus_buffer"
    else:
        return _timing_unavailable(base, "arrival_timing_unavailable")

    departure = getattr(mission, "planned_datetime_utc", None)
    if departure is None:
        return _timing_unavailable(base, "departure_timing_unavailable")

    pump_minutes = abs(demand) / pump_rate
    total_duration_minutes = setup_minutes + pump_minutes + finishing_minutes
    fuel_complete_deadline_utc = departure + _decimal_timedelta(
        Decimal(getattr(operation, "window_minutes", 0) or 0)
        - FUEL_COMPLETE_DEADLINE_OFFSETS_MINUTES[aircraft_type]
    )
    earliest_possible_finish_utc = aircraft_ready_utc + _decimal_timedelta(
        total_duration_minutes
    )
    return AssignmentMissionTiming(
        available=True,
        **base,
        pump_minutes=pump_minutes,
        total_duration_minutes=total_duration_minutes,
        aircraft_ready_utc=aircraft_ready_utc,
        aircraft_ready_source=aircraft_ready_source,
        fuel_complete_deadline_utc=fuel_complete_deadline_utc,
        earliest_possible_finish_utc=earliest_possible_finish_utc,
        deadline_feasible=earliest_possible_finish_utc <= fuel_complete_deadline_utc,
        unavailable_reason=None,
    )


def _timing_unavailable(base, reason):
    return AssignmentMissionTiming(
        available=False,
        **base,
        pump_minutes=None,
        total_duration_minutes=None,
        aircraft_ready_utc=None,
        aircraft_ready_source=None,
        fuel_complete_deadline_utc=None,
        earliest_possible_finish_utc=None,
        deadline_feasible=None,
        unavailable_reason=reason,
    )


def _decimal_timedelta(minutes):
    microseconds = (
        Decimal(minutes) * Decimal("60") * Decimal("1000000")
    ).to_integral_value(rounding=ROUND_HALF_UP)
    return timedelta(microseconds=int(microseconds))


def estimate_fuel_demand_gallons(
    required_fuel_lbs,
    inbound_fuel_lbs,
    fuel_density_lbs_per_gallon,
    *,
    measured_fob_lbs=None,
    fallback_inbound_lbs=DEFAULT_PLANNING_INBOUND_FALLBACK_LBS,
):
    """Return a whole-gallon estimate without mutating any persisted state."""
    required = _decimal_or_none(required_fuel_lbs)
    density = _decimal_or_none(fuel_density_lbs_per_gallon)
    if required is None or density is None or density <= 0:
        return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")

    measured_fob = _decimal_or_none(measured_fob_lbs)
    if measured_fob is not None:
        if measured_fob < 0:
            return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
        effective_inbound = measured_fob
        source = "measured_fob"
        source_label = ""
    else:
        inbound = _decimal_or_none(inbound_fuel_lbs)
        if inbound is not None:
            if inbound < 0:
                return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
            effective_inbound = inbound
            source = "inbound"
            source_label = ""
        else:
            effective_inbound = _decimal_or_none(fallback_inbound_lbs)
            if effective_inbound is None or effective_inbound < 0:
                return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
            source = "fallback"
            source_label = (
                f"{effective_inbound / Decimal('1000'):.1f}K ASSUMED INBOUND"
            )

    if effective_inbound is None:
        return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")

    fuel_needed_lbs = max(Decimal("0"), required - effective_inbound)
    gallons = int(
        (fuel_needed_lbs / density).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return EstimatedFuelDemand(
        gallons,
        int(effective_inbound),
        source,
        source_label,
    )


def project_truck_remaining(current_gallons_by_truck_id, ordered_demands):
    """Project remaining gallons for demand rows already ordered by departure."""
    remaining = {
        truck_id: _int_or_none(gallons)
        for truck_id, gallons in current_gallons_by_truck_id.items()
    }
    incomplete_trucks = set()
    projections = {}
    for row_key, truck_id, demand_gallons in ordered_demands:
        if truck_id is None:
            projections[row_key] = TruckProjection(None, False)
            continue
        demand = _int_or_none(demand_gallons)
        current = remaining.get(truck_id)
        if truck_id in incomplete_trucks or current is None or demand is None:
            incomplete_trucks.add(truck_id)
            projections[row_key] = TruckProjection(None, False)
            continue
        current -= demand
        remaining[truck_id] = current
        projections[row_key] = TruckProjection(current, current < 0)
    return projections


def _decimal_or_none(value):
    if value is None or isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int_or_none(value):
    parsed = _decimal_or_none(value)
    if parsed is None:
        return None
    return int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
