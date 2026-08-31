from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, literal, or_, select, union_all

from app.extensions import db
from app.models import (
    Gateway,
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState,
    NeoSubZeroSetting,
    SortDateMission,
    SortDateParkingAssignment,
    SortDateTailState,
)
from app.neonodes.neosubzero.services import (
    SURFACE_LABELS,
    NeoSubZeroPretreatError,
    _parse_hhmm,
    _tail,
)
from app.services.live_collaboration import entity_version
from app.services.parking_plan import (
    tail_operational_status,
    tail_operational_status_label,
)
from app.services.sort_date_operations import mission_display_timing_data
from app.services.time_display import format_local_hhmm


OUTBOUND_REFRESH_KEY = "neosubzero.outbound"
COORDINATOR_REFRESH_KEY = "neosubzero.coordinator"
RAMP_ORDER = ("Remote", "Alpha", "Bravo", "Charlie", "Delta", "Echo")
PLAN_LABELS = {
    "one_type_i": "1x Type I",
    "two_type_i": "2x Type I",
    "type_i_type_iv": "Type I + Type IV",
}
PLAN_PASS_TYPES = {
    "one_type_i": ("Type I",),
    "two_type_i": ("Type I", "Type I"),
    "type_i_type_iv": ("Type I", "Type IV"),
}
TERMINAL_STATUSES = {"cleared", "negative", "not_sprayed"}
DEFAULT_TYPE_I_FLUID = "Type I"
DEFAULT_TYPE_I_CONCENTRATION = 50
DEFAULT_TYPE_IV_FLUID = "Type IV"


class NeoSubZeroDepartureDeiceError(ValueError):
    """Safe operator-facing departure-deice validation error."""


@dataclass(frozen=True)
class NeoSubZeroFluidSettings:
    type_i_fluid_name: str
    type_i_concentration_percent: int
    type_iv_fluid_name: str
    persisted: NeoSubZeroSetting | None = None


def neosubzero_fluid_settings(gateway):
    row = NeoSubZeroSetting.query.filter_by(gateway_id=gateway.id).one_or_none()
    return NeoSubZeroFluidSettings(
        type_i_fluid_name=(row.type_i_fluid_name if row else DEFAULT_TYPE_I_FLUID),
        type_i_concentration_percent=(
            row.type_i_concentration_percent
            if row
            else DEFAULT_TYPE_I_CONCENTRATION
        ),
        type_iv_fluid_name=(row.type_iv_fluid_name if row else DEFAULT_TYPE_IV_FLUID),
        persisted=row,
    )


def set_neosubzero_fluid_settings(
    gateway,
    type_i_fluid_name,
    type_i_concentration_percent,
    type_iv_fluid_name,
):
    type_i_name = _required_short_text(type_i_fluid_name, "Type I fluid name")
    type_iv_name = _required_short_text(type_iv_fluid_name, "Type IV fluid name")
    try:
        concentration = int(str(type_i_concentration_percent).strip())
    except (TypeError, ValueError) as exc:
        raise NeoSubZeroDepartureDeiceError(
            "Type I concentration must be a whole percentage."
        ) from exc
    if concentration < 1 or concentration > 100:
        raise NeoSubZeroDepartureDeiceError(
            "Type I concentration must be between 1 and 100 percent."
        )
    locked_gateway = Gateway.query.filter_by(id=gateway.id).with_for_update().one_or_none()
    if locked_gateway is None:
        raise NeoSubZeroDepartureDeiceError("Choose an existing gateway.")
    row = (
        NeoSubZeroSetting.query.filter_by(gateway_id=locked_gateway.id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = NeoSubZeroSetting(gateway_id=locked_gateway.id)
        db.session.add(row)
    row.type_i_fluid_name = type_i_name
    row.type_i_concentration_percent = concentration
    row.type_iv_fluid_name = type_iv_name
    return row


def departure_deice_context(gateway, operation, *, now_utc=None):
    settings = neosubzero_fluid_settings(gateway)
    if operation is None:
        return {"operation": None, "rows": [], "fluid_settings": settings}

    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .filter(
            or_(
                SortDateMission.departure_status.is_(None),
                SortDateMission.departure_status != "cancelled",
            )
        )
        .all()
    )
    parking_rows = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id
    ).all()
    parking_by_tail = {
        _tail(row.tail_number): row for row in parking_rows if _tail(row.tail_number)
    }
    tail_states = {
        _tail(row.tail_number): row
        for row in SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
    }
    pretreat_states = {
        _tail(row.tail_number): row
        for row in NeoSubZeroPretreatState.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }
    events = {
        row.sort_date_mission_id: row
        for row in NeoSubZeroDepartureDeiceEvent.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }
    now_utc = now_utc or datetime.utcnow()
    rows = [
        _departure_row(
            mission,
            operation,
            parking_by_tail.get(_tail(mission.assigned_tail_number)),
            tail_states.get(_tail(mission.assigned_tail_number)),
            pretreat_states.get(_tail(mission.assigned_tail_number)),
            events.get(mission.id),
            settings,
            now_utc,
        )
        for mission in missions
    ]
    rows.sort(
        key=lambda row: (
            row["sort_time"] is None,
            row["sort_time"] or datetime.max,
            row["flight"],
            row["mission_id"],
        )
    )
    return {"operation": operation, "rows": rows, "fluid_settings": settings}


