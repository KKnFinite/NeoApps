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
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission, SortDateOperation, StaffingPerson
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.neonodes.neorain.services import (
    NEORAIN_OUTBOUND_REFRESH_KEY,
    NEORAIN_MUTABLE_MILESTONE_FIELDS,
    LoadPlannerAssignmentError,
    NeoRainMilestoneError,
    assign_current_sort_only_departure_load_planner,
    assign_master_departure_load_planner,
    current_neorain_outbound_operation,
    eligible_neorain_load_planners,
    mutate_neorain_departure_milestone,
    neorain_departure_milestone_value,
    neorain_outbound_context,
    neorain_inbound_context,
    neorain_inbound_revision,
    neorain_inbound_refresh_status,
    neorain_inbound_row,
    neorain_inbound_late_summary,
    neorain_outbound_late_summary,
    neorain_outbound_row,
    neorain_outbound_refresh_status,
    neorain_outbound_revision,
    neorain_load_planner_lineup,
    set_neorain_late_metrics_included,
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
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_value,
    save_live_screen_refresh_override,
)
from app.services.live_collaboration import entity_version, version_conflict
from app.services.load_planning_contact import (
    current_load_planning_contact,
    set_load_planning_contact,
)
from app.services.neorain_ground_time_settings import (
    neorain_ground_time_threshold_minutes,
    set_neorain_ground_time_threshold_minutes,
)
from app.services.permission_rules import permission_access, preload_permission_rules, user_can


NEORAIN_LAST_PAGE_SESSION_KEY = "neorain.last_page"


class _LoadPlannerStaleError(ValueError):
    """Keep form stale-edit failures distinct without exposing row internals."""

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
    page = _neorain_page("neorain.inbound")
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neorain.index"))
    session[NEORAIN_LAST_PAGE_SESSION_KEY] = page[1]
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    context = neorain_inbound_context(gateway, operation=operation)
    return render_template(
        "neonodes/neorain/inbound.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        inbound_revision=neorain_inbound_revision(gateway, operation=operation),
        refresh_status=neorain_inbound_refresh_status(gateway, operation=operation),
        **context,
    )


