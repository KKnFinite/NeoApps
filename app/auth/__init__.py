from flask import Blueprint


bp = Blueprint("auth", __name__)

from app.auth import routes  # noqa: E402,F401
from app.auth import accounts  # noqa: E402,F401
from app.auth import fuel_driver_accounts  # noqa: E402,F401
from app.auth import scoped_access  # noqa: E402,F401
