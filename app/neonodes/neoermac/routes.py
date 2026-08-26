from flask import (
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoermac import bp
from app.services.access_control import (
    access_initialization_changed_this_request,
    get_current_gateway,
)
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_destination_pull_times,
    get_departure_destination_choices,
    get_departure_destination_pull_times,
    get_outbound_door_options,
    load_building_lineup_rows,
    lineup_field_name,
    save_building_lineup,
    save_building_lineup_destination,
)
from app.services.neoermac_door_view import (
    current_door_view_operation,
    delete_door_uld_request,
    door_tab_pull_alerts,
    door_view_context,
    door_view_operational_state,
    door_view_poll_revision,
    door_view_uld_state,
    door_view_uld_workspace,
    edit_door_uld_request,
    linked_supervised_pull_doors,
    normalize_door,
    save_door_pulls,
    save_single_door_pull,
    save_uld_request,
    neoermac_refresh_status,
)
from app.services.neoermac_door_supervision import (
    door_supervision_for_user,
    save_door_supervision,
    supervised_doors_for_user,
)
from app.services.neoermac_dashboard import (
    current_upcoming_pulls_operation,
    neoermac_dashboard_context,
    upcoming_pulls_refresh_status,
    upcoming_pulls_revision,
)
from app.services.neoermac_view_outbound import (
    current_view_outbound_operation,
    view_outbound_context,
    view_outbound_refresh_status,
    view_outbound_revision,
)
from app.services.permission_rules import permission_access
from app.services.permission_rules import user_can
from app.services import neostaffing as staffing_service
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_value,
    save_live_screen_refresh_override,
)
from app.services.neoermac_live_refresh import NEOERMAC_LIVE_REFRESH_KEY


NEOERMAC_DASHBOARD_VIEW_PERMISSION = "neoermac.dashboard.view"
UPCOMING_PULLS_VIEW_PERMISSION = "neoermac.upcoming_pulls.view"
BUILDING_LINEUP_VIEW_PERMISSION = "neoermac.building_lineup.view"
BUILDING_LINEUP_EDIT_PERMISSION = "neoermac.building_lineup.edit"
DOOR_VIEW_VIEW_PERMISSION = "neoermac.door_view.view"
DOOR_VIEW_EDIT_PERMISSION = "neoermac.door_view.edit"
VIEW_OUTBOUND_VIEW_PERMISSION = "neoermac.view_outbound.view"
TUG_ASSIGNMENTS_VIEW_PERMISSION = "neoermac.tug_assignments.view"
SETTINGS_VIEW_PERMISSION = "neoermac.settings.view"
REFRESH_SETTINGS_EDIT_PERMISSION = "neoermac.refresh_settings.edit"


NEOERMAC_PAGES = (
    ("UPCOMING PULLS", "neoermac.upcoming_pulls"),
    ("BUILDING LINEUP", "neoermac.building_lineup"),
    ("VIEW OUTBOUND", "neoermac.view_outbound"),
    ("DOOR VIEW", "neoermac.door_view"),
    ("TUG ASSIGNMENTS", "neoermac.tug_assignments"),
    ("SETTINGS", "neoermac.settings"),
)


