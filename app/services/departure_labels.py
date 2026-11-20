"""Presentation of outbound occurrences within one canonical Sort."""
from collections import defaultdict
from datetime import datetime, timedelta

from app.extensions import db
from app.models import SortDateMission
from app.services.mission_identity import flight_identity
from app.services.request_cache import request_cached
from app.services.sort_date_operations import _planned_datetime_utc, effective_window_minutes_for_mission


def effective_departure_utc(mission, operation):
    if mission.eta_datetime_utc:
        return mission.eta_datetime_utc
    planned = mission.planned_datetime_utc
    if planned is None and mission.planned_datetime_local:
        planned = _planned_datetime_utc(mission.planned_datetime_local, mission.timezone)
    if planned:
        return planned + timedelta(
            minutes=effective_window_minutes_for_mission(mission, operation) or 0)
    return None


def departure_order(mission, operation):
    etd = effective_departure_utc(mission, operation)
    return (etd is None, etd or datetime.max, flight_identity(mission.flight_number), mission.id)


def departure_occurrences(missions, operation):
    groups = defaultdict(list)
    for mission in missions:
        if mission.mission_type == "departure" and mission.destination and mission.departure_status != "cancelled":
            groups[mission.destination.strip().upper()].append(mission)
    return {m.id: rank for group in groups.values() if len(group) > 1
            for rank, m in enumerate(sorted(group, key=lambda m: departure_order(m, operation)), 1)}


def destination_label(destination, rank):
    if rank is None:
        return destination
    suffix = "th" if 11 <= rank % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
    return f"{rank}{suffix} {destination}"


def operation_departure_occurrences(operation):
    def load():
        m = SortDateMission
        rows = db.session.query(m.id, m.mission_type, m.destination, m.flight_number, m.wave,
                                m.departure_status, m.eta_datetime_utc, m.planned_datetime_utc, m.planned_datetime_local, m.timezone
                                ).filter_by(sort_date_operation_id=operation.id,
                                            mission_type="departure", master_retirement=None).all()
        return departure_occurrences(rows, operation)
    return request_cached("departure.occurrences", operation.id, load)


def mission_destination_label(mission):
    if mission.mission_type != "departure" or not mission.id or not mission.sort_date_operation_id:
        return mission.destination
    ranks = operation_departure_occurrences(mission.sort_date_operation)
    return destination_label(mission.destination, ranks.get(mission.id))
