from functools import wraps

from flask import (
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    PermissionRule,
    StaffingGroup,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    StaffingVacationUnionCalendar,
)
from app.neostaffing import bp
from app.services.access_control import get_user_app_role, user_can_access_app, user_has_app_access
from app.services import neostaffing as staffing_service
from app.services import neostaffing_attendance_history as attendance_history_service
from app.services import neostaffing_change_requests as change_request_service
from app.services import neostaffing_bulk_change as bulk_change_service
from app.services import neostaffing_management_review as management_review_service
from app.services import neostaffing_notifications as notification_service
from app.services import neostaffing_vacation as vacation_service
from app.services import neostaffing_vacation_reports as vacation_report_service
from app.services.permission_rules import ensure_default_permission_rules, user_can


BOARD_VIEW_PERMISSION = "neostaffing.board.view"
SENIORITY_VIEW_PERMISSION = "neostaffing.seniority.view"
PEOPLE_VIEW_PERMISSION = "neostaffing.people.view"
PEOPLE_EDIT_PERMISSION = "neostaffing.people.edit"
PEOPLE_BULK_ACTIONS_PERMISSION = "neostaffing.people.bulk_actions"
ATTENDANCE_TAKE_PERMISSION = "neostaffing.attendance.take"
STAFFING_GROUPS_VIEW_PERMISSION = "neostaffing.staffing_groups.view"
STAFFING_GROUPS_EDIT_PERMISSION = "neostaffing.staffing_groups.edit"
ORG_CHART_VIEW_PERMISSION = "neostaffing.org_chart.view"
ORG_CHART_EDIT_STRUCTURE_PERMISSION = "neostaffing.org_chart.edit_structure"
REPORTS_VIEW_PERMISSION = "neostaffing.reports.view"
VACATION_SELECTION_VIEW_PERMISSION = "neostaffing.vacation_selection.view"
MANAGEMENT_ASSIGN_PERMISSION = "neostaffing.management.assign"
CHANGE_REQUEST_VIEW_PERMISSION = "neostaffing.change_requests.view"
CHANGE_REQUEST_SUBMIT_PERMISSION = "neostaffing.change_requests.submit"
CHANGE_REQUEST_APPROVE_PERMISSION = "neostaffing.change_requests.approve"
BULK_CHANGE_PERMISSION = "neostaffing.bulk_change.use"
HIERARCHY_VIEW_PERMISSION = "neostaffing.hierarchy.view"
PLANNED_STAFFING_EDIT_PERMISSION = "neostaffing.planned_staffing.edit"
PERMISSIONS_VIEW_PERMISSION = "neostaffing.permissions.view"
PERMISSIONS_EDIT_PERMISSION = "neostaffing.permissions.edit"
ROLE_CHOICES = ("watcher", "operator", "simulator", "master", "grandmaster")

NEOSTAFFING_PERMISSION_LABELS = {
    "neostaffing.board.view": "View Board",
    "neostaffing.seniority.view": "View Seniority",
    "neostaffing.people.view": "View People",
    "neostaffing.people.edit": "Edit People",
    "neostaffing.people.bulk_actions": "People Bulk Actions",
    "neostaffing.attendance.take": "Take Attendance",
    "neostaffing.staffing_groups.view": "View Staffing Groups",
    "neostaffing.staffing_groups.edit": "Edit Staffing Groups",
    "neostaffing.org_chart.view": "View Org Chart",
    "neostaffing.org_chart.edit_structure": "Edit Org Chart Structure",
    "neostaffing.reports.view": "View Reports",
    "neostaffing.vacation_selection.view": "View Vacation Selection",
    "neostaffing.permissions.view": "View Permissions",
    "neostaffing.permissions.edit": "Edit Permissions",
    "neostaffing.management.assign": "Assign Management",
    "neostaffing.app_management.view": "View App Management",
    "neostaffing.hierarchy.view": "View Hierarchy",
    "neostaffing.hierarchy.edit": "Edit Hierarchy",
    "neostaffing.planned_staffing.view": "View Planned Staffing",
    "neostaffing.planned_staffing.edit": "Edit Planned Staffing",
    "neostaffing.people_management.view": "View People Management",
    "neostaffing.people_management.edit": "Edit People Management",
    "neostaffing.work_assignments.view": "View Work Assignments",
    "neostaffing.work_assignments.edit": "Edit Work Assignments",
    "neostaffing.management_assignments.view": "View Management Assignments",
    "neostaffing.management_assignments.edit": "Edit Management Assignments",
    "neostaffing.change_requests.view": "View Change Requests",
    "neostaffing.change_requests.submit": "Submit Change Requests",
    "neostaffing.change_requests.approve": "Approve Change Requests",
    "neostaffing.bulk_change.use": "Use Bulk Change",
}


@bp.context_processor
def inject_neostaffing_navigation():
    return {
        "neostaffing_nav": notification_service.notification_navigation_state(
            current_user
        ),
        "neostaffing_settings_visible": user_can_access_app(
            current_user, "neostaffing", minimum_role="master"
        ),
    }


def neostaffing_app_required(minimum_role="watcher", permission_key=None):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if permission_key:
                if user_has_app_access(current_user, "neostaffing") and user_can(permission_key):
                    return view_func(*args, **kwargs)

                if user_has_app_access(current_user, "neostaffing"):
                    flash("NeoStaffing permission denied.", "error")
                    if request.endpoint == "neostaffing.index":
                        return redirect(url_for("auth.portal_dashboard"))
                    return redirect(url_for("neostaffing.index"))

                flash("Request NeoStaffing access from the NeoApps Portal.", "error")
                return redirect(url_for("auth.portal_dashboard"))

            if user_can_access_app(current_user, "neostaffing", minimum_role=minimum_role):
                return view_func(*args, **kwargs)

            if user_has_app_access(current_user, "neostaffing"):
                flash("NeoStaffing App Management requires Master access.", "error")
                return redirect(url_for("neostaffing.index"))

            flash("Request NeoStaffing access from the NeoApps Portal.", "error")
            return redirect(url_for("auth.portal_dashboard"))

        return wrapped_view

    return decorator


@bp.route("")
@neostaffing_app_required(permission_key=BOARD_VIEW_PERMISSION)
def index():
    role = get_user_app_role(current_user, "neostaffing")
    can_manage = user_can_access_app(current_user, "neostaffing", minimum_role="master")
    return render_template(
        "neostaffing/index.html",
        app_role=role,
        can_manage_app=can_manage,
        attendance_shortcut=staffing_service.management_attendance_context_for_user(current_user),
        landing=staffing_service.landing_context(),
    )


@bp.route("/")
@login_required
def index_slash():
    return redirect(url_for("neostaffing.index"))


@bp.route("/seniority")
@neostaffing_app_required(permission_key=SENIORITY_VIEW_PERMISSION)
def seniority():
    report_filters = {"report_type": "seniority"}
    for key in (
        "sort_id",
        "operation_id",
        "department_id",
        "work_area_id",
        "search",
        "include_management",
    ):
        value = request.args.get(key, "").strip()
        if value:
            report_filters[key] = value
    classification = request.args.get("classification", "").strip()
    if classification in {choice[0] for choice in staffing_service.classification_choices()}:
        report_filters["classification"] = classification
    active = request.args.get("active", "active").strip() or "active"
    if active in {"inactive", "all"}:
        report_filters["active"] = active
    return redirect(url_for("neostaffing.reports", **report_filters))


@bp.route("/people")
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def people():
    can_manage = user_can_access_app(current_user, "neostaffing", minimum_role="master")
    can_edit_people = user_can(PEOPLE_EDIT_PERMISSION)
    can_bulk_people = user_can(PEOPLE_BULK_ACTIONS_PERMISSION)
    classification = request.args.get("classification", "").strip()
    if classification not in {choice[0] for choice in staffing_service.classification_choices()}:
        classification = ""
    active = request.args.get("active", "active").strip() or "active"
    if active not in {"active", "inactive", "all"}:
        active = "active"
    employee_status = request.args.get("employee_status", "").strip()
    if employee_status not in {choice[0] for choice in staffing_service.employee_status_choices()}:
        employee_status = ""
    context = staffing_service.people_context(
        {
            "sort_id": request.args.get("sort_id", "").strip(),
            "operation_id": request.args.get("operation_id", "").strip(),
            "department_id": request.args.get("department_id", "").strip(),
            "work_area_id": request.args.get("work_area_id", "").strip(),
            "classification": classification,
            "employee_status": employee_status,
            "active": active,
            "assignment_status": request.args.get("assignment_status", "").strip(),
            "page": request.args.get("page", "").strip(),
            "per_page": request.args.get("per_page", "").strip(),
            "leadership_only": request.args.get("leadership_only", "").strip(),
            "search": request.args.get("search", "").strip(),
            "person_id": request.args.get("person_id", "").strip(),
        },
        current_user if not can_manage else None,
      )
    shift_flow_areas = staffing_service.shift_flow_area_options(context.get("selected_work_area")) if can_edit_people else []
    creation_context = staffing_service.people_creation_context(context.get("selected_unit")) if can_edit_people else None
    all_classification_choices = staffing_service.classification_choices()
    return render_template(
        "neostaffing/people.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=can_manage,
        can_edit_people=can_edit_people,
        can_bulk_people=can_bulk_people,
        shift_flow_areas=shift_flow_areas,
        creation_context=creation_context,
        classification_choices=all_classification_choices,
        management_classification_choices=[
            choice
            for choice in all_classification_choices
            if choice[0] in staffing_service.MANAGEMENT_CLASSIFICATIONS
        ],
        employee_classification_choices=[
            choice
            for choice in all_classification_choices
            if choice[0] in staffing_service.WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS
        ],
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        shift_work_area_type=staffing_service.shift_work_area_type,
        employee_status_choices=staffing_service.employee_status_choices(),
        employee_status_labels=staffing_service.EMPLOYEE_STATUS_LABELS,
        leadership_level_labels=staffing_service.LEADERSHIP_LEVEL_LABELS,
        unit_type_labels=staffing_service.UNIT_TYPE_LABELS,
        work_areas=staffing_service.work_area_units(),
        unit_path=staffing_service.unit_path,
        people=context,
    )