@bp.route("")
@gateway_node_required("ermac")
def index():
    access = permission_access(NEOERMAC_DASHBOARD_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    gateway = get_current_gateway()
    if access_initialization_changed_this_request():
        db.session.commit()
    return render_template(
        "neonodes/neoermac/index.html",
        gateway=gateway,
        menu_items=NEOERMAC_PAGES,
    )


@bp.route("/")
@gateway_node_required("ermac")
def index_slash():
    return redirect(url_for("neoermac.index"))


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("ermac")
def settings():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION)
    can_edit = user_can(REFRESH_SETTINGS_EDIT_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))
    if request.method == "POST":
        if not can_edit:
            db.session.rollback()
            flash("Access denied.", "error")
            return redirect(url_for("neoermac.settings"))
        try:
            result = save_live_screen_refresh_override(
                gateway,
                NEOERMAC_LIVE_REFRESH_KEY,
                request.form.get("refresh_interval_seconds"),
                allowed_screen_keys=(NEOERMAC_LIVE_REFRESH_KEY,),
            )
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            flash(
                str(exc) if isinstance(exc, ValueError) else "Live refresh setting changed. Reload Settings and try again.",
                "error",
            )
            return redirect(url_for("neoermac.settings"))
        if result.changed:
            db.session.commit()
            flash("LIVE REFRESH SETTING SAVED.", "success")
        else:
            flash("NO LIVE REFRESH SETTING CHANGES.", "info")
        return redirect(url_for("neoermac.settings"))
    return render_template(
        "neonodes/neoermac/settings.html",
        gateway=gateway,
        can_edit_refresh_settings=can_edit,
        refresh_setting=live_screen_refresh_value(gateway, NEOERMAC_LIVE_REFRESH_KEY),
        live_refresh_allowed_seconds=LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    )


