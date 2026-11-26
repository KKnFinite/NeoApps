"""Read-only NeoErmac door evidence for NeoRaiden."""
from types import SimpleNamespace

from sqlalchemy import literal, null, select, type_coerce, union_all

from app.extensions import db
from app.models import NeoErmacBuildingLineup, NeoErmacDoorPull
from app.services.neoermac_building_lineup import (
    BUILDING_LINEUP_BELT_GROUPS, DESTINATION_FIELDS, building_lineup_slot_descriptors,
)
from app.services.neoermac_pull_aggregation import (
    PULL_AGGREGATION_FIELDS, _door_pulls_by_mission_and_door, complete_pull_roster, completed_door_pull,
)


def _evidence_rows(gateway, operation):
    # Fold the gateway lineup into the existing Sort door read. UNION ALL also
    # returns assigned doors before any pull records exist, without a cross join.
    pull = NeoErmacDoorPull
    lineup = NeoErmacBuildingLineup
    pull_columns = (pull.id, pull.sort_date_mission_id, pull.door, pull.destination,
                    pull.updated_at, pull.actual_pure_pull_time_local, pull.no_pure_pull,
                    pull.actual_mix_pull_time_local, pull.no_mix_pull)
    lineup_columns = (lineup.runout_key, lineup.flight_assignments,
                      *(getattr(lineup, field) for field in DESTINATION_FIELDS))
    records = select(literal(False).label("is_lineup"), *pull_columns,
                     *(type_coerce(null(), col.type).label(col.key) for col in lineup_columns)
                     ).where(pull.gateway_id == gateway.id, pull.sort_date_operation_id == operation.id)
    assignments = select(literal(True), *(null() for _ in pull_columns), *lineup_columns
                         ).where(lineup.gateway_id == gateway.id)
    return db.session.execute(union_all(records, assignments))


def completed_pulls(gateway, operation, departures):
    runouts = {key: (east, west, belts) for key, east, west, belts in BUILDING_LINEUP_BELT_GROUPS}
    assignments, records = [], []
    for row in _evidence_rows(gateway, operation):
        if not row.is_lineup:
            records.append(SimpleNamespace(**row._mapping, gateway_id=gateway.id,
                                           sort_date_operation_id=operation.id))
            continue
        if row.runout_key not in runouts:
            continue
        east, west, belts = runouts[row.runout_key]
        slot_row = SimpleNamespace(**row._mapping, east_door=east, west_door=west,
                                   belt_names=belts, belt_group_label=f"{east}-{west}")
        assignments.extend(building_lineup_slot_descriptors(slot_row, include_blank=True))
    roster = complete_pull_roster(gateway, departures, assignments)
    pulls = _door_pulls_by_mission_and_door(gateway, operation, departures, rows=records)
    values = {}
    for mission_id, doors in roster.items():
        door_records = [pulls.get((mission_id, door)) for door in doors]
        values[mission_id] = tuple(
            _completed_kind(operation, door_records, actual, no_pull, label)
            for (_, actual, no_pull), label in zip(PULL_AGGREGATION_FIELDS, ("PURE", "MIX"))
        )
    return roster, values


def _completed_kind(operation, records, actual_attr, no_attr, label):
    latest, all_no_pull = completed_door_pull(operation, records, actual_attr, no_attr)
    if all_no_pull:
        return f"NO {label}"
    return latest.strftime("%H:%M") if latest is not None else None
