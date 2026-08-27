"""NeoStaffing PT Supervisor employee change-request workflow."""

from datetime import date, datetime, timedelta
import json

from sqlalchemy import func, or_

from app.extensions import db
from app.models import (
    StaffingChangeRequest,
    StaffingChangeRequestEvent,
    StaffingChangeRequestItem,
    StaffingLeadershipAssignment,
    StaffingNotification,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingTwentyCAffiliation,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.models.staffing_person import STAFFING_EMPLOYEE_STATUSES
from app.models.user import ROLE_LEVELS
from app.services.access_control import get_user_app_role
from app.services.neostaffing import (
    CLASSIFICATION_LABELS,
    EMPLOYEE_STATUS_LABELS,
    NON_MANAGEMENT_CLASSIFICATIONS,
    WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS,
)
from app.services.permission_rules import user_can
from app.services import neostaffing_notifications as notification_service


CHANGE_REQUEST_VIEW_PERMISSION = "neostaffing.change_requests.view"
CHANGE_REQUEST_SUBMIT_PERMISSION = "neostaffing.change_requests.submit"
CHANGE_REQUEST_APPROVE_PERMISSION = "neostaffing.change_requests.approve"

CHANGE_REQUEST_FIELD_LABELS = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "seniority_date": "Seniority Date",
    "employee_status": "Employee Status",
    "classification": "Classification",
    "work_area_unit_id": "Work Area",
}
CHANGE_REQUEST_STATUS_LABELS = {
    "pending": "Pending",
    "approved": "Approved",
    "denied": "Denied",
    "withdrawn": "Withdrawn",
    "superseded": "Superseded",
}
REQUESTABLE_FORM_FIELDS = {
    "first_name": "requested_first_name",
    "last_name": "requested_last_name",
    "seniority_date": "requested_seniority_date",
    "employee_status": "requested_employee_status",
    "classification": "requested_classification",
    "work_area_unit_id": "requested_work_area_unit_id",
}
APPROVER_CLASSIFICATIONS = {
    "full_time_supervisor",
    "twenty_c_full_time_supervisor",
    "manager",
    "division_manager",
}
REQUEST_HISTORY_DAYS = 14
REQUEST_LIFETIME_DAYS = 30
REQUEST_OVERDUE_HOURS = 48


def change_request_item_revision(item):
    updated_at = item.updated_at or item.created_at
    timestamp = updated_at.isoformat(timespec="microseconds") if updated_at else ""
    return f"{item.id}:{timestamp}:{item.status}"


def can_submit_change_requests(user):
    if not user_can(CHANGE_REQUEST_SUBMIT_PERMISSION, user):
        return False
    app_role = get_user_app_role(user, "neostaffing")
    person = _staffing_person_for_user(user)
    return _can_submit_with_context(user, app_role, person)


def can_approve_change_requests(user):
    if not user_can(CHANGE_REQUEST_APPROVE_PERMISSION, user):
        return False
    app_role = get_user_app_role(user, "neostaffing")
    person = _staffing_person_for_user(user)
    return _can_approve_with_context(user, app_role, person)


