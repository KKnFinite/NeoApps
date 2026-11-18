"""Phase 2 request/candidate primitives and shared transaction boundaries.

After Phase 5 cutover, candidate staging/decisions are closed. Live entitlement
changes use scoped_access_management with the same history and atomic wrapper.
"""
from datetime import datetime
from hashlib import sha256
from functools import wraps
import json
from uuid import UUID

from flask_login import current_user
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountScopedRole, AccountSecurityEvent, Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.models.user import ROLE_LEVELS
from app.services.account_authority import APP_CODES, Scope, read_authority
from app.services.account_security import lock_administration
from app.services.request_cache import clear_request_cache


class AccountConflict(ValueError):
    """Caller must roll back the entire transaction and return a conflict."""


class FoundationNotActivated(ValueError):
    pass


def atomic_account_change(function):
    """The caller commits success; every failed account action rolls back fully."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except IntegrityError as error:
            db.session.rollback()
            raise AccountConflict("Concurrent or invalid account state. Reload before retrying.") from error
        except Exception:
            db.session.rollback()
            raise
        finally:
            clear_request_cache()
    return wrapped


def fulfill_access_request(*_args, **_kwargs):
    raise FoundationNotActivated("Grant fulfillment is disabled pending reviewed inventory and scope mapping.")


def _text(value, label, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"A valid {label} is required (maximum {limit} characters).")
    return value.strip()


def _version(actual, expected):
    if type(expected) is not int or expected != actual:
        raise AccountConflict("Account state changed. Reload before retrying.")


def _actor(expected_session):
    if not current_user.is_authenticated:
        raise ValueError("Authentication required.")
    actor = lock_administration()
    if not NeoNode.query.filter_by(code="motherbrain").first():
        raise FoundationNotActivated("Existing administration serialization scope is unavailable.")
    _version(actor.auth_session_version, expected_session)
    return actor


def _target(user_id, expected_session):
    target = User.query.filter_by(id=user_id).populate_existing().with_for_update().one_or_none()
    if target is None:
        raise ValueError("Unknown account.")
    _version(target.auth_session_version, expected_session)
    return target


def _receipt(actor, target, action, before, after, reason):
    event = AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=target.id,
                                 action=action, details={"before": before, "after": after, "reason": reason,
                                                         "effective_access_changed": False})
    db.session.add(event)
    db.session.flush()
    clear_request_cache()
    return event


def entitlement_fingerprint(user_id, app_code):
    """All persisted entitlement fields, including older writers' changes.

    No version column which legacy writers could silently fail to increment.
    The digest is a conflict token, never an authorization credential.
    """
    with db.session.no_autoflush:
        table = PortalAppAccess.__table__
        row = db.session.execute(table.select().where(table.c.user_id == user_id, table.c.app_code == app_code)).mappings().first()
    return sha256(json.dumps(dict(row) if row else None, sort_keys=True, default=str).encode()).hexdigest()


def _stored_scope_rank(target, scope):
    # Inactive higher-tier grants remain protected from ordinary delegation.
    # They must not become editable merely because an entitlement was revoked.
    if scope.kind == "neoapps":
        return ROLE_LEVELS.get(target.role, 0)
    if scope.kind == "application":
        row = db.session.query(PortalAppAccess.role).filter_by(user_id=target.id, app_code=scope.app_code).first()
    elif scope.kind == "node":
        row = db.session.query(GatewayNodeRole.role).join(GatewayMembership).filter(
            GatewayMembership.user_id == target.id, GatewayMembership.gateway_id == scope.gateway_id,
            GatewayNodeRole.node_id == scope.node_id).first()
    else:
        row = None  # No legacy gateway rank exists; never infer one.
    return ROLE_LEVELS.get(row.role, 0) if row else 0


@atomic_account_change
def request_access(app_code, reason, request_key, *, expected_session):
    if not isinstance(app_code, str) or app_code not in APP_CODES:
        raise ValueError("Unknown application.")
    # Existing catalog availability is respected; no NeoBid launch/cleanup here.
    if app_code == "neobid":
        raise FoundationNotActivated("New NeoBid requests are not available in the foundation.")
    reason = _text(reason, "access reason")
    try:
        key = str(UUID(request_key))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid request identifier is required.") from None
    actor = _actor(expected_session)
    prior = AccountAccessRequest.query.filter_by(user_id=actor.id, request_key=key).one_or_none()
    if prior:
        if prior.app_code != app_code or prior.reason != reason:
            raise AccountConflict("Request identifier already used for different content.")
        return prior
    if AccountAccessRequest.query.filter_by(user_id=actor.id, app_code=app_code, state="pending").first():
        raise AccountConflict("An open request already exists for this application.")
    # A legacy pending row is not automatically an attempt, and must not be
    # duplicated before the compatibility bridge has been reviewed.
    live = PortalAppAccess.query.filter_by(user_id=actor.id, app_code=app_code).one_or_none()
    if live and (live.status == "pending" or live.status == "approved" and live.is_active):
        raise AccountConflict("Existing access or a legacy pending request must be reviewed first.")
    attempt = AccountAccessRequest(user_id=actor.id, app_code=app_code, request_key=key, reason=reason)
    db.session.add(attempt)
    db.session.flush()
    _receipt(actor, actor, "access_request_created", None,
             {"request_id": attempt.id, "app_code": app_code, "state": "pending", "version": 1}, reason)
    return attempt


@atomic_account_change
def decide_access(request_id, outcome, reason, *, expected_version, expected_session,
                  expected_target_session, expected_entitlement):
    from app.services.account_scope_runtime import scoped_active
    if scoped_active():
        raise FoundationNotActivated('Use the live scoped application decision service after cutover.')
    if not isinstance(outcome, str) or outcome not in {"approved", "denied", "withdrawn"}:
        raise ValueError("Invalid access decision.")
    reason = _text(reason, "decision reason")
    actor = _actor(expected_session)
    # Find immutable identity first, then lock account before its request row.
    identity = db.session.query(AccountAccessRequest.user_id).filter_by(id=request_id).one_or_none()
    if not identity:
        raise ValueError("Unknown access request.")
    target = _target(identity.user_id, expected_target_session)
    attempt = AccountAccessRequest.query.filter_by(id=request_id).populate_existing().with_for_update().one()
    _version(attempt.version, expected_version)
    if attempt.state != "pending":
        raise AccountConflict("This request already has a decision.")
    authority = read_authority(actor.id)
    if outcome == "withdrawn":
        if actor.id != target.id:
            raise ValueError("Only the applicant may withdraw this request.")
    elif actor.id == target.id or not authority.can_administer(Scope("application", attempt.app_code)):
        raise ValueError("Application account-administration authority is required; self-approval is prohibited.")
    if outcome == "approved" and (not target.is_active or read_authority(target.id).restricted):
        raise ValueError("Inactive or restricted accounts cannot receive an approval through this workflow.")
    if target.role == "grandmaster" and authority.role(Scope("neoapps")) != "grandmaster":
        raise ValueError("A scoped administrator cannot change a protected administrator's account requests.")
    # Lock any existing entitlement without changing it. All legacy account
    # writers take the same administration anchor. CAS covers older writers too.
    PortalAppAccess.query.filter_by(user_id=target.id, app_code=attempt.app_code).with_for_update().all()
    if expected_entitlement != entitlement_fingerprint(target.id, attempt.app_code):
        raise AccountConflict("Application entitlement changed. Review the current access first.")
    before = {"request_id": attempt.id, "app_code": attempt.app_code, "state": attempt.state, "version": attempt.version}
    result = db.session.execute(update(AccountAccessRequest).where(
        AccountAccessRequest.id == attempt.id, AccountAccessRequest.version == expected_version,
        AccountAccessRequest.state == "pending").values(state=outcome, version=expected_version + 1),
        execution_options={"synchronize_session": False})
    if result.rowcount != 1:
        raise AccountConflict("Concurrent request decision.")
    event = _receipt(actor, target, "access_request_decided", before,
                     {**before, "state": outcome, "version": expected_version + 1,
                      "fulfillment": "not_activated" if outcome == "approved" else "not_applicable",
                      "entitlement_fingerprint": expected_entitlement}, reason)
    decision = AccountAccessDecision(request_id=attempt.id, outcome=outcome, reason=reason, security_event_id=event.id)
    db.session.add(decision)
    db.session.flush()
    db.session.expire(attempt)
    return decision


@atomic_account_change
def stage_role(user_id, scope, role, active, reason, *, expected_version, expected_session, expected_target_session):
    """Stage a reviewed candidate grant; never changes any runtime entitlement."""
    from app.services.account_scope_runtime import scoped_active
    if scoped_active():
        raise FoundationNotActivated('Candidate staging is closed after the reviewed cutover. Use the scoped role service.')
    if not isinstance(scope, Scope) or not isinstance(role, str) or role not in ROLE_LEVELS or type(active) is not bool:
        raise ValueError("Invalid role staging request.")
    reason = _text(reason, "staging reason")
    actor = _actor(expected_session)
    target = _target(user_id, expected_target_session)
    authority = read_authority(actor.id)
    if not authority.can_administer(scope):
        raise ValueError("No administrative authority in this scope.")
    if scope.kind in {"gateway", "node"} and not Gateway.query.filter_by(id=scope.gateway_id, is_active=True).first():
        raise ValueError("Unknown or inactive gateway.")
    if scope.kind == "node" and not NeoNode.query.filter_by(id=scope.node_id, is_active=True).first():
        raise ValueError("Unknown or inactive node.")
    prior = AccountScopedRole.query.filter_by(user_id=target.id, scope_key=scope.key).with_for_update().one_or_none()
    _version(prior.version if prior else 0, expected_version)
    prior_rank = ROLE_LEVELS.get(prior.role, 0) if prior else 0
    current_target = read_authority(target.id)
    if current_target.restricted or active and not target.is_active:
        raise ValueError("Restricted or inactive accounts require a separate reviewed procedure.")
    # Check both staged AND effective target rank, including inactive tombstones.
    prior_rank = max(prior_rank, _stored_scope_rank(target, scope))
    actor_rank = ROLE_LEVELS.get(authority.role(scope), 0)
    platform_parent = authority.role(Scope("neoapps")) == "grandmaster" and scope.kind != "neoapps"
    if target.id == actor.id:
        raise ValueError("Self role changes require a separate protected procedure.")
    if target.role == "grandmaster" and authority.role(Scope("neoapps")) != "grandmaster":
        raise ValueError("Protected platform administrator.")
    if scope.kind == "neoapps" and (prior_rank == 50 or role == "grandmaster"):
        raise ValueError("Platform Grandmaster commissioning/removal requires a separate protected procedure.")
    if not platform_parent and (prior_rank >= actor_rank or ROLE_LEVELS[role] >= actor_rank):
        raise ValueError("Only roles below your authority may be changed.")
    # A platform parent may oversee child scopes, but peer Grandmaster grants
    # still require commissioning rather than this ordinary mutation service.
    if role == "grandmaster":
        raise ValueError("Grandmaster commissioning is not an ordinary role change.")
    before = None if prior is None else {"role": prior.role, "active": prior.is_active, "version": prior.version}
    after = {"scope": scope.key, "role": role, "active": active, "version": expected_version + 1}
    receipt = _receipt(actor, target, "scoped_role_staged", before, after, reason)
    if prior is None:
        prior = AccountScopedRole(user_id=target.id, kind=scope.kind, scope_key=scope.key, app_code=scope.app_code,
                                  gateway_id=scope.gateway_id, node_id=scope.node_id, role=role,
                                  is_active=active, version=1, security_event_id=receipt.id)
        db.session.add(prior)
    else:
        result = db.session.execute(update(AccountScopedRole).where(
            AccountScopedRole.id == prior.id, AccountScopedRole.version == expected_version).values(
                role=role, is_active=active, version=expected_version + 1, security_event_id=receipt.id,
                updated_at=datetime.utcnow()), execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise AccountConflict("Concurrent role staging change.")
        db.session.expire(prior)
    db.session.flush()
    return prior
