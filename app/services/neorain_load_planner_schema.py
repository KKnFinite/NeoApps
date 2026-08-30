"""Targeted PostgreSQL schema ensure for NeoRain Load Planner assignments."""

from sqlalchemy import bindparam, text

from app.extensions import db


NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_KEY = 7_483_327_341_906
NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_TIMEOUT = "5s"
NEORAIN_LOAD_PLANNER_COLUMNS = {
    "master_flight_schedules": "load_planner_person_id",
    "sort_date_missions": "load_planner_person_id",
}


def ensure_neorain_load_planner_columns(app):
    """Add only the two nullable canonical StaffingPerson references."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            with db.engine.connect() as read_connection:
                missing = _missing_columns(read_connection)
            if not missing:
                app.logger.info("NeoRain Load Planner schema ensure already current")
                return True

            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_KEY},
            )
            missing = _missing_columns(connection)
            for table_name, column_name in missing:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} "
                        "INTEGER REFERENCES staffing_people(id)"
                    )
                )
            if missing:
                db.session.commit()
            else:
                db.session.rollback()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoRain Load Planner schema ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoRain Load Planner schema ensure completed")
    return True


def _missing_columns(connection):
    table_names = tuple(NEORAIN_LOAD_PLANNER_COLUMNS)
    rows = connection.execute(
        text(
            "SELECT cls.relname AS table_name, att.attname AS column_name "
            "FROM pg_attribute att "
            "JOIN pg_class cls ON cls.oid = att.attrelid "
            "WHERE cls.relname IN :table_names "
            "AND att.attname = :column_name "
            "AND att.attnum > 0 AND NOT att.attisdropped"
        ).bindparams(bindparam("table_names", expanding=True)),
        {
            "table_names": table_names,
            "column_name": "load_planner_person_id",
        },
    ).mappings().all()
    existing = {(row["table_name"], row["column_name"]) for row in rows}
    return tuple(
        (table_name, column_name)
        for table_name, column_name in NEORAIN_LOAD_PLANNER_COLUMNS.items()
        if (table_name, column_name) not in existing
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
