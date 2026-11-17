"""Independent-transaction races on a new disposable loopback schema."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from uuid import uuid4
import unittest
from unittest.mock import patch

from flask import has_request_context, request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from app.services.user_tokens import EMAIL_VERIFICATION, PASSWORD_RESET, create_user_token, get_valid_token_record
from tests.test_account_security_phase1 import PASSWORD, fixture_user, identity_form


URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class AccountSecurityPhase1PostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(URI)
        if url.host not in {"localhost", "127.0.0.1"} or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing non-disposable database")
        cls.schema = "account_security_" + uuid4().hex
        cls.admin_engine = create_engine(URI)
        with cls.admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.app = create_app(type("Config", (), dict(
            SECRET_KEY="isolated-phase1-postgres", TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI=URI, SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args": {"options":
                f"-csearch_path={cls.schema} -clock_timeout=10000 -cstatement_timeout=15000"}},
        )))
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
        # Keep the synthetic schema as evidence; do not remove pre-existing
        # schemas, databases, directories, processes or other jobs.
        cls.admin_engine.dispose()

    def prepare(self, *, target_nodes=None, actor_nodes=None, platform="grandmaster"):
        with self.app.app_context():
            actor = fixture_user("a" + uuid4().hex[:12], platform=platform, nodes=actor_nodes)
            target = fixture_user("t" + uuid4().hex[:12], nodes=target_nodes)
            db.session.commit()
            actor_name, actor_id, target_id = actor.username, actor.id, target.id
        client = self.app.test_client()
        self.assertEqual(client.post("/login", data={"username": actor_name, "password": PASSWORD}).status_code, 302)
        return client, actor_id, target_id

    def test_email_correction_wins_against_waiting_recovery_and_verification(self):
        for kind in (PASSWORD_RESET, EMAIL_VERIFICATION):
            with self.subTest(kind=kind):
                client, _, target_id = self.prepare()
                with self.app.app_context():
                    target = db.session.get(User, target_id)
                    target.email_verified_at = None
                    raw, _ = create_user_token(target, kind)
                    db.session.commit()
                    waiting = Event()

                    def before(_c, _cu, statement, *_args):
                        if (has_request_context() and request.endpoint in {"auth.verify_email", "auth.reset_password"}
                                and "FROM users" in statement and "FOR UPDATE" in statement):
                            waiting.set()
                    event.listen(self.engine, "before_cursor_execute", before)
                    try:
                        # The correction owns the account first; the token
                        # request independently discovers its link then waits.
                        User.query.filter_by(id=target_id).with_for_update().one()
                        initial_hash = target.password_hash
                        form = identity_form(target, uuid4().hex + "@example.test")

                        def token_request():
                            other = self.app.test_client()
                            if kind == EMAIL_VERIFICATION:
                                return other.get("/verify-email/" + raw).status_code
                            return other.post("/reset-password/" + raw, data={
                                "password": "AChangedRecoveryPassword123!", "confirm_password": "AChangedRecoveryPassword123!",
                            }).status_code
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            future = pool.submit(token_request)
                            try:
                                self.assertTrue(waiting.wait(5))
                                response = client.post(f"/admin/users/{target_id}/edit", data=form)
                                self.assertEqual(response.status_code, 302)
                            finally:
                                db.session.rollback()
                            self.assertEqual(future.result(timeout=12), 400)
                    finally:
                        event.remove(self.engine, "before_cursor_execute", before)
                    db.session.expire_all()
                    self.assertEqual(target.password_hash, initial_hash)
                    self.assertIsNone(target.email_verified_at)
                    self.assertIsNone(get_valid_token_record(raw, kind))
                    self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target_id, action="email_changed").count(), 1)

    def test_old_address_token_issuance_waiting_on_correction_is_rejected(self):
        client, _, target_id = self.prepare()
        with self.app.app_context():
            target = db.session.get(User, target_id)
            old_email = target.email
            User.query.filter_by(id=target_id).with_for_update().one()
            waiting = Event()

            def before(_c, _cu, statement, *_args):
                if (has_request_context() and request.endpoint == "auth.forgot_password"
                        and "FROM users" in statement and "FOR UPDATE" in statement):
                    waiting.set()
            event.listen(self.engine, "before_cursor_execute", before)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: self.app.test_client().post("/forgot-password", data={"email": old_email}).status_code)
                    try:
                        self.assertTrue(waiting.wait(5))
                        response = client.post(f"/admin/users/{target_id}/edit", data=identity_form(target, uuid4().hex + "@example.test"))
                        self.assertEqual(response.status_code, 302)
                    finally:
                        db.session.rollback()
                    self.assertEqual(future.result(timeout=12), 200)
            finally:
                event.remove(self.engine, "before_cursor_execute", before)
            from app.models import UserToken
            self.assertEqual(UserToken.query.filter_by(user_id=target_id, used_at=None).count(), 0)

    def waiting_role_change(self, mutate, *, platform="grandmaster", actor_nodes=None):
        client, actor_id, target_id = self.prepare(platform=platform, actor_nodes=actor_nodes)
        waiting = Event()
        with self.app.app_context():
            node = NeoNode.query.filter_by(code="scorpion").one()
            node_id = node.id
            # Reuse the actual grant serialization row, in an independent
            # transaction which wins before the stale administrator request.
            NeoNode.query.filter_by(code="motherbrain").with_for_update().one()

            def before(_c, _cu, statement, *_args):
                if (has_request_context() and request.endpoint == "auth.user_roles"
                        and "FROM neo_nodes" in statement and "FOR UPDATE" in statement):
                    waiting.set()
            event.listen(self.engine, "before_cursor_execute", before)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: client.post(f"/admin/users/{target_id}/roles", data={f"node_{node_id}": "operator"}).status_code)
                    try:
                        self.assertTrue(waiting.wait(5))
                        mutate(actor_id, target_id, node_id)
                        db.session.commit()
                    finally:
                        db.session.rollback()
                    status = future.result(timeout=12)
            finally:
                event.remove(self.engine, "before_cursor_execute", before)
            member = GatewayMembership.query.filter_by(user_id=target_id).one()
            db.session.expire_all()
            grant = GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node_id).one()
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target_id).count(), 0)
            return status, grant.role

    def test_administrator_revocation_rechecked_after_lock_wait(self):
        def revoke(actor_id, *_):
            db.session.get(User, actor_id).is_active = False
        self.assertEqual(self.waiting_role_change(revoke), (403, "watcher"))

    def test_existing_target_promotion_is_rechecked_before_demoting(self):
        def promote(_actor_id, target_id, node_id):
            member = GatewayMembership.query.filter_by(user_id=target_id).one()
            GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node_id).one().role = "grandmaster"
        status, role = self.waiting_role_change(promote, platform="watcher", actor_nodes={"scorpion": "master"})
        self.assertEqual(status, 302)
        self.assertEqual(role, "grandmaster")

    def test_node_administrator_revocation_rechecked_after_lock_wait(self):
        def revoke(actor_id, _target_id, node_id):
            member = GatewayMembership.query.filter_by(user_id=actor_id).one()
            GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node_id).one().is_active = False
        self.assertEqual(self.waiting_role_change(revoke, platform="watcher",
            actor_nodes={"scorpion": "master"}), (403, "watcher"))

    def test_competing_removals_preserve_last_effective_node_administrator(self):
        first, _, first_id = self.prepare(target_nodes={"motherbrain": "grandmaster"})
        second, _, second_id = self.prepare(target_nodes={"motherbrain": "grandmaster"})
        with self.app.app_context():
            node_id = NeoNode.query.filter_by(code="motherbrain").one().id
        barrier = Barrier(2)

        def before(_c, _cu, statement, *_args):
            if (has_request_context() and request.endpoint == "auth.user_roles"
                    and "FROM neo_nodes" in statement and "FOR UPDATE" in statement):
                barrier.wait(timeout=8)
        event.listen(self.engine, "before_cursor_execute", before)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(client.post, f"/admin/users/{user_id}/roles", data={f"node_{node_id}": "master"})
                           for client, user_id in ((first, first_id), (second, second_id))]
                self.assertEqual([f.result(timeout=15).status_code for f in futures], [302, 302])
        finally:
            event.remove(self.engine, "before_cursor_execute", before)
        with self.app.app_context():
            roles = [GatewayNodeRole.query.join(GatewayMembership).filter(
                GatewayMembership.user_id == user_id, GatewayNodeRole.node_id == node_id,
            ).one().role for user_id in (first_id, second_id)]
            self.assertEqual(sorted(roles), ["grandmaster", "master"])

    def test_failed_email_change_rolls_back_tokens_and_audit_postgres(self):
        client, _, target_id = self.prepare()
        with self.app.app_context():
            target = db.session.get(User, target_id)
            reset, _ = create_user_token(target, PASSWORD_RESET)
            verify, _ = create_user_token(target, EMAIL_VERIFICATION)
            db.session.commit()
            previous = target.email, target.auth_session_version
            form = identity_form(target, uuid4().hex + "@example.test")
            form.update(is_management="1", management_level="invalid")
        self.assertEqual(client.post(f"/admin/users/{target_id}/edit", data=form).status_code, 400)
        with self.app.app_context():
            target = db.session.get(User, target_id)
            self.assertEqual((target.email, target.auth_session_version), previous)
            self.assertIsNotNone(get_valid_token_record(reset, PASSWORD_RESET))
            self.assertIsNotNone(get_valid_token_record(verify, EMAIL_VERIFICATION))
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target_id).count(), 0)

    def test_bootstrap_preserves_existing_security_on_postgres(self):
        from app.services.database_bootstrap import bootstrap_database
        from datetime import datetime, timedelta
        with self.app.app_context():
            user = fixture_user("bootstrap-" + uuid4().hex[:12], platform="watcher")
            user.email_verified_at = None
            user.password_reset_required = user.password_policy_update_required = True
            user.mfa_required = user.mfa_enabled = True
            user.mfa_secret = "isolated-existing-secret"
            user.mfa_verified_at = datetime(2026, 1, 1)
            user.temporary_password_expires_at = datetime.utcnow() + timedelta(hours=1)
            user.is_active = False
            db.session.commit()
            snapshot = {c.name: getattr(user, c.name) for c in User.__table__.columns}
            with patch.dict(os.environ, {
                "BOOTSTRAP_ADMIN_USERNAME": user.username,
                "BOOTSTRAP_ADMIN_EMAIL": uuid4().hex + "@example.test",
                "BOOTSTRAP_ADMIN_PASSWORD": "IgnoredExistingBootstrapPassword123!",
            }):
                result = bootstrap_database(self.app)
            db.session.expire_all()
            self.assertFalse(result["created_user"])
            self.assertFalse(result["password_applied"])
            self.assertEqual(snapshot, {c.name: getattr(user, c.name) for c in User.__table__.columns})
