from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingAttendanceOutcome, StaffingDailyAttendance, StaffingRosterClose,
    StaffingRosterPlacement, StaffingRosterRevision, StaffingTimecardSlice, StaffingUnit,
    SortTimelineSettings, SortTimelineSortSetting,
)
from app.services import neostaffing as staffing
from app.services import neostaffing_historical_records as history
from app.services.neostaffing_attendance_history import process_attendance_rollover
from app.services.neostaffing_history_schema import SOURCES
from tests.test_neostaffing_v11_workspace import site, token  # noqa: F401


def seed_roster(at):
    from app.models import StaffingPerson, StaffingWorkAssignment

    db.session.add(StaffingRosterRevision(kind="baseline", entity_id=0, changed_at=at))
    for model, (_, kind, fields) in zip((StaffingUnit, StaffingPerson, StaffingWorkAssignment), SOURCES):
        db.session.add_all([
            StaffingRosterRevision(kind=kind, entity_id=row.id, changed_at=at,
                                   state={field: getattr(row, field) for field in fields})
            for row in model.query.all()
        ])


def window(operation, opens=time(21), closes=time(5)):
    settings = SortTimelineSettings(gateway_id=operation.gateway_id, gateway_code=operation.gateway_code)
    setting = SortTimelineSortSetting(timeline_settings=settings, gateway_id=operation.gateway_id,
                                    gateway_code=operation.gateway_code, sort_name=operation.sort_name,
                                    sort_window_start_local=opens, sort_window_end_local=closes)
    db.session.add(setting)
    db.session.flush()
    return history._window(operation, setting, ZoneInfo("America/Chicago"))


def test_night_open_and_close_with_concurrent_transaction_boundary_and_combo(site):
    site.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
    night, _, _, area, other = site._hierarchy()
    day, _, _, day_area, _ = site._hierarchy(sort_name="Day")
    op = site._night_operation()
    combo = site._person("COMBO-HISTORY", area)
    combo.classification = "full_time_combo"
    staffing.assign_work_area(combo, day_area)
    night_open, night_close = window(op)
    day_op = SortDateOperation(gateway_id=op.gateway_id, gateway_code=op.gateway_code,
                               sort_name="day", sort_date=op.sort_date)
    db.session.add(day_op)
    db.session.flush()
    db.session.add(SortTimelineSortSetting(timeline_settings=SortTimelineSettings.query.one(),
        gateway_id=op.gateway_id, gateway_code=op.gateway_code, sort_name="day",
        sort_window_start_local=time(8), sort_window_end_local=time(16), updated_at=night_open - timedelta(days=1)))
    op.created_at = day_op.created_at = night_open - timedelta(days=1)
    SortTimelineSortSetting.query.filter_by(sort_name="night").one().updated_at = op.created_at
    db.session.flush()
    seed_roster(night_open - timedelta(days=1))
    original = next(row for row in combo.work_assignments if row.work_area_unit_id == area.id)
    for stamp, destination, change_set in (
        (night_open + timedelta(hours=1), other.id, 1),   # Committed within the Sort.
        (night_close - timedelta(seconds=1), area.id, 2),  # Cross-boundary transaction is excluded in full.
        (night_close + timedelta(seconds=1), area.id, 2),
    ):
        db.session.add(StaffingRosterRevision(kind="assignment", entity_id=original.id, changed_at=stamp,
            change_set_id=change_set, state={"person_id": combo.id, "work_area_unit_id": destination, "active": True}))
    db.session.commit()
    # 1 AM Central still belongs to the preceding Sort date.
    assert history.discover_and_seal(op.gateway_id, at=night_close - timedelta(hours=4))
    assert db.session.get(StaffingRosterClose, op.id).sealed_at is None
    assert history.discover_and_seal(op.gateway_id, at=night_close)
    close = db.session.get(StaffingRosterClose, op.id)
    assert close.sort_date == op.sort_date and close.sort_unit_id == night.id
    assert close.closes_at == night_close and night_close - night_open == timedelta(hours=8)
    assert not history.discover_and_seal(op.gateway_id, at=night_close + timedelta(minutes=1))
    assert {(row.sort_date_operation_id, row.work_area_id) for row in StaffingRosterPlacement.query.all()} == {
        (op.id, other.id), (day_op.id, day_area.id),
    }
    site._user("historical-master", "master")
    site._login("historical-master")
    report = site.client.get(f"/neostaffing/reports?attendance_date={op.sort_date}&classification=full_time_combo")
    assert report.status_code == 200
    assert "Complete roster at the configured Sort close" in report.text
    assert "Assigned employees" in report.text and "<strong>1</strong>" in report.text
    assert report.text.count(combo.full_name) == 2
    assert report.text.count('class="v11-status" data-mark=""') == 2
    scoped = site.client.get(f"/neostaffing/reports?attendance_date={op.sort_date}&sort_id={day.id}")
    areas = scoped.text.split('class="v11-report-areas"', 1)[1]
    assert "Day /" in areas and "Night /" not in areas
    absent = site.client.get(f"/neostaffing/reports?attendance_date={op.sort_date - timedelta(days=1)}")
    assert "No verified roster or retained attendance" in absent.text


