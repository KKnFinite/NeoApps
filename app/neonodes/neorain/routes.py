from datetime import datetime

from flask import (
    current_app,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError
from flask_login import current_user

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import MasterFlightSchedule, NeoRainCrewAdminAssignment, NeoRainDelayInfo, NeoRainLoadPlannerContact, NeoRainOutboundServiceState, SortDateMission, SortDateOperation, StaffingPerson
from app.neonodes.neorain import bp
from app.services.access_control import get_current_gateway
from app.neonodes.neorain.services import (
    NEORAIN_OUTBOUND_REFRESH_KEY,
    NEORAIN_INBOUND_REFRESH_KEY,
    NEORAIN_MUTABLE_MILESTONE_FIELDS,
    LoadPlannerAssignmentError,
    NeoRainMilestoneError,
    assign_current_sort_only_departure_load_planner,
    assign_master_departure_load_planner,
    current_neorain_outbound_operation,
    eligible_neorain_load_planners,
    mutate_neorain_departure_milestone,
    mutate_neorain_arrival_block_in,
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
from app.services.neorain_outbound_service_fields import (
    NeoRainOutboundServiceFieldError,
    apply_outbound_service_value,
    current_outbound_service_value,
    parse_outbound_service_field,
    serialize_outbound_service_state,
)
from app.services.live_collaboration import entity_version, version_conflict
from app.services.operation_lifecycle import current_existing_operational_sort_operations
from app.services.neoscorpion_assets import (
    lock_nightly_asset_scope_for_mutation,
    record_nightly_operational_change,
)
from app.services.neorain_load_planner_contacts import (
    neorain_load_planner_contact_values,
    neorain_load_planner_contacts,
    set_neorain_load_planner_contact,
)
from app.services.neorain_ground_time_settings import (
    neorain_ground_time_threshold_minutes,
    set_neorain_ground_time_threshold_minutes,
)
from app.services.permission_rules import permission_access, preload_permission_rules, user_can
from app.services.neorain_crew_admin import (
    NeoRainCrewAdminError, add_neorain_crew_admin_assignment,
    eligible_neorain_crew_admins, neorain_crew_admin_assignments,
    remove_neorain_crew_admin_assignment, update_neorain_crew_admin_assignment,
)
from app.services.neorain_delay_info import (
    NeoRainDelayInfoError, add_neorain_delay_info, update_neorain_delay_info,
    delete_neorain_delay_info,
)
from app.services.neorain_fuel_authority import (
    RAIN_FUEL_SOURCE_NEO,
    acknowledge_fuel_review,
    completed_scorpion_fuel_by_mission,
    rain_fuel_data_source,
)
from app.services.neorain_reports import (
    REPORT_FIELDS, briefing_text, report_context, save_report_entry,
)


NEORAIN_LAST_PAGE_SESSION_KEY = "neorain.last_page"


class _LoadPlannerStaleError(ValueError):
    """Keep form stale-edit failures distinct without exposing row internals."""

NEORAIN_PAGES = (
    ("Inbound", "neorain.inbound", "neorain.inbound.view", "neorain.inbound.edit"),
    ("Outbound", "neorain.outbound", "neorain.outbound.view", "neorain.outbound.edit"),
    ("Rainrock", "neorain.rainrock", "neorain.rainrock.view", "neorain.rainrock.edit"),
    ("Daily Briefing", "neorain.daily_briefing", "neorain.daily_briefing.view", None),
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


def _selected_report_operation(gateway):
    current = current_neorain_outbound_operation(gateway)
    selected_id = request.values.get("operation_id", type=int)
    if selected_id is None:
        return current, current
    operation = SortDateOperation.query.filter_by(id=selected_id).first()
    if operation is None or operation.gateway_code != gateway.code or (
        operation.gateway_id is not None and operation.gateway_id != gateway.id
    ):
        abort(404)
    return operation, current


def _report_choices(gateway):
    return (SortDateOperation.query.filter_by(gateway_code=gateway.code)
            .order_by(SortDateOperation.sort_date.desc(), SortDateOperation.id.desc())
            .limit(60).all())


@bp.route("/rainrock")
@gateway_node_required("rain")
def rainrock():
    access = permission_access("neorain.rainrock.view", "neorain.rainrock.edit")
    if not access["can_view"]:
        abort(403)
    gateway = get_current_gateway()
    operation, current = _selected_report_operation(gateway)
    session[NEORAIN_LAST_PAGE_SESSION_KEY] = "neorain.rainrock"
    is_current = bool(current and operation and current.id == operation.id)
    return render_template(
        "neonodes/neorain/rainrock.html", gateway=gateway, operation=operation,
        operations=_report_choices(gateway), can_edit=access["can_edit"],
        report=report_context(operation, current=is_current) if operation else None,
        is_current=is_current,
        report_fields=REPORT_FIELDS,
    )


@bp.route("/rainrock/entry", methods=["POST"])
@gateway_node_required("rain")
def rainrock_entry():
    if not user_can("neorain.rainrock.edit"):
        abort(403)
    gateway = get_current_gateway()
    operation, current = _selected_report_operation(gateway)
    if operation is None:
        abort(404)
    try:
        expected_version = int(request.form.get("expected_version", ""))
        save_report_entry(operation, request.form.get("field", ""),
                          request.form.get("value", ""), expected_version, current_user.id,
                          current=bool(current and operation.id == current.id))
        flash("Recap input saved.", "success")
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, ValueError) else "This recap input changed. Reload and try again.", "error")
    return redirect(url_for("neorain.rainrock", operation_id=operation.id))


@bp.route("/daily-briefing")
@gateway_node_required("rain")
def daily_briefing():
    if not user_can("neorain.daily_briefing.view"):
        abort(403)
    gateway = get_current_gateway()
    operation, current = _selected_report_operation(gateway)
    is_current = bool(current and operation and current.id == operation.id)
    report = report_context(operation, current=is_current) if operation else None
    session[NEORAIN_LAST_PAGE_SESSION_KEY] = "neorain.daily_briefing"
    return render_template(
        "neonodes/neorain/daily_briefing.html", gateway=gateway, operation=operation,
        operations=_report_choices(gateway), report=report,
        is_current=is_current,
        briefing=briefing_text(report) if report else "No selected sort.",
    )


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
        can_edit_crew_admin=user_can("neorain.crew_admin.edit"),
        crew_admin_assignments=neorain_crew_admin_assignments(operation),
        eligible_crew_admins=eligible_neorain_crew_admins(),
        inbound_revision=neorain_inbound_revision(gateway, operation=operation),
        refresh_status=neorain_inbound_refresh_status(gateway, operation=operation),
        **context,
    )


