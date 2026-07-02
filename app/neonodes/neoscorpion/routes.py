from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoscorpion import bp
from app.services.access_control import get_current_gateway
from app.services.neoscorpion import (
    CALCULATION_NOT_CONFIGURED_MESSAGE,
    deactivate_truck,
    fuel_dispatch_context,
    fueler_context,
    history_context,
    save_dispatch_row,
    save_fueler_entry,
    save_settings,
    save_truck,
    settings_context,
    truck_manager_context,
    visible_neoscorpion_menu_items,
)
from app.services.permission_rules import permission_access, user_can


FUEL_DISPATCH_VIEW_PERMISSION = "neoscorpion.fuel_dispatch.view"
FUEL_DISPATCH_EDIT_PERMISSION = "neoscorpion.fuel_dispatch.edit"
NEOSCORPION_DASHBOARD_VIEW_PERMISSION = "neoscorpion.dashboard.view"
FUELER_VIEW_PERMISSION = "neoscorpion.fueler.view"
FUELER_EDIT_PERMISSION = "neoscorpion.fueler.edit"
TRUCK_MANAGER_VIEW_PERMISSION = "neoscorpion.truck_manager.view"
TRUCK_MANAGER_EDIT_PERMISSION = "neoscorpion.truck_manager.edit"
SETTINGS_VIEW_PERMISSION = "neoscorpion.settings.view"
SETTINGS_EDIT_PERMISSION = "neoscorpion.settings.edit"
HISTORY_VIEW_PERMISSION = "neoscorpion.history.view"


@bp.context_processor
def inject_neoscorpion_navigation():
    return {
        "neoscorpion_internal_menu_items": _visible_neoscorpion_internal_menu,
        "neoscorpion_calculation_not_configured_message": CALCULATION_NOT_CONFIGURED_MESSAGE,
    }


@bp.route("")
@gateway_node_required("scorpion")
def index():
    gateway = get_current_gateway()
    access = permission_access(NEOSCORPION_DASHBOARD_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    return render_template(
        "neonodes/neoscorpion/index.html",
        gateway=gateway,
        menu_items=_visible_neoscorpion_internal_menu(),
    )


@bp.route("/")
@gateway_node_required("scorpion")
def index_slash():
    return redirect(url_for("neoscorpion.index"))


@bp.route("/fuel-dispatch", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def fuel_dispatch():
    gateway = get_current_gateway()
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _dispatch_response(gateway, access, status_code=403)
        try:
            save_dispatch_row(gateway, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _dispatch_response(gateway, access, status_code=400)
        db.session.commit()
        flash("FUEL DISPATCH UPDATED.", "success")
        return redirect(url_for("neoscorpion.fuel_dispatch"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _dispatch_response(gateway, access)


@bp.route("/fueler", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def fueler():
    gateway = get_current_gateway()
    access = permission_access(FUELER_VIEW_PERMISSION, FUELER_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _fueler_response(gateway, access, status_code=403)
        try:
            save_fueler_entry(gateway, current_user, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _fueler_response(gateway, access, status_code=400)
        db.session.commit()
        flash("FUEL ENTRY UPDATED.", "success")
        return redirect(url_for("neoscorpion.fueler"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _fueler_response(gateway, access)


@bp.route("/truck-manager", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def truck_manager():
    gateway = get_current_gateway()
    access = permission_access(TRUCK_MANAGER_VIEW_PERMISSION, TRUCK_MANAGER_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _truck_manager_response(gateway, access, status_code=403)
        try:
            if request.form.get("action") == "deactivate_truck":
                deactivate_truck(gateway, request.form)
                flash("FUEL TRUCK DEACTIVATED.", "success")
            else:
                save_truck(gateway, request.form)
                flash("FUEL TRUCK SAVED.", "success")
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _truck_manager_response(gateway, access, status_code=400)
        db.session.commit()
        return redirect(url_for("neoscorpion.truck_manager"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _truck_manager_response(gateway, access)


@bp.route("/settings", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def settings():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION, SETTINGS_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _settings_response(gateway, access, status_code=403)
        try:
            save_settings(gateway, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _settings_response(gateway, access, status_code=400)
        db.session.commit()
        flash("NEOSCORPION SETTINGS SAVED.", "success")
        return redirect(url_for("neoscorpion.settings"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _settings_response(gateway, access)


@bp.route("/history")
@gateway_node_required("scorpion")
def history():
    gateway = get_current_gateway()
    access = permission_access(HISTORY_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return render_template(
        "neonodes/neoscorpion/history.html",
        gateway=gateway,
        can_view=access["can_view"],
        **history_context(gateway),
    )


@bp.route("/completed-fuel")
@gateway_node_required("scorpion")
def completed_fuel():
    return redirect(url_for("neoscorpion.history"))


def _dispatch_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/fuel_dispatch.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **fuel_dispatch_context(gateway),
    )
    return response, status_code


def _fueler_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/fueler.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **fueler_context(gateway, current_user),
    )
    return response, status_code


def _truck_manager_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/truck_manager.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **truck_manager_context(gateway),
    )
    return response, status_code


def _settings_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/settings.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **settings_context(gateway),
    )
    return response, status_code


def _visible_neoscorpion_internal_menu():
    return visible_neoscorpion_menu_items(user_can, request.endpoint)
