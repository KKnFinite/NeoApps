"""SubZero FIN/CLEAR remains independent of an unavailable private Vault."""
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation, SortDateParkingAssignment, NeoSubZeroArchiveDelivery
from app.services.neosubzero_archive import drain
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser.test_security_headers import enforced_csp


class ArchiveDeliveryBrowserTest(unittest.TestCase):
    def test_subzero_fin_clear_and_retry_pending_under_storage_outage(self):
        with enforced_csp():
            fixture = fixtures.UccBrowserTest
            fixture.setUpClass()
            origin = patch.object(fixtures.MobileDrawerBrowserTest, 'origin', fixture.origin, create=True)
            origin.start()
            try:
                for engine, width in (('chromium', 1920), ('webkit', 390)):
                    with self.subTest(engine=engine):
                        with fixture.app.app_context():
                            helper = fixtures.NeoSubZeroUccTest()
                            helper.operation = db.session.get(SortDateOperation, fixture.operation_id)
                            mission = helper._departure('ARCHIVE-' + engine, 'N-ARCH-' + engine.upper(), 'SDF', 4)
                            mission_id, gateway_id = mission.id, helper.operation.gateway_id
                            db.session.add(SortDateParkingAssignment(sort_date_operation_id=fixture.operation_id,
                                tail_number=mission.assigned_tail_number, ramp_code='D', position_code='D04'))
                            db.session.commit()
                        browser = getattr(fixture.pw, engine).launch(headless=True)
                        try:
                            page = browser.new_page(viewport={'width': width, 'height': 900}, service_workers='block')
                            page.set_default_timeout(8000)
                            with patch('app.services.neoscorpion_learning_vault._r2_client',
                                       side_effect=AssertionError('No synchronous archive I/O')) as remote:
                                fixture().login(page)
                                fixture().ready(page, f'/neosubzero/operations?mode=outbound&mission={mission_id}')
                                pane = page.locator('[data-ops-detail]')
                                expect(pane).to_be_visible()
                                def submit(button):
                                    with page.expect_response(lambda r: r.request.method == 'POST' and
                                            r.url.endswith('/operations/mutate')) as saved:
                                        button.click()
                                    self.assertEqual(saved.value.status, 200)
                                submit(pane.locator('button[value=initial_contact]'))
                                form = pane.locator('[data-subzero-deice-form]')
                                form.locator('[name=treatment_plan]').select_option('one_type_i')
                                form.locator('[name=pass1_surface_area]').select_option('wings_tail')
                                form.locator('[name=pass1_start]').fill('22:00')
                                form.locator('[name=pass1_end]').fill('22:02')
                                submit(form.locator('button[type=submit]'))
                                row = page.locator(f'[data-ops-row][data-mission-id="{mission_id}"]')
                                expect(row).to_have_attribute('data-status', 'fin')
                                submit(pane.locator('button[value=clear]'))
                                expect(row).to_have_attribute('data-status', 'cleared')
                                remote.assert_not_called()
                            with fixture.app.app_context(), patch(
                                    'app.services.neoscorpion_learning_vault.export_subzero_record',
                                    side_effect=RuntimeError('R2 unavailable')):
                                self.assertEqual(drain(gateway_id), 0)
                                receipt = NeoSubZeroArchiveDelivery.query.filter(
                                    NeoSubZeroArchiveDelivery.source_id.like(f'departure:%:pass:1')).order_by(
                                    NeoSubZeroArchiveDelivery.id.desc()).first()
                                self.assertIsNone(receipt.verified_at)
                                self.assertIsNone(receipt.delivered_checksum)
                                self.assertTrue(receipt.last_error)
                            expect(row).to_have_attribute('data-status', 'cleared')
                        finally:
                            browser.close()
            finally:
                origin.stop()
                fixture.tearDownClass()
