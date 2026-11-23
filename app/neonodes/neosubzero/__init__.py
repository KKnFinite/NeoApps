from flask import Blueprint

bp = Blueprint("neosubzero", __name__)


def operational_blueprint():
    """Bind routes at app assembly so service imports do not initialize HTTP routes."""
    from . import routes, operations_routes, release2_routes, frost_routes  # noqa: F401
    return bp