@bp.route("/upcoming-pulls")
@gateway_node_required("ermac")
def upcoming_pulls():
    access = permission_access(UPCOMING_PULLS_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    gateway = get_current_gateway()
    operation = current_upcoming_pulls_operation(gateway)
    refresh_status = upcoming_pulls_refresh_status(gateway, operation=operation)
    dashboard_context = neoermac_dashboard_context(
        gateway,
        operation=operation,
        refresh_status=refresh_status,
    )
    revision = upcoming_pulls_revision(gateway, operation=operation)
    if (
        dashboard_context.pop("_initialization_changed", False)
        or access_initialization_changed_this_request()
    ):
        db.session.commit()
    return render_template(
        "neonodes/neoermac/upcoming_pulls.html",
        gateway=gateway,
        dashboard_context=dashboard_context,
        upcoming_pulls_revision=revision,
        menu_items=NEOERMAC_PAGES,
    )


@bp.route("/upcoming-pulls/state")
@gateway_node_required("ermac")
def upcoming_pulls_state():
    gateway = get_current_gateway()
    access = permission_access(UPCOMING_PULLS_VIEW_PERMISSION)
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    operation = current_upcoming_pulls_operation(gateway)
    refresh_status = upcoming_pulls_refresh_status(gateway, operation=operation)
    client_revision = str(request.args.get("revision") or "").strip()
    if not client_revision:
        response = jsonify(
            {
                "ok": False,
                "changed": False,
                "refresh": refresh_status,
                "error": "Upcoming Pulls live state revision is required. Reload the page.",
                "reload_required": True,
            }
        )
        response.status_code = 428
        response.headers["Cache-Control"] = "no-store"
        return response

    revision = upcoming_pulls_revision(gateway, operation=operation)
    if client_revision == revision:
        response = jsonify(
            {
                "ok": True,
                "changed": False,
                "revision": revision,
                "refresh": refresh_status,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    dashboard_context = neoermac_dashboard_context(
        gateway,
        operation=operation,
        refresh_status=refresh_status,
        initialize_lineup=False,
    )
    response = jsonify(
        {
            "ok": True,
            "changed": True,
            "revision": revision,
            "refresh": refresh_status,
            "board_html": current_app.jinja_env.get_template(
                "neonodes/neoermac/_upcoming_pulls_board.html"
            ).render(
                dashboard_context=dashboard_context,
            ),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/building-lineup", methods=["GET", "POST"])
@gateway_node_required("ermac")
def building_lineup():
    gateway = get_current_gateway()
    access = permission_access(
        BUILDING_LINEUP_VIEW_PERMISSION,
        BUILDING_LINEUP_EDIT_PERMISSION,
    )

    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _building_lineup_response(gateway, access, status_code=403)

        try:
            save_building_lineup(gateway, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _building_lineup_response(gateway, access, status_code=400)

        db.session.commit()
        flash("BUILDING LINEUP SAVED.", "success")
        return redirect(url_for("neoermac.building_lineup"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    lineup_load = load_building_lineup_rows(gateway)
    if (
        lineup_load.persistent_state_changed
        or access_initialization_changed_this_request()
    ):
        db.session.commit()
    return _building_lineup_response(gateway, access, rows=lineup_load.rows)


@bp.route("/building-lineup/destination", methods=["POST"])
@gateway_node_required("ermac")
def building_lineup_destination_autosave():
    gateway = get_current_gateway()
    access = permission_access(
        BUILDING_LINEUP_VIEW_PERMISSION,
        BUILDING_LINEUP_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Access denied."}), 403

    try:
        result = save_building_lineup_destination(
            gateway,
            request.form.get("field", ""),
            request.form.get("destination", ""),
        )
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, **result})


@bp.route("/building-lineup/state")
@gateway_node_required("ermac")
def building_lineup_state():
    gateway = get_current_gateway()
    access = permission_access(BUILDING_LINEUP_VIEW_PERMISSION)
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403
    operation = current_upcoming_pulls_operation(gateway)
    revision = upcoming_pulls_revision(gateway, operation=operation)
    return jsonify(
        {
            "ok": True,
            "changed": str(request.args.get("revision") or "") != revision,
            "revision": revision,
            "refresh": upcoming_pulls_refresh_status(gateway, operation=operation),
        }
    )


@bp.route("/outbound")
@gateway_node_required("ermac")
def outbound():
    return redirect(url_for("neoermac.view_outbound"))


@bp.route("/view-outbound")
@gateway_node_required("ermac")
def view_outbound():
    gateway = get_current_gateway()
    access = permission_access(VIEW_OUTBOUND_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    operation = current_view_outbound_operation(gateway)
    refresh_status = view_outbound_refresh_status(gateway, operation=operation)
    context = view_outbound_context(
        gateway,
        operation=operation,
        refresh_status=refresh_status,
    )
    revision = view_outbound_revision(gateway, operation=operation)
    return render_template(
        "neonodes/neoermac/view_outbound.html",
        gateway=gateway,
        can_view=access["can_view"],
        outbound_revision=revision,
        **context,
    )


@bp.route("/view-outbound/state")
@gateway_node_required("ermac")
def view_outbound_state():
    gateway = get_current_gateway()
    access = permission_access(VIEW_OUTBOUND_VIEW_PERMISSION)
    if not access["can_view"]:
        response = jsonify({"ok": False, "error": "Access denied."})
        response.status_code = 403
        response.headers["Cache-Control"] = "no-store"
        return response

    operation = current_view_outbound_operation(gateway)
    refresh_status = view_outbound_refresh_status(gateway, operation=operation)
    client_revision = str(request.args.get("revision") or "").strip()
    if not client_revision:
        response = jsonify(
            {
                "ok": False,
                "changed": False,
                "refresh": refresh_status,
                "error": "View Outbound live state revision is required. Reload the page.",
                "reload_required": True,
            }
        )
        response.status_code = 428
        response.headers["Cache-Control"] = "no-store"
        return response

    revision = view_outbound_revision(gateway, operation=operation)
    if client_revision == revision:
        response = jsonify(
            {
                "ok": True,
                "changed": False,
                "revision": revision,
                "refresh": refresh_status,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    context = view_outbound_context(
        gateway,
        operation=operation,
        refresh_status=refresh_status,
        initialize_lineup=False,
    )
    response = jsonify(
        {
            "ok": True,
            "changed": True,
            "revision": revision,
            "refresh": refresh_status,
            "content_html": current_app.jinja_env.get_template(
                "neonodes/neoermac/_view_outbound_content.html"
            ).render(rows=context["rows"]),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/door-view", methods=["GET", "POST"])
@gateway_node_required("ermac")
def door_view():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    selected_door = (
        request.args.get("door", "")
        or request.form.get("active_door", "")
        or request.form.get("door", "")
    )

    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            response, _changed = _door_view_response(
                gateway,
                access,
                selected_door,
                status_code=403,
            )
            return response

        action = request.form.get("action")
        try:
            if action == "save_pulls":
                save_door_pulls(
                    gateway,
                    selected_door,
                    request.form,
                    supervised_doors=_current_user_supervised_doors(gateway),
                    apply_to_both=_apply_pulls_to_both(request.form.get("apply_to_both")),
                )
                flash("DOOR PULLS SAVED.", "success")
            elif action == "save_uld_request":
                request_door = request.form.get("request_door", selected_door)
                save_uld_request(
                    gateway,
                    request_door,
                    request.form,
                    requested_by_user_id=current_user.id,
                )
                flash("ULD REQUEST UPDATED.", "success")
            elif action == "edit_uld_request":
                request_door = request.form.get("request_door", selected_door)
                edit_door_uld_request(gateway, request_door, request.form)
                flash("ULD REQUEST EDITED.", "success")
            elif action == "delete_uld_request":
                request_door = request.form.get("request_door", selected_door)
                delete_door_uld_request(gateway, request_door, request.form)
                flash("ULD REQUEST CANCELLED.", "success")
            else:
                raise ValueError("Unknown Door View action.")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            response, _changed = _door_view_response(
                gateway,
                access,
                selected_door,
                status_code=400,
            )
            return response

        db.session.commit()
        if selected_door:
            return redirect(url_for("neoermac.door_view", door=selected_door))
        return redirect(url_for("neoermac.door_view"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    response, persistent_state_changed = _door_view_response(
        gateway,
        access,
        selected_door,
    )
    if (
        persistent_state_changed
        or access_initialization_changed_this_request()
    ):
        db.session.commit()
    return response


@bp.route("/door-view/manage-employees", methods=["GET", "POST"])
@gateway_node_required("ermac")
def manage_employees():
    gateway = get_current_gateway()
    doors = _current_user_supervised_doors(gateway)
    area_ids = staffing_service.attendance_deep_link_work_area_ids(doors)
    if not area_ids and request.method == "GET":
        return redirect(url_for("neostaffing.attendance"))
    can_edit = user_can("neostaffing.attendance.take")
    if request.method == "POST":
        if not can_edit:
            flash("Access denied.", "error")
        else:
            try:
                saved = staffing_service.save_operational_manage_attendance(
                    request.form, current_user, area_ids
                )
                db.session.commit()
                flash(f"Attendance saved for {saved} people.", "success")
            except (ValueError, IntegrityError) as exc:
                db.session.rollback()
                flash(str(getattr(exc, "orig", None) or exc), "error")
        return redirect(url_for("neoermac.manage_employees"))
    context = staffing_service.operational_manage_employees_context(
        area_ids, later_final_area_ids=area_ids
    )
    return render_template(
        "neostaffing/operational_manage_employees.html",
        title="EMPLOYEE ATTENDANCE",
        attendance=context,
        can_edit_attendance=can_edit,
        show_coming=True,
        area_tabs=(),
        attendance_scope_label=f"Selected Doors: {' · '.join(doors)}",
        attendance_workspace="ermac",
        back_url=url_for("neoermac.door_view"),
    )


@bp.route("/door-view/supervision", methods=["POST"])
@gateway_node_required("ermac")
def door_view_supervision():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION)
    if not access["can_view"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    operation = current_door_view_operation(gateway)
    try:
        supervision = save_door_supervision(
            current_user,
            operation,
            request.form.getlist("doors"),
            get_outbound_door_options(),
            active_door=request.form.get("active_door", ""),
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("neoermac.door_view"))

    db.session.commit()
    active_door = supervision["active_door"]
    if active_door:
        return redirect(url_for("neoermac.door_view", door=active_door))
    return redirect(url_for("neoermac.door_view"))


@bp.route("/door-view/state")
@gateway_node_required("ermac")
def door_view_state():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    try:
        selected_door = request.args.get("door", "")
        operation = current_door_view_operation(gateway)
        revision = door_view_poll_revision(
            gateway,
            selected_door,
            current_user.id,
            operation=operation,
        )
        refresh_status = neoermac_refresh_status(gateway, operation=operation)
        client_revision = str(request.args.get("revision") or "").strip()
        if client_revision and client_revision == revision:
            return jsonify(
                {
                    "ok": True,
                    "changed": False,
                    "revision": revision,
                    "refresh": refresh_status,
                }
            )
        supervised_doors = _uld_workspace_doors(
            supervised_doors_for_user(
                current_user,
                operation,
                get_outbound_door_options(),
            ),
            selected_door,
        )
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=False,
        )
        state = door_view_uld_state(
            gateway,
            selected_door,
            supervised_doors=supervised_doors,
            requested_by_user_id=current_user.id,
            operation=operation,
            refresh_status=refresh_status,
            revision=revision,
            initialize_lineup=False,
            bundle=bundle,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify(
        {
            "ok": True,
            "changed": True,
            "revision": revision,
            "state": state,
        }
    )


@bp.route("/door-view/pull-autosave", methods=["POST"])
@gateway_node_required("ermac")
def door_view_pull_autosave():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    if not access["can_edit"]:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Access denied."}), 403

    selected_door = request.form.get("door", "")
    destination = request.form.get("destination", "")
    pull_key = request.form.get("pull_key", "")
    try:
        operation = current_door_view_operation(gateway)
        supervised_doors = _uld_workspace_doors(
            _current_user_supervised_doors(gateway, operation=operation),
            selected_door,
        )
        bundle = door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=True,
        )
        card = save_single_door_pull(
            gateway,
            selected_door,
            destination,
            pull_key,
            request.form.get("actual_pull", ""),
            request.form.get("no_pull") == "1",
            supervised_doors=supervised_doors,
            apply_to_both=_apply_pulls_to_both(request.form.get("apply_to_both")),
            operation=operation,
            bundle=bundle,
        )
        state = door_view_uld_state(
            gateway,
            selected_door,
            supervised_doors=supervised_doors,
            requested_by_user_id=current_user.id,
            operation=operation,
            bundle=bundle,
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        error_code, field = _pull_autosave_validation_details(exc)
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
                "error_code": error_code,
                "field": field,
            }
        ), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "NeoErmac Door View pull autosave failed "
            "gateway_id=%s door=%s destination=%s pull_key=%s",
            gateway.id,
            str(selected_door or "").strip().upper(),
            str(destination or "").strip().upper(),
            str(pull_key or "").strip().lower(),
        )
        return jsonify(
            {
                "ok": False,
                "error": "Pull could not be saved. Refresh the door and try again.",
                "error_code": "pull_save_failed",
                "field": "",
            }
        ), 500

    return jsonify({"ok": True, "card": card, "state": state})


def _current_user_supervised_doors(gateway, operation=None):
    operation = operation or current_door_view_operation(gateway)
    return supervised_doors_for_user(
        current_user,
        operation,
        get_outbound_door_options(),
    )


def _apply_pulls_to_both(value):
    """Only the explicit compact Door View request value enables propagation."""
    return str(value or "").strip() == "1"


def _pull_autosave_validation_details(error):
    message = str(error)
    if "HH:MM" in message:
        return "invalid_pull_time", "actual_pull"
    if "door" in message.lower():
        return "invalid_door", "door"
    if "destination" in message.lower() or "assigned" in message.lower():
        return "invalid_destination", "destination"
    if "pull type" in message.lower():
        return "invalid_pull_type", "pull_key"
    return "invalid_pull_request", ""


@bp.route("/tug-assignments")
@gateway_node_required("ermac")
def tug_assignments():
    access = permission_access(TUG_ASSIGNMENTS_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    return _placeholder_page("TUG ASSIGNMENTS")


def _placeholder_page(title):
    return render_template(
        "neonodes/neoermac/placeholder.html",
        gateway=get_current_gateway(),
        title=title,
    )


def _building_lineup_response(gateway, access, rows=None, status_code=200):
    rows = rows or get_building_lineup_rows(gateway)
    destination_choices = get_departure_destination_choices(gateway)
    pull_time_lookup = get_departure_destination_pull_times(gateway)
    response = render_template(
        "neonodes/neoermac/building_lineup.html",
        gateway=gateway,
        rows=rows,
        destination_choices=destination_choices,
        pull_time_lookup=pull_time_lookup,
        empty_pull_times=get_destination_pull_times(gateway, ""),
        destination_fields=DESTINATION_FIELDS,
        field_name=lineup_field_name,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        refresh_status=upcoming_pulls_refresh_status(
            gateway,
            operation=current_upcoming_pulls_operation(gateway),
        ),
        building_lineup_revision=upcoming_pulls_revision(gateway),
    )
    return response, status_code


def _door_view_response(gateway, access, selected_door, status_code=200):
    canonical_door_options = get_outbound_door_options()
    operation = current_door_view_operation(gateway)
    supervision = door_supervision_for_user(
        current_user,
        operation,
        canonical_door_options,
        requested_door=selected_door,
    )
    active_door = supervision["active_door"]
    bundle = (
        door_view_operational_state(
            gateway,
            operation=operation,
            initialize_lineup=True,
        )
        if active_door
        else None
    )
    persistent_state_changed = supervision["persistent_state_changed"] or bool(
        bundle and bundle.initialization_changed
    )
    context = door_view_context(gateway, active_door, bundle=bundle)
    context["linked_supervised_pull_doors"] = tuple(
        door
        for destination in context["destinations"]
        for door in linked_supervised_pull_doors(
            gateway,
            active_door,
            destination["destination"],
            supervision["selected_doors"],
            bundle=bundle,
        )
    )
    context["linked_supervised_pull_doors"] = tuple(
        dict.fromkeys(context["linked_supervised_pull_doors"])
    )
    context["door_tab_alerts"] = door_tab_pull_alerts(
        gateway,
        active_door,
        supervision["selected_doors"],
        operation=operation,
        bundle=bundle,
    )
    workspace_doors = _uld_workspace_doors(
        supervision["selected_doors"],
        active_door,
    )
    workspace = door_view_uld_workspace(
        gateway,
        workspace_doors,
        current_user.id,
        operation=operation,
        bundle=bundle,
    )
    context["uld_workspace"] = workspace
    context["uld_requests"] = workspace["requests"]
    context["on_the_way_events"] = workspace["on_the_way_events"]
    context["staffing_attendance_work_area_ids"] = (
        staffing_service.attendance_deep_link_work_area_ids(
            supervision["selected_doors"],
            operation,
        )
    )
    context["door_view_revision"] = (
        door_view_poll_revision(
            gateway,
            active_door,
            current_user.id,
            operation=operation,
        )
        if active_door
        else ""
    )
    response = make_response(
        render_template(
            "neonodes/neoermac/door_view.html",
            gateway=gateway,
            can_view=access["can_view"],
            can_edit=access["can_edit"],
            canonical_door_options=canonical_door_options,
            supervised_doors=supervision["selected_doors"],
            **context,
        ),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response, persistent_state_changed


def _uld_workspace_doors(supervised_doors, active_door):
    """Keep a directly opened door relevant even before supervision persists."""
    doors = list(supervised_doors or ())
    normalized_active = normalize_door(active_door)
    if normalized_active and normalized_active not in doors:
        doors.append(normalized_active)
    return doors