def change_request_context(filters, user):
    filters = filters or {}
    now = datetime.utcnow()
    view = str(filters.get("view") or "active").strip().lower()
    if view not in {"active", "history", "all"}:
        view = "active"

    units = StaffingUnit.query.order_by(
        StaffingUnit.display_order,
        StaffingUnit.name,
        StaffingUnit.id,
    ).all()
    units_by_id = {unit.id: unit for unit in units}
    active_work_areas = [
        unit for unit in units if unit.active and unit.unit_type == "work_area"
    ]

    people = StaffingPerson.query.order_by(
        StaffingPerson.last_name,
        StaffingPerson.first_name,
        StaffingPerson.employee_id,
        StaffingPerson.id,
    ).all()
    people_by_id = {person.id: person for person in people}
    current_person = _person_for_user_from_rows(user, people)

    work_assignments = StaffingWorkAssignment.query.filter_by(active=True).all()
    assignments_by_person = {
        assignment.person_id: assignment for assignment in work_assignments
    }
    leadership_assignments = StaffingLeadershipAssignment.query.filter_by(
        active=True
    ).all()
    leadership_by_person = {}
    for assignment in leadership_assignments:
        leadership_by_person.setdefault(assignment.person_id, []).append(assignment)
    affiliations = StaffingTwentyCAffiliation.query.filter_by(active=True).all()
    authority_unit_ids_by_person = _management_authority_unit_ids_from_rows(
        leadership_by_person, affiliations
    )

    requests_query = StaffingChangeRequest.query
    if view == "active":
        requests_query = requests_query.filter(
            StaffingChangeRequest.status == "pending"
        )
    elif view == "history":
        requests_query = requests_query.filter(
            StaffingChangeRequest.status == "completed",
            StaffingChangeRequest.completed_at
            >= now - timedelta(days=REQUEST_HISTORY_DAYS),
        )
    else:
        requests_query = requests_query.filter(
            or_(
                StaffingChangeRequest.status == "pending",
                StaffingChangeRequest.completed_at
                >= now - timedelta(days=REQUEST_HISTORY_DAYS),
            )
        )
    requests = requests_query.order_by(
        StaffingChangeRequest.submitted_at,
        StaffingChangeRequest.id,
    ).all()
    request_ids = {row.id for row in requests}
    items = StaffingChangeRequestItem.query.filter(
        StaffingChangeRequestItem.request_id.in_(request_ids or {-1})
    ).order_by(StaffingChangeRequestItem.id).all()
    items_by_request = {}
    for item in items:
        items_by_request.setdefault(item.request_id, []).append(item)

    user_ids = {
        row.submitted_by_user_id for row in requests if row.submitted_by_user_id
    } | {item.decided_by_user_id for item in items if item.decided_by_user_id}
    users_by_id = {
        row.id: row
        for row in User.query.filter(User.id.in_(user_ids or {-1})).all()
    }

    app_role = get_user_app_role(user, "neostaffing")
    can_submit = bool(
        user_can(CHANGE_REQUEST_SUBMIT_PERMISSION, user)
        and _can_submit_with_context(user, app_role, current_person)
    )
    can_approve = bool(
        user_can(CHANGE_REQUEST_APPROVE_PERMISSION, user)
        and _can_approve_with_context(user, app_role, current_person)
    )
    default_scope = _default_queue_scope(current_person)
    queue_scope = str(filters.get("queue") or default_scope).strip().lower()
    if queue_scope not in {"routed", "purview", "unassigned", "all"}:
        queue_scope = default_scope
    search = str(filters.get("search") or "").strip().lower()

    rows = []
    for change_request in requests:
        person = people_by_id.get(change_request.person_id)
        if not person:
            continue
        routed_ids = _decode_person_ids(
            change_request.routed_approver_person_ids_json
        )
        if not _request_matches_queue(
            change_request,
            queue_scope,
            current_person,
            routed_ids,
            authority_unit_ids_by_person,
            units_by_id,
        ):
            continue
        if search and search not in (
            f"{person.full_name} {person.employee_id} {change_request.id}"
        ).lower():
            continue
        overdue = bool(
            change_request.status == "pending"
            and change_request.submitted_at
            <= now - timedelta(hours=REQUEST_OVERDUE_HOURS)
        )
        field_rows = []
        for item in items_by_request.get(change_request.id, []):
            original_value = _decode_value(item.original_value_json)
            requested_value = _decode_value(item.requested_value_json)
            field_rows.append(
                {
                    "item": item,
                    "label": CHANGE_REQUEST_FIELD_LABELS[item.field_name],
                    "original": _display_value(
                        item.field_name,
                        original_value,
                        units_by_id,
                    ),
                    "requested": _display_value(
                        item.field_name,
                        requested_value,
                        units_by_id,
                    ),
                    "status_label": CHANGE_REQUEST_STATUS_LABELS[item.status],
                    "revision": change_request_item_revision(item),
                    "decided_by": users_by_id.get(item.decided_by_user_id),
                    "can_approve": can_approve and item.status == "pending",
                    "can_withdraw": (
                        item.status == "pending"
                        and change_request.submitted_by_user_id == user.id
                    ),
                    "can_reverse": (
                        can_approve
                        and item.status in {"approved", "denied"}
                        and item.decided_at
                        and item.decided_at
                        >= now - timedelta(days=REQUEST_HISTORY_DAYS)
                    ),
                }
            )
        rows.append(
            {
                "request": change_request,
                "person": person,
                "submitter": users_by_id.get(change_request.submitted_by_user_id),
                "source_work_area": units_by_id.get(
                    change_request.source_work_area_unit_id
                ),
                "destination_work_area": units_by_id.get(
                    change_request.destination_work_area_unit_id
                ),
                "routed_people": [
                    people_by_id[person_id]
                    for person_id in routed_ids
                    if person_id in people_by_id
                ],
                "fields": field_rows,
                "overdue": overdue,
                "can_bulk_approve": can_approve
                and any(item.status == "pending" for item in items_by_request.get(change_request.id, [])),
                "can_bulk_withdraw": (
                    change_request.submitted_by_user_id == user.id
                    and any(item.status == "pending" for item in items_by_request.get(change_request.id, []))
                ),
            }
        )

    rows.sort(
        key=lambda row: (
            row["request"].status != "pending",
            not row["overdue"],
            row["request"].submitted_at,
            row["request"].id,
        )
    )

    candidates = _submission_candidates(
        people,
        assignments_by_person,
        leadership_by_person,
        current_person,
        app_role,
        user,
    ) if can_submit else []
    selected_person = None
    try:
        selected_person = people_by_id.get(int(filters.get("person_id") or 0))
    except (TypeError, ValueError):
        selected_person = None
    if selected_person not in candidates:
        selected_person = candidates[0] if len(candidates) == 1 else None
    selected_assignment = (
        assignments_by_person.get(selected_person.id) if selected_person else None
    )
    selected_values = None
    if selected_person:
        selected_values = {
            "first_name": selected_person.first_name,
            "last_name": selected_person.last_name,
            "seniority_date": selected_person.seniority_date,
            "employee_status": selected_person.employee_status,
            "classification": selected_person.classification,
            "work_area": units_by_id.get(
                selected_assignment.work_area_unit_id
            ) if selected_assignment else None,
        }

    return {
        "rows": rows,
        "filters": {
            "view": view,
            "queue": queue_scope,
            "search": str(filters.get("search") or "").strip(),
            "person_id": str(filters.get("person_id") or "").strip(),
        },
        "current_person": current_person,
        "app_role": app_role,
        "can_submit": can_submit,
        "can_approve": can_approve,
        "candidates": candidates,
        "selected_person": selected_person,
        "selected_values": selected_values,
        "work_areas": active_work_areas,
        "employee_status_choices": [
            (value, EMPLOYEE_STATUS_LABELS[value])
            for value in STAFFING_EMPLOYEE_STATUSES
        ],
        "classification_choices": [
            (value, CLASSIFICATION_LABELS[value])
            for value in sorted(
                WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS,
                key=lambda value: CLASSIFICATION_LABELS[value],
            )
        ],
        "unassigned_count": sum(
            1 for row in requests if row.unassigned_approval and row.status == "pending"
        ),
        "default_queue": default_scope,
    }


