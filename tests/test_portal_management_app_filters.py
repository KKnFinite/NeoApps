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
    def test_launcher_assets_and_two_approved_apps(self):
        rows = [{'app': app, 'access': {'status':'approved', 'is_active':True}} for app in PORTAL_APPS]
        with patch('app.auth.routes.portal_dashboard_rows_for_user', return_value=rows):
            response = self.client.get('/portal')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        launcher = html.split('<section class="portal-launcher"', 1)[1].split('</main>', 1)[0]
        self.assertEqual(launcher.count('data-portal-app='), 2)
        self.assertNotIn('neobid', launcher.lower())
        for asset in ('neoapps_portal_desktop.webp', 'neoapps_login_mobile.webp', 'neogateway-inapp-128.png', 'neogateway-inapp-256.png', 'neostaffing-inapp-128.png', 'neostaffing-inapp-256.png'):
            self.assertIn(asset, launcher)
        self.assertNotIn('images/icons/icon_', launcher)
        self.assertLess(launcher.index('data-portal-app="neogateway"'), launcher.index('data-portal-app="neostaffing"'))
        self.assertIn('href="/rfd"', launcher)
        self.assertIn('href="/neostaffing"', launcher)
        self.assertNotIn('Approved', launcher)

    def test_launcher_pending_remains_non_launchable(self):
        for access in PortalAppAccess.query.filter_by(user_id=self.admin.id):
            access.status = "pending"
        db.session.commit()
        html = self.client.get('/portal').get_data(as_text=True).split('<section class="portal-launcher"',1)[1]
        self.assertNotIn('data-portal-app=', html)
        self.assertIn('My Account', html)
        self.assertNotIn('action="/portal/request-access"', html)

    def test_launcher_other_states_preserve_request_form(self):
        for status, active in [('denied',True),('approved',False),('pending',False)]:
            with self.subTest(status=status, active=active):
                for access in PortalAppAccess.query.filter_by(user_id=self.admin.id):
                    access.status, access.is_active = status, active
                db.session.commit()
                html=self.client.get('/portal').get_data(as_text=True).split('<section class="portal-launcher"',1)[1]
                self.assertNotIn('data-portal-app=',html)
                self.assertIn('href="/my-account"',html)
                self.assertNotIn('action="/portal/request-access"',html)

    def test_neobid_approved_access_renders_existing_placeholder(self):
        db.session.add(PortalAppAccess(user_id=self.admin.id, app_code="neobid",
                                      status="approved", role="watcher", is_active=True))
        db.session.commit()
        response = self.client.get('/neobid')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Coming Soon', response.data)
        self.assertIn(b'href="/portal"', response.data)

    def test_neobid_unapproved_or_inactive_access_redirects_to_portal(self):
        access = PortalAppAccess(user_id=self.admin.id, app_code="neobid",
                                 status="pending", role="watcher", is_active=True)
        db.session.add(access)
        for status, active in (('pending', True), ('denied', True), ('approved', False)):
            with self.subTest(status=status, active=active):
                access.status, access.is_active = status, active
                db.session.commit()
                response = self.client.get('/neobid')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, '/portal')

    def test_neobid_still_requires_placeholder_permission_and_authentication(self):
        db.session.add(PortalAppAccess(user_id=self.admin.id, app_code="neobid",
                                      status="approved", role="watcher", is_active=True))
        db.session.commit()
        with patch('app.auth.routes.user_can', return_value=False) as permission:
            response = self.client.get('/neobid')
        self.assertEqual(response.location, '/portal')
        permission.assert_called_once_with('neobid.placeholder.view')
        self.client.post('/logout')
        response = self.client.get('/neobid')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.location)

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
        db.session.add(PortalAppAccess(user_id=self.admin.id,app_code='neostaffing',status='approved',role='watcher',is_active=True))
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
            "/accounts/requests",
            query_string={"status": "all"},
        )
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(gateway.user.email, html)
        self.assertIn(staffing.user.email, html)
        self.assertNotIn(bid.user.email, html)
        self.assertIn('All available applications', html)
        self.assertIn('value="all" selected', html)

    def test_each_application_filter_returns_only_that_application(self):
        accesses = {
            app_code: self._access(f"{app_code}_pending", app_code, "pending")
            for app_code in ("neogateway", "neostaffing")
        }
        db.session.commit()

        for app_code, expected_access in accesses.items():
            with self.subTest(app_code=app_code):
                response = self.client.get(
                    "/accounts/requests",
                    query_string={"app": app_code, "status": "pending"},
                )
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn(expected_access.user.email, html)
                for other_code, other_access in accesses.items():
                    if other_code != app_code:
                        self.assertNotIn(other_access.user.email, html)
                self.assertIn(
                    f'value="{app_code}" selected',
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
                    "/accounts/requests",
                    query_string={"app": "neostaffing", "status": status},
                )
                html = response.get_data(as_text=True)

                self.assertIn(expected.user.email, html)
                for access in excluded:
                    self.assertNotIn(access.user.email, html)

    def test_pending_counts_are_per_application_and_ignore_resolved_requests(self):
        self._access("gateway_pending_one","neogateway","pending")
        self._access("gateway_pending_two","neogateway","pending")
        self._access("gateway_approved","neogateway","approved")
        self._access("staffing_pending_one","neostaffing","pending")
        self._access("bid_denied_one","neobid","denied")
        db.session.commit()
        html=self.client.get("/accounts/requests?status=all").get_data(as_text=True)
        self.assertIn("NeoGateway (2)",html)
        self.assertIn("NeoStaffing (1)",html)
        self.assertNotIn('value="neobid"',html)

    def test_filter_links_and_search_preserve_query_state(self):
        target=self._access("query_state","neostaffing","approved")
        for index in range(26):
            self._access(f"query_state_{index}","neostaffing","approved")
        db.session.commit()
        html=self.client.get("/accounts/requests",query_string={"app":"neostaffing","status":"approved","q":"Query State"}).get_data(as_text=True)
        self.assertIn('value="neostaffing" selected',html)
        self.assertIn('value="approved" selected',html)
        self.assertIn('name="q" value="Query State"',html)
        self.assertIn('page=2',html)
        self.assertIn('q=Query+State',html)
        self.assertIn(target.user.email,html)

    def test_invalid_application_filter_falls_back_to_all_requests(self):
        gateway=self._access("invalid_gateway","neogateway","pending")
        staffing=self._access("invalid_staffing","neostaffing","pending")
        db.session.commit()
        response=self.client.get("/accounts/requests?app=not-a-real-app&status=pending")
        self.assertEqual(response.status_code,200)
        self.assertIn(gateway.user.email,response.get_data(as_text=True))
        self.assertIn(staffing.user.email,response.get_data(as_text=True))
        self.assertNotIn('value="not-a-real-app"',response.get_data(as_text=True))

    def test_registry_entries_automatically_receive_application_tabs(self):
        future_apps=PORTAL_APPS+({"code":"neofuture","name":"NeoFuture","coming_soon":True},)
        with patch("app.services.accounts_workspace.portal_app_definitions",return_value=future_apps):
            html=self.client.get("/accounts/requests").get_data(as_text=True)
        self.assertNotIn('value="neofuture"',html)
        self.assertNotIn('value="neobid"',html)
        self.assertIn('value="neostaffing"',html)

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
        access=self._access("unverified_ui","neostaffing","pending",verified=False)
        db.session.commit()
        response=self.client.get(f"/accounts/people/{access.user_id}?tab=access")
        self.assertEqual(response.status_code,200)
        self.assertIn(access.user.email,response.get_data(as_text=True))
        self.assertIn('name="bypass_email_verification"',response.get_data(as_text=True))
        self.assertIn('Explicitly approve without verifying email',response.get_data(as_text=True))

    def test_user_search_renders_nonwrapping_edit_user_action(self):
        target=self._user("edit_button_target",verified=True)
        db.session.commit()
        response=self.client.get("/accounts",query_string={"q":target.email})
        self.assertEqual(response.status_code,200)
        self.assertIn(f'href="/accounts/people/{target.id}"',response.get_data(as_text=True))
        self.assertEqual(response.data.count(b'class="aa-person"'),1)

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
