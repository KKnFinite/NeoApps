"""Targeted production schema ensure for NeoStaffing reporting relationships."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingReportingRelationship


NEOSTAFFING_REPORTING_SCHEMA_LOCK_KEY = 7_483_327_341_909
NEOSTAFFING_REPORTING_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neostaffing_reporting_relationship_table(app):
    """Create only the additive reporting-relationship table in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_REPORTING_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_REPORTING_SCHEMA_LOCK_KEY},
            )
            StaffingReportingRelationship.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing reporting relationship table ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing reporting relationship table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
