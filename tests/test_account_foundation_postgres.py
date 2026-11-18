"""Phase 2 independent transactions on an isolated loopback PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
import os
import unittest

from flask_login import login_user
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app import create_app
from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountScopedRole, AccountSecurityEvent, Gateway, NeoNode, PortalAppAccess, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_authority import Scope, read_authority
from app.services.account_foundation import AccountConflict, decide_access, entitlement_fingerprint, request_access, stage_role
from app.services.account_foundation_schema import ensure_account_history_guards
from tests.test_account_security_phase1 import fixture_user


URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class AccountFoundationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URI)
        if url.host not in {"localhost", "127.0.0.1"} or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing non-disposable database")
        cls.schema = "account_foundation_" + uuid4().hex
        cls.admin_engine = create_engine(URI)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.app = create_app(type("Config", (), dict(
            SECRET_KEY="phase2-postgres", TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI=URI, SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"options":
                f"-csearch_path={cls.schema} -clock_timeout=10000 -cstatement_timeout=15000"}},
        )))
        with cls.app.app_context():
            db.create_all()
            ensure_account_history_guards(db.session.connection())
            ensure_default_gateway_and_nodes()
            db.session.commit()
            cls.engine = db.engine

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        # Preserve this run's isolated evidence, as well as all existing data.
        cls.admin_engine.dispose()

    def prepare(self):
        with self.app.app_context():
            actor = fixture_user("a" + uuid4().hex[:12], platform="grandmaster")
            target = fixture_user("t" + uuid4().hex[:12])
            db.session.commit()
            return actor.id, target.id

    def run_as(self, user_id, callback):
        with self.app.test_request_context():
            login_user(db.session.get(User, user_id))
            try:
                result = callback()
                db.session.commit()
                return result
            except (ValueError, DBAPIError) as error:
                db.session.rollback()
                return type(error).__name__

    def make_attempt(self, target_id):
        return self.run_as(target_id, lambda: request_access("neostaffing", "Synthetic request", str(uuid4()), expected_session=1).id)

    def decision(self, actor_id, target_id, attempt_id, outcome, fingerprint=None):
        return self.run_as(actor_id, lambda: decide_access(
            attempt_id, outcome, "Synthetic review", expected_version=1, expected_session=1, expected_target_session=1,
            expected_entitlement=fingerprint if fingerprint is not None else entitlement_fingerprint(target_id, "neostaffing")).id)

    def test_concurrent_requests_have_one_open_attempt_and_one_creation_audit(self):
        _, target = self.prepare()
        start = Barrier(2)
        def submit():
            start.wait(5)
            return self.run_as(target, lambda: request_access("neostaffing", "Concurrent request", str(uuid4()), expected_session=1).id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sum(isinstance(x, int) for x in results), 1, results)
        self.assertIn("AccountConflict", results)
        with self.app.app_context():
            self.assertEqual(AccountAccessRequest.query.filter_by(user_id=target).count(), 1)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target, action="access_request_created").count(), 1)

    def test_duplicate_retry_key_returns_same_attempt(self):
        _, target = self.prepare()
        key = str(uuid4())
        def submit():
            return self.run_as(target, lambda: request_access("neostaffing", "Same request", key, expected_session=1).id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertIsInstance(results[0], int)
        self.assertEqual(results[0], results[1])

    def test_concurrent_approve_deny_has_one_immutable_decision_no_entitlement(self):
        actor, target = self.prepare()
        attempt = self.make_attempt(target)
        start = Barrier(2)
        def decide(outcome):
            start.wait(5)
            return self.decision(actor, target, attempt, outcome)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(decide, ("approved", "denied")))
        self.assertEqual(sum(isinstance(x, int) for x in results), 1, results)
        self.assertIn("AccountConflict", results)
        with self.app.app_context():
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 1)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target, action="access_request_decided").count(), 1)
            self.assertIsNone(PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").first())

    def test_actor_revocation_while_waiting_reauthorizes_before_decision(self):
        actor, target = self.prepare()
        attempt = self.make_attempt(target)
        waiting = Event()
        def before(_conn, _cursor, statement, *_args):
            if "neo_nodes" in statement and "FOR UPDATE" in statement:
                waiting.set()
        with self.app.app_context():
            NeoNode.query.filter_by(code="motherbrain").with_for_update().one()
            event.listen(self.engine, "before_cursor_execute", before)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(self.decision, actor, target, attempt, "approved")
                    self.assertTrue(waiting.wait(5))
                    user = db.session.get(User, actor)
                    user.is_active = False
                    user.auth_session_version += 1
                    db.session.commit()
                    self.assertEqual(future.result(timeout=12), "ValueError")
            finally:
                db.session.rollback()
                event.remove(self.engine, "before_cursor_execute", before)
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 0)

    def test_changed_entitlement_and_actor_rank_reject_stale_decision(self):
        actor, target = self.prepare()
        attempt = self.make_attempt(target)
        with self.app.app_context():
            old = entitlement_fingerprint(target, "neostaffing")
            db.session.add(PortalAppAccess(user_id=target, app_code="neostaffing", role="watcher", status="denied"))
            db.session.commit()
        self.assertEqual(self.decision(actor, target, attempt, "approved", old), "AccountConflict")
        with self.app.app_context():
            db.session.get(User, actor).role = "watcher"
            db.session.commit()
        self.assertEqual(self.decision(actor, target, attempt, "approved"), "ValueError")

    def test_decision_insert_failure_rolls_back_state_and_audit(self):
        actor, target = self.prepare()
        attempt = self.make_attempt(target)
        def fail(*_args):
            raise ValueError("Injected persistence failure")
        event.listen(AccountAccessDecision, "before_insert", fail)
        try:
            self.assertEqual(self.decision(actor, target, attempt, "approved"), "ValueError")
        finally:
            event.remove(AccountAccessDecision, "before_insert", fail)
        with self.app.app_context():
            request = db.session.get(AccountAccessRequest, attempt)
            self.assertEqual((request.state, request.version), ("pending", 1))
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 0)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target, action="access_request_decided").count(), 0)

    def test_concurrent_staging_versions_and_live_roles_unchanged(self):
        actor, target = self.prepare()
        with self.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one().id
            node = NeoNode.query.filter_by(code="scorpion").one().id
            before = read_authority(target)
        start = Barrier(2)
        def stage(role):
            start.wait(5)
            return self.run_as(actor, lambda: stage_role(target, Scope("node", "neogateway", gateway, node), role, True,
                "Synthetic mapping", expected_version=0, expected_session=1, expected_target_session=1).id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(stage, ("operator", "simulator")))
        self.assertEqual(sum(isinstance(x, int) for x in results), 1, results)
        self.assertIn("AccountConflict", results)
        with self.app.app_context():
            self.assertEqual(read_authority(target), before)
            self.assertEqual(AccountScopedRole.query.filter_by(user_id=target).count(), 1)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target, action="scoped_role_staged").count(), 1)

    def test_database_history_guards_and_repeated_install(self):
        actor, target = self.prepare()
        attempt = self.make_attempt(target)
        self.assertIsInstance(self.decision(actor, target, attempt, "approved"), int)
        with self.app.app_context():
            for _ in range(2):
                ensure_account_history_guards(db.session.connection())
                db.session.commit()
            for table in ("account_security_events", "account_access_decisions"):
                for sql in (f"UPDATE {table} SET id=id", f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
                    with self.subTest(sql=sql), self.assertRaises(DBAPIError):
                        db.session.execute(text(sql))
                    db.session.rollback()
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 1)
