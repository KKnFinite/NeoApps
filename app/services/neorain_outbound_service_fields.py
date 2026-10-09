"""Operator-editable Rain Outbound details independent of Google/Neo milestones."""
import re
from datetime import time

from app.models import NeoRainOutboundServiceState

_SERVICE_FIELDS = frozenset({"meal", "jump", "js_in"})
_HHMM = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_HHMM_COMPACT = re.compile(r"^(?:[01][0-9]|2[0-3])[0-5][0-9]$")


class NeoRainOutboundServiceFieldError(ValueError):
    """Reject invalid operator values without mutating a mission."""


def outbound_service_states(operation):
    if operation is None:
        return {}
    return {
        state.sort_date_mission_id: state
        for state in NeoRainOutboundServiceState.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }


def serialize_outbound_service_state(state):
    return {
        "meal": bool(state.meal) if state is not None else False,
        "jump": state.jump_count if state is not None else None,
        "js_in": (
            state.js_in_time.strftime("%H:%M")
            if state is not None and state.js_in_time is not None
            else ""
        ),
        "service_version": int(state.revision or 1) if state is not None else 0,
    }


def parse_outbound_service_field(field, value):
    if field not in _SERVICE_FIELDS:
        raise NeoRainOutboundServiceFieldError("Choose Meal, Jump, or JS In.")
    if field == "meal":
        if type(value) is not bool:
            raise NeoRainOutboundServiceFieldError("Meal must be checked or unchecked.")
        return value
    if field == "jump":
        if value is None or value == "":
            return None
        if type(value) is int and 0 <= value <= 9:
            return value
        if isinstance(value, str) and re.fullmatch(r"[0-9]", value):
            return int(value)
        raise NeoRainOutboundServiceFieldError("Jump must be one digit from 0 to 9.")
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise NeoRainOutboundServiceFieldError("JS In must use 24-hour HH:MM.")
    if _HHMM_COMPACT.fullmatch(value):
        value = value[:2] + ":" + value[2:]
    if not _HHMM.fullmatch(value):
        raise NeoRainOutboundServiceFieldError("JS In must use 24-hour HH:MM.")
    return time(int(value[:2]), int(value[3:]))


def current_outbound_service_value(state, field):
    if field == "meal":
        return bool(state.meal) if state is not None else False
    if field == "jump":
        return state.jump_count if state is not None else None
    if field == "js_in":
        return state.js_in_time if state is not None else None
    raise NeoRainOutboundServiceFieldError("Choose Meal, Jump, or JS In.")


def apply_outbound_service_value(state, field, normalized):
    if field == "meal":
        state.meal = normalized
    elif field == "jump":
        state.jump_count = normalized
    elif field == "js_in":
        state.js_in_time = normalized
    else:
        raise NeoRainOutboundServiceFieldError("Choose Meal, Jump, or JS In.")
