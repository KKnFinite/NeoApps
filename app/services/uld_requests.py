from datetime import datetime, timedelta
from types import SimpleNamespace

from app.extensions import db
from app.models import NeoErmacUldRequest, NeoSektorUldOnTheWayEvent
from app.services.gateway_matrix import gateway_timezone
from app.services.time_display import format_local_hhmm


ULD_TYPES = ("A2", "A1", "AMP")
ULD_REQUEST_FIELDS = {
    "A2": "a2_count",
    "A1": "a1_count",
    "AMP": "amp_count",
}
ON_THE_WAY_MINUTES = 5


def get_uld_request(gateway, door, setup_needed=None):
    normalized_door = normalize_door(door)
    if not normalized_door:
        return None

    query = NeoErmacUldRequest.query.filter_by(
        gateway_id=gateway.id,
        door=normalized_door,
    )
    if setup_needed is not None:
        query = query.filter_by(setup_needed=bool(setup_needed))

    return query.order_by(
        NeoErmacUldRequest.setup_needed.desc(),
        NeoErmacUldRequest.updated_at.asc(),
        NeoErmacUldRequest.id.asc(),
    ).first()


def active_uld_requests_for_door(gateway, door):
    normalized_door = normalize_door(door)
    if not normalized_door:
        return []

    return [
        request_record
        for request_record in NeoErmacUldRequest.query.filter_by(
            gateway_id=gateway.id,
            door=normalized_door,
        )
        .order_by(
            NeoErmacUldRequest.setup_needed.desc(),
            NeoErmacUldRequest.updated_at.asc(),
            NeoErmacUldRequest.id.asc(),
        )
        .all()
        if request_has_counts(request_record)
    ]


def aggregate_uld_request_for_door(gateway, door):
    request_records = active_uld_requests_for_door(gateway, door)
    if not request_records:
        return None

    return SimpleNamespace(
        id=None,
        door=normalize_door(door),
        a2_count=sum(max(request_record.a2_count or 0, 0) for request_record in request_records),
        a1_count=sum(max(request_record.a1_count or 0, 0) for request_record in request_records),
        amp_count=sum(max(request_record.amp_count or 0, 0) for request_record in request_records),
        setup_needed=any(bool(request_record.setup_needed) for request_record in request_records),
    )


def update_uld_request(gateway, door, counts, setup_needed=False, now=None):
    now = now or datetime.utcnow()
    normalized_door = normalize_door(door)
    if not normalized_door:
        raise ValueError("Select a door.")

    normalized_counts = normalize_uld_counts(counts)
    if not any(normalized_counts.values()):
        raise ValueError("Request at least one ULD.")

    request_record = get_uld_request(gateway, normalized_door, setup_needed=setup_needed)
    if request_record is None:
        request_record = NeoErmacUldRequest(
            gateway_id=gateway.id,
            door=normalized_door,
            setup_needed=bool(setup_needed),
            created_at=now,
            updated_at=now,
        )
        db.session.add(request_record)

    for uld_type, field_name in ULD_REQUEST_FIELDS.items():
        current_count = max(getattr(request_record, field_name) or 0, 0)
        setattr(request_record, field_name, current_count + normalized_counts[uld_type])

    request_record.setup_needed = bool(setup_needed)
    request_record.updated_at = now
    db.session.flush()
    return request_record


def clear_uld_requests_for_door(gateway, door, setup_needed=None):
    normalized_door = normalize_door(door)
    if not normalized_door:
        raise ValueError("Select a door.")

    query = NeoErmacUldRequest.query.filter_by(
        gateway_id=gateway.id,
        door=normalized_door,
    )
    if setup_needed is not None:
        query = query.filter_by(setup_needed=bool(setup_needed))

    for request_record in query.all():
        db.session.delete(request_record)

    db.session.flush()
    return None


