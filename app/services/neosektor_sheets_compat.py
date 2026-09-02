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
from datetime import datetime, timedelta, timezone
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
# Keep the legacy default until an operator deliberately selects the intended
# production mode. This avoids silently changing authority for a gateway merely
# because the application was deployed or restarted.
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

# This control is deliberately opt-in.  Existing deployments and the existing
# standalone application only know about the operational worksheet, so merely
# deploying NeoApps must not silently change who can write those cells.  When
# both applications have been upgraded, configure this tab name in both
# deployments and initialize its one-record contract explicitly.
NEOSEKTOR_SHARED_AUTHORITY_TAB_ENV = "GOOGLE_SHEETS_NEOSEKTOR_AUTHORITY_TAB"
NEOSEKTOR_SHARED_AUTHORITY_HEADERS = (
    "authority",
    "generation",
    "changed_at",
    "changed_by",
    "metadata",
)
NEOSEKTOR_SHARED_AUTHORITY_CELLS = ("A2", "B2", "C2", "D2", "E2")
NEO_PRIMARY_AUTHORITY = "neo_primary"
STANDALONE_PRIMARY_AUTHORITY = "standalone_primary"
LEGACY_UNMANAGED_AUTHORITY = "legacy_unmanaged"
NEOSEKTOR_SHARED_AUTHORITIES = (
    NEO_PRIMARY_AUTHORITY,
    STANDALONE_PRIMARY_AUTHORITY,
)
NEOSEKTOR_SHARED_AUTHORITY_LABELS = {
    NEO_PRIMARY_AUTHORITY: "NEO PRIMARY",
    STANDALONE_PRIMARY_AUTHORITY: "STANDALONE PRIMARY",
    LEGACY_UNMANAGED_AUTHORITY: "NOT ACTIVATED",
}
NEOSEKTOR_COMPAT_FIELD_LABELS = {
    "B2": ("1ST WAVE · EAST LIVE COUNT", "ULD / live counter"),
    "C2": ("1ST WAVE · WEST LIVE COUNT", "ULD / live counter"),
    "D2": ("1ST WAVE · LEFT TO ARRIVE", "Wave planned count"),
    "B3": ("2ND WAVE · EAST LIVE COUNT", "ULD / live counter"),
    "C3": ("2ND WAVE · WEST LIVE COUNT", "ULD / live counter"),
    "D3": ("2ND WAVE · LEFT TO ARRIVE", "Wave planned count"),
    "B4": ("EAST OPEN BAYS", "Open-bay counter"),
    "C4": ("WEST OPEN BAYS", "Open-bay counter"),
    "B6": ("BAY 1 STATUS", "Bay status"),
    "B8": ("BAY 2 STATUS", "Bay status"),
    "B10": ("BAY 3 STATUS", "Bay status"),
    "C6": ("BAY 4 STATUS", "Bay status"),
    "C8": ("BAY 5 STATUS", "Bay status"),
}

# Authority is read immediately before each Neo Google write.  It is not used
# by live page refreshes, so this does not create another polling path.
SHARED_AUTHORITY_CACHE_SECONDS = 5.0
_shared_authority_cache = {}
_shared_authority_cache_lock = threading.Lock()


class NeoSektorGoogleError(ValueError):
    """Safe, user-displayable NeoSektor Google transition failure."""


class NeoSektorSharedAuthorityError(NeoSektorGoogleError):
    """The shared Google authority record is unavailable, stale, or invalid."""


class NeoSektorRecoveryError(NeoSektorSharedAuthorityError):
    """A fenced NeoSektor recovery or return-control operation could not finish."""


def neosektor_integration_mode(gateway=None, *, settings=None):
    if settings is None:
        settings = _existing_operational_settings(gateway)
    return _normalized_mode(getattr(settings, "integration_mode", None))


