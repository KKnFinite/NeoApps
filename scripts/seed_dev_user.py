import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402


DEV_USERNAME_ENV = "NEOAPPS_DEV_GRANDMASTER_USERNAME"
DEV_PASSWORD_ENV = "NEOAPPS_DEV_GRANDMASTER_PASSWORD"
ALLOW_NON_SQLITE_ENV = "NEOAPPS_ALLOW_DEV_USER_SEED"

DEFAULT_DEV_USERNAME = "Kessler"
LOCAL_SQLITE_FALLBACK_PASSWORD = "1313"


def seed_dev_grandmaster(app=None):
    app = app or create_app()

    with app.app_context():
        _validate_seed_target(app)
        username, password, used_fallback = _resolve_credentials(app)

        db.create_all()

        user = User.query.filter_by(username=username).first()
        created = user is None
        if created:
            user = User(username=username)
            db.session.add(user)

        user.role = "grandmaster"
        user.is_active = True
        user.mfa_required = False
        user.mfa_enabled = False
        user.mfa_secret = None
        user.mfa_verified_at = None
        user.set_password(password)

        db.session.commit()

        return {
            "created": created,
            "username": username,
            "used_fallback_password": used_fallback,
        }


def _resolve_credentials(app):
    username = os.getenv(DEV_USERNAME_ENV, DEFAULT_DEV_USERNAME).strip()
    if not username:
        username = DEFAULT_DEV_USERNAME

    password = os.getenv(DEV_PASSWORD_ENV)
    if password:
        return username, password, False

    if not _is_sqlite_database(app):
        raise RuntimeError(
            f"Set {DEV_PASSWORD_ENV} before seeding a non-SQLite database."
        )

    return username, LOCAL_SQLITE_FALLBACK_PASSWORD, True


def _validate_seed_target(app):
    if _is_sqlite_database(app):
        return

    if os.getenv(ALLOW_NON_SQLITE_ENV) == "1":
        return

    raise RuntimeError(
        "Refusing to seed a non-SQLite database. "
        f"Set {ALLOW_NON_SQLITE_ENV}=1 only for an intentional non-production dev target."
    )


def _is_sqlite_database(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    return database_uri.startswith("sqlite:")


def main():
    result = seed_dev_grandmaster()
    action = "created" if result["created"] else "updated"
    password_source = (
        "local SQLite fallback"
        if result["used_fallback_password"]
        else DEV_PASSWORD_ENV
    )

    print(f"Development Grandmaster user {action}.")
    print(f"Username: {result['username']}")
    print(f"Password source: {password_source}")


if __name__ == "__main__":
    main()
