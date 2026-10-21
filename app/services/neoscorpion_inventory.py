"""Epoch-aware physical readings versus pre-transfer dispatcher baselines."""
import re
from datetime import datetime
from app.extensions import db
from app.models import (NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionFuelWorkState,
    NeoScorpionFuelAssignment, NeoScorpionInventoryReading, NeoScorpionVendorRequest,
    NeoScorpionTruckInventoryAudit, NeoScorpionTruckDriver, NeoScorpionSettings)
from app.models import NeoScorpionFuelingEvent, User, SortDateOperation
from sqlalchemy import func
from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change
from app.services.neoscorpion_vendors import dispatcher, audit, vendor_account, require_approved, current_operation


def gallons_value(truck, value):
    if not re.fullmatch(r'\d{1,9}', str(value or '0') if value == 0 else str(value or '')):
        raise ValueError('Enter nonnegative whole gallons.')
    number = int(value)
    if truck.capacity_gallons is None:
        raise ValueError('Configure this truck capacity in Settings first.')
    if number > truck.capacity_gallons:
        raise ValueError('Gallons exceed this truck capacity.')
    return number


def truck_works(operation, truck_id):
    return NeoScorpionFuelWorkState.query.join(NeoScorpionFuelAssignment,
        NeoScorpionFuelAssignment.id == NeoScorpionFuelWorkState.fuel_assignment_id).filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelWorkState.physical_truck_id == truck_id,
            NeoScorpionFuelWorkState.performance_context['duplicate_of_work_id'].as_integer().is_(None))


def physical_interval(operation, truck_id):
    works = truck_works(operation, truck_id).all()
    active = NeoScorpionFuelWorkState.query.filter(
        NeoScorpionFuelWorkState.physical_truck_id==truck_id,
        NeoScorpionFuelWorkState.off_at_utc.is_(None),
        NeoScorpionFuelWorkState.ended_early_at_utc.is_(None),
        NeoScorpionFuelWorkState.physical_finalized_at_utc.is_(None),
        db.or_(NeoScorpionFuelWorkState.manual_on_at_utc.is_not(None),
            NeoScorpionFuelWorkState.confirmed_on_at_utc.is_not(None),
            NeoScorpionFuelWorkState.on_at_utc.is_not(None))).all()
    ended = [w for w in works if w.off_at_utc or w.ended_early_at_utc]
    previous = max(ended, key=lambda w:(w.off_at_utc or w.ended_early_at_utc,w.id), default=None)
    return active, previous


def expire_pending(operation, truck_id, *, next_work_id=None, reason='physical_milestone', actor=None, exclude_request_id=None):
    rows = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=truck_id, kind='fuel', status='pending').all()
    for row in rows:
        if row.id == exclude_request_id:
            continue
        if next_work_id is None or row.next_work_id == next_work_id:
            row.status = 'superseded' if reason == 'superseded_reading' else 'expired'; row.version += 1
            row.decided_at = datetime.utcnow()
            row.decided_by_user_id=actor.id if actor else None
            audit(operation.gateway, actor or row.user, 'fuel_reading_'+row.status, operation=operation,
                subject=row.user, truck_id=truck_id, request_id=row.id, reason=reason, automatic=actor is None)


def on_physical_start(operation, work):
    if not work.physical_truck_id:
        return
    for row in NeoScorpionVendorRequest.query.filter_by(fuel_truck_id=work.physical_truck_id,
            kind='fuel', status='pending').with_for_update().populate_existing().all():
        if row.sort_date_operation_id != operation.id or (row.next_work_id is not None and row.next_work_id != work.id):
            row.status = 'expired'; row.version += 1
            row.decided_at=datetime.utcnow();row.decided_by_user_id=work.manual_on_by_user_id
            actor=db.session.get(User,work.manual_on_by_user_id) if work.manual_on_by_user_id else row.user
            audit(operation.gateway,actor,'fuel_reading_expired',
                operation=db.session.get(SortDateOperation,row.sort_date_operation_id),subject=row.user,
                truck_id=work.physical_truck_id,request_id=row.id,reason='physical_visit_scope_changed',triggering_work_id=work.id)
        else:
            row.next_work_id = work.id