def submit_change_request(values, user):
    if not can_submit_change_requests(user):
        raise ValueError("You do not have authority to submit employee change requests.")
    app_role = get_user_app_role(user, "neostaffing")
    is_grandmaster = _is_grandmaster(user, app_role)
    submitter_person = _staffing_person_for_user(user)
    if not is_grandmaster and (
        not submitter_person
        or submitter_person.classification != "part_time_supervisor"
    ):
        raise ValueError("Only a PT Supervisor may submit this request.")

    try:
        person_id = int(values.get("person_id") or 0)
    except (TypeError, ValueError):
        raise ValueError("Select an employee.")
    person = StaffingPerson.query.filter_by(id=person_id, active=True).with_for_update().first()
    if not person or person.classification not in NON_MANAGEMENT_CLASSIFICATIONS:
        raise ValueError("Select an active non-management employee.")

    assignment = StaffingWorkAssignment.query.filter_by(
        person_id=person.id,
        active=True,
    ).with_for_update().first()
    if not is_grandmaster and ROLE_LEVELS.get(app_role, 0) < ROLE_LEVELS["simulator"]:
        owned_area_ids = {
            row.unit_id
            for row in StaffingLeadershipAssignment.query.filter_by(
                person_id=submitter_person.id,
                leadership_level="work_area",
                active=True,
            ).all()
        }
        if not assignment or assignment.work_area_unit_id not in owned_area_ids:
            raise ValueError("This employee is outside your attendance and staffing area.")

    pending_fields = {
        row.field_name
        for row in StaffingChangeRequestItem.query.filter_by(
            person_id=person.id,
            status="pending",
        ).with_for_update().all()
    }
    parsed_values = _parse_requested_values(values)
    destination_work_area = parsed_values.get("work_area_unit_id", _NOT_SUBMITTED)
    units_by_id = {}
    if destination_work_area is not _NOT_SUBMITTED and destination_work_area is not None:
        destination = db.session.get(StaffingUnit, destination_work_area)
        if not destination or not destination.active or destination.unit_type != "work_area":
            raise ValueError("Select an active Work Area.")
        units_by_id[destination.id] = destination

    current_values = {
        "first_name": person.first_name,
        "last_name": person.last_name,
        "seniority_date": person.seniority_date.isoformat(),
        "employee_status": person.employee_status,
        "classification": person.classification,
        "work_area_unit_id": assignment.work_area_unit_id if assignment else None,
    }
    changed_values = {
        field_name: requested_value
        for field_name, requested_value in parsed_values.items()
        if requested_value is not _NOT_SUBMITTED
        and requested_value != current_values[field_name]
    }
    if not changed_values:
        raise ValueError("Enter at least one employee change.")
    duplicate_fields = sorted(set(changed_values) & pending_fields)
    if duplicate_fields:
        labels = ", ".join(CHANGE_REQUEST_FIELD_LABELS[field] for field in duplicate_fields)
        raise ValueError(f"A Pending request already exists for: {labels}.")

    source_area_id = assignment.work_area_unit_id if assignment else None
    destination_area_id = (
        changed_values.get("work_area_unit_id")
        if "work_area_unit_id" in changed_values
        else None
    )
    routed_ids = _route_approver_person_ids(
        source_area_id,
        destination_area_id,
        submitter_person,
    )
    now = datetime.utcnow()
    change_request = StaffingChangeRequest(
        person_id=person.id,
        submitted_by_user_id=user.id,
        submitted_by_person_id=submitter_person.id if submitter_person else None,
        source_work_area_unit_id=source_area_id,
        destination_work_area_unit_id=destination_area_id,
        routed_approver_person_ids_json=json.dumps(routed_ids),
        unassigned_approval=not bool(routed_ids),
        request_note=_optional_text(values.get("request_note")),
        status="pending",
        submitted_at=now,
        created_at=now,
        updated_at=now,
    )
    db.session.add(change_request)
    db.session.flush()

    items = []
    for field_name, requested_value in changed_values.items():
        item = StaffingChangeRequestItem(
            request_id=change_request.id,
            person_id=person.id,
            field_name=field_name,
            original_value_json=_encode_value(current_values[field_name]),
            requested_value_json=_encode_value(requested_value),
            status="pending",
            created_at=now,
            updated_at=now,
        )
        db.session.add(item)
        items.append(item)
    db.session.flush()
    for item in items:
        _add_event(
            change_request,
            item,
            user,
            "submitted",
            None,
            "pending",
            change_request.request_note,
        )

    if is_grandmaster:
        _decide_locked_items(
            change_request,
            person,
            assignment,
            items,
            "approve",
            None,
            user,
        )
    notification_service.notify_new_requests([change_request])
    db.session.flush()
    return change_request


