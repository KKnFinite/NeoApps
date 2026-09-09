"""Cheap invalidation hint; never a substitute for canonical routing revisions.

Reuse the sort's persisted update timestamp as a monotonic write epoch under
the existing Gateway lock. No schema, child-state reads or GET-side writes.
"""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import NeoSektorOperationalSetting, NeoSektorSortState


def advance_routing_signal(bundle):
    row = bundle.sort_state
    row.updated_at = max(datetime.utcnow(), (row.updated_at or datetime.min) + timedelta(microseconds=1))


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


def read_routing_signal(gateway, sort_date, sort_name):
    # The scope comes from the last canonical response. It is only a hint:
    # the full endpoint always re-resolves current operation/window/permissions.
    # Both scalar subqueries are unique-key lookups, including missing defaults.
    values = db.session.execute(select(
        select(NeoSektorSortState.updated_at).where(
            NeoSektorSortState.gateway_id == gateway.id,
            NeoSektorSortState.sort_date == sort_date,
            NeoSektorSortState.sort_name == sort_name,
        ).scalar_subquery(),
        select(NeoSektorOperationalSetting.updated_at).where(
            NeoSektorOperationalSetting.gateway_id == gateway.id,
        ).scalar_subquery(),
    )).one()
    return _signal(sort_date, sort_name, *values)


def driver_routing_watch_state_payload(gateway, **kwargs):
    from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle
    bundle = NeoSektorOperationalStateBundle.load(gateway, include_routing=True, **kwargs)
    state = bundle.driver_routing_state_payload()
    state["routing_watch"] = routing_signal_for_bundle(bundle)
    return state
