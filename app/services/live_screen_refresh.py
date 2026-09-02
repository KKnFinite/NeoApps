from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.models import Gateway, LiveScreenRefreshSetting
from app.services.live_screen_registry import live_screen_definition


LIVE_SCREEN_REFRESH_ALLOWED_SECONDS = (0, 5, 10, 15, 30, 60)
LIVE_SCREEN_REFRESH_MINIMUM_INTERVAL_MS = 5_000


@dataclass(frozen=True)
class LiveScreenRefreshValue:
    screen_key: str
    override_seconds: int | None
    effective_interval_ms: int
    source: str

    @property
    def enabled(self):
        return self.effective_interval_ms > 0

    @property
    def configured_label(self):
        if self.override_seconds is None:
            return "Render default"
        return _interval_label(self.override_seconds * 1000)

    @property
    def effective_label(self):
        return _interval_label(self.effective_interval_ms)

    @property
    def source_label(self):
        if self.source == "override":
            return "Override"
        if self.source == "legacy_neoermac_override":
            return "Legacy NeoErmac override"
        if self.source == "unregistered":
            return "Not registered"
        return "Render default"


@dataclass(frozen=True)
class LiveScreenRefreshMutationResult:
    changed: bool
    setting: LiveScreenRefreshSetting | None


def live_screen_refresh_value(gateway, screen_key, *, fallback_ms=None):
    values = live_screen_refresh_values(
        gateway,
        (screen_key,),
        fallback_ms=fallback_ms,
    )
    return values[screen_key]


def live_screen_refresh_status(gateway, screen_key, base_status=None):
    """Combine registry-backed timing with an operational-window status."""
    value = live_screen_refresh_value(gateway, screen_key)
    status = dict(base_status or {})
    window_enabled = status.get("auto_refresh_enabled", True) is not False
    status.update(
        {
            "auto_refresh_enabled": bool(window_enabled and value.enabled),
            "live_screen_refresh_interval_ms": value.effective_interval_ms,
            "configured_label": value.configured_label,
            "effective_label": value.effective_label,
            "source_label": value.source_label,
        }
    )
    if not value.enabled:
        status.update(
            {
                "reason": "disabled",
                "message": "Live updates off",
                "live_status_label": "Live updates off",
            }
        )
    return status


def live_screen_refresh_values(gateway, screen_keys, *, fallback_ms=None):
    normalized_keys = tuple(dict.fromkeys(_normalize_screen_key(key) for key in screen_keys))
    if not normalized_keys:
        return {}

    registered_keys = tuple(key for key in normalized_keys if live_screen_definition(key))
    query_keys = tuple(dict.fromkeys((*registered_keys, "neoermac.all")))
    overrides = {
        row.screen_key: row.interval_seconds
        for row in LiveScreenRefreshSetting.query.filter(
            LiveScreenRefreshSetting.gateway_id == gateway.id,
            LiveScreenRefreshSetting.screen_key.in_(query_keys),
        ).all()
    }
    effective_fallback_ms = _effective_fallback_ms(fallback_ms)
    values = {}
    for screen_key in normalized_keys:
        if not live_screen_definition(screen_key):
            values[screen_key] = LiveScreenRefreshValue(
                screen_key, None, 0, "unregistered"
            )
            continue
        override = overrides.get(screen_key)
        source = "override"
        if override is None and screen_key.startswith("neoermac."):
            override = overrides.get("neoermac.all")
            source = "legacy_neoermac_override"
        values[screen_key] = _refresh_value(
            screen_key,
            override,
            effective_fallback_ms,
            override_source=source,
        )
    return values


def save_live_screen_refresh_override(
    gateway,
    screen_key,
    raw_value,
    *,
    allowed_screen_keys=None,
):
    normalized_key = _normalize_screen_key(screen_key)
    if live_screen_definition(normalized_key) is None:
        raise ValueError("Select a registered live screen.")
    if allowed_screen_keys is not None:
        normalized_allowed = {
            _normalize_screen_key(key) for key in allowed_screen_keys
        }
        if normalized_key not in normalized_allowed:
            raise ValueError("Select a supported live screen.")

    locked_gateway = (
        Gateway.query.filter_by(id=gateway.id).with_for_update().first()
    )
    if locked_gateway is None:
        raise ValueError("Select an existing gateway.")

    setting = (
        LiveScreenRefreshSetting.query.filter_by(
            gateway_id=locked_gateway.id,
            screen_key=normalized_key,
        )
        .with_for_update()
        .first()
    )
    normalized_value = str(raw_value or "").strip().lower()
    if normalized_value in {"", "default", "reset"}:
        if setting is None:
            return LiveScreenRefreshMutationResult(False, None)
        db.session.delete(setting)
        return LiveScreenRefreshMutationResult(True, None)

    interval_seconds = _parse_override_seconds(normalized_value)
    if setting is not None and setting.interval_seconds == interval_seconds:
        return LiveScreenRefreshMutationResult(False, setting)
    if setting is None:
        setting = LiveScreenRefreshSetting(
            gateway_id=locked_gateway.id,
            screen_key=normalized_key,
            interval_seconds=interval_seconds,
        )
        db.session.add(setting)
    else:
        setting.interval_seconds = interval_seconds
    return LiveScreenRefreshMutationResult(True, setting)


def _refresh_value(screen_key, override_seconds, fallback_ms, *, override_source="override"):
    if override_seconds is None:
        return LiveScreenRefreshValue(
            screen_key=screen_key,
            override_seconds=None,
            effective_interval_ms=fallback_ms,
            source="render_default",
        )
    return LiveScreenRefreshValue(
        screen_key=screen_key,
        override_seconds=int(override_seconds),
        effective_interval_ms=int(override_seconds) * 1000,
        source=override_source,
    )


def _effective_fallback_ms(value):
    if value is None:
        value = current_app.config.get(
            "LIVE_SCREEN_REFRESH_INTERVAL_MS",
            LIVE_SCREEN_REFRESH_MINIMUM_INTERVAL_MS,
        )
    try:
        interval_ms = int(value)
    except (TypeError, ValueError):
        interval_ms = LIVE_SCREEN_REFRESH_MINIMUM_INTERVAL_MS
    if interval_ms <= 0:
        return 0
    return max(LIVE_SCREEN_REFRESH_MINIMUM_INTERVAL_MS, interval_ms)


def _parse_override_seconds(value):
    if value == "off":
        return 0
    try:
        interval_seconds = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Select OFF, 5, 10, 15, 30, or 60 seconds.") from error
    if interval_seconds not in LIVE_SCREEN_REFRESH_ALLOWED_SECONDS:
        raise ValueError("Select OFF, 5, 10, 15, 30, or 60 seconds.")
    return interval_seconds


def _normalize_screen_key(value):
    normalized = str(value or "").strip().lower()
    if not normalized or len(normalized) > 120:
        raise ValueError("Select a valid live screen.")
    return normalized


def _interval_label(interval_ms):
    if interval_ms <= 0:
        return "OFF"
    seconds = interval_ms / 1000
    rendered = str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"
    return f"{rendered} seconds"
