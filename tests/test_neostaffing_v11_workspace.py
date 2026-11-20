"""Authorization, sort identity and historical facts behind the V11 views."""
from datetime import datetime, timedelta
from html import unescape
import re

import pytest
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingDailyAttendance, StaffingLeadershipAssignment, StaffingPerson,
    StaffingTimecardSlice, StaffingWorkAssignment,
)
from app.services import neostaffing as staffing
from tests import test_neostaffing_daily_attendance as attendance_fixture


@pytest.fixture
def site():
    fixture = attendance_fixture.NeoStaffingDailyAttendanceTest()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def token(response, person_id):
    return unescape(re.search(fr'name="original_{person_id}" value="([^"]+)"', response.text)[1])


def board_move_values(site, person, destination, source):
    board = site.client.get("/neostaffing/board")
    return {
        "scope_stamp": unescape(re.search(r'data-scope-stamp="([^"]+)"', board.text)[1]),
        "expected_person_version": staffing.entity_version(person),
        "expected_assignment_version": str(person.shift_flow_version),
        "source_assignment_id": str(source.id) if source else "",
        "destination_work_area_id": destination.id,
        "confirm_transfer": "yes",
    }


def test_board_transfer_preserves_combo_placement_history_and_attendance(site, monkeypatch):
    sort, _, _, area, destination = site._hierarchy()
    _, _, _, other_sort_area, _ = site._hierarchy("Day")
    current = site._night_operation()
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [current])
    person = site._person("BOARD-COMBO", area)
    person.classification = "full_time_combo"
    source = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).one()
    secondary = staffing.assign_work_area(person, other_sort_area)
    attendance = StaffingDailyAttendance(attendance_date=current.sort_date,
        sort_unit_id=sort.id, work_area_unit_id=area.id, sort_date_operation_id=current.id,
        person_id=person.id, status="here")
    db.session.add(attendance)
    site._user("board-admin", "master")
    db.session.commit()
    site._login("board-admin")
    path = f"/neostaffing/board/placement/{person.id}"
    values = board_move_values(site, person, destination, source)
    assert site.client.post(path, data={**values, "source_assignment_id": secondary.id}).status_code == 403
    assert site.client.post(path, data={**values, "destination_work_area_id": other_sort_area.id}).status_code == 403
    assert site.client.post(path, data={**values, "scope_stamp": "other Sort"}).status_code == 409
    saved = site.client.post(path, data=values)
    assert saved.status_code == 200, saved.text
    assert site.client.post(path, data=values).status_code == 409
    db.session.expire_all()
    assert not source.active and source.work_area_unit_id == area.id
    assert secondary.active and secondary.work_area_unit_id == other_sort_area.id
    assert {row.work_area_unit_id for row in person.work_assignments if row.active} == {
        destination.id, other_sort_area.id,
    }
    assert attendance.status == "here" and attendance.work_area_unit_id == area.id
    assert person.shift_flow_plan is None


def test_board_denies_edits_to_other_scopes_and_watchers(site, monkeypatch):
    _, _, department, area, outside = site._hierarchy()
    current = site._night_operation()
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [current])
    person = site._person("BOARD-SCOPED", area)
    source = StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).one()
    site._user("board-scope-master", "master")
    site._user("board-watcher", "watcher")
    db.session.commit()
    site._login("board-scope-master")
    site.client.post("/neostaffing/workspace-scope", data={"unit_id": department.id})
    path = f"/neostaffing/board/placement/{person.id}"
    assert f'data-board-area="{outside.id}"' not in site.client.get("/neostaffing/board").text
    values = board_move_values(site, person, outside, source)
    assert site.client.post(path, data=values).status_code == 403
    site._login("board-watcher")
    board = site.client.get("/neostaffing/board")
    assert board.status_code == 200 and "data-board-dialog" not in board.text
    assert site.client.post(path, data=values).status_code == 403
    db.session.expire_all()
    assert source.active and source.work_area_unit_id == area.id


