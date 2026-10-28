"""Small, revisable Xchange-to-pre-event evidence index; never spray mutations.

Original exports and immutable source pages retain every revision. Matching is
not model evaluation and never interprets an absent treatment as a negative.
"""
from datetime import timedelta
from types import SimpleNamespace
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_auto as auto, frost_history as jobs, frost_vault as vault
from app.services import frost_analysis as analysis, frost_documents as documents


def queue(document, parsed):
    ctrl=Task.query.filter_by(gateway_id=document.gateway_id,kind='auto_control',identity='collector',version=auto.VERSION).one_or_none()
    start=(ctrl.summary or {}).get('started_at_utc') if ctrl else None
    if not start:return 0
    earliest=(auto.clock.instant(start)-timedelta(days=1)).date().isoformat()
    page=[];count=0
    def save():
        nonlocal count
        count+=1
        payload=dict(schema=auto.VERSION,document_id=document.id,document_sha256=document.sha256,rows=page)
        row=jobs.task(document.gateway_id,'xchange_page',f'{document.id}-{count}',auto.VERSION)
        digest=vault.digest(vault.canonical(payload))
        if row.input_hash and row.input_hash!=digest:raise ValueError('Immutable Xchange source-page conflict.')
        if not row.input_hash:
            row.input_hash=digest
            row.summary=dict(document_id=document.id,records=len(page),
                encrypted_stage=documents.cipher().encrypt(auto.pack(payload).encode()).decode())
    for item in parsed.get('rows',[]):
        if not item.get('application_date') or item['application_date']<earliest:continue
        if not jobs.memory_allows_work():raise RuntimeError('Prospective source staging deferred for memory safety.')
        page.append(analysis.compact_row(item,SimpleNamespace(id=document.id,sha256=document.sha256,extracted_key=document.extracted_key)))
        if len(page)==100:save();page=[]
    if page:save()
    return count


def link(gateway_id,item,page_key):
    if not item.get('source_record_id'):return
    identifier=vault.digest(str(item['source_record_id']).encode())[:32]
    row=jobs.task(gateway_id,'xchange_outcome',identifier,auto.VERSION)
    old=row.summary or {};revision=item.get('modified_utc')
    if row.input_hash==item['row_hash']:return
    conflict=False
    if row.input_hash:
        previous=old.get('modified_utc')
        # Match the established analysis.deduplicate contract: a unique latest
        # documented modification wins; an undated row cannot replace it.
        if previous and (not revision or revision<previous):return
        conflict=revision==previous
    Task.query.filter_by(gateway_id=gateway_id,kind='auto_match',version=auto.VERSION,status='active').filter(
        Task.summary['record_identity'].as_string()==identifier).update(dict(status='superseded'),synchronize_session=False)
    starts=[f['occurred_at_utc'] for f in item.get('fluids',[]) if f.get('occurred_at_utc')]
    at=auto.clock.instant(min(starts)) if starts else None
    review=conflict or item.get('requires_review') or not at
    summary=dict(source=item['source'],page_key=page_key,tail=item.get('tail'),modified_utc=revision,
        classification=item['classification'],event_at_utc=auto.stamp(at) if at else None,
        revision_conflict=conflict,capture_ids=[])
    if not review:
        for h in auto.native.HORIZONS:
            # A target represents the ensuing hour, fixed before observing outcomes.
            cutoff=at-timedelta(hours=h)
            candidates=Task.query.filter_by(gateway_id=gateway_id,kind='auto_capture',version=auto.VERSION,status='ready').filter(
                Task.identity>=auto.identity(cutoff-timedelta(hours=1)),Task.identity<=auto.identity(cutoff)).order_by(Task.identity.desc()).limit(2).all()
            for capture in candidates:
                meta=capture.summary
                target=auto.clock.instant(meta['cutoff_at_utc'])+timedelta(hours=h)
                if auto.clock.instant(meta['committed_at_utc'])>=at or not 0<=(at-target).total_seconds()<3600:continue
                match=jobs.task(gateway_id,'auto_match',vault.digest(f'{identifier}:{capture.id}:{h}'.encode())[:32],auto.VERSION)
                match.status='active';match.input_hash=item['row_hash'];match.source_key=page_key;match.result_key=capture.result_key
                match.summary=dict(record_identity=identifier,capture_id=capture.id,hours=h,event_at_utc=auto.stamp(at),
                    score_available=h in meta.get('scored_hours',[]),classification=item['classification'],source=item['source'])
                summary['capture_ids'].append(capture.id)
                break
    row.status='requires_review' if review else 'matched' if summary['capture_ids'] else 'unmatched'
    row.input_hash=item['row_hash'];row.source_key=page_key;row.summary=summary


def drain(gateway_id):
    if not jobs.memory_allows_work():return 0
    batch=jobs.task(gateway_id,'worker','memory-budget','frost-worker-v1');batch_id=batch.id
    token=jobs.claim(batch,at=auto.naive(auto.now()))
    if not token:return 0
    try:
        row=Task.query.filter_by(gateway_id=gateway_id,kind='xchange_page',version=auto.VERSION).filter(
            Task.status.in_(('pending','retry')),Task.retry_at<=auto.naive(auto.now())).order_by(Task.id).first()
        if row is None:return 0
        identifier=row.id;lease=jobs.claim(row,at=auto.naive(auto.now()))
        if not lease:return 0
        try:
            payload=auto.unpack(documents.cipher().decrypt(row.summary['encrypted_stage'].encode()).decode())
            if len(payload['rows'])>100 or vault.digest(vault.canonical(payload))!=row.input_hash:raise ValueError('Source-page integrity mismatch.')
            with vault.transport_scope():
                key=vault.put_json('frost-observations',gateway_id,'xchange-page-'+str(identifier),payload)
                if vault.digest(vault.canonical(vault.read_json(key,gateway_id,max_bytes=auto.MAX_SNAPSHOT)))!=row.input_hash:raise ValueError('Source-page read-back mismatch.')
            row=Task.query.filter_by(id=identifier).with_for_update().one()
            if row.lease_token!=lease:db.session.rollback();return 0
            for item in payload['rows']:link(gateway_id,item,key)
            row.status='ready';row.source_key=key;row.result_key=key;row.lease_token=None
            row.summary={k:v for k,v in row.summary.items() if k!='encrypted_stage'}
            row.last_error=None;db.session.commit();return 1
        except Exception:
            db.session.rollback();row=Task.query.filter_by(id=identifier).with_for_update().one()
            if row.lease_token==lease:
                row.status='retry';row.lease_token=None;row.retry_at=auto.naive(auto.now()+timedelta(seconds=min(3600,60*2**min(row.attempts,6))))
                row.last_error='Xchange matching deferred; encrypted source and original preserved.';db.session.commit()
            return 0
    finally:jobs.finish(batch_id,token,status='ready',retry_at=auto.naive(auto.now()))
