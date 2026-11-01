"""Authenticated parking/live-state checks with isolated aircraft only."""
from datetime import datetime
from unittest.mock import patch
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateOperation
from app.services.google_motherbrain_parking import apply_google_motherbrain_parking
from tests import test_google_motherbrain_parking as contracts
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.test_security_headers import enforced_csp


class GoogleParkingBrowserTest(unittest.TestCase):
    def test_chromium_parking_sync_and_live_refresh(self):
        self.workflow('chromium', 1920)

    def test_webkit_mobile_parking_sync_and_live_refresh(self):
        self.workflow('webkit', 390)

    def workflow(self, engine, width):
        class Fixture(fixtures.MobileDrawerBrowserTest):
            pass
        with enforced_csp():
            Fixture.setUpClass()
            try:
                with Fixture.app.app_context():
                    helper = contracts.GoogleMotherBrainParkingTest()
                    helper.gateway = Gateway.query.filter_by(code='RFD').one()
                    helper.operation = SortDateOperation(gateway_id=helper.gateway.id, gateway_code='RFD',
                        sort_name='night', sort_date=datetime(2026, 8, 7).date())
                    db.session.add(helper.operation)
                    db.session.flush()
                    helper._add_tail('N401UP')
                    helper._add_tail('N402UP')
                    helper._park('N401UP', 'A01')
                    db.session.commit()
                    operation_id = helper.operation.id
                browser = getattr(Fixture.pw, engine).launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': width, 'height': 900}, service_workers='block')
                    page.set_default_timeout(8000)
                    # Capture the existing controller for deterministic polling,
                    # without installing timers or changing application code.
                    page.add_init_script('''Object.defineProperty(window, 'NeoLiveUpdates', {
                      configurable: true, set(value) {
                        Object.defineProperty(window, 'NeoLiveUpdates', {value: {...value,
                          create(options) {const controller=value.create(options);
                            if (document.querySelector('[data-parking-plan]')) window.parkingTestController=controller;
                            return controller;}}, configurable: true}); }});''')
                    with patch.object(fixtures.MobileDrawerBrowserTest, 'origin', Fixture.origin, create=True):
                        Fixture().login(page)
                        Fixture().ready(page, f'/motherbrain/parking-plan/{operation_id}')
                        root = page.locator('[data-parking-plan]')
                        expect(root).to_have_attribute('data-can-edit', '1')
                        expect(page.locator('[data-parking-slot-id="slot:A:A01:1"]')).to_have_attribute('data-occupied-tail', 'N401UP')
                        before = root.get_attribute('data-parking-revision')
                        with Fixture.app.app_context():
                            apply_google_motherbrain_parking(db.session.get(SortDateOperation, operation_id), 'N401UP', 'A1')
                            db.session.commit()
                        unchanged = page.evaluate('''async () => {const root=document.querySelector('[data-parking-plan]');
                            const url=new URL(root.dataset.stateUrl,location.href);url.searchParams.set('revision',root.dataset.parkingRevision);
                            return (await fetch(url)).json();}''')
                        self.assertFalse(unchanged['changed'])
                        self.assertEqual(unchanged['revision'], before)
                        with Fixture.app.app_context():
                            apply_google_motherbrain_parking(db.session.get(SortDateOperation, operation_id), 'N401UP', 'B2')
                            db.session.commit()
                        # Call the real controller's existing poll callback; the
                        # historical fixture correctly has automatic polling off.
                        page.evaluate('window.parkingTestController.poll()')
                        expect(page.locator('[data-parking-slot-id="slot:B:B02:1"]')).to_have_attribute('data-occupied-tail', 'N401UP')
                        expect(page.locator('[data-parking-slot-id="slot:A:A01:1"]')).to_have_attribute('data-occupied-tail', '')
                        self.assertNotEqual(root.get_attribute('data-parking-revision'), before)
                        # A preflight must receive a full snapshot even when no
                        # poll changed parking; the valid manual save must proceed.
                        if width < 721:
                            page.locator('[data-parking-slot-id="slot:C:C01:1"] [data-mobile-slot-editor-trigger]').click()
                            form = page.locator('#parking-mobile-slot-editor')
                            form.locator('[name=tail_number]').select_option('N402UP')
                            with page.expect_response(lambda response: response.request.method == 'POST' and '/assign' in response.url) as saved:
                                form.locator('button[type=submit]').click()
                            self.assertEqual(saved.value.status, 200)
                            expect(page.locator('[data-parking-slot-id="slot:C:C01:1"]')).to_have_attribute('data-occupied-tail', 'N402UP')
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
                        if engine == 'chromium':
                            page.screenshot(path=str(Fixture.evidence / 'task11-parking-desktop.png'))
                finally:
                    browser.close()
            finally:
                Fixture.tearDownClass()
