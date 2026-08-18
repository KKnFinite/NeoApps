from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoscorpion import bp
from app.services.access_control import get_current_gateway
from app.services.neoscorpion import (
    CALCULATION_NOT_CONFIGURED_MESSAGE,
    current_sort_operation,
    deactivate_truck,
    fuel_dispatch_context,
    fuel_assignments_live_revision,
    fueler_context,
    history_context,
    save_dispatch_row,
    save_aircraft_fuel_settings,
    save_fueler_entry,
    save_settings,
    save_truck,
    settings_context,
    truck_manager_context,
    visible_neoscorpion_menu_items,
)
from app.services.neoscorpion_assets import (
    complete_nightly_truck_top_off,
    eligible_nightly_fueler_users,
    mark_nightly_truck_topping_off,
    remove_nightly_fueler,
    remove_nightly_truck,
    select_nightly_fueler,
    select_nightly_truck,
    set_nightly_fuel_island_count,
    update_nightly_truck,
)
from app.services.permission_rules import permission_access, user_can


FUEL_DISPATCH_VIEW_PERMISSION = "neoscorpion.fuel_dispatch.view"
FUEL_DISPATCH_EDIT_PERMISSION = "neoscorpion.fuel_dispatch.edit"
NEOSCORPION_DASHBOARD_VIEW_PERMISSION = "neoscorpion.dashboard.view"
FUELER_VIEW_PERMISSION = "neoscorpion.fuel_assignments.view"
FUELER_EDIT_PERMISSION = "neoscorpion.fueler.edit"
TRUCK_MANAGER_VIEW_PERMISSION = "neoscorpion.truck_manager.view"
TRUCK_MANAGER_EDIT_PERMISSION = "neoscorpion.truck_manager.edit"
SETTINGS_VIEW_PERMISSION = "neoscorpion.settings.view"
SETTINGS_EDIT_PERMISSION = "neoscorpion.settings.edit"
APU_RATES_EDIT_PERMISSION = "neoscorpion.apu_rates.edit"
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
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _dispatch_response(gateway, access, status_code=403)
        try:
            result = save_dispatch_row(gateway, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _dispatch_response(gateway, access, status_code=400)
        if result.changed:
            db.session.commit()
            flash("FUEL DISPATCH UPDATED.", "success")
        else:
            flash("NO FUEL DISPATCH CHANGES.", "info")
        return redirect(url_for("neoscorpion.fuel_dispatch"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _dispatch_response(gateway, access)


@bp.post("/fuel-dispatch/assets")
@gateway_node_required("scorpion")
def manage_nightly_assets():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)

    try:
        operation = current_sort_operation(gateway)
        if operation is None:
            raise ValueError("No current sort operation is available.")
        result = _apply_nightly_asset_action(gateway, operation, request.form)
        if result.changed:
            db.session.commit()
            flash("TONIGHT'S ASSETS UPDATED.", "success")
        else:
            flash("NO NIGHTLY ASSET CHANGES.", "info")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Nightly assets changed. Reload and try again."
        )
        flash(message, "error")

    return redirect(
        url_for(
            "neoscorpion.fuel_dispatch",
            assets="open",
            _anchor="manage-tonights-assets",
        )
    )


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
            result = save_fueler_entry(gateway, current_user, request.form)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return _fueler_response(gateway, access, status_code=400)
        if result.changed:
            db.session.commit()
            flash("FUEL ENTRY UPDATED.", "success")
        else:
            flash("NO FUEL ENTRY CHANGES.", "info")
        return redirect(url_for("neoscorpion.fueler"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _fueler_response(gateway, access)


@bp.get("/fuel-assignments/revision")
@gateway_node_required("scorpion")
def fuel_assignments_revision():
    gateway = get_current_gateway()
    access = permission_access(FUELER_VIEW_PERMISSION)
    if not access["can_view"]:
        response = jsonify({"ok": False, "error": "Access denied."})
        response.headers["Cache-Control"] = "no-store"
        return response, 403

    response = jsonify(
        {
            "ok": True,
            **fuel_assignments_live_revision(gateway),
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


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
    can_edit_apu_rates = user_can(APU_RATES_EDIT_PERMISSION)
    if request.method == "POST":
        action = (request.form.get("action") or "save_settings").strip()
        if action == "save_apu_rates":
            if not access["can_view"] or not can_edit_apu_rates:
                db.session.rollback()
                flash("Access denied.", "error")
                return _settings_response(gateway, access, status_code=403)
            try:
                result = save_aircraft_fuel_settings(
                    gateway,
                    current_user,
                    request.form,
                )
            except (IntegrityError, ValueError) as exc:
                db.session.rollback()
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "APU rates changed. Reload Settings and try again."
                )
                flash(message, "error")
                return _settings_response(gateway, access, status_code=400)
            if result.changed:
                db.session.commit()
                flash("AIRCRAFT APU RATES SAVED.", "success")
            else:
                flash("NO AIRCRAFT APU RATE CHANGES.", "info")
            return redirect(url_for("neoscorpion.settings"))

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
        can_manage_assets=access["can_edit"],
        **fuel_dispatch_context(
            gateway,
            include_asset_choices=access["can_edit"],
        ),
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
        can_edit_apu_rates=user_can(APU_RATES_EDIT_PERMISSION),
        **settings_context(gateway),
    )
    return response, status_code


def _visible_neoscorpion_internal_menu():
    return visible_neoscorpion_menu_items(user_can, request.endpoint)


def _apply_nightly_asset_action(gateway, operation, form):
    action = (form.get("action") or "").strip()
    if action == "set_islands":
        return set_nightly_fuel_island_count(operation, form.get("fuel_island_count"))
    if action == "add_fueler":
        user_id = _positive_form_id(form.get("user_id"), "fueler")
        eligible_ids = {user.id for user in eligible_nightly_fueler_users(gateway)}
        if user_id not in eligible_ids:
            raise ValueError("Select an eligible NeoScorpion fueler.")
        return select_nightly_fueler(operation, user_id)
    if action == "remove_fueler":
        return remove_nightly_fueler(
            operation,
            _positive_form_id(form.get("user_id"), "fueler"),
        )
    if action == "add_truck":
        return select_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            status=form.get("status"),
            starting_gallons=form.get("starting_gallons"),
            current_gallons=form.get("current_gallons"),
        )
    if action == "update_truck":
        return update_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            status=form.get("status"),
            starting_gallons=form.get("starting_gallons"),
            current_gallons=form.get("current_gallons"),
        )
    if action == "mark_topping_off":
        return mark_nightly_truck_topping_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
        )
    if action == "complete_top_off":
        return complete_nightly_truck_top_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            form.get("current_gallons"),
        )
    if action == "remove_truck":
        return remove_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
        )
    raise ValueError("Select a valid nightly asset action.")


def _positive_form_id(value, label):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Select a valid {label}.")
    if value <= 0:
        raise ValueError(f"Select a valid {label}.")
    return value
