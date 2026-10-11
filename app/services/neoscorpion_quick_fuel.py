"""Read-only, assignment-free Fueler calculation for Quick Fuel."""

from datetime import datetime, timezone
from decimal import InvalidOperation
import re
from zoneinfo import ZoneInfo

from app.models import NeoScorpionAircraftFuelSetting, NeoScorpionSettings
from app.services.gateway_matrix import gateway_timezone
from app.services.neoscorpion import (
    DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR,
    DEFAULT_FUEL_DENSITY_LBS_PER_GALLON,
    NEOSCORPION_TANK_LAYOUTS,
    calculate_apu_allowance_lbs,
    detailed_aircraft_type_for_tail,
    display_thousands_to_lbs,
    format_dispatch_thousands,
)
from app.services.neoscorpion_dispatch_planning import estimate_fuel_demand_gallons
from app.services.neoscorpion_fuel_planning import plan_fuel_by_tank


def _reading(form, name, issues):
    try:
        return display_thousands_to_lbs(form.get(name))
    except ValueError:
        issues.append(f"{name.replace('_', ' ').title()} must be a nonnegative K-LB value.")
        return None


def _departure_utc(value, gateway, issues):
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
        if local.tzinfo is not None:
            raise ValueError
        zone = ZoneInfo(gateway_timezone(gateway))
        candidate = local.replace(tzinfo=zone)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
            raise ValueError
        return candidate.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        issues.append("Enter a valid gateway-local departure date and time.")
        return None


def calculate_quick_fuel(gateway, form, *, now_utc=None):
    """Return only derived values; never create operational records."""
    now_utc = now_utc or datetime.utcnow()
    tail = (form.get("tail_number") or "").strip().upper()
    aircraft_type = detailed_aircraft_type_for_tail(tail)
    if not re.fullmatch(r"[A-Z0-9-]{3,12}", tail) or aircraft_type not in NEOSCORPION_TANK_LAYOUTS:
        return {
            "ok": False, "tail": tail, "aircraft_type": None, "tank_rows": [],
            "issues": ["Enter a supported aircraft tail number."], "totals": {},
        }

    layout = NEOSCORPION_TANK_LAYOUTS[aircraft_type]
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    aircraft_setting = NeoScorpionAircraftFuelSetting.query.filter_by(
        gateway_id=gateway.id, aircraft_type=aircraft_type,
    ).first()
    density = (settings.fuel_density_lbs_per_gallon if settings
               else DEFAULT_FUEL_DENSITY_LBS_PER_GALLON)
    rate = (aircraft_setting.apu_rate_thousand_lbs_per_hour if aircraft_setting
            else DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR)
    lateral_limit = (aircraft_setting.max_lateral_imbalance_lbs
                     if aircraft_setting else None)
    issues = []
    required = _reading(form, "required_fuel", issues)
    remaining = {code: _reading(form, f"remaining_{code}", issues) for code, _ in layout}
    actual = {code: _reading(form, f"actual_{code}", issues) for code, _ in layout}
    apu_choice = form.get("apu_running")
    apu_running = True if apu_choice == "yes" else False if apu_choice == "no" else None
    source = form.get("apu_source_tank_code") or ""
    manual = form.get("apu_override_enabled") == "1"
    departure = _departure_utc(form.get("departure_local"), gateway, issues)
    automatic_apu = None
    allowance = None
    if apu_running is False:
        allowance = 0
        automatic_apu = 0
    elif apu_running is True:
        if source not in remaining:
            issues.append("Select the APU source tank.")
        if departure is not None:
            if departure <= now_utc:
                issues.append("Departure has passed; enter a future time or apply a manual APU allowance.")
            else:
                automatic_apu = calculate_apu_allowance_lbs(departure, 0, now_utc, rate)
        if manual:
            allowance = _reading(form, "apu_override_allowance", issues)
            if allowance is None:
                issues.append("Enter an explicit manual APU allowance.")
        else:
            allowance = automatic_apu
            if departure is None:
                issues.append("Enter departure time or apply a manual APU allowance.")
    else:
        issues.append("Confirm whether the APU is running.")

    remaining_complete = all(value is not None for value in remaining.values())
    actual_complete = all(value is not None for value in actual.values())
    remaining_total = sum(remaining.values()) if remaining_complete else None
    actual_total = sum(actual.values()) if actual_complete else None
    if required is None:
        issues.append("Enter Required Fuel Load.")
    if not remaining_complete:
        issues.append("Enter all Remaining tank readings to calculate Planned fuel.")
    planned = (
        plan_fuel_by_tank(
            aircraft_type, required,
            remaining_lbs_by_tank=remaining,
            actual_lbs_by_tank=actual,
            apu_running=apu_running,
            apu_allowance_lbs=allowance,
            apu_source_tank_code=source,
            max_lateral_imbalance_lbs=lateral_limit,
        )
        if required is not None and remaining_complete and allowance is not None
        and (apu_running is False or source in remaining)
        else None
    )
    if planned is not None and any(value < 0 for value in planned.values()):
        planned = None
        issues.append("Fuel load cannot produce a valid tank distribution.")
    planned_total = sum(planned.values()) if planned is not None else None
    fuel_load = required + allowance if required is not None and allowance is not None else None
    uplift = max(0, fuel_load - remaining_total) if fuel_load is not None and remaining_total is not None else None
    neo_fuel = actual_total - allowance if actual_total is not None and allowance is not None else None
    try:
        estimate = estimate_fuel_demand_gallons(
            fuel_load, None, density, measured_fob_lbs=remaining_total,
            fallback_inbound_lbs=None,
        ).gallons if remaining_total is not None else None
    except (ValueError, InvalidOperation, TypeError):
        estimate = None
    if estimate is None and uplift is not None:
        issues.append("Configure a valid fuel density to estimate gallons.")
    transfer_text = (form.get("transfer_fuel_gallons") or "").strip()
    transfer = int(transfer_text) if re.fullmatch(r"\d+", transfer_text) else None
    if transfer_text and transfer is None:
        issues.append("Transfer Fuel must be whole gallons.")

    return {
        "ok": True, "tail": tail, "aircraft_type": aircraft_type,
        "tank_rows": [
            {"code": code, "label": label,
             "planned": format_dispatch_thousands(planned[code]) if planned is not None else None}
            for code, label in layout
        ],
        "apu": {
            "automatic": format_dispatch_thousands(automatic_apu) if automatic_apu is not None else None,
            "allowance": format_dispatch_thousands(allowance) if allowance is not None else None,
            "source": source if apu_running else None,
            "rate": str(rate),
        },
        "totals": {
            "required": format_dispatch_thousands(required) if required is not None else None,
            "remaining": format_dispatch_thousands(remaining_total) if remaining_total is not None else None,
            "planned": format_dispatch_thousands(planned_total) if planned_total is not None else None,
            "actual": format_dispatch_thousands(actual_total) if actual_total is not None else None,
            "fuel_load": format_dispatch_thousands(fuel_load) if fuel_load is not None else None,
            "uplift": format_dispatch_thousands(uplift) if uplift is not None else None,
            "neo_fuel": format_dispatch_thousands(neo_fuel) if neo_fuel is not None else None,
            "estimated_gallons": estimate,
            "transfer_gallons": transfer,
        },
        "issues": list(dict.fromkeys(issues)),
    }
