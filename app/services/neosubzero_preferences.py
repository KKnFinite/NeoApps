"""Persisted per-user NeoSubZero presentation preferences."""

from app.extensions import db
from app.models import NeoSubZeroUserPreference

OPERATIONS_RAMPS = ('All', 'Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Remote', 'Location Review')


def neosubzero_operations_preferences(user, operation):
    """A new canonical operation resets only the effective ramp, without a poll write."""
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user.id).one_or_none()
    return {
        'mode': getattr(preference, 'operations_mode', None) or 'outbound',
        'layout': getattr(preference, 'operations_layout', None) or 'list',
        'ramp': (getattr(preference, 'operations_ramp', None) or 'All')
        if preference and preference.operations_sort_id == getattr(operation, 'id', None) else 'All',
    }


def set_neosubzero_operations_preferences(user, operation, changes):
    from app.models import User
    User.query.filter_by(id=user.id).with_for_update().one()
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user.id).with_for_update().one_or_none()
    if preference is None:
        preference = NeoSubZeroUserPreference(user_id=user.id)
        db.session.add(preference)
    choices = {'mode': ('pretreat', 'outbound'), 'layout': ('list', 'visual'), 'ramp': OPERATIONS_RAMPS}
    for key, value in changes.items():
        if key not in choices or value not in choices[key]:
            raise ValueError('Choose a valid operations mode, layout or ramp.')
        setattr(preference, 'operations_' + key, value)
    if 'ramp' in changes:
        preference.operations_sort_id = getattr(operation, 'id', None)
    db.session.flush()
    return preference


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


def neosubzero_frost_risk_explanations_enabled(user):
    """Read the user's WHY preference without creating state."""
    user_id = getattr(user, "id", None)
    if not user_id:
        return True
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user_id).one_or_none()
    return (
        bool(preference.frost_risk_explanations_enabled)
        if preference is not None
        else True
    )


def set_neosubzero_frost_risk_explanations_enabled(user, enabled):
    """Stage one user's WHY preference without committing."""
    user_id = getattr(user, "id", None)
    if not user_id:
        raise ValueError("Sign in to save NeoSubZero preferences.")
    if not isinstance(enabled, bool):
        raise ValueError("Frost explanation preference must be true or false.")
    preference = NeoSubZeroUserPreference.query.filter_by(user_id=user_id).one_or_none()
    if preference is None:
        preference = NeoSubZeroUserPreference(user_id=user_id)
    preference.frost_risk_explanations_enabled = enabled
    db.session.add(preference)
    db.session.flush()
    return preference
