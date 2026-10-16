from dataclasses import dataclass

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import or_, select, text

from app.extensions import db
from app.models import (
    Gateway,
    MasterFlightSchedule,
    NeoErmacBuildingLineup,
    SortDateMission,
)
from app.services.operation_scope import current_operational_sort_operation
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

# Physical tuple order: runout key, EAST door, WEST door, belt names.
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
_UNRESOLVED = object()


class LineupConflict(ValueError):
    """A slot no longer has the value displayed by this editor."""


def lineup_original(gateway, field, value):
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="ermac-lineup-slot-v2").dumps(
        [gateway.id, field, str(value or "")]
    )


def _original_value(gateway, field, token):
    try:
        original = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="ermac-lineup-slot-v2").loads(token or "")
    except BadSignature:
        raise LineupConflict("Lineup form is outdated. Refresh before saving.") from None
    if not isinstance(original, list) or len(original) != 3 or original[:2] != [gateway.id, field]:
        raise LineupConflict("Lineup form is outdated. Refresh before saving.")
    return original[2]


def _lock_lineup(gateway):
    # Same reservation as Door pulls, including first-use rows and rollups.
    if db.engine.dialect.name == "sqlite":
        db.session.execute(text("UPDATE gateways SET id=id WHERE id=:id"), {"id": gateway.id})
    else:
        db.session.execute(select(Gateway.id).where(Gateway.id == gateway.id).with_for_update())


def building_lineup_state_payload(gateway, *, operation=_UNRESOLVED):
    rows = get_building_lineup_rows(gateway, initialize=False)
    masters = get_lineup_departures(gateway)
    return {
        "slots": {
            lineup_field_name(row, field): {
                "destination": slot_flight_value(row, field),
                "original": lineup_original(gateway, lineup_field_name(row, field), slot_flight_value(row, field)),
            }
            for row in rows for field in DESTINATION_FIELDS
        },
        "destination_choices": get_departure_destination_choices(gateway, masters=masters),
        "pull_times": get_departure_destination_pull_times(gateway, masters=masters, operation=operation),
    }


@dataclass(frozen=True)
class BuildingLineupLoadResult:
    rows: list
    persistent_state_changed: bool


def get_outbound_door_options():
    return OUTBOUND_DOOR_OPTIONS


def get_building_lineup_rows(gateway, *, initialize=True, for_update=False):
    return load_building_lineup_rows(gateway, initialize=initialize, for_update=for_update).rows


def load_building_lineup_rows(gateway, *, initialize=True, for_update=False):
    query = NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id)
    if for_update:
        query = query.populate_existing()
    existing_rows = {
        row.runout_key: row
        for row in query.all()
    }

    rows = []
    persistent_state_changed = False
    for runout_key, east_door, west_door, belt_names in BUILDING_LINEUP_BELT_GROUPS:
        runout_name = f"{east_door}-{west_door} Belts"
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
        apply_belt_display_metadata(row, east_door, west_door, belt_names)
        rows.append(row)

    if initialize and persistent_state_changed:
        db.session.flush()
    return BuildingLineupLoadResult(
        rows=rows,
        persistent_state_changed=persistent_state_changed,
    )


def get_lineup_departures(gateway):
    """One request-local master snapshot for choices and fallback pull times."""
    return (
        MasterFlightSchedule.query.filter(
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
            or_(
                MasterFlightSchedule.gateway_id == gateway.id,
                (MasterFlightSchedule.gateway_id.is_(None) & (MasterFlightSchedule.gateway_code == gateway.code)),
            ),
        )
        .order_by(MasterFlightSchedule.destination.asc(), MasterFlightSchedule.flight_number.asc())
        .all()
    )


def get_departure_destination_choices(gateway, *, masters=None):
    rows = get_lineup_departures(gateway) if masters is None else masters
    return [{"value": str(row.id), "label": f"{row.flight_number} — {normalize_destination(row.destination)}"}
            for row in rows if normalize_destination(row.destination)]


def slot_flight_value(row, field):
    value = (row.flight_assignments or {}).get(field)
    if value:
        return str(value)
    destination = normalize_destination(getattr(row, field, None))
    return f"legacy:{destination}" if destination else ""


