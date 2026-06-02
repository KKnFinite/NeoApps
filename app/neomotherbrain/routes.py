from datetime import date, datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission, SortDateOperation
from app.neomotherbrain import bp
from app.services.flight_rules import is_mission_crew_covered
from app.services.sort_date_operations import (
    generate_sort_date_operation_from_master,
    mission_display_timing_data,
    normalize_window_minutes,
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

MISSION_TYPES = {"arrival", "departure"}


@bp.route("/")
def dashboard():
    return render_template("neomotherbrain/dashboard.html")


@bp.route("/motherbrain")
@login_required
def motherbrain():
    operation_count = SortDateOperation.query.count()
    master_schedule_count = MasterFlightSchedule.query.count()
    return render_template(
        "neomotherbrain/index.html",
        operation_count=operation_count,
        master_schedule_count=master_schedule_count,
    )


@bp.route("/motherbrain/operations")
@login_required
def operations():
    operations = (
        SortDateOperation.query.order_by(
            SortDateOperation.sort_date.desc(),
            SortDateOperation.generated_at_utc.desc(),
        )
        .all()
    )
    return render_template("neomotherbrain/operations.html", operations=operations)


@bp.route("/motherbrain/master-schedule")
@login_required
def master_schedule():
    schedules = (
        MasterFlightSchedule.query.order_by(
            MasterFlightSchedule.gateway_code.asc(),
            MasterFlightSchedule.sort_name.asc(),
            MasterFlightSchedule.mission_type.asc(),
            MasterFlightSchedule.flight_number.asc(),
        )
        .all()
    )
    return render_template(
        "neomotherbrain/master_schedule.html",
        schedules=schedules,
    )


@bp.route("/motherbrain/master-schedule/new", methods=["GET", "POST"])
@login_required
def new_master_schedule():
    form = _master_schedule_form_from_request()

    if request.method == "POST":
        master_schedule = MasterFlightSchedule()
        try:
            _apply_master_schedule_form(master_schedule, form)
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

    return _render_master_schedule_form(form, "new")


@bp.route("/motherbrain/master-schedule/<int:master_id>")
@login_required
def master_schedule_detail(master_id):
    master_schedule = _master_schedule_or_404(master_id)
    return render_template(
        "neomotherbrain/master_schedule_detail.html",
        master_schedule=master_schedule,
    )


@bp.route("/motherbrain/master-schedule/<int:master_id>/edit", methods=["GET", "POST"])
@login_required
def edit_master_schedule(master_id):
    master_schedule = _master_schedule_or_404(master_id)
    form = (
        _master_schedule_form_from_request()
        if request.method == "POST"
        else _master_schedule_form_from_model(master_schedule)
    )

    if request.method == "POST":
        try:
            _apply_master_schedule_form(master_schedule, form)
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

    return _render_master_schedule_form(form, "edit", master_schedule)


@bp.route("/motherbrain/master-schedule/<int:master_id>/toggle-active", methods=["POST"])
@login_required
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


@bp.route("/motherbrain/operations/new", methods=["GET", "POST"])
@login_required
def new_operation():
    form = {
        "sort_date": request.form.get("sort_date", ""),
        "gateway_code": request.form.get("gateway_code", "RFD"),
        "sort_name": request.form.get("sort_name", "night"),
    }

    if request.method == "POST":
        try:
            sort_date = date.fromisoformat(form["sort_date"])
        except ValueError:
            flash("Enter a valid sort date.", "error")
            return render_template("neomotherbrain/new_operation.html", form=form), 400

        gateway_code = form["gateway_code"].strip().upper()
        sort_name = form["sort_name"].strip().lower()
        if not gateway_code or not sort_name:
            flash("Gateway and sort name are required.", "error")
            return render_template("neomotherbrain/new_operation.html", form=form), 400

        try:
            operation = generate_sort_date_operation_from_master(
                sort_date=sort_date,
                gateway_code=gateway_code,
                sort_name=sort_name,
                generated_by_user_id=current_user.id,
            )
        except ValueError as error:
            existing_operation = SortDateOperation.query.filter_by(
                sort_date=sort_date,
                gateway_code=gateway_code,
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
            return render_template("neomotherbrain/new_operation.html", form=form), 400

        flash("Nightly operation generated.", "info")
        return redirect(
            url_for("neomotherbrain.operation_detail", operation_id=operation.id)
        )

    return render_template("neomotherbrain/new_operation.html", form=form)


@bp.route("/motherbrain/operations/<int:operation_id>")
@login_required
def operation_detail(operation_id):
    operation = _operation_or_404(operation_id)
    arrival_count = _mission_count(operation, "arrival")
    departure_count = _mission_count(operation, "departure")
    return render_template(
        "neomotherbrain/operation_detail.html",
        operation=operation,
        arrival_count=arrival_count,
        departure_count=departure_count,
        mission_count=arrival_count + departure_count,
    )


@bp.route("/motherbrain/operations/<int:operation_id>/arrivals")
@login_required
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
@login_required
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
@login_required
def update_operation_window(operation_id):
    operation = _operation_or_404(operation_id)

    try:
        operation.window_minutes = normalize_window_minutes(
            request.form.get("window_minutes", 0)
        )
    except (TypeError, ValueError):
        flash("Window minutes must be 0 or higher.", "error")
        return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))

    db.session.commit()
    flash("Operation window updated.", "info")
    return redirect(url_for("neomotherbrain.operation_detail", operation_id=operation.id))


