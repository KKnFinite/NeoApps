"""Targeted production schema repair for NeoErmac ULD workspace ownership."""

from sqlalchemy import text

from app.extensions import db


NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_KEY = 7_483_327_341_909
NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_TIMEOUT = "5s"


def ensure_neoermac_uld_workspace_columns(app):
    """Add only ULD requester provenance and its request uniqueness rule."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_KEY},
            )
            connection.execute(
                text(
                    "ALTER TABLE neoermac_uld_requests ADD COLUMN IF NOT EXISTS "
                    "requested_by_user_id INTEGER"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE neosektor_uld_on_the_way_events "
                    "ADD COLUMN IF NOT EXISTS requested_by_user_id INTEGER"
                )
            )
            _ensure_foreign_keys(connection)
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_neoermac_uld_requests_requested_by_user_id "
                    "ON neoermac_uld_requests (requested_by_user_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS "
                    "ix_neosektor_uld_on_the_way_events_requested_by_user_id "
                    "ON neosektor_uld_on_the_way_events (requested_by_user_id)"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE neoermac_uld_requests DROP CONSTRAINT IF EXISTS "
                    "uq_neoermac_uld_requests_gateway_door_setup"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE neoermac_uld_requests DROP CONSTRAINT IF EXISTS "
                    "uq_neoermac_uld_requests_gateway_operation_door_setup"
                )
            )
            connection.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'uq_neoermac_uld_request_scope_requester'
                        ) THEN
                            ALTER TABLE neoermac_uld_requests
                            ADD CONSTRAINT uq_neoermac_uld_request_scope_requester
                            UNIQUE (
                                gateway_id,
                                sort_date_operation_id,
                                door,
                                setup_needed,
                                requested_by_user_id
                            );
                        END IF;
                    END
                    $$;
                    """
                )
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoErmac ULD workspace targeted schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoErmac ULD workspace targeted schema ensure completed")
    return True


def _ensure_foreign_keys(connection):
    statements = (
        (
            "fk_neoermac_uld_requests_requested_by_user_id",
            "neoermac_uld_requests",
        ),
        (
            "fk_neosektor_uld_events_requested_by_user_id",
            "neosektor_uld_on_the_way_events",
        ),
    )
    for constraint_name, table_name in statements:
        connection.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = '{constraint_name}'
                    ) THEN
                        ALTER TABLE {table_name}
                        ADD CONSTRAINT {constraint_name}
                        FOREIGN KEY (requested_by_user_id) REFERENCES users(id);
                    END IF;
                END
                $$;
                """
            )
        )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
