"""Qualitative KRFD air-weather assessment, independent of treatment scores.

Method and reference ranges: docs/audits/2026-11-23-frost-atmospheric-assessment.md.
All temperatures are Celsius internally. No surface temperature is estimated.
"""
import math
from datetime import timedelta

from app.services import frost_analysis, frost_forecast as clock, frost_forecast_native as native
from app.services.frost_schedule import hourly_targets

VERSION = 'krfd-atmospheric-feasibility-v1'
LABELS = {
    'supporting': 'Frost-supporting weather',
    'possible': 'Patchy frost possible',
    'weak': 'Weak frost support',
    'mixed': 'Frost potential uncertain',
    'unknown': 'Frost assessment unavailable',
}
TYPICAL_C = (36 - 32) / 1.8
PATCHY_C = (42 - 32) / 1.8
REFERENCE_DEPRESSION_C = 6 / 1.8
PATCHY_DEPRESSION_C = 10 / 1.8  # Regional surface-to-screen difference in favorable conditions.
MIXING_KT = 8 / 1.150779448


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def ice_threshold(dewpoint):
    """Return the subfreezing deposition threshold, using WMO Annex 4.B.

    For positive water dew points, saturation at freezing already suffices.
    Zero then represents the freezing boundary, not a calculated frost point.
    """
    if not finite(dewpoint) or not -45 <= dewpoint <= 60:
        return None
    if dewpoint >= 0:
        return 0.0
    log_ratio = 17.62 * dewpoint / (243.12 + dewpoint)
    return 272.62 * log_ratio / (22.46 - log_ratio)


def assess(fields, *, minimum, nws_air, grid_only=False):
    """Classify the target hour; overnight guidance is context, not its temperature."""
    air = fields.get('fcst_temperature_c')
    dew = fields.get('fcst_dewpoint_c')
    rh = fields.get('fcst_relative_humidity')
    cloud = fields.get('fcst_cloud_cover_percent')
    wind = fields.get('fcst_wind_kt')
    observed = fields.get('obs_temperature_c')
    obs_dew = fields.get('obs_dewpoint_c')
    observed_trend = fields.get('obs_cooling_3h_c_per_hour')
    change = fields.get('fcst_temperature_change_c')
    night = fields.get('target_night')
    boundary = ice_threshold(dew)
    facts = dict(air_c=air, dewpoint_c=dew, humidity_pct=rh, wind_kt=wind,
                 cloud_pct=cloud, observed_c=observed, observed_dewpoint_c=obs_dew,
                 observed_change_c_per_hour=observed_trend, forecast_change_c=change,
                 forecast_trend_hours=fields.get('fcst_trend_interval_hours'),
                 night=night, nws_target_c=nws_air, nws_minimum_c=minimum,
                 ice_threshold_c=boundary, frost_point_c=boundary if finite(dew) and dew<=0 else None)

    def result(state, reason):
        return dict(state=state, reason=reason, inputs=facts)

    required = (air, dew, rh, cloud, wind, observed, obs_dew, minimum, boundary)
    if (not all(finite(v) for v in required) or night not in (0, 1)
            or not 0 <= rh <= 100 or not 0 <= cloud <= 100 or wind < 0
            or not -45 <= air <= 60 or dew > air or obs_dew > observed):
        return result('unknown', 'Incomplete or inconsistent temperature, moisture, wind, sky or overnight guidance.')
    depression = max(0, air - boundary)
    facts['required_surface_cooling_c'] = depression
    if grid_only and not (finite(observed_trend) or finite(change)):
        return result('unknown', 'No measured or forecast cooling trend is available.')
    if not grid_only and not all(finite(v) for v in (observed_trend, change, nws_air)):
        return result('mixed', 'A cooling trend or independent hourly forecast is missing.')

    # Qualitative regimes can disagree even when each numerical input is valid.
    # In particular, an observation colder than a warm forecast low deserves
    # attention. Do not use an above-freezing minimum to dismiss that signal.
    if (min(air,nws_air) <= 0 < max(air,nws_air)
            or min(air,nws_air) <= TYPICAL_C and max(air,nws_air) > PATCHY_C
            or observed <= PATCHY_C < minimum and finite(observed_trend) and observed_trend < 0):
        return result('mixed', 'Observed cooling or independent guidance disagrees with the forecast thermal regime.')
    if air <= 0 and air <= boundary:
        # Independent RH representation of the same moisture, not a second vote.
        rh_at_ice_saturation = 100 * math.exp(22.46*air/(272.62+air) - 17.62*air/(243.12+air))
        if rh < rh_at_ice_saturation:
            return result('mixed', 'Humidity and dew point disagree about saturation with respect to ice.')
        return result('supporting', 'Target air is at or below freezing and saturated with respect to ice.')
    radiative = bool(night) and cloud <= 30 and wind < MIXING_KT
    if radiative and air <= TYPICAL_C and depression <= REFERENCE_DEPRESSION_C:
        return result('possible', 'Clear, light-wind nighttime conditions could cool a surface to ice saturation.')
    cooling = any(finite(trend) and trend < 0 for trend in (observed_trend, change))
    if radiative and air <= PATCHY_C and cooling and depression <= PATCHY_DEPRESSION_C:
        return result('possible', 'Cooling in clear, light-wind conditions supports localized frost potential.')
    if min(air,nws_air,minimum,observed) > PATCHY_C:
        return result('weak', 'Target temperatures, observations and the overnight low are above the regional patchy-frost guidance range.')
    if depression > 0 and (cloud >= 70 or wind >= MIXING_KT) and air > 0 and minimum > 0:
        return result('weak', 'Above-freezing guidance and cloud cover or wind limit radiational cooling.')
    if depression > REFERENCE_DEPRESSION_C and (cloud >= 70 or wind >= MIXING_KT):
        return result('weak', 'Dry air requires additional cooling to reach ice saturation while cloud or mixing limits heat loss.')
    return result('mixed', 'Atmospheric indicators are mixed. A surface observation would resolve the uncertainty.')


