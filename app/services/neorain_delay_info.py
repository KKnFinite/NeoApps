"""Small, transaction-neutral NeoRain Delay Info helpers."""

from app.extensions import db
from app.models import NeoRainDelayInfo


class NeoRainDelayInfoError(ValueError):
    pass


def neorain_delay_info_rows(mission):
    """List a mission's rows in durable creation/id order without writes."""
    if mission is None or mission.id is None:
        return []
    return NeoRainDelayInfo.query.filter_by(sort_date_mission_id=mission.id).order_by(
        NeoRainDelayInfo.created_at, NeoRainDelayInfo.id
    ).all()


def add_neorain_delay_info(mission, minutes, code, notes=None):
    _require_mission(mission)
    row = NeoRainDelayInfo(sort_date_mission_id=mission.id)
    _apply(row, minutes, code, notes)
    db.session.add(row)
    return row


def update_neorain_delay_info(mission, delay_row, minutes, code, notes=None):
    _belongs_to_mission(mission, delay_row)
    _apply(delay_row, minutes, code, notes)
    return delay_row


def delete_neorain_delay_info(mission, delay_row):
    _belongs_to_mission(mission, delay_row)
    db.session.delete(delay_row)


def _apply(row, minutes, code, notes):
    row.minutes = _positive_minutes(minutes)
    row.code = _delay_code(code)
    row.notes = _notes(notes)


def _require_mission(mission):
    if mission is None or mission.id is None:
        raise NeoRainDelayInfoError("A saved mission is required.")


def _belongs_to_mission(mission, delay_row):
    _require_mission(mission)
    if delay_row is None or delay_row.sort_date_mission_id != mission.id:
        raise NeoRainDelayInfoError("Delay Info does not belong to this mission.")


def _positive_minutes(value):
    if isinstance(value, bool):
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.") from None
    if str(value).strip() != str(parsed) or parsed <= 0:
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.")
    return parsed


def _delay_code(value):
    code = str(value or "").strip().upper()
    if len(code) != 2:
        raise NeoRainDelayInfoError("Delay code must be exactly 2 characters.")
    return code


def _notes(value):
    text = str(value or "").strip()
    return text or None
