from dataclasses import dataclass

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    NeoErmacBuildingLineup,
    SortDateMission,
    SortDateOperation,
)
from app.services.sort_date_operations import mission_display_timing_data


OUTBOUND_DOOR_OPTIONS = (
    "D1",
    "D4",
    "D6",
    "D9",
    "D13",
    "D17",
    "D21",
    "D24",
    "D26",
    "D29",
    "D32",
    "D34",
    "D37",
)

BUILDING_LINEUP_BELT_GROUPS = (
    ("green_runout", OUTBOUND_DOOR_OPTIONS[0], OUTBOUND_DOOR_OPTIONS[1], ("WHT/BLU", "ORG")),
    ("runout_1", OUTBOUND_DOOR_OPTIONS[1], OUTBOUND_DOOR_OPTIONS[2], ("WHT/RED", "WHT/WHT")),
    ("runout_2", OUTBOUND_DOOR_OPTIONS[2], OUTBOUND_DOOR_OPTIONS[3], ("YEL", "BLK")),
    ("runout_3", OUTBOUND_DOOR_OPTIONS[3], OUTBOUND_DOOR_OPTIONS[4], ("BRN/RED", "BRN/WHT")),
    ("runout_4", OUTBOUND_DOOR_OPTIONS[4], OUTBOUND_DOOR_OPTIONS[5], ("BRN/ORG", "BRN/GRN")),
    ("runout_5", OUTBOUND_DOOR_OPTIONS[5], OUTBOUND_DOOR_OPTIONS[6], ("BRN/YEL", "BRN/BLK")),
    ("runout_6", OUTBOUND_DOOR_OPTIONS[6], OUTBOUND_DOOR_OPTIONS[7], ("BRN/BRN", "BRN/BLU")),
    ("runout_7", OUTBOUND_DOOR_OPTIONS[7], OUTBOUND_DOOR_OPTIONS[8], ("WHT/ORG", "WHT/GRN")),
    ("runout_8", OUTBOUND_DOOR_OPTIONS[8], OUTBOUND_DOOR_OPTIONS[9], ("BLU/RED", "BLU/WHT")),
    ("runout_9", OUTBOUND_DOOR_OPTIONS[9], OUTBOUND_DOOR_OPTIONS[10], ("BLU/ORG", "BLU/GRN")),
    ("runout_10", OUTBOUND_DOOR_OPTIONS[10], OUTBOUND_DOOR_OPTIONS[11], ("BLU/BLU", "BRN/WHT")),
    ("runout_11", OUTBOUND_DOOR_OPTIONS[11], OUTBOUND_DOOR_OPTIONS[12], ("BLU/YEL", "BLU/BLK")),
)

BUILDING_LINEUP_SLOT_LAYOUT = (
    (1, "east", 1, "east_destination_1"),
    (1, "east", 2, "east_destination_1_slot_2"),
    (1, "west", 1, "west_destination_1"),
    (1, "west", 2, "west_destination_1_slot_2"),
    (2, "east", 1, "east_destination_2"),
    (2, "east", 2, "east_destination_2_slot_2"),
    (2, "west", 1, "west_destination_2"),
    (2, "west", 2, "west_destination_2_slot_2"),
)

DESTINATION_FIELDS = tuple(
    field_name
    for _belt_number, _side, _slot_number, field_name in BUILDING_LINEUP_SLOT_LAYOUT
)

BELT_COLOR_LABELS = {
    "WHT": "White",
    "BLU": "Blue",
    "ORG": "Orange",
    "RED": "Red",
    "YEL": "Yellow",
    "BLK": "Black",
    "BRN": "Brown",
    "GRN": "Green",
}

BELT_COLOR_KEYS = {
    "WHT": "white",
    "BLU": "blue",
    "ORG": "orange",
    "RED": "red",
    "YEL": "yellow",
    "BLK": "black",
    "BRN": "brown",
    "GRN": "green",
}

DEFAULT_PULL_TIMES = {"pure": "--", "mix": "--"}


@dataclass(frozen=True)
class BuildingLineupLoadResult:
    rows: list
    persistent_state_changed: bool


def get_outbound_door_options():
    return OUTBOUND_DOOR_OPTIONS


