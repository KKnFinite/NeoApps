import hashlib
import json
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import (
    NeoSubZeroPretreatState, SortDateMission, SortDateParkingAssignment,
    SortDateTailState,
)
from app.services.live_collaboration import entity_version
from app.services.live_screen_refresh import live_screen_refresh_value
from app.services.night_sorting import sort_datetime_for_local_time
from app.services.node_refresh import node_auto_refresh_status
from app.services.operation_scope import current_operational_sort_operation
from app.services.parking_plan import tail_operational_status, tail_operational_status_label
from app.services.time_display import format_local_hhmm

PRETREAT_REFRESH_KEY = "neosubzero.pretreat"
SURFACE_AREAS = ("wings_only", "wings_tail", "entire_aircraft")
SURFACE_LABELS = {"wings_only": "Wings Only", "wings_tail": "Wings + Tail", "entire_aircraft": "Entire Aircraft"}
_HHMM = re.compile(r"^\d{4}$")
_OPERATION_UNSET = object()


class NeoSubZeroPretreatError(ValueError):
    pass


def current_neosubzero_operation(gateway):
    return current_operational_sort_operation(gateway)


def pretreat_context(gateway, operation=_OPERATION_UNSET):
    if operation is _OPERATION_UNSET:
        operation = current_neosubzero_operation(gateway)
    if operation is None:
        return {"operation": None, "rows": []}
    missions = SortDateMission.query.filter_by(sort_date_operation_id=operation.id).all()
    departures = [
        m for m in missions
        if m.mission_type == "departure"
        and str(m.departure_status or "scheduled").strip().lower() not in {"cancelled", "departed"}
    ]
    arrivals_by_tail = {}
    for mission in missions:
        tail = _tail(mission.assigned_tail_number)
        if mission.mission_type != "arrival" or not tail:
            continue
        value = mission.eta_datetime_utc or mission.planned_datetime_utc
        if tail not in arrivals_by_tail or (value is not None and (arrivals_by_tail[tail] is None or value < arrivals_by_tail[tail])):
            arrivals_by_tail[tail] = value
    tail_keys = {_tail(m.assigned_tail_number) for m in departures if _tail(m.assigned_tail_number)}
    states = {_tail(row.tail_number): row for row in NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id).all()}
    tail_states = {_tail(row.tail_number): row for row in SortDateTailState.query.filter_by(sort_date=operation.sort_date, gateway_code=operation.gateway_code, sort_name=operation.sort_name).all() if _tail(row.tail_number) in tail_keys}
    parking = {_tail(row.tail_number): row for row in SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id).all()}
    rows = []
    for mission in departures:
        tail = _tail(mission.assigned_tail_number)
        inbound_eta = arrivals_by_tail.get(tail)
        outbound_std = mission.planned_datetime_utc
        state = states.get(tail)
        tail_state = tail_states.get(tail)
        assignment = parking.get(tail)
        completed = bool(tail_state and tail_state.pretreat_status)
        rows.append({
            "mission_id": mission.id, "tail": tail or "-", "inbound_eta": format_local_hhmm(inbound_eta, mission.timezone or None),
            "tail_status": tail_operational_status_label(tail_operational_status(tail_state, assignment=assignment)) or "NORMAL",
            "outbound_std": format_local_hhmm(outbound_std, mission.timezone or None), "parking": assignment.position_code if assignment else "TBD",
            "ground_time": _ground_time(inbound_eta, outbound_std), "state": state, "planned": bool(state and state.pretreat_planned),
            "deice_status": str(getattr(tail_state, "deice_status", "unknown") or "unknown").replace("_", " ").upper(),
            "configured": format_local_hhmm(state.configured_at_utc, mission.timezone or None) if state else "", "completed": completed,
            "pass1_start": _display(state, "pass1_started_at_utc", mission), "pass1_end": _display(state, "pass1_ended_at_utc", mission),
            "pass2_start": _display(state, "pass2_started_at_utc", mission), "pass2_end": _display(state, "pass2_ended_at_utc", mission),
            "version": entity_version(state), "sort_eta": inbound_eta or datetime.max,
        })
    rows.sort(key=lambda row: (row["sort_eta"], row["tail"], row["mission_id"]))
    return {"operation": operation, "rows": rows}


def mutate_pretreat(operation, mission, action, values, state=None):
    tail = _tail(getattr(mission, "assigned_tail_number", None))
    if mission is None or mission.mission_type != "departure" or mission.sort_date_operation_id != operation.id or not tail:
        raise NeoSubZeroPretreatError("Choose a current departure with an assigned tail.")
    if state is None:
        state = NeoSubZeroPretreatState(sort_date_operation_id=operation.id, tail_number=tail)
        db.session.add(state)
    if action == "toggle_planned":
        state.pretreat_planned = not state.pretreat_planned
    elif action == "toggle_configured":
        state.configured_at_utc = None if state.configured_at_utc else datetime.utcnow().replace(second=0, microsecond=0)
    elif action == "save_treatment":
        state.pass1_surface_area = _surface(values.get("pass1_surface_area"))
        state.pass2_surface_area = _surface(values.get("pass2_surface_area"))
        state.pass1_started_at_utc = _parse_hhmm(values.get("pass1_start"), operation, mission)
        state.pass1_ended_at_utc = _parse_hhmm(values.get("pass1_end"), operation, mission)
        state.pass2_started_at_utc = _parse_hhmm(values.get("pass2_start"), operation, mission)
        state.pass2_ended_at_utc = _parse_hhmm(values.get("pass2_end"), operation, mission)
        state.notes = str(values.get("notes") or "").strip() or None
        _validate_sequence(state)
    else:
        raise NeoSubZeroPretreatError("Choose a valid Pretreat action.")
    if action == "save_treatment":
        _sync_completion(operation, tail, state)
    return state


