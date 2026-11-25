"""Read-only NeoErmac door evidence for NeoRaiden."""
from collections import Counter
from types import SimpleNamespace

from sqlalchemy import literal, null, select, type_coerce, union_all

from app.extensions import db
from app.models import NeoErmacBuildingLineup, NeoErmacDoorPull
from app.services.neoermac_building_lineup import (
    BUILDING_LINEUP_BELT_GROUPS, DESTINATION_FIELDS, building_lineup_slot_descriptors,
    get_building_lineup_doors_by_master, normalize_destination,
)
from app.services.neoermac_pull_aggregation import (
    PULL_AGGREGATION_FIELDS, _door_pulls_by_mission_and_door, _latest_actual_time,
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
    assignments, unresolved, records = [], set(), []
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
        for slot in building_lineup_slot_descriptors(slot_row, include_blank=True):
            if slot["master_flight_schedule_id"] and slot["destination"]:
                assignments.append(slot)
            elif slot["destination"]:
                unresolved.add(slot["destination"])
    doors_by_master = get_building_lineup_doors_by_master(gateway, assignments=assignments)
    pulls = _door_pulls_by_mission_and_door(gateway, operation, departures, rows=records)
    master_counts = Counter(mission.master_flight_schedule_id for mission in departures)
    roster, values = {}, {}
    for mission in departures:
        master = mission.master_flight_schedule_id
        doors = doors_by_master.get(master, ())
        if not doors or master_counts[master] != 1 or normalize_destination(mission.destination) in unresolved:
            continue
        roster[mission.id] = doors
        door_records = [pulls.get((mission.id, door)) for door in doors]
        values[mission.id] = tuple(
            _completed_kind(operation, door_records, actual, no_pull, label)
            for (_, actual, no_pull), label in zip(PULL_AGGREGATION_FIELDS, ("PURE", "MIX"))
        )
    return roster, values


def _completed_kind(operation, records, actual_attr, no_attr, label):
    actuals = []
    for record in records:
        if record is None:
            return None
        if not getattr(record, no_attr):
            actual = getattr(record, actual_attr)
            if actual is None:
                return None
            actuals.append(actual)
    latest = _latest_actual_time(operation, actuals)
    return latest.strftime("%H:%M") if latest is not None else f"NO {label}"
