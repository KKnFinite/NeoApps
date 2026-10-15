"""Bounded, current-sort SPEAR timing calibration.

This intentionally derives from canonical completed work on each request.  It
does not retain a training-event stream and it never changes configured
planning settings.  A reset is only a small cutoff marker for the current sort.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from sqlalchemy.orm import joinedload
from app.services.neoscorpion_prof import event_math, minutes as timeline_minutes, timestamp

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionSpearCalibrationReset,
)


CALIBRATION_SCHEMA_VERSION = "v1"
SPEAR_ALGORITHM_VERSION = "spear-v4-resource-aware"
MINIMUM_ACTIVE_SAMPLES = 3
BASELINE_WEIGHT = Decimal("3")
METRICS = (
    "pump_rate",
    "setup_minutes",
    "finishing_minutes",
    "ready_delay_minutes",
    "truck_travel_minutes",
    "fueler_travel_minutes",
    "top_off_minutes",
    "service_minutes", "actual_wrapup_minutes", "assignment_lead_minutes",
    "preparation_minutes", "off_prediction_error_minutes",
)


@dataclass(frozen=True)
class CalibrationObservation:
    metric: str
    scope_key: str
    value: Decimal
    observed_at_utc: datetime
    assignment_id: int | None
    mission_id: int | None
    source_label: str
    excluded_reason: str | None = None


@dataclass(frozen=True)
class LiveCalibration:
    metric: str
    scope_key: str
    configured: Decimal | None
    observed: Decimal | None
    effective: Decimal | None
    samples: int
    excluded_samples: int
    first_observation_utc: datetime | None
    most_recent_observation_utc: datetime | None
    observations: tuple[CalibrationObservation, ...]
    excluded_observations: tuple[CalibrationObservation, ...]

    @property
    def active(self):
        return self.samples >= MINIMUM_ACTIVE_SAMPLES and self.effective is not None

    @property
    def confidence(self):
        if self.samples < MINIMUM_ACTIVE_SAMPLES:
            return "COLLECTING"
        if self.samples <= 5:
            return "LOW"
        if self.samples <= 9:
            return "MEDIUM"
        return "HIGH"

    @property
    def explanation(self):
        if not self.active:
            return "Collecting current-sort operational observations."
        return "Configured baseline blended with current-sort qualifying work."

    def recommendation_using(self, mode):
        return self.effective if mode == "apply" and self.active else self.configured


def blended_estimate(configured, observations):
    """Return the transparent baseline-as-three-observations blend."""
    baseline = _decimal(configured)
    values = tuple(_decimal(item) for item in observations if _decimal(item) is not None)
    if baseline is None or len(values) < MINIMUM_ACTIVE_SAMPLES:
        return baseline
    return (baseline * BASELINE_WEIGHT + sum(values)) / (BASELINE_WEIGHT + len(values))


def build_live_calibration(operation, planning_settings, rows):
    """Build the full current-sort calibration snapshot with bounded queries."""
    if operation is None:
        return {}
    row_by_assignment_id = {
        getattr(row.get("assignment"), "id", None): row
        for row in rows
        if row.get("assignment") is not None
    }
    observations = _canonical_observations(operation.id, row_by_assignment_id)
    reset_cutoffs = {
        (item.metric, item.scope_key): item.observed_after_utc
        for item in NeoScorpionSpearCalibrationReset.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }
    grouped = {}
    for observation in observations:
        grouped.setdefault((observation.metric, observation.scope_key), []).append(observation)

    calibrations = {}
    for (metric, scope_key), items in grouped.items():
        cutoff = reset_cutoffs.get((metric, scope_key))
        eligible = tuple(
            item for item in items
            if item.excluded_reason is None and (
                cutoff is None
                or _utc_naive(item.observed_at_utc) > _utc_naive(cutoff)
            )
        )
        excluded = tuple(item for item in items if item.excluded_reason is not None)
        configured = _configured_baseline(metric, scope_key, planning_settings)
        observed = (sum(item.value for item in eligible) / len(eligible)) if eligible else None
        calibrations[(metric, scope_key)] = LiveCalibration(
            metric=metric,
            scope_key=scope_key,
            configured=configured,
            observed=observed,
            effective=blended_estimate(configured, (item.value for item in eligible)),
            samples=len(eligible),
            excluded_samples=len(excluded),
            first_observation_utc=min((item.observed_at_utc for item in eligible), default=None),
            most_recent_observation_utc=max((item.observed_at_utc for item in eligible), default=None),
            observations=eligible,
            excluded_observations=excluded,
        )
    return calibrations


def calibrated_planning_settings(planning_settings, calibrations, *, mode="observe"):
    """Observe keeps configured planning; apply uses only qualifying active scopes."""
    if mode != "apply" or not calibrations:
        return planning_settings
    pump_rates = dict(planning_settings.pump_rates_gallons_per_minute)
    setup = planning_settings.setup_minutes
    finishing = planning_settings.finishing_minutes
    for aircraft_type, baseline in tuple(pump_rates.items()):
        pump_rates[aircraft_type] = _active_value(
            calibrations, "pump_rate", aircraft_type, baseline
        )
    proxy = SimpleNamespace(
        setup_minutes=setup,
        finishing_minutes=finishing,
        eta_safety_buffer_minutes=planning_settings.eta_safety_buffer_minutes,
        pump_rates_gallons_per_minute=pump_rates,
    )
    proxy.pump_rate_for = lambda aircraft_type: pump_rates.get(aircraft_type)
    proxy.setup_for = lambda aircraft_type: _active_value(
        calibrations, "setup_minutes", aircraft_type, planning_settings.setup_minutes
    )
    proxy.finishing_for = lambda aircraft_type: _active_value(
        calibrations, "finishing_minutes", aircraft_type, planning_settings.finishing_minutes
    )
    proxy.is_complete_for = planning_settings.is_complete_for
    return proxy


def calibration_summary(calibrations):
    active = tuple(item for item in calibrations.values() if item.active)
    collecting = tuple(item for item in calibrations.values() if not item.active)
    return {
        "active_count": len(active),
        "collecting_count": len(collecting),
        "label": f"SPEAR CALIBRATION · {len(active)} ACTIVE" if active else "SPEAR CALIBRATION · COLLECTING",
        "items": tuple(sorted(calibrations.values(), key=lambda item: (item.metric, item.scope_key))),
    }


def reset_live_calibration(operation, metric, scope_key, user, *, now_utc=None):
    if metric not in METRICS:
        raise ValueError("Unknown SPEAR calibration metric.")
    now_utc = now_utc or datetime.utcnow()
    marker = NeoScorpionSpearCalibrationReset.query.filter_by(
        sort_date_operation_id=operation.id, metric=metric, scope_key=scope_key
    ).with_for_update().first()
    if marker is None:
        marker = NeoScorpionSpearCalibrationReset(
            sort_date_operation_id=operation.id, metric=metric, scope_key=scope_key,
            observed_after_utc=now_utc, reset_by_user_id=getattr(user, "id", None),
        )
        db.session.add(marker)
    else:
        marker.observed_after_utc = now_utc
        marker.reset_by_user_id = getattr(user, "id", None)
    db.session.flush()
    return marker


def calibration_review_payload(operation, calibrations):
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "spear_algorithm_version": SPEAR_ALGORITHM_VERSION,
        "capture_mode": "live_calibration_review",
        "operation_id": getattr(operation, "id", None),
        "training_eligible": False,
        "calibrations": [
            {
                "metric": item.metric, "scope": item.scope_key,
                "configured": _display_decimal(item.configured),
                "observed": _display_decimal(item.observed),
                "effective": _display_decimal(item.effective),
                "samples": item.samples, "excluded_samples": item.excluded_samples,
                "confidence": item.confidence,
            }
            for item in calibration_summary(calibrations)["items"]
        ],
    }


def _canonical_observations(operation_id, row_by_assignment_id):
    events = (
        NeoScorpionFuelingEvent.query.join(NeoScorpionFuelAssignment)
        .options(joinedload(NeoScorpionFuelingEvent.fuel_assignment))
        .filter(
            NeoScorpionFuelingEvent.sort_date_operation_id == operation_id,
            NeoScorpionFuelingEvent.started_at_utc.is_not(None),
            NeoScorpionFuelingEvent.ended_at_utc.is_not(None),
        )
        .order_by(NeoScorpionFuelingEvent.ended_at_utc.asc())
        .limit(5000)
        .all()
    )
    output = []
    for event in events:
        row = row_by_assignment_id.get(event.fuel_assignment_id)
        if row is None or event.event_type == "defuel":
            continue
        assignment = event.fuel_assignment
        observed_at = _utc_naive(event.ended_at_utc)
        aircraft_type = row.get("detailed_aircraft_type") or "UNKNOWN"
        math = event_math(event)
        exclusion = "; ".join(math["fueler_reasons"]) or None
        if math["actual_minutes"] is not None:
            output.append(CalibrationObservation(
                "service_minutes", aircraft_type, math["actual_minutes"], observed_at,
                assignment.id, assignment.sort_date_mission_id, "Physical ON to OFF service (not mechanical pump rate)", exclusion))
        resources=math['facts'].get('resource_timing') or {}
        ready = (resources.get('fueler_available_preparation_at') if math['facts'].get('version')==4 else
                 getattr(assignment, "ready_for_fuel_at_utc", None))
        if isinstance(ready,str):
            ready=timestamp(ready)
        ready = _utc_naive(ready)
        started_at = _utc_naive(event.started_at_utc)
        ended_at = _utc_naive(event.ended_at_utc)
        preparation_end=timestamp(resources.get('preparation_ready_at')) if math['facts'].get('version')==4 else started_at
        if ready and preparation_end and started_at and ready <= preparation_end <= started_at:
            setup_minutes = _minutes_between(ready, preparation_end)
            if setup_minutes is not None:
                output.append(CalibrationObservation(
                    "preparation_minutes", aircraft_type, setup_minutes, observed_at,
                    assignment.id, assignment.sort_date_mission_id, "Available preparation opportunity to recorded readiness (not measured active labor)" if math['facts'].get('version')==4 else "Ready for Fuel to fuel start",
                    exclusion,
                ))
        if math["wrap_minutes"] is not None:
            output.append(CalibrationObservation("actual_wrapup_minutes", aircraft_type,
                math["wrap_minutes"], math["actuals_at"] or observed_at, assignment.id,
                assignment.sort_date_mission_id, "OFF to final valid Actual readings; separate from administrative delay", exclusion))
        lead = (timeline_minutes(math['facts'].get('fueler_assigned_at'),math['facts'].get('truck_assigned_at'))
                if math['facts'].get('version')==4 else _decimal(math["facts"].get("lead_minutes")))
        if lead is not None:
            output.append(CalibrationObservation("assignment_lead_minutes", aircraft_type,
                lead, observed_at, assignment.id, assignment.sort_date_mission_id,
                "Recorded assignment lead; policy allowance is not observed preparation", exclusion))
        for forecast in math["facts"].get("forecasts", []):
            error = timeline_minutes(forecast.get("likely"), event.ended_at_utc)
            if error is not None:
                output.append(CalibrationObservation("off_prediction_error_minutes", aircraft_type,
                    error, observed_at, assignment.id, assignment.sort_date_mission_id,
                    "Immutable prediction known before OFF versus observed physical OFF", exclusion))
    return output


def _configured_baseline(metric, scope_key, settings):
    if metric == "pump_rate":
        return _decimal(settings.pump_rate_for(scope_key))
    if metric == "setup_minutes":
        return _decimal(settings.setup_minutes)
    if metric == "finishing_minutes":
        return _decimal(settings.finishing_minutes)
    if metric == "ready_delay_minutes":
        return _decimal(settings.eta_safety_buffer_minutes)
    return None


def _active_value(calibrations, metric, scope_key, baseline):
    candidate = calibrations.get((metric, scope_key))
    return candidate.effective if candidate and candidate.active else baseline


def _minutes_between(start, end):
    if start is None or end is None:
        return None
    value = Decimal(str((
        _utc_naive(end) - _utc_naive(start)
    ).total_seconds())) / Decimal("60")
    return value if value >= 0 else None


def _utc_naive(value):
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _decimal(value):
    if value is None:
        return None
    return Decimal(str(value))


def _display_decimal(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
