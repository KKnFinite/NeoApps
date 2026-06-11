from flask import flash, redirect, render_template, request, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import NeoErmacBuildingLineup
from app.neonodes.neoermac import bp
from app.services.access_control import get_current_gateway
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_departure_destination_choices,
    lineup_field_name,
    save_building_lineup,
)
from app.services.neoermac_door_view import (
    door_view_context,
    save_door_pulls,
    save_uld_request,
)
from app.services.permission_rules import permission_access


BUILDING_LINEUP_VIEW_PERMISSION = "neoermac.building_lineup.view"
BUILDING_LINEUP_EDIT_PERMISSION = "neoermac.building_lineup.edit"
DOOR_VIEW_VIEW_PERMISSION = "neoermac.door_view.view"
DOOR_VIEW_EDIT_PERMISSION = "neoermac.door_view.edit"


NEOERMAC_PAGES = (
    ("BUILDING LINEUP", "neoermac.building_lineup"),
    ("VIEW OUTBOUND", "neoermac.outbound"),
    ("DOOR VIEW", "neoermac.door_view"),
    ("TUG ASSIGNMENTS", "neoermac.tug_assignments"),
)


@bp.route("")
@gateway_node_required("ermac")
def index():
    gateway = get_current_gateway()
    lineup_summary = _building_lineup_summary(gateway)
    return render_template(
        "neonodes/neoermac/index.html",
        gateway=gateway,
        lineup_summary=lineup_summary,
        menu_items=NEOERMAC_PAGES,
    )


@bp.route("/")
@gateway_node_required("ermac")
def index_slash():
    return redirect(url_for("neoermac.index"))


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


@bp.route("/outbound")
@gateway_node_required("ermac")
def outbound():
    return _placeholder_page("VIEW OUTBOUND")


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


def _building_lineup_summary(gateway):
    rows = NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    assigned_slots = sum(
        1
        for row in rows
        for field_name in DESTINATION_FIELDS
        if getattr(row, field_name)
    )
    return {
        "runout_count": len(rows),
        "assigned_slots": assigned_slots,
    }


def _building_lineup_response(gateway, access, rows=None, status_code=200):
    rows = rows or get_building_lineup_rows(gateway)
    destination_choices = get_departure_destination_choices(gateway)
    response = render_template(
        "neonodes/neoermac/building_lineup.html",
        gateway=gateway,
        rows=rows,
        destination_choices=destination_choices,
        destination_fields=DESTINATION_FIELDS,
        field_name=lineup_field_name,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
    )
    return response, status_code


def _door_view_response(gateway, access, selected_door, status_code=200):
    context = door_view_context(gateway, selected_door)
    response = render_template(
        "neonodes/neoermac/door_view.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **context,
    )
    return response, status_code
