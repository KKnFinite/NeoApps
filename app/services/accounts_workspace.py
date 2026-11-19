"""Current-authority UI services. Staged scoped roles remain inactive.

Request attempts/decisions reuse Phase 2 history. Entitlements are still changed
only by the existing, Phase 1 protected application/membership services.
"""
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from flask import current_app
from flask_login import current_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountSecurityEvent, GatewayMembership, GatewayNodeRole, PortalAppAccess, User, FuelDriverIdentity, FuelVendorMembership, FuelVendorCompany
from app.models.account_preference import AccountPreference
from app.services.account_foundation import AccountConflict, atomic_account_change
from app.services.account_security import lock_administration, record_security_change
from app.services.access_control import get_current_gateway, portal_app_definitions, user_has_app_access, user_has_gateway_access
from app.services.permission_rules import user_can


CRYOCHECK_URL = "https://cryocheck.onrender.com"


def available_applications():
    return [app for app in portal_app_definitions() if not app.get("coming_soon")]


def require_available_app(code):
    if code not in {app["code"] for app in available_applications()}:
        raise ValueError("This application is not available for requests or approvals.")


def authorized_applications(user):
    if not user.is_active or user.scorpion_vendor:
        return []
    permissions = {"neogateway": "neogateway.landing.view", "neostaffing": "neostaffing.board.view"}
    return [app for app in available_applications()
            if user_has_app_access(user, app["code"]) and user_can(permissions[app["code"]], user=user)
            and (app["code"] != "neogateway" or user_has_gateway_access(user, get_current_gateway().code))]


def preference(user):
    return db.session.get(AccountPreference, user.id)


def _snapshot(user_id):
    """Hash persisted fields, including legacy writers, without provisioning grants."""
    user = db.session.execute(User.__table__.select().where(User.id == user_id)).mappings().one()
    memberships = db.session.execute(GatewayMembership.__table__.select().where(GatewayMembership.user_id == user_id).order_by(GatewayMembership.id)).mappings().all()
    grants = db.session.execute(GatewayNodeRole.__table__.select().where(
        GatewayNodeRole.gateway_membership_id.in_([m["id"] for m in memberships])).order_by(GatewayNodeRole.id)).mappings().all()
    apps = db.session.execute(PortalAppAccess.__table__.select().where(PortalAppAccess.user_id == user_id).order_by(PortalAppAccess.id)).mappings().all()
    from app.models import AccountScopedRole,AccountAuthorizationState
    scopes=db.session.execute(AccountScopedRole.__table__.select().where(AccountScopedRole.user_id==user_id).order_by(AccountScopedRole.id)).mappings().all()
    attempts = db.session.execute(AccountAccessRequest.__table__.select().where(AccountAccessRequest.user_id == user_id).order_by(AccountAccessRequest.id)).mappings().all()
    pref = db.session.execute(AccountPreference.__table__.select().where(AccountPreference.user_id == user_id)).mappings().first()
    # Last-login changes do not make an administrator's independent review stale.
    identity = dict(user)
    identity.pop("last_login", None)
    driver = db.session.execute(FuelDriverIdentity.__table__.select().where(FuelDriverIdentity.user_id == user_id)).mappings().first()
    affiliations = db.session.execute(FuelVendorMembership.__table__.select().where(FuelVendorMembership.user_id == user_id).order_by(FuelVendorMembership.id)).mappings().all()
    companies = db.session.execute(FuelVendorCompany.__table__.select().where(FuelVendorCompany.id.in_([m['company_id'] for m in affiliations])).order_by(FuelVendorCompany.id)).mappings().all()
    state=db.session.get(AccountAuthorizationState,1)
    payload = [(state.active,state.version) if state else None,identity, [dict(m) for m in memberships], [dict(g) for g in grants], [dict(a) for a in apps], [dict(a) for a in attempts], dict(pref) if pref else None,
        dict(driver) if driver else None, [dict(m) for m in affiliations], [dict(c) for c in companies], [dict(s) for s in scopes]]
    return sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def review_token(target):
    return URLSafeTimedSerializer(current_app.secret_key, salt="accounts-workspace-v1").dumps(
        {"actor": current_user.id, "session": current_user.auth_session_version,
         "target": target.id, "state": _snapshot(target.id)})


def verify_review(target, token):
    from app.services.account_deletion import receipt
    if receipt(target.id):
        raise AccountConflict("This account was deleted. Its historical identity is read-only.")
    try:
        value = URLSafeTimedSerializer(current_app.secret_key, salt="accounts-workspace-v1").loads(token, max_age=1200)
    except (BadSignature, SignatureExpired, TypeError):
        raise AccountConflict("This review expired. Reload and review the current account before retrying.") from None
    if value != {"actor": current_user.id, "session": current_user.auth_session_version,
                 "target": target.id, "state": _snapshot(target.id)}:
        raise AccountConflict("The account or request changed. No changes saved; review the current state.")


