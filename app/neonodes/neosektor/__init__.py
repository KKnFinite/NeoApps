from flask import Blueprint


bp = Blueprint("neosektor", __name__)

from app.neonodes.neosektor import routes  # noqa: E402,F401
