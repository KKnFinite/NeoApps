"""Targeted production compatibility repair for legacy Door Pull columns."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoErmacDoorPull


NEOERMAC_DOOR_PULL_SCHEMA_LOCK_KEY = 7_483_327_341_906
NEOERMAC_DOOR_PULL_SCHEMA_LOCK_TIMEOUT = "5s"
LEGACY_DOOR_PULL_BOOLEAN_COLUMNS = (
    "no_first_mix_pull",
    "no_second_mix_pull",
)
MISSION_AWARE_DOOR_PULL_COLUMN = "sort_date_mission_id"


def ensure_neoermac_door_pull_legacy_defaults(app):
    """Repair the narrow PostgreSQL Door Pull compatibility surface at startup."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    table_name = NeoErmacDoorPull.__tablename__
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOERMAC_DOOR_PULL_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOERMAC_DOOR_PULL_SCHEMA_LOCK_KEY},
            )
            connection.execute(
                text(_legacy_default_repair_sql(table_name))
            )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoErmac Door Pull legacy-default ensure failed safely: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoErmac Door Pull legacy-default ensure completed")
    return True


def _legacy_default_repair_sql(table_name):
    statements = [
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
        f"{MISSION_AWARE_DOOR_PULL_COLUMN} INTEGER;"
    ]
    for column_name in LEGACY_DOOR_PULL_BOOLEAN_COLUMNS:
        statements.append(
            f"""
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = '{table_name}'
                  AND column_name = '{column_name}'
            ) THEN
                ALTER TABLE {table_name}
                    ALTER COLUMN {column_name} SET DEFAULT FALSE;
            END IF;
            """
        )
    body = "\n".join(statements)
    return f"""
        DO $neoapps$
        BEGIN
            IF to_regclass('{table_name}') IS NOT NULL THEN
                {body}
            END IF;
        END
        $neoapps$;
    """


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
