from datetime import datetime
from functools import wraps

from flask import flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, PortalAppAccess, User
from app.models.user import MANAGEMENT_LEVELS, ROLE_LEVELS
from app.services import email_service
from app.services.access_control import (
    ensure_user_app_access,
    get_current_gateway,
    get_user_node_role,
    portal_install_rows_for_user,
    portal_app_definition,
    portal_app_definitions,
    portal_dashboard_rows_for_user,
    request_app_access_for_user,
    request_default_gateway_access_for_user,
    seed_gateway_node_roles,
    user_has_gateway_access,
)
from app.services.permission_rules import ensure_default_permission_rules, grouped_permission_rules, user_can
from app.services.password_policy import (
    PASSWORD_POLICY_GUIDANCE,
    PASSWORD_POLICY_LOGIN_SESSION_KEY,
    set_user_password,
    user_requires_password_change,
)
from app.services.user_tokens import (
    EMAIL_VERIFICATION,
    PASSWORD_RESET,
    create_user_token,
    get_token_record,
    get_valid_token_record,
    mark_token_used,
)


GENERIC_RESET_RESPONSE = "If an account exists for that email, a reset link has been sent."
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
ACCESS_REQUESTS_VIEW_PERMISSION = "neoapps.access_requests.view"
ACCESS_REQUESTS_EDIT_PERMISSION = "neoapps.access_requests.edit"
PERMISSION_RULES_VIEW_PERMISSION = "neomotherbrain.permission_rules.view"
PERMISSION_RULES_EDIT_PERMISSION = "neomotherbrain.permission_rules.edit"
NEOBID_PLACEHOLDER_VIEW_PERMISSION = "neobid.placeholder.view"


def portal_permission_required(permission_key, message="Access denied."):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if user_can(permission_key):
                return view_func(*args, **kwargs)

            flash(message, "error")
            return redirect(url_for("auth.portal_dashboard"))

        return wrapped_view

    return decorator