def submit_bulk_change_requests(packages, user):
    """Create separate employee requests in bounded queries.

    Pending field conflicts are returned per field so one conflict does not block
    otherwise valid staged employee changes.
    """
    if not can_submit_change_requests(user):
        raise ValueError("You do not have authority to submit employee change requests.")
    app_role = get_user_app_role(user, "neostaffing")
    is_grandmaster = _is_grandmaster(user, app_role)
    submitter_person = _staffing_person_for_user(user)
    if not is_grandmaster and (
        not submitter_person
        or submitter_person.classification != "part_time_supervisor"
    ):
        raise ValueError("Only a PT Supervisor may submit these employee changes.")

    normalized_packages = []
    for package in packages or []:
        try:
            person_id = int(package.get("person_id") or 0)
        except (TypeError, ValueError):
            continue
        if person_id > 0:
            normalized_packages.append(
                {
                    "person_id": person_id,
                    "changes": dict(package.get("changes") or {}),
                    "request_note": _optional_text(package.get("request_note")),
                }
            )
    person_ids = {row["person_id"] for row in normalized_packages}
    people = (
        StaffingPerson.query.filter(StaffingPerson.id.in_(person_ids or {-1}))
        .order_by(StaffingPerson.id)
        .with_for_update()
        .all()
    )
    people_by_id = {row.id: row for row in people}
    assignments = (
        StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(person_ids or {-1})
        )
        .order_by(StaffingWorkAssignment.id)
        .with_for_update()
        .all()
    )
    assignments_by_person = {row.person_id: row for row in assignments if row.active}
    pending_items = (
        StaffingChangeRequestItem.query.filter(
            StaffingChangeRequestItem.person_id.in_(person_ids or {-1}),
            StaffingChangeRequestItem.status == "pending",
        )
        .order_by(StaffingChangeRequestItem.id)
        .with_for_update()
        .all()
    )
    pending_fields_by_person = {}
    for item in pending_items:
        pending_fields_by_person.setdefault(item.person_id, set()).add(item.field_name)

    units = StaffingUnit.query.order_by(StaffingUnit.id).all()
    units_by_id = {row.id: row for row in units}
    leadership = StaffingLeadershipAssignment.query.filter_by(active=True).all()
    affiliations = StaffingTwentyCAffiliation.query.filter_by(active=True).all()
    management_people = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        StaffingPerson.classification.in_(APPROVER_CLASSIFICATIONS | {"part_time_supervisor"}),
    ).all()
    management_by_id = {row.id: row for row in management_people}
    if submitter_person:
        management_by_id[submitter_person.id] = submitter_person
    relationship = None
    if submitter_person:
        relationship = StaffingReportingRelationship.query.filter_by(
            person_id=submitter_person.id,
            active=True,
        ).first()

    owned_area_ids = {
        row.unit_id
        for row in leadership
        if submitter_person
        and row.person_id == submitter_person.id
        and row.leadership_level == "work_area"
    }
    can_cross_area = bool(
        is_grandmaster
        or ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["simulator"]
    )
    now = datetime.utcnow()
    requests = []
    submitted_fields = []
    blocked = []
    for package in normalized_packages:
        person = people_by_id.get(package["person_id"])
        if (
            not person
            or not person.active
            or person.classification not in NON_MANAGEMENT_CLASSIFICATIONS
        ):
            blocked.append(
                {
                    "person_id": package["person_id"],
                    "field": None,
                    "reason": "Only active non-management employees can use the current request workflow.",
                }
            )
            continue
        assignment = assignments_by_person.get(person.id)
        if not can_cross_area and (
            not assignment or assignment.work_area_unit_id not in owned_area_ids
        ):
            blocked.append(
                {
                    "person_id": person.id,
                    "field": None,
                    "reason": "This employee is outside your normal staffing area.",
                }
            )
            continue

        current_values = {
            "first_name": person.first_name,
            "last_name": person.last_name,
            "seniority_date": person.seniority_date.isoformat(),
            "employee_status": person.employee_status,
            "classification": person.classification,
            "work_area_unit_id": assignment.work_area_unit_id if assignment else None,
        }
        changed_values = {}
        for field_name, raw_value in package["changes"].items():
            if field_name not in CHANGE_REQUEST_FIELD_LABELS:
                blocked.append(
                    {
                        "person_id": person.id,
                        "field": field_name,
                        "reason": "This field is not supported by the current request workflow.",
                    }
                )
                continue
            requested_value = _normalize_bulk_requested_value(
                field_name,
                raw_value,
                units_by_id,
            )
            if requested_value == current_values[field_name]:
                continue
            if field_name in pending_fields_by_person.get(person.id, set()):
                blocked.append(
                    {
                        "person_id": person.id,
                        "field": field_name,
                        "reason": (
                            "A Pending request already exists for "
                            f"{CHANGE_REQUEST_FIELD_LABELS[field_name]}."
                        ),
                    }
                )
                continue
            changed_values[field_name] = requested_value
        if not changed_values:
            continue

        source_area_id = assignment.work_area_unit_id if assignment else None
        destination_area_id = (
            changed_values.get("work_area_unit_id")
            if "work_area_unit_id" in changed_values
            else None
        )
        routed_ids = _route_approvers_from_loaded_rows(
            source_area_id,
            destination_area_id,
            submitter_person,
            units_by_id,
            leadership,
            management_by_id,
            relationship,
            affiliations,
        )
        change_request = StaffingChangeRequest(
            person_id=person.id,
            submitted_by_user_id=user.id,
            submitted_by_person_id=submitter_person.id if submitter_person else None,
            source_work_area_unit_id=source_area_id,
            destination_work_area_unit_id=destination_area_id,
            routed_approver_person_ids_json=json.dumps(routed_ids),
            unassigned_approval=not bool(routed_ids),
            request_note=package["request_note"],
            status="pending",
            submitted_at=now,
            created_at=now,
            updated_at=now,
        )
        db.session.add(change_request)
        db.session.flush()
        items = []
        for field_name, requested_value in changed_values.items():
            item = StaffingChangeRequestItem(
                request_id=change_request.id,
                person_id=person.id,
                field_name=field_name,
                original_value_json=_encode_value(current_values[field_name]),
                requested_value_json=_encode_value(requested_value),
                status="pending",
                created_at=now,
                updated_at=now,
            )
            db.session.add(item)
            items.append(item)
            submitted_fields.append(
                {"person_id": person.id, "field": field_name}
            )
        db.session.flush()
        for item in items:
            _add_event(
                change_request,
                item,
                user,
                "submitted",
                None,
                "pending",
                package["request_note"],
            )
        requests.append(change_request)
        pending_fields_by_person.setdefault(person.id, set()).update(changed_values)

    notification_service.notify_new_requests(requests)
    db.session.flush()
    return {
        "requests": requests,
        "submitted_fields": submitted_fields,
        "blocked": blocked,
    }


def decide_change_request_item(item_id, action, reason, user, expected_revision):
    _require_approver(user)
    request_id = db.session.query(StaffingChangeRequestItem.request_id).filter_by(
        id=int(item_id)
    ).scalar()
    if not request_id:
        raise ValueError("The request field was not found.")
    change_request, person, assignment, all_items = _locked_request_state(request_id)
    item = next((row for row in all_items if row.id == int(item_id)), None)
    if not item:
        raise ValueError("The request field was not found.")
    if not str(expected_revision or "").strip():
        raise ValueError("Request field version is required. Reload Requests.")
    if change_request_item_revision(item) != str(expected_revision).strip():
        raise ValueError("This request field changed while you were editing. Reload Requests.")
    if item.status != "pending":
        raise ValueError("This request field has already been decided.")
    return _decide_locked_items(
        change_request,
        person,
        assignment,
        [item],
        action,
        reason,
        user,
        all_items=all_items,
    )


