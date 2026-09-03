"""Bounded, current-sort SPEAR timing calibration.

This intentionally derives from canonical completed work on each request.  It
does not retain a training-event stream and it never changes configured
planning settings.  A reset is only a small cutoff marker for the current sort.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionSpearCalibrationReset,
)


CALIBRATION_SCHEMA_VERSION = "v1"
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
            if item.excluded_reason is None and (cutoff is None or item.observed_at_utc > cutoff)
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


def calibrated_planning_settings(planning_settings, calibrations):
    """Use calibration only for active scopes; configured values remain fallback."""
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
        .filter(
            NeoScorpionFuelingEvent.sort_date_operation_id == operation_id,
            NeoScorpionFuelingEvent.started_at_utc.is_not(None),
            NeoScorpionFuelingEvent.ended_at_utc.is_not(None),
        )
        .order_by(NeoScorpionFuelingEvent.ended_at_utc.asc())
        .all()
    )
    output = []
    for event in events:
        row = row_by_assignment_id.get(event.fuel_assignment_id)
        if row is None or event.event_type == "defuel":
            continue
        assignment = event.fuel_assignment
        observed_at = event.ended_at_utc
        aircraft_type = row.get("detailed_aircraft_type") or "UNKNOWN"
        exclusion = getattr(assignment, "hold_reason", None) or None
        if event.transfer_fuel_gallons and event.transfer_fuel_gallons > 0:
            minutes = _minutes_between(event.started_at_utc, event.ended_at_utc)
            if minutes and minutes > 0:
                output.append(CalibrationObservation(
                    "pump_rate", aircraft_type,
                    Decimal(event.transfer_fuel_gallons) / minutes, observed_at,
                    assignment.id, assignment.sort_date_mission_id, "Completed fuel event",
                    exclusion,
                ))
        ready = getattr(assignment, "ready_for_fuel_at_utc", None)
        if ready and event.started_at_utc >= ready:
            setup_minutes = _minutes_between(ready, event.started_at_utc)
            if setup_minutes is not None:
                output.append(CalibrationObservation(
                    "setup_minutes", aircraft_type, setup_minutes, observed_at,
                    assignment.id, assignment.sort_date_mission_id, "Ready for Fuel to fuel start",
                    exclusion,
                ))
        completed = getattr(assignment, "completed_at_utc", None)
        if completed and completed >= event.ended_at_utc:
            wrap_minutes = _minutes_between(event.ended_at_utc, completed)
            if wrap_minutes is not None:
                output.append(CalibrationObservation(
                    "finishing_minutes", aircraft_type, wrap_minutes, completed,
                    assignment.id, assignment.sort_date_mission_id, "Fuel end to completion",
                    exclusion,
                ))
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
    value = Decimal(str((end - start).total_seconds())) / Decimal("60")
    return value if value >= 0 else None


def _decimal(value):
    if value is None:
        return None
    return Decimal(str(value))


def _display_decimal(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
