from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.neonodes.neoscorpion import bp
from app.services.access_control import get_current_gateway
from app.services.neoscorpion import (
    acknowledge_fueler_assignment_update,
    autosave_dispatch_field,
    CALCULATION_NOT_CONFIGURED_MESSAGE,
    confirm_assignment_tail,
    complete_fueled_assignment,
    complete_fuel_on_board,
    correct_fuel_actuals,
    current_sort_operation,
    deactivate_truck,
    fuel_dispatch_context,
    fuel_assignments_live_revision,
    hanzo_context,
    fueler_context,
    history_context,
    mark_fueler_off,
    NEOSCORPION_LIVE_REFRESH_SCREEN_KEYS,
    end_fuel_work_early,
    reopen_fueler_off,
    resume_held_fuel_assignment,
    save_dispatch_row,
    save_dispatch_assignment,
    save_aircraft_fuel_settings,
    save_assignment_planning_settings,
    save_fueler_entry,
    save_settings,
    save_truck,
    settings_context,
    start_follow_up_fuel_cycle,
    swap_assignment_fueler,
    swap_assignment_truck,
    truck_manager_context,
    NEOSCORPION_MENU,
    visible_neoscorpion_menu_items,
)
from app.services.neoscorpion_assets import (
    complete_nightly_truck_top_off,
    eligible_nightly_fueler_users,
    mark_nightly_truck_topping_off,
    mark_nightly_truck_sumped,
    remove_nightly_fueler,
    remove_nightly_truck,
    select_nightly_fueler,
    select_nightly_truck,
    set_nightly_fuel_island_count,
    update_nightly_truck,
)
from app.services.permission_rules import (
    permission_access,
    preload_permission_rules,
    user_can,
)
from app.services.live_screen_refresh import save_live_screen_refresh_override


FUEL_DISPATCH_VIEW_PERMISSION = "neoscorpion.fuel_dispatch.view"
FUEL_DISPATCH_EDIT_PERMISSION = "neoscorpion.fuel_dispatch.edit"
HANZO_VIEW_PERMISSION = "neoscorpion.hanzo.view"
NEOSCORPION_DASHBOARD_VIEW_PERMISSION = "neoscorpion.dashboard.view"
FUELER_VIEW_PERMISSION = "neoscorpion.fuel_assignments.view"
FUELER_EDIT_PERMISSION = "neoscorpion.fueler.edit"
TRUCK_MANAGER_VIEW_PERMISSION = "neoscorpion.truck_manager.view"
TRUCK_MANAGER_EDIT_PERMISSION = "neoscorpion.truck_manager.edit"
SETTINGS_VIEW_PERMISSION = "neoscorpion.settings.view"
SETTINGS_EDIT_PERMISSION = "neoscorpion.settings.edit"
APU_RATES_EDIT_PERMISSION = "neoscorpion.apu_rates.edit"
REFRESH_SETTINGS_EDIT_PERMISSION = "neoscorpion.refresh_settings.edit"
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


@bp.get("/hanzo")
@gateway_node_required("scorpion")
def hanzo():
    gateway = get_current_gateway()
    access = permission_access(HANZO_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _hanzo_response(gateway)


@bp.get("/hanzo/revision")
@gateway_node_required("scorpion")
def hanzo_revision():
    gateway = get_current_gateway()
    access = permission_access(HANZO_VIEW_PERMISSION)
    if not access["can_view"]:
        return _json_no_store({"error": "Access denied."}, 403)
    fingerprint = fuel_assignments_live_revision(gateway)
    return _json_no_store(fingerprint)


@bp.post("/fuel-dispatch/autosave")
@gateway_node_required("scorpion")
def fuel_dispatch_autosave():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    try:
        result = autosave_dispatch_field(
            gateway,
            request.form.get("mission_id"),
            (request.form.get("field_name") or "").strip(),
            request.form.get("value"),
            expected_value=request.form.get("expected_value"),
        )
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc)}, 400)
    if result.changed:
        db.session.commit()
    return _json_no_store(
        {
            "ok": True,
            "changed": result.changed,
            "field_name": result.field_name,
            "display_value": result.display_value,
            "operation_id": result.operation_id,
            "revision": result.revision,
        }
    )