def decide_change_request_remaining(request_id, action, reason, user):
    _require_approver(user)
    change_request, person, assignment, all_items = _locked_request_state(request_id)
    pending_items = [item for item in all_items if item.status == "pending"]
    if not pending_items:
        raise ValueError("This request has no Pending fields.")
    return _decide_locked_items(
        change_request,
        person,
        assignment,
        pending_items,
        action,
        reason,
        user,
        all_items=all_items,
    )


def withdraw_change_request_item(item_id, reason, user, expected_revision):
    request_id = db.session.query(StaffingChangeRequestItem.request_id).filter_by(
        id=int(item_id)
    ).scalar()
    if not request_id:
        raise ValueError("The request field was not found.")
    change_request, _person, _assignment, all_items = _locked_request_state(request_id)
    item = next((row for row in all_items if row.id == int(item_id)), None)
    _validate_withdrawal(change_request, item, user, expected_revision)
    _set_item_status(item, "withdrawn", user, reason)
    _add_event(
        change_request,
        item,
        user,
        "withdrawn",
        "pending",
        "withdrawn",
        _optional_text(reason),
    )
    completed = _refresh_request_completion(change_request, all_items)
    notification_service.notify_submitter_updates_batch(
        [
            {
                "request": change_request,
                "items": all_items,
                "completed": completed,
            }
        ]
    )
    db.session.flush()
    return item


def withdraw_change_request_remaining(request_id, reason, user):
    change_request, _person, _assignment, all_items = _locked_request_state(request_id)
    if change_request.submitted_by_user_id != user.id:
        raise ValueError("Only the submitter may withdraw this request.")
    pending_items = [item for item in all_items if item.status == "pending"]
    if not pending_items:
        raise ValueError("This request has no Pending fields.")
    for item in pending_items:
        _set_item_status(item, "withdrawn", user, reason)
        _add_event(
            change_request,
            item,
            user,
            "withdrawn",
            "pending",
            "withdrawn",
            _optional_text(reason),
        )
    completed = _refresh_request_completion(change_request, all_items)
    notification_service.notify_submitter_updates_batch(
        [
            {
                "request": change_request,
                "items": all_items,
                "completed": completed,
            }
        ]
    )
    db.session.flush()
    return len(pending_items)


def reverse_change_request_item(item_id, reason, user, expected_revision):
    _require_approver(user)
    reason = _required_reason(reason, "A reversal reason is required.")
    request_id = db.session.query(StaffingChangeRequestItem.request_id).filter_by(
        id=int(item_id)
    ).scalar()
    if not request_id:
        raise ValueError("The request field was not found.")
    change_request, person, assignment, all_items = _locked_request_state(request_id)
    locked_item = next((row for row in all_items if row.id == int(item_id)), None)
    if not locked_item or locked_item.status not in {"approved", "denied"}:
        raise ValueError("Only an Approved or Denied field can be reversed.")
    if change_request_item_revision(locked_item) != str(expected_revision or "").strip():
        raise ValueError("This request field changed while you were editing. Reload Requests.")
    if not locked_item.decided_at or locked_item.decided_at < datetime.utcnow() - timedelta(days=REQUEST_HISTORY_DAYS):
        raise ValueError("This request field is outside the reversal history window.")
    pending_conflict = StaffingChangeRequestItem.query.filter(
        StaffingChangeRequestItem.person_id == locked_item.person_id,
        StaffingChangeRequestItem.field_name == locked_item.field_name,
        StaffingChangeRequestItem.status == "pending",
        StaffingChangeRequestItem.id != locked_item.id,
    ).with_for_update().first()
    if pending_conflict:
        raise ValueError("Another Pending request already exists for this field.")

    previous_status = locked_item.status
    if previous_status == "approved":
        requested_value = _decode_value(locked_item.requested_value_json)
        current_value = _current_field_value(person, assignment, locked_item.field_name)
        if current_value != requested_value:
            raise ValueError(
                "The employee value changed after approval, so this approval cannot be reversed."
            )
        original_value = _decode_value(locked_item.original_value_json)
        assignment = _apply_field_value(
            person,
            assignment,
            locked_item.field_name,
            original_value,
        )

    locked_item.status = "pending"
    locked_item.decision_reason = None
    locked_item.decided_by_user_id = None
    locked_item.decided_at = None
    locked_item.updated_at = datetime.utcnow()
    _add_event(
        change_request,
        locked_item,
        user,
        "reversed",
        previous_status,
        "pending",
        reason,
    )
    change_request.status = "pending"
    change_request.completed_at = None
    change_request.updated_at = datetime.utcnow()
    notification_service.notify_submitter_updates_batch(
        [
            {
                "request": change_request,
                "items": all_items,
                "reversed_item": locked_item,
                "reversed_from": previous_status,
            }
        ]
    )
    db.session.flush()
    return locked_item


