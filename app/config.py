import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


DEFAULT_DEVELOPMENT_SECRET_KEY = "dev-change-me"
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_database_uri():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    return f"sqlite:///{BASE_DIR / 'instance' / 'neoapps.sqlite'}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    NEOAPPS_ENV = os.getenv("NEOAPPS_ENV", os.getenv("FLASK_ENV", "")).strip().lower()
    STATIC_ASSET_VERSION = os.getenv("STATIC_ASSET_VERSION", "20260623-3")
    DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    DEFAULT_GATEWAY_CODE = os.getenv("DEFAULT_GATEWAY_CODE", "RFD").upper()
    DEFAULT_GATEWAY_NAME = os.getenv("DEFAULT_GATEWAY_NAME", "NeoGateway")
    DEFAULT_GATEWAY_TIMEZONE = os.getenv("DEFAULT_GATEWAY_TIMEZONE", "America/Chicago")
    DEFAULT_GATEWAY_LOGO = os.getenv(
        "DEFAULT_GATEWAY_LOGO",
        "images/icons/neogateway/inapp/neogateway-inapp-128.png",
    )
    BREVO_API_KEY = os.getenv("BREVO_API_KEY")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "NeoApps Portal")
    MAIL_FROM_EMAIL = os.getenv(
        "MAIL_FROM_EMAIL",
        "no-reply@neogateway.khriskessler.com",
    )
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
    EMAIL_VERIFICATION_TOKEN_HOURS = int(
        os.getenv("EMAIL_VERIFICATION_TOKEN_HOURS", "168")
    )
    PASSWORD_RESET_TOKEN_HOURS = int(os.getenv("PASSWORD_RESET_TOKEN_HOURS", "1"))
    EMERGENCY_PASSWORD_EXPIRATION_HOURS = int(
        os.getenv("EMERGENCY_PASSWORD_EXPIRATION_HOURS", "24")
    )
    SECURITY_HEADERS_CSP_ENABLED = env_flag("SECURITY_HEADERS_CSP_ENABLED", True)
    SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION = env_flag(
        "SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION",
        False,
    )
    SECURITY_HEADERS_HSTS_ENABLED = env_flag("SECURITY_HEADERS_HSTS_ENABLED", True)
    SECURITY_HEADERS_HSTS_ALLOW_NON_PRODUCTION = env_flag(
        "SECURITY_HEADERS_HSTS_ALLOW_NON_PRODUCTION",
        False,
    )
    SECURITY_HEADERS_HSTS_MAX_AGE = int(
        os.getenv("SECURITY_HEADERS_HSTS_MAX_AGE", "31536000")
    )
    CSRF_ENABLED = env_flag("CSRF_ENABLED", True)
    CSRF_TOKEN_TTL_SECONDS = int(os.getenv("CSRF_TOKEN_TTL_SECONDS", "7200"))
    # Existing test fixtures opt in where they need to assert CSRF behavior.
    CSRF_PROTECT_TESTING = env_flag("CSRF_PROTECT_TESTING", False)
    AUTH_RATE_LIMIT_ENABLED = env_flag("AUTH_RATE_LIMIT_ENABLED", True)
    AUTH_RATE_LIMIT_STORAGE = os.getenv(
        "AUTH_RATE_LIMIT_STORAGE", "database"
    ).strip().lower()
    AUTH_TRUST_PROXY_HEADERS = env_flag("AUTH_TRUST_PROXY_HEADERS", False)
    AUTH_TRUSTED_PROXY_IPS = tuple(
        value.strip()
        for value in os.getenv("AUTH_TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    )
    AUTH_LOGIN_WINDOW_SECONDS = int(os.getenv("AUTH_LOGIN_WINDOW_SECONDS", "900"))
    AUTH_LOGIN_IP_MAX_FAILURES = int(os.getenv("AUTH_LOGIN_IP_MAX_FAILURES", "10"))
    AUTH_LOGIN_IDENTIFIER_MAX_FAILURES = int(
        os.getenv("AUTH_LOGIN_IDENTIFIER_MAX_FAILURES", "5")
    )
    AUTH_LOGIN_BASE_COOLDOWN_SECONDS = int(
        os.getenv("AUTH_LOGIN_BASE_COOLDOWN_SECONDS", "30")
    )
    AUTH_LOGIN_MAX_COOLDOWN_SECONDS = int(
        os.getenv("AUTH_LOGIN_MAX_COOLDOWN_SECONDS", "900")
    )
    AUTH_PASSWORD_RESET_WINDOW_SECONDS = int(
        os.getenv("AUTH_PASSWORD_RESET_WINDOW_SECONDS", "3600")
    )
    AUTH_PASSWORD_RESET_IP_MAX_ATTEMPTS = int(
        os.getenv("AUTH_PASSWORD_RESET_IP_MAX_ATTEMPTS", "5")
    )
    AUTH_PASSWORD_RESET_IDENTIFIER_MAX_ATTEMPTS = int(
        os.getenv("AUTH_PASSWORD_RESET_IDENTIFIER_MAX_ATTEMPTS", "3")
    )
    AUTH_PASSWORD_RESET_BASE_COOLDOWN_SECONDS = int(
        os.getenv("AUTH_PASSWORD_RESET_BASE_COOLDOWN_SECONDS", "300")
    )
    AUTH_PASSWORD_RESET_MAX_COOLDOWN_SECONDS = int(
        os.getenv("AUTH_PASSWORD_RESET_MAX_COOLDOWN_SECONDS", "3600")
    )
    SQLALCHEMY_DATABASE_URI = resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Production startup retries transient connection loss during schema/bootstrap
    # work. Tests default to one attempt unless a focused test opts into retries.
    DATABASE_STARTUP_RETRY_ATTEMPTS = int(
        os.getenv("DATABASE_STARTUP_RETRY_ATTEMPTS", "5")
    )
    DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING = int(
        os.getenv("DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING", "1")
    )
    DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS = float(
        os.getenv("DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS", "1")
    )
    DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS = float(
        os.getenv("DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS", "8")
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("SESSION_COOKIE_SECURE", bool(os.getenv("DATABASE_URL")))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    AUTO_BOOTSTRAP_DATABASE = env_flag("AUTO_BOOTSTRAP_DATABASE")


class DevelopmentConfig(Config):
    """Explicit local-development configuration used by the local launch helpers."""

    NEOAPPS_ENV = "development"


def configure_secret_key(config):
    """Require a non-default signing key unless this is an explicit dev/test app."""
    secret_key = config.get("SECRET_KEY")
    if _allows_development_secret_key(config):
        if not isinstance(secret_key, str) or not secret_key.strip():
            config["SECRET_KEY"] = DEFAULT_DEVELOPMENT_SECRET_KEY
        return

    normalized_secret = secret_key.strip() if isinstance(secret_key, str) else ""
    insecure_values = {
        "change-me",
        DEFAULT_DEVELOPMENT_SECRET_KEY,
        "default",
        "password",
        "secret",
    }
    if (
        len(normalized_secret) < MIN_PRODUCTION_SECRET_KEY_LENGTH
        or normalized_secret.casefold() in insecure_values
        or len(set(normalized_secret)) == 1
    ):
        raise RuntimeError(
            "SECRET_KEY configuration error: set a unique SECRET_KEY of at least "
            f"{MIN_PRODUCTION_SECRET_KEY_LENGTH} characters before starting NeoApps."
        )


def _allows_development_secret_key(config):
    environment = str(config.get("NEOAPPS_ENV") or "").strip().lower()
    if environment in {"development", "dev", "testing", "test"}:
        return True
    if config.get("TESTING"):
        return True
    return bool(config.get("DEBUG")) and environment != "production"