def neosektor_integration_status(gateway=None, *, settings=None):
    gateway = _resolve_gateway(gateway)
    if settings is None:
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
        "google_mirror_writes_enabled": neosektor_google_mirror_writes_enabled(
            gateway,
            settings=settings,
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
    if settings.google_mirror_writes_enabled is None:
        settings.google_mirror_writes_enabled = False
    db.session.flush()
    return settings


def change_neosektor_integration_mode(gateway, requested_mode):
    """Persist the requested NeoSektor authority mode without importing Google.

    Google remains authoritative only while the legacy GOOGLE PRIMARY mode is
    selected. Entering either Neo-primary mode deliberately retains Neon state;
    operators explicitly enable the one-way mirror after reviewing it.
    """
    target_mode = _normalized_mode(requested_mode, strict=True)
    settings = ensure_neosektor_integration_setting(gateway)
    current_mode = _normalized_mode(settings.integration_mode)
    if current_mode == target_mode:
        return neosektor_integration_status(gateway)

    settings.integration_mode = target_mode
    if target_mode != NEO_PRIMARY_GOOGLE_MIRROR:
        # Re-entering mirror mode requires another explicit enable + full
        # Neon-to-Google sync; do not carry a prior opt-in across modes.
        settings.google_mirror_writes_enabled = False
        _clear_mirror_warning(settings)
    db.session.commit()

    return neosektor_integration_status(gateway)


def neosektor_google_mirror_writes_enabled(gateway=None, *, settings=None):
    """Whether this gateway permits one-way NeoSektor Google mirroring."""
    gateway = _resolve_gateway(gateway)
    if not gateway:
        return False
    if settings is None:
        settings = _existing_operational_settings(gateway)
    return bool(getattr(settings, "google_mirror_writes_enabled", False))


def set_neosektor_google_mirror_writes(gateway, enabled):
    """Enable/disable the gateway's one-way NeoSektor Google mirror.

    Enabling first force-syncs the complete canonical Neon compatibility state.
    The flag is set only after that write succeeds, so a failed enablement never
    authorizes later background mirroring.
    """
    gateway = _resolve_gateway(gateway)
    if not gateway:
        raise NeoSektorGoogleError("NeoSektor gateway is unavailable.")

    settings = ensure_neosektor_integration_setting(gateway)
    if not bool(enabled):
        settings.google_mirror_writes_enabled = False
        db.session.commit()
        return False

    if neosektor_integration_mode(gateway, settings=settings) != NEO_PRIMARY_GOOGLE_MIRROR:
        raise ValueError(
            "Google Mirror Writes can be enabled only in NEO PRIMARY + GOOGLE MIRROR mode."
        )

    from app.services.neosektor_live_counts import canonical_neosektor_compat_values

    # Publish OFF before the force-sync so another web worker cannot mirror an
    # intervening Neo edit under a stale enabled preference.  ON is still
    # persisted only after the complete first mirror succeeds.
    settings.google_mirror_writes_enabled = False
    db.session.commit()
    canonical_values = canonical_neosektor_compat_values(gateway)
    result = mirror_neosektor_operational_values(
        {},
        canonical_values,
        gateway=gateway,
        force=True,
        integration_mode=NEO_PRIMARY_GOOGLE_MIRROR,
        allow_when_disabled=True,
    )
    if result["status"] != "mirrored":
        raise NeoSektorGoogleError(
            "NeoSektor Google mirror could not be enabled; mirror writes remain OFF."
        )

    settings.google_mirror_writes_enabled = True
    db.session.commit()
    return True


def neosektor_shared_authority_status(gateway=None, *, force=False):
    """Return the external write-fence state without changing it.

    A missing authority-tab configuration is intentionally reported as legacy
    compatibility rather than inferred as Neo primary.  This keeps existing
    Google-primary deployments unchanged until both writers are upgraded.
    """
    gateway = _resolve_gateway(gateway)
    if not _shared_authority_tab_name():
        return _legacy_shared_authority_status("not_configured")
    try:
        return read_neosektor_shared_authority(gateway, force=force)
    except NeoSektorSharedAuthorityError as error:
        return {
            **_legacy_shared_authority_status("unavailable"),
            "available": False,
            "error": str(error),
        }


def read_neosektor_shared_authority(gateway=None, *, force=False, worksheet=None):
    """Read the dedicated shared NeoSektor authority record.

    This is a read-only operation.  The standalone implementation can use the
    same five-cell contract: A2 authority, B2 generation, C2 changed-at, D2
    actor, and E2 JSON metadata.
    """
    gateway = _resolve_gateway(gateway)
    if not _shared_authority_tab_name():
        return _legacy_shared_authority_status("not_configured")
    if not gateway:
        raise NeoSektorSharedAuthorityError("NeoSektor gateway is unavailable.")
    if not force:
        cached = _cached_shared_authority(gateway.id)
        if cached is not None:
            return cached
    if not sheets_credentials_configured():
        raise NeoSektorSharedAuthorityError(
            "NeoSektor Google Sheets credentials are missing."
        )

    try:
        worksheet = worksheet or _get_shared_authority_worksheet()
        batch_values = worksheet.batch_get(list(NEOSEKTOR_SHARED_AUTHORITY_CELLS))
        values = [
            _batch_item_value(batch_values[index])
            if index < len(batch_values)
            else None
            for index in range(len(NEOSEKTOR_SHARED_AUTHORITY_CELLS))
        ]
    except Exception as error:
        if _is_missing_worksheet_error(error):
            return _legacy_shared_authority_status("tab_missing")
        _log_safe_warning("shared authority read", error)
        raise NeoSektorSharedAuthorityError(
            "NeoSektor could not read its shared authority record."
        ) from error

    status = _normalized_shared_authority_record(values)
    _store_shared_authority(gateway.id, status)
    return dict(status)


def initialize_neosektor_shared_authority(gateway, *, actor=None, metadata=None):
    """Create the opt-in authority tab with Neo as generation-one primary.

    No route calls this yet.  It is deliberately an explicit deployment/control
    action so an unmodified standalone application never loses its legacy
    ability to write the operational sheet merely because NeoApps was deployed.
    """
    gateway = _resolve_gateway(gateway)
    if not gateway:
        raise NeoSektorSharedAuthorityError("NeoSektor gateway is unavailable.")
    if not _shared_authority_tab_name():
        raise NeoSektorSharedAuthorityError(
            "Configure the dedicated NeoSektor shared authority tab first."
        )
    current = read_neosektor_shared_authority(gateway, force=True)
    if current["record_present"]:
        return current
    worksheet = _get_shared_authority_worksheet(create=True)
    _write_shared_authority_headers(worksheet)
    status = _shared_authority_status(
        authority=NEO_PRIMARY_AUTHORITY,
        generation=1,
        changed_at=_authority_timestamp(),
        changed_by=_normalized_authority_actor(actor),
        metadata=_normalized_authority_metadata(metadata),
        record_present=True,
        source="shared_record",
    )
    _write_shared_authority_record(worksheet, status)
    verified = read_neosektor_shared_authority(gateway, force=True)
    if (
        verified["authority"] != NEO_PRIMARY_AUTHORITY
        or verified["generation"] != 1
    ):
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority initialization could not be verified."
        )
    return verified


