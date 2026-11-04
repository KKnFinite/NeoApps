"""Request-local critical fragments; no alternate operational state or cache."""
import hashlib
import json
import re

from flask import current_app
from markupsafe import Markup

# Token timestamps may renew without an operational change. Normalize only the
# checksum input, never a delivered response, token, or trusted script element.
_CSRF = re.compile(r'(<input\b[^>]*\bname="csrf_token"[^>]*\bvalue=")[^"]*(")')


def panel_content(html):
    """Private, request-local comparison after authorization and fresh rendering.

    Never cache operational HTML between users or trust a browser's revision as
    authority. A match only omits bytes the same browser already has applied.
    """
    from flask import request
    digest = hashlib.sha256(_CSRF.sub(r'\1<session-token>\2', html).encode()).hexdigest()
    unchanged = request.headers.get('X-Neo-Panel-Digest') == digest
    return dict(html='' if unchanged else html, panel_digest=digest, unchanged=unchanged)


def spear_fragments(context, gateway, access, supplied, *, settings_view=False):
    """SPEAR recalculation never downloads operational forms or fuel history."""
    try:
        previous = json.loads(supplied) if len(supplied) <= 6500 else {}
    except (TypeError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    arguments = dict(context, gateway=gateway, can_view=access['can_view'],
        can_edit=access['can_edit'], can_view_spear_settings=settings_view)
    current_app.update_template_context(arguments)
    hashes, pieces = {}, {}

    def fragment(key, template, **values):
        html = current_app.jinja_env.get_template('neonodes/neoscorpion/' + template).render(
            **dict(arguments, **values))
        digest = hashlib.sha256(_CSRF.sub(r'\1<session-token>\2', html).encode()).hexdigest()[:32]
        if len(hashes) < 80:
            hashes[key] = digest
        if previous.get(key) != digest:
            pieces[key] = html

    fragment('indicator', '_dispatch_spear_indicator.html')
    if context['operation']:
        fragment('overview', '_dispatch_spear_overview.html')
        for row in context['rows']:
            fragment('mission-' + str(row['mission'].id), '_dispatch_spear_region.html', row=row)
            fragment('tail-' + str(row['mission'].id), '_dispatch_spear_tail.html', row=row)
        for truck in context['truck_visuals']:
            fragment('truck-' + str(truck['truck_id']), '_dispatch_truck_spear.html', truck=truck)
    return dict(spear_fragments=pieces, spear_fingerprints=hashes,
        # Existing signed references still bind actions to the exact rendered
        # evidence. Update hidden references as well as the visible explanation.
        spear_references={str(r['mission'].id):r.get('spear_decision_reference', '') for r in context['rows']})


def critical_fragments(context, gateway, access, supplied, *, settings_view=False):
    try:
        previous = json.loads(supplied) if len(supplied) <= 6500 else {}
    except (TypeError, ValueError):
        previous = {}
    if not isinstance(previous, dict):
        previous = {}
    arguments = dict(context, gateway=gateway, can_view=access['can_view'],
        can_edit=access['can_edit'], can_manage_assets=access['can_edit'], can_view_spear_settings=settings_view)
    # Resolve request/template context once, not once per aircraft. In particular
    # shared permission/navigation processors must not turn this into N+1 SQL.
    current_app.update_template_context(arguments)
    def render(template, **values):
        return current_app.jinja_env.get_template(template).render(**dict(arguments, **values))
    hashes, pieces = {}, []

    def fragment(key, template, **values):
        html = render(template, **values)
        digest = hashlib.sha256(_CSRF.sub(r'\1<session-token>\2', html).encode()).hexdigest()
        # Bound round-tripped metadata, not the actual operational population.
        if len(hashes) < 80:
            hashes[key] = digest
        if previous.get(key) != digest:
            pieces.append(html)

    for row in context['rows']:
        fragment(str(row['mission'].id), 'neonodes/neoscorpion/_dispatch_mission_rows.html', row=row)
    assets = render('neonodes/neoscorpion/_nightly_assets.html')
    assets_hash = hashlib.sha256(_CSRF.sub(r'\1<session-token>\2', assets).encode()).hexdigest()
    hashes['assets'] = assets_hash
    operation = context['operation']
    return dict(fingerprints=hashes, compact=True, html=render(
        'neonodes/neoscorpion/_dispatch_critical.html',
        settings=context['settings'], rows_html=Markup(''.join(pieces)),
        assets_html=Markup(assets if previous.get('assets') != assets_hash else ''),
        operation=operation))
