from datetime import datetime
from functools import wraps
import re
from types import SimpleNamespace

from flask import Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.auth import bp
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, PortalAppAccess, User
from app.models.user import MANAGEMENT_LEVELS, ROLE_LEVELS
from app.services import email_service
from app.services.access_control import (
    ensure_user_app_access,
    ensure_user_gateway_membership,
    get_current_gateway,
    get_user_app_access,
    get_user_node_role,
    portal_install_rows_for_user,
    portal_app_definition,
    portal_app_definitions,
    portal_dashboard_rows_for_user,
    prime_user_node_roles_for_request,
    request_app_access_for_user,
    request_default_gateway_access_for_user,
    seed_gateway_node_roles,
    user_has_app_access,
    user_has_gateway_access,
)
from app.services.permission_rules import (
    ensure_default_permission_rules,
    grouped_permission_rules,
    permission_is_configurable,
    user_can,
)
from app.services.auth_session_security import (
    clear_authenticated_session_security_state,
    establish_authenticated_session,
    forced_password_change_session_is_fresh,
    temporary_password_expiration,
    temporary_password_is_expired,
    rotate_user_session_version,
)
from app.services.account_security import (
    guard_app_role_change, guard_node_change, lock_administration, may_manage_permission,
    node_authority, platform_grandmaster,
    record_security_change,
)
from app.services.auth_rate_limits import (
    clear_login_failures,
    clear_login_identifier_failures,
    client_ip_for_request,
    login_is_limited,
    password_reset_is_limited,
    record_login_failure,
    record_password_reset_request,
    reserve_registration_attempt,
)
from app.services.csrf import clear_csrf_session_state, csrf_renewal_response
from app.services.password_policy import (
    PASSWORD_POLICY_GUIDANCE,
    set_user_password,
    user_requires_password_change,
)
from app.services.user_tokens import (
    EMAIL_VERIFICATION,
    PASSWORD_RESET,
    create_user_token,
    claim_password_reset_token,
    claim_user_token,
    get_token_record,
    get_valid_token_record,
    mark_token_used,
    revoke_unused_email_verification_tokens,
    revoke_unused_password_reset_tokens,
)


GENERIC_RESET_RESPONSE = "If an account exists for that email, a reset link has been sent."
GENERIC_REGISTRATION_CONFLICT_RESPONSE = (
    "An account may already exist with the information provided. "
    "Use password reset or contact an administrator if you need help."
)


