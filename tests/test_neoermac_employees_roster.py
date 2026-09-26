"""Ermac door preferences feed Staffing's read-only roster and sort-bound writes."""
from datetime import date, datetime
from html import unescape
import re
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import PortalAppAccess, StaffingUnit, StaffingDailyAttendance, StaffingLeadershipAssignment
from app.services import neostaffing as staffing
from tests import test_neoermac_door_supervision as supervision_fixture


class NeoErmacEmployeesRosterTest(unittest.TestCase):
    _add_operation = supervision_fixture.NeoErmacDoorSupervisionTest._add_operation
    _add_user = supervision_fixture.NeoErmacDoorSupervisionTest._add_user
    _login = supervision_fixture.NeoErmacDoorSupervisionTest._login
    tearDown = supervision_fixture.NeoErmacDoorSupervisionTest.tearDown

    def setUp(self):
        supervision_fixture.NeoErmacDoorSupervisionTest.setUp(self)
        self.night = StaffingUnit(unit_type="sort", name="Night")
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=self.night)
        shift = StaffingUnit(unit_type="department", name="Shift", parent=ramp)
        self.door = StaffingUnit(unit_type="work_area", name="Door 6", parent=shift)
        self.other_door = StaffingUnit(unit_type="work_area", name="Door 9", parent=shift)
        self.ballmat = StaffingUnit(unit_type="work_area", name="East Ballmat", parent=shift)
        db.session.add_all([self.night, ramp, shift, self.door, self.other_door, self.ballmat])
        db.session.flush()
        self.here = self._person("Assigned Employee", self.door, self.door)
        self.coming = self._person("Coming Employee", self.ballmat, self.door)
        self.outside = self._person("Outside Employee", self.other_door, self.other_door)
        self.inactive = self._person("Inactive Employee", self.door, self.door)
        self.inactive.active = False
        self.user.employee_id = "ERMAC-SUP"
        self.manager = staffing.create_person({
            'employee_id': 'ERMAC-SUP', 'first_name': 'Supervisor', 'last_name': 'Fixture',
            'seniority_date': '2020-01-01', 'classification': 'part_time_supervisor',
        })
        db.session.add(StaffingLeadershipAssignment(person=self.manager, unit=self.door, leadership_level='work_area'))
        self.staffing_role = PortalAppAccess(
            user_id=self.user.id, app_code="neostaffing",
            status="approved", role="operator", is_active=True,
        )
        db.session.add(self.staffing_role)
        db.session.commit()
        self.client.post('/neoermac/door-view/supervision', data={'doors': ['D6'], 'active_door': 'D6'})

    def _person(self, name, area, final=None):
        person = staffing.create_person({
            'employee_id': name.replace(' ', ''), 'first_name': name, 'last_name': 'Fixture',
            'seniority_date': '2020-01-01', 'classification': 'part_time', 'employee_status': 'active',
        })
        staffing.assign_work_area(person, area)
        if final:
            staffing.create_shift_flow_plan(person, {
                'shift_flow_sort_start_work_area_id': str(area.id),
                'shift_flow_final_door_work_area_id': str(final.id),
                'shift_flow_ballmat_transition': '1' if area == self.ballmat else '',
            }, area)
        return person

    def _no_sort(self):
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 6, 15, 12)
        self.assertIsNone(staffing.current_night_attendance_operation(self.gateway))

    def _get(self):
        response = self.client.get('/neoermac/door-view/manage-employees')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<h1>EMPLOYEES</h1>', response.data)
        self.assertIn(b'Selected Doors: D6', response.data)
        for person in (self.here, self.coming):
            self.assertEqual(response.data.count(f'<strong>{person.full_name}</strong>'.encode()), 1)
        for person in (self.outside, self.inactive):
            self.assertNotIn(person.full_name.encode(), response.data)
        return response.data

    def test_no_sort_hides_accountability_but_preserves_staffing_backend(self):
        self._no_sort()
        page = self._get()
        self.assertNotIn(b'>ACCOUNTABILITY</a>', page)
        self.assertNotIn(b'SAVE ATTENDANCE', page)
        self.assertEqual(self.client.get('/neostaffing/accountability').status_code, 200)

    def test_node_autosave_and_times_without_leadership_require_door_edit(self):
        StaffingLeadershipAssignment.query.filter_by(person_id=self.manager.id).update({'active':False})
        db.session.commit()
        page = self._get().decode()
        original = unescape(re.search(fr'name="original_{self.here.id}" value="([^"]+)"', page).group(1))
        data = {'sort_date_operation_id':self.operation.id, f'status_{self.here.id}':'here',
                f'original_{self.here.id}':original}
        response = self.client.post('/neoermac/door-view/manage-employees', data=data,
                                    headers={'Accept':'application/json'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'here')
        self.assertIn(b'value="here" selected', self._get())
        self.assertEqual(self.client.get('/neoermac/door-view/manage-employees?mode=times').status_code, 200)
        from app.services.permission_rules import user_can
        with patch('app.services.permission_rules.user_can', side_effect=lambda key, user=None:
                   False if key == 'neoermac.door_view.edit' else user_can(key, user)):
            self.assertEqual(self.client.post('/neoermac/door-view/manage-employees', data=data).status_code, 403)
            self.assertEqual(self.client.get('/neoermac/door-view/manage-employees?mode=times').status_code, 403)
        self.assertEqual(StaffingDailyAttendance.query.count(), 1)

    def test_selected_door_times_reports_and_write_scope(self):
        from app.models.staffing_timecard import StaffingTimecardSlice as Slice, StaffingTimecardSegment as Segment
        self._post(self.here, self.operation.id)
        row = Slice.query.filter_by(person_id=self.here.id).one()
        base = '/neoermac/door-view/manage-employees?door=D6'
        for mode in ('times', 'reports'):
            for period in ('day', 'week'):
                response = self.client.get(base + f'&mode={mode}&period={period}')
                self.assertEqual(response.status_code, 200)
                self.assertIn(self.here.full_name.encode(), response.data)
                self.assertNotIn(self.outside.full_name.encode(), response.data)
                self.assertNotIn(b'>ACCOUNTABILITY</a>', response.data)
                self.assertIn(b'name="door" value="D6"', response.data)
        payload = {'node_workspace':'ermac', 'node_area':'D6', 'commands':[
            {'id':row.id, 'version':row.version, 'segments':[{'start':'2230','end':'930'}]}]}
        result = self.client.post('/neostaffing/timecards/save', json=payload)
        self.assertEqual(result.status_code, 200, result.json)
        saved = result.json['rows'][0]
        self.assertEqual(saved['segments'][0]['start'][11:16], '22:30')
        self.assertEqual(saved['segments'][0]['end'][11:16], '09:30')
        self.assertEqual(Segment.query.count(), 1)
        self.assertEqual(self.client.post('/neostaffing/timecards/save', json=payload).status_code, 409)
        payload['commands'][0]['version'] = saved['version']
        payload['node_area'] = 'D9'
        self.assertEqual(self.client.post('/neostaffing/timecards/save', json=payload).status_code, 409)
        self.assertEqual(self.client.get(base.replace('D6','D9') + '&mode=times').status_code, 403)
        self.assertIn(b'value="22:30"', self.client.get(base + '&mode=times').data)
        self.client.post('/neoermac/door-view/supervision', data={'doors':['D6','D9'], 'active_door':'D6'})
        self._post(self.outside, self.operation.id)
        for mode in ('times','reports'):
            for period in ('day','week'):
                page = self.client.get(base + f'&mode={mode}&period={period}')
                self.assertEqual(page.status_code, 200)
                self.assertNotIn(self.outside.full_name.encode(), page.data)
                self.assertIn(b'data-node-area="D6"', page.data)


    def _post(self, person, operation_id, status='here'):
        page = self.client.get('/neoermac/door-view/manage-employees').get_data(as_text=True)
        original = re.search(fr'name="original_{person.id}" value="([^"]+)"', page)
        return self.client.post('/neoermac/door-view/manage-employees', data={
            'sort_date_operation_id': str(operation_id), f'status_{person.id}': status,
            f'original_{person.id}': unescape(original[1]) if original else '',
        })

    def test_no_sort_roster_uses_assignments_and_flow_without_attendance_or_writes(self):
        self._no_sort()
        no_plan = self._person('Assignment Only', self.door)
        db.session.commit()
        statements = []
        def capture(_c, _cursor, statement, *_args):
            statements.append(statement.lower())
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            html = self._get()
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertIn(no_plan.full_name.encode(), html)
        self.assertIn(b'Door 6', html)
        self.assertIn(b'W1', html)
        self.assertIn(b'COMING TO THESE DOORS', html)
        self.assertIn(b'Attendance available when the Night Sort is active.', html)
        for forbidden in (b'<select', b'SAVE ATTENDANCE', b'Attendance summary', b'Unmarked', b'Called In', b'ATTENDANCE HERE', b'name="sort_date_operation_id"'):
            self.assertNotIn(forbidden, html)
        self.assertFalse(any('staffing_daily_attendance' in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().startswith(('insert', 'update', 'delete')) for sql in statements))
        self._post(self.here, self.operation.id)
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)
        # The old default shared context/resolver still require an operation.
        self.assertEqual(staffing.attendance_deep_link_work_area_ids(['D6']), [])
        self.assertEqual(staffing.operational_manage_employees_context([self.door.id])['here'], [])

    def test_changed_canonical_home_updates_ermac_scope_without_other_sort_interference(self):
        self._no_sort()
        self.here.classification = 'full_time_combo'
        twilight = StaffingUnit(unit_type='sort', name='Twilight')
        extra = StaffingUnit(unit_type='work_area', name='Door 6', parent=twilight)
        db.session.add_all([twilight, extra]); db.session.flush()
        staffing.assign_work_area(self.here, extra)
        staffing.save_shift_flow_plan(self.here, {
            'expected_version': staffing.shift_flow_revision(self.here, self.here.shift_flow_plan,
                staffing.assignment_service.shift_home(self.here)),
            'shift_flow_sort_start_work_area_id': self.other_door.id,
            'shift_flow_final_door_work_area_id': self.other_door.id,
        }, self.door)
        db.session.commit()
        page = self.client.get('/neoermac/door-view/manage-employees').get_data(as_text=True)
        self.assertNotIn(f'<strong>{self.here.full_name}</strong>', page)
        self.client.post('/neoermac/door-view/supervision', data={'doors':['D9'], 'active_door':'D9'})
        page = self.client.get('/neoermac/door-view/manage-employees').get_data(as_text=True)
        self.assertEqual(page.count(f'<strong>{self.here.full_name}</strong>'), 1)
        self.assertIn(extra.id, [a.work_area_unit_id for a in self.here.work_assignments if a.active])
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_transition_preserves_current_week_attendance_for_correction(self):
        self._no_sort()
        self._get()
        current = self._add_operation(date(2026, 6, 15))
        current.generated_by_user_id = self.user.id
        db.session.commit()
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 6, 15, 23)
        self.assertEqual(staffing.current_night_attendance_operation(self.gateway).id, current.id)
        html = self._get()
        self.assertIn(f'name="status_{self.here.id}"'.encode(), html)
        self.assertIn(b'data-attendance-autosave', html)
        self.assertNotIn(b'SAVE ATTENDANCE', html)
        self._post(self.here, self.operation.id)  # Real prior operation, not current.
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)
        self._post(self.here, current.id)
        row = StaffingDailyAttendance.query.one()
        self.assertEqual((row.person_id, row.sort_date_operation_id, row.status), (self.here.id, current.id, 'here'))
        self.assertIn(b'value="here" selected', self._get())
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 6, 18, 12)
        html = self._get()
        self.assertNotIn(b'SAVE ATTENDANCE', html)
        self.assertIn(b'value="here" selected', html)
        self._post(self.here, current.id, 'call_in')
        db.session.expire_all()
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'call_in')

    def test_wrong_stale_out_of_scope_and_unauthorized_posts_do_not_mutate(self):
        for person, operation_id in ((self.here, self.operation.id + 1000), (self.outside, self.operation.id)):
            self._post(person, operation_id)
            self.assertEqual(StaffingDailyAttendance.query.count(), 0)
        self._post(self.here, self.operation.id)
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'here')
        self.staffing_role.role = 'watcher'
        self.manager.active = False
        db.session.commit()
        html = self._get()
        self.assertNotIn(b'SAVE ATTENDANCE', html)
        self.assertNotIn(f'name="status_{self.here.id}"'.encode(), html)
        self._post(self.here, self.operation.id, 'called_in')
        db.session.expire_all()
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'here')
        self._no_sort()
        self._get()  # Read-only users retain the persistent roster too.