def lock_self():
    user_id, version = current_user.id, current_user.auth_session_version
    # Same serialization order as legacy account/request writers.
    actor = lock_administration()
    if actor.id != user_id or actor.auth_session_version != version:
        raise AccountConflict("Your session changed. Sign in again.")
    return actor


def gateway_membership_needs_review(user):
    """An app entitlement never substitutes for an approved gateway membership."""
    gateway = get_current_gateway()
    if not gateway or not gateway.is_active:
        return False
    membership = GatewayMembership.query.filter_by(user_id=user.id, gateway_id=gateway.id).first()
    return not membership or membership.status != 'approved' or not membership.is_active


def new_attempt(user, code, reason, key=None, *, registration=False):
    require_available_app(code)
    reason = str(reason or "").strip()
    if not 3 <= len(reason) <= 2000:
        raise ValueError("Enter an access reason of 3–2000 characters.")
    try:
        key = str(UUID(key)) if key else str(uuid4())
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid request identifier.") from None
    prior = AccountAccessRequest.query.filter_by(user_id=user.id, request_key=key).first()
    if prior:
        if prior.app_code == code and prior.reason == reason:
            return prior
        raise AccountConflict("Request identifier already used.")
    if AccountAccessRequest.query.filter_by(user_id=user.id, app_code=code, state="pending").first():
        raise AccountConflict("An open request already exists for this application.")
    live = PortalAppAccess.query.filter_by(user_id=user.id, app_code=code).first()
    gateway_review = bool(not registration and code == 'neogateway' and live and live.status == 'approved'
                          and live.is_active and gateway_membership_needs_review(user))
    if not registration and live and (live.status == "pending" and live.is_active or live.status == "approved" and live.is_active) and not gateway_review:
        raise AccountConflict("This application already has access or a pending request.")
    attempt = AccountAccessRequest(user_id=user.id, app_code=code, reason=reason, request_key=key)
    db.session.add(attempt)
    db.session.flush()
    previous = None
    if live and live.status == "denied":
        # Preserve the actual legacy decision as observed now, without inventing
        # an earlier request attempt or assigning a fictional decision date.
        previous = {"status": live.status, "denied_at": str(live.denied_at) if live.denied_at else None,
            "denied_by": live.denied_by_user_id, "denial_notes": live.denial_notes}
    db.session.add(AccountSecurityEvent(actor_user_id=user.id, subject_user_id=user.id,
        action="access_request_created", details={"request_id": attempt.id, "app_code": code,
            "origin": "registration" if registration else "my_account", "effective_access_changed": False,
            "previous_legacy_decision_observed": previous,
            "gateway_membership_review": get_current_gateway().id if gateway_review else None}))
    return attempt


def application_decision_reason(outcome, reason):
    """Approval/denial explanations are optional; revocation keeps its guard."""
    reason = str(reason or "").strip()
    if len(reason) > 2000:
        raise ValueError("Decision reason must be at most 2000 characters.")
    if outcome not in {"approved", "denied"} and len(reason) < 3:
        raise ValueError("Enter a decision reason of 3–2000 characters.")
    return reason


def close_open_attempt(user, code, outcome, reason):
    """Legacy and new writers close the same open attempt once, in their transaction."""
    attempt = AccountAccessRequest.query.filter_by(user_id=user.id, app_code=code, state="pending").with_for_update().first()
    if not attempt:
        return
    attempt.state = outcome
    attempt.version += 1
    event = AccountSecurityEvent(actor_user_id=current_user.id, subject_user_id=user.id,
        action="access_request_decided", details={"request_id": attempt.id, "app_code": code,
            "outcome": outcome, "version": attempt.version, "reason": reason or "Administrator decision",
            "entitlement_source": "existing_authorization"})
    db.session.add(event)
    db.session.flush()
    db.session.add(AccountAccessDecision(request_id=attempt.id, outcome=outcome,
        reason=reason or "Administrator decision", security_event_id=event.id))


@atomic_account_change
def save_preferences(token, preferred_app, show_cryocheck):
    actor = lock_self()
    verify_review(actor, token)
    selected = preferred_app or None
    if selected and selected not in {app["code"] for app in authorized_applications(actor)}:
        raise ValueError("Choose an application you can currently access, or turn the preference off.")
    row = preference(actor)
    before = {"preferred_app": row.preferred_app, "show_cryocheck": row.show_cryocheck} if row else None
    if row is None:
        row = AccountPreference(user_id=actor.id)
        db.session.add(row)
    else:
        row.version += 1
    row.preferred_app = selected
    row.show_cryocheck = bool(show_cryocheck)
    record_security_change("account_preferences_changed", actor, before=before,
        after={"preferred_app": selected, "show_cryocheck": bool(show_cryocheck)})
    db.session.flush()
