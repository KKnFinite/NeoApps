"""Authorization, sort identity and historical facts behind the V11 views."""
from datetime import datetime, timedelta
from html import unescape
import re

import pytest

from app.extensions import db
from app.models import (
    SortDateOperation, StaffingDailyAttendance, StaffingLeadershipAssignment, StaffingPerson,
    StaffingTimecardSlice,
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


def test_scope_follows_sort_identity_and_current_authority(site, monkeypatch):
    _, _, department, area, outside = site._hierarchy()
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
    assert f'value="{area.id}" selected' in site.client.get("/neostaffing").text
    site.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(
        current.sort_date + timedelta(days=1), datetime.min.time(),
    ) + timedelta(hours=1)
    assert f'value="{area.id}" selected' in site.client.get("/neostaffing").text

    replacement = SortDateOperation(gateway_id=current.gateway_id, gateway_code=current.gateway_code,
                                    sort_date=current.sort_date + timedelta(days=1), sort_name=current.sort_name)
    db.session.add(replacement)
    db.session.commit()
    monkeypatch.setattr(staffing, "current_operations_for_gateway", lambda gateway: [replacement])
    assert f'value="{area.id}" selected' not in site.client.get("/neostaffing").text
    grant.active = False
    db.session.commit()
    assert f'value="{area.id}"' not in site.client.get("/neostaffing").text


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
