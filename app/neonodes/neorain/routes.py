from flask import flash, jsonify, redirect, render_template, request, session, url_for

from app.auth.decorators import gateway_node_required
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.neonodes.neorain.services import (
    current_neorain_outbound_operation,
    neorain_outbound_context,
    neorain_outbound_refresh_status,
    neorain_outbound_revision,
)
from app.services.permission_rules import permission_access, preload_permission_rules, user_can


NEORAIN_LAST_PAGE_SESSION_KEY = "neorain.last_page"

NEORAIN_PAGES = (
    ("Inbound", "neorain.inbound", "neorain.inbound.view", "neorain.inbound.edit"),
    ("Outbound", "neorain.outbound", "neorain.outbound.view", "neorain.outbound.edit"),
    (
        "Load Planner Lineup",
        "neorain.load_planner_lineup",
        "neorain.load_planner_lineup.view",
        "neorain.load_planner_lineup.edit",
    ),
    ("Settings", "neorain.settings", "neorain.settings.view", "neorain.settings.edit"),
)


@bp.context_processor
def inject_neorain_navigation():
    return {"neorain_menu_items": _visible_neorain_menu_items}


@bp.route("")
@gateway_node_required("rain")
def index():
    endpoint = _last_valid_neorain_endpoint()
    if not endpoint:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))
    return redirect(url_for(endpoint))


@bp.route("/")
@gateway_node_required("rain")
def index_slash():
    return index()


@bp.route("/inbound")
@gateway_node_required("rain")
def inbound():
    return _render_neorain_page("neorain.inbound")


@bp.route("/outbound")
@gateway_node_required("rain")
def outbound():
    page = _neorain_page("neorain.outbound")
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neorain.index"))

    session[NEORAIN_LAST_PAGE_SESSION_KEY] = page[1]
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    context = neorain_outbound_context(gateway, operation=operation)
    return render_template(
        "neonodes/neorain/outbound.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        gateway=gateway,
        outbound_revision=neorain_outbound_revision(gateway, operation=operation),
        **context,
    )


@bp.route("/outbound/revision")
@gateway_node_required("rain")
def outbound_revision():
    page = _neorain_page("neorain.outbound")
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        response = jsonify({"ok": False, "error": "Access denied."})
        response.status_code = 403
        response.headers["Cache-Control"] = "no-store"
        return response

    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    revision = neorain_outbound_revision(gateway, operation=operation)
    refresh = neorain_outbound_refresh_status(gateway, operation=operation)
    response = jsonify(
        {
            "ok": True,
            "changed": str(request.args.get("revision") or "") != revision,
            "revision": revision,
            "refresh": refresh,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/load-planner-lineup")
@gateway_node_required("rain")
def load_planner_lineup():
    return _render_neorain_page("neorain.load_planner_lineup")


@bp.route("/settings")
@gateway_node_required("rain")
def settings():
    return _render_neorain_page("neorain.settings")


def _render_neorain_page(endpoint):
    page = _neorain_page(endpoint)
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neorain.index"))

    session[NEORAIN_LAST_PAGE_SESSION_KEY] = endpoint
    return render_template(
        "neonodes/neorain/workspace.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        page_label=page[0],
    )


def _last_valid_neorain_endpoint():
    visible_pages = _visible_neorain_menu_items()
    visible_endpoints = {item["endpoint"] for item in visible_pages}
    remembered = session.get(NEORAIN_LAST_PAGE_SESSION_KEY)
    if remembered in visible_endpoints:
        return remembered
    return visible_pages[0]["endpoint"] if visible_pages else None


def _visible_neorain_menu_items():
    _preload_neorain_permissions()
    return [
        {
            "label": label,
            "endpoint": endpoint,
            "active": endpoint == _request_endpoint(),
        }
        for label, endpoint, view_permission, _edit_permission in NEORAIN_PAGES
        if user_can(view_permission)
    ]


def _preload_neorain_permissions():
    preload_permission_rules(page[2] for page in NEORAIN_PAGES)


def _neorain_page(endpoint):
    for page in NEORAIN_PAGES:
        if page[1] == endpoint:
            return page
    raise ValueError(f"Unknown NeoRain page: {endpoint}")


def _request_endpoint():
    return request.endpoint
