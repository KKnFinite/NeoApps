from flask import Blueprint


bp = Blueprint("neomotherbrain", __name__)

from app.neomotherbrain import routes  # noqa: E402,F401
