"""Driver selection is sort-scoped identity, not physical truck occupancy."""
from datetime import datetime
from sqlalchemy.orm import joinedload
from app.extensions import db
from app.models import NeoScorpionTruckDriver, NeoScorpionTruckDriverAudit, NeoScorpionSortTruck, NeoScorpionFuelTruck, User
from app.services.neoscorpion_assets import (eligible_nightly_fueler_users,
    lock_nightly_asset_scope_for_mutation, record_nightly_operational_change)

DRIVER_VIEW = "neoscorpion.truck_driver.view"
DRIVER_EDIT = "neoscorpion.truck_driver.edit"


def driver_roster(gateway, operation, *, include_choices=False):
    if operation is None:
        return dict(driver_trucks=[], drivers_by_truck_id={}, eligible_drivers=[])
    trucks = (NeoScorpionFuelTruck.query.join(NeoScorpionSortTruck,
        NeoScorpionSortTruck.fuel_truck_id == NeoScorpionFuelTruck.id)
        .filter(NeoScorpionSortTruck.sort_date_operation_id == operation.id,
                NeoScorpionFuelTruck.gateway_id == gateway.id)
        .order_by(NeoScorpionFuelTruck.truck_number).all())
    drivers = NeoScorpionTruckDriver.query.options(joinedload(NeoScorpionTruckDriver.driver)).filter_by(
        sort_date_operation_id=operation.id).all()
    return dict(driver_trucks=trucks, drivers_by_truck_id={d.fuel_truck_id:d for d in drivers},
        eligible_drivers=eligible_nightly_fueler_users(gateway, permission_key=DRIVER_EDIT) if include_choices else [])


def change_driver(gateway, operation, actor, truck_id, driver_id, *, dispatcher=False,
                  expected_driver_id, expected_driver_truck_id, override=False, reason=""):
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    if operation.gateway_id != gateway.id:
        raise ValueError("This sort belongs to another gateway.")
    truck_id = int(truck_id)
    driver_id = int(driver_id) if driver_id else None
    if not dispatcher and driver_id != actor.id:
        raise ValueError("Drivers may select only their own truck.")
    truck = NeoScorpionFuelTruck.query.join(NeoScorpionSortTruck,
        NeoScorpionSortTruck.fuel_truck_id==NeoScorpionFuelTruck.id).filter(
        NeoScorpionSortTruck.sort_date_operation_id==operation.id,
        NeoScorpionFuelTruck.gateway_id==gateway.id, NeoScorpionFuelTruck.id==truck_id,
        NeoScorpionFuelTruck.is_active.is_(True)).first()
    if not truck:
        raise ValueError("Choose a truck from this sort's nightly roster.")
    if driver_id and not eligible_nightly_fueler_users(gateway, permission_key=DRIVER_EDIT,
            active_users=User.query.filter_by(id=driver_id,is_active=True).all()):
        raise ValueError("Choose an active driver with Truck Driver selection access in this gateway.")
    target = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=truck_id).with_for_update().populate_existing().first()
    previous = target.driver_user_id if target else None
    own = (NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,
        driver_user_id=driver_id).with_for_update().populate_existing().first() if driver_id else None)
    if str(previous or "") != str(expected_driver_id or "") or (
            driver_id and str(own.fuel_truck_id if own else "") != str(expected_driver_truck_id or "")):
        raise ValueError("Driver assignment changed. Refresh before choosing a truck.")
    if previous == driver_id:
        return False, int(state.revision if state else 0)
    reason = str(reason or "").strip()
    conflict = previous is not None and driver_id is not None and previous != driver_id
    moving_other = dispatcher and own is not None and own.fuel_truck_id != truck_id
    if conflict or moving_other:
        if not dispatcher or not override:
            raise ValueError("Driver already assigned. An authorized dispatcher must confirm the override.")
        if not reason:
            raise ValueError("Enter a reason for the dispatcher override.")
    if len(reason)>240:
        raise ValueError("Driver change reason must be 240 characters or fewer.")
    now = datetime.utcnow()

    def set_driver(item, value, explanation):
        db.session.add(NeoScorpionTruckDriverAudit(sort_date_operation_id=operation.id,
            fuel_truck_id=item.fuel_truck_id, previous_driver_user_id=item.driver_user_id,
            driver_user_id=value, changed_by_user_id=actor.id, reason=explanation, created_at=now))
        item.driver_user_id=value; item.changed_by_user_id=actor.id; item.updated_at=now

    if own and own.fuel_truck_id != truck_id:
        set_driver(own, None, reason or "Driver switched vehicles.")
        db.session.flush()  # release the per-sort user uniqueness before moving
    if target is None:
        target = NeoScorpionTruckDriver(sort_date_operation_id=operation.id,fuel_truck_id=truck_id,
                                       changed_by_user_id=actor.id)
        db.session.add(target)
    set_driver(target, driver_id, reason or ("Dispatcher changed driver." if dispatcher else "Driver selected truck."))
    state = record_nightly_operational_change(state, operation.id)
    db.session.flush()
    return True, int(state.revision)


def driver_context(gateway, user, *, manage=False):
    from app.services.neoscorpion import current_sort_operation, fueler_context
    from app.services.neoscorpion_fueler_names import fueler_nicknames, operational_fueler_name
    from app.services.neoscorpion_fueling_status import utc_naive
    operation = current_sort_operation(gateway)
    roster = driver_roster(gateway, operation, include_choices=manage)
    own = next((d for d in roster["drivers_by_truck_id"].values() if d.driver_user_id==user.id),None)
    selected = next((t for t in roster["driver_trucks"] if own and t.id==own.fuel_truck_id),None)
    context = fueler_context(gateway,user,dispatcher=True,truck_id=selected.id if selected else -1,include_completed=True)
    nicknames = fueler_nicknames(gateway,(r["assigned_fueler"] for r in context["rows"]))
    groups = {"current":[],"upcoming":[],"previous":[]}
    for row in context["rows"]:
        work = row["fuel_work_state"]
        previous = row["administratively_complete"] or bool(work and (work.off_at_utc or work.ended_early_at_utc))
        current = work and row.get("fueling_on_trusted") and not previous
        group = "previous" if previous else "current" if current else "upcoming"
        groups[group].append(dict(row=row, fueler_name=operational_fueler_name(row["assigned_fueler"],nicknames)
            if row["assigned_fueler"] else "—"))
    def etd(item):
        mission=item["row"]["mission"]
        return (utc_naive(mission.eta_datetime_utc or mission.planned_datetime_utc) or datetime.max,
                mission.flight_number or "",mission.id)
    groups["upcoming"].sort(key=etd)
    groups["previous"].sort(key=lambda item:(utc_naive(item["row"]["fuel_work_state"].off_at_utc
        or item["row"]["fuel_work_state"].ended_early_at_utc) or datetime.min) if item["row"]["fuel_work_state"] else datetime.min,reverse=True)
    return {**context,**roster,"driver_groups":groups,"selected_driver_truck":selected,
            "current_driver_truck_id":own.fuel_truck_id if own else None}
