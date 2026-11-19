"""Read-only display of committed frozen-model scores, never inference/actions."""
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from flask import current_app
from app.models import FrostHistoryTask as Task
from app.services import frost_intelligence, frost_research, frost_forecast as clock

UTC=timezone.utc


def display_summary(payload):
    """Compact derivative committed with the existing immutable capture receipt."""
    return dict(gateway_id=payload['gateway_id'],baseline=payload['baseline'],
        cutoff_at_utc=payload['source']['cutoff_at_utc'],committed_at_utc=payload['committed_at_utc'],
        predictions=[{key:p.get(key) for key in ('model_version','model_sha256','hours','target_at_utc','state','score')}
            for p in payload['predictions']])


def _now(at):
    at=at or datetime.now(UTC)
    return at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)


def _index(gateway,at):
    policy=frost_intelligence.read(gateway.id)
    rows=[]
    if policy['operational_predictions'] and gateway.code.upper()=='RFD':
        since=(at-timedelta(hours=6)).strftime('%Y%m%dT%H0000Z')
        rows=Task.query.with_entities(Task.id,Task.gateway_id,Task.input_hash,Task.result_key,Task.summary).filter_by(
            gateway_id=gateway.id,kind='auto_capture',version='frost-auto-v1',status='ready').filter(
            Task.identity>=since,Task.identity<=at.strftime('%Y%m%dT%H0000Z')).order_by(Task.id.desc()).limit(16).all()
    signature=[policy['revision'],policy['operational_predictions'],at.strftime('%Y%m%d%H'),[(r.id,r.input_hash) for r in rows]]
    return hashlib.sha256(json.dumps(signature,separators=(',',':')).encode()).hexdigest(),rows


def revision(gateway,*,now=None):
    return _index(gateway,_now(now))[0]


def _legacy(row):
    """Bounded immutable WEATHER-score cache, never permissions or user HTML.

    Existing captures are not rewritten. Fresh settings/index reads precede
    every use. Single-flight legacy reads never make another operator wait.
    """
    state=current_app.extensions.setdefault('frost_ducc_legacy',dict(lock=threading.Lock(),items=OrderedDict()))
    if not state['lock'].acquire(blocking=False):return None
    try:
        key=(row.gateway_id,row.id,row.input_hash);at=time.monotonic()
        cached=state['items'].get(key)
        if cached and (cached[1] is not None or at-cached[0]<60):
            state['items'].move_to_end(key);return cached[1]
        from app.services import frost_vault as vault, frost_history as jobs
        value=None
        if jobs.memory_allows_work():
            try:
                payload=vault.read_json(row.result_key,row.gateway_id,max_bytes=1024*1024)
                if payload.get('schema_version')!='frost-auto-v1' or vault.digest(vault.canonical(payload))!=row.input_hash:
                    raise ValueError('Invalid archived prediction identity.')
                value=display_summary(payload)
            except Exception:pass  # Unavailable evidence is never replaced by a guessed score.
        state['items'][key]=(at,value);state['items'].move_to_end(key)
        while len(state['items'])>16:state['items'].popitem(last=False)
        return value
    finally:state['lock'].release()


def _verified(value,gateway_id,at,baseline):
    try:
        cutoff=clock.instant(value['cutoff_at_utc']);committed=clock.instant(value['committed_at_utc'])
        if (value['gateway_id']!=gateway_id or value['baseline']!=baseline['baseline']
                or not at-timedelta(hours=12)<=cutoff<=committed<=at):return None
        expected={r['model_version']:r for r in baseline['models']};seen=set();pairs={}
        if len(value['predictions'])!=8:return None
        for prediction in value['predictions']:
            model=expected[prediction['model_version']]
            if model['model_version'] in seen or prediction['hours']!=model['hours'] or prediction['model_sha256']!=model['sha256']:
                return None
            seen.add(model['model_version'])
            target=clock.instant(prediction['target_at_utc'])
            if target!=cutoff+timedelta(hours=model['hours']):return None
            if prediction['state']=='UNAVAILABLE':continue
            score=prediction['score']
            if prediction['state']!='SCORED' or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:
                return None
            hour=target.replace(minute=0,second=0,microsecond=0)
            pair=pairs.setdefault(hour,dict(hours=model['hours'],scores={}))
            pair['scores'][model['kind']]=score
        return cutoff,pairs
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError):return None


def project(gateway,*,now=None):
    at=_now(now);version,rows=_index(gateway,at)
    if not rows:return version,{}
    baseline=frost_research.prospective_summary();captures=[]
    for row in rows:
        value=(row.summary or {}).get('prediction_display')
        if value is None:value=_legacy(row)
        verified=_verified(value,row.gateway_id,at,baseline)
        if verified:captures.append(verified)
    hours={}
    for cutoff,pairs in sorted(captures,key=lambda r:r[0],reverse=True):
        for hour,pair in pairs.items():
            if hour in hours:continue
            values=[f"{pair['scores'][kind]*100:.0f}%" if kind in pair['scores'] else '—' for kind in ('logistic','forest')]
            hours[hour]=dict(text='FROST '+' / '.join(values),
                accessible=f"FROST H{pair['hours']} logistic {values[0]}, forest {values[1]}")
    return version,hours


def decorate(cards,hours):
    for card in cards:
        for hour in card.get('hours',()):
            start=hour.get('start')
            if start is not None:
                value=hours.get(start.astimezone(UTC).replace(minute=0,second=0,microsecond=0))
                if value:hour['frost_predictions']=value