def test_canonical_marks_correction_rollback_transfer_and_retention(site):
    site.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
    _, _, _, original, destination = site._hierarchy()
    op = site._night_operation()
    person = site._person("RECORDED-ID", original)
    site._user("historical-manager", "master")
    site._login("historical-manager")
    person_id, operation_id, day = person.id, op.id, op.sort_date

    def save(status, snapshot=None):
        return site.client.post("/neostaffing/attendance/save", data={
            "sort_date_operation_id": operation_id, f"status_{person_id}": status,
            f"original_{person_id}": snapshot or token(site.client.get("/neostaffing/attendance"), person_id),
        })

    first = token(site.client.get("/neostaffing/attendance"), person_id)
    assert save("here", first).status_code == 200
    result = db.session.get(StaffingAttendanceOutcome, (operation_id, person_id))
    assert result.status == "here" and result.classification == "part_time"
    assert save("no_call", first).status_code == 409
    assert save("call_in").status_code == 200
    db.session.refresh(result)
    assert result.status == "call_in"
    assert save("").status_code == 200
    db.session.refresh(result)
    assert result.status is None  # Older source rows cannot resurrect a cleared mark.
    assert save("call_in").status_code == 200
    record = StaffingDailyAttendance.query.filter_by(person_id=person_id).one()
    record.note = "isolated HR note"
    db.session.commit()
    staffing.assign_work_area(person, destination)
    person.classification = "pre_seniority"
    person.employee_id = "NEW-EMPLOYEE-ID"
    db.session.commit()
    next_op = SortDateOperation(gateway_id=op.gateway_id, gateway_code=op.gateway_code,
                               sort_date=day + timedelta(days=8), sort_name=op.sort_name)
    db.session.add(next_op)
    db.session.commit()
    rollover = process_attendance_rollover(next_op, now_local=datetime.combine(next_op.sort_date, time(22)))
    db.session.commit()
    assert rollover.purged_detail_count == 1
    StaffingTimecardSlice.query.filter_by(person_id=person_id).delete()
    db.session.commit()
    route = (f"/neostaffing/reports?report_type=attendance&search=RECORDED-ID&person_id={person_id}"
             f"&from_date={day}&through_date={day}&classification=part_time")
    statements = []
    def collect(_conn, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())
    event.listen(db.engine, "before_cursor_execute", collect)
    try:
        report = site.client.get(route + f"&work_area_id={original.id}")
        unauthorized_scope = site.client.get(route + f"&work_area_id={destination.id}")
    finally:
        event.remove(db.engine, "before_cursor_execute", collect)
    assert report.status_code == 200
    assert 'data-mark="call_in"' in report.text and "isolated HR note" not in report.text
    assert 'data-mark="call_in"' not in unauthorized_scope.text
    assert not {"INSERT", "UPDATE", "DELETE"}.intersection(statements)


def test_insufficient_seed_and_ambiguous_boundaries_do_not_seal(site):
    site.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
    site._hierarchy()
    op = site._night_operation()
    opens, closes = window(op)
    assert history._utc_boundary(datetime(2026, 3, 8, 2, 15), ZoneInfo("America/Chicago")) is None
    assert history._utc_boundary(datetime(2026, 11, 1, 1, 15), ZoneInfo("America/Chicago")) is None
    seed_roster(opens + timedelta(seconds=1))
    db.session.commit()
    assert not history.discover_and_seal(op.gateway_id, at=closes + timedelta(minutes=10))
    assert not StaffingRosterClose.query.all() and not StaffingRosterPlacement.query.all()


def test_rollover_preserves_linked_legacy_marks_but_excludes_unlinked_details(site):
    site.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
    sort, _, _, area, _ = site._hierarchy()
    prior = site._night_operation()
    current = SortDateOperation(gateway_id=prior.gateway_id, gateway_code=prior.gateway_code,
                                sort_date=prior.sort_date + timedelta(days=8), sort_name=prior.sort_name)
    db.session.add(current)
    linked, unlinked = [site._person(key, area) for key in ("LEGACY-LINKED", "LEGACY-UNLINKED")]
    db.session.add_all([
        StaffingDailyAttendance(attendance_date=prior.sort_date, sort_unit_id=sort.id, work_area_unit_id=area.id,
                                sort_date_operation_id=operation_id, person_id=person.id, status="no_call",
                                note="Must never leave original attendance storage")
        for person, operation_id in ((linked, prior.id), (unlinked, None))
    ])
    db.session.commit()
    result = process_attendance_rollover(current, now_local=datetime.combine(current.sort_date, time(22)))
    assert result.purged_detail_count == 2
    db.session.rollback()
    assert StaffingDailyAttendance.query.count() == 2 and StaffingAttendanceOutcome.query.count() == 0
    result = process_attendance_rollover(current, now_local=datetime.combine(current.sort_date, time(22)))
    db.session.commit()
    assert result.purged_detail_count == 2
    assert not StaffingDailyAttendance.query.all()
    retained = StaffingAttendanceOutcome.query.one()
    assert retained.person_id == linked.id and retained.status == "no_call" and retained.classification is None
    assert "note" not in StaffingAttendanceOutcome.__table__.columns


def test_cutover_copies_only_verified_linked_timecard_marks_and_is_idempotent(site):
    _, _, _, area, _ = site._hierarchy()
    op = site._night_operation()
    person = site._person("LEGACY-CUTOVER", area)
    site._user("cutover-master", "master")
    site._login("cutover-master")
    assert site.client.post("/neostaffing/attendance/save", data={
        "sort_date_operation_id": op.id,
        f"status_{person.id}": "here",
        f"original_{person.id}": token(site.client.get("/neostaffing/attendance"), person.id),
    }).status_code == 200
    StaffingDailyAttendance.query.delete()
    db.session.commit()
    assert history.import_available_timecard_marks() == 1
    db.session.commit()
    assert history.import_available_timecard_marks() == 0
    retained = StaffingAttendanceOutcome.query.one()
    assert (retained.sort_date_operation_id, retained.work_area_id, retained.status, retained.classification) == (
        op.id, area.id, "here", "part_time",
    )
