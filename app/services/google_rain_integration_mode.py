"""Gateway/sort authority for the NeoRain Google migration bundle."""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app, has_app_context

from app.extensions import db
from app.models import Gateway, GatewaySortMatrix, MotherBrainGoogleIntegrationSetting, SortDateOperation, SortTimelineSortSetting
from app.services.access_control import get_current_gateway
from app.services.gateway_matrix import (
    SORT_VALUES,
    configured_sort_window_utc,
    current_gateway_local_datetime,
    gateway_timezone,
)


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
    """Safe failure from an atomic NeoRain authority change."""


class RainIntegrationWindowError(RainIntegrationTransitionError):
    """The schedule cannot establish an idle window for the requested change."""


def rain_integration_mode(gateway=None, sort_name=None, *, setting=None):
    """Read the configured mode without creating or updating persistence."""
    if setting is None:
        setting = _existing_setting(gateway, sort_name)
    return _normalized_mode(getattr(setting, "rain_integration_mode", None))


def rain_operation_is_native(operation):
    from app.models import Gateway
    gateway = operation.gateway or Gateway.query.filter_by(code=operation.gateway_code).one_or_none()
    return gateway is not None and rain_integration_mode(gateway, operation.sort_name) == NEO_ONLY


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
            rain_fuel_data_source="google",
        )
        db.session.add(setting)
    else:
        setting.gateway_code = gateway.code
        setting.rain_integration_mode = _normalized_mode(
            setting.rain_integration_mode
        )
        if str(getattr(setting, "rain_fuel_data_source", "") or "").strip().lower() not in {"google", "neo"}:
            setting.rain_fuel_data_source = "google"
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
    """Change future authority in a verified Sort gap without importing any data."""
    if gateway is None:
        raise RainIntegrationTransitionError("NeoRain gateway is unavailable.")
    sort_name = _normalize_sort_name(sort_name)
    target_mode = _validated_mode(requested_mode)
    try:
        with db.session.no_autoflush:
            # Serialize even the first settings write, when there's no mode row to lock.
            db.session.query(Gateway.id).filter_by(id=gateway.id).with_for_update().one()
            setting = MotherBrainGoogleIntegrationSetting.query.filter_by(
                gateway_id=gateway.id, sort_name=sort_name,
            ).populate_existing().with_for_update().one_or_none()
            current_mode = _normalized_mode(getattr(setting, "rain_integration_mode", None))
            if current_mode != target_mode:
                _require_sort_gap(gateway, sort_name)
                setting = setting or ensure_rain_integration_setting(gateway, sort_name)
                setting.rain_integration_mode = target_mode
        if current_mode != target_mode:
            db.session.commit()
    except RainIntegrationTransitionError:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning(
            "NeoRain authority change failed: gateway=%s sort=%s error=%s",
            gateway.code, sort_name, type(error).__name__,
        )
        raise RainIntegrationTransitionError(
            "NeoRain authority could not be changed. The previous mode remains active."
        ) from error

    return {
        **rain_integration_status(gateway, sort_name),
        "handoff_performed": False,
        "handoff_direction": None,
    }


def _require_sort_gap(gateway, sort_name):
    invalid = "NeoRain authority cannot change until the MotherBrain Sort Timeline has valid operating hours."
    settings = SortTimelineSortSetting.query.filter_by(gateway_id=gateway.id).order_by(
        SortTimelineSortSetting.id
    ).populate_existing().with_for_update().all()
    by_name = {row.sort_name: row for row in settings}
    if len(by_name) != len(settings) or any(row.gateway_code != gateway.code for row in settings):
        raise RainIntegrationWindowError(invalid)
    try:
        zone = ZoneInfo(gateway_timezone(gateway))
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise RainIntegrationWindowError(invalid) from error
    local_now = current_gateway_local_datetime(gateway)
    recent_dates = (local_now.date() - timedelta(days=1), local_now.date())
    dates_by_weekday = {day.strftime("%A").lower(): day for day in recent_dates}
    matrix = GatewaySortMatrix.query.filter(
        GatewaySortMatrix.gateway_id == gateway.id,
        GatewaySortMatrix.day_of_week.in_(dates_by_weekday),
    ).order_by(GatewaySortMatrix.id).populate_existing().with_for_update().all()
    expected_cells = {(weekday, name) for weekday in dates_by_weekday for name in SORT_VALUES}
    if (
        len(matrix) != len(expected_cells)
        or {(row.day_of_week, row.sort_name) for row in matrix} != expected_cells
        or any(row.gateway_code != gateway.code for row in matrix)
    ):
        raise RainIntegrationWindowError(
            "NeoRain authority cannot change until the MotherBrain Gateway Sort Matrix is complete and consistent."
        )

    candidates = {(dates_by_weekday[row.day_of_week], row.sort_name) for row in matrix if row.is_active}
    existing = db.session.query(
        SortDateOperation.sort_date, SortDateOperation.sort_name,
        SortDateOperation.gateway_id, SortDateOperation.gateway_code,
    ).filter(
        db.or_(SortDateOperation.gateway_id == gateway.id, SortDateOperation.gateway_code == gateway.code),
        SortDateOperation.sort_date.in_(recent_dates),
        SortDateOperation.archived_at_utc.is_(None),
    ).all()
    if any(code != gateway.code or id_ not in (None, gateway.id) for _, _, id_, code in existing):
        raise RainIntegrationWindowError("NeoRain authority cannot change while an operation has inconsistent gateway scope.")
    candidates.update((day, name) for day, name, _, _ in existing)
    windows = []
    # Validate the target timeline on off-days without treating those days as scheduled.
    for day, name in sorted(candidates | {(day, sort_name) for day in recent_dates}):
        setting = by_name.get(name)
        if setting is None:
            raise RainIntegrationWindowError(invalid)
        start, end = configured_sort_window_utc(gateway, day, name, zone, sort_setting=setting)
        if start is None:
            raise RainIntegrationWindowError(invalid)
        if (day, name) in candidates:
            windows.append((start, end))
    now = current_gateway_local_datetime(gateway).replace(tzinfo=zone).astimezone(timezone.utc)
    if now.astimezone(zone).date() != local_now.date() or any(start <= now < end for start, end in windows):
        raise RainIntegrationWindowError(
            "NeoRain authority cannot change during configured Sort operating hours. Wait until the Sort closes."
        )


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
