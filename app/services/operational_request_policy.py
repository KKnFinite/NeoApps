"""Classification for operational requests that only reconcile live UI state."""

from __future__ import annotations


LIGHTWEIGHT_LIVE_STATE_ENDPOINTS = frozenset(
    {
        "neoermac.door_view_state",
        "neomotherbrain.parking_plan_live_state_endpoint",
        "neomotherbrain.planning_live_state",
        "neosektor.discharge_state",
    }
)

# NeoSektor count/routing state routes are deliberately absent: those GETs can
# advance the persisted all-up timer or synchronize derived driver-route rows.
# Discharge is the only currently established read-only NeoSektor state route.

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
