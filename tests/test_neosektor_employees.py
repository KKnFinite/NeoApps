"""Inbound Home ownership and node-independent attendance authorization."""
from datetime import date, datetime
from html import unescape
import re
import unittest

from sqlalchemy import event
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import (User, PortalAppAccess, StaffingUnit, StaffingPerson, StaffingLeadershipAssignment,
                        StaffingReportingRelationship, StaffingDailyAttendance,
                        StaffingAttendanceOccurrence)
from app.services import neostaffing as staffing
from app.services.neostaffing_attendance_authority import attendance_authority
from tests import test_neosektor_routes as sektor_fixture


class SektorEmployeesTest(unittest.TestCase):
    setUpBase = sektor_fixture.NeoSektorRoutesTest.setUp
    tearDown = sektor_fixture.NeoSektorRoutesTest.tearDown
    _login_approved_user = sektor_fixture.NeoSektorRoutesTest._login_approved_user
    _add_sort_operation = sektor_fixture.NeoSektorRoutesTest._add_sort_operation

    def setUp(self):
        self.setUpBase()
        self._login_approved_user("operator")
        self.user = User.query.filter_by(username="sektor_operator_user").one()
        # Production-approved access, rather than the legacy membership-only
        # fixture whose first shell render backfills PortalAppAccess.
        if not PortalAppAccess.query.filter_by(user_id=self.user.id, app_code="neogateway").first():
            db.session.add(PortalAppAccess(user_id=self.user.id, app_code="neogateway", status="approved", role="operator", is_active=True))
        self.user.employee_id = "SUP-1"
        self.night = StaffingUnit(name="Night", unit_type="sort")
        self.ramp = StaffingUnit(name="Ramp", unit_type="operation", parent=self.night)
        self.shift = StaffingUnit(name="Shift", unit_type="department", parent=self.ramp)
        self.other = StaffingUnit(name="Other", unit_type="department", parent=self.ramp)
        self.areas = {key: StaffingUnit(name=name, unit_type="work_area", parent=self.shift)
                      for key, name in (("ebm", "East Ballmat"), ("wbm", "West Ballmat"),
                                        ("dis", "Discharge"), ("door", "Door 6"))}
        self.outside = StaffingUnit(name="Outside", unit_type="work_area", parent=self.other)
        db.session.add_all([self.night, self.ramp, self.shift, self.other, self.outside, *self.areas.values()])
        db.session.flush()
        self.manager = self.person("SUP-1", classification="part_time_supervisor")
        self.leadership = StaffingLeadershipAssignment(person=self.manager, unit=self.areas["ebm"], leadership_level="work_area")
        db.session.add(self.leadership)
        self.workers = {key: self.person("Worker-" + key, area=area) for key, area in self.areas.items()}
        self.peer = self.person("Peer-EBM", area=self.areas["ebm"])
        self.outsider = self.person("Out-of-scope", area=self.outside)
        db.session.add(StaffingReportingRelationship(person=self.workers["ebm"], reports_to_person=self.manager))
        db.session.commit()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 9, 12, 12)

    def person(self, code, area=None, classification="part_time"):
        person = staffing.create_person(dict(employee_id=code, first_name=code, last_name="Fixture",
                                             seniority_date="2020-01-01", classification=classification))
        if area:
            staffing.assign_work_area(person, area)
        return person

    def active_sort(self):
        self.operation = self._add_sort_operation(date(2026, 9, 12), "night")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 9, 12, 23)
        self.assertIsNotNone(staffing.current_night_attendance_operation())
        return staffing.current_night_attendance_operation()

    def page(self, area="ebm", view="all"):
        response = self.client.get(f"/neosektor/manage-employees?area={area}&view={view}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def form(self, person, status="here", area="ebm"):
        html = self.page(area)
        token = re.search(fr'name="original_{person.id}" value="([^"]+)"', html)
        return MultiDict({"sort_date_operation_id": str(staffing.current_night_attendance_operation().id),
                          f"original_{person.id}": unescape(token[1]), f"status_{person.id}": status})

    def test_navigation_and_nonmanagement_server_denial(self):
        self.assertIn(b'data-node-dashboard-tile="employees"', self.client.get('/neosektor').data)
        menu = self.client.get('/neosektor/manage-employees')
        for label in (b'EAST BALLMAT', b'WEST BALLMAT', b'DISCHARGE'):
            self.assertIn(label, menu.data)
        self.manager.classification = "part_time"
        db.session.commit()
        self.assertEqual(self.client.get('/neosektor/manage-employees').status_code, 403)
        self.assertEqual(self.client.post('/neosektor/manage-employees?area=ebm').status_code, 403)
        self.assertNotIn(b'data-node-dashboard-tile="employees"', self.client.get('/neosektor').data)

    def test_classification_levels_union_inactive_and_no_reports_to_grant(self):
        for classification in ("part_time_supervisor", "full_time_supervisor", "twenty_c_full_time_supervisor", "full_time_specialist"):
            self.manager.classification = classification
            db.session.flush()
            self.assertEqual(attendance_authority(self.user).work_area_ids, frozenset(a.id for a in self.areas.values()))
        self.manager.classification = "manager"
        db.session.flush()
        self.assertIn(self.outside.id, attendance_authority(self.user).work_area_ids)
        self.manager.classification = "division_manager"
        db.session.flush()
        self.assertIn(self.outside.id, attendance_authority(self.user).work_area_ids)
        self.manager.classification = "part_time_supervisor"
        db.session.add(StaffingLeadershipAssignment(person=self.manager, unit=self.outside, leadership_level="work_area"))
        db.session.flush()
        self.assertIn(self.outside.id, attendance_authority(self.user).work_area_ids)
        self.manager.active = False
        db.session.flush()
        self.assertFalse(attendance_authority(self.user).work_area_ids)

    def test_no_sort_home_rosters_default_reports_and_zero_writes(self):
        staffing.create_shift_flow_plan(self.workers["wbm"], {
            "shift_flow_sort_start_work_area_id": str(self.areas["wbm"].id),
            "shift_flow_final_door_work_area_id": str(self.areas["door"].id),
            "shift_flow_ballmat_transition": "1",
        }, self.areas["wbm"])
        db.session.commit()
        statements = []
        def capture(_c, _cur, sql, *_args):
            statements.append(sql.lstrip().lower())
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            for key in ("ebm", "wbm", "dis"):
                html = self.page(key)
                self.assertIn(self.workers[key].full_name, html)
                self.assertNotIn(self.workers["door"].full_name, html)
                for absent in ('<select', 'SAVE ATTENDANCE', 'Attendance summary', 'Unmarked'):
                    self.assertNotIn(absent, html)
            my = self.page(view="my")
            self.assertIn(self.workers["ebm"].full_name, my)
            self.assertNotIn(self.peer.full_name, my)
            self.assertIn(self.peer.full_name, self.page())
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual([sql for sql in statements if sql.startswith(("update", "insert", "delete"))], [])
        self.client.post('/neosektor/manage-employees?area=ebm', data={"sort_date_operation_id": "1", f"status_{self.peer.id}": "here"})
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_active_save_correction_occurrence_and_same_department_peer(self):
        operation = self.active_sort()
        for person in (self.workers["ebm"], self.peer):
            response = self.client.post('/neosektor/manage-employees?area=ebm&view=all', data=self.form(person, "call_in"))
            self.assertEqual(response.status_code, 302)
            record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
            self.assertEqual(record.sort_date_operation_id, operation.id)
            self.assertEqual(record.status, "call_in")
        original_id = StaffingAttendanceOccurrence.query.filter_by(person_id=self.peer.id).one().id
        self.client.post('/neosektor/manage-employees?area=ebm&view=all', data=self.form(self.peer, "no_call"))
        self.assertEqual(StaffingAttendanceOccurrence.query.filter_by(person_id=self.peer.id).one().id, original_id)
        self.client.post('/neosektor/manage-employees?area=ebm&view=all', data=self.form(self.peer, "here"))
        self.assertIsNone(StaffingAttendanceOccurrence.query.filter_by(person_id=self.peer.id).first())
        self.assertIn('value="here" selected', self.page())

    def test_stale_wrong_sort_and_out_of_scope_rejected(self):
        operation = self.active_sort()
        stale = self.form(self.peer, "no_call")
        self.client.post('/neosektor/manage-employees?area=ebm', data=self.form(self.peer, "here"))
        self.client.post('/neosektor/manage-employees?area=ebm', data=stale)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.peer.id).one().status, "here")
        wrong = self.form(self.peer, "no_call")
        wrong["sort_date_operation_id"] = str(operation.id + 999)
        self.client.post('/neosektor/manage-employees?area=ebm', data=wrong)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.peer.id).one().status, "here")
        for person, allowed in ((self.outsider, [self.outside.id]), (self.workers["wbm"], [self.areas["ebm"].id])):
            with self.assertRaisesRegex(ValueError, "outside"):
                staffing.save_operational_manage_attendance(MultiDict({"sort_date_operation_id": str(operation.id), f"status_{person.id}": "here"}), self.user, allowed, home_only=True)
            db.session.rollback()
        self.assertEqual(StaffingDailyAttendance.query.count(), 1)

    def test_direct_shared_write_cannot_bypass_management_and_transition(self):
        self.active_sort()
        form = self.form(self.peer)
        self.manager.active = False
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "authority"):
            staffing.save_operational_manage_attendance(form, self.user, [self.areas["ebm"].id], form_submission=True, home_only=True)
        db.session.rollback()
        self.manager.active = True
        db.session.commit()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 9, 13, 12)
        self.assertIn(self.peer.full_name, self.page())
        self.assertNotIn('SAVE ATTENDANCE', self.page())
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_authorized_management_does_not_need_staffing_app_role(self):
        self.active_sort()
        self.assertFalse(PortalAppAccess.query.filter_by(user_id=self.user.id, app_code="neostaffing").first())
        self.assertIn('SAVE ATTENDANCE', self.page())
        self.leadership.active = False
        db.session.commit()
        self.assertEqual(self.client.get('/neosektor/manage-employees?area=ebm').status_code, 403)

    def test_roster_reads_do_not_scale_per_employee(self):
        def measure():
            statements = []
            def capture(_c, _cur, sql, *_args):
                statements.append(sql.lstrip().lower())
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                self.page()
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            self.assertFalse(any(sql.startswith(("insert", "update", "delete")) for sql in statements))
            return sum(sql.startswith("select") for sql in statements)
        small = measure()
        for index in range(100):
            self.person(f"Scale-{index}", area=self.areas["ebm"])
        db.session.commit()
        self.assertEqual(measure(), small)

    def test_specialist_operation_covers_its_departments_only(self):
        self.manager.classification = 'full_time_specialist'
        self.leadership.unit = self.ramp
        self.leadership.leadership_level = 'operation'
        db.session.commit()
        self.assertIn(self.outside.id, attendance_authority(self.user).work_area_ids)
