"""Shared password validation and password-write helpers for NeoApps."""

from datetime import datetime
import unicodedata


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
PASSWORD_POLICY_LOGIN_SESSION_KEY = "password_policy_login_user_id"
PASSWORD_POLICY_GUIDANCE = (
    "Use 12–128 characters. Longer passphrases are recommended. "
    "Common or compromised passwords are not allowed."
)

# A deliberately local, compact blocklist covers common breached choices that meet the
# application's minimum length. It avoids sending password material to an external API.
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


def set_user_password(user, password, confirmation=None, *, email=None, employee_id=None):
    """Validate and hash a password, consistently recording its change time."""
    validate_password(
        password,
        confirmation,
        user=user,
        email=email,
        employee_id=employee_id,
    )
    user.set_password(password)
    user.password_changed_at = datetime.utcnow()


def user_requires_password_change(user):
    return bool(
        getattr(user, "password_reset_required", False)
        or getattr(user, "password_policy_update_required", False)
    )


def _normalize(value):
    return unicodedata.normalize("NFKC", str(value or "")).casefold()
