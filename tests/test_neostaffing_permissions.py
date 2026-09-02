from datetime import datetime
import unittest

from flask import g

from app import create_app
from app.extensions import db
from app.models import PermissionRule, PortalAppAccess, User
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.services.password_policy import set_user_password


class NeoStaffingPermissionsTest(unittest.TestCase):
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

    def test_local_permissions_page_redirects_to_central_node_permissions(self):
        user = self._user_with_access("staffing_permissions", "grandmaster")
        self._login(user)

        get_response = self.client.get(
            "/neostaffing/permissions", follow_redirects=False
        )
        post_response = self.client.post(
            "/neostaffing/permissions", follow_redirects=False
        )

        self.assertEqual(get_response.status_code, 303)
        self.assertEqual(get_response.location, "/motherbrain/permissions")
        self.assertEqual(post_response.status_code, 303)
        self.assertEqual(post_response.location, "/motherbrain/permissions")

    def test_staffing_action_permissions_keep_configurable_safe_defaults(self):
        self.assertEqual(
            self._rule("neostaffing.attendance.take").minimum_role,
            "operator",
        )
        self.assertEqual(
            self._rule("neostaffing.change_requests.submit").minimum_role,
            "operator",
        )
        self.assertEqual(
            self._rule("neostaffing.change_requests.approve").minimum_role,
            "operator",
        )
        self.assertEqual(
            self._rule("neostaffing.staffing_groups.edit").minimum_role,
            "master",
        )
        self.assertEqual(
            self._rule("neostaffing.vacation_selection.edit").minimum_role,
            "watcher",
        )
        self.assertEqual(
            self._rule("neostaffing.settings.edit").minimum_role,
            "master",
        )

    def test_read_only_staffing_pages_follow_app_access(self):
        watcher = self._user_with_access("staffing_watcher", "watcher")
        self._rule("neostaffing.reports.view").minimum_role = "grandmaster"
        db.session.commit()

        self.assertTrue(user_can("neostaffing.reports.view", watcher))
        self.assertFalse(user_can("neostaffing.settings.edit", watcher))

    def test_unknown_staffing_action_fails_safe_even_for_grandmaster(self):
        grandmaster = self._user_with_access(
            "staffing_unknown_action", "grandmaster"
        )

        self.assertFalse(user_can("neostaffing.unregistered.action", grandmaster))

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
    def _rule(permission_key):
        return PermissionRule.query.filter_by(permission_key=permission_key).one()


if __name__ == "__main__":
    unittest.main()
