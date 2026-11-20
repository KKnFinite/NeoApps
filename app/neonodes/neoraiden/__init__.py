from flask import Blueprint

bp = Blueprint("neoraiden", __name__)

from . import routes  # noqa: E402,F401