@bp.post("/fuel-dispatch/assignment")
@gateway_node_required("scorpion")
def fuel_dispatch_assignment():
    gateway = get_current_gateway()
    json_response = bool(
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": "Access denied."}, 403)
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        result = save_dispatch_assignment(gateway, request.form)
    except ValueError as exc:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": str(exc)}, 400)
        flash(str(exc), "error")
        return _dispatch_response(gateway, access, status_code=400)
    if result.changed:
        db.session.commit()
    if not json_response:
        flash(
            "FUEL ASSIGNMENT UPDATED." if result.changed else "NO ASSIGNMENT CHANGES.",
            "success" if result.changed else "info",
        )
        return redirect(url_for("neoscorpion.fuel_dispatch"))
    return _json_no_store(
        {
            "ok": True,
            "changed": result.changed,
            "assignment_id": result.assignment.id,
            "assigned_fueler_user_id": result.assignment.assigned_fueler_user_id,
            "assigned_truck_id": result.assignment.assigned_truck_id,
            "operation_id": result.assignment.sort_date_operation_id,
            "revision": result.revision,
            "button_label": "UPDATE ASSIGNMENT",
            "automatic_apu_allowance_lbs": (
                result.fuel_work_state.automatic_apu_allowance_lbs
                if result.fuel_work_state is not None
                else None
            ),
            "apu_override_enabled": (
                bool(result.fuel_work_state.apu_override_enabled)
                if result.fuel_work_state is not None
                else False
            ),
            "apu_override_allowance_lbs": (
                result.fuel_work_state.apu_override_allowance_lbs
                if result.fuel_work_state is not None
                else None
            ),
            "effective_apu_allowance_lbs": (
                result.fuel_work_state.apu_allowance_lbs
                if result.fuel_work_state is not None
                else None
            ),
        }
    )


@bp.post("/fuel-dispatch/fuel-on-board")
@gateway_node_required("scorpion")
def fuel_on_board():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)

    try:
        result = complete_fuel_on_board(
            gateway,
            current_user,
            request.form.get("assignment_id"),
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _dispatch_response(gateway, access, status_code=400)

    if result.changed:
        db.session.commit()
        flash("FUEL ON BOARD COMPLETED.", "success")
    else:
        flash("FUEL ON BOARD WAS ALREADY COMPLETED.", "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/complete")
@gateway_node_required("scorpion")
def fuel_dispatch_complete():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)

    try:
        result = complete_fueled_assignment(
            gateway,
            current_user,
            request.form.get("assignment_id"),
        )
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Fuel completion changed. Reload Fuel Dispatch and try again."
        )
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)

    if result.changed:
        db.session.commit()
        flash("FUELING COMPLETE.", "success")
    else:
        flash("FUELING WAS ALREADY COMPLETE.", "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/start-follow-up")
@gateway_node_required("scorpion")
def fuel_dispatch_start_follow_up():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    cycle_type = (request.form.get("cycle_type") or "").strip().lower()
    try:
        result = start_follow_up_fuel_cycle(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            cycle_type,
            request.form.get("required_fuel"),
            request.form.get("assigned_fueler_user_id"),
            request.form.get("assigned_truck_id"),
        )
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Fuel cycle changed. Reload Fuel Dispatch and try again."
        )
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)
    db.session.commit()
    flash(f"{result.assignment.current_cycle_type.upper()} STARTED.", "success")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/reopen-off")
