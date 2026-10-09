"""User-scoped Dispatch organization, isolated from operational data and revisions."""
from app.extensions import db
from app.models import NeoScorpionDispatcherCheck, SortDateMission


class DispatcherCheckConflict(ValueError):
    pass


def checked_dispatch_mission_ids(operation, user):
    if not operation or not getattr(user, "is_authenticated", False):
        return set()
    return {mission_id for (mission_id,) in db.session.query(
        NeoScorpionDispatcherCheck.sort_date_mission_id).filter_by(
            sort_date_operation_id=operation.id, user_id=user.id, checked=True).all()}


def save_dispatcher_check(gateway, user, form):
    from app.services.neoscorpion import current_sort_operation

    operation = current_sort_operation(gateway)
    if not operation or str(operation.id) != str(form.get("operation_id", "")):
        raise DispatcherCheckConflict("The current sort changed. Refresh Dispatch and try again.")
    if form.get("checked") not in ("0", "1") or form.get("expected_checked") not in ("0", "1"):
        raise ValueError("Select a valid dispatcher checkbox state.")
    try:
        mission_id = int(form.get("mission_id", ""))
    except (TypeError, ValueError):
        raise ValueError("Select a current departure mission.") from None
    if not 0 < mission_id <= 2147483647:
        raise ValueError("Select a current departure mission.")
    mission = SortDateMission.query.filter_by(id=mission_id,
        sort_date_operation_id=operation.id, mission_type="departure").first()
    if not mission or mission.departure_status == "cancelled":
        raise ValueError("Departure mission was not found for the current sort.")
    record = NeoScorpionDispatcherCheck.query.filter_by(user_id=user.id,
        sort_date_operation_id=operation.id, sort_date_mission_id=mission.id).with_for_update().first()
    previous = bool(record and record.checked)
    checked = form["checked"] == "1"
    if previous != (form["expected_checked"] == "1") and previous != checked:
        raise DispatcherCheckConflict("Your checkbox changed in another window. Refresh and try again.")
    if previous != checked:
        if record is None:
            record = NeoScorpionDispatcherCheck(user_id=user.id,
                sort_date_operation_id=operation.id, sort_date_mission_id=mission.id)
            db.session.add(record)
        record.checked = checked
        db.session.flush()
    return {"checked": checked, "changed": previous != checked, "mission_id": mission.id,
            "operation_id": operation.id}
