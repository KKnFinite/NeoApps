from functools import wraps

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingPerson, StaffingUnit, StaffingWorkAssignment
from app.neostaffing import bp
from app.services.access_control import get_user_app_role, user_can_access_app, user_has_app_access
from app.services import neostaffing as staffing_service


def neostaffing_app_required(minimum_role="watcher"):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
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
@neostaffing_app_required()
def index():
    role = get_user_app_role(current_user, "neostaffing")
    return render_template(
        "neostaffing/index.html",
        app_role=role,
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
        dashboard=staffing_service.dashboard_context(),
    )


@bp.route("/")
@login_required
def index_slash():
    return redirect(url_for("neostaffing.index"))


@bp.route("/seniority")
@neostaffing_app_required()
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


@bp.route("/app-management")
@neostaffing_app_required(minimum_role="master")
def app_management():
    return render_template(
        "neostaffing/app_management.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        counts={
            "people": StaffingPerson.query.filter_by(active=True).count(),
            "units": StaffingUnit.query.filter_by(active=True).count(),
            "work_areas": StaffingUnit.query.filter_by(unit_type="work_area", active=True).count(),
            "work_assignments": StaffingWorkAssignment.query.filter_by(active=True).count(),
            "leadership": StaffingLeadershipAssignment.query.filter_by(active=True).count(),
        },
    )


@bp.route("/app-management/hierarchy")
@neostaffing_app_required(minimum_role="master")
def hierarchy():
    return render_template(
        "neostaffing/hierarchy.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        hierarchy=staffing_service.staffing_hierarchy_tree(),
        units=StaffingUnit.query.order_by(
            StaffingUnit.unit_type,
            StaffingUnit.display_order,
            StaffingUnit.name,
        ).all(),
        sorts=staffing_service.selectable_parent_units("operation"),
        operations=staffing_service.selectable_parent_units("department"),
        departments=staffing_service.selectable_parent_units("work_area"),
        unit_type_labels=staffing_service.UNIT_TYPE_LABELS,
    )