def test_switching_scope_from_shift_flow_opens_the_matching_staffing_board(site, monkeypatch):
    night, _, _, _, _ = site._hierarchy()
    current = site._night_operation()
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [current])
    ramp = staffing.create_unit({"unit_type": "operation", "name": "Ramp", "parent_id": night.id})
    shift = staffing.create_unit({"unit_type": "department", "name": "Shift", "parent_id": ramp.id})
    door = staffing.create_unit({"unit_type": "work_area", "name": "Door 13", "parent_id": shift.id})
    aviation = staffing.create_unit({"unit_type": "department", "name": "Aviation Services", "parent_id": ramp.id})
    deice = staffing.create_unit({"unit_type": "work_area", "name": "Deice", "parent_id": aviation.id})
    shifter = site._person("IN-SHIFT-ROSTER", door)
    staffing.create_shift_flow_plan(shifter, {"shift_flow_sort_start_work_area_id": door.id}, door)
    deicer = site._person("IN-AVIATION-ROSTER", deice)
    site._user("board-scope-switch", "master")
    db.session.commit()
    site._login("board-scope-switch")

    def select_scope(unit, endpoint="neostaffing.shift_flow"):
        return site.client.post("/neostaffing/workspace-scope", data={
            "endpoint": endpoint, "unit_id": unit.id if unit else "",
        }, follow_redirects=True)

    shift_page = select_scope(shift, "neostaffing.board")
    assert shift_page.request.path == "/neostaffing/shift-flow"
    assert f'data-roster-person="{shifter.id}"' in shift_page.text
    for unit in (aviation, deice, ramp, None):
        board = select_scope(unit)
        assert board.request.path == "/neostaffing/board"
        assert f'data-board-area="{deice.id}"' in board.text
        assert f'data-board-person="{deicer.id}"' in board.text
        assert f'data-board-person="{shifter.id}"' not in board.text
        assert "data-roster-person=" not in board.text
    select_scope(aviation, "neostaffing.attendance")
    bookmarked = site.client.get("/neostaffing/shift-flow", follow_redirects=True)
    assert bookmarked.request.path == "/neostaffing/board"
    assert "data-roster-person=" not in bookmarked.text
    assert select_scope(door).request.path == "/neostaffing/shift-flow"


def test_scope_follows_sort_identity_and_current_authority(site, monkeypatch):
    sort, operation, department, area, outside = site._hierarchy()
    current = site._night_operation()
    # Hold the canonical operation constant while moving the clock over midnight.
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [current])
    actor = site._user("scoped-manager", "operator")
    leader = StaffingPerson.query.filter_by(employee_id=actor.employee_id).one()
    leader.classification = "full_time_supervisor"
    grant = StaffingLeadershipAssignment.query.filter_by(person_id=leader.id).one()
    grant.unit = department
    grant.leadership_level = "department"
    db.session.commit()
    site._login("scoped-manager")
    accepted = site.client.post("/neostaffing/workspace-scope", data={
        "endpoint": "neostaffing.reports", "unit_id": area.id,
        "report_type": "seniority", "classification": "part_time",
    })
    assert accepted.status_code == 302
    assert "classification=part_time" in accepted.location and "report_type=seniority" in accepted.location
    with site.client.session_transaction() as session:
        assert session["staffing_scope"]["id"] == area.id
    denied = site.client.post("/neostaffing/workspace-scope", data={"unit_id": outside.id})
    assert denied.status_code == 302
    with site.client.session_transaction() as session:
        assert session["staffing_scope"]["id"] == area.id
    path = " / ".join(unit.name for unit in (sort, operation, department, area))
    selected = f"<strong data-scope-current>{path}</strong>"
    assert selected in site.client.get("/neostaffing").text
    site.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(
        current.sort_date + timedelta(days=1), datetime.min.time(),
    ) + timedelta(hours=1)
    assert selected in site.client.get("/neostaffing").text

    replacement = SortDateOperation(gateway_id=current.gateway_id, gateway_code=current.gateway_code,
                                    sort_date=current.sort_date + timedelta(days=1), sort_name=current.sort_name)
    db.session.add(replacement)
    db.session.commit()
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [replacement])
    assert '<strong data-scope-current>All My Areas</strong>' in site.client.get("/neostaffing").text
    grant.active = False
    db.session.commit()
    assert f'name="unit_id" value="{area.id}"' not in site.client.get("/neostaffing").text


