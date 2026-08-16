from datetime import datetime
import re
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

    def test_master_can_view_permissions_page(self):
        user = self._user_with_access("staffing_master", "master")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoStaffing permission settings", response.data)
        self.assertIn(b"VIEW ONLY", response.data)
        self.assertNotIn(b"SAVE PERMISSIONS", response.data)

    def test_permission_page_defaults_and_role_choices(self):
        user = self._user_with_access("staffing_defaults", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")
        view_rule = self._rule("neostaffing.permissions.view")
        edit_rule = self._rule("neostaffing.permissions.edit")

        self.assertEqual(view_rule.minimum_role, "master")
        self.assertEqual(edit_rule.minimum_role, "grandmaster")
        for role in ("watcher", "operator", "simulator", "master", "grandmaster"):
            self.assertIn(f'<option value="{role}"'.encode(), response.data)

    def test_master_cannot_save_even_if_edit_threshold_is_lowered(self):
        user = self._user_with_access("staffing_master_save", "master")
        edit_rule = self._rule("neostaffing.permissions.edit")
        edit_rule.minimum_role = "master"
        attendance_rule = self._rule("neostaffing.attendance.take")
        db.session.commit()
        self._login(user)

        response = self.client.post(
            "/neostaffing/permissions",
            data={
                "rule_ids": str(attendance_rule.id),
                f"minimum_role_{attendance_rule.id}": "simulator",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing/permissions")
        db.session.expire_all()
        self.assertEqual(self._rule("neostaffing.attendance.take").minimum_role, "operator")

    def test_grandmaster_can_view_and_save_permissions(self):
        user = self._user_with_access("staffing_grandmaster", "grandmaster")
        attendance_rule = self._rule("neostaffing.attendance.take")
        self._login(user)

        page = self.client.get("/neostaffing/permissions")
        saved = self.client.post(
            "/neostaffing/permissions",
            data={
                "rule_ids": str(attendance_rule.id),
                f"minimum_role_{attendance_rule.id}": "simulator",
            },
            follow_redirects=False,
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"SAVE PERMISSIONS", page.data)
        self.assertEqual(saved.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self._rule("neostaffing.attendance.take").minimum_role, "simulator")

    def test_non_master_access_follows_configured_view_threshold(self):
        user = self._user_with_access("staffing_operator", "operator")
        self._login(user)

        denied = self.client.get("/neostaffing/permissions", follow_redirects=False)
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.location, "/neostaffing")

        self._rule("neostaffing.permissions.view").minimum_role = "operator"
        db.session.commit()
        allowed = self.client.get("/neostaffing/permissions")

        self.assertEqual(allowed.status_code, 200)
        self.assertIn(b"VIEW ONLY", allowed.data)
        self.assertNotIn(b"SAVE PERMISSIONS", allowed.data)

    def test_page_renders_only_neostaffing_rules(self):
        user = self._user_with_access("staffing_rules_only", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")
        rendered_keys = re.findall(
            rb'data-permission-key="([^"]+)"',
            response.data,
        )

        self.assertTrue(rendered_keys)
        self.assertTrue(all(key.startswith(b"neostaffing.") for key in rendered_keys))
        self.assertNotIn(b"neomotherbrain.dashboard.view", response.data)
        self.assertNotIn(b"neoermac.building_lineup.edit", response.data)

    def test_take_attendance_label_and_default_role(self):
        user = self._user_with_access("staffing_attendance_rule", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")
        attendance_start = response.data.index(
            b'data-permission-key="neostaffing.attendance.take"'
        )
        attendance_row = response.data[attendance_start : attendance_start + 1800]

        self.assertIn(b"Take Attendance", attendance_row)
        self.assertIn(b'<option value="operator" selected>', attendance_row)
        self.assertEqual(self._rule("neostaffing.attendance.take").minimum_role, "operator")

    def test_change_request_permissions_have_configurable_safe_defaults(self):
        user = self._user_with_access("staffing_request_rules", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")

        self.assertIn(b"View Change Requests", response.data)
        self.assertIn(b"Submit Change Requests", response.data)
        self.assertIn(b"Approve Change Requests", response.data)
        self.assertEqual(
            self._rule("neostaffing.change_requests.view").minimum_role,
            "watcher",
        )
        self.assertEqual(
            self._rule("neostaffing.change_requests.submit").minimum_role,
            "operator",
        )
        self.assertEqual(
            self._rule("neostaffing.change_requests.approve").minimum_role,
            "operator",
        )

    def test_staffing_group_permissions_have_configurable_defaults(self):
        user = self._user_with_access("staffing_group_rules", "grandmaster")
        self._login(user)

        response = self.client.get("/neostaffing/permissions")

        self.assertIn(b"View Staffing Groups", response.data)
        self.assertIn(b"Edit Staffing Groups", response.data)
        self.assertEqual(
            self._rule("neostaffing.staffing_groups.view").minimum_role,
            "operator",
        )
        self.assertEqual(
            self._rule("neostaffing.staffing_groups.edit").minimum_role,
            "master",
        )

    def test_grandmaster_always_passes_configured_neostaffing_permission(self):
        user = self._user_with_access("staffing_unrestricted", "grandmaster")
        self._rule("neostaffing.board.view").minimum_role = "watcher"
        self._rule("neostaffing.permissions.edit").minimum_role = "grandmaster"
        db.session.commit()

        self.assertTrue(user_can("neostaffing.board.view", user))
        self.assertTrue(user_can("neostaffing.permissions.edit", user))
        self.assertTrue(user_can("neostaffing.unregistered.action", user))

    def test_neostaffing_save_does_not_change_non_staffing_rules(self):
        user = self._user_with_access("staffing_isolated", "grandmaster")
        attendance_rule = self._rule("neostaffing.attendance.take")
        non_staffing_rule = self._rule("neoermac.building_lineup.edit")
        original_non_staffing_role = non_staffing_rule.minimum_role
        self._login(user)

        response = self.client.post(
            "/neostaffing/permissions",
            data={
                "rule_ids": str(attendance_rule.id),
                f"minimum_role_{attendance_rule.id}": "master",
            },
        )

        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(self._rule("neostaffing.attendance.take").minimum_role, "master")
        self.assertEqual(
            self._rule("neoermac.building_lineup.edit").minimum_role,
            original_non_staffing_role,
        )

    def test_forged_non_staffing_rule_update_is_rejected(self):
        user = self._user_with_access("staffing_forged_rule", "grandmaster")
        non_staffing_rule = self._rule("neoermac.building_lineup.edit")
        original_role = non_staffing_rule.minimum_role
        self._login(user)

        response = self.client.post(
            "/neostaffing/permissions",
            data={
                "rule_ids": str(non_staffing_rule.id),
                f"minimum_role_{non_staffing_rule.id}": "watcher",
            },
        )

        self.assertEqual(response.status_code, 200)
        db.session.expire_all()
        self.assertEqual(
            self._rule("neoermac.building_lineup.edit").minimum_role,
            original_role,
        )

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
