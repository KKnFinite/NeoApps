"""Automatic SPEAR learning capture from committed NeoScorpion outcomes."""

from dataclasses import dataclass
from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelingEventTankSnapshot,
    NeoScorpionFuelTankState,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
)
from app.services.neoscorpion_learning_vault import export_learning_record


@dataclass(frozen=True)
class LearningCaptureResult:
    captured: bool
    reason: str
    key: str | None = None
    training_eligible: bool | None = None
    already_saved: bool = False


def capture_completed_learning_outcome(gateway, assignment_id):
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    if settings is None or not bool(settings.spear_learning_capture_enabled):
        return LearningCaptureResult(False, "learning_capture_off")

    assignment = db.session.get(NeoScorpionFuelAssignment, int(assignment_id))
    if assignment is None or assignment.sort_date_operation_id is None:
        return LearningCaptureResult(False, "assignment_not_found")
    mission = db.session.get(SortDateMission, assignment.sort_date_mission_id)
    operation = db.session.get(SortDateOperation, assignment.sort_date_operation_id)
    if mission is None or operation is None:
        return LearningCaptureResult(False, "canonical_record_missing")
    if (
        assignment.review_status != "complete"
        or mission.fuel_status != "complete"
        or (
            assignment.completed_at_utc is None
            and assignment.fuel_on_board_at_utc is None
        )
    ):
        return LearningCaptureResult(False, "assignment_not_terminal")

    record = build_completed_learning_record(gateway, operation, assignment, mission)
    saved = export_learning_record(record)
    return LearningCaptureResult(
        True,
        "captured",
        key=saved["key"],
        training_eligible=record["training_eligible"],
        already_saved=bool(saved["already_saved"]),
    )


def capture_current_sort_learning_outcomes(gateway, operation_id):
    assignment_ids = [
        row.id
        for row in NeoScorpionFuelAssignment.query.join(
            SortDateMission,
            SortDateMission.id == NeoScorpionFuelAssignment.sort_date_mission_id,
        ).filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation_id,
            NeoScorpionFuelAssignment.review_status == "complete",
            SortDateMission.fuel_status == "complete",
        ).all()
    ]
    return tuple(
        capture_completed_learning_outcome(gateway, assignment_id)
        for assignment_id in assignment_ids
    )


