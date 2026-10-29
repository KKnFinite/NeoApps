"""Hourly private KRFD evidence on the existing delivery heartbeat.

No scripts, training libraries, scientific arrays, sort dependency or new worker.
FrostHistoryTask is a receipt/index, not a second operational activity database.
"""
import base64
import gzip
import gc
import json
import re
import zlib
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

from flask import current_app, g, has_app_context
from sqlalchemy import func
from app.extensions import db
from app.models import FrostHistoryTask as Task, Gateway
from app.services import frost_history as jobs, frost_vault as vault
from app.services import frost_forecast as clock, frost_forecast_native as native
from app.services import frost_research, frost_model as model, frost_analysis as analysis
from app.services.memory_diagnostics import memory_diagnostics
from app.services import frost_resources as priority

VERSION='frost-auto-v1'
MAX_SNAPSHOT=1024*1024
MAX_STAGED=256*1024
UTC=timezone.utc


def now():return datetime.now(UTC)
def naive(at):return at.astimezone(UTC).replace(tzinfo=None)
def stamp(at):return at.astimezone(UTC).isoformat()
def slot(at):return at.astimezone(UTC).replace(minute=0,second=0,microsecond=0)
def identity(at):return slot(at).strftime('%Y%m%dT%H0000Z')


def enabled(gateway):
    return gateway.code.upper()=='RFD' and current_app.config.get('FROST_AUTO_COLLECTION',True) is True


def control(gateway_id):return jobs.task(gateway_id,'auto_control','collector',VERSION)


def pack(payload):
    raw=vault.canonical(payload)
    if len(raw)>MAX_SNAPSHOT:raise ValueError('Current forecast evidence exceeds the bounded capture limit.')
    encoded=base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    if len(encoded)>MAX_STAGED:raise ValueError('Forecast staging exceeds the bounded compressed limit.')
    return encoded


def unpack(value):
    if not isinstance(value,str) or len(value)>MAX_STAGED:raise ValueError('Invalid bounded forecast staging.')
    decoder=zlib.decompressobj(31);raw=decoder.decompress(base64.b64decode(value,validate=True),MAX_SNAPSHOT+1)
    if len(raw)>MAX_SNAPSHOT or not decoder.eof or decoder.unused_data:raise ValueError('Malformed forecast staging.')
    return json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite forecast evidence.')))


def fetch(url, maximum):
    """One bounded request, closed response; job-level retry, never hidden loops."""
    priority.checkpoint()
    allowed=(url=='https://aviationweather.gov/api/data/metar?ids=KRFD&format=json&hours=14' or
        url=='https://api.weather.gov/points/42.1915,-89.0876' or
        re.fullmatch(r'https://api.weather.gov/gridpoints/LOT/\d+,\d+',url))
    if not allowed:raise ValueError('Unverified current-weather source URL.')
    request=Request(url,headers={'User-Agent':'NeoApps FROST KRFD research (neoapps.onrender.com)',
        'Accept':'application/geo+json,application/json'})
    with urlopen(request,timeout=10) as response:
        if response.geturl()!=url:raise ValueError('Unexpected weather-source redirect.')
        raw=response.read(maximum+1)
    if len(raw)>maximum:raise ValueError('Current weather response exceeds its size budget.')
    return raw,dict(url=url,retrieved_at_utc=stamp(now()),sha256=vault.digest(raw),bytes=len(raw))


