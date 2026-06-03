import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User  # noqa: E402
from app.services.access_control import (  # noqa: E402
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.schema_sync import sync_local_sqlite_schema  # noqa: E402


BOOTSTRAP_USERNAME_ENV = "BOOTSTRAP_ADMIN_USERNAME"
BOOTSTRAP_EMAIL_ENV = "BOOTSTRAP_ADMIN_EMAIL"
BOOTSTRAP_PASSWORD_ENV = "BOOTSTRAP_ADMIN_PASSWORD"

DEFAULT_BOOTSTRAP_USERNAME = "Kessler"
DEFAULT_BOOTSTRAP_EMAIL = "bootstrap-admin@local.neoapps"
LOCAL_SQLITE_FALLBACK_PASSWORD = "1313"


def bootstrap_database(app=None):
    app = app or create_app()

    with app.app_context():
        username, email, password, used_fallback = _resolve_bootstrap_credentials(app)

        db.create_all()
        sync_local_sqlite_schema(app)
        ensure_default_gateway_and_nodes()

        user = _find_or_create_bootstrap_user(username, email)
        user.username = username
        user.email = email
        user.full_name = user.full_name or username
        user.employee_id = user.employee_id or "BOOTSTRAP"
        user.supervisor_name = user.supervisor_name or "System Bootstrap"
        user.work_area = user.work_area or "NeoGateway"
        user.access_reason = user.access_reason or "Initial NeoGateway Grandmaster bootstrap."
        user.role = "grandmaster"
        user.is_active = True
        user.email_verified_at = user.email_verified_at or datetime.utcnow()
        user.password_reset_required = False
        user.password_changed_at = user.password_changed_at or datetime.utcnow()
        user.mfa_required = False
        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_verified_at = None
        user.set_password(password)
        db.session.flush()

        membership = backfill_default_gateway_node_roles(user, role="grandmaster")

        db.session.commit()

        return {
            "username": user.username,
            "email": user.email,
            "gateway_code": membership.gateway.code,
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

    if not user:
        user = User(username=username)
        db.session.add(user)

    return user


def _is_sqlite_database(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    return database_uri.startswith("sqlite:")


def main():
    result = bootstrap_database()
    password_source = (
        "local SQLite fallback"
        if result["used_fallback_password"]
        else BOOTSTRAP_PASSWORD_ENV
    )
    print("NeoGateway database bootstrap complete.")
    print(f"Username: {result['username']}")
    print(f"Email: {result['email']}")
    print(f"Gateway: {result['gateway_code']}")
    print(f"Active NeoNodes: {result['node_count']}")
    print(f"Grandmaster node roles: {result['grandmaster_role_count']}")
    print(f"Password source: {password_source}")


if __name__ == "__main__":
    main()