def transition_neosektor_shared_authority(
    gateway,
    requested_authority,
    *,
    expected_generation,
    actor=None,
    metadata=None,
):
    """Compare, write, then verify a shared authority generation transition.

    Google Sheets has no compare-and-swap primitive through this integration.
    This is therefore a read/expected-generation/write/re-read fence, not a
    claim of atomic CAS.  Both writers must perform this same verification and
    must stop operational writes before changing the record.
    """
    target = _normalized_shared_authority(requested_authority, strict=True)
    if isinstance(expected_generation, bool):
        raise NeoSektorSharedAuthorityError("Use the current authority generation.")
    try:
        expected = int(expected_generation)
    except (TypeError, ValueError) as error:
        raise NeoSektorSharedAuthorityError("Use the current authority generation.") from error
    if expected < 0:
        raise NeoSektorSharedAuthorityError("Use the current authority generation.")

    gateway = _resolve_gateway(gateway)
    current = read_neosektor_shared_authority(gateway, force=True)
    if current["generation"] != expected:
        raise NeoSektorSharedAuthorityError(
            "NeoSektor authority changed. Refresh before taking control."
        )
    if current["record_present"] and current["authority"] == target:
        return current
    if not _shared_authority_tab_name():
        raise NeoSektorSharedAuthorityError(
            "Configure the dedicated NeoSektor shared authority tab first."
        )

    worksheet = _get_shared_authority_worksheet(create=not current["record_present"])
    if not current["record_present"]:
        _write_shared_authority_headers(worksheet)
    proposed = _shared_authority_status(
        authority=target,
        generation=expected + 1,
        changed_at=_authority_timestamp(),
        changed_by=_normalized_authority_actor(actor),
        metadata=_normalized_authority_metadata(metadata),
        record_present=True,
        source="shared_record",
    )
    _write_shared_authority_record(worksheet, proposed)
    verified = read_neosektor_shared_authority(gateway, force=True)
    if (
        verified["authority"] != proposed["authority"]
        or verified["generation"] != proposed["generation"]
        or verified["changed_at"] != proposed["changed_at"]
        or verified["changed_by"] != proposed["changed_by"]
        or verified["metadata"] != proposed["metadata"]
    ):
        raise NeoSektorSharedAuthorityError(
            "NeoSektor authority transition conflicted. No control change was confirmed."
        )
    return verified


def preview_neosektor_standalone_reconciliation(
    gateway,
    *,
    expected_generation=None,
):
    """Compare a fresh standalone snapshot with persisted Neon compatibility rows."""
    authority = _require_standalone_recovery_authority(
        gateway,
        expected_generation=expected_generation,
    )
    standalone_values = google_primary_operational_values(gateway, force=True)
    _require_standalone_recovery_authority(
        gateway,
        expected_generation=authority["generation"],
    )
    canonical_values = _canonical_neosektor_compat_values(gateway)
    return _reconciliation_preview(
        authority,
        standalone_values,
        canonical_values,
    )


