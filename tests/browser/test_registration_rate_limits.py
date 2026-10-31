"""Actual public registration forms under enforced CSP/CSRF, mocked Brevo."""
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import AuthRateLimitState, GatewayMembership, PortalAppAccess, User, UserToken
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.test_security_headers import enforced_csp


class RegistrationRateLimitsBrowserTest(unittest.TestCase):
    def test_registration_and_throttled_drafts_in_chromium_and_webkit(self):
        with enforced_csp(), patch('app.auth.routes.email_service.send_email_verification') as send:
            fixture = fixtures.MobileDrawerBrowserTest
            fixture.setUpClass()
            fixture.app.config.update(AUTH_REGISTRATION_IP_MAX_ATTEMPTS=2,
                                      AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS=1)
            try:
                for engine, width in (('chromium', 1920), ('webkit', 390)):
                    with self.subTest(engine=engine):
                        with fixture.app.app_context():
                            AuthRateLimitState.query.filter_by(action='registration').delete()
                            db.session.commit()
                        send.reset_mock()
                        browser = getattr(fixture.pw, engine).launch(headless=True)
                        try:
                            page = browser.new_page(viewport={'width': width, 'height': 900}, service_workers='block')
                            for attempt, expected in ((1, 200), (1, 429), (2, 200), (3, 429)):
                                email = f'{engine}-{attempt}@example.test'
                                response = page.goto(fixture.origin + '/create-account')
                                self.assertEqual(response.status, 200)
                                self.assertIn('content-security-policy', response.headers)
                                self.assertTrue(page.locator('input[name=csrf_token]').input_value())
                                values = dict(first_name='Retain employee draft', last_name='Fixture',
                                    employee_id=f'BROWSER-{engine}-{attempt}', supervisor_name='Test Supervisor',
                                    email=email, work_area='Ramp', access_reason='Isolated onboarding test',
                                    password='violet river lantern', confirm_password='violet river lantern')
                                for name, value in values.items():
                                    page.locator(f'[name={name}]').fill(value)
                                page.locator('[name=app_codes][value=neostaffing]').check()
                                with page.expect_response(lambda r: r.request.method == 'POST' and
                                        r.url == fixture.origin + '/create-account') as saved:
                                    page.get_by_role('button', name='Request Access', exact=True).click()
                                self.assertEqual(saved.value.status, expected)
                                if expected == 200:
                                    expect(page.get_by_role('heading', name='Verify Your Email', exact=True)).to_be_visible()
                                else:
                                    self.assertTrue(int(saved.value.headers['retry-after']) > 0)
                                    expect(page.get_by_text('Too many account requests.', exact=False)).to_be_visible()
                                    expect(page.locator('[name=first_name]')).to_have_value('Retain employee draft')
                                    expect(page.locator('[name=email]')).to_have_value(email)
                                    expect(page.locator('[name=password]')).to_have_value('')
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                            self.assertEqual(send.call_count, 2)
                            with fixture.app.app_context():
                                for attempt in (1, 2):
                                    user = User.query.filter_by(email=f'{engine}-{attempt}@example.test').one()
                                    self.assertIsNone(user.email_verified_at)
                                    self.assertEqual(UserToken.query.filter_by(user_id=user.id).count(), 1)
                                    self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).one().status, 'pending')
                                    self.assertTrue(all(row.status == 'pending' for row in
                                        PortalAppAccess.query.filter_by(user_id=user.id)))
                                self.assertIsNone(User.query.filter_by(email=f'{engine}-3@example.test').first())
                        finally:
                            browser.close()
            finally:
                fixture.tearDownClass()
