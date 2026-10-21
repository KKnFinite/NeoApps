from app.services.neoscorpion_tail_notes import tail_notes_revision
from flask import send_file, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from types import SimpleNamespace
import csv
import io
from datetime import datetime

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import NeoScorpionSettings, SortDateMission
from app.neonodes.neoscorpion import bp
from app.services.access_control import get_current_gateway
from app.services.neoscorpion_drivers import DRIVER_VIEW, DRIVER_EDIT, change_driver, driver_context, driver_roster
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
    dispatch_neo_fuel_presentation,
    fuel_assignments_live_revision,
    fueling_board_context,
    hanzo_context,
    fueler_context,
    FuelerDataConflict,
    _fueler_expected,
    history_context,
    fuel_report_context,
    mark_ready_for_fuel,
    mark_fueler_off,
    confirm_fueler_truck_on,
    begin_physical_visit,
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
    cancel_uplift,
    cancel_defuel,
    unassign_assignment_truck,
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
    set_nightly_fueler_nickname,
    select_nightly_truck,
    set_nightly_fuel_island_count,
    update_nightly_truck,
    verify_nightly_truck_inventory,
)
from app.services.neoscorpion_quick_fuel import calculate_quick_fuel
from app.services.neoscorpion_dispatch_checks import DispatcherCheckConflict, save_dispatcher_check
from app.services.permission_rules import (
    permission_access,
    preload_permission_rules,
    user_can,
)
from app.services.neoscorpion_spear import (
    effective_spear_settings,
    execute_spear_step,
    record_spear_completion,
    record_spear_execution,
    save_spear_settings,
)
from app.services.neoscorpion_spear_calibration import (
    calibration_review_payload,
    reset_live_calibration,
)
from app.services.neoscorpion_learning_vault import (
    LearningVaultUnavailable,
    archive_calibration_review,
    learning_vault_status,
    list_calibration_reviews,
    list_learning_records,
    read_calibration_review,
    read_learning_record,
    test_learning_vault_connection,
)
from app.services.neoscorpion_spear_learning_capture import (
    capture_completed_learning_outcome,
    capture_current_sort_learning_outcomes,
)


def _dispatch_tail_revision(operation_id):
    """Shared mission tail changes, independent of NeoScorpion's asset revision."""
    if not operation_id:
        return ""
    latest = db.session.query(db.func.max(SortDateMission.tail_updated_at)).filter(
        SortDateMission.sort_date_operation_id == operation_id,
        SortDateMission.mission_type == "departure",
    ).scalar()
    return latest.isoformat(timespec="microseconds") if latest is not None else ""


FUEL_DISPATCH_VIEW_PERMISSION = "neoscorpion.fuel_dispatch.view"
FUEL_DISPATCH_EDIT_PERMISSION = "neoscorpion.fuel_dispatch.edit"
HANZO_VIEW_PERMISSION = "neoscorpion.hanzo.view"
NEOSCORPION_DASHBOARD_VIEW_PERMISSION = "neoscorpion.dashboard.view"
FUELER_VIEW_PERMISSION = "neoscorpion.fuel_assignments.view"
FUELING_BOARD_VIEW_PERMISSION = "neoscorpion.fueling_board.view"
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


@bp.get("/quick-fuel")
@gateway_node_required("scorpion")
def quick_fuel():
    return render_template(
        "neonodes/neoscorpion/quick_fuel.html",
        gateway=get_current_gateway(),
    )


@bp.post("/quick-fuel/calculate")
@gateway_node_required("scorpion")
def quick_fuel_calculate():
    # Calculator only: no Sort, assignment, fuel event, or inventory write.
    return _json_no_store(calculate_quick_fuel(get_current_gateway(), request.form))


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


@bp.post('/fuel-dispatch/tail-notes')
@gateway_node_required('scorpion')
def fuel_dispatch_tail_notes():
    from app.services.neoscorpion_tail_notes import save_tail_note, TailNoteConflict
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    if not access['can_view'] or not access['can_edit']:
        return _json_no_store({'ok': False, 'error': 'Access denied.'}, 403)
    try:
        note = save_tail_note(get_current_gateway(), current_user, request.form)
        db.session.commit()
    except TailNoteConflict as exc:
        db.session.rollback()
        return _json_no_store({'ok': False, 'error': str(exc)}, 409)
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({'ok': False, 'error': str(exc)}, 400)
    return _json_no_store({'ok': True, 'note': note})


@bp.post('/fuel-dispatch/preferences')
@gateway_node_required('scorpion')
def fuel_dispatch_preferences():
    from app.services.neoscorpion_dispatch_preferences import (
        dispatch_preferences, save_dispatch_preferences, DispatchPreferenceConflict)
    if not permission_access(FUEL_DISPATCH_VIEW_PERMISSION)['can_view']:
        return _json_no_store({'ok': False, 'error': 'Access denied.'}, 403)
    gateway = get_current_gateway()
    try:
        preferences = save_dispatch_preferences(gateway, current_user, request.form)
        db.session.commit()
    except DispatchPreferenceConflict as exc:
        db.session.rollback()
        return _json_no_store({'ok': False, 'error': str(exc),
            'preferences': dispatch_preferences(gateway, current_user)}, 409)
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({'ok': False, 'error': str(exc)}, 400)
    return _json_no_store({'ok': True, 'preferences': preferences})


@bp.post("/fuel-dispatch/check")
@gateway_node_required("scorpion")
def fuel_dispatch_check():
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    if not access["can_view"] or not access["can_edit"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    try:
        result = save_dispatcher_check(get_current_gateway(), current_user, request.form)
        if result["changed"]:
            db.session.commit()
    except (DispatcherCheckConflict, IntegrityError) as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc) if isinstance(exc, DispatcherCheckConflict)
                              else "Your checkbox changed. Refresh Dispatch and try again."}, 409)
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc)}, 400)
    return _json_no_store({"ok": True, **result})


@bp.post("/fuel-dispatch/call-dispatch/ack")
@gateway_node_required("scorpion")
def fuel_dispatch_call_dispatch_ack():
    from app.services.neoscorpion_call_dispatch import acknowledge_call_dispatch, CallDispatchConflict
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    if not access["can_view"] or not access["can_edit"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    try:
        result = acknowledge_call_dispatch(get_current_gateway(), current_user, request.form)
        db.session.commit()
    except CallDispatchConflict as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc)}, 409)
    except ValueError as exc:
        db.session.rollback()
        return _json_no_store({"ok": False, "error": str(exc)}, 400)
    return _json_no_store({"ok": True, **result})


@bp.get("/fuel-dispatch/live-panel")
@gateway_node_required("scorpion")
def fuel_dispatch_live_panel():
    gateway = get_current_gateway()
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    notes_revision = tail_notes_revision(gateway)
    context = _dispatch_presentation_context(
        gateway,
        include_asset_choices=access["can_edit"],
        include_spear=request.args.get("completion_only") != "1",
    )
    operation = context["operation"]
    spear_plan = context["spear_plan"]
    return _json_no_store(
        {
            "ok": True,
            "operation_id": operation.id if operation else None,
            "revision": int(context.get("nightly_asset_revision") or 0),
            "tail_revision": _dispatch_tail_revision(operation.id if operation else None),
            "spear_plan_token": spear_plan.token if spear_plan else "",
            "spear_automatic_reference": context.get("spear_automatic_reference", ""),
            "spear_automation_enabled": bool(
                context["spear_settings"].automation_enabled
                and context["spear_automatic_available"]
            ),
            "tail_notes_revision": notes_revision,
            "html": render_template(
                "neonodes/neoscorpion/_fuel_dispatch_panel.html",
                gateway=gateway,
                can_view=access["can_view"],
                can_edit=access["can_edit"],
                can_manage_assets=access["can_edit"],
                can_view_spear_settings=user_can(SETTINGS_VIEW_PERMISSION),
                **context,
            ),
        }
    )


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
            "neo_fuel": result.neo_fuel_presentation,
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
            "neo_fuel": dispatch_neo_fuel_presentation(gateway, result.assignment.sort_date_mission,
                result.assignment, result.fuel_work_state),
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
        _capture_learning_after_commit(gateway, result.assignment.id)
        flash("FUEL ON BOARD COMPLETED.", "success")
    else:
        flash("FUEL ON BOARD WAS ALREADY COMPLETED.", "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/pending-uplift")
