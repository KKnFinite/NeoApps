from flask import Blueprint

bp = Blueprint("neosubzero", __name__)

from app.neonodes.neosubzero import routes  # noqa: E402,F401
from app.neonodes.neosubzero import operations_routes  # noqa: E402,F401
