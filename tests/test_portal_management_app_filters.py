from datetime import datetime
from urllib.parse import parse_qs, urlparse
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, User
from app.services.access_control import PORTAL_APPS, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class PortalManagementAppFiltersTest(unittest.TestCase):
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
        self.client = self.app.test_client()

        self.admin = self._user("filter_admin", verified=True)
        self.admin.role = "grandmaster"
        backfill_default_gateway_node_roles(self.admin, role="grandmaster")
        db.session.commit()
        self._login(self.admin.username)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_all_requests_shows_mixed_applications_and_statuses(self):
        gateway = self._access("gateway_pending", "neogateway", "pending")
        staffing = self._access("staffing_approved", "neostaffing", "approved")
        bid = self._access("bid_denied", "neobid", "denied")
        db.session.commit()

        response = self.client.get(
            "/portal/manage",
            query_string={"status": "all"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(gateway.user.email, html)
        self.assertIn(staffing.user.email, html)
        self.assertIn(bid.user.email, html)
        self.assertIn('data-app-filter="all"', html)
        self.assertIn('data-status-filter="all"', html)

    def test_each_application_filter_returns_only_that_application(self):
        accesses = {
            app_code: self._access(f"{app_code}_pending", app_code, "pending")
            for app_code in ("neogateway", "neostaffing", "neobid")
        }
        db.session.commit()

        for app_code, expected_access in accesses.items():
            with self.subTest(app_code=app_code):
                response = self.client.get(
                    "/portal/manage",
                    query_string={"app": app_code, "status": "pending"},
                )
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn(expected_access.user.email, html)
                for other_code, other_access in accesses.items():
                    if other_code != app_code:
                        self.assertNotIn(other_access.user.email, html)
                self.assertIn(
                    f'data-app-filter="{app_code}"',
                    html,
                )

    def test_application_and_status_filters_combine(self):
        pending = self._access("staff_pending", "neostaffing", "pending")
        approved = self._access("staff_approved", "neostaffing", "approved")
        denied = self._access("staff_denied", "neostaffing", "denied")
        other = self._access("bid_denied", "neobid", "denied")
        db.session.commit()

        cases = (
            ("pending", pending, (approved, denied, other)),
            ("approved", approved, (pending, denied, other)),
            ("denied", denied, (pending, approved, other)),
        )
        for status, expected, excluded in cases:
            with self.subTest(status=status):
                response = self.client.get(
                    "/portal/manage",
                    query_string={"app": "neostaffing", "status": status},
                )
                html = response.get_data(as_text=True)

                self.assertIn(expected.user.email, html)
                for access in excluded:
                    self.assertNotIn(access.user.email, html)

    def test_pending_counts_are_per_application_and_ignore_resolved_requests(self):
        self._access("gateway_pending_one", "neogateway", "pending")
        self._access("gateway_pending_two", "neogateway", "pending")
        self._access("gateway_approved", "neogateway", "approved")
        self._access("staffing_pending_one", "neostaffing", "pending")
        self._access("bid_denied_one", "neobid", "denied")
        db.session.commit()

        response = self.client.get(
            "/portal/manage",
            query_string={"status": "all"},
        )
        html = response.get_data(as_text=True)

        self.assertRegex(
            html,
            r"NeoGateway\s*<span class=\"portal-request-filter-count\">\(2\)</span>",
        )
        self.assertRegex(
            html,
            r"NeoStaffing\s*<span class=\"portal-request-filter-count\">\(1\)</span>",
        )
        self.assertRegex(
            html,
            r"NeoBid\s*<span class=\"portal-request-filter-count\">\(0\)</span>",
        )

    def test_filter_links_and_search_preserve_query_state(self):
        self._access("query_state", "neostaffing", "approved")
        db.session.commit()

        response = self.client.get(
            "/portal/manage",
            query_string={
                "app": "neostaffing",
                "status": "approved",
                "q": "Query State",
            },
        )
        html = response.get_data(as_text=True)

        self.assertIn('name="app" value="neostaffing"', html)
        self.assertIn('name="status" value="approved"', html)
        self.assertIn('name="q" value="Query State"', html)
        self.assertIn("app=neostaffing", html)
        self.assertIn("status=approved", html)
        self.assertIn("q=Query+State", html)

    def test_invalid_application_filter_falls_back_to_all_requests(self):
        gateway = self._access("invalid_gateway", "neogateway", "pending")
        staffing = self._access("invalid_staffing", "neostaffing", "pending")
        db.session.commit()

        response = self.client.get(
            "/portal/manage",
            query_string={"app": "not-a-real-app", "status": "pending"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(gateway.user.email, html)
        self.assertIn(staffing.user.email, html)
        self.assertRegex(
            html,
            r'data-app-filter="all"[^>]*aria-current="page"',
        )

    def test_registry_entries_automatically_receive_application_tabs(self):
        future_apps = PORTAL_APPS + (
            {
                "code": "neofuture",
                "name": "NeoFuture",
                "description": "Future application.",
                "endpoint": "auth.portal_dashboard",
                "icon_folder": "neogateway",
                "coming_soon": True,
            },
        )

        with patch(
            "app.auth.routes.portal_app_definitions",
            return_value=future_apps,
        ):
            response = self.client.get("/portal/manage")

        html = response.get_data(as_text=True)
        self.assertIn('data-app-filter="neofuture"', html)
        self.assertRegex(
            html,
            r"NeoFuture\s*<span class=\"portal-request-filter-count\">\(0\)</span>",
        )

    def test_approval_and_denial_preserve_active_filters(self):
        approve_access = self._access("filtered_approve", "neostaffing", "pending")
        deny_access = self._access("filtered_deny", "neostaffing", "pending")
        db.session.commit()

        approve_response = self.client.post(
            f"/portal/manage/app-access/{approve_access.id}/update",
            data={
                "action": "approve",
                "role": "master",
                "return_app": "neostaffing",
                "return_status": "pending",
                "return_q": "Filtered",
            },
            follow_redirects=False,
        )
        deny_response = self.client.post(
            f"/portal/manage/app-access/{deny_access.id}/update",
            data={
                "action": "deny",
                "return_app": "neostaffing",
                "return_status": "pending",
                "return_q": "Filtered",
            },
            follow_redirects=False,
        )

        self.assertEqual(approve_response.status_code, 302)
        self.assertEqual(deny_response.status_code, 302)
        self._assert_filter_redirect(approve_response.location)
        self._assert_filter_redirect(deny_response.location)
        self.assertEqual(
            db.session.get(PortalAppAccess, approve_access.id).status,
            "approved",
        )
        self.assertEqual(
            db.session.get(PortalAppAccess, deny_access.id).status,
            "denied",
        )

    def test_unverified_app_access_requires_explicit_bypass_and_stays_unverified(self):
        access = self._access("unverified_override", "neostaffing", "pending", verified=False)
        db.session.commit()

        blocked = self.client.post(
            f"/portal/manage/app-access/{access.id}/update",
            data={"action": "approve", "role": "watcher"},
            follow_redirects=True,
        )
        approved = self.client.post(
            f"/portal/manage/app-access/{access.id}/update",
            data={"action": "approve_without_verification", "role": "watcher"},
            follow_redirects=False,
        )
        db.session.expire_all()
        updated = db.session.get(PortalAppAccess, access.id)

        self.assertIn(b"Email not verified yet", blocked.data)
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(updated.status, "approved")
        self.assertIsNone(updated.user.email_verified_at)
        self.assertIn("EMAIL VERIFICATION BYPASS", updated.approval_notes)

    def test_portal_management_renders_unverified_override_action_for_editors(self):
        access = self._access("unverified_ui", "neostaffing", "pending", verified=False)
        db.session.commit()

        response = self.client.get("/portal/manage", query_string={"status": "pending"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(access.user.email, html)
        self.assertIn("EMAIL UNVERIFIED", html)
        self.assertIn("APPROVE WITHOUT VERIFICATION", html)

    def test_user_search_renders_nonwrapping_edit_user_action(self):
        target = self._user("edit_button_target", verified=True)
        db.session.commit()

        response = self.client.get(
            "/portal/manage/users/edit-users",
            query_string={"q": target.email},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="portal-edit-action"', response.get_data(as_text=True))
        self.assertIn("EDIT USER", response.get_data(as_text=True))

    def _assert_filter_redirect(self, location):
        parsed = urlparse(location)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/portal/manage")
        self.assertEqual(query.get("app"), ["neostaffing"])
        self.assertEqual(query.get("status"), ["pending"])
        self.assertEqual(query.get("q"), ["Filtered"])

    def _access(self, username, app_code, status, *, verified=True):
        user = self._user(username, verified=verified)
        access = PortalAppAccess(
            user_id=user.id,
            app_code=app_code,
            status=status,
            role="watcher",
            is_active=True,
        )
        db.session.add(access)
        db.session.flush()
        return access

    def _user(self, username, *, verified=False):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.replace("_", " ").title(),
            last_name="User",
            full_name=f"{username.replace('_', ' ').title()} User",
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
        )
        if verified:
            user.email_verified_at = datetime.utcnow()
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
