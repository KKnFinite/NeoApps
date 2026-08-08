"""Database-backed coordination for future Google MotherBrain live polling.

This module intentionally does not schedule polls or call Google.  It only
coordinates which worker may perform a future poll for one operation scope.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import MotherBrainGoogleLivePollState
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_enabled,
)


GOOGLE_LIVE_POLL_INTERVAL = timedelta(minutes=1)
GOOGLE_LIVE_POLL_LEASE_DURATION = timedelta(seconds=30)


@dataclass(frozen=True)
class GoogleLivePollLease:
    state_id: int
    token: str


@dataclass(frozen=True)
class GoogleLivePollLeaseResult:
    status: str
    lease: GoogleLivePollLease | None = None

    @property
    def acquired(self):
        return self.status == "acquired"


def acquire_google_motherbrain_live_poll_lease(operation, now=None):
    """Acquire the one-minute polling lease for an operation when it is due.

    A conditional UPDATE is executed through the active session connection so
    SQLite tests do not open a competing connection and production workers use
    the same durable Neon state.
    """
    gateway = _operation_gateway(operation)
    if not google_motherbrain_live_polling_enabled(gateway, operation.sort_name):
        return GoogleLivePollLeaseResult("disabled")

    now_utc = _utc_naive(now)
    state = _find_or_create_state(operation)
    if _lease_is_valid(state, now_utc):
        return GoogleLivePollLeaseResult("in_progress")
    if not _attempt_is_due(state, now_utc):
        return GoogleLivePollLeaseResult("not_due")

    token = secrets.token_urlsafe(32)
    due_before = now_utc - GOOGLE_LIVE_POLL_INTERVAL
    result = db.session.connection().execute(
        update(MotherBrainGoogleLivePollState)
        .where(
            MotherBrainGoogleLivePollState.id == state.id,
            or_(
                MotherBrainGoogleLivePollState.lease_expires_at_utc.is_(None),
                MotherBrainGoogleLivePollState.lease_expires_at_utc <= now_utc,
                MotherBrainGoogleLivePollState.lease_token == "",
            ),
            or_(
                MotherBrainGoogleLivePollState.last_attempt_at_utc.is_(None),
                MotherBrainGoogleLivePollState.last_attempt_at_utc <= due_before,
            ),
        )
        .values(
            last_attempt_at_utc=now_utc,
            lease_expires_at_utc=now_utc + GOOGLE_LIVE_POLL_LEASE_DURATION,
            lease_token=token,
            updated_at=now_utc,
        )
    )
    db.session.commit()
    db.session.expire_all()

    if result.rowcount:
        return GoogleLivePollLeaseResult(
            "acquired",
            GoogleLivePollLease(state_id=state.id, token=token),
        )

    state = db.session.get(MotherBrainGoogleLivePollState, state.id)
    if state and _lease_is_valid(state, now_utc):
        return GoogleLivePollLeaseResult("in_progress")
    return GoogleLivePollLeaseResult("not_due")


def complete_google_motherbrain_live_poll_success(lease, now=None):
    """Record a successful future poll and release only the owning lease."""
    return _complete_lease(
        lease,
        now=now,
        last_success_at_utc=_utc_naive(now),
        last_error=None,
    )


def complete_google_motherbrain_live_poll_failure(lease, error, now=None):
    """Record a minimal failure reason and leave the normal interval intact."""
    return _complete_lease(
        lease,
        now=now,
        last_success_at_utc=None,
        last_error=_minimal_error(error),
    )


def _complete_lease(lease, now=None, last_success_at_utc=None, last_error=None):
    if not isinstance(lease, GoogleLivePollLease):
        raise TypeError("A GoogleLivePollLease is required to complete a poll.")

    completed_at_utc = _utc_naive(now)
    values = {
        "lease_expires_at_utc": None,
        "lease_token": "",
        "updated_at": completed_at_utc,
    }
    if last_success_at_utc is not None:
        values["last_success_at_utc"] = last_success_at_utc
        values["last_error"] = None
    else:
        values["last_error"] = last_error

    result = db.session.connection().execute(
        update(MotherBrainGoogleLivePollState)
        .where(
            MotherBrainGoogleLivePollState.id == lease.state_id,
            MotherBrainGoogleLivePollState.lease_token == lease.token,
        )
        .values(**values)
    )
    db.session.commit()
    db.session.expire_all()
    return bool(result.rowcount)


def _find_or_create_state(operation):
    scope = _operation_scope(operation)
    state = MotherBrainGoogleLivePollState.query.filter_by(**scope).first()
    if state:
        return state

    state = MotherBrainGoogleLivePollState(**scope)
    db.session.add(state)
    try:
        db.session.flush()
    except IntegrityError:
        # Another worker inserted the scope first. Reuse its durable row before
        # applying the conditional lease update below.
        db.session.rollback()
        state = MotherBrainGoogleLivePollState.query.filter_by(**scope).one()
    return state


def _operation_scope(operation):
    gateway = _operation_gateway(operation)
    return {
        "gateway_id": gateway.id,
        "sort_name": str(operation.sort_name or "").strip().lower(),
        "sort_date": operation.sort_date,
    }


def _operation_gateway(operation):
    if not operation:
        raise ValueError("A sort-date operation is required for a Google poll lease.")
    gateway = getattr(operation, "gateway", None)
    if not gateway or not gateway.id:
        raise ValueError("Google poll leases require an operation with a gateway.")
    return gateway


def _attempt_is_due(state, now_utc):
    if not state.last_attempt_at_utc:
        return True
    return state.last_attempt_at_utc <= now_utc - GOOGLE_LIVE_POLL_INTERVAL


def _lease_is_valid(state, now_utc):
    return bool(
        state.lease_token
        and state.lease_expires_at_utc
        and state.lease_expires_at_utc > now_utc
    )


def _minimal_error(error):
    if isinstance(error, BaseException):
        return error.__class__.__name__
    return "poll failed"


def _utc_naive(value=None):
    if value is None:
        return datetime.utcnow()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