def _operation_or_404(operation_id):
    return SortDateOperation.query.get_or_404(operation_id)


def _master_schedule_or_404(master_id):
    return MasterFlightSchedule.query.get_or_404(master_id)


def _render_master_schedule_form(form, mode, master_schedule=None):
    return render_template(
        "neomotherbrain/master_schedule_form.html",
        active_day_options=ACTIVE_DAY_OPTIONS,
        form=form,
        master_schedule=master_schedule,
        mode=mode,
    )


def _master_schedule_form_from_request():
    active_default = "1" if request.method != "POST" else "0"
    return {
        "gateway_code": request.form.get("gateway_code", "RFD"),
        "sort_name": request.form.get("sort_name", "night"),
        "mission_type": request.form.get("mission_type", "departure"),
        "flight_number": request.form.get("flight_number", ""),
        "origin": request.form.get("origin", ""),
        "destination": request.form.get("destination", ""),
        "active_days": set(request.form.getlist("active_days")),
        "planned_time_local": request.form.get("planned_time_local", ""),
        "timezone": request.form.get("timezone", "America/Chicago"),
        "preferred_parking": request.form.get("preferred_parking", ""),
        "pure_pull_time_local": request.form.get("pure_pull_time_local", ""),
        "first_mix_pull_time_local": request.form.get("first_mix_pull_time_local", ""),
        "final_mix_pull_time_local": request.form.get("final_mix_pull_time_local", ""),
        "active": request.form.get("active", active_default) == "1",
    }


def _master_schedule_form_from_model(master_schedule):
    return {
        "gateway_code": master_schedule.gateway_code,
        "sort_name": master_schedule.sort_name,
        "mission_type": master_schedule.mission_type,
        "flight_number": master_schedule.flight_number,
        "origin": master_schedule.origin,
        "destination": master_schedule.destination,
        "active_days": _active_days_set(master_schedule.active_days),
        "planned_time_local": _format_time(master_schedule.planned_time_local),
        "timezone": master_schedule.timezone,
        "preferred_parking": master_schedule.preferred_parking or "",
        "pure_pull_time_local": _format_time(master_schedule.pure_pull_time_local),
        "first_mix_pull_time_local": _format_time(master_schedule.first_mix_pull_time_local),
        "final_mix_pull_time_local": _format_time(master_schedule.final_mix_pull_time_local),
        "active": master_schedule.active,
    }


def _apply_master_schedule_form(master_schedule, form):
    gateway_code = form["gateway_code"].strip().upper()
    sort_name = form["sort_name"].strip().lower()
    mission_type = form["mission_type"].strip().lower()
    flight_number = form["flight_number"].strip()
    origin = form["origin"].strip().upper()
    destination = form["destination"].strip().upper()
    timezone = form["timezone"].strip() or "America/Chicago"

    if mission_type not in MISSION_TYPES:
        raise ValueError("Mission type must be arrival or departure.")

    if not all((gateway_code, sort_name, flight_number, origin, destination)):
        raise ValueError("Gateway, sort, flight, origin, and destination are required.")

    planned_time_local = _parse_time(form["planned_time_local"], "Planned time")

    master_schedule.gateway_code = gateway_code
    master_schedule.sort_name = sort_name
    master_schedule.mission_type = mission_type
    master_schedule.flight_number = flight_number
    master_schedule.origin = origin
    master_schedule.destination = destination
    master_schedule.active_days = _active_days_value(form["active_days"])
    master_schedule.planned_time_local = planned_time_local
    master_schedule.timezone = timezone
    master_schedule.preferred_parking = form["preferred_parking"].strip() or None
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

    duplicate_query = MasterFlightSchedule.query.filter_by(
        active=True,
        gateway_code=master_schedule.gateway_code,
        sort_name=master_schedule.sort_name,
        mission_type=master_schedule.mission_type,
        flight_number=master_schedule.flight_number,
    )

    if master_schedule.id:
        duplicate_query = duplicate_query.filter(MasterFlightSchedule.id != master_schedule.id)

    if duplicate_query.first():
        raise ValueError(
            "An active master schedule row already exists for this "
            "gateway, sort, mission type, and flight number."
        )


def _active_days_value(active_days):
    selected_days = set(active_days or ())
    return ",".join(day for day, _label in ACTIVE_DAY_OPTIONS if day in selected_days)


def _active_days_set(active_days):
    if not active_days:
        return set()

    return {day.strip().lower() for day in active_days.split(",") if day.strip()}


def _parse_time(value, label):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        raise ValueError(f"{label} must use HH:MM format.") from None


def _parse_optional_time(value, label):
    value = (value or "").strip()
    if not value:
        return None

    return _parse_time(value, label)


def _format_time(value):
    return value.strftime("%H:%M") if value else ""


def _missions_for_operation(operation, mission_type):
    return (
        SortDateMission.query.filter_by(
            sort_date_operation_id=operation.id,
            mission_type=mission_type,
        )
        .order_by(SortDateMission.planned_datetime_utc.asc())
        .all()
    )


def _mission_count(operation, mission_type):
    return SortDateMission.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
    ).count()


def _arrival_row(mission):
    return {
        "mission": mission,
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }


def _departure_row(mission, operation):
    return {
        "mission": mission,
        "timing": mission_display_timing_data(mission, operation),
        "crew_covered": is_mission_crew_covered(mission.crew_assignments),
    }
