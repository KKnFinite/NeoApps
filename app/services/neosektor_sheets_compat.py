"""Gateway-scoped NeoSektor Google/Neo transition authority.

Only the established standalone operational cells cross this boundary. Neo-owned
modifiers and application settings remain in Neon in every integration mode.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import has_app_context

try:
    import gspread
except ImportError:  # Optional locally; production installs the dependency.
    gspread = None

from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services.access_control import get_current_gateway


logger = logging.getLogger(__name__)

GOOGLE_PRIMARY = "google_primary"
NEO_PRIMARY_GOOGLE_MIRROR = "neo_primary_google_mirror"
NEO_ONLY = "neo_only"
DEFAULT_NEOSEKTOR_INTEGRATION_MODE = GOOGLE_PRIMARY
NEOSEKTOR_INTEGRATION_MODES = (
    GOOGLE_PRIMARY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    NEO_ONLY,
)
NEOSEKTOR_INTEGRATION_MODE_LABELS = {
    GOOGLE_PRIMARY: "GOOGLE PRIMARY",
    NEO_PRIMARY_GOOGLE_MIRROR: "NEO PRIMARY + GOOGLE MIRROR",
    NEO_ONLY: "NEO ONLY",
}

# B13/B14/B15 are deliberately absent. Modifiers and driver offset are Neo-owned.
SHEET_CELL_ORDER = (
    "B2",
    "C2",
    "D2",
    "B3",
    "C3",
    "D3",
    "B4",
    "C4",
    "B6",
    "B8",
    "B10",
    "C6",
    "C8",
)
COUNT_CELL_MAXIMUMS = {
    "B2": 99,
    "C2": 99,
    "D2": 999,
    "B3": 99,
    "C3": 99,
    "D3": 999,
    "B4": 99,
    "C4": 99,
}
STATUS_CELLS = {"B6", "B8", "B10", "C6", "C8"}
STATUS_LABELS = ("Empty", "Light", "Moderate", "Full", "Overflowing")

# This cache only coalesces nearly simultaneous requests within one worker. It
# is intentionally unrelated to the configurable browser refresh interval.
GOOGLE_TRANSIENT_CACHE_SECONDS = 2.0
_google_state_cache = {}
_google_state_cache_lock = threading.Lock()


class NeoSektorGoogleError(ValueError):
    """Safe, user-displayable NeoSektor Google transition failure."""


def neosektor_integration_mode(gateway=None):
    settings = _existing_operational_settings(gateway)
    return _normalized_mode(getattr(settings, "integration_mode", None))


def neosektor_integration_status(gateway=None):
    gateway = _resolve_gateway(gateway)
    settings = _existing_operational_settings(gateway)
    mode = _normalized_mode(getattr(settings, "integration_mode", None))
    return {
        "mode": mode,
        "mode_label": NEOSEKTOR_INTEGRATION_MODE_LABELS[mode],
        "modes": tuple(
            {"value": value, "label": NEOSEKTOR_INTEGRATION_MODE_LABELS[value]}
            for value in NEOSEKTOR_INTEGRATION_MODES
        ),
        "google_mirror_sync_needed": bool(
            settings and settings.google_mirror_sync_needed
        ),
        "google_mirror_last_error": (
            settings.google_mirror_last_error if settings else None
        ),
        "google_mirror_failed_at_utc": (
            settings.google_mirror_failed_at_utc if settings else None
        ),
        "credentials_configured": sheets_credentials_configured(),
    }


def ensure_neosektor_integration_setting(gateway):
    settings = NeoSektorOperationalSetting.query.filter_by(
        gateway_id=gateway.id
    ).first()
    if not settings:
        settings = NeoSektorOperationalSetting(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            integration_mode=DEFAULT_NEOSEKTOR_INTEGRATION_MODE,
        )
        db.session.add(settings)

    settings.gateway_code = gateway.code
    settings.integration_mode = _normalized_mode(settings.integration_mode)
    if settings.google_mirror_sync_needed is None:
        settings.google_mirror_sync_needed = False
    db.session.flush()
    return settings


def change_neosektor_integration_mode(gateway, requested_mode):
    """Perform any required authority handoff, then persist the target mode."""
    target_mode = _normalized_mode(requested_mode, strict=True)
    settings = ensure_neosektor_integration_setting(gateway)
    current_mode = _normalized_mode(settings.integration_mode)
    if current_mode == target_mode:
        return neosektor_integration_status(gateway)

    if current_mode == GOOGLE_PRIMARY and target_mode in {
        NEO_PRIMARY_GOOGLE_MIRROR,
        NEO_ONLY,
    }:
        try:
            values = google_primary_operational_values(gateway, force=True)
            from app.services.neosektor_live_counts import (
                apply_standalone_compat_values,
            )

            apply_standalone_compat_values(gateway, values)
            settings.integration_mode = target_mode
            _clear_mirror_warning(settings)
            db.session.commit()
        except Exception as error:
            db.session.rollback()
            _log_safe_warning("Google to Neo handoff", error)
            if isinstance(error, NeoSektorGoogleError):
                raise
            raise NeoSektorGoogleError(
                "Google values could not be imported. NeoSektor remains GOOGLE PRIMARY."
            ) from error
        return neosektor_integration_status(gateway)

    settings.integration_mode = target_mode
    if target_mode != NEO_PRIMARY_GOOGLE_MIRROR:
        _clear_mirror_warning(settings)
    db.session.commit()

    if current_mode == NEO_ONLY and target_mode == NEO_PRIMARY_GOOGLE_MIRROR:
        from app.services.neosektor_live_counts import driver_routing_state_payload

        state = driver_routing_state_payload(gateway)
        mirror_neosektor_sheet_update({}, state, gateway=gateway, force=True)

    return neosektor_integration_status(gateway)


def google_primary_operational_values(gateway, force=False):
    """Return authoritative Google operational values without writing Neon."""
    gateway = _resolve_gateway(gateway)
    if not gateway:
        raise NeoSektorGoogleError("NeoSektor gateway is unavailable.")
    if not force:
        cached = _cached_google_values(gateway.id)
        if cached is not None:
            return cached
    if not sheets_credentials_configured():
        raise NeoSektorGoogleError("NeoSektor Google Sheets credentials are missing.")

    try:
        values = normalize_operational_cell_values(read_neosektor_sheet_values())
    except NeoSektorGoogleError:
        raise
    except Exception as error:
        _log_safe_warning("read", error, cell_count=len(SHEET_CELL_ORDER))
        raise NeoSektorGoogleError("NeoSektor could not read its Google live state.") from error

    _store_google_values(gateway.id, values)
    return dict(values)


def write_google_primary_operational_values(gateway, updates):
    """Write Mode 1 operational edits directly to Google or fail visibly."""
    if neosektor_integration_mode(gateway) != GOOGLE_PRIMARY:
        raise NeoSektorGoogleError("NeoSektor is not in GOOGLE PRIMARY mode.")
    normalized = normalize_operational_cell_values(updates, require_complete=False)
    _write_google_operational_values(gateway, normalized)
    return normalized


def mirror_neosektor_sheet_update(
    before_state,
    after_state,
    gateway=None,
    *,
    force=False,
):
    """Mirror authoritative Neon operational values after the Neo commit."""
    gateway = _resolve_gateway(gateway)
    if not gateway or neosektor_integration_mode(gateway) != NEO_PRIMARY_GOOGLE_MIRROR:
        return {"status": "skipped", "updated": 0}

    before_values = _sheet_values_from_state(before_state)
    after_values = _sheet_values_from_state(after_state)
    updates = {
        cell: after_values[cell]
        for cell in SHEET_CELL_ORDER
        if force or before_values.get(cell) != after_values.get(cell)
    }
    if not updates:
        return {"status": "unchanged", "updated": 0}

    try:
        _write_google_operational_values(gateway, updates)
    except Exception as error:
        _record_mirror_failure(gateway, error)
        return {"status": "error", "updated": 0}

    _record_mirror_success(gateway)
    return {"status": "mirrored", "updated": len(updates)}


def retry_neosektor_google_mirror(gateway):
    if neosektor_integration_mode(gateway) != NEO_PRIMARY_GOOGLE_MIRROR:
        raise ValueError("Google mirror retry is available only in NEO PRIMARY + GOOGLE MIRROR mode.")

    from app.services.neosektor_live_counts import driver_routing_state_payload

    result = mirror_neosektor_sheet_update(
        {},
        driver_routing_state_payload(gateway),
        gateway=gateway,
        force=True,
    )
    if result["status"] != "mirrored":
        raise NeoSektorGoogleError("Google mirror retry failed. Neo remains authoritative.")
    return result


def read_neosektor_sheet_values(worksheet=None):
    """Read the fixed operational contract in one Google batch request."""
    worksheet = worksheet or _get_worksheet()
    batch_values = worksheet.batch_get(list(SHEET_CELL_ORDER))
    return {
        cell: _batch_item_value(batch_values[index])
        if index < len(batch_values)
        else None
        for index, cell in enumerate(SHEET_CELL_ORDER)
    }


def normalize_operational_cell_values(values, require_complete=True):
    """Validate and normalize exactly the live operational Google contract."""
    values = values or {}
    unknown = set(values) - set(SHEET_CELL_ORDER)
    if unknown:
        raise NeoSektorGoogleError("Google returned unsupported NeoSektor cells.")

    normalized = {}
    cells = SHEET_CELL_ORDER if require_complete else tuple(values)
    for cell in cells:
        value = values.get(cell)
        if cell in COUNT_CELL_MAXIMUMS:
            normalized[cell] = _normalized_count(
                value,
                COUNT_CELL_MAXIMUMS[cell],
            )
        elif cell in STATUS_CELLS:
            normalized[cell] = _normalized_status(value)
    return normalized


def sheets_credentials_configured():
    return bool(
        os.environ.get("GOOGLE_SHEETS_ID")
        and os.environ.get("GOOGLE_SHEETS_TAB")
        and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    )


def clear_neosektor_google_cache(gateway=None):
    gateway = _resolve_gateway(gateway)
    with _google_state_cache_lock:
        if gateway:
            _google_state_cache.pop(gateway.id, None)
        else:
            _google_state_cache.clear()


# Backward-compatible service names remain importable while the old toggle UI
# is retired. They now describe the canonical three-state integration mode.
def sheets_compatibility_enabled(gateway=None):
    return neosektor_integration_mode(gateway) != NEO_ONLY


def sheets_compatibility_status(gateway=None):
    status = neosektor_integration_status(gateway)
    return {"enabled": status["mode"] != NEO_ONLY, **status}


def set_sheets_compatibility_enabled(gateway, enabled):
    settings = ensure_neosektor_integration_setting(gateway)
    settings.integration_mode = GOOGLE_PRIMARY if enabled else NEO_ONLY
    settings.google_sheets_compat_enabled = bool(enabled)
    db.session.flush()
    return settings


def ensure_sheets_compatibility_setting(gateway):
    return ensure_neosektor_integration_setting(gateway)


def sync_neosektor_from_google_if_due(gateway, now=None):
    """Deprecated read-through shim; it never imports operational rows to Neon."""
    if neosektor_integration_mode(gateway) != GOOGLE_PRIMARY:
        return {"status": "disabled", "updated": 0}
    try:
        google_primary_operational_values(gateway)
    except NeoSektorGoogleError:
        return {"status": "error", "updated": 0}
    return {"status": "read", "updated": 0}


def _write_google_operational_values(gateway, updates):
    if not updates:
        return
    if not sheets_credentials_configured():
        raise NeoSektorGoogleError("NeoSektor Google Sheets credentials are missing.")
    try:
        worksheet = _get_worksheet()
        for cell in SHEET_CELL_ORDER:
            if cell in updates:
                worksheet.update_acell(cell, updates[cell])
    except Exception as error:
        _log_safe_warning("write", error, cell_count=len(updates))
        raise NeoSektorGoogleError("NeoSektor could not save the Google live state.") from error
    _merge_cached_google_values(gateway.id, updates)


def _record_mirror_failure(gateway, error):
    settings = ensure_neosektor_integration_setting(gateway)
    settings.google_mirror_sync_needed = True
    settings.google_mirror_last_error = "Google mirror failed. Retry is required."
    settings.google_mirror_failed_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    _log_safe_warning("mirror", error)


def _record_mirror_success(gateway):
    settings = ensure_neosektor_integration_setting(gateway)
    if not settings.google_mirror_sync_needed and not settings.google_mirror_last_error:
        return
    _clear_mirror_warning(settings)
    db.session.commit()


def _clear_mirror_warning(settings):
    settings.google_mirror_sync_needed = False
    settings.google_mirror_last_error = None
    settings.google_mirror_failed_at_utc = None


def _get_worksheet():
    """Open the exact worksheet configured for standalone NeoSektor."""
    if gspread is None:
        raise RuntimeError("gspread unavailable")

    credentials = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if "private_key" in credentials:
        credentials["private_key"] = credentials["private_key"].replace("\\n", "\n")
    client = gspread.service_account_from_dict(credentials)
    spreadsheet = client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    return spreadsheet.worksheet(os.environ["GOOGLE_SHEETS_TAB"])


def _cached_google_values(gateway_id):
    now = time.monotonic()
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway_id)
        if not cached or now - cached["stored_at"] >= GOOGLE_TRANSIENT_CACHE_SECONDS:
            return None
        return dict(cached["values"])


def _store_google_values(gateway_id, values):
    with _google_state_cache_lock:
        _google_state_cache[gateway_id] = {
            "stored_at": time.monotonic(),
            "values": dict(values),
        }


def _merge_cached_google_values(gateway_id, updates):
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway_id)
        values = dict(cached["values"]) if cached else {}
        values.update(updates)
        _google_state_cache[gateway_id] = {
            "stored_at": time.monotonic(),
            "values": values,
        }


def _sheet_values_from_state(state):
    sides = (state or {}).get("sides") or {}
    waves = (state or {}).get("waves") or []
    return {
        "B2": _side_wave_count(sides, "east", "first"),
        "C2": _side_wave_count(sides, "west", "first"),
        "D2": _wave_planned_count(waves, "1ST WAVE"),
        "B3": _side_wave_count(sides, "east", "second"),
        "C3": _side_wave_count(sides, "west", "second"),
        "D3": _wave_planned_count(waves, "2ND WAVE"),
        "B4": _safe_int((sides.get("east") or {}).get("open_bays")),
        "C4": _safe_int((sides.get("west") or {}).get("open_bays")),
        "B6": _bay_status(sides, "Bay 1"),
        "B8": _bay_status(sides, "Bay 2"),
        "B10": _bay_status(sides, "Bay 3"),
        "C6": _bay_status(sides, "Bay 4"),
        "C8": _bay_status(sides, "Bay 5"),
    }


def _side_wave_count(sides, side_key, wave_key):
    for wave in (sides.get(side_key) or {}).get("waves") or []:
        if wave.get("key") == wave_key:
            return _safe_int(wave.get("count"))
    return 0


def _wave_planned_count(waves, wave_name):
    for wave in waves:
        if wave.get("name") == wave_name:
            return _safe_int(wave.get("planned"))
    return 0


def _bay_status(sides, bay_name):
    for side in sides.values():
        for bay in side.get("bays") or []:
            if bay.get("bay_name") == bay_name:
                return str(bay.get("status") or "Empty")
    return "Empty"


def _normalized_count(value, maximum):
    if value is None or str(value).strip() == "":
        return 0
    if isinstance(value, bool):
        raise NeoSektorGoogleError("Google contains an invalid NeoSektor count.")
    try:
        numeric = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise NeoSektorGoogleError("Google contains an invalid NeoSektor count.") from error
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        raise NeoSektorGoogleError("Google contains an invalid NeoSektor count.")
    parsed = int(numeric)
    if parsed < 0 or parsed > maximum:
        raise NeoSektorGoogleError("Google contains an out-of-range NeoSektor count.")
    return parsed


def _normalized_status(value):
    if value is None or str(value).strip() == "":
        return "Empty"
    normalized = str(value).strip().title()
    if normalized not in STATUS_LABELS:
        raise NeoSektorGoogleError("Google contains an invalid NeoSektor bay status.")
    return normalized


def _normalized_mode(value, strict=False):
    normalized = str(value or DEFAULT_NEOSEKTOR_INTEGRATION_MODE).strip().lower()
    if normalized in NEOSEKTOR_INTEGRATION_MODES:
        return normalized
    if strict:
        raise ValueError("Choose a valid NeoSektor integration mode.")
    return DEFAULT_NEOSEKTOR_INTEGRATION_MODE


def _batch_item_value(item):
    if hasattr(item, "first"):
        try:
            return item.first(default=None)
        except TypeError:
            return item.first()
    if isinstance(item, dict):
        item = item.get("values")
    while isinstance(item, (list, tuple)):
        if not item:
            return None
        item = item[0]
    return item


def _safe_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _existing_operational_settings(gateway=None):
    if not has_app_context():
        return None
    gateway = _resolve_gateway(gateway)
    if not gateway:
        return None
    return NeoSektorOperationalSetting.query.filter_by(gateway_id=gateway.id).first()


def _resolve_gateway(gateway=None):
    if gateway is not None:
        return gateway
    try:
        return get_current_gateway()
    except Exception:
        return None


def _log_safe_warning(operation, error, cell_count=None):
    details = {"operation": operation, "exception_class": error.__class__.__name__}
    if cell_count is not None:
        details["cell_count"] = cell_count
    logger.warning("NeoSektor Google transition warning: %s", details)
