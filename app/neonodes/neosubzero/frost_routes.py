"""Minimal private source-evidence administration; never writes spray records."""
import io
from flask import abort,flash,jsonify,render_template,request,send_file
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.neonodes.neosubzero import bp
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import FrostDocument,FrostDocumentAudit,NeoSubZeroArchiveDelivery
from app.services.access_control import get_current_gateway
from app.services.permission_rules import user_can
from app.services import frost_documents as documents,frost_vault as vault
from app.services.frost_xchange import MAX_DOCUMENT_BYTES
from app.services.neoscorpion_learning_vault import learning_vault_status


@bp.before_app_request
def frost_upload_limit():
    if request.path=='/neosubzero/frost-vault' and request.method=='POST':
        request.max_content_length=MAX_DOCUMENT_BYTES+65536


def authorized():
    # Reuse MotherBrain's configured SubZero administration authority.
    if not user_can('neosubzero.settings.edit'): abort(403)
    return get_current_gateway()


def catalog(gateway):
    return FrostDocument.query.filter_by(gateway_id=gateway.id).order_by(FrostDocument.id.desc())


@bp.route('/frost-vault',methods=['GET','POST'])
@gateway_node_required('subzero')
def frost_vault():
    gateway=authorized();error=None;status=200
    if request.method=='POST':
        try:
            upload=request.files.get('document')
            if upload is None: raise ValueError('Choose an original CSV export.')
            raw=upload.stream.read(MAX_DOCUMENT_BYTES+1)
            document,created=documents.stage(gateway,current_user,upload.filename,raw)
            db.session.commit()
            if request.headers.get('X-Requested-With')=='XMLHttpRequest':
                return jsonify(ok=created,message='Original safely staged; off-site verification pending.' if created else 'Exact duplicate already preserved.',document_id=document.id),200 if created else 409
            flash('Original safely staged; off-site verification pending.' if created else 'Exact duplicate already preserved.','success' if created else 'error')
        except (ValueError,IntegrityError) as exc:
            db.session.rollback();error=str(exc) if isinstance(exc,ValueError) else 'This document was imported by another request. Reload the catalog; no duplicate was created.'
            status=400 if isinstance(exc,ValueError) else 409
            if request.headers.get('X-Requested-With')=='XMLHttpRequest': return jsonify(ok=False,error=error),status
    before=request.args.get('before',type=int)
    query=catalog(gateway)
    if before: query=query.filter(FrostDocument.id<before)
    items=query.limit(51).all()
    return render_template('neonodes/neosubzero/frost_vault.html',gateway=gateway,
        documents=items[:50],next_before=items[49].id if len(items)>50 else None,error=error,
        vault_status=learning_vault_status(),categories=vault.CATEGORIES,
        receipts=NeoSubZeroArchiveDelivery.query.filter_by(gateway_id=gateway.id).order_by(NeoSubZeroArchiveDelivery.id.desc()).limit(100).all()),status


@bp.route('/frost-vault/status')
@gateway_node_required('subzero')
def frost_vault_status():
    gateway=authorized()
    rows=catalog(gateway).limit(100).all()
    response=jsonify(ok=True,documents=[dict(id=row.id,archived=bool(row.archived_at_utc),error=row.last_error,
        attempts=row.attempts) for row in rows])
    response.headers['Cache-Control']='no-store'
    return response


@bp.route('/frost-vault/documents/<int:identifier>')
@gateway_node_required('subzero')
def frost_document(identifier):
    gateway=authorized()
    row=FrostDocument.query.filter_by(id=identifier,gateway_id=gateway.id).first_or_404()
    try:
        if request.args.get('view')=='extracted':
            if not row.extracted_key: return 'Off-site extraction delivery is pending. Original is preserved.',409
            raw=vault.canonical(vault.read_json(row.extracted_key,gateway.id))
            filename=f'frost-document-{row.id}-extracted.json'
        else:
            raw=documents.original(row);filename=row.original_filename
        response=send_file(io.BytesIO(raw),mimetype='application/octet-stream',as_attachment=True,download_name=filename)
        response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
        return response
    except ValueError:
        return 'Source could not be verified or retrieved. Evidence has not been changed; contact an administrator.',503


@bp.route('/frost-vault/evidence/<int:identifier>')
@gateway_node_required('subzero')
def frost_spray_evidence(identifier):
    gateway=authorized()
    row=NeoSubZeroArchiveDelivery.query.filter_by(id=identifier,gateway_id=gateway.id).first_or_404()
    if not row.vault_key: return 'Archive delivery is pending.',409
    try:
        payload=vault.read_json(row.vault_key,gateway.id)
        response=send_file(io.BytesIO(vault.canonical(payload)),mimetype='application/octet-stream',
            as_attachment=True,download_name=f'frost-spray-{row.id}.json')
        response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})
        return response
    except ValueError: return 'Archived evidence could not be verified. Original reference preserved.',503