def update_uld_request_from_form(gateway, door, form_data):
    should_clear = form_data.get("clear_uld_request") == "1"
    if should_clear:
        return clear_uld_requests_for_door(
            gateway,
            door,
            setup_needed=form_data.get("setup_needed") == "on",
        )

    counts = {
        "A2": form_data.get("uld_a2_count"),
        "A1": form_data.get("uld_a1_count"),
        "AMP": form_data.get("uld_amp_count"),
    }
    return update_uld_request(
        gateway,
        door,
        counts,
        setup_needed=form_data.get("setup_needed") == "on",
    )


def discharge_context(gateway, now=None):
    return {
        "requests": active_discharge_request_views(gateway, now),
        "uld_types": ULD_TYPES,
    }


def discharge_state_payload(gateway, now=None):
    return {
        "requests": [_request_payload(row) for row in active_discharge_request_views(gateway, now)],
        "uld_types": list(ULD_TYPES),
    }


def active_discharge_request_views(gateway, now=None):
    return [row for row in active_request_views(gateway, now) if row.get("id")]


def door_uld_state_payload(gateway, door, now=None):
    normalized_door = normalize_door(door)
    if not normalized_door:
        raise ValueError("Select a door.")

    request_records = active_uld_requests_for_door(gateway, normalized_door)
    return {
        "door": normalized_door,
        "request": _aggregate_request_counts_payload(request_records),
        "requests": [
            _single_request_counts_payload(gateway, request_record)
            for request_record in request_records
        ],
        "on_the_way_events": [
            _event_payload(event)
            for event in active_on_the_way_event_views(gateway, normalized_door, now)
        ],
    }


def active_request_views(gateway, now=None):
    now = now or datetime.utcnow()
    active_events_by_door = {}
    for event in active_on_the_way_events(gateway, now=now):
        active_events_by_door.setdefault(event.door, []).append(event)

    requests = []
    request_doors = set()
    for request_record in NeoErmacUldRequest.query.filter_by(gateway_id=gateway.id).all():
        if not request_has_counts(request_record):
            continue

        requests.append(_request_view(gateway, request_record, active_events_by_door, now))
        request_doors.add(request_record.door)

    for door, events in active_events_by_door.items():
        if door in request_doors:
            continue
        requests.append(_event_only_request_view(gateway, door, events))

    return sorted(
        requests,
        key=lambda row: (
            not row["setup_needed"],
            row["updated_at"] or row["created_at"] or datetime.min,
            row["door"],
        ),
    )


def send_uld_on_the_way(gateway, door, uld_type, quantity, request_id=None, now=None):
    normalized_type = normalize_uld_type(uld_type)
    if not normalized_type:
        raise ValueError("Select a valid ULD type.")

    events = send_uld_totals_on_the_way(
        gateway,
        door,
        {normalized_type: quantity},
        request_id=request_id,
        now=now,
    )
    return events[0]


def send_uld_totals_on_the_way(gateway, door, counts, request_id=None, now=None):
    now = now or datetime.utcnow()
    normalized_door = normalize_door(door)
    if not normalized_door:
        raise ValueError("Select a door.")

    normalized_counts = normalize_uld_counts(counts)
    if not any(normalized_counts.values()):
        raise ValueError("Send at least one ULD.")

    request_record = get_uld_request_by_id(gateway, request_id, normalized_door)
    if request_record is None:
        request_record = get_uld_request(gateway, normalized_door)
    if request_record is None:
        raise ValueError("No active ULD request for this door.")

    events = []
    for normalized_type, requested_quantity in normalized_counts.items():
        if requested_quantity <= 0:
            continue

        field_name = ULD_REQUEST_FIELDS[normalized_type]
        available_quantity = max(getattr(request_record, field_name) or 0, 0)
        setattr(request_record, field_name, max(available_quantity - requested_quantity, 0))

        event = NeoSektorUldOnTheWayEvent(
            gateway_id=gateway.id,
            door=normalized_door,
            uld_type=normalized_type,
            quantity=requested_quantity,
            sent_at_utc=now,
            expires_at_utc=now + timedelta(minutes=ON_THE_WAY_MINUTES),
        )
        db.session.add(event)
        events.append(event)

    if not request_has_counts(request_record):
        db.session.delete(request_record)
    db.session.flush()
    return events