def on_transfer_entry(operation, assignment, work, actor):
    truck_id = work.physical_truck_id or assignment.assigned_truck_id
    if not truck_id:
        return
    for row in NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=truck_id, kind='fuel', status='pending').all():
        interval=db.session.get(NeoScorpionFuelWorkState,row.interval_work_id) if row.interval_work_id else None
        interval_end=(interval.off_at_utc or interval.ended_early_at_utc) if interval else None
        work_end=work.off_at_utc or work.ended_early_at_utc
        if interval_end and work_end and work_end <= interval_end:
            continue  # Late paperwork for an older visit is not a new transfer interval.
        if row.interval_work_id != work.id:
            row.status = 'expired'; row.version += 1; row.decided_at = datetime.utcnow()
            row.decided_by_user_id=actor.id
            audit(operation.gateway,actor,'fuel_reading_expired',subject=row.user,operation=operation,truck_id=truck_id,
                request_id=row.id,reason='next_tf_entry')


def verification_available(operation, truck_id):
    selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,fuel_truck_id=truck_id).first()
    truck=db.session.get(NeoScorpionFuelTruck,truck_id)
    if not selection or not truck or not truck.is_active or selection.status=='topping_off':
        return False
    active,previous=physical_interval(operation,truck_id)
    if active:
        return False
    interval=previous.id if previous else None
    return NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=truck_id,kind='fuel',interval_work_id=interval,status='approved').first() is None


def apply_reading(gateway, operation, selection, actor, value, *, mode, request_id=None):
    prior = selection.current_gallons
    old_reading = NeoScorpionInventoryReading.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=selection.fuel_truck_id,epoch=selection.inventory_epoch).first()
    works = truck_works(operation, selection.fuel_truck_id).all()
    covered = list(old_reading.covered_work_ids) if old_reading else [w.id for w in works
        if (w.off_at_utc or w.ended_early_at_utc) and w.off_inventory_epoch is not None
        and w.off_inventory_epoch != selection.inventory_epoch]
    if mode == 'physical':
        covered = sorted(set(covered + [w.id for w in works
            if (w.off_at_utc or w.ended_early_at_utc) and w.physical_finalized_at_utc is None]))
    selection.current_gallons = value
    selection.inventory_verified = True
    selection.inventory_epoch = int(selection.inventory_epoch or 0) + 1
    selection.inventory_pending_epoch = None
    db.session.add(NeoScorpionInventoryReading(sort_date_operation_id=operation.id,
        fuel_truck_id=selection.fuel_truck_id, epoch=selection.inventory_epoch,
        mode=mode, gallons=value, covered_work_ids=covered, actor_user_id=actor.id))
    db.session.add(NeoScorpionTruckInventoryAudit(sort_date_operation_id=operation.id,
        fuel_truck_id=selection.fuel_truck_id, action='verified', prior_recorded_gallons=prior,
        confirmed_gallons=value, actor_user_id=actor.id,
        reason='Dispatcher pre-transfer baseline correction.' if mode == 'baseline' else 'Confirmed physical inventory reading.'))
    expire_pending(operation, selection.fuel_truck_id, reason='new_inventory_reading', actor=actor, exclude_request_id=request_id)
    audit(gateway, actor, 'inventory_' + mode, operation=operation, truck_id=selection.fuel_truck_id,
        prior=prior, gallons=value, epoch=selection.inventory_epoch, covered_work_ids=covered)


def transfer_covered(operation, selection, work, legacy_default):
    """New explicit readings supersede the legacy epoch-only heuristic."""
    reading = NeoScorpionInventoryReading.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=selection.fuel_truck_id, epoch=selection.inventory_epoch).first()
    return work.id in reading.covered_work_ids if reading else legacy_default


def truck_selection(gateway, operation, truck_id):
    # Same persistent truck lock used by physical ON across overlapping sorts.
    truck=NeoScorpionFuelTruck.query.filter_by(id=int(truck_id),gateway_id=gateway.id).with_for_update().populate_existing().one_or_none()
    selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=truck.id).with_for_update().populate_existing().one_or_none() if truck else None
    if not selection:
        raise ValueError('Select a configured truck at this gateway and active sort.')
    return truck,selection


def correct_baseline(gateway, operation, actor, truck_id, gallons, expected_epoch, expected_posting_id=0):
    dispatcher(gateway, actor)
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(gateway, operation.id)
    truck,selection=truck_selection(gateway,operation,truck_id)
    if str(selection.inventory_epoch) != str(expected_epoch):
        raise ValueError('Truck inventory changed. Refresh before correcting it.')
    last_posting=db.session.query(func.max(NeoScorpionFuelingEvent.id)).filter_by(
        sort_date_operation_id=operation.id,fuel_truck_id=truck.id).scalar() or 0
    if str(last_posting) != str(expected_posting_id):
        raise ValueError('A physical transfer posted. Refresh before correcting the new balance.')
    value = gallons_value(truck, gallons)
    apply_reading(gateway, operation, selection, actor, value, mode='baseline')
    record_nightly_operational_change(state, operation.id); db.session.flush()


