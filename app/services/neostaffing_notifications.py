"""NeoStaffing-only notifications and actionable request badges."""

from datetime import datetime, timedelta
import json

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingChangeRequest,
    StaffingLeadershipAssignment,
    StaffingNotification,
    StaffingPerson,
    StaffingUnit,
    User,
)
from app.services.access_control import get_user_app_role
from app.services.permission_rules import user_can
from app.services.request_cache import request_cached


CHANGE_REQUEST_VIEW_PERMISSION = "neostaffing.change_requests.view"
CHANGE_REQUEST_APPROVE_PERMISSION = "neostaffing.change_requests.approve"
NOTIFICATION_RETENTION_DAYS = 14
REQUEST_OVERDUE_HOURS = 48

NOTIFICATION_TYPE_LABELS = {
    "new_request": "New Request",
    "request_completed": "Request Completed",
    "decision_reversed": "Decision Reversed",
    "item_superseded": "Request Superseded",
    "request_overdue": "Request Overdue",
}
FIELD_LABELS = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "seniority_date": "Seniority Date",
    "employee_status": "Employee Status",
    "classification": "Classification",
    "work_area_unit_id": "Work Area",
}


def notify_new_requests(change_requests, now=None):
    """Notify linked routed FT Supervisors once per pending request."""
    now = now or datetime.utcnow()
    pending_requests = [
        row
        for row in change_requests or []
        if row and row.id and row.status == "pending"
    ]
    routed_person_ids = {
        person_id
        for change_request in pending_requests
        for person_id in _decode_person_ids(
            change_request.routed_approver_person_ids_json
        )
    }
    people = _active_people(
        routed_person_ids,
        classifications={
            "full_time_supervisor",
            "twenty_c_full_time_supervisor",
        },
    )
    user_ids_by_person = _linked_user_ids_by_person(people)
    rows = []
    for change_request in pending_requests:
        for person_id in _decode_person_ids(
            change_request.routed_approver_person_ids_json
        ):
            for user_id in user_ids_by_person.get(person_id, ()):
                rows.append(
                    _notification_row(
                        recipient_user_id=user_id,
                        change_request_id=change_request.id,
                        notification_type="new_request",
                        message=(
                            f"Employee change request #{change_request.id} "
                            "is ready for your review."
                        ),
                        dedupe_key=f"new-request:{change_request.id}:user:{user_id}",
                        details={"request_id": change_request.id},
                        now=now,
                    )
                )
    return _insert_notification_rows(rows)


