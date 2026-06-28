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

DESTINATION_FIELDS = (
    "east_destination_1",
    "east_destination_2",
    "west_destination_1",
    "west_destination_2",
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

DEFAULT_PULL_TIMES = {"pure": "--", "first_mix": "--", "final_mix": "--"}


def get_outbound_door_options():
    return OUTBOUND_DOOR_OPTIONS


def get_building_lineup_rows(gateway):
    existing_rows = {
        row.runout_key: row
        for row in NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    }

    rows = []
    for runout_key, start_door, end_door, belt_names in BUILDING_LINEUP_BELT_GROUPS:
        runout_name = f"{start_door}-{end_door} Belts"
        row = existing_rows.get(runout_key)
        if not row:
            row = NeoErmacBuildingLineup(
                gateway_id=gateway.id,
                runout_key=runout_key,
                runout_name=runout_name,
            )
            db.session.add(row)
        else:
            row.runout_name = runout_name
        apply_belt_display_metadata(row, start_door, end_door, belt_names)
        rows.append(row)

    db.session.flush()
    return rows


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
            {"pure": "--", "first_mix": "--", "final_mix": "--"},
        )
        _fill_pull_time(destination_times, "pure", row.pure_pull_time_local)
        _fill_pull_time(destination_times, "first_mix", row.first_mix_pull_time_local)
        _fill_pull_time(destination_times, "final_mix", row.final_mix_pull_time_local)

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
        for field_name in DESTINATION_FIELDS:
            value = normalize_destination(form_data.get(lineup_field_name(row, field_name)))
            if value and value not in destination_choices:
                raise ValueError(f"{value} is not an available master departure destination.")
            setattr(row, field_name, value or None)

    db.session.flush()
    return rows


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
                setattr(row, field_name, value or None)
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
    row.door_start = start_door
    row.door_end = end_door
    row.belt_names = belt_names
    row.belt_group_label = f"{start_door}-{end_door}"
    row.belt_blocks = (
        {
            "label": display_belt_label(first_belt),
            "color_key": belt_color_key(first_belt),
            "slot_number": "1",
            "top_field": "east_destination_1",
            "bottom_field": "west_destination_1",
            "top_slots": (
                {"field": "east_destination_1", "placeholder": "DEST 1"},
            ),
            "bottom_slots": (
                {"field": "west_destination_1", "placeholder": "DEST 1"},
            ),
        },
        {
            "label": display_belt_label(second_belt),
            "color_key": belt_color_key(second_belt),
            "slot_number": "2",
            "top_field": "east_destination_2",
            "bottom_field": "west_destination_2",
            "top_slots": (
                {"field": "east_destination_2", "placeholder": "DEST 2"},
            ),
            "bottom_slots": (
                {"field": "west_destination_2", "placeholder": "DEST 2"},
            ),
        },
    )
    row.slot_labels = {
        "east_destination_1": f"EAST {first_belt} BELT",
        "east_destination_2": f"EAST {second_belt} BELT",
        "west_destination_1": f"WEST {first_belt} BELT",
        "west_destination_2": f"WEST {second_belt} BELT",
    }


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
            {"pure": "--", "first_mix": "--", "final_mix": "--"},
        )
        _fill_pull_time(
            destination_times,
            "pure",
            timing_data.get("base_pure_pull_time") or mission.pure_pull_time_local,
        )
        _fill_pull_time(
            destination_times,
            "first_mix",
            timing_data.get("base_first_mix_pull_time") or mission.first_mix_pull_time_local,
        )
        _fill_pull_time(
            destination_times,
            "final_mix",
            timing_data.get("base_final_mix_pull_time") or mission.final_mix_pull_time_local,
        )

    return pull_times


def _fill_pull_time(destination_times, key, value):
    if destination_times[key] == "--" and value:
        destination_times[key] = value.strftime("%H:%M")
