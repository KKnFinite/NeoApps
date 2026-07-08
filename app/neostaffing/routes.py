from functools import wraps

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingPerson, StaffingUnit
from app.neostaffing import bp
from app.services.access_control import get_user_app_role, user_can_access_app, user_has_app_access
from app.services import neostaffing as staffing_service
from app.services.permission_rules import user_can


BOARD_VIEW_PERMISSION = "neostaffing.board.view"
SENIORITY_VIEW_PERMISSION = "neostaffing.seniority.view"
PEOPLE_VIEW_PERMISSION = "neostaffing.people.view"
PEOPLE_EDIT_PERMISSION = "neostaffing.people.edit"
PEOPLE_BULK_ACTIONS_PERMISSION = "neostaffing.people.bulk_actions"
ATTENDANCE_TAKE_PERMISSION = "neostaffing.attendance.take"
ORG_CHART_VIEW_PERMISSION = "neostaffing.org_chart.view"
ORG_CHART_EDIT_STRUCTURE_PERMISSION = "neostaffing.org_chart.edit_structure"
REPORTS_VIEW_PERMISSION = "neostaffing.reports.view"
MANAGEMENT_ASSIGN_PERMISSION = "neostaffing.management.assign"
HIERARCHY_VIEW_PERMISSION = "neostaffing.hierarchy.view"
PLANNED_STAFFING_EDIT_PERMISSION = "neostaffing.planned_staffing.edit"


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


def _handle_attendance():
    management_context = staffing_service.management_attendance_context_for_user(current_user)
    can_edit = user_can(ATTENDANCE_TAKE_PERMISSION)
    if request.method == "POST":
        if not can_edit:
            flash("Taking NeoStaffing attendance requires Operator access.", "error")
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
                attendance_date=request.form.get("attendance_date", ""),
                sort_id=request.form.get("sort_id", ""),
                operation_id=request.form.get("operation_id", ""),
                department_id=request.form.get("department_id", ""),
                work_area_id=request.form.get("work_area_id", ""),
            )
        )
    context = staffing_service.attendance_context(
        {
            "attendance_date": request.args.get("attendance_date", "").strip(),
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
    context = staffing_service.org_chart_context(request.args.get("unit_id", "").strip())
    return render_template(
        "neostaffing/org_chart.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
        can_edit_structure=user_can(ORG_CHART_EDIT_STRUCTURE_PERMISSION),
        can_assign_management=user_can(MANAGEMENT_ASSIGN_PERMISSION),
        can_edit_people=user_can(PEOPLE_EDIT_PERMISSION),
        org_chart=context,
        hierarchy=context["tree"],
        units=context["units"],
        people=staffing_service.people_query(active="active").all(),
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


@bp.route("/app-management/hierarchy/units", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def create_unit():
    return _mutate(
        lambda: staffing_service.create_unit(request.form),
        "Staffing unit added.",
        "neostaffing.org_chart",
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/update", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def update_unit(unit_id):
    unit = _get_unit(unit_id)
    return _mutate(
        lambda: staffing_service.update_unit(unit, request.form),
        "Staffing unit updated.",
        "neostaffing.org_chart",
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/toggle-active", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def toggle_unit_active(unit_id):
    unit = _get_unit(unit_id)

    def toggle():
        unit.active = not unit.active

    return _mutate(toggle, "Staffing unit status updated.", "neostaffing.org_chart")


@bp.route("/app-management/hierarchy/units/<int:unit_id>/delete", methods=["POST"])
@neostaffing_app_required(permission_key=ORG_CHART_EDIT_STRUCTURE_PERMISSION)
def delete_unit(unit_id):
    unit = _get_unit(unit_id)
    return _mutate(
        lambda: staffing_service.delete_unit(unit),
        "Staffing unit deleted.",
        "neostaffing.org_chart",
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

    query = {
        key: request.form.get(key, "").strip()
        for key in ("sort_id", "operation_id", "department_id", "work_area_id")
        if request.form.get(key, "").strip()
    }
    return redirect(url_for("neostaffing.planned_staffing", **query))


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

    def toggle():
        person.active = not person.active

    return _mutate_to_people(toggle, "Person status updated.", person_id)


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
