import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-change-me")
    DEBUG = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    DEFAULT_GATEWAY_CODE = os.getenv("DEFAULT_GATEWAY_CODE", "RFD").upper()
    DEFAULT_GATEWAY_NAME = os.getenv("DEFAULT_GATEWAY_NAME", "NeoRFD")
    DEFAULT_GATEWAY_LOGO = os.getenv(
        "DEFAULT_GATEWAY_LOGO",
        "images/neorfd_logo1.png",
    )
    BREVO_API_KEY = os.getenv("BREVO_API_KEY")
    MAIL_FROM_NAME = os.getenv("MAIL_FROM_NAME", "NeoGateway")
    MAIL_FROM_EMAIL = os.getenv(
        "MAIL_FROM_EMAIL",
        "no-reply@neogateway.khriskessler.com",
    )
    APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
    EMAIL_VERIFICATION_TOKEN_HOURS = int(
        os.getenv("EMAIL_VERIFICATION_TOKEN_HOURS", "24")
    )
    PASSWORD_RESET_TOKEN_HOURS = int(os.getenv("PASSWORD_RESET_TOKEN_HOURS", "1"))
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'neoapps.sqlite'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