def backfill_lineup_flight_identities():
    """Bootstrap only: resolve each legacy slot once, never replace an identity.

    Explicit nulls freeze ambiguous/missing mappings until an operator selects a
    flight. No historical pull or Sort mission is edited.
    """
    rows = [row for row in NeoErmacBuildingLineup.query.all() if row.flight_assignments is None]
    if not rows:
        return 0
    gateways = {g.id: g for g in Gateway.query.all()}
    masters = MasterFlightSchedule.query.filter_by(active=True, mission_type="departure").all()
    corrected = 0
    for row in rows:
        gateway = gateways.get(row.gateway_id)
        choices = {}
        for master in masters:
            if gateway and (master.gateway_id == gateway.id or
                           (master.gateway_id is None and master.gateway_code == gateway.code)):
                choices.setdefault(normalize_destination(master.destination), []).append(master.id)
        identities = {}
        for field in DESTINATION_FIELDS:
            candidates = choices.get(normalize_destination(getattr(row, field)), ())
            identities[field] = candidates[0] if len(candidates) == 1 else None
            corrected += bool(identities[field])
        row.flight_assignments = identities
    db.session.flush()
    return corrected

def get_departure_destination_pull_times(gateway, *, masters=None, operation=_UNRESOLVED, missions=None):
    if operation is _UNRESOLVED:
        operation = current_operational_sort_operation(gateway)
    if missions is None:
        missions = _lineup_operation_missions(operation)
    by_master = {}
    for mission in missions:
        if mission.master_flight_schedule_id:
            by_master.setdefault(mission.master_flight_schedule_id, []).append(mission)
    result = {}
    for master_id, candidates in by_master.items():
        values = dict(DEFAULT_PULL_TIMES)
        if len(candidates) == 1:
            mission = candidates[0]
            timing = mission_display_timing_data(mission, operation)
            _fill_pull_time(values, "pure", timing.get("adjusted_pure_pull_time") or mission.pure_pull_time_local)
            _fill_pull_time(values, "mix", timing.get("adjusted_mix_pull_time") or mission.mix_pull_time_local)
        result[str(master_id)] = values
    for master in (get_lineup_departures(gateway) if masters is None else masters):
        if str(master.id) in result:
            continue
        values = dict(DEFAULT_PULL_TIMES)
        _fill_pull_time(values, "pure", master.pure_pull_time_local)
        _fill_pull_time(values, "mix", master.mix_pull_time_local)
        result[str(master.id)] = values
    return result

def get_destination_pull_times(gateway, destination, **snapshot):
    if not destination:
        return dict(DEFAULT_PULL_TIMES)
    return dict(get_departure_destination_pull_times(gateway, **snapshot).get(str(destination or ""), DEFAULT_PULL_TIMES))

def save_building_lineup(gateway, form_data):
    _lock_lineup(gateway)
    rows = get_building_lineup_rows(gateway, for_update=True)
    masters = {str(master.id): master for master in get_lineup_departures(gateway)}
    for row in rows:
        for field_name in DESTINATION_FIELDS:
            field = lineup_field_name(row, field_name)
            if field not in form_data:
                continue
            value = str(form_data.get(field) or "").strip()
            original = _original_value(gateway, field, form_data.get(f"original_{field}"))
            if value == original:
                continue
            if slot_flight_value(row, field_name) != original:
                raise LineupConflict("This lineup slot changed since it was displayed. Refresh before saving.")
            _set_slot_flight(row, field_name, value, masters)
    db.session.flush()
    _recompute_current_sort_door_pull_aggregates(gateway, rows)
    db.session.flush()
    return rows


def _set_slot_flight(row, field, value, masters):
    if value and value not in masters:
        raise ValueError("Select an available Master Schedule departure flight.")
    master = masters.get(value)
    row.flight_assignments = {**(row.flight_assignments or {}), field: master.id if master else None}
    setattr(row, field, normalize_destination(master.destination) if master else None)

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
    master_flight_schedule_id=None,
):
    """Return doors facing the same physical belt and selected flight."""
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
        and (master_flight_schedule_id is None or assignment["master_flight_schedule_id"] == master_flight_schedule_id)
    ]
    linked_doors = set()
    for source in source_assignments:
        for candidate in assignments:
            if (
                candidate["runout_key"] == source["runout_key"]
                and candidate["belt_number"] == source["belt_number"]
                and candidate["side"] != source["side"]
                and candidate["master_flight_schedule_id"] == source["master_flight_schedule_id"]
                and candidate["supervising_door"] != door
            ):
                linked_doors.add(candidate["supervising_door"])

    return tuple(
        candidate
        for candidate in OUTBOUND_DOOR_OPTIONS
        if candidate in linked_doors
    )


