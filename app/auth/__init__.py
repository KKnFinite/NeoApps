from flask import Blueprint


bp = Blueprint("auth", __name__)

from app.auth import routes  # noqa: E402,F401
from app.auth import accounts  # noqa: E402,F401