@bp.route("/inbound/revision")
@gateway_node_required("rain")
def inbound_revision():
    page = _neorain_page("neorain.inbound")
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    revision = neorain_inbound_revision(gateway, operation=operation)
    response = jsonify({
        "ok": True,
        "changed": str(request.args.get("revision") or "") != revision,
        "revision": revision,
        "refresh": neorain_inbound_refresh_status(gateway, operation=operation),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/inbound/late-inclusion", methods=["POST"])
@gateway_node_required("rain")
def inbound_late_inclusion():
    page = _neorain_page("neorain.inbound")
    access = permission_access(page[2], page[3])
    if not access["can_edit"]:
        return _json_error("access_denied", "Edit access denied.", 403)
    payload = request.get_json(silent=True)
    expected_keys = {"mission_id", "included", "expected_version"}
    if not isinstance(payload, dict) or set(payload) != expected_keys or type(payload.get("included")) is not bool:
        return _json_error("invalid_request", "Provide only mission_id, included, and expected_version.", 400)
    expected_version = str(payload["expected_version"] or "").strip()
    mission_id = _positive_integer(payload["mission_id"])
    if not expected_version or mission_id is None:
        return _json_error("invalid_request", "Provide a valid current arrival mission version.", 400)
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if operation is None or operation.gateway_id != gateway.id:
        return _json_error("no_current_sort", "No current sort.", 409)
    mission = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        gateway_code=gateway.code,
        mission_type="arrival",
    ).populate_existing().with_for_update().one_or_none()
    if mission is None:
        return _json_error("mission_not_found", "Arrival mission is not in the current sort.", 404)
    conflict = version_conflict(mission, expected_version)
    if conflict:
        row = neorain_inbound_row(mission, operation)
        db.session.rollback()
        return jsonify({"ok": False, "code": "stale_version", "error": conflict["message"], "row": row}), 409
    result = set_neorain_late_metrics_included(mission, payload["included"])
    if result["changed"]:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception("NeoRain inbound late-inclusion save failed: mission_id=%s", mission_id)
            return _json_error("save_failed", "NeoRain could not save late-metrics inclusion.", 500)
    row = neorain_inbound_row(mission, operation)
    if not result["changed"]:
        db.session.rollback()
    return jsonify({
        "ok": True,
        "changed": result["changed"],
        "late_metrics_included": result["included"],
        "late_metrics_inclusion_source": result["source"],
        "version": entity_version(mission),
        "row": row,
        "late_summary": neorain_inbound_late_summary(operation),
        "revision": neorain_inbound_revision(gateway, operation=operation),
    })


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
    integration_mode = (
        rain_integration_mode(gateway, operation.sort_name)
        if operation is not None
        else GOOGLE_PRIMARY
    )
    return render_template(
        "neonodes/neorain/outbound.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        can_edit_timestamp_milestones=(
            access["can_edit"]
            and integration_mode in {NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY}
        ),
        gateway=gateway,
        rain_integration_mode=integration_mode,
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
    expected_keys = {"mission_id", "field", "value", "expected_version"}
    unexpected_keys = set(payload) - expected_keys
    if unexpected_keys or not expected_keys.issubset(payload):
        return _json_error(
            "invalid_request",
            "Provide only mission_id, field, value, and expected_version.",
            400,
        )
    expected_version = str(payload.get("expected_version") or "").strip()
    if not expected_version:
        return _json_error(
            "invalid_request",
            "Provide the current mission version.",
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

    mission_query = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        gateway_code=gateway.code,
        mission_type="departure",
    )
    mode = rain_integration_mode(gateway, operation.sort_name)
    if mode != GOOGLE_PRIMARY:
        mission_query = mission_query.populate_existing().with_for_update()
    mission = mission_query.one_or_none()
    if mission is None:
        return _json_error(
            "mission_not_found",
            "Departure mission is not in the current sort.",
            404,
        )

    if mode == GOOGLE_PRIMARY:
        return _json_error(
            "google_primary",
            "Google Rain is authoritative for outbound milestones.",
            409,
        )

    conflict = version_conflict(mission, expected_version)
    if conflict:
        row = neorain_outbound_row(mission, operation)
        db.session.rollback()
        return jsonify(
            {
                "ok": False,
                "code": "stale_version",
                "error": conflict["message"],
                "row": row,
            }
        ), 409

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
            "version": entity_version(mission),
            "row": row,
            "late_summary": neorain_outbound_late_summary(operation),
            "revision": neorain_outbound_revision(gateway, operation=operation),
        }
    )


