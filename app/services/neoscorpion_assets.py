"""Transactional primitives for SortDateOperation-scoped NeoScorpion assets.

Mutations flush child state and its revision together. The caller owns the
single outer commit, matching the application's existing service convention.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.models import (
    NeoScorpionFuelTruck,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    SortDateOperation,
    User,
)


NIGHTLY_TRUCK_STATUSES = frozenset(
    {"available", "unavailable_oos", "topping_off"}
)
_UNSET = object()


@dataclass(frozen=True)
class NightlyAssetMutationResult:
    changed: bool
    revision: int


def set_nightly_fuel_island_count(operation, fuel_island_count):
    island_count = _validate_fuel_island_count(fuel_island_count)
    locked_operation, state = _lock_operation_and_state(operation)
    if state is None and island_count is None:
        return NightlyAssetMutationResult(False, 0)
    if state is not None and state.fuel_island_count == island_count:
        return _unchanged(state)

    state = _record_change(state, locked_operation.id)
    state.fuel_island_count = island_count
    db.session.flush()
    return _changed(state)


def select_nightly_fueler(operation, user):
    user_id = _entity_id(user, "fueler")
    locked_operation, state = _lock_operation_and_state(operation)
    existing = NeoScorpionSortFueler.query.filter_by(
        sort_date_operation_id=locked_operation.id,
        user_id=user_id,
    ).first()
    if existing is not None:
        return _unchanged(state)
    if db.session.get(User, user_id) is None:
        raise ValueError("Select an existing fueler.")

    db.session.add(
        NeoScorpionSortFueler(
            sort_date_operation_id=locked_operation.id,
            user_id=user_id,
        )
    )
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def remove_nightly_fueler(operation, user):
    user_id = _entity_id(user, "fueler")
    locked_operation, state = _lock_operation_and_state(operation)
    existing = NeoScorpionSortFueler.query.filter_by(
        sort_date_operation_id=locked_operation.id,
        user_id=user_id,
    ).first()
    if existing is None:
        return _unchanged(state)

    db.session.delete(existing)
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def select_nightly_truck(
    operation,
    fuel_truck,
    *,
    status,
    starting_gallons=None,
    current_gallons=None,
):
    status = _validate_truck_status(status)
    starting_gallons = _validate_gallons(starting_gallons, "Starting gallons")
    current_gallons = _validate_gallons(current_gallons, "Current gallons")
    locked_operation, state = _lock_operation_and_state(operation)
    truck = _truck_for_operation(locked_operation, fuel_truck)
    _validate_truck_state(
        truck,
        status,
        starting_gallons,
        current_gallons,
    )

    nightly_truck = NeoScorpionSortTruck.query.filter_by(
        sort_date_operation_id=locked_operation.id,
        fuel_truck_id=truck.id,
    ).first()
    if nightly_truck is None:
        nightly_truck = NeoScorpionSortTruck(
            sort_date_operation_id=locked_operation.id,
            fuel_truck_id=truck.id,
            status=status,
            starting_gallons=starting_gallons,
            current_gallons=current_gallons,
        )
        db.session.add(nightly_truck)
    else:
        if _truck_values(nightly_truck) == (
            status,
            starting_gallons,
            current_gallons,
        ):
            return _unchanged(state)
        if nightly_truck.status == "topping_off" and status == "available":
            raise ValueError("Use Top Off Complete to make this truck available.")
        _set_truck_values(
            nightly_truck,
            status,
            starting_gallons,
            current_gallons,
        )

    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def update_nightly_truck(
    operation,
    fuel_truck,
    *,
    status=_UNSET,
    starting_gallons=_UNSET,
    current_gallons=_UNSET,
):
    locked_operation, state = _lock_operation_and_state(operation)
    truck = _truck_for_operation(locked_operation, fuel_truck)
    nightly_truck = _selected_truck(locked_operation.id, truck.id)

    final_status = (
        nightly_truck.status if status is _UNSET else _validate_truck_status(status)
    )
    final_starting = (
        nightly_truck.starting_gallons
        if starting_gallons is _UNSET
        else _validate_gallons(starting_gallons, "Starting gallons")
    )
    final_current = (
        nightly_truck.current_gallons
        if current_gallons is _UNSET
        else _validate_gallons(current_gallons, "Current gallons")
    )
    _validate_truck_state(truck, final_status, final_starting, final_current)

    final_values = (final_status, final_starting, final_current)
    if _truck_values(nightly_truck) == final_values:
        return _unchanged(state)
    if nightly_truck.status == "topping_off" and final_status == "available":
        raise ValueError("Use Top Off Complete to make this truck available.")

    _set_truck_values(nightly_truck, *final_values)
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def remove_nightly_truck(operation, fuel_truck):
    truck_id = _entity_id(fuel_truck, "fuel truck")
    locked_operation, state = _lock_operation_and_state(operation)
    nightly_truck = NeoScorpionSortTruck.query.filter_by(
        sort_date_operation_id=locked_operation.id,
        fuel_truck_id=truck_id,
    ).first()
    if nightly_truck is None:
        return _unchanged(state)

    db.session.delete(nightly_truck)
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def mark_nightly_truck_topping_off(operation, fuel_truck):
    locked_operation, state = _lock_operation_and_state(operation)
    truck = _truck_for_operation(locked_operation, fuel_truck)
    nightly_truck = _selected_truck(locked_operation.id, truck.id)
    if nightly_truck.status == "topping_off":
        return _unchanged(state)

    nightly_truck.status = "topping_off"
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def complete_nightly_truck_top_off(operation, fuel_truck, current_gallons):
    if current_gallons is None or str(current_gallons).strip() == "":
        raise ValueError("Enter current gallons to complete the top off.")
    current_gallons = _validate_gallons(current_gallons, "Current gallons")
    locked_operation, state = _lock_operation_and_state(operation)
    truck = _truck_for_operation(locked_operation, fuel_truck)
    nightly_truck = _selected_truck(locked_operation.id, truck.id)
    if nightly_truck.status != "topping_off":
        raise ValueError("The selected truck is not currently topping off.")

    _validate_truck_state(
        truck,
        "available",
        nightly_truck.starting_gallons,
        current_gallons,
    )
    nightly_truck.status = "available"
    nightly_truck.current_gallons = current_gallons
    state = _record_change(state, locked_operation.id)
    db.session.flush()
    return _changed(state)


def _lock_operation_and_state(operation):
    operation_id = _entity_id(operation, "sort operation")
    # The always-present operation row serializes both first state creation and
    # every later child/revision mutation for this operational scope.
    locked_operation = (
        SortDateOperation.query.filter_by(id=operation_id).with_for_update().first()
    )
    if locked_operation is None:
        raise ValueError("Select an existing sort operation.")
    state = (
        NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=locked_operation.id
        )
        .with_for_update()
        .first()
    )
    return locked_operation, state


def _record_change(state, operation_id):
    if state is None:
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation_id,
            revision=0,
        )
        db.session.add(state)
    state.revision = int(state.revision or 0) + 1
    return state


def _unchanged(state):
    return NightlyAssetMutationResult(False, int(state.revision if state else 0))


def _changed(state):
    return NightlyAssetMutationResult(True, int(state.revision))


def _entity_id(entity, label):
    value = getattr(entity, "id", entity)
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Select a valid {label}.")
    if value <= 0:
        raise ValueError(f"Select a valid {label}.")
    return value


def _validate_fuel_island_count(value):
    if value is None or str(value).strip() == "":
        return None
    value = _integer_value(value, "Fuel island count")
    if value < 0 or value > 4:
        raise ValueError("Fuel island count must be between 0 and 4.")
    return value


def _validate_truck_status(status):
    status = str(status or "").strip().lower()
    if status not in NIGHTLY_TRUCK_STATUSES:
        raise ValueError("Select a valid nightly truck status.")
    return status


def _validate_gallons(value, label):
    if value is None or str(value).strip() == "":
        return None
    value = _integer_value(value, label)
    if value < 0:
        raise ValueError(f"{label} cannot be negative.")
    return value


def _integer_value(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a whole number.")
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise ValueError(f"{label} must be a whole number.")
    return int(parsed)


def _truck_for_operation(operation, fuel_truck):
    truck_id = _entity_id(fuel_truck, "fuel truck")
    gateway_id = operation.gateway_id
    if gateway_id is None:
        raise ValueError("The sort operation is not linked to a gateway.")
    truck = NeoScorpionFuelTruck.query.filter_by(
        id=truck_id,
        gateway_id=gateway_id,
    ).first()
    if truck is None:
        raise ValueError("Select a fuel truck for this gateway.")
    return truck


def _selected_truck(operation_id, truck_id):
    nightly_truck = NeoScorpionSortTruck.query.filter_by(
        sort_date_operation_id=operation_id,
        fuel_truck_id=truck_id,
    ).first()
    if nightly_truck is None:
        raise ValueError("Select this truck for the nightly operation first.")
    return nightly_truck


def _validate_truck_state(truck, status, starting_gallons, current_gallons):
    if status == "available" and (
        starting_gallons is None or current_gallons is None
    ):
        raise ValueError("Available trucks require starting and current gallons.")
    capacity = truck.capacity_gallons
    if capacity is None:
        return
    if capacity < 0:
        raise ValueError("The fuel truck has an invalid configured capacity.")
    for value, label in (
        (starting_gallons, "Starting gallons"),
        (current_gallons, "Current gallons"),
    ):
        if value is not None and value > capacity:
            raise ValueError(f"{label} cannot exceed truck capacity.")


def _truck_values(nightly_truck):
    return (
        nightly_truck.status,
        nightly_truck.starting_gallons,
        nightly_truck.current_gallons,
    )


def _set_truck_values(nightly_truck, status, starting_gallons, current_gallons):
    nightly_truck.status = status
    nightly_truck.starting_gallons = starting_gallons
    nightly_truck.current_gallons = current_gallons
