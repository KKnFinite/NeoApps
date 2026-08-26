from datetime import datetime
import unittest

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    MotherBrainAlert,
    PermissionRule,
    PortalAppAccess,
    User,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.my_alerts import has_pending_access_requests, my_alert_context
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class PendingAccessAlertsTest(unittest.TestCase):
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
        ensure_default_permission_rules()
        self.gateway = ensure_default_gateway_and_nodes()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_access_request_alert_permission_defaults_to_grandmaster(self):
        rule = PermissionRule.query.filter_by(
            permission_key="neoapps.access_requests.view"
        ).one()

        self.assertEqual(rule.minimum_role, "grandmaster")

    def test_pending_gateway_membership_produces_one_dynamic_alert(self):
        self._gateway_request("gateway_request")

        tray = self._alert_context()

        self.assertEqual(tray["count"], 1)
        self.assertEqual(tray["alerts"][0].title, "Pending Access Requests")
        self.assertEqual(
            tray["alerts"][0].message,
            "There are pending NeoApps access requests awaiting review.",
        )
        self.assertEqual(tray["alerts"][0].related_label, "REVIEW REQUESTS")
        self.assertEqual(tray["alerts"][0].related_url, "/portal/manage")
        self.assertEqual(MotherBrainAlert.query.count(), 0)

    def test_pending_portal_app_access_produces_one_dynamic_alert(self):
        self._portal_app_request("portal_request")

        tray = self._alert_context()

        self.assertEqual(tray["count"], 1)
        self.assertEqual(tray["alerts"][0].title, "Pending Access Requests")

    def test_multiple_pending_records_still_produce_one_aggregate_alert(self):
        self._gateway_request("gateway_request")
        self._portal_app_request("portal_request")
        self._portal_app_request("second_portal_request", app_code="neobid")

        tray = self._alert_context()

        self.assertEqual(tray["count"], 1)
        self.assertEqual(
            [alert.title for alert in tray["alerts"]],
            ["Pending Access Requests"],
        )

    def test_alert_disappears_when_all_pending_requests_are_resolved(self):
        membership = self._gateway_request("resolved_gateway_request")
        app_access = self._portal_app_request("resolved_portal_request")
        self.assertEqual(self._alert_context()["count"], 1)

        membership.status = "approved"
        app_access.status = "denied"
        db.session.commit()

        self.assertEqual(self._alert_context()["count"], 0)

    def test_no_active_pending_records_produce_no_access_request_alert(self):
        approved_user = self._user("approved_request")
        denied_user = self._user("denied_request")
        inactive_user = self._user("inactive_request")
        approved_app_user = self._user("approved_app_request")
        denied_app_user = self._user("denied_app_request")
        inactive_app_user = self._user("inactive_app_request")
        db.session.add_all(
            [
                GatewayMembership(
                    user_id=approved_user.id,
                    gateway_id=self.gateway.id,
                    status="approved",
                    is_active=True,
                ),
                GatewayMembership(
                    user_id=denied_user.id,
                    gateway_id=self.gateway.id,
                    status="denied",
                    is_active=True,
                ),
                GatewayMembership(
                    user_id=inactive_user.id,
                    gateway_id=self.gateway.id,
                    status="pending",
                    is_active=False,
                ),
                PortalAppAccess(
                    user_id=approved_app_user.id,
                    app_code="neostaffing",
                    status="approved",
                    role="watcher",
                    is_active=True,
                ),
                PortalAppAccess(
                    user_id=denied_app_user.id,
                    app_code="neostaffing",
                    status="denied",
                    role="watcher",
                    is_active=True,
                ),
                PortalAppAccess(
                    user_id=inactive_app_user.id,
                    app_code="neostaffing",
                    status="pending",
                    role="watcher",
                    is_active=False,
                ),
            ]
        )
        db.session.commit()

        tray = self._alert_context()

        self.assertEqual(tray["count"], 0)
        self.assertFalse(tray["has_alerts"])

    def test_pending_access_check_uses_one_select_and_no_writes(self):
        self._gateway_request("query_gateway_request")
        self._portal_app_request("query_portal_request")
        db.session.expire_all()
        statements = []

        def capture_statement(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip().upper())

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            self.assertTrue(has_pending_access_requests())
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(
            sum(statement.startswith("SELECT") for statement in statements),
            1,
        )
        self.assertEqual(
            sum(
                statement.startswith(("INSERT", "UPDATE", "DELETE"))
                for statement in statements
            ),
            0,
        )

    def test_pending_alert_respects_access_request_view_permission(self):
        self._gateway_request("restricted_request")

        denied = my_alert_context(can_view_permission=lambda _key: False)
        allowed = self._alert_context()

        self.assertEqual(denied["count"], 0)
        self.assertEqual(allowed["count"], 1)

    def test_grandmaster_sees_pending_alert_and_master_does_not(self):
        self._gateway_request("role_request")
        grandmaster = self._admin("alert_grandmaster", "grandmaster")
        master = self._admin("alert_master", "master")
        db.session.commit()

        self._login(grandmaster.username)
        allowed = self.client.get("/portal")
        self.client.post("/logout")
        self._login(master.username)
        denied = self.client.get("/portal")

        self.assertIn(b"Pending Access Requests", allowed.data)
        self.assertIn(b'href="/portal/manage">REVIEW REQUESTS</a>', allowed.data)
        self.assertNotIn(b"Pending Access Requests", denied.data)
        self.assertIn(b"data-my-alerts-tray", denied.data)

    def test_pending_alert_tray_is_global_across_authenticated_apps(self):
        self._gateway_request("global_request")
        admin = self._admin("global_grandmaster", "grandmaster")
        db.session.add(
            PortalAppAccess(
                user_id=admin.id,
                app_code="neostaffing",
                status="approved",
                role="grandmaster",
                is_active=True,
            )
        )
        db.session.commit()
        self._login(admin.username)

        paths = (
            "/portal",
            "/rfd",
            "/motherbrain",
            "/neoermac",
            "/neosektor",
            "/neoscorpion",
            "/neostaffing",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-my-alerts-tray", response.data)
                self.assertIn(b"Pending Access Requests", response.data)
                self.assertIn(b"REVIEW REQUESTS", response.data)

    def test_no_pending_request_hides_item_but_keeps_global_tray(self):
        admin = self._admin("empty_grandmaster", "grandmaster")
        db.session.commit()
        self._login(admin.username)

        for path in ("/portal", "/rfd"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-my-alerts-tray", response.data)
                self.assertIn(b"My Alerts", response.data)
                self.assertIn(b"No alerts.", response.data)
                self.assertNotIn(b"Pending Access Requests", response.data)

    def test_motherbrain_alerts_remain_scoped_to_motherbrain_pages(self):
        self._gateway_request("scoped_request")
        admin = self._admin("scoped_grandmaster", "grandmaster")
        db.session.add(
            MotherBrainAlert(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                scope="motherbrain",
                title="MotherBrain-only operational alert",
                message="Keep this alert inside its existing scope.",
                severity="warning",
                active=True,
                acknowledged=False,
            )
        )
        db.session.commit()
        self._login(admin.username)

        motherbrain = self.client.get("/motherbrain")
        portal = self.client.get("/portal")
        gateway = self.client.get("/rfd")

        self.assertIn(b"Pending Access Requests", motherbrain.data)
        self.assertIn(b"MotherBrain-only operational alert", motherbrain.data)
        self.assertIn(b'data-alert-count="2"', motherbrain.data)
        self.assertIn(b"Pending Access Requests", portal.data)
        self.assertNotIn(b"MotherBrain-only operational alert", portal.data)
        self.assertIn(b"Pending Access Requests", gateway.data)
        self.assertNotIn(b"MotherBrain-only operational alert", gateway.data)

    def _alert_context(self):
        return my_alert_context(can_view_permission=lambda _key: True)

    def _gateway_request(self, username):
        user = self._user(username)
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="pending",
            is_active=True,
        )
        db.session.add(membership)
        db.session.commit()
        return membership

    def _portal_app_request(self, username, app_code="neostaffing"):
        user = self._user(username)
        access = PortalAppAccess(
            user_id=user.id,
            app_code=app_code,
            status="pending",
            role="watcher",
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
        return access

    def _admin(self, username, role):
        user = self._user(username, role=role)
        backfill_default_gateway_node_roles(user, role=role)
        db.session.flush()
        return user

    def _user(self, username, role="watcher"):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.replace("_", " ").title(),
            last_name="User",
            full_name=f"{username.replace('_', ' ').title()} User",
            employee_id=f"EMP-{username}",
            role=role,
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
