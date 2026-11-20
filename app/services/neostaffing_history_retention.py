"""Calendar-based retention for the new history tables only."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from flask import current_app
from sqlalchemy import delete, exists, func, or_, select, text, tuple_, update

from app.extensions import db
from app.models import (
    Gateway, StaffingAttendanceOutcome as Outcome, StaffingRosterClose as Close,
    StaffingRosterPlacement as Placement, StaffingRosterRevision as Revision,
)
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services.neostaffing_history_schema import ROSTER_HISTORY_LOCK


HISTORY_RETENTION_MONTHS = 9
HISTORY_PURGE_BATCH = 250
HISTORY_PURGE_LOCK = 7_483_327_341_923


def retention_cutoff(as_of):
    """Keep the cutoff Sort date, clamping month-end and leap days."""
    years, month = divmod(as_of.month - 1 - HISTORY_RETENTION_MONTHS, 12)
    year = as_of.year + years
    month += 1
    return date(year, month, min(as_of.day, monthrange(year, month)[1]))


def history_cutoff(*, at=None):
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return retention_cutoff(current_gateway_local_datetime(now=at).date())


def purge_enabled():
    return (current_app.config.get("NEOSTAFFING_HISTORY_ENABLED") is True
            and current_app.config.get("NEOSTAFFING_HISTORY_PURGE_ENABLED") is True)


@dataclass(frozen=True)
class PurgeResult:
    outcomes: int = 0
    placements: int = 0
    closes: int = 0
    revisions: int = 0


def _try_lock(key):
    return (db.session.get_bind().dialect.name != "postgresql" or bool(db.session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key},
    )))


def _compact_roster_revisions(cutoff, batch_size):
    # This floor is earlier than the first retained local day in any gateway
    # timezone. Keep older unresolved windows if they straddle that safe floor.
    before = datetime.combine(cutoff - timedelta(days=1), time.min)
    oldest_open = db.session.scalar(select(func.min(Close.closes_at)).where(
        Close.sort_date >= cutoff, Close.sealed_at.is_(None), Close.unavailable_at.is_(None),
    ))
    if oldest_open is not None:
        before = min(before, oldest_open)
    if not _try_lock(ROSTER_HISTORY_LOCK):
        return 0
    late_changes = select(Revision.change_set_id).where(
        Revision.changed_at >= before, Revision.change_set_id.is_not(None),
    )
    ranked = select(
        Revision.id, Revision.changed_at,
        func.row_number().over(partition_by=(Revision.kind, Revision.entity_id),
            order_by=(Revision.changed_at.desc(), Revision.id.desc())).label("rank"),
    ).where(Revision.kind != "baseline", Revision.changed_at < before,
            or_(Revision.change_set_id.is_(None), Revision.change_set_id.not_in(late_changes))).subquery()
    # Retain a predecessor (including source-removal tombstones) for every
    # entity and every revision in a transaction that crosses the floor.
    victims = select(ranked.c.id).where(ranked.c.rank > 1).order_by(ranked.c.changed_at, ranked.c.id).limit(batch_size)
    return db.session.execute(delete(Revision.__table__).where(Revision.id.in_(victims))).rowcount


def purge_history_batch(gateway_code, cutoff, at, *, batch_size=HISTORY_PURGE_BATCH):
    """One deterministic batch in the caller's transaction. All limits are row limits."""
    if not purge_enabled() or not _try_lock(HISTORY_PURGE_LOCK):
        return PurgeResult()
    batch_size = max(1, min(int(batch_size), HISTORY_PURGE_BATCH))
    expired = (Close.gateway_code == gateway_code, Close.sort_date < cutoff, Close.closes_at <= at)
    mark_keys = select(Outcome.sort_date_operation_id, Outcome.person_id).where(
        Outcome.gateway_code == gateway_code, Outcome.sort_date < cutoff,
    ).order_by(Outcome.sort_date, Outcome.sort_date_operation_id, Outcome.person_id).limit(batch_size)
    outcomes = db.session.execute(delete(Outcome.__table__).where(
        tuple_(Outcome.sort_date_operation_id, Outcome.person_id).in_(mark_keys),
    )).rowcount

    place_keys = db.session.execute(select(
        Placement.sort_date_operation_id, Placement.person_id, Placement.work_area_id,
    ).join(
        Close, Close.sort_date_operation_id == Placement.sort_date_operation_id,
    ).where(*expired).order_by(Close.sort_date, Placement.sort_date_operation_id,
                              Placement.person_id, Placement.work_area_id).limit(batch_size)).all()
    placements = 0
    if place_keys:
        # Withdraw a manifest in the same transaction as its first deletion.
        # Even a reader that started before midnight cannot see a partial roster as complete.
        db.session.execute(update(Close.__table__).where(
            Close.sort_date_operation_id.in_({row.sort_date_operation_id for row in place_keys}),
            Close.sealed_at.is_not(None),
        ).values(sealed_at=None, unavailable_at=at))
        placements = db.session.execute(delete(Placement.__table__).where(
            tuple_(Placement.sort_date_operation_id, Placement.person_id, Placement.work_area_id).in_(place_keys),
        )).rowcount

    empty_closes = select(Close.sort_date_operation_id).where(
        *expired, ~exists().where(Placement.sort_date_operation_id == Close.sort_date_operation_id),
    ).order_by(Close.sort_date, Close.sort_date_operation_id).limit(batch_size)
    closes = db.session.execute(delete(Close.__table__).where(Close.sort_date_operation_id.in_(empty_closes))).rowcount
    revisions = _compact_roster_revisions(cutoff, batch_size)
    return PurgeResult(outcomes, placements, closes, revisions)


def purge_expired_history(gateway_id, *, at=None):
    """Worker entry point. Retry contended batches on the next safety tick."""
    if not purge_enabled():
        return PurgeResult()
    gateway = db.session.get(Gateway, gateway_id)
    if not gateway or gateway.code != current_app.config.get("DEFAULT_GATEWAY_CODE", "RFD").upper():
        return PurgeResult()
    at = at or datetime.now(timezone.utc)
    cutoff = history_cutoff(at=at)
    utc_at = at.astimezone(timezone.utc).replace(tzinfo=None) if at.tzinfo else at
    result = purge_history_batch(gateway.code, cutoff, utc_at)
    db.session.commit()
    return result
