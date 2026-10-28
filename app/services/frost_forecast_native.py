"""Forecast-native research vectors, deliberately incompatible with METAR models.

Pure/bounded transforms only: no history retrieval, fitting, registry or writes.
Every horizon is its own contract. Missing is never meteorological zero.
"""
import math
from datetime import timedelta
from app.services import frost_analysis as analysis, frost_forecast as time, frost_weather as weather

VERSION = 'krfd-forecast-native-v1'
HORIZONS = (0, 1, 3, 6)
TARGET = 'frost_reason_among_recorded_departure_treatments'
OBS = ('temperature_c','dewpoint_c','spread_c','relative_humidity','wind_kt',
       'wind_direction_degrees','gust_kt','ceiling_ft','visibility_sm')
FCST = ('temperature_c','dewpoint_c','spread_c','relative_humidity','wind_kt',
        'wind_direction_degrees','gust_kt','cloud_cover_percent','visibility_sm',
        'precipitation_1h_mm')
FEATURES = tuple('obs_'+x for x in OBS if x!='wind_direction_degrees') + ('obs_wind_direction_sin','obs_wind_direction_cos',
    'obs_age_minutes','obs_cooling_3h_c_per_hour','obs_cooling_6h_c_per_hour',
    'obs_wetting_report_hours','obs_fog_report_hours','obs_freezing_fog',
    'obs_window_thermal_hours','obs_window_cloud_hours') + tuple('fcst_'+x for x in FCST if x!='wind_direction_degrees') + (
    'fcst_wind_direction_sin','fcst_wind_direction_cos',
    'fcst_rain','fcst_snow','fcst_freezing_rain','fcst_ice_pellets',
    'fcst_temperature_change_c','fcst_dewpoint_change_c','fcst_cloud_change_percentage_points',
    'fcst_trend_interval_hours','forecast_age_hours','valid_time_offset_minutes',
    'target_solar_elevation_degrees','target_hours_since_sunset','target_night',
    'aircraft_exposure_hours','prior_pretreat')


def contract(hours):
    if type(hours) is not int or hours not in HORIZONS:
        raise ValueError('Use a separate 0/1/3/6-hour research contract.')
    return dict(version=VERSION, lead_hours=hours, features=list(FEATURES),
        target=TARGET, physical_frost_probability=False,
        units='C; RH/cloud percent; kt; degrees true; modeled one-hour precipitation mm; observed ceiling ft AGL',
        missing='null + explicit missingness mask; fitting-fold imputation only',
        exclusions=['HRRR cloud fraction is not METAR cloud code',
            'HRRR precipitation mm is not observed hourly-reset p01i',
            'HRRR ceiling height excluded: datum unresolved',
            'No parking adjustment or guessed surface temperature'],
        time_basis='cycle != issuance; retain exact issue when known and independently verified available-by bound')


