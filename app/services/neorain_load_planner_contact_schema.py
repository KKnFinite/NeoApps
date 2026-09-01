"""Targeted production schema ensure for NeoRain planner contacts."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoRainLoadPlannerContact


NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_KEY = 7_483_327_341_926
NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neorain_load_planner_contact_table(app):
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_KEY},
            )
            NeoRainLoadPlannerContact.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.error(
                "NeoRain Load Planner contact schema ensure failed safely: error=%s",
                type(exc).__name__,
            )
            raise
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