def neosektor_standalone_recovery_context(gateway):
    """Build read-only authority/reconciliation context for System Settings."""
    authority = neosektor_shared_authority_status(gateway, force=True)
    context = {
        "authority": authority,
        "preview": None,
        "preview_error": None,
        "reconciled_current_generation": bool(
            _current_generation_reconciliation(authority)
        ),
    }
    if authority["authority"] != STANDALONE_PRIMARY_AUTHORITY:
        return context
    if not authority["available"]:
        context["preview_error"] = authority.get("error") or (
            "NeoSektor shared authority is unavailable."
        )
        return context
    try:
        context["preview"] = preview_neosektor_standalone_reconciliation(
            gateway,
            expected_generation=authority["generation"],
        )
    except NeoSektorGoogleError as error:
        context["preview_error"] = str(error)
    return context


def reconcile_neosektor_standalone_state(
    gateway,
    *,
    expected_generation,
    actor=None,
):
    """Import a fenced standalone snapshot into Neon without reclaiming authority."""
    authority = _require_standalone_recovery_authority(
        gateway,
        expected_generation=expected_generation,
    )
    standalone_values = google_primary_operational_values(gateway, force=True)
    canonical_before = _canonical_neosektor_compat_values(gateway)
    preview = _reconciliation_preview(
        authority,
        standalone_values,
        canonical_before,
    )
    _require_standalone_recovery_authority(
        gateway,
        expected_generation=authority["generation"],
    )

    try:
        from app.services.neosektor_live_counts import (
            apply_standalone_compat_values,
        )

        changed = apply_standalone_compat_values(gateway, standalone_values)
        db.session.flush()
        canonical_after = _canonical_neosektor_compat_values(gateway)
        if canonical_after != standalone_values:
            raise NeoSektorRecoveryError(
                "NeoSektor standalone values could not be verified in Neon."
            )
        _require_standalone_recovery_authority(
            gateway,
            expected_generation=authority["generation"],
        )
        db.session.commit()
    except NeoSektorRecoveryError:
        db.session.rollback()
        raise
    except Exception as error:
        db.session.rollback()
        _log_safe_warning("standalone reconciliation", error)
        raise NeoSektorRecoveryError(
            "NeoSektor standalone state could not be reconciled into Neon."
        ) from error

    # A commit cannot be rolled back after this point.  A changed authority
    # generation therefore leaves the imported rows fenced from RETURN CONTROL
    # until a new reconciliation is completed for that newer generation.
    _require_standalone_recovery_authority(
        gateway,
        expected_generation=authority["generation"],
    )
    reconciliation = {
        "generation": authority["generation"],
        "reconciled_at": _authority_timestamp(),
        "reconciled_by": _normalized_authority_actor(actor),
        "changed_cells": [
            row["cell"] for row in preview["fields"] if row["will_change"]
        ],
        "snapshot": standalone_values,
    }
    verified_authority = _record_neosektor_reconciliation_metadata(
        gateway,
        authority,
        reconciliation,
    )
    return {
        "authority": verified_authority,
        "changed": changed,
        "preview": preview,
        "reconciliation": reconciliation,
    }


def return_neosektor_control_to_neo(
    gateway,
    *,
    expected_generation,
    actor=None,
):
    """Deliberately transition a reconciled standalone generation back to Neo."""
    authority = _require_standalone_recovery_authority(
        gateway,
        expected_generation=expected_generation,
    )
    reconciliation = _current_generation_reconciliation(authority)
    if reconciliation is None:
        raise NeoSektorRecoveryError(
            "Reconcile the current standalone authority generation before returning control to Neo."
        )

    metadata = dict(authority["metadata"])
    metadata["return_control"] = {
        "from_generation": authority["generation"],
        "returned_at": _authority_timestamp(),
        "returned_by": _normalized_authority_actor(actor),
        "reconciliation_generation": reconciliation["generation"],
        # A primary record written during recovery remains fenced until a
        # forced read confirms the completed return-control record below.
        "verified": False,
    }
    try:
        transitioned = transition_neosektor_shared_authority(
            gateway,
            NEO_PRIMARY_AUTHORITY,
            expected_generation=authority["generation"],
            actor=actor,
            metadata=metadata,
        )
        return _verify_neosektor_return_control(gateway, transitioned)
    except NeoSektorSharedAuthorityError:
        raise
    except Exception as error:
        _log_safe_warning("return control to Neo", error)
        raise NeoSektorRecoveryError(
            "NeoSektor control could not be returned to Neo."
        ) from error


def _require_standalone_recovery_authority(gateway, *, expected_generation=None):
    authority = read_neosektor_shared_authority(gateway, force=True)
    if not authority["record_present"]:
        raise NeoSektorRecoveryError(
            "NeoSektor shared authority is not configured for recovery."
        )
    if authority["authority"] != STANDALONE_PRIMARY_AUTHORITY:
        raise NeoSektorRecoveryError(
            "Standalone NeoSektor is not the current shared authority."
        )
    if expected_generation is not None:
        expected = _normalized_expected_generation(expected_generation)
        if authority["generation"] != expected:
            raise NeoSektorRecoveryError(
                "NeoSektor authority changed. Refresh and reconcile the current generation."
            )
    return authority


