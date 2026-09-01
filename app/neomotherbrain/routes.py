from datetime import date, datetime, timedelta, timezone
import json
import re

from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.auth.decorators import gateway_node_required
from app.auth.permissions import can_manage_system
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    MasterFlightSchedule,
    MotherBrainAlert,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    SortTimelineSettings,
)
from app.neomotherbrain import bp
from app.services.flight_rules import (
    crew_sections_for_tail_swap,
    default_required_crew_sections,
    derive_aircraft_type_from_tail_number,
    is_mission_crew_covered,
)
from app.services.flight_api import (
    FLIGHT_API_AUTO_POLL_CLIENT_HEADER,
    FLIGHT_API_AUTO_POLL_CLIENT_VERSION,
    FlightApiConfigurationError,
    accept_review_item,
    acquire_flight_api_auto_poll_lock,
    api_polling_window_snapshot,
    coordinate_flight_api_auto_poll_status,
    flight_api_auto_poll_preflight,
    flight_api_auto_poll_status,
    flight_api_last_poll_review,
    flight_api_operational_time_utc,
    flight_api_provider_time_utc,
    flight_api_review_display_rows,
    flight_api_review_reason_detail,
    format_flight_api_local_time,
    ignore_review_item,
    ops_node_online_window_snapshot,
    pending_review_items_for_operation,
    release_flight_api_auto_poll_lock,
    review_item_or_404,
    rapidapi_request_details,
    run_flight_api_import,
    run_flight_api_replay,
    sort_flight_lookup_window_snapshot,
    taxi_to_ramp_minutes,
    _utc_to_local_naive as flight_api_utc_to_local_naive,
)
from app.services.access_control import (
    get_current_gateway,
    get_user_node_role,
    prime_user_node_roles_for_request,
    user_can_access_node,
    user_has_gateway_access,
)
from app.services.alp_import import (
    alp_flight_key,
    apply_alp_paste,
    normalize_alp_flight_number,
    preview_alp_paste,
)
from app.services.alp_preview_state import (
    clear_alp_preview_state,
    get_alp_preview_state,
    save_alp_preview_state,
)
from app.services.sort_date_operations import (
    apply_master_planned_times_to_mission,
    ensure_tail_state_for_mission,
    generate_sort_date_operation_from_master,
    mission_display_timing_data,
    normalize_optional_window_minutes,
    normalize_wave,
)
from app.services.gateway_matrix import (
    DAY_OPTIONS as MATRIX_DAY_OPTIONS,
    SORT_OPTIONS as MATRIX_SORT_OPTIONS,
    current_gateway_local_datetime,
    current_operations_for_gateway,
    matrix_state_for_gateway,
    save_gateway_matrix,
)
from app.services.night_sorting import (
    master_schedule_sort_key,
    mission_board_sort_key,
    sort_datetime_for_local_time,
)
from app.services.permission_rules import permission_access, user_can
from app.services.parking_plan import (
    PARKING_RAMP_GROUPS,
    STANDALONE_SPARE_AIRCRAFT_TYPE_OPTIONS,
    ParkingLaneOccupied,
    ParkingPlanOperationalStateBundle,
    ParkingPlanError,
    ParkingRuleConflict,
    assign_tail_to_lane,
    clear_parking_assignments,
    clear_spare_for_departure,
    clear_tail_spare,
    current_active_sort_operation,
    create_standalone_spare,
    mark_arrival_tail_spare,
    parking_plan_context,
    parking_plan_landing_context,
    remove_standalone_spare,
    set_tail_out_of_service,
    set_tail_hot,
    set_tail_operational_status,
    spare_rows_for_operation,
    tail_status_is_hot_for_operation,
    tail_operational_status_label,
    unassign_tail,
)
from app.services.parking_plan_collaboration import (
    ParkingStateConflict,
    optimizer_revision_conflict,
    parking_plan_live_state,
    parking_plan_revision,
    parking_snapshot_from_form,
    validate_parking_move_snapshot,
    validate_parking_source_snapshot,
)
from app.services.planning_collaboration import planning_state_revision
from app.services.operation_lifecycle import (
    ManualSortCreationError,
    create_manual_current_sort_operation,
    current_existing_operational_sort_operations,
    manual_current_sort_creation_status,
)
from app.services.operation_scope import operation_by_id
from app.services.live_collaboration import (
    changed_field_conflicts,
    entity_version,
    resolved_item_conflict,
    version_conflict,
)
from app.services.node_refresh import node_auto_refresh_status
from app.services.unmatched_review_alerts import (
    UNMATCHED_REVIEW_ALERT_PERMISSION,
    is_unmatched_review_alert,
    mark_unmatched_review_alert_read,
    pending_review_key_sets,
    sync_unmatched_review_alert,
    sync_unmatched_review_alerts_for_operation,
)
from app.services.google_motherbrain_import import (
    GoogleMotherBrainOperationError,
    GoogleMotherBrainPayloadError,
    build_google_motherbrain_preview,
    resolve_google_motherbrain_operation,
    validate_google_motherbrain_envelope,
)
from app.services.google_motherbrain_sheets import (
    GoogleMotherBrainReaderError,
    google_motherbrain_reader_status,
    read_google_motherbrain_envelope,
)
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_status,
    set_google_motherbrain_live_polling_enabled,
)
from app.services.google_motherbrain_live_poll_execution import (
    execute_google_motherbrain_live_poll,
)
from app.services.google_motherbrain_live_poll_health import (
    google_motherbrain_live_poll_health,
)
from app.services.google_rain_integration_mode import (
    RainIntegrationTransitionError,
    change_rain_integration_mode,
    rain_integration_status,
)
from app.services.my_alerts import my_alert_context
from app.services.neosektor_sheets_compat import (
    NeoSektorGoogleError,
    change_neosektor_integration_mode,
    neosektor_integration_status,
    retry_neosektor_google_mirror,
)
from app.services.parking_optimizer import (
    apply_parking_optimizer_plan,
    parking_optimizer_error_preview,
    parking_optimizer_default_options,
    parking_optimizer_preview,
)
from app.services.building_lineup_parking_preferences import (
    save_belt_pair_preferences_from_form,
)
from app.services.parking_rules import (
    AIRCRAFT_TYPE_RAMP_PREFERENCE,
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    ARRIVAL_PARKING_PREFERENCE,
    ARRIVAL_PARKING_REQUIREMENT,
    BLOCKED_PARKING_POSITION,
    DEPARTURE_PARKING_PREFERENCE,
    DEPARTURE_PARKING_REQUIREMENT,
    parking_rules_context,
    save_parking_rules_from_form,
)
from app.services.sort_timeline import (
    DAY_OPTIONS as TIMELINE_DAY_OPTIONS,
    SORT_OPTIONS as TIMELINE_SORT_OPTIONS,
    ensure_sort_timeline_settings,
    format_time as format_timeline_time,
    save_sort_timeline_from_form,
    sort_timeline_context,
)

ACTIVE_DAY_OPTIONS = (
    ("monday", "Monday"),
    ("tuesday", "Tuesday"),
    ("wednesday", "Wednesday"),
    ("thursday", "Thursday"),
    ("friday", "Friday"),
    ("saturday", "Saturday"),
    ("sunday", "Sunday"),
)

FLIGHT_API_REVIEW_VIEW_PERMISSION = "neomotherbrain.flight_api_review.view"
FLIGHT_API_REVIEW_EDIT_PERMISSION = "neomotherbrain.flight_api_review.edit"
FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION = "neomotherbrain.flight_api_auto_poll.trigger"
NEOGATEWAY_LANDING_VIEW_PERMISSION = "neogateway.landing.view"
DASHBOARD_VIEW_PERMISSION = "neomotherbrain.dashboard.view"
MANAGE_SORT_VIEW_PERMISSION = "neomotherbrain.manage_sort.view"
MANAGE_SORT_EDIT_PERMISSION = "neomotherbrain.manage_sort.edit"
ARRIVAL_PLANNING_VIEW_PERMISSION = "neomotherbrain.arrival_planning.view"
ARRIVAL_PLANNING_EDIT_PERMISSION = "neomotherbrain.arrival_planning.edit"
ARRIVAL_PLANNING_RUN_PERMISSION = "neomotherbrain.arrival_planning.run"
DEPARTURE_PLANNING_VIEW_PERMISSION = "neomotherbrain.departure_planning.view"
DEPARTURE_PLANNING_EDIT_PERMISSION = "neomotherbrain.departure_planning.edit"
DEPARTURE_PLANNING_RUN_PERMISSION = "neomotherbrain.departure_planning.run"
MASTER_SCHEDULE_VIEW_PERMISSION = "neomotherbrain.master_schedule.view"
MASTER_SCHEDULE_EDIT_PERMISSION = "neomotherbrain.master_schedule.edit"
GATEWAY_MATRIX_VIEW_PERMISSION = "neomotherbrain.gateway_matrix.view"
GATEWAY_MATRIX_EDIT_PERMISSION = "neomotherbrain.gateway_matrix.edit"
SORT_TIMELINE_VIEW_PERMISSION = "neomotherbrain.sort_timeline.view"
SORT_TIMELINE_EDIT_PERMISSION = "neomotherbrain.sort_timeline.edit"
MANAGE_API_VIEW_PERMISSION = "neomotherbrain.manage_api.view"
MANAGE_API_RUN_PERMISSION = "neomotherbrain.manage_api.run"
PARKING_RULES_VIEW_PERMISSION = "motherbrain.parking_rules.view"
PARKING_RULES_EDIT_PERMISSION = "motherbrain.parking_rules.edit"
PARKING_PLAN_VIEW_PERMISSION = "motherbrain.parking_plan.view"
PARKING_PLAN_EDIT_PERMISSION = "motherbrain.parking_plan.edit"
PARKING_OPTIMIZER_RUN_PERMISSION = "motherbrain.parking_optimizer.run"
PARKING_OPTIMIZER_APPLY_PERMISSION = "motherbrain.parking_optimizer.apply"
CANCELLED_MISSION_STATUS = "cancelled"

SORT_NAME_OPTIONS = (
    ("night", "Night"),
    ("twilight", "Twilight"),
    ("day", "Day"),
    ("sunrise", "Sunrise"),
)
SORT_NAMES = {value for value, _label in SORT_NAME_OPTIONS}
MISSION_TYPE_OPTIONS = (
    ("arrival", "Arrival"),
    ("departure", "Departure"),
)
MISSION_TYPES = {"arrival", "departure"}
WAVE_OPTIONS = (
    ("1", "1"),
    ("2", "2"),
)
WAVES = {value for value, _label in WAVE_OPTIONS}
MASTER_WAVE_OPTIONS = (
    ("", ""),
    *WAVE_OPTIONS,
)
MASTER_AIRCRAFT_TYPE_OPTIONS = ("", "A300", "747", "757", "767", "Other")
FUEL_STATUSES = ("", "waiting", "received", "assigned", "complete")
ARRIVAL_STATUSES = (
    "",
    "scheduled",
    "en_route",
    "on_ground",
    "arrived",
    "unloaded",
    CANCELLED_MISSION_STATUS,
)
DEPARTURE_STATUSES = (
    "",
    "scheduled",
    "loading",
    "last_uld_enroute",
    "ramp_load_complete",
    "crew_load_complete",
    "blocked_out",
    "departed",
    CANCELLED_MISSION_STATUS,
)
MASTER_SCHEDULE_BLANK_ROW_INDEX = "__index__"


def _permission_denied_redirect():
    flash("Access denied.", "error")
    return redirect(url_for("neomotherbrain.rfd_hub"))


def _permission_guard(permission_key):
    if user_can(permission_key):
        return None
    return _permission_denied_redirect()


@bp.route("/")
def dashboard():
    if current_user.is_authenticated:
        return redirect(url_for("auth.portal_dashboard"))
    return render_template("auth/login.html")


@bp.route("/rfd")
@login_required
def rfd_hub():
    gateway = get_current_gateway()
    if not user_has_gateway_access(current_user, gateway.code):
        return redirect(url_for("auth.access_pending"))
    if not user_can(NEOGATEWAY_LANDING_VIEW_PERMISSION):
        flash("NeoGateway landing access denied.", "error")
        return redirect(url_for("auth.portal_dashboard"))

    prime_user_node_roles_for_request(
        current_user,
        gateway.code,
        ("motherbrain", "sektor", "ermac", "scorpion", "rain"),
    )

    current_state = _current_sort_state(gateway)
    current_sort_operations = current_state["operations"]
    active_sort_operation = _selected_current_operation(
        current_sort_operations,
        operation_id=request.args.get("operation_id"),
    )
    if active_sort_operation:
        active_sort_status = "Active sort ready."
    else:
        active_sort_status = "No active sort configured for today."

    return render_template(
        "neomotherbrain/rfd_hub.html",
        gateway=gateway,
        gateway_active_sort_operations=current_sort_operations,
        gateway_active_sort_operation=active_sort_operation,
        gateway_active_sort_status=active_sort_status,
        motherbrain_role=get_user_node_role(current_user, gateway.code, "motherbrain"),
        can_enter_motherbrain=user_can_access_node(
            current_user,
            gateway.code,
            "motherbrain",
            minimum_role="simulator",
        ),
        can_launch_sektor=user_can_access_node(current_user, gateway.code, "sektor"),
        can_launch_ermac=user_can_access_node(current_user, gateway.code, "ermac"),
        can_launch_scorpion=user_can_access_node(current_user, gateway.code, "scorpion"),
        can_launch_rain=user_can_access_node(current_user, gateway.code, "rain"),
        can_launch_subzero=user_can_access_node(current_user, gateway.code, "subzero"),
    )


@bp.route("/rfd/sektor")
@gateway_node_required("sektor")
def sektor_launch():
    return redirect("https://neosektor.onrender.com/")


@bp.route("/motherbrain")
@gateway_node_required("motherbrain", minimum_role="operator")
def motherbrain():
    gateway = get_current_gateway()
    denied = _permission_guard(DASHBOARD_VIEW_PERMISSION)
    if denied:
        return denied

    current_state = _current_sort_state(gateway)
    sort_date = current_state["sort_date"]
    operations = current_state["operations"]
    selected_operation = _selected_current_operation(
        operations,
        operation_id=request.args.get("operation_id"),
    )

    return render_template(
        "neomotherbrain/index.html",
        gateway=gateway,
        sort_date=sort_date,
        current_sort_operations=operations,
        selected_operation=selected_operation,
    )


