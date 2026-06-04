from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User
from app.models.user import ROLE_LEVELS
from app.services import email_service
from app.services.access_control import (
    get_current_gateway,
    request_default_gateway_access_for_user,
    user_has_gateway_access,
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


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = _find_user_by_login(username) if username else None

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
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

        if user.password_reset_required:
            return redirect(url_for("auth.change_password"))

        gateway = get_current_gateway()
        if not user_has_gateway_access(user, gateway.code):
            return redirect(url_for("auth.access_pending"))

        return redirect(url_for("neomotherbrain.rfd_hub"))

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/create-account", methods=["GET", "POST"])
def create_account():
    form = _account_form_from_request()

    if request.method == "POST":
        try:
            user = _build_user_from_account_form(form)
            request_default_gateway_access_for_user(user)
            raw_token, _token_record = create_user_token(user, EMAIL_VERIFICATION)
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return render_template("auth/create_account.html", form=form), 400
        except SQLAlchemyError:
            db.session.rollback()
            flash("Account creation failed. Please try again.", "error")
            return render_template("auth/create_account.html", form=form), 500

        email_service.send_email_verification(user, raw_token)
        return render_template("auth/account_created.html", user=user)

    return render_template("auth/create_account.html", form=form)


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
            _validate_password_pair(password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template("auth/reset_password.html", token=token, valid=True), 400

        token_record.user.set_password(password)
        token_record.user.password_changed_at = datetime.utcnow()
        token_record.user.password_reset_required = False
        mark_token_used(token_record)
        db.session.commit()
        flash("Password reset complete. You can log in now.", "info")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token, valid=True)


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        try:
            _validate_password_pair(password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template("auth/change_password.html"), 400

        current_user.set_password(password)
        current_user.password_changed_at = datetime.utcnow()
        current_user.password_reset_required = False
        db.session.commit()
        flash("Password changed.", "info")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    return render_template("auth/change_password.html")


@bp.route("/admin/users")
@gateway_node_required("motherbrain", minimum_role="grandmaster")
def users():
    gateway = get_current_gateway()
    users = User.query.order_by(User.full_name.asc(), User.username.asc()).all()
    memberships_by_user_id = _gateway_memberships_by_user_id(gateway)
    node_roles_by_user_id = _node_roles_by_user_id(gateway)
    rows = [
        _user_management_row(user, memberships_by_user_id, node_roles_by_user_id)
        for user in users
    ]
    summary = _user_management_summary(gateway)
    return render_template(
        "auth/users.html",
        gateway=gateway,
        rows=rows,
        summary=summary,
    )


@bp.route("/admin/users/pending")
@gateway_node_required("motherbrain", minimum_role="grandmaster")
def pending_users():
    gateway = get_current_gateway()
    memberships = _pending_memberships_for_gateway(gateway).all()
    return render_template(
        "auth/pending_users.html",
        gateway=gateway,
        memberships=memberships,
    )


@bp.route("/admin/users/<int:user_id>")
@gateway_node_required("motherbrain", minimum_role="grandmaster")
def user_detail(user_id):
    target_user = User.query.get_or_404(user_id)
    gateway = get_current_gateway()
    membership = _current_gateway_membership_for_user(target_user, gateway)
    node_rows = _node_role_rows(target_user, membership)
    return render_template(
        "auth/user_detail.html",
        gateway=gateway,
        membership=membership,
        node_rows=node_rows,
        target_user=target_user,
    )


@bp.route("/admin/users/<int:user_id>/roles", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="grandmaster")
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
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    return render_template(
        "auth/user_roles.html",
        gateway=gateway,
        membership=membership,
        node_rows=_node_role_rows(target_user, membership),
        role_choices=ROLE_CHOICES,
        target_user=target_user,
    )


@bp.route("/admin/users/<int:user_id>/gateway-membership", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="grandmaster")
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
@gateway_node_required("motherbrain", minimum_role="master")
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
    )


@bp.route("/admin/access-requests/<int:membership_id>/approve", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="master")
def approve_access_request(membership_id):
    membership = _membership_or_404(membership_id)
    try:
        _approve_membership(
            membership,
            request.form.get("approval_notes", "").strip() or None,
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("auth.access_requests"))

    db.session.commit()
    flash("Access request approved.", "info")
    return redirect(url_for("auth.access_requests"))


@bp.route("/admin/access-requests/<int:membership_id>/deny", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="master")
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
@gateway_node_required("motherbrain", minimum_role="grandmaster")
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
            _validate_password_pair(password, confirm_password)
        except ValueError as error:
            flash(str(error), "error")
            return render_template("auth/emergency_reset.html", target_user=target_user), 400

        target_user.set_password(password)
        target_user.password_reset_required = True
        target_user.last_password_reset_by_user_id = current_user.id
        target_user.last_password_reset_at = datetime.utcnow()
        target_user.last_password_reset_reason = reason
        db.session.commit()
        flash("Temporary password set. User must change it on next login.", "info")
        return redirect(url_for("auth.user_detail", user_id=target_user.id))

    return render_template("auth/emergency_reset.html", target_user=target_user)


def _account_form_from_request():
    return {
        "full_name": request.form.get("full_name", ""),
        "employee_id": request.form.get("employee_id", ""),
        "supervisor_name": request.form.get("supervisor_name", ""),
        "email": request.form.get("email", ""),
        "work_area": request.form.get("work_area", ""),
        "access_reason": request.form.get("access_reason", ""),
        "username": request.form.get("username", ""),
    }


def _build_user_from_account_form(form):
    email = _normalize_email(form["email"])
    username = (form["username"].strip() or email).strip()
    employee_id = form["employee_id"].strip()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    required_values = {
        "Full name": form["full_name"].strip(),
        "Employee ID": employee_id,
        "Supervisor / manager name": form["supervisor_name"].strip(),
        "Email": email,
        "Work area": form["work_area"].strip(),
        "Reason for access": form["access_reason"].strip(),
        "Username": username,
    }
    missing = [label for label, value in required_values.items() if not value]
    if missing:
        raise ValueError(f"{', '.join(missing)} required.")

    _validate_password_pair(password, confirm_password)
    _raise_for_duplicate_identity(username, email, employee_id)

    user = User(
        username=username,
        email=email,
        full_name=form["full_name"].strip(),
        employee_id=employee_id,
        supervisor_name=form["supervisor_name"].strip(),
        work_area=form["work_area"].strip(),
        access_reason=form["access_reason"].strip(),
        role="watcher",
        is_active=True,
        password_changed_at=datetime.utcnow(),
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def _raise_for_duplicate_identity(username, email, employee_id):
    if User.query.filter(func.lower(User.username) == username.lower()).first():
        raise ValueError("That username is already in use.")

    if User.query.filter(func.lower(User.email) == email.lower()).first():
        raise ValueError("That email is already in use.")

    if User.query.filter(func.lower(User.employee_id) == employee_id.lower()).first():
        raise ValueError("That employee ID is already in use.")


def _find_user_by_login(login_value):
    normalized = login_value.strip().lower()
    return User.query.filter(
        (func.lower(User.username) == normalized)
        | (func.lower(User.email) == normalized)
    ).first()


def _find_user_by_email(email):
    return User.query.filter(func.lower(User.email) == email.lower()).first()


def _normalize_email(value):
    return (value or "").strip().lower()


def _validate_password_pair(password, confirm_password):
    if password != confirm_password:
        raise ValueError("Passwords do not match.")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")


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
        return "watcher fallback"

    return ", ".join(f"{role.node.name}: {role.role}" for role in node_roles)


def _highest_node_role(node_roles):
    if not node_roles:
        return "watcher"

    return max(node_roles, key=lambda role: ROLE_LEVELS.get(role.role, 0)).role


def _node_role_rows(user, membership):
    nodes = NeoNode.query.filter_by(is_active=True).order_by(
        NeoNode.sort_order.asc(),
        NeoNode.name.asc(),
    ).all()
    existing_roles = {}
    if membership:
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
            else "watcher",
        }
        for node in nodes
    ]


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
        if _would_remove_last_self_grandmaster(target_user, node, selected_role, existing_role):
            raise ValueError(
                "Cannot remove or downgrade the last active Grandmaster MotherBrain access."
            )

        if selected_role == "watcher":
            if existing_role:
                db.session.delete(existing_role)
            continue

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


def _would_remove_last_self_grandmaster(target_user, node, selected_role, existing_role):
    if target_user.id != current_user.id:
        return False
    if node.code != "motherbrain":
        return False
    if selected_role == "grandmaster":
        return False
    if not existing_role or existing_role.role != "grandmaster" or not existing_role.is_active:
        return False
    return _active_motherbrain_grandmaster_count() <= 1


def _guard_last_grandmaster_gateway_change(target_user):
    if target_user.id != current_user.id:
        return
    if _active_motherbrain_grandmaster_count() <= 1:
        raise ValueError("Cannot remove the last active Grandmaster gateway access.")


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


def _approve_membership(membership, notes):
    if not membership:
        raise ValueError("Gateway membership request was not found.")
    if not membership.user.email_verified_at:
        raise ValueError("Email not verified yet.")

    membership.status = "approved"
    membership.is_active = True
    membership.approved_by_user_id = current_user.id
    membership.approved_at = datetime.utcnow()
    membership.approval_notes = notes
    membership.denied_by_user_id = None
    membership.denied_at = None
    membership.denial_notes = None
    db.session.flush()

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