def for_capture(source, predictions):
    """Snapshot the independent assessment once per horizon, using as-of data only."""
    cutoff = clock.instant(source['cutoff_at_utc'])
    output = {}
    vectors = {p['hours']: p['vector'] for p in predictions if p.get('vector')}
    for h in native.HORIZONS:
        target = cutoff + timedelta(hours=h)
        vector = vectors.get(h)
        if vector is None and source.get('native_forecasts'):
            try:
                snapshots = [r for r in source['native_forecasts'] if clock.instant(r['valid_at_utc']) <= target]
                vector = native.build(source.get('observations', []), snapshots, cutoff=cutoff.isoformat(),
                                      target=target.isoformat(), lead_hours=h)
            except (KeyError, TypeError, ValueError):
                vector = None
        fields = dict(zip(native.FEATURES, vector['features'])) if vector and vector.get('core_available') else {}
        minimum = nws_air = None
        for grid in source.get('nws_forecasts', []):
            if grid.get('hours') != h:
                continue
            try:
                grid_hour = clock.instant(grid['target_at_utc']).replace(minute=0,second=0,microsecond=0)
                if grid_hour != target.replace(minute=0,second=0,microsecond=0) or clock.instant(grid['issued_at_utc']) > cutoff:
                    continue
                nws_air = grid['observation']['temperature_c']
                period = grid.get('period_minimum')
                if period and clock.instant(period['start_utc']) <= target < clock.instant(period['end_utc']):
                    minimum = period['temperature_c']
            except (KeyError, TypeError, ValueError):
                continue
        assessment = assess(fields, minimum=minimum, nws_air=nws_air)
        pair = {p['model_version']: p['score'] for p in predictions
                if p.get('hours') == h and p.get('state') == 'SCORED' and finite(p.get('score'))}
        state = assessment['state']
        output[str(h)] = dict(target_at_utc=target.isoformat(), label=LABELS[state],
                              comparison=comparison(state, pair), **assessment)
    return dict(method=VERSION, horizons=output)


