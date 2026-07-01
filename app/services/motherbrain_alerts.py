from sqlalchemy import and_, or_

from app.models import MotherBrainAlert, SortDateOperation


MOTHERBRAIN_ALERT_SCOPE = "motherbrain"
PARKING_CONFLICT_ALERT_PERMISSION = "motherbrain.parking_conflicts.view"
LEGACY_SORT_SCOPED_ALERT_KEY_PREFIXES = ("parking-physical",)

SEVERITY_LABELS = {
    "critical": "CRITICAL",
    "warning": "WARNING",
    "info": "INFO",
}

SEVERITY_ORDER = {
    "critical": 0,
    "warning": 1,
    "info": 2,
}


def motherbrain_alert_context(gateway, can_view_permission=None, limit=20, operation=None):
    alerts = active_motherbrain_alerts(
        gateway,
        can_view_permission=can_view_permission,
        limit=limit,
        operation=operation,
    )
    return {
        "alerts": alerts,
        "count": len(alerts),
        "has_alerts": bool(alerts),
        "empty_message": "No active MotherBrain alerts",
    }


def active_motherbrain_alerts(gateway, can_view_permission=None, limit=20, operation=None):
    if not gateway:
        return []

    query = (
        MotherBrainAlert.query.filter_by(
            gateway_id=gateway.id,
            scope=MOTHERBRAIN_ALERT_SCOPE,
            active=True,
            acknowledged=False,
        )
    )

    operation_id = getattr(operation, "id", None)
    if operation_id:
        query = query.filter(_operation_or_global_alert_filter(operation_id))
    else:
        query = query.filter(_global_alert_filter())

    query = query.order_by(
        MotherBrainAlert.created_at.desc(),
        MotherBrainAlert.id.desc(),
    ).limit(max(1, int(limit or 20)))

    alerts = query.all()
    visible_alerts = [
        alert for alert in alerts if _alert_is_visible(alert, can_view_permission)
    ]
    visible_alerts.sort(
        key=lambda alert: (
            SEVERITY_ORDER.get(normalize_alert_severity(alert.severity), 99),
            alert.created_at,
            alert.id,
        )
    )
    return visible_alerts


def motherbrain_alert_operation_for_request(gateway, request_obj):
    if not gateway or not request_obj:
        return None

    operation_id = _request_operation_id(request_obj)
    if operation_id:
        operation = SortDateOperation.query.filter_by(
            id=operation_id,
            gateway_code=gateway.code,
        ).first()
        if operation:
            return operation

    from app.services.gateway_matrix import current_operations_for_gateway

    operations = current_operations_for_gateway(gateway)
    return operations[0] if operations else None


def normalize_alert_severity(value):
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SEVERITY_LABELS else "info"


def alert_severity_label(value):
    return SEVERITY_LABELS[normalize_alert_severity(value)]


def _alert_is_visible(alert, can_view_permission):
    permission_key = str(alert.permission_key or "").strip()
    if not permission_key:
        return True
    if not can_view_permission:
        return False
    return bool(can_view_permission(permission_key))


def _operation_or_global_alert_filter(operation_id):
    return or_(
        MotherBrainAlert.sort_date_operation_id == operation_id,
        and_(
            MotherBrainAlert.sort_date_operation_id.is_(None),
            _legacy_sort_scoped_alert_key_filter(operation_id),
        ),
        and_(
            MotherBrainAlert.sort_date_operation_id.is_(None),
            _global_alert_key_filter(),
        ),
    )


def _global_alert_filter():
    return and_(
        MotherBrainAlert.sort_date_operation_id.is_(None),
        _global_alert_key_filter(),
    )


def _global_alert_key_filter():
    not_legacy_sort_scoped = [
        ~MotherBrainAlert.alert_key.like(f"{prefix}:%")
        for prefix in LEGACY_SORT_SCOPED_ALERT_KEY_PREFIXES
    ]
    return or_(
        MotherBrainAlert.alert_key.is_(None),
        MotherBrainAlert.alert_key == "",
        and_(*not_legacy_sort_scoped),
    )


def _legacy_sort_scoped_alert_key_filter(operation_id):
    return or_(
        *[
            MotherBrainAlert.alert_key.like(f"{prefix}:{operation_id}:%")
            for prefix in LEGACY_SORT_SCOPED_ALERT_KEY_PREFIXES
        ]
    )


def _request_operation_id(request_obj):
    view_args = getattr(request_obj, "view_args", None) or {}
    raw_operation_id = view_args.get("operation_id")
    if raw_operation_id is None:
        raw_operation_id = getattr(request_obj, "values", {}).get("operation_id")
    try:
        return int(raw_operation_id)
    except (TypeError, ValueError):
        return None