def get_building_lineup_rows(gateway, *, initialize=True):
    return load_building_lineup_rows(gateway, initialize=initialize).rows


def load_building_lineup_rows(gateway, *, initialize=True):
    existing_rows = {
        row.runout_key: row
        for row in NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    }

    rows = []
    persistent_state_changed = False
    for runout_key, start_door, end_door, belt_names in BUILDING_LINEUP_BELT_GROUPS:
        runout_name = f"{start_door}-{end_door} Belts"
        row = existing_rows.get(runout_key)
        if not row:
            row = NeoErmacBuildingLineup(
                gateway_id=gateway.id,
                runout_key=runout_key,
                runout_name=runout_name,
            )
            if initialize:
                db.session.add(row)
                persistent_state_changed = True
        elif initialize and row.runout_name != runout_name:
            row.runout_name = runout_name
            persistent_state_changed = True
        apply_belt_display_metadata(row, start_door, end_door, belt_names)
        rows.append(row)

    if initialize and persistent_state_changed:
        db.session.flush()
    return BuildingLineupLoadResult(
        rows=rows,
        persistent_state_changed=persistent_state_changed,
    )


def get_departure_destination_choices(gateway):
    rows = (
        MasterFlightSchedule.query.filter(
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
            or_(
                MasterFlightSchedule.gateway_id == gateway.id,
                MasterFlightSchedule.gateway_code == gateway.code,
            ),
        )
        .order_by(MasterFlightSchedule.destination.asc())
        .all()
    )

    destinations = {
        normalize_destination(row.destination)
        for row in rows
        if normalize_destination(row.destination)
    }
    return sorted(destinations)


def get_departure_destination_pull_times(gateway):
    pull_times = _current_sort_destination_pull_times(gateway)

    rows = (
        MasterFlightSchedule.query.filter(
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
            or_(
                MasterFlightSchedule.gateway_id == gateway.id,
                MasterFlightSchedule.gateway_code == gateway.code,
            ),
        )
        .order_by(MasterFlightSchedule.destination.asc(), MasterFlightSchedule.flight_number.asc())
        .all()
    )

    for row in rows:
        destination = normalize_destination(row.destination)
        if not destination:
            continue

        destination_times = pull_times.setdefault(
            destination,
            {"pure": "--", "mix": "--"},
        )
        _fill_pull_time(destination_times, "pure", row.pure_pull_time_local)
        _fill_pull_time(destination_times, "mix", row.mix_pull_time_local)

    return pull_times


def get_destination_pull_times(gateway, destination):
    destination = normalize_destination(destination)
    if not destination:
        return dict(DEFAULT_PULL_TIMES)
    return dict(get_departure_destination_pull_times(gateway).get(destination, DEFAULT_PULL_TIMES))


def save_building_lineup(gateway, form_data):
    rows = get_building_lineup_rows(gateway)
    destination_choices = set(get_departure_destination_choices(gateway))

    for row in rows:
        normalized_values = {}
        for field_name in DESTINATION_FIELDS:
            value = normalize_destination(form_data.get(lineup_field_name(row, field_name)))
            if value and value not in destination_choices:
                raise ValueError(f"{value} is not an available master departure destination.")
            normalized_values[field_name] = value
        _validate_physical_belt_side_destinations(row, normalized_values)
        for field_name, value in normalized_values.items():
            setattr(row, field_name, value or None)

    db.session.flush()
    _recompute_current_sort_door_pull_aggregates(gateway)
    db.session.flush()
    return rows


def get_building_lineup_doors_by_destination(gateway, *, assignments=None):
    doors_by_destination = {}
    if assignments is None:
        assignments = get_building_lineup_assignments(gateway)
    for assignment in assignments:
        destination = assignment["destination"]
        door = assignment["supervising_door"]
        assigned_doors = doors_by_destination.setdefault(destination, [])
        if door not in assigned_doors:
            assigned_doors.append(door)
    return {
        destination: tuple(
            sorted(doors, key=lambda door: OUTBOUND_DOOR_OPTIONS.index(door))
        )
        for destination, doors in doors_by_destination.items()
    }