@bp.route("/attendance", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def attendance():
    return _handle_attendance()


@bp.route("/people/attendance", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def people_attendance():
    if request.method == "GET":
        return redirect(url_for("neostaffing.attendance", **request.args))
    return _handle_attendance()


@bp.route("/staffing-groups", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=STAFFING_GROUPS_VIEW_PERMISSION)
def staffing_groups():
    can_edit = user_can(STAFFING_GROUPS_EDIT_PERMISSION)
    if request.method == "POST":
        if not can_edit:
            flash("You do not currently have Edit Staffing Groups permission.", "error")
            return redirect(url_for("neostaffing.staffing_groups"))
        try:
            action = request.form.get("action", "").strip().lower()
            if action == "create":
                staffing_service.create_staffing_group(request.form)
                success_message = "Staffing Group created."
            elif action == "update":
                group = db.session.get(StaffingGroup, request.form.get("group_id", type=int))
                staffing_service.update_staffing_group(group, request.form)
                success_message = "Staffing Group updated."
            else:
                raise ValueError("Choose a valid Staffing Group action.")
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(getattr(error, "orig", None) or error), "error")
        else:
            flash(success_message, "success")
        return redirect(url_for("neostaffing.staffing_groups"))

    return render_template(
        "neostaffing/staffing_groups.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=user_can_access_app(
            current_user,
            "neostaffing",
            minimum_role="master",
        ),
        can_edit_staffing_groups=can_edit,
        staffing_groups=staffing_service.staffing_groups_context(),
    )


@bp.route("/shift-flow")
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def shift_flow():
    context = staffing_service.shift_flow_context(
        request.args.get("phase", "final_door"), request.args.get("side", "east")
    )
    selected_person_id = request.args.get("person_id", type=int)
    selected = next(
        (row for row in context["rows"] if row["person"].id == selected_person_id), None
    )
    selected_area = selected["assignment"].work_area if selected else None
    return render_template(
        "neostaffing/shift_flow.html",
        shift_flow=context,
        selected=selected,
        shift_flow_areas=staffing_service.shift_flow_area_options(selected_area),
        shift_work_area_type=staffing_service.shift_work_area_type,
        can_edit_shift_flow=user_can(PEOPLE_EDIT_PERMISSION),
    )


@bp.route("/shift-flow/<int:person_id>", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def save_shift_flow(person_id):
    phase = request.form.get("phase", "final_door")
    try:
        person = _get_person(person_id)
        assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).first()
        staffing_service.save_shift_flow_plan(person, request.form, assignment.work_area if assignment else None)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Shift Flow plan saved.", "success")
    return redirect(url_for("neostaffing.shift_flow", phase=phase, person_id=person_id))


@bp.route("/shift-flow/<int:person_id>/final-door", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def move_shift_flow_final_door(person_id):
    """Persist one FINAL DOOR drag without touching the rest of the plan."""
    payload = request.get_json(silent=True) or request.form
    try:
        person = _get_person(person_id)
        assignment = StaffingWorkAssignment.query.filter_by(
            person_id=person.id, active=True
        ).first()
        result = staffing_service.move_shift_flow_final_door(
            person,
            payload.get("final_door_work_area_id"),
            assignment.work_area if assignment else None,
            payload.get("expected_version"),
        )
        if result.get("conflict"):
            db.session.rollback()
            return jsonify({"ok": False, "conflict": result["conflict"]}), 409
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(getattr(error, "orig", None) or error)}), 400

    plan = result["plan"]
    return jsonify(
        {
            "ok": True,
            "changed": result["changed"],
            "person_id": person.id,
            "final_door_work_area_id": plan.final_door_work_area_id,
            "plan_version": result["version"],
            "shorthand": staffing_service.shift_flow_shorthand(plan),
        }
    )


@bp.route("/shift-flow/<int:person_id>/lane", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def move_shift_flow_lane(person_id):
    payload = request.get_json(silent=True) or request.form
    try:
        person = _get_person(person_id)
        assignment = StaffingWorkAssignment.query.filter_by(
            person_id=person.id, active=True
        ).first()
        result = staffing_service.move_shift_flow_phase_lane(
            person,
            payload.get("phase"),
            payload.get("destination_id"),
            assignment.work_area if assignment else None,
            payload.get("expected_version"),
            payload.get("ballmat_transition"),
        )
        if result.get("conflict"):
            db.session.rollback()
            return jsonify({"ok": False, "conflict": result["conflict"]}), 409
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(getattr(error, "orig", None) or error)}), 400

    plan = result["plan"]
    return jsonify(
        {
            "ok": True,
            "changed": result["changed"],
            "person_id": person.id,
            "plan_version": result["version"],
            "shorthand": staffing_service.shift_flow_shorthand(plan),
        }
    )


@bp.route("/shift-flow/<int:person_id>/final-composite", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def move_shift_flow_final_composite(person_id):
    payload = request.get_json(silent=True) or request.form
    try:
        person = _get_person(person_id)
        assignment = StaffingWorkAssignment.query.filter_by(
            person_id=person.id, active=True
        ).first()
        result = staffing_service.move_shift_flow_final_composite(
            person,
            payload.get("final_door_id"),
            payload.get("band"),
            assignment.work_area if assignment else None,
            payload.get("expected_version"),
        )
        if result.get("conflict"):
            db.session.rollback()
            return jsonify({"ok": False, "conflict": result["conflict"]}), 409
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        return jsonify({"ok": False, "error": str(getattr(error, "orig", None) or error)}), 400
    plan = result["plan"]
    return jsonify({
        "ok": True, "changed": result["changed"], "person_id": person.id,
        "created": result.get("created", False),
        "plan_version": result["version"],
        "final_door_work_area_id": plan.final_door_work_area_id,
        "sort_start_work_area_id": plan.sort_start_work_area_id,
        "ballmat_transition": plan.ballmat_transition,
        "setup_assignment": staffing_service.shift_flow_setup_assignment_label(plan),
        "shorthand": staffing_service.shift_flow_shorthand(plan),
    })


@bp.route("/requests")
@neostaffing_app_required(permission_key=CHANGE_REQUEST_VIEW_PERMISSION)
def change_requests():
    maintenance = _maintain_change_request_activity()
    if maintenance["changed"]:
        db.session.commit()
    context = change_request_service.change_request_context(
        {
            "view": request.args.get("view", "").strip(),
            "queue": request.args.get("queue", "").strip(),
            "search": request.args.get("search", "").strip(),
            "person_id": request.args.get("person_id", "").strip(),
        },
        current_user,
    )
    return render_template(
        "neostaffing/change_requests.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        requests_context=context,
    )


@bp.route("/notifications")
@neostaffing_app_required(permission_key=CHANGE_REQUEST_VIEW_PERMISSION)
def staffing_notifications():
    maintenance = _maintain_change_request_activity()
    if maintenance["changed"]:
        db.session.commit()
    return render_template(
        "neostaffing/notifications.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        notifications_context=notification_service.notification_context(
            current_user
        ),
    )


@bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_VIEW_PERMISSION)
def read_staffing_notification(notification_id):
    try:
        maintenance = _maintain_change_request_activity()
        _notification, changed = notification_service.mark_notification_read(
            notification_id,
            current_user,
        )
        if maintenance["changed"] or changed:
            db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("neostaffing.staffing_notifications"))


@bp.route("/notifications/<int:notification_id>/open", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_VIEW_PERMISSION)
def open_staffing_notification(notification_id):
    try:
        maintenance = _maintain_change_request_activity()
        notification, changed = notification_service.mark_notification_read(
            notification_id,
            current_user,
        )
        if maintenance["changed"] or changed:
            db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
        return redirect(url_for("neostaffing.staffing_notifications"))
    return redirect(
        url_for(
            "neostaffing.change_requests",
            view="all",
            queue="all",
            search=str(notification.change_request_id),
        )
    )


