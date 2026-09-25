"""Gap filling for a canonical Upcoming Pulls mission, across the current lineup."""
from app.extensions import db
from app.services.neoermac_building_lineup import normalize_destination
from app.services.neoermac_door_view import (
    DoorPullConflict, _door_pull_for_mission, _door_pull_record,
    _parse_optional_time, _pull_field_by_key, door_view_operational_state,
    locked_door_pull_operation,
)
from app.services.neoermac_dashboard import _planned_pull_time
from app.services.neoermac_pull_aggregation import recompute_current_sort_door_pull_aggregates


def fill_upcoming_pull(gateway, *, operation_id, mission_id, destination, pull_key, actual_pull):
    """Serialize with Door View/Lineup writers; never overwrite an accounted-for door.

    The caller authorizes both Upcoming Pulls view and Door View edit, and owns
    commit/rollback. No supervised-door preference participates in this save.
    """
    field = _pull_field_by_key(pull_key)
    if field is None:
        raise ValueError("Select PURE or MIX.")
    actual = _parse_optional_time(actual_pull)
    if actual is None:
        raise ValueError("Enter an actual HH:MM time.")
    try:
        operation_id, mission_id = int(operation_id), int(mission_id)
    except (TypeError, ValueError) as exc:
        raise DoorPullConflict("Pull identity is missing. Refresh Upcoming Pulls.") from exc
    destination = normalize_destination(destination)
    operation = locked_door_pull_operation(gateway)
    if operation is None or operation.id != operation_id:
        raise DoorPullConflict("The current sort changed. Refresh Upcoming Pulls.")
    bundle = door_view_operational_state(
        gateway, operation=operation, initialize_lineup=False, for_update=True,
    )
    mission = next((m for m in bundle.missions if m.id == mission_id and m.mission_type == "departure"), None)
    if mission is None or not destination or normalize_destination(mission.destination) != destination:
        raise DoorPullConflict("The mission changed. Refresh Upcoming Pulls.")
    doors = bundle.doors_by_destination.get(destination, ())
    if not doors or _planned_pull_time(mission, operation, field["key"]) is None:
        raise DoorPullConflict("This pull is no longer in the current lineup.")

    filled = 0
    for door in dict.fromkeys(doors):
        existing = _door_pull_for_mission(bundle, door, destination, mission)
        if existing is not None and (
            getattr(existing, field["actual_attr"]) is not None
            or getattr(existing, field["no_attr"])
        ):
            continue
        record = _door_pull_record(
            gateway, door, destination, operation, create=True, mission=mission, bundle=bundle,
        )
        setattr(record, field["actual_attr"], actual)
        filled += 1
    if filled:
        db.session.flush()
        recompute_current_sort_door_pull_aggregates(
            gateway, operation=operation, destinations=(destination,),
            doors_by_destination=bundle.doors_by_destination,
            missions_by_destination={destination: mission},
            all_operation_missions=bundle.missions, door_pull_records=bundle.door_pulls,
        )
    return filled
