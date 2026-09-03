"""Pure, versioned SPEAR learning contracts; intentionally no persistence."""

from dataclasses import asdict, dataclass
from datetime import datetime


SPEAR_LEARNING_PAYLOAD_VERSION = "spear-learning/v1"
SPEAR_ALGORITHM_VERSION = "spear-v1"

TEACH_SPEAR_REASON_CODES = (
    "aircraft_not_ready", "better_truck_location", "better_fueler_location",
    "truck_fuel_concern", "truck_needs_top_off", "truck_equipment_issue",
    "fueler_unavailable", "fueler_issue", "ramp_congestion_access_issue",
    "different_mission_more_urgent", "upcoming_work_bad_truck_choice",
    "fuel_load_changed", "spear_data_wrong", "operational_judgment",
    "recommendation_acceptable_chose_differently", "other",
)
READY_FOR_FUEL_DELAY_REASONS = (
    "aircraft_not_ready", "ramp_congestion", "equipment_obstruction",
    "fuel_panel_access_issue", "truck_issue", "fueler_unavailable",
    "waiting_for_updated_fuel_load", "crew_request_change", "safety_hold",
    "weather", "other",
)


@dataclass(frozen=True)
class LocationProvenance:
    location: str | None
    recorded_at_utc: datetime | None
    source: str
    confidence: str

    def to_dict(self):
        payload = asdict(self)
        value = payload["recorded_at_utc"]
        payload["recorded_at_utc"] = value.isoformat() if value else None
        return payload


def build_learning_recommendation_payload(
    *,
    captured_at_utc,
    gateway_id,
    operation_id,
    mission_id,
    recommendation_token,
    soft_priority_order,
    recommendation,
    mission_facts=None,
    candidate_trucks=(),
    candidate_fuelers=(),
    fleet_context=None,
):
    """Create a deterministic, provider-neutral recommendation snapshot.

    Callers supply canonical facts already loaded for planning; this function
    deliberately has no database, filesystem, or network side effect.
    """
    facts = dict(mission_facts or {})
    step = _serialize(recommendation)
    return {
        "schema_version": SPEAR_LEARNING_PAYLOAD_VERSION,
        "record_type": "recommendation_snapshot",
        "captured_at_utc": _timestamp(captured_at_utc),
        "gateway_id": gateway_id,
        "sort_date_operation_id": operation_id,
        "mission_id": mission_id,
        "recommendation": {
            "token": recommendation_token,
            "algorithm_version": SPEAR_ALGORITHM_VERSION,
            "soft_priority_order": list(soft_priority_order),
            "selected": step,
            "ranked_alternatives": list(facts.pop("ranked_alternatives", ())),
        },
        "mission": facts,
        "candidates": {
            "trucks": [_serialize(item) for item in candidate_trucks],
            "fuelers": [_serialize(item) for item in candidate_fuelers],
        },
        "fleet_context": dict(fleet_context or {}),
        "outcome_contract": {
            "ready_for_fuel_at_utc": None,
            "delay_reason": None,
            "actual_truck_travel_minutes": None,
            "actual_fueler_travel_minutes": None,
            "actual_setup_minutes": None,
            "actual_gallons": None,
            "actual_pump_minutes": None,
            "actual_wrap_up_minutes": None,
            "actual_top_off_minutes": None,
            "actual_completion_at_utc": None,
            "final_risk_result": None,
            "override_comparison": None,
        },
    }


def build_dispatcher_feedback_payload(
    *,
    captured_at_utc,
    recommendation_payload,
    outcome,
    reason_code=None,
    reason_details=None,
    note=None,
    selected_resources=None,
):
    """Build a future feedback contract without deciding that it is correct."""
    if reason_code is not None and reason_code not in TEACH_SPEAR_REASON_CODES:
        raise ValueError("Unknown SPEAR feedback reason.")
    return {
        "schema_version": SPEAR_LEARNING_PAYLOAD_VERSION,
        "record_type": "dispatcher_feedback",
        "captured_at_utc": _timestamp(captured_at_utc),
        "recommendation_token": recommendation_payload.get("recommendation", {}).get("token"),
        "outcome": outcome,
        "reason_code": reason_code,
        "reason_details": dict(reason_details or {}),
        "note": (note or "").strip() or None,
        "selected_resources": dict(selected_resources or {}),
    }


def _serialize(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


def _timestamp(value):
    return value.isoformat() if isinstance(value, datetime) else str(value)