def portal_grandmaster_required(view_func):
    return portal_permission_required(
        PORTAL_MANAGEMENT_VIEW_PERMISSION,
        "Portal Management requires Grandmaster access.",
    )(view_func)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated and request.method == "GET":
        return redirect(url_for("auth.portal_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        legacy_username = request.form.get("username", "").strip()
        login_identifier = email or legacy_username
        password = request.form.get("password", "")
        user = _find_user_by_login(login_identifier) if login_identifier else None

        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html"), 401

        if not user.is_active:
            flash("This account is inactive.", "error")
            return render_template("auth/login.html"), 403

        login_user(user)
        user.last_login = datetime.utcnow()

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Login failed. Please try again.", "error")
            return render_template("auth/login.html"), 500

        if user_requires_password_change(user):
            if user.password_policy_update_required:
                session[PASSWORD_POLICY_LOGIN_SESSION_KEY] = user.id
            else:
                session.pop(PASSWORD_POLICY_LOGIN_SESSION_KEY, None)
            return redirect(url_for("auth.change_password"))

        session.pop(PASSWORD_POLICY_LOGIN_SESSION_KEY, None)
        return redirect(url_for("auth.portal_dashboard"))

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    session.pop(PASSWORD_POLICY_LOGIN_SESSION_KEY, None)
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/portal")
@login_required
def portal_dashboard():
    if not user_can(PORTAL_VIEW_PERMISSION):
        flash("NeoApps Portal access denied.", "error")
        return redirect(url_for("auth.access_pending"))

    return render_template(
        "auth/portal.html",
        app_rows=portal_dashboard_rows_for_user(current_user),
        install_rows=portal_install_rows_for_user(current_user),
    )


@bp.route("/portal/request-access", methods=["POST"])
@login_required
def request_portal_app_access():
    if not user_can(PORTAL_REQUEST_ACCESS_PERMISSION):
        flash("Access request permission denied.", "error")
        return redirect(url_for("auth.portal_dashboard"))

    app_code = request.form.get("app_code", "").strip().lower()
    app_definition = portal_app_definition(app_code)
    if not app_definition:
        flash("Unsupported app access request.", "error")
        return redirect(url_for("auth.portal_dashboard"))

    try:
        if app_code == "neogateway":
            request_default_gateway_access_for_user(current_user)
        else:
            request_app_access_for_user(current_user, app_code)
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        flash(f"{app_definition['name']} access requested.", "info")

    return redirect(url_for("auth.portal_dashboard"))


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
            user = _build_user_from_account_form(form)
            requested_apps = _requested_portal_apps_from_form()
            if not requested_apps:
                raise ValueError("Select at least one NeoApps system for access.")
            for app_code in requested_apps:
                if app_code == "neogateway":
                    request_default_gateway_access_for_user(user)
                else:
                    request_app_access_for_user(user, app_code)
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
    token_record = get_token_record(token, EMAIL_VERIFICATION)
    if not token_record:
        return render_template("auth/verify_email.html", verified=False), 400
    if token_record.user.email_verified_at and (
        token_record.is_used or token_record.is_expired()
    ):
        return render_template(
            "auth/verify_email.html",
            already_verified=True,
            verified=True,
        )
    if token_record.is_used:
        return render_template("auth/verify_email.html", verified=False), 400
    if token_record.is_expired():
        return render_template("auth/verify_email.html", verified=False), 400

    token_record = get_valid_token_record(token, EMAIL_VERIFICATION)
    if not token_record:
        return render_template("auth/verify_email.html", verified=False), 400

    token_record.user.email_verified_at = token_record.user.email_verified_at or datetime.utcnow()
    mark_token_used(token_record)
    db.session.commit()

    return render_template("auth/verify_email.html", already_verified=False, verified=True)


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
        user = _find_user_by_email(email) if email else None

        if user:
            raw_token, _token_record = create_user_token(user, PASSWORD_RESET)
            db.session.commit()
            email_service.send_password_reset(user, raw_token)

        flash(GENERIC_RESET_RESPONSE, "info")
        return render_template("auth/forgot_password.html", submitted=True)

    return render_template("auth/forgot_password.html", submitted=False)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    token_record = get_valid_token_record(token, PASSWORD_RESET)
    if not token_record:
        return render_template("auth/reset_password.html", token=token, valid=False), 400

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        try:
            set_user_password(token_record.user, password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template(
                "auth/reset_password.html",
                token=token,
                valid=True,
                password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
            ), 400

        token_record.user.password_reset_required = False
        token_record.user.password_policy_update_required = False
        mark_token_used(token_record)
        db.session.commit()
        flash("Password reset complete. You can log in now.", "info")
        return redirect(url_for("auth.login"))

    return render_template(
        "auth/reset_password.html",
        token=token,
        valid=True,
        password_policy_guidance=PASSWORD_POLICY_GUIDANCE,
    )


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced_change = user_requires_password_change(current_user)
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
        session.pop(PASSWORD_POLICY_LOGIN_SESSION_KEY, None)
        db.session.commit()
        flash("Password changed.", "info")
        return redirect(url_for("auth.portal_dashboard"))

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
    gateway = get_current_gateway()
    memberships = _pending_memberships_for_gateway(gateway).all()
    app_access_requests = _pending_portal_app_accesses().all()
    search = request.args.get("q", "").strip()
    rows = _search_user_management_rows(gateway, search) if search else []
    return render_template(
        "auth/portal_management.html",
        app_access_requests=app_access_requests,
        app_definitions=portal_app_definitions(),
        gateway=gateway,
        memberships=memberships,
        role_choices=ROLE_CHOICES,
        rows=rows,
        search=search,
    )


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
        if action == "approve":
            _approve_portal_app_access(access, role, notes)
            flash("App access approved.", "info")
        elif action == "deny":
            _deny_portal_app_access(access, notes)
            flash("App access denied.", "info")
        else:
            raise ValueError("Unsupported app access action.")
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")

    return redirect(url_for("auth.portal_management"))


@bp.route("/admin/users")
@bp.route("/portal/manage/users")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def users():
    gateway = get_current_gateway()
    memberships = _pending_memberships_for_gateway(gateway).all()
    search = request.args.get("q", "").strip()
    rows = _search_user_management_rows(gateway, search) if search else []
    return render_template(
        "auth/users.html",
        gateway=gateway,
        memberships=memberships,
        role_choices=ROLE_CHOICES,
        rows=rows,
        search=search,
    )


@bp.route("/admin/users/edit-users")
@bp.route("/portal/manage/users/edit-users")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def edit_users():
    gateway = get_current_gateway()
    search = request.args.get("q", "").strip()
    rows = _search_user_management_rows(gateway, search) if search else []

    return render_template(
        "auth/edit_users.html",
        gateway=gateway,
        rows=rows,
        search=search,
    )


@bp.route("/admin/users/manage-roles")
@bp.route("/portal/manage/users/manage-roles")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def manage_roles():
    return redirect(url_for("auth.edit_users", q=request.args.get("q", "")))


@bp.route("/admin/users/pending")
@bp.route("/portal/manage/users/pending")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def pending_users():
    gateway = get_current_gateway()
    memberships = _pending_memberships_for_gateway(gateway).all()
    return render_template(
        "auth/pending_users.html",
        gateway=gateway,
        memberships=memberships,
        role_choices=ROLE_CHOICES,
    )


@bp.route("/admin/permissions", methods=["GET", "POST"])
@bp.route("/motherbrain/permissions", methods=["GET", "POST"])
@login_required
def permission_rules():
    ensure_default_permission_rules()
    view_permission = (
        PERMISSION_RULES_EDIT_PERMISSION
        if request.method == "POST"
        else PERMISSION_RULES_VIEW_PERMISSION
    )
    if not user_can(view_permission):
        flash("Permission Rules access denied.", "error")
        return redirect(url_for("auth.portal_dashboard"))

    if request.method == "POST":
        try:
            _apply_permission_rule_form()
            db.session.commit()
            flash("Permission rules updated.", "info")
            return redirect(url_for("auth.permission_rules"))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")

    rules = PermissionRule.query.order_by(PermissionRule.permission_key.asc()).all()
    return render_template(
        "auth/permission_rules.html",
        grouped_rules=grouped_permission_rules(rules),
        role_choices=ROLE_CHOICES,
        rules=rules,
    )


@bp.route("/admin/users/<int:user_id>")
@bp.route("/portal/manage/users/<int:user_id>")
@portal_permission_required(USER_MANAGEMENT_VIEW_PERMISSION, "User Management access denied.")
def user_detail(user_id):
    target_user = User.query.get_or_404(user_id)
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)
    node_rows = _node_role_rows(target_user, membership)
    return render_template(
        "auth/user_detail.html",
        app_access_rows=_portal_app_access_rows(target_user),
        gateway=gateway,
        membership=membership,
        node_rows=node_rows,
        role_choices=ROLE_CHOICES,
        target_user=target_user,
    )


@bp.route("/admin/users/<int:user_id>/edit", methods=["GET", "POST"])
@bp.route("/portal/manage/users/<int:user_id>/edit", methods=["GET", "POST"])
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def edit_user(user_id):
    target_user = User.query.get_or_404(user_id)
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)
    form = _user_edit_form_from_request(target_user)

    if request.method == "POST":
        try:
            _apply_user_edit_form(target_user, form)
            membership = _apply_gateway_membership_edit_form(target_user, gateway, membership)
            if _has_app_access_form_fields():
                _apply_portal_app_access_form(target_user)
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
@portal_permission_required(USER_MANAGEMENT_EDIT_PERMISSION, "User Management edit access denied.")
def user_roles(user_id):
    target_user = User.query.get_or_404(user_id)
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)

    if not _membership_is_approved_active(membership):
        flash("User must have approved RFD gateway access before assigning node roles.", "error")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    if request.method == "POST":
        try:
            _apply_node_role_form(target_user, membership)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return redirect(url_for("auth.user_roles", user_id=target_user.id))

        db.session.commit()
        flash("Node roles updated.", "info")
        return redirect(url_for("auth.edit_user", user_id=target_user.id))

    return render_template(
        "auth/user_roles.html",
        gateway=gateway,
        membership=membership,
        node_rows=_node_role_rows(target_user, membership),
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

    if action in {"approve", "deny"} and not membership:
        membership = GatewayMembership(
            user_id=target_user.id,
            gateway_id=gateway.id,
            status="pending",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

    try:
        if action == "approve":
            _approve_membership(
                membership,
                request.form.get("approval_notes", "").strip() or None,
                seed_role=request.form.get("role", "watcher").strip().lower() or "watcher",
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
    gateway = get_current_gateway()
    memberships = (
        GatewayMembership.query.filter_by(gateway_id=gateway.id, status="pending")
        .join(User, GatewayMembership.user_id == User.id)
        .order_by(GatewayMembership.created_at.asc())
        .all()
    )
    return render_template(
        "auth/access_requests.html",
        gateway=gateway,
        memberships=memberships,
        role_choices=ROLE_CHOICES,
    )


@bp.route("/admin/access-requests/<int:membership_id>/approve", methods=["POST"])
@bp.route("/portal/manage/access-requests/<int:membership_id>/approve", methods=["POST"])
@portal_permission_required(ACCESS_REQUESTS_EDIT_PERMISSION, "Access Requests edit access denied.")
def approve_access_request(membership_id):
    membership = _membership_or_404(membership_id)
    try:
        _approve_membership(
            membership,
            request.form.get("approval_notes", "").strip() or None,
            seed_role=request.form.get("role", "watcher").strip().lower() or "watcher",
        )
    except ValueError as error:
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
    _deny_membership(
        membership,
        request.form.get("denial_notes", "").strip() or None,
    )
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
        target_user.last_password_reset_by_user_id = current_user.id
        target_user.last_password_reset_at = datetime.utcnow()
        target_user.last_password_reset_reason = reason
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
    valid_codes = {app["code"] for app in portal_app_definitions()}
    normalized = []
    for code in selected:
        app_code = str(code or "").strip().lower()
        if app_code in valid_codes and app_code not in normalized:
            normalized.append(app_code)
    return normalized


def _pending_portal_app_accesses():
    return (
        PortalAppAccess.query.filter_by(status="pending", is_active=True)
        .join(User, PortalAppAccess.user_id == User.id)
        .order_by(PortalAppAccess.created_at.asc())
    )


def _portal_app_access_rows(user):
    rows = []
    for app in portal_app_definitions():
        rows.append(
            {
                "app": app,
                "access": ensure_user_app_access(user, app["code"])
                if app["code"] == "neogateway"
                else PortalAppAccess.query.filter_by(
                    user_id=user.id,
                    app_code=app["code"],
                ).first(),
            }
        )
    return rows


def _has_app_access_form_fields():
    return any(key.startswith("app_status_") for key in request.form)


def _apply_portal_app_access_form(target_user):
    for app in portal_app_definitions():
        app_code = app["code"]
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

        if status == "none":
            if access:
                access.is_active = False
            continue

        access = ensure_user_app_access(target_user, app_code)
        access.status = status
        access.role = role
        access.is_active = is_active
        if status == "approved":
            _approve_portal_app_access(
                access,
                role,
                request.form.get(f"app_notes_{app_code}", "").strip() or None,
                is_active=is_active,
            )
        elif status == "denied":
            _deny_portal_app_access(access, request.form.get(f"app_notes_{app_code}", "").strip() or None)


def _approve_portal_app_access(access, role, notes, is_active=True):
    if not access:
        raise ValueError("App access request was not found.")
    if role not in ROLE_CHOICES:
        raise ValueError("Unsupported app role selected.")
    if not access.user.email_verified_at:
        raise ValueError("Email not verified yet.")

    if access.app_code == "neogateway":
        gateway = get_current_gateway()
        membership = _current_gateway_membership_for_user(access.user, gateway)
        if not membership:
            membership = GatewayMembership(
                user_id=access.user_id,
                gateway_id=gateway.id,
                status="pending",
                is_active=True,
            )
            db.session.add(membership)
            db.session.flush()
        _approve_membership(membership, notes, seed_role=role)

    access.status = "approved"
    access.role = role
    access.is_active = is_active
    access.approved_by_user_id = current_user.id
    access.approved_at = datetime.utcnow()
    access.approval_notes = notes
    access.denied_by_user_id = None
    access.denied_at = None
    access.denial_notes = None


def _deny_portal_app_access(access, notes):
    if not access:
        raise ValueError("App access request was not found.")

    if access.app_code == "neogateway":
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
    }


def _build_user_from_account_form(form):
    email = _normalize_email(form["email"])
    username = email
    first_name = form["first_name"].strip()
    last_name = form["last_name"].strip()
    employee_id = form["employee_id"].strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    required_values = {
        "First name": first_name,
        "Last name": last_name,
        "Employee ID": employee_id,
        "Supervisor / manager name": form["supervisor_name"].strip(),
        "Email": email,
        "Work area": form["work_area"].strip(),
        "Reason for access": form["access_reason"].strip(),
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise ValueError(f"{', '.join(missing)} required.")

    _raise_for_duplicate_identity(email, employee_id)

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
    return user


def _raise_for_duplicate_identity(email, employee_id, user_id=None):
    if User.query.filter(func.lower(User.email) == email.lower()).first():
        existing = User.query.filter(func.lower(User.email) == email.lower()).first()
        if not user_id or existing.id != user_id:
            raise ValueError("That email is already in use.")

    if User.query.filter(func.lower(User.username) == email.lower()).first():
        existing = User.query.filter(func.lower(User.username) == email.lower()).first()
        if not user_id or existing.id != user_id:
            raise ValueError("That email cannot be used.")

    if User.query.filter(func.lower(User.employee_id) == employee_id.lower()).first():
        existing = User.query.filter(
            func.lower(User.employee_id) == employee_id.lower()
        ).first()
        if not user_id or existing.id != user_id:
            raise ValueError("That employee ID is already in use.")


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
    employee_id = form["employee_id"].strip()

    required_values = {
        "First name": first_name,
        "Last name": last_name,
        "Employee ID": employee_id,
        "Email": email,
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise ValueError(f"{', '.join(missing)} required.")

    _raise_for_duplicate_identity(email, employee_id, user_id=user.id)

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


def _apply_gateway_membership_edit_form(target_user, gateway, membership):
    if "membership_status" not in request.form:
        return membership

    status = request.form.get("membership_status", "pending").strip().lower()
    is_active = request.form.get("membership_is_active") == "1"
    if status not in {"pending", "approved", "denied"}:
        raise ValueError("Unsupported gateway membership status.")

    if not membership:
        membership = GatewayMembership(
            user_id=target_user.id,
            gateway_id=gateway.id,
            status="pending",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

    if status != "approved" or not is_active:
        _guard_last_grandmaster_gateway_change(target_user)

    if status == "approved":
        if membership.status != "approved":
            _approve_membership(
                membership,
                request.form.get("approval_notes", "").strip() or None,
            )
        membership.is_active = is_active
    elif status == "denied":
        _deny_membership(
            membership,
            request.form.get("denial_notes", "").strip() or None,
        )
        membership.is_active = is_active
    else:
        membership.status = "pending"
        membership.is_active = is_active
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
    memberships_by_user_id = _gateway_memberships_by_user_id(gateway)
    node_roles_by_user_id = _node_roles_by_user_id(gateway)
    return [
        _user_management_row(user, memberships_by_user_id, node_roles_by_user_id)
        for user in users
    ]


def _user_management_row(user, memberships_by_user_id, node_roles_by_user_id):
    membership = memberships_by_user_id.get(user.id)
    node_roles = node_roles_by_user_id.get(user.id, [])
    return {
        "user": user,
        "membership": membership,
        "membership_status": membership.status if membership else "none",
        "important_roles": _important_node_roles(node_roles),
        "highest_role": _highest_node_role(node_roles),
    }


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
        NeoNode.sort_order.asc(),
        NeoNode.name.asc(),
    ).all()
    seed_role = _neogateway_seed_role_for_membership(user, membership)
    existing_roles = {}
    if membership:
        if _membership_is_approved_active(membership):
            seed_gateway_node_roles(membership, seed_role, overwrite_existing=False)
        existing_roles = {
            role.node_id: role
            for role in GatewayNodeRole.query.filter_by(
                gateway_membership_id=membership.id,
                is_active=True,
            ).all()
        }

    return [
        {
            "node": node,
            "override": existing_roles.get(node.id),
            "effective_role": existing_roles.get(node.id).role
            if existing_roles.get(node.id)
            else seed_role,
            "source_label": existing_roles.get(node.id).role
            if existing_roles.get(node.id)
            else (
                "Seeded from NeoGateway approval"
                if seed_role != "watcher"
                else "Watcher fallback"
            ),
            "role_choices": _role_choices_for_node(
                existing_roles.get(node.id),
                effective_role=existing_roles.get(node.id).role
                if existing_roles.get(node.id)
                else seed_role,
            ),
        }
        for node in nodes
    ]


def _neogateway_seed_role_for_membership(user, membership):
    if not _membership_is_approved_active(membership):
        return "watcher"

    access = ensure_user_app_access(user, "neogateway")
    if access and access.status == "approved" and access.is_active and access.role in ROLE_CHOICES:
        return access.role

    return "watcher"


def _apply_node_role_form(target_user, membership):
    nodes = NeoNode.query.filter_by(is_active=True).all()
    existing_roles = {
        role.node_id: role
        for role in GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
        ).all()
    }

    for node in nodes:
        selected_role = request.form.get(f"node_{node.id}", "watcher").strip().lower()
        if selected_role not in ROLE_CHOICES:
            raise ValueError("Unsupported node role selected.")

        existing_role = existing_roles.get(node.id)
        _guard_role_assignment_allowed(selected_role, existing_role)

        if _would_remove_last_grandmaster(node, selected_role, existing_role):
            raise ValueError(
                "Cannot remove or downgrade the last active Grandmaster MotherBrain access."
            )

        if not existing_role:
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=node.id,
                    role=selected_role,
                    is_active=True,
                )
            )
            continue

        existing_role.role = selected_role
        existing_role.is_active = True


def _apply_permission_rule_form():
    rule_ids = request.form.getlist("rule_ids")
    for rule_id in rule_ids:
        try:
            rule = db.session.get(PermissionRule, int(rule_id))
        except (TypeError, ValueError):
            raise ValueError("Unsupported permission rule selected.")
        if not rule:
            continue

        minimum_role = request.form.get(f"minimum_role_{rule.id}", "").strip().lower()
        description = request.form.get(f"description_{rule.id}", "").strip()
        if minimum_role not in ROLE_CHOICES:
            raise ValueError("Unsupported minimum role selected.")

        rule.minimum_role = minimum_role
        rule.description = description


def _role_choices_for_node(existing_role, effective_role=None):
    choices = [
        role
        for role in ROLE_CHOICES
        if _current_user_can_assign_role(role)
    ]
    existing_effective_role = effective_role or (existing_role.role if existing_role else "watcher")
    if existing_effective_role not in choices:
        choices.append(existing_effective_role)

    return sorted(set(choices), key=lambda role: ROLE_LEVELS.get(role, 0))


def _guard_role_assignment_allowed(selected_role, existing_role):
    existing_effective_role = existing_role.role if existing_role else "watcher"
    if selected_role == existing_effective_role:
        return

    if not _current_user_can_assign_role(selected_role):
        raise ValueError("You cannot assign a role equal to or higher than your own role.")


def _current_user_can_assign_role(role):
    if role not in ROLE_LEVELS:
        return False

    if _is_kessler_account(current_user):
        return ROLE_LEVELS[role] <= ROLE_LEVELS["grandmaster"]

    current_role = get_user_node_role(current_user, get_current_gateway().code, "motherbrain")
    current_level = ROLE_LEVELS.get(current_role, 0)
    return ROLE_LEVELS[role] < current_level


def _is_kessler_account(user):
    identifiers = {
        (getattr(user, "username", "") or "").strip().lower(),
        (getattr(user, "email", "") or "").strip().lower(),
        (getattr(user, "employee_id", "") or "").strip().lower(),
    }
    return "kessler" in identifiers or "kessler@local.neoapps" in identifiers


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
        .filter(
            GatewayMembership.gateway_id == gateway.id,
            GatewayMembership.status == "approved",
            GatewayMembership.is_active.is_(True),
            GatewayNodeRole.node_id == motherbrain.id,
            GatewayNodeRole.role == "grandmaster",
            GatewayNodeRole.is_active.is_(True),
            User.is_active.is_(True),
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


def _approve_membership(membership, notes, seed_role="watcher"):
    if not membership:
        raise ValueError("Gateway membership request was not found.")
    if seed_role not in ROLE_CHOICES:
        raise ValueError("Unsupported node role selected.")
    if not membership.user.email_verified_at:
        raise ValueError("Email not verified yet.")

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

    if not was_approved:
        send_result = email_service.send_access_approved(membership.user, membership.gateway)
        if send_result.get("sent"):
            membership.approval_email_sent_at = datetime.utcnow()


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
