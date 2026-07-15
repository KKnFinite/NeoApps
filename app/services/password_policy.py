"""Shared password validation and password-write helpers for NeoApps."""

from datetime import datetime
import hashlib
import hmac
import logging
import string
import unicodedata
from urllib.request import Request, urlopen

from flask import current_app, has_app_context

from app.services.auth_session_security import rotate_user_session_version


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
HIBP_RANGE_API_URL = "https://api.pwnedpasswords.com/range/"
HIBP_REQUEST_TIMEOUT_SECONDS = 3
PASSWORD_POLICY_GUIDANCE = (
    "Use 12–128 characters. Longer passphrases are recommended. "
    "Common or compromised passwords are not allowed."
)

logger = logging.getLogger(__name__)

# A deliberately local, compact blocklist rejects common breached choices before the
# advisory HIBP range check.
COMMON_COMPROMISED_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890!@",
        "adminpassword",
        "changeme12345",
        "football12345",
        "iloveyou12345",
        "letmein123456",
        "password123!",
        "password12345",
        "passwordpassword",
        "qwerty123456",
        "welcome123456",
    }
)


class PasswordPolicyError(ValueError):
    """Raised when a password does not meet the NeoApps policy."""


def validate_password(
    password,
    confirmation=None,
    *,
    user=None,
    email=None,
    employee_id=None,
):
    """Validate a password without hashing or persisting it."""
    if not isinstance(password, str):
        raise PasswordPolicyError("Password must be text.")
    if confirmation is not None and password != confirmation:
        raise PasswordPolicyError("Passwords do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be no more than {MAX_PASSWORD_LENGTH} characters."
        )
    if not password.strip():
        raise PasswordPolicyError("Password cannot consist only of whitespace.")

    normalized_password = _normalize(password)
    if normalized_password in COMMON_COMPROMISED_PASSWORDS:
        raise PasswordPolicyError("Choose a password that is not commonly compromised.")

    account_identifiers = (
        email if email is not None else getattr(user, "email", None),
        employee_id if employee_id is not None else getattr(user, "employee_id", None),
    )
    for identifier in account_identifiers:
        normalized_identifier = _normalize(identifier)
        if len(normalized_identifier) >= 3 and normalized_identifier in normalized_password:
            raise PasswordPolicyError("Password cannot contain your account information.")

    for protected_term in ("neoapps", "neogateway"):
        if protected_term in normalized_password:
            raise PasswordPolicyError("Password cannot contain NeoApps or NeoGateway.")

    if _password_is_breached(password):
        raise PasswordPolicyError(
            "Choose a password that has not appeared in a known breach."
        )


def set_user_password(user, password, confirmation=None, *, email=None, employee_id=None):
    """Validate and hash a password, consistently recording its change time."""
    validate_password(
        password,
        confirmation,
        user=user,
        email=email,
        employee_id=employee_id,
    )
    user._set_password_hash(password)
    user.password_changed_at = datetime.utcnow()
    if getattr(user, "id", None) is not None:
        rotate_user_session_version(user)


def user_requires_password_change(user):
    return bool(
        getattr(user, "password_reset_required", False)
        or getattr(user, "password_policy_update_required", False)
    )


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


def _password_is_breached(password):
    """Check HIBP's k-anonymity range API without exposing a full password hash."""
    if not _hibp_check_enabled():
        return False

    password_hash = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = password_hash[:5], password_hash[5:]
    request = Request(
        f"{HIBP_RANGE_API_URL}{prefix}",
        headers={
            "Add-Padding": "true",
            "User-Agent": "NeoApps password policy",
        },
    )

    try:
        with urlopen(request, timeout=HIBP_REQUEST_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", None) != 200:
                raise ValueError("Unexpected HIBP response status.")
            response_body = response.read().decode("ascii")
        return _hibp_response_contains_suffix(response_body, suffix)
    except Exception:
        # This check is advisory: local policy remains enforced during HIBP outages.
        # Keep this warning static so it never records password-derived material.
        logger.warning(
            "HIBP breached-password check unavailable; continuing with local password policy."
        )
        return False


def _hibp_response_contains_suffix(response_body, expected_suffix):
    """Return whether a valid HIBP range response includes ``expected_suffix``."""
    found_response_entry = False
    for raw_line in response_body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        returned_suffix, separator, count = line.partition(":")
        if (
            not separator
            or len(returned_suffix) != 35
            or any(character not in string.hexdigits for character in returned_suffix)
            or not count.isdigit()
        ):
            raise ValueError("Unexpected HIBP range response.")
        found_response_entry = True
        if hmac.compare_digest(returned_suffix.upper(), expected_suffix):
            return True

    if not found_response_entry:
        raise ValueError("Unexpected empty HIBP range response.")
    return False


def _hibp_check_enabled():
    """Avoid external network calls in the ordinary unit-test application context."""
    return not (has_app_context() and current_app.config.get("TESTING"))
