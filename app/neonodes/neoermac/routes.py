from flask import redirect, render_template, url_for

from app.auth.decorators import gateway_node_required
from app.neonodes.neoermac import bp
from app.services.access_control import get_current_gateway


NEOERMAC_PAGES = (
    ("BUILDING LINEUP", "neoermac.building_lineup"),
    ("VIEW OUTBOUND", "neoermac.outbound"),
    ("DOOR VIEW", "neoermac.door_view"),
    ("TUG ASSIGNMENTS", "neoermac.tug_assignments"),
)


@bp.route("")
@gateway_node_required("ermac")
def index():
    return render_template(
        "neonodes/neoermac/index.html",
        gateway=get_current_gateway(),
        menu_items=NEOERMAC_PAGES,
    )


@bp.route("/")
@gateway_node_required("ermac")
def index_slash():
    return redirect(url_for("neoermac.index"))


@bp.route("/building-lineup")
@gateway_node_required("ermac")
def building_lineup():
    return _placeholder_page("BUILDING LINEUP")


@bp.route("/outbound")
@gateway_node_required("ermac")
def outbound():
    return _placeholder_page("VIEW OUTBOUND")


@bp.route("/door-view")
@gateway_node_required("ermac")
def door_view():
    return _placeholder_page("DOOR VIEW")


@bp.route("/tug-assignments")
@gateway_node_required("ermac")
def tug_assignments():
    return _placeholder_page("TUG ASSIGNMENTS")


def _placeholder_page(title):
    return render_template(
        "neonodes/neoermac/placeholder.html",
        gateway=get_current_gateway(),
        title=title,
    )
