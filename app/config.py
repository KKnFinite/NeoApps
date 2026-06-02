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
        "images/neorfd_logo.png",
    )
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'instance' / 'neoapps.sqlite'}",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
