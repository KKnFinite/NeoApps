from datetime import date, datetime
import re

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
)
from app.neomotherbrain import bp
from app.services.flight_rules import (
    crew_sections_for_tail_swap,
    default_required_crew_sections,
    derive_aircraft_type_from_tail_number,
    is_mission_crew_covered,
)
from app.services.access_control import (
    get_current_gateway,
    get_user_node_role,
    user_can_access_node,
    user_has_gateway_access,
)
from app.services.sort_date_operations import (
    ensure_tail_state_for_mission,
    generate_sort_date_operation_from_master,
    mission_display_timing_data,
    normalize_optional_window_minutes,
    normalize_wave,
    normalize_window_minutes,
    sync_sort_operation_with_master,
)
from app.services.gateway_matrix import (
    DAY_OPTIONS as MATRIX_DAY_OPTIONS,
    SORT_OPTIONS as MATRIX_SORT_OPTIONS,
    ensure_sort_operations_for_gateway_date,
    matrix_state_for_gateway,
    operations_for_gateway_date,
    save_gateway_matrix,
)
from app.services.night_sorting import master_schedule_sort_key, mission_board_sort_key
from app.services.sort_timeline import (
    DAY_OPTIONS as TIMELINE_DAY_OPTIONS,
    SORT_OPTIONS as TIMELINE_SORT_OPTIONS,
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
MASTER_AIRCRAFT_TYPE_OPTIONS = ("", "A300", "747", "757", "767", "Other")
FUEL_STATUSES = ("", "waiting", "received", "assigned", "complete")
ARRIVAL_STATUSES = (
    "",
    "scheduled",
    "en_route",
    "arrived",
    "unloaded",
)
DEPARTURE_STATUSES = (
    "",
    "loading",
    "last_uld_enroute",
    "ramp_load_complete",
    "crew_load_complete",
    "blocked_out",
)
MASTER_SCHEDULE_BLANK_ROW_INDEX = "__index__"


@bp.route("/")
def dashboard():
    return render_template("auth/login.html")


@bp.route("/rfd")
@login_required
def rfd_hub():
    gateway = get_current_gateway()
    if not user_has_gateway_access(current_user, gateway.code):
        return redirect(url_for("auth.access_pending"))

    return render_template(
        "neomotherbrain/rfd_hub.html",
        gateway=gateway,
        motherbrain_role=get_user_node_role(current_user, gateway.code, "motherbrain"),
        can_enter_motherbrain=user_can_access_node(
            current_user,
            gateway.code,
            "motherbrain",
            minimum_role="simulator",
        ),
        can_launch_sektor=user_can_access_node(current_user, gateway.code, "sektor"),
        can_launch_ermac=user_can_access_node(current_user, gateway.code, "ermac"),
    )


@bp.route("/rfd/sektor")
@gateway_node_required("sektor")
def sektor_launch():
    return redirect("https://neosektor.onrender.com/")


@bp.route("/motherbrain")
@gateway_node_required("motherbrain")
def motherbrain():
    gateway = get_current_gateway()
    generation_result = _auto_generate_today_sorts(gateway)
    sort_date = generation_result["sort_date"]
    current_sort_operations = operations_for_gateway_date(gateway, sort_date)
    operation_count = SortDateOperation.query.filter_by(gateway_code=gateway.code).count()
    master_schedule_count = MasterFlightSchedule.query.filter_by(
        gateway_code=gateway.code
    ).count()
    return render_template(
        "neomotherbrain/index.html",
        gateway=gateway,
        current_sort_operations=current_sort_operations,
        operation_count=operation_count,
        master_schedule_count=master_schedule_count,
        sort_date=sort_date,
    )


@bp.route("/motherbrain/gateway-matrix", methods=["GET", "POST"])
@gateway_node_required("motherbrain")
def gateway_matrix():
    gateway = get_current_gateway()
    if request.method == "POST":
        active_cells = []
        for day, _day_label in MATRIX_DAY_OPTIONS:
            for sort_name, _sort_label in MATRIX_SORT_OPTIONS:
                if request.form.get(f"{day}_{sort_name}") == "1":
                    active_cells.append((day, sort_name))

        save_gateway_matrix(gateway, active_cells)
        flash("Gateway Matrix updated.", "info")
        return redirect(url_for("neomotherbrain.gateway_matrix"))

    return render_template(
        "neomotherbrain/gateway_matrix.html",
        gateway=gateway,
        day_options=MATRIX_DAY_OPTIONS,
        sort_options=MATRIX_SORT_OPTIONS,
        matrix=matrix_state_for_gateway(gateway),
    )


@bp.route("/motherbrain/sort-timeline", methods=["GET", "POST"])
@gateway_node_required("motherbrain", minimum_role="grandmaster")
def sort_timeline():
    gateway = get_current_gateway()
    month_key = request.args.get("month", "")

    if request.method == "POST":
        _settings, month_key = save_sort_timeline_from_form(gateway, request.form)
        db.session.commit()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            context = sort_timeline_context(gateway, month_key)
            return jsonify(_sort_timeline_autosave_payload(context))
        flash("Sort Timeline settings saved.", "info")
        return redirect(url_for("neomotherbrain.sort_timeline", month=month_key))

    context = sort_timeline_context(gateway, month_key)
    return render_template(
        "neomotherbrain/sort_timeline.html",
        gateway=gateway,
        day_options=TIMELINE_DAY_OPTIONS,
        sort_options=TIMELINE_SORT_OPTIONS,
        format_timeline_time=format_timeline_time,
        **context,
    )


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
        "monthly_poll_limit": preview["monthly_poll_limit"],
        "units_used": preview["units_used"],
        "units_remaining": preview["units_remaining"],
        "polls_used": preview["polls_used"],
        "polls_remaining": preview["polls_remaining"],
        "operating_days": preview["operating_days"],
        "api_polling_days": preview["api_polling_days"],
        "original_daily_poll_cap": preview["original_daily_poll_cap"],
        "adjusted_daily_poll_cap": preview["adjusted_daily_poll_cap"],
        "effective_daily_poll_cap": preview["effective_daily_poll_cap"],
        "special_poll_count": preview["special_poll_count"],
        "auto_interval_poll_count": preview["auto_interval_poll_count"],
        "total_scheduled_polls": preview["total_scheduled_polls"],
    }


