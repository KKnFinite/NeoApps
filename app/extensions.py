from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    from app.models.user import User

    try:
        return User.query.get(int(user_id))
    except (TypeError, ValueError):
        return None