def cleanup_change_request_retention(now=None):
    now = now or datetime.utcnow()
    expired_requests = StaffingChangeRequest.query.filter(
        StaffingChangeRequest.status == "pending",
        StaffingChangeRequest.submitted_at
        < now - timedelta(days=REQUEST_LIFETIME_DAYS),
    ).with_for_update().all()
    expired_ids = {row.id for row in expired_requests}
    expired_items = StaffingChangeRequestItem.query.filter(
        StaffingChangeRequestItem.request_id.in_(expired_ids or {-1})
    ).with_for_update().all()
    items_by_request = {}
    for item in expired_items:
        items_by_request.setdefault(item.request_id, []).append(item)
    notification_updates = []
    for change_request in expired_requests:
        superseded_items = []
        request_items = items_by_request.get(change_request.id, [])
        for item in request_items:
            if item.status != "pending":
                continue
            _set_item_status(item, "superseded", None, "Request expired after 30 days.", now=now)
            superseded_items.append(item)
            _add_event(
                change_request,
                item,
                None,
                "expired",
                "pending",
                "superseded",
                "Request expired after 30 days.",
                now=now,
            )
        change_request.status = "completed"
        change_request.completed_at = now
        change_request.updated_at = now
        notification_updates.append(
            {
                "request": change_request,
                "items": request_items,
                "superseded_items": superseded_items,
                "completed": True,
            }
        )

    notification_service.notify_submitter_updates_batch(
        notification_updates,
        now=now,
    )

    purge_rows = StaffingChangeRequest.query.filter(
        StaffingChangeRequest.status == "completed",
        StaffingChangeRequest.completed_at
        < now - timedelta(days=REQUEST_HISTORY_DAYS),
    ).all()
    purge_ids = {row.id for row in purge_rows}
    if purge_ids:
        StaffingNotification.query.filter(
            StaffingNotification.change_request_id.in_(purge_ids)
        ).delete(synchronize_session=False)
        StaffingChangeRequestEvent.query.filter(
            StaffingChangeRequestEvent.request_id.in_(purge_ids)
        ).delete(synchronize_session=False)
        StaffingChangeRequestItem.query.filter(
            StaffingChangeRequestItem.request_id.in_(purge_ids)
        ).delete(synchronize_session=False)
        StaffingChangeRequest.query.filter(
            StaffingChangeRequest.id.in_(purge_ids)
        ).delete(synchronize_session=False)
    changed = bool(expired_requests or purge_rows)
    if changed:
        db.session.flush()
    return {
        "expired": len(expired_requests),
        "purged": len(purge_rows),
        "changed": changed,
    }


def _decide_locked_items(
    change_request,
    person,
    assignment,
    items,
    action,
    reason,
    user,
    all_items=None,
):
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"approve", "deny"}:
        raise ValueError("Choose Approve or Deny.")
    if normalized_action == "deny":
        reason = _required_reason(reason, "A denial reason is required.")
    results = []
    superseded_items = []
    for item in items:
        if item.status != "pending":
            continue
        if normalized_action == "deny":
            _set_item_status(item, "denied", user, reason)
            _add_event(
                change_request,
                item,
                user,
                "denied",
                "pending",
                "denied",
                reason,
            )
            results.append(item)
            continue

        original_value = _decode_value(item.original_value_json)
        requested_value = _decode_value(item.requested_value_json)
        current_value = _current_field_value(person, assignment, item.field_name)
        if current_value != original_value:
            message = "Employee data changed after submission; newer data was preserved."
            _set_item_status(item, "superseded", user, message)
            _add_event(
                change_request,
                item,
                user,
                "superseded",
                "pending",
                "superseded",
                message,
            )
            superseded_items.append(item)
        else:
            assignment = _apply_field_value(
                person,
                assignment,
                item.field_name,
                requested_value,
            )
            _set_item_status(item, "approved", user, None)
            _add_event(
                change_request,
                item,
                user,
                "approved",
                "pending",
                "approved",
                None,
            )
        results.append(item)
    request_items = all_items or items
    completed = _refresh_request_completion(change_request, request_items)
    notification_service.notify_submitter_updates_batch(
        [
            {
                "request": change_request,
                "items": request_items,
                "superseded_items": superseded_items,
                "completed": completed,
            }
        ]
    )
    db.session.flush()
    return results


def _locked_request_state(request_id):
    change_request = StaffingChangeRequest.query.filter_by(
        id=int(request_id)
    ).with_for_update().first()
    if not change_request:
        raise ValueError("The change request was not found.")
    person = StaffingPerson.query.filter_by(
        id=change_request.person_id
    ).with_for_update().first()
    if not person:
        raise ValueError("The employee was not found.")
    assignment = StaffingWorkAssignment.query.filter_by(
        person_id=person.id,
        active=True,
    ).with_for_update().first()
    items = StaffingChangeRequestItem.query.filter_by(
        request_id=change_request.id
    ).order_by(StaffingChangeRequestItem.id).with_for_update().all()
    return change_request, person, assignment, items


def _refresh_request_completion(change_request, items):
    now = datetime.utcnow()
    was_completed = change_request.status == "completed"
    if any(item.status == "pending" for item in items):
        change_request.status = "pending"
        change_request.completed_at = None
    else:
        change_request.status = "completed"
        change_request.completed_at = now
    change_request.updated_at = now
    return bool(not was_completed and change_request.status == "completed")


def _set_item_status(item, status, user, reason, now=None):
    now = now or datetime.utcnow()
    item.status = status
    item.decision_reason = _optional_text(reason)
    item.decided_by_user_id = getattr(user, "id", None)
    item.decided_at = now
    item.updated_at = now


def _validate_withdrawal(change_request, item, user, expected_revision):
    if change_request.submitted_by_user_id != user.id:
        raise ValueError("Only the submitter may withdraw this request field.")
    if not item or item.status != "pending":
        raise ValueError("This request field is no longer Pending.")
    if change_request_item_revision(item) != str(expected_revision or "").strip():
        raise ValueError("This request field changed while you were editing. Reload Requests.")


def _apply_field_value(person, assignment, field_name, value):
    if field_name == "first_name":
        person.first_name = value
    elif field_name == "last_name":
        person.last_name = value
    elif field_name == "seniority_date":
        person.seniority_date = date.fromisoformat(value)
    elif field_name == "employee_status":
        person.employee_status = value
    elif field_name == "classification":
        if value not in WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS:
            raise ValueError("Only non-management classification changes are supported.")
        person.classification = value
    elif field_name == "work_area_unit_id":
        if value is None:
            if assignment:
                assignment.active = False
            return None
        work_area = db.session.get(StaffingUnit, int(value))
        if not work_area or work_area.unit_type != "work_area":
            raise ValueError("The requested Work Area is no longer available.")
        if assignment:
            assignment.work_area_unit_id = work_area.id
            assignment.active = True
        else:
            assignment = StaffingWorkAssignment(
                person_id=person.id,
                work_area_unit_id=work_area.id,
                active=True,
            )
            db.session.add(assignment)
        return assignment
    else:
        raise ValueError("Unsupported change-request field.")
    return assignment


