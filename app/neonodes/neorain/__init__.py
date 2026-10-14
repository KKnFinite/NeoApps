from flask import Blueprint


bp = Blueprint("neorain", __name__)


from app.neonodes.neorain import routes  # noqa: E402,F401
from app.neonodes.neorain import mission_routes  # noqa: E402,F401
from app.neonodes.neorain import override_routes  # noqa: E402,F401
from app.neonodes.neorain import certification_routes  # noqa: E402,F401
