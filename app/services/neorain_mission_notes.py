"""Validated, versioned note mutations against a locked canonical mission."""
from app.extensions import db
from app.models import NeoRainMissionNote


class MissionNoteConflict(ValueError):
    pass


def mission_note_payload(mission):
    return [
        {"id": row.id, "text": row.text, "version": row.version}
        for row in mission.neorain_mission_notes
    ]


def save_mission_note(mission, actor_id, action, *, note_id=None, version=None, text=None):
    """The caller locks and authorizes the current Sort and mission."""
    if action not in {"add", "update", "delete"}:
        raise ValueError("Choose Add, Save or Delete.")
    row = None
    if action != "add":
        row = NeoRainMissionNote.query.filter_by(
            id=note_id, sort_date_mission_id=mission.id
        ).populate_existing().with_for_update().one_or_none()
        if not row or row.version != version:
            raise MissionNoteConflict("This note changed. Copy your draft and reload before trying again.")
    if action == "delete":
        db.session.delete(row)
        return
    text = str(text or "").strip()
    if not text or len(text) > 2000 or "\x00" in text:
        raise ValueError("Enter a mission note with 1–2,000 characters.")
    if row is None:
        db.session.add(NeoRainMissionNote(sort_date_mission_id=mission.id,
            created_by_user_id=actor_id, updated_by_user_id=actor_id, text=text))
    elif row.text != text:
        row.text = text
        row.updated_by_user_id = actor_id
        row.version += 1
