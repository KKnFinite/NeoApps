from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingAttendanceOutcome as Outcome, StaffingDailyAttendance as Detail,
    StaffingRosterClose as Close, StaffingRosterPlacement as Placement, StaffingRosterRevision as Revision,
    StaffingTimecardEdit as Edit, StaffingTimecardSlice as Slice,
)
from app.services import background_deadlines
from app.services import neostaffing_history_retention as retention
from app.services.neostaffing_historical_records import _roster_as_of
from app.services.neostaffing_history_legacy import audit_or_import_legacy_marks
from tests.test_neostaffing_v11_workspace import site  # noqa: F401


@pytest.mark.parametrize(("as_of", "cutoff"), [
    (date(2024, 11, 30), date(2024, 2, 29)),
    (date(2025, 11, 30), date(2025, 2, 28)),
    (date(2026, 1, 31), date(2025, 4, 30)),
    (date(2026, 12, 1), date(2026, 3, 1)),
])
def test_nine_calendar_months_clamp_to_valid_dates(as_of, cutoff):
    assert retention.retention_cutoff(as_of) == cutoff


def outcome(operation_id, person_id, sort_id, area_id, day):
    return Outcome(sort_date_operation_id=operation_id, person_id=person_id, gateway_code="RFD",
        sort_name="night", sort_unit_id=sort_id, sort_date=day, work_area_id=area_id, employee_id=str(person_id),
        employee_name="Synthetic Employee", status="call_in", recorded_at=datetime(2026, 11, 30),
        updated_at=datetime(2026, 11, 30))


def closed(operation_id, sort_id, day):
    opened = datetime.combine(day, datetime.min.time()) + timedelta(hours=21)
    return Close(sort_date_operation_id=operation_id, gateway_code="RFD", sort_name="night", sort_date=day,
                 sort_unit_id=sort_id, opens_at=opened, closes_at=opened + timedelta(hours=8),
                 sealed_at=opened + timedelta(hours=9))


def test_purge_cutoff_keeps_the_boundary_night_and_withdraws_partial_roster(site):
    sort, _, _, area, _ = site._hierarchy()
    site.app.config.update(NEOSTAFFING_HISTORY_ENABLED=True,
                           CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE=datetime(2026, 11, 30, 22))
    day = date(2026, 2, 28)
    earlier = day - timedelta(days=1)
    op = site._night_operation()
    gateway_id = op.gateway_id
    site._user("retention-master", "master")
    site._login("retention-master")
    db.session.add_all([closed(701, sort.id, earlier), closed(702, sort.id, day)])
    db.session.flush()
    for operation_id, sort_day, ids in ((701, earlier, (1, 2, 3)), (702, day, (1,))):
        for person_id in ids:
            db.session.add(outcome(operation_id, person_id, sort.id, area.id, sort_day))
            db.session.add(Placement(sort_date_operation_id=operation_id, person_id=person_id, work_area_id=area.id,
                area_path="Historical Area", employee_id=str(person_id), employee_name="Synthetic Employee",
                classification="part_time"))
    db.session.commit()
    at = datetime(2026, 12, 1, 5, 59)  # Still November 30 locally.
    assert retention.history_cutoff(at=at) == day
    assert retention.history_cutoff(at=at + timedelta(minutes=1)) == date(2026, 3, 1)
    assert retention.purge_expired_history(gateway_id, at=at) == retention.PurgeResult()
    assert Outcome.query.count() == Placement.query.count() == 4
    retained = site.client.get(f"/neostaffing/reports?attendance_date={day}")
    expired = site.client.get(f"/neostaffing/reports?attendance_date={earlier}")
    assert "Complete roster" in retained.text
    assert "No verified roster" in expired.text and "Synthetic Employee" not in expired.text

    site.app.config["NEOSTAFFING_HISTORY_PURGE_ENABLED"] = True
    assert retention.purge_history_batch("RFD", day, at, batch_size=2) == retention.PurgeResult(2, 2, 0, 0)
    db.session.rollback()
    assert db.session.get(Close, 701).sealed_at is not None and Placement.query.count() == 4
    assert retention.purge_history_batch("RFD", day, at, batch_size=2) == retention.PurgeResult(2, 2, 0, 0)
    db.session.commit()
    assert db.session.get(Close, 701).sealed_at is None  # Safe even for a request started earlier.
    assert retention.purge_history_batch("RFD", day, at, batch_size=2) == retention.PurgeResult(1, 1, 1, 0)
    db.session.commit()
    assert retention.purge_history_batch("RFD", day, at) == retention.PurgeResult()
    assert Outcome.query.one().sort_date == day
    assert db.session.get(SortDateOperation, op.id)
    assert db.session.get(Close, 702).sealed_at is not None


def test_journal_compaction_preserves_predecessors_tombstones_and_crossing_transactions(site):
    site.app.config.update(NEOSTAFFING_HISTORY_ENABLED=True, NEOSTAFFING_HISTORY_PURGE_ENABLED=True)
    cutoff = date(2026, 2, 28)
    floor = datetime(2026, 2, 27)
    db.session.add(Revision(kind="baseline", entity_id=0, changed_at=floor - timedelta(days=100)))
    for entity, offset, change_set, state in (
        (1, -4, 1, {"active": True, "name": "obsolete label"}),
        (1, -3, 2, {"active": True, "name": "predecessor"}),
        (1, -1, 3, {"active": True, "name": "before floor, same transaction"}),
        (1, +2, 3, {"active": True, "name": "after floor"}),
        (2, -4, 1, {"active": True, "name": "removed employee"}),
        (2, -2, 2, None),
    ):
        db.session.add(Revision(kind="person", entity_id=entity, changed_at=floor + timedelta(hours=offset),
                               change_set_id=change_set, state=state))
    db.session.commit()
    timestamps = [floor + timedelta(minutes=1), floor + timedelta(hours=1), floor + timedelta(days=1)]
    snapshots = [_roster_as_of(stamp) for stamp in timestamps]
    for remaining in (1, 0):
        assert retention.purge_history_batch("RFD", cutoff, floor, batch_size=1).revisions == 1
        db.session.commit()
        assert len([row for row in Revision.query.all() if row.change_set_id == 1]) == remaining
        assert [_roster_as_of(stamp) for stamp in timestamps] == snapshots
    assert retention.purge_history_batch("RFD", cutoff, floor).revisions == 0
    assert Revision.query.filter_by(entity_id=2).one().state is None


