from datetime import datetime

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    NeoErmacDoorPull,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
)
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_outbound_door_options,
    normalize_destination,
)
from app.services.uld_requests import (
    ULD_TYPES,
    active_on_the_way_event_views,
    get_uld_request,
    update_uld_request_from_form,
)


PULL_FIELDS = (
    {
        "key": "pure",
        "label": "Pure",
        "planned_attr": "pure_pull_time_local",
        "actual_attr": "actual_pure_pull_time_local",
        "no_attr": "no_pure_pull",
        "actual_field": "actual_pure",
        "no_field": "no_pure",
    },
    {
        "key": "first_mix",
        "label": "1st Mix",
        "planned_attr": "first_mix_pull_time_local",
        "actual_attr": "actual_first_mix_pull_time_local",
        "no_attr": "no_first_mix_pull",
        "actual_field": "actual_first_mix",
        "no_field": "no_first_mix",
    },
    {
        "key": "second_mix",
        "label": "2nd Mix",
        "planned_attr": "final_mix_pull_time_local",
        "actual_attr": "actual_second_mix_pull_time_local",
        "no_attr": "no_second_mix_pull",
        "actual_field": "actual_second_mix",
        "no_field": "no_second_mix",
    },
)

def door_view_context(gateway, selected_door=None):
    selected_door = normalize_door(selected_door)
    door_options = get_door_options(gateway)
    if selected_door not in door_options:
        selected_door = None

    operation = _current_operation(gateway)
    destinations = []
    uld_request = None
    if selected_door:
        destinations = _destination_cards_for_door(gateway, selected_door, operation)
        uld_request = _uld_request_for_door(gateway, selected_door)

    return {
        "door_options": door_options,
        "selected_door": selected_door,
        "destinations": destinations,
        "pull_fields": PULL_FIELDS,
        "uld_types": ULD_TYPES,
        "uld_request": uld_request,
        "operation": operation,
        "tugs": [],
        "on_the_way_events": (
            active_on_the_way_event_views(gateway, selected_door) if selected_door else []
        ),
    }


def save_door_pulls(gateway, selected_door, form_data):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")

    context = door_view_context(gateway, selected_door)
    allowed_destinations = {card["destination"] for card in context["destinations"]}
    operation = context["operation"]

    row_count = _int_value(form_data.get("destination_count"), default=0)
    for index in range(row_count):
        destination = normalize_destination(form_data.get(f"destination_{index}"))
        if not destination:
            continue
        if destination not in allowed_destinations:
            raise ValueError(f"{destination} is not assigned to {selected_door}.")

        record = _door_pull_record(gateway, selected_door, destination, operation, create=True)
        for field in PULL_FIELDS:
            no_pull = form_data.get(f"{field['no_field']}_{index}") == "on"
            setattr(record, field["no_attr"], no_pull)
            if no_pull:
                setattr(record, field["actual_attr"], None)
                continue

            setattr(
                record,
                field["actual_attr"],
                _parse_optional_time(form_data.get(f"{field['actual_field']}_{index}")),
            )

    db.session.flush()


def save_uld_request(gateway, selected_door, form_data):
    selected_door = normalize_door(selected_door)
    if not selected_door:
        raise ValueError("Select a door.")
    if selected_door not in get_door_options(gateway):
        raise ValueError(f"{selected_door} is not available.")

    return update_uld_request_from_form(gateway, selected_door, form_data)


def normalize_door(value):
    value = str(value or "").strip().upper()
    if not value:
        return ""
    if value.startswith("D"):
        number = value[1:]
    else:
        number = value
    if not number.isdigit():
        return ""
    return f"D{int(number)}"


def get_door_options(gateway):
    return get_outbound_door_options()