@bp.route("/outbound/late-inclusion", methods=["POST"])
@gateway_node_required("rain")
def outbound_late_inclusion():
    """Persist one current-sort departure's Neo-only late-metrics override."""
    page = _neorain_page("neorain.outbound")
    access = permission_access(page[2], page[3])
    if not access["can_edit"]:
        return _json_error("access_denied", "Edit access denied.", 403)

    payload = request.get_json(silent=True)
    expected_keys = {"mission_id", "included", "expected_version"}
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or type(payload.get("included")) is not bool
    ):
        return _json_error(
            "invalid_request",
            "Provide only mission_id, included, and expected_version.",
            400,
        )
    expected_version = str(payload["expected_version"] or "").strip()
    if not expected_version:
        return _json_error(
            "invalid_request", "Provide the current mission version.", 400
        )
    mission_id = _positive_integer(payload["mission_id"])
    if mission_id is None:
        return _json_error("invalid_mission", "Choose a valid departure mission.", 400)

    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if operation is None or operation.gateway_id != gateway.id:
        return _json_error("no_current_sort", "No current sort.", 409)
    mission = (
        SortDateMission.query.filter_by(
            id=mission_id,
            sort_date_operation_id=operation.id,
            gateway_code=gateway.code,
            mission_type="departure",
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if mission is None:
        return _json_error(
            "mission_not_found", "Departure mission is not in the current sort.", 404
        )
    conflict = version_conflict(mission, expected_version)
    if conflict:
        row = neorain_outbound_row(mission, operation)
        db.session.rollback()
        return jsonify(
            {
                "ok": False,
                "code": "stale_version",
                "error": conflict["message"],
                "row": row,
            }
        ), 409

    result = set_neorain_late_metrics_included(mission, payload["included"])
    if result["changed"]:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception(
                "NeoRain late-inclusion commit failed safely: mission_id=%s error=%s",
                mission_id,
                type(exc).__name__,
            )
            return _json_error(
                "save_failed", "NeoRain could not save late-metrics inclusion.", 500
            )
    row = neorain_outbound_row(mission, operation)
    if not result["changed"]:
        db.session.rollback()
    return jsonify(
        {
            "ok": True,
            "changed": result["changed"],
            "late_metrics_included": result["included"],
            "late_metrics_inclusion_source": result["source"],
            "version": entity_version(mission),
            "row": row,
            "late_summary": neorain_outbound_late_summary(operation),
            "revision": neorain_outbound_revision(gateway, operation=operation),
        }
    )


@bp.route("/load-planner-lineup", methods=["GET", "POST"])
@gateway_node_required("rain")
def load_planner_lineup():
    page = _neorain_page("neorain.load_planner_lineup")
    access = permission_access(page[2], page[3])
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neorain.index"))

    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            return _render_load_planner_lineup(
                gateway,
                operation,
                access,
                status_code=403,
                message=("Edit access denied.", "error"),
            )
        is_contact = request.form.get("action") == "save_contact"
        try:
            if is_contact:
                departure = _save_load_planning_contact(gateway, operation)
            else:
                departure = _save_load_planner_assignment(gateway, operation)
            db.session.commit()
        except _LoadPlannerStaleError as exc:
            db.session.rollback()
            return _render_load_planner_lineup(
                gateway,
                operation,
                access,
                status_code=409,
                message=(str(exc), "error"),
            )
        except (LoadPlannerAssignmentError, ValueError) as exc:
            db.session.rollback()
            return _render_load_planner_lineup(
                gateway,
                operation,
                access,
                status_code=400,
                message=(str(exc), "error"),
            )
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception(
                "NeoRain Load Planner save failed safely: error=%s",
                type(exc).__name__,
            )
            return _render_load_planner_lineup(
                gateway,
                operation,
                access,
                status_code=500,
                message=("NeoRain could not save the Load Planner assignment.", "error"),
            )
        if is_contact:
            flash("LOAD PLANNING CONTACT SAVED.", "success")
        else:
            flash(f"LOAD PLANNER ASSIGNMENT SAVED FOR {departure.flight_number}.", "success")
        return redirect(url_for("neorain.load_planner_lineup"))

    return _render_load_planner_lineup(gateway, operation, access)


def _save_load_planner_assignment(gateway, operation):
    scope = str(request.form.get("assignment_scope") or "").strip()
    departure_id = _positive_integer(request.form.get("departure_id"))
    expected_version = str(request.form.get("expected_version") or "").strip()
    planner_value = str(request.form.get("planner_person_id") or "").strip()
    if scope not in {"master", "current_sort"}:
        raise ValueError("Choose a valid Load Planner assignment scope.")
    if departure_id is None or not expected_version:
        raise ValueError("Choose a valid current departure assignment.")
    planner_id = None
    if planner_value:
        planner_id = _positive_integer(planner_value)
        if planner_id is None:
            raise ValueError("Choose a valid Load Planner.")
    planner = db.session.get(StaffingPerson, planner_id) if planner_id else None
    if planner_id and planner is None:
        raise ValueError("Choose an eligible Load Planner.")

    if scope == "master":
        departure = (
            MasterFlightSchedule.query.filter_by(
                id=departure_id,
                gateway_code=gateway.code,
                sort_name="night",
                mission_type="departure",
                active=True,
            )
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if departure is None:
            raise ValueError("Master departure is not available for this gateway.")
        conflict = version_conflict(departure, expected_version)
        if conflict:
            raise _LoadPlannerStaleError(conflict["message"])
        assign_master_departure_load_planner(departure, planner)
        return departure

    if operation is None or operation.gateway_id != gateway.id:
        raise ValueError("No current sort.")
    departure = (
        SortDateMission.query.filter_by(
            id=departure_id,
            sort_date_operation_id=operation.id,
            gateway_code=gateway.code,
            mission_type="departure",
            master_flight_schedule_id=None,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if departure is None:
        raise ValueError("Current-sort-only departure is not available.")
    conflict = version_conflict(departure, expected_version)
    if conflict:
        raise _LoadPlannerStaleError(conflict["message"])
    assign_current_sort_only_departure_load_planner(departure, planner)
    return departure


def _save_load_planning_contact(gateway, operation):
    """Lock and stage the current sort's shared Load Planning contact."""
    if operation is None or operation.gateway_id != gateway.id:
        raise ValueError("No current sort.")
    expected_version = str(request.form.get("expected_version") or "").strip()
    if not expected_version:
        raise ValueError("Provide the current sort version.")
    if "extension" not in request.form or "radio_channel" not in request.form:
        raise ValueError("Provide both Extension and Radio Channel.")
    locked = (
        SortDateOperation.query.filter_by(
            id=operation.id,
            gateway_id=gateway.id,
            gateway_code=gateway.code,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked is None:
        raise ValueError("No current sort.")
    conflict = version_conflict(locked, expected_version)
    if conflict:
        raise _LoadPlannerStaleError(conflict["message"])
    return set_load_planning_contact(
        locked,
        extension=request.form.get("extension"),
        radio_channel=request.form.get("radio_channel"),
    )


def _render_load_planner_lineup(
    gateway,
    operation,
    access,
    *,
    status_code=200,
    message=None,
):
    session[NEORAIN_LAST_PAGE_SESSION_KEY] = "neorain.load_planner_lineup"
    if message:
        flash(*message)
    return render_template(
        "neonodes/neorain/load_planner_lineup.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        gateway=gateway,
        operation=operation,
        load_planning_contact=current_load_planning_contact(operation),
        contact_version=entity_version(operation) if operation is not None else "",
        eligible_load_planners=eligible_neorain_load_planners(),
        lineup=neorain_load_planner_lineup(gateway, operation),
    ), status_code


NEORAIN_REFRESH_SETTINGS_EDIT_PERMISSION = "neorain.refresh_settings.edit"


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("rain")
def settings():
    page = _neorain_page("neorain.settings")
    access = permission_access(page[2], page[3])
    can_edit_refresh_settings = user_can(NEORAIN_REFRESH_SETTINGS_EDIT_PERMISSION)
    gateway = get_current_gateway()

    if request.method == "POST":
        action = request.form.get("action")
        may_edit = (
            can_edit_refresh_settings if action == "save_live_refresh" else access["can_edit"]
        )
        if not access["can_view"] or not may_edit:
            db.session.rollback()
            response = _render_neorain_settings(
                gateway,
                access,
                can_edit_refresh_settings,
                status_code=403,
                message=("Access denied.", "error"),
            )
            return response
        if action not in {"save_live_refresh", "save_ground_time_threshold"}:
            return _render_neorain_settings(
                gateway,
                access,
                can_edit_refresh_settings,
                status_code=400,
                message=("Choose a valid NeoRain settings action.", "error"),
            )
        try:
            if action == "save_live_refresh":
                result = save_live_screen_refresh_override(
                    gateway,
                    request.form.get("screen_key"),
                    request.form.get("refresh_interval_seconds"),
                    allowed_screen_keys=(NEORAIN_OUTBOUND_REFRESH_KEY,),
                )
            else:
                set_neorain_ground_time_threshold_minutes(
                    gateway, request.form.get("ground_time_threshold_minutes")
                )
                result = type("Result", (), {"changed": True})()
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Unable to save live refresh setting."
            )
            return _render_neorain_settings(
                gateway,
                access,
                can_edit_refresh_settings,
                status_code=400,
                message=(message, "error"),
            )
        if result.changed:
            db.session.commit()
            flash(
                "LIVE REFRESH SETTING SAVED." if action == "save_live_refresh" else "GROUND TIME THRESHOLD SAVED.",
                "success",
            )
        else:
            db.session.rollback()
            flash("NO LIVE REFRESH SETTING CHANGES.", "info")
        return redirect(url_for("neorain.settings"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neorain.index"))
    return _render_neorain_settings(
        gateway,
        access,
        can_edit_refresh_settings,
    )


def _render_neorain_settings(
    gateway,
    access,
    can_edit_refresh_settings,
    *,
    status_code=200,
    message=None,
):
    session[NEORAIN_LAST_PAGE_SESSION_KEY] = "neorain.settings"
    if message:
        flash(*message)
    response = render_template(
        "neonodes/neorain/settings.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        page_label="Settings",
        can_edit_refresh_settings=can_edit_refresh_settings,
        can_edit_ground_time=access["can_edit"],
        refresh_settings=[live_screen_refresh_value(gateway, NEORAIN_OUTBOUND_REFRESH_KEY)],
        ground_time_threshold_minutes=neorain_ground_time_threshold_minutes(gateway),
        live_refresh_allowed_seconds=LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    )
    return response, status_code


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