def build_completed_learning_record(gateway, operation, assignment, mission):
    tail = _tail(mission.assigned_tail_number)
    confirmed_tail = _tail(assignment.confirmed_tail_number) or tail

    work_states = (
        NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment.id)
        .order_by(NeoScorpionFuelWorkState.id)
        .all()
    )
    work_ids = [row.id for row in work_states]
    tank_states = (
        NeoScorpionFuelTankState.query.filter(
            NeoScorpionFuelTankState.fuel_work_state_id.in_(work_ids)
        ).order_by(
            NeoScorpionFuelTankState.fuel_work_state_id,
            NeoScorpionFuelTankState.tank_code,
        ).all()
        if work_ids else []
    )
    tanks_by_work = {}
    for tank_state in tank_states:
        tanks_by_work.setdefault(tank_state.fuel_work_state_id, []).append({
            "code": tank_state.tank_code,
            "remaining_lbs": tank_state.remaining_lbs,
            "actual_lbs": tank_state.actual_lbs,
        })

    events = (
        NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=assignment.id)
        .order_by(
            NeoScorpionFuelingEvent.cycle_number,
            NeoScorpionFuelingEvent.sequence_number,
        ).all()
    )
    event_ids = [event.id for event in events]
    snapshots = (
        NeoScorpionFuelingEventTankSnapshot.query.filter(
            NeoScorpionFuelingEventTankSnapshot.fueling_event_id.in_(event_ids)
        ).order_by(
            NeoScorpionFuelingEventTankSnapshot.fueling_event_id,
            NeoScorpionFuelingEventTankSnapshot.tank_code,
        ).all()
        if event_ids else []
    )
    tanks_by_event = {}
    for snapshot in snapshots:
        tanks_by_event.setdefault(snapshot.fueling_event_id, []).append({
            "code": snapshot.tank_code,
            "remaining_lbs": snapshot.remaining_lbs,
            "planned_lbs": snapshot.planned_lbs,
            "actual_lbs": snapshot.actual_lbs,
        })

    audit_actions = [
        row.action
        for row in NeoScorpionFuelAuditEntry.query.filter_by(
            fuel_assignment_id=assignment.id
        ).order_by(NeoScorpionFuelAuditEntry.created_at, NeoScorpionFuelAuditEntry.id).all()
    ]

    arrival = _matching_arrival(operation.id, tail, mission.planned_datetime_utc)
    parking = SortDateParkingAssignment.query.filter_by(
        sort_date_operation_id=operation.id,
        tail_number=tail,
    ).first()

    exclusion_reasons = []
    if assignment.operational_status != "active":
        exclusion_reasons.append("assignment_not_active")
    if confirmed_tail != tail:
        exclusion_reasons.append("tail_not_confirmed")
    if len(work_states) > 1:
        exclusion_reasons.append("multiple_tail_work_states")
    if any(row.ended_early_at_utc is not None for row in work_states):
        exclusion_reasons.append("ended_early")
    if audit_actions:
        exclusion_reasons.append("correction_or_interruption_history")

    is_fob = assignment.fuel_on_board_at_utc is not None
    if is_fob:
        if assignment.transfer_fuel_gallons not in (None, 0):
            exclusion_reasons.append("fob_with_transfer")
    else:
        current_work = next((row for row in work_states if _tail(row.tail_number) == tail), None)
        if current_work is None:
            exclusion_reasons.append("missing_current_tail_work")
        elif current_work.off_at_utc is None:
            exclusion_reasons.append("missing_off")
        if assignment.current_cycle_type == "defuel":
            exclusion_reasons.append("defuel_excluded_v1")
        if assignment.transfer_fuel_gallons and not events:
            exclusion_reasons.append("missing_fueling_event")

    for event in events:
        if event.started_at_utc is None or event.ended_at_utc is None:
            exclusion_reasons.append("incomplete_event_timing")
        if event.fueler_user_id is None:
            exclusion_reasons.append("missing_event_fueler")
        if event.transfer_fuel_gallons is None or event.transfer_fuel_gallons <= 0:
            exclusion_reasons.append("missing_event_transfer")
        if event.neo_fuel_lbs is None:
            exclusion_reasons.append("missing_event_fuel_measurement")
        if _tail(event.tail_number) != tail:
            exclusion_reasons.append("event_tail_mismatch")
        event_tanks = tanks_by_event.get(event.id, [])
        if not event_tanks or any(
            tank["remaining_lbs"] is None or tank["actual_lbs"] is None
            for tank in event_tanks
        ):
            exclusion_reasons.append("incomplete_event_tanks")

    exclusion_reasons = sorted(set(exclusion_reasons))
    terminal_at = assignment.fuel_on_board_at_utc or assignment.completed_at_utc

    return {
        "schema_version": "spear-learning/v1",
        "record_type": "completed_fuel_outcome",
        "captured_at_utc": _iso(terminal_at),
        "training_eligible": not exclusion_reasons,
        "exclusion_reasons": exclusion_reasons,
        "gateway": {"id": gateway.id, "code": str(gateway.code or "").upper()},
        "sort": {
            "operation_id": operation.id,
            "sort_date": str(operation.sort_date),
            "sort_name": str(operation.sort_name or ""),
        },
        "assignment": {
            "id": assignment.id,
            "cycle_type": str(assignment.current_cycle_type or "fuel"),
            "cycle_number": int(assignment.current_cycle_number or 1),
            "assigned_fueler_user_id": assignment.assigned_fueler_user_id,
            "assigned_truck_id": assignment.assigned_truck_id,
            "transfer_fuel_gallons": assignment.transfer_fuel_gallons,
            "ready_for_fuel_at_utc": _iso(assignment.ready_for_fuel_at_utc),
            "fuel_on_board_at_utc": _iso(assignment.fuel_on_board_at_utc),
            "completed_at_utc": _iso(assignment.completed_at_utc),
            "review_status": str(assignment.review_status or ""),
            "operational_status": str(assignment.operational_status or ""),
        },
        "mission": {
            "id": mission.id,
            "flight_number": str(mission.flight_number or ""),
            "origin": str(mission.origin or ""),
            "destination": str(mission.destination or ""),
            "tail_number": tail,
            "confirmed_tail_number": confirmed_tail,
            "planned_departure_utc": _iso(mission.planned_datetime_utc),
            "required_fuel_lbs": mission.planned_fuel_load,
            "fuel_status": str(mission.fuel_status or ""),
            "departure_status": str(mission.departure_status or ""),
            "api_aircraft_model": mission.api_aircraft_model,
        },
        "arrival": (
            {
                "mission_id": arrival.id,
                "flight_number": str(arrival.flight_number or ""),
                "status": str(
                    arrival.arrival_status
                    or arrival.api_status
                    or arrival.api_status_raw
                    or ""
                ),
                "eta_utc": _iso(arrival.eta_datetime_utc),
                "actual_block_in_utc": _iso(arrival.actual_block_in_datetime_utc),
                "api_assumed_arrived_utc": _iso(arrival.api_assumed_arrived_time_utc),
            }
            if arrival is not None else None
        ),
        "parking": (
            {
                "ramp": parking.ramp_code,
                "position": parking.position_code,
                "lane": parking.lane_number,
            }
            if parking is not None else None
        ),
        "work_states": [
            {
                "id": work.id,
                "tail_number": _tail(work.tail_number),
                "on_at_utc": _iso(work.on_at_utc),
                "off_at_utc": _iso(work.off_at_utc),
                "ended_early_at_utc": _iso(work.ended_early_at_utc),
                "apu_running": work.apu_running,
                "apu_allowance_lbs": work.apu_allowance_lbs,
                "apu_source_tank_code": work.apu_source_tank_code,
                "tanks": tanks_by_work.get(work.id, []),
            }
            for work in work_states
        ],
        "fuel_events": [
            {
                "id": event.id,
                "work_state_id": event.fuel_work_state_id,
                "event_type": str(event.event_type or "fuel"),
                "cycle_number": int(event.cycle_number or 1),
                "sequence_number": int(event.sequence_number),
                "tail_number": _tail(event.tail_number),
                "truck_id": event.fuel_truck_id,
                "fueler_user_id": event.fueler_user_id,
                "started_at_utc": _iso(event.started_at_utc),
                "ended_at_utc": _iso(event.ended_at_utc),
                "transfer_fuel_gallons": event.transfer_fuel_gallons,
                "required_fuel_lbs": event.required_fuel_lbs,
                "neo_fuel_lbs": event.neo_fuel_lbs,
                "apu_running": event.apu_running,
                "apu_allowance_lbs": event.apu_allowance_lbs,
                "apu_source_tank_code": event.apu_source_tank_code,
                "tanks": tanks_by_event.get(event.id, []),
            }
            for event in events
        ],
        "audit_actions": audit_actions,
    }


def _matching_arrival(operation_id, tail, departure_at):
    if not tail:
        return None
    arrivals = SortDateMission.query.filter_by(
        sort_date_operation_id=operation_id,
        mission_type="arrival",
        assigned_tail_number=tail,
    ).order_by(SortDateMission.id).all()
    if not arrivals:
        return None
    departure_at = _naive_utc(departure_at)
    eligible = [
        row for row in arrivals
        if departure_at is None
        or _arrival_time(row) is None
        or _arrival_time(row) <= departure_at
    ]
    return max(
        eligible or arrivals,
        key=lambda row: (_arrival_time(row) or datetime.min, row.id),
    )


def _arrival_time(mission):
    return _naive_utc(
        mission.actual_block_in_datetime_utc
        or mission.api_assumed_arrived_time_utc
        or mission.eta_datetime_utc
        or mission.planned_datetime_utc
    )


def _naive_utc(value):
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(value):
    value = _naive_utc(value)
    if value is None:
        return None
    return value.isoformat(timespec="microseconds") + "Z"


def _tail(value):
    return str(value or "").strip().upper()
