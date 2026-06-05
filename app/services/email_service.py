import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app


BREVO_SMTP_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"


def send_email_verification(user, token):
    verification_url = _absolute_url(f"/verify-email/{token}")
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject="Verify your NeoGateway account",
        html_content=(
            "<p>Welcome to NeoGateway.</p>"
            f'<p><a href="{verification_url}">Verify your email address</a></p>'
            "<p>This verification link will expire.</p>"
        ),
        text_content=(
            "Welcome to NeoGateway.\n\n"
            f"Verify your email address: {verification_url}\n\n"
            "This verification link will expire."
        ),
    )


def send_access_approved(user, gateway):
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject=f"{gateway.name} access approved",
        html_content=(
            f"<p>Your access to {gateway.name} has been approved.</p>"
            f'<p><a href="{_absolute_url("/login")}">Log in to NeoGateway</a></p>'
        ),
        text_content=(
            f"Your access to {gateway.name} has been approved.\n\n"
            f"Log in to NeoGateway: {_absolute_url('/login')}"
        ),
    )


def send_password_reset(user, token):
    reset_url = _absolute_url(f"/reset-password/{token}")
    return _send_transactional_email(
        to_email=user.email,
        to_name=user.display_name,
        subject="Reset your NeoGateway password",
        html_content=(
            "<p>A NeoGateway password reset was requested.</p>"
            f'<p><a href="{reset_url}">Reset your password</a></p>'
            "<p>This reset link will expire and can only be used once.</p>"
        ),
        text_content=(
            "A NeoGateway password reset was requested.\n\n"
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
        BREVO_SMTP_EMAIL_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            response_body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as error:
        current_app.logger.warning("Brevo email send failed: %s", error)
        return {"sent": False, "reason": "send_failed"}

    return {"sent": True, "response": response_body}


def _absolute_url(path):
    base_url = str(current_app.config.get("APP_BASE_URL", "")).rstrip("/")
    return f"{base_url}{path}"