@bp.route("/app-management/hierarchy/units", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def create_unit():
    return _mutate(
        lambda: staffing_service.create_unit(request.form),
        "Staffing unit added.",
        "neostaffing.hierarchy",
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/update", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def update_unit(unit_id):
    unit = _get_unit(unit_id)
    return _mutate(
        lambda: staffing_service.update_unit(unit, request.form),
        "Staffing unit updated.",
        "neostaffing.hierarchy",
    )


@bp.route("/app-management/hierarchy/units/<int:unit_id>/toggle-active", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def toggle_unit_active(unit_id):
    unit = _get_unit(unit_id)

    def toggle():
        unit.active = not unit.active

    return _mutate(toggle, "Staffing unit status updated.", "neostaffing.hierarchy")


@bp.route("/app-management/hierarchy/units/<int:unit_id>/delete", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def delete_unit(unit_id):
    unit = _get_unit(unit_id)
    return _mutate(
        lambda: staffing_service.delete_unit(unit),
        "Staffing unit deleted.",
        "neostaffing.hierarchy",
    )


@bp.route("/app-management/people")
@neostaffing_app_required(minimum_role="master")
def people():
    search = request.args.get("search", "").strip()
    classification = request.args.get("classification", "").strip()
    active = request.args.get("active", "").strip()
    if classification not in {choice[0] for choice in staffing_service.classification_choices()}:
        classification = ""
    people_rows = staffing_service.people_query(
        search=search,
        classification=classification or None,
        active=active or None,
    ).all()
    return render_template(
        "neostaffing/people.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        people=people_rows,
        classification_choices=staffing_service.classification_choices(),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        filters={"search": search, "classification": classification, "active": active},
    )


@bp.route("/app-management/people", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def create_person():
    return _mutate(
        lambda: staffing_service.create_person(request.form),
        "Person added.",
        "neostaffing.people",
    )


@bp.route("/app-management/people/<int:person_id>/update", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def update_person(person_id):
    person = _get_person(person_id)
    return _mutate(
        lambda: staffing_service.update_person(person, request.form),
        "Person updated.",
        "neostaffing.people",
    )


@bp.route("/app-management/people/<int:person_id>/toggle-active", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def toggle_person_active(person_id):
    person = _get_person(person_id)

    def toggle():
        person.active = not person.active

    return _mutate(toggle, "Person status updated.", "neostaffing.people")


@bp.route("/app-management/people/<int:person_id>/delete", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def delete_person(person_id):
    person = _get_person(person_id)
    return _mutate(
        lambda: staffing_service.delete_person(person),
        "Person deleted.",
        "neostaffing.people",
    )


@bp.route("/app-management/work-assignments")
@neostaffing_app_required(minimum_role="master")
def work_assignments():
    people_rows = staffing_service.people_query(
        classification=request.args.get("classification") or None,
        active=request.args.get("active") or None,
    ).all()
    people_rows = _filter_people_for_work_assignment_page(people_rows)
    work_areas = staffing_service.work_area_units()
    return render_template(
        "neostaffing/work_assignments.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        people=people_rows,
        work_areas=work_areas,
        sorts=staffing_service.units_by_type("sort"),
        operations=staffing_service.units_by_type("operation"),
        departments=staffing_service.units_by_type("department"),
        classification_choices=staffing_service.classification_choices(),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        non_management_classifications=staffing_service.NON_MANAGEMENT_CLASSIFICATIONS,
        unit_path=staffing_service.unit_path,
        filters={
            "classification": request.args.get("classification", ""),
            "active": request.args.get("active", ""),
            "assignment_status": request.args.get("assignment_status", ""),
            "sort_id": request.args.get("sort_id", ""),
            "operation_id": request.args.get("operation_id", ""),
            "department_id": request.args.get("department_id", ""),
            "work_area_id": request.args.get("work_area_id", ""),
        },
    )


@bp.route("/app-management/work-assignments/assign", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
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
@neostaffing_app_required(minimum_role="master")
def clear_work_assignment(person_id):
    person = _get_person(person_id)
    return _mutate(
        lambda: staffing_service.clear_work_assignment(person),
        "Work assignment deactivated.",
        "neostaffing.work_assignments",
    )


@bp.route("/app-management/management-assignments")
@neostaffing_app_required(minimum_role="master")
def management_assignments():
    assignments = (
        StaffingLeadershipAssignment.query.join(StaffingPerson)
        .join(StaffingUnit)
        .order_by(StaffingPerson.last_name, StaffingPerson.first_name, StaffingUnit.unit_type)
        .all()
    )
    assignments = _filter_leadership_assignments(assignments)
    people_rows = staffing_service.people_query(active=request.args.get("active") or None).all()
    units = StaffingUnit.query.order_by(StaffingUnit.unit_type, StaffingUnit.display_order, StaffingUnit.name).all()
    return render_template(
        "neostaffing/management_assignments.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
        assignments=assignments,
        people=people_rows,
        units=units,
        sorts=staffing_service.units_by_type("sort"),
        operations=staffing_service.units_by_type("operation"),
        departments=staffing_service.units_by_type("department"),
        work_areas=staffing_service.units_by_type("work_area"),
        classification_labels=staffing_service.CLASSIFICATION_LABELS,
        leadership_level_labels=staffing_service.LEADERSHIP_LEVEL_LABELS,
        unit_type_labels=staffing_service.UNIT_TYPE_LABELS,
        unit_path=staffing_service.unit_path,
        filters={
            "leadership_level": request.args.get("leadership_level", ""),
            "person_id": request.args.get("person_id", ""),
            "active": request.args.get("active", ""),
            "sort_id": request.args.get("sort_id", ""),
            "operation_id": request.args.get("operation_id", ""),
            "department_id": request.args.get("department_id", ""),
            "work_area_id": request.args.get("work_area_id", ""),
        },
    )


@bp.route("/app-management/management-assignments", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def create_management_assignment():
    return _mutate(
        lambda: staffing_service.create_leadership_assignment(
            _get_person(request.form.get("person_id")),
            _get_unit(request.form.get("unit_id")),
            request.form.get("leadership_level") or None,
        ),
        "Management assignment added.",
        "neostaffing.management_assignments",
    )


@bp.route("/app-management/management-assignments/<int:assignment_id>/delete", methods=["POST"])
@neostaffing_app_required(minimum_role="master")
def delete_management_assignment(assignment_id):
    assignment = db.session.get(StaffingLeadershipAssignment, assignment_id)
    if not assignment:
        flash("Management assignment was not found.", "error")
        return redirect(url_for("neostaffing.management_assignments"))
    return _mutate(
        lambda: staffing_service.delete_leadership_assignment(assignment),
        "Management assignment deactivated.",
        "neostaffing.management_assignments",
    )


def _mutate(callback, success_message, redirect_endpoint):
    try:
        callback()
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        message = str(getattr(error, "orig", None) or error)
        flash(message, "error")
    else:
        flash(success_message, "success")
    return redirect(url_for(redirect_endpoint))


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
