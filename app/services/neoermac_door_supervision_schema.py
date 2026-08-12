"""Targeted production schema ensure for Door View supervision preferences."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoErmacDoorSupervision


NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_KEY = 7_483_327_341_908
NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neoermac_door_supervision_table(app):
    """Ensure only the PostgreSQL-backed door-supervision table exists."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_KEY},
            )
            NeoErmacDoorSupervision.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoErmac door-supervision table ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoErmac door-supervision table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
