"""Classification for operational requests that only reconcile live UI state."""

from __future__ import annotations


LIGHTWEIGHT_LIVE_STATE_ENDPOINTS = frozenset(
    {
        "neoermac.door_view_state",
        "neoermac.upcoming_pulls_state",
        "neomotherbrain.parking_plan_live_state_endpoint",
        "neomotherbrain.planning_live_state",
        "neosektor.ballmat_state",
        "neosektor.discharge_state",
        "neosektor.driver_routing_state",
        "neosektor.live_counts_state",
        "neosektor.tunnel_conductor_state",
    }
)

# NeoSektor state routes use virtual defaults and pure calculations. Required
# row initialization remains on normal page/setup and write paths.

# View Outbound serves both its normal page and revision-based live polling from
# one endpoint. Only a request carrying a client revision is a reconciliation
# request; an ordinary page load must keep the full lifecycle path.
REVISION_LIVE_STATE_ENDPOINTS = frozenset(
    {
        "neoermac.view_outbound",
    }
)


def is_lightweight_live_state_request(endpoint, method, args=None):
    """Return whether a matched request may skip global lifecycle maintenance."""
    if str(method or "").upper() not in {"GET", "HEAD"}:
        return False
    if endpoint in LIGHTWEIGHT_LIVE_STATE_ENDPOINTS:
        return True
    if endpoint not in REVISION_LIVE_STATE_ENDPOINTS:
        return False
    return bool(str((args or {}).get("revision") or "").strip())
