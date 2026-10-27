"""Private dataset manifests and explicit model approvals. Never trains."""
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import FrostHistoryTask, FrostModelArtifact, FrostModelRelease, Gateway,FrostDocument,FrostDocumentAudit
from app.services import frost_analysis, frost_history, frost_model as model, frost_vault as vault, frost_weather
from app.services import frost_dataset_contract as local_contract


def source_catalog(gateway_id):
    gateway=db.session.get(Gateway,gateway_id)
    documents=FrostDocument.query.filter_by(gateway_id=gateway_id).order_by(FrostDocument.id).all()
    return local_contract.seal(dict(gateway_id=gateway_id,gateway_code=gateway.code,
        documents=[dict(id=d.id,sha256=d.sha256,filename=d.original_filename,bytes=d.byte_length,
            source_rows=d.extraction_summary.get('source_row_count',0),extracted_key=d.extracted_key,
            verified=bool(d.archived_at_utc),extraction_status=d.extraction_status) for d in documents]))


def verify_local(artifact,gateway_id):
    manifest=local_contract.validate(artifact['local_dataset_manifest'],sources=source_catalog(gateway_id),training=True)
    if artifact['dataset']['fingerprint']!=manifest['fingerprint'] or artifact['training']['source_records']!=manifest['catalog']['deduplicated_records'] or artifact['training']['examples']!=manifest['eligibility']['examples']:
        raise ValueError('Model and validated local dataset counts/fingerprint do not reconcile.')
    if artifact['evaluation']['lead_hours_validated']!=[manifest['eligibility']['lead_hours']] or not set(artifact['seasons'])<=set(manifest['catalog']['seasons']):
        raise ValueError('Local dataset season/lead contract mismatch.')
    return manifest


def retire_publication(gateway_id,actor_id):
    """Explicit authorized retirement, with prior state in existing audit rows."""
    Gateway.query.filter_by(id=gateway_id).with_for_update().one()
    worker=FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='worker',identity='memory-budget',version='frost-worker-v1').one_or_none()
    if worker and worker.lease_token and worker.retry_at>datetime.utcnow():raise ValueError('Historical worker lease is still active; no task retired.')
    rows=FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='partition').filter(FrostHistoryTask.status.in_(('pending','retry'))).with_for_update().all()
    inventory=[dict(id=r.id,month=r.identity,status=r.status,retry_at=r.retry_at.isoformat(),lease_token_present=bool(r.lease_token),
        input_hash=r.input_hash,source_key=r.source_key,result_key=r.result_key,records=r.summary.get('records',0)) for r in rows]
    if not inventory:db.session.rollback();return []
    for row in rows:
        row.status='local_only';row.lease_token=None;row.updated_at=datetime.utcnow()
        row.summary=dict(row.summary,retired_from='bulk_publication',retired_by=actor_id,retired_at_utc=row.updated_at.isoformat()+'Z')
    for document in FrostDocument.query.filter_by(gateway_id=gateway_id).all():
        db.session.add(FrostDocumentAudit(document_id=document.id,actor_user_id=actor_id,action='historical_publication_retired',
            details=dict(reason='Local training architecture; NOT processed on Render',tasks=inventory)))
    db.session.commit();return inventory


def dataset_manifest(gateway_id):
    health = frost_history.health(gateway_id)
    rows = FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='partition',version=health['version']).order_by(FrostHistoryTask.identity).all()
    partitions = [dict(id=r.id, month=r.identity, status=r.status, input_hash=r.input_hash,
        sha256=r.result_key.rsplit('/',1)[-1].split('.')[0] if r.result_key else None,
        url=f'/neosubzero/frost-vault/history/{r.id}', records=r.summary.get('records',0))
        for r in rows if r.status != 'superseded']
    weather=FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='weather',status='ready').filter(FrostHistoryTask.version.in_((frost_weather.VERSION,frost_weather.LEGACY_VERSION))).order_by(FrostHistoryTask.identity,FrostHistoryTask.version).all()
    snapshot = dict(schema='frost-local-dataset-v1', gateway_id=gateway_id, version=health['version'],
        time_contract=frost_analysis.TIME_CONTRACT, source_origin='authenticated_frost_vault',
        catalog=health['catalog'], partitions=partitions, state=health['state'], totals=health['totals'],
        ready=health['state']=='DATA READY', errors=health['errors'], last_success=health['last_success'],
        source_catalog=source_catalog(gateway_id),
        weather=[dict(id=r.id,month=r.identity,status='ready',version=r.version,
            sha256=r.result_key.rsplit('/',1)[-1].split('.')[0],url=f'/neosubzero/frost-vault/weather/{r.id}') for r in weather if r.result_key])
    snapshot['fingerprint'] = vault.digest(vault.canonical(snapshot))
    return snapshot