def notify_submitter_updates_batch(updates, now=None):
    """Create bounded submitter notifications for request state transitions."""
    now = now or datetime.utcnow()
    normalized = [row for row in updates or [] if row.get("request")]
    submitter_person_ids = {
        row["request"].submitted_by_person_id
        for row in normalized
        if row["request"].submitted_by_person_id
    }
    pt_supervisor_ids = {
        person_id
        for (person_id,) in db.session.query(StaffingPerson.id).filter(
            StaffingPerson.id.in_(submitter_person_ids or {-1}),
            StaffingPerson.classification == "part_time_supervisor",
        ).all()
    }
    rows = []
    for update in normalized:
        change_request = update["request"]
        if change_request.submitted_by_person_id not in pt_supervisor_ids:
            continue
        recipient_user_id = change_request.submitted_by_user_id
        items = list(update.get("items") or [])
        for item in update.get("superseded_items") or []:
            field_label = FIELD_LABELS.get(item.field_name, item.field_name)
            rows.append(
                _notification_row(
                    recipient_user_id=recipient_user_id,
                    change_request_id=change_request.id,
                    notification_type="item_superseded",
                    message=(
                        f"{field_label} on request #{change_request.id} became "
                        "Superseded. Newer employee data was preserved."
                    ),
                    dedupe_key=(
                        f"superseded:{change_request.id}:item:{item.id}:"
                        f"user:{recipient_user_id}"
                    ),
                    details={
                        "request_id": change_request.id,
                        "item_id": item.id,
                        "field_name": item.field_name,
                    },
                    now=now,
                )
            )

        reversed_item = update.get("reversed_item")
        if reversed_item:
            previous_status = str(update.get("reversed_from") or "decision").title()
            field_label = FIELD_LABELS.get(
                reversed_item.field_name,
                reversed_item.field_name,
            )
            revision = (reversed_item.updated_at or now).isoformat(
                timespec="microseconds"
            )
            rows.append(
                _notification_row(
                    recipient_user_id=recipient_user_id,
                    change_request_id=change_request.id,
                    notification_type="decision_reversed",
                    message=(
                        f"The {field_label} decision on request "
                        f"#{change_request.id} was reversed from {previous_status} "
                        "to Pending."
                    ),
                    dedupe_key=(
                        f"reversed:{change_request.id}:item:{reversed_item.id}:"
                        f"{revision}:user:{recipient_user_id}"
                    ),
                    details={
                        "request_id": change_request.id,
                        "item_id": reversed_item.id,
                        "field_name": reversed_item.field_name,
                        "from_status": str(update.get("reversed_from") or ""),
                    },
                    now=now,
                )
            )

        if update.get("completed"):
            counts = {
                status: sum(1 for item in items if item.status == status)
                for status in ("approved", "denied", "withdrawn", "superseded")
            }
            summary = ", ".join(
                f"{label} {counts[status]}"
                for status, label in (
                    ("approved", "Approved"),
                    ("denied", "Denied"),
                    ("withdrawn", "Withdrawn"),
                    ("superseded", "Superseded"),
                )
            )
            completed_revision = (
                change_request.completed_at or now
            ).isoformat(timespec="microseconds")
            rows.append(
                _notification_row(
                    recipient_user_id=recipient_user_id,
                    change_request_id=change_request.id,
                    notification_type="request_completed",
                    message=f"Request #{change_request.id} completed: {summary}.",
                    dedupe_key=(
                        f"completed:{change_request.id}:at:{completed_revision}:"
                        f"user:{recipient_user_id}"
                    ),
                    details={"request_id": change_request.id, "counts": counts},
                    now=now,
                )
            )
    return _insert_notification_rows(rows)


def maintain_notifications(now=None):
    """Purge old history and materialize one reminder per overdue recipient."""
    now = now or datetime.utcnow()
    expired_ids = [
        notification_id
        for (notification_id,) in db.session.query(StaffingNotification.id).filter(
            StaffingNotification.created_at
            < now - timedelta(days=NOTIFICATION_RETENTION_DAYS)
        ).all()
    ]
    purged = 0
    if expired_ids:
        purged = StaffingNotification.query.filter(
            StaffingNotification.id.in_(expired_ids)
        ).delete(synchronize_session=False)
    overdue_created = _materialize_overdue_notifications(now)
    changed = bool(purged or overdue_created)
    if changed:
        db.session.flush()
    return {
        "purged": int(purged or 0),
        "overdue_created": overdue_created,
        "changed": changed,
    }


def notification_context(user, now=None):
    now = now or datetime.utcnow()
    navigation = notification_navigation_state(user)
    notifications = (
        StaffingNotification.query.options(
            joinedload(StaffingNotification.change_request).joinedload(
                StaffingChangeRequest.person
            )
        )
        .filter(
            StaffingNotification.recipient_user_id == user.id,
            StaffingNotification.created_at
            >= now - timedelta(days=NOTIFICATION_RETENTION_DAYS),
        )
        .order_by(
            StaffingNotification.read_at.is_(None).desc(),
            StaffingNotification.created_at.desc(),
            StaffingNotification.id.desc(),
        )
        .all()
    )
    return {
        "notifications": notifications,
        "unread_count": navigation["unread_notifications"],
        "type_labels": NOTIFICATION_TYPE_LABELS,
    }


def mark_notification_read(notification_id, user, now=None):
    notification = StaffingNotification.query.filter_by(
        id=int(notification_id),
        recipient_user_id=user.id,
    ).with_for_update().first()
    if not notification:
        raise ValueError("The notification was not found.")
    if notification.read_at:
        return notification, False
    notification.read_at = now or datetime.utcnow()
    db.session.flush()
    return notification, True


def notification_navigation_state(user):
    return request_cached(
        "neostaffing.notification_navigation",
        getattr(user, "id", None),
        lambda: _resolve_notification_navigation_state(user),
    )


