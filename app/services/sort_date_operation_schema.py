"""Targeted PostgreSQL repair for nullable operation window settings."""

from sqlalchemy import text

from app.extensions import db
from app.models import SortDateOperation


SORT_DATE_OPERATION_SCHEMA_LOCK_KEY = 7_483_327_341_904
SORT_DATE_OPERATION_SCHEMA_LOCK_TIMEOUT = "5s"
WINDOW_CONSTRAINT_NAME = "ck_sort_date_operations_window_minutes_nonnegative"


def ensure_sort_date_operation_window_nullable(app):
    """Allow a blank Global window without changing existing numeric values."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{SORT_DATE_OPERATION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": SORT_DATE_OPERATION_SCHEMA_LOCK_KEY},
            )
            column_state = connection.execute(
                text(
                    "SELECT is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'sort_date_operations' "
                    "AND column_name = 'window_minutes'"
                )
            ).mappings().one_or_none()
            if not column_state:
                raise RuntimeError("sort_date_operations.window_minutes is missing.")
            if (
                str(column_state["is_nullable"]).upper() != "YES"
                or column_state["column_default"] is not None
            ):
                connection.execute(
                    text(
                        "ALTER TABLE sort_date_operations "
                        "ALTER COLUMN window_minutes DROP NOT NULL, "
                        "ALTER COLUMN window_minutes DROP DEFAULT"
                    )
                )
            definition = connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'sort_date_operations'::regclass "
                    "AND conname = :constraint_name"
                ),
                {"constraint_name": WINDOW_CONSTRAINT_NAME},
            ).scalar()
            if not definition or "IS NULL" not in str(definition).upper():
                connection.execute(
                    text(
                        "ALTER TABLE sort_date_operations DROP CONSTRAINT IF EXISTS "
                        f"{WINDOW_CONSTRAINT_NAME}"
                    )
                )
                connection.execute(
                    text(
                        "ALTER TABLE sort_date_operations ADD CONSTRAINT "
                        f"{WINDOW_CONSTRAINT_NAME} CHECK ({_model_constraint_sql()})"
                    )
                )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Operation-window schema ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Operation-window schema ensure completed")
    return True


def _model_constraint_sql():
    constraint = next(
        constraint
        for constraint in SortDateOperation.__table__.constraints
        if constraint.name == WINDOW_CONSTRAINT_NAME
    )
    return str(constraint.sqltext)


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
