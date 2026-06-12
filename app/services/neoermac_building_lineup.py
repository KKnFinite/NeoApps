from sqlalchemy import or_

from app.extensions import db
from app.models import MasterFlightSchedule, NeoErmacBuildingLineup


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
    "WHT": "white",
    "BLU": "blue",
    "ORG": "orange",
    "RED": "red",
    "YEL": "yellow",
    "BLK": "black",
    "BRN": "brown",
    "GRN": "green",
}


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
            "label": display_belt_label(second_belt),
            "top_slots": (
                {"field": "east_destination_2", "placeholder": "DEST 1"},
                {"field": None, "placeholder": "DEST 2"},
            ),
            "bottom_slots": (
                {"field": "west_destination_2", "placeholder": "DEST 1"},
                {"field": None, "placeholder": "DEST 2"},
            ),
        },
        {
            "label": display_belt_label(first_belt),
            "top_slots": (
                {"field": "east_destination_1", "placeholder": "DEST 1"},
                {"field": None, "placeholder": "DEST 2"},
            ),
            "bottom_slots": (
                {"field": "west_destination_1", "placeholder": "DEST 1"},
                {"field": None, "placeholder": "DEST 2"},
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
    return "/".join(BELT_COLOR_LABELS.get(part, part.lower()) for part in parts)
