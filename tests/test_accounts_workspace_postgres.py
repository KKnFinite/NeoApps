"""Real PostgreSQL locking for Phase 3 compatibility writes, no staged-role cutover."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

from flask_login import login_user
from sqlalchemy import text

from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountSecurityEvent, PortalAppAccess, User
from app.services.accounts_workspace import close_open_attempt, lock_self, new_attempt, review_token, verify_review
from app.services.account_security import lock_administration
from app.auth.routes import _approve_portal_app_access, _deny_portal_app_access
from app.services.permission_rules import ensure_default_permission_rules
from tests import test_account_foundation_postgres as fixture


@skipUnless(fixture.URI, "Requires disposable loopback PostgreSQL")
class AccountsWorkspacePostgresTest(TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.AccountFoundationPostgresTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            ensure_default_permission_rules()
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        fixture.AccountFoundationPostgresTest.tearDownClass.__func__(cls)

    prepare = fixture.AccountFoundationPostgresTest.prepare
    run_as = fixture.AccountFoundationPostgresTest.run_as

    def request_attempt(self, target):
        def action():
            user = lock_self()
            return new_attempt(user, "neostaffing", "Synthetic Phase 3 staffing request").id
        return self.run_as(target, action)

    def token(self, actor, target):
        with self.app.test_request_context():
            login_user(db.session.get(User, actor))
            return review_token(db.session.get(User, target))

    def decide(self, actor, target, token, outcome):
        def action():
            locked = lock_administration()
            if locked.role != "grandmaster":
                raise ValueError("Authority revoked")
            user = User.query.filter_by(id=target).populate_existing().with_for_update().one()
            verify_review(user, token)
            access = PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").one_or_none()
            if not access:
                access = PortalAppAccess(user_id=target, app_code="neostaffing", role="watcher", status="pending", is_active=True)
                db.session.add(access)
                db.session.flush()
            if outcome == "approved":
                _approve_portal_app_access(access, "operator", "Synthetic approval")
            else:
                _deny_portal_app_access(access, "Synthetic denial")
            return outcome
        return self.run_as(actor, action)

    def test_competing_approval_denial_has_one_decision_and_one_effective_result(self):
        actor, target = self.prepare()
        attempt = self.request_attempt(target)
        token = self.token(actor, target)
        barrier = Barrier(2)
        def run(outcome):
            barrier.wait(5)
            return self.decide(actor, target, token, outcome)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ["approved", "denied"]))
        self.assertEqual(sum(r in {"approved", "denied"} for r in results), 1, results)
        self.assertIn("AccountConflict", results)
        with self.app.app_context():
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 1)
            state = db.session.get(AccountAccessRequest, attempt).state
            self.assertEqual(PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").one().status, state)

    def test_concurrent_reapplications_create_one_open_attempt(self):
        _, target = self.prepare()
        with self.app.app_context():
            db.session.add(PortalAppAccess(user_id=target, app_code="neostaffing", role="watcher", status="denied", is_active=True, denial_notes="Keep prior decision"))
            db.session.commit()
        barrier = Barrier(2)
        def submit(_):
            barrier.wait(5)
            return self.request_attempt(target)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(isinstance(r, int) for r in results), 1, results)
        self.assertIn("AccountConflict", results)
        with self.app.app_context():
            self.assertEqual(AccountAccessRequest.query.filter_by(user_id=target, state="pending").count(), 1)
            row = PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").one()
            self.assertEqual((row.status, row.denial_notes), ("denied", "Keep prior decision"))

    def test_revoked_administrator_cannot_decide_a_previously_reviewed_request(self):
        actor, target = self.prepare()
        attempt = self.request_attempt(target)
        token = self.token(actor, target)
        with self.app.app_context():
            db.session.get(User, actor).role = "watcher"
            db.session.commit()
        self.assertEqual(self.decide(actor, target, token, "approved"), "ValueError")
        with self.app.app_context():
            self.assertEqual(db.session.get(AccountAccessRequest, attempt).state, "pending")
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 0)

    def test_failed_decision_rolls_back_entitlement_and_immutable_history(self):
        actor, target = self.prepare()
        attempt = self.request_attempt(target)
        token = self.token(actor, target)
        with patch("app.services.accounts_workspace.close_open_attempt", side_effect=ValueError("Synthetic failed audit")):
            self.assertEqual(self.decide(actor, target, token, "approved"), "ValueError")
        with self.app.app_context():
            self.assertIsNone(PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").first())
            self.assertEqual(db.session.get(AccountAccessRequest, attempt).state, "pending")
            self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt).count(), 0)

    def test_two_independent_profiles_cannot_overwrite_newer_grants(self):
        actor, target = self.prepare()
        self.request_attempt(target)
        token = self.token(actor, target)
        self.assertEqual(self.decide(actor, target, token, "approved"), "approved")
        self.assertEqual(self.decide(actor, target, token, "denied"), "AccountConflict")
        with self.app.app_context():
            self.assertEqual(PortalAppAccess.query.filter_by(user_id=target, app_code="neostaffing").one().status, "approved")