def get_building_lineup_assignments(
    gateway,
    include_blank=False,
    *,
    initialize=True,
    rows=None,
):
    assignments = []
    if rows is None:
        rows = get_building_lineup_rows(gateway, initialize=initialize)
    for row in rows:
        assignments.extend(
            building_lineup_slot_descriptors(row, include_blank=include_blank)
        )
    return tuple(assignments)


def get_building_lineup_destinations_for_door(
    gateway,
    door,
    *,
    initialize=True,
    assignments=None,
):
    door = str(door or "").strip().upper()
    destinations = {}
    if assignments is None:
        assignments = get_building_lineup_assignments(
            gateway,
            initialize=initialize,
        )
    for assignment in assignments:
        if assignment["supervising_door"] != door:
            continue
        labels = destinations.setdefault(assignment["destination"], [])
        if assignment["display_label"] not in labels:
            labels.append(assignment["display_label"])
    return destinations


def get_linked_building_lineup_doors(
    gateway,
    door,
    destination,
    *,
    assignments=None,
):
    """Return doors facing the same physical belt and destination."""
    door = str(door or "").strip().upper()
    destination = normalize_destination(destination)
    if not door or not destination:
        return ()

    if assignments is None:
        assignments = get_building_lineup_assignments(gateway)
    source_assignments = [
        assignment
        for assignment in assignments
        if assignment["supervising_door"] == door
        and assignment["destination"] == destination
    ]
    linked_doors = set()
    for source in source_assignments:
        for candidate in assignments:
            if (
                candidate["runout_key"] == source["runout_key"]
                and candidate["belt_number"] == source["belt_number"]
                and candidate["side"] != source["side"]
                and candidate["destination"] == destination
                and candidate["supervising_door"] != door
            ):
                linked_doors.add(candidate["supervising_door"])

    return tuple(
        candidate
        for candidate in OUTBOUND_DOOR_OPTIONS
        if candidate in linked_doors
    )


def save_building_lineup_destination(gateway, field_token, destination):
    field_token = str(field_token or "").strip()
    if not field_token:
        raise ValueError("Building Lineup destination field is required.")

    rows = get_building_lineup_rows(gateway)
    destination_choices = set(get_departure_destination_choices(gateway))
    value = normalize_destination(destination)
    if value and value not in destination_choices:
        raise ValueError(f"{value} is not an available master departure destination.")

    for row in rows:
        for field_name in DESTINATION_FIELDS:
            if lineup_field_name(row, field_name) == field_token:
                normalized_values = {
                    candidate: normalize_destination(getattr(row, candidate, None))
                    for candidate in DESTINATION_FIELDS
                }
                normalized_values[field_name] = value
                _validate_physical_belt_side_destinations(row, normalized_values)
                setattr(row, field_name, value or None)
                db.session.flush()
                _recompute_current_sort_door_pull_aggregates(gateway)
                db.session.flush()
                return {
                    "field": field_token,
                    "destination": value,
                    "pull_times": get_destination_pull_times(gateway, value),
                }

    raise ValueError("Unknown Building Lineup destination field.")


def lineup_field_name(row, field_name):
    return f"lineup_{row.runout_key}_{field_name}"


def apply_belt_display_metadata(row, start_door, end_door, belt_names):
    first_belt, second_belt = belt_names
    row.door_start, row.door_end = _ordered_doors(start_door, end_door)
    row.belt_names = belt_names
    row.belt_group_label = f"{row.door_start}-{row.door_end}"
    row.belt_blocks = (
        {
            "belt_number": 1,
            "label": display_belt_label(first_belt),
            "color_key": belt_color_key(first_belt),
            "slot_number": "1",
            "sides": (
                {
                    "side": "east",
                    "label": "EAST",
                    "door": row.door_start,
                    "slots": (
                        {"field": "east_destination_1", "placeholder": "DEST 1"},
                        {"field": "east_destination_1_slot_2", "placeholder": "DEST 2"},
                    ),
                },
                {
                    "side": "west",
                    "label": "WEST",
                    "door": row.door_end,
                    "slots": (
                        {"field": "west_destination_1", "placeholder": "DEST 1"},
                        {"field": "west_destination_1_slot_2", "placeholder": "DEST 2"},
                    ),
                },
            ),
        },
        {
            "belt_number": 2,
            "label": display_belt_label(second_belt),
            "color_key": belt_color_key(second_belt),
            "slot_number": "2",
            "sides": (
                {
                    "side": "east",
                    "label": "EAST",
                    "door": row.door_start,
                    "slots": (
                        {"field": "east_destination_2", "placeholder": "DEST 1"},
                        {"field": "east_destination_2_slot_2", "placeholder": "DEST 2"},
                    ),
                },
                {
                    "side": "west",
                    "label": "WEST",
                    "door": row.door_end,
                    "slots": (
                        {"field": "west_destination_2", "placeholder": "DEST 1"},
                        {"field": "west_destination_2_slot_2", "placeholder": "DEST 2"},
                    ),
                },
            ),
        },
    )
    row.slot_labels = {
        descriptor["field_name"]: descriptor["display_label"]
        for descriptor in building_lineup_slot_descriptors(row, include_blank=True)
    }


