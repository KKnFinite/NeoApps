from flask import Blueprint


bp = Blueprint("neostaffing", __name__)


from app.neostaffing import routes  # noqa: E402,F401