@gateway_node_required("scorpion")
def fuel_dispatch_reopen_off():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        result = reopen_fueler_off(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            request.form.get("reopen_reason"),
        )
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "OFF state changed. Reload Fuel Dispatch and try again."
        )
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)
    if result.changed:
        db.session.commit()
        flash("FUELER OFF REOPENED.", "success")
    else:
        flash("FUELER WORK WAS ALREADY OPEN.", "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/correct-actual")
@gateway_node_required("scorpion")
def fuel_dispatch_correct_actual():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        result = correct_fuel_actuals(
            gateway,
            current_user,
            request.form,
        )
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Actual fuel changed. Reload Fuel Dispatch and try again."
        )
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)
    if result.changed:
        db.session.commit()
        flash("ACTUAL FUEL CORRECTED.", "success")
    else:
        flash("NO ACTUAL FUEL CHANGES.", "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/resume")
@gateway_node_required("scorpion")
def fuel_dispatch_resume():
    return _run_fuel_interruption_action(
        lambda gateway: resume_held_fuel_assignment(
            gateway,
            current_user,
            request.form.get("assignment_id"),
        ),
        "FUEL ASSIGNMENT RESUMED.",
        "FUEL ASSIGNMENT WAS ALREADY ACTIVE.",
    )


@bp.post("/fuel-dispatch/swap-fueler")
@gateway_node_required("scorpion")
def fuel_dispatch_swap_fueler():
    return _run_fuel_interruption_action(
        lambda gateway: swap_assignment_fueler(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            request.form.get("replacement_fueler_user_id"),
        ),
        "ASSIGNED FUELER CHANGED.",
        "FUELER ASSIGNMENT WAS UNCHANGED.",
    )


@bp.post("/fuel-dispatch/swap-truck")
@gateway_node_required("scorpion")
def fuel_dispatch_swap_truck():
    return _run_fuel_interruption_action(
        lambda gateway: swap_assignment_truck(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            request.form.get("replacement_truck_id"),
        ),
        "ASSIGNED TRUCK CHANGED.",
        "TRUCK ASSIGNMENT WAS UNCHANGED.",
    )


@bp.post("/fuel-dispatch/confirm-tail")
@gateway_node_required("scorpion")
def fuel_dispatch_confirm_tail():
    return _run_fuel_interruption_action(
        lambda gateway: confirm_assignment_tail(
            gateway,
            current_user,
            request.form.get("assignment_id"),
        ),
        "CURRENT MISSION TAIL CONFIRMED.",
        "MISSION TAIL WAS ALREADY CONFIRMED.",
    )


@bp.post("/fuel-dispatch/end-early")
@gateway_node_required("scorpion")
def fuel_dispatch_end_early():
    return _run_fuel_interruption_action(
        lambda gateway: end_fuel_work_early(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            request.form.get("end_early_reason"),
        ),
        "FUEL WORK ENDED EARLY.",
        "FUEL WORK WAS ALREADY ENDED EARLY.",
    )


