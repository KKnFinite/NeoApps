"""Targeted additive schema ensure for NeoStaffing Staffing Groups."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingGroup, StaffingGroupMembership


NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_KEY = 7_483_327_341_911
NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neostaffing_staffing_group_tables(app):
    """Create only the two new Staffing Group tables in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_KEY},
            )
            for model in (StaffingGroup, StaffingGroupMembership):
                model.__table__.create(bind=connection, checkfirst=True)
            connection.execute(
                text(
                    """
                    INSERT INTO permission_rules (
                        permission_key,
                        minimum_role,
                        description,
                        created_at,
                        updated_at
                    )
                    VALUES
                        (
                            'neostaffing.staffing_groups.view',
                            'operator',
                            'View NeoStaffing Staffing Groups and current Daily Staffing totals.',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        ),
                        (
                            'neostaffing.staffing_groups.edit',
                            'master',
                            'Create, rename, activate, deactivate, and change NeoStaffing Staffing Groups.',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        )
                    ON CONFLICT (permission_key) DO NOTHING
                    """
                )
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing Staffing Group table ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing Staffing Group table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
