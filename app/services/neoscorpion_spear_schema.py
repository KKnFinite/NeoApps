"""Narrow startup compatibility ensure for the SPEAR model contract."""

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import NeoScorpionSpearAuditEntry


NEOSCORPION_SPEAR_SCHEMA_LOCK_KEY = 7_483_327_341_913
NEOSCORPION_SPEAR_SCHEMA_LOCK_TIMEOUT = "5s"
SPEAR_SETTINGS_TABLE = "neoscorpion_settings"
SPEAR_SETTINGS_COLUMNS = {
    "spear_recommendations_enabled": "BOOLEAN NOT NULL DEFAULT TRUE",
    "spear_automation_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
    "spear_learning_capture_enabled": "BOOLEAN NOT NULL DEFAULT FALSE",
    "spear_minimum_truck_reserve_gallons": "INTEGER NOT NULL DEFAULT 500",
    "spear_do_not_top_off_above_percent": "INTEGER NOT NULL DEFAULT 70",
    "spear_truck_minutes_per_ramp_move": "NUMERIC(8, 2) NOT NULL DEFAULT 2",
    "spear_fueler_begins_at": "VARCHAR(16) NOT NULL DEFAULT 'Remote'",
    "spear_truck_begins_at": "VARCHAR(16) NOT NULL DEFAULT 'Remote'",
    "spear_truck_after_top_off": "VARCHAR(16) NOT NULL DEFAULT 'Remote'",
    "spear_incoming_early_staging_minutes": "INTEGER NOT NULL DEFAULT 15",
    "spear_recalculation_interval_minutes": "INTEGER NOT NULL DEFAULT 2",
    "spear_automation_stability_delay_seconds": "INTEGER NOT NULL DEFAULT 5",
    "spear_priority_order_json": "TEXT",
}


def ensure_neoscorpion_spear_schema_compatibility(app):
    """Ensure only the additive SPEAR PostgreSQL contract before route queries."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSCORPION_SPEAR_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSCORPION_SPEAR_SCHEMA_LOCK_KEY},
            )
            for column_name, column_type in SPEAR_SETTINGS_COLUMNS.items():
                connection.execute(
                    text(
                        f"ALTER TABLE {SPEAR_SETTINGS_TABLE} "
                        f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                    )
                )
            NeoScorpionSpearAuditEntry.__table__.create(
                bind=connection,
                checkfirst=True,
            )
            _verify_spear_schema_contract(connection)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoScorpion SPEAR schema compatibility ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoScorpion SPEAR schema compatibility ensure completed")
    return True


def _verify_spear_schema_contract(connection):
    schema_inspector = inspect(connection)
    tables = set(schema_inspector.get_table_names())
    audit_table = NeoScorpionSpearAuditEntry.__tablename__
    if audit_table not in tables:
        raise RuntimeError("NeoScorpion SPEAR audit table is missing.")
    actual_settings_columns = {
        column["name"]
        for column in schema_inspector.get_columns(SPEAR_SETTINGS_TABLE)
    }
    missing_settings_columns = sorted(
        set(SPEAR_SETTINGS_COLUMNS) - actual_settings_columns
    )
    if missing_settings_columns:
        raise RuntimeError(
            "NeoScorpion SPEAR settings columns are missing: "
            + ", ".join(missing_settings_columns)
        )
    actual_audit_columns = {
        column["name"] for column in schema_inspector.get_columns(audit_table)
    }
    missing_audit_columns = sorted(
        set(NeoScorpionSpearAuditEntry.__table__.columns.keys())
        - actual_audit_columns
    )
    if missing_audit_columns:
        raise RuntimeError(
            "NeoScorpion SPEAR audit columns are missing: "
            + ", ".join(missing_audit_columns)
        )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
