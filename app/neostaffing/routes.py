from functools import wraps

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    PermissionRule,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
)
from app.neostaffing import bp
from app.services.access_control import get_user_app_role, user_can_access_app, user_has_app_access
from app.services import neostaffing as staffing_service
from app.services import neostaffing_change_requests as change_request_service
from app.services.permission_rules import ensure_default_permission_rules, user_can


BOARD_VIEW_PERMISSION = "neostaffing.board.view"
SENIORITY_VIEW_PERMISSION = "neostaffing.seniority.view"
PEOPLE_VIEW_PERMISSION = "neostaffing.people.view"
PEOPLE_EDIT_PERMISSION = "neostaffing.people.edit"
PEOPLE_BULK_ACTIONS_PERMISSION = "neostaffing.people.bulk_actions"
ATTENDANCE_TAKE_PERMISSION = "neostaffing.attendance.take"
ORG_CHART_VIEW_PERMISSION = "neostaffing.org_chart.view"
ORG_CHART_EDIT_STRUCTURE_PERMISSION = "neostaffing.org_chart.edit_structure"
REPORTS_VIEW_PERMISSION = "neostaffing.reports.view"
VACATION_SELECTION_VIEW_PERMISSION = "neostaffing.vacation_selection.view"
MANAGEMENT_ASSIGN_PERMISSION = "neostaffing.management.assign"
CHANGE_REQUEST_VIEW_PERMISSION = "neostaffing.change_requests.view"
CHANGE_REQUEST_SUBMIT_PERMISSION = "neostaffing.change_requests.submit"
CHANGE_REQUEST_APPROVE_PERMISSION = "neostaffing.change_requests.approve"
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
    classification = request.args.get("classification", "").strip()
    if classification not in {choice[0] for choice in staffing_service.classification_choices()}:
        classification = ""
    active = request.args.get("active", "active").strip() or "active"
    if active not in {"active", "inactive", "all"}:
        active = "active"
    context = staffing_service.seniority_context(
        {
            "sort_id": request.args.get("sort_id", "").strip(),
            "operation_id": request.args.get("operation_id", "").strip(),
            "classification": classification,
            "department_id": request.args.get("department_id", "").strip(),
            "work_area_id": request.args.get("work_area_id", "").strip(),
            "search": request.args.get("search", "").strip(),
            "active": active,
            "include_management": request.args.get("include_management", "").strip(),
        }
    )
    return render_template(
        "neostaffing/seniority.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
        classification_choices=staffing_service.classification_choices(),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        unit_path=staffing_service.unit_path,
        seniority=context,
    )


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
    return render_template(
        "neostaffing/people.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=can_manage,
        can_edit_people=can_edit_people,
        can_bulk_people=can_bulk_people,
        can_assign_management=user_can(MANAGEMENT_ASSIGN_PERMISSION),
        classification_choices=staffing_service.classification_choices(),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
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


@bp.route("/requests")
@neostaffing_app_required(permission_key=CHANGE_REQUEST_VIEW_PERMISSION)
def change_requests():
    cleanup = change_request_service.cleanup_change_request_retention()
    if cleanup["changed"]:
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


@bp.route("/requests/submit", methods=["POST"])
@neostaffing_app_required(permission_key=CHANGE_REQUEST_SUBMIT_PERMISSION)
def submit_change_request():
    try:
        change_request_service.cleanup_change_request_retention()
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
        change_request_service.cleanup_change_request_retention()
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
        change_request_service.cleanup_change_request_retention()
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
        change_request_service.cleanup_change_request_retention()
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
        change_request_service.cleanup_change_request_retention()
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
        change_request_service.cleanup_change_request_retention()
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
    can_edit = user_can(ATTENDANCE_TAKE_PERMISSION)
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
            )
        )
    context = staffing_service.attendance_context(
        {
            "sort_id": request.args.get("sort_id", "").strip(),
            "operation_id": request.args.get("operation_id", "").strip(),
            "department_id": request.args.get("department_id", "").strip(),
            "work_area_id": request.args.get("work_area_id", "").strip(),
        },
        current_user,
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
    context = staffing_service.reports_context(
        {
            "report_type": request.args.get("report_type", "").strip(),
            "sort_id": request.args.get("sort_id", "").strip(),
            "operation_id": request.args.get("operation_id", "").strip(),
            "department_id": request.args.get("department_id", "").strip(),
            "work_area_id": request.args.get("work_area_id", "").strip(),
            "classification": request.args.get("classification", "").strip(),
            "employee_status": request.args.get("employee_status", "").strip(),
            "assignment_status": request.args.get("assignment_status", "").strip(),
            "attendance_date": request.args.get("attendance_date", "").strip(),
            "attendance_status": request.args.get("attendance_status", "").strip(),
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


@bp.route("/vacation-selection")
@neostaffing_app_required(permission_key=VACATION_SELECTION_VIEW_PERMISSION)
def vacation_selection():
    return render_template(
        "neostaffing/vacation_selection.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
    )


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
        can_assign_management=user_can(MANAGEMENT_ASSIGN_PERMISSION),
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
    return _mutate(
        lambda: staffing_service.update_unit(unit, request.form),
        "Staffing unit updated.",
        "neostaffing.org_chart",
        _org_chart_return_values(unit.id),
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
        initial_work_area_id = request.form.get("initial_work_area_unit_id", "").strip()
        if initial_work_area_id:
            staffing_service.assign_work_area(person, _get_unit(initial_work_area_id))
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(getattr(error, "orig", None) or error), "error")
    else:
        flash("Person added.", "success")
    return redirect(_people_return_url(person.id if person else None))


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
    return_unit_id = request.form.get("return_unit_id", "").strip()
    return_people = request.form.get("return_people", "").strip()
    if return_people:
        redirect_endpoint = "neostaffing.people"
        redirect_values = {
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
    else:
        redirect_endpoint = "neostaffing.org_chart" if return_unit_id else "neostaffing.management_assignments"
        redirect_values = {"unit_id": return_unit_id} if return_unit_id else None
    return _mutate(
        lambda: staffing_service.create_leadership_assignment(
            _get_person(request.form.get("person_id")),
            _get_unit(request.form.get("unit_id")),
            request.form.get("leadership_level") or None,
        ),
        "Management assignment added.",
        redirect_endpoint,
        redirect_values,
    )


@bp.route("/app-management/management-assignments/<int:assignment_id>/delete", methods=["POST"])
@neostaffing_app_required(permission_key=MANAGEMENT_ASSIGN_PERMISSION)
def delete_management_assignment(assignment_id):
    assignment = db.session.get(StaffingLeadershipAssignment, assignment_id)
    if not assignment:
        flash("Management assignment was not found.", "error")
        return redirect(url_for("neostaffing.management_assignments"))
    return_unit_id = request.form.get("return_unit_id", "").strip()
    redirect_endpoint = "neostaffing.org_chart" if return_unit_id else "neostaffing.management_assignments"
    redirect_values = {"unit_id": return_unit_id} if return_unit_id else None
    return _mutate(
        lambda: staffing_service.delete_leadership_assignment(assignment),
        "Management assignment deactivated.",
        redirect_endpoint,
        redirect_values,
    )


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


def _change_requests_return_url():
    query = {
        key: request.form.get(key, "").strip()
        for key in ("view", "queue", "search", "person_id")
        if request.form.get(key, "").strip()
    }
    return url_for("neostaffing.change_requests", **query)
