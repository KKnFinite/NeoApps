"""Freeze accountability grouping without changing source attendance identity."""
from datetime import timedelta

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, SortDateOperation
from app.models.staffing_accountability import (
    StaffingComboWorkday as Combo, StaffingAccountabilityWorkday as Workday,
    StaffingAccountabilitySource as Source,
)
from app.services.neostaffing_assignments import FT_COMBO
from app.services.gateway_matrix import sort_lookup_window_for_operation


def chronological_pair(operations, first_name, second_name, source_id, window):
    """Pair adjacent configured Sort instances by canonical local start time.

    The selected order, not name/ID/date order, defines the workday. A second
    first-Sort before the next second-Sort closes the earlier unmatched workday;
    missing operations are never borrowed from a later workday.
    """
    first_name, second_name = first_name.strip().casefold(), second_name.strip().casefold()
    relevant = [op for op in operations if op.sort_name.strip().casefold() in {first_name, second_name}]
    ordered = sorted(relevant, key=lambda op: (window(op)[0], op.id))
    for first, second in zip(ordered, ordered[1:]):
        if (first.sort_name.strip().casefold() == first_name
                and second.sort_name.strip().casefold() == second_name
                and window(first)[0] < window(second)[0]
                and source_id in (first.id, second.id)):
            return first, second
    return None


def bind_workday_sources(operation, person_ids, *, user_id, qualifying_person_ids=None):
    """Attendance caller already holds ordered employee locks.

    Snapshot configuration at the first qualifying write. Nonqualifying writes
    can complete an existing frozen workday but never create empty workdays.
    Missing partners stay provisional; no GET performs this work.
    """
    if not person_ids:
        return
    qualifying_person_ids = set(person_ids if qualifying_person_ids is None else qualifying_person_ids)
    people = {row.id: row for row in StaffingPerson.query.filter(StaffingPerson.id.in_(person_ids)).all()}
    configs = {row.person_id: row for row in Combo.query.filter(Combo.person_id.in_(person_ids)).all()}
    # Bounded real-operation candidates; unresolved/out-of-window pairs remain
    # provisional instead of fabricating a SortDateOperation or workday date.
    operations = SortDateOperation.query.filter(
        SortDateOperation.gateway_code == operation.gateway_code,
        SortDateOperation.sort_date.between(operation.sort_date - timedelta(days=14),
                                          operation.sort_date + timedelta(days=14)),
    ).order_by(SortDateOperation.sort_date, SortDateOperation.id).limit(256).all()
    units = {row.id: row for row in StaffingUnit.query.filter_by(unit_type="sort").all()}
    bindings = db.session.query(Source, Workday).join(Workday, Workday.id == Source.workday_id).filter(
        Source.person_id.in_(person_ids), Source.operation_id.in_([op.id for op in operations]),
    ).all()
    by_source = {(source.person_id, source.operation_id): (source, group) for source, group in bindings}
    by_group = {}
    for source, group in bindings:
        by_group.setdefault(group.id, set()).add(source.operation_id)
    windows = {}

    def window(op):
        if op.id not in windows:
            windows[op.id] = sort_lookup_window_for_operation(op, operation.gateway)
        return windows[op.id]

    pending = []
    for person_id in sorted(person_ids):
        if person_id not in people:
            continue
        source, group = by_source.get((person_id, operation.id), (None, None))
        config = configs.get(person_id)
        if group is None:
            # An in-progress workday keeps the configuration captured by its
            # first source, even if the employee setting changed between legs.
            candidates = {prior.id: prior for _, prior in bindings
                          if prior.person_id == person_id and prior.requires_pair
                          and len(by_group[prior.id]) < 2}
            matches = []
            for prior in candidates.values():
                if prior.first_sort_id not in units or prior.second_sort_id not in units:
                    continue
                prior_pair = chronological_pair(operations, units[prior.first_sort_id].name,
                    units[prior.second_sort_id].name, operation.id, window)
                if prior_pair and by_group[prior.id] <= {op.id for op in prior_pair}:
                    matches.append(prior)
            if len(matches) == 1:
                group = matches[0]
        if group and len(by_group.get(group.id, ())) == (2 if group.requires_pair else 1):
            continue
        if not group:
            group = Workday(person_id=person_id, workday_date=operation.sort_date,
                requires_pair=people[person_id].classification in FT_COMBO,
                first_sort_id=config.first_sort_id if config else None,
                second_sort_id=config.second_sort_id if config else None,
                created_by_user_id=user_id)
        pair = None
        if group.requires_pair and group.first_sort_id in units and group.second_sort_id in units:
            pair = chronological_pair(operations, units[group.first_sort_id].name,
                units[group.second_sort_id].name, operation.id, window)
        if pair and not source:
            # The other side may already have frozen this workday under an older
            # config. Its source binding wins; never regroup a historical source.
            counterpart = next((by_source[(person_id, op.id)] for op in pair
                                if (person_id, op.id) in by_source), None)
            if counterpart:
                prior = counterpart[1]
                if (prior.first_sort_id, prior.second_sort_id) == (group.first_sort_id, group.second_sort_id):
                    group = prior
                else:
                    pair = None
        if group.id is None:
            if person_id not in qualifying_person_ids:
                continue
            db.session.add(group)
        bound = by_group.get(group.id, set())
        if pair:
            group.workday_date = pair[0].sort_date
            for position, op in enumerate(pair, 1):
                if op.id not in bound:
                    pending.append((group, person_id, op.id, position))
        elif operation.id not in bound:
            second = units.get(group.second_sort_id)
            position = 2 if second and second.name.strip().casefold() == operation.sort_name.strip().casefold() else 1
            pending.append((group, person_id, operation.id, position))
    if pending:
        db.session.flush()
        db.session.add_all(Source(workday_id=group.id, person_id=person_id,
            operation_id=operation_id, position=position)
            for group, person_id, operation_id, position in pending)
