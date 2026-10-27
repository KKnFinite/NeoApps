"""Bounded authenticated file handoff. Analysis and weather retrieval run locally.

Export tickets attest the exact source snapshot; they are not bearer credentials.
Publishing still requires the same signed-in gateway administrator and CSRF.
"""
import io
import json
import zipfile
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from itsdangerous import URLSafeTimedSerializer,BadSignature
from flask import current_app
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_history as history,frost_analysis as analysis,frost_weather as weather,frost_vault as vault

VERSION='frost-local-backfill-v1'
MAX_ZIP=8*1024*1024
MAX_EXPANDED=24*1024*1024


def implementation_sha():
    root=Path(__file__).resolve().parents[2]
    return vault.digest(b'\n'.join((root/path).read_bytes().replace(b'\r\n',b'\n') for path in
        ('app/services/frost_analysis.py','app/services/frost_weather.py','scripts/frost_backfill.py')))


def signer():return URLSafeTimedSerializer(current_app.secret_key,salt=VERSION)


def months_needed(records):
    months=set()
    for row in records:
        if row['requires_review']:continue
        for fluid in row['fluids']:
            if fluid.get('occurred_at_utc'):
                at=datetime.fromisoformat(fluid['occurred_at_utc'])
                months.update((at.strftime('%Y-%m'),(at-timedelta(hours=12)).strftime('%Y-%m')))
    return sorted(months)


def pending(gateway_id):
    return Task.query.filter_by(gateway_id=gateway_id,kind='partition',version=analysis.VERSION).filter(
        Task.status.in_(('pending','retry'))).order_by(Task.identity).all()


@vault._bounded_transport
def export(gateway_id,actor_id,identifier):
    row=Task.query.filter_by(id=identifier,gateway_id=gateway_id,kind='partition',version=analysis.VERSION).one_or_none()
    if row is None or row.status not in ('pending','retry'):raise ValueError('This month is not pending. Completed evidence is preserved.')
    if row.lease_token and row.retry_at>datetime.utcnow():raise ValueError('Another processor owns this month; retry after its lease expires.')
    if not history.memory_allows_work():raise RuntimeError('Insufficient server headroom for a bounded evidence transfer. Retry later; no job was changed.')
    identifier,month,digest,source_key,previous=row.id,row.identity,row.input_hash,row.source_key,row.result_key
    db.session.commit()
    with vault.transport_scope():
        source=vault.read_json(source_key,gateway_id)
        if vault.digest(vault.canonical(source))!=digest:raise ValueError('Source partition hash mismatch.')
        records=history.resolve_records(source,lambda key:vault.read_json(key,gateway_id))
        cached={}
        for needed in months_needed(records):
            entry=Task.query.filter_by(gateway_id=gateway_id,kind='weather',identity=needed,version=weather.VERSION,status='ready').one_or_none()
            if entry and entry.result_key:
                cached[needed]=dict(key=entry.result_key,payload=vault.read_json(entry.result_key,gateway_id))
    job=dict(schema=VERSION,gateway_id=gateway_id,actor_id=actor_id,task_id=identifier,month=month,
        input_hash=digest,source_key=source_key,previous_result_key=previous,records=records,weather=cached,
        implementation_sha256=implementation_sha(),historical_version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT)
    raw=vault.canonical(job)
    if len(raw)>MAX_EXPANDED:raise ValueError('Monthly handoff exceeds bounded size; no work started.')
    return dict(job=job,sha256=vault.digest(raw),ticket=signer().dumps(vault.digest(raw)))


def unpack(raw):
    if not raw or len(raw)>MAX_ZIP:raise ValueError('Local result ZIP must be at most 8 MiB.')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries=archive.infolist();names=[i.filename for i in entries]
            if len(entries)>8 or len(set(names))!=len(names) or sum(i.file_size for i in entries)>MAX_EXPANDED:
                raise ValueError('Duplicate or oversized result archive.')
            if any('/' in n or '\\' in n or '..' in n or ':' in n for n in names):raise ValueError('Unsafe archive path.')
            files={i.filename:archive.read(i) for i in entries}
        envelope=json.loads(files.pop('job.json'));dataset=json.loads(files.pop('result.json'))
        return envelope,dataset,files
    except (zipfile.BadZipFile,KeyError,UnicodeError,json.JSONDecodeError,RuntimeError) as exc:
        raise ValueError('Malformed local result archive.') from exc


