from flask import Blueprint


bp = Blueprint("neonodes", __name__)

from app.neonodes import routes  # noqa: E402,F401
