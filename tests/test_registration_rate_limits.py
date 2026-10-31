"""Registration admission with isolated users, fake clocks and mocked Brevo."""
from datetime import datetime, timedelta
import re
import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import AuthRateLimitState, GatewayMembership, GatewayNodeRole, PortalAppAccess, User, UserToken
from app.services import auth_rate_limits as limits
from tests import test_auth_account_flows as fixtures


class RegistrationRateLimitsTest(unittest.TestCase):
    _account_form = fixtures.AuthAccountFlowsTest._account_form
    _user = fixtures.AuthAccountFlowsTest._user

    def setUp(self):
        fixtures.AuthAccountFlowsTest.setUp(self)
        self.mail_patch = patch('app.auth.routes.email_service.send_email_verification')
        self.send = self.mail_patch.start()
        self.addCleanup(self.mail_patch.stop)

    tearDown = fixtures.AuthAccountFlowsTest.tearDown

    def post(self, email='employee@example.test', employee='EMP-001', ip='198.51.100.10', **overrides):
        return self.client.post('/create-account', data=self._account_form(
            email=email, employee_id=employee, **overrides), environ_base={'REMOTE_ADDR': ip})

    def state(self, subject, value):
        return limits._get_state(limits.REGISTRATION_ACTION, subject, value, lock=False)

    def test_normal_registration_and_shared_workplace_keep_access_pending(self):
        for number in range(8):
            result = self.post(email=f'employee{number}@example.test', employee=f'EMP-{number}')
            self.assertEqual(result.status_code, 200)
        self.assertEqual(self.send.call_count, 8)
        self.assertEqual(User.query.count(), 8)
        self.assertEqual(UserToken.query.count(), 8)
        self.assertEqual(GatewayMembership.query.filter_by(status='pending').count(), 8)
        self.assertEqual(PortalAppAccess.query.filter_by(status='pending').count(), 8)
        self.assertEqual(GatewayNodeRole.query.count(), 0)
        self.assertTrue(all(user.email_verified_at is None for user in User.query.all()))
        self.assertEqual(self.state('ip', '198.51.100.10').attempt_count, 8)

    def test_identifier_limit_precedes_hash_provisioning_token_and_delivery(self):
        for _ in range(5):
            self.assertEqual(self.post(password='bad').status_code, 400)
        with patch('app.auth.routes._build_user_from_account_form') as build, \
                patch('app.auth.routes.set_user_password') as hash_password, \
                patch('app.auth.routes.create_user_token') as token:
            result = self.post(email=' Employee@Example.Test ', first_name='Keep this draft',
                               password='never-reflect-this-password')
            self.assertEqual(result.status_code, 429)
            build.assert_not_called()
            hash_password.assert_not_called()
            token.assert_not_called()
        self.send.assert_not_called()
        self.assertIn(b'Keep this draft', result.data)
        self.assertIn(b'Employee@Example.Test', result.data)
        self.assertNotIn(b'never-reflect-this-password', result.data)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertTrue(1 <= int(result.headers['Retry-After']) <= 3600)
        self.assertEqual(User.query.count(), 0)
        self.assertEqual(UserToken.query.count(), 0)

    def test_rotating_emails_cannot_bypass_ip_or_create_unbounded_state(self):
        self.app.config['AUTH_REGISTRATION_IP_MAX_ATTEMPTS'] = 3
        for number in range(3):
            self.assertEqual(self.post(email=f'rotating{number}@example.test', password='bad').status_code, 400)
        for number in range(3, 15):
            self.assertEqual(self.post(email=f'rotating{number}@example.test').status_code, 429)
        self.assertEqual(AuthRateLimitState.query.count(), 4)  # one IP + three emails
        self.send.assert_not_called()

    def test_one_blocked_identifier_does_not_spend_remaining_workplace_allowance(self):
        self.app.config.update(AUTH_REGISTRATION_IP_MAX_ATTEMPTS=3,
                               AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS=2)
        for _ in range(2):
            self.assertEqual(self.post(password='bad').status_code, 400)
        for _ in range(10):
            self.assertEqual(self.post().status_code, 429)
        self.assertEqual(self.state('ip', '198.51.100.10').attempt_count, 2)
        self.assertEqual(self.post(email='different@example.test').status_code, 200)
        self.assertEqual(self.send.call_count, 1)

    def test_cooldown_boundary_and_blocked_requests_never_extend_window(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 1
        start = datetime(2026, 10, 31, 12)
        with patch.object(limits, 'datetime') as clock:
            clock.utcnow.return_value = start
            self.assertEqual(self.post(password='bad').status_code, 400)
            first = self.post()
            self.assertEqual(first.headers['Retry-After'], '3600')
            clock.utcnow.return_value = start + timedelta(seconds=3599)
            self.assertEqual(self.post().headers['Retry-After'], '1')
            self.assertEqual(self.state('identifier', 'employee@example.test').window_started_at, start)
            clock.utcnow.return_value = start + timedelta(seconds=3600)
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.send.call_count, 1)

    def test_reservation_survives_failed_registration_and_new_session(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 1
        self.assertEqual(self.post(password='bad').status_code, 400)
        db.session.remove()
        self.assertEqual(self.post().status_code, 429)
        self.send.assert_not_called()

    def test_rate_limit_records_contain_only_hmac_subjects(self):
        self.post(password='bad')
        records = AuthRateLimitState.query.all()
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertRegex(record.subject_digest, r'^[0-9a-f]{64}$')
            self.assertNotIn('employee@example.test', str(record.__dict__))
            self.assertNotIn('198.51.100.10', str(record.__dict__))
        self.assertNotEqual(records[0].subject_digest, records[1].subject_digest)

    def test_admission_database_failure_fails_closed_without_sensitive_logs(self):
        private_detail = 'do-not-log-private-email@example.test'
        failure = OperationalError(private_detail, {}, RuntimeError(private_detail))
        with patch.object(db.session, 'commit', side_effect=failure), \
                patch('app.auth.routes._build_user_from_account_form') as build, \
                self.assertLogs(self.app.logger, level='WARNING') as logs:
            result = self.post()
            build.assert_not_called()
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.headers['Retry-After'], '60')
        self.assertNotIn(private_detail, str(logs.output))
        self.assertNotIn('employee@example.test', str(logs.output))
        self.assertEqual(User.query.count(), 0)
        self.assertEqual(UserToken.query.count(), 0)
        self.send.assert_not_called()
        self.assertEqual(self.post().status_code, 200)

    def test_failed_account_transaction_rolls_back_user_access_and_token(self):
        failure = OperationalError('isolated', {}, RuntimeError('isolated'))
        with patch('app.auth.routes.create_user_token', side_effect=failure):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(User.query.count(), 0)
        self.assertEqual(GatewayMembership.query.count(), 0)
        self.assertEqual(PortalAppAccess.query.count(), 0)
        self.assertEqual(UserToken.query.count(), 0)
        self.assertEqual(self.state('ip', '198.51.100.10').attempt_count, 1)
        self.send.assert_not_called()

    def test_registration_admission_failure_does_not_poison_login_or_password_recovery(self):
        target = self._user('existing-employee', verified=True)
        db.session.commit()
        with patch('app.auth.routes.reserve_registration_attempt',
                   side_effect=OperationalError('fixture', {}, RuntimeError())):
            self.assertEqual(self.post().status_code, 503)
        login = self.client.post('/login', data={'email': target.email, 'password': 'TestPassword123!'})
        self.assertEqual(login.status_code, 302)
        with patch('app.auth.routes.email_service.send_password_reset') as send_reset:
            recovery = self.client.post('/forgot-password', data={'email': target.email})
            self.assertEqual(recovery.status_code, 200)
            send_reset.assert_called_once()

    def test_existing_email_and_other_identity_conflicts_remain_generic(self):
        self.assertEqual(self.post().status_code, 200)
        self.send.reset_mock()
        same_email = self.post(email=' EMPLOYEE@EXAMPLE.TEST ', employee='DIFFERENT')
        same_employee = self.post(email='other@example.test')
        message = b'An account may already exist with the information provided.'
        for result in (same_email, same_employee):
            self.assertEqual(result.status_code, 400)
            self.assertIn(message, result.data)
            self.assertNotIn(b'email is already', result.data)
        self.assertEqual(User.query.count(), 1)
        self.assertEqual(UserToken.query.count(), 1)
        self.send.assert_not_called()

    def test_existing_disabled_grants_and_user_are_never_reactivated(self):
        self.assertEqual(self.post().status_code, 200)
        membership = GatewayMembership.query.one()
        access = PortalAppAccess.query.one()
        membership.status, membership.is_active = 'approved', False
        access.status, access.is_active = 'denied', False
        db.session.commit()
        self.send.reset_mock()
        self.assertEqual(self.post().status_code, 400)
        db.session.expire_all()
        self.assertEqual((membership.status, membership.is_active), ('approved', False))
        self.assertEqual((access.status, access.is_active), ('denied', False))
        self.assertEqual(User.query.count(), 1)
        self.send.assert_not_called()

    def test_registration_login_and_password_reset_states_are_independent(self):
        self.app.config.update(AUTH_LOGIN_IP_MAX_FAILURES=1, AUTH_PASSWORD_RESET_IP_MAX_ATTEMPTS=1,
                               AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS=1)
        limits.record_login_failure('198.51.100.10', 'employee@example.test')
        limits.record_password_reset_request('198.51.100.10', 'employee@example.test')
        db.session.commit()
        before = [(row.id, row.attempt_count, row.blocked_until) for row in AuthRateLimitState.query.all()]
        self.assertEqual(self.post(password='bad').status_code, 400)
        self.assertEqual(self.post().status_code, 429)
        for row_id, count, blocked in before:
            row = db.session.get(AuthRateLimitState, row_id)
            self.assertEqual((row.attempt_count, row.blocked_until), (count, blocked))
        self.assertTrue(limits.login_is_limited('198.51.100.10', 'employee@example.test'))
        self.assertTrue(limits.password_reset_is_limited('198.51.100.10', 'employee@example.test'))
        self.assertFalse(limits.login_is_limited('198.51.100.20', 'another@example.test'))

    def test_untrusted_forwarding_cannot_rotate_the_ip_allowance(self):
        self.app.config.update(AUTH_REGISTRATION_IP_MAX_ATTEMPTS=1, AUTH_TRUST_PROXY_HEADERS=True,
                               AUTH_TRUSTED_PROXY_IPS=('10.0.0.0/24',))
        for index, expected in ((1, 400), (2, 429)):
            result = self.client.post('/create-account', data=self._account_form(password='bad'),
                environ_base={'REMOTE_ADDR': '198.51.100.10'},
                headers={'X-Forwarded-For': f'198.51.100.{index}'})
            self.assertEqual(result.status_code, expected)

    def test_trusted_proxy_and_normalized_ipv6_use_existing_resolution_rules(self):
        self.app.config.update(AUTH_REGISTRATION_IP_MAX_ATTEMPTS=1, AUTH_TRUST_PROXY_HEADERS=True,
                               AUTH_TRUSTED_PROXY_IPS=('10.0.0.0/24',))
        for address in ('198.51.100.1', '198.51.100.2'):
            result = self.client.post('/create-account', data=self._account_form(password='bad'),
                environ_base={'REMOTE_ADDR': '10.0.0.5'}, headers={'X-Forwarded-For': address})
            self.assertEqual(result.status_code, 400)
        self.assertEqual(self.post(ip='2001:0db8:0:0:0:0:0:1', password='bad').status_code, 400)
        self.assertEqual(self.post(ip='2001:db8::1').status_code, 429)

    def test_csrf_rejection_spends_no_quota_and_csp_stays_enforced(self):
        self.app.config.update(CSRF_PROTECT_TESTING=True, SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION=True)
        page = self.client.get('/create-account')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Content-Security-Policy', page.headers)
        self.assertNotIn('Content-Security-Policy-Report-Only', page.headers)
        self.assertEqual(self.post().status_code, 400)
        self.assertEqual(AuthRateLimitState.query.count(), 0)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        self.assertEqual(self.post(password='bad', csrf_token=token).status_code, 400)
        self.assertEqual(AuthRateLimitState.query.count(), 2)
        self.send.assert_not_called()

    def test_explicitly_disabled_limiter_and_invalid_configuration(self):
        self.app.config['AUTH_RATE_LIMIT_ENABLED'] = False
        for _ in range(6):
            self.assertEqual(self.post(password='bad').status_code, 400)
        self.assertEqual(AuthRateLimitState.query.count(), 0)
        self.app.config['AUTH_RATE_LIMIT_ENABLED'] = True
        for setting in ('AUTH_REGISTRATION_WINDOW_SECONDS', 'AUTH_REGISTRATION_IP_MAX_ATTEMPTS',
                        'AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'):
            with self.subTest(setting=setting), patch.dict(self.app.config, {setting: 0}):
                with self.assertRaisesRegex(RuntimeError, 'must be positive'):
                    limits.initialize_auth_rate_limit_storage(self.app)
