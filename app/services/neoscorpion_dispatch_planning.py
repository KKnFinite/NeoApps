"""Pure dispatcher fuel-demand and nightly truck projection calculations."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


DEFAULT_PLANNING_INBOUND_FALLBACK_LBS = 12_000


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


def estimate_fuel_demand_gallons(
    required_fuel_lbs,
    inbound_fuel_lbs,
    fuel_density_lbs_per_gallon,
    *,
    fallback_inbound_lbs=DEFAULT_PLANNING_INBOUND_FALLBACK_LBS,
):
    """Return a whole-gallon estimate without mutating any persisted state."""
    required = _decimal_or_none(required_fuel_lbs)
    density = _decimal_or_none(fuel_density_lbs_per_gallon)
    if required is None or density is None or density <= 0:
        return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")

    inbound = _decimal_or_none(inbound_fuel_lbs)
    if inbound is None:
        inbound = _decimal_or_none(fallback_inbound_lbs)
        if inbound is None or inbound < 0:
            return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
        source = "fallback"
        source_label = f"{inbound / Decimal('1000'):.1f}K ASSUMPTION"
    else:
        if inbound < 0:
            return EstimatedFuelDemand(None, None, "incomplete", "INCOMPLETE")
        source = "actual_inbound"
        source_label = "ACTUAL INBOUND"

    fuel_needed_lbs = max(Decimal("0"), required - inbound)
    gallons = int(
        (fuel_needed_lbs / density).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return EstimatedFuelDemand(gallons, int(inbound), source, source_label)


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