def _normalized_expected_generation(value):
    if isinstance(value, bool):
        raise NeoSektorRecoveryError("Use the current NeoSektor authority generation.")
    try:
        generation = int(value)
    except (TypeError, ValueError) as error:
        raise NeoSektorRecoveryError(
            "Use the current NeoSektor authority generation."
        ) from error
    if generation < 1:
        raise NeoSektorRecoveryError("Use the current NeoSektor authority generation.")
    return generation


def _canonical_neosektor_compat_values(gateway):
    from app.services.neosektor_live_counts import canonical_neosektor_compat_values

    values = canonical_neosektor_compat_values(gateway)
    return normalize_operational_cell_values(values)


def _reconciliation_preview(authority, standalone_values, canonical_values):
    fields = []
    for cell in SHEET_CELL_ORDER:
        label, category = NEOSEKTOR_COMPAT_FIELD_LABELS[cell]
        standalone_value = standalone_values[cell]
        canonical_value = canonical_values[cell]
        fields.append(
            {
                "cell": cell,
                "label": label,
                "category": category,
                "standalone_value": standalone_value,
                "canonical_value": canonical_value,
                "will_change": standalone_value != canonical_value,
            }
        )
    return {
        "authority_generation": authority["generation"],
        "fields": fields,
        "changed_count": sum(row["will_change"] for row in fields),
        "standalone_values": dict(standalone_values),
        "canonical_values": dict(canonical_values),
    }


def _record_neosektor_reconciliation_metadata(gateway, authority, reconciliation):
    current = _require_standalone_recovery_authority(
        gateway,
        expected_generation=authority["generation"],
    )
    metadata = dict(current["metadata"])
    metadata["reconciliation"] = reconciliation
    worksheet = _get_shared_authority_worksheet()
    _write_shared_authority_metadata(worksheet, metadata)
    verified = _require_standalone_recovery_authority(
        gateway,
        expected_generation=authority["generation"],
    )
    if verified["metadata"] != metadata:
        raise NeoSektorRecoveryError(
            "NeoSektor reconciliation metadata could not be verified."
        )
    return verified


def _current_generation_reconciliation(authority):
    reconciliation = (authority.get("metadata") or {}).get("reconciliation")
    if not isinstance(reconciliation, dict):
        return None
    try:
        generation = int(reconciliation.get("generation"))
    except (TypeError, ValueError):
        return None
    if generation != authority.get("generation"):
        return None
    if not str(reconciliation.get("reconciled_at") or "").strip():
        return None
    return reconciliation


def _verify_neosektor_return_control(gateway, transitioned_authority):
    """Mark a previously verified return record writable only after a fresh read."""
    current = read_neosektor_shared_authority(gateway, force=True)
    return_control = (current.get("metadata") or {}).get("return_control")
    if (
        current.get("authority") != NEO_PRIMARY_AUTHORITY
        or current.get("generation") != transitioned_authority.get("generation")
        or not isinstance(return_control, dict)
        or return_control.get("verified") is not False
    ):
        raise NeoSektorRecoveryError(
            "NeoSektor Return Control could not be verified. Neo remains fenced."
        )

    metadata = dict(current["metadata"])
    verified_return = dict(return_control)
    verified_return["verified"] = True
    verified_return["verified_at"] = _authority_timestamp()
    metadata["return_control"] = verified_return
    worksheet = _get_shared_authority_worksheet()
    _write_shared_authority_metadata(worksheet, metadata)
    verified = read_neosektor_shared_authority(gateway, force=True)
    if (
        verified.get("authority") != NEO_PRIMARY_AUTHORITY
        or verified.get("generation") != transitioned_authority.get("generation")
        or verified.get("metadata") != metadata
        or not verified.get("can_neo_write")
    ):
        raise NeoSektorRecoveryError(
            "NeoSektor Return Control could not be verified. Neo remains fenced."
        )
    return verified


def neo_may_write_neosektor_google(gateway=None, *, force=False):
    """Whether the shared fence permits Neo operational Google writes."""
    status = read_neosektor_shared_authority(gateway, force=force)
    return bool(status["can_neo_write"])


def standalone_may_write_neosektor_google(gateway=None, *, force=False):
    """Expose the complementary decision for a later standalone implementation."""
    status = read_neosektor_shared_authority(gateway, force=force)
    return bool(status["can_standalone_write"])


def assert_neo_may_write_neosektor_google(gateway):
    """Fence an imminent Neo operational write with a fresh authority read."""
    status = read_neosektor_shared_authority(gateway, force=True)
    if not status["can_neo_write"]:
        raise NeoSektorSharedAuthorityError(
            "Standalone NeoSektor currently controls the shared operational sheet."
        )
    return status


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


