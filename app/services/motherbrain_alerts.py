from app.models import MotherBrainAlert


MOTHERBRAIN_ALERT_SCOPE = "motherbrain"
PARKING_CONFLICT_ALERT_PERMISSION = "motherbrain.parking_conflicts.view"

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


def motherbrain_alert_context(gateway, can_view_permission=None, limit=20):
    alerts = active_motherbrain_alerts(
        gateway,
        can_view_permission=can_view_permission,
        limit=limit,
    )
    return {
        "alerts": alerts,
        "count": len(alerts),
        "has_alerts": bool(alerts),
        "empty_message": "No active MotherBrain alerts",
    }


def active_motherbrain_alerts(gateway, can_view_permission=None, limit=20):
    if not gateway:
        return []

    query = (
        MotherBrainAlert.query.filter_by(
            gateway_id=gateway.id,
            scope=MOTHERBRAIN_ALERT_SCOPE,
            active=True,
            acknowledged=False,
        )
        .order_by(MotherBrainAlert.created_at.desc(), MotherBrainAlert.id.desc())
        .limit(max(1, int(limit or 20)))
    )
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
