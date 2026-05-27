from pathlib import Path

from flask import Flask

from app.auth.permissions import (
    can_enter_data,
    can_make_decisions,
    can_manage_system,
    can_manage_users,
)
from app.config import Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    register_blueprints(app)
    register_template_helpers(app)

    return app


def register_template_helpers(app):
    @app.context_processor
    def permission_helpers():
        return {
            "can_enter_data": can_enter_data,
            "can_make_decisions": can_make_decisions,
            "can_manage_users": can_manage_users,
            "can_manage_system": can_manage_system,
        }


def register_blueprints(app):
    from app.auth import bp as auth_bp
    from app.neomotherbrain import bp as neomotherbrain_bp
    from app.neonodes import bp as neonodes_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(neomotherbrain_bp)
    app.register_blueprint(neonodes_bp, url_prefix="/nodes")
