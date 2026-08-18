"""Targeted production schema ensure for NeoScorpion operational models."""

import re

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import (
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    NeoScorpionTailFuelState,
)


NEOSCORPION_SCHEMA_LOCK_KEY = 7_483_327_341_912
NEOSCORPION_SCHEMA_LOCK_TIMEOUT = "5s"

NEOSCORPION_MODEL_TABLES = (
    NeoScorpionTailFuelState,
    NeoScorpionFuelTruck,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelWorkState,
    NeoScorpionFuelTankState,
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelAuditEntry,
)

NEOSCORPION_ADDITIVE_COLUMNS = {
    "neoscorpion_settings": {
        "planning_inbound_fuel_fallback_lbs": "INTEGER",
    },
    "neoscorpion_fuel_assignments": {
        "fuel_on_board_at_utc": "TIMESTAMP",
        "fuel_on_board_by_user_id": "INTEGER",
        "completed_at_utc": "TIMESTAMP",
        "completed_by_user_id": "INTEGER",
        "confirmed_tail_number": "VARCHAR(32)",
        "operational_status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
        "hold_reason": "TEXT",
        "hold_at_utc": "TIMESTAMP",
        "hold_by_user_id": "INTEGER",
    },
    "neoscorpion_fuel_work_states": {
        "apu_running": "BOOLEAN",
        "apu_confirmed_at_utc": "TIMESTAMP",
        "apu_allowance_lbs": "INTEGER",
        "applied_apu_rate_thousand_lbs_per_hour": "NUMERIC(8, 4)",
        "apu_source_tank_code": "VARCHAR(32)",
        "off_at_utc": "TIMESTAMP",
        "off_by_user_id": "INTEGER",
        "truck_segment_started_at_utc": "TIMESTAMP",
        "ended_early_at_utc": "TIMESTAMP",
        "ended_early_by_user_id": "INTEGER",
        "ended_early_reason": "TEXT",
    },
}

NEOSCORPION_CHECK_CONSTRAINTS = (
    (
        "neoscorpion_fuel_assignments",
        "ck_neoscorpion_fuel_assignment_operational_status",
        "operational_status",
        ("active", "hold_review"),
    ),
    (
        "neoscorpion_fuel_audit_entries",
        "ck_neoscorpion_fuel_audit_entry_action",
        "action",
        (
            "reopen_off",
            "correct_actual",
            "auto_hold",
            "resume_hold",
            "swap_fueler",
            "swap_truck",
            "confirm_tail",
            "end_early",
        ),
    ),
)


def ensure_neoscorpion_production_schema(app):
    """Repair and verify only the active NeoScorpion PostgreSQL schema."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSCORPION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSCORPION_SCHEMA_LOCK_KEY},
            )
            for model in NEOSCORPION_MODEL_TABLES:
                model.__table__.create(bind=connection, checkfirst=True)
            _ensure_additive_columns(connection)
            _ensure_check_constraints(connection)
            _verify_model_schema_contract(connection)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoScorpion production schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoScorpion production schema ensure completed")
    return True


def neoscorpion_model_schema_contract():
    return {
        model.__table__.name: frozenset(model.__table__.columns.keys())
        for model in NEOSCORPION_MODEL_TABLES
    }


def _ensure_additive_columns(connection):
    for table_name, columns in NEOSCORPION_ADDITIVE_COLUMNS.items():
        for column_name, column_type in columns.items():
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                    f"{column_name} {column_type}"
                )
            )


def _ensure_check_constraints(connection):
    for table_name, constraint_name, column_name, allowed_values in (
        NEOSCORPION_CHECK_CONSTRAINTS
    ):
        definition = connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid)
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                JOIN pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE relation.relname = :table_name
                  AND namespace.nspname = current_schema()
                  AND constraint_row.conname = :constraint_name
                """
            ),
            {
                "table_name": table_name,
                "constraint_name": constraint_name,
            },
        ).scalar()
        if _constraint_values(definition) == frozenset(allowed_values):
            continue

        connection.execute(
            text(
                f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
                f"{constraint_name}"
            )
        )
        quoted_values = ", ".join(f"'{value}'" for value in allowed_values)
        connection.execute(
            text(
                f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
                f"CHECK ({column_name} IN ({quoted_values}))"
            )
        )


def _verify_model_schema_contract(connection):
    schema_inspector = inspect(connection)
    actual_tables = set(schema_inspector.get_table_names())
    expected_contract = neoscorpion_model_schema_contract()
    missing_tables = sorted(set(expected_contract) - actual_tables)
    if missing_tables:
        raise RuntimeError(
            "NeoScorpion schema contract missing tables: "
            + ", ".join(missing_tables)
        )

    missing_columns = {}
    for table_name, expected_columns in expected_contract.items():
        actual_columns = {
            column["name"] for column in schema_inspector.get_columns(table_name)
        }
        missing = sorted(expected_columns - actual_columns)
        if missing:
            missing_columns[table_name] = missing
    if missing_columns:
        details = "; ".join(
            f"{table_name}: {', '.join(columns)}"
            for table_name, columns in sorted(missing_columns.items())
        )
        raise RuntimeError(f"NeoScorpion schema contract missing columns: {details}")


def _constraint_values(definition):
    return frozenset(re.findall(r"'([^']+)'", str(definition or "")))


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