def google_primary_wave_timer_starts(gateway, *, operational_settings=None):
    """Return process-local ALL UP starts for Google-owned waves.

    Google has no timer columns.  Keep the timer process-local, but apply the
    same side-specific ALL UP and wave-activation rules used by Neon.
    """
    gateway = _resolve_gateway(gateway)
    if not gateway:
        return {}
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway.id) or {}
        values = cached.get("values") or {}
        starts = _updated_google_wave_timers(
            cached.get("wave_all_up_started_at"),
            values,
            down_timer_minutes=_google_primary_down_timer_minutes(
                operational_settings
            ),
        )
        if cached:
            cached["wave_all_up_started_at"] = starts
            _google_state_cache[gateway.id] = cached
        return dict(starts)


def write_google_primary_operational_values(
    gateway,
    updates,
    *,
    integration_mode=None,
):
    """Write Mode 1 operational edits directly to Google or fail visibly."""
    mode = (
        _normalized_mode(integration_mode)
        if integration_mode is not None
        else neosektor_integration_mode(gateway)
    )
    if mode != GOOGLE_PRIMARY:
        raise NeoSektorGoogleError("NeoSektor is not in GOOGLE PRIMARY mode.")
    normalized = normalize_operational_cell_values(updates, require_complete=False)
    assert_neo_may_write_neosektor_google(gateway)
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
    return mirror_neosektor_operational_values(
        _sheet_values_from_state(before_state),
        _sheet_values_from_state(after_state),
        gateway=gateway,
        force=force,
    )


def mirror_neosektor_operational_values(
    before_values,
    after_values,
    gateway=None,
    *,
    force=False,
    integration_mode=None,
    settings=None,
    warning_pending=None,
    allow_when_disabled=False,
):
    """Mirror compact cell snapshots after the authoritative Neo commit."""
    gateway = _resolve_gateway(gateway)
    mode = (
        _normalized_mode(integration_mode)
        if integration_mode is not None
        else neosektor_integration_mode(gateway, settings=settings)
    )
    if not gateway or mode != NEO_PRIMARY_GOOGLE_MIRROR:
        return {"status": "skipped", "updated": 0}
    if not allow_when_disabled and not neosektor_google_mirror_writes_enabled(
        gateway,
        settings=settings,
    ):
        return {"status": "mirror_writes_off", "updated": 0}

    updates = {
        cell: after_values[cell]
        for cell in SHEET_CELL_ORDER
        if force or before_values.get(cell) != after_values.get(cell)
    }
    if not updates:
        return {"status": "unchanged", "updated": 0}

    try:
        assert_neo_may_write_neosektor_google(gateway)
        _write_google_operational_values(gateway, updates)
    except NeoSektorSharedAuthorityError:
        return {"status": "blocked_by_shared_authority", "updated": 0}
    except Exception as error:
        _record_mirror_failure(gateway, error, settings=settings)
        return {"status": "error", "updated": 0}

    _record_mirror_success(
        gateway,
        settings=settings,
        warning_pending=warning_pending,
    )
    return {"status": "mirrored", "updated": len(updates)}


def retry_neosektor_google_mirror(gateway):
    if neosektor_integration_mode(gateway) != NEO_PRIMARY_GOOGLE_MIRROR:
        raise ValueError("Google mirror retry is available only in NEO PRIMARY + GOOGLE MIRROR mode.")

    if not neosektor_google_mirror_writes_enabled(gateway):
        raise NeoSektorGoogleError("NeoSektor Google Mirror Writes are OFF.")
    return set_neosektor_google_mirror_writes(gateway, True)


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
    with _shared_authority_cache_lock:
        if gateway:
            _shared_authority_cache.pop(gateway.id, None)
        else:
            _shared_authority_cache.clear()


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


def _record_mirror_failure(gateway, error, *, settings=None):
    settings = settings or ensure_neosektor_integration_setting(gateway)
    settings.google_mirror_sync_needed = True
    settings.google_mirror_last_error = "Google mirror failed. Retry is required."
    settings.google_mirror_failed_at_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    db.session.commit()
    _log_safe_warning("mirror", error)


def _record_mirror_success(
    gateway,
    *,
    settings=None,
    warning_pending=None,
):
    if warning_pending is False:
        return
    settings = settings or ensure_neosektor_integration_setting(gateway)
    if warning_pending is None and (
        not settings.google_mirror_sync_needed
        and not settings.google_mirror_last_error
    ):
        return
    _clear_mirror_warning(settings)
    db.session.commit()


def _clear_mirror_warning(settings):
    settings.google_mirror_sync_needed = False
    settings.google_mirror_last_error = None
    settings.google_mirror_failed_at_utc = None