@bp.route("/motherbrain/manage-sort")
@gateway_node_required("motherbrain")
def manage_sort():
    gateway = get_current_gateway()
    generation_result = _auto_generate_today_sorts(gateway)
    sort_date = generation_result["sort_date"]
    operations = operations_for_gateway_date(gateway, sort_date)
    sync_results = [_sync_operation_with_master(operation) for operation in operations]
    if any(
        result["added"] or result["updated"]
        for result in sync_results
    ):
        db.session.commit()
        operations = operations_for_gateway_date(gateway, sort_date)
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
        created_count=len(generation_result["created"]),
        errors=generation_result["errors"],
    )


@bp.route("/motherbrain/operations")
@gateway_node_required("motherbrain")
def operations():
    gateway = get_current_gateway()
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
@gateway_node_required("motherbrain")
def master_schedule():
    gateway = get_current_gateway()
    schedules = _master_schedules_for_gateway(gateway)
    if request.method == "POST":
        mission_type = request.form.get("board_mission_type", "").strip().lower()
        rows = _master_schedule_bulk_rows_from_request(gateway)
        try:
            updated_count, created_count = _apply_master_schedule_board_edit(
                rows,
                schedules,
                gateway,
                mission_type,
            )
        except ValueError as error:
            db.session.rollback()
            if _wants_json_response():
                return {"ok": False, "message": str(error)}, 400
            flash(str(error), "error")
            return redirect(url_for("neomotherbrain.master_schedule"))

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
        return redirect(url_for("neomotherbrain.master_schedule"))

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
        wave_options=WAVE_OPTIONS,
    )


@bp.route("/motherbrain/master-schedule/new", methods=["GET", "POST"])
@gateway_node_required("motherbrain")
def new_master_schedule():
    gateway = get_current_gateway()
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
@gateway_node_required("motherbrain")
def bulk_edit_master_schedule():
    gateway = get_current_gateway()
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
@gateway_node_required("motherbrain")
def master_schedule_detail(master_id):
    master_schedule = _master_schedule_or_404(master_id)
    return render_template(
        "neomotherbrain/master_schedule_detail.html",
        master_schedule=master_schedule,
    )


