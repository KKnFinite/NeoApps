from datetime import date

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