def submit_verification(gateway, operation, actor, truck_id, gallons):
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(gateway, operation.id)
    account = vendor_account(actor)
    if account:
        require_approved(account, operation)
        own = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id,
            fuel_truck_id=int(truck_id), driver_user_id=actor.id).first()
        if not own:
            raise PermissionError('Verify only your approved truck.')
    else:
        from app.services.neoscorpion_assets import eligible_nightly_fueler_users
        if not eligible_nightly_fueler_users(gateway, active_users=[actor],permission_key='neoscorpion.fueler.edit'):
            raise PermissionError('Authorized nightly Fueler required.')
    truck,selection=truck_selection(gateway,operation,truck_id)
    from app.services.neoscorpion_assets import _assert_inventory_measurement_safe
    _assert_inventory_measurement_safe(operation.id,truck.id)
    active, previous = physical_interval(operation, truck.id)
    if active or selection.status == 'topping_off' or not truck.is_active:
        raise ValueError('Optional readings are accepted only between physical OFF and the next ON, outside TOP OFF.')
    if not account and (not previous or previous.physical_fueler_user_id != actor.id):
        raise PermissionError('Verify only a truck from your finished physical assignment.')
    value = gallons_value(truck, gallons)
    interval_id = previous.id if previous else None
    prior = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=truck.id, kind='fuel', interval_work_id=interval_id).order_by(NeoScorpionVendorRequest.id.desc()).first()
    if prior and prior.status == 'approved':
        return prior
    if prior and prior.status == 'pending' and prior.gallons == value:
        return prior
    expire_pending(operation, truck.id, reason='superseded_reading', actor=actor)
    settings = NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
    threshold = settings.truck_fuel_discrepancy_threshold_gallons if settings else 1000
    difference = abs(value-selection.current_gallons) if selection.current_gallons is not None else None
    row = NeoScorpionVendorRequest(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
        user_id=actor.id, kind='fuel', gallons=value, calculated_gallons=selection.current_gallons,
        inventory_epoch=selection.inventory_epoch, interval_work_id=interval_id)
    db.session.add(row); db.session.flush()
    if difference is not None and difference <= threshold:
        apply_reading(gateway, operation, selection, actor, value, mode='physical', request_id=row.id)
        row.status = 'approved'; row.decided_at = datetime.utcnow(); row.decided_by_user_id = actor.id
    audit(gateway, actor, 'fuel_reading_' + row.status, operation=operation,
        truck_id=truck.id, request_id=row.id, difference=difference, threshold=threshold)
    record_nightly_operational_change(state, operation.id); db.session.flush()
    return row


def decide_verification(gateway, operation, actor, request_id, approve, expected_version):
    dispatcher(gateway, actor)
    operation, state = lock_nightly_asset_scope_for_mutation(operation)
    current_operation(gateway, operation.id)
    row = NeoScorpionVendorRequest.query.filter_by(id=int(request_id), sort_date_operation_id=operation.id,
        kind='fuel').with_for_update().populate_existing().one_or_none()
    if not row or row.status != 'pending' or str(row.version) != str(expected_version):
        raise ValueError('Reading already decided, expired or changed.')
    selection = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
        fuel_truck_id=row.fuel_truck_id).with_for_update().populate_existing().one_or_none()
    next_work = db.session.get(NeoScorpionFuelWorkState, row.next_work_id) if row.next_work_id else None
    if not selection or selection.inventory_epoch != row.inventory_epoch or (next_work and
            (next_work.off_at_utc or next_work.ended_early_at_utc or next_work.physical_transfer_gallons is not None)):
        row.status = 'expired'
    elif approve:
        value = gallons_value(row.truck, row.gallons)
        apply_reading(gateway, operation, selection, actor, value, mode='physical', request_id=row.id)
        row.status = 'approved'
    else:
        row.status = 'denied'
    row.version += 1; row.decided_at = datetime.utcnow(); row.decided_by_user_id = actor.id
    audit(gateway, actor, 'fuel_reading_' + row.status, operation=operation, truck_id=row.fuel_truck_id, request_id=row.id)
    record_nightly_operational_change(state, operation.id); db.session.flush()
    return row
