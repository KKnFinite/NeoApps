"""Minimal private source-evidence administration; never writes spray records."""
import io
from flask import abort,flash,jsonify,render_template,request,send_file
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from app.neonodes.neosubzero import bp
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import FrostDocument,FrostDocumentAudit,NeoSubZeroArchiveDelivery,FrostHistoryTask
from app.services.access_control import get_current_gateway
from app.services.permission_rules import user_can
from app.services import frost_documents as documents,frost_vault as vault
from app.services.frost_xchange import MAX_DOCUMENT_BYTES
from app.services.neoscorpion_learning_vault import learning_vault_status
from app.services import frost_history as history, frost_analysis
from app.services import frost_model as model, frost_model_registry as models
from app.services import frost_local_backfill as backfill
from app.services import frost_research


@bp.before_app_request
def frost_upload_limit():
    if request.path=='/neosubzero/frost-vault' and request.method=='POST':
        request.max_content_length=MAX_DOCUMENT_BYTES+65536
    elif request.path=='/neosubzero/frost-vault/models' and request.method=='POST':
        request.max_content_length=model.MAX_BYTES+65536
    elif request.path=='/neosubzero/frost-vault/backfill/publish' and request.method=='POST':
        request.max_content_length=backfill.MAX_ZIP+65536


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
    imported_id=request.args.get('imported',type=int)
    imported=catalog(gateway).filter_by(id=imported_id).one_or_none() if imported_id else None
    import_message=f'IMPORTED: {imported.original_filename}. Original securely saved.' if imported else None
    return render_template('neonodes/neosubzero/frost_vault.html',gateway=gateway,
        documents=items[:50],next_before=items[49].id if len(items)>50 else None,error=error,import_message=import_message,
        vault_status=learning_vault_status(),categories=vault.CATEGORIES,history=history.health(gateway.id),
        model_status=models.status(gateway.id,before=request.args.get('models_before',type=int)),
        research=frost_research.status(),
        local_backfill=backfill.pending(gateway.id),
        receipts=NeoSubZeroArchiveDelivery.query.filter_by(gateway_id=gateway.id).order_by(NeoSubZeroArchiveDelivery.id.desc()).limit(100).all()),status


@bp.route('/frost-vault/forecast-validation')
@gateway_node_required('subzero')
def frost_forecast_validation():
    authorized()
    study=frost_research.status()
    if study is None:
        return jsonify(error='Research summary unavailable; no successful validation claimed.'),503
    response=jsonify(study)
    response.headers['Cache-Control']='private, no-store'
    response.headers['Content-Disposition']='attachment; filename="frost-forecast-research.json"'
    response.headers['X-Content-Type-Options']='nosniff'
    return response


@bp.route('/frost-vault/status')
@gateway_node_required('subzero')
def frost_vault_status():
    gateway=authorized()
    rows=catalog(gateway).limit(100).all()
    response=jsonify(ok=True,history=history.health(gateway.id),documents=[dict(id=row.id,archived=bool(row.archived_at_utc),error=row.last_error,
        attempts=row.attempts) for row in rows])
    response.headers['Cache-Control']='no-store'
    return response


@bp.route('/frost-vault/dataset-manifest')
@gateway_node_required('subzero')
def frost_dataset_manifest():
    from app.services.csrf import csrf_token
    gateway=authorized()
    response=jsonify(manifest=models.dataset_manifest(gateway.id),csrf_token=csrf_token())
    response.headers['Cache-Control']='private, no-store'
    return response


@bp.route('/frost-vault/backfill/<int:identifier>')
@gateway_node_required('subzero')
def frost_backfill_export(identifier):
    authorized()
    return 'Bulk historical publication is retired. Use frost_local assemble and submit only a compact model artifact.',410


@bp.route('/frost-vault/backfill/publish',methods=['POST'])
@gateway_node_required('subzero')
def frost_backfill_publish():
    authorized()
    return jsonify(ok=False,error='Bulk historical publication is retired. Keep completed local results; submit only a compact model artifact.'),410


