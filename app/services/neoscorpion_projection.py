"""Read-only inventory reservations keyed by physical work, not current labels."""
from app.models import NeoScorpionInventoryReading


def inventory_reservations(operation, selections, rows, works):
    readings={(r.fuel_truck_id,r.epoch):set(r.covered_work_ids or ()) for r in
        NeoScorpionInventoryReading.query.filter_by(sort_date_operation_id=operation.id).all()}
    balances={identifier:s.current_gallons if (s.inventory_verified or
        s.inventory_pending_epoch is not None and s.inventory_pending_epoch==s.inventory_epoch) else None
        for identifier,s in selections.items()}
    by_work={row['fuel_work_state'].id:row for row in rows if row.get('fuel_work_state')}
    reserved=set()
    for work in works:
        if (work.performance_context or {}).get('duplicate_of_work_id') or work.physical_finalized_at_utc:
            continue
        row=by_work.get(work.id)
        truck_id=work.physical_truck_id or (getattr(row.get('assignment'),'assigned_truck_id',None) if row else None)
        selection=selections.get(truck_id)
        if not selection or not (work.on_at_utc or work.manual_on_at_utc or work.off_at_utc or work.ended_early_at_utc):
            continue
        reading=readings.get((truck_id,selection.inventory_epoch))
        kind=work.physical_cycle_type or (row.get('cycle_type') if row else None)
        if row and row.get('direction_mismatch'):
            balances[truck_id]=None;reserved.add(work.id);continue
        # A recorded receiving DEFUEL is not a serviceability release. Its
        # pending/sump recovery must not become a fresh usable stock forecast.
        if kind=='defuel' and (work.physical_transfer_gallons or 0)>0:
            balances[truck_id]=None;reserved.add(work.id);continue
        covered=(work.id in reading if reading is not None else
            work.off_inventory_epoch is not None and work.off_inventory_epoch!=selection.inventory_epoch)
        if covered:
            reserved.add(work.id);continue
        if row and not (work.off_at_utc or work.ended_early_at_utc):
            continue  # Active current demand is reserved by its existing planner.
        transfer=work.physical_transfer_gallons
        if transfer is None and row and work.physical_sequence_number is None:
            transfer=row.get('transfer_fuel_gallons')
        if transfer is None or work.physical_transfer_review or kind not in ('fuel','uplift','defuel'):
            balances[truck_id]=None
        elif balances[truck_id] is not None:
            balances[truck_id] += transfer if kind=='defuel' else -transfer
        reserved.add(work.id)
    for row in rows:
        work=row.get('fuel_work_state')
        row['inventory_projection_bases']=balances
        row['inventory_demand_gallons']=0 if work and (work.id in reserved or work.physical_finalized_at_utc) else row.get('planning_demand_gallons')
        if work and (work.id in reserved or work.physical_finalized_at_utc):
            row['truck_projection_demand_gallons']=0
    return balances
