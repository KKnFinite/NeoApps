"""Independent HTTP transactions for protected platform commissioning."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
import os
import unittest

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError, DBAPIError

from app import create_app
from app.extensions import db
from app.models import AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_security_history import ensure_account_security_history
from app.services.permission_rules import ensure_default_permission_rules
from app.services.grandmaster_commissioning import scoped_grant_fingerprint
from app.services.user_tokens import PASSWORD_RESET, create_user_token, get_valid_token_record
from tests.test_account_security_phase1 import fixture_user, PASSWORD, identity_form
from tests.test_grandmaster_commissioning import field


URI=os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI,"Requires disposable loopback PostgreSQL")
class GrandmasterCommissioningPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url=make_url(URI)
        if url.host not in {"127.0.0.1","localhost"} or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing non-disposable database")
        cls.schema="commissioning_"+uuid4().hex
        cls.admin_engine=create_engine(URI)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.app=create_app(type("Config",(),dict(SECRET_KEY="commissioning-pg-test",TESTING=True,
            CSRF_PROTECT_TESTING=True,AUTH_RATE_LIMIT_ENABLED=False,AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI=URI,SQLALCHEMY_TRACK_MODIFICATIONS=False,
            SQLALCHEMY_ENGINE_OPTIONS={"connect_args":{"options":
                f"-csearch_path={cls.schema} -clock_timeout=10000 -cstatement_timeout=15000"}})))
        with cls.app.app_context():
            db.create_all()
            ensure_account_security_history(db.session.connection())
            ensure_default_gateway_and_nodes()
            ensure_default_permission_rules()
            db.session.commit()
            cls.engine=db.engine

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        cls.admin_engine.dispose() # Preserve synthetic schema/evidence.

    def prepare(self):
        with self.app.app_context():
            actor=fixture_user("a"+uuid4().hex[:12],platform="grandmaster")
            target=fixture_user("t"+uuid4().hex[:12])
            db.session.commit()
            return actor.id,target.id,actor.username

    def client(self,username):
        client=self.app.test_client()
        page=client.get("/login")
        self.assertEqual(client.post("/login",data={"username":username,"password":PASSWORD,
            "csrf_token":field(page,"csrf_token")}).status_code,302)
        return client

    def form(self,client,target):
        page=client.get(f"/admin/users/{target}/commission-grandmaster")
        self.assertEqual(page.status_code,200)
        return dict(csrf_token=field(page,"csrf_token"),review_token=field(page,"review_token"),
            current_password=PASSWORD,confirmed_target_id=str(target),confirm_platform_authority="yes",
            reason="Verified synthetic backup administrator")

    def test_two_commissions_one_role_version_and_one_audit(self):
        actor,target,username=self.prepare()
        clients=[self.client(username),self.client(username)]
        forms=[self.form(client,target) for client in clients]
        start=Barrier(2)
        def submit(index):
            start.wait(5)
            return clients[index].post(f"/admin/users/{target}/commission-grandmaster",data=forms[index]).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(submit,range(2)))
        self.assertEqual(sorted(statuses),[302,409])
        with self.app.app_context():
            user=db.session.get(User,target)
            self.assertEqual((user.role,user.auth_session_version),("grandmaster",2))
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action="platform_grandmaster_commissioned").count(),1)

    def wait_race(self,mutation,expected):
        actor,target,username=self.prepare()
        client=self.client(username)
        form=self.form(client,target)
        waiting=Event()
        def before(_c,_cu,statement,*_args):
            if "neo_nodes" in statement and "FOR UPDATE" in statement:
                waiting.set()
        with self.app.app_context():
            NeoNode.query.filter_by(code="motherbrain").with_for_update().one()
            event.listen(self.engine,"before_cursor_execute",before)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(lambda:client.post(f"/admin/users/{target}/commission-grandmaster",data=form).status_code)
                    self.assertTrue(waiting.wait(5))
                    mutation(actor,target)
                    db.session.commit()
                    self.assertEqual(future.result(timeout=12),expected)
            finally:
                db.session.rollback()
                event.remove(self.engine,"before_cursor_execute",before)
            self.assertEqual(db.session.get(User,target).role,"watcher")
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action="platform_grandmaster_commissioned").count(),0)

    def test_actor_revocation_after_wait_is_rechecked(self):
        def revoke(actor,_target):
            user=db.session.get(User,actor)
            user.role="watcher"
            user.auth_session_version+=1
        self.wait_race(revoke,403)

    def test_changed_target_scoped_grant_after_wait_conflicts(self):
        def change(_actor,target):
            membership=GatewayMembership.query.filter_by(user_id=target).one()
            row=GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).first()
            row.role="simulator"
        self.wait_race(change,409)

    def test_audit_failure_rolls_back_promotion_session_version_and_tokens(self):
        _actor,target,username=self.prepare()
        client=self.client(username)
        with self.app.app_context():
            user=db.session.get(User,target)
            raw,_=create_user_token(user,PASSWORD_RESET)
            db.session.commit()
            grants=scoped_grant_fingerprint(target)
        form=self.form(client,target)
        def fail(*_args):
            raise SQLAlchemyError("Injected commissioning audit failure")
        event.listen(AccountSecurityEvent,"before_insert",fail)
        try:
            self.assertEqual(client.post(f"/admin/users/{target}/commission-grandmaster",data=form).status_code,503)
        finally:
            event.remove(AccountSecurityEvent,"before_insert",fail)
        with self.app.app_context():
            user=db.session.get(User,target)
            self.assertEqual((user.role,user.auth_session_version),("watcher",1))
            self.assertIsNotNone(get_valid_token_record(raw,PASSWORD_RESET))
            self.assertEqual(scoped_grant_fingerprint(target),grants)
            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target).count(),0)

    def test_concurrent_email_corrections_preserve_last_recoverable_admin(self):
        a,b,username=self.prepare()
        with self.app.app_context():
            # Controlled setup of two existing platform administrators.
            db.session.get(User,b).role="grandmaster"
            db.session.commit()
            names=[username,db.session.get(User,b).username]
            identities=[identity_form(db.session.get(User,id_),uuid4().hex+"@example.test") for id_ in (a,b)]
            # Scope this preservation assertion to the shared serialized global
            # population: earlier tests also created eligible administrators.
            for user in User.query.filter(User.role=="grandmaster",User.id.notin_([a,b])).all():
                user.is_active=False
            db.session.commit()
        clients=[self.client(name) for name in names]
        for client,form,id_ in zip(clients,identities,(a,b)):
            form["csrf_token"]=field(client.get(f"/admin/users/{id_}/edit"),"csrf_token")
        start=Barrier(2)
        def correct(index):
            start.wait(5)
            return clients[index].post(f"/admin/users/{(a,b)[index]}/edit",data=identities[index]).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(correct,range(2)))
        self.assertEqual(sorted(statuses),[302,400])
        with self.app.app_context():
            self.assertEqual(User.query.filter(User.id.in_([a,b]),User.email_verified_at.is_not(None)).count(),1)

    def test_audit_is_immutable_through_bulk_sql_and_truncate(self):
        _actor,target,username=self.prepare()
        client=self.client(username)
        self.assertEqual(client.post(f"/admin/users/{target}/commission-grandmaster",data=self.form(client,target)).status_code,302)
        with self.app.app_context():
            for sql in ("UPDATE account_security_events SET action='invalid'","DELETE FROM account_security_events", "TRUNCATE account_security_events"):
                with self.subTest(sql=sql),self.assertRaises(DBAPIError):
                    db.session.execute(text(sql))
                db.session.rollback()
