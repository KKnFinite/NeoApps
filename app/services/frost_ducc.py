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
from app.services import frost_atmosphere, frost_intelligence, frost_research, frost_forecast as clock
from app.services.frost_schedule import cycle_identity, hourly_targets

UTC=timezone.utc


def display_summary(payload):
    """Compact derivative committed with the existing immutable capture receipt."""
    atmospheric=payload.get('atmosphere') or {}
    outlook={}
    if atmospheric.get('method')==frost_atmosphere.VERSION:
        outlook={h:{k:item[k] for k in ('state','reason','target_at_utc')}
                 for h,item in atmospheric['horizons'].items()}
    coverage = atmospheric.get('hourly')
    hourly = None
    if coverage and atmospheric.get('method') == frost_atmosphere.VERSION:
        hourly = dict(sort_start_utc=coverage['sort_start_utc'], sort_end_utc=coverage['sort_end_utc'],
                      hours=[{key: row[key] for key in ('target_at_utc', 'state', 'reason')}
                             for row in coverage['hours']])
        if 'sort_windows' in coverage:
            hourly['sort_windows'] = coverage['sort_windows']
    return dict(gateway_id=payload['gateway_id'],baseline=payload['baseline'],outlook=outlook,hourly=hourly,
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
        # Keep the opening receipt available through a 25-hour fall-back Sort.
        since=(at-timedelta(hours=26)).strftime('%Y%m%dT%H0000Z')
        rows=Task.query.with_entities(Task.id,Task.identity,Task.gateway_id,Task.input_hash,Task.result_key,Task.summary).filter_by(
            gateway_id=gateway.id,kind='auto_capture',version='frost-auto-v1',status='ready').filter(
            Task.identity>=since,Task.identity<=cycle_identity(at)).order_by(Task.identity.desc()).limit(32).all()
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


def _verified(value,gateway_id,at,baseline,window=None):
    try:
        cutoff=clock.instant(value['cutoff_at_utc']);committed=clock.instant(value['committed_at_utc'])
        coverage = value.get('hourly')
        max_age = timedelta(hours=26 if coverage is not None else 12)
        if (value['gateway_id']!=gateway_id or value['baseline']!=baseline['baseline']
                or not at-max_age<=cutoff<=committed<=at):return None
        expected={r['model_version']:r for r in baseline['models']};seen=set();pairs={}
        if len(value['predictions'])!=8:return None
        for prediction in value['predictions']:
            model=expected[prediction['model_version']]
            if model['model_version'] in seen or prediction['hours']!=model['hours'] or prediction['model_sha256']!=model['sha256']:
                return None
            seen.add(model['model_version'])
            target=clock.instant(prediction['target_at_utc'])
            if target!=cutoff+timedelta(hours=model['hours']):return None
            assessment=(value.get('outlook') or {}).get(str(model['hours'])) or {}
            state=assessment.get('state','unknown')
            if state not in frost_atmosphere.LABELS or (assessment and clock.instant(assessment['target_at_utc'])!=target):
                return None
            if prediction['state']=='UNAVAILABLE' and not assessment:continue
            hour=target.replace(minute=0,second=0,microsecond=0)
            pair=pairs.setdefault(hour,dict(hours=model['hours'],scores={},state=state,
                reason=assessment.get('reason','This capture has no verified atmospheric assessment.')))
            if prediction['state']=='UNAVAILABLE':continue
            score=prediction['score']
            if prediction['state']!='SCORED' or type(score) not in (int,float) or not math.isfinite(score) or not 0<=score<=1:
                return None
            pair['scores'][model['kind']]=score
        if coverage is not None:
            start = clock.instant(coverage['sort_start_utc'])
            end = clock.instant(coverage['sort_end_utc'])
            windows = [(clock.instant(a), clock.instant(b)) for a,b in coverage['sort_windows']] if 'sort_windows' in coverage else [(start,end)]
            if (not start <= cutoff < end or not windows
                    or any(not timedelta(0) < b-a <= timedelta(hours=25) for a,b in windows)
                    or min(a for a,b in windows) != start or max(b for a,b in windows) != end
                    or max(a for a,b in windows) > cutoff or end-cutoff > timedelta(hours=25)):
                return None
            if window and (window not in windows or cutoff >= window[1]):
                return None
            expected_targets = tuple(hourly_targets(cutoff, end))
            if len(coverage['hours']) != len(expected_targets):
                return None
            in_sort = {}
            for target, row in zip(expected_targets, coverage['hours']):
                if clock.instant(row['target_at_utc']) != target or row['state'] not in frost_atmosphere.LABELS:
                    return None
                hour = target.replace(minute=0, second=0, microsecond=0)
                pair = pairs.get(hour)
                if window and target >= window[1]:
                    continue
                if pair is None or cutoff + timedelta(hours=pair['hours']) >= (window[1] if window else end):
                    pair = dict(hours=None, scores={})
                in_sort[hour] = dict(pair, state=row['state'], reason=row['reason'], hourly=True)
            return cutoff, in_sort
        return cutoff,pairs
    except (AttributeError,KeyError,TypeError,ValueError,OverflowError):return None


def project(gateway,*,now=None,window=None):
    at=_now(now);version,rows=_index(gateway,at)
    if not rows:return version,{}
    baseline=frost_research.prospective_summary();captures=[]
    for row in rows:
        value=(row.summary or {}).get('prediction_display')
        if not (value or {}).get('hourly') and row.identity < (at-timedelta(hours=7)).strftime('%Y%m%dT%H0000Z'):
            continue
        if value is None:value=_legacy(row)
        verified=_verified(value,row.gateway_id,at,baseline,window)
        if verified:captures.append(verified)
    hours={}
    for cutoff,pairs in sorted(captures,key=lambda r:r[0],reverse=True):
        for hour,pair in pairs.items():
            if hour in hours:continue
            values=[f"{pair['scores'][kind]*100:.0f}%" if kind in pair['scores'] else '—' for kind in ('logistic','forest')]
            state=pair['state']
            hours[hour]=dict(outlook=frost_atmosphere.LABELS[state],reason=pair['reason'],
                hourly=pair.get('hourly',False), captured_cutoff_utc=cutoff.isoformat(),
                comparison=frost_atmosphere.comparison(state,pair['scores']),
                text='Treatment reason (FROST) '+' / '.join(values) if pair['hours'] is not None else None,
                accessible=(f"Recorded FROST treatment reason, H{pair['hours']} logistic {values[0]}, forest {values[1]}. Atmospheric frost probability is unvalidated."
                            if pair['hours'] is not None else None))
    return version,hours


def decorate(cards,hours):
    for card in cards:
        for hour in card.get('hours',()):
            start=hour.get('start')
            if start is not None:
                value=hours.get(start.astimezone(UTC).replace(minute=0,second=0,microsecond=0))
                if value:hour['frost_predictions']=value