@gateway_node_required("scorpion")
def fuel_dispatch_pending_uplift():
    access=permission_access(FUEL_DISPATCH_VIEW_PERMISSION,FUEL_DISPATCH_EDIT_PERMISSION)
    if not access['can_view'] or not access['can_edit']:
        return _json_no_store({'ok':False,'error':'Access denied.'},403)
    from app.services.neoscorpion_pending_uplift import save_pending_uplift,plan_continuation,review_handoff,PendingUpliftConflict
    try:
        action=plan_continuation if request.form.get('plan_kind')=='continuation' else save_pending_uplift
        if request.form.get('pending_action') in {'review_source','resolve_handoff','cancel_unstarted_handoff'}:action=review_handoff
        result=action(get_current_gateway(),current_user,request.form)
        db.session.commit()
    except (ValueError,IntegrityError) as exc:
        db.session.rollback()
        return _json_no_store({'ok':False,'error':str(exc) if isinstance(exc,ValueError) else 'Pending uplift changed; refresh and review.'},
            409 if isinstance(exc,(PendingUpliftConflict,IntegrityError)) else 400)
    return _json_no_store({'ok':True,'changed':result.changed,'revision':result.revision,
        'operation_id':result.pending.sort_date_operation_id,'tail_revision':_dispatch_tail_revision(result.pending.sort_date_operation_id),
        'pending_status':result.pending.status,'pending_version':result.pending.version})


@bp.post("/fuel-dispatch/complete")
@gateway_node_required("scorpion")
def fuel_dispatch_complete():
    gateway = get_current_gateway()
    json_response = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    access = permission_access(
        FUEL_DISPATCH_VIEW_PERMISSION,
        FUEL_DISPATCH_EDIT_PERMISSION,
    )
    if not access["can_edit"]:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False,"error":"Access denied."},403)
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)

    try:
        result = complete_fueled_assignment(
            gateway,
            current_user,
            request.form.get("assignment_id"),
            physical_work_id=request.form.get("physical_work_id"),
            physical_sequence=request.form.get("physical_sequence"),
        )
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "Fuel completion changed. Reload Fuel Dispatch and try again."
        )
        if json_response:
            return _json_no_store({"ok":False,"error":message},409 if isinstance(exc,FuelerDataConflict) else 400)
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=400)

    if result.changed:
        db.session.commit()
        _capture_learning_after_commit(gateway, result.assignment.id)
        owner=getattr(result.assignment,'_parent_assignment',result.assignment)
        flash("FUELING COMPLETE." if owner.completed_at_utc else "PHYSICAL VISIT VERIFIED · REMAINING WORK / VERIFICATION STILL OPEN.", "success")
        event = result.fueling_event
        current_app.logger.info("scorpion_dispatch_complete actor=%s operation=%s mission=%s assignment=%s event=%s work=%s tail=%s revision=%s",
            current_user.id, result.assignment.sort_date_operation_id,result.mission.id,result.assignment.id,
            getattr(event,"id",None),getattr(event,"fuel_work_state_id",None),getattr(event,"tail_number",None),result.revision)
    else:
        flash("FUELING WAS ALREADY COMPLETE.", "info")
    if json_response:
        return _json_no_store({"ok":True,"changed":result.changed,
            **fuel_assignments_live_revision(gateway),
            "tail_revision":_dispatch_tail_revision(result.assignment.sort_date_operation_id)})
    return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.post("/fuel-dispatch/ready-for-fuel")
@gateway_node_required("scorpion")
def fuel_dispatch_ready_for_fuel():
    gateway = get_current_gateway()
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    if not access["can_edit"]:
        db.session.rollback()
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        result = mark_ready_for_fuel(gateway, current_user, request.form.get("assignment_id"))
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, ValueError) else "Fuel assignment changed. Reload and try again.", "error")
        return _dispatch_response(gateway, access, status_code=400)
    if result.changed:
        db.session.commit()
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
            expected_cycle=request.form.get("expected_cycle", ""),
            expected_tail=request.form.get("expected_tail", ""),
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


@bp.post("/fuel-dispatch/cancel-uplift")
@gateway_node_required("scorpion")
def fuel_dispatch_cancel_uplift():
    return _run_dispatch_lifecycle_action(lambda gateway: cancel_uplift(
        gateway, current_user, request.form.get("assignment_id"),
        expected_cycle=request.form.get("expected_cycle", ""),
        expected_tail=request.form.get("expected_tail", ""),
    ), "UPLIFT CANCELLED.")


@bp.post("/fuel-dispatch/cancel-defuel")
@gateway_node_required("scorpion")
def fuel_dispatch_cancel_defuel():
    return _run_dispatch_lifecycle_action(lambda gateway: cancel_defuel(
        gateway, current_user, request.form.get("assignment_id"),
        expected_cycle=request.form.get("expected_cycle", ""),
        expected_tail=request.form.get("expected_tail", ""),
    ), "DEFUEL CANCELLED.")


@bp.post("/fuel-dispatch/unassign-truck")
@gateway_node_required("scorpion")
def fuel_dispatch_unassign_truck():
    return _run_dispatch_lifecycle_action(lambda gateway: unassign_assignment_truck(
        gateway, current_user, request.form.get("assignment_id"),
        expected_truck_id=request.form.get("expected_truck_id", ""),
        expected_cycle=request.form.get("expected_cycle", ""),
        expected_tail=request.form.get("expected_tail", ""),
    ), "TRUCK UNASSIGNED.")


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
            expected_cycle=request.form.get("expected_cycle", ""),
            expected_tail=request.form.get("expected_tail", ""),
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
            physical_work_id=request.form.get("physical_work_id"),
            physical_sequence=request.form.get("physical_sequence"),
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
        and action in {"mark_topping_off", "complete_top_off", "mark_sumped", "verify_inventory"}
    )
    # The compact Fuel Dispatch cards explicitly opt into the JSON contract.
    # Do not depend on optional fetch headers here: a missing or altered header
    # previously made a successful card request fall through to a 302 redirect,
    # which the client then reported as a generic save failure.
    workspace_request = request.form.get("dispatch_assets_workspace") == "1"
    json_response = compact_truck_card or workspace_request
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
        if (workspace_request or action in {"mark_topping_off", "complete_top_off"}) and str(operation.id) != request.form.get("operation_id"):
            raise ValueError("The active sort changed. Refresh tonight's assets before continuing.")
        if action in {"mark_topping_off", "complete_top_off"} and not request.form.get("top_off_action_state"):
            raise ValueError("Refresh the truck controls before submitting TOP OFF / RETURN.")
        result = _apply_nightly_asset_action(gateway, operation, request.form)
        if result.changed:
            db.session.commit()
            if not workspace_request:
                flash("TONIGHT'S ASSETS UPDATED.", "success")
        else:
            if not workspace_request:
                flash("NO NIGHTLY ASSET CHANGES.", "info")
    except (IntegrityError, ValueError) as exc:
        db.session.rollback()
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else (
                "Nickname is already in use at this gateway. Reload and try again."
                if action == "set_fueler_nickname"
                else "Nightly assets changed. Reload and try again."
            )
        )
        if not workspace_request:
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
                **({"operation_id": operation.id} if workspace_request else {}),
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
            result = save_fueler_entry(gateway, current_user, request.form, require_expected=True)
        except FuelerDataConflict as exc:
            db.session.rollback()
            return _fuel_data_payload(gateway, int(request.form.get("assignment_id", 0)), False,
                                      error=str(exc), status=409)
        except ValueError as exc:
            db.session.rollback()
            if json_response:
                return _json_no_store({"ok": False, "error": str(exc)}, 400)
            flash(str(exc), "error")
            return _fueler_response(gateway, access, status_code=400)
        if result.changed:
            terminal = bool(result.fuel_work_state and result.fuel_work_state.fuel_assignment.completed_at_utc)
            db.session.commit()
            if terminal:
                _capture_learning_after_commit(gateway, request.form.get("assignment_id"))
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
        flash("TRUCK OFF RECORDED. Final data remains editable.", "success")
    else:
        flash("TRUCK OFF WAS ALREADY RECORDED.", "info")
    return redirect(url_for("neoscorpion.fueler"))