def acquire():
    from app.services.frost_inference import normalize_metars
    from app.services.frost_weather import STATION
    sources=[]
    def get(url,maximum):
        raw,receipt=fetch(url,maximum)
        sources.append(dict(receipt,raw_base64=base64.b64encode(raw).decode()))
        return json.loads(raw),receipt
    metars,receipt=get('https://aviationweather.gov/api/data/metar?ids=KRFD&format=json&hours=14',128*1024)
    observations=normalize_metars(metars)
    for row in observations:row['received_at_utc']=receipt['retrieved_at_utc']
    projected=[];errors=[];cutoff=None
    try:
        points,_=get('https://api.weather.gov/points/42.1915,-89.0876',64*1024)
        x,y=points['geometry']['coordinates']
        if abs(x-STATION['longitude'])>.0001 or abs(y-STATION['latitude'])>.0001:raise ValueError('NWS location is not KRFD.')
        grid,_=get(points['properties']['forecastGridData'],512*1024)
        cutoff=now()
        for h in native.HORIZONS:
            projected.append(dict(hours=h,**clock.nws_grid(grid,cutoff=cutoff,target=cutoff+timedelta(hours=h))))
    except Exception as error:
        errors.append('NWS forecast unavailable: '+type(error).__name__)
    if cutoff is None:cutoff=now()
    native_point=None;native_forecasts=[];native_error='Lightweight HRRR capture disabled.';resources={}
    if has_app_context() and current_app.config.get('FROST_AUTO_HRRR',True) and getattr(g,'frost_capture_gateway',None):
        from app.services import frost_hrrr_point as hrrr
        def guarded():
            priority.checkpoint()
            budget=jobs.memory_budget()
            if budget['usage'] is not None:
                resources['container_sample_peak_bytes']=max(resources.get('container_sample_peak_bytes',0),budget['usage'])
                resources['container_limit_bytes']=budget['limit']
            return hrrr.memory_available()
        def retain(raw,receipt):
            if receipt['method']=='HEAD':return receipt
            if not guarded():raise RuntimeError('HRRR evidence deferred: hard memory guard plus 32 MiB transfer reserve.')
            evidence=dict(schema=hrrr.VERSION,sha256=receipt['sha256'],raw_base64=base64.b64encode(raw).decode())
            # Content-addressed originals remain independently durable. The
            # hourly snapshot holds compact references, never all tile bodies.
            key=vault.put_json('weather',g.frost_capture_gateway,
                'hrrr-source-'+receipt['sha256'],evidence)
            return dict(receipt,source_key=key)
        try:
            with vault.transport_scope():
                client=hrrr.Source(now,retain,guarded)
                native_point,native_forecasts=hrrr.collect(client,cutoff)
                resources.update(requests=client.calls,downloaded_bytes=client.bytes)
            native_error=None
        except Exception as error:
            detail=str(error)[:180] if isinstance(error,(ValueError,RuntimeError,TimeoutError)) else type(error).__name__
            native_error='Lightweight HRRR unavailable: '+detail
            errors.append(native_error)
        cutoff=now()
    recent=analysis.features(stamp(cutoff),observations)
    if not recent['matched']:raise ValueError('KRFD observations are missing/stale; capture will retry, never backdate.')
    return dict(station=STATION,cutoff_at_utc=stamp(cutoff),observations=observations,
        sources=sources,nws_forecasts=projected,native_forecasts=native_forecasts,native_point=native_point,errors=errors,
        native_unavailable=native_error,native_resources=resources)


def frozen_models():return frost_research.prospective_summary()['models']


def register_research(gateway_id,actor_id,raw):
    """Exact pinned JSON only; never enters FrostModelArtifact/approval registry."""
    if not jobs.memory_allows_work():raise RuntimeError('Research upload deferred for memory safety. Keep the selected file.')
    if not raw or len(raw)>model.MAX_BYTES:raise ValueError('Research JSON exceeds the 256 KiB limit.')
    digest=vault.digest(raw);expected=next((r for r in frozen_models() if r['sha256']==digest),None)
    if expected is None:raise ValueError('Artifact is not one of the eight exact frozen Phase 12 model files.')
    artifact=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite research model.')))
    if (artifact['contract']!=native.contract(expected['hours']) or artifact['model_version']!=expected['model_version'] or
        artifact['schema']!='frost-native-experimental-candidate-v2' or artifact['policy']!='frost-native-exploratory-v2' or
        artifact['target']!=native.TARGET or any(artifact.get(k) is not False for k in
            ('approved','operationally_validated','physical_frost_probability','registry_compatible'))):
        raise ValueError('Research artifact contract mismatch.')
    for check in artifact['parity_checks']:
        if abs(model.score_vector(artifact,check['features'],len(native.FEATURES))-check['score'])>1e-9:raise ValueError('Research numerical self-test failed.')
    existing=Task.query.filter_by(gateway_id=gateway_id,kind='research_model',identity=f"h{expected['hours']}-{expected['kind']}",version=expected['model_version']).one_or_none()
    if existing:
        if existing.input_hash!=digest:raise ValueError('Immutable research identity conflict.')
        return existing,False
    with vault.transport_scope():
        key=vault.put_json('models',gateway_id,'research-'+expected['model_version'],artifact)
        manifest=vault.put_json('manifests',gateway_id,'research-'+digest,dict(schema=VERSION,model=expected,
            imported_by=actor_id,imported_at_utc=stamp(now()),approved=False,operationally_validated=False))
    row=Task(gateway_id=gateway_id,kind='research_model',identity=f"h{expected['hours']}-{expected['kind']}",
        version=expected['model_version'],input_hash=digest,source_key=key,result_key=manifest,status='ready',
        summary=dict(model=expected,imported_by=actor_id,imported_at_utc=stamp(now()),approved=False))
    db.session.add(row);db.session.flush()
    return row,True