def mutate_departure_deice(operation, mission, action, values, *, event=None):
    _validate_mission(operation, mission)
    action = str(action or "").strip().lower()
    if action in {"initial_contact", "move_to_planned"}:
        event = event or _new_event(operation, mission)
        _reset_for_new_departure_event(event)
        event.status = "deice_planned"
    elif action == "set_negative":
        event = event or _new_event(operation, mission)
        _clear_treatment(event)
        event.configured_at_utc = None
        event.status = "negative"
    elif action == "manual_not_sprayed":
        event = event or _new_event(operation, mission)
        _clear_treatment(event)
        event.configured_at_utc = None
        event.status = "not_sprayed"
    else:
        if event is None:
            raise NeoSubZeroDepartureDeiceError(
                "Record Initial Contact before editing departure deice."
            )
        if action == "toggle_configured":
            if event.status in TERMINAL_STATUSES:
                raise NeoSubZeroDepartureDeiceError(
                    "Move this mission to Deice Planned before configuring it."
                )
            event.configured_at_utc = (
                None
                if event.configured_at_utc
                else datetime.utcnow().replace(second=0, microsecond=0)
            )
            _derive_active_status(event)
        elif action == "save_treatment":
            if event.status in {"negative", "not_sprayed"}:
                raise NeoSubZeroDepartureDeiceError(
                    "Move this mission to Deice Planned before entering treatment."
                )
            _save_treatment(operation, mission, event, values)
        elif action == "clear":
            if event.status != "finished":
                raise NeoSubZeroDepartureDeiceError(
                    "Clear is available only after treatment is Finished."
                )
            event.status = "cleared"
        else:
            raise NeoSubZeroDepartureDeiceError("Choose a valid departure-deice action.")

    _sync_tail_deice_status(operation, mission, event)
    return event