@bp.post("/fuel-dispatch/assets")
@gateway_node_required("scorpion")
def manage_nightly_assets():
    gateway = get_current_gateway()
    action = (request.form.get("action") or "").strip()
    compact_truck_card = (
        request.form.get("dispatch_truck_card") == "1"
        and action in {"mark_topping_off", "complete_top_off"}
    )
    # The compact Fuel Dispatch cards explicitly opt into the JSON contract.
    # Do not depend on optional fetch headers here: a missing or altered header
    # previously made a successful card request fall through to a 302 redirect,
    # which the client then reported as a generic save failure.
    json_response = compact_truck_card
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": "Access denied."}, 403)
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    if not access["can_edit"]:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": "Access denied."}, 403)
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)

    operation = None
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
        if json_response:
            return _json_no_store({"ok": False, "error": message}, 400)
    except Exception:
        db.session.rollback()
        if not json_response:
            raise
        current_app.logger.exception(
            "NeoScorpion compact truck-card update failed: "
            "gateway_id=%s operation_id=%s truck_id=%s action=%s",
            getattr(gateway, "id", None),
            getattr(operation, "id", None),
            request.form.get("fuel_truck_id"),
            action,
        )
        return _json_no_store(
            {"ok": False, "error": "Truck update failed on the server."},
            500,
        )

    if json_response:
        return _json_no_store(
            {
                "ok": True,
                "changed": result.changed,
                "revision": result.revision,
            }
        )

    if compact_truck_card:
        return redirect(url_for("neoscorpion.fuel_dispatch"))

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
    json_response = bool(
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            if json_response:
                return _json_no_store({"ok": False, "error": "Access denied."}, 403)
            flash("Access denied.", "error")
            return _fueler_response(gateway, access, status_code=403)
        try:
            result = save_fueler_entry(gateway, current_user, request.form)
        except ValueError as exc:
            db.session.rollback()
            if json_response:
                return _json_no_store({"ok": False, "error": str(exc)}, 400)
            flash(str(exc), "error")
            return _fueler_response(gateway, access, status_code=400)
        if result.changed:
            db.session.commit()
        if json_response:
            work_state = result.fuel_work_state
            return _json_no_store(
                {
                    "ok": True,
                    "changed": result.changed,
                    "revision": result.revision,
                    "effective_apu_allowance_lbs": (
                        work_state.apu_allowance_lbs if work_state else None
                    ),
                    "automatic_apu_allowance_lbs": (
                        work_state.automatic_apu_allowance_lbs
                        if work_state
                        else None
                    ),
                    "apu_override_enabled": bool(
                        work_state and work_state.apu_override_enabled
                    ),
                    "apu_override_allowance_lbs": (
                        work_state.apu_override_allowance_lbs
                        if work_state and work_state.apu_override_enabled
                        else None
                    ),
                }
            )
            flash("FUEL ENTRY UPDATED.", "success")
        else:
            flash("NO FUEL ENTRY CHANGES.", "info")
        return redirect(url_for("neoscorpion.fueler"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return _fueler_response(gateway, access)


@bp.post("/fueler/off")
@gateway_node_required("scorpion")
def fueler_off():
    gateway = get_current_gateway()
    access = permission_access(FUELER_VIEW_PERMISSION, FUELER_EDIT_PERMISSION)
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _fueler_response(gateway, access, status_code=403)

    try:
        result = mark_fueler_off(
            gateway,
            current_user,
            request.form.get("assignment_id"),
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return _fueler_response(gateway, access, status_code=400)

    if result.changed:
        db.session.commit()
        flash("FUELER MARKED OFF.", "success")
    else:
        flash("FUELER WAS ALREADY OFF.", "info")
    return redirect(url_for("neoscorpion.fueler"))


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


@bp.post("/fuel-assignments/acknowledge-update")
@gateway_node_required("scorpion")
def fuel_assignments_acknowledge_update():
    gateway = get_current_gateway()
    access = permission_access(FUELER_VIEW_PERMISSION)
    if not access["can_view"]:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    try:
        result = acknowledge_fueler_assignment_update(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            request.form.get("update_version"),
        )
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc)}, 400)
    if result.changed:
        db.session.commit()
    return _json_no_store(
        {
            "ok": True,
            "changed": result.changed,
            "acknowledged_version": result.acknowledged_version,
        }
    )


@bp.get("/fuel-dispatch/revision")
@gateway_node_required("scorpion")
def fuel_dispatch_revision():
    gateway = get_current_gateway()
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION)
    if not access["can_view"]:
        response = jsonify({"error": "Access denied."})
        response.headers["Cache-Control"] = "no-store"
        return response, 403

    fingerprint = fuel_assignments_live_revision(gateway)
    response = jsonify(
        fingerprint
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
            action = request.form.get("action")
            if action == "mark_sumped":
                operation = current_sort_operation(gateway)
                if operation is None:
                    raise ValueError("No current sort operation is available.")
                mark_nightly_truck_sumped(
                    operation,
                    _positive_form_id(
                        request.form.get("truck_id"),
                        "fuel truck",
                    ),
                    request.form.get("current_gallons"),
                )
                flash("FUEL TRUCK MARKED SUMPED.", "success")
            elif action == "deactivate_truck":
                deactivate_truck(gateway, request.form, current_user)
                flash("FUEL TRUCK DEACTIVATED.", "success")
            else:
                save_truck(gateway, request.form, current_user)
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
    can_edit_refresh_settings = user_can(REFRESH_SETTINGS_EDIT_PERMISSION)
    if request.method == "POST":
        action = (request.form.get("action") or "save_settings").strip()
        if action == "save_live_refresh":
            if not access["can_view"] or not can_edit_refresh_settings:
                db.session.rollback()
                flash("Access denied.", "error")
                return _settings_response(gateway, access, status_code=403)
            try:
                result = save_live_screen_refresh_override(
                    gateway,
                    request.form.get("screen_key"),
                    request.form.get("refresh_interval_seconds"),
                    allowed_screen_keys=NEOSCORPION_LIVE_REFRESH_SCREEN_KEYS,
                )
            except (IntegrityError, ValueError) as exc:
                db.session.rollback()
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Live refresh setting changed. Reload Settings and try again."
                )
                flash(message, "error")
                return _settings_response(gateway, access, status_code=400)
            if result.changed:
                db.session.commit()
                flash("LIVE REFRESH SETTING SAVED.", "success")
            else:
                flash("NO LIVE REFRESH SETTING CHANGES.", "info")
            return redirect(url_for("neoscorpion.settings"))

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

        if action == "save_assignment_planning_settings":
            if not access["can_edit"]:
                db.session.rollback()
                flash("Access denied.", "error")
                return _settings_response(gateway, access, status_code=403)
            try:
                result = save_assignment_planning_settings(
                    gateway,
                    current_user,
                    request.form,
                )
            except (IntegrityError, ValueError) as exc:
                db.session.rollback()
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Assignment planning settings changed. Reload Settings and try again."
                )
                flash(message, "error")
                return _settings_response(gateway, access, status_code=400)
            if result.changed:
                db.session.commit()
                flash("ASSIGNMENT PLANNING SETTINGS SAVED.", "success")
            else:
                flash("NO ASSIGNMENT PLANNING SETTING CHANGES.", "info")
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


