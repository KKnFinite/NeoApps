"""Targeted production schema ensure for per-screen live refresh settings."""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import LiveScreenRefreshSetting


LIVE_SCREEN_REFRESH_SCHEMA_LOCK_KEY = 7_483_327_341_913
LIVE_SCREEN_REFRESH_SCHEMA_LOCK_TIMEOUT = "5s"
LIVE_SCREEN_REFRESH_EDIT_PERMISSION = "neoscorpion.refresh_settings.edit"


def ensure_live_screen_refresh_setting_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{LIVE_SCREEN_REFRESH_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": LIVE_SCREEN_REFRESH_SCHEMA_LOCK_KEY},
            )
            LiveScreenRefreshSetting.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO permission_rules (
                        permission_key,
                        minimum_role,
                        description,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        'neoscorpion.refresh_settings.edit',
                        'grandmaster',
                        'Edit NeoScorpion live-screen refresh intervals.',
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (permission_key) DO NOTHING
                    """
                )
            )
            _verify_live_screen_refresh_schema(connection)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Live-screen refresh setting schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Live-screen refresh setting schema ensure completed")
    return True


def _verify_live_screen_refresh_schema(connection):
    schema_inspector = inspect(connection)
    table_name = LiveScreenRefreshSetting.__table__.name
    if table_name not in set(schema_inspector.get_table_names()):
        raise RuntimeError("Live-screen refresh setting table is missing.")
    expected_columns = set(LiveScreenRefreshSetting.__table__.columns.keys())
    actual_columns = {
        column["name"] for column in schema_inspector.get_columns(table_name)
    }
    missing_columns = sorted(expected_columns - actual_columns)
    if missing_columns:
        raise RuntimeError(
            "Live-screen refresh setting columns are missing: "
            + ", ".join(missing_columns)
        )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