def current_release(gateway_id):
    return FrostModelRelease.query.filter_by(gateway_id=gateway_id).order_by(FrostModelRelease.id.desc()).first()


def status(gateway_id, *, before=None):
    release = current_release(gateway_id)
    artifact = db.session.get(FrostModelArtifact, release.artifact_id) if release and release.artifact_id else None
    query=FrostModelArtifact.query.filter_by(gateway_id=gateway_id)
    if before:query=query.filter(FrostModelArtifact.id<before)
    rows = query.order_by(FrostModelArtifact.id.desc()).limit(20).all()
    previously={r[0] for r in db.session.query(FrostModelRelease.artifact_id).filter(
        FrostModelRelease.gateway_id==gateway_id,FrostModelRelease.artifact_id.in_([r.id for r in rows])).distinct()}
    forecast=FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='forecast-status',identity='latest',version=model.VERSION).one_or_none()
    catalog_fingerprint=source_catalog(gateway_id)['fingerprint']
    legacy_ready=None
    if any(not r.metadata_summary.get('local_dataset') for r in rows):legacy_ready=frost_history.health(gateway_id)['state']=='DATA READY'
    return dict(revision=release.id if release else 0, approved_version=artifact.model_version if artifact else None,
        candidates=[dict(id=r.id, version=r.model_version, status=r.status, sha256=r.sha256,
            metadata=r.metadata_summary,previously_approved=r.id in previously,
            approval_ready=r.status=='validated' and r.metadata_summary.get('classification_version')==frost_analysis.VERSION and (r.metadata_summary.get('source_catalog_fingerprint')==catalog_fingerprint if r.metadata_summary.get('local_dataset') else legacy_ready)) for r in rows],
        last_forecast=forecast.summary.get('calculated_at_utc') if forecast else None,
        next_before=rows[-1].id if len(rows)==20 else None,
        message='Local training only. No operational prediction is enabled without an explicit approved release.')


