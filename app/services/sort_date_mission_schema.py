"""Targeted production schema ensure for active SortDateMission additions."""

import re

from sqlalchemy import bindparam, text

from app.extensions import db
from app.models import SortDateMission


SORT_DATE_MISSION_SCHEMA_LOCK_KEY = 7_483_327_341_903
SORT_DATE_MISSION_SCHEMA_LOCK_TIMEOUT = "5s"
DEPARTURE_STATUS_CONSTRAINT_NAME = "ck_sort_date_missions_departure_status"
GOOGLE_RAIN_MILESTONE_COLUMNS = {
    "elmac_completed_at_utc": "TIMESTAMP",
    "elmac_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
    "ramp_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
    "crew_load_completed_source": "VARCHAR(32) NOT NULL DEFAULT 'unknown'",
}


def ensure_sort_date_mission_departure_status_constraint(app):
    """Ensure only the active departure constraint and additive Rain columns."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                missing_columns, constraint_is_current = _schema_state(
                    read_connection
                )
            if not missing_columns and constraint_is_current:
                app.logger.info(
                    "SortDateMission targeted schema ensure already current"
                )
                return True

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
            missing_columns, constraint_is_current = _schema_state(connection)
            for column_name in missing_columns:
                column_sql = GOOGLE_RAIN_MILESTONE_COLUMNS[column_name]
                connection.execute(
                    text(
                        f"ALTER TABLE {SortDateMission.__tablename__} "
                        f"ADD COLUMN {column_name} {column_sql}"
                    )
                )
            if not constraint_is_current:
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
            if missing_columns or not constraint_is_current:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "SortDateMission targeted schema ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("SortDateMission targeted schema ensure completed")
    return True


def _schema_state(connection):
    column_result = connection.execute(
        text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'sort_date_missions'::regclass "
            "AND attnum > 0 AND NOT attisdropped "
            "AND attname IN :column_names"
        ).bindparams(bindparam("column_names", expanding=True)),
        {"column_names": tuple(GOOGLE_RAIN_MILESTONE_COLUMNS)},
    )
    existing_columns = set(column_result.scalars().all())
    missing_columns = tuple(
        column_name
        for column_name in GOOGLE_RAIN_MILESTONE_COLUMNS
        if column_name not in existing_columns
    )
    definition = connection.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'sort_date_missions'::regclass "
            "AND conname = :constraint_name"
        ),
        {"constraint_name": DEPARTURE_STATUS_CONSTRAINT_NAME},
    ).scalar()
    expected_values = re.findall(r"'([^']+)'", _model_constraint_sql())
    constraint_is_current = bool(
        definition
        and all(f"'{value}'" in str(definition) for value in expected_values)
    )
    return missing_columns, constraint_is_current


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
