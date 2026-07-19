import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.database_bootstrap import (  # noqa: E402
    BOOTSTRAP_PASSWORD_ENV,
    bootstrap_database,
)


def deployment_bootstrap_config():
    """Return a bootstrap-only config with bounded PostgreSQL wait times."""
    from app.config import Config

    database_url = os.getenv("DATABASE_URL", "")
    engine_options = {}
    if database_url.startswith(("postgresql:", "postgres:")):
        connect_timeout = _positive_int_env(
            "DATABASE_BOOTSTRAP_CONNECT_TIMEOUT_SECONDS",
            5,
        )
        lock_timeout = _positive_int_env(
            "DATABASE_BOOTSTRAP_LOCK_TIMEOUT_MILLISECONDS",
            5000,
        )
        statement_timeout = _positive_int_env(
            "DATABASE_BOOTSTRAP_STATEMENT_TIMEOUT_MILLISECONDS",
            15000,
        )
        engine_options = {
            "pool_pre_ping": True,
            "pool_timeout": connect_timeout,
            "connect_args": {
                "connect_timeout": connect_timeout,
                "options": (
                    f"-c lock_timeout={lock_timeout}ms "
                    f"-c statement_timeout={statement_timeout}ms"
                ),
            },
        }

    return type(
        "DeploymentBootstrapConfig",
        (Config,),
        {
            "SQLALCHEMY_ENGINE_OPTIONS": engine_options,
            "DATABASE_STARTUP_RETRY_ATTEMPTS": _positive_int_env(
                "DATABASE_BOOTSTRAP_RETRY_ATTEMPTS",
                4,
            ),
            "DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS": _positive_float_env(
                "DATABASE_BOOTSTRAP_RETRY_INITIAL_DELAY_SECONDS",
                1.0,
            ),
            "DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS": _positive_float_env(
                "DATABASE_BOOTSTRAP_RETRY_MAX_DELAY_SECONDS",
                4.0,
            ),
        },
    )


def create_deployment_bootstrap_app():
    from app import create_app

    return create_app(deployment_bootstrap_config(), auto_bootstrap=False)


def main():
    app = None
    print("NeoApps bootstrap phase 1/3: creating deployment database client.", flush=True)
    try:
        app = create_deployment_bootstrap_app()
        print("NeoApps bootstrap phase 2/3: synchronizing schema and seed data.", flush=True)
        result = bootstrap_database(app)
    except Exception:
        print(
            "NeoApps bootstrap failed during schema synchronization; deployment data "
            "was not marked ready.",
            file=sys.stderr,
            flush=True,
        )
        raise
    finally:
        if app is not None:
            _dispose_bootstrap_engine(app)

    password_source = (
        "local SQLite fallback"
        if result["used_fallback_password"]
        else BOOTSTRAP_PASSWORD_ENV
    )
    password_status = (
        password_source
        if result["password_applied"]
        else "existing password preserved"
    )
    print("NeoApps bootstrap phase 3/3: schema and seed data ready.", flush=True)
    print("NeoGateway database bootstrap complete.")
    print(f"Username: {result['username']}")
    print(f"Email: {result['email']}")
    print(f"Gateway: {result['gateway_code']}")
    print(f"Active NeoNodes: {result['node_count']}")
    print(f"Grandmaster node roles: {result['grandmaster_role_count']}")
    print(f"Password source: {password_status}")


def _dispose_bootstrap_engine(app):
    from app.extensions import db

    with app.app_context():
        try:
            db.session.remove()
        except Exception:
            # Cleanup must never mask the schema/bootstrap failure that triggered it.
            pass
        try:
            db.engine.dispose()
        except Exception:
            pass


def _positive_int_env(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _positive_float_env(name, default):
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
