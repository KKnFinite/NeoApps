"""Durable deice assets. No assignment or historical spray evidence is changed here."""
import re
from datetime import datetime

from app.extensions import db
from app.models import Gateway, NeoSubZeroTruck, NeoSubZeroUccTruckAssignment, SortDateOperation
from app.services.live_collaboration import entity_version


def normalize_truck(value):
    number = re.sub(r'\s+', ' ', str(value or '').strip()).upper()
    if not number or len(number) > 32 or not re.fullmatch(r'[A-Z0-9][A-Z0-9 ._-]*', number):
        raise ValueError('Enter a truck identifier of 1–32 letters, numbers, spaces, dots, underscores or hyphens.')
    return number


def registered_trucks(gateway, *, active_only=False):
    query = NeoSubZeroTruck.query.filter_by(gateway_id=gateway.id, archived_at_utc=None)
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(NeoSubZeroTruck.number).all()


def manage_truck(gateway, action, values, *, actor_id):
    # Serializes first registration as well as activation changes for this gateway.
    Gateway.query.filter_by(id=gateway.id).populate_existing().with_for_update().one()
    now = datetime.utcnow()
    prior_number = prior_active = None
    if action == 'add_truck':
        number = normalize_truck(values.get('truck_number'))
        _require_unique_number(gateway, number)
        row = NeoSubZeroTruck(gateway_id=gateway.id, number=number, active=True)
        db.session.add(row)
    else:
        try:
            truck_id = int(values.get('truck_id', ''))
        except (TypeError, ValueError):
            raise ValueError('Choose a registered truck.') from None
        if not 0 < truck_id <= 2147483647:
            raise ValueError('Choose a registered truck.')
        row = NeoSubZeroTruck.query.filter_by(
            id=truck_id, gateway_id=gateway.id, archived_at_utc=None,
        ).populate_existing().with_for_update().one_or_none()
        if row is None or values.get('expected_version') != entity_version(row):
            raise ValueError('Truck registry changed. Reload before retrying.')
        prior_number, prior_active = row.number, row.active
        if action == 'edit_truck':
            number = normalize_truck(values.get('truck_number'))
            if number == prior_number:
                return row
            _require_unique_number(gateway, number)
            if assignment := _current_truck_assignment(gateway, prior_number):
                raise ValueError(f'Remove or replace truck {prior_number} at {assignment.ramp} position '
                                 f'{assignment.position_number} before changing its identifier.')
            row.number = number
        elif action in {'activate_truck', 'deactivate_truck', 'delete_truck'}:
            if values.get('confirm') != 'yes':
                raise ValueError('Confirm the truck status or deletion change.')
            if action == 'delete_truck':
                row.archived_at_utc = now
                row.active = False
            else:
                row.active = action == 'activate_truck'
                if row.active == prior_active:
                    raise ValueError('This truck already has that status.')
        else:
            raise ValueError('Choose a valid Truck Management action.')
    row.history = [*(row.history or []), dict(
        actor_id=actor_id, at_utc=now.isoformat(), action=action, prior_active=prior_active,
        active=row.active, prior_number=prior_number, number=row.number,
    )]
    return row


def _require_unique_number(gateway, number):
    if NeoSubZeroTruck.query.filter_by(gateway_id=gateway.id, number=number).first():
        raise ValueError('This truck identifier is already registered or reserved by an archived truck.')


def _current_truck_assignment(gateway, number):
    from app.services.gateway_matrix import current_gateway_local_datetime
    from app.services.operation_lifecycle import current_existing_operational_sort_operations

    local_now = current_gateway_local_datetime(gateway)
    live_ids = [operation.id for operation in current_existing_operational_sort_operations(gateway, local_now=local_now)]
    return NeoSubZeroUccTruckAssignment.query.join(SortDateOperation).filter(
        SortDateOperation.gateway_code == gateway.code,
        SortDateOperation.archived_at_utc.is_(None),
        db.or_(SortDateOperation.id.in_(live_ids), SortDateOperation.sort_date >= local_now.date()),
        db.func.upper(db.func.trim(NeoSubZeroUccTruckAssignment.truck_number)) == number,
    ).order_by(SortDateOperation.sort_date, NeoSubZeroUccTruckAssignment.id).first()


def validate_registered_truck(operation, number, *, existing=None):
    if not number:
        return ''
    normalized = normalize_truck(number)
    gateway_id = operation.gateway_id or Gateway.query.filter_by(code=operation.gateway_code).one().id
    # Leaving the truck in its original slot is a no-op. Any new placement
    # must come from the current in-service fleet.
    row = NeoSubZeroTruck.query.filter_by(gateway_id=gateway_id, number=normalized).populate_existing().with_for_update().one_or_none()
    if normalized != str(existing or '').strip().upper() and (
        not row or not row.active or row.archived_at_utc is not None
    ):
        raise ValueError('Select an in-service registered deice truck in Truck Management.')
    return normalized
