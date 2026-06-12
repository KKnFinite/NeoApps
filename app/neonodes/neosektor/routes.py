from flask import flash, redirect, render_template, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neosektor import bp
from app.services.access_control import get_current_gateway
from app.services.neosektor_live_counts import live_counts_context
from app.services.permission_rules import permission_access


DASHBOARD_VIEW_PERMISSION = "neosektor.dashboard.view"

NEOSEKTOR_PAGES = (
    (
        "TUNNEL CONDUCTOR",
        "neosektor.tunnel_conductor",
        "neosektor.tunnel_conductor.view",
        "neosektor.tunnel_conductor.edit",
        "Tunnel Conductor foundation for left-to-arrive and tunnel workflow controls.",
    ),
    (
        "EBM",
        "neosektor.ebm",
        "neosektor.ebm.view",
        "neosektor.ebm.edit",
        "East Ballmat Manager foundation for future EBM count entry.",
    ),
    (
        "WBM",
        "neosektor.wbm",
        "neosektor.wbm.view",
        "neosektor.wbm.edit",
        "West Ballmat Manager foundation for future WBM count entry.",
    ),
    (
        "DISCHARGE",
        "neosektor.discharge",
        "neosektor.discharge.view",
        "neosektor.discharge.edit",
        "Future NeoSektor ULD request queue foundation.",
    ),
    (
        "VIEW LIVE COUNTS",
        "neosektor.live_counts",
        "neosektor.live_counts.view",
        None,
        "Read-only live counts foundation.",
    ),
    (
        "DRIVER ROUTING",
        "neosektor.driver_routing",
        "neosektor.driver_routing.view",
        "neosektor.driver_routing.edit",
        "Driver routing foundation.",
    ),
)


@bp.route("")
@gateway_node_required("sektor")
def index():
    access = permission_access(DASHBOARD_VIEW_PERMISSION)
    live_counts_access = permission_access("neosektor.live_counts.view")
    if not access["can_view"] and not live_counts_access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    return render_template(
        "neonodes/neosektor/index.html",
        gateway=get_current_gateway(),
        menu_items=NEOSEKTOR_PAGES,
    )


@bp.route("/")
@gateway_node_required("sektor")
def index_slash():
    return redirect(url_for("neosektor.index"))


@bp.route("/tunnel-conductor")
@gateway_node_required("sektor")
def tunnel_conductor():
    return _placeholder_page("TUNNEL CONDUCTOR")


@bp.route("/ebm")
@gateway_node_required("sektor")
def ebm():
    return _placeholder_page("EBM")


@bp.route("/wbm")
@gateway_node_required("sektor")
def wbm():
    return _placeholder_page("WBM")


@bp.route("/discharge")
@gateway_node_required("sektor")
def discharge():
    return _placeholder_page("DISCHARGE")


@bp.route("/live-counts")
@gateway_node_required("sektor")
def live_counts():
    page = _page_by_title("VIEW LIVE COUNTS")
    access = permission_access(page["view_permission"])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    context = live_counts_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/live_counts.html",
        gateway=gateway,
        can_view=access["can_view"],
        **context,
    )


@bp.route("/driver-routing")
@gateway_node_required("sektor")
def driver_routing():
    return _placeholder_page("DRIVER ROUTING")


def _placeholder_page(title):
    page = _page_by_title(title)
    view_permission = page["view_permission"]
    edit_permission = page["edit_permission"]
    access = permission_access(view_permission, edit_permission)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    return render_template(
        "neonodes/neosektor/placeholder.html",
        gateway=get_current_gateway(),
        title=title,
        description=page["description"],
        can_view=access["can_view"],
        can_edit=access["can_edit"],
    )


def _page_by_title(title):
    for label, endpoint, view_permission, edit_permission, description in NEOSEKTOR_PAGES:
        if label == title:
            return {
                "label": label,
                "endpoint": endpoint,
                "view_permission": view_permission,
                "edit_permission": edit_permission,
                "description": description,
            }
    raise ValueError(f"Unknown NeoSektor page: {title}")
