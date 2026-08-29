"""Gateway/sort authority for the NeoRain Google migration bundle."""

from flask import has_app_context

from app.extensions import db
from app.models import MotherBrainGoogleIntegrationSetting
from app.services.access_control import get_current_gateway


GOOGLE_PRIMARY = "google_primary"
NEO_PRIMARY_GOOGLE_MIRROR = "neo_primary_google_mirror"
NEO_ONLY = "neo_only"
DEFAULT_RAIN_INTEGRATION_MODE = GOOGLE_PRIMARY
RAIN_INTEGRATION_MODES = (
    GOOGLE_PRIMARY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    NEO_ONLY,
)
RAIN_INTEGRATION_MODE_LABELS = {
    GOOGLE_PRIMARY: "GOOGLE PRIMARY",
    NEO_PRIMARY_GOOGLE_MIRROR: "NEO PRIMARY + GOOGLE MIRROR",
    NEO_ONLY: "NEO ONLY",
}
DEFAULT_RAIN_SORT = "night"


def rain_integration_mode(gateway=None, sort_name=None, *, setting=None):
    """Read the configured mode without creating or updating persistence."""
    if setting is None:
        setting = _existing_setting(gateway, sort_name)
    return _normalized_mode(getattr(setting, "rain_integration_mode", None))


def rain_integration_status(gateway=None, sort_name=None):
    gateway = _resolve_gateway(gateway)
    normalized_sort = _normalize_sort_name(sort_name)
    setting = _existing_setting(gateway, normalized_sort)
    mode = _normalized_mode(getattr(setting, "rain_integration_mode", None))
    return {
        "mode": mode,
        "mode_label": RAIN_INTEGRATION_MODE_LABELS[mode],
        "modes": tuple(
            {"value": value, "label": RAIN_INTEGRATION_MODE_LABELS[value]}
            for value in RAIN_INTEGRATION_MODES
        ),
        "gateway_code": gateway.code if gateway else None,
        "sort_name": normalized_sort,
        "persisted": setting is not None,
    }


def ensure_rain_integration_setting(gateway, sort_name=DEFAULT_RAIN_SORT):
    """Explicitly ensure the shared gateway/sort setting row exists."""
    normalized_sort = _normalize_sort_name(sort_name)
    setting = MotherBrainGoogleIntegrationSetting.query.filter_by(
        gateway_id=gateway.id,
        sort_name=normalized_sort,
    ).first()
    if not setting:
        setting = MotherBrainGoogleIntegrationSetting(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            sort_name=normalized_sort,
            live_polling_enabled=False,
            rain_integration_mode=DEFAULT_RAIN_INTEGRATION_MODE,
        )
        db.session.add(setting)
    else:
        setting.gateway_code = gateway.code
        setting.rain_integration_mode = _normalized_mode(
            setting.rain_integration_mode
        )
    db.session.flush()
    return setting


def set_rain_integration_mode(gateway, sort_name, mode):
    """Persist one validated Rain authority change without committing it."""
    normalized_mode = _validated_mode(mode)
    setting = ensure_rain_integration_setting(gateway, sort_name)
    setting.rain_integration_mode = normalized_mode
    db.session.flush()
    return setting


def rain_google_read_enabled(gateway=None, sort_name=None):
    return rain_integration_mode(gateway, sort_name) == GOOGLE_PRIMARY


def _existing_setting(gateway=None, sort_name=None):
    if not has_app_context():
        return None
    gateway = _resolve_gateway(gateway)
    if not gateway:
        return None
    return MotherBrainGoogleIntegrationSetting.query.filter_by(
        gateway_id=gateway.id,
        sort_name=_normalize_sort_name(sort_name),
    ).first()


def _resolve_gateway(gateway=None):
    if gateway is not None:
        return gateway
    try:
        return get_current_gateway()
    except Exception:
        return None


def _normalize_sort_name(sort_name):
    return str(sort_name or DEFAULT_RAIN_SORT).strip().lower()


def _normalized_mode(mode):
    normalized = str(mode or "").strip().lower()
    return (
        normalized
        if normalized in RAIN_INTEGRATION_MODES
        else DEFAULT_RAIN_INTEGRATION_MODE
    )


def _validated_mode(mode):
    normalized = str(mode or "").strip().lower()
    if normalized not in RAIN_INTEGRATION_MODES:
        raise ValueError("Choose a valid NeoRain integration mode.")
    return normalized
