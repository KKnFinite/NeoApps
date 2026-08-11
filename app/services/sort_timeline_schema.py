"""Targeted production schema ensure for additive Sort Timeline time fields."""

from sqlalchemy import text

from app.extensions import db
from app.models import SortTimelineSortSetting


# A stable transaction-scoped lock serializes this narrow additive DDL work
# across concurrent Render workers without coordinating unrelated schema work.
SORT_TIMELINE_SCHEMA_LOCK_KEY = 7_483_327_341_902
SORT_TIMELINE_SCHEMA_LOCK_TIMEOUT = "5s"
SORT_TIMELINE_ADDITIVE_TIME_COLUMNS = (
    "planning_start_local",
    "google_polling_start_local",
    "google_polling_end_local",
)


def ensure_sort_timeline_sort_setting_columns(app):
    """Ensure only the additive Sort Timeline time columns exist at startup."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{SORT_TIMELINE_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": SORT_TIMELINE_SCHEMA_LOCK_KEY},
            )
            for column_name in SORT_TIMELINE_ADDITIVE_TIME_COLUMNS:
                connection.execute(
                    text(
                        f"ALTER TABLE {SortTimelineSortSetting.__tablename__} "
                        f"ADD COLUMN IF NOT EXISTS {column_name} TIME"
                    )
                )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "Sort Timeline time-column ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("Sort Timeline time-column ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
