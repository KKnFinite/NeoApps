from datetime import datetime, timedelta

from app.extensions import db
from app.models import NeoErmacUldRequest, NeoSektorUldOnTheWayEvent


ULD_TYPES = ("A2", "A1", "AMP")
ULD_REQUEST_FIELDS = {
    "A2": "a2_count",
    "A1": "a1_count",
    "AMP": "amp_count",
}
ON_THE_WAY_MINUTES = 5


def get_uld_request(gateway, door):
    normalized_door = normalize_door(door)
    if not normalized_door:
        return None

    return NeoErmacUldRequest.query.filter_by(
        gateway_id=gateway.id,
        door=normalized_door,
    ).first()


def update_uld_request(gateway, door, counts, setup_needed=False):
    normalized_door = normalize_door(door)
    if not normalized_door:
        raise ValueError("Select a door.")

    request_record = get_uld_request(gateway, normalized_door)
    if request_record is None:
        request_record = NeoErmacUldRequest(gateway_id=gateway.id, door=normalized_door)
        db.session.add(request_record)

    normalized_counts = normalize_uld_counts(counts)
    for uld_type, field_name in ULD_REQUEST_FIELDS.items():
        setattr(request_record, field_name, normalized_counts[uld_type])

    request_record.setup_needed = bool(setup_needed)
    db.session.flush()
    return request_record


def update_uld_request_from_form(gateway, door, form_data):
    should_clear = form_data.get("clear_uld_request") == "1"
    counts = {
        "A2": 0 if should_clear else form_data.get("uld_a2_count"),
        "A1": 0 if should_clear else form_data.get("uld_a1_count"),
        "AMP": 0 if should_clear else form_data.get("uld_amp_count"),
    }
    return update_uld_request(
        gateway,
        door,
        counts,
        setup_needed=False if should_clear else form_data.get("setup_needed") == "on",
    )


def discharge_context(gateway, now=None):
    return {
        "requests": active_request_views(gateway, now),
        "uld_types": ULD_TYPES,
    }


def active_request_views(gateway, now=None):
    now = now or datetime.utcnow()
    active_events_by_door = {}
    for event in active_on_the_way_events(gateway, now=now):
        active_events_by_door.setdefault(event.door, []).append(event)

    requests = []
    for request_record in NeoErmacUldRequest.query.filter_by(gateway_id=gateway.id).all():
        has_active_events = bool(active_events_by_door.get(request_record.door))
        if not request_has_counts(request_record) and not has_active_events:
            continue

        requests.append(_request_view(request_record, active_events_by_door, now))

    return sorted(
        requests,
        key=lambda row: (
            not row["setup_needed"],
            row["updated_at"] or row["created_at"] or datetime.min,
            row["door"],
        ),
    )


def send_uld_on_the_way(gateway, door, uld_type, quantity, now=None):
    now = now or datetime.utcnow()
    normalized_door = normalize_door(door)
    normalized_type = normalize_uld_type(uld_type)
    if not normalized_door:
        raise ValueError("Select a door.")
    if not normalized_type:
        raise ValueError("Select a valid ULD type.")

    requested_quantity = clean_count(quantity)
    if requested_quantity <= 0:
        raise ValueError("Send quantity must be greater than zero.")

    request_record = get_uld_request(gateway, normalized_door)
    if request_record is None:
        raise ValueError("No active ULD request for this door.")

    field_name = ULD_REQUEST_FIELDS[normalized_type]
    available_quantity = max(getattr(request_record, field_name) or 0, 0)
    send_quantity = min(requested_quantity, available_quantity)
    if send_quantity <= 0:
        raise ValueError(f"No {normalized_type} requested for {normalized_door}.")

    setattr(request_record, field_name, available_quantity - send_quantity)

    event = NeoSektorUldOnTheWayEvent(
        gateway_id=gateway.id,
        door=normalized_door,
        uld_type=normalized_type,
        quantity=send_quantity,
        sent_at_utc=now,
        expires_at_utc=now + timedelta(minutes=ON_THE_WAY_MINUTES),
    )
    db.session.add(event)
    db.session.flush()
    return event


def active_on_the_way_events(gateway, door=None, now=None):
    now = now or datetime.utcnow()
    query = NeoSektorUldOnTheWayEvent.query.filter(
        NeoSektorUldOnTheWayEvent.gateway_id == gateway.id,
        NeoSektorUldOnTheWayEvent.expires_at_utc > now,
    )
    normalized_door = normalize_door(door)
    if normalized_door:
        query = query.filter(NeoSektorUldOnTheWayEvent.door == normalized_door)

    return query.order_by(
        NeoSektorUldOnTheWayEvent.sent_at_utc.asc(),
        NeoSektorUldOnTheWayEvent.id.asc(),
    ).all()


def active_on_the_way_event_views(gateway, door=None, now=None):
    return [_event_view(event) for event in active_on_the_way_events(gateway, door, now)]


def normalize_uld_counts(counts):
    counts = counts or {}
    return {uld_type: clean_count(counts.get(uld_type)) for uld_type in ULD_TYPES}


def clean_count(value, default=0):
    value = str(value if value is not None else "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("ULD counts must be whole numbers.") from exc
    if parsed < 0:
        raise ValueError("ULD counts cannot be negative.")
    return parsed


def normalize_uld_type(value):
    normalized = str(value or "").strip().upper()
    return normalized if normalized in ULD_TYPES else ""


def normalize_door(value):
    value = str(value or "").strip().upper()
    if not value:
        return ""
    number = value[1:] if value.startswith("D") else value
    if not number.isdigit():
        return ""
    return f"D{int(number)}"


def request_has_counts(request_record):
    return any((getattr(request_record, field_name) or 0) > 0 for field_name in ULD_REQUEST_FIELDS.values())


def _request_view(request_record, active_events_by_door, now):
    events = active_events_by_door.get(request_record.door, [])
    return {
        "id": request_record.id,
        "door": request_record.door,
        "counts": {
            uld_type: max(getattr(request_record, field_name) or 0, 0)
            for uld_type, field_name in ULD_REQUEST_FIELDS.items()
        },
        "setup_needed": bool(request_record.setup_needed),
        "created_at": request_record.created_at,
        "updated_at": request_record.updated_at,
        "on_the_way_events": [_event_view(event) for event in events],
    }


def _event_view(event):
    return {
        "id": event.id,
        "door": event.door,
        "uld_type": event.uld_type,
        "quantity": max(event.quantity or 0, 0),
        "sent_at": event.sent_at_utc,
        "expires_at": event.expires_at_utc,
        "label": (
            f"{max(event.quantity or 0, 0)} {_plural_uld(event.uld_type, event.quantity)} "
            f"sent at {event.sent_at_utc.strftime('%H:%M')}"
        ),
    }


def _plural_uld(uld_type, quantity):
    label = normalize_uld_type(uld_type) or str(uld_type or "").strip().upper()
    return label if int(quantity or 0) == 1 else f"{label}s"
