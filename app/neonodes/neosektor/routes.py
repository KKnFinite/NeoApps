from flask import flash, jsonify, redirect, render_template, request, session, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neosektor import bp
from app.services.access_control import get_current_gateway
from app.services.neosektor_live_counts import (
    TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    TUNNEL_CONDUCTOR_VIEW_PERMISSION,
    adjust_tunnel_count,
    ballmat_operations_context,
    ballmat_state_payload,
    driver_routing_context,
    driver_routing_state_payload,
    live_counts_context,
    normalize_ballmat_side,
    tunnel_conductor_context,
    update_driver_routing_settings,
    update_ballmat_side,
)
from app.services.permission_rules import user_can


EBM_VIEW_PERMISSION = "neosektor.ebm.view"
EBM_EDIT_PERMISSION = "neosektor.ebm.edit"
WBM_VIEW_PERMISSION = "neosektor.wbm.view"
WBM_EDIT_PERMISSION = "neosektor.wbm.edit"

NEOSEKTOR_PAGES = (
    (
        "TUNNEL CONDUCTOR",
        "neosektor.tunnel_conductor",
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
        "Tunnel Conductor live count controls.",
    ),
    (
        "EBM",
        "neosektor.ebm",
        EBM_VIEW_PERMISSION,
        EBM_EDIT_PERMISSION,
        "East Ballmat Operations count entry.",
    ),
    (
        "WBM",
        "neosektor.wbm",
        WBM_VIEW_PERMISSION,
        WBM_EDIT_PERMISSION,
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
        None,
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
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    context = tunnel_conductor_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/tunnel_conductor.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )


@bp.route("/tunnel-conductor/state")
@gateway_node_required("sektor")
def tunnel_conductor_state():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    state = ballmat_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/delta", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_delta():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    try:
        state = adjust_tunnel_count(
            get_current_gateway(),
            payload.get("side"),
            payload.get("wave"),
            payload.get("delta"),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/ebm")
@gateway_node_required("sektor")
def ebm():
    return _render_ballmat_operations("east")


@bp.route("/wbm")
@gateway_node_required("sektor")
def wbm():
    return _render_ballmat_operations("west")


@bp.route("/ballmat")
@gateway_node_required("sektor")
def ballmat_operations():
    selected_side = _selected_ballmat_side()
    return redirect(_ballmat_route_for_side(selected_side))


def _render_ballmat_operations(selected_side):
    access = _ballmat_access(selected_side)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

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
    if not _can_view_any_ballmat():
        return jsonify({"ok": False, "error": "Access denied."}), 403

    state = ballmat_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/ballmat/update", methods=["POST"])
@gateway_node_required("sektor")
def ballmat_update():
    selected_side = _selected_ballmat_side()
    access = _ballmat_access(selected_side)
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

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
    gateway = get_current_gateway()
    context = live_counts_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/live_counts.html",
        gateway=gateway,
        can_view=True,
        **context,
    )


@bp.route("/driver-routing")
@gateway_node_required("sektor")
def driver_routing():
    page = _page_by_title("DRIVER ROUTING")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    context = driver_routing_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/driver_routing.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )


@bp.route("/driver-routing/state")
@gateway_node_required("sektor")
def driver_routing_state():
    page = _page_by_title("DRIVER ROUTING")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    state = driver_routing_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/driver-routing/update", methods=["POST"])
@gateway_node_required("sektor")
def driver_routing_update():
    page = _page_by_title("DRIVER ROUTING")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    state = update_driver_routing_settings(get_current_gateway(), payload)
    db.session.commit()
    return jsonify({"ok": True, "state": state})


def _placeholder_page(title):
    page = _page_by_title(title)
    view_permission = page["view_permission"]
    edit_permission = page["edit_permission"]
    access = _neosektor_access(view_permission, edit_permission)
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


def _ballmat_access(side):
    view_permission, edit_permission = _ballmat_permission_keys(side)
    return _neosektor_access(view_permission, edit_permission)


def _neosektor_access(view_permission, edit_permission=None):
    can_view = True if not view_permission else user_can(view_permission)
    can_edit = bool(edit_permission and can_view and user_can(edit_permission))

    return {
        "can_view": can_view,
        "can_edit": can_edit,
    }


def _can_view_any_ballmat():
    for side in ("east", "west"):
        if _ballmat_access(side)["can_view"]:
            return True
    return False


def _ballmat_permission_keys(side):
    selected_side = normalize_ballmat_side(side)
    if selected_side == "west":
        return WBM_VIEW_PERMISSION, WBM_EDIT_PERMISSION
    return EBM_VIEW_PERMISSION, EBM_EDIT_PERMISSION


def _ballmat_route_for_side(side):
    selected_side = normalize_ballmat_side(side)
    endpoint = "neosektor.wbm" if selected_side == "west" else "neosektor.ebm"
    return url_for(endpoint)
