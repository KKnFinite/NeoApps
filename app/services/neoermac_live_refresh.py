"""Shared, gateway-wide live refresh policy for NeoErmac operational screens."""

from app.services.live_screen_refresh import live_screen_refresh_value


NEOERMAC_LIVE_REFRESH_KEY = "neoermac.all"


def neoermac_live_refresh_status(gateway):
    """Expose the one NeoErmac setting in the existing live-refresh contract."""
    value = live_screen_refresh_value(gateway, NEOERMAC_LIVE_REFRESH_KEY)
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
