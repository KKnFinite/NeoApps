"""Private dataset manifests and explicit model approvals. Never trains."""
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import FrostHistoryTask, FrostModelArtifact, FrostModelRelease, Gateway
from app.services import frost_analysis, frost_history, frost_model as model, frost_vault as vault, frost_weather


def dataset_manifest(gateway_id):
    health = frost_history.health(gateway_id)
    rows = FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='partition',version=frost_analysis.VERSION).order_by(FrostHistoryTask.identity).all()
    partitions = [dict(id=r.id, month=r.identity, status=r.status, input_hash=r.input_hash,
        sha256=r.result_key.rsplit('/',1)[-1].split('.')[0] if r.result_key else None,
        url=f'/neosubzero/frost-vault/history/{r.id}', records=r.summary.get('records',0))
        for r in rows if r.status != 'superseded']
    weather=FrostHistoryTask.query.filter_by(gateway_id=gateway_id,kind='weather',version=frost_weather.VERSION,status='ready').order_by(FrostHistoryTask.identity).all()
    snapshot = dict(schema='frost-local-dataset-v1', gateway_id=gateway_id, version=frost_analysis.VERSION,
        time_contract=frost_analysis.TIME_CONTRACT, source_origin='authenticated_frost_vault',
        catalog=health['catalog'], partitions=partitions, state=health['state'], totals=health['totals'],
        ready=health['state']=='DATA READY', errors=health['errors'], last_success=health['last_success'],
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
    return dict(revision=release.id if release else 0, approved_version=artifact.model_version if artifact else None,
        candidates=[dict(id=r.id, version=r.model_version, status=r.status, sha256=r.sha256,
            metadata=r.metadata_summary,previously_approved=r.id in previously) for r in rows],
        last_forecast=forecast.summary.get('calculated_at_utc') if forecast else None,
        next_before=rows[-1].id if len(rows)==20 else None,
        message='Local training only. No operational prediction is enabled without an explicit approved release.')


def register(gateway_id, actor_id, raw):
    artifact = model.decode(raw)
    if artifact['training'].get('test_fixture'):raise ValueError('Synthetic resource/test fixtures cannot be registered as candidate models.')
    canonical = vault.canonical(artifact)
    if len(canonical)>model.MAX_BYTES: raise ValueError('Model exceeds runtime size limit.')
    digest = vault.digest(canonical)
    existing = FrostModelArtifact.query.filter_by(gateway_id=gateway_id,model_version=artifact['model_version']).one_or_none()
    if existing:
        if existing.sha256 != digest: raise ValueError('This model version already has different immutable evidence.')
        return existing
    manifest = dataset_manifest(gateway_id)
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


def release(gateway_id, actor_id, identifier, expected_revision, reason, *, rollback=False):
    if not reason or len(reason)>240: raise ValueError('Record a concise approval/rollback reason (up to 240 characters).')
    row = FrostModelArtifact.query.filter_by(id=identifier,gateway_id=gateway_id).one_or_none()
    if row is None: raise ValueError('Model is not available in this gateway.')
    artifact = vault.read_json(row.vault_key,gateway_id,max_bytes=model.MAX_BYTES)
    model.decode(vault.canonical(artifact),row.sha256)
    if not all(model.quality_gates(artifact['evaluation']).values()): raise ValueError('Candidate evaluation gates have not passed.')
    manifest = dataset_manifest(gateway_id) if not rollback else None
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
    elif not manifest['ready'] or artifact['dataset']['fingerprint'] != manifest['fingerprint'] or dataset_manifest(gateway_id)['fingerprint'] != manifest['fingerprint']:
        db.session.rollback();raise ValueError('Phase 5 data readiness/freshness gate blocks approval.')
    item = FrostModelRelease(gateway_id=gateway_id,artifact_id=identifier,actor_user_id=actor_id,
        action='rollback' if rollback else 'approve',reason=reason)
    db.session.add(item);db.session.commit()
    return item