@bp.route("/motherbrain/master-schedule/<int:master_id>/edit", methods=["GET", "POST"])
@gateway_node_required("motherbrain")
def edit_master_schedule(master_id):
    gateway = get_current_gateway()
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
@gateway_node_required("motherbrain")
def toggle_master_schedule_active(master_id):
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
@gateway_node_required("motherbrain")
def delete_master_schedule(master_id):
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
@gateway_node_required("motherbrain")
def new_operation():
    gateway = get_current_gateway()
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
@gateway_node_required("motherbrain")
def operation_detail(operation_id):
    operation = _operation_or_404(operation_id)
    sync_result = _sync_operation_with_master(operation)
    if sync_result["added"] or sync_result["updated"]:
        db.session.commit()
        operation = _operation_or_404(operation_id)
    arrival_count = _mission_count(operation, "arrival")
    departure_count = _mission_count(operation, "departure")
    rows = [_mission_list_row(mission, operation) for mission in _all_missions_for_operation(operation)]
    return render_template(
        "neomotherbrain/operation_detail.html",
        operation=operation,
        arrival_count=arrival_count,
        departure_count=departure_count,
        mission_count=arrival_count + departure_count,
        rows=rows,
    )


@bp.route("/motherbrain/operations/<int:operation_id>/arrivals")
@gateway_node_required("motherbrain")
def arrival_board(operation_id):
    operation = _operation_or_404(operation_id)
    missions = _missions_for_operation(operation, "arrival")
    rows = [_arrival_row(mission) for mission in missions]
    return render_template(
        "neomotherbrain/arrival_board.html",
        operation=operation,
        rows=rows,
    )


@bp.route("/motherbrain/operations/<int:operation_id>/departures")
@gateway_node_required("motherbrain")
def departure_board(operation_id):
    operation = _operation_or_404(operation_id)
    missions = _missions_for_operation(operation, "departure")
    rows = [_departure_row(mission, operation) for mission in missions]
    return render_template(
        "neomotherbrain/departure_board.html",
        operation=operation,
        rows=rows,
    )


@bp.route("/motherbrain/operations/<int:operation_id>/window", methods=["POST"])
@gateway_node_required("motherbrain")
def update_operation_window(operation_id):
    operation = _operation_or_404(operation_id)

    try:
        operation.window_minutes = normalize_window_minutes(
            request.form.get("window_minutes", 0)
        )
        operation.first_wave_window_minutes = normalize_optional_window_minutes(
            request.form.get("first_wave_window_minutes", "")
        )
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
@gateway_node_required("motherbrain")
def new_mission(operation_id):
    operation = _operation_or_404(operation_id)
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
@gateway_node_required("motherbrain")
def mission_detail(operation_id, mission_id):
    operation = _operation_or_404(operation_id)
    mission = _mission_or_404(operation, mission_id)
    return render_template(
        "neomotherbrain/mission_detail.html",
        operation=operation,
        mission=mission,
        timing=mission_display_timing_data(mission, operation),
        crew_covered=is_mission_crew_covered(mission.crew_assignments),
    )


@bp.route(
    "/motherbrain/operations/<int:operation_id>/missions/<int:mission_id>/edit",
    methods=["GET", "POST"],
)
@gateway_node_required("motherbrain")
def edit_mission(operation_id, mission_id):
    operation = _operation_or_404(operation_id)
    mission = _mission_or_404(operation, mission_id)
    form = (
        _mission_form_from_request(operation)
        if request.method == "POST"
        else _mission_form_from_model(mission)
    )

    if request.method == "POST":
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
@gateway_node_required("motherbrain")
def delete_mission(operation_id, mission_id):
    operation = _operation_or_404(operation_id)
    mission = _mission_or_404(operation, mission_id)

    SortDateCrewAssignment.query.filter_by(sort_date_mission_id=mission.id).delete()
    db.session.delete(mission)
    db.session.commit()
    flash("Mission deleted.", "info")
    return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))


def _operation_or_404(operation_id):
    gateway = get_current_gateway()
    return SortDateOperation.query.filter_by(
        id=operation_id,
        gateway_code=gateway.code,
    ).first_or_404()


def _render_new_operation_form(form):
    return render_template(
        "neomotherbrain/new_operation.html",
        form=form,
        sort_name_options=SORT_NAME_OPTIONS,
    )


def _auto_generate_today_sorts(gateway):
    result = ensure_sort_operations_for_gateway_date(
        gateway,
        generated_by_user_id=current_user.id,
    )
    for error in result["errors"]:
        current_app.logger.warning("Gateway Matrix generation skipped: %s", error)
    return result


def _sync_operation_with_master(operation):
    result = sync_sort_operation_with_master(operation)
    for master_row in result["skipped"]:
        current_app.logger.warning(
            "Master schedule sync skipped duplicate flight %s for %s %s %s",
            master_row.flight_number,
            operation.gateway_code,
            operation.sort_date,
            operation.sort_name,
        )
    return result