@bp.route("/inbound/crew-admin", methods=["POST"])
@gateway_node_required("rain")
def inbound_crew_admin():
    if not user_can("neorain.crew_admin.edit"):
        return "Access denied.", 403
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if operation is None:
        flash("No current sort.", "error")
        return redirect(url_for("neorain.inbound"))
    action = request.form.get("action")
    try:
        if action == "add":
            person = db.session.get(StaffingPerson, request.form.get("person_id", type=int))
            add_neorain_crew_admin_assignment(operation, person, request.form.getlist("ramps"), request.form.get("printer_number"), request.form.get("van_number"))
        else:
            row = db.session.execute(db.select(NeoRainCrewAdminAssignment).where(NeoRainCrewAdminAssignment.id == request.form.get("assignment_id", type=int), NeoRainCrewAdminAssignment.sort_date_operation_id == operation.id).with_for_update()).scalar_one_or_none()
            if row is None:
                raise NeoRainCrewAdminError("Crew Admin assignment was not found.")
            if request.form.get("expected_version") != entity_version(row):
                raise NeoRainCrewAdminError("Crew Admin assignment changed while you were editing.")
            if action == "remove":
                remove_neorain_crew_admin_assignment(row)
            elif action == "update":
                person = db.session.get(StaffingPerson, request.form.get("person_id", type=int))
                update_neorain_crew_admin_assignment(row, person, request.form.getlist("ramps"), request.form.get("printer_number"), request.form.get("van_number"))
            else:
                raise NeoRainCrewAdminError("Choose a valid Crew Admin action.")
        db.session.commit(); flash("CREW ADMIN SAVED.", "success")
    except (NeoRainCrewAdminError, IntegrityError) as exc:
        db.session.rollback(); flash(str(exc) if isinstance(exc, NeoRainCrewAdminError) else "Unable to save Crew Admin.", "error")
    return redirect(url_for("neorain.inbound"))