@bp.route('/frost-vault/weather/<int:identifier>')
@gateway_node_required('subzero')
def frost_weather_evidence(identifier):
    from app.services import frost_weather
    gateway=authorized()
    if not history.memory_allows_work():return 'Historical evidence transfer deferred for memory safety. Originals and archived results are preserved; use verified local caches.',503
    row=FrostHistoryTask.query.filter_by(id=identifier,gateway_id=gateway.id,kind='weather',status='ready').filter(FrostHistoryTask.version.in_((frost_weather.VERSION,frost_weather.LEGACY_VERSION))).first_or_404()
    try:
        payload=vault.read_json(row.result_key,gateway.id)
        response=send_file(io.BytesIO(vault.canonical(payload)),mimetype='application/octet-stream',as_attachment=True,
            download_name=f'frost-weather-{row.identity}.json')
        response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
        return response
    except (ValueError,RuntimeError):return 'Weather evidence could not be verified; original evidence is preserved.',503


@bp.route('/frost-vault/models',methods=['GET','POST'])
@gateway_node_required('subzero')
def frost_models():
    gateway=authorized()
    if request.method=='POST':
        try:
            action=request.form.get('action')
            message='Model '+str(action)+' recorded.'
            if action=='import':
                file=request.files.get('model')
                if not file or not file.filename.lower().endswith('.json'): raise ValueError('Choose a locally generated JSON model.')
                models.register(gateway.id,current_user.id,file.stream.read(model.MAX_BYTES+1))
            elif action in ('approve','rollback'):
                models.release(gateway.id,current_user.id,int(request.form.get('model_id','')),
                    int(request.form.get('revision','')),request.form.get('reason','').strip(),rollback=action=='rollback')
            elif action=='retire-publication':
                retired=models.retire_publication(gateway.id,current_user.id)
                message=f'RETIRED {len(retired)} legacy publication jobs. Source documents, archived evidence and local results are preserved.'
            else: raise ValueError('Unknown model action. Training is local-only.')
            return jsonify(ok=True,message=message,models=models.status(gateway.id))
        except (ValueError,RuntimeError,KeyError,TypeError) as exc:
            db.session.rollback()
            code=503 if 'headroom' in str(exc).lower() else 409 if any(term in str(exc).lower() for term in ('changed','gate','snapshot','conflict')) else 400
            return jsonify(ok=False,error=str(exc)),code
    response=jsonify(models=models.status(gateway.id))
    response.headers['Cache-Control']='private, no-store'
    return response


@bp.route('/frost-vault/models/forecast',methods=['POST'])
@gateway_node_required('subzero')
def frost_forecast():
    gateway=authorized()
    from app.services.frost_inference import predict
    try:
        result=predict(gateway.id,record=True)
        return jsonify(ok=True,message=result['state'],prediction=result,models=models.status(gateway.id))
    except (ValueError,RuntimeError) as exc:
        db.session.rollback();return jsonify(ok=False,error=str(exc)),503


@bp.route('/frost-vault/history/<int:identifier>')
@gateway_node_required('subzero')
def frost_history_evidence(identifier):
    gateway=authorized()
    if not history.memory_allows_work():return 'Historical evidence transfer deferred for memory safety. Preserve local results and reconstruct missing features on the workstation.',503
    row=FrostHistoryTask.query.filter_by(id=identifier,gateway_id=gateway.id,kind='partition').filter(FrostHistoryTask.version.in_((frost_analysis.VERSION,'frost-history-v2'))).first_or_404()
    if not row.result_key or row.status in ('pending','retry','superseded','local_only'):
        return 'Current historical dataset is not ready; previous evidence is retained.',409
    try:
        payload=vault.read_json(row.result_key,gateway.id)
        response=send_file(io.BytesIO(vault.canonical(payload)),mimetype='application/octet-stream',as_attachment=True,
            download_name=f'frost-research-{row.identity}-{row.version}.json')
        response.headers.update({'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; sandbox"})
        return response
    except (ValueError,RuntimeError):
        return 'Historical evidence could not be verified. Retry later; original sources remain preserved.',503


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
