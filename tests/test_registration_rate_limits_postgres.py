"""Small transaction races in a disposable loopback PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from uuid import uuid4
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app import create_app
from app.extensions import db
from app.models import AuthRateLimitState, GatewayMembership, PortalAppAccess, User, UserToken
from app.services import auth_rate_limits as limits
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from tests import test_auth_account_flows as fixtures


URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class RegistrationRateLimitsPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URI)
        if url.host not in {'127.0.0.1', 'localhost'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable database')
        cls.schema = 'registration_' + uuid4().hex
        cls.admin = create_engine(URI)
        with cls.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        config = type('RegistrationConfig', (), dict(
            SECRET_KEY='isolated-registration-only', TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI=URI, SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options':
                f'-csearch_path={cls.schema} -clock_timeout=10000 -cstatement_timeout=15000'}},
            AUTH_REGISTRATION_IP_MAX_ATTEMPTS=20, AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS=5,
            AUTH_REGISTRATION_WINDOW_SECONDS=3600, AUTH_RATE_LIMIT_ENABLED=True,
            GOOGLE_MOTHERBRAIN_READER_ENABLED=False, FLIGHT_API_ENABLED=False,
        ))
        cls.app = create_app(config)
        with cls.app.app_context():
            db.create_all()
            ensure_default_gateway_and_nodes()
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin.dispose()

    def setUp(self):
        self.email = uuid4().hex + '@example.test'
        self.app.config.update(AUTH_REGISTRATION_IP_MAX_ATTEMPTS=20,
                               AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS=5)
        with self.app.app_context():
            AuthRateLimitState.query.delete()
            db.session.commit()
        self.mail = patch('app.auth.routes.email_service.send_email_verification')
        self.send = self.mail.start()
        self.addCleanup(self.mail.stop)

    def post(self, email=None, ip='198.51.100.10'):
        with self.app.test_client() as client:
            return client.post('/create-account', data=fixtures.AuthAccountFlowsTest._account_form(
                self, email=email or self.email, employee_id='EMP-' + uuid4().hex),
                environ_base={'REMOTE_ADDR': ip}).status_code

    def race(self, count, action):
        barrier = Barrier(count)
        def run(number):
            with self.app.app_context():
                barrier.wait(timeout=10)
                result = action(number)
                self.assertEqual(db.session.execute(text('SELECT 1')).scalar_one(), 1)
                return result
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def counter_insert_race(self):
        barrier = Barrier(2)
        def before(_mapper, _connection, state):
            if state.action == limits.REGISTRATION_ACTION and state.subject_type == limits.IP_SUBJECT:
                barrier.wait(timeout=10)
        event.listen(AuthRateLimitState, 'before_insert', before)
        self.addCleanup(lambda: event.remove(AuthRateLimitState, 'before_insert', before))

    def test_first_use_conflict_admits_exactly_one_and_session_stays_usable(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 1
        self.counter_insert_race()
        result = self.race(2, lambda _: limits.reserve_registration_attempt('198.51.100.10', self.email))
        self.assertEqual(sum(value == 0 for value in result), 1)
        with self.app.app_context():
            rows = AuthRateLimitState.query.all()
            self.assertEqual(len(rows), 2)
            self.assertEqual([row.attempt_count for row in rows], [1, 1])

    def test_rotating_identifiers_share_atomic_ip_budget(self):
        self.app.config['AUTH_REGISTRATION_IP_MAX_ATTEMPTS'] = 2
        result = self.race(6, lambda n: limits.reserve_registration_attempt('198.51.100.10', f'{n}@example.test'))
        self.assertEqual(sum(value == 0 for value in result), 2)
        with self.app.app_context():
            self.assertEqual(AuthRateLimitState.query.count(), 3)
            self.assertEqual(AuthRateLimitState.query.filter_by(subject_type='ip').one().attempt_count, 2)

    def test_rotating_ips_share_identifier_budget_without_spending_blocked_ip_allowances(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 2
        result = self.race(6, lambda n: limits.reserve_registration_attempt(f'198.51.100.{n+1}', self.email))
        self.assertEqual(sum(value == 0 for value in result), 2)
        with self.app.app_context():
            self.assertEqual(sum(row.attempt_count for row in AuthRateLimitState.query.filter_by(subject_type='ip')), 2)

    def test_stale_identity_map_cannot_bypass_locked_fresh_state(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 2
        with self.app.app_context():
            limits.reserve_registration_attempt('198.51.100.10', self.email)
        loaded = Barrier(2)
        def action(_):
            retained = limits._get_state(limits.REGISTRATION_ACTION, 'ip', '198.51.100.10', lock=False)
            self.assertEqual(retained.attempt_count, 1)
            loaded.wait(timeout=10)
            return limits.reserve_registration_attempt('198.51.100.10', self.email)
        result = self.race(2, action)
        self.assertEqual(sum(value == 0 for value in result), 1)

    def test_blocked_concurrent_registration_creates_one_user_token_and_email(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 1
        self.counter_insert_race()
        self.assertEqual(sorted(self.race(2, lambda _: self.post())), [200, 429])
        with self.app.app_context():
            user = User.query.filter_by(email=self.email).one()
            self.assertEqual(UserToken.query.filter_by(user_id=user.id).count(), 1)
            self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).one().status, 'pending')
            self.assertEqual(PortalAppAccess.query.filter_by(user_id=user.id).one().status, 'pending')
        self.send.assert_called_once()

    def test_admitted_duplicate_accounts_still_resolve_uniqueness_without_duplicate_email(self):
        barrier = Barrier(2)
        def before(_mapper, _connection, _user):
            barrier.wait(timeout=10)
        event.listen(User, 'before_insert', before)
        try:
            self.assertEqual(sorted(self.race(2, lambda _: self.post())), [200, 400])
        finally:
            event.remove(User, 'before_insert', before)
        with self.app.app_context():
            user = User.query.filter_by(email=self.email).one()
            self.assertEqual(UserToken.query.filter_by(user_id=user.id).count(), 1)
        self.send.assert_called_once()

    def test_failed_account_transaction_has_no_partial_user_or_grants(self):
        with patch('app.auth.routes.create_user_token', side_effect=OperationalError('fixture', {}, RuntimeError())):
            self.assertEqual(self.post(), 500)
        with self.app.app_context():
            self.assertIsNone(User.query.filter_by(email=self.email).first())
            self.assertEqual(AuthRateLimitState.query.filter_by(subject_type='identifier').one().attempt_count, 1)
        self.send.assert_not_called()
        self.assertEqual(self.post(), 200)
        self.send.assert_called_once()

    def test_recreated_app_connection_observes_durable_reservation(self):
        self.app.config['AUTH_REGISTRATION_IDENTIFIER_MAX_ATTEMPTS'] = 1
        with self.app.app_context():
            self.assertEqual(limits.reserve_registration_attempt('198.51.100.10', self.email), 0)
        other = Flask(__name__)
        other.config.update(self.app.config)
        db.init_app(other)
        try:
            with other.app_context():
                self.assertGreater(limits.reserve_registration_attempt('198.51.100.10', self.email), 0)
        finally:
            with other.app_context():
                db.session.remove()
                db.engine.dispose()

    def test_admission_locks_are_released_before_password_hashing(self):
        hashing, resume = Event(), Event()
        def hash_password(*args, **kwargs):
            hashing.set()
            if not resume.wait(10):
                raise RuntimeError('Test did not release hash')
            return set_user_password(*args, **kwargs)
        with patch('app.auth.routes.set_user_password', side_effect=hash_password), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.post)
            try:
                self.assertTrue(hashing.wait(10))
                with self.app.app_context(), db.engine.begin() as connection:
                    self.assertEqual(len(connection.execute(text(
                        'SELECT id FROM auth_rate_limit_states FOR UPDATE NOWAIT')).all()), 2)
            finally:
                resume.set()
            self.assertEqual(future.result(timeout=15), 200)