def predictions(gateway_id,source):
    cutoff=clock.instant(source['cutoff_at_utc']);result=[]
    # Detach compact keys and release the read transaction before private I/O.
    registered=dict(db.session.query(Task.version,Task.source_key).filter_by(
        gateway_id=gateway_id,kind='research_model',status='ready').all())
    db.session.rollback()
    for entry in frozen_models():
        deferred=priority.reason()
        h=entry['hours'];target=cutoff+timedelta(hours=h)
        value=dict(model_version=entry['model_version'],model_sha256=entry['sha256'],hours=h,threshold=.5,
            target=native.TARGET,target_at_utc=stamp(target),state='UNAVAILABLE',score=None,classification=None,
            physical_frost_probability=False)
        if not source.get('native_forecasts'):
            value['reason']=source.get('native_unavailable','Compatible native forecast evidence unavailable.')
        else:
            key=registered.get(entry['model_version'])
            if key is None:value['reason']='Exact frozen research artifact has not been privately registered.'
            elif deferred:value['reason']='Research scoring deferred: '+deferred
            elif not jobs.memory_allows_work():value['reason']='Research scoring deferred for memory safety.'
            else:
                try:
                    vector=native.build(source['observations'],[s for s in source['native_forecasts'] if clock.instant(s['valid_at_utc'])<=target],
                        cutoff=stamp(cutoff),target=stamp(target),lead_hours=h)
                except (ValueError,KeyError,TypeError):
                    value['reason']='Native forecast contract rejected: unavailable, incomplete or future input evidence.'
                    result.append(value);continue
                if not vector['core_available'] or analysis.features(stamp(cutoff),source['observations'])['incomplete_window']:
                    value['reason']='Missing/stale core inputs or incomplete preceding observation window.'
                else:
                    artifact=vault.read_json(key,gateway_id,max_bytes=model.MAX_BYTES)
                    if vault.digest(vault.canonical(artifact))!=entry['sha256']:raise ValueError('Research model hash mismatch.')
                    deferred=priority.reason()
                    if deferred or not jobs.memory_allows_work():
                        value['reason']='Research scoring deferred: '+(deferred or 'memory safety')
                        result.append(value);continue
                    score=model.score_vector(artifact,vector['features'],len(native.FEATURES))
                    value.update(state='SCORED',score=score,classification=score>=.5,vector=vector)
        result.append(value)
    return result


