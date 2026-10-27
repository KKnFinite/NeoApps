"""Lazy, bounded inference only. Never imports local training or historical jobs."""
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from flask import current_app
from app.extensions import db
from app.models import FrostModelArtifact
from app.services import frost_model as model, frost_vault as vault
from app.services.frost_model_registry import current_release


def _cache():
    # One cache per application/process, not one model instance per request.
    with _initialization:
        return current_app.extensions.setdefault('frost_inference',dict(lock=threading.RLock(),models=OrderedDict(),predictions=OrderedDict()))


_initialization=threading.Lock()


def approved(gateway_id):
    release=current_release(gateway_id)
    if not release or not release.artifact_id:return None,None
    row=FrostModelArtifact.query.filter_by(id=release.artifact_id,gateway_id=gateway_id).one()
    cache=_cache();key=(gateway_id,row.sha256)
    with cache['lock']:
        if key not in cache['models']:
            artifact=vault.read_json(row.vault_key,gateway_id,max_bytes=model.MAX_BYTES)
            model.decode(vault.canonical(artifact),row.sha256)
            cache['models'][key]=artifact
            while len(cache['models'])>2:cache['models'].popitem(last=False)
        cache['models'].move_to_end(key)
        return row,cache['models'][key]


def normalize_metars(rows):
    """AviationWeather measured Celsius/knots/feet to the historical contract."""
    if len(rows)>256:raise ValueError('Current observation count exceeds the bounded window.')
    result=[]
    for row in rows:
        if row.get('icaoId')!='KRFD':raise ValueError('Current observation station is not verified KRFD.')
        at=row.get('reportTime')
        if not at:continue
        at=datetime.fromisoformat(at.replace('Z','+00:00'))
        if at.tzinfo is None:continue
        t,d=model.finite(row.get('temp')),model.finite(row.get('dewp'))
        wind=model.finite(row.get('wspd'))
        clouds=row.get('clouds') or []
        covers=[c.get('cover') for c in clouds if c.get('cover')]
        ceilings=[model.finite(c.get('base')) for c in clouds if c.get('cover') in ('BKN','OVC','VV') and c.get('base') is not None]
        visibility=row.get('visib')
        try:visibility=float(visibility) if visibility is not None and '+' not in str(visibility) else None
        except (ValueError,TypeError):visibility=None
        result.append(dict(utc=at.astimezone(timezone.utc).isoformat(),temperature_c=t,dewpoint_c=d,
            relative_humidity=100*math.exp(17.625*d/(243.04+d)-17.625*t/(243.04+t)) if t is not None and d is not None and min(t,d)>-243.04 else None,
            spread_c=t-d if t is not None and d is not None else None,wind_kt=wind,
            wind_direction_degrees=model.finite(row.get('wdir')) if isinstance(row.get('wdir'),(int,float)) and wind else None,
            gust_kt=model.finite(row.get('wgst')),ceiling_ft=min(ceilings) if ceilings else None,
            clear_sky=all(c in ('CLR','SKC','NSC','NCD') for c in covers) if covers else None,
            cloud_layers=covers,
            visibility_sm=visibility,weather_codes=row.get('wxString') or None,
            precipitation_since_hourly_reset_in=None,raw_metar=row.get('rawOb'),source='AviationWeather.gov'))
    return result


def current_observations(now):
    from app.services import neosubzero_weather as weather
    # Reuse official-source cache/retry infrastructure. Only a bounded recent
    # 14-hour observation window; no historical season/catalog download.
    url=weather.AVIATION_WEATHER_METAR_URL+'?'+urlencode(dict(ids='KRFD',format='json',hours=14))
    cached=weather._cached_fetch('frost_recent_metar',300,900,lambda **_:weather._request_json(url,max_bytes=512*1024),now=now.timestamp())
    if cached['stale']:raise ValueError('Current weather is stale; no new model score produced.')
    rows=cached.get('value')
    if not isinstance(rows,list):raise ValueError('Current weather is unavailable.')
    return normalize_metars(rows)