def _capture_learning_after_commit(gateway, assignment_id):
    try:
        result = capture_completed_learning_outcome(gateway, assignment_id)
        if result.captured:
            current_app.logger.info(
                "SPEAR learning outcome archived: assignment_id=%s eligible=%s duplicate=%s",
                assignment_id,
                result.training_eligible,
                result.already_saved,
            )
    except Exception:
        # Fuel operations are canonical and already committed. Learning storage
        # is deliberately fail-open so Vault availability can never undo work.
        current_app.logger.exception(
            "SPEAR learning capture failed after committed fuel operation: assignment_id=%s",
            assignment_id,
        )


def _backfill_learning_after_enable(gateway, operation_id):
    try:
        results = capture_current_sort_learning_outcomes(gateway, operation_id)
        captured = sum(1 for item in results if item.captured)
        current_app.logger.info(
            "SPEAR learning capture enabled: operation_id=%s archived=%s",
            operation_id,
            captured,
        )
    except Exception:
        current_app.logger.exception(
            "SPEAR current-sort learning backfill failed safely: operation_id=%s",
            operation_id,
        )


def _fuel_data_permissions(dispatcher):
    if dispatcher:
        return (
            user_can(FUEL_DISPATCH_VIEW_PERMISSION),
            user_can(FUEL_DISPATCH_EDIT_PERMISSION),
        )
    return (
        user_can(FUELER_VIEW_PERMISSION),
        user_can(FUELER_EDIT_PERMISSION),
    )


def _fuel_data_payload(gateway, assignment_id, dispatcher, *, error=None, status=200, submission_receipt=None):
    _can_view, can_edit = _fuel_data_permissions(dispatcher)
    physical_work_id=request.args.get("physical_work_id")
    physical_sequence=request.args.get("physical_sequence")
    if submission_receipt and error is None:
        # Source-addressed submission must show the next visit only when an
        # exact recorded handoff proves the relationship (not a tail lookup).
        from app.models import NeoScorpionPendingUplift,NeoScorpionFuelAssignment
        owner=db.session.get(NeoScorpionFuelAssignment,assignment_id)
        promoted=NeoScorpionPendingUplift.query.filter_by(fuel_assignment_id=assignment_id,
            parent_work_state_id=submission_receipt['work_id'],status='promoted',
            promoted_work_state_id=owner.active_work_state_id if owner else None).first()
        if (promoted and promoted.plan_kind in ('continuation','increase')
                and (promoted.handoff_snapshot or {}).get('submission_version')==submission_receipt['version']):
            physical_work_id=None;physical_sequence=None
    context = fueler_context(gateway, current_user, assignment_id=assignment_id,
                             dispatcher=dispatcher, physical_work_id=physical_work_id,
                             physical_sequence=physical_sequence)
    row = context["rows"][0] if context["rows"] else None
    return _json_no_store({
        "ok": error is None,
        "error": error,
        "revision": context["fuel_assignments_revision"],
        "operation_id": context["operation"].id if context["operation"] else None,
        "closed": row is None,
        "submission_receipt": submission_receipt,
        "next_visit_activated": bool(submission_receipt and row and row['physical_work_id'] != submission_receipt['work_id']),
        "html": render_template("neonodes/neoscorpion/_fueler_card.html", row=row,
                                can_edit=can_edit, dispatcher_panel=dispatcher) if row else "",
    }, status)


def _fuel_data_request(assignment_id, *, dispatcher=False, off=False, truck_on=False, new_visit=False, submit=False):
    gateway = get_current_gateway()
    can_view, can_edit = _fuel_data_permissions(dispatcher)
    if request.method == "GET":
        if not can_view:
            return _json_no_store({"ok": False, "error": "Access denied."}, 403)
        context = fueler_context(gateway, current_user, assignment_id=assignment_id,
                                 dispatcher=dispatcher, physical_work_id=request.args.get("physical_work_id"),
                                 physical_sequence=request.args.get("physical_sequence"))
        if not context["rows"]:
            return _json_no_store({"ok": False, "error": "Active assignment not found."}, 404)
        row = context["rows"][0]
        return _json_no_store({"ok": True, "revision": context["fuel_assignments_revision"],
            "operation_id": context["operation"].id if context["operation"] else None,
            "html": render_template("neonodes/neoscorpion/_fueler_card.html", row=row,
                                    can_edit=can_edit, dispatcher_panel=dispatcher)})
    if not can_edit:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    form = request.form.copy()
    form["assignment_id"] = str(assignment_id)
    if request.args.get("physical_work_id"):
        form["physical_work_id"] = request.args.get("physical_work_id")
        form["physical_sequence"] = request.args.get("physical_sequence", "")
    try:
        if submit:
            from app.services.neoscorpion import submit_fueler_assignment
            result=submit_fueler_assignment(gateway,current_user,assignment_id,
                expected=_fueler_expected(form,required=True),physical_work_id=form.get("physical_work_id"),
                physical_sequence=form.get("physical_sequence"))
        elif new_visit:
            result = begin_physical_visit(gateway,current_user,assignment_id,
                expected=_fueler_expected(form,required=True),dispatcher=dispatcher,
                separate_visit_confirmed=form.get('separate_visit_confirmed') == '1')
        elif truck_on:
            if dispatcher:
                raise ValueError("Only the assigned Fueler can confirm TRUCK ON.")
            changed, revision, _work = confirm_fueler_truck_on(
                gateway, current_user, assignment_id,
                expected_cycle=form.get("expected_cycle"),
                expected_tail=form.get("expected_tail"),
                expected_truck_id=form.get("expected_truck_id"),
                expected_work_ref=form.get("expected_work_ref"), require_work_ref=True,
                resume_off_at=form.get("resume_off_at",request.args.get('resume_off_at')),
            )
            result = SimpleNamespace(changed=changed, revision=revision, fuel_work_state=_work)
        elif off:
            result = mark_fueler_off(gateway, current_user, assignment_id,
                dispatcher=dispatcher, expected=_fueler_expected(form, required=True),
                defer_completion=form.get("completion_deferred") == "1",
                physical_work_id=form.get("physical_work_id"), physical_sequence=form.get("physical_sequence"))
        else:
            result = save_fueler_entry(gateway, current_user, form,
                                      dispatcher=dispatcher, require_expected=True)
        if result.changed:
            work = getattr(result, "fuel_work_state", None)
            terminal = bool(work and work.fuel_assignment.completed_at_utc)
            db.session.commit()
            if terminal:
                _capture_learning_after_commit(gateway, assignment_id)
        else:
            db.session.rollback()
    except FuelerDataConflict as exc:
        db.session.rollback()
        current_app.logger.warning("scorpion_action_rejected actor=%s assignment=%s work=%s sequence=%s reason=stale_evidence",
            current_user.id,assignment_id,form.get("physical_work_id"),form.get("physical_sequence"))
        return _fuel_data_payload(gateway, assignment_id, dispatcher, error=str(exc), status=409)
    except ValueError as exc:
        db.session.rollback()
        current_app.logger.warning("scorpion_action_rejected actor=%s assignment=%s work=%s sequence=%s reason=%s",
            current_user.id,assignment_id,form.get("physical_work_id"),form.get("physical_sequence"),str(exc))
        return _json_no_store({"ok": False, "error": str(exc)}, 400)
    receipt = None
    if submit:
        work = result.fuel_work_state
        receipt = dict(work_id=work.id, cycle=work.physical_cycle_number,
            sequence=work.physical_sequence_number, actor_id=work.fueler_submitted_by_user_id,
            submitted_at=work.fueler_submitted_at_utc.isoformat(), version=work.fueler_submission_version)
    logged_work = getattr(result, "fuel_work_state", None)
    logged_assignment = logged_work.fuel_assignment if logged_work else None
    current_app.logger.info("scorpion_action_committed action=%s actor=%s operation=%s mission=%s assignment=%s work=%s cycle=%s sequence=%s tail=%s truck=%s revision=%s changed=%s",
        "submit" if submit else "on" if truck_on else "off" if off else "new_visit" if new_visit else "fuel_data",
        current_user.id, getattr(logged_assignment,"sort_date_operation_id",None),
        getattr(logged_assignment,"sort_date_mission_id",None), assignment_id,
        getattr(logged_work,"id",None), getattr(logged_work,"physical_cycle_number",None),
        getattr(logged_work,"physical_sequence_number",None), getattr(logged_work,"tail_number",None),
        getattr(logged_work,"physical_truck_id",None), result.revision, result.changed)
    return _fuel_data_payload(gateway, assignment_id, dispatcher, submission_receipt=receipt)


