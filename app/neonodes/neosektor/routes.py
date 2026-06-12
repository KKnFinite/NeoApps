from flask import flash, jsonify, redirect, render_template, request, session, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neosektor import bp
from app.services.access_control import get_current_gateway
from app.services.neosektor_live_counts import (
    BALLMAT_EDIT_PERMISSION,
    BALLMAT_VIEW_PERMISSION,
    ballmat_operations_context,
    ballmat_state_payload,
    live_counts_context,
    normalize_ballmat_side,
    update_ballmat_side,
)
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
        BALLMAT_VIEW_PERMISSION,
        BALLMAT_EDIT_PERMISSION,
        "East Ballmat Operations count entry.",
    ),
    (
        "WBM",
        "neosektor.wbm",
        BALLMAT_VIEW_PERMISSION,
        BALLMAT_EDIT_PERMISSION,
        "West Ballmat Operations count entry.",
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
    return redirect(url_for("neosektor.ballmat_operations", side="east"))


@bp.route("/wbm")
@gateway_node_required("sektor")
def wbm():
    return redirect(url_for("neosektor.ballmat_operations", side="west"))


@bp.route("/ballmat")
@gateway_node_required("sektor")
def ballmat_operations():
    access = permission_access(BALLMAT_VIEW_PERMISSION, BALLMAT_EDIT_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    selected_side = _selected_ballmat_side()
    session["neosektor_ballmat_side"] = selected_side
    gateway = get_current_gateway()
    context = ballmat_operations_context(gateway, selected_side)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/ballmat.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )


@bp.route("/ballmat/state")
@gateway_node_required("sektor")
def ballmat_state():
    access = permission_access(BALLMAT_VIEW_PERMISSION, BALLMAT_EDIT_PERMISSION)
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    state = ballmat_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/ballmat/update", methods=["POST"])
@gateway_node_required("sektor")
def ballmat_update():
    access = permission_access(BALLMAT_VIEW_PERMISSION, BALLMAT_EDIT_PERMISSION)
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    selected_side = _selected_ballmat_side()
    payload = request.get_json(silent=True) or request.form.to_dict(flat=False)
    try:
        state = update_ballmat_side(get_current_gateway(), selected_side, payload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403

    session["neosektor_ballmat_side"] = selected_side
    db.session.commit()
    return jsonify({"ok": True, "state": state})


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


def _selected_ballmat_side():
    requested_side = normalize_ballmat_side(request.args.get("side"))
    session_side = normalize_ballmat_side(session.get("neosektor_ballmat_side"))
    return requested_side or session_side or "east"
