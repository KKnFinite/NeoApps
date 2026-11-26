"""NeoRaiden's read-only display of physical-tail treatment milestones."""
from collections import defaultdict

from sqlalchemy import literal, null, select, union_all

from app.extensions import db
from app.models import NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState
from app.neonodes.neosubzero.services import PRETREAT_MILESTONES, pretreat_complete
from app.services.neosubzero_departure_deice import (
    PLAN_PASS_TYPES, NeoSubZeroDepartureDeiceError, _validate_chronology,
)
from app.services.neosubzero_evidence import normalized_tail, select_tail_event
from app.services.neosubzero_operations import departure_presentation
from app.services.time_display import format_local_hhmm


def treatment_by_tail(operation_id):
    # Add pretreatment to the existing scoped event read without multiplying rows
    # when a tail has several departure events or only one kind of treatment.
    event, pretreat = NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState
    milestones = tuple(attribute for _, attribute in PRETREAT_MILESTONES)
    event_fields = (event.sort_date_mission_id, event.status, event.configured_at_utc, event.cleared_at_utc,
                    event.radio_state, event.clear_outcome, event.treatment_plan)
    departure_rows = select(literal(False).label("is_pretreat"), event.id, event.tail_number,
                            *(getattr(event, name) for name in milestones), *event_fields,
                            null().label("original_mission_id")).where(
        event.sort_date_operation_id == operation_id, event.reset_at_utc.is_(None))
    pretreat_rows = select(literal(True), pretreat.id, pretreat.tail_number,
                           *(getattr(pretreat, name) for name in milestones),
                           *(null() for _ in event_fields), pretreat.original_mission_id).where(
        pretreat.sort_date_operation_id == operation_id)

    events, pretreats = defaultdict(list), defaultdict(list)
    for row in db.session.execute(union_all(departure_rows, pretreat_rows)):
        (pretreats if row.is_pretreat else events)[normalized_tail(row.tail_number)].append(row)
    return {tail: select_tail_event(rows) for tail, rows in events.items()}, pretreats


def treatment_label(mission, tail, event, review, tail_state, pretreats, *, shared_tail):
    row = dict(event=event, tail=tail, ownership_review=review)
    if review:
        return departure_presentation(row, tail_state)
    if event:
        if normalized_tail(event.tail_number) != tail:
            return "review", "TAIL IDENTITY REVIEW"
        if event.status == "finished" or (event.status == "cleared" and event.clear_outcome in ("sprayed", None)):
            completed = _final_departure_end(event)
            if completed:
                return "fin", f"Completed {format_local_hhmm(completed, mission.timezone)}"
            if event.status == "finished" or event.clear_outcome == "sprayed":
                return "fin", "FIN"
        if event.status == "cleared" and event.clear_outcome in ("negative", "not_sprayed"):
            return ("negative", "NEGATIVE DEICE") if event.clear_outcome == "negative" else ("not-sprayed", "NOT SPRAYED")
        if event.status != "cleared" or event.clear_outcome not in ("pretreated", "no_deice", None):
            return departure_presentation(row, tail_state)
    elif tail_state and tail_state.deice_status in ("required", "configured", "negative"):
        return departure_presentation(row, tail_state)

    if len(pretreats) > 1:
        return "review", "TREATMENT OWNERSHIP REVIEW"
    if pretreats and pretreat_complete(pretreats[0]):
        pretreat = pretreats[0]
        if shared_tail and pretreat.original_mission_id != mission.id:
            return "review", "TREATMENT OWNERSHIP REVIEW"
        return "pretreated", f"Pretreated {format_local_hhmm(pretreat.pass2_started_at_utc, mission.timezone)}"
    if event or tail_state:
        return departure_presentation(row, tail_state)
    return None, None


def _final_departure_end(event):
    passes = len(PLAN_PASS_TYPES.get(event.treatment_plan, ()))
    if not passes:
        return None
    final_end = getattr(event, f"pass{passes}_ended_at_utc")
    if final_end is not None:
        try:
            _validate_chronology(event, passes)
        except NeoSubZeroDepartureDeiceError:
            return None
    return final_end