def _current_field_value(person, assignment, field_name):
    if field_name == "work_area_unit_id":
        return assignment.work_area_unit_id if assignment and assignment.active else None
    value = getattr(person, field_name)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _parse_requested_values(values):
    parsed = {field_name: _NOT_SUBMITTED for field_name in REQUESTABLE_FORM_FIELDS}
    first_name = str(values.get("requested_first_name") or "").strip()
    if first_name:
        parsed["first_name"] = _validate_name(first_name, "First name")
    last_name = str(values.get("requested_last_name") or "").strip()
    if last_name:
        parsed["last_name"] = _validate_name(last_name, "Last name")
    seniority_date = str(values.get("requested_seniority_date") or "").strip()
    if seniority_date:
        try:
            parsed["seniority_date"] = date.fromisoformat(seniority_date).isoformat()
        except ValueError:
            raise ValueError("Seniority date must be a valid date.")
    employee_status = str(values.get("requested_employee_status") or "").strip().lower()
    if employee_status:
        if employee_status not in STAFFING_EMPLOYEE_STATUSES:
            raise ValueError("Choose a valid employee status.")
        parsed["employee_status"] = employee_status
    classification = str(values.get("requested_classification") or "").strip().lower()
    if classification:
        if classification not in WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS:
            raise ValueError("Only non-management classification changes are supported.")
        parsed["classification"] = classification
    work_area = str(values.get("requested_work_area_unit_id") or "").strip()
    if work_area:
        if work_area == "__unassigned__":
            parsed["work_area_unit_id"] = None
        else:
            try:
                parsed["work_area_unit_id"] = int(work_area)
            except ValueError:
                raise ValueError("Select a valid Work Area.")
    return parsed


def _route_approver_person_ids(source_area_id, destination_area_id, submitter_person):
    area_ids = {area_id for area_id in (source_area_id, destination_area_id) if area_id}
    units = StaffingUnit.query.all()
    units_by_id = {unit.id: unit for unit in units}
    department_ids = {
        unit.parent_id
        for area_id in area_ids
        for unit in (units_by_id.get(area_id),)
        if unit
        and unit.parent_id
        and units_by_id.get(unit.parent_id)
        and units_by_id[unit.parent_id].unit_type == "department"
    }
    approver_ids = []
    if department_ids:
        approver_ids = [
            row.person_id
            for row in (
                StaffingLeadershipAssignment.query.join(StaffingPerson)
                .filter(
                    StaffingLeadershipAssignment.active.is_(True),
                    StaffingLeadershipAssignment.unit_id.in_(department_ids),
                    StaffingLeadershipAssignment.leadership_level == "department",
                    StaffingPerson.active.is_(True),
                    StaffingPerson.classification == "full_time_supervisor",
                )
                .order_by(StaffingLeadershipAssignment.person_id)
                .all()
            )
        ]
    ft_supervisor_ids = set(approver_ids)
    if ft_supervisor_ids:
        approver_ids.extend(
            row.twenty_c_person_id
            for row in StaffingTwentyCAffiliation.query.filter(
                StaffingTwentyCAffiliation.active.is_(True),
                StaffingTwentyCAffiliation.ft_supervisor_person_id.in_(
                    ft_supervisor_ids
                ),
            ).all()
        )
    if not approver_ids and submitter_person:
        relationship = StaffingReportingRelationship.query.join(
            StaffingPerson,
            StaffingReportingRelationship.reports_to_person_id == StaffingPerson.id,
        ).filter(
            StaffingReportingRelationship.person_id == submitter_person.id,
            StaffingReportingRelationship.active.is_(True),
            StaffingPerson.active.is_(True),
            StaffingPerson.classification == "full_time_supervisor",
        ).first()
        if relationship:
            approver_ids = [relationship.reports_to_person_id]
    return sorted(set(approver_ids))


def _route_approvers_from_loaded_rows(
    source_area_id,
    destination_area_id,
    submitter_person,
    units_by_id,
    leadership,
    people_by_id,
    submitter_relationship,
    affiliations=(),
):
    area_ids = {
        area_id
        for area_id in (source_area_id, destination_area_id)
        if area_id
    }
    department_ids = {
        unit.parent_id
        for area_id in area_ids
        for unit in (units_by_id.get(area_id),)
        if unit
        and unit.parent_id
        and units_by_id.get(unit.parent_id)
        and units_by_id[unit.parent_id].unit_type == "department"
    }
    approver_ids = {
        row.person_id
        for row in leadership
        if row.active
        and row.unit_id in department_ids
        and row.leadership_level == "department"
        and people_by_id.get(row.person_id)
        and people_by_id[row.person_id].active
        and people_by_id[row.person_id].classification == "full_time_supervisor"
    }
    ft_supervisor_ids = set(approver_ids)
    approver_ids.update(
        row.twenty_c_person_id
        for row in affiliations
        if row.active and row.ft_supervisor_person_id in ft_supervisor_ids
    )
    if not approver_ids and submitter_person and submitter_relationship:
        target = people_by_id.get(submitter_relationship.reports_to_person_id)
        if (
            target
            and target.active
            and target.classification == "full_time_supervisor"
        ):
            approver_ids.add(target.id)
    return sorted(approver_ids)


def _normalize_bulk_requested_value(field_name, value, units_by_id):
    if field_name == "first_name":
        return _validate_name(value, "First name")
    if field_name == "last_name":
        return _validate_name(value, "Last name")
    if field_name == "seniority_date":
        try:
            return date.fromisoformat(str(value or "").strip()).isoformat()
        except ValueError as error:
            raise ValueError("Seniority date must be a valid date.") from error
    if field_name == "employee_status":
        normalized = str(value or "").strip().lower()
        if normalized not in STAFFING_EMPLOYEE_STATUSES:
            raise ValueError("Choose a valid employee status.")
        return normalized
    if field_name == "classification":
        normalized = str(value or "").strip().lower()
        if normalized not in WRITABLE_NON_MANAGEMENT_CLASSIFICATIONS:
            raise ValueError("Only non-management classification changes are supported.")
        return normalized
    if field_name == "work_area_unit_id":
        if value is None:
            return None
        try:
            unit_id = int(value)
        except (TypeError, ValueError):
            raise ValueError("Select a valid Work Area.")
        unit = units_by_id.get(unit_id)
        if not unit or not unit.active or unit.unit_type != "work_area":
            raise ValueError("Select an active Work Area.")
        return unit.id
    raise ValueError("Unsupported change-request field.")