@bp.route("/bulk-change", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=BULK_CHANGE_PERMISSION)
def bulk_change():
    workspace = bulk_change_service.new_workspace(current_user)
    token_valid = True
    if request.method == "POST":
        try:
            workspace = bulk_change_service.decode_workspace(
                request.form.get("workspace_token"),
                current_user,
            )
        except ValueError as error:
            flash(str(error), "error")
            workspace = bulk_change_service.new_workspace(current_user)
            token_valid = False

        action = request.form.get("action", "").strip() if token_valid else ""
        if action == "cancel":
            flash("Bulk Change workspace discarded.", "success")
            return redirect(url_for("neostaffing.bulk_change"))
        if action == "apply":
            try:
                result = bulk_change_service.apply_workspace(workspace, current_user)
                db.session.commit()
            except (ValueError, IntegrityError) as error:
                db.session.rollback()
                flash(str(getattr(error, "orig", None) or error), "error")
            else:
                flash(
                    "Applied the complete Bulk Change package in one transaction "
                    f"({result['people']} people updated).",
                    "success",
                )
                return redirect(url_for("neostaffing.bulk_change"))
        elif action == "submit":
            try:
                result = bulk_change_service.submit_workspace(workspace, current_user)
                if result["requests"]:
                    db.session.commit()
            except (ValueError, IntegrityError) as error:
                db.session.rollback()
                flash(str(getattr(error, "orig", None) or error), "error")
            else:
                if result["requests"]:
                    flash(
                        f"Submitted {len(result['requests'])} employee request(s) for approval.",
                        "success",
                    )
                for blocked in result["blocked"]:
                    flash(blocked["reason"], "warning")
                if result["unsupported"]:
                    flash(
                        "Unsupported management or structural items remain staged and were not applied.",
                        "warning",
                    )
        elif action:
            try:
                bulk_change_service.stage_workspace_change(
                    workspace,
                    action,
                    request.form,
                    current_user,
                )
            except ValueError as error:
                flash(str(error), "error")
            else:
                flash("Bulk Change workspace updated. Nothing is live yet.", "success")

    context = bulk_change_service.bulk_change_context(workspace, current_user)
    return render_template(
        "neostaffing/bulk_change.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        bulk_change=context,
        workspace_token=bulk_change_service.encode_workspace(workspace),
    )


