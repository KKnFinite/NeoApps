"""Authenticated callers stage immutable originals; the existing worker retries R2."""
import base64
import hashlib
from datetime import date,datetime,timedelta
from cryptography.fernet import Fernet,MultiFernet,InvalidToken
from flask import current_app
from app.extensions import db
from app.models import FrostDocument as Document,FrostDocumentAudit as Audit
from app.services import frost_vault as vault
from app.services.frost_xchange import extract,MAX_DOCUMENT_BYTES,PARSER_VERSION


def cipher():
    keys=[current_app.config.get('SECRET_KEY'),*(current_app.config.get('SECRET_KEY_FALLBACKS') or [])]
    if not keys[0]: raise ValueError('Secure FROST staging is unavailable.')
    return MultiFernet([Fernet(base64.urlsafe_b64encode(hashlib.sha256(
        b'neoapps.frost-original.v1\0'+str(key).encode()).digest())) for key in keys if key])


def stage(gateway,actor,filename,raw):
    filename=str(filename or '')
    if (not filename or len(filename)>240 or any(c in filename for c in ('/','\\',':','\x00','\r','\n'))
            or any(ord(c)<32 for c in filename) or not filename.lower().endswith('.csv')):
        raise ValueError('Upload an original .csv export with a filename, not a path.')
    parsed=extract(raw,gateway_code=gateway.code)
    if any('Source gateway does not match' in issue['message'] for issue in parsed['issues']):
        raise ValueError('The export contains another gateway. Import it in its authorized gateway instead.')
    checksum=vault.digest(raw)
    existing=Document.query.filter_by(gateway_id=gateway.id,sha256=checksum).one_or_none()
    if existing:
        db.session.add(Audit(document_id=existing.id,actor_user_id=actor.id,action='duplicate_upload_rejected',details={'attempted_filename':filename,'sha256':checksum}))
        return existing,False
    summary={key:parsed.get(key) for key in ('source_row_count','contribution_count','issues','missing_contract_fields','timestamp_basis')}
    document=Document(gateway_id=gateway.id,original_filename=filename,sha256=checksum,
        byte_length=len(raw),imported_by_user_id=actor.id,
        document_type='xchange-deicing-log-csv' if not parsed['missing_contract_fields'] else 'unsupported-csv',
        extraction_status=parsed['status'],extraction_summary=summary,staged_bytes=cipher().encrypt(raw),
        date_start=date.fromisoformat(parsed['date_start']) if parsed.get('date_start') else None,
        date_end=date.fromisoformat(parsed['date_end']) if parsed.get('date_end') else None)
    db.session.add(document);db.session.flush()
    db.session.add(Audit(document_id=document.id,actor_user_id=actor.id,action='import',details={
        'sha256':checksum,'filename':filename,'byte_length':len(raw),'parser':PARSER_VERSION,
        'extraction_status':parsed['status'],'training_eligible':False}))
    return document,True


def original(document):
    encrypted=bytes(document.staged_bytes) if document.staged_bytes is not None else vault.read_bytes(document.original_key,document.gateway_id)
    try: raw=cipher().decrypt(encrypted)
    except InvalidToken: raise ValueError('Source document decryption failed; no evidence was changed.') from None
    if len(raw)!=document.byte_length or vault.digest(raw)!=document.sha256:
        raise ValueError('Original document hash verification failed.')
    return raw


def drain(gateway_id,*,limit=2):
    now=datetime.utcnow()
    rows=Document.query.filter_by(gateway_id=gateway_id,archived_at_utc=None).filter(
        Document.retry_at<=now).order_by(Document.id).limit(limit).with_for_update(skip_locked=True).all()
    identifiers=[]
    for row in rows:
        row.attempts+=1;row.retry_at=now+timedelta(seconds=min(3600,30*2**min(row.attempts,6)))
        identifiers.append(row.id)
    db.session.commit()
    delivered=0
    for identifier in identifiers:
        row=db.session.get(Document,identifier)
        try:
            from app.models import Gateway
            gateway=db.session.get(Gateway,gateway_id)
            raw=original(row);parsed=extract(raw,gateway_code=gateway.code)
            encrypted=bytes(row.staged_bytes)
            key=f'{vault.namespace(gateway_id,"original-xchange")}{row.sha256}.csv.enc'
            metadata=dict(schema_version='frost-source-manifest/v1',document_id=row.id,gateway_id=gateway_id,
                original_filename=row.original_filename,sha256=row.sha256,byte_length=row.byte_length,
                imported_at_utc=row.imported_at_utc,imported_by_user_id=row.imported_by_user_id,
                parser=PARSER_VERSION,extraction_status=row.extraction_status,
                evidence_kind='source_document',training_eligible=False,
                audit=[dict(id=a.id,actor=a.actor_user_id,action=a.action,utc=a.recorded_at_utc,details=a.details)
                    for a in Audit.query.filter_by(document_id=row.id).order_by(Audit.id).all()])
            db.session.rollback()  # No operational/database lock spans provider I/O.
            vault.put_bytes(key,encrypted,gateway_id)
            parsed['source_document_id']=identifier;parsed['source_original_key']=key
            extracted=vault.put_json('extracted-xchange',gateway_id,str(identifier),parsed)
            metadata.update(original_key=key,extracted_key=extracted)
            manifest=vault.put_json('manifests',gateway_id,'document-'+str(identifier),metadata)
            row=Document.query.filter_by(id=identifier,gateway_id=gateway_id).with_for_update().one()
            row.original_key=key;row.extracted_key=extracted;row.manifest_key=manifest
            row.archived_at_utc=datetime.utcnow();row.last_error=None;row.staged_bytes=None
            db.session.add(Audit(document_id=identifier,action='archive_verified',details={
                'original_key':key,'extracted_key':extracted,'manifest_key':manifest,'sha256':row.sha256}))
            db.session.commit();delivered+=1
        except Exception:
            db.session.rollback()
            row=db.session.get(Document,identifier)
            row.last_error='Off-site synchronization failed. Encrypted original retained; automatic retry pending.'
            db.session.commit()
            current_app.logger.warning('FROST document delivery deferred: document=%s',identifier,exc_info=True)
    return delivered
