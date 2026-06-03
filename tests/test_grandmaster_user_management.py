from datetime import datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
    user_can_access_node,
)


class GrandmasterUserManagementTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
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

    def test_grandmaster_can_access_user_management(self):
        grandmaster = self._admin("grandmaster_admin", "grandmaster")
        target = self._approved_user("approved_user", "approved@example.com")[0]
        db.session.commit()
        self._login(grandmaster.username)

        paths = (
            "/admin/users",
            "/admin/users/pending",
            f"/admin/users/{target.id}",
            f"/admin/users/{target.id}/roles",
            f"/admin/users/{target.id}/emergency-password-reset",
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_master_cannot_access_grandmaster_user_management(self):
        master = self._admin("master_admin", "master")
        target = self._approved_user("target_user", "target@example.com")[0]
        db.session.commit()
        self._login(master.username)

        paths = (
            "/admin/users",
            "/admin/users/pending",
            f"/admin/users/{target.id}",
            f"/admin/users/{target.id}/roles",
            f"/admin/users/{target.id}/emergency-password-reset",
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, "/")

    def test_pending_users_appear_on_pending_requests_screen(self):
        grandmaster = self._admin("pending_grandmaster", "grandmaster")
        self._pending_user("pending_user", "pending@example.com", verified=True)
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.get("/admin/users/pending")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"pending_user", response.data)
        self.assertIn(b"pending@example.com", response.data)

    def test_unverified_pending_user_cannot_be_approved(self):
        grandmaster = self._admin("unverified_grandmaster", "grandmaster")
        _user, membership = self._pending_user(
            "unverified_pending",
            "unverified@example.com",
            verified=False,
        )
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.post(
            f"/admin/users/{membership.user_id}/gateway-membership",
            data={"action": "approve"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Email not verified yet.", response.data)
        self.assertEqual(db.session.get(GatewayMembership, membership.id).status, "pending")

    def test_verified_pending_user_can_be_approved_by_grandmaster(self):
        grandmaster = self._admin("approve_grandmaster", "grandmaster")
        user, membership = self._pending_user(
            "verified_pending",
            "verified@example.com",
            verified=True,
        )
        db.session.commit()
        self._login(grandmaster.username)

        with patch(
            "app.auth.routes.email_service.send_access_approved",
            return_value={"sent": True},
        ) as send_approved:
            response = self.client.post(
                f"/admin/users/{user.id}/gateway-membership",
                data={
                    "action": "approve",
                    "approval_notes": "Supervisor confirmed.",
                },
                follow_redirects=False,
            )

        updated = db.session.get(GatewayMembership, membership.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.approved_by_user_id, grandmaster.id)
        self.assertIsNotNone(updated.approved_at)
        self.assertEqual(updated.approval_notes, "Supervisor confirmed.")
        self.assertIsNotNone(updated.approval_email_sent_at)
        self.assertEqual(send_approved.call_count, 1)
        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "watcher"))
        self.assertEqual(GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(), 0)

    def test_denial_sets_metadata_and_sends_no_email(self):
        grandmaster = self._admin("deny_grandmaster", "grandmaster")
        user, membership = self._pending_user("deny_pending", "deny@example.com", verified=True)
        db.session.commit()
        self._login(grandmaster.username)

        with patch("app.auth.routes.email_service.send_access_approved") as send_approved:
            response = self.client.post(
                f"/admin/users/{user.id}/gateway-membership",
                data={
                    "action": "deny",
                    "denial_notes": "Needs manager follow-up.",
                },
                follow_redirects=False,
            )

        updated = db.session.get(GatewayMembership, membership.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.status, "denied")
        self.assertEqual(updated.denied_by_user_id, grandmaster.id)
        self.assertIsNotNone(updated.denied_at)
        self.assertEqual(updated.denial_notes, "Needs manager follow-up.")
        self.assertEqual(send_approved.call_count, 0)

    def test_node_role_updates_create_update_and_remove_watcher_override(self):
        grandmaster = self._admin("roles_grandmaster", "grandmaster")
        user, membership = self._approved_user("role_user", "role@example.com")
        db.session.commit()
        self._login(grandmaster.username)

        sektor = NeoNode.query.filter_by(code="sektor").first()
        form = self._role_form()
        form[f"node_{sektor.id}"] = "operator"

        create_response = self.client.post(
            f"/admin/users/{user.id}/roles",
            data=form,
            follow_redirects=False,
        )
        role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=sektor.id,
        ).first()
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(role.role, "operator")
        self.assertTrue(user_can_access_node(user, "RFD", "sektor", "operator"))

        form[f"node_{sektor.id}"] = "simulator"
        update_response = self.client.post(
            f"/admin/users/{user.id}/roles",
            data=form,
            follow_redirects=False,
        )
        updated_role = db.session.get(GatewayNodeRole, role.id)

        form[f"node_{sektor.id}"] = "watcher"
        remove_response = self.client.post(
            f"/admin/users/{user.id}/roles",
            data=form,
            follow_redirects=False,
        )

        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(updated_role.role, "simulator")
        self.assertEqual(remove_response.status_code, 302)
        self.assertIsNone(db.session.get(GatewayNodeRole, role.id))
        self.assertTrue(user_can_access_node(user, "RFD", "sektor", "watcher"))
        self.assertFalse(user_can_access_node(user, "RFD", "sektor", "operator"))

    def test_node_roles_require_approved_gateway_membership(self):
        grandmaster = self._admin("roles_pending_grandmaster", "grandmaster")
        user, _membership = self._pending_user("role_pending", "rolepending@example.com", verified=True)
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.get(f"/admin/users/{user.id}/roles", follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"User must have approved RFD gateway access before assigning node roles.",
            response.data,
        )

    def test_last_active_grandmaster_cannot_downgrade_own_motherbrain_role(self):
        grandmaster = self._admin("only_grandmaster", "grandmaster")
        db.session.commit()
        self._login(grandmaster.username)
        motherbrain = NeoNode.query.filter_by(code="motherbrain").first()
        form = self._role_form()
        form[f"node_{motherbrain.id}"] = "master"

        response = self.client.post(
            f"/admin/users/{grandmaster.id}/roles",
            data=form,
            follow_redirects=True,
        )

        membership = GatewayMembership.query.filter_by(user_id=grandmaster.id).first()
        role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=motherbrain.id,
        ).first()
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Cannot remove or downgrade the last active Grandmaster", response.data)
        self.assertEqual(role.role, "grandmaster")

    def test_grandmaster_emergency_reset_forces_password_change(self):
        grandmaster = self._admin("reset_grandmaster", "grandmaster")
        master = self._admin("reset_master", "master")
        target, _membership = self._approved_user("reset_target", "reset@example.com")
        db.session.commit()

        self._login(master.username)
        master_response = self.client.post(
            f"/admin/users/{target.id}/emergency-password-reset",
            data={
                "reason": "Master should not be allowed.",
                "password": "TempPassword123!",
                "confirm_password": "TempPassword123!",
            },
            follow_redirects=False,
        )
        self.client.get("/logout")

        self._login(grandmaster.username)
        grandmaster_response = self.client.post(
            f"/admin/users/{target.id}/emergency-password-reset",
            data={
                "reason": "Email reset unavailable.",
                "password": "TempPassword123!",
                "confirm_password": "TempPassword123!",
            },
            follow_redirects=False,
        )
        self.client.get("/logout")

        login_response = self.client.post(
            "/login",
            data={"username": "reset_target", "password": "TempPassword123!"},
            follow_redirects=False,
        )
        blocked = self.client.get("/motherbrain", follow_redirects=False)
        change_response = self.client.post(
            "/change-password",
            data={
                "password": "PermanentPassword123!",
                "confirm_password": "PermanentPassword123!",
            },
            follow_redirects=False,
        )

        updated = db.session.get(User, target.id)
        self.assertEqual(master_response.status_code, 302)
        self.assertEqual(master_response.location, "/")
        self.assertEqual(grandmaster_response.status_code, 302)
        self.assertTrue(updated.password_reset_required is False)
        self.assertTrue(updated.check_password("PermanentPassword123!"))
        self.assertEqual(updated.last_password_reset_by_user_id, grandmaster.id)
        self.assertIsNotNone(updated.last_password_reset_at)
        self.assertEqual(updated.last_password_reset_reason, "Email reset unavailable.")
        self.assertEqual(login_response.location, "/change-password")
        self.assertEqual(blocked.location, "/change-password")
        self.assertEqual(change_response.status_code, 302)

    def test_grandmaster_only_links_do_not_appear_for_master(self):
        master = self._admin("link_master", "master")
        db.session.commit()
        self._login(master.username)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access Requests", response.data)
        self.assertNotIn(b"User Management", response.data)
        self.assertNotIn(b'href="/admin/users"', response.data)

    def test_kessler_style_grandmaster_login_reaches_user_management(self):
        kessler = self._admin("Kessler", "grandmaster")
        db.session.commit()

        login_response = self.client.post(
            "/login",
            data={"username": "kessler", "password": "Password123!"},
            follow_redirects=False,
        )
        users_response = self.client.get("/admin/users")

        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.location, "/motherbrain")
        self.assertEqual(users_response.status_code, 200)
        self.assertIn(b"User Management", users_response.data)

    def test_pending_denied_and_no_membership_users_go_to_access_pending(self):
        gateway = ensure_default_gateway_and_nodes()
        pending = self._pending_user("pending_access", "pendingaccess@example.com", verified=True)[0]
        denied = self._user("denied_access", "deniedaccess@example.com", verified=True)
        db.session.add(
            GatewayMembership(
                user_id=denied.id,
                gateway_id=gateway.id,
                status="denied",
                is_active=True,
            )
        )
        no_membership = self._user("no_membership", "nomembership@example.com", verified=True)
        db.session.commit()

        for user in (pending, denied, no_membership):
            with self.subTest(username=user.username):
                self.client.get("/logout")
                login = self.client.post(
                    "/login",
                    data={"username": user.username, "password": "Password123!"},
                    follow_redirects=False,
                )
                motherbrain = self.client.get("/motherbrain", follow_redirects=False)
                self.assertEqual(login.location, "/access-pending")
                self.assertEqual(motherbrain.location, "/access-pending")

    def _role_form(self):
        return {
            f"node_{node.id}": "watcher"
            for node in NeoNode.query.filter_by(is_active=True).all()
        }

    def _user(self, username, email, verified=False):
        user = User(
            username=username,
            email=email,
            full_name=username.replace("_", " ").title(),
            employee_id=f"EMP-{username}",
            supervisor_name="Supervisor",
            work_area="Ramp",
            access_reason="Operational access.",
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
        user = self._user(username, f"{username.lower()}@example.com", verified=True)
        user.role = node_role
        backfill_default_gateway_node_roles(user, role=node_role)
        db.session.flush()
        return user

    def _pending_user(self, username, email, verified):
        user = self._user(username, email, verified=verified)
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
        user = self._user(username, email, verified=True)
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
