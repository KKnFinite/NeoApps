"""Focused live-test presentation and retained operational timecard contracts."""
import re
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.extensions import db
from app.models import PortalAppAccess, StaffingWorkAssignment
from app.models.staffing_timecard import StaffingTimecardSlice as Slice
from app.services import neostaffing as staffing
from app.services import neostaffing_timecards as tc
from tests import test_neostaffing_timecards as fixtures


class LiveUxTest(unittest.TestCase):
    # Reuse fixtures without collecting the original suite a second time.
    setUp = fixtures.TimecardsTest.setUp
    setUpBase = fixtures.TimecardsTest.setUpBase
    tearDown = fixtures.TimecardsTest.tearDown
    _login_approved_user = fixtures.TimecardsTest._login_approved_user
    _add_sort_operation = fixtures.TimecardsTest._add_sort_operation
    person = fixtures.TimecardsTest.person
    active_sort = fixtures.TimecardsTest.active_sort
    page = fixtures.TimecardsTest.page
    form = fixtures.TimecardsTest.form
    attendance = fixtures.TimecardsTest.attendance
    command = fixtures.TimecardsTest.command

    def test_current_cleanup_mobile_and_time_only_contracts(self):
        from tests.html_contracts import document
        from tests.presentation_contracts import asset_source
        row=self.attendance()
        saved=self.client.post('/neostaffing/timecards/save',json={'node_workspace':'sektor','node_area':'ebm','commands':[self.command(row)]})
        self.assertEqual(saved.status_code,200,saved.json)
        self.user.role='grandmaster'
        db.session.add(PortalAppAccess(user_id=self.user.id,app_code='neostaffing',status='approved',role='master',is_active=True));db.session.commit()
        response=self.client.get('/neosektor/manage-employees?area=ebm&mode=times');self.assertEqual(response.status_code,200)
        root=document(response)
        for marker in ('data-start','data-end'):
            fields=root.findall('input',**{marker:None});self.assertTrue(fields)
            for field in fields:
                self.assertEqual(field.attrs['type'],'text');self.assertEqual(field.attrs['inputmode'],'numeric')
                self.assertEqual(field.attrs['maxlength'],'5');self.assertEqual(field.attrs['placeholder'],'HHMM')
                self.assertIn('T',field.attrs['data-original'])
        self.assertIn('WEEK',root.text)
        self.assertTrue(root.findall(**{'data-timecard-id':None}))
        script=asset_source(self,response,'js/neostaffing_node_times.js',tag='script')
        self.assertIn('version',script);self.assertIn('HHMM',script)
        shift=self.client.get('/neostaffing/shift-flow');self.assertEqual(shift.status_code,200)
        roster=document(shift).one(**{'data-shift-roster':None})
        self.assertTrue(roster.findall(**{'data-roster-search':None}))
        self.assertTrue(roster.findall(**{'data-roster-scroll':None}))
        self.assertIn('NEEDS ASSIGNMENT',roster.text)
        drawer=document(shift).one(**{'data-drawer-view':'menu'})
        self.assertTrue(drawer.findall('a',href='/neostaffing/vacation-selection'))

    def test_shared_staffing_drawer_always_loads_style_and_behavior(self):
        self.user.role = 'grandmaster'
        db.session.add(PortalAppAccess(user_id=self.user.id, app_code='neostaffing', status='approved', role='master', is_active=True))
        db.session.commit()
        for path in ('', '/people', '/org-chart', '/reports', '/attendance', '/shift-flow', '/accountability', '/timecards', '/settings'):
            with self.subTest(path=path):
                response = self.client.get('/neostaffing' + path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertIn('data-mobile-navigation', html)
                self.assertIn('css/mobile_drawer.css', html.split('</head>')[0])
                self.assertIn('js/mobile_drawer.js', html)
        # The flag must exist before stylesheet evaluation, not just in body.
        template = Path('app/templates/base.html').read_text()
        self.assertLess(template.index('{% set uses_shared_mobile_drawer'), template.index('<head>'))
        self.assertIn('{% if uses_shared_mobile_drawer or is_gateway_dock_page %}', template)

    def test_people_mobile_control_theme_is_scoped_and_keeps_sheet_geometry(self):
        css = Path('app/static/css/neostaffing_people_mobile.css').read_text()
        self.assertTrue(css.index('@media (max-width: 980px)') < css.index('color-scheme:dark'))
        for contract in ('background-color:#101e26', 'color:#dce6ec', 'appearance:none',
                         'background-image:linear-gradient', ':focus-visible', 'min-height:44px',
                         'height:100dvh', 'overflow-y:auto', 'safe-area-inset-bottom'):
            self.assertIn(contract, css)

    def test_grandmaster_can_view_without_staffing_identity_but_not_write(self):
        self.user.role = "grandmaster"
        self.user.employee_id = None
        db.session.commit()
        self.assertIn(b'data-node-dashboard-tile="employees"', self.client.get('/neosektor').data)
        self.assertEqual(self.client.get('/neosektor/manage-employees').status_code, 200)
        page = self.page()
        self.assertIn(self.peer.full_name, page)
        self.assertNotIn('SAVE ATTENDANCE', page)
        self.assertNotIn('COMING TO', page)
        self.assertEqual(self.client.post('/neosektor/manage-employees?area=ebm').status_code, 403)
        self.assertEqual(self.client.get('/neosektor/manage-employees?area=ebm&mode=times').status_code, 403)

    def test_dashboard_order_and_normal_half_width_live_counts(self):
        html = self.client.get('/neosektor').get_data(as_text=True)
        keys = re.findall(r'data-node-dashboard-tile="([^"]+)"', html)
        self.assertEqual(keys, ['ebm','wbm','tunnel','driver-routing','discharge','stats','live-counts','employees','settings'])
        css = Path('app/static/css/neosektor_dashboard.css').read_text()
        self.assertNotIn('.sektor-command-tile--live-counts', css)
        self.assertIn('<strong>Employees</strong>', html)
        self.assertIn('.sektor-command-tile--employees .sektor-command-copy strong { text-transform:none; }', css)
        theme = Path('app/static/css/neosektor_employees.css').read_text()
        self.assertIn('#ef5361', theme)
        for obsolete in ('#8bd4df', '#43616d', '#17323d', '#538a98'):
            self.assertNotIn(obsolete, theme)

    def test_optional_phone_create_clear_bulk_and_null_template(self):
        values = dict(employee_id='Optional', first_name='Optional', last_name='Phone',
                      seniority_date='2020-01-01', classification='part_time', phone_number='')
        person = staffing.create_person(values)
        self.assertIsNone(person.phone_number)
        staffing.update_person(person, dict(values, phone_number='5551234567'))
        self.assertTrue(person.phone_number)
        staffing.update_person(person, values)
        self.assertIsNone(person.phone_number)
        staffing.create_people_batch([dict(values, employee_id='Bulk-optional')], self.areas['ebm'])
        with self.assertRaises(ValueError):
            staffing.update_person(person, dict(values, phone_number='123'))
        self.user.role = 'grandmaster'
        db.session.add(PortalAppAccess(user_id=self.user.id, app_code='neostaffing', status='approved', role='master', is_active=True))
        db.session.commit()
        html = self.client.get(f'/neostaffing/people?person_id={person.id}').get_data(as_text=True)
        self.assertNotIn('value="None"', html)
        self.assertIn('name="phone_number" value=""', html)
        self.assertIn('neostaffing_people_mobile.js', html)
        self.assertIn('data-mobile-drawer', html)
        self.assertNotIn('data-mobile-shell-menu-panel', html)

    def test_week_uses_retained_slices_not_today_roster_and_keeps_nonhere(self):
        current = self.attendance()
        prior = self._add_sort_operation(date(2026, 9, 10), 'night')
        assignment = StaffingWorkAssignment.query.filter_by(person_id=self.peer.id, active=True).one()
        tc.sync_attendance(prior, {self.peer.id:'call_in'}, {self.peer.id:assignment}, self.user.id, as_of=date(2026,9,12))
        db.session.commit()
        historical = Slice.query.filter_by(person_id=self.peer.id).one()
        staffing.assign_work_area(self.peer, self.areas['wbm'])
        db.session.commit()
        url = '/neosektor/manage-employees?area=ebm&view=all&mode=times'
        self.assertNotIn(f'data-timecard-id="{historical.id}"', self.client.get(url).get_data(as_text=True))
        with patch.object(db.session, 'commit', wraps=db.session.commit) as committed:
            response = self.client.get(url + '&period=week&date=2026-09-12')
            committed.assert_not_called()
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'data-timecard-id="{current.id}"', html)
        self.assertNotIn(f'data-timecard-id="{historical.id}"', html)
        # Node authority follows current Home, not the old leadership union.
        # The retained slice is available in the employee's current area.
        moved = self.client.get(url.replace('area=ebm', 'area=wbm') + '&period=week&date=2026-09-12').get_data(as_text=True)
        self.assertIn(f'data-timecard-id="{historical.id}"', moved)
        self.assertIn('Call In', moved)
        self.assertNotIn(self.outsider.full_name, html)
        self.assertIn('aria-label="Time entry period"', html)
        self.assertIn('>DAY</a>', html)
        self.assertIn('>WEEK</a>', html)
        self.assertEqual(Slice.query.count(), 2)
        next_week = self.client.get(url + '&period=week&date=2026-09-13').get_data(as_text=True)
        self.assertNotIn('data-timecard-id=', next_week)

    def test_retained_historical_edit_reuses_version_authority(self):
        self.active_sort()
        operation = self._add_sort_operation(date(2026,9,10), 'night')
        assignment = StaffingWorkAssignment.query.filter_by(person_id=self.peer.id, active=True).one()
        tc.sync_attendance(operation, {self.peer.id:'here'}, {self.peer.id:assignment}, self.user.id, as_of=date(2026,9,12))
        db.session.commit()
        row = Slice.query.one()
        command = self.command(row, [{'start':'22:00', 'end':'02:00'}])
        self.assertEqual(self.client.post('/neostaffing/timecards/save', json={'commands':[command]}).status_code, 200)
        self.assertEqual(self.client.post('/neostaffing/timecards/save', json={'commands':[command]}).status_code, 409)
        self.manager.active = False
        db.session.commit()
        self.assertEqual(self.client.post('/neostaffing/timecards/save', json={'commands':[self.command(row, [])]}).status_code, 409)

    def test_week_shows_both_combo_halves_using_shared_authority(self):
        from app.models import StaffingUnit
        from app.models.staffing_accountability import StaffingComboWorkday
        worker = self.workers['ebm']
        worker.classification = 'full_time_combo'
        sort = StaffingUnit(name='Other configured sort', unit_type='sort')
        operation = StaffingUnit(name='Operation', unit_type='operation', parent=sort)
        department = StaffingUnit(name='Department', unit_type='department', parent=operation)
        area = StaffingUnit(name='Area', unit_type='work_area', parent=department)
        db.session.add_all([sort, operation, department, area])
        db.session.flush()
        staffing.assign_work_area(worker, area)
        db.session.add(StaffingComboWorkday(person_id=worker.id, first_sort_id=sort.id, second_sort_id=self.night.id))
        db.session.commit()
        night = self.attendance()
        earlier = self._add_sort_operation(date(2026,9,12), 'other configured sort')
        assignment = StaffingWorkAssignment.query.filter_by(person_id=worker.id, work_area_unit_id=area.id, active=True).one()
        tc.sync_attendance(earlier, {worker.id:'here'}, {worker.id:assignment}, self.user.id, as_of=date(2026,9,12))
        db.session.commit()
        other = Slice.query.filter_by(sort_unit_id=sort.id).one()
        html = self.client.get('/neosektor/manage-employees?area=ebm&mode=times&period=week&date=2026-09-12').get_data(as_text=True)
        for row in (night, other):
            self.assertEqual(html.count(f'data-timecard-id="{row.id}"'), 1)
        response = self.client.post('/neostaffing/timecards/save', json={'commands':[self.command(other, [{'start':'15:00','end':'17:00'}])]})
        self.assertEqual(response.status_code, 200)

    def test_ermac_week_reuses_retained_authorized_rows_without_warm_writes(self):
        from sqlalchemy import event
        from app.models.neoermac_door_preference import NeoErmacDoorPreference
        row = self.attendance()
        staffing.assign_work_area(self.workers['ebm'], self.areas['door'])
        self.user.role = 'grandmaster'
        db.session.add(NeoErmacDoorPreference(user_id=self.user.id, gateway_id=self.gateway.id,
            selected_doors_json='["D6"]', active_door='D6'))
        db.session.commit()
        # The legacy fixture provisions missing Grandmaster node access on its
        # first shell render. Measure the established, already-authorized GET.
        self.client.get('/neoermac/door-view/manage-employees?mode=times&period=week&date=2026-09-12')
        statements = []
        def capture(_conn, _cursor, sql, *_args):
            statements.append(sql.lstrip().lower())
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            with patch.object(db.session, 'commit', wraps=db.session.commit) as committed:
                response = self.client.get('/neoermac/door-view/manage-employees?mode=times&period=week&date=2026-09-12')
                committed.assert_not_called()
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'data-timecard-id="{row.id}"'.encode(), response.data)
        self.assertEqual([sql for sql in statements if sql.startswith(('insert', 'update', 'delete'))], [])
