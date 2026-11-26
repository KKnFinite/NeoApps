"""Small, transaction-neutral NeoRain Delay Info helpers."""

from app.extensions import db
from app.models import NeoRainDelayInfo, NeoRainMissionNote
from app.services.live_collaboration import entity_version


class NeoRainDelayInfoError(ValueError):
    pass


def mission_delay_entries(mission):
    """Merge the two preloaded sources without copying or renumbering historical notes."""
    rows = (*mission.delay_info_rows, *mission.neorain_mission_notes)
    return sorted(rows, key=lambda row: (row.created_at, isinstance(row, NeoRainMissionNote), row.id))


def delay_entry_payload(row):
    return dict(id=f"note-{row.id}" if isinstance(row, NeoRainMissionNote) else row.id,
                minutes=row.minutes, code=row.code, notes=row.notes or "", version=entity_version(row))


def mission_delay_entry_count(mission_id):
    def count(model):
        return db.select(db.func.count()).select_from(model).where(model.sort_date_mission_id == mission_id).scalar_subquery()
    return db.session.scalar(db.select(count(NeoRainDelayInfo) + count(NeoRainMissionNote)))


def find_delay_entry_for_update(mission, entry_id):
    model = NeoRainMissionNote if entry_id.startswith("note-") else NeoRainDelayInfo
    try:
        row_id = int(entry_id.removeprefix("note-"))
    except ValueError:
        return None
    if not 0 < row_id <= 2_147_483_647:
        return None
    return model.query.filter_by(id=row_id, sort_date_mission_id=mission.id).populate_existing().with_for_update().one_or_none()


def format_delay_details(entries, *, notes_only=False):
    if notes_only:
        return "; ".join(entry.notes for entry in entries if entry.notes and entry.notes.strip())
    details = []
    for entry in entries:
        prefix = " ".join(str(value) for value in (entry.minutes, entry.code) if value is not None and value != "")
        if prefix and entry.notes:
            details.append(f"{prefix} - {entry.notes}")
        elif prefix or entry.notes:
            details.append(prefix or entry.notes)
    return "; ".join(details)


def add_neorain_delay_info(mission, minutes, code, notes=None):
    _require_mission(mission)
    row = NeoRainDelayInfo(sort_date_mission_id=mission.id)
    _apply(row, minutes, code, notes)
    db.session.add(row)
    return row


def update_neorain_delay_info(mission, delay_row, minutes, code, notes=None, *, actor_id=None):
    _belongs_to_mission(mission, delay_row)
    before = (delay_row.minutes, delay_row.code, delay_row.notes)
    _apply(delay_row, minutes, code, notes)
    if isinstance(delay_row, NeoRainMissionNote) and before != (delay_row.minutes, delay_row.code, delay_row.notes):
        delay_row.updated_by_user_id = actor_id
        delay_row.version += 1
    return delay_row


def delete_neorain_delay_info(mission, delay_row):
    _belongs_to_mission(mission, delay_row)
    db.session.delete(delay_row)


def _apply(row, minutes, code, notes):
    minutes = _positive_minutes(minutes)
    code = _delay_code(code)
    # Browsers normalize textarea newlines. Preserve the recorded text when
    # changing only minutes or code on an existing entry.
    recorded = row.notes
    submitted = str(notes or "").replace("\r\n", "\n").replace("\r", "\n")
    notes = recorded if recorded and submitted == recorded.replace("\r\n", "\n").replace("\r", "\n") else submitted.strip()
    if minutes is None and code is None and not (notes and notes.strip()):
        raise NeoRainDelayInfoError("Enter minutes, a code or a note.")
    if notes and "\x00" in notes:
        raise NeoRainDelayInfoError("Remove invalid characters from the note.")
    row.minutes, row.code = minutes, code
    row.notes = notes or ("" if isinstance(row, NeoRainMissionNote) else None)


def _require_mission(mission):
    if mission is None or mission.id is None:
        raise NeoRainDelayInfoError("A saved mission is required.")


def _belongs_to_mission(mission, delay_row):
    _require_mission(mission)
    if delay_row is None or delay_row.sort_date_mission_id != mission.id:
        raise NeoRainDelayInfoError("Delay Info does not belong to this mission.")


def _positive_minutes(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.") from None
    if str(value).strip() != str(parsed) or not 0 < parsed <= 2_147_483_647:
        raise NeoRainDelayInfoError("Delay minutes must be a positive whole number.")
    return parsed


def _delay_code(value):
    code = str(value or "").strip().upper()
    if not code:
        return None
    if len(code) != 2 or "\x00" in code:
        raise NeoRainDelayInfoError("Delay code must be exactly 2 characters.")
    return code
