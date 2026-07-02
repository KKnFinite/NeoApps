from flask import Blueprint

bp = Blueprint("neoscorpion", __name__)

from app.neonodes.neoscorpion import routes  # noqa: E402,F401