def _hanzo_response(gateway):
    return render_template(
        "neonodes/neoscorpion/hanzo.html",
        gateway=gateway,
        **hanzo_context(gateway),
    )


def _json_no_store(payload, status_code=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
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
        can_edit_refresh_settings=user_can(REFRESH_SETTINGS_EDIT_PERMISSION),
        **settings_context(gateway),
    )
    return response, status_code


def _run_fuel_interruption_action(action, success_message, no_change_message):
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        result = action(gateway)
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Fuel assignment changed. Reload Fuel Dispatch and review it."
        )
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)
    if result.changed:
        db.session.commit()
        flash(success_message, "success")
    else:
        flash(no_change_message, "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


def _visible_neoscorpion_internal_menu():
    preload_permission_rules(item.permission for item in NEOSCORPION_MENU)
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
            changed_by_user=current_user,
        )
    if action == "add_truck":
        return select_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            status=form.get("status"),
            starting_gallons=form.get("starting_gallons"),
            current_gallons=form.get("current_gallons"),
            changed_by_user=current_user,
        )
    if action == "update_truck":
        return update_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            status=form.get("status"),
            starting_gallons=form.get("starting_gallons"),
            current_gallons=form.get("current_gallons"),
            changed_by_user=current_user,
        )
    if action == "mark_topping_off":
        return mark_nightly_truck_topping_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            changed_by_user=current_user,
        )
    if action == "complete_top_off":
        return complete_nightly_truck_top_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            form.get("current_gallons"),
        )
    if action == "mark_sumped":
        return mark_nightly_truck_sumped(
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