@bp.route("/fueler/assignments/<int:assignment_id>", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def fueler_assignment_data(assignment_id):
    return _fuel_data_request(assignment_id)


@bp.post("/fueler/assignments/<int:assignment_id>/submit")
@gateway_node_required("scorpion")
def fueler_assignment_submit(assignment_id):
    return _fuel_data_request(assignment_id, submit=True)


@bp.post("/fueler/assignments/<int:assignment_id>/off")
@gateway_node_required("scorpion")
def fueler_assignment_off(assignment_id):
    return _fuel_data_request(assignment_id, off=True)


@bp.post("/fueler/assignments/<int:assignment_id>/on")
@gateway_node_required("scorpion")
def fueler_assignment_on(assignment_id):
    return _fuel_data_request(assignment_id, truck_on=True)


@bp.post("/fueler/assignments/<int:assignment_id>/new-visit")
@gateway_node_required("scorpion")
def fueler_new_visit(assignment_id):
    return _fuel_data_request(assignment_id, new_visit=True)


@bp.route("/fuel-dispatch/fueler-data/<int:assignment_id>", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def dispatch_fueler_data(assignment_id):
    return _fuel_data_request(assignment_id, dispatcher=True)


@bp.post("/fuel-dispatch/fueler-data/<int:assignment_id>/off")
@gateway_node_required("scorpion")
def dispatch_fueler_off(assignment_id):
    return _fuel_data_request(assignment_id, dispatcher=True, off=True)


@bp.get("/fueler/live-panel")
@gateway_node_required("scorpion")
def fuel_assignments_live_panel():
    gateway = get_current_gateway()
    access = permission_access(FUELER_VIEW_PERMISSION, FUELER_EDIT_PERMISSION)
    if not access["can_view"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)

    notes_revision = tail_notes_revision(gateway)
    context = fueler_context(gateway, current_user)
    operation = context["operation"]
    return _json_no_store(
        {
            "ok": True,
            "operation_id": operation.id if operation else None,
            "revision": int(context.get("fuel_assignments_revision") or 0),
            "tail_notes_revision": notes_revision,
            "html": render_template(
                "neonodes/neoscorpion/_fueler_panel.html",
                can_edit=access["can_edit"],
                **context,
            ),
        }
    )


@bp.get("/fueling-board")
@gateway_node_required("scorpion")
def fueling_board():
    if not permission_access(FUELING_BOARD_VIEW_PERMISSION)["can_view"]:
        return "Access denied.", 403
    gateway = get_current_gateway()
    return render_template(
        "neonodes/neoscorpion/fueling_board.html",
        gateway=gateway,
        **fueling_board_context(gateway, current_user),
    )


def _driver_page_context(gateway):
    manage = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)["can_edit"]
    return dict(driver_context(gateway,current_user,manage=manage),gateway=gateway,can_manage_drivers=False,
        can_select_driver_truck=permission_access(DRIVER_VIEW,DRIVER_EDIT)["can_edit"])


@bp.get("/truck-driver")
@gateway_node_required("scorpion")
def truck_driver():
    if not permission_access(DRIVER_VIEW)["can_view"]:
        return "Access denied.",403
    return render_template("neonodes/neoscorpion/truck_driver.html",**_driver_page_context(get_current_gateway()))


@bp.post("/truck-driver/assignment")
@gateway_node_required("scorpion")
def truck_driver_assignment():
    gateway=get_current_gateway()
    manage=request.form.get("manage") == "1"
    access=(permission_access(FUEL_DISPATCH_VIEW_PERMISSION,FUEL_DISPATCH_EDIT_PERMISSION)
            if manage else permission_access(DRIVER_VIEW,DRIVER_EDIT))
    permitted=access["can_view"] and access["can_edit"]
    if not permitted:
        return _json_no_store({"ok":False,"error":"Access denied."},403)
    try:
        operation=current_sort_operation(gateway)
        if not operation or str(operation.id)!=str(request.form.get("operation_id")):
            raise ValueError("The current sort changed. Refresh before choosing a truck.")
        if not {"expected_driver_id","expected_driver_truck_id"}.issubset(request.form):
            raise ValueError("Refresh before choosing a truck.")
        changed,revision=change_driver(gateway,operation,current_user,request.form.get("truck_id"),
            request.form.get("driver_user_id") if manage else current_user.id,dispatcher=manage,
            expected_driver_id=request.form.get("expected_driver_id"),
            expected_driver_truck_id=request.form.get("expected_driver_truck_id"),
            override=request.form.get("override") == "1",reason=request.form.get("reason"))
        db.session.commit()
    except (ValueError,TypeError,IntegrityError) as exc:
        db.session.rollback()
        error="Driver selection changed. Refresh and try again." if isinstance(exc,IntegrityError) else str(exc)
        if request.accept_mimetypes.best == "application/json":
            return _json_no_store({"ok":False,"error":error},409)
        flash(error,"error")
        return redirect(url_for("neoscorpion.truck_driver"))
    if request.accept_mimetypes.best == "application/json":
        return _json_no_store({"ok":True,"changed":changed,"revision":revision,"operation_id":operation.id})
    return redirect(url_for("neoscorpion.truck_driver"))


@bp.get("/truck-driver/live-panel")
@gateway_node_required("scorpion")
def truck_driver_live_panel():
    if not permission_access(DRIVER_VIEW)["can_view"]:
        return _json_no_store({"ok":False,"error":"Access denied."},403)
    context=_driver_page_context(get_current_gateway())
    operation=context["operation"]
    return _json_no_store({"ok":True,"operation_id":operation.id if operation else None,
        "revision":context["fuel_assignments_revision"],
        "html":render_template("neonodes/neoscorpion/_truck_driver_panel.html",**context)})


@bp.get("/truck-driver/revision")
@gateway_node_required("scorpion")
def truck_driver_revision():
    if not permission_access(DRIVER_VIEW)["can_view"]:
        return _json_no_store({"ok":False,"error":"Access denied."},403)
    return _json_no_store({"ok":True,**fuel_assignments_live_revision(get_current_gateway())})


@bp.get("/fueling-board/live-panel")
@gateway_node_required("scorpion")
def fueling_board_live_panel():
    if not permission_access(FUELING_BOARD_VIEW_PERMISSION)["can_view"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    gateway = get_current_gateway()
    context = fueling_board_context(gateway, current_user)
    operation = context["operation"]
    return _json_no_store({
        "ok": True,
        "operation_id": operation.id if operation else None,
        "revision": int(context["fuel_assignments_revision"] or 0),
        "html": render_template("neonodes/neoscorpion/_fueling_board_panel.html", **context),
    })


@bp.get("/fueling-board/revision")
@gateway_node_required("scorpion")
def fueling_board_revision():
    if not permission_access(FUELING_BOARD_VIEW_PERMISSION)["can_view"]:
        return _json_no_store({"ok": False, "error": "Access denied."}, 403)
    return _json_no_store({"ok": True, **fuel_assignments_live_revision(get_current_gateway())})


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
            "tail_notes_revision": tail_notes_revision(gateway),
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
    fingerprint["tail_revision"] = _dispatch_tail_revision(fingerprint["operation_id"])
    fingerprint["tail_notes_revision"] = tail_notes_revision(gateway)
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
                    changed_by_user=current_user,
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


@bp.route("/settings/spear", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def spear_settings():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION, SETTINGS_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _spear_settings_response(gateway, access, status_code=403)
        previous_learning_capture = effective_spear_settings(
            NeoScorpionSettings.query.filter_by(gateway_id=gateway.id).first()
        ).learning_capture_enabled
        try:
            result = save_spear_settings(gateway, current_user, request.form)
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            flash(
                str(exc) if isinstance(exc, ValueError) else "SPEAR settings changed. Reload and try again.",
                "error",
            )
            return _spear_settings_response(gateway, access, status_code=400)
        db.session.commit()
        learning_capture_now = request.form.get("learning_capture_enabled") == "1"
        if learning_capture_now and not previous_learning_capture:
            operation = current_sort_operation(gateway)
            if operation is not None:
                _backfill_learning_after_enable(gateway, operation.id)
        flash("SPEAR SETTINGS SAVED." if result.changed else "NO SPEAR SETTING CHANGES.", "success" if result.changed else "info")
        if result.automation_just_enabled:
            return redirect(url_for("neoscorpion.fuel_dispatch"))
        return redirect(url_for("neoscorpion.spear_settings"))

    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.settings"))
    return _spear_settings_response(gateway, access)


@bp.route("/settings/spear/calibration", methods=["GET", "POST"])
@gateway_node_required("scorpion")
def spear_calibration():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION, SETTINGS_EDIT_PERMISSION)
    if request.method == "POST":
        if not access["can_edit"]:
            db.session.rollback()
            flash("Access denied.", "error")
            return _spear_calibration_response(gateway, access, status_code=403)
        context = fuel_dispatch_context(gateway)
        operation = context["operation"]
        if operation is None:
            flash("No current sort operation is available.", "error")
            return _spear_calibration_response(gateway, access, status_code=400)
        try:
            reset_live_calibration(
                operation, request.form.get("metric"), request.form.get("scope_key"), current_user
            )
            db.session.commit()
        except (IntegrityError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc) if isinstance(exc, ValueError) else "Calibration changed. Reload and try again.", "error")
            return _spear_calibration_response(gateway, access, status_code=400)
        flash("SPEAR LIVE CALIBRATION RESET TO CONFIGURED BASELINE.", "success")
        return redirect(url_for("neoscorpion.spear_calibration"))
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.spear_settings"))
    return _spear_calibration_response(gateway, access)


@bp.post("/settings/spear/vault/test")
@gateway_node_required("scorpion")
def spear_vault_test():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION, SETTINGS_EDIT_PERMISSION)
    if not access["can_edit"]:
        flash("Access denied.", "error")
        return _spear_settings_response(gateway, access, status_code=403)
    try:
        test_learning_vault_connection()
        flash("SPEAR VAULT CONNECTION VERIFIED.", "success")
    except (LearningVaultUnavailable, ValueError):
        flash("SPEAR Vault connection failed. Check the R2 bucket, credentials, and Render configuration.", "error")
    return redirect(url_for("neoscorpion.spear_settings"))


