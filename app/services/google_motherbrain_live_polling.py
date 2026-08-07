"""Persistent authorization state for future Google MotherBrain live polling."""

from flask import has_app_context

from app.extensions import db
from app.models import MotherBrainGoogleIntegrationSetting
from app.services.access_control import get_current_gateway


DEFAULT_GOOGLE_MOTHERBRAIN_SORT = "night"


def google_motherbrain_live_polling_enabled(gateway=None, sort_name=None):
    """Return the database-backed live-poll authorization state."""
    setting = _existing_setting(gateway, sort_name)
    return bool(setting and setting.live_polling_enabled)


def google_motherbrain_live_polling_status(gateway=None, sort_name=None):
    gateway = _resolve_gateway(gateway)
    normalized_sort = _normalize_sort_name(sort_name)
    setting = _existing_setting(gateway, normalized_sort)
    return {
        "enabled": bool(setting and setting.live_polling_enabled),
        "gateway_code": gateway.code if gateway else None,
        "sort_name": normalized_sort,
        "persisted": setting is not None,
    }


def set_google_motherbrain_live_polling_enabled(gateway, sort_name, enabled):
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
        )
        db.session.add(setting)

    setting.gateway_code = gateway.code
    setting.live_polling_enabled = bool(enabled)
    db.session.flush()
    return setting


def ensure_google_motherbrain_live_polling_setting(
    gateway,
    sort_name=DEFAULT_GOOGLE_MOTHERBRAIN_SORT,
):
    normalized_sort = _normalize_sort_name(sort_name)
    setting = MotherBrainGoogleIntegrationSetting.query.filter_by(
        gateway_id=gateway.id,
        sort_name=normalized_sort,
    ).first()
    if setting:
        setting.gateway_code = gateway.code
        if setting.live_polling_enabled is None:
            setting.live_polling_enabled = False
        db.session.flush()
        return setting

    setting = MotherBrainGoogleIntegrationSetting(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        sort_name=normalized_sort,
        live_polling_enabled=False,
    )
    db.session.add(setting)
    db.session.flush()
    return setting


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
    return str(sort_name or DEFAULT_GOOGLE_MOTHERBRAIN_SORT).strip().lower()