def _resolve_notification_navigation_state(user):
    empty = {"unread_notifications": 0, "actionable_requests": 0}
    if not getattr(user, "is_authenticated", False):
        return empty
    if not user_can(CHANGE_REQUEST_VIEW_PERMISSION, user):
        return empty

    unread = StaffingNotification.query.filter_by(
        recipient_user_id=user.id,
        read_at=None,
    ).count()
    app_role = get_user_app_role(user, "neostaffing")
    if app_role == "watcher" or not user_can(CHANGE_REQUEST_APPROVE_PERMISSION, user):
        return {"unread_notifications": unread, "actionable_requests": 0}

    employee_id = str(getattr(user, "employee_id", "") or "").strip().lower()
    if not employee_id:
        return {"unread_notifications": unread, "actionable_requests": 0}
    person = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        func.lower(StaffingPerson.employee_id) == employee_id,
    ).first()
    if not person or person.classification not in {
        "full_time_supervisor",
        "twenty_c_full_time_supervisor",
        "manager",
    }:
        return {"unread_notifications": unread, "actionable_requests": 0}

    pending = db.session.query(
        StaffingChangeRequest.id,
        StaffingChangeRequest.routed_approver_person_ids_json,
        StaffingChangeRequest.source_work_area_unit_id,
        StaffingChangeRequest.destination_work_area_unit_id,
    ).filter(StaffingChangeRequest.status == "pending").all()
    if person.classification in {
        "full_time_supervisor",
        "twenty_c_full_time_supervisor",
    }:
        actionable = sum(
            1
            for row in pending
            if person.id in _decode_person_ids(row.routed_approver_person_ids_json)
        )
    else:
        led_unit_ids = {
            unit_id
            for (unit_id,) in db.session.query(
                StaffingLeadershipAssignment.unit_id
            ).filter_by(person_id=person.id, active=True).all()
        }
        units = db.session.query(
            StaffingUnit.id,
            StaffingUnit.parent_id,
        ).all()
        parent_by_id = {row.id: row.parent_id for row in units}
        actionable = sum(
            1
            for row in pending
            if any(
                _unit_is_within(unit_id, led_unit_ids, parent_by_id)
                for unit_id in (
                    row.source_work_area_unit_id,
                    row.destination_work_area_unit_id,
                )
                if unit_id
            )
        )
    return {"unread_notifications": unread, "actionable_requests": actionable}


def _materialize_overdue_notifications(now):
    overdue_requests = StaffingChangeRequest.query.filter(
        StaffingChangeRequest.status == "pending",
        StaffingChangeRequest.submitted_at
        <= now - timedelta(hours=REQUEST_OVERDUE_HOURS),
    ).order_by(StaffingChangeRequest.id).all()
    if not overdue_requests:
        return 0

    routed_ids = {
        person_id
        for change_request in overdue_requests
        for person_id in _decode_person_ids(
            change_request.routed_approver_person_ids_json
        )
    }
    people = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        or_(
            StaffingPerson.id.in_(routed_ids or {-1}),
            StaffingPerson.classification == "manager",
        ),
    ).all()
    people_by_id = {row.id: row for row in people}
    manager_ids = {
        row.id for row in people if row.classification == "manager"
    }
    manager_ids_by_unit = {}
    if manager_ids:
        leadership = StaffingLeadershipAssignment.query.filter(
            StaffingLeadershipAssignment.active.is_(True),
            StaffingLeadershipAssignment.person_id.in_(manager_ids),
        ).all()
        for assignment in leadership:
            manager_ids_by_unit.setdefault(assignment.unit_id, set()).add(
                assignment.person_id
            )
    units = db.session.query(StaffingUnit.id, StaffingUnit.parent_id).all()
    parent_by_id = {row.id: row.parent_id for row in units}

    recipient_person_ids_by_request = {}
    for change_request in overdue_requests:
        recipient_ids = {
            person_id
            for person_id in _decode_person_ids(
                change_request.routed_approver_person_ids_json
            )
            if people_by_id.get(person_id)
            and people_by_id[person_id].classification in {
                "full_time_supervisor",
                "twenty_c_full_time_supervisor",
            }
        }
        for unit_id in (
            change_request.source_work_area_unit_id,
            change_request.destination_work_area_unit_id,
        ):
            current_id = unit_id
            visited = set()
            while current_id and current_id not in visited:
                visited.add(current_id)
                recipient_ids.update(manager_ids_by_unit.get(current_id, set()))
                current_id = parent_by_id.get(current_id)
        recipient_person_ids_by_request[change_request.id] = recipient_ids

    recipient_person_ids = {
        person_id
        for values in recipient_person_ids_by_request.values()
        for person_id in values
    }
    recipient_people = [
        people_by_id[person_id]
        for person_id in recipient_person_ids
        if person_id in people_by_id
    ]
    user_ids_by_person = _linked_user_ids_by_person(recipient_people)
    rows = []
    for change_request in overdue_requests:
        for person_id in recipient_person_ids_by_request[change_request.id]:
            for user_id in user_ids_by_person.get(person_id, ()):
                rows.append(
                    _notification_row(
                        recipient_user_id=user_id,
                        change_request_id=change_request.id,
                        notification_type="request_overdue",
                        message=(
                            f"Employee change request #{change_request.id} is overdue "
                            "and still has Pending fields."
                        ),
                        dedupe_key=(
                            f"overdue:{change_request.id}:user:{user_id}"
                        ),
                        details={"request_id": change_request.id},
                        now=now,
                    )
                )
    return _insert_notification_rows(rows)


