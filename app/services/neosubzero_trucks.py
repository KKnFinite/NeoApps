"""Durable deice assets. No assignment or historical spray evidence is changed here."""
import re
from datetime import datetime

from app.extensions import db
from app.models import Gateway, NeoSubZeroTruck
from app.services.live_collaboration import entity_version


def normalize_truck(value):
    number = re.sub(r'\s+', ' ', str(value or '').strip()).upper()
    if not number or len(number) > 32 or not re.fullmatch(r'[A-Z0-9][A-Z0-9 ._-]*', number):
        raise ValueError('Enter a truck identifier of 1–32 letters, numbers, spaces, dots, underscores or hyphens.')
    return number


def registered_trucks(gateway, *, active_only=False):
    query = NeoSubZeroTruck.query.filter_by(gateway_id=gateway.id)
    if active_only:
        query = query.filter_by(active=True)
    return query.order_by(NeoSubZeroTruck.number).all()


def manage_truck(gateway, action, values, *, actor_id):
    # Serializes first registration as well as activation changes for this gateway.
    Gateway.query.filter_by(id=gateway.id).populate_existing().with_for_update().one()
    if action == 'add_truck':
        number = normalize_truck(values.get('truck_number'))
        if NeoSubZeroTruck.query.filter_by(gateway_id=gateway.id, number=number).first():
            raise ValueError('This truck is already registered. Reactivate its existing record if needed.')
        row = NeoSubZeroTruck(gateway_id=gateway.id, number=number, active=True)
        db.session.add(row)
        previous = None
    else:
        row = NeoSubZeroTruck.query.filter_by(id=values.get('truck_id'), gateway_id=gateway.id).populate_existing().with_for_update().one_or_none()
        if row is None or values.get('expected_version') != entity_version(row):
            raise ValueError('Truck registry changed. Reload before retrying.')
        if values.get('confirm') != 'yes' or action not in {'activate_truck', 'deactivate_truck'}:
            raise ValueError('Confirm the truck activation change.')
        previous = row.active
        row.active = action == 'activate_truck'
        if row.active == previous:
            raise ValueError('This truck already has that status.')
    row.history = [*(row.history or []), dict(actor_id=actor_id, at_utc=datetime.utcnow().isoformat(), action=action, prior_active=previous, active=row.active)]
    return row


def validate_registered_truck(operation, number, *, existing=None):
    if not number:
        return ''
    normalized = normalize_truck(number)
    gateway_id = operation.gateway_id or Gateway.query.filter_by(code=operation.gateway_code).one().id
    # Retained assignments remain movable/readable even after deactivation. A new
    # truck selection must always come from the active permanent registry.
    row = NeoSubZeroTruck.query.filter_by(gateway_id=gateway_id, number=normalized).populate_existing().with_for_update().one_or_none()
    if not row or (not row.active and normalized != str(existing or '').strip().upper()):
        raise ValueError('Select an active registered deice truck in Settings.')
    return normalized
