"""Dispatcher-only FOB excess advisory. No operational effects or holds."""
import hashlib
import json
from datetime import datetime

from app.extensions import db
from app.models import NeoScorpionCallDispatchAck, NeoScorpionSettings, SortDateMission
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change


class CallDispatchConflict(ValueError):
    pass


def call_dispatch_alert(row, settings):
    from app.services.neoscorpion import neo_fuel_excess_alert, DEFAULT_FUEL_DENSITY_LBS_PER_GALLON
    if row["administratively_complete"] or not (row.get("fob_likely") or row["fuel_on_board_ready"]):
        return None
    density = getattr(settings, "fuel_density_lbs_per_gallon", DEFAULT_FUEL_DENSITY_LBS_PER_GALLON)
    threshold = getattr(settings, "neo_fuel_excess_alert_gallons", 500)
    if not neo_fuel_excess_alert(row.get("fob_projected_neo_lbs"), row["required_fuel_lbs"], density, threshold):
        return None
    work = row["fuel_work_state"]
    values = {
        "mission_id": row["mission"].id, "cycle_number": row["cycle_number"],
        "available_lbs": row["fob_available_lbs"], "required_lbs": row["required_fuel_lbs"],
        "apu_allowance_lbs": row["fob_apu_allowance_lbs"],
        "apu_running": work.apu_running if work else None,
        "apu_source": work.apu_source_tank_code if work else None,
        "apu_override_enabled": bool(work and work.apu_override_enabled),
        "apu_override_lbs": work.apu_override_allowance_lbs if work else None,
        "trusted_event_id": row.get("tail_swap_inherited_event_id"),
        "density": str(density), "threshold_gallons": threshold,
    }
    fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"fingerprint": fingerprint, "values": values}


def attach_call_dispatch_alerts(rows, operation, settings):
    latest = {}
    for ack in NeoScorpionCallDispatchAck.query.filter_by(sort_date_operation_id=operation.id).order_by(NeoScorpionCallDispatchAck.id.desc()).all():
        latest.setdefault((ack.mission_id, ack.cycle_number), ack)
    for row in rows:
        alert = call_dispatch_alert(row, settings)
        ack = latest.get((row["mission"].id, row["cycle_number"]))
        row["call_dispatch_alert"] = alert if alert and (ack is None or ack.alert_fingerprint != alert["fingerprint"]) else None


def acknowledge_call_dispatch(gateway, user, form):
    from app.services.neoscorpion import (current_sort_operation, _fuel_rows, _assignments_by_mission,
        _fuel_work_states_by_assignment_tail, _effective_aircraft_fuel_configuration)
    operation = current_sort_operation(gateway)
    if operation is None:
        raise CallDispatchConflict("The current sort has changed. Refresh Dispatch.")
    operation, asset_state = lock_nightly_asset_scope_for_mutation(operation)
    try:
        mission_id, cycle = int(form.get("mission_id", "")), int(form.get("expected_cycle", ""))
    except (TypeError, ValueError):
        raise ValueError("A mission and cycle are required.")
    mission = SortDateMission.query.filter_by(id=mission_id, sort_date_operation_id=operation.id, mission_type="departure").with_for_update().first()
    if mission is None:
        raise CallDispatchConflict("The mission is no longer in the current sort.")
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    assignments = _assignments_by_mission(operation)
    rates, limits = _effective_aircraft_fuel_configuration(gateway.id)
    row = _fuel_rows(operation, [mission], assignments_by_mission=assignments,
                     fuel_work_states_by_assignment_tail=_fuel_work_states_by_assignment_tail(assignments.values()),
                     apu_rates_by_aircraft_type=rates, lateral_imbalance_limits_by_aircraft_type=limits,
                     status_settings=settings)[0]
    alert = call_dispatch_alert(row, settings)
    if cycle != row["cycle_number"] or alert is None or form.get("alert_fingerprint") != alert["fingerprint"]:
        raise CallDispatchConflict("Fuel/APU/Required or alert settings changed. Refresh and review CALL DISPATCH again.")
    latest = NeoScorpionCallDispatchAck.query.filter_by(sort_date_operation_id=operation.id, mission_id=mission_id, cycle_number=cycle).order_by(NeoScorpionCallDispatchAck.id.desc()).first()
    changed = latest is None or latest.alert_fingerprint != alert["fingerprint"]
    if changed:
        db.session.add(NeoScorpionCallDispatchAck(sort_date_operation_id=operation.id, mission_id=mission_id,
            cycle_number=cycle, actor_user_id=user.id, acknowledged_at_utc=datetime.utcnow(),
            alert_fingerprint=alert["fingerprint"], alert_values=alert["values"]))
        asset_state = record_nightly_operational_change(asset_state, operation.id)
        db.session.flush()
    return {"changed": changed, "revision": int(asset_state.revision or 0)}
