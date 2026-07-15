import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, flash, redirect, request, send_from_directory, session, url_for
from flask_login import current_user, logout_user

from app.auth.permissions import (
    can_enter_data,
    can_make_decisions,
    can_manage_system,
    can_manage_users,
)
from app.config import Config, configure_secret_key
from app.extensions import db, login_manager
from app.services.access_control import user_can_access_node, user_has_gateway_access
from app.services.permission_rules import permission_access, user_can
from app.services.auth_session_security import (
    clear_authenticated_session_security_state,
    session_version_matches_user,
)
from app.services.auth_rate_limits import initialize_auth_rate_limit_storage
from app.services.password_policy import user_requires_password_change
from app.services.time_display import format_local_hhmm


def create_app(config_class=Config, auto_bootstrap=True):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if config_class is not Config:
        app.config.from_object(config_class)
    configure_secret_key(app.config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    sync_existing_local_schema(app)

    if auto_bootstrap:
        maybe_auto_bootstrap_database(app)
    initialize_auth_rate_limit_storage(app)
    register_pwa_assets(app)
    register_blueprints(app)
    register_template_helpers(app)
    register_request_guards(app)
    register_security_headers(app)

    return app


def sync_existing_local_schema(app):
    if app.config.get("TESTING"):
        return False

    database_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if not database_uri.startswith("sqlite:"):
        return False

    from app.services.schema_sync import sync_local_sqlite_schema

    with app.app_context():
        sync_local_sqlite_schema(app)
        db.session.commit()
    return True


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
    role_labels = {
        "watcher": "Watcher",
        "operator": "Operator",
        "simulator": "Simulator",
        "master": "Master",
        "grandmaster": "Grandmaster",
    }

    def format_role_label(value):
        if not value:
            return ""
        normalized = str(value).strip().lower()
        return role_labels.get(normalized, str(value).strip().replace("_", " ").title())

    def format_status_label(value):
        if not value:
            return "-"
        return str(value).strip().replace("_", " ").title()

    def format_wave_label(value):
        normalized = str(value or "").strip().lower()
        if not normalized:
            return ""
        if normalized in ("1", "1st", "first", "first wave", "1st wave"):
            return "1"
        if normalized in ("2", "2nd", "second", "second wave", "2nd wave"):
            return "2"
        return ""

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

    def format_local_time(value, timezone_name=None):
        return format_local_hhmm(value, timezone_name)

    def format_motherbrain_alert_severity(value):
        from app.services.motherbrain_alerts import alert_severity_label

        return alert_severity_label(value)

    def current_pwa_manifest_key():
        path = request.path.rstrip("/") or "/"
        if path == "/rfd" or path.startswith("/rfd/"):
            return "neogateway"
        if path == "/motherbrain" or path.startswith("/motherbrain/"):
            return "neomotherbrain"
        if path == "/neostaffing" or path.startswith("/neostaffing/"):
            return "neostaffing"
        if path == "/neobid" or path.startswith("/neobid/"):
            return "neobid"
        if path == "/neoermac" or path.startswith("/neoermac/"):
            return "neoermac"
        if path == "/neosektor" or path.startswith("/neosektor/"):
            return "neosektor"
        if path == "/neoscorpion" or path.startswith("/neoscorpion/"):
            return "neoscorpion"
        if path == "/neoreptile" or path.startswith("/neoreptile/"):
            return "reptile"
        if (
            path == "/neosubzero"
            or path.startswith("/neosubzero/")
            or path == "/neosub-zero"
            or path.startswith("/neosub-zero/")
            or path == "/neo-sub-zero"
            or path.startswith("/neo-sub-zero/")
        ):
            return "subzero"
        if path == "/neorain" or path.startswith("/neorain/"):
            return "rain"
        return "neoapps"

    def change_character_targets():
        if not current_user.is_authenticated:
            return []

        gateway_code = app.config["DEFAULT_GATEWAY_CODE"]
        targets = []
        node_specs = (
            {
                "key": "motherbrain",
                "node_code": "motherbrain",
                "node_word": "MotherBrain",
                "endpoint": "neomotherbrain.motherbrain",
                "minimum_role": "simulator",
                "path_prefixes": ("/motherbrain", "/admin/users", "/admin/permissions"),
                "icon_src": "images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png",
            },
            {
                "key": "ermac",
                "node_code": "ermac",
                "node_word": "Ermac",
                "endpoint": "neoermac.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neoermac",),
                "icon_src": "images/icons/neoermac/inapp/neoermac-inapp-128.png",
            },
            {
                "key": "sektor",
                "node_code": "sektor",
                "node_word": "Sektor",
                "endpoint": "neosektor.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neosektor",),
                "icon_src": "images/icons/neosektor/inapp/neosektor-icon-128x128.png",
            },
            {
                "key": "scorpion",
                "node_code": "scorpion",
                "node_word": "Scorpion",
                "endpoint": "neoscorpion.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neoscorpion",),
                "icon_src": "images/icons/neoscorpion/inapp/neoscorpion-128x128.png",
            },
            {
                "key": "reptile",
                "node_code": "reptile",
                "node_word": "Reptile",
                "endpoint": "neoreptile.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neoreptile",),
            },
            {
                "key": "subzero",
                "node_code": "subzero",
                "node_word": "Sub-Zero",
                "endpoint": "neosubzero.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neosubzero", "/neosub-zero", "/neo-sub-zero"),
            },
            {
                "key": "rain",
                "node_code": "rain",
                "node_word": "Rain",
                "endpoint": "neorain.index",
                "minimum_role": "watcher",
                "path_prefixes": ("/neorain",),
            },
        )
        for spec in node_specs:
            if spec["endpoint"] not in app.view_functions:
                continue
            if not user_can_access_node(
                current_user,
                gateway_code,
                spec["node_code"],
                minimum_role=spec["minimum_role"],
            ):
                continue
            is_current = any(
                request.path.startswith(prefix) for prefix in spec["path_prefixes"]
            )
            if is_current:
                continue
            targets.append(
                {
                    "key": spec["key"],
                    "node_word": spec["node_word"],
                    "suffix": "",
                    "href": url_for(spec["endpoint"]),
                    "is_current": False,
                    "icon_src": spec.get(
                        "icon_src",
                        f"images/icons/{spec['key']}/icon_192.png",
                    ),
                }
            )

        return targets

    app.jinja_env.filters["local_datetime"] = format_local_datetime
    app.jinja_env.filters["local_time"] = format_local_time
    app.jinja_env.filters["motherbrain_alert_severity"] = format_motherbrain_alert_severity
    app.jinja_env.filters["role_label"] = format_role_label
    app.jinja_env.filters["status_label"] = format_status_label
    app.jinja_env.filters["wave_label"] = format_wave_label

    @app.context_processor
    def permission_helpers():
        return {
            "can_enter_data": can_enter_data,
            "can_make_decisions": can_make_decisions,
            "can_manage_users": can_manage_users,
            "can_manage_system": can_manage_system,
            "user_has_gateway_access": user_has_gateway_access,
            "user_can_access_node": user_can_access_node,
            "user_can": user_can,
            "permission_access": permission_access,
            "change_character_targets": change_character_targets,
            "current_pwa_manifest_key": current_pwa_manifest_key,
        }

    @app.context_processor
    def motherbrain_alerts():
        if (
            not current_user.is_authenticated
            or not (
                request.path.startswith("/motherbrain")
                or request.path.startswith("/portal/manage")
            )
        ):
            return {
                "motherbrain_alert_tray": None,
                "motherbrain_shell_operation": None,
            }

        from app.services.access_control import get_current_gateway
        from app.services.motherbrain_alerts import (
            motherbrain_alert_context,
            motherbrain_alert_operation_for_request,
        )

        gateway = get_current_gateway()
        operation = motherbrain_alert_operation_for_request(gateway, request)

        return {
            "motherbrain_alert_tray": motherbrain_alert_context(
                gateway,
                can_view_permission=user_can,
                operation=operation,
            ),
            "motherbrain_shell_operation": operation,
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

        if not session_version_matches_user(session, current_user):
            logout_user()
            clear_authenticated_session_security_state(session)
            flash("Your session has expired. Please sign in again.", "info")
            return redirect(url_for("auth.login"))

        if not user_requires_password_change(current_user):
            return None

        allowed_endpoints = {
            "auth.change_password",
            "auth.logout",
            "static",
        }
        if request.endpoint in allowed_endpoints:
            return None

        return redirect(url_for("auth.change_password"))


def register_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        return response


def register_pwa_assets(app):
    manifest_definitions = _pwa_manifest_definitions()

    def send_pwa_image(filename):
        response = send_from_directory(
            app.static_folder,
            f"images/{filename}",
            mimetype="image/png",
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    def manifest_response(manifest_key):
        manifest = manifest_definitions.get(manifest_key)
        if not manifest:
            abort(404)

        response = app.response_class(
            json.dumps(manifest, indent=2),
            mimetype="application/manifest+json",
        )
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.route("/manifest.webmanifest")
    def pwa_manifest():
        return manifest_response("neoapps")

    @app.route("/manifest/<manifest_key>.webmanifest")
    def pwa_manifest_by_key(manifest_key):
        return manifest_response(str(manifest_key or "").strip().lower())

    @app.route("/service-worker.js")
    def service_worker():
        response = send_from_directory(
            app.static_folder,
            "service-worker.js",
            mimetype="application/javascript",
            max_age=0,
        )
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/apple-touch-icon.png")
    def apple_touch_icon():
        return send_pwa_image("icons/neoapps/pwa/apple-touch-icon.png")

    @app.route("/apple-touch-icon-precomposed.png")
    def apple_touch_icon_precomposed():
        return send_pwa_image("icons/neoapps/pwa/apple-touch-icon.png")

    @app.route("/favicon-32x32.png")
    def favicon_32():
        return send_pwa_image("icons/neoapps/favicon/favicon-32.png")

    @app.route("/favicon-16x16.png")
    def favicon_16():
        return send_pwa_image("icons/neoapps/favicon/favicon-16.png")

    @app.route("/favicon.ico")
    def favicon_ico():
        return send_pwa_image("icons/neoapps/favicon/favicon-32.png")


def _pwa_manifest_definitions():
    specs = {
        "neoapps": {
            "name": "NeoApps",
            "short_name": "NeoApps",
            "description": "NeoApps access dashboard.",
            "start_url": "/portal",
            "theme_color": "#d9362e",
            "icons": [
                _pwa_icon_src("/static/images/icons/neoapps/pwa/neoapps-icon-192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neoapps/pwa/neoapps-icon-512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neoapps/pwa/neoapps-maskable-192.png", "192x192", "maskable"),
                _pwa_icon_src("/static/images/icons/neoapps/pwa/neoapps-maskable-512.png", "512x512", "maskable"),
            ],
        },
        "neogateway": {
            "name": "NeoGateway",
            "short_name": "NeoGateway",
            "description": "NeoGateway operations hub.",
            "start_url": "/rfd",
            "theme_color": "#d95a1f",
            "icons": [
                _pwa_icon_src("/static/images/icons/neogateway/pwa/neogateway-icon-192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neogateway/pwa/neogateway-icon-512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neogateway/pwa/neogateway-maskable-512.png", "512x512", "any maskable"),
            ],
        },
        "neostaffing": {
            "name": "NeoStaffing",
            "short_name": "NeoStaffing",
            "description": "NeoStaffing workforce planning.",
            "start_url": "/neostaffing",
            "theme_color": "#27d0c2",
            "icons": [
                _pwa_icon_src("/static/images/icons/neostaffing/pwa/neostaffing-icon-192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neostaffing/pwa/neostaffing-icon-512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neostaffing/pwa/neostaffing-maskable-512.png", "512x512", "any maskable"),
            ],
        },
        "neobid": {
            "name": "NeoBid",
            "short_name": "NeoBid",
            "description": "NeoBid bid tools placeholder.",
            "start_url": "/neobid",
            "theme_color": "#4db7ff",
            "icon_folder": "neobid",
        },
        "neomotherbrain": {
            "name": "NeoMotherBrain",
            "short_name": "MotherBrain",
            "description": "NeoMotherBrain operations core.",
            "start_url": "/motherbrain",
            "theme_color": "#cf6a6e",
            "icons": [
                _pwa_icon_src("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neomotherbrain/pwa/neomotherbrain-maskable-512.png", "512x512", "any maskable"),
            ],
        },
        "neosektor": {
            "name": "NeoSektor",
            "short_name": "NeoSektor",
            "description": "NeoSektor ballmat operations.",
            "start_url": "/neosektor",
            "theme_color": "#b5121b",
            "icons": [
                _pwa_icon_src("/static/images/icons/neosektor/pwa/android-chrome-192x192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neosektor/pwa/android-chrome-512x512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neosektor/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                _pwa_icon_src("/static/images/icons/neosektor/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
            ],
        },
        "neoermac": {
            "name": "NeoErmac",
            "short_name": "NeoErmac",
            "description": "NeoErmac outbound operations.",
            "start_url": "/neoermac",
            "theme_color": "#8f1826",
            "icons": [
                _pwa_icon_src("/static/images/icons/neoermac/pwa/neoermac-icon-192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neoermac/pwa/neoermac-icon-512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neoermac/pwa/neoermac-maskable-512.png", "512x512", "any maskable"),
            ],
        },
        "neoscorpion": {
            "name": "NeoScorpion",
            "short_name": "NeoScorpion",
            "description": "NeoScorpion fueling operations.",
            "start_url": "/neoscorpion",
            "theme_color": "#f4c21f",
            "icons": [
                _pwa_icon_src("/static/images/icons/neoscorpion/pwa/icon-192x192.png", "192x192", "any"),
                _pwa_icon_src("/static/images/icons/neoscorpion/pwa/icon-512x512.png", "512x512", "any"),
                _pwa_icon_src("/static/images/icons/neoscorpion/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                _pwa_icon_src("/static/images/icons/neoscorpion/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
            ],
        },
        "reptile": {
            "name": "NeoReptile",
            "short_name": "NeoReptile",
            "description": "NeoReptile placeholder.",
            "start_url": "/nodes/",
            "theme_color": "#70e13b",
            "icon_folder": "reptile",
        },
        "subzero": {
            "name": "NeoSub-Zero",
            "short_name": "Sub-Zero",
            "description": "NeoSub-Zero placeholder.",
            "start_url": "/nodes/",
            "theme_color": "#4db7ff",
            "icon_folder": "subzero",
        },
        "rain": {
            "name": "NeoRain",
            "short_name": "NeoRain",
            "description": "NeoRain placeholder.",
            "start_url": "/nodes/",
            "theme_color": "#7f4dff",
            "icon_folder": "rain",
        },
    }

    definitions = {key: _build_pwa_manifest(key, spec) for key, spec in specs.items()}
    legacy_aliases = {
        "neoportal": "neoapps",
        "motherbrain": "neomotherbrain",
        "sektor": "neosektor",
        "ermac": "neoermac",
        "scorpion": "neoscorpion",
    }
    for alias, canonical_key in legacy_aliases.items():
        definitions[alias] = _build_pwa_manifest(alias, specs[canonical_key])

    return definitions


def _build_pwa_manifest(manifest_key, spec):
    icons = spec.get("icons")
    if icons is None:
        icon_folder = spec["icon_folder"]
        icons = [
            _pwa_icon(icon_folder, "icon_192.png", "192x192", "any"),
            _pwa_icon(icon_folder, "icon_512.png", "512x512", "any"),
        ]
        if spec.get("maskable"):
            icons.extend(
                [
                    _pwa_icon(icon_folder, "icon_maskable_192.png", "192x192", "maskable"),
                    _pwa_icon(icon_folder, "icon_maskable_512.png", "512x512", "maskable"),
                ]
            )

    return {
        "id": f"/manifest/{manifest_key}.webmanifest",
        "name": spec["name"],
        "short_name": spec["short_name"],
        "description": spec["description"],
        "start_url": spec["start_url"],
        "scope": "/",
        "display": "standalone",
        "background_color": "#050506",
        "theme_color": spec["theme_color"],
        "icons": icons,
    }


def _pwa_icon(icon_folder, filename, sizes, purpose):
    return _pwa_icon_src(f"/static/images/icons/{icon_folder}/{filename}", sizes, purpose)


def _pwa_icon_src(src, sizes, purpose):
    return {
        "src": src,
        "sizes": sizes,
        "type": "image/png",
        "purpose": purpose,
    }


def register_blueprints(app):
    from app.auth import bp as auth_bp
    from app.neomotherbrain import bp as neomotherbrain_bp
    from app.neonodes import bp as neonodes_bp
    from app.neonodes.neoermac import bp as neoermac_bp
    from app.neonodes.neosektor import bp as neosektor_bp
    from app.neonodes.neoscorpion import bp as neoscorpion_bp
    from app.neostaffing import bp as neostaffing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(neomotherbrain_bp)
    app.register_blueprint(neonodes_bp, url_prefix="/nodes")
    app.register_blueprint(neoermac_bp, url_prefix="/neoermac")
    app.register_blueprint(neosektor_bp, url_prefix="/neosektor")
    app.register_blueprint(neoscorpion_bp, url_prefix="/neoscorpion")
    app.register_blueprint(neostaffing_bp, url_prefix="/neostaffing")


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