def _active_people(person_ids, classifications=None):
    if not person_ids:
        return []
    query = StaffingPerson.query.filter(
        StaffingPerson.id.in_(person_ids),
        StaffingPerson.active.is_(True),
    )
    if classifications:
        query = query.filter(StaffingPerson.classification.in_(classifications))
    return query.all()


def _linked_user_ids_by_person(people):
    person_by_employee_id = {
        str(person.employee_id or "").strip().lower(): person.id
        for person in people
        if str(person.employee_id or "").strip()
    }
    if not person_by_employee_id:
        return {}
    records = (
        db.session.query(User.id, User.employee_id)
        .join(
            PortalAppAccess,
            PortalAppAccess.user_id == User.id,
        )
        .filter(
            User.is_active.is_(True),
            func.lower(User.employee_id).in_(list(person_by_employee_id)),
            PortalAppAccess.app_code == "neostaffing",
            PortalAppAccess.status == "approved",
            PortalAppAccess.is_active.is_(True),
        )
        .all()
    )
    user_ids_by_person = {}
    for user_id, employee_id in records:
        person_id = person_by_employee_id.get(str(employee_id or "").lower())
        if person_id:
            user_ids_by_person.setdefault(person_id, set()).add(user_id)
    return user_ids_by_person


def _notification_row(
    *,
    recipient_user_id,
    change_request_id,
    notification_type,
    message,
    dedupe_key,
    details,
    now,
):
    return {
        "recipient_user_id": recipient_user_id,
        "change_request_id": change_request_id,
        "notification_type": notification_type,
        "message": message,
        "details_json": json.dumps(details, sort_keys=True) if details else None,
        "dedupe_key": dedupe_key,
        "created_at": now,
        "read_at": None,
    }


def _insert_notification_rows(rows):
    if not rows:
        return 0
    deduped = {row["dedupe_key"]: row for row in rows}
    existing = {
        key
        for (key,) in db.session.query(StaffingNotification.dedupe_key).filter(
            StaffingNotification.dedupe_key.in_(deduped)
        ).all()
    }
    values = [row for key, row in deduped.items() if key not in existing]
    if not values:
        return 0
    dialect_name = db.session.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(StaffingNotification.__table__).values(values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["dedupe_key"]
        ).returning(StaffingNotification.id)
        return len(db.session.execute(statement).scalars().all())
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(StaffingNotification.__table__).values(values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["dedupe_key"]
        ).returning(StaffingNotification.id)
        return len(db.session.execute(statement).scalars().all())

    db.session.execute(StaffingNotification.__table__.insert(), values)
    return len(values)


def _decode_person_ids(value):
    try:
        rows = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return sorted({int(row) for row in rows if str(row).isdigit()})


def _unit_is_within(unit_id, ancestor_ids, parent_by_id):
    current_id = unit_id
    visited = set()
    while current_id and current_id not in visited:
        if current_id in ancestor_ids:
            return True
        visited.add(current_id)
        current_id = parent_by_id.get(current_id)
    return False
