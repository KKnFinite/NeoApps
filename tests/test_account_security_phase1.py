"""Focused HTTP and transaction regressions for the four Phase 1 findings."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from flask import g

from app import create_app
from app.extensions import db
from app.models import AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, PortalAppAccess, User
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes, user_can_access_node
from app.services.database_bootstrap import bootstrap_database
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.services.user_tokens import EMAIL_VERIFICATION, PASSWORD_RESET, create_user_token, get_valid_token_record


PASSWORD = "FocusedSecurityFixture123!"


def fixture_user(name, platform="watcher", nodes=None):
    user = User(username=name, email=name + "@example.test", first_name=name,
                last_name="Fixture", employee_id="SEC-" + name, role=platform,
                email_verified_at=datetime.utcnow(), is_active=True)
    set_user_password(user, PASSWORD)
    db.session.add(user)
    db.session.flush()
    membership = backfill_default_gateway_node_roles(user, role="watcher")
    for node_code, role in (nodes or {}).items():
        node = NeoNode.query.filter_by(code=node_code).one()
        grant = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one()
        grant.role = role
    return user


def identity_form(user, email):
    return dict(first_name=user.first_name, last_name=user.last_name, email=email,
                employee_id=user.employee_id, supervisor_name="", work_area="", access_reason="")


class AccountSecurityPhase1Test(TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), dict(
            SECRET_KEY="isolated-phase1", TESTING=True, AUTO_BOOTSTRAP_DATABASE=False,
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:", SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, user):
        db.session.commit()
        g.pop("_login_user", None)
        response = self.client.post("/login", data=dict(username=user.username, password=PASSWORD))
        self.assertEqual(response.status_code, 302)

    def policy_post(self, *entries):
        form = {"rule_ids": [str(rule.id) for rule, _ in entries]}
        for rule, role in entries:
            form[f"minimum_role_{rule.id}"] = role
            form[f"description_{rule.id}"] = rule.description
        return self.client.post("/motherbrain/permissions", data=form)

    def test_node_admin_cannot_change_platform_or_foreign_policy_atomic(self):
        actor = fixture_user("motherbrain-only", nodes={"motherbrain": "grandmaster"})
        self.login(actor)
        local = PermissionRule.query.filter_by(permission_key="neomotherbrain.manage_sort.edit").one()
        foreign = PermissionRule.query.filter_by(permission_key="neoscorpion.assignments.edit").first()
        # Use an actually registered Scorpion action without coupling to a label.
        if foreign is None:
            foreign = PermissionRule.query.filter(PermissionRule.permission_key.like("neoscorpion.%.edit")).first()
        platform = PermissionRule.query.filter_by(permission_key="neoapps.user_management.edit").one()
        page = self.client.get("/motherbrain/permissions")
        self.assertNotIn(f'name="minimum_role_{platform.id}"'.encode(), page.data)
        self.assertNotIn(f'name="minimum_role_{foreign.id}"'.encode(), page.data)
        original = local.minimum_role
        for forbidden in (platform, foreign):
            with self.subTest(permission=forbidden.permission_key):
                self.policy_post((local, "master"), (forbidden, "watcher"))
                db.session.expire_all()
                self.assertEqual(local.minimum_role, original)
                self.assertNotEqual(forbidden.minimum_role, "watcher")
                self.assertEqual(AccountSecurityEvent.query.count(), 0)
        self.policy_post((local, "master"))
        self.assertEqual(local.minimum_role, "master")

    def test_permissive_legacy_platform_threshold_cannot_grant_authority(self):
        actor = fixture_user("legacy-threshold", nodes={"motherbrain": "grandmaster"})
        PermissionRule.query.filter_by(permission_key="neoapps.user_management.edit").one().minimum_role = "watcher"
        db.session.commit()
        with self.app.test_request_context():
            self.assertFalse(user_can("neoapps.user_management.edit", user=actor))
        self.login(actor)
        self.assertEqual(self.client.post("/admin/users/1/emergency-reset", data={}).status_code, 302)

    def test_platform_admin_preserves_operational_management_but_not_security_downgrade(self):
        actor = fixture_user("platform", platform="grandmaster")
        self.login(actor)
        security = PermissionRule.query.filter_by(permission_key="neoapps.user_management.edit").one()
        operation = PermissionRule.query.filter_by(permission_key="neoermac.building_lineup.edit").one()
        self.policy_post((security, "watcher"))
        self.assertEqual(security.minimum_role, "grandmaster")
        self.policy_post((operation, "master"))
        self.assertEqual(operation.minimum_role, "master")

    def test_legacy_security_threshold_renders_locked_without_migrating_saved_policy(self):
        from tests.html_contracts import document
        actor = fixture_user("legacy-policy-platform", platform="grandmaster")
        security = PermissionRule.query.filter_by(permission_key="neoapps.user_management.edit").one()
        security.minimum_role = "master"
        self.login(actor)
        root = document(self.client.get("/admin/permissions"))
        select = root.one("select", name=f"minimum_role_{security.id}")
        self.assertIn("disabled", select.attrs)
        self.assertIn("selected", select.one("option", value="grandmaster").attrs)
        self.assertFalse(root.findall("input", name="rule_ids", value=str(security.id)))
        operation = PermissionRule.query.filter_by(permission_key="neoermac.building_lineup.edit").one()
        self.policy_post((operation, "master"))
        self.assertEqual(operation.minimum_role, "master")
        self.assertEqual(security.minimum_role, "master")

    def test_bootstrap_preserves_every_existing_identity_security_field_and_tokens(self):
        user = fixture_user("Kessler", platform="watcher")
        user.email = "preserved@example.test"
        user.email_verified_at = None
        user.password_reset_required = True
        user.password_policy_update_required = True
        user.temporary_password_expires_at = datetime.utcnow() + timedelta(hours=1)
        user.mfa_required = user.mfa_enabled = True
        user.mfa_secret = "synthetic-enrolled-secret"
        user.mfa_verified_at = datetime(2026, 1, 1)
        raw, token = create_user_token(user, PASSWORD_RESET)
        user.is_active = False
        db.session.commit()
        snapshot = {col.name: getattr(user, col.name) for col in User.__table__.columns}
        roles = [(r.id, r.role, r.is_active) for r in GatewayNodeRole.query.all()]
        with patch.dict("os.environ", {"BOOTSTRAP_ADMIN_EMAIL": "different@example.test", "BOOTSTRAP_ADMIN_PASSWORD": "DifferentBootstrapPassword123!"}):
            for _ in range(2):
                result = bootstrap_database(self.app)
                self.assertFalse(result["created_user"])
                self.assertFalse(result["password_applied"])
        db.session.expire_all()
        self.assertEqual(snapshot, {col.name: getattr(user, col.name) for col in User.__table__.columns})
        self.assertEqual(roles, [(r.id, r.role, r.is_active) for r in GatewayNodeRole.query.all()])
        self.assertIsNone(token.used_at)
        self.assertIsNotNone(get_valid_token_record(raw, PASSWORD_RESET))

    def test_existing_bootstrap_email_match_does_not_rename_or_restore_missing_grants(self):
        user = fixture_user("renamed-bootstrap")
        member = GatewayMembership.query.filter_by(user_id=user.id).one()
        GatewayNodeRole.query.filter_by(gateway_membership_id=member.id).delete()
        PortalAppAccess.query.filter_by(user_id=user.id).delete()
        db.session.delete(member)
        db.session.commit()
        with patch.dict("os.environ", {"BOOTSTRAP_ADMIN_EMAIL": user.email, "BOOTSTRAP_ADMIN_PASSWORD": "DifferentBootstrapPassword123!"}):
            bootstrap_database(self.app)
        self.assertEqual(user.username, "renamed-bootstrap")
        self.assertEqual(GatewayMembership.query.count(), 0)
        self.assertEqual(PortalAppAccess.query.count(), 0)
        self.assertEqual(GatewayNodeRole.query.count(), 0)

    def test_email_change_revokes_both_tokens_sessions_and_audits_without_losing_access(self):
        admin = fixture_user("email-admin", platform="grandmaster")
        target = fixture_user("email-target", nodes={"scorpion": "operator"})
        reset, _ = create_user_token(target, PASSWORD_RESET)
        verify, _ = create_user_token(target, EMAIL_VERIFICATION)
        password_hash, version, user_id = target.password_hash, target.auth_session_version, target.id
        self.login(admin)
        response = self.client.post(f"/admin/users/{target.id}/edit", data=identity_form(target, "corrected@example.test"))
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(target.email_verified_at)
        self.assertIsNone(get_valid_token_record(reset, PASSWORD_RESET))
        self.assertIsNone(get_valid_token_record(verify, EMAIL_VERIFICATION))
        self.assertEqual((target.id, target.password_hash), (user_id, password_hash))
        self.assertEqual(target.auth_session_version, version + 1)
        self.assertTrue(user_can_access_node(target, "RFD", "scorpion", "operator"))
        self.assertEqual(self.client.get("/verify-email/" + verify).status_code, 400)
        self.assertIsNone(target.email_verified_at)
        self.assertEqual(self.client.post("/reset-password/" + reset, data=dict(password=PASSWORD, confirm_password=PASSWORD)).status_code, 400)
        event = AccountSecurityEvent.query.one()
        self.assertEqual((event.actor_user_id, event.subject_user_id, event.action), (admin.id, target.id, "email_changed"))
        self.assertEqual(event.details["new_email"], "corrected@example.test")

    def test_email_change_failure_rolls_back_identity_token_and_security_audit(self):
        admin = fixture_user("rollback-admin", platform="grandmaster")
        target = fixture_user("rollback-target")
        reset, _ = create_user_token(target, PASSWORD_RESET)
        verify, _ = create_user_token(target, EMAIL_VERIFICATION)
        self.login(admin)
        before = target.email, target.auth_session_version, target.email_verified_at
        form = identity_form(target, "rollback-new@example.test")
        form["management_level"] = "invalid"
        form["is_management"] = "1"
        self.assertEqual(self.client.post(f"/admin/users/{target.id}/edit", data=form).status_code, 400)
        self.assertEqual((target.email, target.auth_session_version, target.email_verified_at), before)
        self.assertIsNotNone(get_valid_token_record(reset, PASSWORD_RESET))
        self.assertIsNotNone(get_valid_token_record(verify, EMAIL_VERIFICATION))
        self.assertEqual(AccountSecurityEvent.query.count(), 0)

    def test_node_master_only_delegates_lower_roles_in_own_node(self):
        actor = fixture_user("node-master", nodes={"scorpion": "master", "motherbrain": "grandmaster"})
        target = fixture_user("role-target")
        self.login(actor)
        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        rain = NeoNode.query.filter_by(code="rain").one()
        member = GatewayMembership.query.filter_by(user_id=target.id).one()
        grant = GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=scorpion.id).one()
        path = f"/admin/users/{target.id}/roles"
        self.client.post(path, data={f"node_{scorpion.id}": "simulator"})
        self.assertEqual(grant.role, "simulator")
        for selected in ("master", "grandmaster"):
            self.client.post(path, data={f"node_{scorpion.id}": selected})
            self.assertEqual(grant.role, "simulator")
        self.client.post(path, data={f"node_{rain.id}": "operator"})
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=rain.id).one().role, "watcher")
        grant.role = "grandmaster"
        db.session.commit()
        self.client.post(path, data={f"node_{scorpion.id}": "watcher"})
        self.assertEqual(grant.role, "grandmaster")

    def test_node_grandmaster_cannot_commission_peer_or_promote_self(self):
        actor = fixture_user("node-gm", nodes={"scorpion": "grandmaster"})
        target = fixture_user("peer-target")
        self.login(actor)
        node = NeoNode.query.filter_by(code="scorpion").one()
        self.client.post(f"/admin/users/{target.id}/roles", data={f"node_{node.id}": "grandmaster"})
        member = GatewayMembership.query.filter_by(user_id=target.id).one()
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node.id).one().role, "watcher")
        platform = fixture_user("self-platform", platform="grandmaster")
        self.login(platform)
        self.client.post(f"/admin/users/{platform.id}/roles", data={f"node_{node.id}": "grandmaster"})
        own = GatewayMembership.query.filter_by(user_id=platform.id).one()
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=own.id, node_id=node.id).one().role, "watcher")
        self.assertFalse(platform.can_manage_role("grandmaster"))
        # Parent-scope oversight remains valid for a different person's node
        # grant; it does not commission a peer NeoApps Grandmaster.
        self.client.post(f"/admin/users/{target.id}/roles", data={f"node_{node.id}": "grandmaster"})
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node.id).one().role, "grandmaster")

    def test_scoped_role_page_and_change_do_not_provision_foreign_grants(self):
        actor = fixture_user("read-only-role-admin", nodes={"scorpion": "master"})
        target = fixture_user("legacy-role-target")
        membership = GatewayMembership.query.filter_by(user_id=target.id).one()
        GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).delete()
        PortalAppAccess.query.filter_by(user_id=target.id).delete()
        self.login(actor)
        path = f"/admin/users/{target.id}/roles"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(), 0)
        self.assertEqual(PortalAppAccess.query.filter_by(user_id=target.id).count(), 0)
        node = NeoNode.query.filter_by(code="scorpion").one()
        self.client.post(path, data={f"node_{node.id}": "operator"})
        self.assertEqual([(r.node_id, r.role) for r in GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id)], [(node.id, "operator")])
        self.assertEqual(PortalAppAccess.query.filter_by(user_id=target.id).count(), 0)

    def test_missing_explicit_row_does_not_allow_demoting_inherited_higher_role(self):
        actor = fixture_user("inferred-role-admin", nodes={"scorpion": "master"})
        target = fixture_user("inferred-role-target")
        PortalAppAccess.query.filter_by(user_id=target.id, app_code="neogateway").one().role = "grandmaster"
        member = GatewayMembership.query.filter_by(user_id=target.id).one()
        node = NeoNode.query.filter_by(code="scorpion").one()
        GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node.id).delete()
        self.login(actor)
        self.client.post(f"/admin/users/{target.id}/roles", data={f"node_{node.id}": "watcher"})
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node.id).count(), 0)

    def test_new_address_can_verify_and_unchanged_save_does_not_repeat_security_change(self):
        admin = fixture_user("fresh-verification-admin", platform="grandmaster")
        target = fixture_user("fresh-verification-target")
        self.login(admin)
        path = f"/admin/users/{target.id}/edit"
        form = identity_form(target, "fresh-verified@example.test")
        self.assertEqual(self.client.post(path, data=form).status_code, 302)
        version = target.auth_session_version
        fresh, _ = create_user_token(target, EMAIL_VERIFICATION)
        db.session.commit()
        self.assertEqual(self.client.get("/verify-email/" + fresh).status_code, 200)
        self.assertIsNotNone(target.email_verified_at)
        self.assertEqual(self.client.post(path, data=form).status_code, 302)
        self.assertEqual(target.auth_session_version, version)
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target.id, action="email_changed").count(), 1)

    def test_last_effective_admin_not_replaced_by_revoked_backup(self):
        platform = fixture_user("last-admin-platform", platform="grandmaster")
        target = fixture_user("last-effective", nodes={"motherbrain": "grandmaster"})
        backup = fixture_user("revoked-backup", nodes={"motherbrain": "grandmaster"})
        PortalAppAccess.query.filter_by(user_id=backup.id, app_code="neogateway").one().is_active = False
        self.login(platform)
        node = NeoNode.query.filter_by(code="motherbrain").one()
        self.client.post(f"/admin/users/{target.id}/roles", data={f"node_{node.id}": "master"})
        member = GatewayMembership.query.filter_by(user_id=target.id).one()
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=member.id, node_id=node.id).one().role, "grandmaster")