def save_building_lineup_destination(gateway, field_token, destination, *, expected_original=None):
    field_token = str(field_token or "").strip()
    _lock_lineup(gateway)
    rows = get_building_lineup_rows(gateway, for_update=True)
    masters = get_lineup_departures(gateway)
    choices = {str(master.id): master for master in masters}
    value = str(destination or "").strip()
    for row in rows:
        for field_name in DESTINATION_FIELDS:
            if lineup_field_name(row, field_name) == field_token:
                original = _original_value(gateway, field_token, expected_original)
                if slot_flight_value(row, field_name) != original:
                    raise LineupConflict("This lineup slot changed since it was displayed. Refresh before saving.")
                _set_slot_flight(row, field_name, value, choices)
                db.session.flush()
                operation, missions = _recompute_current_sort_door_pull_aggregates(gateway, rows)
                db.session.flush()
                return {"field": field_token, "destination": value,
                        "original": lineup_original(gateway, field_token, value),
                        "pull_times": get_destination_pull_times(gateway, value, masters=masters,
                                                                operation=operation, missions=missions)}
    raise ValueError("Unknown Building Lineup flight field.")

def lineup_field_name(row, field_name):
    return f"lineup_{row.runout_key}_{field_name}"


def apply_belt_display_metadata(row, east_door, west_door, belt_names):
    first_belt, second_belt = belt_names
    row.east_door, row.west_door = east_door, west_door
    row.belt_names = belt_names
    row.belt_group_label = f"{row.east_door}-{row.west_door}"
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
                    "door": row.east_door,
                    "slots": (
                        {"field": "east_destination_1", "placeholder": "DEST 1"},
                        {"field": "east_destination_1_slot_2", "placeholder": "DEST 2"},
                    ),
                },
                {
                    "side": "west",
                    "label": "WEST",
                    "door": row.west_door,
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
                    "door": row.east_door,
                    "slots": (
                        {"field": "east_destination_2", "placeholder": "DEST 1"},
                        {"field": "east_destination_2_slot_2", "placeholder": "DEST 2"},
                    ),
                },
                {
                    "side": "west",
                    "label": "WEST",
                    "door": row.west_door,
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
        master_id = (row.flight_assignments or {}).get(field_name)
        if (not destination or not master_id) and not include_blank:
            continue
        belt_name = row.belt_names[belt_number - 1]
        supervising_door = row.east_door if side == "east" else row.west_door
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
                "master_flight_schedule_id": master_id,
                "display_label": (
                    f"{row.belt_group_label} BELT {belt_number} "
                    f"{side.upper()} SLOT {slot_number}"
                ),
            }
        )
    return tuple(descriptors)


def normalize_destination(destination):
    return str(destination or "").strip().upper()


def display_belt_label(belt_name):
    parts = str(belt_name or "").split("/")
    return "/".join(BELT_COLOR_LABELS.get(part, part.title()) for part in parts)


def belt_color_key(belt_name):
    first_part = str(belt_name or "").split("/", 1)[0].strip().upper()
    return BELT_COLOR_KEYS.get(first_part, "neutral")


def _lineup_operation_missions(operation):
    if operation is None:
        return ()
    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )


def _fill_pull_time(destination_times, key, value):
    if destination_times[key] == "--" and value:
        destination_times[key] = value.strftime("%H:%M")


def _recompute_current_sort_door_pull_aggregates(gateway, rows):
    from app.services.neoermac_pull_aggregation import (
        recompute_current_sort_door_pull_aggregates,
    )

    operation = current_operational_sort_operation(gateway)
    missions = _lineup_operation_missions(operation)
    if operation is not None:
        assignments = get_building_lineup_assignments(gateway, rows=rows)
        recompute_current_sort_door_pull_aggregates(
            gateway, operation=operation, all_operation_missions=missions,
            doors_by_master=get_building_lineup_doors_by_master(gateway, assignments=assignments),
        )
    # These same ORM missions now contain the post-aggregation values.
    return operation, missions


def _door_number(door):
    value = str(door or "").strip().upper()
    if value.startswith("D"):
        value = value[1:]
    return int(value) if value.isdigit() else None


def get_building_lineup_doors_by_master(gateway, *, assignments=None):
    result = {}
    if assignments is None:
        assignments = get_building_lineup_assignments(gateway)
    for slot in assignments:
        master_id = slot.get("master_flight_schedule_id")
        if master_id:
            result.setdefault(master_id, set()).add(slot["supervising_door"])
    return {key: tuple(door for door in OUTBOUND_DOOR_OPTIONS if door in doors)
            for key, doors in result.items()}