def build(observations, snapshots, *, cutoff, target, lead_hours, aircraft=None):
    contract(lead_hours)
    cutoff, target = time.instant(cutoff), time.instant(target)
    if target-cutoff != timedelta(hours=lead_hours):
        raise ValueError('Prediction cutoff violates the horizon contract.')
    # Only report-time evidence is available for historical METARs. Retain that
    # limitation; a future receipt/correction is never treated as prior knowledge.
    allowed=[]
    for row in observations:
        observed=time.instant(row['utc'])
        receipt=time.instant(row['received_at_utc']) if row.get('received_at_utc') else None
        if observed<=cutoff and (receipt is None or receipt<=cutoff):allowed.append(row)
    history=analysis.features(cutoff.isoformat(),allowed)
    current=history['observation'] or {}
    accepted=[]
    for item in snapshots:
        time.station(item['station'])
        cycle=time.instant(item['cycle_at_utc']);valid=time.instant(item['valid_at_utc'])
        available=time.instant(item['available_by_utc'])
        issued=time.instant(item['issued_at_utc']) if item.get('issued_at_utc') else None
        if not cycle<=available<=cutoff or (issued and not cycle<=issued<=available):
            raise ValueError('Forecast was unavailable at prediction cutoff.')
        if valid<cycle or valid>target:
            raise ValueError('Forecast valid time is outside the target window.')
        if item.get('availability_basis')!='NOAA_original_object_Last-Modified':
            raise ValueError('Unverified forecast availability basis.')
        if not item.get('source_sha256') or len(item['source_sha256'])!=64:
            raise ValueError('Missing verified forecast provenance.')
        accepted.append(item)
    eligible=[r for r in accepted if timedelta(0)<=target-time.instant(r['valid_at_utc'])<timedelta(hours=1)]
    if not eligible:raise ValueError('No pre-cutoff forecast for this target hour.')
    latest=max(eligible,key=lambda r:(time.instant(r['valid_at_utc']),time.instant(r['cycle_at_utc'])))
    valid=time.instant(latest['valid_at_utc']);cycle=time.instant(latest['cycle_at_utc'])
    # Trends use only the SAME cycle and actual archived availability proofs.
    earlier=[r for r in accepted if r['cycle_at_utc']==latest['cycle_at_utc'] and
        timedelta(0)<valid-time.instant(r['valid_at_utc'])<=timedelta(hours=6)]
    old=min(earlier,key=lambda r:r['valid_at_utc']) if earlier else None
    fc=latest['values'];previous=old['values'] if old else {}
    values={**{'obs_'+k:current.get(k) for k in OBS},**{'fcst_'+k:fc.get(k) for k in FCST}}
    for prefix in ('obs','fcst'):
        degrees=values[prefix+'_wind_direction_degrees']
        values[prefix+'_wind_direction_sin']=math.sin(math.radians(degrees)) if degrees is not None else None
        values[prefix+'_wind_direction_cos']=math.cos(math.radians(degrees)) if degrees is not None else None
    values.update(obs_age_minutes=(cutoff-time.instant(current['utc'])).total_seconds()/60 if current else None,
        obs_cooling_3h_c_per_hour=history['cooling_rate_3h_c_per_hour'],
        obs_cooling_6h_c_per_hour=history['cooling_rate_6h_c_per_hour'],
        obs_wetting_report_hours=history['precipitation_report_proxy_hours'] if history['observation_count_12h'] else None,
        obs_fog_report_hours=history['fog_mist_proxy_hours'] if history['observation_count_12h'] else None,
        obs_freezing_fog=int('FZFG' in current['weather_codes']) if current.get('weather_codes') is not None else None,
        obs_window_thermal_hours=history['thermal_coverage_proxy_hours'],
        obs_window_cloud_hours=history['cloud_coverage_proxy_hours'],
        forecast_age_hours=(cutoff-cycle).total_seconds()/3600,
        valid_time_offset_minutes=(target-valid).total_seconds()/60)
    codes=fc.get('weather_codes')
    for name,code in [('rain','RA'),('snow','SN'),('freezing_rain','FZRA'),('ice_pellets','PL')]:
        values['fcst_'+name]=int(code in codes.split()) if codes is not None else None
    for short,field in [('temperature','temperature_c'),('dewpoint','dewpoint_c'),('cloud','cloud_cover_percent')]:
        key='fcst_'+short+('_change_percentage_points' if short=='cloud' else '_change_c')
        values[key]=fc[field]-previous[field] if fc.get(field) is not None and previous.get(field) is not None else None
    values['fcst_trend_interval_hours']=(valid-time.instant(old['valid_at_utc'])).total_seconds()/3600 if old else None
    solar=analysis.solar(target)
    sunset=time.instant(solar['sunset_utc'])
    if sunset>target:sunset=time.instant(analysis.solar(target-timedelta(days=1))['sunset_utc'])
    values.update(target_solar_elevation_degrees=solar['elevation_degrees'],target_night=int(solar['night']),
        target_hours_since_sunset=(target-sunset).total_seconds()/3600 if solar['night'] else None,
        aircraft_exposure_hours=None,prior_pretreat=None)
    # An explicit point-in-time aircraft snapshot is required. Later outcomes
    # (including later pretreat decisions) cannot become prediction inputs.
    if aircraft and aircraft.get('recorded_at_utc') and time.instant(aircraft['recorded_at_utc'])<=cutoff:
        values['aircraft_exposure_hours']=aircraft.get('exposure_hours')
        values['prior_pretreat']=int(aircraft['prior_pretreat']) if type(aircraft.get('prior_pretreat')) is bool else None
    for key,value in values.items():
        if value is not None and (not isinstance(value,(int,float)) or not math.isfinite(value)):
            raise ValueError('Nonfinite or invalid native feature: '+key)
    quality=[]
    for key in ('fcst_relative_humidity','fcst_cloud_cover_percent'):
        if values[key] is not None and not 0<=values[key]<=100:
            quality.append(dict(field=key,source_value=values[key],disposition='outside 0..100; feature missing, source retained; no clamping'))
            values[key]=None
    return dict(contract=contract(lead_hours),features=[values[k] for k in FEATURES],
        missing=[k for k in FEATURES if values[k] is None],quality=quality,cutoff_at_utc=cutoff.isoformat(),target_at_utc=target.isoformat(),
        provenance=dict(forecasts=[{k:r.get(k) for k in ('source_sha256','cycle_at_utc','issued_at_utc','valid_at_utc','available_by_utc','availability_basis')} for r in [latest]+([old] if old else [])],
            observed_at_utc=current.get('utc'),observation_receipt_quality='historical report time only unless explicit receipt retained',
            station=weather.STATION,parking=None,surface_temperature=None),
        core_available=bool(history['matched'] and fc.get('temperature_c') is not None and fc.get('dewpoint_c') is not None))