def building_lineup_slot_descriptors(row, include_blank=False):
    descriptors = []
    for belt_number, side, slot_number, field_name in BUILDING_LINEUP_SLOT_LAYOUT:
        destination = normalize_destination(getattr(row, field_name, None))
        if not destination and not include_blank:
            continue
        belt_name = row.belt_names[belt_number - 1]
        supervising_door = row.door_start if side == "east" else row.door_end
        descriptors.append(
            {
                "runout_key": row.runout_key,
                "runout_label": row.belt_group_label,
                "belt_number": belt_number,
                "belt_name": belt_name,
                "belt_label": display_belt_label(belt_name),
                "side": side,
                "slot_number": slot_number,
                "field_name": field_name,
                "supervising_door": supervising_door,
                "destination": destination,
                "display_label": (
                    f"{row.belt_group_label} BELT {belt_number} "
                    f"{side.upper()} SLOT {slot_number}"
                ),
            }
        )
    return tuple(descriptors)


def _validate_physical_belt_side_destinations(row, values):
    for belt_number in (1, 2):
        for side in ("east", "west"):
            side_values = [
                values[field_name]
                for candidate_belt, candidate_side, _slot_number, field_name
                in BUILDING_LINEUP_SLOT_LAYOUT
                if candidate_belt == belt_number and candidate_side == side
            ]
            if side_values[0] and side_values[0] == side_values[1]:
                raise ValueError(
                    f"{side_values[0]} cannot fill both destination slots on "
                    f"{row.belt_group_label} Belt {belt_number} {side.title()}."
                )


def _ordered_doors(first, second):
    return tuple(sorted((first, second), key=lambda door: _door_number(door) or 0))


def normalize_destination(destination):
    return str(destination or "").strip().upper()


def display_belt_label(belt_name):
    parts = str(belt_name or "").split("/")
    return "/".join(BELT_COLOR_LABELS.get(part, part.title()) for part in parts)


def belt_color_key(belt_name):
    first_part = str(belt_name or "").split("/", 1)[0].strip().upper()
    return BELT_COLOR_KEYS.get(first_part, "neutral")


def _current_sort_destination_pull_times(gateway):
    operation = (
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
    if not operation:
        return {}

    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )

    pull_times = {}
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if not destination:
            continue

        timing_data = mission_display_timing_data(mission, operation)
        destination_times = pull_times.setdefault(
            destination,
            {"pure": "--", "mix": "--"},
        )
        _fill_pull_time(
            destination_times,
            "pure",
            timing_data.get("adjusted_pure_pull_time") or mission.pure_pull_time_local,
        )
        _fill_pull_time(
            destination_times,
            "mix",
            timing_data.get("adjusted_mix_pull_time") or mission.mix_pull_time_local,
        )

    return pull_times


def _fill_pull_time(destination_times, key, value):
    if destination_times[key] == "--" and value:
        destination_times[key] = value.strftime("%H:%M")


def _recompute_current_sort_door_pull_aggregates(gateway):
    from app.services.neoermac_pull_aggregation import (
        recompute_current_sort_door_pull_aggregates,
    )

    return recompute_current_sort_door_pull_aggregates(gateway)


def _door_number(door):
    value = str(door or "").strip().upper()
    if value.startswith("D"):
        value = value[1:]
    return int(value) if value.isdigit() else None
