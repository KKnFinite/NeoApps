from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import GatewayMembership, User
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
    get_valid_token_record,
    mark_token_used,
)


GENERIC_RESET_RESPONSE = "If an account exists for that email, a reset link has been sent."


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = _find_user_by_login(username) if username else None

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("neomotherbrain/dashboard.html"), 401

        if not user.is_active:
            flash("This account is inactive.", "error")
            return render_template("neomotherbrain/dashboard.html"), 403

        login_user(user)
        user.last_login = datetime.utcnow()

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Login failed. Please try again.", "error")
            return render_template("neomotherbrain/dashboard.html"), 500

        if user.password_reset_required:
            return redirect(url_for("auth.change_password"))

        gateway = get_current_gateway()
        if not user_has_gateway_access(user, gateway.code):
            return redirect(url_for("auth.access_pending"))

        return redirect(url_for("neomotherbrain.motherbrain"))

    return render_template("neomotherbrain/dashboard.html")


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
    token_record = get_valid_token_record(token, EMAIL_VERIFICATION)
    if not token_record:
        return render_template("auth/verify_email.html", verified=False), 400

    token_record.user.email_verified_at = token_record.user.email_verified_at or datetime.utcnow()
    mark_token_used(token_record)
    db.session.commit()

    return render_template("auth/verify_email.html", verified=True)


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
        return redirect(url_for("neomotherbrain.motherbrain"))

    return render_template("auth/change_password.html")


@bp.route("/admin/users")
@gateway_node_required("motherbrain", minimum_role="master")
def users():
    users = User.query.order_by(User.username.asc()).all()
    return render_template("auth/users.html", users=users)


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
    if not membership.user.email_verified_at:
        flash("Email not verified yet", "error")
        return redirect(url_for("auth.access_requests"))

    membership.status = "approved"
    membership.is_active = True
    membership.approved_by_user_id = current_user.id
    membership.approved_at = datetime.utcnow()
    membership.approval_notes = request.form.get("approval_notes", "").strip() or None
    membership.denied_by_user_id = None
    membership.denied_at = None
    membership.denial_notes = None
    db.session.commit()

    send_result = email_service.send_access_approved(membership.user, membership.gateway)
    if send_result.get("sent"):
        membership.approval_email_sent_at = datetime.utcnow()
        db.session.commit()

    flash("Access request approved.", "info")
    return redirect(url_for("auth.access_requests"))


@bp.route("/admin/access-requests/<int:membership_id>/deny", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="master")
def deny_access_request(membership_id):
    membership = _membership_or_404(membership_id)
    membership.status = "denied"
    membership.is_active = True
    membership.denied_by_user_id = current_user.id
    membership.denied_at = datetime.utcnow()
    membership.denial_notes = request.form.get("denial_notes", "").strip() or None
    membership.approved_by_user_id = None
    membership.approved_at = None
    membership.approval_notes = None
    membership.approval_email_sent_at = None
    db.session.commit()

    flash("Access request updated.", "info")
    return redirect(url_for("auth.access_requests"))


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
        return redirect(url_for("auth.users"))

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
