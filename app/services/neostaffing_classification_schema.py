"""Targeted Phase 1 schema compatibility for Staffing classifications."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingPerson
from app.models.staffing_person import STAFFING_DATABASE_CLASSIFICATIONS


NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_KEY = 7_483_327_341_909
NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_TIMEOUT = "5s"
NEOSTAFFING_CLASSIFICATION_CONSTRAINT = "ck_staffing_people_classification"
NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT = (
    "ck_staffing_people_classification_phase1"
)


def ensure_neostaffing_classification_constraint(app):
    """Widen the PostgreSQL classification CHECK without changing any rows."""
    if app.config.get("TESTING") or not _is_postgresql(app):
        return False

    table_name = StaffingPerson.__tablename__
    with app.app_context():
        try:
            connection = db.session.connection()
            connection.execute(
                text(
                    "SET LOCAL lock_timeout = "
                    f"'{NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_TIMEOUT}'"
                )
            )
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_KEY},
            )
            _ensure_classification_constraint(connection, table_name)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            app.logger.error(
                "NeoStaffing classification schema ensure failed: error=%s",
                type(error).__name__,
            )
            raise

    app.logger.info("NeoStaffing classification schema ensure completed")
    return True


def _ensure_classification_constraint(connection, table_name):
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
            "constraint_name": NEOSTAFFING_CLASSIFICATION_CONSTRAINT,
            "table_name": table_name,
        },
    ).scalar()
    normalized_definition = str(definition or "").casefold()
    if all(
        f"'{classification}'" in normalized_definition
        for classification in STAFFING_DATABASE_CLASSIFICATIONS
    ):
        return

    allowed_values = ", ".join(
        f"'{classification}'"
        for classification in STAFFING_DATABASE_CLASSIFICATIONS
    )
    # PostgreSQL DDL is transactional. The old constraint remains authoritative
    # while the superset is added and validated; the canonical-name swap is
    # committed atomically with this startup ensure.
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} ADD CONSTRAINT "
            f"{NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT} "
            f"CHECK (classification IN ({allowed_values})) NOT VALID"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
            f"{NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{NEOSTAFFING_CLASSIFICATION_CONSTRAINT}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} RENAME CONSTRAINT "
            f"{NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT} TO "
            f"{NEOSTAFFING_CLASSIFICATION_CONSTRAINT}"
        )
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