def _get_worksheet():
    """Open the exact operational worksheet configured for standalone NeoSektor."""
    return _get_spreadsheet().worksheet(os.environ["GOOGLE_SHEETS_TAB"])


def _get_spreadsheet():
    """Open the configured shared workbook once for operational/control access."""
    if gspread is None:
        raise RuntimeError("gspread unavailable")

    credentials = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if "private_key" in credentials:
        credentials["private_key"] = credentials["private_key"].replace("\\n", "\n")
    client = gspread.service_account_from_dict(credentials)
    return client.open_by_key(os.environ["GOOGLE_SHEETS_ID"])


def _get_shared_authority_worksheet(*, create=False):
    title = _shared_authority_tab_name()
    if not title:
        raise NeoSektorSharedAuthorityError(
            "Configure the dedicated NeoSektor shared authority tab first."
        )
    spreadsheet = _get_spreadsheet()
    try:
        return spreadsheet.worksheet(title)
    except Exception as error:
        if not create or not _is_missing_worksheet_error(error):
            raise
    try:
        return spreadsheet.add_worksheet(title=title, rows=4, cols=5)
    except Exception as error:
        _log_safe_warning("shared authority tab creation", error)
        raise NeoSektorSharedAuthorityError(
            "NeoSektor could not create its shared authority tab."
        ) from error


def _shared_authority_tab_name():
    return str(os.environ.get(NEOSEKTOR_SHARED_AUTHORITY_TAB_ENV) or "").strip()


def _is_missing_worksheet_error(error):
    return error.__class__.__name__ in {"WorksheetNotFound", "APIError"} and (
        error.__class__.__name__ == "WorksheetNotFound"
        or "not found" in str(error).lower()
    )


def _legacy_shared_authority_status(source):
    return _shared_authority_status(
        authority=LEGACY_UNMANAGED_AUTHORITY,
        generation=0,
        changed_at=None,
        changed_by=None,
        metadata={},
        record_present=False,
        source=source,
    )


def _shared_authority_status(
    *,
    authority,
    generation,
    changed_at,
    changed_by,
    metadata,
    record_present,
    source,
):
    authority = _normalized_shared_authority(authority)
    enforced = bool(record_present)
    pending_neo_return = (
        enforced
        and authority == NEO_PRIMARY_AUTHORITY
        and isinstance((metadata or {}).get("return_control"), dict)
        and (metadata or {})["return_control"].get("verified") is not True
    )
    return {
        "authority": authority,
        "authority_label": NEOSEKTOR_SHARED_AUTHORITY_LABELS[authority],
        "generation": generation,
        "changed_at": changed_at,
        "changed_by": changed_by,
        "metadata": dict(metadata or {}),
        "record_present": enforced,
        "enforced": enforced,
        # Legacy compatibility intentionally leaves both writers available
        # until the shared record is provisioned by both deployments.
        "can_neo_write": (
            authority != STANDALONE_PRIMARY_AUTHORITY and not pending_neo_return
        ),
        "can_standalone_write": authority != NEO_PRIMARY_AUTHORITY,
        "available": True,
        "source": source,
        "control_tab": _shared_authority_tab_name() or None,
    }


def _normalized_shared_authority_record(values):
    authority, generation, changed_at, changed_by, metadata = values
    if all(value is None or str(value).strip() == "" for value in values):
        return _legacy_shared_authority_status("tab_empty")
    normalized_authority = _normalized_shared_authority(authority, strict=True)
    try:
        normalized_generation = int(str(generation).strip())
    except (TypeError, ValueError) as error:
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority generation is invalid."
        ) from error
    if normalized_generation < 1:
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority generation is invalid."
        )
    changed_at = str(changed_at or "").strip()
    if not changed_at:
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority timestamp is missing."
        )
    return _shared_authority_status(
        authority=normalized_authority,
        generation=normalized_generation,
        changed_at=changed_at,
        changed_by=_normalized_authority_actor(changed_by),
        metadata=_normalized_authority_metadata(metadata),
        record_present=True,
        source="shared_record",
    )


def _normalized_shared_authority(value, *, strict=False):
    normalized = str(value or "").strip().lower()
    if normalized in NEOSEKTOR_SHARED_AUTHORITIES:
        return normalized
    if normalized == LEGACY_UNMANAGED_AUTHORITY:
        return normalized
    if strict:
        raise NeoSektorSharedAuthorityError("Choose a valid NeoSektor authority.")
    return LEGACY_UNMANAGED_AUTHORITY


def _normalized_authority_actor(value):
    actor = str(value or "").strip()
    if len(actor) > 160:
        raise NeoSektorSharedAuthorityError("NeoSektor authority actor is too long.")
    return actor or None


