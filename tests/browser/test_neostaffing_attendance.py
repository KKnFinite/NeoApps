"""Live Attendance interactions against the isolated browser fixture database."""
from datetime import datetime
from threading import Event
import unittest

from flask import jsonify, request
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, StaffingDailyAttendance, StaffingLeadershipAssignment, User
from app.models.staffing_daily_attendance import StaffingUnmarkedAttendanceNote
from app.services import neostaffing as staffing
from tests import test_neostaffing_daily_attendance as attendance_fixture
from tests.browser import test_mobile_drawer as browser_fixture


class AttendanceBrowserTest(unittest.TestCase):
    login = browser_fixture.MobileDrawerBrowserTest.login

    @classmethod
    def setUpClass(cls):
        browser_fixture.MobileDrawerBrowserTest.setUpClass.__func__(cls)
        cls.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 8, 15, 21)
        cls.delayed = None
        cls.fail_note = False

        @cls.app.before_request
        def attendance_test_network():
            if request.endpoint != 'neostaffing.attendance_save':
                return None
            if cls.fail_note:
                cls.fail_note = False
                return jsonify(error='Temporary failure'), 503
            if cls.delayed:
                name, edits, arrived, release = cls.delayed
                if name in request.form:
                    edits.append(request.form[name])
                    if len(edits) == 1:
                        arrived.set()
                        release.wait(15)
            return None

        with cls.app.app_context():
            fixture = attendance_fixture.NeoStaffingDailyAttendanceTest()
            fixture.gateway = Gateway.query.filter_by(code='RFD').one()
            sort, operation_unit, department, area, outside = fixture._hierarchy()
            operation = fixture._night_operation()
            cls.scope = {'sort_date_operation_id': operation.id, 'work_area_id': area.id, 'sort_id': sort.id}
            cls.scope_chain = [sort.id, operation_unit.id, department.id, area.id]
            cls.outside_area_id = outside.id
            cls.path = f'/neostaffing/attendance?sort_id={sort.id}'
            cls.people = {}
            for employee, first, last, work_area in (
                ('AX100', 'Alina', 'Rivera', area), ('BX200', 'Jordan', 'Lee', area),
                ('CX300', 'Casey', 'Blake', area), ('DX400', 'Sam', 'Fields', area),
                ('EX500', 'Robin', 'Kim', area), ('OUT999', 'Terry', 'Outside', outside),
            ):
                person = fixture._person(employee, work_area)
                person.first_name, person.last_name = first, last
                cls.people[employee] = person.id
            # Both sessions have active department authority, but neither can edit the direct area.
            for username in ('drawer-admin', 'drawer-watcher'):
                user = User.query.filter_by(username=username).one()
                user.employee_id = f'TEST-{username}'
                supervisor = staffing.create_person({'employee_id': user.employee_id, 'first_name': 'Test',
                    'last_name': username, 'seniority_date': '2020-01-01', 'classification': 'full_time_supervisor'})
                db.session.add(StaffingLeadershipAssignment(person=supervisor, unit=department, leadership_level='department'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        browser_fixture.MobileDrawerBrowserTest.tearDownClass.__func__(cls)

    def row(self, page, employee):
        return page.locator(f'[data-attendance-employee-id="{employee}"]')

    def expect_scope_here(self, page, count):
        for unit_id in self.scope_chain:
            expect(page.locator(f'[data-attendance-scope-here="{unit_id}"]')).to_have_text(str(count))
        expect(page.locator(f'[data-attendance-scope-here="{self.outside_area_id}"]')).to_have_text('—')

    def open(self, browser, username):
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        self.login(page, username)
        self.assertEqual(page.goto(self.origin + self.path).status, 200)
        expect(page.get_by_role('searchbox', name='Search employees')).to_be_visible()
        return page

    def exercise(self, engine):
        with self.app.app_context():
            StaffingUnmarkedAttendanceNote.query.delete()
            StaffingDailyAttendance.query.delete()
            user = User.query.filter_by(username='drawer-admin').one()
            staffing.save_attendance({**self.scope, f'status_{self.people["EX500"]}': 'vacation',
                f'note_{self.people["EX500"]}': 'Approved vacation'}, user)
            db.session.commit()

        browser = getattr(self.pw, engine).launch()
        try:
            page = self.open(browser, 'drawer-admin')
            other = self.open(browser, 'drawer-watcher')
            search = page.get_by_role('searchbox', name='Search employees')
            bulk = page.get_by_role('button', name='ALL HERE — CURRENT SCOPE')
            self.expect_scope_here(page, 0)
            for unit_id in self.scope_chain[:-1]:
                toggle = page.locator(f'[data-attendance-tree-id="unit-{unit_id}"] > div > [data-attendance-tree-toggle]')
                if toggle.get_attribute('aria-expanded') == 'false':
                    toggle.click()
            expect(self.row(page, 'OUT999').locator('select')).to_be_disabled()
            rejected = page.request.post(self.origin + '/neostaffing/attendance/save',
                headers={'Accept': 'application/json'}, form=self.scope)
            self.assertEqual(rejected.headers.get('x-neoapps-csrf-rejected'), '1')
            for width, height in ((1920,1080), (1536,864), (1366,768), (1280,720), (1024,768), (390,844), (430,932)):
                page.set_viewport_size({'width':width, 'height':height})
                geometry = page.evaluate('''() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
                    bodyOverflow: getComputedStyle(document.body).overflowX,
                    controls: [...document.querySelectorAll(`[data-attendance-search], [data-attendance-all],
                        [data-attendance-person] :is(select, input:not([type=hidden]))`)].map(el => {
                        const r=el.getBoundingClientRect(); return [r.left, r.right, r.width];
                    })})''')
                self.assertLessEqual(geometry['scrollWidth'], width, (engine, width, geometry))
                self.assertNotIn(geometry['bodyOverflow'], ('clip', 'hidden'))
                self.assertTrue(all(left >= 0 and right <= width and control_width >= 60
                                    for left, right, control_width in geometry['controls']), (engine, width, geometry))
                count_edges = page.locator('[data-attendance-scope-here]').evaluate_all('''elements => elements.map(el => {
                    const {left, right, width} = el.getBoundingClientRect();
                    return {left, right, width, align: getComputedStyle(el).textAlign};
                })''')
                self.assertEqual(len(count_edges), 5)
                self.assertTrue(all(edge['width'] > 0 and edge['left'] >= 0 and edge['right'] <= width
                                    and edge['align'] == 'right' for edge in count_edges), (engine, width, count_edges))
                self.assertLessEqual(max(e['right'] for e in count_edges) - min(e['right'] for e in count_edges), 1)
                if width in (1920, 1024, 390):
                    page.screenshot(path=str(self.evidence / f'attendance-{engine}-{width}.png'))

            search.fill('  riVERA   alina ')
            expect(page.locator('[data-attendance-matches]')).to_have_text('1 match')
            self.expect_scope_here(page, 0)
            expect(self.row(page, 'AX100')).to_be_visible()
            search.fill('BX200')
            expect(self.row(page, 'BX200')).to_be_visible()
            search.fill('missing person')
            expect(page.locator('[data-attendance-empty]')).to_contain_text('No employees match this search')
            page.get_by_role('button', name='Clear', exact=True).click()
            expect(page.locator('[data-attendance-matches]')).to_have_text('6 matches')

            # Debounced note save on mobile, while the employee remains Unmarked.
            first = self.row(page, 'AX100')
            first_note = first.locator('input[name^="note_"]')
            first_note.fill('Debounced note')
            expect(first.locator('[data-attendance-state]')).to_have_text('Saved')
            expect(first.locator('select')).to_have_value('')

            # Hold one request. Two rapid changes in that row serialize, while a
            # different supervisor can independently save a different row.
            page.set_viewport_size({'width': 1366, 'height': 768})
            edits = []
            arrived, release = Event(), Event()
            type(self).delayed = (f'status_{self.people["AX100"]}', edits, arrived, release)
            first.locator('select').select_option('here')
            self.assertTrue(arrived.wait(5))
            expect(first.locator('[data-attendance-state]')).to_have_text('Saving…')
            self.expect_scope_here(page, 0)
            first.locator('select').select_option('optional_day')
            first.locator('select').select_option('scheduled_off')
            other_row = self.row(other, 'CX300')
            other_row.locator('select').select_option('no_call')
            expect(other_row.locator('[data-attendance-state]')).to_have_text('Saved')
            self.assertEqual(edits, ['here'])
            release.set()
            expect(first.locator('[data-attendance-state]')).to_have_text('Saved')
            expect(first.locator('select')).to_have_value('scheduled_off')
            self.expect_scope_here(page, 0)
            self.assertEqual(edits, ['here', 'scheduled_off'])
            type(self).delayed = None

            # A failed save retains the draft, and deliberate Retry persists it.
            type(self).fail_note = True
            second = self.row(page, 'BX200')
            second_note = second.locator('input[name^="note_"]')
            second_note.fill('Preserve with bulk')
            second_note.press('Tab')
            expect(second.locator('[data-attendance-state]')).to_contain_text('Error: Temporary failure')
            expect(second_note).to_have_value('Preserve with bulk')
            second.get_by_role('button', name='Retry').click()
            expect(second.locator('[data-attendance-state]')).to_have_text('Saved')

            # Search hides eligible Sam and Jordan as well as a stale Casey row.
            search.fill('Alina')
            bulk.click()
            expect(page.locator('[data-attendance-bulk-result]')).to_have_text('2 marked Here.')
            expect(search).to_have_value('Alina')
            expect(page.locator('[data-attendance-count="here"]')).to_have_text('2')
            self.expect_scope_here(page, 2)
            page.get_by_role('button', name='Clear', exact=True).click()
            expect(second.locator('select')).to_have_value('here')
            expect(second_note).to_have_value('Preserve with bulk')
            expect(self.row(page, 'CX300').locator('select')).to_have_value('no_call')
            expect(self.row(page, 'EX500').locator('select')).to_have_value('vacation')
            expect(self.row(page, 'OUT999').locator('select')).to_have_value('')

            type(self).fail_note = True
            second.locator('select').select_option('call_in')
            expect(second.locator('[data-attendance-state]')).to_contain_text('Temporary failure')
            self.expect_scope_here(page, 2)
            second.get_by_role('button', name='Retry').click()
            expect(second.locator('[data-attendance-state]')).to_have_text('Saved')
            self.expect_scope_here(page, 1)
            second.locator('select').select_option('here')
            expect(second.locator('[data-attendance-state]')).to_have_text('Saved')
            self.expect_scope_here(page, 2)

            # A stale note edit cannot overwrite a status set by another supervisor.
            stale = self.row(other, 'BX200')
            stale_note = stale.locator('input[name^="note_"]')
            stale_note.fill('Reviewed new note')
            expect(stale.locator('[data-attendance-state]')).to_contain_text('Changed by another supervisor')
            self.expect_scope_here(other, 2)
            expect(stale_note).to_have_value('Reviewed new note')
            expect(stale.locator('select')).to_have_value('here')
            stale.get_by_role('button', name='Retry').click()
            expect(stale.locator('[data-attendance-state]')).to_have_text('Saved')
            page.reload()
            expect(second_note).to_have_value('Reviewed new note')
            expect(first.locator('select')).to_have_value('scheduled_off')
        finally:
            if type(self).delayed:
                type(self).delayed[3].set()
                type(self).delayed = None
            type(self).fail_note = False
            browser.close()

    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')
