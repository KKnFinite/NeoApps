from flask import flash, jsonify, make_response, redirect, render_template, request, url_for

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
    delete_door_uld_request,
    door_view_context,
    door_view_uld_state,
    edit_door_uld_request,
    save_door_pulls,
    save_single_door_pull,
    save_uld_request,
)
from app.services.neoermac_dashboard import neoermac_dashboard_context
from app.services.neoermac_view_outbound import view_outbound_context
from app.services.permission_rules import permission_access


BUILDING_LINEUP_VIEW_PERMISSION = "neoermac.building_lineup.view"
BUILDING_LINEUP_EDIT_PERMISSION = "neoermac.building_lineup.edit"
DOOR_VIEW_VIEW_PERMISSION = "neoermac.door_view.view"
DOOR_VIEW_EDIT_PERMISSION = "neoermac.door_view.edit"
VIEW_OUTBOUND_VIEW_PERMISSION = "neoermac.view_outbound.view"


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
    gateway = get_current_gateway()
    dashboard_context = neoermac_dashboard_context(gateway)
    db.session.commit()
    return render_template(
        "neonodes/neoermac/upcoming_pulls.html",
        gateway=gateway,
        dashboard_context=dashboard_context,
        menu_items=NEOERMAC_PAGES,
    )


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

    context = view_outbound_context(gateway)
    return render_template(
        "neonodes/neoermac/view_outbound.html",
        gateway=gateway,
        can_view=access["can_view"],
        **context,
    )


@bp.route("/door-view", methods=["GET", "POST"])
@gateway_node_required("ermac")
def door_view():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    selected_door = request.values.get("door", "")

    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _door_view_response(gateway, access, selected_door, status_code=403)

        action = request.form.get("action")
        try:
            if action == "save_pulls":
                save_door_pulls(gateway, selected_door, request.form)
                flash("DOOR PULLS SAVED.", "success")
            elif action == "save_uld_request":
                save_uld_request(gateway, selected_door, request.form)
                flash("ULD REQUEST UPDATED.", "success")
            elif action == "edit_uld_request":
                edit_door_uld_request(gateway, selected_door, request.form)
                flash("ULD REQUEST EDITED.", "success")
            elif action == "delete_uld_request":
                delete_door_uld_request(gateway, selected_door, request.form)
                flash("ULD REQUEST CANCELLED.", "success")
            else:
                raise ValueError("Unknown Door View action.")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _door_view_response(gateway, access, selected_door, status_code=400)

        db.session.commit()
        return redirect(url_for("neoermac.door_view", door=selected_door))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoermac.index"))

    db.session.commit()
    return _door_view_response(gateway, access, selected_door)


@bp.route("/door-view/state")
@gateway_node_required("ermac")
def door_view_state():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    if not access["can_view"]:
        return jsonify({"ok": False, "error": "Access denied."}), 403

    try:
        state = door_view_uld_state(gateway, request.args.get("door", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, "state": state})


@bp.route("/door-view/pull-autosave", methods=["POST"])
@gateway_node_required("ermac")
def door_view_pull_autosave():
    gateway = get_current_gateway()
    access = permission_access(DOOR_VIEW_VIEW_PERMISSION, DOOR_VIEW_EDIT_PERMISSION)
    if not access["can_edit"]:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Access denied."}), 403

    try:
        card = save_single_door_pull(
            gateway,
            request.form.get("door", ""),
            request.form.get("destination", ""),
            request.form.get("pull_key", ""),
            request.form.get("actual_pull", ""),
            request.form.get("no_pull") == "1",
        )
    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(exc)}), 400

    db.session.commit()
    return jsonify({"ok": True, "card": card})


@bp.route("/tug-assignments")
@gateway_node_required("ermac")
def tug_assignments():
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
    context = door_view_context(gateway, selected_door)
    response = make_response(
        render_template(
            "neonodes/neoermac/door_view.html",
            gateway=gateway,
            can_view=access["can_view"],
            can_edit=access["can_edit"],
            canonical_door_options=get_outbound_door_options(),
            **context,
        ),
        status_code,
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