def departure_deice_revision(gateway, operation):
    operation_id = operation.id if operation else None
    mission_ids = select(SortDateMission.id).where(
        SortDateMission.sort_date_operation_id == operation_id,
        SortDateMission.mission_type == "departure",
    )
    aggregates = (
        _aggregate(
            "missions",
            SortDateMission,
            SortDateMission.updated_at,
            SortDateMission.id.in_(mission_ids),
        ),
        _aggregate(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.updated_at,
            SortDateParkingAssignment.sort_date_operation_id == operation_id,
        ),
        _aggregate(
            "tails",
            SortDateTailState,
            SortDateTailState.updated_at,
            SortDateTailState.sort_date == (operation.sort_date if operation else None),
            SortDateTailState.gateway_code == gateway.code,
            SortDateTailState.sort_name
            == (operation.sort_name if operation else "night"),
        ),
        _aggregate(
            "pretreat",
            NeoSubZeroPretreatState,
            NeoSubZeroPretreatState.updated_at,
            NeoSubZeroPretreatState.sort_date_operation_id == operation_id,
        ),
        _aggregate(
            "departure_deice",
            NeoSubZeroDepartureDeiceEvent,
            NeoSubZeroDepartureDeiceEvent.updated_at,
            NeoSubZeroDepartureDeiceEvent.sort_date_operation_id == operation_id,
        ),
        _aggregate(
            "settings",
            NeoSubZeroSetting,
            NeoSubZeroSetting.updated_at,
            NeoSubZeroSetting.gateway_id == gateway.id,
        ),
    )
    values = db.session.execute(union_all(*aggregates)).all()
    payload = [
        (row.source, int(row.row_count or 0), int(row.max_id or 0), str(row.latest or ""))
        for row in values
    ]
    threshold_states = db.session.execute(
        select(
            SortDateMission.id,
            SortDateMission.actual_block_out_datetime_utc,
        ).where(
            SortDateMission.sort_date_operation_id == operation_id,
            SortDateMission.mission_type == "departure",
            SortDateMission.actual_block_out_datetime_utc.is_not(None),
        )
    ).all()
    now_utc = datetime.utcnow()
    payload.append(
        (
            "not_sprayed_clock",
            tuple(
                (row.id, now_utc >= row.actual_block_out_datetime_utc + timedelta(minutes=5))
                for row in threshold_states
            ),
        )
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _departure_row(
    mission,
    operation,
    parking,
    tail_state,
    pretreat,
    event,
    settings,
    now_utc,
):
    timing = mission_display_timing_data(mission, operation)
    adjusted = timing.get("adjusted_planned_departure_time")
    base_local = timing.get("base_planned_departure_time")
    display_time = adjusted or base_local
    time_label = "ETD" if adjusted and base_local and adjusted != base_local else "STD"
    tail = _tail(mission.assigned_tail_number)
    pretreat_complete = bool(tail_state and tail_state.pretreat_status)
    collapse_state = _collapse_state(
        mission,
        event,
        tail_state=tail_state,
        pretreat_complete=pretreat_complete,
        now_utc=now_utc,
    )
    plan_types = PLAN_PASS_TYPES.get(getattr(event, "treatment_plan", None), ())
    final_pass_index = len(plan_types)
    final_type = plan_types[-1] if plan_types else ""
    final_start = (
        event.pass2_started_at_utc
        if event and final_pass_index == 2
        else getattr(event, "pass1_started_at_utc", None)
    )
    final_end = (
        event.pass2_ended_at_utc
        if event and final_pass_index == 2
        else getattr(event, "pass1_ended_at_utc", None)
    )
    fluid = _fluid_reference(final_type, settings)
    pretreat_reference = _pretreat_reference(pretreat, mission, settings)
    pretreat_status = "-"
    if pretreat_complete:
        pretreat_status = "PRETREATED"
    elif pretreat and (
        pretreat.pass1_started_at_utc
        or pretreat.pass1_ended_at_utc
        or pretreat.pass2_started_at_utc
        or pretreat.pass2_ended_at_utc
    ):
        pretreat_status = "PRETREAT IN PROGRESS"
    elif pretreat and pretreat.pretreat_planned:
        pretreat_status = "PRETREAT PLANNED"
    script = None
    if event and event.status == "cleared" and final_start:
        script = {
            "flight": mission.flight_number,
            "tail": tail,
            "fluid": fluid["name"],
            "concentration": fluid["concentration"],
            "start": format_local_hhmm(final_start, mission.timezone or None),
            "text": (
                f"{mission.flight_number} deice complete: {fluid['concentration']}% "
                f"{fluid['name']}, Start Time "
                f"{format_local_hhmm(final_start, mission.timezone or None).replace(':', '')} local."
            ),
        }
    block_out = mission.actual_block_out_datetime_utc
    return {
        "mission_id": mission.id,
        "flight": str(mission.flight_number or "").strip(),
        "tail": tail or "-",
        "destination": str(mission.destination or "").strip().upper() or "-",
        "parking": str(getattr(parking, "position_code", "") or "").strip() or "TBD",
        "ramp": _ramp_name(getattr(parking, "ramp_code", None)),
        "departure_time": _format_local_time_value(display_time, mission.timezone),
        "departure_time_label": time_label,
        "tail_status": tail_operational_status_label(
            tail_operational_status(tail_state, assignment=parking)
        )
        or "NORMAL",
        "ramp_load_complete": format_local_hhmm(
            mission.ramp_load_completed_at_utc, mission.timezone or None
        ),
        "crew_load_complete": format_local_hhmm(
            mission.crew_load_completed_at_utc, mission.timezone or None
        ),
        "block_out": format_local_hhmm(block_out, mission.timezone or None),
        "block_out_variance": _variance(mission.planned_datetime_utc, block_out),
        "pretreat_complete": pretreat_complete,
        "pretreat_status": pretreat_status,
        "pretreat_reference": pretreat_reference,
        "deice_status": _status_label(event, collapse_state, tail_state),
        "event": event,
        "event_version": entity_version(event),
        "configured": format_local_hhmm(
            getattr(event, "configured_at_utc", None), mission.timezone or None
        ),
        "plan": getattr(event, "treatment_plan", None),
        "plan_label": PLAN_LABELS.get(getattr(event, "treatment_plan", None), ""),
        "pass_types": plan_types,
        "pass1_surface": getattr(event, "pass1_surface_area", None),
        "pass2_surface": getattr(event, "pass2_surface_area", None),
        "pass1_start": _event_time(event, "pass1_started_at_utc", mission),
        "pass1_end": _event_time(event, "pass1_ended_at_utc", mission),
        "pass2_start": _event_time(event, "pass2_started_at_utc", mission),
        "pass2_end": _event_time(event, "pass2_ended_at_utc", mission),
        "deice_minutes": _duration_minutes(
            getattr(event, "pass1_started_at_utc", None), final_end
        ),
        "final_fluid": fluid,
        "final_start": format_local_hhmm(final_start, mission.timezone or None),
        "collapse_state": collapse_state,
        "terminal": collapse_state in {
            "cleared",
            "pretreated",
            "negative",
            "not_sprayed",
        },
        "script": script,
        "sort_time": mission.planned_datetime_utc,
    }


def _save_treatment(operation, mission, event, values):
    plan = str(values.get("treatment_plan") or "").strip().lower()
    pass_types = PLAN_PASS_TYPES.get(plan)
    if not pass_types:
        raise NeoSubZeroDepartureDeiceError("Choose a valid treatment plan.")
    event.treatment_plan = plan
    event.pass1_surface_area = _surface(values.get("pass1_surface_area"), "Pass 1")
    event.pass1_started_at_utc = _departure_hhmm(
        values.get("pass1_start"), operation, mission
    )
    event.pass1_ended_at_utc = _departure_hhmm(
        values.get("pass1_end"), operation, mission
    )
    if len(pass_types) == 2:
        event.pass2_surface_area = _surface(
            values.get("pass2_surface_area"), "Pass 2"
        )
        event.pass2_started_at_utc = _departure_hhmm(
            values.get("pass2_start"), operation, mission
        )
        event.pass2_ended_at_utc = _departure_hhmm(
            values.get("pass2_end"), operation, mission
        )
    else:
        event.pass2_surface_area = None
        event.pass2_started_at_utc = None
        event.pass2_ended_at_utc = None
    _validate_chronology(event, len(pass_types))
    _derive_active_status(event)


def _derive_active_status(event):
    pass_count = len(PLAN_PASS_TYPES.get(event.treatment_plan, ()))
    final_end = event.pass1_ended_at_utc if pass_count == 1 else event.pass2_ended_at_utc
    if final_end:
        event.status = "finished"
    elif event.configured_at_utc:
        event.status = "configured"
    else:
        event.status = "deice_planned"


def _validate_chronology(event, pass_count):
    values = [event.pass1_started_at_utc, event.pass1_ended_at_utc]
    if pass_count == 2:
        values.extend([event.pass2_started_at_utc, event.pass2_ended_at_utc])
    for index in range(1, len(values)):
        if values[index] is not None and values[index - 1] is None:
            raise NeoSubZeroDepartureDeiceError(
                "Complete departure treatment milestones in pass order."
            )
        if values[index] is not None and values[index] < values[index - 1]:
            raise NeoSubZeroDepartureDeiceError(
                "Departure treatment times must remain chronological."
            )


def _sync_tail_deice_status(operation, mission, event):
    tail = _tail(mission.assigned_tail_number)
    row = (
        SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            tail_number=tail,
        )
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        row = SortDateTailState(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            tail_number=tail,
        )
        db.session.add(row)
    row.deice_status = {
        "deice_planned": "required",
        "configured": "configured",
        "finished": "configured",
        "cleared": "cleared",
        "negative": "negative",
        "not_sprayed": "unknown",
    }[event.status]
    row.deice_completed_at_utc = (
        event.pass2_ended_at_utc or event.pass1_ended_at_utc
        if event.status == "cleared"
        else None
    )


def _collapse_state(mission, event, *, tail_state, pretreat_complete, now_utc):
    if event and event.status in {"cleared", "negative", "not_sprayed"}:
        return event.status
    if event:
        return "active"
    if pretreat_complete:
        return "pretreated"
    canonical_deice = str(
        getattr(tail_state, "deice_status", "unknown") or "unknown"
    ).strip().lower()
    if canonical_deice == "negative":
        return "negative"
    if canonical_deice in {"required", "configured", "cleared"}:
        return "active"
    block_out = mission.actual_block_out_datetime_utc
    if block_out and now_utc >= block_out + timedelta(minutes=5):
        return "not_sprayed"
    return "active"


def _pretreat_reference(pretreat, mission, settings):
    if pretreat is None:
        return None
    pass2 = bool(pretreat.pass2_started_at_utc)
    start = pretreat.pass2_started_at_utc or pretreat.pass1_started_at_utc
    if not start:
        return None
    return {
        "fluid": (
            settings.type_iv_fluid_name if pass2 else settings.type_i_fluid_name
        ),
        "concentration": (
            100 if pass2 else settings.type_i_concentration_percent
        ),
        "surface": SURFACE_LABELS.get(
            pretreat.pass2_surface_area if pass2 else pretreat.pass1_surface_area,
            "",
        ),
        "start": format_local_hhmm(start, mission.timezone or None),
    }


def _fluid_reference(pass_type, settings):
    if pass_type == "Type IV":
        return {
            "name": settings.type_iv_fluid_name,
            "concentration": 100,
            "type": "Type IV",
        }
    if pass_type == "Type I":
        return {
            "name": settings.type_i_fluid_name,
            "concentration": settings.type_i_concentration_percent,
            "type": "Type I",
        }
    return {"name": "", "concentration": None, "type": ""}


def _new_event(operation, mission):
    event = NeoSubZeroDepartureDeiceEvent(
        sort_date_operation_id=operation.id,
        sort_date_mission_id=mission.id,
        tail_number=_tail(mission.assigned_tail_number),
        status="deice_planned",
    )
    db.session.add(event)
    return event


def _reset_for_new_departure_event(event):
    _clear_treatment(event)
    event.configured_at_utc = None


def _clear_treatment(event):
    event.treatment_plan = None
    event.pass1_surface_area = None
    event.pass1_started_at_utc = None
    event.pass1_ended_at_utc = None
    event.pass2_surface_area = None
    event.pass2_started_at_utc = None
    event.pass2_ended_at_utc = None


def _validate_mission(operation, mission):
    if operation is None or mission is None:
        raise NeoSubZeroDepartureDeiceError("Choose a current departure mission.")
    if (
        mission.sort_date_operation_id != operation.id
        or mission.mission_type != "departure"
        or not _tail(mission.assigned_tail_number)
    ):
        raise NeoSubZeroDepartureDeiceError(
            "Departure deice applies only to a current mission with an assigned tail."
        )


def _surface(value, label):
    normalized = str(value or "").strip().lower()
    if normalized not in SURFACE_LABELS:
        raise NeoSubZeroDepartureDeiceError(f"Choose a valid {label} Surface Area.")
    return normalized


def _event_time(event, attribute, mission):
    return (
        format_local_hhmm(getattr(event, attribute, None), mission.timezone or None)
        if event
        else ""
    )


def _variance(scheduled, actual):
    if not scheduled or not actual:
        return "-"
    minutes = int((actual - scheduled).total_seconds() / 60)
    return f"+{minutes}" if minutes > 0 else str(minutes)


def _duration_minutes(start, end):
    if not start or not end or end < start:
        return None
    return int((end - start).total_seconds() / 60)


def _format_local_time_value(value, timezone_name):
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M")
    return "-"


def _status_label(event, collapse_state, tail_state):
    if collapse_state == "pretreated":
        return "PRETREATED"
    if collapse_state == "not_sprayed":
        return "NOT SPRAYED"
    if event is None:
        canonical = str(getattr(tail_state, "deice_status", "") or "").strip()
        if canonical and canonical != "unknown":
            return canonical.replace("_", " ").upper()
        return "UNTREATED"
    if event.status == "finished":
        return "FINISHED / AWAITING CLEARANCE"
    return event.status.replace("_", " ").upper()


def _ramp_name(value):
    normalized = str(value or "").strip().upper()
    mapping = {
        "R": "Remote",
        "REMOTE": "Remote",
        "A": "Alpha",
        "ALPHA": "Alpha",
        "B": "Bravo",
        "BRAVO": "Bravo",
        "C": "Charlie",
        "CHARLIE": "Charlie",
        "D": "Delta",
        "DELTA": "Delta",
        "E": "Echo",
        "ECHO": "Echo",
    }
    return mapping.get(normalized, "")


def _required_short_text(value, label):
    normalized = str(value or "").strip()
    if not normalized:
        raise NeoSubZeroDepartureDeiceError(f"{label} is required.")
    if len(normalized) > 80:
        raise NeoSubZeroDepartureDeiceError(f"{label} must be 80 characters or fewer.")
    return normalized


def _departure_hhmm(value, operation, mission):
    try:
        return _parse_hhmm(value, operation, mission)
    except NeoSubZeroPretreatError as exc:
        raise NeoSubZeroDepartureDeiceError(str(exc)) from exc


def _aggregate(source, model, timestamp, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.max(timestamp).label("latest"),
    ).where(*criteria)