@bp.post("/settings/spear/calibration/archive")
@gateway_node_required("scorpion")
def spear_calibration_archive():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION, SETTINGS_EDIT_PERMISSION)
    if not access["can_edit"]:
        flash("Access denied.", "error")
        return _spear_calibration_response(gateway, access, status_code=403)
    dispatch = fuel_dispatch_context(gateway)
    operation = dispatch["operation"]
    if operation is None:
        flash("No current sort operation is available.", "error")
        return _spear_calibration_response(gateway, access, status_code=400)
    try:
        result = archive_calibration_review(
            calibration_review_payload(operation, {(item.metric, item.scope_key): item for item in dispatch["spear_calibration"]["items"]}),
            gateway=gateway, operation=operation, user=current_user,
            learning_capture_enabled=dispatch["spear_settings"].learning_capture_enabled,
        )
        flash("ALREADY SAVED TO SPEAR VAULT." if result["already_saved"] else "SAVED TO SPEAR VAULT.", "info" if result["already_saved"] else "success")
    except (LearningVaultUnavailable, ValueError):
        flash("SPEAR Vault connection failed. Check the R2 bucket, credentials, and Render configuration.", "error")
    return redirect(url_for("neoscorpion.spear_calibration"))


@bp.get("/settings/spear/vault")
@gateway_node_required("scorpion")
def spear_vault():
    gateway = get_current_gateway()
    access = permission_access(SETTINGS_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.spear_settings"))
    status = learning_vault_status()
    reviews = ()
    learning_records = ()
    payload = None
    payload_kind = None
    try:
        if status.configured:
            learning_key = request.args.get("learning", "")
            review_key = request.args.get("review", "")
            if learning_key:
                payload = read_learning_record(learning_key)
                payload_kind = "learning"
            elif review_key:
                payload = read_calibration_review(review_key)
                payload_kind = "review"
            else:
                learning_records = list_learning_records()
                reviews = list_calibration_reviews()
    except (LearningVaultUnavailable, ValueError):
        flash("SPEAR Vault connection failed. Check the R2 bucket, credentials, and Render configuration.", "error")
    return render_template(
        "neonodes/neoscorpion/spear_vault.html",
        gateway=gateway,
        can_edit=user_can(SETTINGS_EDIT_PERMISSION),
        spear_learning_vault=status,
        reviews=reviews,
        learning_records=learning_records,
        review_payload=payload if payload_kind == "review" else None,
        learning_payload=payload if payload_kind == "learning" else None,
    )


@bp.post("/fuel-dispatch/spear-action")
@gateway_node_required("scorpion")
def fuel_dispatch_spear_action():
    gateway = get_current_gateway()
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    json_response = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not access["can_edit"]:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": "Access denied."}, 403)
        flash("Access denied.", "error")
        return _dispatch_response(gateway, access, status_code=403)
    try:
        context = fuel_dispatch_context(
            gateway, include_asset_choices=True, display_nicknames=False,
            include_fueling_performance=False,
        )
        operation = context["operation"]
        plan = context["spear_plan"]
        if operation is None or plan is None:
            raise ValueError("No current SPEAR plan is available.")
        automatic = request.form.get("execution_mode") == "automatic"
        if automatic and not context["spear_settings"].automation_enabled:
            raise ValueError("SPEAR Automation is off.")
        if request.form.get("plan_token") != plan.token:
            raise ValueError("SPEAR plan changed. Review the current recommendation.")
        requested_mission_id = _positive_form_id(request.form.get("mission_id"), "mission") if request.form.get("mission_id") else None
        requested_truck_id = _positive_form_id(request.form.get("truck_id"), "fuel truck") if request.form.get("truck_id") else None
        step = next(
            (
                item for item in plan.steps
                if (automatic and item.automatic_eligible)
                or (
                    not automatic
                    and item.mission_id == requested_mission_id
                    and item.truck_id == requested_truck_id
                )
            ),
            None,
        )
        if step is None:
            raise ValueError("SPEAR recommendation is no longer available.")
        if step.action_type == 'assign' and step.requires_resource_release:
            raise ValueError('Future SPEAR assignment requires verified resource release / TOP OFF RETURN. Review the current plan.')
        approved_top_off = False
        if step.action_type == 'top_off':
            from app.services.neoscorpion_spear import validate_top_off_approval
            approved_top_off = not automatic and validate_top_off_approval(
                request.form.get('dispatcher_approval'), current_user.id, operation.id, plan, step)
            if not approved_top_off:
                raise ValueError('TOP OFF SENT requires a current Dispatcher approval.')
            from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation
            from app.services.neoscorpion_spear_decisions import prepare_scope
            operation, locked_assets = lock_nightly_asset_scope_for_mutation(operation)
            source_row = next((row for row in context['rows'] if row['mission'].id == step.mission_id), None)
            fleet_before = prepare_scope(operation, source_row['mission'], source_row.get('assignment'), locked_assets.revision) if source_row else dict(operation_id=operation.id,mission_id=step.mission_id)
        assignment_id = None

        def assign_action(selected_step):
            row = next(
                row
                for row in context["rows"]
                if row["mission"].id == selected_step.mission_id
            )
            assignment = row["assignment"]
            if assignment is not None and row.get("work_has_begun"):
                swap_result = None
                if assignment.assigned_fueler_user_id != selected_step.fueler_id:
                    swap_result = swap_assignment_fueler(
                        gateway,
                        current_user,
                        assignment.id,
                        selected_step.fueler_id,
                    )
                if assignment.assigned_truck_id != selected_step.truck_id:
                    swap_result = swap_assignment_truck(
                        gateway,
                        current_user,
                        assignment.id,
                        selected_step.truck_id,
                    )
                if swap_result is None:
                    raise ValueError("SPEAR replacement is already current.")
                return swap_result
            return save_dispatch_assignment(
                gateway,
                {
                    "mission_id": str(selected_step.mission_id),
                    "expected_assigned_fueler_user_id": str(assignment.assigned_fueler_user_id) if assignment and assignment.assigned_fueler_user_id else "",
                    "expected_assigned_truck_id": str(assignment.assigned_truck_id) if assignment and assignment.assigned_truck_id else "",
                    "assigned_fueler_user_id": str(selected_step.fueler_id or ""),
                    "assigned_truck_id": str(selected_step.truck_id or ""),
                    "review_status": "assigned",
                    "spear_decision_reference": request.form.get("spear_decision_reference"),
                },
            )

        def top_off_action(selected_step):
            return mark_nightly_truck_topping_off(
                operation, selected_step.truck_id, changed_by_user=current_user
            )

        result = execute_spear_step(
            step, assign_action=assign_action, top_off_action=top_off_action, dispatcher_approved=approved_top_off
        )
        changed = result.changed
        if step.action_type == "assign":
            assignment_id = result.assignment.id
        if changed:
            spear_audit = record_spear_execution(
                operation, step, current_user, automatic=automatic, assignment_id=assignment_id
            )
            if step.action_type == 'top_off':
                from app.services.neoscorpion_spear_decisions import observe_fleet
                observe_fleet(gateway, operation, spear_audit, step, current_user, fleet_before)
            db.session.commit()
        if json_response:
            return _json_no_store({"ok": True, "changed": changed})
        flash("SPEAR ACTION ASSIGNED." if changed else "SPEAR ACTION ALREADY CURRENT.", "success" if changed else "info")
        return redirect(url_for("neoscorpion.fuel_dispatch"))
    except (IntegrityError, StopIteration, ValueError) as exc:
        db.session.rollback()
        message = str(exc) if isinstance(exc, ValueError) else "SPEAR plan changed. Reload and review it."
        if json_response:
            return _json_no_store({"ok": False, "error": message}, 409 if "changed" in message.lower() else 400)
        flash(message, "error")
        return redirect(url_for("neoscorpion.fuel_dispatch"))


@bp.route("/reports")
@gateway_node_required("scorpion")
def reports():
    gateway = get_current_gateway()
    access = permission_access(HISTORY_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    return render_template(
        "neonodes/neoscorpion/reports.html",
        gateway=gateway,
        can_view=access["can_view"],
    )


@bp.route("/reports/fuel")
@gateway_node_required("scorpion")
def fuel_report():
    gateway = get_current_gateway()
    access = permission_access(HISTORY_VIEW_PERMISSION)
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neoscorpion.index"))
    context = fuel_report_context(gateway)
    if request.args.get("format") == "pdf":
        from app.services.neoscorpion_fuel_report_pdf import fuel_report_pdf
        return send_file(fuel_report_pdf(context), mimetype="application/pdf",
                         as_attachment=True, download_name="fuel-report.pdf", max_age=0)
    return render_template(
        "neonodes/neoscorpion/fuel_report.html",
        gateway=gateway,
        can_view=access["can_view"],
        **context,
    )


@bp.get("/spear/scorecard")
@gateway_node_required("scorpion")
def spear_scorecard():
    from app.models import SortDateOperation
    from app.services.neoscorpion_spear_scorecard import report_context
    gateway = get_current_gateway()
    if not user_can(HISTORY_VIEW_PERMISSION):
        return "Access denied.", 403
    choices = SortDateOperation.query.filter_by(gateway_id=gateway.id).order_by(
        SortDateOperation.sort_date.desc(), SortDateOperation.id.desc()).limit(200).all()
    operation = current_sort_operation(gateway) or (choices[0] if choices else None)
    try:
        if request.args.get('operation_id'):
            operation = SortDateOperation.query.filter_by(id=int(request.args['operation_id']), gateway_id=gateway.id).first()
            if operation is None:
                return "Sort not found for this gateway.", 404
        truck = int(request.args['truck_id']) if request.args.get('truck_id') else None
        fueler = int(request.args['fueler_id']) if request.args.get('fueler_id') else None
        archive = read_learning_record(request.args['learning']) if request.args.get('learning') else None
        context = report_context(gateway, operation, truck_id=truck, fueler_id=fueler, archive=archive)
    except (ValueError, LearningVaultUnavailable):
        return "Evidence unavailable or invalid for the selected gateway, sort or resource. Return to SPEAR and select again.", 400
    return render_template('neonodes/neoscorpion/spear_scorecard.html', gateway=gateway,
                           operation=operation, operations=choices, learning=request.args.get('learning', ''), **context)


@bp.get("/reports/prof")
@gateway_node_required("scorpion")
def prof_report():
    from app.models import SortDateOperation
    from app.services.neoscorpion_prof import report_context
    gateway = get_current_gateway()
    if not user_can(HISTORY_VIEW_PERMISSION):
        return "Access denied.", 403
    choices = SortDateOperation.query.filter_by(gateway_id=gateway.id).order_by(
        SortDateOperation.sort_date.desc(), SortDateOperation.id.desc()).limit(200).all()
    current = current_sort_operation(gateway)
    operation = current or (choices[0] if choices else None)
    try:
        if request.args.get("operation_id"):
            operation = SortDateOperation.query.filter_by(id=int(request.args["operation_id"]), gateway_id=gateway.id).first()
            if operation is None:
                return "Sort not found for this gateway.", 404
        fueler = int(request.args["fueler_id"]) if request.args.get("fueler_id") else None
        truck = int(request.args["truck_id"]) if request.args.get("truck_id") else None
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        return "Invalid report selection.", 400
    context = report_context(gateway, operation, fueler_id=fueler, truck_id=truck, page=page)
    if request.args.get("format") == "csv":
        output = io.StringIO(newline="")
        rows = [job["columns"] for job in context["all_jobs"]]
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow({key: "'"+value if isinstance(value,str) and value.startswith(("=","+","-","@")) else value for key,value in row.items()})
        return send_file(io.BytesIO(output.getvalue().encode("utf-8-sig")), mimetype="text/csv",
                         as_attachment=True, download_name=f"prof-{operation.sort_date if operation else 'empty'}.csv", max_age=0)
    return render_template("neonodes/neoscorpion/prof_report.html", gateway=gateway,
        operation=operation, operations=choices, fueler_id=fueler, truck_id=truck, **context)


@bp.get("/reports/truck-rolldown")
@gateway_node_required("scorpion")
def truck_rolldown():
    from app.models import SortDateOperation
    from app.services.neoscorpion_rolldown import report_context
    gateway = get_current_gateway()
    if not user_can(HISTORY_VIEW_PERMISSION):
        return "Access denied.", 403
    choices = SortDateOperation.query.filter_by(gateway_id=gateway.id).order_by(
        SortDateOperation.sort_date.desc(), SortDateOperation.id.desc()).limit(200).all()
    operation = current_sort_operation(gateway) or (choices[0] if choices else None)
    try:
        if request.args.get("operation_id"):
            operation = SortDateOperation.query.filter_by(id=int(request.args['operation_id']),gateway_id=gateway.id).first()
            if operation is None:
                return "Sort not found for this gateway.", 404
        truck_id = int(request.args['truck_id']) if request.args.get('truck_id') else None
        context = report_context(gateway, operation, truck_id=truck_id)
    except ValueError as exc:
        return str(exc), 400
    if request.args.get('format') == 'csv':
        output = io.StringIO(newline='')
        rows = context['export_rows']
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow({key:"'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value
                                 for key,value in row.items()})
        return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')),mimetype='text/csv',
                         as_attachment=True,download_name=f'truck-rolldown-{operation.sort_date if operation else "empty"}.csv',max_age=0)
    return render_template('neonodes/neoscorpion/truck_rolldown.html', gateway=gateway,
        operation=operation, operations=choices, truck_id=truck_id, **context)


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


def _dispatch_presentation_context(gateway, *, include_asset_choices=False, include_spear=True):
    # All projections, scheduling and SPEAR enrichment finish in their existing
    # order. Sort a new list only for these two Dispatch render paths.
    context = fuel_dispatch_context(gateway, include_asset_choices=include_asset_choices, include_spear=include_spear)
    rows = [dict(row) for row in context["rows"]]
    # Browser reorders existing DOM groups using ranks derived from full UTC
    # timestamps, never formatted clock strings or operational planning order.
    from app.services.neoscorpion_dispatch_preferences import dispatch_preferences
    preferences = dispatch_preferences(gateway, current_user)
    for mode in ("arrival", "etd"):
        for rank, row in enumerate(sorted(rows, key=lambda row: _dispatch_presentation_key(row, mode, False))):
            row[f"dispatch_{mode}_rank"] = rank
    from app.models import NeoScorpionVendorRequest, NeoScorpionSortTruck, NeoScorpionFuelingEvent
    from sqlalchemy import func
    from app.services.neoscorpion_vendors import driver_name
    operation=context.get('operation')
    roster=driver_roster(gateway,operation)
    pending={}
    if operation:
        for item in NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id,kind='fuel',status='pending').all():
            pending.setdefault(item.fuel_truck_id,[]).append(item)
        selections={s.fuel_truck_id:s for s in NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id).all()}
        postings=dict(db.session.query(NeoScorpionFuelingEvent.fuel_truck_id,func.max(NeoScorpionFuelingEvent.id)).filter_by(
            sort_date_operation_id=operation.id).group_by(NeoScorpionFuelingEvent.fuel_truck_id).all())
        for visual in context.get('truck_visuals',[]):
            selection=selections.get(visual['truck_id'])
            from app.services.neoscorpion_assets import top_off_action_state
            visual['top_off_action_state'] = top_off_action_state(selection) if selection else ''
            visual['inventory_epoch']=selection.inventory_epoch if selection else None
            visual['inventory_posting_id']=postings.get(visual['truck_id'],0)
            visual['driver_name']=driver_name(gateway,operation,visual['truck_id'],roster['drivers_by_truck_id'])
    # Presentation-only pointers to existing truck controls: never duplicate
    # inventory/approval mutations inside a mission's DETAILS.
    dispatch_editable = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)['can_edit']
    driver_admin = None
    if dispatch_editable and include_asset_choices:
        from app.neonodes.neoscorpion.vendor_routes import driver_management_context
        driver_admin = driver_management_context(gateway, operation)
    for row in rows:
        truck = row.get('assigned_truck')
        visual = next((v for v in context.get('truck_visuals', []) if truck and v['truck_id'] == truck.id), None)
        reasons = []
        if truck and not row.get('administratively_complete') and pending.get(truck.id):
            reasons.append('Fuel verification awaiting Dispatcher approval')
        if visual and not row.get('administratively_complete') and visual.get('status') in ('needs_sump', 'unavailable_oos'):
            reasons.append('Serviceability: ' + visual['status'].replace('_', ' ').upper())
        if visual and not row.get('administratively_complete') and visual.get('inventory_reconciliation_required') and visual.get('status') == 'available':
            reasons.append('Truck inventory discrepancy / unknown baseline')
        row['console_truck_review'] = ' · '.join(reasons)
        row['action_count'] += int(bool(row.get('call_dispatch_alert') and dispatch_editable))
        row['action_count'] += int(bool(reasons))
        # Presentation only: missing row data is not an additional mandatory
        # action. Existing canonical eligibility and mutation guards still rule.
        row['details_action_count'] = max(0, row['action_count'] - int(row['mission_data_action']))
        row['details_action_count'] += int(bool(row.get('confirm_tail_available')))
        # Current-visit verification is already directly on the mission row.
        # DETAILS counts only actions actually offered inside its workspace.
        if not row.get('assignment'):
            second_reason = 'Assign the mission first.'
        elif row.get('administratively_complete'):
            second_reason = 'Use UPLIFT after completed physical fueling.'
        elif row.get('pending_uplift') and row['pending_uplift'].plan_kind=='uplift':
            second_reason = 'Review the saved post-completion uplift first.'
        elif row.get('cycle_type') == 'defuel':
            second_reason = 'Complete or review DEFUEL first.'
        elif not row['mission'].planned_fuel_load:
            second_reason = 'Enter Required Fuel first.'
        else:
            second_reason = ''
        row['second_truck_reason'] = second_reason
        if row.get('fuel_on_board_complete'):
            follow_reason = 'FOB does not establish a physical fueling event.'
        elif row.get('pending_uplift'):
            follow_reason = 'Resolve the saved truck / uplift plan first.'
        elif not row.get('normal_completion_complete'):
            follow_reason = 'Dispatcher COMPLETE required.'
        elif not row.get('has_prior_fueling_events'):
            follow_reason = 'A completed physical fueling event is required.'
        elif not row.get('follow_up_available') or row['assignment'].review_status != 'complete' or row['mission'].fuel_status != 'complete':
            follow_reason = 'Review the completed cycle and confirmed aircraft.'
        else:
            follow_reason = ''
        row['follow_up_reason'] = follow_reason
    return {**context, 'asset_driver_admin':driver_admin, 'dispatch_preferences':preferences, 'vendor_pending_by_truck':pending,
        "rows": sorted(rows, key=lambda row: _dispatch_presentation_key(row, preferences['secondary_sort'], preferences['arrived_first'])), **roster}


