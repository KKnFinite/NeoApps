"""Real Shift Flow drops, shared editor, permissions and contained roster scroll."""
import unittest
import os
import colorsys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingUnit, StaffingPerson
from app.services import neostaffing as staffing
from tests.browser import test_mobile_drawer as existing


class ShiftRosterBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = existing.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.evidence = Path('instance/browser-evidence/shift-flow').resolve()
        cls.evidence.mkdir(parents=True, exist_ok=True)
        with cls.fixture.app.app_context():
            night = StaffingUnit(unit_type='sort', name='Night')
            ramp = StaffingUnit(unit_type='operation', name='Ramp', parent=night)
            shift = StaffingUnit(unit_type='department', name='Shift', parent=ramp)
            cls.areas = {name: StaffingUnit(unit_type='work_area', name=name, parent=shift)
                for name in ['West Ballmat', 'East Ballmat', 'Discharge'] +
                [f'Door {n}' for n in (34, 32, 29, 26, 24, 21, 17, 13, 9, 6, 4, 1)]}
            db.session.add_all([night, ramp, shift, *cls.areas.values()]); db.session.commit()
            cls.ids = {}
            for employee_id, first, last, start, final, transition in [
                ('GREEN', 'Grace', 'Door', 'Door 34', 'Door 34', ''),
                ('WAVE1', 'Alex', 'Wave', 'West Ballmat', 'Door 34', '1'),
                ('WAVE2', 'Blake', 'Wave', 'West Ballmat', 'Door 34', '2'),
                ('CLEANUP', 'Chris', 'Wave', 'West Ballmat', 'Door 34', '3'),
                ('MISSING', 'Ada', 'Smith', 'West Ballmat', 'Door 34', ''),
                ('AFTER', 'Zoe', 'Smith', 'West Ballmat', 'Door 32', '2'),
                ('BEFORE', 'Zoe', 'Adams', 'Door 32', 'Door 32', ''),
                ('DISCHARGE', 'Drew', 'Discharge', 'Discharge', 'Door 1', ''),
                ('UNSET', 'Una', 'Assigned', 'Door 1', '', ''),
            ]:
                person = staffing.create_person({'employee_id':employee_id, 'first_name':first, 'last_name':last,
                    'seniority_date':'2020-01-01', 'classification':'part_time', 'employee_status':'active'})
                staffing.assign_work_area(person, cls.areas[start])
                if final:
                    staffing.create_shift_flow_plan(person, {'shift_flow_sort_start_work_area_id':cls.areas[start].id,
                        'shift_flow_final_door_work_area_id':cls.areas[final].id,
                        'shift_flow_setup_work_area_id':cls.areas[start].id if employee_id == 'GREEN' else '',
                        'shift_flow_ballmat_transition':transition}, cls.areas[start])
                cls.ids[employee_id] = person.id
            db.session.commit()
            cls.area_ids = {name: area.id for name, area in cls.areas.items()}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def test_real_drop_editor_and_responsive_roster(self):
        browser = self.fixture.pw.chromium.launch(channel=os.environ.get('NEO_BROWSER_CHANNEL'))
        context = browser.new_context(viewport={'width':1920, 'height':1080})
        context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname == '127.0.0.1' else route.abort())
        page = context.new_page(); errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('dialog', lambda dialog: self.fail(f'Unexpected confirmation: {dialog.message}'))
        helper = self.fixture()
        helper.login(page)
        helper.ready(page, '/neostaffing/shift-flow')
        card = page.locator(f'[data-roster-person="{self.ids["MISSING"]}"]')
        destination = page.locator(f'[data-final-door-target="{self.area_ids["Door 32"]}"]')
        # Read the rendered colors, including inherited text color, so these
        # assertions catch dull tints or unreadable text after stylesheet edits.
        def luminance(rgb):
            channels = [channel / 255 for channel in rgb]
            linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
            return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
        for employee_id, tint, old_rgb, hue_range in [
            ('DISCHARGE','discharge',None,(195,220)),
            ('GREEN','at-door',(23,45,37),(130,165)),
            ('WAVE1','wave-1',(42,43,30),(45,65)),
            ('WAVE2','wave-2',(48,38,29),(15,35)),
            ('CLEANUP','cleanup',(48,33,36),(340,360)),
        ]:
            person = page.locator(f'[data-roster-person="{self.ids[employee_id]}"]')
            expect(person).to_have_class(f'shift-door-person is-{tint}')
            colors = person.evaluate('''el => {
                const css = getComputedStyle(el);
                const rgb = value => value.match(/[\\d.]+/g).slice(0,3).map(Number);
                return {background:rgb(css.backgroundColor),text:rgb(css.color)};
            }''')
            background = colors['background']
            hue = colorsys.rgb_to_hsv(*(c / 255 for c in background))[0] * 360
            self.assertGreaterEqual(hue, hue_range[0]); self.assertLessEqual(hue, hue_range[1])
            self.assertGreaterEqual((luminance(colors['text']) + .05) / (luminance(background) + .05), 4.5)
            self.assertLessEqual(max(background), 160, 'Tints stay within the dark palette')
            if old_rgb:
                self.assertGreater(luminance(background), luminance(old_rgb) * 3, 'Tint must be visibly brighter')
        expect(card.locator('[data-flow-warning]')).to_be_visible()
        self.assertEqual(page.locator('[data-roster-discharge], [data-discharge-person]').count(), 0)
        discharge_person = page.locator(f'[data-roster-person="{self.ids["DISCHARGE"]}"]')
        self.assertEqual(discharge_person.count(), 1)
        expect(discharge_person).to_have_class('shift-door-person is-discharge')
        rail = page.locator('.shift-roster-rail')
        self.assertEqual(rail.locator(':scope > section').count(), 1)
        self.assertEqual(rail.locator('.shift-roster-needs').count(), 1)
        self.assertEqual(rail.locator('[data-roster-person]').count(), 1)
        expect(rail.locator('[data-roster-person]')).to_have_attribute('data-roster-person', str(self.ids['UNSET']))
        door_one = page.locator('[data-final-door-target][data-door-label="D1"]')
        self.assertGreaterEqual(rail.bounding_box()['x'], door_one.bounding_box()['x'] + door_one.bounding_box()['width'])
        self.assertLessEqual(abs(rail.bounding_box()['y'] - door_one.bounding_box()['y']), 1)
        before = None
        with self.fixture.app.app_context():
            person = db.session.get(StaffingPerson, self.ids['MISSING'])
            plan = person.shift_flow_plan; home = staffing.assignment_service.shift_home(person)
            before = (home.id, home.work_area_unit_id, home.updated_at, plan.setup_work_area_id,
                      plan.sort_start_work_area_id, plan.ballmat_transition)
        # Hold the real request before sending it to prove movement/sorting
        # happens before the database responds, and drag clicks do not navigate.
        page.evaluate('''() => {
            const original = window.fetch;
            window.fetch = (url, options) => {
                if (!String(url).endsWith('/final-door')) return original(url, options);
                window.fetch = original;
                return new Promise(resolve => { window.releaseRosterSave = () => resolve(original(url, options)); });
            };
        }''')
        with page.expect_response(lambda response: response.url.endswith('/final-door')) as saved:
            card.drag_to(destination)
            expect(destination.locator('[data-roster-person]')).to_have_text(['Zoe Adams!', 'Ada Smith!', 'Zoe Smith!'])
            expect(page.locator('[data-shift-roster]')).to_have_attribute('aria-busy', 'true')
            self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
            self.assertTrue(card.evaluate('el => !el.dispatchEvent(new MouseEvent("click", {bubbles:true,cancelable:true}))'))
            self.assertNotIn('person_id=', page.url)
            page.evaluate('window.releaseRosterSave()')
        self.assertEqual(saved.value.status, 200)
        self.assertEqual(set(saved.value.request.post_data_json), {'final_door_work_area_id', 'expected_version'})
        expect(destination.locator('[data-roster-person]')).to_have_text(['Zoe Adams!', 'Ada Smith!', 'Zoe Smith!'])
        expect(page.locator('[data-roster-feedback]')).to_contain_text('Final Door saved')
        expect(card.locator('[data-flow-warning]')).to_be_visible()
        with self.fixture.app.app_context():
            person = db.session.get(StaffingPerson, self.ids['MISSING'])
            plan = person.shift_flow_plan; home = staffing.assignment_service.shift_home(person)
            self.assertEqual((home.id, home.work_area_unit_id, home.updated_at, plan.setup_work_area_id,
                              plan.sort_start_work_area_id, plan.ballmat_transition), before)
            self.assertEqual(plan.final_door_work_area_id, self.area_ids['Door 32'])
        # An actual 409 response restores the optimistic move and leaves the
        # accepted revision, flow warning and editor fields unchanged.
        revision = card.get_attribute('data-flow-version')
        source = page.locator(f'[data-final-door-target="{self.area_ids["Door 34"]}"]')
        page.route('**/final-door', lambda route: route.fulfill(status=409, content_type='application/json',
            body='{"ok":false,"conflict":{"message":"Shift Flow changed. Reload and try again."}}'))
        with page.expect_response(lambda response: response.url.endswith('/final-door')) as failed:
            card.drag_to(source)
        self.assertEqual(failed.value.status, 409)
        expect(destination.locator(f'[data-roster-person="{self.ids["MISSING"]}"]')).to_be_visible()
        expect(card).to_have_attribute('data-flow-version', revision)
        expect(card.locator('[data-flow-warning]')).to_be_visible()
        expect(page.locator('[data-roster-feedback]')).to_contain_text('Reload and try again')
        page.unroute('**/final-door')
        card.click()
        expect(page.locator('.neostaffing-shift-flow-drawer')).to_be_visible()
        self.assertEqual(page.locator('.neostaffing-shift-flow-drawer form').count(), 1)
        expect(page.locator('[name="shift_flow_final_door_work_area_id"]')).to_have_value(str(self.area_ids['Door 32']))
        page.locator('[name="shift_flow_ballmat_transition"]').select_option('2')
        page.locator('button').filter(has_text='SAVE FLOW').click()
        expect(card.locator('[data-flow-warning]')).to_be_hidden()
        expect(card).to_have_class('shift-door-person is-wave-2')
        page.locator('.neostaffing-shift-flow-drawer header a').click()
        for width in (1920, 1280, 900, 390):
            page.set_viewport_size({'width':width, 'height':900})
            if width == 390:
                page.locator('[data-roster-side="east"]').click()
                page.locator('[data-roster-next]').click()
            page.screenshot(path=str(self.evidence / f'roster-{width}.png'), full_page=True)
            geometry = page.evaluate('''() => ({page:document.documentElement.scrollWidth,
                viewport:innerWidth, roster:document.querySelector('[data-roster-scroll]').clientWidth,
                scroll:document.querySelector('[data-roster-scroll]').scrollWidth})''')
            self.assertLessEqual(geometry['page'], geometry['viewport'], geometry)
            if width in (1280, 900, 390):
                self.assertGreater(geometry['scroll'], geometry['roster'], geometry)
                page.locator('[data-roster-scroll]').evaluate('el => { el.scrollLeft = el.scrollWidth; }')
                rail = page.locator('.shift-roster-needs').bounding_box()
                self.assertLessEqual(rail['x'] + rail['width'], width)
                page.screenshot(path=str(self.evidence / f'roster-{width}-right.png'), full_page=True)
                page.locator('[data-roster-scroll]').evaluate('el => { el.scrollLeft = 0; }')
            else:
                self.assertLessEqual(geometry['scroll'], geometry['roster'] + 1, geometry)
        self.assertEqual(errors, [])
        watcher_context = browser.new_context(viewport={'width':1920, 'height':1080})
        watcher_context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname == '127.0.0.1' else route.abort())
        watcher = watcher_context.new_page(); helper.login(watcher, 'drawer-watcher')
        helper.ready(watcher, '/neostaffing/shift-flow')
        self.assertEqual(watcher.locator('[draggable="true"], [data-final-door-target]').count(), 0)
        watcher.locator(f'[data-roster-person="{self.ids["MISSING"]}"]').click()
        expect(watcher.locator('.neostaffing-shift-flow-drawer')).to_be_visible()
        self.assertEqual(watcher.locator('.neostaffing-shift-flow-drawer form').count(), 0)
        browser.close()

    def test_special_moves_keep_colors_setup_and_ballmat_counts_in_sync(self):
        browser = self.fixture.pw.chromium.launch(channel=os.environ.get('NEO_BROWSER_CHANNEL'))
        context = browser.new_context(viewport={'width':1920, 'height':1080})
        context.route('**/*', lambda route: route.continue_() if urlsplit(route.request.url).hostname == '127.0.0.1' else route.abort())
        page = context.new_page()
        helper = self.fixture(); helper.login(page); helper.ready(page, '/neostaffing/shift-flow')
        def move(employee, door, color):
            card = page.locator(f'[data-roster-person="{self.ids[employee]}"]')
            target = page.locator(f'[data-final-door-target="{self.area_ids[door]}"]')
            with page.expect_response(lambda response: response.url.endswith('/final-door')) as response:
                card.drag_to(target)
            self.assertEqual(response.value.status, 200)
            expect(target.locator(f'[data-roster-person="{self.ids[employee]}"]')).to_be_visible()
            expect(card).to_have_class(f'shift-door-person is-{color}')
            self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
            return response.value.json()
        move('GREEN', 'Door 17', 'at-door')
        move('BEFORE', 'Door 13', 'at-door')
        with self.fixture.app.app_context():
            for employee, door, setup in [('GREEN','Door 17','Door 17'),('BEFORE','Door 13',None)]:
                person = db.session.get(StaffingPerson, self.ids[employee])
                self.assertEqual(staffing.assignment_service.shift_home(person).work_area_unit_id, self.area_ids[door])
                self.assertEqual(person.shift_flow_plan.setup_work_area_id, self.area_ids[setup] if setup else None)
                self.assertIsNone(person.shift_flow_plan.ballmat_transition)
        west = page.locator('[data-ballmat-side-count="west"]')
        east = page.locator('[data-ballmat-side-count="east"]')
        before_west, before_east = int(west.inner_text()), int(east.inner_text())
        payload = move('WAVE2', 'Door 17', 'wave-2')
        self.assertEqual(payload['ballmat_side'], 'east')
        expect(west).to_have_text(str(before_west - 1)); expect(east).to_have_text(str(before_east + 1))
        expect(page.locator(f'[data-ballmat-door="{self.area_ids["Door 17"]}"] [data-ballmat-person="{self.ids["WAVE2"]}"]')).to_be_visible()
        payload = move('WAVE2', 'Door 34', 'wave-2')
        self.assertEqual(payload['ballmat_side'], 'west')
        expect(west).to_have_text(str(before_west)); expect(east).to_have_text(str(before_east))
        with self.fixture.app.app_context():
            person = db.session.get(StaffingPerson, self.ids['WAVE2'])
            self.assertEqual(staffing.assignment_service.shift_home(person).work_area_unit_id, self.area_ids['West Ballmat'])
            self.assertEqual(person.shift_flow_plan.ballmat_transition, 2)
        move('DISCHARGE', 'Door 21', 'discharge')
        with self.fixture.app.app_context():
            person = db.session.get(StaffingPerson, self.ids['DISCHARGE'])
            self.assertEqual(staffing.assignment_service.shift_home(person).work_area_unit_id, self.area_ids['Discharge'])
        browser.close()
