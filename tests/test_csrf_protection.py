from datetime import datetime
import re
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    PortalAppAccess,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


class CsrfProtectionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "csrf-test-secret-key-with-enough-length",
                "TESTING": True,
                "CSRF_PROTECT_TESTING": True,
                "CSRF_TOKEN_TTL_SECONDS": 7200,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_login_requires_valid_csrf_token_and_renders_form_token(self):
        user = self._user("csrf_login")
        db.session.commit()

        without_token = self.client.post(
            "/login",
            data={"username": user.username, "password": "Password123!"},
        )
        invalid_token = self.client.post(
            "/login",
            data={
                "username": user.username,
                "password": "Password123!",
                "csrf_token": "invalid",
            },
        )
        login_page = self.client.get("/login")
        token = self._csrf_token(login_page)
        successful = self.client.post(
            "/login",
            data={
                "username": user.username,
                "password": "Password123!",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

        self.assertEqual(without_token.status_code, 400)
        self.assertIn(b"Form session expired", without_token.data)
        self.assertEqual(invalid_token.status_code, 400)
        self.assertIn(b'name="csrf_token"', login_page.data)
        self.assertIn(b'X-CSRF-Token', login_page.data)
        self.assertEqual(successful.status_code, 302)

    def test_expired_csrf_token_is_rejected(self):
        user = self._user("csrf_expired")
        db.session.commit()
        self.app.config["CSRF_TOKEN_TTL_SECONDS"] = 1

        with patch("app.services.csrf.time.time", return_value=1_000_000):
            token = self._csrf_token(self.client.get("/login"))
        with patch("app.services.csrf.time.time", return_value=1_000_002):
            response = self.client.post(
                "/login",
                data={
                    "username": user.username,
                    "password": "Password123!",
                    "csrf_token": token,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Form session expired", response.data)

    def test_portal_management_mutation_requires_csrf(self):
        administrator = self._user("csrf_admin", role="grandmaster")
        target = self._user("csrf_target")
        access = PortalAppAccess(
            user_id=target.id,
            app_code="neostaffing",
            status="pending",
            role="watcher",
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
        self._login(administrator)

        without_token = self.client.post(
            f"/portal/manage/app-access/{access.id}/update",
            data={"action": "approve", "role": "simulator"},
        )
        token = self._csrf_token(self.client.get("/portal/manage"))
        with_token = self.client.post(
            f"/portal/manage/app-access/{access.id}/update",
            data={
                "action": "approve",
                "role": "simulator",
                "csrf_token": token,
            },
            follow_redirects=False,
        )

        self.assertEqual(without_token.status_code, 400)
        self.assertEqual(with_token.status_code, 302)
        self.assertEqual(db.session.get(PortalAppAccess, access.id).status, "approved")

    def test_neosektor_fetch_mutation_requires_and_accepts_csrf_header(self):
        user = self._user("csrf_sektor")
        self._grant_gateway_node_access(user, "sektor", "simulator")
        db.session.commit()
        self._login(user)

        conductor_page = self.client.get("/neosektor/tunnel-conductor")
        token = self._csrf_token(conductor_page)
        without_token = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "1", "delta": 1},
            headers={"Accept": "application/json"},
        )
        with_token = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "1", "delta": 1},
            headers={"Accept": "application/json", "X-CSRF-Token": token},
        )

        self.assertEqual(conductor_page.status_code, 200)
        self.assertEqual(without_token.status_code, 400)
        self.assertEqual(without_token.get_json()["error"], "CSRF validation failed. Refresh and try again.")
        self.assertEqual(with_token.status_code, 200)
        self.assertTrue(with_token.get_json()["ok"])

    def test_neostaffing_mutation_requires_csrf(self):
        user = self._user("csrf_staffing")
        self._grant_app_access(user, "neostaffing", "simulator")
        work_area = self._work_area()
        person = staffing_service.create_person(
            {
                "employee_id": "CSRF-100",
                "first_name": "Casey",
                "last_name": "Staffing",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "employee_status": "active",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user)

        without_token = self.client.post(
            f"/neostaffing/people/{person.id}/clear-work-area",
        )
        token = self._csrf_token(self.client.get("/neostaffing/people"))
        with_token = self.client.post(
            f"/neostaffing/people/{person.id}/clear-work-area",
            data={"csrf_token": token},
            follow_redirects=False,
        )

        self.assertEqual(without_token.status_code, 400)
        self.assertEqual(with_token.status_code, 302)
        assignment = StaffingWorkAssignment.query.filter_by(person_id=person.id).first()
        self.assertIsNotNone(assignment)
        self.assertFalse(assignment.active)

    def test_logout_is_post_only_requires_csrf_and_get_keeps_session(self):
        user = self._user("csrf_logout")
        db.session.commit()
        self._login(user)
        token = self._csrf_token(self.client.get("/access-pending"))

        get_logout = self.client.get("/logout", follow_redirects=False)
        without_token = self.client.post("/logout", follow_redirects=False)
        still_authenticated = self.client.get("/access-pending", follow_redirects=False)
        with_token = self.client.post(
            "/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        after_logout = self.client.get("/access-pending", follow_redirects=False)

        self.assertEqual(get_logout.status_code, 405)
        self.assertEqual(without_token.status_code, 400)
        self.assertEqual(still_authenticated.status_code, 200)
        self.assertEqual(with_token.status_code, 302)
        self.assertEqual(with_token.location, "/login")
        self.assertEqual(after_logout.status_code, 302)
        self.assertIn("/login", after_logout.location)

    def _csrf_token(self, response):
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def _login(self, user):
        token = self._csrf_token(self.client.get("/login"))
        response = self.client.post(
            "/login",
            data={
                "username": user.username,
                "password": "Password123!",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        return response

    def _user(self, username, *, role="watcher"):
        user = User(
            username=username,
            email=f"{username}@example.test",
            role=role,
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        user.set_password("Password123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _grant_app_access(self, user, app_code, role):
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code=app_code,
                status="approved",
                role=role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )

    def _grant_gateway_node_access(self, user, node_code, role):
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=NeoNode.query.filter_by(code=node_code).one().id,
                role=role,
                is_active=True,
            )
        )

    def _work_area(self):
        sort = staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Operations", "parent_id": sort.id}
        )
        return staffing_service.create_unit(
            {"unit_type": "work_area", "name": "EBM", "parent_id": operation.id}
        )


if __name__ == "__main__":
    unittest.main()