def _dispatch_presentation_key(row, mode="etd", arrived_first=True):
    from app.services.neoscorpion_fueling_status import utc_naive
    mission, arrival = row["mission"], row.get("arrival_mission")
    completed = row.get("dispatch_status_key") in {"complete", "fob"}
    etd = utc_naive(mission.eta_datetime_utc or mission.planned_datetime_utc)
    if mode == "etd":
        stamp = etd
    else:
        stamp = (getattr(arrival, "actual_block_in_datetime_utc", None)
                 or getattr(arrival, "eta_datetime_utc", None)
                 or getattr(arrival, "planned_datetime_utc", None))
    present = str(row.get('arrival_status') or '').strip().casefold() in {'arrived', 'on ground'}
    return (completed, not present if arrived_first and not completed else False, stamp is None,
            utc_naive(stamp) or datetime.max,
            etd or datetime.max,
            mission.flight_number or "", mission.id)


def _dispatch_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/fuel_dispatch.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        can_manage_assets=access["can_edit"],
        can_view_spear_settings=user_can(SETTINGS_VIEW_PERMISSION),
        **_dispatch_presentation_context(
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
        **settings_context(gateway),
    )
    return response, status_code


def _spear_settings_response(gateway, access, status_code=200):
    response = render_template(
        "neonodes/neoscorpion/spear_settings.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        **settings_context(gateway),
    )
    return response, status_code