def _mission_or_404(operation, mission_id):
    return SortDateMission.query.filter_by(
        id=mission_id,
        sort_date_operation_id=operation.id,
    ).first_or_404()


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
        wave_options=WAVE_OPTIONS,
    )


def _master_schedule_form_from_request(gateway=None, prefix="", source=None):
    source = source or request.form
    active_default = "1" if request.method != "POST" else "0"
    gateway_code = gateway.code if gateway else source.get(f"{prefix}gateway_code", "RFD")
    form = {
        "gateway_code": gateway_code,
        "sort_name": source.get(f"{prefix}sort_name", "night"),
        "mission_type": source.get(f"{prefix}mission_type", "departure"),
        "wave": source.get(f"{prefix}wave", "1"),
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
        "first_mix_pull_time_local": _time_value_from_form(
            source,
            f"{prefix}first_mix_pull_time_local",
        ),
        "final_mix_pull_time_local": _time_value_from_form(
            source,
            f"{prefix}final_mix_pull_time_local",
        ),
        "active": source.get(f"{prefix}active", active_default) == "1",
    }
    _apply_gateway_airport_defaults(form, gateway)
    if form["mission_type"] == "arrival":
        form["pure_pull_time_local"] = ""
        form["first_mix_pull_time_local"] = ""
        form["final_mix_pull_time_local"] = ""
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
        form["first_mix_pull_time_local"] = ""
        form["final_mix_pull_time_local"] = ""
    return form


def _master_schedule_form_from_model(master_schedule):
    return {
        "gateway_code": master_schedule.gateway_code,
        "sort_name": master_schedule.sort_name,
        "mission_type": master_schedule.mission_type,
        "wave": normalize_wave(master_schedule.wave),
        "flight_number": master_schedule.flight_number,
        "aircraft_type": master_schedule.aircraft_type or "",
        "origin": master_schedule.origin,
        "destination": master_schedule.destination,
        "active_days": _active_days_set(master_schedule.active_days),
        "planned_time_local": _format_time(master_schedule.planned_time_local),
        "timezone": master_schedule.timezone,
        "pure_pull_time_local": _format_time(master_schedule.pure_pull_time_local),
        "first_mix_pull_time_local": _format_time(master_schedule.first_mix_pull_time_local),
        "final_mix_pull_time_local": _format_time(master_schedule.final_mix_pull_time_local),
        "active": master_schedule.active,
    }


def _blank_master_schedule_form(gateway=None):
    gateway_code = gateway.code if gateway else "RFD"
    form = {
        "gateway_code": gateway_code,
        "sort_name": "night",
        "mission_type": "departure",
        "wave": "1",
        "flight_number": "",
        "aircraft_type": "",
        "origin": "",
        "destination": "",
        "active_days": set(),
        "planned_time_local": "",
        "timezone": _gateway_timezone(gateway),
        "pure_pull_time_local": "",
        "first_mix_pull_time_local": "",
        "final_mix_pull_time_local": "",
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
            (row.get("first_mix_pull_time_local") or "").strip(),
            (row.get("final_mix_pull_time_local") or "").strip(),
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


def _apply_master_schedule_board_edit(rows, schedules, gateway, mission_type):
    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")

    schedules_by_id = {
        str(schedule.id): schedule
        for schedule in schedules
        if schedule.mission_type == mission_type
    }
    processed_schedules = []
    created_schedules = []

    for row in rows:
        row["mission_type"] = mission_type
        schedule_id = row.get("id", "").strip()
        if not schedule_id:
            if not _master_schedule_board_row_has_data(row):
                continue
            if not _master_schedule_board_row_is_complete(row):
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
                (row.get("first_mix_pull_time_local") or "").strip(),
                (row.get("final_mix_pull_time_local") or "").strip(),
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
        and time_field_is_complete("pure_pull_time_local")
        and time_field_is_complete("first_mix_pull_time_local")
        and time_field_is_complete("final_mix_pull_time_local")
    )


