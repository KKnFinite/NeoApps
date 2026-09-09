"""Cheap invalidation hint; never a substitute for canonical routing revisions.

Reuse the sort's persisted update timestamp as a monotonic write epoch under
the existing Gateway lock. No schema, child-state reads or GET-side writes.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import NeoSektorOperationalSetting, NeoSektorSortState
from app.services.request_cache import MISSING, get_request_cached


ROUTING_SIGNAL_NAMESPACE = "neosektor.routing_signal_timestamps"


def routing_signal_scope(args):
    sort_date = date.fromisoformat(args.get("sort_date", ""))
    sort_name = args.get("sort_name", "").strip().lower()
    if not sort_name or len(sort_name) > 32:
        raise ValueError("Invalid routing scope.")
    return sort_date, sort_name


def advance_routing_signal(bundle, *, previous_updated_at=None):
    row = bundle.sort_state
    # A locked spotter mutation may already have flushed its sort rollup and
    # onupdate timestamp. That committed epoch will invalidate the same signal;
    # do not issue a second timestamp-only UPDATE in the same transaction.
    if (previous_updated_at is not None and row.updated_at is not None
            and row.updated_at > previous_updated_at):
        return
    baseline = max(row.updated_at or datetime.min, previous_updated_at or datetime.min)
    row.updated_at = max(datetime.utcnow(), baseline + timedelta(microseconds=1))


def _signal(sort_date, sort_name, sort_updated, settings_updated):
    return {
        "sort_date": sort_date.isoformat(), "sort_name": sort_name.lower(),
        "version": "|".join(value.isoformat(timespec="microseconds") if value else "-"
                            for value in (sort_updated, settings_updated)),
    }


def routing_signal_for_bundle(bundle):
    row = bundle.routing_sort_state or bundle.sort_state
    return _signal(bundle.sort_date, bundle.sort_name, getattr(row, "updated_at", None),
                   getattr(bundle.operational_settings, "updated_at", None))


def routing_signal_columns(gateway_id, sort_date, sort_name):
    """Unique-key scalar lookups, also usable in the authorized Gateway query."""
    return (
        select(NeoSektorSortState.updated_at).where(
            NeoSektorSortState.gateway_id == gateway_id,
            NeoSektorSortState.sort_date == sort_date,
            NeoSektorSortState.sort_name == sort_name,
        ).scalar_subquery(),
        select(NeoSektorOperationalSetting.updated_at).where(
            NeoSektorOperationalSetting.gateway_id == gateway_id,
        ).scalar_subquery(),
    )


def read_routing_signal(gateway, sort_date, sort_name):
    # The scope comes from the last canonical response. It is only a hint:
    # the full endpoint always re-resolves current operation/window/permissions.
    values = get_request_cached(ROUTING_SIGNAL_NAMESPACE, (gateway.id, sort_date, sort_name))
    if values is MISSING:
        values = db.session.execute(select(*routing_signal_columns(gateway.id, sort_date, sort_name))).one()
    return _signal(sort_date, sort_name, *values)


def driver_routing_watch_state_payload(gateway, **kwargs):
    from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle
    bundle = NeoSektorOperationalStateBundle.load(gateway, include_routing=True, **kwargs)
    state = bundle.driver_routing_state_payload()
    state["routing_watch"] = routing_signal_for_bundle(bundle)
    return state
