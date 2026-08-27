"""Targeted additive schema ensure for 20C FT Supervisor affiliations."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingTwentyCAffiliation


NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_KEY = 7_483_327_341_920
NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neostaffing_twenty_c_affiliation_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_KEY},
            )
            StaffingTwentyCAffiliation.__table__.create(
                bind=connection, checkfirst=True
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing 20C affiliation table ensure failed: error=%s",
                type(error).__name__,
            )
            raise
    app.logger.info("NeoStaffing 20C affiliation table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