def _apply_master_schedule_form(master_schedule, form, gateway=None):
    gateway_code = gateway.code if gateway else form["gateway_code"].strip().upper()
    sort_name = form["sort_name"].strip().lower()
    mission_type = form["mission_type"].strip().lower()
    wave = normalize_wave(form.get("wave"))
    flight_number = _normalize_flight_number(form["flight_number"])
    aircraft_type = _normalize_master_aircraft_type(form.get("aircraft_type", ""))
    origin = _normalize_airport_code(form["origin"], "Origin")
    destination = _normalize_airport_code(form["destination"], "Destination")
    timezone = _gateway_timezone(gateway)

    if sort_name not in SORT_NAMES:
        raise ValueError("Sort name must be Night, Twilight, Day, or Sunrise.")
    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")
    if wave not in WAVES:
        raise ValueError("Wave must be 1 or 2.")

    if not all((gateway_code, sort_name, flight_number, origin, destination)):
        raise ValueError("Gateway, sort, flight, origin, and destination are required.")

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
        master_schedule.first_mix_pull_time_local = None
        master_schedule.final_mix_pull_time_local = None
        return

    master_schedule.pure_pull_time_local = _parse_optional_time(
        form["pure_pull_time_local"],
        "Pure pull time",
    )
    master_schedule.first_mix_pull_time_local = _parse_optional_time(
        form["first_mix_pull_time_local"],
        "First mix pull time",
    )
    master_schedule.final_mix_pull_time_local = _parse_optional_time(
        form["final_mix_pull_time_local"],
        "Final mix pull time",
    )


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


def _render_mission_form(operation, form, mode, mission=None):
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
    )


def _mission_form_from_request(operation):
    return {
        "mission_type": request.form.get("mission_type", "departure"),
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
        "first_mix_pull_time_local": _time_value_from_form(
            request.form,
            "first_mix_pull_time_local",
        ),
        "final_mix_pull_time_local": _time_value_from_form(
            request.form,
            "final_mix_pull_time_local",
        ),
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
        "first_mix_pull_time_local": _format_time(mission.first_mix_pull_time_local),
        "final_mix_pull_time_local": _format_time(mission.final_mix_pull_time_local),
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
    planned_datetime_local = datetime.combine(operation.sort_date, planned_time_local)

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
        mission.first_mix_pull_time_local = None
        mission.final_mix_pull_time_local = None
        mission.pull_time_source = None
        mission.departure_status = None
        return

    mission.arrival_status = None
    mission.departure_status = _choice_or_none(
        form["departure_status"],
        DEPARTURE_STATUSES,
        "Departure status",
    )
    mission.pure_pull_time_local = _parse_optional_time(
        form["pure_pull_time_local"],
        "Pure pull time",
    )
    mission.first_mix_pull_time_local = _parse_optional_time(
        form["first_mix_pull_time_local"],
        "First mix pull time",
    )
    mission.final_mix_pull_time_local = _parse_optional_time(
        form["final_mix_pull_time_local"],
        "Final mix pull time",
    )
    if any(
        (
            mission.pure_pull_time_local,
            mission.first_mix_pull_time_local,
            mission.final_mix_pull_time_local,
        )
    ):
        mission.pull_time_source = "manual"
    else:
        mission.pull_time_source = None


def _raise_for_duplicate_operation_flight_number(operation, mission):
    with db.session.no_autoflush:
        duplicate_query = SortDateMission.query.filter(
            SortDateMission.sort_date_operation_id == operation.id,
            func.upper(SortDateMission.flight_number) == mission.flight_number.upper(),
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


def _missions_for_operation(operation, mission_type):
    missions = (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        .order_by(SortDateMission.planned_datetime_utc.asc())
        .all()
    )
    return sorted(missions, key=mission_board_sort_key)


def _mission_count(operation, mission_type):
    return SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
    ).count()


def _arrival_row(mission):
    tail_state = _tail_state_for_mission(mission)
    return {
        "mission": mission,
        "parking_position": tail_state.parking_position if tail_state else None,
        "eta_time": mission.eta_datetime_utc or mission.planned_datetime_local,
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }


def _departure_row(mission, operation):
    tail_state = _tail_state_for_mission(mission)
    return {
        "mission": mission,
        "timing": mission_display_timing_data(mission, operation),
        "parking_position": tail_state.parking_position if tail_state else None,
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }


def _mission_list_row(mission, operation):
    tail_state = _tail_state_for_mission(mission)
    timing = mission_display_timing_data(mission, operation)
    if mission.mission_type == "arrival":
        display_time = mission.eta_datetime_utc or mission.planned_datetime_local
    else:
        display_time = timing.get("adjusted_planned_departure_time")

    return {
        "mission": mission,
        "timing": timing,
        "parking_position": tail_state.parking_position if tail_state else None,
        "display_time": display_time,
        "status": (
            mission.arrival_status
            if mission.mission_type == "arrival"
            else mission.departure_status
        ),
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
