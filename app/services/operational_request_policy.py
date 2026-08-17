"""Classification for operational requests that only reconcile live UI state."""

from __future__ import annotations

from flask import g, has_request_context


LIGHTWEIGHT_LIVE_STATE_ENDPOINTS = frozenset(
    {
        "neoermac.door_view_state",
        "neoermac.upcoming_pulls_state",
        "neoermac.view_outbound_state",
        "neomotherbrain.parking_plan_live_state_endpoint",
        "neomotherbrain.planning_live_state",
        "neoscorpion.fuel_assignments_revision",
        "neosektor.ballmat_state",
        "neosektor.discharge_state",
        "neosektor.driver_routing_state",
        "neosektor.live_counts_state",
        "neosektor.tunnel_conductor_state",
    }
)

LIGHTWEIGHT_LIVE_STATE_NODE_CODES = {
    "neoermac.door_view_state": "ermac",
    "neoermac.upcoming_pulls_state": "ermac",
    "neoermac.view_outbound_state": "ermac",
    "neomotherbrain.parking_plan_live_state_endpoint": "motherbrain",
    "neomotherbrain.planning_live_state": "motherbrain",
    "neoscorpion.fuel_assignments_revision": "scorpion",
    "neosektor.ballmat_state": "sektor",
    "neosektor.discharge_state": "sektor",
    "neosektor.driver_routing_state": "sektor",
    "neosektor.live_counts_state": "sektor",
    "neosektor.tunnel_conductor_state": "sektor",
}

OPERATION_ID_LIVE_STATE_ENDPOINTS = frozenset(
    {
        "neomotherbrain.parking_plan_live_state_endpoint",
        "neomotherbrain.planning_live_state",
    }
)

CURRENT_ERMAC_OPERATION_ENDPOINTS = frozenset(
    {
        "neoermac.door_view_state",
        "neoermac.upcoming_pulls_state",
        "neoermac.view_outbound_state",
    }
)

# NeoSektor state routes use virtual defaults and pure calculations. Required
# row initialization remains on normal page/setup and write paths.

def is_lightweight_live_state_request(endpoint, method, args=None):
    """Return whether a matched request may skip global lifecycle maintenance."""
    if str(method or "").upper() not in {"GET", "HEAD"}:
        return False
    if endpoint in LIGHTWEIGHT_LIVE_STATE_ENDPOINTS:
        return True
    return False


def lightweight_live_state_scope_spec(endpoint, view_args=None):
    """Describe the shared read-only scope an approved live route needs."""
    node_code = LIGHTWEIGHT_LIVE_STATE_NODE_CODES.get(endpoint)
    if not node_code:
        return None

    view_args = view_args or {}
    operation_id = (
        view_args.get("operation_id")
        if endpoint in OPERATION_ID_LIVE_STATE_ENDPOINTS
        else None
    )
    return {
        "node_code": node_code,
        "operation_id": operation_id,
        "include_current_ermac_operation": endpoint
        in CURRENT_ERMAC_OPERATION_ENDPOINTS,
    }


def current_request_is_lightweight_live_state():
    return bool(
        has_request_context()
        and getattr(g, "is_lightweight_live_state_request", False)
    )