def _spear_calibration_response(gateway, access, status_code=200):
    dispatch = fuel_dispatch_context(gateway)
    calibration = dispatch["spear_calibration"]
    response = render_template(
        "neonodes/neoscorpion/spear_calibration.html",
        gateway=gateway,
        can_view=access["can_view"],
        can_edit=access["can_edit"],
        operation=dispatch["operation"],
        spear_calibration=calibration,
        spear_calibration_review=calibration_review_payload(
            dispatch["operation"], { (item.metric, item.scope_key): item for item in calibration["items"] }
        ),
        **settings_context(gateway),
    )
    return response, status_code


def _run_dispatch_lifecycle_action(action, success_message):
    gateway = get_current_gateway()
    access = permission_access(FUEL_DISPATCH_VIEW_PERMISSION, FUEL_DISPATCH_EDIT_PERMISSION)
    json_response = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"
    status_code, message, result = 200, success_message, None
    if not access["can_edit"]:
        status_code, message = 403, "Access denied."
    else:
        try:
            result = action(gateway)
        except (IntegrityError, ValueError) as exc:
            status_code = 400
            message = str(exc) if isinstance(exc, ValueError) else "Fuel assignment changed. Refresh and review the current state."
    if status_code != 200:
        db.session.rollback()
        if json_response:
            return _json_no_store({"ok": False, "error": message}, status_code)
        flash(message, "error")
        return _dispatch_response(gateway, access, status_code=status_code)
    if result.changed:
        db.session.commit()
    else:
        db.session.rollback()
    if json_response:
        return _json_no_store({"ok": True, "changed": result.changed,
            "operation_id": result.assignment.sort_date_operation_id, "revision": result.revision})
    flash(message if result.changed else "NO CHANGES.", "success" if result.changed else "info")
    return redirect(url_for("neoscorpion.fuel_dispatch"))


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
    preload_permission_rules(item.permission for item in NEOSCORPION_MENU if item.permission)
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
    if action == "set_fueler_nickname":
        return set_nightly_fueler_nickname(
            operation, gateway,
            _positive_form_id(form.get("user_id"), "fueler"),
            form.get("nickname"), form.get("expected_nickname"),
        )
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
            starting_gallons=form.get("current_gallons"),
            current_gallons=form.get("current_gallons"),
            changed_by_user=current_user,
        )
    if action == "update_truck":
        # An already-open pre-release browser still submits the retired input.
        # Never report its edited balance as saved while silently ignoring it.
        if "current_gallons" in form:
            from app.models import NeoScorpionSortTruck
            selected=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation.id,
                fuel_truck_id=_positive_form_id(form.get("fuel_truck_id"),"fuel truck")).first()
            stored=str(selected.current_gallons) if selected and selected.current_gallons is not None else ''
            if str(form.get('current_gallons') or '').strip() != stored:
                raise ValueError('Current Gallons corrections moved to the Dispatch truck card. Refresh before correcting the balance.')
        return update_nightly_truck(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            status=form.get("status"),
            changed_by_user=current_user,
        )
    if action == "verify_inventory":
        return verify_nightly_truck_inventory(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            form.get("current_gallons"),
            changed_by_user=current_user,
        )
    if action == "mark_topping_off":
        return mark_nightly_truck_topping_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            changed_by_user=current_user,
            expected_state=form.get("top_off_action_state"),
        )
    if action == "complete_top_off":
        return complete_nightly_truck_top_off(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            form.get("current_gallons"),
            changed_by_user=current_user,
            expected_state=form.get("top_off_action_state"),
        )
    if action == "mark_sumped":
        return mark_nightly_truck_sumped(
            operation,
            _positive_form_id(form.get("fuel_truck_id"), "fuel truck"),
            form.get("current_gallons"),
            changed_by_user=current_user,
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


@bp.after_request
def _spear_learning_delivery(response):
    # Background only, bounded and rate limited; no external I/O in the request.
    if request.endpoint == 'neoscorpion.spear_scorecard':
        return response  # The scorecard never reconciles, trains or schedules delivery.
    if getattr(current_user, 'is_authenticated', False):
        try:
            from app.services.neoscorpion_spear_decisions import schedule_delivery
            gateway = get_current_gateway()
            if gateway:
                schedule_delivery(current_app._get_current_object(), gateway.id)
        except Exception:
            current_app.logger.warning('SPEAR learning retry scheduling deferred safely')
    return response


@bp.post('/fuel-dispatch/spear-decision-reason')
@gateway_node_required('scorpion')
def spear_decision_reason():
    if not user_can(FUEL_DISPATCH_VIEW_PERMISSION) or not user_can(FUEL_DISPATCH_EDIT_PERMISSION):
        return 'Access denied.', 403
    gateway = get_current_gateway()
    from app.services.neoscorpion_spear_decisions import add_reason
    try:
        from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation
        observation = db.session.get(NeoScorpionSpearObservation, request.form.get('decision_id'))
        operation = current_sort_operation(gateway)
        if not operation or not observation or observation.operation_id != operation.id:
            raise ValueError('Decision is outside the current sort.')
        add_reason(gateway, current_user, observation.id, request.form.get('reason'), request.form.get('note'))
        db.session.commit()
        flash('Optional SPEAR decision note saved.', 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('neoscorpion.fuel_dispatch'))