def forecast_features(observations, forecasts, *, issued_at, as_of, target):
    """Explicit future-weather domain, never substituted for historical truth."""
    from app.services import frost_analysis
    if any(t.tzinfo is None for t in (issued_at,as_of,target)) or issued_at>as_of or target<as_of:raise ValueError('Invalid forecast issue/valid time.')
    measured=[row for row in observations if datetime.fromisoformat(row['utc'])<=as_of]
    future=[row for row in forecasts if as_of<datetime.fromisoformat(row['utc'])<=target]
    return frost_analysis.features(target.isoformat(),measured+future)


def predict(gateway_id, *, now=None, observations=None, record=False):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Prediction time must be timezone-aware.')
    row,artifact=approved(gateway_id)
    if artifact is None:return dict(enabled=False,state='NO APPROVED MODEL',physical_frost_probability=False)
    from app.services import frost_analysis
    observations=current_observations(now) if observations is None else observations
    observations=[r for r in observations if datetime.fromisoformat(r['utc'])<=now]
    if len(observations)>256:raise ValueError('Observation window exceeds inference limit.')
    features=frost_analysis.features(now.isoformat(),observations)
    lead=artifact['evaluation']['lead_hours_validated'][0]
    values=model.vector(features)
    if not features['matched'] or features['incomplete_window']:
        return dict(enabled=False,state='WEATHER INCOMPLETE',model_version=row.model_version,
            missing_features=[key for key,value in zip(model.FEATURES,values) if value is None],physical_frost_probability=False)
    key=vault.digest(vault.canonical(dict(gateway=gateway_id,model=row.sha256,at=now.replace(second=0,microsecond=0).isoformat(),features=values)))
    cache=_cache()
    with cache['lock']:
        hit=cache['predictions'].get(key)
        if hit and time.monotonic()-hit[0]<300:
            result=dict(hit[1])
            if record:return _record(gateway_id,key,result)
            return result
        result=dict(enabled=True,state='APPROVED TREATMENT-COHORT MODEL',target=model.TARGET,
            model_version=row.model_version,model_sha256=row.sha256,calculated_at_utc=now.isoformat(),
            target_time_utc=(now+timedelta(hours=lead)).isoformat(),lead_hours=lead,
            weather_observed_at_utc=features['observation']['utc'],score=model.score(artifact,values),
            station='KRFD',weather_source='AviationWeather.gov',feature_version=model.FEATURE_VERSION,
            feature_values=values,weather_window_sha256=vault.digest(vault.canonical(observations)),
            physical_frost_probability=False,limitations=artifact['limitations'],
            missing_features=[key for key,value in zip(model.FEATURES,values) if value is None],
            contributing_conditions={key:value for key,value in zip(model.FEATURES,values) if key in ('temperature_c','spread_c','wind_kt','night','clear_sky')},
            future_windows=[dict(valid_at_utc=(now+timedelta(hours=i)).isoformat(),state='VALIDATED OBSERVATION-BASED LEAD' if lead==i else 'HORIZON / FORECAST DOMAIN NOT BACKTESTED',
                score=model.score(artifact,values) if lead==i else None) for i in range(1,13)])
        cache['predictions'][key]=(time.monotonic(),dict(result))
        while len(cache['predictions'])>32:cache['predictions'].popitem(last=False)
    if record:return _record(gateway_id,key,result)
    return result


def _record(gateway_id,key,result):
    # A small immutable evaluation record, never a spray event or a training
    # job. Archive failure remains visible and does not claim success.
    payload={k:v for k,v in result.items() if k!='evidence_key'}
    result['evidence_key']=vault.put_json('evaluations',gateway_id,'prediction-'+key,payload)
    from app.services.frost_history import task
    status=task(gateway_id,'forecast-status','latest',model.VERSION)
    status.summary=dict(model_version=result['model_version'],calculated_at_utc=result['calculated_at_utc'],evidence_key=result['evidence_key'])
    status.status='ready';status.updated_at=datetime.utcnow();db.session.commit()
    return result
