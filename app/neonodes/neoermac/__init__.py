from flask import Blueprint


bp = Blueprint("neoermac", __name__)

from app.neonodes.neoermac import routes  # noqa: E402,F401