def get_uld_request_by_id(gateway, request_id, door=None):
    try:
        request_id = int(request_id)
    except (TypeError, ValueError):
        return None
    if request_id <= 0:
        return None

    query = NeoErmacUldRequest.query.filter_by(
        id=request_id,
        gateway_id=gateway.id,
    )
    normalized_door = normalize_door(door)
    if normalized_door:
        query = query.filter_by(door=normalized_door)
    return query.first()


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
    return [
        _event_view(gateway, event)
        for event in active_on_the_way_events(gateway, door, now)
    ]


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


def _request_view(gateway, request_record, active_events_by_door, now):
    events = active_events_by_door.get(request_record.door, [])
    timezone_name = gateway_timezone(gateway)
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
        "updated_at_label": format_local_hhmm(request_record.updated_at, timezone_name),
        "on_the_way_events": [_event_view(gateway, event) for event in events],
    }


def _event_only_request_view(gateway, door, events):
    first_event = events[0] if events else None
    timezone_name = gateway_timezone(gateway)
    return {
        "id": None,
        "door": door,
        "counts": {uld_type: 0 for uld_type in ULD_TYPES},
        "setup_needed": False,
        "created_at": getattr(first_event, "sent_at_utc", None),
        "updated_at": getattr(first_event, "sent_at_utc", None),
        "updated_at_label": format_local_hhmm(
            getattr(first_event, "sent_at_utc", None),
            timezone_name,
        ),
        "on_the_way_events": [_event_view(gateway, event) for event in events],
    }


def _request_payload(row):
    return {
        "id": row["id"],
        "door": row["door"],
        "counts": row["counts"],
        "setup_needed": row["setup_needed"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "updated_at_label": row.get("updated_at_label"),
        "on_the_way_events": [_event_payload(event) for event in row["on_the_way_events"]],
    }


def _single_request_counts_payload(gateway, request_record):
    timezone_name = gateway_timezone(gateway)
    return {
        "id": request_record.id,
        "counts": {
            uld_type: max(getattr(request_record, field_name, 0) or 0, 0)
            for uld_type, field_name in ULD_REQUEST_FIELDS.items()
        },
        "setup_needed": bool(getattr(request_record, "setup_needed", False)),
        "created_at": request_record.created_at.isoformat() if request_record.created_at else None,
        "updated_at": request_record.updated_at.isoformat() if request_record.updated_at else None,
        "updated_at_label": format_local_hhmm(request_record.updated_at, timezone_name),
    }


def _aggregate_request_counts_payload(request_records):
    request_records = list(request_records or [])
    return {
        "counts": {
            uld_type: sum(
                max(getattr(request_record, field_name, 0) or 0, 0)
                for request_record in request_records
            )
            for uld_type, field_name in ULD_REQUEST_FIELDS.items()
        },
        "setup_needed": any(bool(request_record.setup_needed) for request_record in request_records),
    }


def _event_view(gateway, event):
    timezone_name = gateway_timezone(gateway)
    sent_label = format_local_hhmm(event.sent_at_utc, timezone_name)
    return {
        "id": event.id,
        "door": event.door,
        "uld_type": event.uld_type,
        "quantity": max(event.quantity or 0, 0),
        "sent_at": event.sent_at_utc,
        "expires_at": event.expires_at_utc,
        "label": (
            f"{max(event.quantity or 0, 0)} {_plural_uld(event.uld_type, event.quantity)} "
            f"sent at {sent_label}"
        ),
    }


def _event_payload(event):
    return {
        "id": event["id"],
        "door": event["door"],
        "uld_type": event["uld_type"],
        "quantity": event["quantity"],
        "label": event["label"],
        "sent_at": event["sent_at"].isoformat() if event.get("sent_at") else None,
        "expires_at": event["expires_at"].isoformat() if event.get("expires_at") else None,
    }


def _plural_uld(uld_type, quantity):
    label = normalize_uld_type(uld_type) or str(uld_type or "").strip().upper()
    return label if int(quantity or 0) == 1 else f"{label}s"
