"""Real Rain pages: compact controls, autosave/reset and existing live refresh."""
from contextlib import ExitStack
from datetime import date, datetime
import unittest
from unittest.mock import patch

from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation


class CompactRainBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 1000
        with cls.fixture.app.app_context():
            gateway = Gateway.query.filter_by(code='RFD').one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code='RFD',
                sort_date=date.today(), sort_name='night')
            db.session.add(operation)
            db.session.flush()
            cls.ids = {}
            for kind in ('arrival', 'departure'):
                mission = SortDateMission(sort_date_operation_id=operation.id,
                    sort_date=operation.sort_date, gateway_code='RFD', sort_name='night',
                    mission_type=kind, mission_source='manual', flight_number='UPS123',
                    origin='SDF' if kind == 'arrival' else 'RFD',
                    destination='RFD' if kind == 'arrival' else 'SDF',
                    assigned_tail_number='N123UP', timezone='America/Chicago',
                    planned_datetime_utc=datetime(2026, 10, 16, 6),
                    planned_source='manual', departure_status='scheduled' if kind == 'departure' else None)
                db.session.add(mission)
                db.session.flush()
                cls.ids[kind] = mission.id
            db.session.commit()
            cls.operation_id = operation.id
        cls.stack = ExitStack()
        cls.stack.enter_context(patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
            side_effect=lambda gateway: db.session.get(SortDateOperation, cls.operation_id)))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        cls.fixture.tearDownClass()

    def assert_compact(self, page, board):
        root = page.locator(f'[data-neorain-board-page="{board}"]')
        self.assertNotRegex(root.inner_text(), r'UPSTREAM|GOOGLE|NEO_ONLY|Use Google|Reset to Google')
        for value in root.locator('[data-rain-override-readout]:visible').all_text_contents():
            self.assertRegex(value, r'^(?:\d{2}:\d{2}|\u2014)$')
        self.assertEqual(root.locator('[data-rain-override-reset]:visible').count(), 0)
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
        geometry = root.locator('.rain-override:visible').evaluate_all('''els => els.map(el => {
            const r=el.getBoundingClientRect();
            return {width:r.width, scroll:el.scrollWidth, height:r.height,
                nowrap:getComputedStyle(el.querySelector('[data-rain-override-readout]') || el).whiteSpace};
        })''')
        self.assertTrue(all(x['scroll'] <= x['width'] + 1 for x in geometry), geometry)
        self.assertTrue(all(x['height'] <= 50 for x in geometry), geometry)

    def test_desktop_mobile_autosave_reset_checkboxes_and_refresh(self):
        for engine in ('chromium', 'webkit'):
            browser = getattr(self.fixture.pw, engine).launch(headless=True)
            try:
                for width, height in ((1920, 1080), (390, 844), (375, 667)):
                    with self.subTest(engine=engine, width=width):
                        context = browser.new_context(viewport={'width': width, 'height': height})
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        self.fixture().login(page)
                        for board, kind, field in (('inbound', 'arrival', 'block_in'),
                                                   ('outbound', 'departure', 'ramp_load_complete')):
                            self.fixture().ready(page, '/neorain/' + board)
                            self.assert_compact(page, board)
                            if engine == 'chromium':
                                page.screenshot(path=str(self.fixture.evidence / f'rain-{board}-compact-{width}.png'))
                            cell = page.locator(f'.rain-override[data-rain-override-field="{field}"]:visible').first
                            opener = cell.locator('[data-rain-override-open]')
                            page.evaluate('window.rainOriginalDocument = true')
                            opener.click()
                            expect(opener).to_have_attribute('aria-expanded', 'true')
                            reset = cell.locator('[data-rain-override-reset]')
                            expect(reset).to_be_visible()
                            self.assertLessEqual(reset.bounding_box()['width'], 22)
                            self.assertTrue(reset.get_attribute('aria-label').startswith('Reset '))
                            cell.locator('input[type=text]').fill('2460')
                            expect(cell.locator('[data-rain-override-message]')).to_have_text('Invalid time.')
                            self.assertTrue(cell.locator('input[type=text]').evaluate('''el => {
                                const r=el.getBoundingClientRect();
                                return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2) === el;
                            }'''))
                            # Partial input remains a draft until all four digits are valid.
                            cell.locator('input[type=text]').fill('12')
                            page.wait_for_timeout(1200)
                            expect(cell.locator('input[type=text]')).to_have_value('12')
                            with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as saved:
                                cell.locator('input[type=text]').fill('1234')
                            self.assertEqual(saved.value.status, 200)
                            expect(cell.locator('[data-rain-override-readout]')).to_have_text('12:34')
                            opener.click()
                            self.assert_compact(page, board)
                            # The real existing revision poll reloads canonical controls.
                            page.wait_for_function('!window.rainOriginalDocument', timeout=20000)
                            self.assert_compact(page, board)
                            cell = page.locator(f'.rain-override[data-rain-override-field="{field}"]:visible').first
                            expect(cell.locator('[data-rain-override-readout]')).to_have_text('12:34')
                            cell.locator('[data-rain-override-open]').click()
                            with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as reset_response:
                                cell.locator('[data-rain-override-reset]').click()
                            self.assertEqual(reset_response.value.status, 200)
                            expect(cell.locator('[data-rain-override-readout]')).to_have_text('\u2014')
                            cell.locator('[data-rain-override-open]').click()
                            self.assert_compact(page, board)
                            if board == 'outbound':
                                checkbox = page.locator('.rain-override[data-rain-override-field="included"]:visible').first
                                with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as checked:
                                    checkbox.locator('[data-rain-override-checkbox]').click()
                                self.assertEqual(checked.value.status, 200)
                                self.assertNotIn('Include', checkbox.inner_text())
                                checkbox.locator('[data-rain-override-open]').click()
                                with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as reset_check:
                                    checkbox.locator('[data-rain-override-reset]').click()
                                self.assertEqual(reset_check.value.status, 200)
                                expect(checkbox.locator('[data-rain-override-checkbox]')).to_be_checked(checked=reset_check.value.json()['value'] is True)
                                checkbox.locator('[data-rain-override-open]').click()
                        self.assertEqual(errors, [])
                        context.close()
            finally:
                browser.close()
