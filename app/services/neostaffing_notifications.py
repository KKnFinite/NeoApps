"""NeoStaffing-only notifications and actionable request badges."""

from datetime import datetime, timedelta
import json

from sqlalchemy import func, or_, select, and_, cast, String, literal
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
    if not normalized:
        return 0
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


def maintain_notifications(now=None, *, user=None):
    """Purge old history and materialize one reminder per overdue recipient."""
    now = now or datetime.utcnow()
    expired_query = db.session.query(StaffingNotification.id).filter(
        StaffingNotification.created_at < now - timedelta(days=NOTIFICATION_RETENTION_DAYS))
    if user is not None:
        expired_query = expired_query.filter(StaffingNotification.recipient_user_id == user.id)
    expired_ids = [
        notification_id
        for (notification_id,) in expired_query.all()
    ]
    purged = 0
    if expired_ids:
        purged = StaffingNotification.query.filter(
            StaffingNotification.id.in_(expired_ids)
        ).delete(synchronize_session=False)
    overdue_created = _materialize_overdue_notifications(now, user=user)
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

    person = notification_person(user)
    if not person or person.classification not in {
        "full_time_supervisor",
        "twenty_c_full_time_supervisor",
        "manager",
    }:
        return {"unread_notifications": unread, "actionable_requests": 0}

    if person.classification in {
        "full_time_supervisor",
        "twenty_c_full_time_supervisor",
    }:
        pending = db.session.query(StaffingChangeRequest.routed_approver_person_ids_json).filter(
            StaffingChangeRequest.status == "pending").yield_per(200)
        actionable = sum(
            1
            for row in pending
            if person.id in _decode_person_ids(row.routed_approver_person_ids_json)
        )
    else:
        actionable = StaffingChangeRequest.query.filter(
            StaffingChangeRequest.status == "pending", manager_request_scope(person.id),
        ).count()
    return {"unread_notifications": unread, "actionable_requests": actionable}


def notification_person(user):
    employee_id = str(getattr(user, "employee_id", "") or "").strip().lower()
    if not employee_id:
        return None
    return request_cached("staffing.notification_person", user.id, lambda: StaffingPerson.query.filter(
        StaffingPerson.active.is_(True), func.lower(StaffingPerson.employee_id) == employee_id,
    ).first())


def descendant_unit_ids(roots):
    """Cycle-safe, SQL-scoped hierarchy; inactive units retain legacy ancestry."""
    tree = select(StaffingUnit.id).where(StaffingUnit.id.in_(roots)).cte(recursive=True)
    tree = tree.union(select(StaffingUnit.id).join(tree, StaffingUnit.parent_id == tree.c.id))
    return select(tree.c.id)


def manager_request_scope(person_id):
    roots = select(StaffingLeadershipAssignment.unit_id).where(
        StaffingLeadershipAssignment.person_id == person_id,
        StaffingLeadershipAssignment.active.is_(True))
    units = descendant_unit_ids(roots)
    return or_(StaffingChangeRequest.source_work_area_unit_id.in_(units),
               StaffingChangeRequest.destination_work_area_unit_id.in_(units))


def _materialize_overdue_notifications(now, *, user=None):
    """Materialize in 200-request batches; GET callers supply their recipient."""
    query = db.session.query(
        StaffingChangeRequest.id, StaffingChangeRequest.source_work_area_unit_id,
        StaffingChangeRequest.destination_work_area_unit_id,
        StaffingChangeRequest.routed_approver_person_ids_json,
    ).filter(
        StaffingChangeRequest.status == "pending",
        StaffingChangeRequest.submitted_at <= now - timedelta(hours=REQUEST_OVERDUE_HOURS),
    )
    person = None
    if user is not None:
        person = notification_person(user)
        if (not user.is_active or not get_user_app_role(user, "neostaffing") or not person
                or person.classification not in {"manager", "full_time_supervisor", "twenty_c_full_time_supervisor"}):
            return 0
        # Existing keys need no hierarchy/user resolution or attempted INSERT.
        key = literal("overdue:") + cast(StaffingChangeRequest.id, String) + literal(f":user:{user.id}")
        query = query.filter(~select(StaffingNotification.id).where(
            StaffingNotification.dedupe_key == key).exists())
        if person.classification == "manager":
            query = query.filter(manager_request_scope(person.id))
    total, last_id = 0, 0
    while True:
        batch = query.filter(StaffingChangeRequest.id > last_id).order_by(StaffingChangeRequest.id).limit(200).all()
        if not batch:
            break
        last_id = batch[-1].id
        if user is not None:
            recipients = {
                row.id: {user.id} for row in batch
                if person.classification == "manager"
                or person.id in _decode_person_ids(row.routed_approver_person_ids_json)
            }
        else:
            recipients = _overdue_recipients(batch)
        rows = [
            _notification_row(
                recipient_user_id=user_id, change_request_id=row.id,
                notification_type="request_overdue",
                message=f"Employee change request #{row.id} is overdue and still has Pending fields.",
                dedupe_key=f"overdue:{row.id}:user:{user_id}",
                details={"request_id": row.id}, now=now,
            )
            for row in batch for user_id in recipients.get(row.id, ())
        ]
        total += _insert_notification_rows(rows)
        if len(batch) < 200:
            break
    return total


def _overdue_recipients(requests):
    routed_ids = {person_id for row in requests
                  for person_id in _decode_person_ids(row.routed_approver_person_ids_json)}
    area_ids = {unit_id for row in requests for unit_id in
                (row.source_work_area_unit_id, row.destination_work_area_unit_id) if unit_id}
    ancestry = select(StaffingUnit.id, StaffingUnit.parent_id).where(
        StaffingUnit.id.in_(area_ids)).cte(recursive=True)
    ancestry = ancestry.union(select(StaffingUnit.id, StaffingUnit.parent_id).join(
        ancestry, StaffingUnit.id == ancestry.c.parent_id))
    parents = dict(db.session.execute(select(ancestry.c.id, ancestry.c.parent_id)).all())
    # Only routed supervisors and managers leading an actual ancestor are needed.
    records = db.session.query(StaffingPerson, StaffingLeadershipAssignment.unit_id).outerjoin(
        StaffingLeadershipAssignment, and_(StaffingLeadershipAssignment.person_id == StaffingPerson.id,
                                           StaffingLeadershipAssignment.active.is_(True)),
    ).filter(StaffingPerson.active.is_(True), or_(
        and_(StaffingPerson.id.in_(routed_ids), StaffingPerson.classification.in_(
            ("full_time_supervisor", "twenty_c_full_time_supervisor"))),
        and_(StaffingPerson.classification == "manager", StaffingLeadershipAssignment.unit_id.in_(parents)),
    )).order_by(StaffingPerson.id).all()
    people = {person.id: person for person, _ in records}
    managers = {}
    for person, unit_id in records:
        if person.classification == "manager":
            managers.setdefault(unit_id, set()).add(person.id)
    users = _linked_user_ids_by_person(people.values())
    recipients = {}
    for row in requests:
        ids = {person_id for person_id in _decode_person_ids(row.routed_approver_person_ids_json)
               if person_id in people and people[person_id].classification != "manager"}
        for unit_id in (row.source_work_area_unit_id, row.destination_work_area_unit_id):
            visited = set()
            while unit_id and unit_id not in visited:
                visited.add(unit_id)
                ids.update(managers.get(unit_id, ()))
                unit_id = parents.get(unit_id)
        recipients[row.id] = {user_id for person_id in ids for user_id in users.get(person_id, ())}
    return recipients


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