@bp.route("/delay-info", methods=["POST"])
@gateway_node_required("rain")
def delay_info():
    """One bounded form mutation shared by the two current-sort Rain boards."""
    board = str(request.form.get("board") or "").strip().lower()
    mission_type, endpoint, permission = {
        "inbound": ("arrival", "neorain.inbound", "neorain.inbound.edit"),
        "outbound": ("departure", "neorain.outbound", "neorain.outbound.edit"),
    }.get(board, (None, None, None))
    if permission is None or not user_can(permission):
        return "Access denied.", 403
    gateway = get_current_gateway(); operation = current_neorain_outbound_operation(gateway)
    mission_id = request.form.get("mission_id", type=int)
    if operation is None:
        flash("No current sort.", "error"); return redirect(url_for(endpoint))
    mission = SortDateMission.query.filter_by(id=mission_id, sort_date_operation_id=operation.id, gateway_code=gateway.code, mission_type=mission_type).one_or_none()
    if mission is None:
        flash("Mission is not in the current sort.", "error"); return redirect(url_for(endpoint))
    action = request.form.get("action")
    try:
        if action == "add":
            add_neorain_delay_info(mission, request.form.get("minutes"), request.form.get("code"), request.form.get("notes"))
        else:
            row = db.session.execute(db.select(NeoRainDelayInfo).where(NeoRainDelayInfo.id == request.form.get("delay_id", type=int), NeoRainDelayInfo.sort_date_mission_id == mission.id).with_for_update()).scalar_one_or_none()
            if row is None: raise NeoRainDelayInfoError("Delay Info was not found for this mission.")
            conflict = version_conflict(row, request.form.get("expected_version"))
            if conflict: raise NeoRainDelayInfoError(conflict["message"])
            if action == "update": update_neorain_delay_info(mission, row, request.form.get("minutes"), request.form.get("code"), request.form.get("notes"))
            elif action == "delete": delete_neorain_delay_info(mission, row)
            else: raise NeoRainDelayInfoError("Choose a valid Delay Info action.")
        db.session.commit(); flash("DELAY INFO SAVED.", "success")
    except (NeoRainDelayInfoError, IntegrityError) as exc:
        db.session.rollback(); flash(str(exc) if isinstance(exc, NeoRainDelayInfoError) else "Unable to save Delay Info.", "error")
    anchor = f"mobile-flight-{mission_id}" if request.form.get("return_view") == "mobile" else f"delay-info-{mission_id}"
    return redirect(f"{url_for(endpoint)}#{anchor}")


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


