"""The sole protected platform commissioning action; never edits scoped grants."""
from hashlib import sha256
import json

from flask import current_app
from flask_login import current_user
from itsdangerous import BadData, URLSafeTimedSerializer

from app.extensions import db
from app.models import AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.services.account_security import lock_administration, platform_grandmaster
from app.services.auth_session_security import rotate_user_session_version
from app.services.request_cache import clear_request_cache
from app.services.user_tokens import revoke_unused_email_verification_tokens, revoke_unused_password_reset_tokens


class CommissioningDenied(ValueError):
    pass


class CommissioningConflict(ValueError):
    pass


class ReauthenticationFailed(ValueError):
    pass


def _ready(user):
    # MFA flags are not an MFA challenge. Never claim password-only step-up
    # satisfies an MFA requirement or change these flags to admit the action.
    return bool(user and user.is_active and user.email and user.email_verified_at and user.password_hash
                and not user.scorpion_vendor and not user.password_reset_required
                and not user.password_policy_update_required
                and not user.mfa_required and not user.mfa_enabled)


def may_initiate_commissioning(actor):
    return platform_grandmaster(actor) and _ready(actor)


def may_commission(actor, target):
    return bool(may_initiate_commissioning(actor) and _ready(target)
                and actor.id != target.id and target.role != "grandmaster")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def scoped_grant_fingerprint(user_id):
    """Bounded, request-local evidence; contains no rendered or shared cache."""
    with db.session.no_autoflush:
        apps = db.session.query(PortalAppAccess.id, PortalAppAccess.app_code, PortalAppAccess.role,
            PortalAppAccess.status, PortalAppAccess.is_active, PortalAppAccess.updated_at).filter_by(user_id=user_id).order_by(PortalAppAccess.id).all()
        memberships = db.session.query(GatewayMembership.id, GatewayMembership.gateway_id, GatewayMembership.status,
            GatewayMembership.is_active, GatewayMembership.updated_at).filter_by(user_id=user_id).order_by(GatewayMembership.id).all()
        nodes = db.session.query(GatewayNodeRole.id, GatewayNodeRole.gateway_membership_id, GatewayNodeRole.node_id,
            GatewayNodeRole.role, GatewayNodeRole.is_active, GatewayNodeRole.updated_at).join(GatewayMembership).filter(
                GatewayMembership.user_id == user_id).order_by(GatewayNodeRole.id).all()
    return _digest([[tuple(row) for row in rows] for rows in (apps, memberships, nodes)])


def _security_fingerprint(user):
    return _digest({key: getattr(user, key) for key in (
        "id", "username", "first_name", "last_name", "email", "employee_id", "role", "is_active",
        "email_verified_at", "auth_session_version", "password_hash", "password_changed_at",
        "password_reset_required", "password_policy_update_required", "mfa_required", "mfa_enabled",
        "temporary_password_expires_at")})


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="neoapps.platform-commissioning.v1")


def commissioning_review(actor, target):
    """Read-only signed review; IDs/state bound to this actor, never authority."""
    if not may_commission(actor, target):
        raise CommissioningDenied("A different active, verified, eligible account and platform Grandmaster are required. MFA reauthentication is not implemented for this action.")
    return _signer().dumps({"actor_id": actor.id, "target_id": target.id,
        "actor_state": _security_fingerprint(actor), "target_state": _security_fingerprint(target),
        "grants": scoped_grant_fingerprint(target.id)})


def commission_grandmaster(target_id, *, review_token, current_password, confirmed_target_id,
                          confirm_platform_authority, reason):
    """Caller commits success. Every failure rolls back the entire action."""
    try:
        if not current_user.is_authenticated:
            raise CommissioningDenied("Authentication required.")
        if not isinstance(review_token, str) or len(review_token) > 4096:
            raise CommissioningConflict("The commissioning review is invalid.")
        try:
            review = _signer().loads(review_token, max_age=10 * 60)
        except (BadData, TypeError):
            raise CommissioningConflict("The commissioning review expired or is invalid. Review the account again.") from None
        try:
            actor = lock_administration()
        except ValueError as error:
            raise CommissioningDenied("Administrative authority changed. Sign in and review the account again.") from error
        if not NeoNode.query.filter_by(code="motherbrain").first() or not may_initiate_commissioning(actor):
            raise CommissioningDenied("Verified platform Grandmaster authority with supported password reauthentication is required.")
        if actor.id == target_id:
            raise CommissioningDenied("Self-directed commissioning is prohibited.")
        target = User.query.filter_by(id=target_id).populate_existing().with_for_update().one_or_none()
        if target is None:
            raise CommissioningDenied("Unknown target account.")
        if target.role == "grandmaster":
            raise CommissioningConflict("This account is already a platform Grandmaster. No change was made.")
        if not _ready(target):
            raise CommissioningDenied("The target must be active, email verified, unrestricted and recoverable through the supported authentication flow.")
        if (not isinstance(review, dict) or review.get("actor_id") != actor.id or review.get("target_id") != target.id
                or review.get("actor_state") != _security_fingerprint(actor)
                or review.get("target_state") != _security_fingerprint(target)
                or review.get("grants") != scoped_grant_fingerprint(target.id)):
            raise CommissioningConflict("Account or permission state changed. Review and confirm the current account again.")
        if str(confirmed_target_id).strip() != str(target.id) or confirm_platform_authority != "yes":
            raise CommissioningDenied(f"Type {target.id} to confirm this NeoApps account and check the platform-wide authority box.")
        if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 1000:
            raise CommissioningDenied("A commissioning reason of 10–1000 characters is required.")
        if not isinstance(current_password, str) or len(current_password) > 1024 or not actor.check_password(current_password):
            raise ReauthenticationFailed("Current password could not be verified. No change was made.")
        before = {"role": target.role, "session_version": target.auth_session_version}
        grant_digest = review["grants"]
        # Only this authorized service writes the platform role. No scoped grant,
        # account identity, password, MFA or recovery restriction is reset.
        target.role = "grandmaster"
        rotate_user_session_version(target)
        revoke_unused_password_reset_tokens(target)
        revoke_unused_email_verification_tokens(target)
        db.session.add(AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=target.id,
            action="platform_grandmaster_commissioned", details={
                "scope": "neoapps", "before": before,
                "after": {"role": target.role, "session_version": target.auth_session_version},
                "reason": reason.strip(), "reauthentication": "current_password",
                "target_confirmed": True, "platform_authority_confirmed": True,
                "scoped_grants_before": grant_digest, "scoped_grants_after": scoped_grant_fingerprint(target.id),
                "outstanding_recovery_tokens_revoked": True}))
        db.session.flush()
        if scoped_grant_fingerprint(target.id) != grant_digest:
            raise CommissioningConflict("Scoped permissions changed during commissioning.")
        return target
    except Exception:
        db.session.rollback()
        raise
    finally:
        clear_request_cache()


def guard_last_recoverable_platform_admin_email_change(target):
    """Call under the existing administration/account locks, before email edit."""
    if target.role != "grandmaster" or not target.is_active or not target.email_verified_at:
        return
    others = User.query.filter(User.id != target.id, User.role == "grandmaster", User.is_active.is_(True),
        User.email_verified_at.is_not(None), User.email.is_not(None), User.email != "",
        User.password_reset_required.is_(False), User.password_policy_update_required.is_(False),
        User.mfa_required.is_(False), User.mfa_enabled.is_(False), ~User.scorpion_vendor.has()).count()
    if not others:
        raise ValueError("Cannot remove verification from the last recoverable platform Grandmaster. Commission another verified administrator first.")
