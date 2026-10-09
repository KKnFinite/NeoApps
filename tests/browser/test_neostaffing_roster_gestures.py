"""Trusted mouse/touch input must reach Doors outside the initial viewport/page."""
import json
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit
from app.services import neostaffing as staffing
from tests.browser import test_neostaffing_shift_roster as roster_fixture


class NeedsAssignmentGesturesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = roster_fixture.ShiftRosterBrowserTest
        cls.roster.setUpClass()
        cls.fixture = cls.roster.fixture
        cls.areas = cls.roster.area_ids

    @classmethod
    def tearDownClass(cls):
        cls.roster.tearDownClass()

    def pending_person(self, employee_id):
        with self.fixture.app.app_context():
            start = db.session.get(StaffingUnit, self.areas['West Ballmat'])
            person = staffing.create_person({'employee_id':employee_id, 'first_name':'Touch',
                'last_name':employee_id, 'seniority_date':'2020-01-01', 'classification':'part_time',
                'employee_status':'active'})
            staffing.assign_work_area(person, start)
            staffing.create_shift_flow_plan(person, {
                'shift_flow_sort_start_work_area_id':start.id,
                'shift_flow_setup_work_area_id':self.areas['Door 21'],
                'shift_flow_final_door_work_area_id':'', 'shift_flow_ballmat_transition':'2'}, start)
            db.session.commit()
            return person.id

    def assert_saved_flow(self, person_id, door, side):
        with self.fixture.app.app_context():
            person = db.session.get(StaffingPerson, person_id)
            plan = person.shift_flow_plan
            self.assertEqual(plan.final_door_work_area_id, self.areas[door])
            self.assertEqual(plan.sort_start_work_area_id, self.areas[f'{side} Ballmat'])
            self.assertEqual(staffing.assignment_service.shift_home(person).work_area_unit_id,
                             self.areas[f'{side} Ballmat'])
            self.assertEqual(plan.setup_work_area_id, self.areas['Door 21'])
            self.assertEqual(plan.ballmat_transition, 2)

    def test_desktop_mouse_drag_scrolls_both_edges_and_saves_optimistically(self):
        person_id = self.pending_person('MOUSE-EDGES')
        browser = self.fixture.pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':1280, 'height':900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            helper = self.fixture(); helper.login(page); helper.ready(page, '/neostaffing/shift-flow')
            self.assertEqual(page.locator('[data-needs-door-select]').count(), 0)
            self.assertIn('20261009-roster-touch-drag-v1', page.locator(
                'script[src*="neostaffing_shift_roster.js"]').get_attribute('src'))
            scroll = page.locator('[data-roster-scroll]')
            card = page.locator(f'[data-roster-person="{person_id}"]')
            card.scroll_into_view_if_needed()
            self.assertGreater(scroll.evaluate('el => el.scrollLeft'), 0)

            def mouse_drag(door, edge):
                target = page.locator(f'[data-final-door-target="{self.areas[door]}"]')
                box, source = scroll.bounding_box(), card.bounding_box()
                self.assertTrue(target.bounding_box()['x'] < box['x'] or
                                target.bounding_box()['x'] + target.bounding_box()['width'] > box['x'] + box['width'])
                x, y = source['x'] + source['width'] / 2, source['y'] + source['height'] / 2
                page.mouse.move(x, y); page.mouse.down()
                page.mouse.move(x - 12 if edge < 0 else x + 12, y, steps=4)
                page.mouse.move(box['x'] + (8 if edge < 0 else box['width'] - 8), y, steps=12)
                page.wait_for_function('''direction => {
                    const el = document.querySelector('[data-roster-scroll]');
                    return direction < 0 ? el.scrollLeft <= 1 : el.scrollLeft >= el.scrollWidth - el.clientWidth - 1;
                }''', arg=edge)
                destination = target.bounding_box()
                page.mouse.move(destination['x'] + destination['width'] / 2, y, steps=6)
                with page.expect_response(lambda response: response.url.endswith('/final-door')) as response:
                    page.mouse.up()
                    if page.evaluate("typeof window.releaseGestureSave === 'function'"):
                        expect(target.locator(f'[data-roster-person="{person_id}"]')).to_be_visible()
                        expect(page.locator('[data-shift-roster]')).to_have_attribute('aria-busy', 'true')
                        self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
                        page.evaluate('window.releaseGestureSave(); delete window.releaseGestureSave;')
                self.assertEqual(response.value.status, 200, response.value.text())
                self.assertEqual(set(response.value.request.post_data_json),
                                 {'final_door_work_area_id', 'expected_version'})
                expect(target.locator(f'[data-roster-person="{person_id}"]')).to_be_visible()
                expect(card).to_have_class('shift-door-person is-wave-2')
                expect(card.locator('[data-setup-strip]')).to_be_visible()
                self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)

            # Delay the real request: the card and counters move before save.
            page.evaluate('''() => {
                const original = window.fetch;
                window.fetch = (url, options) => {
                    if (!String(url).endsWith('/final-door')) return original(url, options);
                    window.fetch = original;
                    return new Promise(resolve => { window.releaseGestureSave = () => resolve(original(url, options)); });
                };
            }''')
            mouse_drag('Door 34', -1)
            self.assertEqual(page.locator(f'.shift-roster-needs [data-roster-person="{person_id}"]').count(), 0)
            self.assert_saved_flow(person_id, 'Door 34', 'West')
            mouse_drag('Door 1', 1)
            self.assert_saved_flow(person_id, 'Door 1', 'East')
            page.screenshot(path=str(self.roster.evidence / 'needs-mouse-offscreen-door.png'), full_page=True)
            card.click()
            expect(page.locator('.neostaffing-shift-flow-drawer')).to_be_visible()
            self.assertFalse(errors)
        finally:
            browser.close()

    def test_mobile_trusted_touch_reaches_offscreen_pages_and_rolls_back_conflict(self):
        browser = self.fixture.pw.chromium.launch()
        try:
            for width in (390, 450):
                with self.subTest(width=width):
                    person_id = self.pending_person(f'TOUCH-{width}')
                    context = browser.new_context(viewport={'width':width, 'height':844},
                                                  is_mobile=True, has_touch=True)
                    page = context.new_page(); errors, saves = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: saves.append(request) if request.url.endswith('/final-door') else None)
                    page.add_init_script('''
                        window.touchInputs = [];
                        addEventListener('pointermove', e => touchInputs.push({trusted:e.isTrusted, type:e.pointerType}));
                    ''')
                    helper = self.fixture(); helper.login(page); helper.ready(page, '/neostaffing/shift-flow')
                    scroll = page.locator('[data-roster-scroll]')
                    card = page.locator(f'[data-roster-person="{person_id}"]')
                    card.scroll_into_view_if_needed()
                    session = context.new_cdp_session(page)
                    def touch(kind, x=None, y=None):
                        session.send('Input.dispatchTouchEvent', {'type':kind, 'touchPoints':[]
                            if kind in ('touchEnd', 'touchCancel') else [{'id':1, 'x':x, 'y':y,
                                                                        'radiusX':8, 'radiusY':8, 'force':1}]})

                    def start_drag(direction):
                        source, box = card.bounding_box(), scroll.bounding_box()
                        x, y = source['x'] + source['width']/2, source['y'] + source['height']/2
                        touch('touchStart', x, y)
                        touch('touchMove', x + direction * 12, y)
                        touch('touchMove', box['x'] + (8 if direction < 0 else box['width'] - 8), y)
                        preview = page.locator('.shift-drag-preview')
                        expect(preview).to_be_visible()
                        self.assertEqual(preview.locator('[data-flow-warning]').is_visible(),
                                         card.locator('[data-flow-warning]').is_visible())
                        expect(preview.locator('[data-setup-strip]')).to_be_visible()
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)

                    def drop(door):
                        target = page.locator(f'[data-final-door-target="{self.areas[door]}"]')
                        box = target.bounding_box()
                        touch('touchMove', box['x'] + box['width']/2, box['y'] + 45)
                        with page.expect_response(lambda response: response.url.endswith('/final-door')) as response:
                            touch('touchEnd')
                        return response.value

                    start_drag(1)
                    expect(page.locator('[data-roster-range]')).to_have_text(
                        'D6–D1' if width == 390 else 'D9–D1', timeout=15000)
                    response = drop('Door 1')
                    self.assertEqual(response.status, 200, response.text())
                    expect(card).to_have_class('shift-door-person is-wave-2')
                    expect(card.locator('[data-setup-strip]')).to_be_visible()
                    self.assertEqual(page.locator(f'.shift-roster-needs [data-roster-person="{person_id}"]').count(), 0)
                    self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
                    self.assert_saved_flow(person_id, 'Door 1', 'East')
                    page.screenshot(path=str(self.roster.evidence / f'needs-touch-{width}-offscreen.png'), full_page=True)

                    # Existing Door cards use the same touch path in reverse.
                    start_drag(-1)
                    expect(page.locator('[data-roster-range]')).to_have_text(
                        'D34–D29' if width == 390 else 'D34–D26', timeout=15000)
                    self.assertEqual(drop('Door 34').status, 200)
                    self.assert_saved_flow(person_id, 'Door 34', 'West')
                    self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
                    box = card.bounding_box()
                    page.touchscreen.tap(box['x'] + box['width']/2, box['y'] + box['height']/2)
                    expect(page.locator('.neostaffing-shift-flow-drawer')).to_be_visible()
                    page.locator('.neostaffing-shift-flow-drawer header a').tap()

                    pending_id = self.pending_person(f'CONFLICT-{width}')
                    helper.ready(page, '/neostaffing/shift-flow')
                    card = page.locator(f'[data-roster-person="{pending_id}"]')
                    card.scroll_into_view_if_needed()
                    before = card.get_attribute('data-flow-version')
                    needs_count = page.locator('[data-roster-needs-count]').inner_text()
                    page.route('**/final-door', lambda route: route.fulfill(status=409, content_type='application/json',
                        body=json.dumps({'ok':False, 'conflict':{'message':'Reload and try again.'}})))
                    start_drag(-1)
                    page.wait_for_function("document.querySelector('[data-roster-scroll]').scrollLeft <= 1")
                    self.assertEqual(drop('Door 34').status, 409)
                    expect(page.locator(f'.shift-roster-needs [data-roster-person="{pending_id}"]')).to_have_count(1)
                    expect(page.locator('[data-roster-needs-count]')).to_have_text(needs_count)
                    expect(card).to_have_attribute('data-flow-version', before)
                    expect(card.locator('[data-setup-strip]')).to_be_visible()
                    expect(page.locator('[data-roster-feedback]')).to_contain_text('Reload and try again')
                    self.assertEqual(page.locator('.neostaffing-shift-flow-drawer').count(), 0)
                    self.assertTrue(page.evaluate("touchInputs.length > 0 && touchInputs.every(e => e.trusted && e.type === 'touch')"))
                    self.assertEqual(len(saves), 3)
                    self.assertFalse(errors)
                    context.close()
        finally:
            browser.close()
