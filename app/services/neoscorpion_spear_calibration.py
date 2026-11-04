"""Bounded, gateway-isolated rolling SPEAR timing calibration.

This intentionally derives from canonical completed work on each request.  It
does not retain a training-event stream and it never changes configured
planning settings.  A reset is only a small cutoff marker for the current sort.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, time
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from statistics import median
from flask import current_app, has_app_context
from app.services.neoscorpion_prof import minutes as timeline_minutes, timestamp

from app.extensions import db
from app.models import (
    NeoScorpionSpearCalibrationReset,
)


CALIBRATION_SCHEMA_VERSION = "v1"
SPEAR_ALGORITHM_VERSION = "spear-v4-resource-aware"
MINIMUM_ACTIVE_SAMPLES = 3
LEARNING_DAYS = 30
HALF_LIFE_DAYS = 7
MAX_LEARNING_EVENTS = 2000
MAX_DISPLAY_OBSERVATIONS = 25
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
    "service_ratio",
)


def learning_window(now_utc=None):
    """Latest 30 gateway-local calendar dates, including today; never future data."""
    now = _utc_naive(now_utc or datetime.utcnow())
    zone = ZoneInfo(current_app.config.get('DEFAULT_GATEWAY_TIMEZONE', 'America/Chicago')
                    if has_app_context() else 'UTC')
    today = now.replace(tzinfo=timezone.utc).astimezone(zone).date()
    start = datetime.combine(today-timedelta(days=LEARNING_DAYS-1), time.min, zone)
    return start.astimezone(timezone.utc).replace(tzinfo=None), now


def recency_weight(observed_at, now_utc):
    zone = ZoneInfo(current_app.config.get('DEFAULT_GATEWAY_TIMEZONE', 'America/Chicago')
                    if has_app_context() else 'UTC')
    local_date = lambda value: _utc_naive(value).replace(tzinfo=timezone.utc).astimezone(zone).date()
    age = (local_date(now_utc)-local_date(observed_at)).days
    return Decimal(str(2 ** (-max(0, age)/HALF_LIFE_DAYS)))


def learning_reasons(job, now_utc=None):
    """Canonical completion/participation remain mandatory, even for imported samples."""
    event, facts = job['event'], job['facts']
    reasons = list(job.get('fueler_reasons') or ())
    start, now = learning_window(now_utc)
    ended = timestamp(getattr(event, 'ended_at_utc', None))
    completed = timestamp(facts.get('completed_at'))
    if ended is None or not start <= ended <= now:
        reasons.append('Outside rolling 30 calendar days or future evidence')
    if ended is None or completed is None or not ended <= completed <= now:
        reasons.append('Completed operation provenance unavailable')
    actuals=timestamp(facts.get('actuals_received_at'))
    if actuals is None or completed is None or actuals>completed:
        reasons.append('Final actual-measurement chronology unavailable')
    if str(facts.get('learning_origin', 'production')).lower() != 'production' or any(
            str(facts.get(key)).lower() in ('true','1') for key in ('test_data','synthetic','simulation')):
        reasons.append('Test/simulated evidence')
    if str(facts.get('training_eligible')).lower() in ('false','0'):
        reasons.append('Explicitly ineligible evidence')
    if getattr(event, 'event_type', None) == 'defuel':
        reasons.append('DEFUEL is not a fueling-service calibration sample')
    return tuple(dict.fromkeys(reasons))


def robust_weighted_samples(samples, now_utc, *, floor=Decimal('0.1')):
    """Samples are (value, observed time, relevance weight). No history is edited."""
    start, now = learning_window(now_utc)
    valid = [(number, timestamp(at), Decimal(str(weight))) for value, at, weight in samples
        if (number := _decimal(value)) is not None and timestamp(at) is not None
        and start <= timestamp(at) <= now and Decimal(str(weight)) > 0]
    if not valid:
        return (), (), None
    center = median(v for v, _, _ in valid)
    deviation = median(abs(v-center) for v, _, _ in valid)
    band = max(Decimal('4.5')*deviation, abs(center)*Decimal('0.5'), floor)
    kept, rejected = [], []
    for value, at, relevance in valid:
        (kept if len(valid)<3 or abs(value-center)<=band else rejected).append((value, at, relevance))
    denominator = sum(recency_weight(at, now)*w for _, at, w in kept)
    average = sum(value*recency_weight(at, now)*w for value, at, w in kept)/denominator if denominator else None
    return tuple(kept), tuple(rejected), average


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
    reset_after_utc: datetime | None = None

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
        if self.metric in {'truck_travel_minutes', 'fueler_travel_minutes'}:
            return 'Independent measured travel unavailable; configured allowances are not observations.'
        if self.configured is None:
            return 'Review-only observed opportunity/duration; no compatible predictive baseline. History is not active labor or pump-rate evidence.'
        if not self.active:
            return 'Fewer than 3 qualifying observations in the last 30 calendar days; configured baseline retained.'
        return '30 calendar days; 7-day recency half-life, robust outlier exclusion, baseline weight 3.'

    def recommendation_using(self, mode):
        return self.effective if mode == "apply" and self.active else self.configured


def blended_estimate(configured, observations):
    """Return the transparent baseline-as-three-observations blend."""
    baseline = _decimal(configured)
    values = tuple(_decimal(item) for item in observations if _decimal(item) is not None)
    if baseline is None or len(values) < MINIMUM_ACTIVE_SAMPLES:
        return baseline
    return (baseline * BASELINE_WEIGHT + sum(values)) / (BASELINE_WEIGHT + len(values))


def build_live_calibration(operation, planning_settings, rows, *, jobs=None, now_utc=None):
    """One bounded history projection shared with operational OFF forecasts."""
    if operation is None:
        return {}
    now = _utc_naive(now_utc or datetime.utcnow())
    if jobs is None:
        from app.services.neoscorpion_prof import load_observation_jobs
        jobs = load_observation_jobs(operation.gateway_id, learning=True, now_utc=now)
    observations = _canonical_observations(jobs, now)
    reset_cutoffs = {(item.metric, item.scope_key):item.observed_after_utc
        for item in NeoScorpionSpearCalibrationReset.query.filter_by(sort_date_operation_id=operation.id).all()}
    grouped = {}
    for observation in observations:
        grouped.setdefault((observation.metric, observation.scope_key), []).append(observation)
    types = {row.get('detailed_aircraft_type') for row in rows if row.get('detailed_aircraft_type')}
    types.update(scope for _, scope in grouped)
    for kind in types:
        for metric in ('service_ratio','preparation_minutes','actual_wrapup_minutes',
                       'truck_travel_minutes','fueler_travel_minutes'):
            grouped.setdefault((metric,kind), [])
    calibrations = {}
    from dataclasses import replace
    for (metric, scope_key), items in grouped.items():
        cutoff = reset_cutoffs.get((metric, scope_key))
        candidates, excluded = [], []
        for item in items:
            if item.excluded_reason:
                excluded.append(item)
            elif cutoff is not None and item.observed_at_utc <= _utc_naive(cutoff):
                excluded.append(replace(item, excluded_reason='Before current-sort calibration reset'))
            else:
                candidates.append(item)
        kept, outliers, observed = robust_weighted_samples(
            [(item.value,item.observed_at_utc,1) for item in candidates], now,
            floor=Decimal('0.1') if metric=='service_ratio' else Decimal(1))
        rejected = {(value,at) for value,at,_ in outliers}
        eligible = [item for item in candidates if (item.value,item.observed_at_utc) not in rejected]
        excluded.extend(replace(item,excluded_reason='Outlier outside robust median band')
            for item in candidates if (item.value,item.observed_at_utc) in rejected)
        configured = _configured_baseline(metric,scope_key,planning_settings)
        effective = configured
        if configured is not None and len(eligible)>=MINIMUM_ACTIVE_SAMPLES:
            weight = sum(recency_weight(item.observed_at_utc,now) for item in eligible)
            effective = (configured*BASELINE_WEIGHT + sum(item.value*recency_weight(item.observed_at_utc,now)
                for item in eligible))/(BASELINE_WEIGHT+weight)
            if metric=='service_ratio':effective=max(Decimal('0.5'),min(Decimal(2),effective))
            if metric=='actual_wrapup_minutes':effective=max(Decimal(0),min(Decimal(60),effective))
        calibrations[(metric,scope_key)] = LiveCalibration(metric,scope_key,configured,observed,effective,
            len(eligible),len(excluded),min((i.observed_at_utc for i in eligible),default=None),
            max((i.observed_at_utc for i in eligible),default=None),
            tuple(eligible[:MAX_DISPLAY_OBSERVATIONS]),tuple(excluded[:MAX_DISPLAY_OBSERVATIONS]),cutoff)
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
        calibrations, "actual_wrapup_minutes" if ("actual_wrapup_minutes",aircraft_type) in calibrations else "finishing_minutes", aircraft_type, planning_settings.finishing_minutes
    )
    proxy.service_factor_for = lambda aircraft_type: _active_value(calibrations,"service_ratio",aircraft_type,Decimal(1))
    proxy.is_complete_for = planning_settings.is_complete_for
    return proxy


def calibration_summary(calibrations):
    active = tuple(item for item in calibrations.values() if item.active)
    collecting = tuple(item for item in calibrations.values() if not item.active)
    return {
        "active_count": len(active),
        "collecting_count": len(collecting),
        "label": f"SPEAR CALIBRATION · {len(active)} ACTIVE · 30 DAYS" if active else "SPEAR CALIBRATION · COLLECTING · 30 DAYS",
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


def _canonical_observations(jobs, now):
    output, seen = [], set()
    for job in jobs:
        event, facts = job['event'],job['facts']
        identity = getattr(event,'fuel_work_state_id',None)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        at = timestamp(event.ended_at_utc)
        if at is None:continue
        aircraft = facts.get('aircraft_type') or 'UNKNOWN'
        exclusion = '; '.join(learning_reasons(job,now)) or None
        def add(metric,value,label,reason=None):
            value=_decimal(value)
            if value is not None:
                output.append(CalibrationObservation(metric,aircraft,value,at,event.fuel_assignment_id,
                    getattr(event,'sort_date_mission_id',None),label,reason or exclusion))
        add('service_minutes',job['actual_minutes'],'Physical ON to OFF service, not mechanical pump rate')
        expected,actual = _decimal(job['expected_fueler']),_decimal(job['actual_minutes'])
        if expected is not None and expected>0 and actual is not None and actual>0:
            add('service_ratio',actual/expected,'Verified physical service / frozen configured service')
        resources=facts.get('resource_timing') or {}
        available=timestamp(resources.get('fueler_available_preparation_at'))
        prepared=timestamp(resources.get('preparation_ready_at'))
        on=timestamp(event.started_at_utc)
        if facts.get('version')==4 and available and prepared and on and available<=prepared<=on:
            add('preparation_minutes',timeline_minutes(available,prepared),
                'Elapsed available preparation opportunity, not active labor')
        ready=timestamp(facts.get('fueler_work_end_at'))
        completed=timestamp(facts.get('completed_at'))
        if ready and completed and at<=ready<=completed:
            add('actual_wrapup_minutes',timeline_minutes(at,ready),'Physical OFF to validated final Fueler data')
        forecast=facts.get('last_forecast') or {}
        observed=timestamp(forecast.get('observed_at'))
        if observed and on and on<=observed<=at and timestamp(forecast.get('likely')):
            add('off_prediction_error_minutes',timeline_minutes(forecast['likely'],at),
                'Latest immutable pre-OFF forecast error')
    return output


def _configured_baseline(metric, scope_key, settings):
    if metric == "pump_rate":
        return _decimal(settings.pump_rate_for(scope_key))
    if metric == "setup_minutes":
        return _decimal(settings.setup_minutes)
    if metric in {"finishing_minutes", "actual_wrapup_minutes"}:
        return _decimal(settings.finishing_minutes)
    if metric == "service_ratio":
        return Decimal(1)
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
    from app.services.neoscorpion_prof import number
    return number(value)


def _display_decimal(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
