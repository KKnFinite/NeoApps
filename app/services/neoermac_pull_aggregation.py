from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models import NeoErmacDoorPull, SortDateMission
from app.services.gateway_matrix import gateway_timezone
from app.services.neoermac_building_lineup import (
    get_building_lineup_doors_by_destination,
    normalize_destination,
)
from app.services.night_sorting import sort_datetime_for_local_time
from app.services.operation_scope import current_operational_sort_operation


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
    # ``missions_by_destination`` is retained as a compatibility argument for
    # callers that preloaded the Door View bundle.  Work is deliberately
    # performed per mission, not per destination: two OAK departures may have
    # the same destination and must never share a pull aggregate.
    if missions_by_destination is None:
        missions = _departure_missions(operation)
    else:
        missions = tuple(missions_by_destination.values())
    if requested_destinations:
        missions = tuple(
            mission
            for mission in missions
            if normalize_destination(mission.destination) in requested_destinations
        )

    if pulls_by_destination_and_door is None:
        pulls_by_mission_and_door = _door_pulls_by_mission_and_door(
            gateway,
            operation,
            missions,
        )
    else:
        pulls_by_mission_and_door = _mission_pull_lookup_from_legacy_bundle(
            pulls_by_destination_and_door,
            missions,
        )
    results = {}
    for mission in missions:
        results[mission.id] = _recompute_mission(
            gateway,
            operation,
            mission,
            doors_by_destination.get(normalize_destination(mission.destination), ()),
            pulls_by_mission_and_door,
        )
    return results


def _recompute_mission(
    gateway,
    operation,
    mission,
    assigned_doors,
    pulls_by_mission_and_door,
):
    requirement_results = []
    completed_actual_datetimes = []
    aggregate_values = {}

    for planned_attr, actual_attr, no_attr in PULL_AGGREGATION_FIELDS:
        door_pulls = [
            pulls_by_mission_and_door.get((mission.id, door))
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


def _departure_missions(operation):
    return tuple(
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )


def _door_pulls_by_mission_and_door(gateway, operation, missions):
    rows = (
        NeoErmacDoorPull.query.filter_by(
            gateway_id=gateway.id,
            sort_date_operation_id=operation.id,
        )
        .order_by(NeoErmacDoorPull.updated_at.desc(), NeoErmacDoorPull.id.desc())
        .all()
    )
    result = {}
    missions_by_destination = {}
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if destination:
            missions_by_destination.setdefault(destination, []).append(mission)
    for row in rows:
        door = str(row.door or "").strip().upper()
        if not door:
            continue
        mission_id = getattr(row, "sort_date_mission_id", None)
        if mission_id:
            result.setdefault((mission_id, door), row)
            continue
        # Legacy destination-only records can be used only when that
        # destination maps uniquely in this operation.  Ambiguous legacy
        # records are intentionally not guessed onto either mission.
        destination = normalize_destination(row.destination)
        candidates = missions_by_destination.get(destination, ())
        if len(candidates) == 1:
            result.setdefault((candidates[0].id, door), row)
    return result


def _mission_pull_lookup_from_legacy_bundle(records, missions):
    """Convert the older destination lookup without unsafe ambiguous reuse."""
    by_destination = {}
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if destination:
            by_destination.setdefault(destination, []).append(mission)
    result = {}
    for key, record in records.items():
        mission_id = getattr(record, "sort_date_mission_id", None)
        if mission_id:
            result.setdefault((mission_id, str(record.door or "").strip().upper()), record)
            continue
        if not isinstance(key, tuple) or len(key) != 2:
            continue
        destination, door = key
        candidates = by_destination.get(normalize_destination(destination), ())
        if len(candidates) == 1:
            result.setdefault((candidates[0].id, str(door or "").strip().upper()), record)
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
    return current_operational_sort_operation(gateway)