def _submission_candidates(
    people,
    assignments_by_person,
    leadership_by_person,
    current_person,
    app_role,
    user,
):
    candidates = [
        person
        for person in people
        if person.active and person.classification in NON_MANAGEMENT_CLASSIFICATIONS
    ]
    if _is_grandmaster(user, app_role) or ROLE_LEVELS.get(app_role, 0) >= ROLE_LEVELS["simulator"]:
        return candidates
    if not current_person or current_person.classification != "part_time_supervisor":
        return []
    owned_area_ids = {
        row.unit_id
        for row in leadership_by_person.get(current_person.id, [])
        if row.leadership_level == "work_area"
    }
    return [
        person
        for person in candidates
        if assignments_by_person.get(person.id)
        and assignments_by_person[person.id].work_area_unit_id in owned_area_ids
    ]


def _default_queue_scope(person):
    if not person:
        return "all"
    if person.classification == "full_time_supervisor":
        return "routed"
    if person.classification == "twenty_c_full_time_supervisor":
        return "purview"
    if person.classification in {"manager", "division_manager"}:
        return "purview"
    return "all"


def _request_matches_queue(
    change_request,
    queue_scope,
    current_person,
    routed_ids,
    authority_unit_ids_by_person,
    units_by_id,
):
    if queue_scope == "all":
        return True
    if queue_scope == "unassigned":
        return bool(change_request.unassigned_approval)
    if queue_scope == "routed":
        return bool(current_person and current_person.id in routed_ids)
    if not current_person:
        return False
    led_unit_ids = authority_unit_ids_by_person.get(current_person.id, set())
    return any(
        _unit_is_within(unit_id, led_unit_ids, units_by_id)
        for unit_id in (
            change_request.source_work_area_unit_id,
            change_request.destination_work_area_unit_id,
        )
        if unit_id
    )


def _unit_is_within(unit_id, ancestor_ids, units_by_id):
    visited = set()
    current = units_by_id.get(unit_id)
    while current and current.id not in visited:
        if current.id in ancestor_ids:
            return True
        visited.add(current.id)
        current = units_by_id.get(current.parent_id)
    return False


def _management_authority_unit_ids_from_rows(leadership_by_person, affiliations):
    authority = {
        person_id: {row.unit_id for row in rows if row.active}
        for person_id, rows in leadership_by_person.items()
    }
    for affiliation in affiliations:
        if not affiliation.active:
            continue
        authority.setdefault(affiliation.twenty_c_person_id, set()).update(
            row.unit_id
            for row in leadership_by_person.get(
                affiliation.ft_supervisor_person_id, ()
            )
            if row.active
        )
    return authority


def _require_approver(user):
    if not can_approve_change_requests(user):
        raise ValueError("You do not have authority to approve employee change requests.")


def _staffing_person_for_user(user):
    employee_id = str(getattr(user, "employee_id", "") or "").strip()
    if not employee_id:
        return None
    return StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        func.lower(StaffingPerson.employee_id) == employee_id.lower(),
    ).first()


def _person_for_user_from_rows(user, people):
    employee_id = str(getattr(user, "employee_id", "") or "").strip().lower()
    if not employee_id:
        return None
    return next(
        (
            person
            for person in people
            if person.active and person.employee_id.lower() == employee_id
        ),
        None,
    )


def _is_grandmaster(user, app_role):
    return bool(
        getattr(user, "role", None) == "grandmaster" or app_role == "grandmaster"
    )


def _can_submit_with_context(user, app_role, person):
    if _is_grandmaster(user, app_role):
        return True
    return bool(person and person.classification == "part_time_supervisor")


def _can_approve_with_context(user, app_role, person):
    if _is_grandmaster(user, app_role):
        return True
    if app_role == "watcher":
        return False
    return bool(person and person.classification in APPROVER_CLASSIFICATIONS)


def _add_event(
    change_request,
    item,
    user,
    event_type,
    from_status,
    to_status,
    reason,
    details=None,
    now=None,
):
    db.session.add(
        StaffingChangeRequestEvent(
            request_id=change_request.id,
            item_id=item.id if item else None,
            actor_user_id=getattr(user, "id", None),
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            reason=_optional_text(reason),
            details_json=json.dumps(details, sort_keys=True) if details else None,
            created_at=now or datetime.utcnow(),
        )
    )


def _encode_value(value):
    return json.dumps(value, sort_keys=True)


def _decode_value(value):
    return json.loads(value) if value is not None else None


def _decode_person_ids(value):
    try:
        rows = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return sorted({int(row) for row in rows if str(row).isdigit()})


def _display_value(field_name, value, units_by_id):
    if value is None:
        return "Unassigned" if field_name == "work_area_unit_id" else "-"
    if field_name == "classification":
        return CLASSIFICATION_LABELS.get(value, str(value))
    if field_name == "employee_status":
        return EMPLOYEE_STATUS_LABELS.get(value, str(value))
    if field_name == "work_area_unit_id":
        unit = units_by_id.get(int(value))
        return unit.name if unit else f"Work Area #{value}"
    return str(value)


def _validate_name(value, label):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{label} is required.")
    if len(value) > 80:
        raise ValueError(f"{label} must be 80 characters or fewer.")
    return value


def _required_reason(value, message):
    reason = _optional_text(value)
    if not reason:
        raise ValueError(message)
    return reason


def _optional_text(value):
    text = str(value or "").strip()
    return text or None


_NOT_SUBMITTED = object()
