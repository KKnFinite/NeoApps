"""Targeted additive schema repair for NeoErmac Building Lineup capacity."""

from sqlalchemy import text

from app.extensions import db
from app.models import NeoErmacBuildingLineup


NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_KEY = 7_483_327_341_907
NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_TIMEOUT = "5s"
NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS = (
    "east_destination_1_slot_2",
    "east_destination_2_slot_2",
    "west_destination_1_slot_2",
    "west_destination_2_slot_2",
)


def ensure_neoermac_building_lineup_columns(app):
    """Add only the four nullable second-slot columns in PostgreSQL."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    table_name = NeoErmacBuildingLineup.__tablename__
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_KEY},
            )
            for column_name in NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "
                        f"{column_name} VARCHAR(8)"
                    )
                )
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoErmac Building Lineup targeted schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoErmac Building Lineup targeted schema ensure completed")
    return True


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
