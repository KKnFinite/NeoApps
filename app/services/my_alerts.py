from dataclasses import dataclass

from sqlalchemy import select, union_all

from app.extensions import db
from app.models import GatewayMembership, PortalAppAccess
from app.services.motherbrain_alerts import active_motherbrain_alerts


PENDING_ACCESS_REQUESTS_PERMISSION = "neoapps.access_requests.view"


@dataclass(frozen=True)
class DynamicAlert:
    severity: str
    title: str
    message: str
    related_url: str
    related_label: str
    created_at: object = None


def my_alert_context(
    *,
    can_view_permission=None,
    gateway=None,
    operation=None,
    include_motherbrain=False,
    limit=20,
    current_user_id=None,
):
    alerts = []
    if include_motherbrain:
        alerts.extend(
            active_motherbrain_alerts(
                gateway,
                can_view_permission=can_view_permission,
                limit=limit,
                operation=operation,
                user_id=current_user_id,
            )
        )

    pending_alert = pending_access_request_alert(can_view_permission)
    if pending_alert:
        alerts.append(pending_alert)

    unread_count = sum(
        1 for alert in alerts if getattr(alert, "is_unread", True)
    )
    return {
        "alerts": alerts,
        "count": len(alerts),
        "active_count": len(alerts),
        "unread_count": unread_count,
        "has_alerts": bool(alerts),
        "empty_message": "No alerts.",
    }


def pending_access_request_alert(can_view_permission=None):
    if not can_view_permission or not can_view_permission(
        PENDING_ACCESS_REQUESTS_PERMISSION
    ):
        return None
    if not has_pending_access_requests():
        return None

    return DynamicAlert(
        severity="info",
        title="Pending Access Requests",
        message="There are pending NeoApps access requests awaiting review.",
        related_url="/portal/manage",
        related_label="REVIEW REQUESTS",
    )


def has_pending_access_requests():
    pending_ids = union_all(
        select(GatewayMembership.id).where(
            GatewayMembership.status == "pending",
            GatewayMembership.is_active.is_(True),
        ),
        select(PortalAppAccess.id).where(
            PortalAppAccess.status == "pending",
            PortalAppAccess.is_active.is_(True),
        ),
    )
    return db.session.execute(pending_ids.limit(1)).scalar() is not None
