"""Grandmaster previews use the target's read model and the actor's own session."""
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace
from typing import Callable
from urllib.parse import urlsplit

from flask import abort, g, jsonify, make_response, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from app.auth import bp
from app.auth.accounts import _tabs
from app.extensions import db
from app.models import NeoScorpionVendorAccount, User
from app.services.access_control import get_current_gateway, get_user_node_role
from app.services.account_types import administrator
from app.services.fuel_driver_accounts import identity_verified, require_supervisor, supervisor_gateway_context, supervisor_membership
from app.services.permission_rules import permission_access


VIEW_AS_PREFIX = '/accounts/view-as'


@bp.before_app_request
def reject_preview_writes():
    if request.method in {'GET', 'HEAD', 'OPTIONS'}:
        return None
    try:
        path = urlsplit(request.referrer or '').path
    except ValueError:
        path = ''
    if any(candidate == VIEW_AS_PREFIX or candidate.startswith(VIEW_AS_PREFIX + '/')
           for candidate in (path, request.path)):
        return jsonify(ok=False, error='PREVIEW MODE is read only.'), 403
    return None


def preview_only(view):
    @wraps(view)
    @login_required
    def authorized(*args, **kwargs):
        if not administrator(current_user):
            abort(403)
        gateway = get_current_gateway()
        if not gateway.is_active:
            abort(404)
        # No provisioning or data repair is allowed in a preview transaction.
        if db.session.get_bind().dialect.name == 'postgresql':
            db.session.execute(text('SET TRANSACTION READ ONLY'))
        try:
            response = make_response(view(gateway, *args, **kwargs))
        except PermissionError as error:
            response = make_response(jsonify(ok=False, error=str(error)), 403)
        response.headers['Cache-Control'] = 'private, no-store'
        return response
    return authorized


def _eligible_vendor(gateway, user, supervisor):
    account = user.scorpion_vendor
    if (not account or account.gateway_id != gateway.id or not account.enabled or
            not user.is_active or not user.email_verified_at or bool(supervisor_membership(account)) != supervisor):
        return False
    return not supervisor or identity_verified(user)


def driver_subjects(gateway):
    return _vendor_subjects(gateway, supervisor=False)


def supervisor_subjects(gateway):
    return _vendor_subjects(gateway, supervisor=True)


def _vendor_subjects(gateway, *, supervisor):
    users = User.query.join(NeoScorpionVendorAccount, NeoScorpionVendorAccount.user_id == User.id).filter(
        NeoScorpionVendorAccount.gateway_id == gateway.id, NeoScorpionVendorAccount.enabled.is_(True),
        User.is_active.is_(True), User.email_verified_at.is_not(None),
    ).order_by(User.last_name, User.first_name, User.id).all()
    return [user for user in users if _eligible_vendor(gateway, user, supervisor)]


def _fueler_access(gateway, user):
    from app.neonodes.neoscorpion.routes import FUELER_VIEW_PERMISSION, FUELER_EDIT_PERMISSION
    if not user.is_active or not user.email_verified_at or not get_user_node_role(user, gateway.code, 'scorpion'):
        raise PermissionError('The selected account cannot open Fueler at this gateway.')
    access = permission_access(FUELER_VIEW_PERMISSION, FUELER_EDIT_PERMISSION, user=user)
    if not access['can_view']:
        raise PermissionError('The selected account cannot open Fueler at this gateway.')
    return access


def fueler_subjects(gateway):
    from app.models import GatewayMembership
    users = User.query.join(GatewayMembership, GatewayMembership.user_id == User.id).filter(
        GatewayMembership.gateway_id == gateway.id, GatewayMembership.is_active.is_(True),
        GatewayMembership.status == 'approved', User.is_active.is_(True), User.email_verified_at.is_not(None),
    ).order_by(User.last_name, User.first_name, User.id)
    results = []
    for user in users:
        try:
            _fueler_access(gateway, user)
        except PermissionError:
            continue
        results.append(user)
    return results


def driver_preview(gateway, user, part):
    from app.neonodes.neoscorpion.vendor_routes import driver_access, driver_screen_context, vendor_fingerprint, waiting_context
    from app.services.neoscorpion import current_sort_operation
    from app.services.neoscorpion_vendors import require_approved
    if user is None:
        operation = current_sort_operation(gateway)
        if operation and operation.archived_at_utc:
            operation = None
        account = SimpleNamespace(user_id=None, gateway=gateway, gateway_id=gateway.id)
        status = 'AWAITING DISPATCHER APPROVAL' if operation else 'NO ACTIVE SORT'
        return waiting_context(account, operation, status)
    if not _eligible_vendor(gateway, user, False):
        raise PermissionError('The selected account cannot open Fuel Truck Driver at this gateway.')
    account = user.scorpion_vendor
    if part == 'revision':
        operation, status = driver_access(account)
        if status:
            return waiting_context(account, operation, status)
        require_approved(account, operation)
        return {'fingerprint': vendor_fingerprint(account, operation, include_sort_missions=True)}
    return driver_screen_context(account, user)


