"""Phase 3 HTTP contracts use current permissions, never staged roles."""
from datetime import datetime
from html.parser import HTMLParser
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountScopedRole, AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.models.account_preference import AccountPreference
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_foundation import FoundationNotActivated, fulfill_access_request
from app.services.account_foundation_schema import ensure_account_history_guards
from app.services.permission_rules import ensure_default_permission_rules
from app.services.user_tokens import create_user_token, EMAIL_VERIFICATION, PASSWORD_RESET, get_valid_token_record
from tests.test_account_security_phase1 import fixture_user, PASSWORD, identity_form


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html.decode() if isinstance(html, bytes) else html)

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == "input" and row.get("name"):
            self.values[row["name"]] = row.get("value", "")


class AccountsWorkspaceTest(TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), dict(
            SECRET_KEY="accounts-workspace-fixture", TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AUTH_RATE_LIMIT_ENABLED=False,
        )))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_account_history_guards(db.session.connection())
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.admin = fixture_user("workspace-owner", platform="grandmaster")
        self.target = fixture_user("workspace-target")
        self.backup = fixture_user("workspace-backup", platform="grandmaster")
        db.session.commit()
        self.client = self.app.test_client()
        self.mail = patch("app.auth.routes.email_service.send_access_approved", return_value={"sent": False})
        self.mail.start()
        self.addCleanup(self.mail.stop)

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, user):
        db.session.commit()
        g.pop("_login_user", None)
        self.client.post("/logout")
        g.pop("_login_user", None)
        result = self.client.post("/login", data={"email": user.email, "password": PASSWORD})
        self.assertEqual(result.status_code, 302)
        return result

    def review(self, user=None, tab="identity"):
        target = user or self.target
        result = self.client.get(f"/accounts/people/{target.id}?tab={tab}")
        self.assertEqual(result.status_code, 200)
        return Inputs(result.data).values["review_token"]

    def action(self, action, data, user=None, review=None):
        target = user or self.target
        token = review or self.review(target, "access" if action != "identity" else "identity")
        return self.client.post(f"/accounts/people/{target.id}/{action}", data={"review_token": token, **data})

    def my_request(self, code="neostaffing", reason="Need staffing access"):
        values = Inputs(self.client.get("/my-account").data).values
        return self.client.post("/my-account/app-access", data={"review_token": values["review_token"],
            "request_key": values.get("request_key", str(uuid4())), "app_code": code, "reason": reason})

    def test_people_directory_filters_pagination_and_legacy_links(self):
        for index in range(27):
            db.session.add(User(username=f"page-{index}", password_hash="unusable", role="watcher", is_active=True))
        self.login(self.admin)
        result = self.client.get("/accounts")
        self.assertEqual(result.status_code, 200)
        self.assertIn(b"Page 1 of 2", result.data)
        self.assertEqual(result.data.count(b'class="aa-person"'), 25)
        self.assertIn(b"workspace-target", self.client.get("/accounts?q=workspace-target").data)
        self.assertNotIn(b"workspace-backup@example.test", self.client.get("/accounts?q=workspace-target").data)
        for path in ["/admin/users", "/admin/users/all", "/portal/manage/users/edit-users", "/portal/manage"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue("/accounts" in response.location)

    def test_read_only_profiles_preserve_missing_inherited_roles_and_write_nothing(self):
        access = PortalAppAccess.query.filter_by(user_id=self.target.id, app_code="neogateway").one()
        access.role = "simulator"
        rows = GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id == self.target.id).all()
        for row in rows:
            db.session.delete(row)
        self.login(self.admin)
        statements = []
        def record(_conn, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", record)
        try:
            for path in ["/accounts", "/accounts/requests", "/accounts/security", f"/accounts/people/{self.target.id}?tab=access", "/my-account"]:
                self.assertEqual(self.client.get(path).status_code, 200)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        self.assertEqual(statements, [])
        self.assertEqual(GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id == self.target.id).count(), 0)
        self.assertEqual(AccountScopedRole.query.count(), 0)
        with self.assertRaises(FoundationNotActivated):
            fulfill_access_request(1)

    def test_identity_save_is_independent_and_stale_draft_preserved(self):
        self.login(self.admin)
        token = self.review()
        self.target.work_area = "Concurrent change"
        db.session.commit()
        response = self.action("identity", {**identity_form(self.target, self.target.email), "work_area": "Unsaved proposed area"}, review=token)
        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Unsaved proposed area", response.data)
        self.assertEqual(self.target.work_area, "Concurrent change")
        prior = [(a.id, a.status, a.role) for a in PortalAppAccess.query.filter_by(user_id=self.target.id)]
        response = self.action("identity", {**identity_form(self.target, self.target.email), "first_name": "Updated", "app_status_neogateway": "denied", "node_1": "disabled"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.target.first_name, "Updated")
        self.assertEqual([(a.id, a.status, a.role) for a in PortalAppAccess.query.filter_by(user_id=self.target.id)], prior)

    def test_email_change_invalidates_recovery_tokens_and_preserves_credentials(self):
        reset, _ = create_user_token(self.target, PASSWORD_RESET)
        verify, _ = create_user_token(self.target, EMAIL_VERIFICATION)
        before = self.target.password_hash
        self.login(self.admin)
        response = self.action("identity", identity_form(self.target, "corrected@example.test"))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.target.email_verified_at)
        self.assertIsNone(get_valid_token_record(reset, PASSWORD_RESET))
        self.assertIsNone(get_valid_token_record(verify, EMAIL_VERIFICATION))
        self.assertEqual(self.target.password_hash, before)
        self.assertEqual(self.target.auth_session_version, 2)

    def test_reapplication_preserves_denial_and_each_immutable_decision(self):
        db.session.add(PortalAppAccess(user_id=self.target.id, app_code="neostaffing", role="watcher", status="denied", is_active=True, denial_notes="Original denial"))
        self.login(self.target)
        self.assertEqual(self.my_request().status_code, 302)
        access = PortalAppAccess.query.filter_by(user_id=self.target.id, app_code="neostaffing").one()
        self.assertEqual(access.status, "denied")
        self.assertEqual(access.denial_notes, "Original denial")
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=self.target.id,action="access_request_created").one().details["previous_legacy_decision_observed"]["denial_notes"], "Original denial")
        self.assertEqual(self.my_request().status_code, 409)
        self.login(self.admin)
        self.assertIn(b"workspace-target", self.client.get("/accounts/requests").data)
        result = self.action("application", dict(app_code="neostaffing", outcome="denied", reason="Needs more justification"))
        self.assertEqual(result.status_code, 302)
        first = AccountAccessRequest.query.filter_by(user_id=self.target.id).one()
        self.assertEqual(first.state, "denied")
        self.assertEqual(AccountAccessDecision.query.filter_by(request_id=first.id).count(), 1)
        self.login(self.target)
        self.assertEqual(self.my_request(reason="Additional approved staffing duties").status_code, 302)
        self.login(self.admin)
        token = self.review(tab="access")
        result = self.action("application", dict(app_code="neostaffing", outcome="approved", role="operator", reason="Verified staffing responsibilities"), review=token)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(access.status, "approved")
        self.assertEqual(access.role, "operator")
        self.assertEqual(AccountAccessDecision.query.count(), 2)
        self.assertEqual(self.action("application", dict(app_code="neostaffing", outcome="denied", reason="Stale retry"), review=token).status_code, 409)
        self.assertEqual(AccountAccessDecision.query.count(), 2)

    def test_preferences_default_off_authorized_only_and_cryocheck_not_an_app(self):
        db.session.add(PortalAppAccess(user_id=self.target.id, app_code="neostaffing", status="approved", role="watcher", is_active=True))
        self.assertEqual(self.login(self.target).location, "/portal")
        self.assertIsNone(db.session.get(AccountPreference, self.target.id))
        values = Inputs(self.client.get("/my-account").data).values
        response = self.client.post("/my-account/preferences", data={"review_token": values["review_token"], "preferred_app": "neostaffing", "show_cryocheck": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertIn(b"https://cryocheck.onrender.com", self.client.get("/portal").data)
        self.assertEqual(self.login(self.target).location, "/neostaffing")
        access = PortalAppAccess.query.filter_by(user_id=self.target.id, app_code="neostaffing").one()
        access.status = "denied"
        db.session.commit()
        self.assertEqual(self.login(self.target).location, "/rfd")
        self.assertNotIn(b'data-portal-app="neostaffing"', self.client.get("/portal").data)
        values = Inputs(self.client.get("/my-account").data).values
        self.assertEqual(self.client.post("/my-account/preferences", data={"review_token": values["review_token"], "preferred_app": "neobid"}).status_code, 400)

    def test_neobid_not_requestable_or_approvable_and_grants_unchanged(self):
        access = PortalAppAccess(user_id=self.target.id, app_code="neobid", status="approved", role="master", is_active=True)
        db.session.add(access)
        self.login(self.target)
        self.assertNotIn(b'value="neobid"', self.client.get("/my-account").data)
        self.assertEqual(self.my_request("neobid").status_code, 400)
        self.login(self.admin)
        self.assertEqual(self.action("application", dict(app_code="neobid", outcome="approved", role="watcher", reason="Forged launch attempt")).status_code, 400)
        self.client.post(f"/portal/manage/app-access/{access.id}/update", data={"action": "approve", "role": "watcher"})
        self.assertEqual(access.role, "master")
        self.assertTrue(access.is_active)

    def test_node_save_changes_only_explicit_selected_scope(self):
        self.login(self.admin)
        nodes = GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id == self.target.id).all()
        before = {r.id: (r.role, r.is_active) for r in nodes}
        selected = next(r for r in nodes if r.node.code == "scorpion")
        response = self.action("node", dict(node_id=selected.node_id, role="operator", app_code="neostaffing"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(selected.role, "operator")
        self.assertEqual({r.id: (r.role, r.is_active) for r in nodes if r.id != selected.id}, {key: value for key,value in before.items() if key != selected.id})

    def test_scope_only_admin_and_revoked_actor_cannot_use_workspace_mutations(self):
        scoped = fixture_user("scope-only", nodes={"motherbrain": "grandmaster"})
        self.login(scoped)
        self.assertEqual(self.client.get("/accounts/security").status_code, 403)
        self.assertEqual(self.client.post(f"/accounts/people/{self.target.id}/identity", data=identity_form(self.target, self.target.email)).status_code, 403)
        self.login(self.admin)
        token = self.review()
        self.admin.role = "watcher"
        db.session.commit()
        self.assertEqual(self.action("identity", identity_form(self.target, self.target.email), review=token).status_code, 403)

    def test_driver_my_account_is_own_read_only_and_restricted(self):
        from app.models import NeoScorpionVendorAccount
        membership = GatewayMembership.query.filter_by(user_id=self.target.id).one()
        db.session.add(NeoScorpionVendorAccount(user_id=self.target.id, gateway_id=membership.gateway_id, enabled=True, invited_by_user_id=self.admin.id))
        self.login(self.target)
        self.assertEqual(self.client.get("/my-account").status_code, 200)
        for path in ["/accounts", "/accounts/security", "/portal", "/my-account/preferences", "/my-account/app-access"]:
            with self.subTest(path=path):
                result = self.client.post(path) if path.startswith("/my-account/") else self.client.get(path)
                self.assertEqual(result.status_code, 403)
        self.assertNotIn(b"Preferred application", self.client.get("/my-account").data)

    def test_failed_transaction_rolls_back_identity_and_audit(self):
        self.login(self.admin)
        token = self.review()
        before = self.target.first_name
        count = AccountSecurityEvent.query.count()
        with patch("app.auth.accounts.record_security_change", side_effect=ValueError("Synthetic rollback")):
            response = self.action("identity", {**identity_form(self.target, self.target.email), "first_name": "Should rollback"}, review=token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.target.first_name, before)
        self.assertEqual(AccountSecurityEvent.query.count(), count)

    def test_registration_multiple_apps_no_roles_and_atomic_history(self):
        form = dict(first_name="New", last_name="Applicant", email="new-applicant@example.test", employee_id="1234567",
            supervisor_name="Synthetic Supervisor", work_area="Ramp", access_reason="Operational staffing duties",
            app_codes=["neogateway", "neostaffing"], password=PASSWORD, confirm_password=PASSWORD, role="grandmaster")
        with patch("app.auth.routes.email_service.send_email_verification", return_value={"sent": False}):
            response = self.client.post("/create-account", data=form)
        self.assertEqual(response.status_code, 200)
        user = User.query.filter_by(email=form["email"]).one()
        self.assertEqual(user.role, "watcher")
        self.assertIsNone(user.email_verified_at)
        self.assertEqual(AccountAccessRequest.query.filter_by(user_id=user.id, state="pending").count(), 2)
        self.assertEqual(PortalAppAccess.query.filter_by(user_id=user.id, status="pending").count(), 2)
        bad = {**form, "email": "forged-neobid@example.test", "employee_id": "1234568", "app_codes": ["neogateway", "neobid"]}
        self.assertEqual(self.client.post("/create-account", data=bad).status_code, 400)
        self.assertIsNone(User.query.filter_by(email=bad["email"]).first())

    def test_csrf_cannot_be_bypassed_on_new_actions(self):
        self.login(self.admin)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        response = self.client.post(f"/accounts/people/{self.target.id}/identity", data=identity_form(self.target, self.target.email))
        self.assertEqual(response.status_code, 400)

    def test_workspace_policy_oversight_retains_platform_boundaries_and_legacy_node_access(self):
        from app.models import PermissionRule
        self.login(self.admin)
        page = self.client.get("/accounts/security/permissions")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Permission oversight", page.data)
        rule = PermissionRule.query.filter_by(permission_key="neoapps.user_management.edit").one()
        before = rule.minimum_role
        self.client.post("/accounts/security/permissions", data={"rule_ids": str(rule.id),
            f"minimum_role_{rule.id}": "watcher", f"description_{rule.id}": rule.description})
        self.assertEqual(rule.minimum_role, before)
        scoped = fixture_user("policy-scope-only", nodes={"motherbrain": "grandmaster"})
        self.login(scoped)
        self.assertEqual(self.client.get("/accounts/security/permissions").status_code, 403)
        self.assertEqual(self.client.get("/motherbrain/permissions").status_code, 200)
