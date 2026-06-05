import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, redirect, request, url_for
from flask_login import current_user

from app.auth.permissions import (
    can_enter_data,
    can_make_decisions,
    can_manage_system,
    can_manage_users,
)
from app.config import Config
from app.extensions import db, login_manager
from app.services.access_control import user_can_access_node, user_has_gateway_access


def create_app(config_class=Config, auto_bootstrap=True):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if config_class is not Config:
        app.config.from_object(config_class)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    if auto_bootstrap:
        maybe_auto_bootstrap_database(app)
    register_blueprints(app)
    register_template_helpers(app)
    register_request_guards(app)

    return app


def maybe_auto_bootstrap_database(app):
    if not app.config.get("AUTO_BOOTSTRAP_DATABASE"):
        return False

    if not os.getenv("DATABASE_URL"):
        app.logger.info("Bootstrap skipped")
        return False

    app.logger.info("Auto bootstrap enabled")
    from app.services.database_bootstrap import bootstrap_database

    bootstrap_database(app)
    app.logger.info("Bootstrap completed")
    return True


def register_template_helpers(app):
    def format_local_datetime(value, timezone_name=None):
        if not value:
            return ""

        timezone_name = timezone_name or app.config.get(
            "DEFAULT_GATEWAY_TIMEZONE",
            "America/Chicago",
        )
        utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        try:
            local_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            if timezone_name == "America/Chicago":
                return _fallback_chicago_datetime(
                    utc_value.replace(tzinfo=None)
                ).strftime("%Y-%m-%d %H:%M")
            return value.strftime("%Y-%m-%d %H:%M")

        return utc_value.astimezone(local_timezone).strftime("%Y-%m-%d %H:%M")

    app.jinja_env.filters["local_datetime"] = format_local_datetime

    @app.context_processor
    def permission_helpers():
        return {
            "can_enter_data": can_enter_data,
            "can_make_decisions": can_make_decisions,
            "can_manage_users": can_manage_users,
            "can_manage_system": can_manage_system,
            "user_has_gateway_access": user_has_gateway_access,
            "user_can_access_node": user_can_access_node,
        }

    @app.context_processor
    def gateway_branding():
        return {
            "default_gateway": {
                "code": app.config["DEFAULT_GATEWAY_CODE"],
                "name": app.config["DEFAULT_GATEWAY_NAME"],
                "logo": app.config["DEFAULT_GATEWAY_LOGO"],
            }
        }


def register_request_guards(app):
    @app.before_request
    def force_required_password_change():
        if not current_user.is_authenticated:
            return None

        if not getattr(current_user, "password_reset_required", False):
            return None

        allowed_endpoints = {
            "auth.change_password",
            "auth.logout",
            "static",
        }
        if request.endpoint in allowed_endpoints:
            return None

        return redirect(url_for("auth.change_password"))


def register_blueprints(app):
    from app.auth import bp as auth_bp
    from app.neomotherbrain import bp as neomotherbrain_bp
    from app.neonodes import bp as neonodes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(neomotherbrain_bp)
    app.register_blueprint(neonodes_bp, url_prefix="/nodes")


def _fallback_chicago_datetime(utc_datetime):
    standard_local = utc_datetime - timedelta(hours=6)
    if _is_us_central_daylight_time(standard_local):
        return utc_datetime - timedelta(hours=5)
    return standard_local


def _is_us_central_daylight_time(local_datetime):
    year = local_datetime.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= local_datetime < dst_end


def _nth_weekday_of_month(year, month, weekday, nth):
    current = datetime(year, month, 1)
    while current.weekday() != weekday:
        current += timedelta(days=1)
    return current + timedelta(days=7 * (nth - 1))
