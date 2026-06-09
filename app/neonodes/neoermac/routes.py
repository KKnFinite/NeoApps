from flask import flash, redirect, render_template, request, url_for

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoermac import bp
from app.services.access_control import get_current_gateway
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS,
    get_building_lineup_rows,
    get_departure_destination_choices,
    lineup_field_name,
    save_building_lineup,
)
from app.services.permission_rules import user_can


NEOERMAC_PAGES = (
    ("BUILDING LINEUP", "neoermac.building_lineup"),
    ("VIEW OUTBOUND", "neoermac.outbound"),
    ("DOOR VIEW", "neoermac.door_view"),
    ("TUG ASSIGNMENTS", "neoermac.tug_assignments"),
)


@bp.route("")
@gateway_node_required("ermac")
def index():
    return render_template(
        "neonodes/neoermac/index.html",
        gateway=get_current_gateway(),
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
    can_edit = user_can("neoermac.building_lineup.edit")

    if request.method == "POST":
        if not can_edit:
            db.session.rollback()
            flash("Access denied.", "error")
            return _building_lineup_response(gateway, can_edit, status_code=403)

        try:
            save_building_lineup(gateway, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _building_lineup_response(gateway, can_edit, status_code=400)

        db.session.commit()
        flash("BUILDING LINEUP SAVED.", "success")
        return redirect(url_for("neoermac.building_lineup"))

    rows = get_building_lineup_rows(gateway)
    db.session.commit()
    return _building_lineup_response(gateway, can_edit, rows=rows)


@bp.route("/outbound")
@gateway_node_required("ermac")
def outbound():
    return _placeholder_page("VIEW OUTBOUND")


@bp.route("/door-view")
@gateway_node_required("ermac")
def door_view():
    return _placeholder_page("DOOR VIEW")


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


def _building_lineup_response(gateway, can_edit, rows=None, status_code=200):
    rows = rows or get_building_lineup_rows(gateway)
    destination_choices = get_departure_destination_choices(gateway)
    response = render_template(
        "neonodes/neoermac/building_lineup.html",
        gateway=gateway,
        rows=rows,
        destination_choices=destination_choices,
        destination_fields=DESTINATION_FIELDS,
        field_name=lineup_field_name,
        can_edit=can_edit,
    )
    return response, status_code
