"""Targeted PostgreSQL schema ensure for current-sort Load Planning contacts."""

from sqlalchemy import bindparam, text

from app.extensions import db
from app.models import SortDateOperation


LOAD_PLANNING_CONTACT_SCHEMA_LOCK_KEY = 7_483_327_341_907
LOAD_PLANNING_CONTACT_SCHEMA_LOCK_TIMEOUT = "5s"
LOAD_PLANNING_CONTACT_COLUMNS = (
    "load_planner_extension",
    "load_planner_radio_channel",
)


def ensure_load_planning_contact_columns(app):
    """Add only nullable per-operation Load Planning contact columns."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                missing = _missing_columns(read_connection)
            if not missing:
                app.logger.info("Load Planning contact schema ensure already current")
                return True

            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{LOAD_PLANNING_CONTACT_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": LOAD_PLANNING_CONTACT_SCHEMA_LOCK_KEY},
            )
            missing = _missing_columns(connection)
            for column_name in missing:
                connection.execute(
                    text(
                        f"ALTER TABLE {SortDateOperation.__tablename__} "
                        f"ADD COLUMN {column_name} VARCHAR(64)"
                    )
                )
            if missing:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Load Planning contact schema ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Load Planning contact schema ensure completed")
    return True


def _missing_columns(connection):
    rows = connection.execute(
        text(
            "SELECT attname FROM pg_attribute "
            "WHERE attrelid = 'sort_date_operations'::regclass "
            "AND attname IN :column_names "
            "AND attnum > 0 AND NOT attisdropped"
        ).bindparams(bindparam("column_names", expanding=True)),
        {"column_names": LOAD_PLANNING_CONTACT_COLUMNS},
    )
    existing = set(rows.scalars().all())
    return tuple(
        column_name
        for column_name in LOAD_PLANNING_CONTACT_COLUMNS
        if column_name not in existing
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
