from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User, UserToken
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
    user_can_access_node,
)
from app.services.user_tokens import (
    EMAIL_VERIFICATION,
    PASSWORD_RESET,
    create_user_token,
)


class AuthAccountFlowsTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "APP_BASE_URL": "http://test.local",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_create_account_creates_user_pending_rfd_membership_and_sends_verification(self):
        with patch(
            "app.auth.routes.email_service.send_email_verification",
            return_value={"sent": False, "reason": "test"},
        ) as send_verification:
            response = self.client.post(
                "/create-account",
                data=self._account_form(username="newuser", email="NewUser@Example.com"),
            )

        user = User.query.filter_by(username="newuser").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()
        token = UserToken.query.filter_by(user_id=user.id, token_type=EMAIL_VERIFICATION).first()
        raw_token = send_verification.call_args.args[1]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.email, "newuser@example.com")
        self.assertTrue(user.check_password("AccountPass123!"))
        self.assertEqual(membership.gateway.code, "RFD")
        self.assertEqual(membership.status, "pending")
        self.assertFalse(user_can_access_node(user, "RFD", "motherbrain"))
        self.assertEqual(GatewayNodeRole.query.count(), 0)
        self.assertEqual(send_verification.call_count, 1)
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertNotIn(raw_token, token.token_hash)

    def test_pending_account_cannot_access_operational_data_before_approval(self):
        user = self._user("pending", email="pending@example.com", verified=True)
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=gateway.id,
                status="pending",
                is_active=True,
            )
        )
        db.session.commit()

        login = self.client.post(
            "/login",
            data={"username": "pending", "password": "Password123!"},
            follow_redirects=False,
        )
        motherbrain = self.client.get("/motherbrain", follow_redirects=False)

        self.assertEqual(login.status_code, 302)
        self.assertEqual(login.location, "/access-pending")
        self.assertEqual(motherbrain.status_code, 302)
        self.assertEqual(motherbrain.location, "/access-pending")

    def test_valid_email_verification_token_sets_verified_and_is_single_use(self):
        user = self._user("verify", email="verify@example.com")
        raw_token, token_record = create_user_token(user, EMAIL_VERIFICATION)
        db.session.commit()

        response = self.client.get(f"/verify-email/{raw_token}")
        second_response = self.client.get(f"/verify-email/{raw_token}")

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(db.session.get(User, user.id).email_verified_at)
        self.assertIsNotNone(db.session.get(UserToken, token_record.id).used_at)
        self.assertEqual(second_response.status_code, 400)

    def test_expired_email_verification_token_is_rejected(self):
        user = self._user("expiredverify", email="expiredverify@example.com")
        raw_token, token_record = create_user_token(user, EMAIL_VERIFICATION)
        token_record.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = self.client.get(f"/verify-email/{raw_token}")

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(db.session.get(User, user.id).email_verified_at)

    def test_master_and_grandmaster_approve_only_after_email_verified(self):
        master = self._admin("master_admin", "master")
        unverified_user, unverified_membership = self._pending_user(
            "unverified",
            "unverified@example.com",
            verified=False,
        )
        verified_user, verified_membership = self._pending_user(
            "verified",
            "verified@example.com",
            verified=True,
        )
        db.session.commit()
        self._login(master.username)

        blocked = self.client.post(
            f"/admin/access-requests/{unverified_membership.id}/approve",
            follow_redirects=True,
        )

        with patch(
            "app.auth.routes.email_service.send_access_approved",
            return_value={"sent": True},
        ) as send_approved:
            approved = self.client.post(
                f"/admin/access-requests/{verified_membership.id}/approve",
                data={"approval_notes": "Cleared by supervisor."},
                follow_redirects=False,
            )

        self.assertEqual(blocked.status_code, 200)
        self.assertIn(b"Email not verified yet", blocked.data)
        self.assertEqual(db.session.get(GatewayMembership, unverified_membership.id).status, "pending")
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(db.session.get(GatewayMembership, verified_membership.id).status, "approved")
        self.assertEqual(verified_membership.approved_by_user_id, master.id)
        self.assertEqual(verified_membership.approval_notes, "Cleared by supervisor.")
        self.assertIsNotNone(verified_membership.approval_email_sent_at)
        self.assertTrue(user_can_access_node(verified_user, "RFD", "motherbrain"))
        self.assertEqual(send_approved.call_count, 1)
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=verified_membership.id).count(), 0)
        self.assertIsNotNone(unverified_user)

    def test_denial_sends_no_email(self):
        grandmaster = self._admin("grand_admin", "grandmaster")
        _user, membership = self._pending_user("denied", "denied@example.com", verified=True)
        db.session.commit()
        self._login(grandmaster.username)

        with patch("app.auth.routes.email_service.send_access_approved") as send_approved:
            response = self.client.post(
                f"/admin/access-requests/{membership.id}/deny",
                data={"denial_notes": "Need supervisor confirmation."},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(GatewayMembership, membership.id).status, "denied")
        self.assertEqual(membership.denied_by_user_id, grandmaster.id)
        self.assertEqual(membership.denial_notes, "Need supervisor confirmation.")
        self.assertEqual(send_approved.call_count, 0)

    def test_grandmaster_can_use_master_level_approval_action(self):
        grandmaster = self._admin("grand_approver", "grandmaster")
        _user, membership = self._pending_user(
            "grandapproved",
            "grandapproved@example.com",
            verified=True,
        )
        db.session.commit()
        self._login(grandmaster.username)

        with patch(
            "app.auth.routes.email_service.send_access_approved",
            return_value={"sent": True},
        ):
            response = self.client.post(
                f"/admin/access-requests/{membership.id}/approve",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(GatewayMembership, membership.id).status, "approved")
        self.assertEqual(membership.approved_by_user_id, grandmaster.id)

    def test_approved_membership_gets_watcher_and_node_role_overrides_it(self):
        user, membership = self._approved_user("watcher", "watcher@example.com")

        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "watcher"))
        self.assertFalse(user_can_access_node(user, "RFD", "motherbrain", "operator"))

        sektor = NeoNode.query.filter_by(code="sektor").first()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=sektor.id,
                role="operator",
                is_active=True,
            )
        )
        db.session.commit()

        self.assertTrue(user_can_access_node(user, "RFD", "sektor", "operator"))
        self.assertFalse(user_can_access_node(user, "RFD", "ermac", "operator"))

    def test_forgot_password_is_generic_and_reset_token_works_once(self):
        user = self._user("resetme", email="resetme@example.com", verified=True)
        old_hash = user.password_hash
        db.session.commit()

        with patch(
            "app.auth.routes.email_service.send_password_reset",
            return_value={"sent": False, "reason": "test"},
        ) as send_reset:
            existing = self.client.post("/forgot-password", data={"email": "resetme@example.com"})
            missing = self.client.post("/forgot-password", data={"email": "missing@example.com"})

        raw_token = send_reset.call_args.args[1]
        reset = self.client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "NewPassword123!"},
            follow_redirects=False,
        )
        second_reset = self.client.post(
            f"/reset-password/{raw_token}",
            data={"password": "AnotherPassword123!", "confirm_password": "AnotherPassword123!"},
        )

        updated = db.session.get(User, user.id)
        self.assertIn(b"If an account exists", existing.data)
        self.assertIn(b"If an account exists", missing.data)
        self.assertEqual(send_reset.call_count, 1)
        self.assertEqual(reset.status_code, 302)
        self.assertEqual(second_reset.status_code, 400)
        self.assertNotEqual(updated.password_hash, old_hash)
        self.assertTrue(updated.check_password("NewPassword123!"))
        self.assertFalse(updated.password_reset_required)

    def test_expired_password_reset_token_is_rejected(self):
        user = self._user("expiredreset", email="expiredreset@example.com", verified=True)
        raw_token, token_record = create_user_token(user, PASSWORD_RESET)
        token_record.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = self.client.post(
            f"/reset-password/{raw_token}",
            data={"password": "NewPassword123!", "confirm_password": "NewPassword123!"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(user.check_password("NewPassword123!"))

    def test_grandmaster_emergency_reset_forces_password_change_and_master_cannot(self):
        grandmaster = self._admin("grandmaster_reset", "grandmaster")
        master = self._admin("master_reset", "master")
        target = self._user("target", email="target@example.com", verified=True)
        db.session.commit()

        self._login(master.username)
        master_response = self.client.post(
            f"/admin/users/{target.id}/emergency-reset",
            data={
                "reason": "Support call.",
                "password": "TempPassword123!",
                "confirm_password": "TempPassword123!",
            },
            follow_redirects=False,
        )
        self.client.get("/logout")

        self._login(grandmaster.username)
        grandmaster_response = self.client.post(
            f"/admin/users/{target.id}/emergency-reset",
            data={
                "reason": "Email reset unavailable.",
                "password": "TempPassword123!",
                "confirm_password": "TempPassword123!",
            },
            follow_redirects=False,
        )
        self.client.get("/logout")

        target_login = self.client.post(
            "/login",
            data={"username": "target", "password": "TempPassword123!"},
            follow_redirects=False,
        )
        forced_access = self.client.get("/motherbrain", follow_redirects=False)
        changed = self.client.post(
            "/change-password",
            data={"password": "PermanentPassword123!", "confirm_password": "PermanentPassword123!"},
            follow_redirects=False,
        )

        updated_target = db.session.get(User, target.id)
        self.assertEqual(master_response.status_code, 302)
        self.assertEqual(grandmaster_response.status_code, 302)
        self.assertTrue(updated_target.check_password("PermanentPassword123!"))
        self.assertFalse(updated_target.password_reset_required)
        self.assertEqual(updated_target.last_password_reset_by_user_id, grandmaster.id)
        self.assertEqual(updated_target.last_password_reset_reason, "Email reset unavailable.")
        self.assertEqual(target_login.location, "/change-password")
        self.assertEqual(forced_access.location, "/change-password")
        self.assertEqual(changed.status_code, 302)

    def test_landing_ui_account_links_and_neosektor_image_only(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/images/neorfd_logo1.png"', response.data)
        self.assertIn(b'<button class="command-access-panel command-enter-button" type="submit">', response.data)
        self.assertIn(b'href="/create-account"', response.data)
        self.assertIn(b'href="/forgot-password"', response.data)
        self.assertIn(b'href="https://neosektor.onrender.com/"', response.data)
        self.assertIn(b'src="/static/images/neosektor_logo1.png"', response.data)
        self.assertNotIn(b">NeoSektor</span>", response.data)

    def _account_form(self, **overrides):
        values = {
            "full_name": "New User",
            "employee_id": "E12345",
            "supervisor_name": "Boss Person",
            "email": "new@example.com",
            "work_area": "Ramp",
            "access_reason": "Need operational visibility.",
            "username": "",
            "password": "AccountPass123!",
            "confirm_password": "AccountPass123!",
        }
        values.update(overrides)
        return values

    def _user(self, username, email=None, verified=False):
        user = User(
            username=username,
            email=email or f"{username}@example.com",
            full_name=username.title(),
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
        )
        if verified:
            user.email_verified_at = datetime.utcnow()
        user.set_password("Password123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _admin(self, username, node_role):
        user = self._user(username, verified=True)
        user.role = node_role
        backfill_default_gateway_node_roles(user, role=node_role)
        db.session.flush()
        return user

    def _pending_user(self, username, email, verified):
        user = self._user(username, email=email, verified=verified)
        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="pending",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        return user, membership

    def _approved_user(self, username, email):
        user = self._user(username, email=email, verified=True)
        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        return user, membership

    def _login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "Password123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
