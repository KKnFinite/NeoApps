from flask import Blueprint


bp = Blueprint("neorain", __name__)


from app.neonodes.neorain import routes  # noqa: E402,F401
