"""Gateway-level NeoRain Ground Time settings."""

from app.extensions import db
from app.models import NeoRainOperationalSetting


DEFAULT_NEORAIN_GROUND_TIME_THRESHOLD_MINUTES = 120


def neorain_ground_time_threshold_minutes(gateway, *, setting=None):
    """Return the effective threshold without creating persistence on reads."""
    if setting is None and gateway is not None:
        setting = NeoRainOperationalSetting.query.filter_by(gateway_id=gateway.id).one_or_none()
    return (
        int(setting.ground_time_threshold_minutes)
        if setting is not None
        else DEFAULT_NEORAIN_GROUND_TIME_THRESHOLD_MINUTES
    )


def set_neorain_ground_time_threshold_minutes(gateway, minutes, *, setting=None):
    """Stage one validated gateway setting; callers own the commit."""
    if gateway is None or gateway.id is None:
        raise ValueError("A gateway is required.")
    value = _positive_whole_minutes(minutes)
    if setting is None:
        setting = NeoRainOperationalSetting.query.filter_by(gateway_id=gateway.id).one_or_none()
    if setting is None:
        setting = NeoRainOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code)
        db.session.add(setting)
    setting.ground_time_threshold_minutes = value
    return setting


def _positive_whole_minutes(value):
    if isinstance(value, bool):
        raise ValueError("Ground Time threshold must be positive whole minutes.")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("Ground Time threshold must be positive whole minutes.") from None
    if str(value).strip() != str(parsed) or parsed <= 0:
        raise ValueError("Ground Time threshold must be positive whole minutes.")
    return parsed
