"""Small error-message boundary: domain validation stays specific, SQL does not."""

from pathlib import Path
import traceback

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError


def safe_mutation_error(error, action):
    if not isinstance(error, SQLAlchemyError):
        return str(error)
    original = getattr(error, "orig", None)
    if (getattr(original, "pgcode", None) == "23505"
            and getattr(getattr(original, "diag", None), "constraint_name", None)
            in {"uq_staffing_people_employee_id_normalized",
                "ix_staffing_people_employee_id", "staffing_people_employee_id_key"}):
        return "Employee ID already exists."
    # No exception formatting/locals/SQL/parameters: SQLAlchemy exception text
    # commonly includes complete submitted records, even with exc_info=True.
    frames = traceback.extract_tb(error.__traceback__)
    location = "; ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in frames[-5:])
    current_app.logger.error(
        "Database action failed: action=%s error_type=%s location=%s",
        action, type(error).__name__, location or "unavailable",
    )
    return f"Unable to {action}. Please try again."