def _normalized_authority_metadata(value):
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        metadata = value
    else:
        try:
            metadata = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise NeoSektorSharedAuthorityError(
                "NeoSektor shared authority metadata is invalid."
            ) from error
    if not isinstance(metadata, dict):
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority metadata is invalid."
        )
    serialized = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    if len(serialized) > 2000:
        raise NeoSektorSharedAuthorityError(
            "NeoSektor shared authority metadata is too large."
        )
    return metadata


def _authority_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _write_shared_authority_headers(worksheet):
    if hasattr(worksheet, "batch_update"):
        worksheet.batch_update(
            [{"range": "A1:E1", "values": [list(NEOSEKTOR_SHARED_AUTHORITY_HEADERS)]}]
        )
        return
    for cell, value in zip(
        ("A1", "B1", "C1", "D1", "E1"),
        NEOSEKTOR_SHARED_AUTHORITY_HEADERS,
    ):
        worksheet.update_acell(cell, value)


def _write_shared_authority_record(worksheet, status):
    values = (
        status["authority"],
        status["generation"],
        status["changed_at"],
        status["changed_by"] or "",
        json.dumps(status["metadata"], sort_keys=True, separators=(",", ":")),
    )
    if hasattr(worksheet, "batch_update"):
        worksheet.batch_update(
            [{"range": "A2:E2", "values": [list(values)]}]
        )
        return
    for cell, value in zip(NEOSEKTOR_SHARED_AUTHORITY_CELLS, values):
        worksheet.update_acell(cell, value)


def _write_shared_authority_metadata(worksheet, metadata):
    value = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    if hasattr(worksheet, "batch_update"):
        worksheet.batch_update([{"range": "E2", "values": [[value]]}])
        return
    worksheet.update_acell("E2", value)


def _cached_shared_authority(gateway_id):
    now = time.monotonic()
    with _shared_authority_cache_lock:
        cached = _shared_authority_cache.get(gateway_id)
        if (
            not cached
            or now - cached["stored_at"] >= SHARED_AUTHORITY_CACHE_SECONDS
        ):
            return None
        return dict(cached["status"])


def _store_shared_authority(gateway_id, status):
    with _shared_authority_cache_lock:
        _shared_authority_cache[gateway_id] = {
            "stored_at": time.monotonic(),
            "status": dict(status),
        }


def _cached_google_values(gateway_id):
    now = time.monotonic()
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway_id)
        if not cached or now - cached["stored_at"] >= GOOGLE_TRANSIENT_CACHE_SECONDS:
            return None
        return dict(cached["values"])


def _store_google_values(gateway_id, values):
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway_id) or {}
        _google_state_cache[gateway_id] = {
            "stored_at": time.monotonic(),
            "values": dict(values),
            "wave_all_up_started_at": _updated_google_wave_timers(
                cached.get("wave_all_up_started_at"),
                values,
            ),
        }


def _merge_cached_google_values(gateway_id, updates):
    with _google_state_cache_lock:
        cached = _google_state_cache.get(gateway_id)
        values = dict(cached["values"]) if cached else {}
        values.update(updates)
        _google_state_cache[gateway_id] = {
            "stored_at": time.monotonic(),
            "values": values,
            "wave_all_up_started_at": _updated_google_wave_timers(
                (cached or {}).get("wave_all_up_started_at"),
                values,
            ),
        }


def _updated_google_wave_timers(existing, values, *, down_timer_minutes=15):
    now = datetime.utcnow()
    starts = dict(existing or {})
    first_is_all_up = _google_wave_is_all_up(values, "B2", "C2", "D2")
    second_is_all_up = _google_wave_is_all_up(values, "B3", "C3", "D3")

    if first_is_all_up:
        starts.setdefault("1ST WAVE", now)
    else:
        starts.pop("1ST WAVE", None)
        starts.pop("2ND WAVE", None)
        return starts

    first_started_at = starts.get("1ST WAVE")
    first_is_down = bool(
        first_started_at
        and now - first_started_at >= timedelta(minutes=down_timer_minutes)
    )
    if first_is_down and second_is_all_up:
        starts.setdefault("2ND WAVE", now)
    else:
        starts.pop("2ND WAVE", None)
    return starts


def _google_wave_is_all_up(values, east_count_cell, west_count_cell, left_to_arrive_cell):
    return (
        _safe_int(values.get(left_to_arrive_cell)) == 0
        and _safe_int(values.get(east_count_cell)) <= _safe_int(values.get("B4"))
        and _safe_int(values.get(west_count_cell)) <= _safe_int(values.get("C4"))
    )


def _google_primary_down_timer_minutes(settings):
    try:
        value = int(getattr(settings, "all_up_to_down_minutes", 15))
    except (TypeError, ValueError):
        value = 15
    return min(max(value, 1), 120)


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
