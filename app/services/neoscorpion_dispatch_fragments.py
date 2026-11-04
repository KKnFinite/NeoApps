"""Request-local critical fragments; no alternate operational state or cache."""
import hashlib
import json
import re

from flask import current_app
from jinja2 import pass_context
from markupsafe import Markup

# Token timestamps may renew without an operational change. Normalize only the
# checksum input, never a delivered response, token, or trusted script element.
_CSRF = re.compile(r'(<input\b[^>]*\bname="csrf_token"[^>]*\bvalue=")[^"]*(")')
_REFERENCE = re.compile(r'(<input\b[^>]*\bname="spear_decision_reference"[^>]*\bvalue=")[^"]*(")')
_RANK = re.compile(r'\sdata-dispatch-(?:arrival|etd)-rank="[^"]*"')


def _previous(supplied):
    try:
        value = json.loads(supplied) if len(supplied) <= 6500 else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or len(value) > 80:
        return {}
    return {k:v for k,v in value.items() if isinstance(k,str) and re.fullmatch(r'[\w-]{1,64}',k)
            and isinstance(v,str) and re.fullmatch(r'[a-f0-9]{32,64}',v)}


def _scope(context, gateway, access, settings_view):
    from app.services.live_screen_refresh import live_poll_scope
    operation = context.get('operation')
    return json.dumps([live_poll_scope(gateway), operation.id if operation else None,
                       bool(access['can_view']), bool(access['can_edit']), bool(settings_view)])


def _digest(html, scope, key):
    # Sort ranks travel in the complete manifest and are applied even when a
    # mission's markup is reused. Reordering one flight need not resend all rows.
    normalized = _RANK.sub('', _REFERENCE.sub(r'\1<current-reference>\2', _CSRF.sub(r'\1<session-token>\2', html)))
    return hashlib.sha256((scope + ':' + key + ':' + normalized).encode()).hexdigest()


class DispatchParts:
    """Wire deltas of canonical templates. No stored HTML, alternate state or I/O.

    Comment boundaries preserve table semantics and carry initial fingerprints.
    Reuse placeholders only reference the same freshly authorized user's DOM.
    Critical fingerprints come from the same pre-SPEAR canonical read, never an
    extra speculative database snapshot or a client claim about newer evidence.
    """
    def __init__(self, context, gateway, access, *, supplied='', critical_supplied='', settings_view=False):
        self.previous, self.critical_previous = _previous(supplied), _previous(critical_supplied)
        self.scope = _scope(context, gateway, access, settings_view)
        critical = context.get('_dispatch_critical_context', context)
        self.arguments = dict(critical, gateway=gateway, can_view=access['can_view'],
            can_edit=access['can_edit'], can_manage_assets=access['can_edit'], can_view_spear_settings=settings_view)
        self.rows = {str(r['mission'].id):r for r in critical['rows']}

    @pass_context
    def render(self, template_context, key, caller):
        html = str(caller())
        digest = _digest(html, self.scope, key)[:32]
        critical = '-'
        reuse = self.previous.get(key) == digest
        old_critical_key = key.removeprefix('mission-') if key.startswith('mission-') else key
        if key == 'assets' or key.startswith('mission-'):
            critical = self.critical_previous.get(old_critical_key, '-') if reuse else '-'
            if critical == '-':
                template = '_nightly_assets.html' if key == 'assets' else '_dispatch_mission_rows.html'
                values = {} if key == 'assets' else {'row':self.rows[old_critical_key]}
                body = current_app.jinja_env.get_template('neonodes/neoscorpion/' + template).render(
                    **dict(template_context.get_all(), **dict(self.arguments, **values)))
                critical = _digest(body, self.scope, old_critical_key)
        if reuse:
            return Markup(f'<template data-dispatch-reuse="{key}" data-part-digest="{digest}"></template>')
        return Markup(f'<!--neo-dispatch:{key}:{digest}:{critical}-->' + html + f'<!--/neo-dispatch:{key}-->')


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
    scope = _scope(context, gateway, access, settings_view)
    def render(template, **values):
        return current_app.jinja_env.get_template(template).render(**dict(arguments, **values))
    hashes, pieces = {}, []

    def fragment(key, template, **values):
        html = render(template, **values)
        digest = _digest(html, scope, key)
        # Bound round-tripped metadata, not the actual operational population.
        if len(hashes) < 80:
            hashes[key] = digest
        if previous.get(key) != digest:
            pieces.append(html)

    for row in context['rows']:
        fragment(str(row['mission'].id), 'neonodes/neoscorpion/_dispatch_mission_rows.html', row=row)
    assets = render('neonodes/neoscorpion/_nightly_assets.html')
    assets_hash = _digest(assets, scope, 'assets')
    hashes['assets'] = assets_hash
    operation = context['operation']
    return dict(fingerprints=hashes, compact=True, can_edit=bool(access['can_edit']),
        mission_manifest={str(row['mission'].id):{'arrival':row['dispatch_arrival_rank'], 'etd':row['dispatch_etd_rank']}
                          for row in context['rows']}, html=render(
        'neonodes/neoscorpion/_dispatch_critical.html',
        settings=context['settings'], rows_html=Markup(''.join(pieces)),
        assets_html=Markup(assets if previous.get('assets') != assets_hash else ''),
        operation=operation))
