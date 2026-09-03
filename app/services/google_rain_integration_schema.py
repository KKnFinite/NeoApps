"""Targeted production schema ensure for NeoRain integration authority."""

from sqlalchemy import text

from app.extensions import db
from app.models import MotherBrainGoogleIntegrationSetting


RAIN_INTEGRATION_MODE_COLUMN = "rain_integration_mode"
RAIN_FUEL_DATA_SOURCE_COLUMN = "rain_fuel_data_source"
RAIN_INTEGRATION_SCHEMA_LOCK_KEY = 7_483_327_341_905
RAIN_INTEGRATION_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_google_rain_integration_mode_column(app):
    """Ensure the two small Rain authority columns with one bounded lock."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                columns_current = all(_column_exists(read_connection, column) for column in _required_columns())
            if columns_current:
                app.logger.info("NeoRain integration schema ensure already current")
                return True

            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{RAIN_INTEGRATION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": RAIN_INTEGRATION_SCHEMA_LOCK_KEY},
            )
            changed = False
            if not _column_exists(connection, RAIN_INTEGRATION_MODE_COLUMN):
                connection.execute(text("ALTER TABLE " f"{MotherBrainGoogleIntegrationSetting.__tablename__} " f"ADD COLUMN {RAIN_INTEGRATION_MODE_COLUMN} VARCHAR(40) NOT NULL DEFAULT 'google_primary'"))
                changed = True
            if not _column_exists(connection, RAIN_FUEL_DATA_SOURCE_COLUMN):
                connection.execute(text("ALTER TABLE " f"{MotherBrainGoogleIntegrationSetting.__tablename__} " f"ADD COLUMN {RAIN_FUEL_DATA_SOURCE_COLUMN} VARCHAR(16) NOT NULL DEFAULT 'google'"))
                changed = True
            if changed:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoRain integration schema ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoRain integration schema ensure completed")
    return True


def _required_columns():
    return (RAIN_INTEGRATION_MODE_COLUMN, RAIN_FUEL_DATA_SOURCE_COLUMN)


def _column_exists(connection, column_name):
    return bool(
        connection.execute(
            text(
                "SELECT 1 FROM pg_attribute "
                "WHERE attrelid = "
                "'motherbrain_google_integration_settings'::regclass "
                "AND attname = :column_name "
                "AND attnum > 0 AND NOT attisdropped"
            ),
            {"column_name": column_name},
        ).scalar()
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
