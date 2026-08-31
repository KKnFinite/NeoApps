"""Persisted per-user NeoSubZero presentation preferences."""

from app.extensions import db
from app.models import NeoSubZeroUserPreference


def neosubzero_weather_animations_enabled(user):
    """Read the user's weather-motion preference without creating state."""
    user_id = getattr(user, "id", None)
    if not user_id:
        return True
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user_id).one_or_none()
    return (
        bool(preference.weather_animations_enabled)
        if preference is not None
        else True
    )


def set_neosubzero_weather_animations_enabled(user, enabled):
    """Stage one user's weather-motion preference without committing."""
    user_id = getattr(user, "id", None)
    if not user_id:
        raise ValueError("Sign in to save NeoSubZero preferences.")
    if not isinstance(enabled, bool):
        raise ValueError("Weather animation preference must be true or false.")
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user_id).one_or_none()
    if preference is None:
        preference = NeoSubZeroUserPreference(user_id=user_id)
    preference.weather_animations_enabled = enabled
    db.session.add(preference)
    db.session.flush()
    return preference
