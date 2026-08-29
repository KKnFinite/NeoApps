"""Gateway/sort authority for the NeoRain Google migration bundle."""

from flask import current_app, has_app_context

from app.extensions import db
from app.models import MotherBrainGoogleIntegrationSetting
from app.services.access_control import get_current_gateway
from app.services.operation_scope import current_operational_sort_operation


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


class RainIntegrationTransitionError(RuntimeError):
    """Safe failure from an atomic NeoRain authority handoff."""


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


def change_rain_integration_mode(gateway, sort_name, requested_mode):
    """Perform a current-sort authority handoff, then persist its Rain mode."""
    if gateway is None:
        raise RainIntegrationTransitionError("NeoRain gateway is unavailable.")
    normalized_sort = _normalize_sort_name(sort_name)
    target_mode = _validated_mode(requested_mode)
    current_mode = rain_integration_mode(gateway, normalized_sort)
    if current_mode == target_mode:
        return _transition_status(
            gateway,
            normalized_sort,
            handoff_performed=False,
            handoff_direction=None,
        )

    neo_to_neo = (
        current_mode in {NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY}
        and target_mode in {NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY}
    )
    operation = None if neo_to_neo else current_operational_sort_operation(gateway)
    if (
        operation is not None
        and _normalize_sort_name(operation.sort_name) != normalized_sort
    ):
        operation = None

    handoff_direction = None
    try:
        if operation is not None:
            if current_mode == GOOGLE_PRIMARY:
                handoff_direction = "google_to_neo"
            elif target_mode == GOOGLE_PRIMARY:
                handoff_direction = "neo_to_google"
            _apply_current_google_rain_handoff(operation, handoff_direction)

        set_rain_integration_mode(gateway, normalized_sort, target_mode)
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning(
            "NeoRain authority handoff failed safely: gateway=%s sort=%s direction=%s error=%s",
            gateway.code,
            normalized_sort,
            handoff_direction or "none",
            type(error).__name__,
        )
        if isinstance(error, RainIntegrationTransitionError):
            raise
        raise RainIntegrationTransitionError(
            "NeoRain authority could not be changed. The previous mode remains active."
        ) from error

    return _transition_status(
        gateway,
        normalized_sort,
        handoff_performed=operation is not None,
        handoff_direction=handoff_direction,
    )


def rain_google_read_enabled(gateway=None, sort_name=None):
    return rain_integration_mode(gateway, sort_name) == GOOGLE_PRIMARY


def _apply_current_google_rain_handoff(operation, direction):
    from app.services.google_rain_live_milestones import (
        GOOGLE_TO_NEO_AUTHORITY_HANDOFF,
        NEO_TO_GOOGLE_AUTHORITY_HANDOFF,
        apply_google_rain_departure_milestones,
    )
    from app.services.google_rain_sheets import read_google_rain_outbound_milestones

    handoff_mode = {
        "google_to_neo": GOOGLE_TO_NEO_AUTHORITY_HANDOFF,
        "neo_to_google": NEO_TO_GOOGLE_AUTHORITY_HANDOFF,
    }.get(direction)
    if handoff_mode is None:
        raise RainIntegrationTransitionError("NeoRain authority handoff is invalid.")
    rows = read_google_rain_outbound_milestones()
    return apply_google_rain_departure_milestones(
        operation,
        rows=rows,
        authority_handoff=handoff_mode,
    )


def _transition_status(
    gateway,
    sort_name,
    *,
    handoff_performed,
    handoff_direction,
):
    status = rain_integration_status(gateway, sort_name)
    status["handoff_performed"] = bool(handoff_performed)
    status["handoff_direction"] = handoff_direction
    return status


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
