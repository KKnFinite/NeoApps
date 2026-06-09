from sqlalchemy import or_

from app.extensions import db
from app.models import MasterFlightSchedule, NeoErmacBuildingLineup


BUILDING_LINEUP_RUNOUTS = (
    ("green_runout", "Green Runout"),
    *tuple((f"runout_{number}", f"Runout {number}") for number in range(1, 23)),
)

DESTINATION_FIELDS = (
    "east_destination_1",
    "east_destination_2",
    "west_destination_1",
    "west_destination_2",
)


def get_building_lineup_rows(gateway):
    existing_rows = {
        row.runout_key: row
        for row in NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    }

    rows = []
    for runout_key, runout_name in BUILDING_LINEUP_RUNOUTS:
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


def normalize_destination(destination):
    return str(destination or "").strip().upper()
