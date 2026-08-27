"""Targeted schema compatibility for Staffing management classifications."""

from sqlalchemy import text

from app.extensions import db
from app.models import StaffingPerson, User
from app.models.staffing_person import STAFFING_DATABASE_CLASSIFICATIONS
from app.models.user import MANAGEMENT_LEVELS


NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_KEY = 7_483_327_341_909
NEOSTAFFING_CLASSIFICATION_SCHEMA_LOCK_TIMEOUT = "5s"
NEOSTAFFING_CLASSIFICATION_CONSTRAINT = "ck_staffing_people_classification"
NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT = (
    "ck_staffing_people_classification_phase1"
)
NEOSTAFFING_USER_MANAGEMENT_CONSTRAINT = "ck_users_management_level_supported"
NEOSTAFFING_USER_MANAGEMENT_TRANSITION_CONSTRAINT = (
    "ck_users_management_level_supported_transition"
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
            _ensure_allowed_constraint(
                connection,
                User.__tablename__,
                "management_level",
                MANAGEMENT_LEVELS,
                NEOSTAFFING_USER_MANAGEMENT_CONSTRAINT,
                NEOSTAFFING_USER_MANAGEMENT_TRANSITION_CONSTRAINT,
                nullable=True,
            )
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
    return _ensure_allowed_constraint(
        connection,
        table_name,
        "classification",
        STAFFING_DATABASE_CLASSIFICATIONS,
        NEOSTAFFING_CLASSIFICATION_CONSTRAINT,
        NEOSTAFFING_CLASSIFICATION_TRANSITION_CONSTRAINT,
    )


def _ensure_allowed_constraint(
    connection,
    table_name,
    column_name,
    allowed,
    constraint_name,
    transition_name,
    *,
    nullable=False,
):
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
            "constraint_name": constraint_name,
            "table_name": table_name,
        },
    ).scalar()
    normalized_definition = str(definition or "").casefold()
    if all(
        f"'{classification}'" in normalized_definition
        for classification in allowed
    ):
        return

    allowed_values = ", ".join(
        f"'{classification}'"
        for classification in allowed
    )
    expression = f"{column_name} IN ({allowed_values})"
    if nullable:
        expression = f"{column_name} IS NULL OR {expression}"
    # PostgreSQL DDL is transactional. The old constraint remains authoritative
    # while the superset is added and validated; the canonical-name swap is
    # committed atomically with this startup ensure.
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{transition_name}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} ADD CONSTRAINT "
            f"{transition_name} CHECK ({expression}) NOT VALID"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} VALIDATE CONSTRAINT "
            f"{transition_name}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS "
            f"{constraint_name}"
        )
    )
    connection.execute(
        text(
            f"ALTER TABLE {table_name} RENAME CONSTRAINT "
            f"{transition_name} TO {constraint_name}"
        )
    )


def _is_postgresql(app):
    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).lower()
    return database_uri.startswith(
        ("postgresql:", "postgresql+", "postgres:", "postgres+")
    )
