"""Read-only, four-column projection of the current Dispatch missions."""

import hashlib
import json
from datetime import datetime

from app.models import SortDateMission, SortDateParkingAssignment
from app.services.neoscorpion import departure_mission_query
from app.services.neoscorpion_fueling_status import utc_naive


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
        groups[group].append((
            row["tail_number"],
            row["destination"],
            row["parking_position"],
            row["departure_time"] or "—",
        ))
    return groups


def sort_missions_revision(operation):
    """Detect schedule and parking edits even when the asset revision stays fixed."""
    mission = SortDateMission
    parking = SortDateParkingAssignment
    departures = departure_mission_query(operation).with_entities(
        mission.id, mission.assigned_tail_number, mission.destination,
        mission.eta_datetime_utc, mission.planned_datetime_utc, mission.timezone, mission.fuel_status,
    ).order_by(mission.id).all()
    positions = parking.query.filter_by(sort_date_operation_id=operation.id).with_entities(
        parking.tail_number, parking.ramp_code, parking.position_code, parking.lane_number,
    ).order_by(parking.id).all()
    inputs = [[tuple(row) for row in rows] for rows in (departures, positions)]
    return hashlib.sha256(json.dumps(inputs, default=str).encode()).hexdigest()
