"""Temporary NeoGateway-to-standalone NeoSektor Google Sheets bridge.

The standalone NeoSektor application remains the owner of its Google Sheets
integration. This adapter mirrors only the existing, fixed-cell operational
values after a NeoGateway user has committed an update to the NeoGateway
database. It deliberately has no read or polling hooks so it can be removed
cleanly when the standalone application is retired.
"""

from __future__ import annotations

import json
import logging
import os

from flask import has_app_context

try:
    import gspread
except ImportError:  # Optional locally; production installs the dependency.
    gspread = None


from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services.access_control import get_current_gateway


logger = logging.getLogger(__name__)

# These cells are the existing standalone NeoSektor sheet contract. Updating
# cells preserves the sheet's rows, columns, formulas, and formatting.
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
    "B13",
    "B14",
    "B15",
)


def mirror_neosektor_sheet_update(before_state, after_state, gateway=None):
    """Mirror changed standalone-compatible values without affecting DB success.

    Routes call this only after the database transaction commits. Read, poll,
    refresh, and page-render paths never call the bridge. Values that do not
    have an established standalone sheet cell are intentionally not invented.
    """
    if not sheets_compatibility_enabled(gateway):
        return False

    try:
        before_values = _sheet_values_from_state(before_state)
        after_values = _sheet_values_from_state(after_state)
        updates = [
            (cell, after_values[cell])
            for cell in SHEET_CELL_ORDER
            if before_values.get(cell) != after_values.get(cell)
        ]
    except Exception as error:  # Never let a bridge problem affect the update.
        _log_safe_warning("state comparison", error)
        return False

    if not updates:
        return False

    if not sheets_credentials_configured():
        _log_safe_warning("configuration", RuntimeError("missing Google Sheets credentials"))
        return False

    try:
        worksheet = _get_worksheet()
        for cell, value in updates:
            # Match the standalone writer's individual-cell update behavior.
            worksheet.update_acell(cell, value)
    except Exception as error:
        _log_safe_warning("write", error, cell_count=len(updates))
        return False

    return True


def sheets_compatibility_enabled(gateway=None):
    """Return whether gateway-scoped Google Sheets mirroring is explicitly ON."""
    settings = _existing_operational_settings(gateway)
    return bool(settings and settings.google_sheets_compat_enabled)


def sheets_credentials_configured():
    return bool(
        os.environ.get("GOOGLE_SHEETS_ID")
        and os.environ.get("GOOGLE_SHEETS_TAB")
        and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    )


def sheets_compatibility_status(gateway=None):
    settings = _existing_operational_settings(gateway)
    enabled = bool(settings and settings.google_sheets_compat_enabled)
    return {
        "enabled": enabled,
        "credentials_configured": sheets_credentials_configured(),
        "sheet_id_configured": bool(os.environ.get("GOOGLE_SHEETS_ID")),
        "sheet_tab_configured": bool(os.environ.get("GOOGLE_SHEETS_TAB")),
        "service_account_configured": bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")),
    }


def set_sheets_compatibility_enabled(gateway, enabled):
    settings = NeoSektorOperationalSetting.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        settings = NeoSektorOperationalSetting(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            google_sheets_compat_enabled=False,
        )
        db.session.add(settings)

    settings.gateway_code = gateway.code
    settings.google_sheets_compat_enabled = bool(enabled)
    db.session.flush()
    return settings


def ensure_sheets_compatibility_setting(gateway):
    settings = NeoSektorOperationalSetting.query.filter_by(gateway_id=gateway.id).first()
    if settings:
        settings.gateway_code = gateway.code
        if settings.google_sheets_compat_enabled is None:
            settings.google_sheets_compat_enabled = False
        db.session.flush()
        return settings

    settings = NeoSektorOperationalSetting(
        gateway_id=gateway.id,
        gateway_code=gateway.code,
        google_sheets_compat_enabled=False,
    )
    db.session.add(settings)
    db.session.flush()
    return settings


def _get_worksheet():
    """Open the exact worksheet configured for the standalone application."""
    if gspread is None:
        raise RuntimeError("gspread unavailable")

    credentials = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if "private_key" in credentials:
        credentials["private_key"] = credentials["private_key"].replace("\\n", "\n")

    client = gspread.service_account_from_dict(credentials)
    spreadsheet = client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])
    return spreadsheet.worksheet(os.environ["GOOGLE_SHEETS_TAB"])


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


def _sheet_values_from_state(state):
    """Translate a NeoGateway live-count state payload into standalone cells."""
    sides = (state or {}).get("sides") or {}
    waves = (state or {}).get("waves") or []
    settings = (state or {}).get("operational_settings") or {}
    routing = (state or {}).get("routing") or {}

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
        "B13": _safe_int(settings.get("first_modifier")),
        "B14": _safe_int(settings.get("second_modifier")),
        "B15": _safe_int(routing.get("west_offset")),
    }


def _side_wave_count(sides, side_key, wave_key):
    side = sides.get(side_key) or {}
    for wave in side.get("waves") or []:
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


def _safe_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _log_safe_warning(operation, error, cell_count=None):
    details = {
        "operation": operation,
        "exception_class": error.__class__.__name__,
    }
    if cell_count is not None:
        details["cell_count"] = cell_count
    logger.warning("NeoSektor Sheets compatibility warning: %s", details)