@vault._bounded_transport
def publish(gateway_id,actor_id,raw):
    if not history.memory_allows_work():raise RuntimeError('Insufficient server headroom for publication. Keep the result file and retry later.')
    envelope,dataset,files=unpack(raw);job=envelope['job']
    digest=vault.digest(vault.canonical(job))
    try:verified=signer().loads(envelope['ticket'],max_age=86400)
    except BadSignature as exc:raise ValueError('Expired or invalid source ticket. Download a fresh month; local weather caches are retained.') from exc
    if verified!=digest or envelope['sha256']!=digest or job['gateway_id']!=gateway_id or job['actor_id']!=actor_id:
        raise ValueError('Source identity, gateway or operator mismatch.')
    if job['implementation_sha256']!=implementation_sha() or job['historical_version']!=analysis.VERSION or job['time_contract']!=analysis.TIME_CONTRACT:
        raise ValueError('Local processing code changed. Update the checkout and regenerate this result.')
    row=Task.query.filter_by(id=job['task_id'],gateway_id=gateway_id,kind='partition',version=analysis.VERSION).one_or_none()
    if not row or row.input_hash!=job['input_hash'] or row.source_key!=job['source_key']:raise ValueError('Historical source changed; refresh the month.')
    if dataset['schema_version']!=analysis.VERSION or dataset['time_contract']!=analysis.TIME_CONTRACT or len(dataset['records'])!=len(job['records']):
        raise ValueError('Result contract/count mismatch.')
    counts=Counter()
    for original,result in zip(job['records'],dataset['records']):
        unchanged=dict(result,fluids=[{k:v for k,v in f.items() if k!='weather'} for f in result['fluids']])
        if unchanged!=original:raise ValueError('Source readings, identity or provenance changed during analysis.')
        counts[result['classification']['label']]+=1
        counts['review_records']+=int(result['requires_review'] or any(f['time_quality']!='verified_local_calendar' for f in result['fluids']))
        counts['weather_matched']+=int(bool(result['fluids']) and all(f['weather']['matched'] for f in result['fluids']))
        counts['weather_unmatched']+=int(not result['fluids'] or any(not f['weather']['matched'] for f in result['fluids']))
        counts['incomplete_windows']+=int(any(f['weather'].get('incomplete_window',True) for f in result['fluids']))
        for fluid in result['fluids']:
            observed=fluid['weather'].get('observation')
            if fluid['weather']['matched'] and (not observed or not fluid.get('occurred_at_utc') or not
                0<=(datetime.fromisoformat(fluid['occurred_at_utc'])-datetime.fromisoformat(observed['utc'])).total_seconds()<=5400):
                raise ValueError('Invalid historical weather time alignment.')
    if dict(counts)!=dataset['summary'] or analysis.cohorts(dataset['records'])!=dataset['descriptive_cohorts']:
        raise ValueError('Result summary does not reconcile.')
    dataset.update(source_partition_key=job['source_key'],source_partition_hash=job['input_hash'],station=weather.STATION,
        local_processing=dict(actor_id=actor_id,implementation_sha256=job['implementation_sha256'],source_package_sha256=digest,result_zip_sha256=vault.digest(raw)))
    needed=months_needed(job['records']);weather_objects={};new_raw={}
    for month in needed:
        if month in job['weather']:
            weather_objects[month]=job['weather'][month]
        else:
            payload=json.loads(files.pop('weather-'+month+'.json'));raw_weather=files.pop('weather-'+month+'.csv')
            new_raw[month]=raw_weather
            start,end=history.month_bounds(month)
            if payload['schema_version']!=weather.VERSION or payload['station']!=weather.STATION or payload['raw_sha256']!=vault.digest(raw_weather) or payload['start_utc']!=start.isoformat() or payload['end_utc']!=end.isoformat():
                raise ValueError('Historical weather evidence contract/hash mismatch.')
            if not payload['observations'] or len(payload['observations'])>10000:raise ValueError('Invalid monthly weather count.')
            for observation in payload['observations']:
                if not start<=datetime.fromisoformat(observation['utc'])<end:raise ValueError('Weather observation outside requested month.')
            weather_objects[month]=dict(payload=payload)
    if files:raise ValueError('Unexpected result archive entries.')
    # Durable writes before short metadata locks. No feature calculation/fetch.
    db.session.commit()
    with vault.transport_scope():
        evidence=[]
        for month,item in weather_objects.items():
            payload=item['payload']
            if 'key' not in item:
                raw_weather=new_raw[month]
                raw_key=vault.namespace(gateway_id,'weather')+weather.VERSION+'/'+month+'/'+vault.digest(raw_weather)+'.csv'
                if payload['raw_key']!=raw_key:raise ValueError('Weather raw evidence path mismatch.')
                vault.put_bytes(raw_key,raw_weather,gateway_id)
                item['key']=vault.put_json('weather',gateway_id,weather.VERSION+'-'+month,payload)
            evidence.append(item['key'])
        dataset['weather_evidence']=evidence
        key=vault.put_json('training-datasets',gateway_id,analysis.VERSION+'-'+job['month'],dataset)
    db.session.rollback()
    current=Task.query.filter_by(id=job['task_id'],gateway_id=gateway_id).with_for_update().one()
    if current.result_key==key and current.input_hash==job['input_hash']:db.session.rollback();return current
    if current.input_hash!=job['input_hash'] or current.result_key!=job['previous_result_key'] or current.status not in ('pending','retry') or (current.lease_token and current.retry_at>datetime.utcnow()):
        db.session.rollback();raise ValueError('Concurrent result/source conflict. Existing evidence was not replaced.')
    for month,item in weather_objects.items():
        entry=history.task(gateway_id,'weather',month,weather.VERSION)
        entry=Task.query.filter_by(id=entry.id).populate_existing().with_for_update().one()
        if entry.status=='ready' and entry.result_key and entry.result_key!=item['key']:
            db.session.rollback();raise ValueError('Weather cache changed concurrently. Refresh the month; no completed evidence replaced.')
        entry.status='ready';entry.result_key=item['key'];entry.source_key=None;entry.lease_token=None;entry.last_error=None
        entry.summary=dict(observations=len(item['payload']['observations']));entry.updated_at=datetime.utcnow();entry.retry_at=datetime.utcnow()
    summary=dict(counts,records=len(dataset['records']),cohorts=dataset['descriptive_cohorts'],processing_location='local')
    current.status='requires_review' if counts['review_records'] else 'weather_incomplete' if counts['weather_unmatched'] or counts['incomplete_windows'] else 'ready'
    current.result_key=key;current.summary=summary;current.last_error=None;current.lease_token=None;current.updated_at=datetime.utcnow()
    current.retry_at=datetime.utcnow()+timedelta(days=1);db.session.commit()
    return current
