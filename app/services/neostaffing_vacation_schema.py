"""Targeted additive schema ensure for NeoStaffing Vacation Selection."""

from sqlalchemy import text

from app.extensions import db
from app.models import (
    StaffingVacationManagementCapacity,
    StaffingVacationManagementSelection,
    StaffingVacationManagementTurnResolution,
    StaffingVacationManagementTurnState,
    StaffingVacationManagementWeekOverride,
    StaffingVacationUnionCalendar,
    StaffingVacationUnionCalendarScope,
)


NEOSTAFFING_VACATION_SCHEMA_LOCK_KEY = 7_483_327_341_916
NEOSTAFFING_VACATION_SCHEMA_LOCK_TIMEOUT = "5s"
NEOSTAFFING_VACATION_MODELS = (
    StaffingVacationUnionCalendar,
    StaffingVacationUnionCalendarScope,
    StaffingVacationManagementCapacity,
    StaffingVacationManagementWeekOverride,
    StaffingVacationManagementSelection,
    StaffingVacationManagementTurnState,
    StaffingVacationManagementTurnResolution,
)


def ensure_neostaffing_vacation_tables(app):
    """Create only additive Vacation foundation tables in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_VACATION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_VACATION_SCHEMA_LOCK_KEY},
            )
            for model in NEOSTAFFING_VACATION_MODELS:
                model.__table__.create(bind=connection, checkfirst=True)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing Vacation schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing Vacation schema ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