def _destination_cards_for_door(gateway, selected_door, operation):
    destination_slots = _destination_slots_for_door(gateway, selected_door)
    missions = _missions_by_destination(gateway, operation)
    masters = _master_departures_by_destination(gateway)
    cards = []

    for destination, slot_labels in destination_slots.items():
        mission = missions.get(destination)
        master = masters.get(destination)
        door_pull = _door_pull_record(gateway, selected_door, destination, operation)
        tail_state = _tail_state_for_mission(mission)

        cards.append(
            {
                "destination": destination,
                "slot_labels": slot_labels,
                "tail": mission.assigned_tail_number if mission else "",
                "parking": tail_state.parking_position if tail_state else "",
                "planned": {
                    "pure": _time_value(
                        getattr(mission, "pure_pull_time_local", None)
                        or getattr(master, "pure_pull_time_local", None)
                    ),
                    "first_mix": _time_value(
                        getattr(mission, "first_mix_pull_time_local", None)
                        or getattr(master, "first_mix_pull_time_local", None)
                    ),
                    "second_mix": _time_value(
                        getattr(mission, "final_mix_pull_time_local", None)
                        or getattr(master, "final_mix_pull_time_local", None)
                    ),
                },
                "actual": {
                    "pure": _time_value(getattr(door_pull, "actual_pure_pull_time_local", None)),
                    "first_mix": _time_value(
                        getattr(door_pull, "actual_first_mix_pull_time_local", None)
                    ),
                    "second_mix": _time_value(
                        getattr(door_pull, "actual_second_mix_pull_time_local", None)
                    ),
                },
                "no_pull": {
                    "pure": bool(getattr(door_pull, "no_pure_pull", False)),
                    "first_mix": bool(getattr(door_pull, "no_first_mix_pull", False)),
                    "second_mix": bool(getattr(door_pull, "no_second_mix_pull", False)),
                },
            }
        )

    return cards


def _destination_slots_for_door(gateway, selected_door):
    selected_number = _door_number(selected_door)
    destination_slots = {}

    for row in get_building_lineup_rows(gateway):
        start = _door_number(row.door_start)
        end = _door_number(row.door_end)
        if selected_number is None or start is None or end is None:
            continue
        low, high = sorted((start, end))
        if not (low <= selected_number <= high):
            continue

        for field_name in DESTINATION_FIELDS:
            destination = normalize_destination(getattr(row, field_name, None))
            if not destination:
                continue
            label = row.slot_labels.get(field_name, field_name.replace("_", " ").upper())
            destination_slots.setdefault(destination, []).append(f"{row.belt_group_label} {label}")

    return dict(sorted(destination_slots.items()))


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


def _missions_by_destination(gateway, operation):
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
    result = {}
    for mission in missions:
        destination = normalize_destination(mission.destination)
        if destination and destination not in result:
            result[destination] = mission
    return result


def _master_departures_by_destination(gateway):
    masters = (
        MasterFlightSchedule.query.filter(
            MasterFlightSchedule.mission_type == "departure",
            MasterFlightSchedule.active.is_(True),
            or_(
                MasterFlightSchedule.gateway_id == gateway.id,
                MasterFlightSchedule.gateway_code == gateway.code,
            ),
        )
        .order_by(MasterFlightSchedule.planned_time_local.asc(), MasterFlightSchedule.id.asc())
        .all()
    )
    result = {}
    for master in masters:
        destination = normalize_destination(master.destination)
        if destination and destination not in result:
            result[destination] = master
    return result


def _door_pull_record(gateway, selected_door, destination, operation, create=False):
    query = NeoErmacDoorPull.query.filter_by(
        gateway_id=gateway.id,
        door=selected_door,
        destination=destination,
    )
    if operation:
        query = query.filter_by(sort_date_operation_id=operation.id)
    else:
        query = query.filter(NeoErmacDoorPull.sort_date_operation_id.is_(None))

    record = query.first()
    if not record and create:
        record = NeoErmacDoorPull(
            gateway_id=gateway.id,
            sort_date_operation_id=operation.id if operation else None,
            door=selected_door,
            destination=destination,
        )
        db.session.add(record)
    return record


def _uld_request_for_door(gateway, selected_door):
    return get_uld_request(gateway, selected_door)


def _tail_state_for_mission(mission):
    if not mission or not mission.assigned_tail_number:
        return None

    return SortDateTailState.query.filter_by(
        sort_date=mission.sort_date,
        gateway_code=mission.gateway_code,
        sort_name=mission.sort_name,
        tail_number=mission.assigned_tail_number.strip().upper(),
    ).first()


def _parse_optional_time(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time().replace(second=0, microsecond=0)
    except ValueError as exc:
        raise ValueError("Actual pull times must use HH:MM format.") from exc


def _time_value(value):
    if not value:
        return ""
    return value.strftime("%H:%M")


def _int_value(value, default=0):
    value = str(value if value is not None else "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("ULD counts must be whole numbers.") from exc
    if parsed < 0:
        raise ValueError("ULD counts cannot be negative.")
    return parsed


def _door_number(door):
    normalized = normalize_door(door)
    if not normalized:
        return None
    return int(normalized[1:])
