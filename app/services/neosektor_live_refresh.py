"""Compact revisions for read-only NeoSektor live reconciliation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import func, literal, or_, select, union_all

from app.extensions import db
from app.models import (
    NeoSektorBallmatCount,
    NeoErmacUldRequest,
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorSortState,
    NeoSektorUldOnTheWayEvent,
    NeoSektorWaveState,
    SortDateMission,
)
from app.services.operation_scope import current_operational_sort_operation
from app.services.neosektor_live_counts import current_neosektor_sort_date, read_neosektor_operational_settings
from app.services.neosektor_sheets_compat import (
    DEFAULT_NEOSEKTOR_INTEGRATION_MODE,
    GOOGLE_PRIMARY,
    NEOSEKTOR_INTEGRATION_MODES,
    google_primary_operational_values,
    google_primary_wave_timer_starts,
)


COUNT_STATE_SCOPE = "counts"
ROUTING_STATE_SCOPE = "routing"
DISCHARGE_STATE_SCOPE = "discharge"
_WAVE_TIMER_SOURCE_PREFIX = "wave_timer:"
NEOSEKTOR_LIVE_STATE_SCOPES = {
    COUNT_STATE_SCOPE,
    ROUTING_STATE_SCOPE,
    DISCHARGE_STATE_SCOPE,
}


def neosektor_state_revision(
    gateway,
    scope,
    *,
    sort_date=None,
    sort_name="night",
    now_utc=None,
):
    """Fingerprint visible count/routing inputs without constructing view state."""
    if scope not in {COUNT_STATE_SCOPE, ROUTING_STATE_SCOPE}:
        raise ValueError("Invalid NeoSektor live-state scope.")

    sort_name = str(sort_name or "night").strip().lower() or "night"
    sort_date = sort_date or current_neosektor_sort_date(gateway, sort_name)
    now_utc = _naive_utc(now_utc)
    settings = read_neosektor_operational_settings(gateway)
    mode = _mode_from_settings(settings)
    google_values = (
        google_primary_operational_values(gateway)
        if mode == GOOGLE_PRIMARY
        else None
    )

    sort_state_ids = select(NeoSektorSortState.id).where(
        NeoSektorSortState.gateway_id == gateway.id,
        NeoSektorSortState.sort_date == sort_date,
        NeoSektorSortState.sort_name == sort_name,
    )
    aggregate_queries = []
    timer_rows = []
    if mode == GOOGLE_PRIMARY:
        timer_starts = google_primary_wave_timer_starts(
            gateway,
            operational_settings=settings,
        )
        timer_rows = [
            SimpleNamespace(
                wave_name=wave_name,
                all_up_started_at=timer_starts.get(wave_name),
            )
            for wave_name in ("1ST WAVE", "2ND WAVE")
        ]
        if scope == ROUTING_STATE_SCOPE:
            aggregate_queries.append(_aggregate_query(
                "sort_state", NeoSektorSortState, NeoSektorSortState.id.in_(sort_state_ids)))
            aggregate_queries.append(
                _aggregate_query(
                    "driver_routes",
                    NeoSektorDriverRouteSetting,
                    NeoSektorDriverRouteSetting.sort_state_id.in_(sort_state_ids),
                )
            )
    else:
        aggregate_queries = [
            _aggregate_query(
                "ballmats", NeoSektorBallmatCount,
                NeoSektorBallmatCount.sort_state_id.in_(sort_state_ids),
            ),
            _aggregate_query(
                "sort_state",
                NeoSektorSortState,
                NeoSektorSortState.gateway_id == gateway.id,
                NeoSektorSortState.sort_date == sort_date,
                NeoSektorSortState.sort_name == sort_name,
            ),
            _aggregate_query(
                "waves",
                NeoSektorWaveState,
                NeoSektorWaveState.sort_state_id.in_(sort_state_ids),
            ),
            _aggregate_query(
                "wave_counts",
                NeoSektorBallmatWaveCount,
                NeoSektorBallmatWaveCount.sort_state_id.in_(sort_state_ids),
            ),
            _aggregate_query(
                "open_bays",
                NeoSektorOpenBayState,
                NeoSektorOpenBayState.sort_state_id.in_(sort_state_ids),
            ),
            _aggregate_query(
                "bay_statuses",
                NeoSektorBayStatus,
                NeoSektorBayStatus.sort_state_id.in_(sort_state_ids),
            ),
        ]
        if scope == ROUTING_STATE_SCOPE:
            aggregate_queries.append(
                _aggregate_query(
                    "driver_routes",
                    NeoSektorDriverRouteSetting,
                    NeoSektorDriverRouteSetting.sort_state_id.in_(sort_state_ids),
                )
            )
        # Carry timer starts in the same round trip as the aggregate inputs.
        # These tagged rows are unpacked below, not added to the fingerprint's
        # aggregate list, so the revision and timer-phase semantics stay exact.
        aggregate_queries.append(
            select(
                (literal(_WAVE_TIMER_SOURCE_PREFIX) + NeoSektorWaveState.wave_name).label("source"),
                literal(0).label("row_count"),
                literal(0).label("max_id"),
                literal(0).label("id_sum"),
                literal(0).label("state_value"),
                NeoSektorWaveState.all_up_started_at.label("latest_updated_at"),
            ).where(NeoSektorWaveState.sort_state_id.in_(sort_state_ids))
        )

    if scope == ROUTING_STATE_SCOPE:
        # Match both canonical arrival gates used by Driver Routing.
        # Fold it into the existing UNION so Block-In invalidation needs no
        # separate mission read (the operation resolver is request-cached).
        operation = current_operational_sort_operation(gateway)
        operation_id = (operation.id if operation and operation.sort_date == sort_date
                        and operation.sort_name.lower() == sort_name else -1)
        for label, aliases in (("first", ("1", "1st Wave")), ("second", ("2", "2nd Wave"))):
            aggregate_queries.append(_aggregate_query(
                label + "_wave_arrivals", SortDateMission,
                SortDateMission.sort_date_operation_id == operation_id,
                SortDateMission.mission_type == "arrival",
                SortDateMission.wave.in_(aliases),
                SortDateMission.actual_block_in_datetime_utc.isnot(None),
            ))

    aggregate_rows = (
        sorted(
            db.session.execute(
                aggregate_queries[0]
                if len(aggregate_queries) == 1
                else union_all(*aggregate_queries)
            ).all(),
            key=lambda row: row.source,
        )
        if aggregate_queries
        else []
    )

    if mode != GOOGLE_PRIMARY:
        timer_rows = [
            SimpleNamespace(
                wave_name=row.source.removeprefix(_WAVE_TIMER_SOURCE_PREFIX),
                all_up_started_at=row.latest_updated_at,
            )
            for row in aggregate_rows
            if row.source.startswith(_WAVE_TIMER_SOURCE_PREFIX)
        ]
        aggregate_rows = [
            row for row in aggregate_rows
            if not row.source.startswith(_WAVE_TIMER_SOURCE_PREFIX)
        ]

    return _digest(
        {
            "gateway_id": gateway.id,
            "scope": scope,
            "sort_date": sort_date.isoformat(),
            "sort_name": sort_name,
            "mode": mode,
            "google_values": google_values,
            "settings": _settings_revision_values(settings),
            "timer_phases": _timer_phase_tokens(timer_rows, settings, now_utc),
            "inputs": [_aggregate_values(row) for row in aggregate_rows],
        }
    )


def neosektor_discharge_revision(
    gateway,
    *,
    operation_id=None,
    now_utc=None,
):
    """Fingerprint active ULD workflow rows without loading the queue."""
    now_utc = _naive_utc(now_utc)
    operation_filter = (
        NeoErmacUldRequest.sort_date_operation_id == operation_id
        if operation_id is not None
        else NeoErmacUldRequest.sort_date_operation_id.is_(None)
    )
    event_operation_filter = (
        NeoSektorUldOnTheWayEvent.sort_date_operation_id == operation_id
        if operation_id is not None
        else NeoSektorUldOnTheWayEvent.sort_date_operation_id.is_(None)
    )
    active_request = or_(
        NeoErmacUldRequest.a2_count > 0,
        NeoErmacUldRequest.a1_count > 0,
        NeoErmacUldRequest.amp_count > 0,
    )
    rows = sorted(
        db.session.execute(
            union_all(
                select(
                    literal("requests").label("source"),
                    func.count(NeoErmacUldRequest.id).label("row_count"),
                    func.max(NeoErmacUldRequest.id).label("max_id"),
                    func.coalesce(func.sum(NeoErmacUldRequest.id), 0).label(
                        "id_sum"
                    ),
                    func.coalesce(
                        func.sum(
                            NeoErmacUldRequest.a2_count
                            + NeoErmacUldRequest.a1_count
                            + NeoErmacUldRequest.amp_count
                        ),
                        0,
                    ).label("state_value"),
                    func.max(NeoErmacUldRequest.updated_at).label(
                        "latest_updated_at"
                    ),
                ).where(
                    NeoErmacUldRequest.gateway_id == gateway.id,
                    operation_filter,
                    active_request,
                ),
                select(
                    literal("on_the_way").label("source"),
                    func.count(NeoSektorUldOnTheWayEvent.id).label("row_count"),
                    func.max(NeoSektorUldOnTheWayEvent.id).label("max_id"),
                    func.coalesce(func.sum(NeoSektorUldOnTheWayEvent.id), 0).label(
                        "id_sum"
                    ),
                    func.coalesce(
                        func.sum(NeoSektorUldOnTheWayEvent.quantity), 0
                    ).label("state_value"),
                    func.max(NeoSektorUldOnTheWayEvent.expires_at_utc).label(
                        "latest_updated_at"
                    ),
                ).where(
                    NeoSektorUldOnTheWayEvent.gateway_id == gateway.id,
                    event_operation_filter,
                    NeoSektorUldOnTheWayEvent.expires_at_utc > now_utc,
                ),
            )
        ).all(),
        key=lambda row: row.source,
    )
    return _digest(
        {
            "gateway_id": gateway.id,
            "operation_id": operation_id,
            "inputs": [_aggregate_values(row) for row in rows],
        }
    )


def _aggregate_query(source, model, *criteria):
    return select(
        literal(source).label("source"),
        func.count(model.id).label("row_count"),
        func.max(model.id).label("max_id"),
        func.coalesce(func.sum(model.id), 0).label("id_sum"),
        literal(0).label("state_value"),
        func.max(model.updated_at).label("latest_updated_at"),
    ).where(*criteria)


def _aggregate_values(row):
    return {
        "source": row.source,
        "row_count": int(row.row_count or 0),
        "max_id": int(row.max_id or 0),
        "id_sum": int(row.id_sum or 0),
        "state_value": int(row.state_value or 0),
        "latest_updated_at": _value_token(row.latest_updated_at),
    }


def _settings_revision_values(settings):
    if not settings:
        return {
            "first_modifier": 45,
            "second_modifier": 37,
            "down_timer_minutes": 15,
            "updated_at": None,
        }
    return {
        "first_modifier": settings.first_wave_unload_modifier,
        "second_modifier": settings.second_wave_unload_modifier,
        "down_timer_minutes": settings.all_up_to_down_minutes,
        "updated_at": _value_token(settings.updated_at),
    }


def _timer_phase_tokens(rows, settings, now_utc):
    delay = timedelta(
        minutes=max(int(getattr(settings, "all_up_to_down_minutes", 15) or 15), 1)
    )
    return [
        {
            "wave": row.wave_name,
            "started_at": _value_token(row.all_up_started_at),
            "phase": (
                "down"
                if row.all_up_started_at and now_utc >= row.all_up_started_at + delay
                else "all_up" if row.all_up_started_at else "idle"
            ),
        }
        for row in sorted(rows, key=lambda row: row.wave_name)
    ]


def _mode_from_settings(settings):
    value = str(getattr(settings, "integration_mode", "") or "").strip().lower()
    return value if value in NEOSEKTOR_INTEGRATION_MODES else DEFAULT_NEOSEKTOR_INTEGRATION_MODE


def _naive_utc(value):
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_value_token,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _value_token(value):
    return value.isoformat() if hasattr(value, "isoformat") else value
