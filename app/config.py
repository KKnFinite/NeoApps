import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


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
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")
    STATIC_ASSET_VERSION = os.getenv("STATIC_ASSET_VERSION", "20260613-1")
    DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    DEFAULT_GATEWAY_CODE = os.getenv("DEFAULT_GATEWAY_CODE", "RFD").upper()
    DEFAULT_GATEWAY_NAME = os.getenv("DEFAULT_GATEWAY_NAME", "NeoGateway")
    DEFAULT_GATEWAY_TIMEZONE = os.getenv("DEFAULT_GATEWAY_TIMEZONE", "America/Chicago")
    DEFAULT_GATEWAY_LOGO = os.getenv(
        "DEFAULT_GATEWAY_LOGO",
        "images/neogateway_logo3_small.png",
    )
    BREVO_API_KEY = os.getenv("BREVO_API_KEY")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "NeoApps Portal")
    MAIL_FROM_EMAIL = os.getenv(
        "MAIL_FROM_EMAIL",
        "no-reply@neogateway.khriskessler.com",
    )
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
    EMAIL_VERIFICATION_TOKEN_HOURS = int(
        os.getenv("EMAIL_VERIFICATION_TOKEN_HOURS", "24")
    )
    PASSWORD_RESET_TOKEN_HOURS = int(os.getenv("PASSWORD_RESET_TOKEN_HOURS", "1"))
    SQLALCHEMY_DATABASE_URI = resolve_database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = env_flag("SESSION_COOKIE_SECURE", bool(os.getenv("DATABASE_URL")))
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    AUTO_BOOTSTRAP_DATABASE = env_flag("AUTO_BOOTSTRAP_DATABASE")
