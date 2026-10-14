"""Grandmaster-only, GET-only diagnostic. No operational mutation surface."""
from time import monotonic
from flask import abort, current_app, jsonify, render_template, request
from flask_login import current_user
from app.auth.decorators import gateway_node_required
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.services.neorain_google_overrides import APPROVED
from app.services.neorain_google_runtime import RuntimeGoogleAdapter


@bp.route('/google-certification',methods=['GET'])
@gateway_node_required('rain')
def google_certification():
    if current_user.role != 'grandmaster':abort(403)
    if set(request.args)-{'field'}:abort(400)
    field=request.args.get('field')
    if field is None:return render_template('neonodes/neorain/google_certification.html',fields=APPROVED)
    if field not in APPROVED:abort(400)
    key=(get_current_gateway().id,field)
    cache=current_app.extensions.setdefault('neorain_certification_read_cache',{})
    existing=cache.get(key)
    if existing and monotonic()-existing[0]<30:
        payload=existing[1]
    else:
        try:payload=RuntimeGoogleAdapter(current_app.config).diagnose(field)
        except Exception:
            # Never return provider exception text: it can include tokens, URLs,
            # request bodies, raw credential parser input or private identifiers.
            payload={'field':field,'status':'BLOCKED','reason':'Runtime Google diagnostic unavailable; check configured account access and metadata scopes. No write attempted.'}
        cache[key]=(monotonic(),payload)
    response=jsonify(payload)
    response.headers['Cache-Control']='no-store, private'
    response.headers['Vary']='Cookie'
    return response
