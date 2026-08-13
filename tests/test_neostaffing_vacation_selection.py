from datetime import datetime
import unittest

from flask import g
from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import PermissionRule, PortalAppAccess, User
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingVacationSelectionTest(unittest.TestCase):
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
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_user_meeting_view_threshold_can_open_placeholder(self):
        user = self._user_with_access("vacation_watcher", "watcher")
        self._login(user)

        response = self.client.get("/neostaffing/vacation-selection")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VACATION SELECTION", response.data)
        self.assertIn(b"COMING LATER", response.data)
        self.assertIn(
            b"Management vacation selection and seniority-based scheduling will be available here.",
            response.data,
        )

    def test_user_below_configured_threshold_is_denied(self):
        user = self._user_with_access("vacation_below_threshold", "watcher")
        self._rule().minimum_role = "operator"
        db.session.commit()
        self._login(user)

        response = self.client.get(
            "/neostaffing/vacation-selection",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing")

    def test_permission_defaults_to_watcher(self):
        self.assertEqual(self._rule().minimum_role, "watcher")

    def test_permission_appears_on_neostaffing_permissions_page(self):
        user = self._user_with_access("vacation_grandmaster", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"View Vacation Selection", response.data)
        self.assertIn(
            b'data-permission-key="neostaffing.vacation_selection.view"',
            response.data,
        )

    def test_navigation_exposes_vacation_selection_when_authorized(self):
        user = self._user_with_access("vacation_navigation", "watcher")
        self._login(user)

        landing = self.client.get("/neostaffing")
        section = self.client.get("/neostaffing/vacation-selection")

        self.assertIn(b'href="/neostaffing/vacation-selection"', landing.data)
        self.assertIn(b'href="/neostaffing/vacation-selection"', section.data)
        self.assertIn(b'aria-current="page"', section.data)

    def test_placeholder_has_no_write_surface_or_vacation_tables(self):
        user = self._user_with_access("vacation_read_only", "watcher")
        self._login(user)

        page = self.client.get("/neostaffing/vacation-selection")
        post = self.client.post("/neostaffing/vacation-selection")
        table_names = inspect(db.engine).get_table_names()
        page_content = page.data[
            page.data.index(b'<section class="neostaffing-page') : page.data.index(b"</main>")
        ]

        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"<form", page_content)
        self.assertEqual(post.status_code, 405)
        self.assertFalse(any("vacation" in name for name in table_names))

    def _user_with_access(self, username, role):
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
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role=role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.commit()
        return user

    def _login(self, user):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

    @staticmethod
    def _rule():
        return PermissionRule.query.filter_by(
            permission_key="neostaffing.vacation_selection.view"
        ).one()


if __name__ == "__main__":
    unittest.main()
