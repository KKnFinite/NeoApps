from datetime import datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, PortalAppAccess, User
from app.services.access_control import ensure_default_gateway_and_nodes


class NeoStaffingRoutesTest(unittest.TestCase):
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

    def test_approved_neostaffing_user_can_open_dashboard(self):
        user = self._user("staffing_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"STAFFING CONTROL", response.data)
        self.assertIn(b"STAFFING BOARD", response.data)
        self.assertIn(b"EMPLOYEE ROSTER", response.data)
        self.assertIn(b"SENIORITY LISTS", response.data)
        self.assertIn(b"APP MANAGEMENT", response.data)
        self.assertIn(b"BACK TO NEOAPPS PORTAL", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b"Change Characters", response.data)

    def test_user_without_neostaffing_access_cannot_open_dashboard(self):
        user = self._user("no_staffing")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_neogateway_only_user_cannot_open_neostaffing(self):
        user = self._user("gateway_only")
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_master_and_grandmaster_can_open_app_management(self):
        for role in ("master", "grandmaster"):
            with self.subTest(role=role):
                client = self.app.test_client()
                user = self._user(f"staffing_{role}")
                self._grant_app_access(user, "neostaffing", role)
                db.session.commit()
                client.post(
                    "/login",
                    data={"username": user.username, "password": "Password123!"},
                    follow_redirects=False,
                )

                response = client.get("/neostaffing/app-management")

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"APP MANAGEMENT", response.data)
                self.assertIn(b"WORK AREA HIERARCHY", response.data)
                self.assertIn(b"CLASSIFICATION MANAGEMENT", response.data)
                self.assertIn(b"MANAGEMENT ASSIGNMENTS", response.data)
                self.assertIn(b"PERMISSIONS", response.data)

    def test_lower_neostaffing_role_cannot_open_app_management(self):
        user = self._user("staffing_watcher")
        self._grant_app_access(user, "neostaffing", "watcher")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/app-management", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing")

    def test_portal_tile_opens_neostaffing_for_approved_user(self):
        user = self._user("staffing_portal")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/portal")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"APPROVED Operator", response.data)
        self.assertIn(b'href="/neostaffing"', response.data)

    def _user(self, username):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.title(),
            last_name="User",
            full_name=f"{username.title()} User",
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        user.set_password("Password123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _grant_app_access(self, user, app_code, role):
        access = PortalAppAccess(
            user_id=user.id,
            app_code=app_code,
            status="approved",
            role=role,
            is_active=True,
            approved_at=datetime.utcnow(),
        )
        db.session.add(access)
        db.session.flush()
        return access

    def _login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "Password123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
