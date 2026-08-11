"""Targeted production schema ensure for SortDateMission departure statuses."""

from sqlalchemy import text

from app.extensions import db
from app.models import SortDateMission


SORT_DATE_MISSION_SCHEMA_LOCK_KEY = 7_483_327_341_903
SORT_DATE_MISSION_SCHEMA_LOCK_TIMEOUT = "5s"
DEPARTURE_STATUS_CONSTRAINT_NAME = "ck_sort_date_missions_departure_status"


def ensure_sort_date_mission_departure_status_constraint(app):
    """Ensure PostgreSQL accepts the model's scheduled departure status."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{SORT_DATE_MISSION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": SORT_DATE_MISSION_SCHEMA_LOCK_KEY},
            )
            definition = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'sort_date_missions'::regclass "
                    "AND conname = :constraint_name"
                ),
                {"constraint_name": DEPARTURE_STATUS_CONSTRAINT_NAME},
            ).scalar()
            if not definition or "'scheduled'" not in str(definition):
                connection.execute(
                    text(
                        "ALTER TABLE sort_date_missions DROP CONSTRAINT IF EXISTS "
                        f"{DEPARTURE_STATUS_CONSTRAINT_NAME}"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE sort_date_missions ADD CONSTRAINT "
                        f"{DEPARTURE_STATUS_CONSTRAINT_NAME} CHECK ("
                        f"{_model_constraint_sql()})"
                    )
                )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Departure-status constraint ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Departure-status constraint ensure completed")
    return True


def _model_constraint_sql():
    constraint = next(
        constraint
        for constraint in SortDateMission.__table__.constraints
        if constraint.name == DEPARTURE_STATUS_CONSTRAINT_NAME
    )
    return str(constraint.sqltext)


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
