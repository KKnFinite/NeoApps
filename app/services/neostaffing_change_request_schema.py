"""Targeted additive schema ensure for NeoStaffing change requests."""

from sqlalchemy import text

from app.extensions import db
from app.models import (
    StaffingChangeRequest,
    StaffingChangeRequestEvent,
    StaffingChangeRequestItem,
)


NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_KEY = 7_483_327_341_910
NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neostaffing_change_request_tables(app):
    """Create only the three new change-request tables in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_KEY},
            )
            for model in (
                StaffingChangeRequest,
                StaffingChangeRequestItem,
                StaffingChangeRequestEvent,
            ):
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
                            'neostaffing.change_requests.view',
                            'watcher',
                            'View NeoStaffing employee change requests and history.',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        ),
                        (
                            'neostaffing.change_requests.submit',
                            'operator',
                            'Submit or withdraw PT Supervisor employee change requests.',
                            CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP
                        ),
                        (
                            'neostaffing.change_requests.approve',
                            'operator',
                            'Approve, deny, or reverse NeoStaffing employee change requests when management classification permits.',
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
                "NeoStaffing change-request table ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing change-request table ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