@bp.route("/requests/submit", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_SUBMIT_PERMISSION)
def submit_change_request():
    try:
        _maintain_change_request_activity()
        change_request = change_request_service.submit_change_request(
            request.form,
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        if change_request.status == "completed":
            flash("Employee changes applied and recorded.", "success")
        else:
            flash("Employee change request submitted.", "success")
    return redirect(_change_requests_return_url())


@bp.route("/requests/items/<int:item_id>/decision", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_APPROVE_PERMISSION)
def decide_change_request_item(item_id):
    try:
        _maintain_change_request_activity()
        rows = change_request_service.decide_change_request_item(
            item_id,
            request.form.get("action"),
            request.form.get("reason"),
            current_user,
            request.form.get("expected_revision"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        status = rows[0].status if rows else "updated"
        flash(f"Request field {status}.", "success")
    return redirect(_change_requests_return_url())


@bp.route("/requests/<int:request_id>/decision", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_APPROVE_PERMISSION)
def decide_change_request_remaining(request_id):
    try:
        _maintain_change_request_activity()
        rows = change_request_service.decide_change_request_remaining(
            request_id,
            request.form.get("action"),
            request.form.get("reason"),
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(f"Updated {len(rows)} Pending request fields.", "success")
    return redirect(_change_requests_return_url())


@bp.route("/requests/items/<int:item_id>/withdraw", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_SUBMIT_PERMISSION)
def withdraw_change_request_item(item_id):
    try:
        _maintain_change_request_activity()
        change_request_service.withdraw_change_request_item(
            item_id,
            request.form.get("reason"),
            current_user,
            request.form.get("expected_revision"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Request field withdrawn.", "success")
    return redirect(_change_requests_return_url())


@bp.route("/requests/<int:request_id>/withdraw", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_SUBMIT_PERMISSION)
def withdraw_change_request_remaining(request_id):
    try:
        _maintain_change_request_activity()
        count = change_request_service.withdraw_change_request_remaining(
            request_id,
            request.form.get("reason"),
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(f"Withdrew {count} remaining request fields.", "success")
    return redirect(_change_requests_return_url())


@bp.route("/requests/items/<int:item_id>/reverse", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_APPROVE_PERMISSION)
def reverse_change_request_item(item_id):
    try:
        _maintain_change_request_activity()
        change_request_service.reverse_change_request_item(
            item_id,
            request.form.get("reason"),
            current_user,
            request.form.get("expected_revision"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Request decision reversed to Pending.", "success")
    return redirect(_change_requests_return_url())


def _handle_attendance():
    if request.method == "GET":
        try:
            rollover = attendance_history_service.maintain_current_attendance_rollover(
                current_user
            )
            if rollover.changed:
                db.session.commit()
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "NeoStaffing attendance rollover maintenance failed"
            )
    can_edit = user_can(ATTENDANCE_TAKE_PERMISSION)
    can_view_staffing_groups = user_can(STAFFING_GROUPS_VIEW_PERMISSION)
    if request.method == "POST":
        if not can_edit:
            flash("You do not currently have Take Attendance permission.", "error")
            return redirect(url_for("neostaffing.attendance", **request.args))
        try:
            saved = staffing_service.save_attendance(request.form, current_user)
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(getattr(error, "orig", None) or error), "error")
        else:
            flash(f"Attendance saved for {saved} people.", "success")
        return redirect(
            url_for(
                "neostaffing.attendance",
                sort_id=request.form.get("sort_id", ""),
                operation_id=request.form.get("operation_id", ""),
                department_id=request.form.get("department_id", ""),
                work_area_id=request.form.get("work_area_id", ""),
                work_area_ids=request.form.getlist("work_area_ids"),
            )
        )
    filters = {
        "sort_id": request.args.get("sort_id", "").strip(),
        "operation_id": request.args.get("operation_id", "").strip(),
        "department_id": request.args.get("department_id", "").strip(),
        "work_area_id": request.args.get("work_area_id", "").strip(),
    }
    if request.args.getlist("work_area_ids"):
        filters["work_area_ids"] = request.args.getlist("work_area_ids")
    context = staffing_service.attendance_context(
        filters,
        current_user,
        include_staffing_groups=can_view_staffing_groups,
    )
    return render_template(
        "neostaffing/attendance.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
        can_edit_attendance=can_edit,
        attendance=context,
        unit_path=staffing_service.unit_path,
    )


@bp.route("/app-management")
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def app_management():
    return redirect(url_for("neostaffing.people", **request.args))


@bp.route("/org-chart")
@neostaffing_app_required(permission_key=ORG_CHART_VIEW_PERMISSION)
def org_chart():
    return _render_org_chart()


@bp.route("/reports")
@neostaffing_app_required(permission_key=REPORTS_VIEW_PERMISSION)
def reports():
    can_manage = user_can_access_app(current_user, "neostaffing", minimum_role="master")
    report_type = request.args.get("report_type", "").strip()
    if report_type == "vacation_calendars":
        vacation_year = _vacation_year_arg()
        context = {
            "report_type": report_type,
            "vacation": vacation_report_service.accessible_vacation_calendars(
                vacation_year, current_user
            ),
            "filters": {"report_type": report_type, "vacation_year": vacation_year},
        }
    elif report_type == "union_seniority":
        context = {
            "report_type": report_type,
            "union_seniority": {
                "scopes": vacation_report_service.union_seniority_scope_options()
            },
            "filters": {
                "report_type": report_type,
                "scope_id": request.args.get("scope_id", "").strip(),
                "union_classification": request.args.get(
                    "union_classification", "both"
                ).strip(),
            },
        }
    else:
        context = staffing_service.reports_context(
            {
                "report_type": report_type,
                "sort_id": request.args.get("sort_id", "").strip(),
                "operation_id": request.args.get("operation_id", "").strip(),
                "department_id": request.args.get("department_id", "").strip(),
                "work_area_id": request.args.get("work_area_id", "").strip(),
                "classification": request.args.get("classification", "").strip(),
                "employee_status": request.args.get("employee_status", "").strip(),
                "assignment_status": request.args.get(
                    "assignment_status", ""
                ).strip(),
                "attendance_date": request.args.get("attendance_date", "").strip(),
                "attendance_status": request.args.get(
                    "attendance_status", ""
                ).strip(),
                "active": request.args.get("active", "").strip(),
                "search": request.args.get("search", "").strip(),
                "include_management": request.args.get(
                    "include_management", ""
                ).strip(),
            },
            current_user if not can_manage else None,
        )
    return render_template(
        "neostaffing/reports.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=can_manage,
        reports=context,
        unit_path=staffing_service.unit_path,
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        employee_status_labels=staffing_service.EMPLOYEE_STATUS_LABELS,
        attendance_status_labels=staffing_service.ATTENDANCE_STATUS_LABELS,
    )


@bp.get("/reports/vacation-calendar.pdf")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_calendar_pdf():
    try:
        report = vacation_report_service.vacation_calendar_report_data(
            request.args.get("kind", ""),
            request.args.get("calendar_id", ""),
            request.args.get("year", ""),
            current_user,
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(
            url_for(
                "neostaffing.reports",
                report_type="vacation_calendars",
                year=request.args.get("year", ""),
            )
        )
    return send_file(
        vacation_report_service.build_vacation_calendar_pdf(report),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"vacation-calendar-{report['vacation_year']}.pdf",
    )


@bp.get("/reports/union-seniority.pdf")
@neostaffing_app_required(permission_key=REPORTS_VIEW_PERMISSION)
def vacation_union_seniority_pdf():
    try:
        report = vacation_report_service.union_seniority_report_data(
            request.args.get("scope_id", ""),
            request.args.get("union_classification", "both"),
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(
            url_for(
                "neostaffing.reports",
                report_type="union_seniority",
                scope_id=request.args.get("scope_id", ""),
                union_classification=request.args.get(
                    "union_classification", "both"
                ),
            )
        )
    return send_file(
        vacation_report_service.build_union_seniority_pdf(report),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="union-seniority-list.pdf",
    )


@bp.route("/vacation-selection")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_selection():
    vacation_year = _vacation_year_arg()
    return render_template(
        "neostaffing/vacation_selection.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        vacation_year=vacation_year,
        vacation_years=_vacation_year_options(vacation_year),
        selection_opens_on=vacation_service.vacation_selection_opens_on(
            vacation_year
        ),
        week_count=len(vacation_service.vacation_year_weeks(vacation_year)),
    )


@bp.route("/vacation-selection/management")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management():
    vacation_year = _vacation_year_arg()
    return render_template(
        "neostaffing/vacation_management.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        vacation=vacation_service.management_vacation_context(
            vacation_year,
            current_user,
        ),
        vacation_years=_vacation_year_options(vacation_year),
    )


@bp.route("/vacation-selection/management/capacity", methods=["POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_capacity():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.save_management_capacity(
            vacation_year,
            request.form.get("area_unit_id"),
            request.form,
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation capacity updated.", "success")
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.route("/vacation-selection/management/initialize", methods=["POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_initialize():
    vacation_year = request.form.get("vacation_year")
    context = vacation_service.management_vacation_context(
        vacation_year,
        current_user,
    )
    area_ids = [row["area"].id for row in context["areas"] if row["can_edit"]]
    try:
        created = vacation_service.initialize_management_capacity_year(
            vacation_year,
            area_ids,
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(
            f"Carried forward {len(created)} Management capacity setting(s).",
            "success",
        )
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.route("/vacation-selection/management/reduced-capacity", methods=["POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_reduced_capacity():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.set_reduced_capacity_enabled(
            vacation_year,
            request.form.get("area_unit_id"),
            request.form.get("week_ending"),
            request.form.get("enabled"),
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Weekly reduced-capacity setting updated.", "success")
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.route("/vacation-selection/management/select", methods=["POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_select():
    vacation_year = request.form.get("vacation_year")
    try:
        person = db.session.get(
            StaffingPerson,
            int(request.form.get("staffing_person_id") or 0),
        )
        week_endings = request.form.getlist("week_endings") or [
            request.form.get("week_ending")
        ]
        add_weeks = (
            vacation_service.add_division_manager_weeks
            if person and person.classification == "division_manager"
            else vacation_service.add_management_weeks
        )
        saved = add_weeks(person, vacation_year, week_endings, current_user)
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(f"Added {len(saved)} Management vacation week(s).", "success")
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.post("/vacation-selection/management/change-request")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_change_request():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.request_management_selection_change(
            request.form.get("selection_id"),
            request.form.get("request_type"),
            current_user,
            requested_week_ending=request.form.get("requested_week_ending"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation change request submitted.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/change-request/<int:request_id>/cancel")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_change_request_cancel(request_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.cancel_management_selection_change_request(
            request_id, current_user
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation change request cancelled.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/change-request/<int:request_id>/review")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_change_request_review(request_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.review_management_selection_change_request(
            request_id,
            request.form.get("decision"),
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation change request resolved.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/selection/<int:selection_id>/move")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_selection_move(selection_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.move_management_selection(
            selection_id,
            request.form.get("requested_week_ending"),
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation week moved.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/selection/<int:selection_id>/cancel")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_selection_cancel(selection_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.cancel_management_selection(
            selection_id,
            current_user,
            correction=request.form.get("correction"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation week removed and bank restored.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/split")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_split():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.split_management_week(
            request.form.get("staffing_person_id"),
            vacation_year,
            current_user,
            selection=request.form.get("selection_id"),
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation week split into five days.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.route("/vacation-selection/management/pass", methods=["POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_pass():
    vacation_year = request.form.get("vacation_year")
    try:
        person = db.session.get(
            StaffingPerson,
            int(request.form.get("staffing_person_id") or 0),
        )
        vacation_service.pass_management_turn(
            vacation_year,
            request.form.get("area_unit_id"),
            person,
            current_user,
            administrative=request.form.get("administrative") == "1",
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management vacation turn advanced.", "success")
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.route("/vacation-selection/union")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendars():
    vacation_year = _vacation_year_arg()
    try:
        owner_changes = vacation_service.reconcile_union_calendar_owners(
            vacation_year
        )
        if owner_changes:
            db.session.commit()
    except (ValueError, IntegrityError):
        db.session.rollback()
    return render_template(
        "neostaffing/vacation_union.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        vacation=vacation_service.union_calendars_context(
            vacation_year,
            current_user,
        ),
        vacation_years=_vacation_year_options(vacation_year),
    )


@bp.post("/vacation-selection/union/<int:calendar_id>/carry-forward")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_carry_forward(calendar_id):
    try:
        created = vacation_service.carry_forward_official_calendar(
            calendar_id, current_user
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
        return redirect(url_for("neostaffing.vacation_union_calendars"))
    flash(f"Created {created.name} for {created.vacation_year}.", "success")
    return redirect(
        url_for(
            "neostaffing.vacation_union_calendars",
            year=created.vacation_year,
        )
    )


@bp.get("/vacation-selection/union/<int:calendar_id>/view")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_view(calendar_id):
    try:
        calendar = vacation_service.view_union_calendar_context(
            calendar_id, current_user
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("neostaffing.vacation_union_calendars"))
    return render_template(
        "neostaffing/vacation_union_view.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        view=calendar,
    )


@bp.post("/vacation-selection/union/<int:calendar_id>/select")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_select(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.add_union_week(
            calendar_id,
            request.form.get("staffing_person_id"),
            vacation_year,
            request.form.get("week_ending"),
            request.form.get("bank_type"),
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Union vacation week reserved.", "success")
    return redirect(url_for("neostaffing.vacation_union_calendars", year=vacation_year))


@bp.post("/vacation-selection/union/<int:calendar_id>/split")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_split(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.split_union_optional_week(
            calendar_id,
            request.form.get("staffing_person_id"),
            vacation_year,
            current_user,
            selection=request.form.get("selection_id"),
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Optional Week split into five vacation days.", "success")
    return redirect(url_for("neostaffing.vacation_union_calendars", year=vacation_year))


@bp.post("/vacation-selection/split-day/schedule")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_split_day_schedule():
    vacation_year = request.form.get("vacation_year")
    program = request.form.get("program")
    try:
        vacation_service.schedule_split_vacation_day(
            request.form.get("conversion_id"),
            request.form.get("vacation_date"),
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Split vacation day scheduled.", "success")
    return redirect(_vacation_program_url(program, vacation_year))


@bp.post("/vacation-selection/split-day/<int:day_id>/cancel")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_split_day_cancel(day_id):
    vacation_year = request.form.get("vacation_year")
    program = request.form.get("program")
    try:
        vacation_service.cancel_split_vacation_day(day_id, current_user)
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Split vacation day removed.", "success")
    return redirect(_vacation_program_url(program, vacation_year))


@bp.post("/vacation-selection/day/schedule")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_day_schedule():
    vacation_year = request.form.get("vacation_year")
    program = request.form.get("program")
    try:
        vacation_service.schedule_vacation_entitlement_day(
            request.form.get("staffing_person_id"),
            request.form.get("vacation_date"),
            request.form.get("item_type"),
            current_user,
            program=program,
            entitlement_id=request.form.get("entitlement_id"),
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Vacation day scheduled.", "success")
    return redirect(_vacation_program_url(program, vacation_year))


@bp.post("/vacation-selection/day/<int:day_id>/cancel")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_day_cancel(day_id):
    vacation_year = request.form.get("vacation_year")
    program = request.form.get("program")
    try:
        vacation_service.cancel_vacation_entitlement_day(day_id, current_user)
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Vacation day removed; entitlement restored.", "success")
    return redirect(_vacation_program_url(program, vacation_year))


@bp.post("/vacation-selection/management/availability")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_availability():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.schedule_management_availability_day(
            request.form.get("staffing_person_id"),
            request.form.get("availability_date"),
            request.form.get("item_type"),
            current_user,
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management availability updated.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/management/availability/<int:day_id>/remove")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_availability_remove(day_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.cancel_management_availability_day(day_id, current_user)
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management availability entry removed.", "success")
    return redirect(url_for("neostaffing.vacation_management", year=vacation_year))


@bp.post("/vacation-selection/split-week/<int:conversion_id>/recombine")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_split_week_recombine(conversion_id):
    vacation_year = request.form.get("vacation_year")
    program = request.form.get("program")
    try:
        vacation_service.recombine_split_vacation_week(conversion_id, current_user)
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Five split days recombined into one unused vacation week.", "success")
    return redirect(_vacation_program_url(program, vacation_year))


@bp.post("/vacation-selection/union/selection/<int:selection_id>/review")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_selection_review(selection_id):
    vacation_year = request.form.get("vacation_year")
    try:
        decision = str(request.form.get("decision") or "").strip().casefold()
        if decision not in {"approve", "deny"}:
            raise ValueError("Choose Approve or Deny.")
        vacation_service.review_union_selection(
            selection_id,
            decision == "approve",
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Union vacation selection reviewed.", "success")
    return redirect(url_for("neostaffing.vacation_union_calendars", year=vacation_year))


@bp.post("/vacation-selection/union/selection/<int:selection_id>/cancel")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_selection_cancel(selection_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.cancel_union_selection(
            selection_id,
            current_user,
            correction=request.form.get("correction"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Union vacation selection cancelled.", "success")
    return redirect(url_for("neostaffing.vacation_union_calendars", year=vacation_year))


@bp.post("/vacation-selection/union/selection/<int:selection_id>/move")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_selection_move(selection_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.move_union_selection(
            selection_id,
            request.form.get("requested_week_ending"),
            current_user,
            capacity_override=request.form.get("capacity_override"),
        )
        db.session.commit()
    except (TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Union vacation week moved.", "success")
    return redirect(
        url_for("neostaffing.vacation_union_calendars", year=vacation_year)
    )


@bp.route("/vacation-selection/union/new", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_new():
    vacation_year = _vacation_year_arg()
    if request.method == "POST":
        vacation_year = request.form.get("vacation_year")
        try:
            calendar = vacation_service.create_union_calendar(
                request.form,
                current_user,
            )
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(getattr(error, "orig", None) or error), "error")
        else:
            flash("Union vacation calendar created.", "success")
            return redirect(
                url_for(
                    "neostaffing.vacation_union_calendar_edit",
                    calendar_id=calendar.id,
                )
            )
    return _render_vacation_union_editor(None, vacation_year)


@bp.route(
    "/vacation-selection/union/<int:calendar_id>/edit",
    methods=["GET", "POST"],
)
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_edit(calendar_id):
    calendar = db.session.get(StaffingVacationUnionCalendar, calendar_id)
    if not calendar:
        flash("The selected Union vacation calendar was not found.", "error")
        return redirect(url_for("neostaffing.vacation_union_calendars"))
    if request.method == "POST":
        try:
            vacation_service.update_union_calendar(
                calendar,
                request.form,
                current_user,
            )
            db.session.commit()
        except (ValueError, IntegrityError) as error:
            db.session.rollback()
            flash(str(getattr(error, "orig", None) or error), "error")
        else:
            flash("Union vacation calendar updated.", "success")
            return redirect(
                url_for(
                    "neostaffing.vacation_union_calendar_edit",
                    calendar_id=calendar.id,
                )
            )
    return _render_vacation_union_editor(calendar, calendar.vacation_year)


@bp.post("/vacation-selection/union/<int:calendar_id>/delete")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_delete(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.delete_union_calendar(calendar_id, current_user)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Union vacation calendar deleted; employee selections were preserved.", "success")
    return redirect(
        url_for("neostaffing.vacation_union_calendars", year=vacation_year)
    )


@bp.post("/vacation-selection/union/<int:calendar_id>/shares")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_shares(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.update_view_calendar_shares(
            calendar_id,
            request.form.getlist("recipient_user_ids"),
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("View Only calendar sharing updated.", "success")
    return redirect(
        url_for("neostaffing.vacation_union_calendar_edit", calendar_id=calendar_id)
    )


@bp.get("/vacation-selection/union/share-search")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_share_search():
    rows = vacation_service.search_management_calendar_users(
        request.args.get("q"), exclude_user_id=current_user.id
    )
    return jsonify(
        {
            "results": [
                {
                    "user_id": row["user"].id,
                    "employee_id": getattr(row["person"], "employee_id", None)
                    or row["user"].employee_id
                    or "",
                    "name": getattr(row["person"], "full_name", None)
                    or row["user"].full_name,
                }
                for row in rows
            ]
        }
    )


@bp.post("/vacation-selection/union/<int:calendar_id>/copy")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_copy(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        copied = vacation_service.copy_shared_view_calendar(
            calendar_id, request.form.get("name"), current_user
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Independent View Only calendar created from shared scope.", "success")
        return redirect(
            url_for(
                "neostaffing.vacation_union_calendar_edit", calendar_id=copied.id
            )
        )
    return redirect(
        url_for("neostaffing.vacation_union_calendars", year=vacation_year)
    )


@bp.get("/vacation-selection/union/admin")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_admin():
    try:
        admin = vacation_service.union_calendar_admin_context(current_user)
        owner_changes = vacation_service.reconcile_union_calendar_owners()
        if owner_changes:
            db.session.commit()
            admin = vacation_service.union_calendar_admin_context(current_user)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("neostaffing.vacation_union_calendars"))
    return render_template(
        "neostaffing/vacation_union_admin.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        admin=admin,
    )


@bp.post("/vacation-selection/union/<int:calendar_id>/reset")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_union_calendar_reset(calendar_id):
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.reset_union_vacation_calendar(
            calendar_id, vacation_year, current_user
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Official Union calendar reset to its fresh-year state.", "success")
    return redirect(url_for("neostaffing.vacation_union_calendar_admin"))


@bp.post("/vacation-selection/management/reset")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_management_reset():
    vacation_year = request.form.get("vacation_year")
    try:
        vacation_service.reset_management_vacation_area(
            request.form.get("area_unit_id"),
            vacation_year,
            current_user,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management calendar reset to its fresh-year state.", "success")
    return redirect(
        url_for("neostaffing.vacation_management", year=vacation_year)
    )


@bp.route("/settings")
@neostaffing_app_required(minimum_role="master")
def settings():
    contract = vacation_service.qualifying_holiday_settings(current_user)
    return render_template(
        "neostaffing/settings.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        holidays=contract["holidays"],
        can_edit_settings=contract["can_edit"],
        holiday_month_choices=contract["month_choices"],
        holiday_weekday_choices=contract["weekday_choices"],
        holiday_occurrence_choices=contract["occurrence_choices"],
        holiday_rule_label=contract["rule_label"],
    )


@bp.route("/settings/floating-holidays", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def save_floating_holiday_setting():
    try:
        vacation_service.save_qualifying_holiday(
            request.form.get("holiday_id"),
            name=request.form.get("name"),
            user=current_user,
            rule_type=request.form.get("rule_type"),
            month=request.form.get("month"),
            day_of_month=request.form.get("day_of_month"),
            weekday=request.form.get("weekday"),
            occurrence=request.form.get("occurrence"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Floating Holiday rule saved.", "success")
    return redirect(url_for("neostaffing.settings"))


@bp.route("/settings/floating-holidays/<int:holiday_id>/delete", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def delete_floating_holiday_setting(holiday_id):
    try:
        vacation_service.delete_qualifying_holiday(holiday_id, current_user)
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        flash("Floating Holiday rule removed; existing earned awards were preserved.", "success")
    return redirect(url_for("neostaffing.settings"))


@bp.route("/permissions", methods=["GET", "POST"])
@neostaffing_app_required(permission_key=PERMISSIONS_VIEW_PERMISSION)
def permissions():
    ensure_default_permission_rules()
    app_role = get_user_app_role(current_user, "neostaffing")
    can_edit = app_role == "grandmaster" and user_can(PERMISSIONS_EDIT_PERMISSION)

    if request.method == "POST":
        if not can_edit:
            flash("Only a NeoStaffing Grandmaster can save permission settings.", "error")
            return redirect(url_for("neostaffing.permissions"))
        try:
            _apply_neostaffing_permission_rule_form()
            db.session.commit()
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
        else:
            flash("NeoStaffing permission settings updated.", "success")
            return redirect(url_for("neostaffing.permissions"))

    rules = PermissionRule.query.filter(
        PermissionRule.permission_key.like("neostaffing.%")
    ).order_by(PermissionRule.permission_key.asc()).all()
    rule_by_key = {rule.permission_key: rule for rule in rules}
    ordered_keys = [
        key for key in NEOSTAFFING_PERMISSION_LABELS if key in rule_by_key
    ]
    ordered_keys.extend(
        sorted(key for key in rule_by_key if key not in NEOSTAFFING_PERMISSION_LABELS)
    )
    capabilities = [
        {
            "label": NEOSTAFFING_PERMISSION_LABELS.get(
                key,
                key.removeprefix("neostaffing.")
                .replace("_", " ")
                .replace(".", " ")
                .title(),
            ),
            "rule": rule_by_key[key],
        }
        for key in ordered_keys
    ]
    return render_template(
        "neostaffing/permissions.html",
        app_role=app_role,
        can_edit_permissions=can_edit,
        capabilities=capabilities,
        role_choices=ROLE_CHOICES,
    )


@bp.route("/people/<int:person_id>/assign-work-area", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def people_assign_work_area(person_id):
    try:
        staffing_service.assign_work_area(
            _get_person(person_id),
            _get_unit(request.form.get("work_area_unit_id")),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Work area assignment updated.", "success")
    return redirect(_people_return_url(person_id))


@bp.route("/people/<int:person_id>/clear-work-area", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def people_clear_work_area(person_id):
    try:
        staffing_service.clear_work_assignment(_get_person(person_id))
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Work area assignment cleared.", "success")
    return redirect(_people_return_url(person_id))


@bp.route("/people/bulk-work-area", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_BULK_ACTIONS_PERMISSION)
def people_bulk_work_area():
    action = request.form.get("bulk_action", "").strip()
    try:
        work_area = None
        if action in {"assign", "move"}:
            work_area = _get_unit(request.form.get("work_area_unit_id"))
        result = staffing_service.bulk_update_work_area_assignments(
            request.form.getlist("person_ids"),
            action,
            work_area,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(f"Bulk work-area action updated {result['updated']} people.", "success")
        if result["skipped"]:
            flash(
                "Skipped management classifications: " + ", ".join(result["skipped"]),
                "warning",
            )
        if result["missing"]:
            flash("Skipped missing people: " + ", ".join(result["missing"]), "warning")
    return redirect(_people_return_url())


@bp.route("/app-management/hierarchy")
@neostaffing_app_required(permission_key=HIERARCHY_VIEW_PERMISSION)
def hierarchy():
    return _render_org_chart()


def _render_org_chart():
    view = request.args.get("view", "").strip().lower()
    if view == "management":
        app_role = get_user_app_role(current_user, "neostaffing")
        can_direct_edit = bool(
            user_can(MANAGEMENT_ASSIGN_PERMISSION)
            and staffing_service.can_user_directly_edit_reporting_relationship(
                current_user,
                app_role,
            )
        )
        return render_template(
            "neostaffing/org_chart_management.html",
            app_role=app_role,
            can_manage_app=user_can_access_app(
                current_user,
                "neostaffing",
                minimum_role="master",
            ),
            can_direct_edit=can_direct_edit,
            management=staffing_service.management_org_chart_context(
                request.args.get("person_id", "").strip()
            ),
            classification_labels=staffing_service.CLASSIFICATION_LABELS,
            employee_status_labels=staffing_service.EMPLOYEE_STATUS_LABELS,
        )

    context = staffing_service.org_chart_context(request.args.get("unit_id", "").strip())
    return render_template(
        "neostaffing/org_chart.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
        can_edit_structure=user_can(ORG_CHART_EDIT_STRUCTURE_PERMISSION),
        can_assign_management=bool(
            user_can(MANAGEMENT_ASSIGN_PERMISSION)
            and _can_directly_change_management_relationships()
        ),
        org_chart=context,
        hierarchy=context["tree"],
        units=context["units"],
        management_candidates=staffing_service.management_candidates_for_unit(context["selected_unit"]),
        sorts=staffing_service.selectable_parent_units("operation"),
        operations=staffing_service.selectable_parent_units("department"),
        departments=staffing_service.units_by_type("department"),
        work_area_parents=staffing_service.selectable_parent_units("work_area"),
        unit_type_labels=staffing_service.UNIT_TYPE_LABELS,
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        unit_path=staffing_service.unit_path,
        linked_user_for_person=staffing_service.linked_user_for_person,
    )


@bp.route(
    "/app-management/reporting/<int:person_id>/update",
    methods=["POST"],
)
@neostaffing_app_required(permission_key=MANAGEMENT_ASSIGN_PERMISSION)
def update_reporting_relationship(person_id):
    app_role = get_user_app_role(current_user, "neostaffing")
    if not staffing_service.can_user_directly_edit_reporting_relationship(
        current_user,
        app_role,
    ):
        flash(
            "Direct Reports To changes require an eligible FT Supervisor, Manager, Division Manager, or Grandmaster.",
            "error",
        )
        return redirect(
            url_for(
                "neostaffing.org_chart",
                view="management",
                person_id=person_id,
            )
        )
    try:
        staffing_service.update_reporting_relationship(
            person_id,
            request.form.get("reports_to_person_id"),
            request.form.get("expected_revision"),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Reports To updated.", "success")
    return redirect(
        url_for(
            "neostaffing.org_chart",
            view="management",
            person_id=person_id,
        )
    )


@bp.route("/app-management/hierarchy/units", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def create_unit():
    return _mutate(
        lambda: staffing_service.create_unit(request.form),
        "Staffing unit added.",
        "neostaffing.org_chart",
        _org_chart_return_values(),
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/update", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def update_unit(unit_id):
    unit = _get_unit(unit_id)
    try:
        normalized = staffing_service.validated_unit_update_values(unit, request.form)
        if normalized["parent_id"] != unit.parent_id:
            mutation = management_review_service.unit_update_mutation(unit, normalized)
            review = management_review_service.prepare_management_relationship_review(
                mutation
            )
            if review["required"]:
                if not _can_directly_change_management_relationships():
                    raise ValueError(
                        "Direct management relationship changes require an eligible FT Supervisor, "
                        "Manager, Division Manager, or Grandmaster."
                    )
                return _render_management_relationship_review(
                    review,
                    "neostaffing.org_chart",
                    _org_chart_return_values(unit.id),
                )
        staffing_service.update_unit(unit, request.form)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Staffing unit updated.", "success")
    return redirect(
        url_for("neostaffing.org_chart", **_org_chart_return_values(unit.id))
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/toggle-active", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def toggle_unit_active(unit_id):
    unit = _get_unit(unit_id)

    def toggle():
        unit.active = not unit.active

    return _mutate(
        toggle,
        "Staffing unit status updated.",
        "neostaffing.org_chart",
        _org_chart_return_values(unit.id),
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/delete", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def delete_unit(unit_id):
    unit = _get_unit(unit_id)
    parent_id = unit.parent_id
    return _mutate(
        lambda: staffing_service.delete_unit(unit),
        "Staffing unit deleted.",
        "neostaffing.org_chart",
        _org_chart_return_values(parent_id),
    )


@bp.route("/app-management/required-headcount")
@neostaffing_app_required(permission_key=ORG_CHART_VIEW_PERMISSION)
def required_headcount():
    return _redirect_legacy_scope_to_org_chart()


@bp.route("/app-management/planned-staffing")
@neostaffing_app_required(permission_key=ORG_CHART_VIEW_PERMISSION)
def planned_staffing():
    return _redirect_legacy_scope_to_org_chart()


@bp.route("/app-management/required-headcount/<int:unit_id>/update", methods=["POST"])
@bp.route("/app-management/planned-staffing/<int:unit_id>/update", methods=["POST"])
@neostaffing_app_required(permission_key=PLANNED_STAFFING_EDIT_PERMISSION)
def update_planned_staffing(unit_id):
    unit = _get_unit(unit_id)
    try:
        staffing_service.update_required_headcount(unit, request.form.get("required_headcount"))
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        message = str(getattr(error, "orig", None) or error)
        flash(message, "error")
    else:
        flash("Planned staffing updated.", "success")

    return redirect(url_for("neostaffing.org_chart", **_org_chart_return_values(unit.id)))


@bp.route("/app-management/people")
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def people_management():
    return redirect(url_for("neostaffing.people", **request.args))


@bp.route("/app-management/people", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def create_person():
    person = None
    try:
        person = staffing_service.create_person(request.form)
        creation_flow = request.form.get("creation_flow", "").strip().lower()
        if creation_flow == "management":
            if person.classification not in staffing_service.MANAGEMENT_CLASSIFICATIONS:
                raise ValueError("Add Management requires a management classification.")
            raw_unit_ids = request.form.getlist("initial_assignment_unit_ids")
        elif creation_flow == "employee":
            if person.classification not in staffing_service.WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS:
                raise ValueError("Add Employee requires a writable employee classification.")
            work_area_id = request.form.get("initial_work_area_unit_id", "").strip()
            if not work_area_id:
                raise ValueError("Add Employee requires a selected Work Area.")
            raw_unit_ids = [work_area_id]
        else:
            raise ValueError("Select a valid People creation workflow.")
        try:
            unit_ids = list(dict.fromkeys(int(value) for value in raw_unit_ids if str(value).strip()))
        except (TypeError, ValueError):
            raise ValueError("Select valid initial assignment units.")
        units = (
            StaffingUnit.query.filter(
                StaffingUnit.id.in_(unit_ids or {-1}),
                StaffingUnit.active.is_(True),
            )
            .order_by(StaffingUnit.id)
            .all()
        )
        if len(units) != len(unit_ids):
            raise ValueError("Select active initial assignment units.")
        if creation_flow == "employee" and (
            len(units) != 1 or units[0].unit_type != "work_area"
        ):
            raise ValueError("Add Employee requires one active Work Area.")
        if units and person.classification not in staffing_service.NON_MANAGEMENT_CLASSIFICATIONS:
            if not (
                user_can(MANAGEMENT_ASSIGN_PERMISSION)
                and _can_directly_change_management_relationships()
            ):
                raise ValueError("You do not have permission to assign management.")
        staffing_service.create_initial_person_assignments(person, units)

        primary_value = request.form.get("twenty_c_primary", "").strip()
        if primary_value:
            if creation_flow != "management":
                raise ValueError("Primary FT Supervisor applies only to Add Management.")
            if person.classification != "twenty_c_full_time_supervisor":
                raise ValueError("Primary FT Supervisor applies only to a 20C Full-Time Supervisor.")
            try:
                sort_id, ft_supervisor_id = (int(value) for value in primary_value.split(":", 1))
            except (TypeError, ValueError):
                raise ValueError("Select a valid Primary FT Supervisor.")
            sort_unit = db.session.get(StaffingUnit, sort_id)
            ft_supervisor = db.session.get(StaffingPerson, ft_supervisor_id)
            staffing_service.create_twenty_c_affiliation(
                person, ft_supervisor, sort_unit, "primary"
            )
            staffing_service.update_reporting_relationship(
                person.id, ft_supervisor.id, "none"
            )

        if creation_flow == "employee":
            staffing_service.create_shift_flow_plan(person, request.form, units[0])
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        person = None
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Person added.", "success")
    return redirect(_people_return_url(person.id if person else None))


@bp.route("/app-management/people/bulk-create", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def create_people_bulk():
    people = []
    try:
        work_area = _get_unit(request.form.get("initial_work_area_unit_id"))
        people = staffing_service.create_people_batch(
            _bulk_employee_rows(request.form.get("employee_rows")),
            work_area,
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash(f"Added {len(people)} employees.", "success")
    return redirect(_people_return_url(people[0].id if len(people) == 1 else None))


def _bulk_employee_rows(raw_rows):
    columns = (
        "employee_id",
        "first_name",
        "last_name",
        "seniority_date",
        "phone_number",
        "classification",
        "employee_status",
    )
    header = (
        "employee id",
        "first name",
        "last name",
        "seniority date",
        "phone",
        "classification",
        "employee status",
    )
    rows = []
    for line_number, raw_line in enumerate(str(raw_rows or "").splitlines(), start=1):
        if not raw_line.strip():
            continue
        cells = [cell.strip() for cell in raw_line.split("\t")]
        if not rows and tuple(cell.lower() for cell in cells) == header:
            continue
        if len(cells) != len(columns):
            raise ValueError(
                f"Bulk row {line_number} must contain {len(columns)} tab-separated fields."
            )
        row = dict(zip(columns, cells))
        row["active"] = "1"
        rows.append(row)
    return rows


@bp.route("/app-management/people/<int:person_id>/update", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def update_person(person_id):
    person = _get_person(person_id)
    return _mutate_to_people(
        lambda: staffing_service.update_person(person, request.form),
        "Person updated.",
        person_id,
    )


@bp.route("/app-management/people/<int:person_id>/toggle-active", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def toggle_person_active(person_id):
    person = _get_person(person_id)
    return _mutate_to_people(
        lambda: staffing_service.toggle_person_active(person),
        "Person status updated.",
        person_id,
    )


@bp.route("/app-management/people/<int:person_id>/delete", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def delete_person(person_id):
    person = _get_person(person_id)
    return _mutate_to_people(lambda: staffing_service.delete_person(person), "Person deleted.")


@bp.route("/app-management/work-assignments")
@neostaffing_app_required(permission_key=PEOPLE_VIEW_PERMISSION)
def work_assignments():
    return redirect(url_for("neostaffing.people", **request.args))


@bp.route("/app-management/work-assignments/assign", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def assign_work_area():
    return _mutate(
        lambda: staffing_service.assign_work_area(
            _get_person(request.form.get("person_id")),
            _get_unit(request.form.get("work_area_unit_id")),
            request.form.get("effective_date"),
        ),
        "Work assignment updated.",
        "neostaffing.work_assignments",
    )


@bp.route("/app-management/work-assignments/<int:person_id>/clear", methods=["POST"])
@neostaffing_app_required(permission_key=PEOPLE_EDIT_PERMISSION)
def clear_work_assignment(person_id):
    person = _get_person(person_id)
    return _mutate(
        lambda: staffing_service.clear_work_assignment(person),
        "Work assignment deactivated.",
        "neostaffing.work_assignments",
    )


@bp.route("/app-management/management-assignments")
@neostaffing_app_required(permission_key=ORG_CHART_VIEW_PERMISSION)
def management_assignments():
    return redirect(url_for("neostaffing.org_chart", **request.args))


@bp.route("/app-management/management-assignments", methods=["POST"])
@neostaffing_app_required(permission_key=MANAGEMENT_ASSIGN_PERMISSION)
def create_management_assignment():
    redirect_endpoint, redirect_values = _management_assignment_return_target()
    if not _can_directly_change_management_relationships():
        flash(
            "Direct management assignment changes require an eligible FT Supervisor, Manager, "
            "Division Manager, or Grandmaster.",
            "error",
        )
        return redirect(url_for(redirect_endpoint, **(redirect_values or {})))
    try:
        mutation = management_review_service.assignment_add_mutation(
            request.form.get("person_id"),
            request.form.get("unit_id"),
            request.form.get("leadership_level"),
        )
        review = management_review_service.prepare_management_relationship_review(
            mutation
        )
        if review["required"]:
            return _render_management_relationship_review(
                review,
                redirect_endpoint,
                redirect_values,
            )
        staffing_service.create_leadership_assignment(
            _get_person(mutation["person_id"]),
            _get_unit(mutation["unit_id"]),
            mutation["leadership_level"],
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management assignment added.", "success")
    return redirect(url_for(redirect_endpoint, **(redirect_values or {})))


@bp.route("/app-management/management-assignments/<int:assignment_id>/delete", methods=["POST"])
@neostaffing_app_required(permission_key=MANAGEMENT_ASSIGN_PERMISSION)
def delete_management_assignment(assignment_id):
    assignment = db.session.get(StaffingLeadershipAssignment, assignment_id)
    if not assignment:
        flash("Management assignment was not found.", "error")
        return redirect(url_for("neostaffing.management_assignments"))
    redirect_endpoint, redirect_values = _management_assignment_return_target()
    if not _can_directly_change_management_relationships():
        flash(
            "Direct management assignment changes require an eligible FT Supervisor, Manager, "
            "Division Manager, or Grandmaster.",
            "error",
        )
        return redirect(url_for(redirect_endpoint, **(redirect_values or {})))
    try:
        mutation = management_review_service.assignment_remove_mutation(assignment.id)
        review = management_review_service.prepare_management_relationship_review(
            mutation
        )
        if review["required"]:
            return _render_management_relationship_review(
                review,
                redirect_endpoint,
                redirect_values,
            )
        staffing_service.delete_leadership_assignment(assignment)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Management assignment deactivated.", "success")
    return redirect(url_for(redirect_endpoint, **(redirect_values or {})))


@bp.route("/app-management/management-review/apply", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_VIEW_PERMISSION)
def apply_management_relationship_review():
    redirect_endpoint, redirect_values = _review_return_target()
    try:
        mutation = _review_mutation_from_form()
        permission_key = (
            ORG_CHART_EDIT_STRUCTURE_PERMISSION
            if mutation["kind"] == "update_unit"
            else MANAGEMENT_ASSIGN_PERMISSION
        )
        if not user_can(permission_key):
            raise ValueError("You do not have permission to apply this operational change.")
        if not _can_directly_change_management_relationships():
            raise ValueError(
                "Direct management relationship changes require an eligible FT Supervisor, "
                "Manager, Division Manager, or Grandmaster."
            )
        management_review_service.apply_management_relationship_review(
            mutation,
            request.form.get("review_revision"),
            _management_review_decisions(),
        )
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Operational assignment and Reports To review applied.", "success")
    return redirect(url_for(redirect_endpoint, **(redirect_values or {})))


def _can_directly_change_management_relationships():
    return bool(
        staffing_service.can_user_directly_edit_reporting_relationship(
            current_user,
            get_user_app_role(current_user, "neostaffing"),
        )
    )


def _render_management_relationship_review(
    review,
    return_endpoint,
    return_values=None,
):
    return render_template(
        "neostaffing/management_relationship_review.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        review=review,
        return_endpoint=return_endpoint,
        return_values=return_values or {},
        cancel_url=url_for(return_endpoint, **(return_values or {})),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        unit_type_labels=staffing_service.UNIT_TYPE_LABELS,
    )


def _management_assignment_return_target():
    return_unit_id = request.form.get("return_unit_id", "").strip()
    if request.form.get("return_people", "").strip():
        return "neostaffing.people", {
            key: request.form.get(key, "").strip()
            for key in (
                "sort_id",
                "operation_id",
                "department_id",
                "work_area_id",
                "classification",
                "employee_status",
                "active",
                "assignment_status",
                "search",
                "page",
                "per_page",
                "leadership_only",
            )
            if request.form.get(key, "").strip()
        }
    if return_unit_id:
        return "neostaffing.org_chart", {"unit_id": return_unit_id}
    return "neostaffing.management_assignments", {}


def _review_return_target():
    endpoint = request.form.get("return_endpoint", "").strip()
    allowed_keys = {
        "neostaffing.people": {
            "sort_id",
            "operation_id",
            "department_id",
            "work_area_id",
            "classification",
            "employee_status",
            "active",
            "assignment_status",
            "search",
            "page",
            "per_page",
            "leadership_only",
        },
        "neostaffing.org_chart": {"unit_id"},
        "neostaffing.management_assignments": set(),
    }
    if endpoint not in allowed_keys:
        endpoint = "neostaffing.org_chart"
    values = {
        key.removeprefix("return_value_"): value.strip()
        for key, value in request.form.items()
        if key.startswith("return_value_")
        and key.removeprefix("return_value_") in allowed_keys[endpoint]
        and value.strip()
    }
    return endpoint, values


def _review_mutation_from_form():
    kind = request.form.get("kind", "").strip()
    if kind == "add_assignment":
        return management_review_service.assignment_add_mutation(
            request.form.get("person_id"),
            request.form.get("unit_id"),
            request.form.get("leadership_level"),
        )
    if kind == "remove_assignment":
        return management_review_service.assignment_remove_mutation(
            request.form.get("assignment_id")
        )
    if kind != "update_unit":
        raise ValueError("Unsupported management relationship review.")
    unit = _get_unit(request.form.get("unit_id"))
    normalized = staffing_service.validated_unit_update_values(unit, request.form)
    return management_review_service.unit_update_mutation(unit, normalized)


def _management_review_decisions():
    decisions = {}
    for raw_person_id in request.form.getlist("affected_person_ids"):
        try:
            person_id = int(raw_person_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid management relationship review person.")
        raw_action = request.form.get(f"relationship_action_{person_id}", "").strip()
        target_id = None
        if raw_action.startswith("target:"):
            action = "change"
            target_id = raw_action.partition(":")[2]
        elif raw_action == "different":
            action = "change"
            target_id = request.form.get(f"different_reports_to_{person_id}")
        elif raw_action == "keep":
            action = "keep"
        else:
            raise ValueError("Choose a Reports To decision for every affected person.")
        decisions[person_id] = {
            "action": action,
            "reports_to_person_id": target_id,
            "expected_revision": request.form.get(
                f"relationship_revision_{person_id}",
                "",
            ).strip(),
        }
    return decisions


def _mutate(callback, success_message, redirect_endpoint, redirect_values=None):
    try:
        callback()
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        message = str(getattr(error, "orig", None) or error)
        flash(message, "error")
    else:
        flash(success_message, "success")
    return redirect(url_for(redirect_endpoint, **(redirect_values or {})))


def _apply_neostaffing_permission_rule_form():
    for rule_id in request.form.getlist("rule_ids"):
        try:
            rule = db.session.get(PermissionRule, int(rule_id))
        except (TypeError, ValueError):
            raise ValueError("Unsupported NeoStaffing permission selected.")
        if not rule or not rule.permission_key.startswith("neostaffing."):
            raise ValueError("Unsupported NeoStaffing permission selected.")

        minimum_role = request.form.get(f"minimum_role_{rule.id}", "").strip().lower()
        if minimum_role not in ROLE_CHOICES:
            raise ValueError("Unsupported minimum role selected.")
        rule.minimum_role = minimum_role


def _org_chart_return_values(default_unit_id=None):
    """Keep the selected tree unit stable after an Org Chart mutation."""
    unit_id = request.form.get("return_unit_id", "").strip() or str(default_unit_id or "")
    return {"unit_id": unit_id} if unit_id else {}


def _redirect_legacy_scope_to_org_chart():
    query = dict(request.args)
    for key in ("work_area_id", "department_id", "operation_id", "sort_id"):
        unit_id = query.get(key)
        if unit_id:
            query["unit_id"] = unit_id
            break
    for key in ("work_area_id", "department_id", "operation_id", "sort_id"):
        query.pop(key, None)
    return redirect(url_for("neostaffing.org_chart", **query))


def _mutate_to_people(callback, success_message, person_id=None):
    try:
        callback()
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        message = str(getattr(error, "orig", None) or error)
        flash(message, "error")
    else:
        flash(success_message, "success")
    return redirect(_people_return_url(person_id))


def _get_person(person_id):
    person = db.session.get(StaffingPerson, int(person_id or 0))
    if not person:
        raise ValueError("Person was not found.")
    return person


def _get_unit(unit_id):
    unit = db.session.get(StaffingUnit, int(unit_id or 0))
    if not unit:
        raise ValueError("Staffing unit was not found.")
    return unit


def _filter_people_for_work_assignment_page(people_rows):
    assignment_status = request.args.get("assignment_status", "").strip()
    allowed_work_area_ids = _selected_work_area_filter_ids()
    filtered = []
    for person in people_rows:
        active_assignment = person.work_assignment if person.work_assignment and person.work_assignment.active else None
        has_assignment = active_assignment is not None
        if assignment_status == "assigned" and not has_assignment:
            continue
        if assignment_status == "unassigned" and has_assignment:
            continue
        if allowed_work_area_ids is not None:
            if not has_assignment or active_assignment.work_area_unit_id not in allowed_work_area_ids:
                continue
        filtered.append(person)
    return filtered


def _filter_leadership_assignments(assignments):
    leadership_level = request.args.get("leadership_level", "").strip()
    person_id = request.args.get("person_id", "").strip()
    active = request.args.get("active", "").strip()
    allowed_unit_ids = _selected_unit_scope_filter_ids()
    filtered = []
    for assignment in assignments:
        if leadership_level and assignment.leadership_level != leadership_level:
            continue
        if person_id and str(assignment.person_id) != person_id:
            continue
        if active in {"active", "inactive"} and assignment.active != (active == "active"):
            continue
        if allowed_unit_ids is not None and assignment.unit_id not in allowed_unit_ids:
            continue
        filtered.append(assignment)
    return filtered


def _selected_work_area_filter_ids():
    unit = _selected_scope_unit()
    if not unit:
        return None
    return staffing_service.work_area_ids_under(unit)


def _selected_unit_scope_filter_ids():
    unit = _selected_scope_unit()
    if not unit:
        return None
    return staffing_service.unit_ids_under(unit)


def _selected_scope_unit():
    for key in ("work_area_id", "department_id", "operation_id", "sort_id"):
        value = request.args.get(key, "").strip()
        if not value:
            continue
        return db.session.get(StaffingUnit, int(value))
    return None


def _people_return_url(person_id=None):
    query = {
        key: request.form.get(key, "").strip()
        for key in (
            "sort_id",
            "operation_id",
            "department_id",
            "work_area_id",
            "classification",
            "employee_status",
            "active",
            "assignment_status",
            "search",
            "page",
            "per_page",
        )
        if request.form.get(key, "").strip()
    }
    if person_id:
        query["person_id"] = person_id
    return url_for("neostaffing.people", **query)


def _maintain_change_request_activity():
    request_cleanup = change_request_service.cleanup_change_request_retention()
    notification_cleanup = notification_service.maintain_notifications()
    return {
        "request_cleanup": request_cleanup,
        "notification_cleanup": notification_cleanup,
        "changed": bool(
            request_cleanup["changed"] or notification_cleanup["changed"]
        ),
    }


def _change_requests_return_url():
    query = {
        key: request.form.get(key, "").strip()
        for key in ("view", "queue", "search", "person_id")
        if request.form.get(key, "").strip()
    }
    return url_for("neostaffing.change_requests", **query)


def _vacation_year_arg():
    raw_year = request.args.get("year", "").strip()
    if not raw_year:
        return vacation_service.default_vacation_year()
    try:
        return vacation_service.normalize_vacation_year(raw_year)
    except ValueError:
        return vacation_service.default_vacation_year()


def _vacation_program_url(program, vacation_year):
    endpoint = (
        "neostaffing.vacation_union_calendars"
        if str(program or "").strip().casefold() == "union"
        else "neostaffing.vacation_management"
    )
    return url_for(endpoint, year=vacation_year)


def _vacation_year_options(selected_year):
    default_year = vacation_service.default_vacation_year()
    return sorted(
        {
            selected_year - 1,
            selected_year,
            selected_year + 1,
            default_year,
            default_year + 1,
        }
    )


def _render_vacation_union_editor(calendar, vacation_year):
    year = vacation_service.normalize_vacation_year(vacation_year)
    hierarchy = vacation_service.vacation_hierarchy()
    actor = vacation_service.vacation_actor(current_user, hierarchy)
    calendar_type = str(
        (request.form.get("calendar_type") if request.method == "POST" else None)
        or getattr(calendar, "calendar_type", None)
        or request.args.get("type")
        or "official"
    ).casefold()
    operations = [
        unit for unit in hierarchy["units"] if unit.unit_type == "operation"
    ]
    submitted_operation_id = request.form.get("operation_unit_id") if request.method == "POST" else None
    try:
        selected_operation_id = int(
            submitted_operation_id
            or (calendar.operation_unit_id if calendar else 0)
            or request.args.get("operation_id", 0)
        )
    except (TypeError, ValueError):
        selected_operation_id = 0
    if not selected_operation_id and operations:
        selected_operation_id = next(
            (
                operation.id
                for operation in operations
                if vacation_service.operation_has_editable_union_scope(
                    actor,
                    operation.id,
                    hierarchy,
                )
            ),
            operations[0].id,
        )
    if request.method == "POST":
        selected_scope_ids = {
            int(value)
            for value in request.form.getlist("staffing_unit_ids")
            if str(value).isdigit()
        }
    elif calendar:
        selected_scope_ids = {
            scope.staffing_unit_id for scope in calendar.scopes
        }
    else:
        selected_scope_ids = set()

    if calendar and not vacation_service.can_edit_union_calendar(
        calendar, current_user
    ):
        flash("You do not have authority to edit this Union vacation calendar.", "error")
        return redirect(
            url_for(
                "neostaffing.vacation_union_calendars",
                year=calendar.vacation_year,
            )
        )
    if not calendar and not vacation_service.can_create_union_calendar_type(
        calendar_type, current_user
    ):
        flash("You do not have authority to create a Union vacation calendar.", "error")
        return redirect(
            url_for("neostaffing.vacation_union_calendars", year=year)
        )

    operation_trees = [
        {
            "operation": operation,
            "tree": vacation_service.union_scope_tree(
                operation.id,
                selected_scope_ids if operation.id == selected_operation_id else (),
                hierarchy,
            ),
        }
        for operation in operations
    ]
    return render_template(
        "neostaffing/vacation_union_editor.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        calendar=calendar,
        vacation_year=year,
        vacation_years=_vacation_year_options(year),
        operations=operations,
        operation_trees=operation_trees,
        selected_operation_id=selected_operation_id,
        selected_scope_ids=selected_scope_ids,
        calendar_type=calendar_type,
        share_recipients=(
            [share.recipient for share in calendar.shares]
            if calendar and calendar_type == "view_only"
            else []
        ),
        full_scope_label=(
            vacation_service.union_calendar_scope_label(calendar, hierarchy)
            if calendar
            else ""
        ),
    )
