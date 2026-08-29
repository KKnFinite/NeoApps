from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import SortDateMission
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.neonodes.neorain.services import (
    NEORAIN_MUTABLE_MILESTONE_FIELDS,
    NeoRainMilestoneError,
    current_neorain_outbound_operation,
    mutate_neorain_departure_milestone,
    neorain_departure_milestone_value,
    neorain_outbound_context,
    neorain_outbound_row,
    neorain_outbound_refresh_status,
    neorain_outbound_revision,
)
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    rain_integration_mode,
)
from app.services.google_rain_sheets import (
    GoogleRainWriterError,
    write_google_rain_departure_milestone,
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


@bp.route("/outbound/milestone", methods=["POST"])
@gateway_node_required("rain")
def outbound_milestone():
    """Mutate one current-sort Rain milestone under the configured authority mode."""
    page = _neorain_page("neorain.outbound")
    access = permission_access(page[2], page[3])
    if not access["can_edit"]:
        return _json_error("access_denied", "Edit access denied.", 403)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("invalid_request", "A JSON milestone request is required.", 400)
    unexpected_keys = set(payload) - {"mission_id", "field", "value"}
    if unexpected_keys or not {"mission_id", "field", "value"}.issubset(payload):
        return _json_error(
            "invalid_request",
            "Provide only mission_id, field, and value.",
            400,
        )

    field = str(payload.get("field") or "").strip().lower()
    if field not in NEORAIN_MUTABLE_MILESTONE_FIELDS:
        return _json_error(
            "unsupported_field",
            "Choose a valid NeoRain milestone.",
            400,
        )
    mission_id = _positive_integer(payload.get("mission_id"))
    if mission_id is None:
        return _json_error("invalid_mission", "Choose a valid departure mission.", 400)

    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if operation is None or operation.gateway_id != gateway.id:
        return _json_error("no_current_sort", "No current sort.", 409)

    mission = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        gateway_code=gateway.code,
        mission_type="departure",
    ).one_or_none()
    if mission is None:
        return _json_error(
            "mission_not_found",
            "Departure mission is not in the current sort.",
            404,
        )

    mode = rain_integration_mode(gateway, operation.sort_name)
    if mode == GOOGLE_PRIMARY:
        return _json_error(
            "google_primary",
            "Google Rain is authoritative for outbound milestones.",
            409,
        )

    previous_value = neorain_departure_milestone_value(mission, field)
    try:
        mutation = mutate_neorain_departure_milestone(
            mission,
            operation,
            field,
            payload.get("value"),
        )
    except NeoRainMilestoneError as exc:
        db.session.rollback()
        status_code = 409 if "owned" in str(exc).lower() else 400
        error_code = "ownership_conflict" if status_code == 409 else "invalid_milestone"
        return _json_error(error_code, str(exc), status_code)

    if mode == NEO_PRIMARY_GOOGLE_MIRROR:
        try:
            write_google_rain_departure_milestone(
                mission,
                field,
                mutation["value"],
                operation=operation,
            )
        except GoogleRainWriterError as exc:
            db.session.rollback()
            current_app.logger.warning(
                "NeoRain Google mirror rejected: mission_id=%s field=%s code=%s",
                mission_id,
                field,
                exc.code,
            )
            return _json_error(
                "google_mirror_failed",
                "Google Rain could not be updated. Neo was not changed.",
                502,
            )
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error(
                "NeoRain Google mirror failed safely: mission_id=%s field=%s error=%s",
                mission_id,
                field,
                type(exc).__name__,
            )
            return _json_error(
                "google_mirror_failed",
                "Google Rain could not be updated. Neo was not changed.",
                502,
            )
    elif mode != NEO_ONLY:
        db.session.rollback()
        return _json_error("invalid_mode", "NeoRain authority mode is invalid.", 409)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "NeoRain milestone commit failed safely: mission_id=%s field=%s error=%s",
            mission_id,
            field,
            type(exc).__name__,
        )
        if mode == NEO_PRIMARY_GOOGLE_MIRROR:
            _restore_google_rain_milestone(
                mission,
                operation,
                field,
                previous_value,
            )
        return _json_error(
            "save_failed",
            "NeoRain could not save the milestone.",
            500,
        )

    row = neorain_outbound_row(mission, operation)
    return jsonify(
        {
            "ok": True,
            "mode": mode,
            "changed": mutation["changed"],
            "field": field,
            "source": mutation["source"],
            "row": row,
        }
    )


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


def _restore_google_rain_milestone(mission, operation, field, previous_value):
    try:
        write_google_rain_departure_milestone(
            mission,
            field,
            previous_value,
            operation=operation,
        )
    except Exception as exc:
        current_app.logger.error(
            "NeoRain Google compensation failed: mission_id=%s field=%s error=%s",
            mission.id,
            field,
            type(exc).__name__,
        )


def _positive_integer(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_error(code, message, status_code):
    return jsonify({"ok": False, "code": code, "error": message}), status_code


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
