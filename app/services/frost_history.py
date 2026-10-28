"""Incremental, leased FROST research jobs on the existing archive worker.

One small DB row per source catalog/month, not a second CSV database. Network
I/O never holds an operational lock. All results are immutable content-addressed
FROST objects; catalog pointers change only after read-back verification.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import gc
import os
from pathlib import Path

from flask import current_app,has_app_context
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import FrostDocument, FrostHistoryTask as Task, Gateway
from app.services import frost_analysis as analysis, frost_vault as vault, frost_weather as weather


def local_execution_required():
    """Historical computation must never compete with live Render operations."""
    return bool(os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID') or
        has_app_context() and current_app.config.get('FROST_HISTORY_EXECUTION') == 'local')


def memory_budget():
    """Use container totals, not only one gunicorn process's RSS.

    Fails closed on an unmeasurable production budget. Do not spend money by
    changing the service plan, or start historical work without safe headroom.
    """
    for usage_path,limit_path in (
        ('/sys/fs/cgroup/memory.current','/sys/fs/cgroup/memory.max'),
        ('/sys/fs/cgroup/memory/memory.usage_in_bytes','/sys/fs/cgroup/memory/memory.limit_in_bytes')):
        try:
            usage=int(Path(usage_path).read_text().strip())
            limit=int(Path(limit_path).read_text().strip())
            if 0 < limit < 2**60:
                return dict(usage=usage,limit=limit,safe=usage <= limit*.75 and limit-usage >= 128*1024*1024)
        except (OSError,ValueError):
            continue
    return dict(usage=None,limit=None,safe=False)


def memory_allows_work():
    return bool(current_app.config.get('TESTING') or memory_budget()['safe'])


def task(gateway_id, kind, identity, version=None):
    version = version or analysis.VERSION
    row = Task.query.filter_by(gateway_id=gateway_id,kind=kind,identity=identity,version=version).one_or_none()
    if row is None:
        try:
            with db.session.begin_nested():
                row = Task(gateway_id=gateway_id,kind=kind,identity=identity,version=version)
                db.session.add(row)
                db.session.flush()
        except IntegrityError:
            row = Task.query.filter_by(gateway_id=gateway_id,kind=kind,identity=identity,version=version).one()
    return row


def claim(row, *, at=None):
    identifier = row.id
    db.session.commit()
    row = Task.query.filter_by(id=identifier).with_for_update().one()
    now = at or datetime.utcnow()
    if row.retry_at > now:
        db.session.rollback()
        return None
    token = uuid4().hex
    row.lease_token = token
    row.retry_at = now + timedelta(minutes=15)
    row.attempts += 1
    db.session.commit()
    return token


def finish(identifier, token, **values):
    row = Task.query.filter_by(id=identifier).with_for_update().one()
    if row.lease_token != token:
        db.session.rollback()
        return False
    for key, value in values.items():
        setattr(row,key,value)
    row.lease_token = None
    row.updated_at = datetime.utcnow()
    row.last_error = None
    db.session.commit()
    return True


def defer(identifier, token, exc):
    db.session.rollback()
    row = Task.query.filter_by(id=identifier).with_for_update().one()
    if row.lease_token == token:
        row.status = 'retry'
        row.last_error = str(exc)[:220] if isinstance(exc,(ValueError,weather.WeatherUnavailable)) else 'Historical processing unavailable; source evidence preserved, automatic retry pending.'
        row.retry_at = datetime.utcnow() + timedelta(seconds=max(getattr(exc,'retry_after',0),min(3600,30*2**min(row.attempts,7))))
        row.lease_token = None
        db.session.commit()
    else:
        db.session.rollback()
    current_app.logger.warning('FROST historical task deferred: id=%s',identifier,exc_info=True)


def discover(gateway_id):
    if local_execution_required(): return False
    gateway = db.session.get(Gateway,gateway_id)
    # KRFD and Central source contract were verified for RFD only.
    if gateway.code.upper() != 'RFD':
        return False
    docs = FrostDocument.query.filter_by(gateway_id=gateway_id).filter(FrostDocument.archived_at_utc.isnot(None)).order_by(FrostDocument.id).all()
    if not docs: return False
    fingerprint = vault.digest(vault.canonical([(d.id,d.sha256,d.extracted_key,d.extraction_status) for d in docs]))
    catalog = task(gateway_id,'catalog','sources')
    identifier = catalog.id
    if catalog.input_hash == fingerprint and catalog.status == 'ready':
        db.session.commit()
        return False
    token = claim(catalog)
    if not token: return False
    try:
        records = []
        for document in docs:
            extracted = vault.read_json(document.extracted_key,gateway_id)
            if extracted.get('source_sha256') != document.sha256 or extracted.get('source_document_id') != document.id:
                raise ValueError('Extracted source identity/hash mismatch; review required.')
            if extracted.get('missing_contract_fields'):
                continue
            # Never retain a decade of tank/fluid/raw CSV dictionaries at once.
            # Archive document/month fragments and keep only compact revision
            # pointers for global overlap resolution. Source bytes stay intact.
            fragments = defaultdict(list)
            for raw_row in extracted['rows']:
                item = analysis.compact_row(raw_row,document)
                fragments[(item.get('application_date') or 'unknown')[:7]].append(item)
            del extracted
            for month,items in fragments.items():
                fragment = vault.put_json('training-datasets',gateway_id,f'{analysis.VERSION}-doc-{document.id}-{month}',
                    dict(schema_version=analysis.VERSION,records=items))
                records.extend(dict(source_record_id=item['source_record_id'],application_date=item['application_date'],
                    row_hash=item['row_hash'],modified_utc=item['modified_utc'],source=item['source'],
                    requires_review=item['requires_review'],fragment_key=fragment) for item in items)
            del fragments
            checkpoint = Task.query.filter_by(id=identifier).with_for_update().one()
            if checkpoint.lease_token != token:
                db.session.rollback()
                return False
            checkpoint.summary = dict(documents=len(docs),documents_scanned=docs.index(document)+1,
                source_rows_scanned=len(records))
            checkpoint.updated_at = datetime.utcnow()
            checkpoint.retry_at = datetime.utcnow()+timedelta(minutes=15)
            db.session.commit()
        records, summary = analysis.deduplicate(records)
        partitions = defaultdict(list)
        for row in records:
            partitions[(row.get('application_date') or 'unknown')[:7]].append(row)
        saved = {}
        for month, rows in sorted(partitions.items()):
            payload = dict(schema_version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,record_pointers=rows)
            digest = vault.digest(vault.canonical(payload))
            source_key = vault.put_json('training-datasets',gateway_id,'source-'+month,payload)
            saved[month] = (digest, source_key, len(rows))
        db.session.rollback()
        catalog = Task.query.filter_by(id=identifier).with_for_update().one()
        if catalog.lease_token != token:
            db.session.rollback()
            return False
        for month,(digest,key,count) in saved.items():
            row = task(gateway_id,'partition',month)
            if row.input_hash != digest:
                row.input_hash, row.source_key = digest,key
                row.status, row.retry_at, row.lease_token = 'pending',datetime.utcnow(),None
                row.summary = dict(records=count)
                row.last_error = None
        # Records may move between months after a source correction. Rebuild
        # affected partitions, retire pointers without deleting old evidence.
        for row in Task.query.filter_by(gateway_id=gateway_id,kind='partition',version=analysis.VERSION).all():
            if row.identity not in saved:
                row.status, row.summary = 'superseded', dict(records=0)
        summary.update(documents=len(docs),date_start=min((d.date_start.isoformat() for d in docs if d.date_start),default=None),
            source_documents_requiring_review=sum(d.extraction_status != 'extracted' for d in docs),
            date_end=max((d.date_end.isoformat() for d in docs if d.date_end),default=None),
            seasons=sorted({str(int(month[:4])-(int(month[5:7]) < 7))+'-'+str(int(month[:4])+(int(month[5:7]) >= 7)) for month in saved if month != 'unknown'}))
        catalog.input_hash, catalog.summary = fingerprint,summary
        catalog.status, catalog.lease_token = 'ready',None
        catalog.updated_at, catalog.retry_at = datetime.utcnow(),datetime.utcnow()
        db.session.commit()
        return True
    except Exception as exc:
        defer(identifier,token,exc)
        return False


def month_bounds(identity):
    start = datetime.strptime(identity,'%Y-%m').replace(tzinfo=timezone.utc)
    end = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
    return start,end


def observations(gateway_id, identity):
    if local_execution_required(): raise RuntimeError('Historical weather processing is workstation-only.')
    row = task(gateway_id,'weather',identity,weather.VERSION)
    identifier = row.id
    _, end = month_bounds(identity)
    closed = end <= datetime.now(timezone.utc)
    if row.status == 'ready' and row.result_key and (closed or row.updated_at > datetime.utcnow()-timedelta(days=1)):
        key = row.result_key
        db.session.commit()
        payload = vault.read_json(key,gateway_id)
        return payload['observations'],key
    token = claim(row)
    if not token: raise weather.WeatherUnavailable('Weather partition is leased or waiting for retry.',60)
    try:
        start,end = month_bounds(identity)
        checkpoint = db.session.get(Task,identifier)
        if checkpoint.source_key and checkpoint.summary.get('request_url'):
            raw,url = vault.read_bytes(checkpoint.source_key,gateway_id),checkpoint.summary['request_url']
        else:
            raw,url = weather.fetch(start,end)
        parsed = weather.parse(raw,start,end)
        if not parsed: raise weather.WeatherUnavailable('No historical observations returned for this month; review coverage.',3600)
        raw_key = vault.namespace(gateway_id,'weather')+weather.VERSION+'/'+identity+'/'+vault.digest(raw)+'.csv'
        vault.put_bytes(raw_key,raw,gateway_id)
        checkpoint = Task.query.filter_by(id=identifier).with_for_update().one()
        if checkpoint.lease_token != token:
            db.session.rollback()
            raise weather.WeatherUnavailable('Weather checkpoint lease expired; retry pending.')
        checkpoint.source_key = raw_key
        checkpoint.summary = dict(request_url=url,raw_sha256=vault.digest(raw))
        db.session.commit()  # Resume a failed derived-object delivery without redownloading observations.
        key = vault.put_json('weather',gateway_id,weather.VERSION+'-'+identity,dict(schema_version=weather.VERSION,
            station=weather.STATION,request_url=url,start_utc=start,end_utc=end,raw_key=raw_key,raw_sha256=vault.digest(raw),
            fetched_at_utc=datetime.now(timezone.utc),observations=parsed,quality='as-is archive; missing fields retained'))
        if not finish(identifier,token,status='ready',result_key=key,source_key=None,summary=dict(observations=len(parsed)),retry_at=datetime.utcnow()):
            raise weather.WeatherUnavailable('Weather lease expired; retry pending.')
        return parsed,key
    except Exception as exc:
        defer(identifier,token,exc)
        raise


def process(row):
    if local_execution_required(): return False
    identifier, gateway_id, source_key, digest = row.id,row.gateway_id,row.source_key,row.input_hash
    token = claim(row)
    if not token: return False
    try:
        source = vault.read_json(source_key,gateway_id)
        if vault.digest(vault.canonical(source)) != digest:
            raise ValueError('Research source partition hash mismatch.')
        # Resolve just this month's selected rows. Raw and normalized records
        # from other seasons never enter this worker's working set.
        records = resolve_records(source,lambda key:vault.read_json(key,gateway_id))
        months = set()
        for record in records:
            if record['requires_review']: continue
            for fluid in record['fluids']:
                if fluid.get('occurred_at_utc'):
                    at = datetime.fromisoformat(fluid['occurred_at_utc'])
                    months.update((at.strftime('%Y-%m'),(at-timedelta(hours=12)).strftime('%Y-%m')))
        observed, evidence = [], []
        for month in sorted(months):
            rows,key = observations(gateway_id,month)
            observed.extend(rows)
            evidence.append(key)
        dataset = analysis.analyze(records,observed)
        dataset.update(source_partition_key=source_key,source_partition_hash=digest,weather_evidence=evidence,
            station=weather.STATION)
        key = vault.put_json('training-datasets',gateway_id,analysis.VERSION+'-'+row.identity,dataset)
        summary = dataset['summary']
        summary['records'] = len(dataset['records'])
        summary['cohorts'] = dataset['descriptive_cohorts']
        # Recheck input after I/O: a new upload/correction cannot publish an old
        # result over a newer source partition, even if a lease was superseded.
        db.session.rollback()
        current = Task.query.filter_by(id=identifier).with_for_update().one()
        if current.input_hash != digest or current.lease_token != token:
            db.session.rollback()
            return False
        state = 'requires_review' if summary.get('review_records') else 'weather_incomplete' if summary.get('weather_unmatched') or summary.get('incomplete_windows') else 'ready'
        return finish(identifier,token,status=state,result_key=key,summary=summary,retry_at=datetime.utcnow()+timedelta(days=1))
    except Exception as exc:
        defer(identifier,token,exc)
        return False


def drain(gateway_id, *, limit=1):
    """Use one transport per small batch; release cyclic SDK resources after it."""
    if local_execution_required() or not memory_allows_work():
        return 0
    # A gunicorn master and worker can both wake the archive scheduler. The
    # process-local lock is insufficient: serialize FROST batches in PostgreSQL
    # so two jobs cannot independently spend the same container headroom.
    batch=task(gateway_id,'worker','memory-budget','frost-worker-v1')
    identifier=batch.id
    token=claim(batch)
    if not token:
        return 0
    failed=False
    try:
        with vault.transport_scope():
            return _drain(gateway_id,limit=limit)
    except Exception:
        failed=True
        raise
    finally:
        gc.collect()
        finish(identifier,token,status='retry' if failed else 'ready',
            retry_at=datetime.utcnow()+timedelta(seconds=60 if failed else 0))


def resolve_records(source, read):
    """Resolve one existing source partition; no weather requests/calculation."""
    fragments,records = {},[]
    for pointer in source['record_pointers']:
        key=pointer['fragment_key']
        if key not in fragments:
            fragments[key]={item['source']['source_row']:item for item in read(key)['records']}
        selected=fragments[key][pointer['source']['source_row']]
        if selected['row_hash']!=pointer['row_hash']:raise ValueError('Historical fragment row hash mismatch.')
        record=dict(selected,sources=pointer['sources'],revision_evidence=pointer['revision_evidence'],
            revision_conflict=pointer['revision_conflict'],requires_review=pointer['requires_review'])
        if pointer['revision_conflict']:record['classification'],record['fluids']=analysis.classification(None),[]
        records.append(record)
    return records


def _drain(gateway_id, *, limit=1):
    """Bounded background work. Never waits in an upload/spray request."""
    discover(gateway_id)
    identifiers = [row.id for row in Task.query.filter_by(gateway_id=gateway_id,kind='partition',version=analysis.VERSION).filter(
        Task.status.in_(('pending','retry')),Task.retry_at <= datetime.utcnow()).order_by(Task.identity).limit(limit).all()]
    db.session.commit()
    return sum(process(db.session.get(Task,identifier)) for identifier in identifiers)


def health(gateway_id):
    rows = Task.query.filter_by(gateway_id=gateway_id,version=analysis.VERSION).all()
    archive_version=analysis.VERSION
    if not rows:
        # A LOCAL feature revision must not hide immutable published evidence.
        # This is display/retrieval compatibility, never a readiness upgrade.
        archive_version='frost-history-v2'
        rows=Task.query.filter_by(gateway_id=gateway_id,version=archive_version).all()
    catalog = next((row for row in rows if row.kind=='catalog'),None)
    partitions = [row for row in rows if row.kind=='partition' and row.status!='superseded']
    totals = Counter()
    for row in partitions:
        # A previous result pointer is retained for history, but never counted
        # as current after a changed import invalidates its source hash.
        if row.status not in ('pending','retry','local_only'):
            totals.update({key:value for key,value in (row.summary or {}).items() if isinstance(value,int)})
    pending = sum(row.status in ('pending','retry') for row in partitions)
    errors = [dict(month=row.identity,error=row.last_error) for row in rows if row.last_error]
    documents = FrostDocument.query.filter_by(gateway_id=gateway_id).all()
    fingerprint = vault.digest(vault.canonical([(d.id,d.sha256,d.extracted_key,d.extraction_status) for d in sorted(documents,key=lambda d:d.id) if d.archived_at_utc]))
    uncataloged = not catalog or catalog.input_hash != fingerprint or catalog.status != 'ready'
    pending_sources = sum(not bool(d.archived_at_utc) for d in documents)
    state = 'REQUIRES REVIEW' if errors or totals.get('review_records') or catalog and catalog.summary.get('source_documents_requiring_review') else 'WEATHER INCOMPLETE' if uncataloged or pending_sources or pending or totals.get('weather_unmatched') or totals.get('incomplete_windows') or not partitions else 'DATA READY'
    if any(r.status=='local_only' for r in partitions) and state!='REQUIRES REVIEW':state='LOCAL RESEARCH ONLY'
    seasonal = defaultdict(lambda: dict(applications=0,fields=defaultdict(lambda: dict(samples=0,total=0.0)),directions=Counter()))
    for row in partitions:
        if row.status in ('pending','retry','local_only'): continue
        year = int(row.identity[:4]) if row.identity != 'unknown' else 0
        first = year - (int(row.identity[5:7]) < 7) if year else 0
        season = f'{first}-{first+1}' if year else 'unknown'
        for label,cohort in (row.summary or {}).get('cohorts',{}).items():
            group = seasonal[(season,label)]
            group['applications'] += cohort['applications']
            for field,values in cohort.items():
                if field == 'applications': continue
                if field == 'wind_direction_degrees':
                    group['directions'].update(values['sectors'])
                else:
                    group['fields'][field]['samples'] += values['samples']
                    group['fields'][field]['total'] += values['mean']*values['samples']
    cohorts = [dict(season=season,label=label,applications=group['applications'],
        fields={key:dict(samples=values['samples'],mean=values['total']/values['samples']) for key,values in group['fields'].items() if values['samples']},
        wind_sectors=dict(group['directions'])) for (season,label),group in sorted(seasonal.items())]
    return dict(state=state,version=archive_version,current_feature_version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,catalog=catalog.summary if catalog else {},
        totals=dict(totals),pending_partitions=pending,partitions=len(partitions),errors=errors,
        pending_sources=pending_sources,uncataloged_sources=uncataloged,
        local_only_partitions=sum(r.status=='local_only' for r in partitions),
        descriptive_cohorts=cohorts,
        last_success=max((row.updated_at.isoformat()+'Z' for row in partitions if row.result_key and row.status not in ('pending','retry','local_only')),default=None),
        partition_links=[dict(id=row.id,month=row.identity,status=row.status) for row in partitions if row.result_key and row.status not in ('pending','retry','local_only')])