@bp.route("/share/neoapps-qr.svg")
@login_required
def neoapps_share_qr():
    """Render a local QR for the public NeoApps root without an external service."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib.colors import black, white

    target = url_for("neonodes.index", _external=True)
    qr = QrCodeWidget(target)
    qr.barBorder = 4  # Four-module quiet zone on every side, inside the white square.
    qr.barFillColor = black
    size = 220
    qr.barWidth = qr.barHeight = size
    drawing = Drawing(size, size)
    drawing.add(Rect(0, 0, size, size, fillColor=white, strokeColor=None))
    drawing.add(qr)
    return Response(
        renderSVG.drawToString(drawing),
        mimetype="image/svg+xml",
        headers={"Cache-Control": "private, max-age=300"},
    )
ROLE_CHOICES = ("watcher", "operator", "simulator", "master", "grandmaster")
ROLE_DISPLAY_LABELS = {
    "watcher": "Watcher",
    "operator": "Operator",
    "simulator": "Simulator",
    "master": "Master",
    "grandmaster": "Grandmaster",
}
PORTAL_VIEW_PERMISSION = "neoapps.portal.view"
PORTAL_REQUEST_ACCESS_PERMISSION = "neoapps.portal.request_access.edit"
PORTAL_MANAGEMENT_VIEW_PERMISSION = "neoapps.portal_management.view"
PORTAL_MANAGEMENT_EDIT_PERMISSION = "neoapps.portal_management.edit"
USER_MANAGEMENT_VIEW_PERMISSION = "neoapps.user_management.view"
USER_MANAGEMENT_EDIT_PERMISSION = "neoapps.user_management.edit"
EMAIL_VERIFICATION_RESEND_PERMISSION = "neoapps.email_verification.resend"
ACCESS_REQUESTS_VIEW_PERMISSION = "neoapps.access_requests.view"
ACCESS_REQUESTS_EDIT_PERMISSION = "neoapps.access_requests.edit"
PERMISSION_RULES_VIEW_PERMISSION = "neomotherbrain.permission_rules.view"
PERMISSION_RULES_EDIT_PERMISSION = "neomotherbrain.permission_rules.edit"
NEOBID_PLACEHOLDER_VIEW_PERMISSION = "neobid.placeholder.view"
PORTAL_ACCESS_REQUEST_DEFAULT_STATUS = "pending"
PORTAL_ACCESS_REQUEST_STATUS_FILTERS = (
    {"value": "all", "label": "All"},
    {"value": "pending", "label": "Pending"},
    {"value": "approved", "label": "Approved"},
    {"value": "denied", "label": "Denied"},
)


def portal_permission_required(permission_key, message="Access denied."):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if user_can(permission_key):
                if request.method == "POST" and not _sort_scoped_account_conversion_requested():
                    try:
                        actor = lock_administration()
                        if not user_can(permission_key, user=actor):
                            raise ValueError("Administrative authority changed.")
                    except ValueError:
                        db.session.rollback()
                        abort(403)
                return view_func(*args, **kwargs)

            flash(message, "error")
            return redirect(url_for("auth.portal_dashboard"))

        return wrapped_view

    return decorator


def _sort_scoped_account_conversion_requested():
    # Conversion already locks active Sorts before account/grant rows. Do not
    # invert that order by locking the administrator in the route decorator.
    action = request.form.get("action", "").strip().lower()
    account_type = request.form.get("account_type", "").strip().lower()
    return (request.endpoint == "auth.change_user_account_type"
            or request.endpoint == "auth.update_portal_app_access" and action == "approve_driver"
            or request.endpoint == "auth.update_user_gateway_membership" and action == "approve" and account_type == "vendor"
            or request.endpoint == "auth.approve_access_request" and account_type == "vendor")


def portal_grandmaster_required(view_func):
    return portal_permission_required(
        PORTAL_MANAGEMENT_VIEW_PERMISSION,
        "Portal Management requires Grandmaster access.",
    )(view_func)


def node_role_management_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(*args, **kwargs):
        def permitted():
            return platform_grandmaster(current_user) or any(
                node_authority(current_user, node) >= ROLE_LEVELS["master"]
                for node in NeoNode.query.filter_by(is_active=True).all()
            )
        if not permitted():
            abort(403)
        if request.method == "POST":
            try:
                lock_administration()
                if not permitted():
                    raise ValueError("Role administration authority changed.")
            except ValueError:
                db.session.rollback()
                abort(403)
        return view_func(*args, **kwargs)
    return wrapped


@bp.get("/csrf-token")
def renew_csrf():
    return csrf_renewal_response()


@bp.route("/login", methods=["GET", "POST"])
def login():


    if current_user.is_authenticated and request.method == "GET":
        return redirect(_authenticated_landing_url(current_user))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        legacy_username = request.form.get("username", "").strip()
        login_identifier = email or legacy_username
        password = request.form.get("password", "")
        client_ip = client_ip_for_request(request)

        if login_is_limited(client_ip, login_identifier):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html"), 401

        user = _find_user_by_login(login_identifier) if login_identifier else None

        credentials_valid = bool(
            user and user.check_password(password) and user.is_active
        )
        vendor = user.scorpion_vendor if credentials_valid else None
        if not credentials_valid or temporary_password_is_expired(user):
            record_login_failure(client_ip, login_identifier)
            db.session.commit()
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html"), 401

        if vendor and not vendor.enabled:
            flash("Your NeoScorpion driver account is disabled. Contact Dispatch for help.", "error")
            return render_template("auth/login.html"), 403
        if vendor and not user.email_verified_at:
            flash("Verify your NeoApps email before signing in.", "error")
            return render_template("auth/login.html"), 403
        if user.mfa_required or user.mfa_enabled:
            # The shared login has no MFA challenge handler. A password alone
            # must never establish a session for an account that requires MFA.
            flash("This account requires additional verification. Contact your NeoApps administrator.", "error")
            return render_template("auth/login.html"), 403

        clear_login_failures(client_ip, login_identifier)
        login_user(user)
        clear_csrf_session_state()
        user.last_login = datetime.utcnow()

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Login failed. Please try again.", "error")
            return render_template("auth/login.html"), 500

        if user_requires_password_change(user):
            establish_authenticated_session(
                session,
                user,
                forced_password_change=True,
            )
            return redirect(url_for("auth.change_password"))

        establish_authenticated_session(session, user)
        return redirect(_authenticated_landing_url(user))

    return render_template("auth/login.html")


@bp.post("/account/operational-screen")
@login_required
def remember_operational_screen():
    from app.services.operational_navigation import store_visit
    return ("", 204) if store_visit(current_user, request.form.get("visit", "")) else ("", 403)


def _authenticated_landing_url(user):
    from app.services.operational_navigation import restored_url
    return restored_url(user) or url_for(_authenticated_landing_endpoint(user))


def _authenticated_landing_endpoint(user):
    """Choose only an accessible canonical landing page, never a supplied URL."""
    if user.scorpion_vendor:
        from app.services.fuel_driver_accounts import supervisor_membership
        return "scorpion_vendor.supervisor" if supervisor_membership(user.scorpion_vendor) else "scorpion_vendor.my_truck"
    from app.services.accounts_workspace import authorized_applications, preference

    apps = authorized_applications(user)
    available = [app['endpoint'] for app in apps]
    preferred = preference(user)
    if preferred and preferred.preferred_app:
        for app in apps:
            if app['code'] == preferred.preferred_app:
                return app['endpoint']
    if len(available) == 1:
        return available[0]
    return 'auth.portal_dashboard' if available else 'auth.access_pending'


@bp.route("/logout", methods=["POST"])
@login_required
def logout():


    logout_user()
    clear_authenticated_session_security_state(session)
    clear_csrf_session_state()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/portal")
@login_required
def portal_dashboard():
    if not user_can(PORTAL_VIEW_PERMISSION):
        flash("NeoApps Portal access denied.", "error")
        return redirect(url_for("auth.access_pending"))

    from app.services.accounts_workspace import CRYOCHECK_URL, authorized_applications, preference
    return render_template(
        "auth/portal.html",
        app_rows=portal_dashboard_rows_for_user(current_user),
        install_rows=portal_install_rows_for_user(current_user),
        authorized_apps=authorized_applications(current_user),
        account_preference=preference(current_user), cryocheck_url=CRYOCHECK_URL,
    )


@bp.route("/portal/request-access", methods=["POST"])
@login_required
def request_portal_app_access():
    # Old launcher bookmarks lead to the justified, history-backed request flow.
    return redirect(url_for("auth.my_account", _anchor="app-access"), code=303)


@bp.route("/neobid")
@login_required
def neobid_placeholder():
    if not user_has_app_access(current_user, "neobid") or not user_can(
        NEOBID_PLACEHOLDER_VIEW_PERMISSION
    ):
        flash("Request NeoBid access from the NeoApps Portal.", "error")
        return redirect(url_for("auth.portal_dashboard"))
    return render_template(
        "auth/app_placeholder.html",
        app_name="NeoBid",
        app_status="Coming Soon",
    )


@bp.route("/create-account", methods=["GET", "POST"])
def create_account():
    form = _account_form_from_request()

    if request.method == "POST":
        try:
            retry_after = reserve_registration_attempt(
                client_ip_for_request(request), _normalize_email(form["email"])
            )
        except SQLAlchemyError as error:
            db.session.rollback()
            current_app.logger.warning(
                "Registration admission unavailable: exception_class=%s", type(error).__name__
            )
            flash("Account requests are temporarily unavailable. Please try again later.", "error")
            return render_template(
                "auth/create_account.html", app_definitions=portal_app_definitions(), form=form,
            ), 503, {"Retry-After": "60", "Cache-Control": "no-store"}
        if retry_after:
            flash("Too many account requests. Please wait before trying again or contact an administrator.", "error")
            return render_template(
                "auth/create_account.html", app_definitions=portal_app_definitions(), form=form,
            ), 429, {"Retry-After": str(retry_after), "Cache-Control": "no-store"}
        try:
            user = _build_user_from_account_form(form)
            requested_apps = _requested_portal_apps_from_form()
            if not requested_apps:
                raise ValueError("Select at least one NeoApps system for access.")
            for app_code in requested_apps:
                if app_code == "neogateway":
                    request_default_gateway_access_for_user(user)
                else:
                    request_app_access_for_user(user, app_code)
                from app.services.accounts_workspace import new_attempt
                new_attempt(user, app_code, form["access_reason"], registration=True)
            raw_token, _token_record = create_user_token(user, EMAIL_VERIFICATION)
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return render_template(
                "auth/create_account.html",
                app_definitions=portal_app_definitions(),
                form=form,
            ), 400
        except IntegrityError:
            # A concurrent registration can pass the preflight identity check.
            # Keep that database-level uniqueness outcome indistinguishable too.
            db.session.rollback()
            flash(GENERIC_REGISTRATION_CONFLICT_RESPONSE, "error")
            return render_template(
                "auth/create_account.html",
                app_definitions=portal_app_definitions(),
                form=form,
            ), 400
        except SQLAlchemyError:
            db.session.rollback()
            flash("Account creation failed. Please try again.", "error")
            return render_template(
                "auth/create_account.html",
                app_definitions=portal_app_definitions(),
                form=form,
            ), 500

        email_service.send_email_verification(user, raw_token)
        return render_template("auth/account_created.html", user=user)

    return render_template(
        "auth/create_account.html",
        app_definitions=portal_app_definitions(),
        form=form,
    )


@bp.route("/verify-email/<token>")
def verify_email(token):
    token_record = claim_user_token(token, EMAIL_VERIFICATION)
    if not token_record:
        # Preserve the friendly already-verified response, using fresh state
        # after the account lock; a corrected address is unverified and fails.
        token_record = get_token_record(token, EMAIL_VERIFICATION)
        if (token_record and token_record.user.is_active and token_record.user.email_verified_at
                and (not token_record.user.scorpion_vendor or token_record.user.scorpion_vendor.enabled)):
            return render_template("auth/verify_email.html", already_verified=True, verified=True,
                                   driver_recovery=bool(token_record.user.scorpion_vendor))
        return render_template("auth/verify_email.html", verified=False), 400
    user = token_record.user
    vendor = user.scorpion_vendor
    if not user.is_active or (vendor and not vendor.enabled):
        db.session.rollback()
        return render_template("auth/verify_email.html", verified=False), 400

    token_record.user.email_verified_at = token_record.user.email_verified_at or datetime.utcnow()
    mark_token_used(token_record)
    db.session.commit()

    return render_template("auth/verify_email.html", already_verified=False, verified=True,
                           driver_recovery=bool(vendor))


@bp.route("/access-pending")
@login_required
def access_pending():
    gateway = get_current_gateway()
    membership = GatewayMembership.query.filter_by(
        user_id=current_user.id,
        gateway_id=gateway.id,
    ).first()
    return render_template(
        "auth/access_pending.html",
        gateway=gateway,
        membership=membership,
    )


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = _normalize_email(request.form.get("email"))
        client_ip = client_ip_for_request(request)

        if not password_reset_is_limited(client_ip, email):
            user = _find_user_by_email(email) if email else None
            vendor = user.scorpion_vendor if user else None
            eligible = bool(user and user.is_active and (not vendor or vendor.enabled))
            needs_verification = bool(eligible and vendor and not user.email_verified_at)
            record_password_reset_request(client_ip, email)
            if eligible:
                kind = EMAIL_VERIFICATION if needs_verification else PASSWORD_RESET
                try:
                    raw_token, _token_record = create_user_token(user, kind)
                except ValueError:
                    # An email correction/revocation won the account lock.
                    # Keep the recovery response generic and its rate limit.
                    eligible = needs_verification = False
            db.session.commit()
            if needs_verification:
                email_service.send_email_verification(user, raw_token)
            elif eligible:
                email_service.send_password_reset(user, raw_token)

        flash(GENERIC_RESET_RESPONSE, "info")
        return render_template("auth/forgot_password.html", submitted=True)

    return render_template("auth/forgot_password.html", submitted=False)


def _password_recovery_allowed(user):
    vendor = user.scorpion_vendor
    return bool(user.is_active and (not vendor or (vendor.enabled and user.email_verified_at)))


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    def render_reset(valid):
        return render_template("auth/reset_password.html", token=token,
                                  valid=valid, password_policy_guidance=PASSWORD_POLICY_GUIDANCE)

    submitting = request.method == "POST"
    try:
        token_record = (claim_password_reset_token(token) if submitting
                        else get_valid_token_record(token, PASSWORD_RESET))
        if not token_record or not _password_recovery_allowed(token_record.user):
            if submitting:
                db.session.rollback()
            return render_reset(False), 400
        if submitting:
            set_user_password(
                token_record.user,
                request.form.get("password", ""),
                request.form.get("confirm_password", ""),
            )
            token_record.user.password_reset_required = False
            token_record.user.password_policy_update_required = False
            token_record.user.temporary_password_expires_at = None
            revoke_unused_password_reset_tokens(token_record.user)
            db.session.commit()
    except ValueError as error:
        # A policy rejection undoes the claim so the password can be corrected.
        db.session.rollback()
        flash(str(error), "error")
        return render_reset(True), 400
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning("Password reset transaction could not be confirmed.")
        flash("Password reset could not be confirmed. Try signing in or request a new reset link.", "error")
        return render_reset(True), 500

    if submitting:
        flash("Password reset complete. You can log in now.", "info")
        return redirect(url_for("auth.login"))
    return render_reset(True)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced_change = user_requires_password_change(
        current_user
    ) and forced_password_change_session_is_fresh(session, current_user)
    if request.method == "POST":
        if not forced_change and not current_user.check_password(
            request.form.get("current_password", "")
        ):
            flash("Current password is incorrect.", "error")
            return render_template(
                "auth/change_password.html",
                password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
                require_current_password=True,
            ), 400

        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        try:
            set_user_password(current_user, password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template(
                "auth/change_password.html",
                password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
                require_current_password=not forced_change,
            ), 400

        current_user.password_reset_required = False
        current_user.password_policy_update_required = False
        current_user.temporary_password_expires_at = None
        db.session.commit()
        establish_authenticated_session(session, current_user)
        flash("Password changed.", "info")
        return redirect(_authenticated_landing_url(current_user))

    return render_template(
        "auth/change_password.html",
        password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
        require_current_password=not forced_change,
    )


@bp.route("/portal/manage")
@portal_permission_required(
    PORTAL_MANAGEMENT_VIEW_PERMISSION,
    "Portal Management requires Grandmaster access.",
)
def portal_management():
    return redirect(url_for("auth.accounts_requests", app=request.args.get("app", ""), status=request.args.get("status", "pending"), q=request.args.get("q", "")))


@bp.route("/portal/manage/app-access/<int:access_id>/update", methods=["POST"])
@portal_permission_required(
    PORTAL_MANAGEMENT_EDIT_PERMISSION,
    "Portal Management edit access denied.",
)
def update_portal_app_access(access_id):
    access = PortalAppAccess.query.get_or_404(access_id)
    action = request.form.get("action", "").strip().lower()
    role = request.form.get("role", access.role or "watcher").strip().lower()
    notes = request.form.get("notes", "").strip() or None

    try:
        if action == "approve_driver":
            _change_driver_account_type(get_current_gateway(), access.user, "vendor")
            flash("Restricted Fuel Truck Driver account approved.", "info")
        elif action in {"approve", "approve_without_verification"}:
            _lock_staff_account(access.user)
            db.session.refresh(access)
            _approve_portal_app_access(
                access, role, notes,
                bypass_email_verification=(action == "approve_without_verification"),
            )
            flash("App access approved.", "info")
        elif action == "deny":
            _lock_staff_account(access.user)
            db.session.refresh(access)
            _deny_portal_app_access(access, notes)
            flash("App access denied.", "info")
        else:
            raise ValueError("Unsupported app access action.")
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")

    return redirect(_portal_management_return_url())


@bp.route("/admin/users")
@bp.route("/portal/manage/users")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def users():
    return redirect(url_for("auth.accounts_people", q=request.args.get("q", "")))


@bp.route("/admin/users/all")
@bp.route("/portal/manage/users/all")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def all_users():
    return redirect(url_for("auth.accounts_people"))


@bp.route("/admin/users/edit-users")
@bp.route("/portal/manage/users/edit-users")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def edit_users():
    return redirect(url_for("auth.accounts_people", q=request.args.get("q", "")))


@bp.route("/admin/users/manage-roles")
@bp.route("/portal/manage/users/manage-roles")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def manage_roles():
    return redirect(url_for("auth.edit_users", q=request.args.get("q", "")))


@bp.route("/admin/users/pending")
@bp.route("/portal/manage/users/pending")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def pending_users():
    return redirect(url_for("auth.accounts_requests"))


@bp.route("/accounts/security/permissions", methods=["GET", "POST"])
@bp.route("/admin/permissions", methods=["GET", "POST"])
@bp.route("/motherbrain/permissions", methods=["GET", "POST"])
@login_required
def permission_rules():
    from app.services.account_scope_runtime import scoped_active
    from app.services.permission_thresholds import THRESHOLD_ROLES, threshold_field
    if scoped_active():
        from app.auth.scoped_access import scoped_permissions
        return scoped_permissions('motherbrain' if request.path.startswith('/motherbrain/') else 'neoapps')
    from app.auth.accounts import _tabs
    workspace = request.path.startswith("/accounts/")
    if workspace and not platform_grandmaster(current_user):
        abort(403)
    ensure_default_permission_rules()
    def scoped_rules():
        return [rule for rule in PermissionRule.query.order_by(PermissionRule.permission_key.asc()).all()
                if may_manage_permission(current_user, rule.permission_key)]

    rules = scoped_rules()
    if not rules:
        flash("Node Permissions access denied.", "error")
        return redirect(url_for("auth.portal_dashboard"))

    if request.method == "POST":
        try:
            lock_administration()
            _apply_permission_rule_form()
            db.session.commit()
            flash("Permission rules updated.", "info")
            return redirect("/accounts/security/permissions" if workspace else url_for("auth.permission_rules"))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")

    rules = scoped_rules()
    return render_template(
        "auth/permission_rules.html",
        grouped_rules=grouped_permission_rules(rules),
        threshold_roles=THRESHOLD_ROLES,
        threshold=lambda rule: threshold_field(rule.permission_key, rule.minimum_role, scoped=False),
        rules=rules,
        workspace_tabs=_tabs("security")["workspace_tabs"] if workspace else [],
        active_tab="security", account_workspace=workspace,
    )


@bp.route("/admin/users/<int:user_id>")
@bp.route("/portal/manage/users/<int:user_id>")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def user_detail(user_id):
    return redirect(url_for("auth.account_profile", user_id=user_id, tab=request.args.get("tab", "identity")))


@bp.route("/admin/users/<int:user_id>/commission-grandmaster", methods=["GET", "POST"])
@login_required
def commission_platform_grandmaster(user_id):
    from app.services.grandmaster_commissioning import (
        CommissioningConflict, CommissioningDenied, ReauthenticationFailed,
        commission_grandmaster, commissioning_review, may_initiate_commissioning,
    )
    if not may_initiate_commissioning(current_user):
        abort(403)
    target = User.query.get_or_404(user_id)
    status = 200
    if request.method == "POST":
        client_ip = client_ip_for_request(request)
        identifier = current_user.email
        if login_is_limited(client_ip, identifier):
            db.session.rollback()
            return Response("Reauthentication is temporarily limited. Try again later.", 429,
                            headers={"Retry-After": "60"})
        try:
            commission_grandmaster(user_id, review_token=request.form.get("review_token", ""),
                current_password=request.form.get("current_password", ""),
                confirmed_target_id=request.form.get("confirmed_target_id", ""),
                confirm_platform_authority=request.form.get("confirm_platform_authority", ""),
                reason=request.form.get("reason", ""))
            db.session.commit()
            flash("NeoApps platform Grandmaster commissioned. Existing sessions were invalidated; application and node grants were preserved.", "info")
            return redirect(url_for("auth.account_profile", user_id=user_id, tab="security"))
        except ReauthenticationFailed as error:
            try:
                record_login_failure(client_ip, identifier)
                db.session.commit()
                flash(str(error), "error")
                status = 400
            except SQLAlchemyError:
                db.session.rollback()
                flash("Reauthentication could not be recorded. No change was made. Please retry.", "error")
                status = 503
        except CommissioningConflict as error:
            flash(str(error), "error")
            status = 409
        except CommissioningDenied as error:
            flash(str(error), "error")
            status = 403
        except SQLAlchemyError:
            db.session.rollback()
            flash("Commissioning could not be saved. No change was made. Please retry.", "error")
            status = 503
        db.session.expire_all()
        if not may_initiate_commissioning(current_user):
            abort(403)
        target = User.query.get_or_404(user_id)
    try:
        review_token = commissioning_review(current_user, target)
    except CommissioningDenied as error:
        return render_template("auth/commission_grandmaster.html", target_user=target,
                               review_token=None, unavailable=str(error)), status if status != 200 else 409
    return render_template("auth/commission_grandmaster.html", target_user=target,
                           review_token=review_token, unavailable=None), status


@bp.route("/admin/users/<int:user_id>/resend-verification", methods=["POST"])
@bp.route("/portal/manage/users/<int:user_id>/resend-verification", methods=["POST"])
@portal_permission_required(
    EMAIL_VERIFICATION_RESEND_PERMISSION,
    "Resend verification email access denied.",
)
def resend_user_verification_email(user_id):
    target_user = User.query.get_or_404(user_id)

    if target_user.email_verified_at:
        flash("Email is already verified; no verification email was sent.", "info")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))
    if not target_user.email:
        flash("This user has no email address; no verification email was sent.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    try:
        revoke_unused_email_verification_tokens(target_user)
        raw_token, _token_record = create_user_token(target_user, EMAIL_VERIFICATION)
        db.session.commit()
    except (SQLAlchemyError, ValueError):
        db.session.rollback()
        current_app.logger.warning(
            "Verification resend token creation failed for user id=%s.",
            target_user.id,
        )
        flash("Verification email could not be prepared. Please try again.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    try:
        delivery = email_service.send_email_verification(target_user, raw_token)
    except Exception:  # Keep unexpected delivery failures from exposing token details.
        current_app.logger.warning(
            "Verification resend delivery failed for user id=%s.",
            target_user.id,
        )
        delivery = {"sent": False}

    if delivery.get("sent"):
        flash("Verification email sent.", "info")
    else:
        flash("Verification email could not be delivered. No email was sent.", "error")
    return redirect(url_for("auth.user_detail", user_id=target_user.id))


@bp.post("/admin/users/<int:user_id>/account-type")
@bp.post("/portal/manage/users/<int:user_id>/account-type")
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def change_user_account_type(user_id):
    target_user = User.query.get_or_404(user_id)
    account_type = (request.form.get("account_type") or "").strip()
    try:
        changed = _change_driver_account_type(get_current_gateway(), target_user, account_type)
        db.session.commit()
        flash(("Account converted. " + (
            "Fuel Truck Driver access is restricted and each Sort still requires dispatcher approval."
            if account_type == "vendor" else
            "Full NeoApps access is now pending normal app and node role approval."
        )) if changed else "Account is already that type.", "info")
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else
              "Account type could not be updated. Please retry.", "error")
    return redirect(url_for("auth.user_detail", user_id=target_user.id))


@bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@bp.route("/portal/manage/users/<int:user_id>/edit", methods=["GET", "POST"])
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def edit_user(user_id):
    if request.method == "GET":
        return redirect(url_for("auth.account_profile", user_id=user_id, tab="identity"))
    target_user = User.query.get_or_404(user_id)
    if target_user.scorpion_vendor:
        flash("Convert to FULL NEO ACCOUNT before editing staff roles or identity.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)
    form = _user_edit_form_from_request(target_user)

    if request.method == "POST":
        try:
            target_user = _lock_staff_account(target_user)
            membership = _current_gateway_membership_for_user(target_user, gateway)
            _apply_user_edit_form(target_user, form)
            bypass_email_verification = _email_verification_bypass_requested()
            membership = _apply_gateway_membership_edit_form(
                target_user, gateway, membership, bypass_email_verification
            )
            if _has_app_access_form_fields():
                _apply_portal_app_access_form(target_user, bypass_email_verification)
            if _has_node_role_form_fields():
                if not _membership_is_approved_active(membership):
                    raise ValueError(
                        "User must have approved RFD gateway access before assigning node roles."
                    )
                _apply_node_role_form(target_user, membership)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            membership = _current_gateway_membership_for_user(target_user, gateway)
            return _render_user_edit_form(target_user, gateway, membership, form), 400

        db.session.commit()
        flash("User updated.", "info")
        return redirect(url_for("auth.edit_user", user_id=target_user.id))

    return _render_user_edit_form(target_user, gateway, membership, form)


@bp.route("/admin/users/<int:user_id>/roles", methods=["GET", "POST"])
@bp.route("/portal/manage/users/<int:user_id>/roles", methods=["GET", "POST"])
@node_role_management_required
def user_roles(user_id):
    if request.method == "GET" and user_can(USER_MANAGEMENT_VIEW_PERMISSION):
        return redirect(url_for("auth.account_profile", user_id=user_id, tab="access"))
    target_user = User.query.get_or_404(user_id)
    if target_user.scorpion_vendor:
        flash("Convert this Fuel Truck Driver to FULL NEO ACCOUNT before granting NeoNode roles.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)

    if not _membership_is_approved_active(membership):
        flash("User must have approved RFD gateway access before assigning node roles.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    if request.method == "POST":
        try:
            target_user = _lock_staff_account(target_user)
            membership = _current_gateway_membership_for_user(target_user, gateway)
            _apply_node_role_form(target_user, membership)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return redirect(url_for("auth.user_roles", user_id=target_user.id))

        db.session.commit()
        flash("Node roles updated.", "info")
        return redirect(url_for("auth.user_roles", user_id=target_user.id))

    return render_template(
        "auth/user_roles.html",
        gateway=gateway,
        membership=membership,
        node_rows=[row for row in _node_role_rows(target_user, membership)
                   if node_authority(current_user, row["node"]) >= ROLE_LEVELS["master"]],
        target_user=target_user,
    )


@bp.route("/admin/users/<int:user_id>/gateway-membership", methods=["POST"])
@bp.route("/portal/manage/users/<int:user_id>/gateway-membership", methods=["POST"])
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def update_user_gateway_membership(user_id):
    target_user = User.query.get_or_404(user_id)
    gateway = get_current_gateway()
    action = request.form.get("action", "").strip().lower()
    membership = _current_gateway_membership_for_user(target_user, gateway)

    try:
        if action == "approve" and request.form.get("account_type", "").strip() == "vendor":
            if _email_verification_bypass_requested():
                raise ValueError("Fuel Truck Drivers must verify their NeoApps email before approval.")
            _change_driver_account_type(gateway, target_user, "vendor")
            flash("Restricted Fuel Truck Driver account approved.", "info")
            db.session.commit()
            return redirect(url_for("auth.user_detail", user_id=target_user.id))
        target_user = _lock_staff_account(target_user)
        membership = _current_gateway_membership_for_user(target_user, gateway)
        if action in {"approve", "deny"} and not membership:
            membership = ensure_user_gateway_membership(target_user, gateway)
        if action == "approve":
            node_role_values = _approval_node_role_values_from_form()
            _approve_account_request(
                membership,
                request.form.get("approval_notes", "").strip() or None,
                seed_role=(
                    "watcher"
                    if node_role_values is not None
                    else request.form.get("role", "watcher").strip().lower() or "watcher"
                ),
                node_role_values=node_role_values,
                bypass_email_verification=_email_verification_bypass_requested(),
            )
            flash("Access request approved.", "info")
        elif action == "deny":
            _guard_last_grandmaster_gateway_change(target_user)
            _deny_membership(
                membership,
                request.form.get("denial_notes", "").strip() or None,
            )
            flash("Access request updated.", "info")
        else:
            raise ValueError("Unsupported gateway membership action.")
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    db.session.commit()
    return redirect(url_for("auth.user_detail", user_id=target_user.id))


@bp.route("/admin/access-requests")
@bp.route("/portal/manage/access-requests")
@portal_permission_required(ACCESS_REQUESTS_VIEW_PERMISSION, "Access Requests access denied.")
def access_requests():
    return redirect(url_for("auth.accounts_requests"))


@bp.route("/admin/access-requests/<int:membership_id>/approve", methods=["POST"])
@bp.route("/portal/manage/access-requests/<int:membership_id>/approve", methods=["POST"])
@portal_permission_required(ACCESS_REQUESTS_EDIT_PERMISSION, "Access Requests edit access denied.")
def approve_access_request(membership_id):
    membership = _membership_or_404(membership_id)
    try:
        node_role_values = _approval_node_role_values_from_form()
        _approve_account_request(
            membership,
            request.form.get("approval_notes", "").strip() or None,
            seed_role=(
                "watcher"
                if node_role_values is not None
                else request.form.get("role", "watcher").strip().lower() or "watcher"
            ),
            node_role_values=node_role_values,
            bypass_email_verification=_email_verification_bypass_requested(),
        )
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("auth.access_requests"))

    db.session.commit()
    flash("Access request approved.", "info")
    return redirect(url_for("auth.access_requests"))


@bp.route("/admin/access-requests/<int:membership_id>/deny", methods=["POST"])
@bp.route("/portal/manage/access-requests/<int:membership_id>/deny", methods=["POST"])
@portal_permission_required(ACCESS_REQUESTS_EDIT_PERMISSION, "Access Requests edit access denied.")
def deny_access_request(membership_id):
    membership = _membership_or_404(membership_id)
    try:
        target_user = _lock_staff_account(membership.user)
        db.session.refresh(membership)
        _guard_last_grandmaster_gateway_change(target_user)
        _deny_membership(
            membership,
            request.form.get("denial_notes", "").strip() or None,
        )
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("auth.access_requests"))
    db.session.commit()

    flash("Access request updated.", "info")
    return redirect(url_for("auth.access_requests"))


@bp.route("/admin/users/<int:user_id>/emergency-password-reset", methods=["GET", "POST"])
@bp.route("/admin/users/<int:user_id>/emergency-reset", methods=["GET", "POST"])
@bp.route("/portal/manage/users/<int:user_id>/emergency-password-reset", methods=["GET", "POST"])
@bp.route("/portal/manage/users/<int:user_id>/emergency-reset", methods=["GET", "POST"])
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def emergency_reset_user_password(user_id):
    target_user = User.query.get_or_404(user_id)

    if request.method == "POST":
        target_user = User.query.filter_by(id=user_id).with_for_update().populate_existing().one()
        reason = request.form.get("reason", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not reason:
            flash("Emergency reset reason is required.", "error")
            return render_template("auth/emergency_reset.html", target_user=target_user), 400

        try:
            set_user_password(target_user, password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template(
                "auth/emergency_reset.html",
                target_user=target_user,
                password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
            ), 400

        target_user.password_reset_required = True
        target_user.password_policy_update_required = False
        target_user.temporary_password_expires_at = temporary_password_expiration(
            current_app.config["EMERGENCY_PASSWORD_EXPIRATION_HOURS"]
        )
        target_user.last_password_reset_by_user_id = current_user.id
        target_user.last_password_reset_at = datetime.utcnow()
        target_user.last_password_reset_reason = reason
        revoke_unused_password_reset_tokens(target_user)
        revoke_unused_email_verification_tokens(target_user)
        record_security_change("emergency_password_reset", target_user, reason=reason)
        clear_login_identifier_failures(target_user.email, target_user.username)
        db.session.commit()
        flash("Temporary password set. User must change it on next login.", "info")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    return render_template(
        "auth/emergency_reset.html",
        target_user=target_user,
        password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
    )


def _requested_portal_apps_from_form():
    selected = request.form.getlist("app_codes")
    if not selected and "app_codes" not in request.form:
        selected = ["neogateway"]
    valid_codes = {app["code"] for app in portal_app_definitions() if not app.get("coming_soon")}
    if any(str(code).strip().lower() not in valid_codes for code in selected):
        # Return invalid selections for the server validator; never silently turn
        # a forged NeoBid request into a different application's request.
        return [str(code).strip().lower() for code in selected]
    normalized = []
    for code in selected:
        app_code = str(code or "").strip().lower()
        if app_code in valid_codes and app_code not in normalized:
            normalized.append(app_code)
    return normalized


def _portal_app_access_requests(*, app_code=None, status=None):
    query = PortalAppAccess.query.join(User, PortalAppAccess.user_id == User.id)
    if app_code:
        query = query.filter(PortalAppAccess.app_code == app_code)
    if status and status != "all":
        query = query.filter(PortalAppAccess.status == status)
    if status == "pending":
        query = query.filter(PortalAppAccess.is_active.is_(True))
    return query.order_by(
        PortalAppAccess.created_at.asc(),
        PortalAppAccess.id.asc(),
    )


def _portal_management_app_filter(app_definitions, value=None):
    requested = str(
        request.args.get("app", "") if value is None else value
    ).strip().lower()
    valid_codes = {app["code"] for app in app_definitions}
    return requested if requested in valid_codes else None


def _portal_management_status_filter(value=None):
    requested = str(
        request.args.get("status", PORTAL_ACCESS_REQUEST_DEFAULT_STATUS)
        if value is None
        else value
    ).strip().lower()
    valid_statuses = {
        status_filter["value"]
        for status_filter in PORTAL_ACCESS_REQUEST_STATUS_FILTERS
    }
    return (
        requested
        if requested in valid_statuses
        else PORTAL_ACCESS_REQUEST_DEFAULT_STATUS
    )


def _portal_app_pending_counts(app_definitions):
    counts = {app["code"]: 0 for app in app_definitions}
    rows = (
        db.session.query(PortalAppAccess.app_code, func.count(PortalAppAccess.id))
        .filter(
            PortalAppAccess.status == "pending",
            PortalAppAccess.is_active.is_(True),
        )
        .group_by(PortalAppAccess.app_code)
        .all()
    )
    for app_code, count in rows:
        if app_code in counts:
            counts[app_code] = count
    return counts


def _portal_management_return_url():
    raw_app = request.form.get("return_app")
    raw_status = request.form.get("return_status")
    search = request.form.get("return_q", "").strip()
    if raw_app is None and raw_status is None and not search:
        return url_for("auth.portal_management")

    app_definitions = portal_app_definitions()
    selected_app = _portal_management_app_filter(app_definitions, raw_app)
    selected_status = _portal_management_status_filter(raw_status)
    query = {}
    if selected_app:
        query["app"] = selected_app
    query["status"] = selected_status
    if search:
        query["q"] = search
    return url_for("auth.portal_management", **query)


def _portal_app_access_rows(user):
    # Profiles are read-only. Missing legacy mappings are displayed, never seeded.
    access_by_code = {a.app_code: a for a in PortalAppAccess.query.filter_by(user_id=user.id).all()}
    return [{"app": app, "access": access_by_code.get(app["code"])} for app in portal_app_definitions()]


def _has_app_access_form_fields():
    return any(key.startswith("app_status_") for key in request.form)


def _email_verification_bypass_requested():
    """The checkbox is intentionally per-request, never a user-level grant."""
    return str(request.form.get("bypass_email_verification", "")).lower() in {
        "1", "true", "on", "yes"
    }


def _approval_notes_with_verification_bypass(notes, bypass):
    if not bypass:
        return notes
    marker = "EMAIL VERIFICATION BYPASS"
    if marker in str(notes or ""):
        return notes
    return f"{marker} · {notes}" if notes else marker


def _apply_portal_app_access_form(target_user, bypass_email_verification=False):
    for app in portal_app_definitions():
        app_code = app["code"]
        if f"app_status_{app_code}" not in request.form:
            continue
        if app.get("coming_soon"):
            raise ValueError("NeoBid access changes are unavailable before launch.")
        status = request.form.get(f"app_status_{app_code}", "none").strip().lower()
        is_active = request.form.get(f"app_active_{app_code}") == "1"
        if status not in {"none", "pending", "approved", "denied"}:
            raise ValueError("Unsupported app access status selected.")

        access = PortalAppAccess.query.filter_by(
            user_id=target_user.id,
            app_code=app_code,
        ).first()
        role_value = request.form.get(f"app_role_{app_code}")
        if role_value is None:
            role_value = access.role if access else "watcher"
        role = str(role_value or "watcher").strip().lower()
        if role not in ROLE_CHOICES:
            raise ValueError("Unsupported app role selected.")
        guard_app_role_change(target_user, access, role, granting=(status == "approved" and is_active))

        if status == "none":
            if access:
                access.is_active = False
            continue

        access = ensure_user_app_access(target_user, app_code)
        previous_status = access.status
        if status == "approved" and (previous_status != "approved" or (is_active and not access.is_active)):
            _approve_portal_app_access(
                access,
                role,
                request.form.get(f"app_notes_{app_code}", "").strip() or None,
                is_active=is_active,
                bypass_email_verification=bypass_email_verification,
            )
        elif status == "denied" and previous_status != "denied":
            _deny_portal_app_access(access, request.form.get(f"app_notes_{app_code}", "").strip() or None)
        else:
            access.status = status
        access.role = role
        access.is_active = is_active


def _approve_portal_app_access(
    access, role, notes, is_active=True, bypass_email_verification=False
):
    from app.services.accounts_workspace import require_available_app, close_open_attempt
    if access:
        require_available_app(access.app_code)
        from app.services.fuel_driver_accounts import requested_driver
        if requested_driver(access.user):
            raise ValueError("Verify the UPS ID and approve this request as a restricted Fuel Truck Driver account.")
    if access and access.user.scorpion_vendor:
        raise ValueError("Convert this Fuel Truck Driver to FULL NEO ACCOUNT before granting app access.")
    if not access:
        raise ValueError("App access request was not found.")
    if role not in ROLE_CHOICES:
        raise ValueError("Unsupported app role selected.")
    guard_app_role_change(access.user, access, role)
    if not access.user.email_verified_at and not bypass_email_verification:
        raise ValueError("Email not verified yet.")
    notes = _approval_notes_with_verification_bypass(notes, bypass_email_verification)

    if access.app_code == "neogateway":
        gateway = get_current_gateway()
        membership = _current_gateway_membership_for_user(access.user, gateway)
        if not membership:
            membership = ensure_user_gateway_membership(access.user, gateway)
        _approve_membership(
            membership, notes, seed_role=role,
            bypass_email_verification=bypass_email_verification,
        )

    access.status = "approved"
    access.role = role
    access.is_active = is_active
    access.approved_by_user_id = current_user.id
    access.approved_at = datetime.utcnow()
    access.approval_notes = notes
    access.denied_by_user_id = None
    access.denied_at = None
    access.denial_notes = None
    close_open_attempt(access.user, access.app_code, "approved", notes)


def _deny_portal_app_access(access, notes):
    if not access:
        raise ValueError("App access request was not found.")

    if access.app_code == "neogateway":
        from app.services.account_scope_runtime import revoke_gateway_ranks
        revoke_gateway_ranks(access.user_id,notes or 'NeoGateway access denied.')
        gateway = get_current_gateway()
        membership = _current_gateway_membership_for_user(access.user, gateway)
        if membership:
            _guard_last_grandmaster_gateway_change(access.user)
            _deny_membership(membership, notes)

    access.status = "denied"
    access.is_active = True
    access.denied_by_user_id = current_user.id
    access.denied_at = datetime.utcnow()
    access.denial_notes = notes
    access.approved_by_user_id = None
    access.approved_at = None
    access.approval_notes = None
    from app.services.accounts_workspace import close_open_attempt
    close_open_attempt(access.user, access.app_code, "denied", notes)


def _account_form_from_request():
    return {
        "first_name": request.form.get("first_name", ""),
        "last_name": request.form.get("last_name", ""),
        "employee_id": request.form.get("employee_id", ""),
        "supervisor_name": request.form.get("supervisor_name", ""),
        "email": request.form.get("email", ""),
        "work_area": request.form.get("work_area", ""),
        "access_reason": request.form.get("access_reason", ""),
        "app_codes": _requested_portal_apps_from_form() if request.method == "POST" else ["neogateway"],
        "account_type": request.form.get("account_type", request.args.get("account_type", "full")),
    }


def _build_user_from_account_form(form):
    email = _normalize_email(form["email"])
    username = email
    first_name = form["first_name"].strip()
    last_name = form["last_name"].strip()
    employee_id = form["employee_id"].strip()
    if not re.fullmatch(r"(?:[0-9]{7}|[0-9]{10})", employee_id):
        raise ValueError("Employee / UPS ID is required. Enter exactly 7 or 10 digits (1234567 or 1234567890), numbers only.")
    account_type = form.get("account_type", "full")
    if account_type not in {"full", "fuel_driver", "vendor_supervisor"}:
        raise ValueError("Choose Full NeoApps, Fuel Truck Driver or Vendor Supervisor.")
    if account_type != "full":
        if form["app_codes"] != ["neogateway"]:
            raise ValueError("Restricted account requests use NeoGateway access only.")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    required_values = {
        "First name": first_name,
        "Last name": last_name,
        "Supervisor / manager name": form["supervisor_name"].strip(),
        "Email": email,
        "Work area": form["work_area"].strip(),
        "Reason for access": form["access_reason"].strip(),
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise ValueError(f"{', '.join(missing)} required.")
    from app.services.accounts_workspace import require_available_app
    for app_code in form["app_codes"]:
        require_available_app(app_code)
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 80:
        raise ValueError("Enter a valid email address (80 characters maximum).")
    for key, limit in (("first_name", 80), ("last_name", 80), ("employee_id", 80), ("supervisor_name", 160), ("work_area", 160), ("access_reason", 2000)):
        if len(form[key]) > limit:
            raise ValueError(f"{key.replace('_', ' ').title()} is too long.")

    _raise_for_duplicate_identity(
        email,
        employee_id,
        generic_registration_conflict=True,
    )

    user = User(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        full_name=_combined_name(first_name, last_name),
        employee_id=employee_id,
        supervisor_name=form["supervisor_name"].strip(),
        work_area=form["work_area"].strip(),
        access_reason=form["access_reason"].strip(),
        role="watcher",
        is_active=True,
    )
    set_user_password(
        user,
        password,
        confirm_password,
        email=email,
        employee_id=employee_id,
    )
    db.session.add(user)
    db.session.flush()
    from app.models import FuelDriverIdentity, AccountSecurityEvent
    db.session.add(FuelDriverIdentity(user_id=user.id, requested_driver=account_type != "full", account_type=account_type))
    db.session.add(AccountSecurityEvent(actor_user_id=user.id,subject_user_id=user.id,
        action='account_type_requested',details=dict(account_type=account_type,applications=form['app_codes'],origin='registration')))
    return user


def _raise_for_duplicate_identity(
    email,
    employee_id,
    user_id=None,
    generic_registration_conflict=False,
):
    def raise_conflict(message):
        if generic_registration_conflict:
            raise ValueError(GENERIC_REGISTRATION_CONFLICT_RESPONSE)
        raise ValueError(message)

    if employee_id:
        from app.services.fuel_driver_accounts import guard_identity_unique
        try:
            guard_identity_unique(employee_id, user_id)
        except ValueError:
            raise_conflict("That UPS / employee ID is already assigned to another account.")

    if User.query.filter(func.lower(User.email) == email.lower()).first():
        existing = User.query.filter(func.lower(User.email) == email.lower()).first()
        if not user_id or existing.id != user_id:
            raise_conflict("That email is already in use.")

    if User.query.filter(func.lower(User.username) == email.lower()).first():
        existing = User.query.filter(func.lower(User.username) == email.lower()).first()
        if not user_id or existing.id != user_id:
            raise_conflict("That email cannot be used.")

    if employee_id:
        existing = User.query.filter(func.lower(User.employee_id) == employee_id.lower()).first()
        if existing and (not user_id or existing.id != user_id):
            raise_conflict("That employee ID is already in use.")


def _find_user_by_login(login_value):
    normalized = login_value.strip().lower()
    user = User.query.filter(func.lower(User.email) == normalized).first()
    if user:
        return user

    # Legacy fallback only: older local/admin accounts may still know username.
    return User.query.filter(func.lower(User.username) == normalized).first()


def _user_edit_form_from_request(user):
    if request.method == "POST":
        return {
            "first_name": request.form.get("first_name", ""),
            "last_name": request.form.get("last_name", ""),
            "employee_id": request.form.get("employee_id", ""),
            "email": request.form.get("email", ""),
            "is_management": request.form.get("is_management", ""),
            "management_level": request.form.get("management_level", ""),
            "supervisor_name": request.form.get("supervisor_name", ""),
            "work_area": request.form.get("work_area", ""),
            "access_reason": request.form.get("access_reason", ""),
        }

    first_name = user.first_name or ""
    last_name = user.last_name or ""
    if not first_name and not last_name and user.full_name:
        parts = user.full_name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

    return {
        "first_name": first_name,
        "last_name": last_name,
        "employee_id": user.employee_id or "",
        "email": user.email or "",
        "is_management": "1" if getattr(user, "is_management", False) else "",
        "management_level": user.management_level or "",
        "supervisor_name": user.supervisor_name or "",
        "work_area": user.work_area or "",
        "access_reason": user.access_reason or "",
    }


def _apply_user_edit_form(user, form):
    first_name = form["first_name"].strip()
    last_name = form["last_name"].strip()
    email = _normalize_email(form["email"])
    employee_id = form["employee_id"].strip() or None

    required_values = {
        "First name": first_name,
        "Last name": last_name,
        "Email": email,
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise ValueError(f"{', '.join(missing)} required.")
    import re
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 80:
        raise ValueError("Enter a valid email address (80 characters maximum).")
    for key, limit in (("first_name",80),("last_name",80),("employee_id",80),("supervisor_name",160),("work_area",160),("access_reason",2000)):
        if len(form[key]) > limit:
            raise ValueError(f"{key.replace('_',' ').title()} is too long.")

    _raise_for_duplicate_identity(email, employee_id, user_id=user.id)

    old_email = _normalize_email(user.email)
    old_username = user.username
    if email != old_email:
        from app.services.grandmaster_commissioning import guard_last_recoverable_platform_admin_email_change

        guard_last_recoverable_platform_admin_email_change(user)
        if old_email and _normalize_email(old_username) == old_email:
            user.username = email
        user.email_verified_at = None
        revoke_unused_email_verification_tokens(user)
        revoke_unused_password_reset_tokens(user)
        rotate_user_session_version(user)
        record_security_change("email_changed", user, previous_email=old_email, new_email=email,
                               verification_required=True)
        clear_login_identifier_failures(old_email, email, old_username, user.username)

    user.first_name = first_name
    user.last_name = last_name
    user.full_name = _combined_name(first_name, last_name)
    user.email = email
    user.employee_id = employee_id
    user.is_management = str(form.get("is_management") or "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }
    management_level = str(form.get("management_level") or "").strip() or None
    if management_level and management_level not in MANAGEMENT_LEVELS:
        raise ValueError("Unsupported management level.")
    user.management_level = management_level if user.is_management else None
    user.supervisor_name = form["supervisor_name"].strip()
    user.work_area = form["work_area"].strip()
    user.access_reason = form["access_reason"].strip()


def _render_user_edit_form(target_user, gateway, membership, form):
    return render_template(
        "auth/user_edit.html",
        app_access_rows=_portal_app_access_rows(target_user),
        form=form,
        gateway=gateway,
        membership=membership,
        node_rows=_node_role_rows(target_user, membership),
        target_user=target_user,
        management_level_choices=MANAGEMENT_LEVELS,
    )


def _apply_gateway_membership_edit_form(
    target_user, gateway, membership, bypass_email_verification=False
):
    if "membership_status" not in request.form:
        return membership

    status = request.form.get("membership_status", "pending").strip().lower()
    is_active = request.form.get("membership_is_active") == "1"
    if status not in {"pending", "approved", "denied"}:
        raise ValueError("Unsupported gateway membership status.")

    if not membership:
        membership = ensure_user_gateway_membership(target_user, gateway)

    if status != "approved" or not is_active:
        _guard_last_grandmaster_gateway_change(target_user)

    was_inactive = not membership.is_active
    if status == "approved":
        if membership.status != "approved":
            _approve_membership(
                membership,
                request.form.get("approval_notes", "").strip() or None,
                bypass_email_verification=bypass_email_verification,
            )
        membership.is_active = is_active
        if was_inactive and membership.is_active:
            _restore_approved_membership_operational_state(membership)
    elif status == "denied":
        if membership.status != "denied":
            _deny_membership(
                membership,
                request.form.get("denial_notes", "").strip() or None,
            )
        membership.is_active = is_active
    else:
        changed = membership.status != "pending"
        membership.status = "pending"
        membership.is_active = is_active
        if changed:
            membership.approved_by_user_id = None
            membership.approved_at = None
            membership.approval_notes = None
            membership.denied_by_user_id = None
            membership.denied_at = None
            membership.denial_notes = None
            membership.approval_email_sent_at = None

    return membership


def _has_node_role_form_fields():
    return any(key.startswith("node_") for key in request.form)


def _combined_name(first_name, last_name):
    return " ".join(part for part in (first_name, last_name) if part).strip()


def _find_user_by_email(email):
    return User.query.filter(func.lower(User.email) == email.lower()).first()


def _normalize_email(value):
    return (value or "").strip().lower()


def _membership_or_404(membership_id):
    gateway = get_current_gateway()
    return GatewayMembership.query.filter_by(
        id=membership_id,
        gateway_id=gateway.id,
    ).first_or_404()


def _pending_memberships_for_gateway(gateway):
    return (
        GatewayMembership.query.filter_by(gateway_id=gateway.id, status="pending")
        .join(User, GatewayMembership.user_id == User.id)
        .order_by(GatewayMembership.created_at.asc())
    )


def _current_gateway_membership_for_user(user, gateway):
    return GatewayMembership.query.filter_by(
        user_id=user.id,
        gateway_id=gateway.id,
    ).first()


def _gateway_memberships_by_user_id(gateway):
    memberships = GatewayMembership.query.filter_by(gateway_id=gateway.id).all()
    return {membership.user_id: membership for membership in memberships}


def _node_roles_by_user_id(gateway):
    memberships = GatewayMembership.query.filter_by(gateway_id=gateway.id).all()
    for membership in memberships:
        if _membership_is_approved_active(membership):
            seed_gateway_node_roles(
                membership,
                _neogateway_seed_role_for_membership(membership.user, membership),
                overwrite_existing=False,
            )

    membership_ids = [membership.id for membership in memberships]
    user_id_by_membership_id = {
        membership.id: membership.user_id for membership in memberships
    }
    roles_by_user_id = {}
    if not membership_ids:
        return roles_by_user_id

    roles = (
        GatewayNodeRole.query.filter(
            GatewayNodeRole.gateway_membership_id.in_(membership_ids),
            GatewayNodeRole.is_active.is_(True),
        )
        .join(NeoNode)
        .order_by(NeoNode.sort_order.asc(), NeoNode.name.asc())
        .all()
    )
    for role in roles:
        user_id = user_id_by_membership_id.get(role.gateway_membership_id)
        roles_by_user_id.setdefault(user_id, []).append(role)
    return roles_by_user_id


def _search_user_management_rows(gateway, search):
    pattern = f"%{search.lower()}%"
    query = User.query.filter(
        func.lower(User.first_name).like(pattern)
        | func.lower(User.last_name).like(pattern)
        | func.lower(User.full_name).like(pattern)
        | func.lower(User.employee_id).like(pattern)
        | func.lower(User.email).like(pattern)
    )

    users = query.order_by(
        User.last_name.asc(),
        User.first_name.asc(),
        User.full_name.asc(),
        User.email.asc(),
    ).limit(50).all()
    return _user_management_rows(gateway, users)


def _all_user_management_rows(gateway):
    """Return every user in the stable administrative directory order."""
    users = User.query.order_by(
        func.lower(User.last_name).asc(),
        func.lower(User.first_name).asc(),
        func.lower(User.full_name).asc(),
        func.lower(User.email).asc(),
    ).all()
    return _user_management_rows(gateway, users)


def _user_management_rows(gateway, users):
    memberships_by_user_id = _gateway_memberships_by_user_id(gateway)
    node_roles_by_user_id = _node_roles_by_user_id(gateway)
    app_accesses_by_user_id = _portal_app_accesses_by_user_id(users)
    return [
        _user_management_row(
            user,
            memberships_by_user_id,
            node_roles_by_user_id,
            app_accesses_by_user_id,
        )
        for user in users
    ]


def _portal_app_accesses_by_user_id(users):
    user_ids = [user.id for user in users]
    if not user_ids:
        return {}

    access_rows = PortalAppAccess.query.filter(
        PortalAppAccess.user_id.in_(user_ids)
    ).all()
    accesses_by_user_id = {}
    for access in access_rows:
        accesses_by_user_id.setdefault(access.user_id, []).append(access)
    return accesses_by_user_id


def _user_management_row(
    user,
    memberships_by_user_id,
    node_roles_by_user_id,
    app_accesses_by_user_id,
):
    membership = memberships_by_user_id.get(user.id)
    node_roles = node_roles_by_user_id.get(user.id, [])
    app_accesses = app_accesses_by_user_id.get(user.id, [])
    return {
        "user": user,
        "membership": membership,
        "membership_status": membership.status if membership else "none",
        "important_roles": _important_node_roles(node_roles),
        "highest_role": _highest_node_role(node_roles),
        "approval_summary": _user_approval_summary(membership, app_accesses),
        "access_summary": _user_access_summary(membership, node_roles, app_accesses),
    }


def _user_approval_summary(membership, app_accesses):
    statuses = []
    if membership:
        statuses.append(f"RFD: {_access_status_label(membership)}")

    app_names = {app["code"]: app["name"] for app in portal_app_definitions()}
    for access in sorted(app_accesses, key=lambda row: row.app_code):
        if access.app_code == "neogateway":
            continue
        statuses.append(
            f"{app_names.get(access.app_code, access.app_code)}: "
            f"{_access_status_label(access)}"
        )
    return " | ".join(statuses) if statuses else "No app access requested"


def _user_access_summary(membership, node_roles, app_accesses):
    access_levels = []
    if membership:
        if _membership_is_approved_active(membership):
            access_levels.append(f"RFD: {_role_label(_highest_node_role(node_roles))}")
        else:
            access_levels.append(f"RFD: {_access_status_label(membership)}")

    app_names = {app["code"]: app["name"] for app in portal_app_definitions()}
    for access in sorted(app_accesses, key=lambda row: row.app_code):
        if access.app_code == "neogateway":
            continue
        if access.status == "approved" and access.is_active:
            value = _role_label(access.role)
        else:
            value = _access_status_label(access)
        access_levels.append(f"{app_names.get(access.app_code, access.app_code)}: {value}")
    return " | ".join(access_levels) if access_levels else "No active app access"


def _access_status_label(access):
    label = str(access.status or "none").strip().replace("_", " ").title()
    return label if access.is_active else f"{label} (inactive)"


def _user_management_summary(gateway):
    return {
        "pending": GatewayMembership.query.filter_by(
            gateway_id=gateway.id,
            status="pending",
        ).count(),
        "approved": GatewayMembership.query.filter_by(
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        ).count(),
        "denied": GatewayMembership.query.filter_by(
            gateway_id=gateway.id,
            status="denied",
        ).count(),
        "password_reset_required": User.query.filter_by(
            password_reset_required=True
        ).count(),
        "unverified_email": User.query.filter(User.email_verified_at.is_(None)).count(),
    }


def _important_node_roles(node_roles):
    if not node_roles:
        return "Watcher fallback"

    return ", ".join(f"{role.node.name}: {_role_label(role.role)}" for role in node_roles)


def _role_label(role):
    if not role:
        return ""
    normalized = str(role).strip().lower()
    return ROLE_DISPLAY_LABELS.get(
        normalized,
        str(role).strip().replace("_", " ").title(),
    )


def _highest_node_role(node_roles):
    if not node_roles:
        return "watcher"

    return max(node_roles, key=lambda role: ROLE_LEVELS.get(role.role, 0)).role


def _node_role_rows(user, membership):
    nodes = NeoNode.query.filter_by(is_active=True).order_by(
        NeoNode.sort_order.asc(), NeoNode.name.asc(),
    ).all()
    prime_user_node_roles_for_request(user, get_current_gateway().code, [node.code for node in nodes])
    seed_role = _neogateway_seed_role_for_membership(user, membership)
    existing_roles = {}
    if membership:
        existing_roles = {
            role.node_id: role for role in GatewayNodeRole.query.filter_by(
                gateway_membership_id=membership.id,
            ).all()
        }

    rows = []
    for node in nodes:
        override = existing_roles.get(node.id)
        effective_role = (override.role if override.is_active else "disabled") if override else seed_role
        rows.append({
            "node": node, "override": override, "effective_role": effective_role,
            "source_label": "Disabled" if effective_role == "disabled" else (
                override.role if override else (
                    "Seeded from NeoGateway approval" if seed_role != "watcher" else "Watcher fallback"
                )
            ),
            "role_choices": _role_choices_for_node(override, effective_role=effective_role, node=node),
        })
    return rows


def _approval_node_role_rows():
    return [
        {
            "node": node,
            "effective_role": "watcher",
            "role_choices": _role_choices_for_node(None, effective_role="watcher", node=node),
        }
        for node in NeoNode.query.filter_by(is_active=True).order_by(
            NeoNode.sort_order.asc(),
            NeoNode.name.asc(),
        ).all()
    ]


def _neogateway_seed_role_for_membership(user, membership):
    from app.services.account_scope_runtime import scoped_active
    if scoped_active():return 'watcher'
    if not _membership_is_approved_active(membership):
        return "watcher"

    access = get_user_app_access(user, "neogateway")
    if access and access.status == "approved" and access.is_active and access.role in ROLE_CHOICES:
        return access.role

    return "watcher"


def _apply_node_role_form(target_user, membership):
    if target_user.scorpion_vendor:
        raise ValueError("Fuel Truck Driver accounts cannot receive NeoNode roles.")
    if not _membership_is_approved_active(membership):
        raise ValueError("User must have approved RFD gateway access before assigning node roles.")

    # A role edit must work for every canonical approved membership, including
    # one approved by the explicit email-verification override.  Seed any
    # missing active NeoNodes before applying independent changes.
    if platform_grandmaster(current_user):
        seed_gateway_node_roles(
            membership,
            _neogateway_seed_role_for_membership(target_user, membership),
            overwrite_existing=False,
        )
    nodes = NeoNode.query.filter_by(is_active=True).all()
    prime_user_node_roles_for_request(target_user, get_current_gateway().code, [node.code for node in nodes])
    existing_roles = {
        role.node_id: role
        for role in GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
        ).all()
    }

    for node in nodes:
        field_name = f"node_{node.id}"
        if field_name not in request.form:
            continue

        selected_role = request.form.get(field_name, "").strip().lower()
        existing_role = existing_roles.get(node.id)
        effective_existing = existing_role or SimpleNamespace(
            role=_neogateway_seed_role_for_membership(target_user, membership), is_active=True,
        )
        guard_node_change(current_user, target_user, node, effective_existing, selected_role)
        if selected_role == "disabled":
            if existing_role and existing_role.is_active:
                if _would_remove_last_grandmaster(node, selected_role, existing_role):
                    raise ValueError("Cannot remove the last active Grandmaster MotherBrain access.")
                existing_role.is_active = False
                record_security_change("node_role_changed", target_user, node=node.code,
                                       previous_role=existing_role.role, new_role="disabled")
            continue
        if selected_role not in ROLE_CHOICES:
            raise ValueError("Unsupported node role selected.")

        existing_role = existing_roles.get(node.id)
        _guard_role_assignment_allowed(selected_role, existing_role, node=node)

        if _would_remove_last_grandmaster(node, selected_role, existing_role):
            raise ValueError(
                "Cannot remove or downgrade the last active Grandmaster MotherBrain access."
            )

        if not existing_role:
            record_security_change("node_role_changed", target_user, node=node.code,
                                   previous_role=None, new_role=selected_role)
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=node.id,
                    role=selected_role,
                    is_active=True,
                )
            )
            continue

        if existing_role.role != selected_role or not existing_role.is_active:
            record_security_change("node_role_changed", target_user, node=node.code,
                                   previous_role=existing_role.role, new_role=selected_role)
        existing_role.role = selected_role
        existing_role.is_active = True


def _approval_node_role_values_from_form():
    if not _has_node_role_form_fields():
        return None

    values = {}
    for node in NeoNode.query.filter_by(is_active=True).all():
        field_name = f"node_{node.id}"
        if field_name not in request.form:
            continue
        selected_role = request.form.get(field_name, "").strip().lower()
        if selected_role == "disabled":
            values[node.id] = selected_role
            continue
        if selected_role not in ROLE_CHOICES:
            raise ValueError("Unsupported node role selected.")
        _guard_role_assignment_allowed(selected_role, None, node=node)
        values[node.id] = selected_role
    return values


def _apply_permission_rule_form():
    from app.services.permission_thresholds import validate_threshold

    rule_ids = request.form.getlist("rule_ids")
    parsed_ids = []
    for value in rule_ids:
        try:
            parsed_ids.append(int(value))
        except (TypeError, ValueError):
            parsed_ids.append(None)
    rules = {rule.id: rule for rule in PermissionRule.query.filter(
        PermissionRule.id.in_({value for value in parsed_ids if value is not None}),
    ).all()} if any(value is not None for value in parsed_ids) else {}
    # Replay original order: missing IDs are skipped and duplicates retained.
    # A malformed later ID must not mask an earlier role/configurability error.
    for rule_id in parsed_ids:
        if rule_id is None:
            raise ValueError("Unsupported permission rule selected.")
        rule = rules.get(rule_id)
        if not rule:
            continue
        if not permission_is_configurable(rule.permission_key):
            raise ValueError("Unsupported permission rule selected.")
        if not may_manage_permission(current_user, rule.permission_key):
            raise ValueError("You cannot modify permissions outside your administrative scope.")

        selected = request.form.get(f"minimum_role_{rule.id}")
        minimum_role = validate_threshold(
            rule.permission_key, rule.minimum_role,
            selected.strip().lower() if selected is not None else None, scoped=False,
        )
        if selected is None:
            continue
        description = request.form.get(f"description_{rule.id}", "").strip()

        if rule.minimum_role != minimum_role or rule.description != description:
            record_security_change("permission_rule_changed", current_user,
                                   permission=rule.permission_key, previous_role=rule.minimum_role,
                                   new_role=minimum_role)
        rule.minimum_role = minimum_role
        rule.description = description


def _role_choices_for_node(existing_role, effective_role=None, node=None):
    choices = [
        role
        for role in ROLE_CHOICES
        if _current_user_can_assign_role(role, node=node)
    ]
    existing_effective_role = effective_role or (existing_role.role if existing_role else "watcher")
    if existing_effective_role not in choices:
        choices.append(existing_effective_role)

    return sorted(set(choices), key=lambda role: ROLE_LEVELS.get(role, 0))


def _guard_role_assignment_allowed(selected_role, existing_role, node=None):
    existing_effective_role = existing_role.role if existing_role else "watcher"
    if selected_role == existing_effective_role and (not existing_role or existing_role.is_active):
        return

    if (existing_role and not _current_user_can_assign_role(existing_role.role, node=node)
            or not _current_user_can_assign_role(selected_role, node=node)):
        raise ValueError("You cannot assign a role equal to or higher than your own role.")


def _current_user_can_assign_role(role, node=None):
    from app.services.account_scope_runtime import scoped_active
    if node is not None and role=='grandmaster' and scoped_active():
        return False
    if role not in ROLE_LEVELS:
        return False
    if node is not None:
        return platform_grandmaster(current_user) or (node_authority(current_user, node) >= ROLE_LEVELS["master"]
                and ROLE_LEVELS[role] < node_authority(current_user, node))
    return (user_can(USER_MANAGEMENT_EDIT_PERMISSION)
            and ROLE_LEVELS[role] < ROLE_LEVELS.get(current_user.role, 0))


def _would_remove_last_grandmaster(node, selected_role, existing_role):
    if node.code != "motherbrain":
        return False
    if selected_role == "grandmaster":
        return False
    if not existing_role or existing_role.role != "grandmaster" or not existing_role.is_active:
        return False
    return _active_motherbrain_grandmaster_count() <= 1


def _guard_last_grandmaster_gateway_change(target_user):
    if not _target_has_active_motherbrain_grandmaster(target_user):
        return
    if _active_motherbrain_grandmaster_count() <= 1:
        raise ValueError("Cannot remove the last active Grandmaster gateway access.")


def _target_has_active_motherbrain_grandmaster(target_user):
    gateway = get_current_gateway()
    motherbrain = NeoNode.query.filter_by(code="motherbrain", is_active=True).first()
    if not motherbrain:
        return False

    return (
        GatewayNodeRole.query.join(GatewayMembership)
        .filter(
            GatewayMembership.user_id == target_user.id,
            GatewayMembership.gateway_id == gateway.id,
            GatewayMembership.status == "approved",
            GatewayMembership.is_active.is_(True),
            GatewayNodeRole.node_id == motherbrain.id,
            GatewayNodeRole.role == "grandmaster",
            GatewayNodeRole.is_active.is_(True),
        )
        .first()
        is not None
    )


def _active_motherbrain_grandmaster_count():
    gateway = get_current_gateway()
    motherbrain = NeoNode.query.filter_by(code="motherbrain", is_active=True).first()
    if not motherbrain:
        return 0

    return (
        GatewayNodeRole.query.join(GatewayMembership)
        .join(User, GatewayMembership.user_id == User.id)
        .outerjoin(PortalAppAccess, and_(PortalAppAccess.user_id == User.id,
                                        PortalAppAccess.app_code == "neogateway"))
        .filter(
            GatewayMembership.gateway_id == gateway.id,
            GatewayMembership.status == "approved",
            GatewayMembership.is_active.is_(True),
            GatewayNodeRole.node_id == motherbrain.id,
            GatewayNodeRole.role == "grandmaster",
            GatewayNodeRole.is_active.is_(True),
            User.is_active.is_(True),
            or_(PortalAppAccess.id.is_(None), and_(
                PortalAppAccess.status == "approved", PortalAppAccess.is_active.is_(True))),
            ~User.scorpion_vendor.has(),
        )
        .count()
    )


def _membership_is_approved_active(membership):
    return bool(
        membership
        and membership.status == "approved"
        and membership.is_active
        and membership.gateway
        and membership.gateway.is_active
    )


def _restore_approved_membership_operational_state(membership):
    """Keep an approved active RFD membership usable for independent roles."""
    if not _membership_is_approved_active(membership):
        return
    if membership.user.scorpion_vendor:
        raise ValueError("Fuel Truck Driver accounts cannot receive full NeoApps access.")
    if not membership.gateway or membership.gateway.code != get_current_gateway().code:
        return

    app_access = ensure_user_app_access(membership.user, "neogateway")
    app_access.status = "approved"
    app_access.is_active = True
    if app_access.role not in ROLE_CHOICES:
        app_access.role = "watcher"
    seed_gateway_node_roles(membership, app_access.role, overwrite_existing=False)


def _change_driver_account_type(gateway, target_user, account_type):
    if not user_can(USER_MANAGEMENT_EDIT_PERMISSION):
        raise ValueError("User Management edit authority is required for account conversion.")
    if target_user.id == current_user.id:
        raise ValueError("You cannot convert your own account.")
    if target_user.role == "grandmaster":
        raise ValueError("A protected platform Grandmaster cannot be converted through ordinary account controls.")
    if account_type == "vendor":
        from app.services.fuel_driver_accounts import ups_id, identity_verified, requested_driver
        ups_id(target_user.employee_id)
        if requested_driver(target_user) and not identity_verified(target_user):
            raise ValueError("An administrator must verify this Fuel Truck Driver applicant's UPS ID before approval.")
    def authorize(locked_target):
        lock_administration()
        if not user_can(USER_MANAGEMENT_EDIT_PERMISSION):
            raise ValueError("User Management authority changed.")
        if locked_target.role == "grandmaster":
            raise ValueError("A protected platform Grandmaster cannot be converted through ordinary account controls.")
        if account_type == "vendor":
            from app.services.fuel_driver_accounts import ups_id, identity_verified, requested_driver
            ups_id(locked_target.employee_id)
            if requested_driver(locked_target) and not identity_verified(locked_target):
                raise ValueError("Verify the Fuel Truck Driver UPS ID before approval.")
        if "review_token" in request.form:
            from app.services.accounts_workspace import verify_review
            verify_review(locked_target, request.form.get("review_token", ""))
    from app.services.neoscorpion_account_types import convert_account
    changed = convert_account(gateway, current_user, target_user, account_type,
                           can_change_role=lambda role: platform_grandmaster(current_user) and role in ROLE_CHOICES,
                           authorize=authorize)
    if changed:
        from app.models import FuelDriverIdentity, AccountAccessRequest
        from app.services.accounts_workspace import close_open_attempt
        identity = db.session.get(FuelDriverIdentity, target_user.id)
        if identity:
            identity.requested_driver = False
            identity.version += 1
        if account_type == 'vendor':
            for attempt in AccountAccessRequest.query.filter_by(user_id=target_user.id, state='pending').all():
                close_open_attempt(target_user, attempt.app_code, 'approved' if attempt.app_code == 'neogateway' else 'withdrawn',
                    'Approved as a restricted Fuel Truck Driver; no full application access granted.')
    return changed


def _lock_staff_account(user):
    from app.services.neoscorpion_account_types import lock_account_grants
    user = lock_account_grants(user)
    from app.services.account_deletion import receipt
    if receipt(user.id):
        raise ValueError("This account was deleted. Its historical identity is read-only.")
    if user.scorpion_vendor:
        raise ValueError("Convert this Fuel Truck Driver to FULL NEO ACCOUNT before changing staff access.")
    return user


def _approve_account_request(membership, notes, seed_role="watcher",
                             node_role_values=None, bypass_email_verification=False):
    """One approval point for full NeoApps or restricted Fuel Truck Driver."""
    account_type = (request.form.get("account_type") or "full").strip()
    if account_type == "vendor":
        if bypass_email_verification:
            raise ValueError("Fuel Truck Drivers must verify their NeoApps email before approval.")
        _change_driver_account_type(membership.gateway, membership.user, "vendor")
        return
    if account_type != "full":
        raise ValueError("Select a valid account type.")
    _lock_staff_account(membership.user)
    db.session.refresh(membership)
    _approve_membership(membership, notes, seed_role=seed_role,
                        node_role_values=node_role_values,
                        bypass_email_verification=bypass_email_verification)


def _approve_membership(
    membership,
    notes,
    seed_role="watcher",
    node_role_values=None,
    bypass_email_verification=False,
):
    if not membership:
        raise ValueError("Gateway membership request was not found.")
    from app.services.fuel_driver_accounts import requested_driver
    if requested_driver(membership.user):
        raise ValueError("Approve this request as a restricted Fuel Truck Driver, not full application access.")
    if membership.user.scorpion_vendor:
        raise ValueError("Fuel Truck Driver accounts cannot receive full NeoApps access.")
    if seed_role not in ROLE_CHOICES:
        raise ValueError("Unsupported node role selected.")
    prior_app = PortalAppAccess.query.filter_by(user_id=membership.user_id, app_code="neogateway").first()
    guard_app_role_change(membership.user, prior_app, seed_role)
    if not membership.user.email_verified_at and not bypass_email_verification:
        raise ValueError("Email not verified yet.")
    notes = _approval_notes_with_verification_bypass(notes, bypass_email_verification)

    was_approved = membership.status == "approved"
    membership.status = "approved"
    membership.is_active = True
    membership.approved_by_user_id = current_user.id
    membership.approved_at = datetime.utcnow()
    membership.approval_notes = notes
    membership.denied_by_user_id = None
    membership.denied_at = None
    membership.denial_notes = None
    db.session.flush()

    if membership.gateway and membership.gateway.code == get_current_gateway().code:
        app_access = ensure_user_app_access(membership.user, "neogateway")
        app_access.status = "approved"
        app_access.role = seed_role
        app_access.is_active = True
        app_access.approved_by_user_id = current_user.id
        app_access.approved_at = app_access.approved_at or datetime.utcnow()
        app_access.approval_notes = notes
        app_access.denied_by_user_id = None
        app_access.denied_at = None
        app_access.denial_notes = None
        seed_gateway_node_roles(membership, seed_role, overwrite_existing=False)
        if node_role_values is not None:
            node_roles = {
                role.node_id: role
                for role in GatewayNodeRole.query.filter_by(
                    gateway_membership_id=membership.id,
                ).all()
            }
            for node_id, selected_role in node_role_values.items():
                node_role = node_roles.get(node_id)
                if not node_role:
                    continue
                guard_node_change(current_user, membership.user, node_role.node, node_role, selected_role)
                if selected_role == "disabled":
                    if _would_remove_last_grandmaster(node_role.node, selected_role, node_role):
                        raise ValueError("Cannot remove the last active Grandmaster MotherBrain access.")
                    node_role.is_active = False
                else:
                    node_role.role = selected_role
                    node_role.is_active = True

    if not was_approved:
        send_result = email_service.send_access_approved(membership.user, membership.gateway)
        if send_result.get("sent"):
            membership.approval_email_sent_at = datetime.utcnow()
    from app.services.accounts_workspace import close_open_attempt
    close_open_attempt(membership.user, "neogateway", "approved", notes)


def _deny_membership(membership, notes):
    if not membership:
        raise ValueError("Gateway membership request was not found.")

    membership.status = "denied"
    membership.is_active = True
    membership.denied_by_user_id = current_user.id
    membership.denied_at = datetime.utcnow()
    membership.denial_notes = notes
    membership.approved_by_user_id = None
    membership.approved_at = None
    membership.approval_notes = None
    membership.approval_email_sent_at = None

    if membership.gateway and membership.gateway.code == get_current_gateway().code:
        app_access = ensure_user_app_access(membership.user, "neogateway")
        app_access.status = "denied"
        app_access.is_active = True
        app_access.denied_by_user_id = current_user.id
        app_access.denied_at = app_access.denied_at or datetime.utcnow()
        app_access.denial_notes = notes
        app_access.approved_by_user_id = None
        app_access.approved_at = None
        app_access.approval_notes = None
    from app.services.accounts_workspace import close_open_attempt
    close_open_attempt(membership.user, "neogateway", "denied", notes)