@vault._bounded_transport
def register(gateway_id, actor_id, raw):
    if not frost_history.memory_allows_work():raise RuntimeError('Insufficient server headroom for model submission. Keep the local artifact; retry when safe.')
    artifact = model.decode(raw)
    if artifact['training'].get('test_fixture'):raise ValueError('Synthetic resource/test fixtures cannot be registered as candidate models.')
    canonical = vault.canonical(artifact)
    if len(canonical)>model.MAX_BYTES: raise ValueError('Model exceeds runtime size limit.')
    digest = vault.digest(canonical)
    existing = FrostModelArtifact.query.filter_by(gateway_id=gateway_id,model_version=artifact['model_version']).one_or_none()
    if existing:
        if existing.sha256 != digest: raise ValueError('This model version already has different immutable evidence.')
        return existing
    local=bool(artifact.get('local_dataset_manifest'))
    manifest = verify_local(artifact,gateway_id) if local else dataset_manifest(gateway_id)
    if artifact['dataset']['fingerprint'] != manifest['fingerprint'] or artifact['dataset'].get('source_origin') != 'authenticated_frost_vault':
        raise ValueError('Model dataset is not the current authenticated historical snapshot. Refresh locally.')
    if artifact['training']['source_records']!=manifest['catalog'].get('deduplicated_records') or not set(artifact['seasons'])<=set(manifest['catalog'].get('seasons',[])):
        raise ValueError('Model source counts/seasons do not reconcile with the historical catalog.')
    # Candidate upload is never promotion. The raw JSON remains private and
    # immutable; SQL stores only small review metadata, not trees or history.
    gates = model.quality_gates(artifact['evaluation'])
    manifest_key=vault.put_json('manifests',gateway_id,'model-dataset-'+manifest['fingerprint'],manifest)
    key = vault.put_json('models',gateway_id,digest,artifact)
    metadata = {k:artifact[k] for k in ('dataset','target','trained_at_utc','seasons','evaluation','limitations','classification_version','runtime')}
    metadata['gates'] = gates
    metadata['dataset_manifest_key'] = manifest_key
    metadata['local_dataset']=local
    if local:
        metadata['source_catalog_fingerprint']=manifest['source_catalog']['fingerprint']
        metadata['local_validation']=dict(state=manifest['state'],source_records=manifest['catalog']['deduplicated_records'],
            eligible_examples=manifest['eligibility']['examples'],classes=manifest['eligibility']['classes'],
            weather_matched=manifest['quality']['weather_matched'],weather_unmatched=manifest['quality']['weather_unmatched'],
            incomplete_windows=manifest['quality']['incomplete_window'])
    row = FrostModelArtifact(gateway_id=gateway_id,model_version=artifact['model_version'],sha256=digest,
        vault_key=key,status='validated' if all(gates.values()) and manifest['ready'] else 'candidate',
        metadata_summary=metadata,imported_by_user_id=actor_id)
    db.session.add(row)
    try: db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = FrostModelArtifact.query.filter_by(gateway_id=gateway_id,model_version=artifact['model_version']).one()
        if existing.sha256 != digest: raise ValueError('Concurrent model version conflict; nothing replaced.')
        return existing
    return row


@vault._bounded_transport
def release(gateway_id, actor_id, identifier, expected_revision, reason, *, rollback=False):
    if not frost_history.memory_allows_work():raise RuntimeError('Insufficient server headroom for model approval. Existing release unchanged.')
    if not reason or len(reason)>240: raise ValueError('Record a concise approval/rollback reason (up to 240 characters).')
    row = FrostModelArtifact.query.filter_by(id=identifier,gateway_id=gateway_id).one_or_none()
    if row is None: raise ValueError('Model is not available in this gateway.')
    artifact = vault.read_json(row.vault_key,gateway_id,max_bytes=model.MAX_BYTES)
    model.decode(vault.canonical(artifact),row.sha256)
    if not all(model.quality_gates(artifact['evaluation']).values()): raise ValueError('Candidate evaluation gates have not passed.')
    local=bool(artifact.get('local_dataset_manifest'))
    manifest = (verify_local(artifact,gateway_id) if local else dataset_manifest(gateway_id)) if not rollback else None
    db.session.commit()  # No operational row locks during remote storage I/O.
    Gateway.query.filter_by(id=gateway_id).with_for_update().one()
    latest = current_release(gateway_id)
    if latest and latest.artifact_id==identifier and latest.actor_user_id==actor_id and latest.reason==reason and latest.action==('rollback' if rollback else 'approve'):
        db.session.rollback();return latest
    if (latest.id if latest else 0) != expected_revision:
        db.session.rollback();raise ValueError('Model approval changed in another session. Reload current values; your inputs are retained.')
    if rollback:
        if not FrostModelRelease.query.filter_by(gateway_id=gateway_id,artifact_id=identifier).first():
            db.session.rollback();raise ValueError('Rollback requires a previously approved model.')
    elif not manifest['ready'] or artifact['dataset']['fingerprint'] != manifest['fingerprint'] or (verify_local(artifact,gateway_id)['fingerprint'] if local else dataset_manifest(gateway_id)['fingerprint']) != manifest['fingerprint']:
        db.session.rollback();raise ValueError('Validated dataset readiness/freshness gate blocks approval.')
    item = FrostModelRelease(gateway_id=gateway_id,artifact_id=identifier,actor_user_id=actor_id,
        action='rollback' if rollback else 'approve',reason=reason)
    db.session.add(item);db.session.commit()
    return item
