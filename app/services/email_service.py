import json
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app


BREVO_SMTP_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_SMTP_EMAIL_HOST = "api.brevo.com"


def send_email_verification(user, token):
    verification_url = _absolute_url(f"/verify-email/{token}")
    expiration = _email_verification_expiration_copy()
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject="Verify your NeoApps Portal account",
        html_content=(
            "<p>Welcome to NeoApps Portal.</p>"
            f'<p><a href="{verification_url}">Verify your email address</a></p>'
            f"<p>This verification link expires in {expiration}.</p>"
        ),
        text_content=(
            "Welcome to NeoApps Portal.\n\n"
            f"Verify your email address: {verification_url}\n\n"
            f"This verification link expires in {expiration}."
        ),
    )


def send_access_approved(user, gateway):
    approved_app_name = getattr(gateway, "name", None) or "NeoGateway"
    login_url = _absolute_url("/login")
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject="NeoApps access approved",
        html_content=(
            "<p>Your access request has been approved.</p>"
            f"<p>Approved access: <strong>{escape(approved_app_name)}</strong></p>"
            f'<p>You can open NeoApps Portal here: <a href="{login_url}">{login_url}</a></p>'
        ),
        text_content=(
            "Your access request has been approved.\n\n"
            f"Approved access: {approved_app_name}\n\n"
            f"Open NeoApps Portal: {login_url}"
        ),
    )


def send_password_reset(user, token):
    reset_url = _absolute_url(f"/reset-password/{token}")
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject="Reset your NeoApps Portal password",
        html_content=(
            "<p>A NeoApps Portal password reset was requested.</p>"
            f'<p><a href="{reset_url}">Reset your password</a></p>'
            "<p>This reset link will expire and can only be used once.</p>"
        ),
        text_content=(
            "A NeoApps Portal password reset was requested.\n\n"
            f"Reset your password: {reset_url}\n\n"
            "This reset link will expire and can only be used once."
        ),
    )


def _send_transactional_email(
    *,
    to_email,
    to_name,
    subject,
    html_content,
    text_content,
):
    api_key = current_app.config.get("BREVO_API_KEY")
    from_name = current_app.config.get("MAIL_FROM_NAME")
    from_email = current_app.config.get("MAIL_FROM_EMAIL")

    if current_app.config.get("TESTING") and not current_app.config.get(
        "SEND_EMAIL_IN_TESTS"
    ):
        current_app.logger.info("Email skipped because the app is in test mode.")
        return {"sent": False, "reason": "test_mode"}

    if not all((api_key, from_name, from_email, to_email)):
        current_app.logger.info("Email skipped because mail configuration is incomplete.")
        return {"sent": False, "reason": "missing_config"}

    payload = {
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email, "name": to_name or to_email}],
        "subject": subject,
        "htmlContent": html_content,
        "textContent": text_content,
    }
    request = Request(
        _validated_brevo_email_url(),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:  # nosec B310
            response_body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as error:
        current_app.logger.warning("Brevo email send failed: %s", error)
        return {"sent": False, "reason": "send_failed"}

    return {"sent": True, "response": response_body}


def _validated_brevo_email_url():
    parsed = urlparse(BREVO_SMTP_EMAIL_URL)
    if parsed.scheme != "https" or parsed.hostname != BREVO_SMTP_EMAIL_HOST:
        raise RuntimeError("Brevo email URL must use the approved HTTPS API host.")
    return BREVO_SMTP_EMAIL_URL


def _absolute_url(path):
    base_url = str(current_app.config.get("APP_BASE_URL", "")).rstrip("/")
    return f"{base_url}{path}"


def _email_verification_expiration_copy():
    hours = int(current_app.config.get("EMAIL_VERIFICATION_TOKEN_HOURS", 168))
    if hours and hours % 24 == 0:
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"
