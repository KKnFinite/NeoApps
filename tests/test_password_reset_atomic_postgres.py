"""Real reset HTTP requests racing in disposable loopback PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier, Event, local
from uuid import uuid4
from itertools import count
import os
import re
import unittest
from unittest.mock import patch
from flask import has_request_context, request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from app import create_app
from app.extensions import db
from app.models import User, UserToken
from app.services import user_tokens as tokens
from app.services.password_policy import set_user_password
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class AtomicPasswordResetPostgresTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        url = make_url(URI)
        if url.host not in {'127.0.0.1', 'localhost'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable database')
        cls.schema = 'reset_atomic_' + uuid4().hex
        cls.admin_engine = create_engine(URI)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        config = type('Config', (), dict(
            SECRET_KEY='isolated-atomic-reset-secret',
            TESTING=True,
            AUTO_BOOTSTRAP_DATABASE=False,
            CSRF_PROTECT_TESTING=True,
            SESSION_COOKIE_SECURE=False,
            SQLALCHEMY_DATABASE_URI=URI,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': (
                f'-csearch_path={cls.schema} -clock_timeout=10000 -cstatement_timeout=15000'
            )}},
            GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
            GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
            FLIGHT_API_ENABLED=False,
            NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED=False,
        ))
        cls.app = create_app(config)
        cls.number = count(1)
        with cls.app.app_context():
            db.create_all()
            ensure_default_gateway_and_nodes()
            ensure_default_permission_rules()
            db.session.commit()
            cls.engine = db.engine

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin_engine.dispose()

    def setUp(self):
        self.address = '198.51.100.' + str(next(self.number))
        self.passwords = ['violet harbor lantern one', 'silver meadow lantern two']
        with self.app.app_context():
            user = User(
                username='atomic-' + uuid4().hex,
                email=uuid4().hex + '@example.test',
                role='watcher', email_verified_at=datetime.utcnow(),
            )
            set_user_password(user, 'OriginalFixturePassword123!')
            db.session.add(user)
            db.session.flush()
            first, record = tokens.create_user_token(user, tokens.PASSWORD_RESET)
            second = 'legacy-' + uuid4().hex
            # Exercise legacy outstanding links even though normal issuance
            # already revokes the preceding link.
            legacy = UserToken(
                user_id=user.id, token_hash=tokens.hash_user_token(second),
                token_type=tokens.PASSWORD_RESET,
                expires_at=datetime.utcnow() + timedelta(hours=1),
            )
            db.session.add(legacy)
            db.session.commit()
            self.user_id = user.id
            self.username = user.username
            self.initial_hash = user.password_hash
            self.version = user.auth_session_version
            self.raw = [first, second]
            self.record_id = record.id
        self.existing = self.app.test_client()
        self.assertEqual(self.login(self.existing, 'OriginalFixturePassword123!').status_code, 302)

    def csrf(self, client, path):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        match = re.search(b'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def login(self, client, password):
        csrf = self.csrf(client, '/login')
        return client.post('/login', data={'username': self.username, 'password': password, 'csrf_token': csrf}, environ_overrides={'REMOTE_ADDR': self.address})

    def post(self, client, raw, password):
        csrf = self.csrf(client, '/reset-password/' + raw)
        return client.post('/reset-password/' + raw, data={'password': password, 'confirm_password': password, 'csrf_token': csrf})

    def race(self, links):
        gate = Barrier(2)
        state = local()

        def synchronize(_connection, _cursor, statement, *_args):
            # Both requests discover the token before either can lock the
            # account. This reproduces the original race deterministically.
            if (
                has_request_context() and request.endpoint == 'auth.reset_password'
                and request.method == 'POST'
                and statement.lstrip().upper().startswith('SELECT')
                and 'user_tokens.token_hash =' in statement
                and not getattr(state, 'arrived', False)
            ):
                state.arrived = True
                gate.wait(timeout=10)
        event.listen(self.engine, 'after_cursor_execute', synchronize)
        try:

            def worker(index):
                state.arrived = False
                client = self.app.test_client()
                return self.post(client, links[index], self.passwords[index]).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, range(2)))
        finally:
            event.remove(self.engine, 'after_cursor_execute', synchronize)
        self.assertEqual(sorted(results), [302, 400])
        winner = results.index(302)
        loser = 1 - winner
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertTrue(user.check_password(self.passwords[winner]))
            self.assertFalse(user.check_password(self.passwords[loser]))
            self.assertEqual(user.auth_session_version, self.version + 1)
            self.assertTrue(all((record.is_used for record in UserToken.query.filter_by(user_id=self.user_id, token_type=tokens.PASSWORD_RESET))))
        stale = self.existing.get('/portal')
        self.assertEqual(stale.status_code, 302)
        self.assertTrue(stale.location.startswith('/login'))
        self.assertEqual(self.login(self.app.test_client(), self.passwords[loser]).status_code, 401)
        self.assertEqual(self.login(self.app.test_client(), self.passwords[winner]).status_code, 302)
        return results

    def test_same_token_two_simultaneous_requests_exactly_one_wins(self):
        self.race([self.raw[0], self.raw[0]])

    def test_different_outstanding_tokens_for_same_account_exactly_one_wins(self):
        self.race(self.raw)

    def test_expiry_revalidated_after_waiting_for_account_lock(self):
        self.check_waiting_rejection('expires_at')

    def test_revocation_revalidated_after_waiting_for_account_lock(self):
        self.check_waiting_rejection('used_at')

    def check_waiting_rejection(self, column):
        client = self.app.test_client()
        csrf = self.csrf(client, '/reset-password/' + self.raw[0])
        waiting = Event()

        def before(_connection, _cursor, statement, *_args):
            if (
                has_request_context() and request.method == 'POST'
                and 'FROM users' in statement and 'FOR UPDATE' in statement
            ):
                waiting.set()
        event.listen(self.engine, 'before_cursor_execute', before)
        try:
            with self.admin_engine.connect() as connection:
                transaction = connection.begin()
                connection.execute(text(f'SELECT id FROM "{self.schema}".users WHERE id=:id FOR UPDATE'), {'id': self.user_id})

                def worker():
                    return client.post('/reset-password/' + self.raw[0], data={'password': self.passwords[0], 'confirm_password': self.passwords[0], 'csrf_token': csrf}).status_code
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(worker)
                    try:
                        self.assertTrue(waiting.wait(5))
                        stamp = datetime.utcnow() - timedelta(seconds=1) if column == 'expires_at' else datetime.utcnow()
                        connection.execute(text(f'UPDATE "{self.schema}".user_tokens SET {column}=:stamp WHERE id=:id'), {'stamp': stamp, 'id': self.record_id})
                        transaction.commit()
                    finally:
                        if transaction.is_active:
                            transaction.rollback()
                    self.assertEqual(future.result(timeout=10), 400)
        finally:
            event.remove(self.engine, 'before_cursor_execute', before)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.user_id).password_hash, self.initial_hash)

    def test_commit_failure_rolls_back_password_and_claim_postgres(self):
        client = self.app.test_client()
        csrf = self.csrf(client, '/reset-password/' + self.raw[0])
        with patch('app.auth.routes.db.session.commit', side_effect=OperationalError('isolated commit failure', {}, Exception('fixture'))):
            response = client.post('/reset-password/' + self.raw[0], data={'password': self.passwords[0], 'confirm_password': self.passwords[0], 'csrf_token': csrf})
        self.assertEqual(response.status_code, 500)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.user_id).password_hash, self.initial_hash)
            self.assertEqual(db.session.get(User, self.user_id).auth_session_version, self.version)
            self.assertTrue(all((not row.is_used for row in UserToken.query.filter_by(user_id=self.user_id, token_type=tokens.PASSWORD_RESET))))
        self.assertEqual(self.post(client, self.raw[0], self.passwords[0]).status_code, 302)

    def test_invalid_password_does_not_consume_token_postgres(self):
        client = self.app.test_client()
        self.assertEqual(self.post(client, self.raw[0], 'short').status_code, 400)
        with self.app.app_context():
            self.assertIsNone(db.session.get(UserToken, self.record_id).used_at)
            self.assertEqual(db.session.get(User, self.user_id).password_hash, self.initial_hash)
        self.assertEqual(self.post(client, self.raw[0], self.passwords[0]).status_code, 302)