def record_gap(ctrl,current):
    previous=(ctrl.summary or {}).get('last_slot')
    if previous:
        prior=clock.instant(previous);missed=int((current-prior).total_seconds()//3600)-1
        if missed>0:
            gap=jobs.task(ctrl.gateway_id,'auto_gap',identity(prior+timedelta(hours=1)),VERSION)
            gap.status='missed';gap.summary=dict(from_utc=stamp(prior+timedelta(hours=1)),to_utc=stamp(current),intervals=missed,
                reason='No heartbeat capture during service downtime/suspension; never reconstruct pre-event predictions.')
    ctrl.summary=dict(ctrl.summary or {},last_slot=stamp(current),
        started_at_utc=(ctrl.summary or {}).get('started_at_utc') or stamp(current))
    db.session.commit()


@memory_diagnostics('frost_auto_capture')
def tick(gateway_id):
    """One small slot/delivery per heartbeat, serialized across real processes."""
    gateway=db.session.get(Gateway,gateway_id)
    if not gateway or not enabled(gateway):return 0
    current=slot(now());ctrl=control(gateway_id);db.session.commit()
    batch=jobs.task(gateway_id,'worker','memory-budget','frost-worker-v1');batch_id=batch.id
    token=jobs.claim(batch,at=naive(now()))
    if not token:return 0
    try:
        ctrl=Task.query.filter_by(id=ctrl.id).with_for_update().one()
        record_gap(ctrl,current)
        Task.query.filter(Task.gateway_id==gateway_id,Task.kind=='auto_capture',Task.version==VERSION,
            Task.identity<identity(current),Task.status.in_(('pending','retry')),
            Task.retry_at<=naive(now())).update(dict(status='missed',lease_token=None,
                last_error='Slot expired without a committed capture; no historical reconstruction.'),synchronize_session=False)
        current_row=jobs.task(gateway_id,'auto_capture',identity(current),VERSION)
        db.session.commit()
        # Retry already committed evidence first. Never reacquire/backdate it.
        row=Task.query.filter_by(gateway_id=gateway_id,kind='auto_capture',version=VERSION).filter(
            Task.status.in_(('staged','archive_retry')),Task.retry_at<=naive(now())).order_by(Task.identity).first()
        if row is None:
            row=current_row
        if row.status in ('ready','failed','missed') or row.retry_at>naive(now()):db.session.rollback();return 0
        resource_reason=priority.reason()
        if not resource_reason and not jobs.memory_allows_work():
            # Pools are closed by their owners; reclaim unreachable cyclic
            # graphs before deciding whether this due slot must suspend. The
            # exact same container threshold/headroom check still decides.
            gc.collect()
        if resource_reason or not jobs.memory_allows_work():
            why=resource_reason or '75% / 128 MiB headroom protection'
            ctrl.summary=dict(ctrl.summary or {},last_attempt_utc=stamp(now()),resource_suspended_at_utc=stamp(now()),resource_reason=why)
            row.summary=dict(row.summary or {},resource_deferrals=(row.summary or {}).get('resource_deferrals',0)+1,last_resource_reason=why)
            row.retry_at=naive(now()+timedelta(minutes=1 if resource_reason else 5));row.last_error='Capture deferred: '+why
            db.session.commit();return 0
        queued=Task.query.filter_by(gateway_id=gateway_id,kind='auto_capture',version=VERSION).filter(Task.status.in_(('staged','archive_retry'))).count()
        if queued>=2 and row.status not in ('staged','archive_retry'):
            row.status='missed';row.last_error='Two durable staging slots await private storage; no unbounded backlog collected.'
            db.session.commit();return 0
        identifier=row.id;lease=jobs.claim(row,at=naive(now()))
        if not lease:return 0
        ctrl=db.session.get(Task,ctrl.id);ctrl.summary=dict(ctrl.summary or {},last_attempt_utc=stamp(now()),resource_suspended_at_utc=None,resource_reason=None)
        db.session.commit()
        try:
            row=db.session.get(Task,identifier)
            if not (row.summary or {}).get('staged'):
                if row.identity!=identity(current):raise ValueError('Old slot has no committed evidence; never manufacture a historical capture.')
                # Request-local gateway context, not mutable application config.
                g.frost_capture_gateway=gateway_id
                db.session.rollback()  # No read transaction spanning weather acquisition.
                try:
                    with vault.transport_scope():
                        source=acquire()
                        scores=predictions(gateway_id,source)
                finally:
                    # SDK graphs can be cyclic even after their pools close.
                    # One client for raw sources + all eight models, then reclaim
                    # before opening the small snapshot-delivery transport.
                    gc.collect()
                payload=dict(schema_version=VERSION,gateway_id=gateway_id,slot_utc=stamp(current),
                    station=source['station'],captured_at_utc=stamp(now()),committed_at_utc=None,
                    source=source,predictions=scores,baseline=frost_research.prospective_summary()['baseline'],
                    approved=False,operationally_validated=False,physical_frost_probability=False)
                locked=Task.query.filter_by(id=identifier).with_for_update().one()
                if locked.lease_token!=lease:db.session.rollback();return 0
                locked.summary=dict(resource_deferrals=(locked.summary or {}).get('resource_deferrals',0),
                    last_resource_reason=(locked.summary or {}).get('last_resource_reason'),
                    staged=pack(payload),captured_at_utc=payload['captured_at_utc'],
                    committed_at_utc=payload['committed_at_utc'],cutoff_at_utc=source['cutoff_at_utc'],
                    source_errors=source.get('errors',[]),nws_horizons=len(source.get('nws_forecasts',[])),
                    scored_hours=sorted({p['hours'] for p in scores if p['state']=='SCORED'}),scored=sum(p['state']=='SCORED' for p in scores),
                    unavailable=sum(p['state']!='SCORED' for p in scores),models=len(scores))
                locked.input_hash=vault.digest(vault.canonical(payload));locked.status='staged';db.session.commit()
            row=db.session.get(Task,identifier);payload=unpack(row.summary['staged'])
            if vault.digest(vault.canonical(payload))!=row.input_hash:raise ValueError('Committed capture checksum mismatch.')
            if payload['committed_at_utc'] is None:
                # The evidence is already committed. Receipt time is AFTER that
                # commit, conservatively later on restart, never before it.
                payload['committed_at_utc']=stamp(now())
                locked=Task.query.filter_by(id=identifier).with_for_update().one()
                if locked.lease_token!=lease:db.session.rollback();return 0
                locked.summary=dict(locked.summary,staged=pack(payload),committed_at_utc=payload['committed_at_utc'])
                locked.input_hash=vault.digest(vault.canonical(payload));db.session.commit()
                row=db.session.get(Task,identifier)
            delivery_reason=priority.reason()
            if delivery_reason or not jobs.memory_allows_work():
                ctrl=db.session.get(Task,ctrl.id)
                ctrl.summary=dict(ctrl.summary or {},resource_suspended_at_utc=stamp(now()),resource_reason=delivery_reason or '75% / 128 MiB headroom protection')
                db.session.commit()
                if delivery_reason:raise priority.Deferred(delivery_reason)
                raise RuntimeError('Private delivery deferred for memory safety; committed evidence retained.')
            with vault.transport_scope():
                capture_identity,digest=row.identity,row.input_hash
                summary={k:v for k,v in row.summary.items() if k!='staged'}
                db.session.rollback()
                key=vault.put_json('weather',gateway_id,'auto-'+capture_identity,payload)
                if vault.digest(vault.canonical(vault.read_json(key,gateway_id,max_bytes=MAX_SNAPSHOT)))!=digest:raise ValueError('Capture read-back checksum mismatch.')
            summary['archive_verified_at_utc']=stamp(now())
            if not jobs.finish(identifier,lease,status='ready',source_key=key,result_key=key,summary=summary):return 0
            ctrl=db.session.get(Task,ctrl.id);ctrl.summary=dict(ctrl.summary or {},last_success_utc=payload['captured_at_utc'],last_archive_utc=stamp(now()),
                source_errors=payload['source'].get('errors',[]),nws_horizons=len(payload['source'].get('nws_forecasts',[])),
                last_scored_utc=payload['captured_at_utc'] if any(p['state']=='SCORED' for p in payload['predictions']) else (ctrl.summary or {}).get('last_scored_utc'),
                native_horizons=sorted({p['hours'] for p in payload['predictions'] if p['state']=='SCORED'}))
            db.session.commit()
            current_app.logger.info('FROST autonomous capture verified slot=%s gateway=%s sha256=%s',row.identity,gateway_id,row.input_hash)
            return 1
        except Exception as error:
            db.session.rollback();row=Task.query.filter_by(id=identifier).with_for_update().one()
            if row.lease_token==lease:
                staged=bool((row.summary or {}).get('staged'))
                contention=isinstance(error,priority.Deferred)
                if contention:
                    row.attempts=max(0,row.attempts-1)
                    row.summary=dict(row.summary or {},resource_deferrals=(row.summary or {}).get('resource_deferrals',0)+1,last_resource_reason=str(error))
                row.status='archive_retry' if staged else 'failed' if row.attempts>=3 else 'retry'
                row.retry_at=naive(now()+timedelta(seconds=60 if contention else min(3600,60*2**min(row.attempts,6))))
                row.lease_token=None;row.last_error=str(error) if contention else type(error).__name__+': acquisition/archive deferred; no success claimed.'
                row.updated_at=naive(now());db.session.commit()
            current_app.logger.warning('FROST autonomous capture deferred slot=%s error=%s',row.identity,type(error).__name__)
            return 0
    finally:
        jobs.finish(batch_id,token,status='ready',retry_at=naive(now()))


def status(gateway_id):
    gateway=db.session.get(Gateway,gateway_id);is_enabled=bool(gateway and enabled(gateway))
    ctrl=Task.query.filter_by(gateway_id=gateway_id,kind='auto_control',identity='collector',version=VERSION).one_or_none()
    summary=ctrl.summary if ctrl else {}
    counts=dict(db.session.query(Task.status,func.count(Task.id)).filter(Task.gateway_id==gateway_id,
        Task.kind=='auto_capture',Task.version==VERSION).group_by(Task.status).all())
    totals=db.session.query(func.coalesce(func.sum(Task.summary['scored'].as_integer()),0),
        func.coalesce(func.sum(Task.summary['unavailable'].as_integer()),0)).filter(Task.gateway_id==gateway_id,
        Task.kind=='auto_capture',Task.version==VERSION,Task.status=='ready').one()
    missed_gaps=db.session.query(func.coalesce(func.sum(Task.summary['intervals'].as_integer()),0)).filter(
        Task.gateway_id==gateway_id,Task.kind=='auto_gap',Task.version==VERSION).scalar()
    recent=recent_captures(gateway_id)
    last=summary.get('last_archive_utc');active=bool(is_enabled and last and not summary.get('resource_suspended_at_utc') and now()-clock.instant(last)<timedelta(hours=2))
    matched_captures=db.session.query(func.count(func.distinct(Task.summary['capture_id'].as_integer()))).filter(
        Task.gateway_id==gateway_id,Task.kind=='auto_match',Task.version==VERSION,Task.status=='active').scalar()
    outcome_query=Task.query.filter_by(gateway_id=gateway_id,kind='xchange_outcome',version=VERSION)
    return dict(enabled=is_enabled,active=active,state='AUTOMATIC COLLECTION ACTIVE' if active else 'AUTOMATIC COLLECTION ENABLED — AWAITING VERIFIED CAPTURE' if is_enabled else 'AUTOMATIC COLLECTION DISABLED',
        last_attempt=summary.get('last_attempt_utc'),last_success=summary.get('last_success_utc'),last_archive=last,last_scored=summary.get('last_scored_utc'),
        next_capture=stamp(slot(now())+timedelta(hours=1)),archived=counts.get('ready',0),failed=counts.get('failed',0),
        missed=int(missed_gaps or 0)+counts.get('missed',0),pending=sum(n for key,n in counts.items() if key not in ('ready','failed','missed')),
        scored=int(totals[0]),unavailable=int(totals[1]),resource_suspended=bool(summary.get('resource_suspended_at_utc')),
        resource_reason=summary.get('resource_reason'),recent=recent,
        source_quality='; '.join(summary.get('source_errors',[])) or f"Latest capture: {summary.get('nws_horizons',0)} issued NWS target horizons retained",
        xchange_matches=outcome_query.filter_by(status='matched').count(),
        awaiting_outcomes=max(0,counts.get('ready',0)-int(matched_captures or 0)),
        outcome_records=outcome_query.count(),research_models=Task.query.filter_by(gateway_id=gateway_id,kind='research_model',status='ready').count(),
        source_limit='METAR/SPECI + issued NWS grid evidence; bounded HRRR KRFD chunks when verified and memory-safe. Exact frozen-model registration and complete native inputs are required for research scoring.',
        limitation='No Xchange record means UNKNOWN. Matched RecordIDs are not independent physical-frost observations; substantial evaluation stays LOCAL.')


def recent_captures(gateway_id):
    """24 compact index entries only; never fetch private weather on page load."""
    rows=Task.query.filter_by(gateway_id=gateway_id,kind='auto_capture',version=VERSION).order_by(Task.identity.desc()).limit(24).all()
    identifiers=[r.id for r in rows]
    matches=dict(db.session.query(Task.summary['capture_id'].as_integer(),func.count(Task.id)).filter(
        Task.gateway_id==gateway_id,Task.kind=='auto_match',Task.version==VERSION,Task.status=='active',
        Task.summary['capture_id'].as_integer().in_(identifiers)).group_by(Task.summary['capture_id'].as_integer()).all()) if identifiers else {}
    return [dict(id=r.id,slot=r.identity,state=r.status,scored=(r.summary or {}).get('scored',0),
        unavailable=(r.summary or {}).get('unavailable',0),verified=(r.summary or {}).get('archive_verified_at_utc'),
        deferrals=(r.summary or {}).get('resource_deferrals',0),error=r.last_error,
        quality=(r.summary or {}).get('source_errors',[]),matches=matches.get(r.id,0)) for r in rows]
