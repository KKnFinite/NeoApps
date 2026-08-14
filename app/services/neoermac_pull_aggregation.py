from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_

from app.models import NeoErmacDoorPull, SortDateMission, SortDateOperation
from app.services.gateway_matrix import gateway_timezone
from app.services.neoermac_building_lineup import (
    get_building_lineup_doors_by_destination,
    normalize_destination,
)
from app.services.night_sorting import sort_datetime_for_local_time


PULL_AGGREGATION_FIELDS = (
    (
        "pure_pull_time_local",
        "actual_pure_pull_time_local",
        "no_pure_pull",
    ),
    (
        "mix_pull_time_local",
        "actual_mix_pull_time_local",
        "no_mix_pull",
    ),
)

STRONGER_DEPARTURE_STATUSES = {
    "ramp_load_complete",
    "crew_load_complete",
    "blocked_out",
    "departed",
    "cancelled",
}


def recompute_current_sort_door_pull_aggregates(
    gateway,
    operation=None,
    destinations=None,
    *,
    doors_by_destination=None,
    missions_by_destination=None,
    pulls_by_destination_and_door=None,
):
    operation = operation or _current_operation(gateway)
    if not operation:
        return {}

    requested_destinations = {
        normalize_destination(destination)
        for destination in (destinations or ())
        if normalize_destination(destination)
    }
    if doors_by_destination is None:
        doors_by_destination = get_building_lineup_doors_by_destination(gateway)
    if missions_by_destination is None:
        missions_by_destination = _departure_missions_by_destination(operation)
    if requested_destinations:
        missions_by_destination = {
            destination: mission
            for destination, mission in missions_by_destination.items()
            if destination in requested_destinations
        }

    if pulls_by_destination_and_door is None:
        pulls_by_destination_and_door = _door_pulls_by_destination_and_door(
            gateway,
            operation,
        )
    results = {}
    for destination, mission in missions_by_destination.items():
        results[destination] = _recompute_mission(
            gateway,
            operation,
            mission,
            doors_by_destination.get(destination, ()),
            pulls_by_destination_and_door,
        )
    return results


def _recompute_mission(
    gateway,
    operation,
    mission,
    assigned_doors,
    pulls_by_destination_and_door,
):
    destination = normalize_destination(mission.destination)
    requirement_results = []
    completed_actual_datetimes = []
    aggregate_values = {}

    for planned_attr, actual_attr, no_attr in PULL_AGGREGATION_FIELDS:
        door_pulls = [
            pulls_by_destination_and_door.get((destination, door))
            for door in assigned_doors
        ]
        actual_values = [
            getattr(door_pull, actual_attr, None)
            for door_pull in door_pulls
            if door_pull is not None
            and not bool(getattr(door_pull, no_attr, False))
            and getattr(door_pull, actual_attr, None) is not None
        ]
        latest_actual = _latest_actual_time(operation, actual_values)
        setattr(mission, actual_attr, latest_actual)
        aggregate_values[actual_attr] = latest_actual

        if getattr(mission, planned_attr, None) is None:
            continue

        for door_pull in door_pulls:
            requirement_results.append(
                bool(
                    door_pull is not None
                    and (
                        getattr(door_pull, actual_attr, None) is not None
                        or bool(getattr(door_pull, no_attr, False))
                    )
                )
            )

        completed_actual_datetimes.extend(
            _actual_local_datetime(operation, value)
            for value in actual_values
        )

    pulls_complete = bool(requirement_results) and all(requirement_results)
    stronger_progress = _has_stronger_departure_progress(mission)
    status = _normalized_status(mission.departure_status)

    if pulls_complete and not stronger_progress:
        mission.departure_status = "last_uld_enroute"
        latest_actual_local = (
            max(completed_actual_datetimes) if completed_actual_datetimes else None
        )
        mission.last_uld_enroute_at_utc = _local_datetime_to_utc(
            latest_actual_local,
            gateway_timezone(gateway),
        )
    elif (
        not pulls_complete
        and not stronger_progress
        and status == "last_uld_enroute"
    ):
        mission.departure_status = "scheduled"
        mission.last_uld_enroute_at_utc = None

    return {
        "assigned_doors": tuple(assigned_doors),
        "pulls_complete": pulls_complete,
        "actual_pure_pull_time_local": aggregate_values.get(
            "actual_pure_pull_time_local"
        ),
        "actual_mix_pull_time_local": aggregate_values.get(
            "actual_mix_pull_time_local"
        ),
        "last_uld_enroute_at_utc": mission.last_uld_enroute_at_utc,
    }


def _departure_missions_by_destination(operation):
    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )
    result = {}
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if destination and destination not in result:
            result[destination] = mission
    return result


def _door_pulls_by_destination_and_door(gateway, operation):
    rows = (
        NeoErmacDoorPull.query.filter_by(
            gateway_id=gateway.id,
            sort_date_operation_id=operation.id,
        )
        .order_by(NeoErmacDoorPull.updated_at.desc(), NeoErmacDoorPull.id.desc())
        .all()
    )
    result = {}
    for row in rows:
        destination = normalize_destination(row.destination)
        door = str(row.door or "").strip().upper()
        if destination and door:
            result.setdefault((destination, door), row)
    return result


def _latest_actual_time(operation, values):
    if not values:
        return None
    return max(values, key=lambda value: _actual_local_datetime(operation, value))


def _actual_local_datetime(operation, value):
    return sort_datetime_for_local_time(
        operation.sort_date,
        operation.sort_name,
        value,
    )


def _local_datetime_to_utc(value, timezone_name):
    if value is None:
        return None
    try:
        localized = value.replace(tzinfo=ZoneInfo(timezone_name))
        return localized.astimezone(timezone.utc).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        return value


def _has_stronger_departure_progress(mission):
    if _normalized_status(mission.departure_status) in STRONGER_DEPARTURE_STATUSES:
        return True
    return any(
        (
            mission.ramp_load_completed_at_utc,
            mission.crew_load_completed_at_utc,
            mission.actual_block_out_datetime_utc,
        )
    )


def _normalized_status(value):
    return str(value or "").strip().lower()


def _current_operation(gateway):
    return (
        SortDateOperation.query.filter(
            SortDateOperation.archived_at_utc.is_(None),
            or_(
                SortDateOperation.gateway_id == gateway.id,
                SortDateOperation.gateway_code == gateway.code,
            ),
        )
        .order_by(
            SortDateOperation.sort_date.desc(),
            SortDateOperation.generated_at_utc.desc(),
            SortDateOperation.id.desc(),
        )
        .first()
    )
