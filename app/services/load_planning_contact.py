"""Canonical current-sort Load Planning contact resolution."""

from sqlalchemy import select

from app.extensions import db
from app.models import SortDateOperation


LOAD_PLANNING_CONTACT_MAX_LENGTH = 64


def current_load_planning_contact(operation):
    """Return effective current-sort contacts without persisting inheritance."""
    if operation is None:
        return {"extension": "", "radio_channel": ""}

    values = {
        "extension": operation.load_planner_extension,
        "radio_channel": operation.load_planner_radio_channel,
    }
    for key, column_name in (
        ("extension", "load_planner_extension"),
        ("radio_channel", "load_planner_radio_channel"),
    ):
        if values[key] is None:
            values[key] = _prior_contact_value(operation, column_name)
    return {key: value if value is not None else "" for key, value in values.items()}


def set_load_planning_contact(operation, *, extension, radio_channel):
    """Stage both current-sort contact values without committing."""
    if operation is None:
        raise ValueError("A current sort is required for Load Planning contacts.")
    operation.load_planner_extension = _nullable_contact_text(extension, "Extension")
    operation.load_planner_radio_channel = _nullable_contact_text(
        radio_channel,
        "Radio channel",
    )
    return operation


def _prior_contact_value(operation, column_name):
    column = getattr(SortDateOperation, column_name)
    return db.session.scalar(
        select(column)
        .where(
            SortDateOperation.gateway_code == operation.gateway_code,
            SortDateOperation.sort_name == operation.sort_name,
            SortDateOperation.sort_date < operation.sort_date,
            column.is_not(None),
        )
        .order_by(SortDateOperation.sort_date.desc(), SortDateOperation.id.desc())
        .limit(1)
    )


def _nullable_contact_text(value, label):
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > LOAD_PLANNING_CONTACT_MAX_LENGTH:
        raise ValueError(f"{label} must be {LOAD_PLANNING_CONTACT_MAX_LENGTH} characters or fewer.")
    return text