def test_legacy_audit_withholds_invalid_links_and_never_clobbers_a_clear(site):
    sort, _, _, area, _ = site._hierarchy()
    operation = site._night_operation()
    day = operation.sort_date
    people = {key: site._person(key, area) for key in ("DAILY", "UNLINKED", "BAD", "CLEAR", "OLD")}
    old = SortDateOperation(gateway_id=operation.gateway_id, gateway_code=operation.gateway_code,
        sort_date=retention.retention_cutoff(day) - timedelta(days=1), sort_name=operation.sort_name)
    db.session.add(old)
    db.session.flush()
    details = [
        Detail(sort_date_operation_id=op_id, person_id=people[key].id, attendance_date=source_date,
            sort_unit_id=sort.id, work_area_unit_id=area.id, status="here", note="private field")
        for key, op_id, source_date in (("DAILY", operation.id, day), ("UNLINKED", None, day),
                                       ("BAD", operation.id, day - timedelta(days=1)), ("OLD", old.id, old.sort_date))
    ]
    slice_row = Slice(sort_date_operation_id=operation.id, person_id=people["CLEAR"].id, sort_unit_id=sort.id,
        work_area_unit_id=area.id, workday_date=day, attendance_status="", version=2,
        context_json='{"employee":"Recorded Name","employee_id":"CLEAR","classification_at_recording":"part_time"}')
    unsupported = Slice(sort_date_operation_id=operation.id, person_id=people["OLD"].id, sort_unit_id=sort.id,
        work_area_unit_id=area.id, workday_date=day, attendance_status="here", version=1,
        context_json='{"employee":"Unverified Person","employee_id":"OLD"}')
    db.session.add_all([*details, slice_row, unsupported])
    stamp = datetime.combine(day, datetime.min.time())
    for version, status in ((1, "call_in"), (2, "")):
        db.session.add(Edit(slice=slice_row, version=version, recorded_at=stamp + timedelta(hours=version),
                            snapshot_json='{"status":"' + status + '"}'))
    db.session.commit()
    statements = []

    def observe(_connection, _cursor, statement, *_args):
        statements.append(statement.split()[0].upper())

    event.listen(db.engine, "before_cursor_execute", observe)
    try:
        audit = audit_or_import_legacy_marks(as_of=day)
    finally:
        event.remove(db.engine, "before_cursor_execute", observe)
    assert set(statements) == {"SELECT"}
    assert audit.daily_attendance == audit.timecard == audit.explicit_clears == 1
    assert audit.unknown_classification == 2 and audit.earliest_mark == day.isoformat()
    assert audit.withheld == {"unlinked_daily_attendance": 1, "invalid_daily_attendance_attribution": 1,
                              "unverified_timecard": 1}
    assert Outcome.query.count() == 0
    assert audit_or_import_legacy_marks(write=True, as_of=day).inserted == 2
    db.session.commit()
    assert audit_or_import_legacy_marks(write=True, as_of=day).inserted == 0
    assert db.session.get(Outcome, (operation.id, people["DAILY"].id)).source_record_id == details[0].id
    withdrawal = db.session.get(Outcome, (operation.id, people["CLEAR"].id))
    assert withdrawal.status is None and withdrawal.source_kind == "timecard"
    assert withdrawal.source_record_id == slice_row.id and withdrawal.classification is None
    assert not Placement.query.count() and Detail.query.count() == 4


def test_existing_scheduler_recovers_purge_after_transaction_failure(site):
    sort, _, _, area, _ = site._hierarchy()
    gateway_id = site._night_operation().gateway_id
    site.app.config.update(NEOSTAFFING_HISTORY_ENABLED=True, FROST_AUTO_COLLECTION=False)
    db.session.add(outcome(100, 31, sort.id, area.id, date(2026, 1, 1)))
    db.session.commit()
    at = datetime(2026, 12, 1)
    with site.app.app_context():
        due, _ = background_deadlines.plan(at, safety=True)
        assert "staffing_history" in due[gateway_id] and "staffing_history_purge" not in due[gateway_id]
        site.app.config["NEOSTAFFING_HISTORY_PURGE_ENABLED"] = True
        due, _ = background_deadlines.plan(at, safety=False)
        assert "staffing_history_purge" not in due.get(gateway_id, ())
        due, _ = background_deadlines.plan(at, safety=True)
        assert "staffing_history_purge" in due[gateway_id]
        original = retention.purge_history_batch

        def fail_before_commit(*args, **kwargs):
            assert original(*args, **kwargs).outcomes == 1
            raise RuntimeError("synthetic interruption")

        with patch.object(retention, "purge_history_batch", side_effect=fail_before_commit):
            background_deadlines.perform(gateway_id, {"staffing_history_purge"})
        assert Outcome.query.count() == 1
        # Next safety pass may retry using a new database session.
        background_deadlines.perform(gateway_id, {"staffing_history_purge"})
        assert Outcome.query.count() == 0
