from flask import flash, jsonify, redirect, render_template, request, session, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neosektor import bp
from app.services.access_control import get_current_gateway
from app.services.neosektor_live_counts import (
    TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    TUNNEL_CONDUCTOR_VIEW_PERMISSION,
    adjust_tunnel_wave_arrivals,
    ballmat_operations_context,
    ballmat_state_payload,
    driver_routing_context,
    driver_routing_state_payload,
    live_counts_context,
    normalize_ballmat_side,
    tunnel_conductor_context,
    update_neosektor_operational_settings,
    update_tunnel_driver_offset,
    update_ballmat_side,
)
from app.services.permission_rules import user_can
from app.services.uld_requests import (
    discharge_context,
    discharge_state_payload,
    send_uld_totals_on_the_way,
    send_uld_on_the_way,
)


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
        "NeoSektor ULD request discharge queue.",
    ),
    (
        "DRIVER ROUTING",
        "neosektor.driver_routing",
        "neosektor.driver_routing.view",
        None,
        "Driver routing foundation.",
    ),
)

NEOSEKTOR_INTERNAL_MENU = (
    ("Live Counts", "neosektor.index", None),
    ("Tunnel Conductor", "neosektor.tunnel_conductor", TUNNEL_CONDUCTOR_VIEW_PERMISSION),
    ("East Ballmat", "neosektor.ebm", EBM_VIEW_PERMISSION),
    ("West Ballmat", "neosektor.wbm", WBM_VIEW_PERMISSION),
    ("Driver Routing", "neosektor.driver_routing", "neosektor.driver_routing.view"),
    ("Discharge", "neosektor.discharge", "neosektor.discharge.view"),
)


@bp.context_processor
def inject_neosektor_navigation():
    return {
        "neosektor_internal_menu_items": _visible_neosektor_menu_items,
    }


@bp.route("")
@gateway_node_required("sektor")
def index():
    gateway = get_current_gateway()
    context = live_counts_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neosektor/live_counts.html",
        gateway=gateway,
        can_view=True,
        **context,
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

    state = driver_routing_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/wave", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_wave():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    try:
        state = adjust_tunnel_wave_arrivals(
            get_current_gateway(),
            payload.get("wave"),
            payload.get("delta"),
            value=payload.get("value") if "value" in payload else None,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/offset", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_offset():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    state = update_tunnel_driver_offset(get_current_gateway(), payload)
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/settings", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_settings():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    state = update_neosektor_operational_settings(get_current_gateway(), payload)
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/tunnel-conductor/ballmat", methods=["POST"])
@gateway_node_required("sektor")
def tunnel_conductor_ballmat():
    access = _neosektor_access(
        TUNNEL_CONDUCTOR_VIEW_PERMISSION,
        TUNNEL_CONDUCTOR_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        return jsonify({"ok": False, "error": "Edit access denied."}), 403

    payload = request.get_json(silent=True) or request.form
    side = normalize_ballmat_side((payload or {}).get("side"))
    if not side:
        return jsonify({"ok": False, "error": "Invalid side."}), 400

    try:
        gateway = get_current_gateway()
        update_ballmat_side(gateway, side, payload)
        state = driver_routing_state_payload(gateway)
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
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.index"))

    gateway = get_current_gateway()
    context = discharge_context(gateway)
    selected_request_id = request.args.get("request_id", type=int)
    selected_request = next(
        (
            request_row
            for request_row in context["requests"]
            if request_row.get("id") == selected_request_id
        ),
        None,
    )
    db.session.commit()
    return render_template(
        "neonodes/neosektor/discharge.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        selected_request=selected_request,
        **context,
    )


@bp.route("/discharge/state")
@gateway_node_required("sektor")
def discharge_state():
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    state = discharge_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/discharge/send", methods=["POST"])
@gateway_node_required("sektor")
def discharge_send():
    page = _page_by_title("DISCHARGE")
    access = _neosektor_access(page["view_permission"], page["edit_permission"])
    if not access["can_edit"]:
        if request.is_json:
            return jsonify({"ok": False, "error": "Edit access denied."}), 403
        flash("Access denied.", "error")
        return redirect(url_for("neosektor.discharge"))

    payload = request.get_json(silent=True) if request.is_json else request.form
    try:
        if _has_multi_uld_send_payload(payload):
            events = send_uld_totals_on_the_way(
                get_current_gateway(),
                payload.get("door"),
                {
                    "A2": payload.get("send_a2_count"),
                    "A1": payload.get("send_a1_count"),
                    "AMP": payload.get("send_amp_count"),
                },
                request_id=payload.get("request_id"),
            )
            event = events[0]
        else:
            event = send_uld_on_the_way(
                get_current_gateway(),
                payload.get("door"),
                payload.get("uld_type"),
                payload.get("quantity"),
                request_id=payload.get("request_id"),
            )
    except ValueError as exc:
        db.session.rollback()
        if request.is_json:
            return jsonify({"ok": False, "error": str(exc)}), 400
        flash(str(exc), "error")
        return redirect(url_for("neosektor.discharge", request_id=payload.get("request_id")))

    db.session.commit()
    if request.is_json:
        return jsonify(
            {
                "ok": True,
                "event": {
                    "door": event.door,
                    "uld_type": event.uld_type,
                    "quantity": event.quantity,
                },
                "state": discharge_state_payload(get_current_gateway()),
            }
        )

    flash("ULDs marked on the way.", "success")
    return redirect(url_for("neosektor.discharge"))


@bp.route("/live-counts")
@gateway_node_required("sektor")
def live_counts():
    return redirect(url_for("neosektor.index"))


def _has_multi_uld_send_payload(payload):
    return any(
        key in payload
        for key in ("send_a2_count", "send_a1_count", "send_amp_count")
    )


@bp.route("/live-counts/state")
@gateway_node_required("sektor")
def live_counts_state():
    state = ballmat_state_payload(get_current_gateway())
    db.session.commit()
    return jsonify({"ok": True, "state": state})


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


def _visible_neosektor_menu_items():
    items = []
    for label, endpoint, view_permission in NEOSEKTOR_INTERNAL_MENU:
        if view_permission and not user_can(view_permission):
            continue
        items.append(
            {
                "label": label,
                "endpoint": endpoint,
                "active": request.endpoint == endpoint,
            }
        )
    return items


def _visible_neosektor_page_items():
    items = []
    for label, endpoint, view_permission, edit_permission, description in NEOSEKTOR_PAGES:
        if view_permission and not user_can(view_permission):
            continue
        items.append((label, endpoint, view_permission, edit_permission, description))
    return items


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
