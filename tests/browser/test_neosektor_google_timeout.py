"""Authenticated screens with a real gspread client and a fake Google provider."""
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import NeoSektorBallmatWaveCount, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services import neosektor_sheets_compat as sheets
from tests import test_neosektor_integration_modes as contracts
from tests import test_neosektor_google_timeout as transport
from tests.browser import test_mobile_drawer as browser_fixture
from tests.browser.test_security_headers import enforced_csp


class NeoSektorGoogleTimeoutBrowserTest(unittest.TestCase):
    def test_google_reads_saves_uncertain_response_and_recovery_in_both_engines(self):
        with enforced_csp():
            fixture = browser_fixture.MobileDrawerBrowserTest
            fixture.setUpClass()
            with fixture.app.app_context():
                operator = User(username='google-operator', email='google-operator@example.test',
                    role='master', email_verified_at=datetime.utcnow())
                set_user_password(operator, 'BrowserFixture123!')
                db.session.add(operator)
                db.session.flush()
                backfill_default_gateway_node_roles(operator, role='master')
                db.session.commit()
            values = contracts._complete_sheet_values()
            writes, clients = [], []
            control = {'write_timeout': False, 'read_timeout': False}

            def create(_credentials, **_options):
                provider = transport.GoogleFixture()
                provider.values, provider.writes = values, writes
                provider.fail_cell = 'B2' if control['write_timeout'] else None
                provider.fail_at = 1 if control['read_timeout'] else None
                clients.append(provider)
                return provider.client

            def clear():
                with fixture.app.app_context():
                    sheets.clear_neosektor_google_cache()

            try:
                with patch.dict(os.environ, {**contracts.FAKE_SHEETS_ENV,
                        sheets.NEOSEKTOR_SHARED_AUTHORITY_TAB_ENV: ''}), \
                        patch.object(sheets.gspread, 'service_account_from_dict', side_effect=create):
                    for engine in ('chromium', 'webkit'):
                        with self.subTest(engine=engine):
                            clear()
                            browser = getattr(fixture.pw, engine).launch(headless=True)
                            try:
                                page = browser.new_page(viewport={'width': 390, 'height': 844}, service_workers='block')
                                fixture().login(page, 'google-operator')
                                fixture().ready(page, '/neosektor/ebm')
                                count = page.locator('input[data-bm-value="first:total"]')
                                button = page.locator('[data-bm-step="1"][data-metric="first"][data-position="total"]')
                                before = int(count.input_value())
                                url = fixture.origin + page.locator('[data-ballmat-operations]').get_attribute('data-update-url')
                                with page.expect_response(lambda r: r.request.method == 'POST' and r.url == url) as saved:
                                    button.click()
                                self.assertEqual(saved.value.status, 200)
                                expect(count).to_have_value(str(before + 1))
                                # The fake provider accepts the write but loses
                                # its response. A 502 cannot trigger a POST replay.
                                control['write_timeout'] = True
                                mark = len(writes)
                                with page.expect_response(lambda r: r.request.method == 'POST' and r.url == url) as uncertain:
                                    button.click()
                                self.assertEqual(uncertain.value.status, 502)
                                expect(page.locator('[data-neosektor-edit-error]')).to_contain_text('unverified')
                                expect(button).to_be_enabled()
                                self.assertEqual(writes[mark:], [('B2', before + 2)])
                                control['write_timeout'] = False
                                clear()
                                fixture().ready(page, '/neosektor/ebm')
                                expect(page.locator('input[data-bm-value="first:total"]')).to_have_value(str(before + 2))
                                # Existing desktop/live read screens still load.
                                page.set_viewport_size({'width': 1920, 'height': 900})
                                fixture().ready(page, '/neosektor/live-counts')
                                fixture().ready(page, '/neosektor/tunnel-conductor')
                                control['read_timeout'] = True
                                clear()
                                page.goto(fixture.origin + '/neosektor/ebm')
                                expect(page.get_by_text('NeoSektor could not open its configured Google workbook.', exact=True)).to_be_visible()
                                control['read_timeout'] = False
                                clear()
                                fixture().ready(page, '/neosektor/ebm')
                            finally:
                                control['write_timeout'] = control['read_timeout'] = False
                                browser.close()
                    for client in clients:
                        client.transport.close.assert_called_once()
                        self.assertTrue(all(call[2]['timeout'] == 5 for call in client.calls))
                    with fixture.app.app_context():
                        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)
            finally:
                fixture.tearDownClass()
