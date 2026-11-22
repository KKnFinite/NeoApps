from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    from app.models.user import User
    from sqlalchemy.orm import defer

    try:
        # Session validation does not need credentials or administrative notes.
        # Explicit account-management reads can still load those fields on demand.
        return db.session.get(User, int(user_id), options=(
            defer(User.password_hash), defer(User.mfa_secret), defer(User.access_reason),
            defer(User.last_password_reset_reason),
        ))
    except (TypeError, ValueError):
        return None