@bp.route("/motherbrain/gateway-matrix", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def gateway_matrix():
    gateway = get_current_gateway()
    denied = _permission_guard(GATEWAY_MATRIX_VIEW_PERMISSION)
    if denied:
        return denied
    selected_operation = _selected_manage_sort_operation(gateway)
    if request.method == "POST":
        denied = _permission_guard(GATEWAY_MATRIX_EDIT_PERMISSION)
        if denied:
            return denied
        active_cells = []
        for day, _day_label in MATRIX_DAY_OPTIONS:
            for sort_name, _sort_label in MATRIX_SORT_OPTIONS:
                if request.form.get(f"{day}_{sort_name}") == "1":
                    active_cells.append((day, sort_name))

        save_gateway_matrix(gateway, active_cells)
        flash("Gateway Matrix updated.", "info")
        redirect_args = {}
        if selected_operation:
            redirect_args["operation_id"] = selected_operation.id
        return redirect(url_for("neomotherbrain.gateway_matrix", **redirect_args))

    return render_template(
        "neomotherbrain/gateway_matrix.html",
        gateway=gateway,
        day_options=MATRIX_DAY_OPTIONS,
        sort_options=MATRIX_SORT_OPTIONS,
        matrix=matrix_state_for_gateway(gateway),
        selected_operation=selected_operation,
    )


@bp.route("/motherbrain/system-settings", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def system_settings():
    gateway = get_current_gateway()
    can_edit = can_manage_system(current_user)

    if request.method == "POST":
        if not can_edit:
            db.session.rollback()
            flash("System Settings changes require Grandmaster access.", "error")
            return _render_system_settings(gateway, can_edit=False), 403

        action = str(request.form.get("action") or "").strip().lower()
        try:
            if action == "set_neosektor_mode":
                status = change_neosektor_integration_mode(
                    gateway,
                    request.form.get("integration_mode"),
                )
                flash(
                    f"NeoSektor integration mode is now {status['mode_label']}.",
                    "success",
                )
            elif action == "set_neorain_mode":
                status = change_rain_integration_mode(
                    gateway,
                    "night",
                    request.form.get("integration_mode"),
                )
                flash(
                    f"NeoRain integration mode is now {status['mode_label']}.",
                    "success",
                )
            elif action in {"enable_google_live_polling", "disable_google_live_polling"}:
                enabled = action == "enable_google_live_polling"
                set_google_motherbrain_live_polling_enabled(
                    gateway,
                    "night",
                    enabled,
                )
                db.session.commit()
                flash(
                    f"Live Google Polling is now {'ON' if enabled else 'OFF'}.",
                    "success",
                )
            elif action == "retry_neosektor_google_mirror":
                retry_neosektor_google_mirror(gateway)
                flash("NeoSektor Google mirror is current.", "success")
            else:
                raise ValueError("Choose a valid System Settings action.")
        except RainIntegrationTransitionError:
            db.session.rollback()
            flash(
                "NeoRain authority change failed; the previous mode remains active.",
                "error",
            )
            return _render_system_settings(gateway, can_edit=True), 400
        except (NeoSektorGoogleError, ValueError) as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_system_settings(gateway, can_edit=True), 400

        return redirect(url_for("neomotherbrain.system_settings"))

    return _render_system_settings(gateway, can_edit=can_edit)


def _render_system_settings(gateway, can_edit):
    return render_template(
        "neomotherbrain/system_settings.html",
        gateway=gateway,
        can_edit_system_settings=can_edit,
        neosektor_status=neosektor_integration_status(gateway),
        neorain_status=rain_integration_status(gateway, "night"),
        google_live_polling_status=google_motherbrain_live_polling_status(
            gateway,
            "night",
        ),
        google_live_poll_health=google_motherbrain_live_poll_health(gateway),
    )


@bp.route("/motherbrain/sort-timeline", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def sort_timeline():
    gateway = get_current_gateway()
    denied = _permission_guard(SORT_TIMELINE_VIEW_PERMISSION)
    if denied:
        return denied
    month_key = request.args.get("month", "")
    selected_operation = _selected_manage_sort_operation(gateway)

    if request.method == "POST":
        denied = _permission_guard(SORT_TIMELINE_EDIT_PERMISSION)
        if denied:
            return denied
        _settings, month_key = save_sort_timeline_from_form(gateway, request.form)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            context = sort_timeline_context(gateway, month_key)
            return jsonify(_sort_timeline_autosave_payload(context))
        flash("Sort Timeline settings saved.", "info")
        redirect_args = {"month": month_key}
        if selected_operation:
            redirect_args["operation_id"] = selected_operation.id
        return redirect(url_for("neomotherbrain.sort_timeline", **redirect_args))

    context = sort_timeline_context(gateway, month_key)
    return render_template(
        "neomotherbrain/sort_timeline.html",
        gateway=gateway,
        day_options=TIMELINE_DAY_OPTIONS,
        sort_options=TIMELINE_SORT_OPTIONS,
        format_timeline_time=format_timeline_time,
        selected_operation=selected_operation,
        **context,
    )


@bp.route("/motherbrain/flight-api-test", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def flight_api_test():
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_API_VIEW_PERMISSION)
    if denied:
        return denied
    current_state = _current_sort_state(gateway)
    sort_date = current_state["sort_date"]
    operations = current_state["operations"]
    selected_operation = _selected_current_operation(operations)
    import_result = None
    selected_lookup_window = None
    selected_polling_window = None
    selected_ops_window = None
    selected_request_details = None
    settings = ensure_sort_timeline_settings(gateway)
    replay_payload = ""
    auto_poll_status = None

    if request.method == "POST" and request.form.get("flight_api_action") == "pull":
        denied = _permission_guard(MANAGE_API_RUN_PERMISSION)
        if denied:
            return denied
        selected_operation = _selected_current_operation(
            operations,
            operation_id=request.form.get("operation_id"),
        )
        try:
            import_result = run_flight_api_import(
                gateway,
                operation=selected_operation,
            )
            db.session.commit()
            if import_result.get("attempted"):
                if import_result.get("provider_error"):
                    flash(import_result.get("message", "Flight API provider error."), "error")
                else:
                    flash("Flight API test import completed.", "info")
            else:
                flash(import_result.get("message", "Flight API import skipped."), "info")
        except FlightApiConfigurationError as error:
            db.session.rollback()
            flash(f"Flight API import failed: {error}", "error")
    elif request.method == "POST" and request.form.get("flight_api_action") == "replay":
        denied = _permission_guard(MANAGE_API_RUN_PERMISSION)
        if denied:
            return denied
        selected_operation = _selected_current_operation(
            operations,
            operation_id=request.form.get("operation_id"),
        )
        replay_payload = request.form.get("replay_payload", "")
        import_result = run_flight_api_replay(
            gateway,
            operation=selected_operation,
            payload_text=replay_payload,
        )
        if import_result.get("provider_error"):
            flash(import_result.get("message", "Flight API replay failed."), "error")
        else:
            flash("Flight API replay preview completed.", "info")

    if selected_operation:
        selected_lookup_window = sort_flight_lookup_window_snapshot(
            selected_operation,
            settings,
        )
        selected_polling_window = api_polling_window_snapshot(
            selected_operation,
            settings,
        )
        selected_ops_window = ops_node_online_window_snapshot(
            selected_operation,
            settings,
        )
        if (
            selected_lookup_window.get("provider_from_local")
            and selected_lookup_window.get("provider_to_local")
        ):
            selected_request_details = rapidapi_request_details(
                gateway.code,
                selected_lookup_window["provider_from_local"],
                selected_lookup_window["provider_to_local"],
            )
        auto_poll_status = flight_api_auto_poll_status(
            gateway,
            operation=selected_operation,
        )

    pending_items = pending_review_items_for_operation(selected_operation)
    last_poll_snapshot = (
        import_result.get("last_poll_snapshot")
        if import_result and not import_result.get("provider_error")
        else None
    )
    last_poll_review = flight_api_last_poll_review(
        selected_operation,
        gateway,
        snapshot=last_poll_snapshot,
    )
    return render_template(
        "neomotherbrain/flight_api_test.html",
        gateway=gateway,
        sort_date=sort_date,
        operations=operations,
        selected_operation=selected_operation,
        selected_lookup_window=selected_lookup_window,
        selected_polling_window=selected_polling_window,
        selected_ops_window=selected_ops_window,
        selected_request_details=selected_request_details,
        import_result=import_result,
        pending_review_items=pending_items,
        last_poll_review=last_poll_review,
        replay_payload=replay_payload,
        auto_poll_status=auto_poll_status,
        can_trigger_auto_poll=user_can(FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION),
        flight_api_auto_poll_client_header=FLIGHT_API_AUTO_POLL_CLIENT_HEADER,
        flight_api_auto_poll_client_version=FLIGHT_API_AUTO_POLL_CLIENT_VERSION,
        sort_timeline_settings=settings,
        flight_api_operational_time=flight_api_operational_time_utc,
        flight_api_provider_time=flight_api_provider_time_utc,
        format_flight_api_time=format_flight_api_local_time,
        **_flight_api_auto_poll_timer_context(gateway, operation=selected_operation),
    )


@bp.route("/motherbrain/flight-api-auto-poll/check", methods=["POST"])
@login_required
def flight_api_auto_poll_check():
    if not user_can(FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION):
        return jsonify(
            {
                "ok": False,
                "eligible": False,
                "skipped": True,
                "reason": "Access denied.",
                "poll_action": "stop",
                "terminal": True,
                "continue_polling": False,
            }
        ), 403

    gateway = get_current_gateway()
    status = flight_api_auto_poll_preflight(gateway)
    operation = status.get("operation")
    if status.get("poll_action") != "execute":
        return jsonify(_flight_api_auto_poll_payload(gateway, status, skipped=True))

    lock_token = acquire_flight_api_auto_poll_lock(operation)
    if not lock_token:
        db.session.rollback()
        status["eligible"] = False
        status["reason"] = "poll already in progress"
        coordinate_flight_api_auto_poll_status(status, action="continue")
        return jsonify(_flight_api_auto_poll_payload(gateway, status, skipped=True))

    db.session.commit()
    try:
        import_result = run_flight_api_import(gateway, operation=operation)
        release_flight_api_auto_poll_lock(operation, lock_token)
        db.session.commit()
    except Exception:
        db.session.rollback()
        operation = db.session.get(SortDateOperation, operation.id)
        release_flight_api_auto_poll_lock(operation, lock_token)
        db.session.commit()
        status = flight_api_auto_poll_preflight(gateway)
        payload = _flight_api_auto_poll_payload(
            gateway,
            status,
            skipped=False,
            import_result={
                "provider_error": True,
                "message": "Flight API auto poll failed safely.",
            },
        )
        return jsonify(payload), 500

    refreshed_status = flight_api_auto_poll_preflight(gateway)
    return jsonify(
        _flight_api_auto_poll_payload(
            gateway,
            refreshed_status,
            skipped=not bool(import_result.get("attempted")),
            import_result=import_result,
            initial_eligible=True,
        )
    )


@bp.post("/motherbrain/google-live-poll/execute")
def execute_google_live_poll():
    """Future heartbeat target; all Google polling scope is resolved server-side."""
    if not current_user.is_authenticated:
        return jsonify(
            {"status": "unauthenticated", "continue_heartbeat": False}
        ), 401

    gateway = get_current_gateway()
    if not user_has_gateway_access(current_user, gateway.code):
        return jsonify(
            {"status": "access_denied", "continue_heartbeat": False}
        ), 403

    result = execute_google_motherbrain_live_poll(gateway)
    result["continue_heartbeat"] = result["status"] not in {
        "disabled",
        "outside_window",
    }
    status_code = 500 if result["status"] in {"failed", "lifecycle_error"} else 200
    return jsonify(result), status_code


@bp.route("/motherbrain/flight-api-review")
@login_required
def flight_api_review():
    gateway = get_current_gateway()
    access = permission_access(
        FLIGHT_API_REVIEW_VIEW_PERMISSION,
        FLIGHT_API_REVIEW_EDIT_PERMISSION,
    )
    if not access["can_view"]:
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    current_state = _current_sort_state(gateway)
    sort_date = current_state["sort_date"]
    operations = current_state["operations"]
    selected_operation = _selected_current_operation(
        operations,
        operation_id=request.args.get("operation_id"),
    )
    mission_type_filter = str(request.args.get("mission_type") or "").strip().lower()
    if mission_type_filter not in {"arrival", "departure"}:
        mission_type_filter = None
    pending_items = pending_review_items_for_operation(selected_operation)
    if mission_type_filter:
        pending_items = [
            item for item in pending_items if item.mission_type == mission_type_filter
        ]
    pending_item_rows = flight_api_review_display_rows(pending_items, selected_operation)
    settings = ensure_sort_timeline_settings(gateway)
    return render_template(
        "neomotherbrain/flight_api_review.html",
        gateway=gateway,
        sort_date=sort_date,
        operations=operations,
        selected_operation=selected_operation,
        pending_review_items=pending_items,
        pending_review_item_rows=pending_item_rows,
        mission_type_filter=mission_type_filter,
        can_edit=access["can_edit"],
        entity_version=entity_version,
        sort_timeline_settings=settings,
        flight_api_operational_time=flight_api_operational_time_utc,
        flight_api_provider_time=flight_api_provider_time_utc,
        format_flight_api_time=format_flight_api_local_time,
        **_flight_api_auto_poll_timer_context(gateway, operation=selected_operation),
    )


@bp.route("/motherbrain/flight-api-review/<int:review_item_id>/add", methods=["POST"])
@login_required
def add_flight_api_review_item(review_item_id):
    if not user_can(FLIGHT_API_REVIEW_EDIT_PERMISSION):
        db.session.rollback()
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    gateway = get_current_gateway()
    review_item = review_item_or_404(gateway, review_item_id, for_update=True)
    if not _review_item_matches_selected_operation(gateway, review_item):
        db.session.rollback()
        flash("Review item is not part of the selected current sort operation.", "error")
        return redirect(url_for("neomotherbrain.flight_api_review"))

    operation = db.session.get(SortDateOperation, review_item.sort_date_operation_id)
    conflict = _review_item_conflict(operation, item=review_item)
    if conflict:
        return _flight_api_review_conflict_response(operation, review_item, conflict)
    accept_review_item(review_item)
    db.session.commit()
    flash("API flight added to current sort operation.", "info")
    return redirect(
        url_for(
            "neomotherbrain.flight_api_review",
            operation_id=review_item.sort_date_operation_id,
            mission_type=request.form.get("mission_type") or None,
        )
    )


@bp.route("/motherbrain/flight-api-review/<int:review_item_id>/ignore", methods=["POST"])
@login_required
def ignore_flight_api_review_item(review_item_id):
    if not user_can(FLIGHT_API_REVIEW_EDIT_PERMISSION):
        db.session.rollback()
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    gateway = get_current_gateway()
    review_item = review_item_or_404(gateway, review_item_id, for_update=True)
    if not _review_item_matches_selected_operation(gateway, review_item):
        db.session.rollback()
        flash("Review item is not part of the selected current sort operation.", "error")
        return redirect(url_for("neomotherbrain.flight_api_review"))

    operation = db.session.get(SortDateOperation, review_item.sort_date_operation_id)
    conflict = _review_item_conflict(operation, item=review_item)
    if conflict:
        return _flight_api_review_conflict_response(operation, review_item, conflict)
    ignore_review_item(review_item)
    db.session.commit()
    flash("API flight ignored for this sort operation.", "info")
    return redirect(
        url_for(
            "neomotherbrain.flight_api_review",
            operation_id=review_item.sort_date_operation_id,
            mission_type=request.form.get("mission_type") or None,
        )
    )


@bp.post("/motherbrain/alerts/<int:alert_id>/read")
@login_required
def read_motherbrain_alert(alert_id):
    alert = _unmatched_review_alert_for_user(alert_id)
    mark_unmatched_review_alert_read(alert, current_user)
    db.session.commit()
    return jsonify({"ok": True, "alert_id": alert.id, "unread": False})


@bp.post("/motherbrain/alerts/<int:alert_id>/open")
@login_required
def open_motherbrain_alert(alert_id):
    alert = _unmatched_review_alert_for_user(alert_id)
    mark_unmatched_review_alert_read(alert, current_user)
    db.session.commit()
    target = str(alert.related_url or "/motherbrain/flight-api-review")
    if not target.startswith("/") or target.startswith("//"):
        target = "/motherbrain/flight-api-review"
    return redirect(target)


def _unmatched_review_alert_for_user(alert_id):
    gateway = get_current_gateway()
    alert = MotherBrainAlert.query.filter_by(
        id=alert_id,
        gateway_id=gateway.id,
        scope="motherbrain",
        active=True,
        acknowledged=False,
    ).first_or_404()
    if not is_unmatched_review_alert(alert):
        abort(404)
    if not user_can(alert.permission_key or UNMATCHED_REVIEW_ALERT_PERMISSION):
        abort(403)
    return alert


def _flight_api_review_conflict_response(operation, review_item, conflict):
    db.session.rollback()
    if _planning_json_requested():
        return jsonify({"ok": False, "conflict": conflict}), 409
    flash(conflict["message"], "error")
    return redirect(
        url_for(
            "neomotherbrain.flight_api_review",
            operation_id=operation.id,
            mission_type=request.form.get("mission_type") or review_item.mission_type,
        )
    )


@bp.route("/motherbrain/parking-plan")
@gateway_node_required("motherbrain", minimum_role="operator")
def parking_plan():
    gateway = get_current_gateway()
    denied = _permission_guard(PARKING_PLAN_VIEW_PERMISSION)
    if denied:
        return denied
    context = parking_plan_landing_context(gateway)
    return render_template(
        "neomotherbrain/parking_plan.html",
        gateway=gateway,
        operation=None,
        selection_mode=True,
        **context,
        **_flight_api_auto_poll_timer_context(gateway),
    )


@bp.route("/motherbrain/parking-plan/<int:operation_id>")
@gateway_node_required("motherbrain", minimum_role="operator")
def parking_plan_operation(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(PARKING_PLAN_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _parking_plan_operation_or_404(gateway, operation_id)
    bundle = ParkingPlanOperationalStateBundle.load(gateway, operation)
    context = parking_plan_context(gateway, operation=operation, bundle=bundle)
    live_context = _parking_plan_live_context(gateway, context, bundle=bundle)
    if context.get("parking_physical_alert_sync", {}).get("changed"):
        db.session.commit()
    return render_template(
        "neomotherbrain/parking_plan.html",
        gateway=gateway,
        selection_mode=False,
        optimizer_defaults=parking_optimizer_default_options(gateway),
        optimizer_preview=None,
        can_run_parking_optimizer=user_can(PARKING_OPTIMIZER_RUN_PERMISSION),
        can_apply_parking_optimizer=user_can(PARKING_OPTIMIZER_APPLY_PERMISSION),
        can_edit_parking_plan=user_can(PARKING_PLAN_EDIT_PERMISSION),
        **live_context,
        **context,
        **_flight_api_auto_poll_timer_context(gateway, operation=context["operation"]),
    )


@bp.route("/motherbrain/parking-plan/<int:operation_id>/optimize", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def optimize_parking_plan(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(PARKING_PLAN_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _parking_plan_operation_or_404(gateway, operation_id)
    if not user_can(PARKING_OPTIMIZER_RUN_PERMISSION):
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.parking_plan_operation", operation_id=operation.id))

    include_remote = request.form.get("include_remote") == "1"
    include_throat = request.form.get("include_throat") == "1"
    bundle = ParkingPlanOperationalStateBundle.load(gateway, operation)
    context = parking_plan_context(gateway, operation=operation, bundle=bundle)
    live_context = _parking_plan_live_context(gateway, context, bundle=bundle)
    try:
        optimizer_preview = parking_optimizer_preview(
            gateway,
            operation,
            include_remote=include_remote,
            include_throat=include_throat,
            tail_rows=context["tail_rows"],
        )
    except Exception as exc:
        current_app.logger.exception(
            "Parking optimizer preview failed for operation %s",
            operation.id,
        )
        optimizer_preview = parking_optimizer_error_preview(
            gateway,
            operation,
            include_remote=include_remote,
            include_throat=include_throat,
            tail_rows=context["tail_rows"],
            message=f"Optimizer failed before solver completed: {exc}",
        )
        flash("Suggest Plan failed safely. Existing assignments were preserved.", "error")
    if context.get("parking_physical_alert_sync", {}).get("changed"):
        db.session.commit()
    return render_template(
        "neomotherbrain/parking_plan.html",
        gateway=gateway,
        selection_mode=False,
        optimizer_defaults={
            **parking_optimizer_default_options(gateway),
            "include_remote": include_remote,
            "include_throat": include_throat,
        },
        optimizer_preview=optimizer_preview,
        can_run_parking_optimizer=True,
        can_apply_parking_optimizer=user_can(PARKING_OPTIMIZER_APPLY_PERMISSION),
        can_edit_parking_plan=user_can(PARKING_PLAN_EDIT_PERMISSION),
        **live_context,
        **context,
        **_flight_api_auto_poll_timer_context(gateway, operation=context["operation"]),
    )


@bp.route("/motherbrain/parking-plan/<int:operation_id>/optimize/apply", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def apply_parking_plan_optimizer(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(PARKING_PLAN_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _parking_plan_operation_or_404(gateway, operation_id)
    if not user_can(PARKING_OPTIMIZER_APPLY_PERMISSION):
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.parking_plan_operation", operation_id=operation.id))

    include_remote = request.form.get("include_remote") == "1"
    include_throat = request.form.get("include_throat") == "1"
    if request.form.get("confirm_apply") != "1":
        flash("Confirm optimizer apply before writing suggested assignments.", "error")
        return redirect(url_for("neomotherbrain.parking_plan_operation", operation_id=operation.id))

    conflict = optimizer_revision_conflict(
        operation,
        request.form.get("expected_plan_revision"),
    )
    if conflict:
        return _parking_plan_response(
            False,
            conflict["message"],
            status=409,
            payload={"conflict": conflict},
            operation_id=operation.id,
        )

    try:
        result = apply_parking_optimizer_plan(
            gateway,
            operation,
            include_remote=include_remote,
            include_throat=include_throat,
            user=current_user,
        )
    except Exception:
        current_app.logger.exception(
            "Parking optimizer apply failed for operation %s",
            operation.id,
        )
        db.session.rollback()
        flash("Optimizer apply failed safely. Existing assignments were preserved.", "error")
        return redirect(url_for("neomotherbrain.parking_plan_operation", operation_id=operation.id))
    context = parking_plan_context(gateway, operation=operation)
    category = "info" if result["ok"] else "error"
    flash(result["message"], category)
    for skipped in result.get("skipped", [])[:3]:
        flash(f"{skipped['tail']}: {skipped['reason']}", "warning")
    if result.get("preview", {}).get("unassigned_tails"):
        flash(
            f"{len(result['preview']['unassigned_tails'])} tail(s) remain unresolved by optimizer.",
            "warning",
        )
    db.session.commit()
    return redirect(url_for("neomotherbrain.parking_plan_operation", operation_id=operation.id))


@bp.route("/motherbrain/parking-plan/<int:operation_id>/state")
@gateway_node_required("motherbrain", minimum_role="operator")
def parking_plan_live_state_endpoint(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(PARKING_PLAN_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _parking_plan_operation_or_404(gateway, operation_id)
    client_revision = str(request.args.get("revision") or "").strip()
    current_revision = parking_plan_revision(operation)
    live_update_status = node_auto_refresh_status(
        gateway,
        operation=operation,
    )
    if client_revision and client_revision == current_revision:
        response = jsonify(
            {
                "ok": True,
                "changed": False,
                "revision": current_revision,
                "refresh": live_update_status,
                "can_edit": user_can(PARKING_PLAN_EDIT_PERMISSION),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    bundle = ParkingPlanOperationalStateBundle.load(gateway, operation)
    context = parking_plan_context(
        gateway,
        operation=operation,
        sync_physical_alerts=False,
        bundle=bundle,
    )
    live_context = _parking_plan_live_context(
        gateway,
        context,
        revision=current_revision,
        live_update_status=live_update_status,
        bundle=bundle,
    )
    state = live_context["parking_live_state"]
    changed = client_revision != state["revision"]
    payload = {
        "ok": True,
        "changed": changed,
        "revision": state["revision"],
        "operation": state["operation"],
        "summary": state["summary"],
        "conflicts": state["conflicts"],
        "tails": state["tails"],
        "slots": state["slots"],
        "refresh": live_context["live_update_status"],
        "can_edit": user_can(PARKING_PLAN_EDIT_PERMISSION),
    }
    if changed:
        payload["fragments"] = {
            "tail_cards": render_template(
                "neomotherbrain/_parking_plan_live_tail_cards.html",
                operation=operation,
                tail_rows=context["tail_rows"],
                parking_live_state=state,
                can_edit_parking_plan=user_can(PARKING_PLAN_EDIT_PERMISSION),
            )
        }
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/motherbrain/parking-rules", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def parking_rules():
    gateway = get_current_gateway()
    if not user_can(PARKING_RULES_VIEW_PERMISSION):
        flash("Access denied.", "error")
        return redirect(url_for("neomotherbrain.rfd_hub"))

    can_edit = user_can(PARKING_RULES_EDIT_PERMISSION)
    operation = _parking_rules_operation_context(gateway)
    if request.method == "POST":
        return_to = request.form.get("return_to")
        if not can_edit:
            db.session.rollback()
            flash("Access denied.", "error")
            return redirect(_parking_rules_redirect(operation, anchor=return_to))
        if "save_building_lineup_belt_preferences" in request.form:
            save_belt_pair_preferences_from_form(gateway, request.form)
            db.session.commit()
            flash("Building Lineup belt parking preferences saved.", "info")
            return redirect(
                _parking_rules_redirect(
                    operation,
                    anchor="parking-rule-section-building-lineup-belt-preferences",
                )
            )
        save_parking_rules_from_form(gateway, request.form)
        db.session.commit()
        flash("Parking rules saved.", "info")
        return redirect(_parking_rules_redirect(operation, anchor=return_to))

    context = parking_rules_context(gateway, operation=operation)
    return render_template(
        "neomotherbrain/parking_rules.html",
        gateway=gateway,
        operation=operation,
        can_edit_parking_rules=can_edit,
        categories={
            "arrival_preferred": ARRIVAL_PARKING_PREFERENCE,
            "arrival_required": ARRIVAL_PARKING_REQUIREMENT,
            "departure_preferred": DEPARTURE_PARKING_PREFERENCE,
            "departure_required": DEPARTURE_PARKING_REQUIREMENT,
            "aircraft_restrictions": AIRCRAFT_TYPE_RAMP_RESTRICTION,
            "aircraft_preferences": AIRCRAFT_TYPE_RAMP_PREFERENCE,
            "blocked_positions": BLOCKED_PARKING_POSITION,
        },
        **context,
        **_flight_api_auto_poll_timer_context(gateway, operation=operation),
    )


@bp.route("/motherbrain/parking-plan/assign", methods=["POST"])
@bp.route("/motherbrain/parking-plan/<int:operation_id>/assign", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def assign_parking_plan_tail(operation_id=None):
    gateway = get_current_gateway()
    if not user_can(PARKING_PLAN_EDIT_PERMISSION):
        return _parking_plan_response(False, "Access denied.", status=403)
    operation = _parking_plan_operation_for_action(gateway, operation_id)
    if not operation:
        return _parking_plan_response(
            False,
            "Select a sort operation before assigning parking.",
            status=400,
        )
    selected_operation_id = operation.id

    try:
        source_assignment, target_assignment = validate_parking_move_snapshot(
            operation,
            tail_number=request.form.get("tail_number"),
            ramp_code=request.form.get("ramp_code"),
            position_code=request.form.get("position_code"),
            lane_number=request.form.get("lane_number"),
            expected=parking_snapshot_from_form(request.form),
        )
        bundle = ParkingPlanOperationalStateBundle.load(gateway, operation)
        assignment = assign_tail_to_lane(
            operation,
            request.form.get("tail_number"),
            request.form.get("ramp_code"),
            request.form.get("position_code"),
            request.form.get("lane_number"),
            user=current_user,
            replace_occupied=request.form.get("replace_occupied") == "1",
            is_hot=_truthy_form_value(request.form.get("is_hot"))
            if "is_hot" in request.form
            else None,
            note=request.form.get("note") if "note" in request.form else None,
            confirm_rule_override=request.form.get("confirm_rule_override") == "1",
            bundle=bundle,
            source_assignment=source_assignment,
            target_assignment=target_assignment,
        )
        success_message = (
            f"{assignment.tail_number} assigned to "
            f"{assignment.position_code} Slot {assignment.lane_number}."
        )
        db.session.commit()
    except ParkingStateConflict as error:
        db.session.rollback()
        return _parking_plan_response(
            False,
            str(error),
            status=409,
            payload={"conflict": error.conflict, "refresh_required": True},
            operation_id=operation.id,
        )
    except ParkingLaneOccupied as error:
        db.session.rollback()
        return _parking_plan_response(
            False,
            str(error),
            status=409,
            payload={"occupied_tail": error.occupied_tail},
        )
    except ParkingRuleConflict as error:
        db.session.rollback()
        return _parking_plan_response(
            False,
            str(error),
            status=409,
            payload={"requires_confirmation": True, "rule_conflict": True},
        )
    except ParkingPlanError as error:
        db.session.rollback()
        return _parking_plan_response(False, str(error), status=400)
    except IntegrityError:
        db.session.rollback()
        conflict = {
            "type": "parking_state_changed",
            "reason": "concurrent_write",
            "message": "Parking changed while you were editing. Latest plan has been loaded.",
            "can_overwrite": False,
            "refresh_required": True,
        }
        return _parking_plan_response(
            False,
            conflict["message"],
            status=409,
            payload={"conflict": conflict, "refresh_required": True},
            operation_id=operation.id,
        )

    return _parking_plan_response(
        True,
        success_message,
        operation_id=selected_operation_id,
    )


@bp.route("/motherbrain/parking-plan/unassign", methods=["POST"])
@bp.route("/motherbrain/parking-plan/<int:operation_id>/unassign", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def unassign_parking_plan_tail(operation_id=None):
    gateway = get_current_gateway()
    if not user_can(PARKING_PLAN_EDIT_PERMISSION):
        return _parking_plan_response(False, "Access denied.", status=403)
    operation = _parking_plan_operation_for_action(gateway, operation_id)
    if not operation:
        return _parking_plan_response(
            False,
            "Select a sort operation before updating parking.",
            status=400,
        )

    selected_operation_id = operation.id
    tail_number = request.form.get("tail_number")
    try:
        source_assignment = validate_parking_source_snapshot(
            operation,
            tail_number=tail_number,
            expected=parking_snapshot_from_form(request.form),
        )
        bundle = ParkingPlanOperationalStateBundle.load(
            gateway,
            operation,
            include_tail_states=False,
            include_missions=False,
            include_timeline=False,
        )
        unassign_tail(
            operation,
            tail_number,
            user=current_user,
            bundle=bundle,
            source_assignment=source_assignment,
        )
        db.session.commit()
    except ParkingStateConflict as error:
        db.session.rollback()
        return _parking_plan_response(
            False,
            str(error),
            status=409,
            payload={"conflict": error.conflict, "refresh_required": True},
            operation_id=operation.id,
        )
    return _parking_plan_response(
        True,
        f"{str(tail_number or '').strip().upper()} unassigned.",
        operation_id=selected_operation_id,
    )


@bp.route("/motherbrain/parking-plan/clear", methods=["POST"])
@bp.route("/motherbrain/parking-plan/<int:operation_id>/clear", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def clear_parking_plan_assignments(operation_id=None):
    gateway = get_current_gateway()
    if not user_can(PARKING_PLAN_EDIT_PERMISSION):
        return _parking_plan_response(False, "Access denied.", status=403)
    operation = _parking_plan_operation_for_action(gateway, operation_id)
    if not operation:
        return _parking_plan_response(
            False,
            "Select a sort operation before clearing parking.",
            status=400,
        )

    cleared_count = clear_parking_assignments(operation, user=current_user)
    db.session.commit()
    return _parking_plan_response(
        True,
        f"Cleared {cleared_count} parked tail assignment(s) for this sort.",
        operation_id=operation.id,
    )


@bp.route("/motherbrain/parking-plan/hot", methods=["POST"])
@bp.route("/motherbrain/parking-plan/<int:operation_id>/hot", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def update_parking_plan_hot(operation_id=None):
    gateway = get_current_gateway()
    if not user_can(PARKING_PLAN_EDIT_PERMISSION):
        return _parking_plan_response(False, "Access denied.", status=403)
    operation = _parking_plan_operation_for_action(gateway, operation_id)
    if not operation:
        return _parking_plan_response(
            False,
            "Select a sort operation before updating parking.",
            status=400,
        )

    selected_operation_id = operation.id
    try:
        bundle = ParkingPlanOperationalStateBundle.load(
            gateway,
            operation,
            include_timeline=False,
        )
        tail_state = set_tail_hot(
            operation,
            request.form.get("tail_number"),
            _truthy_form_value(request.form.get("is_hot"))
            if "is_hot" in request.form and request.form.get("is_hot") != ""
            else None,
            user=current_user,
            note=request.form.get("note") if "note" in request.form else None,
            bundle=bundle,
        )
        state = tail_operational_status_label(tail_state.operational_status) or "NORMAL"
        success_message = f"{tail_state.tail_number} marked {state}."
        db.session.commit()
    except ParkingPlanError as error:
        db.session.rollback()
        return _parking_plan_response(False, str(error), status=400)

    return _parking_plan_response(
        True,
        success_message,
        operation_id=selected_operation_id,
    )


@bp.route("/motherbrain/parking-plan/tail-status", methods=["POST"])
@bp.route("/motherbrain/parking-plan/<int:operation_id>/tail-status", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def update_parking_plan_tail_status(operation_id=None):
    gateway = get_current_gateway()
    if not user_can(PARKING_PLAN_EDIT_PERMISSION):
        return _parking_plan_response(False, "Access denied.", status=403)
    operation = _parking_plan_operation_for_action(gateway, operation_id)
    if not operation:
        return _parking_plan_response(
            False,
            "Select a sort operation before updating tail status.",
            status=400,
        )

    selected_operation_id = operation.id
    try:
        bundle = ParkingPlanOperationalStateBundle.load(
            gateway,
            operation,
            include_timeline=False,
        )
        if "operational_status" in request.form:
            tail_state = set_tail_operational_status(
                operation,
                request.form.get("tail_number"),
                request.form.get("operational_status"),
                user=current_user,
                bundle=bundle,
            )
        else:
            tail_state = set_tail_out_of_service(
                operation,
                request.form.get("tail_number"),
                _truthy_form_value(request.form.get("is_out_of_service")),
                user=current_user,
                bundle=bundle,
            )
        state = tail_operational_status_label(tail_state.operational_status) or "NORMAL"
        success_message = f"{tail_state.tail_number} marked {state}."
        db.session.commit()
    except ParkingPlanError as error:
        db.session.rollback()
        return _parking_plan_response(False, str(error), status=400)

    return _parking_plan_response(
        True,
        success_message,
        operation_id=selected_operation_id,
    )


def _parking_plan_operation_or_404(gateway, operation_id):
    operation = operation_by_id(operation_id)
    if (
        not operation
        or operation.gateway_code != gateway.code
        or operation.archived_at_utc is not None
    ):
        abort(404)
    return operation


def _parking_plan_operation_for_action(gateway, operation_id=None):
    form_operation_id = request.form.get("operation_id")
    selected_id = operation_id or form_operation_id
    if selected_id:
        try:
            return _parking_plan_operation_or_404(gateway, int(selected_id))
        except (TypeError, ValueError):
            abort(404)
    return current_active_sort_operation(gateway)


def _parking_plan_live_context(
    gateway,
    context,
    revision=None,
    live_update_status=None,
    bundle=None,
):
    operation = context["operation"]
    state = parking_plan_live_state(
        operation,
        tail_rows=context["tail_rows"],
        summary=context["summary"],
        parking_status=context["parking_status"],
        revision=revision,
        bundle=bundle,
    )
    return {
        "parking_live_state": state,
        "live_update_status": live_update_status
        or node_auto_refresh_status(gateway, operation=operation),
    }


def _parking_rules_operation_context(gateway):
    operation_id = request.values.get("operation_id")
    if not operation_id:
        return None
    try:
        return _parking_plan_operation_or_404(gateway, int(operation_id))
    except (TypeError, ValueError):
        abort(404)


def _parking_rules_redirect(operation=None, anchor=None):
    if operation:
        destination = url_for("neomotherbrain.parking_rules", operation_id=operation.id)
    else:
        destination = url_for("neomotherbrain.parking_rules")
    if anchor and re.fullmatch(
        r"parking-rule-(?:section|new)-[a-z0-9_-]+|parking-rule-\d+|parking-rules-other-rules",
        anchor,
    ):
        return f"{destination}#{anchor}"
    return destination


def _parking_plan_response(success, message, status=200, payload=None, operation_id=None):
    payload = dict(payload or {})
    payload.update({"ok": bool(success), "message": message})
    if _wants_json_response():
        return jsonify(payload), status

    flash(message, "info" if success else "error")
    if operation_id:
        return redirect(
            url_for("neomotherbrain.parking_plan_operation", operation_id=operation_id)
        )
    return redirect(url_for("neomotherbrain.parking_plan"))


def _wants_json_response():
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


def _truthy_form_value(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "hot"}


def _flight_api_auto_poll_payload(
    gateway,
    status,
    skipped=False,
    import_result=None,
    initial_eligible=None,
):
    import_result = import_result or {}
    operation = status.get("operation")
    eligible = bool(status.get("eligible")) if initial_eligible is None else bool(initial_eligible)
    provider_error = bool(import_result.get("provider_error"))
    message = import_result.get("message") or status.get("reason") or ""
    return {
        "ok": not provider_error,
        "eligible": eligible,
        "skipped": bool(skipped),
        "reason": status.get("reason") or message,
        "current_operation_id": operation.id if operation else status.get("operation_id"),
        "current_operation_name": (
            operation.sort_name if operation else status.get("operation_sort_name")
        ),
        "sort_date": str(operation.sort_date if operation else status.get("operation_sort_date") or ""),
        "last_attempted_poll": _flight_api_json_time(
            status.get("last_attempted_poll_utc"),
            gateway,
        ),
        "last_successful_poll": _flight_api_json_time(
            status.get("last_successful_poll_utc"),
            gateway,
        ),
        "last_failed_poll": _flight_api_json_time(
            status.get("last_failed_poll_utc"),
            gateway,
        ),
        "next_auto_poll_eligible_at": _flight_api_json_time(
            status.get("next_eligible_time_utc"),
            gateway,
        ),
        "actual_auto_poll_interval_minutes": status.get("actual_interval_minutes"),
        "remaining_polls": status.get("polls_remaining", 0),
        "units_consumed": import_result.get("usage_units_consumed", 0),
        "matched_arrivals": import_result.get("matched_arrivals_count", 0),
        "matched_departures": import_result.get("matched_departures_count", 0),
        "unmatched_arrivals": import_result.get("unmatched_arrivals_count", 0),
        "unmatched_departures": import_result.get("unmatched_departures_count", 0),
        "non_ups_ignored_arrivals": import_result.get("non_ups_ignored_arrivals_count", 0),
        "non_ups_ignored_departures": import_result.get("non_ups_ignored_departures_count", 0),
        "review_added": len(import_result.get("review_items") or []),
        "stale_removed": import_result.get("replaced_review_count", 0),
        "suppressed_review": import_result.get("suppressed_review_count", 0),
        "provider_status": import_result.get("provider_status_code"),
        "safe_error_text": message if provider_error else "",
        "attempted": bool(import_result.get("attempted")),
        "poll_action": status.get("poll_action") or "stop",
        "terminal": bool(status.get("terminal")),
        "continue_polling": bool(status.get("continue_polling")),
        "wait_until": _flight_api_json_time(
            status.get("next_check_at_utc"),
            gateway,
        ),
        "next_check_seconds": status.get("next_check_seconds"),
    }


def _flight_api_json_time(value, gateway):
    if not value:
        return None
    if value.tzinfo:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return {
        "utc": f"{value.isoformat(timespec='seconds')}Z",
        "local": format_flight_api_local_time(value, gateway),
    }


def _flight_api_auto_poll_timer_context(gateway, operation=None):
    if not user_can(FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION):
        return {"flight_api_auto_poll_timer": None}

    status = coordinate_flight_api_auto_poll_status(
        flight_api_auto_poll_status(gateway, operation=operation)
    )
    operation = status.get("operation")
    inactive_reasons = {
        "no current sort operation",
        "operation is not current active operation",
    }
    if (
        not operation
        or not status.get("provider_enabled")
        or not status.get("api_schedule_enabled")
        or status.get("reason") in inactive_reasons
    ):
        return {"flight_api_auto_poll_timer": None}

    return {
        "flight_api_auto_poll_timer": {
            "endpoint": url_for("neomotherbrain.flight_api_auto_poll_check"),
            "client_header": FLIGHT_API_AUTO_POLL_CLIENT_HEADER,
            "client_version": FLIGHT_API_AUTO_POLL_CLIENT_VERSION,
            "initial_status": _flight_api_auto_poll_payload(
                gateway,
                status,
                skipped=not bool(status.get("eligible")),
            ),
        }
    }


def _sort_timeline_autosave_payload(context):
    current_preview = context["current_preview"]
    next_preview = context["next_preview"]
    return {
        "status": "saved",
        "month": context["month_key"],
        "previews": {
            current_preview["month_key"]: _sort_timeline_preview_payload(current_preview),
            next_preview["month_key"]: _sort_timeline_preview_payload(next_preview),
        },
        "sort_previews": {
            preview["sort_name"]: {
                "api_day_count": preview["api_day_count"],
                "api_day_label": f"{preview['api_day_count']} API DAYS THIS MONTH",
                "special_poll_count": preview["special_poll_count"],
                "budget_poll_interval_minutes": (
                    preview["budget_poll_interval_minutes"]
                    if preview["budget_poll_interval_minutes"] is not None
                    else "NONE"
                ),
                "actual_auto_poll_interval_minutes": (
                    preview["actual_auto_poll_interval_minutes"]
                    if preview["actual_auto_poll_interval_minutes"] is not None
                    else "NONE"
                ),
                "projected_polls_per_polling_day": preview["projected_polls_per_polling_day"],
                "next_poll_time": format_timeline_time(preview["next_poll_time"]),
            }
            for preview in current_preview["sort_previews"]
        },
    }


def _sort_timeline_preview_payload(preview):
    return {
        "monthly_api_units": preview["monthly_api_units"],
        "units_per_poll": preview["units_per_poll"],
        "taxi_to_ramp_minutes": preview["taxi_to_ramp_minutes"],
        "minimum_auto_poll_interval_minutes": preview["minimum_auto_poll_interval_minutes"],
        "monthly_poll_limit": preview["monthly_poll_limit"],
        "units_used": preview["units_used"],
        "units_remaining": preview["units_remaining"],
        "polls_used": preview["polls_used"],
        "polls_remaining": preview["polls_remaining"],
        "operating_days": preview["operating_days"],
        "api_polling_days": preview["api_polling_days"],
        "full_month_api_polling_days": preview["full_month_api_polling_days"],
        "remaining_api_polling_days": preview["remaining_api_polling_days"],
        "original_daily_poll_cap": preview["original_daily_poll_cap"],
        "adjusted_daily_poll_cap": preview["adjusted_daily_poll_cap"],
        "effective_daily_poll_cap": preview["effective_daily_poll_cap"],
        "budget_poll_interval_minutes": (
            preview["budget_poll_interval_minutes"]
            if preview["budget_poll_interval_minutes"] is not None
            else "NONE"
        ),
        "actual_auto_poll_interval_minutes": (
            preview["actual_auto_poll_interval_minutes"]
            if preview["actual_auto_poll_interval_minutes"] is not None
            else "NONE"
        ),
        "projected_polls_per_polling_day": preview["projected_polls_per_polling_day"],
        "special_poll_count": preview["special_poll_count"],
        "auto_interval_poll_count": preview["auto_interval_poll_count"],
        "total_scheduled_polls": preview["total_scheduled_polls"],
    }


@bp.route("/motherbrain/manage-sort")
@gateway_node_required("motherbrain", minimum_role="operator")
def manage_sort():
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    current_state = _current_sort_state(gateway)
    manual_creation = manual_current_sort_creation_status(
        gateway,
        local_now=current_state["local_now"],
    )
    sort_date = current_state["sort_date"]
    operations = current_state["operations"]
    selected_operation = _selected_current_operation(
        operations,
        operation_id=request.args.get("operation_id"),
    )
    if not selected_operation:
        selected_sort_name = request.args.get("sort", "").strip().lower()
        selected_operation = next(
            (
                operation
                for operation in operations
                if operation.sort_name == selected_sort_name
            ),
            operations[0] if operations else None,
        )

    return render_template(
        "neomotherbrain/manage_sort.html",
        gateway=gateway,
        sort_date=sort_date,
        operations=operations,
        selected_operation=selected_operation,
        created_count=0,
        errors=(),
        manual_creation=manual_creation,
        can_create_tonight_sort=(
            not manual_creation["operation_exists"]
            and user_can(MANAGE_SORT_EDIT_PERMISSION)
            and (
                manual_creation["scheduled"]
                or can_manage_system(current_user)
            )
        ),
        **_flight_api_auto_poll_timer_context(gateway, operation=selected_operation),
    )


@bp.post("/motherbrain/manage-sort/create-tonight")
@gateway_node_required("motherbrain", minimum_role="operator")
def create_tonight_sort():
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION)
    if denied:
        return denied
    try:
        result = create_manual_current_sort_operation(
            gateway,
            current_user.id,
            allow_unscheduled=can_manage_system(current_user),
        )
    except ManualSortCreationError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("neomotherbrain.manage_sort"))

    operation = result["operation"]
    flash(
        "Tonight's sort operation created."
        if result["created"]
        else "Tonight's sort operation already exists.",
        "info",
    )
    return redirect(
        url_for("neomotherbrain.manage_sort", operation_id=operation.id)
    )


@bp.route("/motherbrain/operations")
@gateway_node_required("motherbrain", minimum_role="operator")
def operations():
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    operations = (
        SortDateOperation.query.filter_by(gateway_code=gateway.code)
        .order_by(
            SortDateOperation.sort_date.desc(),
            SortDateOperation.generated_at_utc.desc(),
        )
        .all()
    )
    return render_template("neomotherbrain/operations.html", operations=operations)


@bp.route("/motherbrain/master-schedule", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def master_schedule():
    gateway = get_current_gateway()
    denied = _permission_guard(MASTER_SCHEDULE_VIEW_PERMISSION)
    if denied:
        return denied
    selected_operation = _selected_manage_sort_operation(gateway)
    schedules = _master_schedules_for_gateway(gateway)
    if request.method == "POST":
        denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION)
        if denied:
            return denied
        mission_type = request.form.get("board_mission_type", "").strip().lower()
        rows = _master_schedule_bulk_rows_from_request(gateway)
        strict_row_index = request.form.get("master_save_row", "").strip()
        create_complete_new_rows = bool(strict_row_index) or not _wants_json_response()
        try:
            updated_count, created_count = _apply_master_schedule_board_edit(
                rows,
                schedules,
                gateway,
                mission_type,
                strict_row_index=strict_row_index or None,
                create_complete_new_rows=create_complete_new_rows,
            )
        except ValueError as error:
            db.session.rollback()
            if _wants_json_response():
                return {"ok": False, "message": str(error)}, 400
            flash(str(error), "error")
            redirect_args = {}
            if selected_operation:
                redirect_args["operation_id"] = selected_operation.id
            return redirect(url_for("neomotherbrain.master_schedule", **redirect_args))

        db.session.commit()
        if _wants_json_response():
            return {
                "ok": True,
                "mission_type": mission_type,
                "updated": updated_count,
                "created": created_count,
            }
        flash(
            f"Master {mission_type} board saved: "
            f"{updated_count} updated, {created_count} created.",
            "info",
        )
        redirect_args = {}
        if selected_operation:
            redirect_args["operation_id"] = selected_operation.id
        return redirect(url_for("neomotherbrain.master_schedule", **redirect_args))

    return render_template(
        "neomotherbrain/master_schedule.html",
        arrival_schedules=[
            schedule for schedule in schedules if schedule.mission_type == "arrival"
        ],
        departure_schedules=[
            schedule for schedule in schedules if schedule.mission_type == "departure"
        ],
        active_day_options=ACTIVE_DAY_OPTIONS,
        aircraft_type_options=MASTER_AIRCRAFT_TYPE_OPTIONS,
        gateway=gateway,
        selected_operation=selected_operation,
        wave_options=MASTER_WAVE_OPTIONS,
    )


@bp.route("/motherbrain/master-schedule/new", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def new_master_schedule():
    gateway = get_current_gateway()
    denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION if request.method == "POST" else MASTER_SCHEDULE_VIEW_PERMISSION)
    if denied:
        return denied
    if request.method == "POST" and request.form.getlist("row_indexes"):
        rows = _master_schedule_bulk_rows_from_request(gateway)
        try:
            created_schedules = _create_master_schedules_from_bulk_rows(rows, gateway)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_master_schedule_form(rows=rows, mode="new"), 400

        db.session.add_all(created_schedules)
        db.session.commit()
        flash(f"{len(created_schedules)} master flight schedule row(s) created.", "info")
        return redirect(url_for("neomotherbrain.master_schedule"))

    form = (
        _master_schedule_form_from_request(gateway)
        if request.method == "POST"
        else _master_schedule_form_for_get(gateway)
    )

    if request.method == "POST":
        master_schedule = MasterFlightSchedule()
        try:
            _apply_master_schedule_form(master_schedule, form, gateway)
            _raise_for_duplicate_active_master_schedule(master_schedule)
        except ValueError as error:
            flash(str(error), "error")
            return _render_master_schedule_form(form, "new"), 400

        db.session.add(master_schedule)
        db.session.commit()
        flash("Master flight schedule created.", "info")
        return redirect(
            url_for(
                "neomotherbrain.master_schedule_detail",
                master_id=master_schedule.id,
            )
        )

    return _render_master_schedule_form(
        rows=[_master_schedule_row_from_form(form, 0)],
        mode="new",
    )


@bp.route("/motherbrain/master-schedule/bulk-edit", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def bulk_edit_master_schedule():
    gateway = get_current_gateway()
    denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION if request.method == "POST" else MASTER_SCHEDULE_VIEW_PERMISSION)
    if denied:
        return denied
    schedules = _master_schedules_for_gateway(gateway)

    if request.method == "POST":
        rows = _master_schedule_bulk_rows_from_request(gateway)
        try:
            updated_count, created_count = _apply_master_schedule_bulk_edit(
                rows,
                schedules,
                gateway,
            )
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_master_schedule_form(rows=rows, mode="bulk_edit"), 400

        db.session.commit()
        flash(
            f"Master schedule saved: {updated_count} updated, {created_count} created.",
            "info",
        )
        return redirect(url_for("neomotherbrain.master_schedule"))

    rows = [
        _master_schedule_row_from_form(
            _master_schedule_form_from_model(schedule),
            index,
            schedule.id,
        )
        for index, schedule in enumerate(schedules)
    ]
    if not rows:
        rows = [_master_schedule_row_from_form(_blank_master_schedule_form(gateway), 0)]

    return _render_master_schedule_form(rows=rows, mode="bulk_edit")


@bp.route("/motherbrain/master-schedule/<int:master_id>")
@gateway_node_required("motherbrain", minimum_role="operator")
def master_schedule_detail(master_id):
    denied = _permission_guard(MASTER_SCHEDULE_VIEW_PERMISSION)
    if denied:
        return denied
    master_schedule = _master_schedule_or_404(master_id)
    return render_template(
        "neomotherbrain/master_schedule_detail.html",
        master_schedule=master_schedule,
    )


@bp.route("/motherbrain/master-schedule/<int:master_id>/edit", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def edit_master_schedule(master_id):
    gateway = get_current_gateway()
    denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION if request.method == "POST" else MASTER_SCHEDULE_VIEW_PERMISSION)
    if denied:
        return denied
    master_schedule = _master_schedule_or_404(master_id)
    if request.method == "POST" and request.form.getlist("row_indexes"):
        rows = _master_schedule_bulk_rows_from_request(gateway)
        row = _first_master_schedule_row(rows)
        row["id"] = str(master_schedule.id)
        try:
            _apply_master_schedule_form(master_schedule, row, gateway)
            _raise_for_duplicate_active_master_schedule(master_schedule)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_master_schedule_form(
                rows=rows,
                mode="edit",
                master_schedule=master_schedule,
            ), 400

        db.session.commit()
        flash("Master flight schedule updated.", "info")
        return redirect(
            url_for(
                "neomotherbrain.master_schedule_detail",
                master_id=master_schedule.id,
            )
        )

    form = (
        _master_schedule_form_from_request(gateway)
        if request.method == "POST"
        else _master_schedule_form_for_get(gateway, master_schedule)
    )

    if request.method == "POST":
        try:
            _apply_master_schedule_form(master_schedule, form, gateway)
            _raise_for_duplicate_active_master_schedule(master_schedule)
        except ValueError as error:
            flash(str(error), "error")
            return _render_master_schedule_form(form, "edit", master_schedule), 400

        db.session.commit()
        flash("Master flight schedule updated.", "info")
        return redirect(
            url_for(
                "neomotherbrain.master_schedule_detail",
                master_id=master_schedule.id,
            )
        )

    return _render_master_schedule_form(
        rows=[_master_schedule_row_from_form(form, 0, master_schedule.id)],
        mode="edit",
        master_schedule=master_schedule,
    )


@bp.route("/motherbrain/master-schedule/<int:master_id>/toggle-active", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def toggle_master_schedule_active(master_id):
    denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION)
    if denied:
        return denied
    master_schedule = _master_schedule_or_404(master_id)
    master_schedule.active = not master_schedule.active

    try:
        _raise_for_duplicate_active_master_schedule(master_schedule)
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(
            url_for(
                "neomotherbrain.master_schedule_detail",
                master_id=master_schedule.id,
            )
        )

    db.session.commit()
    state = "activated" if master_schedule.active else "deactivated"
    flash(f"Master flight schedule {state}.", "info")
    return redirect(
        url_for(
            "neomotherbrain.master_schedule_detail",
            master_id=master_schedule.id,
        )
    )


@bp.route("/motherbrain/master-schedule/<int:master_id>/delete", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def delete_master_schedule(master_id):
    denied = _permission_guard(MASTER_SCHEDULE_EDIT_PERMISSION)
    if denied:
        return denied
    master_schedule = _master_schedule_or_404(master_id)
    SortDateMission.query.filter_by(master_flight_schedule_id=master_schedule.id).update(
        {"master_flight_schedule_id": None},
        synchronize_session=False,
    )
    db.session.delete(master_schedule)
    db.session.commit()
    flash("Master flight schedule row deleted.", "info")
    return redirect(url_for("neomotherbrain.master_schedule"))


@bp.route("/motherbrain/operations/new", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def new_operation():
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION if request.method == "POST" else MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    form = {
        "sort_date": request.form.get("sort_date", ""),
        "gateway_code": gateway.code,
        "sort_name": request.form.get("sort_name", "night"),
    }

    if request.method == "POST":
        try:
            sort_date = date.fromisoformat(form["sort_date"])
        except ValueError:
            flash("Enter a valid sort date.", "error")
            return _render_new_operation_form(form), 400

        sort_name = form["sort_name"].strip().lower()
        if sort_name not in SORT_NAMES:
            flash("Sort name must be Night, Twilight, Day, or Sunrise.", "error")
            return _render_new_operation_form(form), 400

        try:
            operation = generate_sort_date_operation_from_master(
                sort_date=sort_date,
                gateway_code=gateway.code,
                sort_name=sort_name,
                generated_by_user_id=current_user.id,
            )
        except ValueError as error:
            existing_operation = SortDateOperation.query.filter_by(
                sort_date=sort_date,
                gateway_code=gateway.code,
                sort_name=sort_name,
            ).first()
            if existing_operation:
                flash("That nightly operation already exists.", "info")
                return redirect(
                    url_for(
                        "neomotherbrain.operation_detail",
                        operation_id=existing_operation.id,
                    )
                )

            flash(str(error), "error")
            return _render_new_operation_form(form), 400

        flash("Nightly operation generated.", "info")
        return redirect(
            url_for("neomotherbrain.operation_detail", operation_id=operation.id)
        )

    return _render_new_operation_form(form)


@bp.route("/motherbrain/operations/<int:operation_id>")
@gateway_node_required("motherbrain", minimum_role="operator")
def operation_detail(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)
    arrival_count = _mission_count(operation, "arrival")
    departure_count = _mission_count(operation, "departure")
    return render_template(
        "neomotherbrain/operation_detail.html",
        operation=operation,
        arrival_count=arrival_count,
        departure_count=departure_count,
        mission_count=arrival_count + departure_count,
        google_reader_status=google_motherbrain_reader_status(),
        **_flight_api_auto_poll_timer_context(gateway, operation=operation),
    )


@bp.post(
    "/motherbrain/operations/<int:operation_id>/google-live-polling"
)
@gateway_node_required("motherbrain", minimum_role="operator")
def update_google_live_polling(operation_id):
    if not can_manage_system(current_user):
        return _permission_denied_redirect()

    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    if (
        str(operation.gateway_code or "").strip().upper() != "RFD"
        or str(operation.sort_name or "").strip().lower() != "night"
    ):
        abort(400)

    action = str(request.form.get("action") or "").strip().lower()
    if action not in {"enable", "disable"}:
        flash("Choose Enable or Disable for Live Google Polling.", "error")
        return redirect(url_for("neomotherbrain.system_settings"))

    enabled = action == "enable"
    set_google_motherbrain_live_polling_enabled(
        gateway,
        operation.sort_name,
        enabled,
    )
    db.session.commit()
    flash(
        f"Live Google Polling is now {'ON' if enabled else 'OFF'}.",
        "info",
    )
    return redirect(url_for("neomotherbrain.system_settings"))


@bp.post(
    "/motherbrain/operations/<int:operation_id>/google-current-sort/preview"
)
@gateway_node_required("motherbrain", minimum_role="operator")
def google_current_sort_preview(operation_id):
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION)
    if denied:
        return denied

    operation = _operation_or_404(operation_id)
    try:
        if (
            str(operation.gateway_code or "").strip().upper() != "RFD"
            or str(operation.sort_name or "").strip().lower() != "night"
        ):
            raise GoogleMotherBrainReaderError(
                "operation_mismatch",
                "Google current-sort preview is available only for the RFD Night Sort.",
            )

        envelope = read_google_motherbrain_envelope()
        validated = validate_google_motherbrain_envelope(
            envelope,
            current_app.config.get("GOOGLE_MOTHERBRAIN_SPREADSHEET_ID"),
        )
        if (
            validated["sort_date"] != operation.sort_date
            or validated["gateway_code"]
            != str(operation.gateway_code or "").strip().upper()
            or validated["sort_name"]
            != str(operation.sort_name or "").strip().lower()
        ):
            raise GoogleMotherBrainReaderError(
                "operation_mismatch",
                "The Google workbook sort date does not match the selected Neo operation.",
            )

        resolved_operation = resolve_google_motherbrain_operation(validated)
        if resolved_operation.id != operation.id:
            raise GoogleMotherBrainReaderError(
                "operation_mismatch",
                "The Google workbook sort date does not match the selected Neo operation.",
            )

        preview = build_google_motherbrain_preview(validated, resolved_operation)
        response = make_response(
            render_template(
                "neomotherbrain/google_current_sort_preview.html",
                operation=resolved_operation,
                preview=preview,
                preview_timestamp=validated["submitted_at"],
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    except (
        GoogleMotherBrainReaderError,
        GoogleMotherBrainPayloadError,
        GoogleMotherBrainOperationError,
    ) as error:
        current_app.logger.warning(
            "Google MotherBrain reader preview failed code=%s operation_id=%s",
            getattr(error, "code", "preview_failed"),
            operation.id,
        )
        flash(
            getattr(
                error,
                "message",
                "Google current-sort preview could not be built.",
            ),
            "error",
        )
        response = make_response(
            redirect(
                url_for(
                    "neomotherbrain.operation_detail",
                    operation_id=operation.id,
                )
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as error:
        current_app.logger.error(
            "Google MotherBrain reader preview failed error_type=%s operation_id=%s",
            type(error).__name__,
            operation.id,
        )
        flash("Google current-sort preview could not be built.", "error")
        response = make_response(
            redirect(
                url_for(
                    "neomotherbrain.operation_detail",
                    operation_id=operation.id,
                )
            )
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    finally:
        db.session.rollback()


@bp.route("/motherbrain/operations/<int:operation_id>/arrivals")
@gateway_node_required("motherbrain", minimum_role="operator")
def arrival_board(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(ARRIVAL_PLANNING_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)
    missions = _missions_for_operation(operation, "arrival", include_cancelled=False)
    parking_assignments = _parking_assignments_for_operation(operation)
    rows = [_arrival_row(mission, operation, parking_assignments) for mission in missions]
    return render_template(
        "neomotherbrain/arrival_board.html",
        operation=operation,
        rows=rows,
        **_flight_api_auto_poll_timer_context(gateway, operation=operation),
    )


@bp.route("/motherbrain/operations/<int:operation_id>/departures")
@gateway_node_required("motherbrain", minimum_role="operator")
def departure_board(operation_id):
    gateway = get_current_gateway()
    denied = _permission_guard(DEPARTURE_PLANNING_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)
    missions = _missions_for_operation(operation, "departure", include_cancelled=False)
    parking_assignments = _parking_assignments_for_operation(operation)
    rows = [
        _departure_row(mission, operation, parking_assignments)
        for mission in missions
    ]
    return render_template(
        "neomotherbrain/departure_board.html",
        operation=operation,
        rows=rows,
        **_flight_api_auto_poll_timer_context(gateway, operation=operation),
    )


@bp.route("/motherbrain/operations/<int:operation_id>/alp/<mission_type>", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def alp_import(operation_id, mission_type):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = (mission_type or "").strip().lower()
    if mission_type not in {"arrival", "departure"}:
        abort(404)
    denied = _permission_guard(_planning_view_permission(mission_type))
    if denied:
        return denied

    preview_state = get_alp_preview_state(operation, mission_type, current_user)
    paste_text = preview_state.paste_text if preview_state else ""
    preview = None
    preview_is_active = preview_state is not None
    if request.method == "POST":
        denied = _permission_guard(_planning_run_permission(mission_type))
        if denied:
            return denied
        action = request.form.get("alp_action", "preview")
        if action in {"clear", "cancel"}:
            clear_alp_preview_state(operation, mission_type, current_user)
            _clear_pending_alp_planning_rows(operation, mission_type)
            db.session.commit()
            flash("ALP preview cleared.", "info")
            if action == "cancel":
                return redirect(
                    url_for("neomotherbrain.operation_detail", operation_id=operation.id)
                )
            return redirect(_planning_url(operation.id, mission_type))

        submitted_paste = request.form.get("paste_text")
        if action == "apply" and not str(submitted_paste or "").strip() and preview_state:
            paste_text = preview_state.paste_text
        else:
            paste_text = str(submitted_paste or "")
        try:
            if action == "apply":
                preview = apply_alp_paste(
                    operation,
                    mission_type,
                    paste_text,
                    user=current_user,
                )
                flash(
                    f"Applied {preview['applied_count']} ALP {preview['label'].lower()} rows.",
                    "info",
                )
                clear_alp_preview_state(operation, mission_type, current_user)
                preview_is_active = False
            else:
                preview = preview_alp_paste(operation, mission_type, paste_text)
                save_alp_preview_state(
                    operation,
                    mission_type,
                    paste_text,
                    current_user,
                )
                preview_is_active = True
            _persist_alp_unmatched_rows(operation, mission_type, preview)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            preview_state = get_alp_preview_state(operation, mission_type, current_user)
            if preview_state:
                paste_text = preview_state.paste_text
                preview = preview_alp_paste(operation, mission_type, paste_text)
                preview_is_active = True
    settings = ensure_sort_timeline_settings(gateway)
    collections = _planning_live_collections(
        operation,
        mission_type,
        preview=preview,
        preview_state=preview_state,
        load_preview_state=False,
        settings=settings,
        include_page_support=True,
    )
    preview = collections["preview"]
    planning_rows = collections["planning_rows"]
    _apply_alp_row_action_error(
        planning_rows,
        _consume_alp_row_action_error(operation, mission_type),
    )
    return render_template(
        "neomotherbrain/alp_import.html",
        operation=operation,
        mission_type=mission_type,
        label="Arrival" if mission_type == "arrival" else "Departure",
        planning_title=_planning_title(mission_type),
        paste_text=paste_text,
        preview=preview,
        preview_is_active=preview_is_active,
        planning_rows=planning_rows,
        mission_rows=collections["mission_rows"],
        tail_swap_options=collections["tail_swap_options"],
        can_edit=_planning_can_edit(mission_type),
        sort_timeline_settings=settings,
        flight_api_operational_time=flight_api_operational_time_utc,
        format_flight_api_time=format_flight_api_local_time,
        wave_options=WAVE_OPTIONS,
        spare_rows=collections["spare_rows"],
        arrival_spare_candidates=collections["arrival_spare_candidates"],
        parking_positions=PARKING_RAMP_GROUPS,
        standalone_spare_aircraft_type_options=STANDALONE_SPARE_AIRCRAFT_TYPE_OPTIONS,
        live_update_status=node_auto_refresh_status(
            gateway,
            operation=operation,
        ),
        planning_revision=planning_state_revision(
            operation,
            mission_type,
            current_user,
        ),
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/<mission_type>/state"
)
@gateway_node_required("motherbrain", minimum_role="operator")
def planning_live_state(operation_id, mission_type):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = _planning_mission_type_or_404(mission_type)
    denied = _permission_guard(_planning_view_permission(mission_type))
    if denied:
        return denied

    refresh = node_auto_refresh_status(gateway, operation=operation)
    client_revision = str(request.args.get("revision") or "").strip()
    if not client_revision:
        response = jsonify(
            {
                "ok": False,
                "changed": False,
                "refresh": refresh,
                "error": "Planning live state revision is required. Reload the page.",
                "reload_required": True,
            }
        )
        response.status_code = 428
        response.headers["Cache-Control"] = "no-store"
        return response
    current_revision = planning_state_revision(
        operation,
        mission_type,
        current_user,
    )
    if client_revision == current_revision:
        response = jsonify(
            {
                "ok": True,
                "changed": False,
                "revision": current_revision,
                "refresh": refresh,
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    collections = _planning_live_collections(operation, mission_type)
    label = "Arrival" if mission_type == "arrival" else "Departure"
    fragment_context = {
        "operation": operation,
        "mission_type": mission_type,
        "label": label,
        "planning_rows": collections["planning_rows"],
        "mission_rows": collections["mission_rows"],
        "tail_swap_options": collections["tail_swap_options"],
        "can_edit": _planning_can_edit(mission_type),
        "wave_options": WAVE_OPTIONS,
    }
    fragments = _render_planning_live_fragments(operation, fragment_context)
    response = jsonify(
        {
            "ok": True,
            "changed": True,
            "revision": current_revision,
            "operation_id": operation.id,
            "mission_type": mission_type,
            "refresh": refresh,
            "rows": {
                "review": _planning_review_state_rows(collections["planning_rows"]),
                "missions": _planning_mission_state_rows(collections["mission_rows"]),
            },
            "fragments": fragments,
        }
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/<mission_type>/alp/add",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def add_alp_planning_row(operation_id, mission_type):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = _planning_mission_type_or_404(mission_type)
    if not _planning_can_run(mission_type):
        return _planning_action_error(operation, mission_type, "Access denied.", 403)

    conflict = _review_item_conflict(
        operation,
        review_key=request.form.get("review_key"),
    )
    if conflict:
        return _planning_conflict_response(operation, mission_type, conflict)

    try:
        row = _alp_planning_row_from_form(
            operation,
            mission_type,
            require_wave=mission_type == "departure",
        )
        mission = _create_or_update_mission_from_alp_planning_row(operation, row)
        _record_alp_planning_marker(operation, row, "accepted", mission)
        db.session.commit()
        message = f"{row['normalized_flight_number']} added to current sort."
    except ValueError as error:
        db.session.rollback()
        _remember_alp_row_action_error(
            operation,
            mission_type,
            request.form.get("review_key"),
            str(error),
        )
        return _planning_action_error(operation, mission_type, error)
    return _planning_action_response(operation, mission_type, message)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/<mission_type>/alp/hot",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def hot_alp_planning_row(operation_id, mission_type):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = _planning_mission_type_or_404(mission_type)
    if not _planning_can_run(mission_type):
        return _planning_action_error(operation, mission_type, "Access denied.", 403)

    conflict = _review_item_conflict(
        operation,
        review_key=request.form.get("review_key"),
    )
    if conflict:
        return _planning_conflict_response(operation, mission_type, conflict)

    try:
        row = _alp_planning_row_from_form(operation, mission_type)
        mission = _create_or_update_mission_from_alp_planning_row(operation, row)
        set_tail_hot(operation, mission.assigned_tail_number, True, user=current_user)
        _record_alp_planning_marker(operation, row, "accepted", mission)
        db.session.commit()
        message = f"{row['normalized_flight_number']} added as HOT."
    except (ParkingPlanError, ValueError) as error:
        db.session.rollback()
        _remember_alp_row_action_error(
            operation,
            mission_type,
            request.form.get("review_key"),
            str(error),
        )
        return _planning_action_error(operation, mission_type, error)
    return _planning_action_response(operation, mission_type, message)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/<mission_type>/alp/ignore",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def ignore_alp_planning_row(operation_id, mission_type):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = _planning_mission_type_or_404(mission_type)
    if not _planning_can_run(mission_type):
        return _planning_action_error(operation, mission_type, "Access denied.", 403)

    conflict = _review_item_conflict(
        operation,
        review_key=request.form.get("review_key"),
    )
    if conflict:
        return _planning_conflict_response(operation, mission_type, conflict)

    try:
        row = _alp_planning_row_from_form(operation, mission_type)
        _record_alp_planning_marker(operation, row, "ignored")
        db.session.commit()
        message = f"{row['normalized_flight_number']} ignored for this sort."
    except ValueError as error:
        db.session.rollback()
        _remember_alp_row_action_error(
            operation,
            mission_type,
            request.form.get("review_key"),
            str(error),
        )
        return _planning_action_error(operation, mission_type, error)
    return _planning_action_response(operation, mission_type, message)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/api/<int:review_item_id>/add",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def add_api_planning_row(operation_id, review_item_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = request.form.get("mission_type", "arrival")
    if not _planning_can_run(mission_type):
        return _planning_action_error(operation, mission_type, "Access denied.", 403)

    review_item, missing_response = _review_item_for_planning_action(
        gateway,
        operation,
        review_item_id,
        mission_type,
    )
    if missing_response:
        return missing_response
    if review_item.sort_date_operation_id != operation.id:
        return _planning_action_error(
            operation,
            mission_type,
            "Review item is not part of this sort operation.",
        )

    mission_type = review_item.mission_type
    conflict = _review_item_conflict(operation, item=review_item)
    if conflict:
        return _planning_conflict_response(operation, mission_type, conflict)
    try:
        wave = _planning_wave_from_form(required=mission_type == "departure")
        mission = accept_review_item(review_item)
        mission.wave = wave
        if mission.mission_type == "departure" and mission.assigned_tail_number:
            clear_spare_for_departure(
                operation,
                mission.assigned_tail_number,
                user=current_user,
            )
        db.session.commit()
        message = "API flight added to current sort operation."
    except ValueError as error:
        db.session.rollback()
        _remember_alp_row_action_error(
            operation,
            mission_type,
            review_item.review_key,
            str(error),
        )
        return _planning_action_error(operation, mission_type, error)
    return _planning_action_response(operation, mission_type, message)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/api/<int:review_item_id>/hot",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def hot_api_planning_row(operation_id, review_item_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    if not _planning_can_run("departure"):
        return _planning_action_error(operation, "departure", "Access denied.", 403)

    review_item, missing_response = _review_item_for_planning_action(
        gateway,
        operation,
        review_item_id,
        "departure",
    )
    if missing_response:
        return missing_response
    if review_item.sort_date_operation_id != operation.id or review_item.mission_type != "departure":
        return _planning_action_error(
            operation,
            "departure",
            "Review item is not a departure for this sort operation.",
        )
    conflict = _review_item_conflict(operation, item=review_item)
    if conflict:
        return _planning_conflict_response(operation, "departure", conflict)
    if not review_item.tail_number:
        return _planning_action_error(
            operation,
            "departure",
            "A tail is required to mark a planning row HOT.",
        )

    try:
        mission = accept_review_item(review_item)
        set_tail_hot(operation, mission.assigned_tail_number, True, user=current_user)
        db.session.commit()
        message = "API departure added as HOT."
    except ParkingPlanError as error:
        db.session.rollback()
        _remember_alp_row_action_error(
            operation,
            "departure",
            review_item.review_key,
            str(error),
        )
        return _planning_action_error(operation, "departure", error)
    return _planning_action_response(operation, "departure", message)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/planning/api/<int:review_item_id>/ignore",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def ignore_api_planning_row(operation_id, review_item_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission_type = request.form.get("mission_type", "arrival")
    if not _planning_can_run(mission_type):
        return _planning_action_error(operation, mission_type, "Access denied.", 403)

    review_item, missing_response = _review_item_for_planning_action(
        gateway,
        operation,
        review_item_id,
        mission_type,
    )
    if missing_response:
        return missing_response
    mission_type = review_item.mission_type
    if review_item.sort_date_operation_id != operation.id:
        return _planning_action_error(
            operation,
            mission_type,
            "Review item is not part of this sort operation.",
        )

    conflict = _review_item_conflict(operation, item=review_item)
    if conflict:
        return _planning_conflict_response(operation, mission_type, conflict)

    ignore_review_item(review_item)
    db.session.commit()
    return _planning_action_response(
        operation,
        mission_type,
        "Planning row ignored for this sort operation.",
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/spares/mark",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def mark_operation_spare(operation_id):
    operation = _operation_or_404(operation_id)
    if not _planning_can_edit("departure"):
        flash("Access denied.", "error")
        return redirect(_planning_url(operation.id, "departure"))

    try:
        tail_state = mark_arrival_tail_spare(
            operation,
            request.form.get("tail_number"),
            user=current_user,
        )
        db.session.commit()
        flash(f"{tail_state.tail_number} marked SPARE.", "info")
    except ParkingPlanError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(_planning_url(operation.id, "departure"))


@bp.route(
    "/motherbrain/operations/<int:operation_id>/spares/clear",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def clear_operation_spare(operation_id):
    operation = _operation_or_404(operation_id)
    if not _planning_can_edit("departure"):
        flash("Access denied.", "error")
        return redirect(_planning_url(operation.id, "departure"))

    try:
        tail_state = clear_tail_spare(
            operation,
            request.form.get("tail_number"),
            user=current_user,
        )
        db.session.commit()
        flash(f"{tail_state.tail_number} cleared from SPARE.", "info")
    except ParkingPlanError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(_planning_url(operation.id, "departure"))


@bp.route(
    "/motherbrain/operations/<int:operation_id>/spares/create",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def create_operation_spare(operation_id):
    operation = _operation_or_404(operation_id)
    if not _planning_can_edit("departure"):
        flash("Access denied.", "error")
        return redirect(_planning_url(operation.id, "departure"))

    try:
        tail_state = create_standalone_spare(
            operation,
            request.form.get("tail_number"),
            request.form.get("aircraft_type"),
            ramp_code=request.form.get("ramp_code"),
            position_code=request.form.get("position_code"),
            lane_number=request.form.get("lane_number") or 1,
            user=current_user,
        )
        db.session.commit()
        flash(f"{tail_state.tail_number} standalone SPARE created.", "info")
    except ParkingPlanError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(_planning_url(operation.id, "departure"))


@bp.route(
    "/motherbrain/operations/<int:operation_id>/spares/remove",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def remove_operation_spare(operation_id):
    operation = _operation_or_404(operation_id)
    if not _planning_can_edit("departure"):
        flash("Access denied.", "error")
        return redirect(_planning_url(operation.id, "departure"))

    try:
        tail_number = remove_standalone_spare(
            operation,
            request.form.get("tail_number"),
        )
        db.session.commit()
        flash(f"{tail_number} standalone SPARE removed.", "info")
    except ParkingPlanError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(_planning_url(operation.id, "departure"))


@bp.route("/motherbrain/operations/<int:operation_id>/window", methods=["POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def update_operation_window(operation_id):
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)

    try:
        operation.window_minutes = normalize_optional_window_minutes(
            request.form.get("window_minutes", "")
        )
        if "first_wave_window_minutes" in request.form:
            operation.first_wave_window_minutes = normalize_optional_window_minutes(
                request.form.get("first_wave_window_minutes", "")
            )
        if "second_wave_window_minutes" in request.form:
            operation.second_wave_window_minutes = normalize_optional_window_minutes(
                request.form.get("second_wave_window_minutes", "")
            )
    except (TypeError, ValueError):
        flash("Window minutes must be 0 or higher.", "error")
        return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))

    db.session.commit()
    flash("Operation window updated.", "info")
    return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))


@bp.route("/motherbrain/operations/<int:operation_id>/missions/new", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="operator")
def new_mission(operation_id):
    operation = _operation_or_404(operation_id)
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION if request.method == "POST" else MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    form = _mission_form_from_request(operation)

    if request.method == "POST":
        mission = SortDateMission(sort_date_operation=operation)
        try:
            _apply_mission_form(mission, operation, form)
            _raise_for_duplicate_operation_flight_number(operation, mission)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_mission_form(operation, form, "new"), 400

        db.session.add(mission)
        db.session.flush()
        _sync_tail_state_and_crew_slots(mission)
        db.session.commit()
        flash("Manual mission created.", "info")
        return redirect(
            url_for(
                "neomotherbrain.mission_detail",
                operation_id=operation.id,
                mission_id=mission.id,
            )
        )

    return _render_mission_form(operation, form, "new")


@bp.route("/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>")
@gateway_node_required("motherbrain", minimum_role="operator")
def mission_detail(operation_id, mission_id):
    gateway = get_current_gateway()
    denied = _permission_guard(MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)
    mission = _mission_or_404(operation, mission_id)
    return render_template(
        "neomotherbrain/mission_detail.html",
        operation=operation,
        mission=mission,
        timing=mission_display_timing_data(mission, operation),
        crew_covered=is_mission_crew_covered(mission.crew_assignments),
        **_flight_api_auto_poll_timer_context(gateway, operation=operation),
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/edit",
    methods=["GET", "POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def edit_mission(operation_id, mission_id):
    operation = _operation_or_404(operation_id)
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION if request.method == "POST" else MANAGE_SORT_VIEW_PERMISSION)
    if denied:
        return denied
    mission = _mission_or_404(
        operation,
        mission_id,
        for_update=request.method == "POST",
    )
    form = (
        _mission_form_from_request(operation)
        if request.method == "POST"
        else _mission_form_from_model(mission)
    )

    if request.method == "POST":
        conflict = _mission_edit_conflict(mission, form)
        if conflict:
            db.session.rollback()
            if _planning_json_requested():
                return jsonify({"ok": False, "conflict": conflict}), 409
            return (
                _render_mission_form(
                    operation,
                    form,
                    "edit",
                    mission,
                    conflict=conflict,
                ),
                409,
            )

        old_tail_number = mission.assigned_tail_number
        old_aircraft_type = _aircraft_type_for_tail(
            operation,
            old_tail_number,
        )
        try:
            _apply_mission_form(mission, operation, form)
            _raise_for_duplicate_operation_flight_number(operation, mission)
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
            return _render_mission_form(operation, form, "edit", mission), 400

        db.session.flush()
        _sync_tail_state_and_crew_slots(
            mission,
            old_tail_number=old_tail_number,
            old_aircraft_type=old_aircraft_type,
        )
        db.session.commit()
        flash("Mission updated.", "info")
        return redirect(
            url_for(
                "neomotherbrain.mission_detail",
                operation_id=operation.id,
                mission_id=mission.id,
            )
        )

    return _render_mission_form(operation, form, "edit", mission)


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/delete",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def delete_mission(operation_id, mission_id):
    denied = _permission_guard(MANAGE_SORT_EDIT_PERMISSION)
    if denied:
        return denied
    operation = _operation_or_404(operation_id)
    mission = _mission_or_404(operation, mission_id)

    SortDateCrewAssignment.query.filter_by(sort_date_mission_id=mission.id).delete()
    db.session.delete(mission)
    db.session.commit()
    flash("Mission deleted.", "info")
    return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/cancel",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def cancel_mission(operation_id, mission_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission, missing_response = _mission_for_planning_action(operation, mission_id)
    if missing_response:
        return missing_response
    if not _planning_can_edit(mission.mission_type):
        return _planning_action_error(
            operation,
            mission.mission_type,
            "Access denied.",
            403,
        )

    conflict = _mission_action_conflict(mission)
    if conflict:
        return _planning_conflict_response(operation, mission.mission_type, conflict)

    _set_mission_cancelled(mission)
    db.session.commit()
    return _planning_action_response(
        operation,
        mission.mission_type,
        f"{mission.flight_number.upper()} cancelled for this sort.",
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/restore",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def restore_mission(operation_id, mission_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission, missing_response = _mission_for_planning_action(operation, mission_id)
    if missing_response:
        return missing_response
    if not _planning_can_edit(mission.mission_type):
        return _planning_action_error(
            operation,
            mission.mission_type,
            "Access denied.",
            403,
        )

    conflict = _mission_action_conflict(mission)
    if conflict:
        return _planning_conflict_response(operation, mission.mission_type, conflict)

    _restore_mission(mission)
    db.session.commit()
    return _planning_action_response(
        operation,
        mission.mission_type,
        f"{mission.flight_number.upper()} restored for this sort.",
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/tail-swap",
    methods=["POST"],
)
@gateway_node_required("motherbrain", minimum_role="operator")
def tail_swap_mission(operation_id, mission_id):
    gateway = get_current_gateway()
    operation = _operation_or_404(operation_id)
    mission, missing_response = _mission_for_planning_action(operation, mission_id)
    if missing_response:
        return missing_response
    if not _planning_can_edit(mission.mission_type):
        return _planning_action_error(
            operation,
            mission.mission_type,
            "Access denied.",
            403,
        )
    if mission.mission_type != "departure":
        return _planning_action_error(
            operation,
            mission.mission_type,
            "Tail Swap is only available for departure missions.",
        )
    if _is_cancelled_mission(mission):
        return _planning_action_error(
            operation,
            mission.mission_type,
            "Restore the departure before swapping its tail.",
        )

    conflict = _mission_action_conflict(mission)
    if conflict:
        return _planning_conflict_response(operation, mission.mission_type, conflict)

    try:
        replacement_tail = _normalize_tail_swap_tail(
            request.form.get("replacement_tail")
        )
    except ValueError as error:
        return _planning_action_error(operation, mission.mission_type, error)

    current_tail = (mission.assigned_tail_number or "").strip().upper()
    if current_tail == replacement_tail:
        return _planning_action_response(
            operation,
            mission.mission_type,
            f"{mission.flight_number.upper()} already uses {replacement_tail}.",
        )

    conflicts = _tail_swap_departure_conflicts(operation, mission, replacement_tail)
    if conflicts and not _truthy_form_value(request.form.get("confirm_tail_swap")):
        conflict_list = ", ".join(
            _tail_swap_conflict_label(conflict) for conflict in conflicts
        )
        return _planning_action_error(
            operation,
            mission.mission_type,
            f"{replacement_tail} is already chained to {conflict_list}. "
            "Check CONFIRM to flag the source departure as needing a replacement "
            "tail and finish Tail Swap.",
        )

    old_tail_number = mission.assigned_tail_number
    old_aircraft_type = _aircraft_type_for_tail(operation, old_tail_number)
    now = datetime.utcnow()
    replacement_needed_conflicts = []
    for conflict in conflicts:
        conflict_old_tail_number = conflict.assigned_tail_number
        conflict_old_aircraft_type = _aircraft_type_for_tail(
            operation,
            conflict_old_tail_number,
        )
        conflict.assigned_tail_number = None
        conflict.tail_source = "unknown"
        conflict.tail_updated_at = now
        db.session.flush()
        _sync_tail_state_and_crew_slots(
            conflict,
            old_tail_number=conflict_old_tail_number,
            old_aircraft_type=conflict_old_aircraft_type,
        )
        replacement_needed_conflicts.append(conflict)

    mission.assigned_tail_number = replacement_tail
    mission.tail_source = "manual"
    mission.tail_updated_at = now
    db.session.flush()
    _sync_tail_state_and_crew_slots(
        mission,
        old_tail_number=old_tail_number,
        old_aircraft_type=old_aircraft_type,
    )
    db.session.commit()

    if conflicts:
        conflict_list = ", ".join(
            _tail_swap_conflict_label(conflict)
            for conflict in replacement_needed_conflicts
        )
        message = (
            f"Tail Swap complete. {mission.flight_number.upper()} now uses "
            f"{replacement_tail}; flagged {conflict_list} as needing a "
            "replacement tail."
        )
    else:
        message = (
            f"Tail Swap complete. {mission.flight_number.upper()} now uses "
            f"{replacement_tail}."
        )
    return _planning_action_response(operation, mission.mission_type, message)


def _operation_or_404(operation_id):
    gateway = get_current_gateway()
    operation = operation_by_id(operation_id)
    if not operation or operation.gateway_code != gateway.code:
        abort(404)
    return operation


def _render_new_operation_form(form):
    return render_template(
        "neomotherbrain/new_operation.html",
        form=form,
        sort_name_options=SORT_NAME_OPTIONS,
    )


def _current_sort_state(gateway):
    local_now = current_gateway_local_datetime(gateway)
    operations = current_existing_operational_sort_operations(
        gateway,
        local_now=local_now,
    )
    return {
        "sort_date": operations[0].sort_date if operations else local_now.date(),
        "local_now": local_now,
        "operations": operations,
    }


def _selected_current_operation(operations, operation_id=None):
    if operation_id:
        try:
            operation_id = int(operation_id)
        except (TypeError, ValueError):
            operation_id = None
    if operation_id:
        selected = next((operation for operation in operations if operation.id == operation_id), None)
        if selected:
            return selected
    query_operation_id = request.args.get("operation_id")
    if query_operation_id:
        try:
            query_operation_id = int(query_operation_id)
        except (TypeError, ValueError):
            query_operation_id = None
        if query_operation_id:
            selected = next(
                (operation for operation in operations if operation.id == query_operation_id),
                None,
            )
            if selected:
                return selected
    return operations[0] if operations else None


def _selected_manage_sort_operation(gateway):
    operation_id = request.values.get("operation_id")
    if not operation_id:
        return None
    try:
        operation_id = int(operation_id)
    except (TypeError, ValueError):
        return None
    return SortDateOperation.query.filter_by(
        id=operation_id,
        gateway_code=gateway.code,
    ).first()


def _planning_mission_type_or_404(mission_type):
    mission_type = str(mission_type or "").strip().lower()
    if mission_type not in {"arrival", "departure"}:
        abort(404)
    return mission_type


def _planning_title(mission_type):
    return "Arrival Planning" if mission_type == "arrival" else "Departure Planning"


def _planning_url(operation_id, mission_type):
    mission_type = _planning_mission_type_or_404(mission_type)
    return url_for(
        "neomotherbrain.alp_import",
        operation_id=operation_id,
        mission_type=mission_type,
    )


_ALP_ROW_ACTION_ERROR_SESSION_KEY = "motherbrain_alp_row_action_error"


def _remember_alp_row_action_error(operation, mission_type, review_key, message):
    review_key = str(review_key or "").strip()
    if not review_key:
        return
    session[_ALP_ROW_ACTION_ERROR_SESSION_KEY] = {
        "operation_id": operation.id,
        "mission_type": mission_type,
        "review_key": review_key,
        "message": str(message or "Unable to resolve this planning row."),
    }


def _consume_alp_row_action_error(operation, mission_type):
    error = session.get(_ALP_ROW_ACTION_ERROR_SESSION_KEY)
    if not isinstance(error, dict):
        return None
    if (
        error.get("operation_id") != operation.id
        or error.get("mission_type") != mission_type
    ):
        return None
    session.pop(_ALP_ROW_ACTION_ERROR_SESSION_KEY, None)
    return error


def _apply_alp_row_action_error(rows, error):
    if not error:
        return
    for row in rows:
        if row.get("review_key") == error.get("review_key"):
            row["action_error"] = error.get("message")
            return


def _planning_view_permission(mission_type):
    mission_type = _planning_mission_type_or_404(mission_type)
    if mission_type == "arrival":
        return ARRIVAL_PLANNING_VIEW_PERMISSION
    return DEPARTURE_PLANNING_VIEW_PERMISSION


def _planning_edit_permission(mission_type):
    mission_type = _planning_mission_type_or_404(mission_type)
    if mission_type == "arrival":
        return ARRIVAL_PLANNING_EDIT_PERMISSION
    return DEPARTURE_PLANNING_EDIT_PERMISSION


def _planning_run_permission(mission_type):
    mission_type = _planning_mission_type_or_404(mission_type)
    if mission_type == "arrival":
        return ARRIVAL_PLANNING_RUN_PERMISSION
    return DEPARTURE_PLANNING_RUN_PERMISSION


def _planning_can_edit(mission_type):
    return user_can(_planning_edit_permission(mission_type))


def _planning_can_run(mission_type):
    return user_can(_planning_run_permission(mission_type))


def _planning_live_collections(
    operation,
    mission_type,
    *,
    preview=None,
    preview_state=None,
    load_preview_state=True,
    settings=None,
    include_page_support=False,
):
    all_missions = (
        SortDateMission.query.options(
            selectinload(SortDateMission.crew_assignments)
        )
        .filter_by(sort_date_operation_id=operation.id)
        .order_by(
            SortDateMission.mission_type.asc(),
            SortDateMission.planned_datetime_utc.asc(),
            SortDateMission.id.asc(),
        )
        .all()
    )
    missions = sorted(
        (
            mission
            for mission in all_missions
            if mission.mission_type == mission_type
        ),
        key=mission_board_sort_key,
    )
    departure_missions = [
        mission for mission in all_missions if mission.mission_type == "departure"
    ]
    review_items = (
        FlightApiReviewItem.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        .order_by(FlightApiReviewItem.id.asc())
        .all()
    )
    review_items_by_key = {
        item.review_key: item for item in review_items if item.review_key
    }
    if load_preview_state:
        preview_state = get_alp_preview_state(
            operation,
            mission_type,
            current_user,
        )
    if preview is None and preview_state:
        preview = preview_alp_paste(
            operation,
            mission_type,
            preview_state.paste_text,
            missions=missions,
            departure_missions=departure_missions,
        )

    if settings is None:
        settings = SortTimelineSettings.query.filter_by(
            gateway_id=operation.gateway_id
        ).first()
    parking_assignments = _parking_assignments_for_operation(operation)
    tail_states = _tail_states_for_operation(operation)
    if mission_type == "arrival":
        mission_rows = [
            _arrival_row(
                mission,
                operation,
                parking_assignments,
                include_parking_context=True,
                tail_states=tail_states,
                taxi_minutes=taxi_to_ramp_minutes(settings),
            )
            for mission in missions
        ]
    else:
        mission_rows = [
            _departure_row(
                mission,
                operation,
                parking_assignments,
                include_parking_context=True,
                tail_states=tail_states,
            )
            for mission in missions
        ]
    planning_rows = _planning_review_rows(
        operation,
        mission_type,
        preview=preview,
        settings=settings,
        all_missions=all_missions,
        review_items=review_items,
        review_items_by_key=review_items_by_key,
    )
    _decorate_planning_review_rows(
        operation,
        planning_rows,
        review_items_by_key=review_items_by_key,
    )
    collections = {
        "preview": preview,
        "settings": settings,
        "all_missions": all_missions,
        "missions": missions,
        "parking_assignments": parking_assignments,
        "tail_states": tail_states,
        "planning_rows": planning_rows,
        "mission_rows": mission_rows,
        "tail_swap_options": _tail_swap_options_for_operation(
            operation,
            missions=all_missions,
            parking_assignments=parking_assignments.values(),
            tail_states=tail_states.values(),
        ),
        "spare_rows": [],
        "arrival_spare_candidates": [],
    }
    if include_page_support and mission_type == "departure":
        parking_bundle = ParkingPlanOperationalStateBundle(
            gateway=operation.gateway,
            operation=operation,
            assignments=list(parking_assignments.values()),
            tail_states=list(tail_states.values()),
            missions=all_missions,
            timeline_settings=settings,
        )
        collections["spare_rows"] = spare_rows_for_operation(
            operation.gateway,
            operation,
            bundle=parking_bundle,
        )
        collections["arrival_spare_candidates"] = _arrival_spare_candidate_rows(
            operation,
            parking_assignments=parking_assignments,
            tail_states=tail_states,
            all_missions=all_missions,
        )
    return collections


def _render_planning_live_fragments(operation, fragment_context):
    templates = {
        "review": "neomotherbrain/_planning_review_rows.html",
        "missions": "neomotherbrain/_planning_mission_rows.html",
        "mobile_missions": "neomotherbrain/_planning_mobile_mission_rows.html",
    }
    fragments = {
        name: current_app.jinja_env.get_template(template_name).render(
            **fragment_context
        )
        for name, template_name in templates.items()
    }
    fragments["alert_tray"] = current_app.jinja_env.get_template(
        "_my_alert_tray.html"
    ).render(
        my_alert_tray=my_alert_context(
            can_view_permission=user_can,
            gateway=operation.gateway,
            operation=operation,
            include_motherbrain=True,
            current_user_id=current_user.id,
        )
    )
    return fragments


def _planning_json_requested():
    return (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best == "application/json"
    )


def _planning_action_response(operation, mission_type, message):
    if _planning_json_requested():
        return jsonify({"ok": True, "message": message})
    flash(message, "info")
    return redirect(_planning_url(operation.id, mission_type))


def _planning_action_error(operation, mission_type, message, status_code=400):
    if _planning_json_requested():
        return jsonify({"ok": False, "error": str(message)}), status_code
    flash(str(message), "error")
    return redirect(_planning_url(operation.id, mission_type))


def _planning_conflict_response(operation, mission_type, conflict):
    db.session.rollback()
    if _planning_json_requested():
        return jsonify({"ok": False, "conflict": conflict}), 409
    flash(conflict["message"], "error")
    return redirect(_planning_url(operation.id, mission_type))


def _force_planning_overwrite():
    return str(request.form.get("force_overwrite") or "").strip() == "1"


def _review_item_conflict(operation, item=None, review_key=None):
    expected_version = request.form.get("expected_version")
    if not expected_version:
        return None
    if item is not None:
        item = (
            FlightApiReviewItem.query.filter_by(
                id=item.id,
                sort_date_operation_id=operation.id,
            )
            .with_for_update()
            .first()
        )
    elif review_key:
        item = (
            FlightApiReviewItem.query.filter_by(
                sort_date_operation_id=operation.id,
                review_key=review_key,
            )
            .with_for_update()
            .first()
        )
    if item is None or item.review_status != "pending":
        return resolved_item_conflict(getattr(item, "id", None))
    return version_conflict(
        item,
        expected_version,
        force_overwrite=_force_planning_overwrite(),
    )


def _review_item_for_planning_action(
    gateway,
    operation,
    review_item_id,
    mission_type,
):
    item = (
        FlightApiReviewItem.query.filter_by(
            id=review_item_id,
            gateway_id=gateway.id,
        )
        .with_for_update()
        .first()
    )
    if item is not None:
        return item, None
    if request.form.get("expected_version"):
        return None, _planning_conflict_response(
            operation,
            mission_type,
            resolved_item_conflict(review_item_id),
        )
    abort(404)


def _mission_action_conflict(mission):
    return version_conflict(
        mission,
        request.form.get("expected_version"),
        force_overwrite=_force_planning_overwrite(),
    )


def _mission_for_planning_action(operation, mission_id):
    mission = (
        SortDateMission.query.filter_by(
            id=mission_id,
            sort_date_operation_id=operation.id,
        )
        .with_for_update()
        .first()
    )
    if mission is not None:
        return mission, None
    if not request.form.get("expected_version"):
        abort(404)

    mission_type = _planning_mission_type_or_404(
        request.form.get("mission_type", "departure")
    )
    if not _planning_can_edit(mission_type):
        return None, _planning_action_error(
            operation,
            mission_type,
            "Access denied.",
            403,
        )
    return None, _planning_conflict_response(
        operation,
        mission_type,
        resolved_item_conflict(mission_id),
    )


def _mission_edit_conflict(mission, submitted_form):
    if _force_planning_overwrite():
        return None
    original_values = _mission_original_values_from_request()
    current_values = _mission_form_from_model(mission)
    fields = changed_field_conflicts(
        original_values,
        current_values,
        submitted_form,
        labels={
            "mission_type": "Mission type",
            "wave": "Wave",
            "flight_number": "Flight number",
            "origin": "Origin",
            "destination": "Destination",
            "assigned_tail_number": "Assigned tail",
            "planned_time_local": "Planned time",
            "timezone": "Timezone",
            "eta_datetime_utc": "ETA",
            "actual_block_in_datetime_utc": "Actual block in",
            "actual_block_out_datetime_utc": "Actual block out",
            "planned_fuel_load": "Planned fuel load",
            "fuel_status": "Fuel status",
            "arrival_status": "Arrival status",
            "departure_status": "Departure status",
            "pure_pull_time_local": "Pure pull",
            "mix_pull_time_local": "Mix pull",
        },
    )
    return version_conflict(
        mission,
        request.form.get("expected_version"),
        field_conflicts=fields,
    )


def _mission_original_values_from_request():
    try:
        values = json.loads(request.form.get("original_values") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return values if isinstance(values, dict) else {}


def _decorate_planning_review_rows(
    operation,
    rows,
    review_items_by_key=None,
):
    for row in rows:
        item = row.get("item")
        if item is None and row.get("review_key"):
            if review_items_by_key is None:
                item = FlightApiReviewItem.query.filter_by(
                    sort_date_operation_id=operation.id,
                    review_key=row["review_key"],
                    review_status="pending",
                ).first()
            else:
                candidate = review_items_by_key.get(row["review_key"])
                item = (
                    candidate
                    if candidate is not None
                    and candidate.review_status == "pending"
                    else None
                )
            if item is not None:
                row["item"] = item
        if item is not None:
            row["live_id"] = f"review:{item.id}"
            row["live_version"] = entity_version(item)
        else:
            row["live_id"] = f"preview:{row.get('review_key', '')}"
            row["live_version"] = str(row.get("review_key") or "")


def _planning_review_state_rows(rows):
    return [
        {
            "id": row["live_id"],
            "version": row["live_version"],
            "source": row.get("source"),
            "flight": row.get("flight"),
            "airport": row.get("airport"),
            "tail": row.get("tail"),
            "local_time": row.get("local_time"),
            "reason": row.get("reason"),
        }
        for row in rows
    ]


def _planning_mission_state_rows(rows):
    state_rows = []
    for row in rows:
        mission = row["mission"]
        timing = row.get("timing") or {}
        parking = row.get("parking_context") or {}
        state_rows.append(
            {
                "id": f"mission:{mission.id}",
                "entity_id": mission.id,
                "version": entity_version(mission),
                "mission_type": mission.mission_type,
                "flight_number": mission.flight_number,
                "tail_number": mission.assigned_tail_number,
                "airport": (
                    mission.origin
                    if mission.mission_type == "arrival"
                    else mission.destination
                ),
                "wave": mission.wave,
                "planned_datetime_local": _iso_value(
                    mission.planned_datetime_local
                ),
                "eta_datetime_utc": _iso_value(mission.eta_datetime_utc),
                "status": (
                    mission.arrival_status
                    if mission.mission_type == "arrival"
                    else mission.departure_status
                ),
                "parking": parking.get("label") if parking else None,
                "adjusted_departure_datetime_local": _iso_value(
                    timing.get("adjusted_planned_departure_time")
                ),
            }
        )
    return state_rows


def _iso_value(value):
    return value.isoformat() if value is not None else None


def _planning_review_rows(
    operation,
    mission_type,
    preview=None,
    settings=None,
    all_missions=None,
    review_items=None,
    review_items_by_key=None,
):
    rows = []
    persisted_keys = set()
    if all_missions is None:
        all_missions = SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    pending_items = [
        item
        for item in pending_review_items_for_operation(
            operation,
            missions=all_missions,
            items=review_items,
        )
        if item.mission_type == mission_type
    ]
    wave_lookup = _planning_wave_lookup(all_missions)
    normal_outbound_keys_by_tail = _planning_normal_outbound_keys_by_tail(
        all_missions
    )
    for item in pending_items:
        if _planning_item_is_suppressed_hot_duplicate(
            operation,
            item,
            normal_outbound_keys_by_tail=normal_outbound_keys_by_tail,
        ):
            continue
        rows.append(
            _review_item_planning_row(
                operation,
                item,
                settings,
                missions=all_missions,
                wave_lookup=wave_lookup,
            )
        )
        persisted_keys.add(item.review_key)

    if preview:
        for row in _alp_planning_rows_from_preview(
            operation,
            mission_type,
            preview,
            missions=all_missions,
            review_items_by_key=review_items_by_key,
            wave_lookup=wave_lookup,
        ):
            if row["review_key"] not in persisted_keys:
                rows.append(row)
    return sorted(rows, key=_planning_row_sort_key)


def _alp_planning_rows_from_preview(
    operation,
    mission_type,
    preview,
    missions=None,
    review_items_by_key=None,
    wave_lookup=None,
):
    rows = []
    for row in preview.get("unmatched_rows", []):
        review_key = _alp_planning_review_key(operation, mission_type, row)
        reason_detail = _alp_planning_mismatch_detail(
            operation,
            mission_type,
            row,
            missions=missions,
        )
        if review_items_by_key is None:
            existing = FlightApiReviewItem.query.filter_by(
                sort_date_operation_id=operation.id,
                review_key=review_key,
            ).first()
        else:
            existing = review_items_by_key.get(review_key)
        if existing and existing.review_status in {"ignored", "accepted"}:
            continue
        rows.append(
            {
                "source": "ALP",
                "kind": "alp",
                "mission_type": mission_type,
                "reference": f"LINE {row.get('line_number', '-')}",
                "flight": row.get("normalized_flight_number") or row.get("flight_number") or "-",
                "airport": row.get("airport") or "-",
                "tail": row.get("tail_number") or "-",
                "local_time": row.get("local_display") or "-",
                "reason": row.get("reason") or "-",
                "reason_detail": reason_detail,
                "review_key": review_key,
                "line_number": row.get("line_number"),
                "utc_datetime": row.get("utc_datetime"),
                "airport_value": row.get("airport") or "",
                "tail_value": row.get("tail_number") or "",
                "inferred_wave": _planning_inferred_wave(
                    operation,
                    mission_type,
                    row.get("normalized_flight_number") or row.get("flight_number"),
                    missions=missions,
                    wave_lookup=wave_lookup,
                ),
                "item": existing if existing and existing.review_status == "pending" else None,
            }
        )
    return rows


def _api_planning_row(
    operation,
    item,
    settings,
    missions=None,
    wave_lookup=None,
):
    operational_time = flight_api_operational_time_utc(item, settings)
    return {
        "source": "API",
        "kind": "api",
        "item": item,
        "mission_type": item.mission_type,
        "reference": f"API #{item.id}",
        "flight": (item.flight_number or "-").upper(),
        "airport": (
            item.origin if item.mission_type == "arrival" else item.destination
        ) or "-",
        "tail": item.tail_number or "-",
        "local_time": format_flight_api_local_time(operational_time, operation.gateway),
        "reason": getattr(item, "review_reason", None) or "no matching mission",
        "reason_detail": flight_api_review_reason_detail(
            item,
            operation,
            missions,
        ),
        "review_key": item.review_key,
        "inferred_wave": _planning_inferred_wave(
            operation,
            item.mission_type,
            item.flight_number,
            missions=missions,
            wave_lookup=wave_lookup,
        ),
    }


def _review_item_planning_row(
    operation,
    item,
    settings,
    missions=None,
    wave_lookup=None,
):
    payload = _planning_review_payload(item)
    if str(payload.get("source") or "").strip().upper() == "ALP":
        return _alp_planning_row_from_item(
            operation,
            item,
            payload,
            missions=missions,
            wave_lookup=wave_lookup,
        )
    return _api_planning_row(
        operation,
        item,
        settings,
        missions=missions,
        wave_lookup=wave_lookup,
    )


def _alp_planning_row_from_item(
    operation,
    item,
    payload,
    missions=None,
    wave_lookup=None,
):
    airport = (item.origin if item.mission_type == "arrival" else item.destination) or ""
    line_number = payload.get("line_number") or ""
    reference = f"LINE {line_number}" if line_number else f"ALP #{item.id}"
    return {
        "source": "ALP",
        "kind": "alp",
        "mission_type": item.mission_type,
        "reference": reference,
        "flight": item.flight_number or "-",
        "airport": airport or "-",
        "tail": item.tail_number or "-",
        "local_time": format_flight_api_local_time(item.revised_time_utc, operation.gateway),
        "reason": payload.get("reason") or "No current operation mission match.",
        "reason_detail": payload.get("reason_detail") or "",
        "review_key": item.review_key,
        "line_number": line_number,
        "utc_datetime": item.revised_time_utc,
        "airport_value": airport,
        "tail_value": item.tail_number or "",
        "inferred_wave": _planning_inferred_wave(
            operation,
            item.mission_type,
            item.flight_number,
            missions=missions,
            wave_lookup=wave_lookup,
        ),
        "item": item,
    }


def _planning_review_payload(item):
    try:
        payload = json.loads(item.raw_payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _planning_item_is_suppressed_hot_duplicate(
    operation,
    item,
    normal_outbound_keys_by_tail=None,
):
    payload = _planning_review_payload(item)
    source = str(payload.get("source") or getattr(item, "api_status", "") or "").strip().upper()
    if source != "ALP":
        return False
    flight_key = alp_flight_key(getattr(item, "flight_number", None))
    if not flight_key or not flight_key.startswith("9"):
        return False
    tail = str(getattr(item, "tail_number", "") or "").strip().upper()
    if not tail:
        return False
    if normal_outbound_keys_by_tail is not None:
        return any(
            mission_key != flight_key and not mission_key.startswith("9")
            for mission_key in normal_outbound_keys_by_tail.get(tail, ())
        )
    normal_outbound = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type="departure",
        )
        .filter(func.upper(SortDateMission.assigned_tail_number) == tail)
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )
    return any(
        mission_key
        and mission_key != flight_key
        and not mission_key.startswith("9")
        for mission_key in (alp_flight_key(mission.flight_number) for mission in normal_outbound)
    )


def _planning_row_sort_key(row):
    return (
        str(row.get("mission_type") or ""),
        str(row.get("source") or ""),
        str(row.get("local_time") or ""),
        str(row.get("flight") or ""),
        str(row.get("reference") or ""),
    )


def _alp_planning_mismatch_detail(
    operation,
    mission_type,
    row,
    missions=None,
):
    reason = str(row.get("reason") or "").strip().lower()
    if reason not in {
        "no current operation mission match.",
        "multiple current operation missions share this flight.",
    }:
        return ""
    candidates = _alp_planning_candidate_missions(
        operation,
        mission_type,
        row,
        missions=missions,
    )
    if not candidates:
        return ""
    if reason == "multiple current operation missions share this flight.":
        current_flights = ", ".join(
            sorted(
                {
                    _clean_planning_value(getattr(mission, "flight_number", None))
                    for mission in candidates
                }
            )
        )
        return (
            f"Current flight: {current_flights or '-'} / "
            f"ALP flight: {_clean_planning_value(row.get('normalized_flight_number') or row.get('flight_number'))}"
        )
    current_mission = _nearest_alp_planning_candidate(candidates, row)
    if not current_mission:
        return ""
    return (
        f"Current flight: {_clean_planning_value(getattr(current_mission, 'flight_number', None))} / "
        f"ALP flight: {_clean_planning_value(row.get('normalized_flight_number') or row.get('flight_number'))}"
    )


def _alp_planning_candidate_missions(
    operation,
    mission_type,
    row,
    missions=None,
):
    airport = str(row.get("airport") or "").strip().upper()
    if missions is None:
        query = SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        if airport:
            if mission_type == "arrival":
                query = query.filter(func.upper(SortDateMission.origin) == airport)
            else:
                query = query.filter(func.upper(SortDateMission.destination) == airport)
        candidates = query.all()
    else:
        candidates = [
            mission
            for mission in missions
            if mission.sort_date_operation_id == operation.id
            and mission.mission_type == mission_type
            and (
                not airport
                or str(
                    (
                        mission.origin
                        if mission_type == "arrival"
                        else mission.destination
                    )
                    or ""
                ).strip().upper()
                == airport
            )
        ]
    row_key = alp_flight_key(row.get("flight_number"))
    exact = [
        mission
        for mission in candidates
        if alp_flight_key(getattr(mission, "flight_number", None)) == row_key
    ]
    return exact or candidates


def _nearest_alp_planning_candidate(candidates, row):
    row_time = row.get("utc_datetime")
    if not row_time:
        return sorted(candidates, key=lambda mission: getattr(mission, "id", 0) or 0)[0]
    return sorted(
        candidates,
        key=lambda mission: (
            abs(
                (
                    (getattr(mission, "planned_datetime_utc", None) or row_time)
                    - row_time
                ).total_seconds()
            ),
            getattr(mission, "id", 0) or 0,
        ),
    )[0]


def _clean_planning_value(value):
    text = str(value or "").strip()
    return text or "-"


def _alp_planning_review_key(operation, mission_type, row):
    flight_key = row.get("flight_key") or alp_flight_key(row.get("flight_number"))
    airport = str(row.get("airport") or "").strip().upper()
    tail = str(row.get("tail_number") or "").strip().upper()
    utc_value = row.get("utc_datetime")
    utc_key = utc_value.strftime("%Y%m%d%H%M") if hasattr(utc_value, "strftime") else ""
    return f"alp:{mission_type}:{flight_key or ''}:{airport}:{tail}:{utc_key}"


def _persist_alp_unmatched_rows(operation, mission_type, preview):
    if not preview:
        return
    previous_keys = pending_review_key_sets(operation)
    rows = preview.get("unmatched_rows", [])
    active_review_keys = {
        _alp_planning_review_key(operation, mission_type, row)
        for row in rows
    }
    pending_alp_items = FlightApiReviewItem.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
        review_status="pending",
    ).all()
    for item in pending_alp_items:
        payload = _planning_review_payload(item)
        if str(payload.get("source") or "").strip().upper() != "ALP":
            continue
        if item.review_key not in active_review_keys:
            db.session.delete(item)

    for row in rows:
        row = {**row, "mission_type": mission_type}
        review_key = _alp_planning_review_key(operation, mission_type, row)
        existing = FlightApiReviewItem.query.filter_by(
            sort_date_operation_id=operation.id,
            review_key=review_key,
        ).first()
        if existing and existing.review_status in {"ignored", "accepted"}:
            continue
        row["review_key"] = review_key
        _record_alp_planning_marker(operation, row, "pending", sync_alert=False)
    db.session.flush()
    sync_unmatched_review_alerts_for_operation(
        operation,
        previous_keys=previous_keys,
    )


def _clear_pending_alp_planning_rows(operation, mission_type):
    previous_keys = pending_review_key_sets(operation)
    pending_items = FlightApiReviewItem.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
        review_status="pending",
    ).all()
    for item in pending_items:
        payload = _planning_review_payload(item)
        if str(payload.get("source") or "").strip().upper() == "ALP":
            db.session.delete(item)
    db.session.flush()
    sync_unmatched_review_alerts_for_operation(
        operation,
        previous_keys=previous_keys,
    )


def _planning_inferred_wave(
    operation,
    mission_type,
    flight_number,
    missions=None,
    wave_lookup=None,
):
    flight_key = alp_flight_key(flight_number)
    if not flight_key:
        return ""

    if wave_lookup is not None:
        waves = set(wave_lookup.get((mission_type, flight_key), ()))
    else:
        if missions is None:
            missions = SortDateMission.query.filter_by(
                sort_date_operation_id=operation.id,
                mission_type=mission_type,
            ).all()
        waves = {
            normalize_wave(mission.wave)
            for mission in missions
            if mission.mission_type == mission_type
            and alp_flight_key(mission.flight_number) == flight_key
        }
    waves.discard(None)
    return next(iter(waves)) if len(waves) == 1 else ""


def _planning_wave_lookup(missions):
    if missions is None:
        return None
    lookup = {}
    for mission in missions:
        flight_key = alp_flight_key(mission.flight_number)
        wave = normalize_wave(mission.wave)
        if flight_key and wave:
            lookup.setdefault((mission.mission_type, flight_key), set()).add(wave)
    return lookup


def _planning_normal_outbound_keys_by_tail(missions):
    if missions is None:
        return None
    lookup = {}
    for mission in missions:
        if mission.mission_type != "departure":
            continue
        tail = str(mission.assigned_tail_number or "").strip().upper()
        flight_key = alp_flight_key(mission.flight_number)
        if tail and flight_key:
            lookup.setdefault(tail, set()).add(flight_key)
    return lookup


def _planning_wave_from_form(required=False):
    submitted_wave = str(request.form.get("wave") or "").strip()
    if not submitted_wave:
        if required:
            raise ValueError("Wave is required. Select 1 or 2.")
        return None
    if submitted_wave not in WAVES:
        raise ValueError("Wave must be 1 or 2.")
    return submitted_wave


def _alp_planning_row_from_form(operation, mission_type, require_wave=False):
    flight_number = (
        request.form.get("flight_number")
        or request.form.get("normalized_flight_number")
        or ""
    )
    normalized_flight_number = normalize_alp_flight_number(flight_number)
    if not normalized_flight_number:
        raise ValueError("Flight number is required.")

    airport = str(request.form.get("airport") or "").strip().upper()
    if not airport:
        raise ValueError("Airport is required.")

    tail_number = str(request.form.get("tail_number") or "").strip().upper()
    if not tail_number:
        raise ValueError("Tail is required.")

    utc_datetime = _parse_planning_utc_datetime(request.form.get("utc_datetime"))
    row = {
        "line_number": request.form.get("line_number") or "",
        "mission_type": mission_type,
        "flight_number": flight_number,
        "normalized_flight_number": normalized_flight_number,
        "flight_key": alp_flight_key(normalized_flight_number),
        "airport": airport,
        "tail_number": tail_number,
        "utc_datetime": utc_datetime,
        "reason": request.form.get("reason") or "",
        "reason_detail": request.form.get("reason_detail") or "",
        "wave": _planning_wave_from_form(required=require_wave),
    }
    expected_key = _alp_planning_review_key(operation, mission_type, row)
    submitted_key = request.form.get("review_key") or expected_key
    if submitted_key != expected_key:
        raise ValueError("Planning row identity no longer matches.")
    row["review_key"] = expected_key
    return row


def _parse_planning_utc_datetime(value):
    value = str(value or "").strip()
    if not value:
        raise ValueError("Planning time is required.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Planning time is invalid.") from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _create_mission_from_alp_planning_row(operation, row):
    mission_type = row["mission_type"]
    timezone_name = _gateway_timezone(operation.gateway)
    planned_utc = row["utc_datetime"]
    planned_local = flight_api_utc_to_local_naive(planned_utc, timezone_name)
    airport = row["airport"]
    mission = SortDateMission(
        sort_date_operation=operation,
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        mission_type=mission_type,
        mission_source="manual",
        wave=(
            row.get("wave")
            if mission_type == "arrival"
            else row.get("wave") or "1"
        ),
        master_flight_schedule_id=None,
        flight_number=row["normalized_flight_number"],
        origin=airport if mission_type == "arrival" else operation.gateway_code,
        destination=operation.gateway_code if mission_type == "arrival" else airport,
        timezone=timezone_name,
        planned_datetime_local=planned_local,
        planned_datetime_utc=planned_utc,
        planned_source="alp",
        assigned_tail_number=row["tail_number"],
        tail_source="alp",
        tail_updated_at=datetime.utcnow(),
        api_added_current_sort_only=True,
    )
    if mission_type == "arrival":
        mission.eta_datetime_utc = planned_utc
        mission.eta_source = "alp"
        mission.arrival_status = "scheduled"
    else:
        mission.actual_block_out_datetime_utc = planned_utc
        mission.actual_block_out_source = "alp"
        mission.departure_status = "scheduled"

    _raise_for_duplicate_operation_flight_number(
        operation,
        mission,
        scope_to_mission_type=True,
    )
    db.session.add(mission)
    db.session.flush()
    _sync_tail_state_and_crew_slots(mission)
    db.session.flush()
    return mission


def _create_or_update_mission_from_alp_planning_row(operation, row):
    existing = _existing_operation_mission_for_alp_row(operation, row)
    if not existing:
        return _create_mission_from_alp_planning_row(operation, row)

    old_tail_number = existing.assigned_tail_number
    old_aircraft_type = _aircraft_type_for_tail(operation, old_tail_number)
    existing.assigned_tail_number = row["tail_number"]
    existing.tail_source = "alp"
    existing.tail_updated_at = datetime.utcnow()
    if row.get("wave") in WAVES:
        existing.wave = row["wave"]
    if existing.mission_type == "arrival":
        existing.eta_datetime_utc = row["utc_datetime"]
        existing.eta_source = "alp"
    else:
        existing.actual_block_out_datetime_utc = row["utc_datetime"]
        existing.actual_block_out_source = "alp"
    db.session.flush()
    _sync_tail_state_and_crew_slots(
        existing,
        old_tail_number=old_tail_number,
        old_aircraft_type=old_aircraft_type,
    )
    db.session.flush()
    return existing


def _existing_operation_mission_for_alp_row(operation, row):
    return SortDateMission.query.filter(
        SortDateMission.sort_date_operation_id == operation.id,
        SortDateMission.mission_type == row["mission_type"],
        func.upper(SortDateMission.flight_number)
        == row["normalized_flight_number"].upper(),
    ).first()


def _record_alp_planning_marker(
    operation,
    row,
    review_status,
    mission=None,
    *,
    sync_alert=True,
):
    review_key = row.get("review_key") or _alp_planning_review_key(
        operation,
        row["mission_type"],
        row,
    )
    item = FlightApiReviewItem.query.filter_by(
        sort_date_operation_id=operation.id,
        review_key=review_key,
    ).first()
    was_pending = bool(item and item.review_status == "pending")
    if not item:
        item = FlightApiReviewItem(
            sort_date_operation_id=operation.id,
            gateway_id=operation.gateway_id,
            gateway_code=operation.gateway_code,
            sort_date=operation.sort_date,
            sort_name=operation.sort_name,
            mission_type=row["mission_type"],
            review_key=review_key,
        )
        db.session.add(item)
    item.review_status = review_status
    item.flight_number = row["normalized_flight_number"]
    item.call_sign = None
    item.origin = row["airport"] if row["mission_type"] == "arrival" else operation.gateway_code
    item.destination = operation.gateway_code if row["mission_type"] == "arrival" else row["airport"]
    item.revised_time_utc = row["utc_datetime"]
    item.runway_time_utc = None
    item.tail_number = row["tail_number"]
    item.aircraft_model = None
    item.api_status = "ALP"
    item.accepted_mission_id = getattr(mission, "id", None)
    item.raw_payload = json.dumps(
        {
            "source": "ALP",
            "line_number": row.get("line_number"),
            "reason": row.get("reason"),
            "reason_detail": row.get("reason_detail")
            or _alp_planning_mismatch_detail(operation, row["mission_type"], row),
        },
        sort_keys=True,
    )
    db.session.flush()
    if sync_alert:
        sync_unmatched_review_alert(
            operation,
            row["mission_type"],
            new_review_keys=(
                {review_key}
                if review_status == "pending" and not was_pending
                else set()
            ),
        )
    return item


def _review_item_matches_selected_operation(gateway, review_item):
    operations = _current_sort_state(gateway)["operations"]
    selected_operation = _selected_current_operation(
        operations,
        operation_id=request.form.get("operation_id"),
    )
    return bool(
        selected_operation
        and selected_operation.id == review_item.sort_date_operation_id
    )


def _mission_or_404(operation, mission_id, for_update=False):
    query = SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
    )
    if for_update:
        query = query.with_for_update()
    return query.first_or_404()


def _master_schedule_or_404(master_id):
    gateway = get_current_gateway()
    return MasterFlightSchedule.query.filter_by(
        id=master_id,
        gateway_code=gateway.code,
    ).first_or_404()


def _master_schedules_for_gateway(gateway):
    schedules = (
        MasterFlightSchedule.query.filter_by(gateway_code=gateway.code)
        .order_by(
            MasterFlightSchedule.gateway_code.asc(),
            MasterFlightSchedule.mission_type.asc(),
            MasterFlightSchedule.sort_name.asc(),
            MasterFlightSchedule.planned_time_local.asc(),
            MasterFlightSchedule.flight_number.asc(),
        )
        .all()
    )
    return sorted(schedules, key=master_schedule_sort_key)


def _render_master_schedule_form(form=None, mode="new", master_schedule=None, rows=None):
    gateway = get_current_gateway()
    if rows is None:
        rows = [_master_schedule_row_from_form(form, 0, master_schedule.id if master_schedule else None)]

    return render_template(
        "neomotherbrain/master_schedule_form.html",
        active_day_options=ACTIVE_DAY_OPTIONS,
        aircraft_type_options=MASTER_AIRCRAFT_TYPE_OPTIONS,
        blank_row=_master_schedule_row_from_form(
            _blank_master_schedule_form(gateway),
            MASTER_SCHEDULE_BLANK_ROW_INDEX,
        ),
        gateway=gateway,
        master_schedule=master_schedule,
        mode=mode,
        mission_type_options=MISSION_TYPE_OPTIONS,
        rows=rows,
        sort_name_options=SORT_NAME_OPTIONS,
        wave_options=MASTER_WAVE_OPTIONS,
    )


def _master_schedule_form_from_request(gateway=None, prefix="", source=None):
    source = source or request.form
    active_default = "1" if request.method != "POST" else "0"
    gateway_code = gateway.code if gateway else source.get(f"{prefix}gateway_code", "RFD")
    form = {
        "gateway_code": gateway_code,
        "sort_name": source.get(f"{prefix}sort_name", "night"),
        "mission_type": source.get(f"{prefix}mission_type", "departure"),
        "wave": source.get(f"{prefix}wave", ""),
        "flight_number": source.get(f"{prefix}flight_number", ""),
        "aircraft_type": source.get(f"{prefix}aircraft_type", ""),
        "origin": source.get(f"{prefix}origin", ""),
        "destination": source.get(f"{prefix}destination", ""),
        "active_days": set(source.getlist(f"{prefix}active_days")),
        "planned_time_local": _time_value_from_form(
            source,
            f"{prefix}planned_time_local",
        ),
        "timezone": _gateway_timezone(gateway),
        "pure_pull_time_local": _time_value_from_form(
            source,
            f"{prefix}pure_pull_time_local",
        ),
        "mix_pull_time_local": _time_value_from_form(
            source,
            f"{prefix}mix_pull_time_local",
        ),
        "active": source.get(f"{prefix}active", active_default) == "1",
    }
    _apply_gateway_airport_defaults(form, gateway)
    if form["mission_type"] == "arrival":
        form["pure_pull_time_local"] = ""
        form["mix_pull_time_local"] = ""
    return form


def _master_schedule_form_for_get(gateway=None, master_schedule=None):
    form = (
        _master_schedule_form_from_model(master_schedule)
        if master_schedule
        else _blank_master_schedule_form(gateway)
    )
    requested_mission_type = request.args.get("mission_type", "").strip().lower()
    if requested_mission_type in MISSION_TYPES:
        previous_mission_type = form["mission_type"]
        form["mission_type"] = requested_mission_type
        _apply_gateway_airport_defaults(
            form,
            gateway,
            previous_mission_type=previous_mission_type,
        )
    else:
        _apply_gateway_airport_defaults(form, gateway)
    if form["mission_type"] == "arrival":
        form["pure_pull_time_local"] = ""
        form["mix_pull_time_local"] = ""
    return form


def _master_schedule_form_from_model(master_schedule):
    return {
        "gateway_code": master_schedule.gateway_code,
        "sort_name": master_schedule.sort_name,
        "mission_type": master_schedule.mission_type,
        "wave": normalize_wave(master_schedule.wave) or "",
        "flight_number": master_schedule.flight_number,
        "aircraft_type": master_schedule.aircraft_type or "",
        "origin": master_schedule.origin,
        "destination": master_schedule.destination,
        "active_days": _active_days_set(master_schedule.active_days),
        "planned_time_local": _format_time(master_schedule.planned_time_local),
        "timezone": master_schedule.timezone,
        "pure_pull_time_local": _format_time(master_schedule.pure_pull_time_local),
        "mix_pull_time_local": _format_time(master_schedule.mix_pull_time_local),
        "active": master_schedule.active,
    }


def _blank_master_schedule_form(gateway=None):
    gateway_code = gateway.code if gateway else "RFD"
    form = {
        "gateway_code": gateway_code,
        "sort_name": "night",
        "mission_type": "departure",
        "wave": "",
        "flight_number": "",
        "aircraft_type": "",
        "origin": "",
        "destination": "",
        "active_days": set(),
        "planned_time_local": "",
        "timezone": _gateway_timezone(gateway),
        "pure_pull_time_local": "",
        "mix_pull_time_local": "",
        "active": True,
    }
    _apply_gateway_airport_defaults(form, gateway)
    return form


def _apply_gateway_airport_defaults(form, gateway=None, previous_mission_type=None):
    gateway_code = gateway.code if gateway else form.get("gateway_code", "RFD")
    gateway_code = (gateway_code or "RFD").strip().upper()
    mission_type = (form.get("mission_type") or "").strip().lower()

    if mission_type == "arrival":
        if previous_mission_type == "departure" and form.get("origin") == gateway_code:
            form["origin"] = form.get("destination", "")
        form["destination"] = gateway_code
    elif mission_type == "departure":
        if previous_mission_type == "arrival" and form.get("destination") == gateway_code:
            form["destination"] = form.get("origin", "")
        form["origin"] = gateway_code


def _master_schedule_row_from_form(form, index, schedule_id=None):
    row = dict(form or {})
    row["index"] = str(index)
    row["id"] = "" if schedule_id is None else str(schedule_id)
    row["active_days"] = set(row.get("active_days") or ())
    return row


def _master_schedule_bulk_rows_from_request(gateway):
    rows = []
    for index in request.form.getlist("row_indexes"):
        prefix = f"row_{index}_"
        row = _master_schedule_form_from_request(gateway, prefix=prefix)
        row["index"] = index
        row["id"] = request.form.get(f"{prefix}id", "").strip()
        rows.append(row)
    return rows


def _first_master_schedule_row(rows):
    for row in rows:
        if _master_schedule_row_has_data(row) or row.get("id"):
            return row
    raise ValueError("Add at least one master schedule row.")


def _master_schedule_row_has_data(row):
    return any(
        (
            (row.get("flight_number") or "").strip(),
            (row.get("aircraft_type") or "").strip(),
            (row.get("origin") or "").strip(),
            (row.get("destination") or "").strip(),
            (row.get("planned_time_local") or "").strip(),
            (row.get("pure_pull_time_local") or "").strip(),
            (row.get("mix_pull_time_local") or "").strip(),
            row.get("active_days"),
        )
    )


def _create_master_schedules_from_bulk_rows(rows, gateway):
    schedules = []
    for row in rows:
        if not _master_schedule_row_has_data(row):
            continue

        schedule = MasterFlightSchedule()
        _apply_master_schedule_form(schedule, row, gateway)
        schedules.append(schedule)

    if not schedules:
        raise ValueError("Add at least one master schedule row.")

    _raise_for_duplicate_active_master_schedule_rows(schedules)
    for schedule in schedules:
        _raise_for_duplicate_active_master_schedule(schedule)
    return schedules


def _apply_master_schedule_bulk_edit(rows, schedules, gateway):
    schedules_by_id = {str(schedule.id): schedule for schedule in schedules}
    processed_schedules = []
    created_schedules = []

    for row in rows:
        schedule_id = row.get("id", "").strip()
        if not schedule_id and not _master_schedule_row_has_data(row):
            continue

        if schedule_id:
            schedule = schedules_by_id.get(schedule_id)
            if not schedule:
                raise ValueError("Master schedule row was not found.")
        else:
            schedule = MasterFlightSchedule()
            created_schedules.append(schedule)

        _apply_master_schedule_form(schedule, row, gateway)
        processed_schedules.append(schedule)

    if not processed_schedules:
        raise ValueError("Add at least one master schedule row.")

    _raise_for_duplicate_active_master_schedule_rows(processed_schedules)
    for schedule in processed_schedules:
        _raise_for_duplicate_active_master_schedule(schedule)

    db.session.add_all(created_schedules)
    updated_count = len(processed_schedules) - len(created_schedules)
    return updated_count, len(created_schedules)


def _apply_master_schedule_board_edit(
    rows,
    schedules,
    gateway,
    mission_type,
    strict_row_index=None,
    create_complete_new_rows=True,
):
    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")

    schedules_by_id = {
        str(schedule.id): schedule
        for schedule in schedules
        if schedule.mission_type == mission_type
    }
    processed_schedules = []
    created_schedules = []
    strict_row_found = not strict_row_index

    for row in rows:
        row["mission_type"] = mission_type
        is_strict_row = bool(strict_row_index and row.get("index") == strict_row_index)
        strict_row_found = strict_row_found or is_strict_row
        schedule_id = row.get("id", "").strip()
        if not schedule_id:
            if strict_row_index and not is_strict_row:
                continue
            if not strict_row_index and not create_complete_new_rows:
                continue
            if not _master_schedule_board_row_has_data(row):
                if is_strict_row:
                    raise ValueError("Complete the new master schedule row before saving.")
                continue
            if not _master_schedule_board_row_is_complete(row):
                if is_strict_row:
                    raise ValueError(
                        "Complete all required fields for the new master schedule row before saving."
                    )
                continue

        if schedule_id:
            schedule = schedules_by_id.get(schedule_id)
            if not schedule:
                raise ValueError("Master schedule row was not found.")
        else:
            schedule = MasterFlightSchedule()
            created_schedules.append(schedule)

        _apply_master_schedule_form(schedule, row, gateway)
        processed_schedules.append(schedule)

    if not strict_row_found:
        raise ValueError("New master schedule row was not found.")

    _raise_for_duplicate_active_master_schedule_rows(processed_schedules)
    for schedule in processed_schedules:
        _raise_for_duplicate_active_master_schedule(schedule)

    db.session.add_all(created_schedules)
    updated_count = len(processed_schedules) - len(created_schedules)
    return updated_count, len(created_schedules)


def _master_schedule_board_row_has_data(row):
    fields = [
        (row.get("flight_number") or "").strip(),
        (row.get("aircraft_type") or "").strip(),
        (row.get("planned_time_local") or "").strip(),
    ]
    if row.get("mission_type") == "arrival":
        fields.append((row.get("origin") or "").strip())
    else:
        fields.extend(
            [
                (row.get("destination") or "").strip(),
                (row.get("pure_pull_time_local") or "").strip(),
                (row.get("mix_pull_time_local") or "").strip(),
            ]
        )
    return any(fields)


def _master_schedule_board_row_is_complete(row):
    def time_field_is_complete(field_name):
        return bool(
            re.fullmatch(
                r"([01][0-9]|2[0-3]):[0-5][0-9]",
                (row.get(field_name) or "").strip(),
            )
        )

    if not (row.get("flight_number") or "").strip():
        return False
    if not time_field_is_complete("planned_time_local"):
        return False
    if row.get("mission_type") == "arrival":
        airport_code = (row.get("origin") or "").strip()
        return len(airport_code) == 3 and airport_code.isalpha()

    airport_code = (row.get("destination") or "").strip()
    return (
        len(airport_code) == 3
        and airport_code.isalpha()
        and bool((row.get("aircraft_type") or "").strip())
    )


def _apply_master_schedule_form(master_schedule, form, gateway=None):
    previous_planned_times = (
        (
            master_schedule.planned_time_local,
            master_schedule.pure_pull_time_local,
            master_schedule.mix_pull_time_local,
        )
        if master_schedule.id is not None
        else None
    )
    gateway_code = gateway.code if gateway else form["gateway_code"].strip().upper()
    sort_name = form["sort_name"].strip().lower()
    mission_type = form["mission_type"].strip().lower()
    wave = _normalize_master_schedule_wave(form.get("wave"))
    flight_number = _normalize_flight_number(form["flight_number"])
    aircraft_type = _normalize_master_aircraft_type(form.get("aircraft_type", ""))
    origin = _normalize_airport_code(form["origin"], "Origin")
    destination = _normalize_airport_code(form["destination"], "Destination")
    timezone = _gateway_timezone(gateway)

    if sort_name not in SORT_NAMES:
        raise ValueError("Sort name must be Night, Twilight, Day, or Sunrise.")
    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")
    if not all((gateway_code, sort_name, flight_number, origin, destination)):
        raise ValueError("Gateway, sort, flight, origin, and destination are required.")
    if mission_type == "departure" and master_schedule.id is None and not aircraft_type:
        raise ValueError("AC Type is required for new departures.")

    planned_time_local = _parse_time(form["planned_time_local"], "Planned time")

    master_schedule.gateway_code = gateway_code
    master_schedule.gateway_id = gateway.id if gateway else None
    master_schedule.sort_name = sort_name
    master_schedule.mission_type = mission_type
    master_schedule.wave = wave
    master_schedule.flight_number = flight_number
    master_schedule.aircraft_type = aircraft_type
    master_schedule.origin = origin
    master_schedule.destination = destination
    master_schedule.active_days = _active_days_value(form["active_days"])
    master_schedule.planned_time_local = planned_time_local
    master_schedule.timezone = timezone
    master_schedule.active = bool(form["active"])

    if mission_type == "arrival":
        master_schedule.pure_pull_time_local = None
        master_schedule.mix_pull_time_local = None
        return

    master_schedule.pure_pull_time_local = _parse_optional_time(
        form["pure_pull_time_local"],
        "Pure pull time",
    )
    master_schedule.mix_pull_time_local = _parse_optional_time(
        form["mix_pull_time_local"],
        "Mix pull time",
    )
    current_planned_times = (
        master_schedule.planned_time_local,
        master_schedule.pure_pull_time_local,
        master_schedule.mix_pull_time_local,
    )
    if (
        gateway
        and previous_planned_times is not None
        and previous_planned_times != current_planned_times
    ):
        _propagate_master_planned_times(master_schedule, gateway)


def _propagate_master_planned_times(master_schedule, gateway):
    operation_ids = [
        operation.id
        for operation in current_operations_for_gateway(gateway)
        if operation.id is not None
    ]
    if not operation_ids:
        return []

    missions = (
        SortDateMission.query.filter(
            SortDateMission.master_flight_schedule_id == master_schedule.id,
            SortDateMission.sort_date_operation_id.in_(operation_ids),
            SortDateMission.mission_type == "departure",
        )
        .order_by(SortDateMission.id.asc())
        .all()
    )
    return [
        mission
        for mission in missions
        if apply_master_planned_times_to_mission(
            mission,
            master_schedule,
            mission.sort_date_operation,
        )
    ]


def _raise_for_duplicate_active_master_schedule(master_schedule):
    if not master_schedule.active:
        return

    duplicate_query = MasterFlightSchedule.query.filter(
        MasterFlightSchedule.active.is_(True),
        MasterFlightSchedule.gateway_code == master_schedule.gateway_code,
        MasterFlightSchedule.sort_name == master_schedule.sort_name,
        MasterFlightSchedule.mission_type == master_schedule.mission_type,
        func.upper(MasterFlightSchedule.flight_number) == master_schedule.flight_number.upper(),
    )

    if master_schedule.id:
        duplicate_query = duplicate_query.filter(MasterFlightSchedule.id != master_schedule.id)

    if duplicate_query.first():
        raise ValueError(
            "An active master schedule row already exists for this "
            "gateway, sort, mission type, and flight number."
        )


def _raise_for_duplicate_active_master_schedule_rows(schedules):
    seen = {}
    for schedule in schedules:
        if not schedule.active:
            continue

        key = (
            schedule.gateway_code,
            schedule.sort_name,
            schedule.mission_type,
            schedule.flight_number.upper(),
        )
        if key in seen:
            raise ValueError(
                "Duplicate active master schedule rows are not allowed in the same save."
            )
        seen[key] = schedule.id


def _active_days_value(active_days):
    selected_days = set(active_days or ())
    return ",".join(day for day, _label in ACTIVE_DAY_OPTIONS if day in selected_days)


def _active_days_set(active_days):
    if not active_days:
        return set()

    return {day.strip().lower() for day in active_days.split(",") if day.strip()}


def _parse_time(value, label):
    value = (value or "").strip()
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError(f"{label} must use HH:MM military format.")
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        raise ValueError(f"{label} must use HH:MM military format.") from None


def _parse_optional_time(value, label):
    value = (value or "").strip()
    if not value:
        return None

    return _parse_time(value, label)


def _time_value_from_form(source, name):
    direct_value = (source.get(name, "") or "").strip()
    if direct_value:
        return direct_value

    hour = (source.get(f"{name}_hour", "") or "").strip()
    minute = (source.get(f"{name}_minute", "") or "").strip()
    if not hour and not minute:
        return ""

    if hour.isdigit():
        hour = hour.zfill(2)
    if minute.isdigit():
        minute = minute.zfill(2)

    return f"{hour}:{minute}"


def _normalize_master_schedule_wave(value):
    raw_wave = (value or "").strip()
    if not raw_wave:
        return None

    wave = normalize_wave(raw_wave)
    if wave not in WAVES:
        raise ValueError("Wave must be blank, 1, or 2.")
    return wave


def _normalize_master_aircraft_type(value):
    aircraft_type = (value or "").strip()
    if not aircraft_type:
        return None
    normalized = aircraft_type.upper()
    options_by_upper = {
        option.upper(): option
        for option in MASTER_AIRCRAFT_TYPE_OPTIONS
        if option
    }
    if normalized not in options_by_upper:
        raise ValueError("AC Type must be A300, 747, 757, 767, or Other.")
    return options_by_upper[normalized]


def _wants_json_response():
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or request.accept_mimetypes.best == "application/json"
    )


def _datetime_value_from_form(source, name):
    direct_value = (source.get(name, "") or "").strip()
    if direct_value:
        return direct_value

    date_value = (source.get(f"{name}_date", "") or "").strip()
    time_value = _time_value_from_form(source, name)
    if not date_value and not time_value:
        return ""
    if date_value and time_value:
        return f"{date_value} {time_value}"
    return f"{date_value} {time_value}".strip()


def _format_time(value):
    return value.strftime("%H:%M") if value else ""


def _gateway_timezone(gateway=None):
    return current_app.config.get("DEFAULT_GATEWAY_TIMEZONE", "America/Chicago")


def _normalize_flight_number(value):
    flight_number = (value or "").strip().upper()
    if not flight_number:
        raise ValueError("Flight number is required.")
    if len(flight_number) > 8:
        raise ValueError("Flight number must be 8 characters or fewer.")
    return flight_number


def _normalize_airport_code(value, label):
    code = (value or "").strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"{label} must be exactly 3 letters.")
    return code


def _render_mission_form(operation, form, mode, mission=None, conflict=None):
    current_values = _mission_form_from_model(mission) if mission else {}
    original_values = request.form.get("original_values") if conflict else None
    if not original_values:
        original_values = json.dumps(
            {
                key: value
                for key, value in current_values.items()
                if key != "expected_version"
            },
            sort_keys=True,
        )
    return render_template(
        "neomotherbrain/mission_form.html",
        arrival_statuses=ARRIVAL_STATUSES,
        departure_statuses=DEPARTURE_STATUSES,
        form=form,
        fuel_statuses=FUEL_STATUSES,
        mission=mission,
        mode=mode,
        operation=operation,
        wave_options=WAVE_OPTIONS,
        conflict=conflict,
        expected_version=(
            request.form.get("expected_version", "")
            if conflict
            else entity_version(mission) if mission else ""
        ),
        original_values=original_values,
    )


def _mission_form_from_request(operation):
    requested_mission_type = request.form.get("mission_type") or request.args.get(
        "mission_type",
        "departure",
    )
    return {
        "mission_type": requested_mission_type,
        "wave": request.form.get("wave", "1"),
        "flight_number": request.form.get("flight_number", ""),
        "origin": request.form.get("origin", ""),
        "destination": request.form.get("destination", ""),
        "assigned_tail_number": request.form.get("assigned_tail_number", ""),
        "planned_time_local": _time_value_from_form(request.form, "planned_time_local"),
        "timezone": request.form.get("timezone", "America/Chicago"),
        "eta_datetime_utc": _datetime_value_from_form(request.form, "eta_datetime_utc"),
        "actual_block_in_datetime_utc": _datetime_value_from_form(
            request.form,
            "actual_block_in_datetime_utc",
        ),
        "actual_block_out_datetime_utc": _datetime_value_from_form(
            request.form,
            "actual_block_out_datetime_utc",
        ),
        "planned_fuel_load": request.form.get("planned_fuel_load", ""),
        "fuel_status": request.form.get("fuel_status", ""),
        "arrival_status": request.form.get("arrival_status", ""),
        "departure_status": request.form.get("departure_status", ""),
        "pure_pull_time_local": _time_value_from_form(
            request.form,
            "pure_pull_time_local",
        ),
        "mix_pull_time_local": _time_value_from_form(
            request.form,
            "mix_pull_time_local",
        ),
        "expected_version": request.form.get("expected_version", ""),
    }


def _mission_form_from_model(mission):
    return {
        "mission_type": mission.mission_type,
        "wave": normalize_wave(mission.wave),
        "flight_number": mission.flight_number,
        "origin": mission.origin,
        "destination": mission.destination,
        "assigned_tail_number": mission.assigned_tail_number or "",
        "planned_time_local": _format_time(
            mission.planned_datetime_local.time()
            if mission.planned_datetime_local
            else None
        ),
        "timezone": mission.timezone,
        "eta_datetime_utc": _format_datetime_local(mission.eta_datetime_utc),
        "actual_block_in_datetime_utc": _format_datetime_local(
            mission.actual_block_in_datetime_utc
        ),
        "actual_block_out_datetime_utc": _format_datetime_local(
            mission.actual_block_out_datetime_utc
        ),
        "planned_fuel_load": "" if mission.planned_fuel_load is None else str(mission.planned_fuel_load),
        "fuel_status": mission.fuel_status or "",
        "arrival_status": mission.arrival_status or "",
        "departure_status": mission.departure_status or "",
        "pure_pull_time_local": _format_time(mission.pure_pull_time_local),
        "mix_pull_time_local": _format_time(mission.mix_pull_time_local),
        "expected_version": entity_version(mission),
    }


def _apply_mission_form(mission, operation, form):
    mission_type = form["mission_type"].strip().lower()
    wave = normalize_wave(form.get("wave"))
    flight_number = form["flight_number"].strip().upper()
    origin = form["origin"].strip().upper()
    destination = form["destination"].strip().upper()
    timezone = form["timezone"].strip() or "America/Chicago"
    assigned_tail_number = form["assigned_tail_number"].strip().upper() or None

    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")
    if wave not in WAVES:
        raise ValueError("Wave must be 1 or 2.")

    if not all((flight_number, origin, destination)):
        raise ValueError("Flight number, origin, and destination are required.")

    planned_time_local = _parse_time(form["planned_time_local"], "Planned time")
    planned_datetime_local = sort_datetime_for_local_time(
        operation.sort_date,
        operation.sort_name,
        planned_time_local,
    )

    mission.sort_date_operation = operation
    mission.sort_date = operation.sort_date
    mission.gateway_code = operation.gateway_code
    mission.sort_name = operation.sort_name
    mission.mission_type = mission_type
    mission.mission_source = "manual"
    mission.wave = wave
    mission.master_flight_schedule_id = None
    mission.flight_number = flight_number
    mission.origin = origin
    mission.destination = destination
    mission.timezone = timezone
    mission.planned_datetime_local = planned_datetime_local
    mission.planned_datetime_utc = _planned_datetime_utc_for_mission(
        planned_datetime_local,
        timezone,
    )
    mission.planned_source = "manual"
    mission.assigned_tail_number = assigned_tail_number
    mission.tail_source = "manual" if assigned_tail_number else "unknown"
    mission.tail_updated_at = datetime.utcnow() if assigned_tail_number else None
    mission.eta_datetime_utc = _parse_optional_datetime(
        form["eta_datetime_utc"],
        "ETA UTC",
    )
    mission.eta_source = "manual" if mission.eta_datetime_utc else "unknown"
    mission.actual_block_in_datetime_utc = _parse_optional_datetime(
        form["actual_block_in_datetime_utc"],
        "Actual block in UTC",
    )
    mission.actual_block_in_source = (
        "manual" if mission.actual_block_in_datetime_utc else "unknown"
    )
    mission.actual_block_out_datetime_utc = _parse_optional_datetime(
        form["actual_block_out_datetime_utc"],
        "Actual block out UTC",
    )
    mission.actual_block_out_source = (
        "manual" if mission.actual_block_out_datetime_utc else "unknown"
    )
    mission.planned_fuel_load = _parse_optional_int(
        form["planned_fuel_load"],
        "Planned fuel load",
    )
    mission.fuel_status = _choice_or_none(form["fuel_status"], FUEL_STATUSES, "Fuel status")

    if mission_type == "arrival":
        mission.arrival_status = _choice_or_none(
            form["arrival_status"],
            ARRIVAL_STATUSES,
            "Arrival status",
        ) or "scheduled"
        mission.pure_pull_time_local = None
        mission.mix_pull_time_local = None
        mission.pull_time_source = None
        mission.departure_status = None
        return

    mission.arrival_status = None
    mission.departure_status = _choice_or_none(
        form["departure_status"],
        DEPARTURE_STATUSES,
        "Departure status",
    ) or "scheduled"
    mission.pure_pull_time_local = _parse_optional_time(
        form["pure_pull_time_local"],
        "Pure pull time",
    )
    mission.mix_pull_time_local = _parse_optional_time(
        form["mix_pull_time_local"],
        "Mix pull time",
    )
    if any(
        (
            mission.pure_pull_time_local,
            mission.mix_pull_time_local,
        )
    ):
        mission.pull_time_source = "manual"
    else:
        mission.pull_time_source = None


def _raise_for_duplicate_operation_flight_number(
    operation,
    mission,
    scope_to_mission_type=False,
):
    with db.session.no_autoflush:
        duplicate_query = SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            func.upper(SortDateMission.flight_number) == mission.flight_number.upper(),
        )
        if scope_to_mission_type:
            duplicate_query = duplicate_query.filter(
                SortDateMission.mission_type == mission.mission_type
            )

        if mission.id:
            duplicate_query = duplicate_query.filter(SortDateMission.id != mission.id)

        if duplicate_query.first():
            raise ValueError("A mission with this flight number already exists in this operation.")


def _sync_tail_state_and_crew_slots(
    mission,
    old_tail_number=None,
    old_aircraft_type="unknown",
):
    tail_state = ensure_tail_state_for_mission(mission)
    if mission.mission_type == "departure" and mission.assigned_tail_number:
        tail_state = clear_spare_for_departure(
            mission.sort_date_operation,
            mission.assigned_tail_number,
            user=current_user if current_user and not current_user.is_anonymous else None,
        ) or tail_state
    new_aircraft_type = _aircraft_type_from_tail_state_or_number(
        tail_state,
        mission.assigned_tail_number,
    )

    current_assignments = list(mission.crew_assignments)
    current_sections = tuple(assignment.aircraft_section for assignment in current_assignments)
    required_sections = tuple(default_required_crew_sections(new_aircraft_type))

    if old_tail_number is not None and old_tail_number != mission.assigned_tail_number:
        keep_sections = set(
            crew_sections_for_tail_swap(
                current_sections,
                old_aircraft_type,
                new_aircraft_type,
            )["keep"]
        )
    else:
        keep_sections = set(current_sections)

    for assignment in current_assignments:
        if (
            assignment.aircraft_section not in required_sections
            or assignment.aircraft_section not in keep_sections
        ):
            db.session.delete(assignment)

    db.session.flush()
    existing_sections = {
        assignment.aircraft_section
        for assignment in SortDateCrewAssignment.query.filter_by(
            sort_date_mission_id=mission.id
        ).all()
    }
    for section in required_sections:
        if section in existing_sections:
            continue
        db.session.add(
            SortDateCrewAssignment(
                sort_date_mission_id=mission.id,
                aircraft_section=section,
                required=True,
            )
        )


def _aircraft_type_for_tail(operation, tail_number):
    if not tail_number:
        return "unknown"

    tail_state = SortDateTailState.query.filter_by(
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        tail_number=tail_number,
    ).first()
    return _aircraft_type_from_tail_state_or_number(tail_state, tail_number)


def _aircraft_type_from_tail_state_or_number(tail_state, tail_number):
    if tail_state:
        if tail_state.aircraft_type_source == "manual":
            return tail_state.aircraft_type or "unknown"
        if tail_state.aircraft_type:
            return tail_state.aircraft_type

    return derive_aircraft_type_from_tail_number(tail_number)


def _choice_or_none(value, allowed_values, label):
    value = (value or "").strip()
    if not value:
        return None
    if value not in allowed_values:
        raise ValueError(f"{label} is invalid.")
    return value


def _parse_optional_int(value, label):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{label} must be a whole number.") from None


def _parse_optional_datetime(value, label):
    value = (value or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} ([01][0-9]|2[0-3]):[0-5][0-9]", value):
        raise ValueError(f"{label} must use YYYY-MM-DD HH:MM military format.")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"{label} must use YYYY-MM-DD HH:MM military format.") from None


def _format_datetime_local(value):
    return value.strftime("%Y-%m-%d %H:%M") if value else ""


def _planned_datetime_utc_for_mission(planned_datetime_local, timezone):
    from app.services.sort_date_operations import _planned_datetime_utc

    return _planned_datetime_utc(planned_datetime_local, timezone)


def _all_missions_for_operation(operation):
    missions = (
        SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
        .order_by(
            SortDateMission.mission_type.asc(),
            SortDateMission.planned_datetime_utc.asc(),
            SortDateMission.flight_number.asc(),
        )
        .all()
    )
    return sorted(missions, key=mission_board_sort_key)


def _missions_for_operation(operation, mission_type, include_cancelled=True):
    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        .order_by(SortDateMission.planned_datetime_utc.asc())
        .all()
    )
    if not include_cancelled:
        missions = [mission for mission in missions if not _is_cancelled_mission(mission)]
    return sorted(missions, key=mission_board_sort_key)


def _mission_count(operation, mission_type):
    return SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
    ).count()


def _arrival_row(
    mission,
    operation=None,
    parking_assignments=None,
    include_parking_context=False,
    tail_states=None,
    taxi_minutes=None,
):
    arrival_display = _arrival_board_display(
        mission,
        operation,
        taxi_minutes=taxi_minutes,
    )
    eta_delta_minutes = _arrival_eta_delta_minutes(mission, arrival_display)
    row = {
        "mission": mission,
        "is_cancelled": _is_cancelled_mission(mission),
        "parking_position": _parking_position_for_mission(
            mission,
            parking_assignments,
            tail_states=tail_states,
        ),
        "eta_time": arrival_display["time"],
        "eta_time_note": arrival_display["time_note"],
        "eta_delta_minutes": eta_delta_minutes,
        "eta_delta_display": _format_arrival_eta_delta(eta_delta_minutes),
        "is_late": eta_delta_minutes is not None and eta_delta_minutes > 0,
        "status_label": arrival_display["status_label"],
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }
    if include_parking_context:
        row["parking_context"] = _planning_parking_context_for_mission(
            mission,
            parking_assignments=parking_assignments,
            tail_states=tail_states,
        )
    return row


def _departure_row(
    mission,
    operation,
    parking_assignments=None,
    include_parking_context=False,
    tail_states=None,
):
    row = {
        "mission": mission,
        "is_cancelled": _is_cancelled_mission(mission),
        "needs_replacement_tail": _mission_needs_replacement_tail(mission),
        "timing": mission_display_timing_data(mission, operation),
        "parking_position": _parking_position_for_mission(
            mission,
            parking_assignments,
            tail_states=tail_states,
        ),
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }
    if include_parking_context:
        row["parking_context"] = _planning_parking_context_for_mission(
            mission,
            parking_assignments=parking_assignments,
            tail_states=tail_states,
        )
    return row


def _mission_list_row(mission, operation, parking_assignments=None):
    timing = mission_display_timing_data(mission, operation)
    if mission.mission_type == "arrival":
        display_time = mission.eta_datetime_utc or mission.planned_datetime_local
    else:
        display_time = timing.get("adjusted_planned_departure_time")

    return {
        "mission": mission,
        "timing": timing,
        "parking_position": _parking_position_for_mission(mission, parking_assignments),
        "display_time": display_time,
        "status": (
            mission.arrival_status
            if mission.mission_type == "arrival"
            else mission.departure_status
        ),
    }


def _arrival_eta_display_time(
    mission,
    operation=None,
    taxi_minutes=None,
):
    if mission.eta_datetime_utc:
        timezone_name = (
            getattr(mission, "timezone", None)
            or _gateway_timezone(getattr(operation, "gateway", None))
        )
        eta_datetime_utc = mission.eta_datetime_utc
        if _arrival_eta_uses_taxi_offset(mission):
            eta_datetime_utc = eta_datetime_utc + timedelta(
                minutes=_arrival_board_taxi_minutes(
                    operation,
                    taxi_minutes=taxi_minutes,
                )
            )
        return flight_api_utc_to_local_naive(eta_datetime_utc, timezone_name)
    return mission.planned_datetime_local


def _arrival_eta_uses_taxi_offset(mission):
    return (getattr(mission, "eta_source", "") or "").strip().lower() == "api"


def _arrival_board_display(
    mission,
    operation=None,
    taxi_minutes=None,
):
    timezone_name = (
        getattr(mission, "timezone", None)
        or _gateway_timezone(getattr(operation, "gateway", None))
    )
    manual_status = (mission.arrival_status or "").strip().lower()
    if manual_status == CANCELLED_MISSION_STATUS:
        return {
            "time": _arrival_eta_display_time(
                mission,
                operation,
                taxi_minutes=taxi_minutes,
            ),
            "time_note": "",
            "status_label": "CANCELLED",
        }

    if manual_status in {"arrived", "unloaded"}:
        return {
            "time": _arrival_manual_time(
                mission,
                operation,
                timezone_name,
                taxi_minutes=taxi_minutes,
            ),
            "time_note": "",
            "status_label": _status_label(manual_status),
        }

    if mission.api_runway_time_utc:
        parking_time = _arrival_assumed_arrived_time_utc(
            mission,
            operation,
            taxi_minutes=taxi_minutes,
        )
        return {
            "time": _arrival_local_time(parking_time, timezone_name),
            "time_note": "",
            "status_label": _arrival_runway_status_label(mission, parking_time),
        }

    raw_status = (getattr(mission, "api_status_raw", None) or "").strip().lower()
    if "arrived" in raw_status:
        return {
            "time": _arrival_eta_display_time(
                mission,
                operation,
                taxi_minutes=taxi_minutes,
            ),
            "time_note": "",
            "status_label": "Arrived",
        }

    api_status = (mission.api_status or "").strip().lower()
    if api_status:
        status_label = "Expected" if api_status == "scheduled" else mission.api_status
    elif manual_status:
        status_label = _status_label(manual_status)
    else:
        status_label = "Expected"

    return {
        "time": _arrival_eta_display_time(
            mission,
            operation,
            taxi_minutes=taxi_minutes,
        ),
        "time_note": "",
        "status_label": status_label,
    }


def _arrival_assumed_arrived_time_utc(
    mission,
    operation=None,
    taxi_minutes=None,
):
    if not getattr(mission, "api_runway_time_utc", None):
        return None
    return mission.api_runway_time_utc + timedelta(
        minutes=_arrival_board_taxi_minutes(
            operation,
            taxi_minutes=taxi_minutes,
        )
    )


def _arrival_runway_status_label(mission, assumed_arrived_time_utc):
    api_status = (getattr(mission, "api_status", None) or "").strip().lower()
    if api_status == "assumed arrived":
        return "Assumed Arrived"
    if assumed_arrived_time_utc:
        now_utc = _coerce_utc_naive(_current_utc_naive())
        if now_utc and now_utc >= _coerce_utc_naive(assumed_arrived_time_utc):
            return "Assumed Arrived"
    return "On Ground"


def _arrival_eta_delta_display(mission, arrival_display):
    delta = _arrival_eta_delta_minutes(mission, arrival_display)
    return _format_arrival_eta_delta(delta)


def _format_arrival_eta_delta(delta):
    if delta is None:
        return "-"
    if delta > 0:
        return f"+{delta}"
    return str(delta)


def _arrival_eta_delta_minutes(mission, arrival_display):
    planned_local = getattr(mission, "planned_datetime_local", None)
    display_time = (arrival_display or {}).get("time")
    if not planned_local or not display_time:
        return None

    has_operational_eta = any(
        getattr(mission, attr, None)
        for attr in (
            "actual_block_in_datetime_utc",
            "api_runway_time_utc",
            "eta_datetime_utc",
        )
    )
    if not has_operational_eta:
        return None

    display_time = _nearest_operational_datetime(display_time, planned_local)
    delta = display_time - planned_local
    return int(round(delta.total_seconds() / 60))


def _nearest_operational_datetime(value, reference):
    candidates = [value + timedelta(days=offset) for offset in (-1, 0, 1)]
    return min(candidates, key=lambda candidate: abs(candidate - reference))


def _arrival_manual_time(
    mission,
    operation=None,
    timezone_name=None,
    taxi_minutes=None,
):
    timezone_name = timezone_name or (
        getattr(mission, "timezone", None)
        or _gateway_timezone(getattr(operation, "gateway", None))
    )
    if mission.actual_block_in_datetime_utc:
        return _arrival_local_time(mission.actual_block_in_datetime_utc, timezone_name)
    return _arrival_eta_display_time(
        mission,
        operation,
        taxi_minutes=taxi_minutes,
    )


def _arrival_local_time(value, timezone_name):
    if not value:
        return None
    return flight_api_utc_to_local_naive(value, timezone_name)


def _current_utc_naive():
    return datetime.utcnow()


def _coerce_utc_naive(value):
    if not value:
        return None
    if getattr(value, "tzinfo", None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _arrival_board_taxi_minutes(operation=None, taxi_minutes=None):
    if taxi_minutes is not None:
        return int(taxi_minutes)
    gateway = getattr(operation, "gateway", None)
    if not gateway:
        return taxi_to_ramp_minutes(None)
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    return taxi_to_ramp_minutes(settings)


def _status_label(value):
    return str(value or "").replace("_", " ").title()


def _mission_status_field(mission):
    return "arrival_status" if mission.mission_type == "arrival" else "departure_status"


def _is_cancelled_mission(mission):
    status = getattr(mission, _mission_status_field(mission), None)
    return (status or "").strip().lower() == CANCELLED_MISSION_STATUS


def _mission_needs_replacement_tail(mission):
    return (
        mission.mission_type == "departure"
        and not _is_cancelled_mission(mission)
        and not (mission.assigned_tail_number or "").strip()
    )


def _set_mission_cancelled(mission):
    setattr(mission, _mission_status_field(mission), CANCELLED_MISSION_STATUS)


def _restore_mission(mission):
    if _is_cancelled_mission(mission):
        setattr(mission, _mission_status_field(mission), None)


def _normalize_tail_swap_tail(value):
    tail_number = re.sub(r"\s+", "", value or "").strip().upper()
    if not tail_number:
        raise ValueError("Replacement tail is required.")
    if len(tail_number) > 32:
        raise ValueError("Replacement tail is too long.")
    return tail_number


def _tail_swap_departure_conflicts(operation, mission, replacement_tail):
    conflicts = (
        SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            SortDateMission.mission_type == "departure",
            func.upper(SortDateMission.assigned_tail_number) == replacement_tail,
            SortDateMission.id != mission.id,
        )
        .order_by(SortDateMission.planned_datetime_utc.asc(), SortDateMission.id.asc())
        .all()
    )
    active_conflicts = [
        conflict for conflict in conflicts if not _is_cancelled_mission(conflict)
    ]
    return sorted(
        active_conflicts,
        key=lambda conflict: (
            getattr(conflict, "planned_datetime_utc", None) or datetime.max,
            getattr(conflict, "id", 0) or 0,
        ),
    )


def _tail_swap_conflict_label(mission):
    flight = (mission.flight_number or "").strip().upper() or f"MISSION {mission.id}"
    tail = (mission.assigned_tail_number or "").strip().upper()
    if tail:
        return f"{flight} ({tail})"
    return flight


def _tail_swap_options_for_operation(
    operation,
    missions=None,
    parking_assignments=None,
    tail_states=None,
):
    tails = set()
    if missions is None:
        missions = SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    if parking_assignments is None:
        parking_assignments = SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    if tail_states is None:
        tail_states = SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
    for mission in missions:
        tail = (mission.assigned_tail_number or "").strip().upper()
        if tail:
            tails.add(tail)
    for assignment in parking_assignments:
        tail = (assignment.tail_number or "").strip().upper()
        if tail:
            tails.add(tail)
    for state in tail_states:
        tail = (state.tail_number or "").strip().upper()
        if tail:
            tails.add(tail)
    return sorted(tails)


def _parking_assignments_for_operation(operation):
    operation_id = getattr(operation, "id", operation)
    if not operation_id:
        return {}

    return {
        (assignment.tail_number or "").strip().upper(): assignment
        for assignment in SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation_id
        ).all()
        if assignment.tail_number
    }


def _parking_position_for_mission(
    mission,
    parking_assignments=None,
    tail_states=None,
):
    tail_number = (mission.assigned_tail_number or "").strip().upper()
    if not tail_number:
        return None

    if parking_assignments is None:
        parking_assignments = _parking_assignments_for_operation(
            mission.sort_date_operation_id
        )
    assignment = parking_assignments.get(tail_number)
    if assignment and assignment.position_code:
        return assignment.position_code

    tail_state = (
        tail_states.get(tail_number)
        if tail_states is not None
        else _tail_state_for_mission(mission)
    )
    return tail_state.parking_position if tail_state else None


def _planning_parking_context_for_mission(
    mission,
    parking_assignments=None,
    tail_states=None,
):
    tail_number = (mission.assigned_tail_number or "").strip().upper()
    if not tail_number:
        return {
            "has_tail": False,
            "tail_number": "",
            "label": "-",
            "position": None,
            "is_assigned": False,
            "is_oos": False,
            "is_cancelled": _is_cancelled_mission(mission),
        }

    if parking_assignments is None:
        parking_assignments = _parking_assignments_for_operation(
            mission.sort_date_operation_id
        )
    tail_states_supplied = tail_states is not None
    if tail_states is None:
        tail_states = {}
    assignment = parking_assignments.get(tail_number)
    position = None
    if assignment and assignment.position_code:
        position = assignment.position_code

    tail_state = tail_states.get(tail_number)
    if tail_state is None and not tail_states_supplied:
        tail_state = _tail_state_for_mission(mission)
    if not position and tail_state and tail_state.parking_position:
        position = tail_state.parking_position

    return {
        "has_tail": True,
        "tail_number": tail_number,
        "label": (position or "NOT PARKED"),
        "position": position,
        "is_assigned": bool(position),
        "is_oos": bool(tail_state and tail_state.is_out_of_service),
        "is_cancelled": _is_cancelled_mission(mission),
    }


def _tail_states_for_operation(operation):
    if not operation:
        return {}

    return {
        (state.tail_number or "").strip().upper(): state
        for state in SortDateTailState.query.filter_by(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
        ).all()
        if state.tail_number
    }


def _arrival_spare_candidate_rows(
    operation,
    parking_assignments=None,
    tail_states=None,
    all_missions=None,
):
    if not operation:
        return []

    if tail_states is None:
        tail_states = _tail_states_for_operation(operation)
    active_departure_tails = _active_departure_tails_for_operation(
        operation,
        missions=all_missions,
    )
    spare_tails = {
        tail_number
        for tail_number, tail_state in tail_states.items()
        if str(getattr(tail_state, "operational_status", "") or "").strip().lower()
        == "spare"
    }
    if all_missions is None:
        arrivals = (
            SortDateMission.query.filter(
                SortDateMission.sort_date_operation_id == operation.id,
                SortDateMission.mission_type == "arrival",
                SortDateMission.assigned_tail_number.isnot(None),
            )
            .order_by(
                SortDateMission.planned_datetime_utc.asc(),
                SortDateMission.id.asc(),
            )
            .all()
        )
    else:
        arrivals = sorted(
            (
                mission
                for mission in all_missions
                if mission.sort_date_operation_id == operation.id
                and mission.mission_type == "arrival"
                and mission.assigned_tail_number
            ),
            key=lambda mission: (
                mission.planned_datetime_utc or datetime.max,
                mission.id or 0,
            ),
        )
    rows = []
    for mission in arrivals:
        if _is_cancelled_mission(mission):
            continue
        tail_number = (mission.assigned_tail_number or "").strip().upper()
        if not tail_number or tail_number in active_departure_tails or tail_number in spare_tails:
            continue
        row = _arrival_row(
            mission,
            operation,
            parking_assignments=parking_assignments,
            include_parking_context=True,
            tail_states=tail_states,
        )
        row["aircraft_type"] = _aircraft_type_from_tail_state_or_number(
            tail_states.get(tail_number),
            tail_number,
        )
        rows.append(row)
    return rows


def _active_departure_tails_for_operation(operation, missions=None):
    if missions is None:
        missions = (
            SortDateMission.query.filter(
                SortDateMission.sort_date_operation_id == operation.id,
                SortDateMission.mission_type == "departure",
                SortDateMission.assigned_tail_number.isnot(None),
            )
            .all()
        )
    return {
        (mission.assigned_tail_number or "").strip().upper()
        for mission in missions
        if mission.sort_date_operation_id == operation.id
        and mission.mission_type == "departure"
        and mission.assigned_tail_number
        and not _is_cancelled_mission(mission)
    }


def _tail_state_for_mission(mission):
    tail_number = (mission.assigned_tail_number or "").strip().upper()
    if not tail_number:
        return None

    return SortDateTailState.query.filter_by(
        sort_date=mission.sort_date,
        gateway_code=mission.gateway_code,
        sort_name=mission.sort_name,
        tail_number=tail_number,
    ).first()
