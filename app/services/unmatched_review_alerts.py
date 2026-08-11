"""Aggregate, permission-scoped alerts for unmatched planning review queues."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    MotherBrainAlert,
    MotherBrainAlertUserState,
    SortDateOperation,
    SortTimelineSettings,
)
from app.services.gateway_matrix import current_gateway_local_datetime


UNMATCHED_REVIEW_ALERT_PERMISSION = "neomotherbrain.flight_api_review.edit"
UNMATCHED_REVIEW_ALERT_KEY_PREFIX = "flight-api-unmatched"
UNMATCHED_REVIEW_QUEUE_TYPES = {"arrival", "departure"}


def pending_review_key_sets(operation):
    result = {"arrival": set(), "departure": set()}
    if not operation or not getattr(operation, "id", None):
        return result
    rows = (
        FlightApiReviewItem.query.filter_by(
            sort_date_operation_id=operation.id,
            review_status="pending",
        )
        .with_entities(
            FlightApiReviewItem.mission_type,
            FlightApiReviewItem.review_key,
        )
        .all()
    )
    for mission_type, review_key in rows:
        if mission_type in result and review_key:
            result[mission_type].add(review_key)
    return result


def sync_unmatched_review_alerts_for_operation(
    operation,
    *,
    previous_keys=None,
    new_keys_by_type=None,
    now=None,
):
    current_keys = pending_review_key_sets(operation)
    changed = False
    for mission_type in sorted(UNMATCHED_REVIEW_QUEUE_TYPES):
        if new_keys_by_type is not None:
            new_keys = set(new_keys_by_type.get(mission_type) or ())
        elif previous_keys is not None:
            new_keys = current_keys[mission_type] - set(
                previous_keys.get(mission_type) or ()
            )
        else:
            new_keys = set()
        result = sync_unmatched_review_alert(
            operation,
            mission_type,
            pending_keys=current_keys[mission_type],
            new_review_keys=new_keys,
            now=now,
        )
        changed = bool(result["changed"] or changed)
    return {"changed": changed, "current_keys": current_keys}


def sync_unmatched_review_alert(
    operation,
    mission_type,
    *,
    pending_keys=None,
    new_review_keys=None,
    now=None,
):
    mission_type = _queue_type(mission_type)
    _acquire_alert_scope_lock(operation, mission_type)
    timestamp = _utc_naive(now)
    if pending_keys is None:
        pending_keys = pending_review_key_sets(operation)[mission_type]
    pending_keys = set(pending_keys or ())
    new_review_keys = set(new_review_keys or ())
    count = len(pending_keys)
    sort_ended = unmatched_alert_sort_has_ended(operation, now=now)

    alerts = (
        MotherBrainAlert.query.filter_by(
            gateway_id=operation.gateway_id,
            sort_date_operation_id=operation.id,
            scope="motherbrain",
            alert_key=unmatched_review_alert_key(mission_type),
        )
        .order_by(MotherBrainAlert.id.asc())
        .all()
    )
    alert = alerts[0] if alerts else None
    was_active = bool(alert and alert.active and not alert.acknowledged)
    changed = False
    for duplicate in alerts[1:]:
        if duplicate.active or not duplicate.acknowledged:
            duplicate.active = False
            duplicate.acknowledged = True
            duplicate.acknowledged_at = timestamp
            changed = True

    should_be_active = count > 0 and not sort_ended
    title, message, related_label = _alert_copy(mission_type, count)
    if alert is None and should_be_active:
        alert = MotherBrainAlert(
            gateway_id=operation.gateway_id,
            sort_date_operation_id=operation.id,
            gateway_code=operation.gateway_code,
            scope="motherbrain",
            alert_key=unmatched_review_alert_key(mission_type),
            severity="warning",
            title=title,
            message=message,
            related_url=unmatched_review_alert_url(operation, mission_type),
            related_label=related_label,
            permission_key=UNMATCHED_REVIEW_ALERT_PERMISSION,
            active=True,
            acknowledged=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.session.add(alert)
        db.session.flush()
        changed = True

    if alert is None:
        return {"alert": None, "count": count, "changed": changed}

    values = {
        "gateway_code": operation.gateway_code,
        "title": title,
        "message": message,
        "related_url": unmatched_review_alert_url(operation, mission_type),
        "related_label": related_label,
        "permission_key": UNMATCHED_REVIEW_ALERT_PERMISSION,
        "severity": "warning",
    }
    for field_name, value in values.items():
        if getattr(alert, field_name) != value:
            setattr(alert, field_name, value)
            changed = True

    if alert.active != should_be_active:
        alert.active = should_be_active
        changed = True
    acknowledged = not should_be_active
    if alert.acknowledged != acknowledged:
        alert.acknowledged = acknowledged
        changed = True
    if acknowledged and alert.acknowledged_at is None:
        alert.acknowledged_at = timestamp
        changed = True
    elif not acknowledged and alert.acknowledged_at is not None:
        alert.acknowledged_at = None
        changed = True

    reactivated = should_be_active and not was_active
    if should_be_active and (new_review_keys or reactivated):
        MotherBrainAlertUserState.query.filter_by(alert_id=alert.id).delete(
            synchronize_session=False
        )
        alert.created_at = timestamp
        changed = True

    if changed:
        alert.updated_at = timestamp
    db.session.flush()
    return {"alert": alert, "count": count, "changed": changed}


def mark_unmatched_review_alert_read(alert, user, now=None):
    if not is_unmatched_review_alert(alert) or not alert.active or alert.acknowledged:
        return None
    state = MotherBrainAlertUserState.query.filter_by(
        alert_id=alert.id,
        user_id=user.id,
    ).first()
    timestamp = _utc_naive(now)
    if state is None:
        state = MotherBrainAlertUserState(
            alert_id=alert.id,
            user_id=user.id,
            read_at=timestamp,
        )
        db.session.add(state)
    else:
        state.read_at = timestamp
        state.updated_at = timestamp
    db.session.flush()
    return state


def expire_unmatched_review_alerts(gateway, now=None):
    alerts = (
        MotherBrainAlert.query.filter(
            MotherBrainAlert.gateway_id == gateway.id,
            MotherBrainAlert.scope == "motherbrain",
            MotherBrainAlert.alert_key.like(
                f"{UNMATCHED_REVIEW_ALERT_KEY_PREFIX}:%"
            ),
            MotherBrainAlert.active.is_(True),
        )
        .all()
    )
    changed = 0
    timestamp = _utc_naive(now)
    for alert in alerts:
        operation = db.session.get(SortDateOperation, alert.sort_date_operation_id)
        if not operation or not unmatched_alert_sort_has_ended(operation, now=now):
            continue
        alert.active = False
        alert.acknowledged = True
        alert.acknowledged_at = timestamp
        alert.updated_at = timestamp
        changed += 1
    if changed:
        db.session.flush()
    return changed


def unmatched_alert_sort_has_ended(operation, now=None):
    setting = _sort_setting_for_operation(operation)
    if not (
        setting
        and setting.sort_window_start_local is not None
        and setting.sort_window_end_local is not None
    ):
        return False
    start_local = datetime.combine(
        operation.sort_date,
        setting.sort_window_start_local,
    )
    end_local = datetime.combine(
        operation.sort_date,
        setting.sort_window_end_local,
    )
    if end_local <= start_local:
        end_local += timedelta(days=1)
    local_now = current_gateway_local_datetime(operation.gateway, now=now)
    return local_now >= end_local


def unmatched_review_alert_key(mission_type):
    return f"{UNMATCHED_REVIEW_ALERT_KEY_PREFIX}:{_queue_type(mission_type)}"


def unmatched_review_alert_url(operation, mission_type):
    return (
        "/motherbrain/flight-api-review"
        f"?operation_id={operation.id}&mission_type={_queue_type(mission_type)}"
    )


def is_unmatched_review_alert(alert):
    return bool(
        alert
        and str(getattr(alert, "alert_key", "") or "").startswith(
            f"{UNMATCHED_REVIEW_ALERT_KEY_PREFIX}:"
        )
    )


def _alert_copy(mission_type, count):
    label = "Arrival" if mission_type == "arrival" else "Departure"
    plural = label.lower() if count == 1 else f"{label.lower()}s"
    return (
        f"Unmatched {label}s",
        f"{count} unmatched {plural} awaiting review.",
        f"REVIEW {label.upper()}S",
    )


def _sort_setting_for_operation(operation):
    settings = SortTimelineSettings.query.filter_by(
        gateway_id=operation.gateway_id,
    ).first()
    if not settings:
        return None
    return next(
        (
            setting
            for setting in settings.sort_settings
            if setting.sort_name == operation.sort_name
        ),
        None,
    )


def _acquire_alert_scope_lock(operation, mission_type):
    bind = db.session.get_bind()
    if not bind or bind.dialect.name != "postgresql":
        return
    queue_lock = 1 if mission_type == "arrival" else 2
    db.session.connection().execute(
        text("SELECT pg_advisory_xact_lock(:operation_id, :queue_lock)"),
        {"operation_id": int(operation.id), "queue_lock": queue_lock},
    )


def _queue_type(value):
    value = str(value or "").strip().lower()
    if value not in UNMATCHED_REVIEW_QUEUE_TYPES:
        raise ValueError("Unmatched review queue type must be arrival or departure.")
    return value


def _utc_naive(value=None):
    value = value or datetime.utcnow()
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