def test_attendance_quick_filter_bulk_post_rejects_an_authorized_employee_in_another_area(site):
    sort, _, _, area, outside = site._hierarchy()
    operation = site._night_operation()
    first = site._person("QUICK-IN", area)
    second = site._person("QUICK-OUT", outside)
    site._user("quick-filter-manager", "master")
    db.session.commit()
    site._login("quick-filter-manager")
    response = site.client.get("/neostaffing/attendance")
    assert f'data-attendance-work-area="{area.id}"' in response.text
    assert f'data-attendance-work-area="{outside.id}"' in response.text
    form = MultiDict({
        "sort_date_operation_id": operation.id, "sort_id": sort.id, "work_area_ids": area.id,
        "bulk_status": "here", f"status_{first.id}": "", f"original_{first.id}": token(response, first.id),
    })
    forged = form.copy()
    forged.add(f"status_{second.id}", "")
    forged.add(f"original_{second.id}", token(response, second.id))
    rejected = site.client.post("/neostaffing/attendance/save", data=forged)
    assert rejected.status_code == 400 and "outside the selected scope" in rejected.json["error"]
    assert StaffingDailyAttendance.query.count() == 0
    saved = site.client.post("/neostaffing/attendance/save", data=form)
    assert saved.status_code == 200 and saved.json["saved"] == 1
    assert StaffingDailyAttendance.query.one().person_id == first.id


def test_status_only_autosave_preserves_notes_and_rejects_stale_edits(site):
    sort, _, _, area, _ = site._hierarchy()
    operation = site._night_operation()
    person = site._person("NOTE-KEEP", area)
    actor = site._user("note-manager", "operator")
    record = StaffingDailyAttendance(
        attendance_date=operation.sort_date, sort_unit_id=sort.id, work_area_unit_id=area.id,
        sort_date_operation_id=operation.id, person_id=person.id, status="here",
        note="Original protected note", recorded_by_user_id=actor.id,
    )
    db.session.add(record)
    db.session.commit()
    site._login("note-manager")
    response = site.client.get("/neostaffing/attendance")
    assert response.status_code == 200
    assert "Original protected note" not in response.text
    assert f'name="note_{person.id}"' not in response.text
    original = token(response, person.id)
    signed = staffing._attendance_snapshot_serializer().loads(original)
    assert "note" not in signed["original"]
    args = {
        "sort_date_operation_id": operation.id, "sort_id": sort.id,
        f"status_{person.id}": "call_in", f"original_{person.id}": original,
        f"note_{person.id}": "Forged replacement",
    }
    saved = site.client.post("/neostaffing/attendance/save", data=args)
    assert saved.status_code == 200
    assert "note" not in saved.json["rows"][0]
    db.session.expire_all()
    assert record.status == "call_in"
    assert record.note == "Original protected note"
    stale = site.client.post("/neostaffing/attendance/save", data=args)
    assert stale.status_code == 409
    assert "note" not in stale.json["row"]


def test_retained_reports_use_original_area_and_recorded_classification(site):
    _, _, _, original_area, new_area = site._hierarchy()
    operation = site._night_operation()
    person = site._person("REPORT-KEEP", original_area)
    site._user("history-master", "master")
    site._user("history-view", "watcher")
    db.session.commit()
    site._login("history-master")
    saved = site.client.post("/neostaffing/attendance/save", data={
        "sort_date_operation_id": operation.id,
        f"status_{person.id}": "here", f"original_{person.id}": token(site.client.get("/neostaffing/attendance"), person.id),
    })
    assert saved.status_code == 200
    retained = StaffingTimecardSlice.query.filter_by(person_id=person.id).one()
    assert retained.work_area_unit_id == original_area.id
    # The report must still work from retained timecards when daily detail has rolled off.
    StaffingDailyAttendance.query.filter_by(person_id=person.id).delete()
    staffing.assign_work_area(person, new_area)
    db.session.commit()
    daily = site.client.get(f"/neostaffing/reports?report_type=staffing&work_area_id={original_area.id}&classification=part_time")
    assert "1 HERE" in daily.text
    assert "Headcounts cover recorded attendance" not in daily.text
    assert "classification captured at the time" in daily.text
    other = site.client.get(f"/neostaffing/reports?work_area_id={new_area.id}")
    assert "1 HERE" not in other.text
    path = f"/neostaffing/reports?report_type=attendance&search=REPORT-KEEP&person_id={person.id}"
    history = site.client.get(path)
    assert f'datetime="{operation.sort_date}"' in history.text
    assert 'type="search" name="search"' in history.text
    assert history.text.count('data-v11-report-select') == 1
    assert "Vacation Calendars" not in history.text
    site.client.post("/logout")
    site._login("history-view")
    limited = site.client.get(path)
    assert f'datetime="{operation.sort_date}"' not in limited.text
