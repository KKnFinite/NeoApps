"""Targeted additive schema repair for operation-linked staffing attendance."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingDailyAttendance
from app.models.staffing_daily_attendance import STAFFING_DAILY_ATTENDANCE_STATUSES


NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY = 7_483_327_341_908
NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_TIMEOUT = "5s"
NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS = (
    "sort_date_operation_id",
    "department_unit_id",
    "operation_unit_id",
)
NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT = "ck_staffing_daily_attendance_status"
NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT = (
    "ck_staffing_daily_attendance_status_phase1"
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
            _ensure_attendance_status_constraint(connection, table_name)
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


def _ensure_attendance_status_constraint(connection, table_name):
    definition = connection.execute(
        text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conname = :constraint_name
              AND conrelid = CAST(:table_name AS regclass)
            """
        ),
        {
            "constraint_name": NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT,
            "table_name": table_name,
        },
    ).scalar()
    normalized_definition = str(definition or "").casefold()
    if all(
        f"'{status}'" in normalized_definition
        for status in STAFFING_DAILY_ATTENDANCE_STATUSES
    ):
        return

    allowed_values = ", ".join(
        f"'{status}'" for status in STAFFING_DAILY_ATTENDANCE_STATUSES
    )
    # PostgreSQL DDL is transactional. The already-valid narrow constraint stays
    # in force while the widened constraint is added and validated; the swap to
    # the canonical name is committed atomically with the surrounding ensure.
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} ADD CONSTRAINT "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT} "
            f"CHECK (status IN ({allowed_values})) NOT VALID"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} RENAME CONSTRAINT "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT} TO "
            f"{NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT}"
        )
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