def pretreat_revision(gateway, operation=_OPERATION_UNSET):
    if operation is _OPERATION_UNSET:
        operation = current_neosubzero_operation(gateway)
    operation_id = operation.id if operation else None
    mission_ids = select(SortDateMission.id).where(SortDateMission.sort_date_operation_id == operation_id) if operation_id else select(SortDateMission.id).where(SortDateMission.id.is_(None))
    aggregates = (
        _aggregate("missions", SortDateMission, SortDateMission.updated_at, SortDateMission.id.in_(mission_ids)),
        _aggregate("parking", SortDateParkingAssignment, SortDateParkingAssignment.updated_at, SortDateParkingAssignment.sort_date_operation_id == operation_id),
        _aggregate("pretreat", NeoSubZeroPretreatState, NeoSubZeroPretreatState.updated_at, NeoSubZeroPretreatState.sort_date_operation_id == operation_id),
        _aggregate("tails", SortDateTailState, SortDateTailState.updated_at, SortDateTailState.sort_date == (operation.sort_date if operation else None), SortDateTailState.gateway_code == gateway.code, SortDateTailState.sort_name == (operation.sort_name if operation else "night")),
    )
    values = db.session.execute(union_all(*aggregates)).all()
    payload = [(r.source, int(r.row_count or 0), int(r.max_id or 0), str(r.latest or "")) for r in values]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def pretreat_refresh_status(gateway, operation=None):
    status = node_auto_refresh_status(gateway, operation=operation)
    setting = live_screen_refresh_value(gateway, PRETREAT_REFRESH_KEY)
    status["live_screen_refresh_interval_ms"] = setting.effective_interval_ms
    if not setting.enabled: status.update({"auto_refresh_enabled": False, "reason": "disabled", "message": "Live updates off", "live_status_label": "Live updates off"})
    return status


def _parse_hhmm(value, operation, mission):
    raw = str(value or "").strip()
    if not raw: return None
    if not _HHMM.fullmatch(raw) or int(raw[:2]) > 23 or int(raw[2:]) > 59: raise NeoSubZeroPretreatError("Times must use valid four-digit HHMM values.")
    local = sort_datetime_for_local_time(operation.sort_date, operation.sort_name, time(int(raw[:2]), int(raw[2:])))
    timezone = ZoneInfo(mission.timezone or "America/Chicago")
    return local.replace(tzinfo=timezone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _validate_sequence(state):
    values = [state.pass1_started_at_utc, state.pass1_ended_at_utc, state.pass2_started_at_utc, state.pass2_ended_at_utc]
    for index in range(1, len(values)):
        if values[index] is not None and values[index - 1] is None: raise NeoSubZeroPretreatError("Complete Pretreat milestones in pass order.")
        if values[index] is not None and values[index] < values[index - 1]: raise NeoSubZeroPretreatError("Pretreat times must remain chronological.")


def _sync_completion(operation, tail, state):
    row = SortDateTailState.query.filter_by(sort_date=operation.sort_date, gateway_code=operation.gateway_code, sort_name=operation.sort_name, tail_number=tail).one_or_none()
    completed = bool(state.pass1_started_at_utc and state.pass1_ended_at_utc and state.pass2_started_at_utc and state.pass2_ended_at_utc)
    if row is None and completed:
        row = SortDateTailState(sort_date=operation.sort_date, gateway_code=operation.gateway_code, sort_name=operation.sort_name, tail_number=tail)
        db.session.add(row)
    if row is not None:
        row.pretreat_status = completed


def _surface(value):
    value = str(value or "").strip().lower()
    if value not in SURFACE_AREAS: raise NeoSubZeroPretreatError("Choose a valid Surface Area for both passes.")
    return value

def _display(state, attribute, mission):
    return format_local_hhmm(getattr(state, attribute, None), mission.timezone or None) if state else ""

def _tail(value): return str(value or "").strip().upper()

def _ground_time(start, end):
    if not start or not end or end < start: return "-"
    minutes = int((end - start).total_seconds() // 60); return f"{minutes // 60}:{minutes % 60:02d}"

def _aggregate(source, model, timestamp, *criteria):
    return select(literal(source).label("source"), func.count(model.id).label("row_count"), func.max(model.id).label("max_id"), func.max(timestamp).label("latest")).where(*criteria)
