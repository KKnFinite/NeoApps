"""Protected commissioning through real authenticated, CSRF-protected requests."""
from datetime import datetime
from html import unescape
import re
from unittest import TestCase
from unittest.mock import patch

from flask import g
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app import create_app
from app.extensions import db
from app.models import AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_security_history import ensure_account_security_history
from app.services.grandmaster_commissioning import scoped_grant_fingerprint
from app.services.grandmaster_commissioning import _signer
from app.models.neoscorpion_vendor import NeoScorpionVendorAccount
from app.services.permission_rules import ensure_default_permission_rules
from app.services.user_tokens import PASSWORD_RESET, EMAIL_VERIFICATION, create_user_token, get_valid_token_record
from tests.test_account_security_phase1 import fixture_user, PASSWORD, identity_form


def field(response, name):
    match = re.search(r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', response.get_data(as_text=True))
    if not match:
        raise AssertionError(f"Missing form field {name}: HTTP {response.status_code}")
    return unescape(match.group(1))


class GrandmasterCommissioningTest(TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), dict(SECRET_KEY="commissioning-fixture-secret",
            TESTING=True, CSRF_PROTECT_TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            AUTH_RATE_LIMIT_ENABLED=False, SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False)))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_account_security_history(db.session.connection())
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.owner = fixture_user("commission-owner", platform="grandmaster")
        self.target = fixture_user("commission-target", nodes={"motherbrain": "master", "rain": "master"})
        db.session.commit()
        self.client = self.app.test_client()
        self.login(self.client, self.owner)

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, client, user):
        g.pop("_login_user", None)
        page = client.get("/login")
        if page.status_code == 302:
            page = client.get("/portal", follow_redirects=True)
            client.post("/logout", data={"csrf_token":field(page,"csrf_token")})
            g.pop("_login_user", None)
            page = client.get("/login")
        self.assertEqual(client.post("/login", data={"username": user.username, "password": PASSWORD,
            "csrf_token": field(page, "csrf_token")}).status_code, 302)

    @property
    def url(self):
        return f"/admin/users/{self.target.id}/commission-grandmaster"

    def form(self):
        g.pop("_login_user", None)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        return dict(csrf_token=field(page,"csrf_token"), review_token=field(page,"review_token"),
            current_password=PASSWORD, confirmed_target_id=str(self.target.id),
            confirm_platform_authority="yes", reason="Commission verified backup administrator")

    def unchanged(self):
        db.session.expire_all()
        self.assertEqual(self.target.role, "watcher")
        self.assertEqual(AccountSecurityEvent.query.filter_by(action="platform_grandmaster_commissioned").count(), 0)

    def test_success_preserves_grants_security_fields_invalidates_sessions_tokens_and_audits(self):
        other = self.app.test_client()
        self.login(other, self.target)
        raw_reset, _ = create_user_token(self.target, PASSWORD_RESET)
        raw_verify, _ = create_user_token(self.target, EMAIL_VERIFICATION)
        db.session.commit()
        before = {c.name:getattr(self.target,c.name) for c in User.__table__.columns
                  if c.name not in {"role","auth_session_version","last_login"}}
        grants = scoped_grant_fingerprint(self.target.id)
        version = self.target.auth_session_version
        form = self.form()
        response = self.client.post(self.url, data=form)
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self.target.role, "grandmaster")
        self.assertEqual(self.target.auth_session_version, version+1)
        self.assertEqual(scoped_grant_fingerprint(self.target.id), grants)
        for key,value in before.items():
            self.assertEqual(getattr(self.target,key),value,key)
        self.assertIsNone(get_valid_token_record(raw_reset,PASSWORD_RESET))
        self.assertIsNone(get_valid_token_record(raw_verify,EMAIL_VERIFICATION))
        g.pop("_login_user", None)
        self.assertIn("/login", other.get("/portal").location)
        audit = AccountSecurityEvent.query.filter_by(action="platform_grandmaster_commissioned").one()
        self.assertEqual((audit.actor_user_id,audit.subject_user_id),(self.owner.id,self.target.id))
        self.assertEqual(audit.details["scope"],"neoapps")
        self.assertEqual(audit.details["before"]["role"],"watcher")
        self.assertEqual(audit.details["after"]["role"],"grandmaster")
        self.assertEqual(audit.details["scoped_grants_before"],audit.details["scoped_grants_after"])
        self.assertNotIn(PASSWORD,str(audit.details))
        self.assertNotIn(form["review_token"],str(audit.details))

    def test_get_is_read_only_and_separate_from_scoped_role_editor(self):
        statements=[]
        def observe(_c,_cu,statement,*_args):
            if statement.lstrip().split()[0].upper() in {"INSERT","UPDATE","DELETE"}:
                statements.append(statement)
        event.listen(db.engine,"before_cursor_execute",observe)
        try:
            page=self.client.get(self.url)
        finally:
            event.remove(db.engine,"before_cursor_execute",observe)
        self.assertEqual(page.status_code,200)
        self.assertEqual(statements,[])
        self.assertIn(b"platform-wide",page.data)
        self.assertIn(f"Type {self.target.id} to confirm {self.target.display_name}".encode(),page.data)
        self.assertIn(b'name="current_password"',page.data)
        self.assertNotIn(b'name="node_',page.data)
        self.assertNotIn(b'name="app_role_',page.data)
        detail=self.client.get(f"/admin/users/{self.target.id}")
        self.assertIn(self.url.encode(),detail.data)
        self.assertIn(b"NEOAPPS PLATFORM ROLE",detail.data)
        self.unchanged()

    def test_wrong_password_draft_not_echoed_and_no_change(self):
        form=self.form()
        form["current_password"]="WrongSecretNeverEcho!"
        response=self.client.post(self.url,data=form)
        self.assertEqual(response.status_code,400)
        self.assertNotIn(b"WrongSecretNeverEcho",response.data)
        self.unchanged()

    def test_csrf_confirmation_reason_and_forged_target_rejected(self):
        form=self.form()
        for changes,expected in (({"csrf_token":"invalid"},400),({"confirm_platform_authority":""},403),
            ({"confirmed_target_id":"99999"},403),({"reason":""},403),({"review_token":"forged"},409)):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post(self.url,data={**form,**changes}).status_code,expected)
                self.unchanged()
        third=fixture_user("other-target")
        db.session.commit()
        response=self.client.post(f"/admin/users/{third.id}/commission-grandmaster",
            data={**form,"confirmed_target_id":str(third.id)})
        self.assertEqual(response.status_code,409)
        self.assertEqual(third.role,"watcher")

    def test_old_review_expires_and_cannot_cross_actor(self):
        form=self.form()
        payload=_signer().loads(form["review_token"])
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp",return_value=1_000_000):
            form["review_token"]=_signer().dumps(payload)
        self.assertEqual(self.client.post(self.url,data=form).status_code,409)
        form=self.form()
        peer=fixture_user("commission-peer",platform="grandmaster")
        db.session.commit()
        self.login(self.client,peer)
        csrf=field(self.client.get(self.url),"csrf_token")
        self.assertEqual(self.client.post(self.url,data={**form,"csrf_token":csrf}).status_code,409)
        self.unchanged()

    def test_duplicate_self_and_existing_grandmaster_rejected(self):
        form=self.form()
        self.assertEqual(self.client.post(self.url,data=form).status_code,302)
        self.assertEqual(self.client.post(self.url,data=form).status_code,409)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action="platform_grandmaster_commissioned").count(),1)
        self.assertEqual(self.client.get(f"/admin/users/{self.owner.id}/commission-grandmaster").status_code,409)

    def test_scoped_grandmaster_and_platform_master_do_not_authorize(self):
        for role in ("watcher","master"):
            actor=fixture_user("scoped-"+role,platform=role,nodes={"motherbrain":"grandmaster","scorpion":"grandmaster"})
            db.session.commit()
            self.login(self.client,actor)
            self.assertEqual(self.client.get(self.url).status_code,403)
            csrf=field(self.client.get("/portal"),"csrf_token")
            self.assertEqual(self.client.post(self.url,data={"csrf_token":csrf}).status_code,403)
            self.unchanged()

    def test_stale_identity_grants_and_actor_revocation_rejected(self):
        form=self.form()
        self.target.first_name="Updated identity"
        db.session.commit()
        self.assertEqual(self.client.post(self.url,data=form).status_code,409)
        self.unchanged()
        form=self.form()
        membership=GatewayMembership.query.filter_by(user_id=self.target.id).one()
        grant=GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).first()
        grant.is_active=False
        db.session.commit()
        self.assertEqual(self.client.post(self.url,data=form).status_code,409)
        self.unchanged()
        form=self.form()
        self.owner.role="watcher" # controlled revocation fixture, not a production operation
        db.session.commit()
        self.assertEqual(self.client.post(self.url,data=form).status_code,403)
        self.unchanged()

    def test_inactive_unverified_restricted_recovery_and_mfa_states_fail_closed(self):
        for name,value in (("is_active",False),("email_verified_at",None),("password_reset_required",True),
                           ("password_policy_update_required",True),("mfa_required",True),("mfa_enabled",True)):
            with self.subTest(state=name):
                form=self.form()
                prior=getattr(self.target,name)
                setattr(self.target,name,value)
                db.session.commit()
                self.assertEqual(self.client.post(self.url,data=form).status_code,403)
                self.unchanged()
                setattr(self.target,name,prior)
                db.session.commit()
        form=self.form()
        membership=GatewayMembership.query.filter_by(user_id=self.target.id).one()
        db.session.add(NeoScorpionVendorAccount(user_id=self.target.id,gateway_id=membership.gateway_id,
            invited_by_user_id=self.owner.id))
        db.session.commit()
        self.assertEqual(self.client.post(self.url,data=form).status_code,403)
        self.unchanged()
        self.owner.mfa_required=True
        db.session.commit()
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_persistence_failure_rolls_back_role_version_tokens_and_audit(self):
        raw,_=create_user_token(self.target,PASSWORD_RESET)
        db.session.commit()
        version=self.target.auth_session_version
        form=self.form()
        def fail(*_args):
            raise SQLAlchemyError("Injected audit failure")
        event.listen(AccountSecurityEvent,"before_insert",fail)
        try:
            self.assertEqual(self.client.post(self.url,data=form).status_code,503)
        finally:
            event.remove(AccountSecurityEvent,"before_insert",fail)
        self.unchanged()
        self.assertEqual(self.target.auth_session_version,version)
        self.assertIsNotNone(get_valid_token_record(raw,PASSWORD_RESET))

    def test_history_bulk_update_delete_blocked(self):
        self.client.post(self.url,data=self.form())
        for statement in ("UPDATE account_security_events SET action='tampered'","DELETE FROM account_security_events"):
            with self.assertRaises(DBAPIError):
                db.session.execute(text(statement))
            db.session.rollback()
        self.assertEqual(AccountSecurityEvent.query.filter_by(action="platform_grandmaster_commissioned").count(),1)

    def test_reauthentication_rate_limit_is_enforced(self):
        self.app.config.update(AUTH_RATE_LIMIT_ENABLED=True,AUTH_LOGIN_IP_MAX_FAILURES=1,AUTH_LOGIN_IDENTIFIER_MAX_FAILURES=1)
        form=self.form()
        form["current_password"]="invalid"
        self.assertEqual(self.client.post(self.url,data=form).status_code,400)
        self.assertEqual(self.client.post(self.url,data=form).status_code,429)
        self.unchanged()

    def test_last_recoverable_platform_admin_email_cannot_be_unverified(self):
        page=self.client.get(f"/admin/users/{self.owner.id}/edit")
        form=identity_form(self.owner,"replacement@example.test")
        form["csrf_token"]=field(page,"csrf_token") if b'name="csrf_token"' in page.data else self.form()["csrf_token"]
        response=self.client.post(f"/admin/users/{self.owner.id}/edit",data=form)
        self.assertEqual(response.status_code,400)
        db.session.expire_all()
        self.assertEqual(self.owner.email,"commission-owner@example.test")
        self.assertIsNotNone(self.owner.email_verified_at)
