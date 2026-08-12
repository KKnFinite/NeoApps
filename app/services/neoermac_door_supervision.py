"""Per-user current-sort door supervision preferences."""

import json

from app.extensions import db
from app.models import NeoErmacDoorSupervision
from app.services.neoermac_door_view import normalize_door


def door_supervision_for_user(
    user,
    operation,
    available_doors,
    requested_door=None,
):
    """Resolve tabs and active door, recording an explicit door navigation."""
    available = _normalized_available_doors(available_doors)
    requested = normalize_door(requested_door)
    if requested not in available:
        requested = ""

    if not operation or not getattr(user, "is_authenticated", False):
        return _payload([], requested or None, operation)

    record = _record_for(user.id, operation.id)
    selected = _selected_doors(record, available)
    active = normalize_door(getattr(record, "active_door", ""))
    changed = False

    if requested:
        if requested not in selected:
            selected.append(requested)
            selected = _sort_doors(selected, available)
            changed = True
        if active != requested:
            active = requested
            changed = True
    elif active not in selected:
        active = selected[0] if selected else ""
        changed = bool(record and record.active_door)

    if changed:
        record = record or NeoErmacDoorSupervision(
            user_id=user.id,
            sort_date_operation_id=operation.id,
        )
        record.selected_doors_json = json.dumps(selected)
        record.active_door = active or None
        db.session.add(record)
        db.session.flush()

    return _payload(selected, active or None, operation)


def save_door_supervision(
    user,
    operation,
    selected_doors,
    available_doors,
    active_door=None,
):
    """Replace one user's selected doors for one current-sort operation."""
    if not operation:
        raise ValueError("No current sort operation is available.")
    if not getattr(user, "is_authenticated", False):
        raise ValueError("Sign in to manage supervised doors.")

    available = _normalized_available_doors(available_doors)
    selected = _sort_doors(selected_doors, available)
    requested_active = normalize_door(active_door)
    record = _record_for(user.id, operation.id)
    previous_active = normalize_door(getattr(record, "active_door", ""))

    if requested_active in selected:
        active = requested_active
    elif previous_active in selected:
        active = previous_active
    else:
        active = selected[0] if selected else ""

    record = record or NeoErmacDoorSupervision(
        user_id=user.id,
        sort_date_operation_id=operation.id,
    )
    record.selected_doors_json = json.dumps(selected)
    record.active_door = active or None
    db.session.add(record)
    db.session.flush()
    return _payload(selected, active or None, operation)


def _record_for(user_id, operation_id):
    return NeoErmacDoorSupervision.query.filter_by(
        user_id=user_id,
        sort_date_operation_id=operation_id,
    ).first()


def _selected_doors(record, available):
    if not record:
        return []
    try:
        values = json.loads(record.selected_doors_json or "[]")
    except (TypeError, ValueError):
        values = []
    return _sort_doors(values if isinstance(values, list) else [], available)


def _normalized_available_doors(available_doors):
    result = []
    for value in available_doors or ():
        door = normalize_door(value)
        if door and door not in result:
            result.append(door)
    return result


def _sort_doors(values, available):
    selected = {normalize_door(value) for value in values or ()}
    selected.discard("")
    return [door for door in available if door in selected]


def _payload(selected_doors, active_door, operation):
    return {
        "selected_doors": list(selected_doors),
        "active_door": active_door,
        "operation_id": operation.id if operation else None,
    }
