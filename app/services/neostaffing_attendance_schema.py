"""Targeted additive schema repair for operation-linked staffing attendance."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingDailyAttendance


NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY = 7_483_327_341_908
NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_TIMEOUT = "5s"
NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS = (
    "sort_date_operation_id",
    "department_unit_id",
    "operation_unit_id",
)


def ensure_neostaffing_attendance_columns(app):
    """Add only nullable attendance anchor/snapshot columns in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    table_name = StaffingDailyAttendance.__tablename__
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY},
            )
            for column_name in NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                        f"{column_name} INTEGER"
                    )
                )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing attendance targeted schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing attendance targeted schema ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
