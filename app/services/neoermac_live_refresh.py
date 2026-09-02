"""Shared, gateway-wide live refresh policy for NeoErmac operational screens."""

from app.services.live_screen_refresh import live_screen_refresh_value


NEOERMAC_LEGACY_LIVE_REFRESH_KEY = "neoermac.all"
NEOERMAC_UPCOMING_PULLS_REFRESH_KEY = "neoermac.upcoming_pulls"
NEOERMAC_BUILDING_LINEUP_REFRESH_KEY = "neoermac.building_lineup"
NEOERMAC_VIEW_OUTBOUND_REFRESH_KEY = "neoermac.view_outbound"
NEOERMAC_DOOR_VIEW_REFRESH_KEY = "neoermac.door_view"
NEOERMAC_REFRESH_KEYS = (
    NEOERMAC_UPCOMING_PULLS_REFRESH_KEY,
    NEOERMAC_BUILDING_LINEUP_REFRESH_KEY,
    NEOERMAC_VIEW_OUTBOUND_REFRESH_KEY,
    NEOERMAC_DOOR_VIEW_REFRESH_KEY,
)


def neoermac_live_refresh_status(gateway, screen_key):
    """Expose one registered NeoErmac screen in the live-refresh contract."""
    value = live_screen_refresh_value(gateway, screen_key)
    enabled = value.enabled
    return {
        "auto_refresh_enabled": enabled,
        "reason": "active" if enabled else "disabled",
        "message": "Live updates on" if enabled else "Live updates off",
        "live_status_label": "Live updates on" if enabled else "Live updates off",
        "live_screen_refresh_interval_ms": value.effective_interval_ms,
        "configured_label": value.configured_label,
        "effective_label": value.effective_label,
        "source_label": value.source_label,
    }
