"""Tail notes use their own versions; they never alter fuel work or its revision."""
from datetime import datetime
import re
from sqlalchemy import func, update
from app.extensions import db
from app.models.neoscorpion_tail_note import NeoScorpionTailNote as TailNote


class TailNoteConflict(ValueError):
    pass


def normalize_tail(value):
    tail = (value or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9-]{0,31}', tail):
        raise ValueError('A valid aircraft tail is required.')
    return tail


def tail_notes_revision(gateway):
    return int(db.session.query(func.coalesce(func.sum(TailNote.version), 0)).filter(
        TailNote.gateway_id == gateway.id).scalar())


def tail_notes_by_tail(gateway_id, tails):
    tails = set(tails) - {''}
    if not tails:
        return {}
    return {item.tail_number: presentation(item) for item in TailNote.query.filter(
        TailNote.gateway_id == gateway_id, TailNote.tail_number.in_(tails)).all()}


def presentation(record):
    editor = record.editor
    return dict(tail=record.tail_number, notes=record.notes, version=record.version,
        editor=(getattr(editor, 'display_name', None) or editor.username) if editor else 'Unknown editor',
        updated_at=record.updated_at_utc.isoformat(timespec='seconds') + 'Z')


def save_tail_note(gateway, user, form):
    tail = normalize_tail(form.get('tail_number'))
    notes = form.get('notes', '').replace('\r\n', '\n').replace('\r', '\n')
    if len(notes) > 10000:
        raise ValueError('Tail Notes must be 10,000 characters or fewer.')
    try:
        version = int(form.get('expected_version', ''))
        if not 0 <= version < 2147483647:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('Refresh the aircraft notes before saving.') from None
    now = datetime.utcnow()
    if db.session.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.session.execute(insert(TailNote).values(gateway_id=gateway.id, tail_number=tail,
        notes='', version=0, updated_at_utc=now).on_conflict_do_nothing(
            index_elements=['gateway_id', 'tail_number']))
    changed = db.session.execute(update(TailNote).where(TailNote.gateway_id == gateway.id,
        TailNote.tail_number == tail, TailNote.version == version).values(notes=notes,
        version=version + 1, updated_by_user_id=user.id, updated_at_utc=now))
    if changed.rowcount != 1:
        raise TailNoteConflict('Tail Notes changed in another window. Your draft is kept. Reload and review the latest note before saving.')
    db.session.flush()
    record = TailNote.query.filter_by(gateway_id=gateway.id, tail_number=tail).populate_existing().one()
    return presentation(record)
