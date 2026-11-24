"""Read-only outbound roster shared by authorized vendor screens."""

import hashlib
import json
from datetime import datetime

from app.models import SortDateMission, SortDateParkingAssignment
from app.services.neoscorpion import departure_mission_query, fuel_dispatch_context
from app.services.neoscorpion_fueling_status import utc_naive


def outbound_missions(gateway, operation):
    if operation is None or operation.archived_at_utc:
        return None
    dispatch = fuel_dispatch_context(gateway, include_spear=False, display_nicknames=False)
    if not dispatch["operation"] or dispatch["operation"].id != operation.id:
        raise PermissionError("The current Sort changed. Reload the vendor workspace.")
    return group_missions(dispatch["rows"])


def group_missions(rows):
    groups = {"working": [], "left": [], "complete": []}

    def etd_key(row):
        mission = row["mission"]
        etd = utc_naive(mission.eta_datetime_utc or mission.planned_datetime_utc)
        return etd or datetime.max, mission.id

    for row in sorted(rows, key=etd_key):
        stage = row["dispatch_status_key"]
        if stage in {"complete", "fob"}:
            group = "complete"
        elif (
            stage in {"fueling", "off"}
            or row["is_off"]
            or row["work_ended_early"]
            or (stage == "review" and (row["work_has_begun"] or row["end_early_available"]))
        ):
            group = "working"
        else:
            group = "left"
        mission = row["mission"]
        groups[group].append(dict(id=mission.id, flight=mission.flight_number,
                                  tail=row["tail_number"], destination=row["destination"],
                                  parking=row["parking_position"], etd=row["departure_time"] or "—",
                                  status=row["dispatch_status_label"]))
    return groups


def sort_missions_revision(operation):
    """Detect schedule and parking edits even when the asset revision stays fixed."""
    mission = SortDateMission
    parking = SortDateParkingAssignment
    departures = departure_mission_query(operation).with_entities(
        mission.id, mission.flight_number, mission.wave, mission.assigned_tail_number, mission.destination,
        mission.planned_datetime_local, mission.updated_at,
        mission.eta_datetime_utc, mission.planned_datetime_utc, mission.timezone, mission.fuel_status,
    ).order_by(mission.id).all()
    positions = parking.query.filter_by(sort_date_operation_id=operation.id).with_entities(
        parking.tail_number, parking.ramp_code, parking.position_code, parking.lane_number,
    ).order_by(parking.id).all()
    arrivals = mission.query.filter_by(sort_date_operation_id=operation.id, mission_type='arrival').with_entities(
        mission.id, mission.updated_at, mission.assigned_tail_number, mission.arrival_status,
        mission.actual_block_in_datetime_utc, mission.api_status, mission.api_assumed_arrived_time_utc,
    ).order_by(mission.id).all()
    inputs = [[tuple(row) for row in rows] for rows in (departures, positions, arrivals)]
    return hashlib.sha256(json.dumps(inputs, default=str).encode()).hexdigest()
