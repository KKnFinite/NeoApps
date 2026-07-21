import os
from datetime import datetime

from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password
from app.services.schema_sync import sync_database_schema
from app.services.database_startup_retry import run_startup_database_action
from app.services.neosektor_sheets_compat import ensure_sheets_compatibility_setting


BOOTSTRAP_USERNAME_ENV = "BOOTSTRAP_ADMIN_USERNAME"
BOOTSTRAP_EMAIL_ENV = "BOOTSTRAP_ADMIN_EMAIL"
BOOTSTRAP_PASSWORD_ENV = "BOOTSTRAP_ADMIN_PASSWORD"

DEFAULT_BOOTSTRAP_USERNAME = "Kessler"
DEFAULT_BOOTSTRAP_EMAIL = "bootstrap-admin@local.neoapps"
LOCAL_SQLITE_FALLBACK_PASSWORD = "LocalDevPassphrase2026!"


def bootstrap_database(app=None):
    if app is None:
        from app import create_app

        app = create_app(auto_bootstrap=False)

    with app.app_context():
        username, email, password, used_fallback = _resolve_bootstrap_credentials(app)
        return run_startup_database_action(
            app,
            lambda: _bootstrap_database_once(
                app,
                username,
                email,
                password,
                used_fallback,
            ),
            action_name="database bootstrap and schema synchronization",
        )


def _bootstrap_database_once(app, username, email, password, used_fallback):
    db.create_all()
    sync_database_schema(app)
    gateway = ensure_default_gateway_and_nodes()
    ensure_default_permission_rules()
    ensure_sheets_compatibility_setting(gateway)

    user, created_user = _find_or_create_bootstrap_user(username, email)
    user.username = username
    user.email = email
    user.first_name = user.first_name or username
    user.last_name = user.last_name or ""
    user.full_name = user.full_name or username
    user.employee_id = user.employee_id or "BOOTSTRAP"
    user.supervisor_name = user.supervisor_name or "System Bootstrap"
    user.work_area = user.work_area or "NeoGateway"
    user.access_reason = user.access_reason or "Initial NeoGateway Grandmaster bootstrap."
    user.role = "grandmaster"
    user.is_active = True
    user.email_verified_at = user.email_verified_at or datetime.utcnow()
    user.password_reset_required = False
    user.temporary_password_expires_at = None
    user.password_changed_at = user.password_changed_at or datetime.utcnow()
    user.mfa_required = False
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_verified_at = None

    password_applied = created_user or not user.password_hash
    if password_applied:
        set_user_password(user, password)

    db.session.flush()

    membership = backfill_default_gateway_node_roles(user, role="grandmaster")

    db.session.commit()

    return {
        "username": user.username,
        "email": user.email,
        "gateway_code": membership.gateway.code,
        "created_user": created_user,
        "password_applied": password_applied,
        "used_fallback_password": used_fallback,
        "node_count": NeoNode.query.filter_by(is_active=True).count(),
        "membership_count": GatewayMembership.query.filter_by(user_id=user.id).count(),
        "grandmaster_role_count": GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            role="grandmaster",
            is_active=True,
        ).count(),
    }


def _resolve_bootstrap_credentials(app):
    username = os.getenv(BOOTSTRAP_USERNAME_ENV, DEFAULT_BOOTSTRAP_USERNAME).strip()
    email = os.getenv(BOOTSTRAP_EMAIL_ENV, DEFAULT_BOOTSTRAP_EMAIL).strip().lower()
    password = os.getenv(BOOTSTRAP_PASSWORD_ENV)

    if not username:
        username = DEFAULT_BOOTSTRAP_USERNAME
    if not email:
        email = DEFAULT_BOOTSTRAP_EMAIL

    if password:
        return username, email, password, False

    if _is_sqlite_database(app):
        return username, email, LOCAL_SQLITE_FALLBACK_PASSWORD, True

    raise RuntimeError(f"Set {BOOTSTRAP_PASSWORD_ENV} before bootstrapping a non-SQLite database.")


def _find_or_create_bootstrap_user(username, email):
    with db.session.no_autoflush:
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User.query.filter_by(email=email).first()

        existing_email_user = User.query.filter(
            User.email == email,
            User.id != getattr(user, "id", None),
        ).first()

    if existing_email_user:
        raise RuntimeError("Bootstrap email belongs to a different user.")

    if user:
        return user, False

    user = User(username=username)
    db.session.add(user)
    return user, True


def _is_sqlite_database(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    return database_uri.startswith("sqlite:")
