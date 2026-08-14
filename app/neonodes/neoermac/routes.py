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

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoermac import bp
from app.services.access_control import get_current_gateway
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_destination_pull_times,
    get_departure_destination_choices,
    get_departure_destination_pull_times,
    get_outbound_door_options,
    lineup_field_name,
    save_building_lineup,
    save_building_lineup_destination,
)
from app.services.neoermac_door_view import (
    current_door_view_operation,
    delete_door_uld_request,
    door_tab_pull_alerts,
    door_view_context,
    door_view_poll_revision,
    door_view_uld_state,
    door_view_uld_workspace,
    edit_door_uld_request,
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


NEOERMAC_DASHBOARD_VIEW_PERMISSION = "neoermac.dashboard.view"
UPCOMING_PULLS_VIEW_PERMISSION = "neoermac.upcoming_pulls.view"
BUILDING_LINEUP_VIEW_PERMISSION = "neoermac.building_lineup.view"
BUILDING_LINEUP_EDIT_PERMISSION = "neoermac.building_lineup.edit"
DOOR_VIEW_VIEW_PERMISSION = "neoermac.door_view.view"
DOOR_VIEW_EDIT_PERMISSION = "neoermac.door_view.edit"
VIEW_OUTBOUND_VIEW_PERMISSION = "neoermac.view_outbound.view"
TUG_ASSIGNMENTS_VIEW_PERMISSION = "neoermac.tug_assignments.view"


NEOERMAC_PAGES = (
    ("UPCOMING PULLS", "neoermac.upcoming_pulls"),
    ("BUILDING LINEUP", "neoermac.building_lineup"),
    ("VIEW OUTBOUND", "neoermac.view_outbound"),
    ("DOOR VIEW", "neoermac.door_view"),
    ("TUG ASSIGNMENTS", "neoermac.tug_assignments"),
)


@bp.route("")
@gateway_node_required("ermac")
def index():
    access = permission_access(NEOERMAC_DASHBOARD_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    gateway = get_current_gateway()
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

    rows = get_building_lineup_rows(gateway)
    db.session.commit()
    return _building_lineup_response(gateway, access, rows=rows)


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
    revision = view_outbound_revision(gateway, operation=operation)
    refresh_status = view_outbound_refresh_status(gateway, operation=operation)
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

    context = view_outbound_context(
        gateway,
        operation=operation,
        refresh_status=refresh_status,
    )
    return render_template(
        "neonodes/neoermac/view_outbound.html",
        gateway=gateway,
        can_view=access["can_view"],
        outbound_revision=revision,
        **context,
    )


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
            return _door_view_response(gateway, access, selected_door, status_code=403)

        action = request.form.get("action")
        try:
            if action == "save_pulls":
                save_door_pulls(
                    gateway,
                    selected_door,
                    request.form,
                    supervised_doors=_current_user_supervised_doors(gateway),
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
            return _door_view_response(gateway, access, selected_door, status_code=400)

        db.session.commit()
        if selected_door:
            return redirect(url_for("neoermac.door_view", door=selected_door))
        return redirect(url_for("neoermac.door_view"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    response = _door_view_response(gateway, access, selected_door)
    db.session.commit()
    return response


@bp.route("/door-view/supervision", methods=["POST"])
@gateway_node_required("ermac")
def door_view_supervision():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION)
    if not access["can_view"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    base_context = door_view_context(gateway)
    try:
        supervision = save_door_supervision(
            current_user,
            base_context["operation"],
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
        state = door_view_uld_state(
            gateway,
            selected_door,
            supervised_doors=supervised_doors,
            requested_by_user_id=current_user.id,
            operation=operation,
            refresh_status=refresh_status,
            revision=revision,
            initialize_lineup=False,
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
        supervised_doors = _uld_workspace_doors(
            _current_user_supervised_doors(gateway),
            selected_door,
        )
        card = save_single_door_pull(
            gateway,
            selected_door,
            destination,
            pull_key,
            request.form.get("actual_pull", ""),
            request.form.get("no_pull") == "1",
            supervised_doors=supervised_doors,
        )
        state = door_view_uld_state(
            gateway,
            selected_door,
            supervised_doors=supervised_doors,
            requested_by_user_id=current_user.id,
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


def _current_user_supervised_doors(gateway):
    operation = door_view_context(gateway)["operation"]
    return supervised_doors_for_user(
        current_user,
        operation,
        get_outbound_door_options(),
    )


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
    )
    return response, status_code


def _door_view_response(gateway, access, selected_door, status_code=200):
    canonical_door_options = get_outbound_door_options()
    context = door_view_context(gateway, selected_door)
    supervision = door_supervision_for_user(
        current_user,
        context["operation"],
        canonical_door_options,
        requested_door=selected_door,
    )
    active_door = supervision["active_door"]
    if active_door != context["selected_door"]:
        context = door_view_context(gateway, active_door)
    context["door_tab_alerts"] = door_tab_pull_alerts(
        gateway,
        active_door,
        supervision["selected_doors"],
        operation=context["operation"],
    )
    workspace_doors = _uld_workspace_doors(
        supervision["selected_doors"],
        active_door,
    )
    workspace = door_view_uld_workspace(
        gateway,
        workspace_doors,
        current_user.id,
        operation=context["operation"],
    )
    context["uld_workspace"] = workspace
    context["uld_requests"] = workspace["requests"]
    context["on_the_way_events"] = workspace["on_the_way_events"]
    context["door_view_revision"] = (
        door_view_poll_revision(
            gateway,
            active_door,
            current_user.id,
            operation=context["operation"],
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
    return response


def _uld_workspace_doors(supervised_doors, active_door):
    """Keep a directly opened door relevant even before supervision persists."""
    doors = list(supervised_doors or ())
    normalized_active = normalize_door(active_door)
    if normalized_active and normalized_active not in doors:
        doors.append(normalized_active)
    return doors
