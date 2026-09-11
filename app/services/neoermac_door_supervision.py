"""Per-user gateway door supervision preferences."""

import json

from app.extensions import db
from app.models import NeoErmacDoorPreference
from app.services.neoermac_door_view import normalize_door


def door_supervision_for_user(
    user,
    gateway,
    available_doors,
    requested_door=None,
):
    """Resolve tabs and active door, recording an explicit door navigation."""
    available = _normalized_available_doors(available_doors)
    requested = normalize_door(requested_door)
    if requested not in available:
        requested = ""

    if not gateway or not getattr(user, "is_authenticated", False):
        return _payload([], requested or None, gateway, changed=False)

    record = _record_for(user.id, gateway.id)
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
        record = record or NeoErmacDoorPreference(
            user_id=user.id,
            gateway_id=gateway.id,
        )
        record.selected_doors_json = json.dumps(selected)
        record.active_door = active or None
        db.session.add(record)
        db.session.flush()

    return _payload(selected, active or None, gateway, changed=changed)


def save_door_supervision(
    user,
    gateway,
    selected_doors,
    available_doors,
    active_door=None,
):
    """Replace one user's selected doors for one gateway."""
    if not gateway:
        raise ValueError("No gateway is available.")
    if not getattr(user, "is_authenticated", False):
        raise ValueError("Sign in to manage supervised doors.")

    available = _normalized_available_doors(available_doors)
    selected = _sort_doors(selected_doors, available)
    requested_active = normalize_door(active_door)
    record = _record_for(user.id, gateway.id)
    previous_active = normalize_door(getattr(record, "active_door", ""))

    if requested_active in selected:
        active = requested_active
    elif previous_active in selected:
        active = previous_active
    else:
        active = selected[0] if selected else ""

    record = record or NeoErmacDoorPreference(
        user_id=user.id,
        gateway_id=gateway.id,
    )
    record.selected_doors_json = json.dumps(selected)
    record.active_door = active or None
    db.session.add(record)
    db.session.flush()
    return _payload(selected, active or None, gateway, changed=True)


def supervised_doors_for_user(user, gateway, available_doors):
    """Return one user's persisted supervised doors without changing them."""
    if not gateway or not getattr(user, "is_authenticated", False):
        return []
    available = _normalized_available_doors(available_doors)
    record = _record_for(user.id, gateway.id)
    return _selected_doors(record, available)


def _record_for(user_id, gateway_id):
    return NeoErmacDoorPreference.query.filter_by(
        user_id=user_id,
        gateway_id=gateway_id,
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


def _payload(selected_doors, active_door, gateway, *, changed):
    return {
        "selected_doors": list(selected_doors),
        "active_door": active_door,
        "gateway_id": gateway.id if gateway else None,
        "persistent_state_changed": changed,
    }
