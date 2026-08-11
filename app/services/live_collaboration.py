from datetime import datetime


def entity_version(entity):
    """Return a stable optimistic-concurrency token for a persisted entity."""
    updated_at = getattr(entity, "updated_at", None)
    if not updated_at:
        return ""
    if isinstance(updated_at, datetime):
        return updated_at.isoformat(timespec="microseconds")
    return str(updated_at)


def version_conflict(
    entity,
    expected_version,
    *,
    force_overwrite=False,
    field_conflicts=None,
):
    """Describe a stale edit without locking a row across browser activity."""
    expected = str(expected_version or "").strip()
    if force_overwrite or not expected:
        return None
    current = entity_version(entity)
    if expected == current:
        return None
    conflict = {
        "type": "stale_version",
        "entity_id": getattr(entity, "id", None),
        "expected_version": expected,
        "current_version": current,
        "message": "This item changed while you were editing. Review the latest value before saving.",
        "can_overwrite": True,
    }
    if field_conflicts:
        conflict["fields"] = field_conflicts
        first = field_conflicts[0]
        conflict["message"] = (
            f"{first['label']} changed from {first['original_display']} to "
            f"{first['current_display']} while you were editing."
        )
    return conflict


def changed_field_conflicts(original, current, submitted, labels=None):
    """Return fields that a stale full-row form would overwrite."""
    labels = labels or {}
    conflicts = []
    for field_name in current:
        if field_name == "expected_version":
            continue
        original_value = _comparable_value(original.get(field_name))
        current_value = _comparable_value(current.get(field_name))
        submitted_value = _comparable_value(submitted.get(field_name))
        if current_value == original_value or submitted_value == current_value:
            continue
        conflicts.append(
            {
                "field": field_name,
                "label": labels.get(field_name, field_name.replace("_", " ").title()),
                "original": original_value,
                "current": current_value,
                "submitted": submitted_value,
                "original_display": original_value or "blank",
                "current_display": current_value or "blank",
                "submitted_display": submitted_value or "blank",
            }
        )
    return conflicts


def _comparable_value(value):
    if value is None:
        return ""
    return str(value).strip()


def resolved_item_conflict(entity_id=None):
    return {
        "type": "item_changed",
        "entity_id": entity_id,
        "message": "This item has already changed or been resolved. The latest state will be loaded.",
        "can_overwrite": False,
    }
