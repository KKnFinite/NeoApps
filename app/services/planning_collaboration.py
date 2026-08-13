import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, literal, select, union_all

from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    GatewayMembership,
    GatewayNodeRole,
    MotherBrainAlert,
    MotherBrainAlertUserState,
    PermissionRule,
    PortalAppAccess,
    SortDateAlpPreview,
    SortDateMission,
    SortDateParkingAssignment,
    SortDateTailState,
    SortTimelineSettings,
)
from app.services.live_collaboration import entity_version


def planning_state_revision(operation, mission_type, user, *, now_utc=None):
    """Return a compact fingerprint for visible Planning collaboration state."""
    mission_type = _mission_type(mission_type)
    user_id = int(getattr(user, "id", 0) or 0)
    now_utc = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(timezone.utc).replace(tzinfo=None)

    mission_criteria = [
        SortDateMission.sort_date_operation_id == operation.id,
    ]
    if mission_type == "arrival":
        mission_criteria.append(SortDateMission.mission_type == mission_type)

    aggregate_queries = [
        _aggregate_query("missions", SortDateMission, *mission_criteria),
        _aggregate_query(
            "pending_review",
            FlightApiReviewItem,
            FlightApiReviewItem.sort_date_operation_id == operation.id,
            FlightApiReviewItem.mission_type == mission_type,
            FlightApiReviewItem.review_status == "pending",
        ),
        _aggregate_query(
            "preview",
            SortDateAlpPreview,
            SortDateAlpPreview.sort_date_operation_id == operation.id,
            SortDateAlpPreview.mission_type == mission_type,
        ),
        _aggregate_query(
            "parking",
            SortDateParkingAssignment,
            SortDateParkingAssignment.sort_date_operation_id == operation.id,
        ),
        _aggregate_query(
            "tail_states",
            SortDateTailState,
            SortDateTailState.sort_date == operation.sort_date,
            SortDateTailState.gateway_code == operation.gateway_code,
            SortDateTailState.sort_name == operation.sort_name,
        ),
        _timeline_settings_query(operation.gateway_id),
        _aggregate_query(
            "alerts",
            MotherBrainAlert,
            MotherBrainAlert.gateway_id == operation.gateway_id,
        ),
        _aggregate_query(
            "alert_read_state",
            MotherBrainAlertUserState,
            MotherBrainAlertUserState.user_id == user_id,
        ),
        _aggregate_query(
            "permission_rules",
            PermissionRule,
        ),
        _aggregate_query(
            "user_gateway_access",
            GatewayMembership,
            GatewayMembership.user_id == user_id,
            GatewayMembership.gateway_id == operation.gateway_id,
        ),
        _aggregate_query(
            "user_node_roles",
            GatewayNodeRole,
            GatewayNodeRole.gateway_membership_id.in_(
                select(GatewayMembership.id).where(
                    GatewayMembership.user_id == user_id,
                    GatewayMembership.gateway_id == operation.gateway_id,
                )
            ),
        ),
        _aggregate_query(
            "user_app_access",
            PortalAppAccess,
            PortalAppAccess.user_id == user_id,
        ),
        _aggregate_query(
            "pending_gateway_access",
            GatewayMembership,
            GatewayMembership.status == "pending",
            GatewayMembership.is_active.is_(True),
        ),
        _aggregate_query(
            "pending_app_access",
            PortalAppAccess,
            PortalAppAccess.status == "pending",
            PortalAppAccess.is_active.is_(True),
        ),
    ]
    if mission_type == "arrival":
        aggregate_queries.extend(
            [
                _aggregate_query(
                    "assumed_arrived_phase",
                    SortDateMission,
                    SortDateMission.sort_date_operation_id == operation.id,
                    SortDateMission.mission_type == "arrival",
                    SortDateMission.api_assumed_arrived_time_utc.is_not(None),
                    SortDateMission.api_assumed_arrived_time_utc <= now_utc,
                ),
                _aggregate_query(
                    "legacy_runway_phase",
                    SortDateMission,
                    SortDateMission.sort_date_operation_id == operation.id,
                    SortDateMission.mission_type == "arrival",
                    SortDateMission.api_assumed_arrived_time_utc.is_(None),
                    SortDateMission.api_runway_time_utc.is_not(None),
                    timestamp_column=SortDateMission.api_runway_time_utc,
                ),
            ]
        )

    aggregate_rows = sorted(
        db.session.execute(union_all(*aggregate_queries)).all(),
        key=lambda row: row.source,
    )
    inputs = [
        {
            "source": row.source,
            "row_count": int(row.row_count or 0),
            "max_id": int(row.max_id or 0),
            "id_sum": int(row.id_sum or 0),
            "state_value": int(row.state_value or 0),
            "latest_updated_at": _value_token(row.latest_updated_at),
        }
        for row in aggregate_rows
    ]

    # Old rows without the stored assumed-arrival timestamp still have a
    # time-driven status transition. Invalidate by minute only while their
    # latest runway time could still be crossing the normal taxi threshold.
    legacy_runway = next(
        (row for row in aggregate_rows if row.source == "legacy_runway_phase"),
        None,
    )
    legacy_phase_token = None
    if legacy_runway and legacy_runway.row_count and legacy_runway.latest_updated_at:
        latest_runway = legacy_runway.latest_updated_at
        timeline_settings = next(
            (row for row in aggregate_rows if row.source == "timeline_settings"),
            None,
        )
        taxi_minutes = int(getattr(timeline_settings, "state_value", 10) or 0)
        if now_utc <= latest_runway + timedelta(minutes=taxi_minutes):
            legacy_phase_token = now_utc.replace(second=0, microsecond=0).isoformat()

    return _digest(
        {
            "operation_id": operation.id,
            "operation_version": entity_version(operation),
            "mission_type": mission_type,
            "user_id": user_id,
            "user_role": str(getattr(user, "role", "") or ""),
            "legacy_arrival_phase": legacy_phase_token,
            "inputs": inputs,
        }
    )


def _aggregate_query(source, model, *criteria, timestamp_column=None):
    if timestamp_column is None:
        timestamp_column = model.updated_at
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        literal(0).label("state_value"),
        func.max(timestamp_column).label("latest_updated_at"),
    ).where(*criteria)


def _timeline_settings_query(gateway_id):
    return select(
        literal("timeline_settings").label("source"),
        func.count(SortTimelineSettings.id).label("row_count"),
        func.max(SortTimelineSettings.id).label("max_id"),
        func.coalesce(func.sum(SortTimelineSettings.id), 0).label("id_sum"),
        func.coalesce(func.max(SortTimelineSettings.taxi_to_ramp_minutes), 10).label(
            "state_value"
        ),
        func.max(SortTimelineSettings.updated_at).label("latest_updated_at"),
    ).where(SortTimelineSettings.gateway_id == gateway_id)


def _mission_type(value):
    normalized = str(value or "").strip().lower()
    if normalized not in {"arrival", "departure"}:
        raise ValueError("Planning mission type must be arrival or departure.")
    return normalized


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _value_token(value):
    return value.isoformat() if hasattr(value, "isoformat") else value