def supervisor_preview(gateway, user, part):
    if user is not None:
        if not _eligible_vendor(gateway, user, True):
            raise PermissionError('The selected account cannot open Vendor Supervisor at this gateway.')
        require_supervisor(user.scorpion_vendor)
    return supervisor_gateway_context(gateway)


def fueler_preview(gateway, user, part):
    from app.services.neoscorpion import fueler_context, neoscorpion_live_revision
    from app.services.neoscorpion_assets import fueler_on_break
    from app.services.neoscorpion_tail_notes import tail_notes_revision
    access = _fueler_access(gateway, user) if user is not None else None
    if part == 'revision':
        fingerprint = neoscorpion_live_revision(gateway, include_remaining=True)
        fingerprint['fueler_on_break'] = bool(user and fueler_on_break(fingerprint['operation_id'], user.id))
    else:
        subject = user or SimpleNamespace(id=None, display_name='Fueler')
        context = fueler_context(gateway, subject, truck_id=None if user else -1, include_remaining=True)
        operation = context['operation']
        fingerprint = {'operation_id': operation.id if operation else None, 'revision': context['fuel_assignments_revision']}
    fingerprint['tail_notes_revision'] = tail_notes_revision(gateway)
    if part == 'revision':
        return {'fingerprint': fingerprint}
    return dict(context, fingerprint=fingerprint, effective_edit=access['can_edit'] if access else None)


@dataclass(frozen=True)
class PreviewInterface:
    label: str
    template: str
    subjects: Callable
    context: Callable
    panel: str | None = None


# An interface registers its established read model and templates here.
INTERFACES = {
    'fuel-truck-driver': PreviewInterface('Fuel Truck Driver', 'neonodes/neoscorpion/vendor_truck.html', driver_subjects, driver_preview),
    'vendor-supervisor': PreviewInterface('Vendor Supervisor', 'neonodes/neoscorpion/vendor_supervisor.html', supervisor_subjects, supervisor_preview),
    'fueler': PreviewInterface('NeoScorpion Fueler', 'neonodes/neoscorpion/fueler.html', fueler_subjects, fueler_preview,
                               'neonodes/neoscorpion/_fueler_panel.html'),
}


@bp.get(VIEW_AS_PREFIX)
@preview_only
def view_as_index(gateway):
    return render_template('auth/accounts/view_as.html', gateway=gateway,
                           interfaces=[(key, view.label, view.subjects(gateway)) for key, view in INTERFACES.items()],
                           **_tabs('view-as'))


def _selection(key):
    interface = INTERFACES.get(key)
    if interface is None:
        abort(404)
    selected = request.args.get('user', '')
    subject = None
    if selected:
        selected_id = request.args.get('user', type=int)
        if selected_id is None or not 0 < selected_id <= 2**31 - 1:
            abort(400)
        subject = db.get_or_404(User, selected_id)
    return interface, subject


@bp.get(VIEW_AS_PREFIX + '/<key>')
@preview_only
def view_as_screen(gateway, key):
    from app.services.page_assets import page_stylesheets
    from app.neonodes.neoscorpion.vendor_routes import vendor_refresh_windows
    interface, subject = _selection(key)
    context = interface.context(gateway, subject, 'screen')
    g.view_as_screen = True
    subject_id = subject.id if subject else None
    return render_template(
        interface.template, **context, gateway=gateway, can_edit=False, view_as=True,
        refresh_windows=vendor_refresh_windows(gateway) if not interface.panel else [],
        view_as_label=interface.label, view_as_subject=subject.display_name if subject else 'Role preview',
        view_as_revision_url=url_for('auth.view_as_live', key=key, part='revision', user=subject_id),
        view_as_panel_url=url_for('auth.view_as_live', key=key, part='panel', user=subject_id) if interface.panel else None,
        view_as_screen_url=url_for('auth.view_as_screen', key=key, user=subject_id),
        view_as_storage_scope=f'preview:{current_user.id}:{key}:{subject_id or "role"}',
        view_as_fueler=key == 'fueler', view_as_stylesheets=page_stylesheets('neoscorpion') if key == 'fueler' else (),
    )


@bp.get(VIEW_AS_PREFIX + '/<key>/live/<part>')
@preview_only
def view_as_live(gateway, key, part):
    interface, subject = _selection(key)
    if part != 'revision' and (part != 'panel' or not interface.panel):
        abort(404)
    context = interface.context(gateway, subject, part)
    fingerprint = context.get('fingerprint') or context['waiting_fingerprint']
    if part == 'revision':
        return jsonify(ok=True, **fingerprint) if interface.panel else jsonify(fingerprint)
    from app.services.neoscorpion_dispatch_fragments import panel_content
    html = render_template(interface.panel, **context, can_edit=False, view_as=True)
    return jsonify(ok=True, **fingerprint, **panel_content(html))