@bp.route("/inbound/block-in", methods=["POST"])
@gateway_node_required("rain")
def inbound_block_in():
    """Save one current-sort arrival Block-In for the mobile compact board."""
    page = _neorain_page("neorain.inbound")
    access = permission_access(page[2], page[3])
    if not access["can_edit"]:
        return _json_error("access_denied", "Edit access denied.", 403)
    payload = request.get_json(silent=True)
    expected_keys = {"mission_id", "value", "expected_version"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        return _json_error(
            "invalid_request",
            "Provide only mission_id, value, and expected_version.",
            400,
        )
    mission_id = _positive_integer(payload.get("mission_id"))
    expected_version = str(payload.get("expected_version") or "").strip()
    if mission_id is None or not expected_version:
        return _json_error(
            "invalid_request", "Provide a valid current arrival mission version.", 400
        )
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    if operation is None or operation.gateway_id != gateway.id:
        return _json_error("no_current_sort", "No current sort.", 409)
    mission = (
        SortDateMission.query.filter_by(
            id=mission_id,
            sort_date_operation_id=operation.id,
            gateway_code=gateway.code,
            mission_type="arrival",
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if mission is None:
        return _json_error(
            "mission_not_found", "Arrival mission is not in the current sort.", 404
        )
    conflict = version_conflict(mission, expected_version)
    if conflict:
        row = neorain_inbound_row(mission, operation)
        db.session.rollback()
        return jsonify(
            {
                "ok": False,
                "code": "stale_version",
                "error": conflict["message"],
                "row": row,
            }
        ), 409
    try:
        result = mutate_neorain_arrival_block_in(mission, operation, payload["value"])
    except NeoRainMilestoneError as exc:
        db.session.rollback()
        status = 409 if "owned by" in str(exc).lower() else 400
        return _json_error(
            "ownership_conflict" if status == 409 else "invalid_block_in",
            str(exc),
            status,
        )
    try:
        if result["changed"]:
            db.session.commit()
        else:
            db.session.rollback()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "NeoRain Block-In save failed safely: mission_id=%s", mission_id
        )
        return _json_error("save_failed", "NeoRain could not save Block-In.", 500)
    return jsonify(
        {
            "ok": True,
            "changed": result["changed"],
            "row": neorain_inbound_row(mission, operation),
            "late_summary": neorain_inbound_late_summary(operation),
            "revision": neorain_inbound_revision(gateway, operation=operation),
        }
    )


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
        can_edit_service_fields=(
            access["can_view"]
            and user_can("neorain.outbound.service_fields.edit")
        ),
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


@bp.route("/outbound/service-field", methods=["POST"])
@gateway_node_required("rain")
def outbound_service_field():
    """Save operator-owned Outbound values in every Rain milestone authority mode."""
    if not (
        user_can("neorain.outbound.view")
        and user_can("neorain.outbound.service_fields.edit")
    ):
        return _json_error("access_denied", "Operator edit access required.", 403)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {
        "mission_id", "field", "value", "expected_version",
    }:
        return _json_error("invalid_request", "Invalid Outbound service field request.", 400)
    mission_id = _positive_integer(payload["mission_id"])
    expected_version = payload["expected_version"]
    if (
        mission_id is None
        or type(expected_version) is not int
        or expected_version < 0
        or not isinstance(payload["field"], str)
    ):
        return _json_error("invalid_request", "Provide a valid mission, field and version.", 400)
    field = payload["field"]
    try:
        normalized = parse_outbound_service_field(field, payload["value"])
    except NeoRainOutboundServiceFieldError as exc:
        return _json_error("invalid_service_field", str(exc), 400)

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
        return _json_error("mission_not_found", "Departure mission is not in the current sort.", 404)

    state = (
        NeoRainOutboundServiceState.query.filter_by(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    actual_version = int(state.revision or 1) if state is not None else 0
    if expected_version != actual_version:
        latest = serialize_outbound_service_state(state)
        db.session.rollback()
        return jsonify({
            "ok": False,
            "code": "stale_version",
            "error": "These Outbound details changed. Review the latest values.",
            "service": latest,
        }), 409

    changed = current_outbound_service_value(state, field) != normalized
    if changed:
        if state is None:
            state = NeoRainOutboundServiceState(
                sort_date_operation_id=operation.id,
                sort_date_mission_id=mission.id,
                revision=1,
            )
            db.session.add(state)
        else:
            state.revision = actual_version + 1
        apply_outbound_service_value(state, field, normalized)
        state.updated_by_user_id = current_user.id
        state.updated_at = datetime.utcnow()
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            latest = NeoRainOutboundServiceState.query.filter_by(
                sort_date_operation_id=operation.id,
                sort_date_mission_id=mission_id,
            ).one_or_none()
            return jsonify({
                "ok": False,
                "code": "stale_version",
                "error": "These Outbound details changed. Review the latest values.",
                "service": serialize_outbound_service_state(latest),
            }), 409
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "NeoRain Outbound service field save failed: mission_id=%s", mission_id
            )
            return _json_error("save_failed", "Unable to save Outbound details.", 500)

    values = serialize_outbound_service_state(state)
    if not changed:
        db.session.rollback()
    response = jsonify({
        "ok": True,
        "changed": changed,
        "service": values,
        "revision": neorain_outbound_revision(gateway, operation=operation),
    })
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


@bp.route("/outbound/fuel-reviewed", methods=["POST"])
@gateway_node_required("rain")
def outbound_fuel_reviewed():
    """Acknowledge one exact, published NeoScorpion fuel revision globally."""
    page = _neorain_page("neorain.outbound")
    access = permission_access(page[2], page[3])
    if not access["can_edit"]:
        flash("Edit access is required to review fuel data.", "error")
        return redirect(url_for("neorain.outbound"))
    gateway = get_current_gateway()
    operation = current_neorain_outbound_operation(gateway)
    mission_id = _positive_integer(request.form.get("mission_id"))
    requested_revision = str(request.form.get("fuel_revision") or "").strip()
    if operation is None or mission_id is None:
        flash("The current Rain mission is unavailable.", "error")
        return redirect(url_for("neorain.outbound"))
    if rain_fuel_data_source(gateway, operation.sort_name) != RAIN_FUEL_SOURCE_NEO:
        flash("Fuel review is available only while Rain Fuel Data Source is NEO.", "error")
        return redirect(url_for("neorain.outbound"))
    mission = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
        mission_type="departure",
    ).with_for_update().one_or_none()
    current = completed_scorpion_fuel_by_mission(operation).get(mission_id)
    if mission is None or current is None or current["revision"] != requested_revision:
        db.session.rollback()
        flash("Fuel data changed before it could be reviewed. Please review the current values.", "error")
        return redirect(url_for("neorain.outbound"))
    try:
        acknowledge_fuel_review(operation, mission.id, requested_revision, current_user)
        db.session.commit()
        flash("Fuel data review recorded for this mission.", "success")
    except Exception:
        db.session.rollback()
        current_app.logger.exception("NeoRain fuel review failed safely: mission_id=%s", mission_id)
        flash("NeoRain could not record the fuel review.", "error")
    return redirect(url_for("neorain.outbound"))


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
        is_planner_contact = request.form.get("action") == "save_planner_contact"
        try:
            if is_planner_contact:
                saved = _save_neorain_load_planner_contact(gateway)
            else:
                saved = _save_load_planner_assignment(gateway, operation)
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
        except IntegrityError:
            db.session.rollback()
            return _render_load_planner_lineup(
                gateway,
                operation,
                access,
                status_code=409,
                message=("The Load Planner contact changed. Refresh and try again.", "error"),
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
        if is_planner_contact:
            flash("LOAD PLANNER CONTACT SAVED.", "success")
        else:
            flash(f"LOAD PLANNER ASSIGNMENT SAVED FOR {saved.flight_number}.", "success")
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
        # Lock affected live operations before the departure, matching Dispatch's
        # operation -> child order. Historical sorts do not need a live refresh.
        dispatch_scopes = _master_load_planner_dispatch_scopes(gateway, departure_id)
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
        previous_planner_id = departure.load_planner_person_id
        assign_master_departure_load_planner(departure, planner)
        if departure.load_planner_person_id != previous_planner_id:
            for dispatch_operation, state in dispatch_scopes:
                record_nightly_operational_change(state, dispatch_operation.id)
        return departure

    if operation is None or operation.gateway_id != gateway.id:
        raise ValueError("No current sort.")
    dispatch_operation, state = lock_nightly_asset_scope_for_mutation(operation)
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
    previous_planner_id = departure.load_planner_person_id
    assign_current_sort_only_departure_load_planner(departure, planner)
    if departure.load_planner_person_id != previous_planner_id:
        record_nightly_operational_change(state, dispatch_operation.id)
    return departure


def _master_load_planner_dispatch_scopes(gateway, master_departure_id):
    """Publish only to live operations linked by canonical Master Schedule ID."""
    operations = current_existing_operational_sort_operations(gateway)
    if not operations:
        return []
    linked_operation_ids = {
        operation_id for (operation_id,) in db.session.query(
            SortDateMission.sort_date_operation_id
        ).filter(
            SortDateMission.sort_date_operation_id.in_(op.id for op in operations),
            SortDateMission.master_flight_schedule_id == master_departure_id,
            SortDateMission.mission_type == "departure",
        ).distinct().all()
    }
    return [
        lock_nightly_asset_scope_for_mutation(operation)
        for operation in sorted(operations, key=lambda op: op.id)
        if operation.id in linked_operation_ids
    ]


def _save_neorain_load_planner_contact(gateway):
    """Lock and stage one gateway-scoped eligible planner contact."""
    planner_id = _positive_integer(request.form.get("planner_person_id"))
    expected_version = str(request.form.get("expected_version") or "").strip()
    if planner_id is None:
        raise ValueError("Choose a valid Load Planner.")
    if "extension" not in request.form or "radio_channel" not in request.form:
        raise ValueError("Provide both Extension and Radio Channel.")
    planner = db.session.get(StaffingPerson, planner_id)
    if planner is None or planner.id not in {
        person.id for person in eligible_neorain_load_planners()
    }:
        raise ValueError("Choose an active eligible Load Planner.")
    locked = (
        NeoRainLoadPlannerContact.query.filter_by(
            gateway_id=gateway.id,
            staffing_person_id=planner.id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if locked is not None and not expected_version:
        raise ValueError("Contact version is required.")
    conflict = version_conflict(locked, expected_version) if locked else None
    if conflict is not None:
        raise _LoadPlannerStaleError(conflict["message"])
    return set_neorain_load_planner_contact(
        gateway,
        planner,
        extension=request.form.get("extension"),
        radio_channel=request.form.get("radio_channel"),
        contact=locked,
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
    eligible_load_planners = eligible_neorain_load_planners()
    contact_by_planner_id = neorain_load_planner_contacts(
        gateway,
        eligible_load_planners,
    )
    planner_contacts = tuple(
        {
            "planner": planner,
            "contact": contact_by_planner_id.get(planner.id),
            "values": neorain_load_planner_contact_values(
                contact_by_planner_id.get(planner.id)
            ),
            "version": (
                entity_version(contact_by_planner_id[planner.id])
                if planner.id in contact_by_planner_id
                else ""
            ),
        }
        for planner in eligible_load_planners
    )
    return render_template(
        "neonodes/neorain/load_planner_lineup.html",
        can_edit=access["can_edit"],
        can_view=access["can_view"],
        gateway=gateway,
        operation=operation,
        eligible_load_planners=eligible_load_planners,
        planner_contacts=planner_contacts,
        lineup=neorain_load_planner_lineup(gateway, operation),
    ), status_code


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("rain")
def settings():
    page = _neorain_page("neorain.settings")
    access = permission_access(page[2], page[3])
    gateway = get_current_gateway()

    if request.method == "POST":
        action = request.form.get("action")
        if not access["can_view"] or not access["can_edit"]:
            db.session.rollback()
            response = _render_neorain_settings(
                gateway,
                access,
                status_code=403,
                message=("Access denied.", "error"),
            )
            return response
        if action != "save_ground_time_threshold":
            return _render_neorain_settings(
                gateway,
                access,
                status_code=400,
                message=("Choose a valid NeoRain settings action.", "error"),
            )
        try:
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
                status_code=400,
                message=(message, "error"),
            )
        if result.changed:
            db.session.commit()
            flash(
                "GROUND TIME THRESHOLD SAVED.",
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
    )


def _render_neorain_settings(
    gateway,
    access,
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
        can_edit_ground_time=access["can_edit"],
        ground_time_threshold_minutes=neorain_ground_time_threshold_minutes(gateway),
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
