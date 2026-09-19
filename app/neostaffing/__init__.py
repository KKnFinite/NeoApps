from flask import Blueprint


bp = Blueprint("neostaffing", __name__)


from app.neostaffing import routes  # noqa: E402,F401
from app.neostaffing import employee_records  # noqa: E402,F401