def hourly_for_sort(source, cycle):
    """Assess actual in-window forecast periods using only this capture's source evidence."""
    from app.services.neosubzero_weather import FORECAST_STALE_TTL

    cutoff = clock.instant(source['cutoff_at_utc'])
    history = native.observation_history(source.get('observations', ()), cutoff)
    observation = history['observation'] or {}
    grids = {}
    for grid in (*source.get('nws_forecasts', ()), *source.get('nws_hourly', ())):
        issued = clock.instant(grid['issued_at_utc'])
        if cutoff - FORECAST_STALE_TTL <= issued <= cutoff:
            grids[clock.instant(grid['target_at_utc'])] = grid
    snapshots = source.get('native_forecasts') or []
    hours = []
    prior_grid = None
    for target in hourly_targets(cutoff, cycle.sort_end):
        grid = grids.get(target) or {}
        forecast = grid.get('observation') or {}
        period = grid.get('period_minimum') or {}
        minimum = None
        if period and clock.instant(period['start_utc']) <= target < clock.instant(period['end_utc']):
            minimum = period['temperature_c']
        fields = dict(target_night=int(frost_analysis.solar(target)['night']),
                      obs_temperature_c=observation.get('temperature_c'),
                      obs_dewpoint_c=observation.get('dewpoint_c'),
                      obs_cooling_3h_c_per_hour=history['cooling_rate_3h_c_per_hour'])
        latest = previous = None
        if snapshots:
            try:
                latest, previous = native.forecast_pair(
                    [r for r in snapshots if clock.instant(r['valid_at_utc']) <= target], cutoff, target)
                if cutoff - clock.instant(latest['cycle_at_utc']) > FORECAST_STALE_TTL:
                    latest = previous = None
            except (KeyError, TypeError, ValueError):
                pass
        required = ('temperature_c', 'dewpoint_c', 'relative_humidity', 'cloud_cover_percent', 'wind_kt')
        use_native = latest is not None and all(finite(latest['values'].get(k)) for k in required)
        if use_native:
            values = latest['values']
            earlier = previous['values'].get('temperature_c') if previous else None
            interval = ((clock.instant(latest['valid_at_utc']) - clock.instant(previous['valid_at_utc']))
                        .total_seconds() / 3600) if previous else None
        else:
            values = dict(forecast, cloud_cover_percent=grid.get('sky_cover_percent'))
            earlier = prior_grid['observation'].get('temperature_c') if prior_grid else None
            interval = (target - clock.instant(prior_grid['target_at_utc'])).total_seconds() / 3600 if prior_grid else None
        fields.update({'fcst_' + field: values.get(field) for field in required})
        air = values.get('temperature_c')
        if finite(air) and finite(earlier):
            fields.update(fcst_temperature_change_c=air-earlier, fcst_trend_interval_hours=interval)
        nws_air = forecast.get('temperature_c')
        assessment = assess(fields, minimum=minimum, nws_air=nws_air, grid_only=not use_native)
        if not finite(nws_air):
            assessment.update(state='unknown', reason='Current NWS temperature guidance is unavailable for this hour.')
        hours.append(dict(target_at_utc=target.isoformat(), nws_issued_at_utc=grid.get('issued_at_utc'),
                          observed_at_utc=observation.get('utc'), guidance='HRRR and NWS' if use_native else 'NWS',
                          native_source_sha256=latest['source_sha256'] if use_native else None, **assessment))
        prior_grid = grid or None
    coverage = dict(sort_start_utc=cycle.sort_start.isoformat(), sort_end_utc=cycle.sort_end.isoformat(), hours=hours)
    if len(cycle.windows) > 1:
        coverage['sort_windows'] = [(a.isoformat(), b.isoformat()) for a,b in cycle.windows]
    return coverage


def comparison(state, scores):
    """Use the existing frozen class boundary, never convert a score to frost probability."""
    high = [value >= 0.5 for value in scores.values()]
    messages = []
    if any(high) and state == 'weak':
        messages.append('Score unreliable for frost')
    elif any(high) and state in ('unknown', 'mixed'):
        messages.append('High treatment score unconfirmed')
    elif high and not all(high) and state == 'supporting':
        messages.append('Low treatment score cannot rule out frost')
    if any(high) and not all(high):
        messages.append('Models disagree')
    return ' · '.join(messages)
