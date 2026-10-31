from datetime import datetime, timedelta
import hashlib
import hmac
import secrets

from flask import current_app

from app.extensions import db
from app.models import User, UserToken


EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"


def create_user_token(user, token_type, expires_in_hours=None):
    if token_type not in {EMAIL_VERIFICATION, PASSWORD_RESET}:
        raise ValueError("Unsupported token type.")

    if token_type == PASSWORD_RESET:
        revoke_unused_password_reset_tokens(user)

    raw_token = secrets.token_urlsafe(48)
    token_hash = hash_user_token(raw_token)
    expires_at = datetime.utcnow() + timedelta(
        hours=_expiration_hours(token_type, expires_in_hours)
    )

    user_token = UserToken(
        user_id=user.id,
        token_hash=token_hash,
        token_type=token_type,
        expires_at=expires_at,
    )
    db.session.add(user_token)
    db.session.flush()
    return raw_token, user_token


def hash_user_token(raw_token):
    secret = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(
        secret,
        str(raw_token).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def get_token_record(raw_token, token_type):
    token_hash = hash_user_token(raw_token)
    return UserToken.query.filter_by(
        token_hash=token_hash,
        token_type=token_type,
    ).first()


def get_valid_token_record(raw_token, token_type):
    token_record = get_token_record(raw_token, token_type)
    if not token_record:
        return None
    if token_record.is_used or token_record.is_expired():
        return None
    return token_record


def claim_password_reset_token(raw_token):
    """Claim once inside the caller's transaction, before changing a password.

    Always lock the account before any reset-token writes. Different outstanding
    links for one account must serialize too. Refresh ORM state after waiting;
    neither a prior form GET nor an identity-map snapshot authorizes a reset.
    The caller must commit password + revocations together, or roll back all.
    """
    token_hash = hash_user_token(raw_token)
    user_id = db.session.query(UserToken.user_id).filter_by(
        token_hash=token_hash, token_type=PASSWORD_RESET,
    ).scalar()
    if user_id is None:
        return None
    user = User.query.filter_by(id=user_id).with_for_update().populate_existing().first()
    if user is None:
        return None
    token_record = UserToken.query.filter_by(
        token_hash=token_hash, token_type=PASSWORD_RESET, user_id=user.id,
    ).populate_existing().first()
    now = datetime.utcnow()  # Evaluate after the account lock, not request start.
    if token_record is None or token_record.is_used or token_record.is_expired(now):
        return None
    claimed = UserToken.query.filter(
        UserToken.id == token_record.id,
        UserToken.used_at.is_(None),
        UserToken.expires_at > now,
    ).update({UserToken.used_at: now}, synchronize_session=False)
    if claimed != 1:
        return None
    db.session.expire(token_record, ["used_at"])
    return token_record


def mark_token_used(token_record):
    token_record.used_at = datetime.utcnow()
    db.session.flush()


def revoke_unused_password_reset_tokens(user):
    """Serialize issuance/revocation with reset completion for this account."""
    # Account-first order matches claim_password_reset_token. Do not refresh or
    # overwrite caller-owned password edits while acquiring this lock.
    db.session.query(User.id).filter(User.id == user.id).with_for_update().one()
    UserToken.query.filter(
        UserToken.user_id == user.id,
        UserToken.token_type == PASSWORD_RESET,
        UserToken.used_at.is_(None),
    ).update({UserToken.used_at: datetime.utcnow()}, synchronize_session=False)
    db.session.flush()


def revoke_unused_email_verification_tokens(user):
    """Invalidate unused verification links before issuing a replacement."""
    UserToken.query.filter(
        UserToken.user_id == user.id,
        UserToken.token_type == EMAIL_VERIFICATION,
        UserToken.used_at.is_(None),
    ).update({UserToken.used_at: datetime.utcnow()}, synchronize_session=False)
    db.session.flush()


def _expiration_hours(token_type, explicit_hours=None):
    if explicit_hours is not None:
        return int(explicit_hours)
    if token_type == EMAIL_VERIFICATION:
        return int(current_app.config.get("EMAIL_VERIFICATION_TOKEN_HOURS", 168))
    return int(current_app.config.get("PASSWORD_RESET_TOKEN_HOURS", 1))
