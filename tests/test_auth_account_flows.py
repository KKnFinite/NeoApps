from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User, UserToken
from app.services.access_control import (
    DEFAULT_NEONODES,
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
                data=self._account_form(email="NewUser@Example.com"),
            )

        user = User.query.filter_by(email="newuser@example.com").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()
        app_access = PortalAppAccess.query.filter_by(user_id=user.id, app_code="neogateway").first()
        token = UserToken.query.filter_by(user_id=user.id, token_type=EMAIL_VERIFICATION).first()
        raw_token = send_verification.call_args.args[1]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(user.first_name, "New")
        self.assertEqual(user.last_name, "User")
        self.assertEqual(user.full_name, "New User")
        self.assertEqual(user.username, "newuser@example.com")
        self.assertEqual(user.email, "newuser@example.com")
        self.assertTrue(user.check_password("AccountPass123!"))
        self.assertEqual(membership.gateway.code, "RFD")
        self.assertEqual(membership.status, "pending")
        self.assertEqual(app_access.status, "pending")
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
        self.assertEqual(login.location, "/portal")
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
        self.assertEqual(second_response.status_code, 200)
        self.assertIn(b"Already Verified", second_response.data)

    def test_expired_email_verification_token_is_rejected(self):
        user = self._user("expiredverify", email="expiredverify@example.com")
        raw_token, token_record = create_user_token(user, EMAIL_VERIFICATION)
        token_record.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = self.client.get(f"/verify-email/{raw_token}")

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(db.session.get(User, user.id).email_verified_at)

    def test_expired_verification_token_for_already_verified_user_is_friendly(self):
        user = self._user("oldverify", email="oldverify@example.com")
        user.email_verified_at = datetime.utcnow()
        raw_token, token_record = create_user_token(user, EMAIL_VERIFICATION)
        token_record.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()

        response = self.client.get(f"/verify-email/{raw_token}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Already Verified", response.data)

    def test_master_and_grandmaster_approve_only_after_email_verified(self):
        master = self._admin("master_admin", "master")
        grandmaster = self._admin("grand_admin_for_approval", "grandmaster")
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

        master_blocked = self.client.post(
            f"/admin/access-requests/{verified_membership.id}/approve",
            follow_redirects=False,
        )
        self.client.get("/logout")
        self._login(grandmaster.username)

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
        self.assertEqual(master_blocked.status_code, 302)
        self.assertEqual(master_blocked.location, "/portal")
        self.assertIn(b"Email not verified yet", blocked.data)
        self.assertEqual(db.session.get(GatewayMembership, unverified_membership.id).status, "pending")
        self.assertEqual(approved.status_code, 302)
        self.assertEqual(db.session.get(GatewayMembership, verified_membership.id).status, "approved")
        self.assertEqual(verified_membership.approved_by_user_id, grandmaster.id)
        self.assertEqual(verified_membership.approval_notes, "Cleared by supervisor.")
        self.assertIsNotNone(verified_membership.approval_email_sent_at)
        self.assertTrue(user_can_access_node(verified_user, "RFD", "motherbrain"))
        self.assertEqual(send_approved.call_count, 1)
        self.assertEqual(
            GatewayNodeRole.query.filter_by(gateway_membership_id=verified_membership.id).count(),
            len(DEFAULT_NEONODES),
        )
        self.assertTrue(
            all(
                role.role == "watcher"
                for role in GatewayNodeRole.query.filter_by(
                    gateway_membership_id=verified_membership.id,
                ).all()
            )
        )
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
        sektor_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=sektor.id,
        ).one()
        sektor_role.role = "operator"
        sektor_role.is_active = True
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

    def test_landing_ui_account_links_and_no_public_node_links(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"portal-brand-logo portal-login-logo", response.data)
        self.assertIn(b'src="/static/images/neoapps_logo_transparent.png"', response.data)
        self.assertNotIn(b'class="topbar"', response.data)
        self.assertNotIn(b"mobile-account-trigger", response.data)
        self.assertNotIn(b"data-mobile-topbar", response.data)
        self.assertNotIn(b"<strong>PORTAL</strong>", response.data)
        self.assertNotIn(b"Sign in once", response.data)
        self.assertIn(b'<button class="command-access-panel command-enter-button" type="submit">', response.data)
        self.assertIn(b'<label for="dashboard-email">Email</label>', response.data)
        self.assertIn(b'name="email"', response.data)
        self.assertIn(b'href="/create-account"', response.data)
        self.assertIn(b'href="/forgot-password"', response.data)
        self.assertNotIn(b"Username", response.data)
        self.assertNotIn(b"NeoSektor", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b'href="https://neosektor.onrender.com/"', response.data)

    def test_create_account_form_collects_split_name_and_not_username(self):
        response = self.client.get("/create-account")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="first_name"', response.data)
        self.assertIn(b'name="last_name"', response.data)
        self.assertIn(b'name="employee_id"', response.data)
        self.assertIn(b'name="email"', response.data)
        self.assertIn(b'name="app_codes"', response.data)
        self.assertIn(b'NeoGateway', response.data)
        self.assertIn(b'NeoStaffing', response.data)
        self.assertIn(b'NeoBid', response.data)
        self.assertNotIn(b'name="full_name"', response.data)
        self.assertNotIn(b'name="username"', response.data)

    def test_login_uses_email_with_legacy_username_fallback(self):
        user = self._user("loginchoice", email="loginchoice@example.com", verified=True)
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

        email_login = self.client.post(
            "/login",
            data={"email": "loginchoice@example.com", "password": "Password123!"},
            follow_redirects=False,
        )
        self.client.get("/logout")
        legacy_username_login = self.client.post(
            "/login",
            data={"username": "loginchoice", "password": "Password123!"},
            follow_redirects=False,
        )
        self.client.get("/logout")
        employee_login = self.client.post(
            "/login",
            data={"email": "EMP-loginchoice", "password": "Password123!"},
            follow_redirects=False,
        )

        self.assertEqual(email_login.location, "/portal")
        self.assertEqual(legacy_username_login.location, "/portal")
        self.assertEqual(employee_login.status_code, 401)

    def test_portal_dashboard_shows_approved_pending_and_request_states(self):
        user = self._user("portalstates", email="portalstates@example.com", verified=True)
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="pending",
                role="watcher",
                is_active=True,
            )
        )
        db.session.commit()

        self._login(user.username)
        response = self.client.get("/portal")
        html = response.get_data(as_text=True)
        app_card_section = html.split('<section class="portal-app-grid"', 1)[1].split("</section>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="portal-header-logo portal-header-logo-icon"', html)
        self.assertIn("portal-shell-page", html)
        self.assertIn('src="/static/images/icons/neoapps/inapp/neoapps-inapp-128.png"', html)
        self.assertIn('class="portal-header-title neo-brand-title', html)
        self.assertIn("neo-brand-title__node--apps", html)
        self.assertIn('<span class="portal-header-word">Portal</span>', html)
        self.assertNotIn("portal-dashboard-logo", html)
        self.assertNotIn("Choose an approved", html)
        self.assertNotIn("Gateway operations and NeoNode systems.", app_card_section)
        self.assertNotIn("Staffing operations and workforce planning.", app_card_section)
        self.assertNotIn("Bid tools placeholder for future buildout.", app_card_section)
        self.assertIn('class="portal-app-icon"', app_card_section)
        self.assertIn('src="/static/images/icons/neogateway/inapp/neogateway-inapp-128.png"', app_card_section)
        self.assertIn('src="/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png"', app_card_section)
        self.assertIn('src="/static/images/icons/neobid/icon_192.png"', app_card_section)
        self.assertIn(b"NeoGateway", response.data)
        self.assertIn(b"portal-app-title neo-brand-title", response.data)
        self.assertIn(b"neo-brand-title__node--gateway", response.data)
        self.assertIn(b"neo-brand-title__node--staffing", response.data)
        self.assertIn(b"neo-brand--gateway", response.data)
        self.assertIn(b"neo-brand__neo neo-word", response.data)
        self.assertIn(b"neo-brand__node node-word", response.data)
        self.assertNotIn(b"<h2>NeoGateway</h2>", response.data)
        self.assertIn(b"APPROVED", response.data)
        self.assertIn(b'href="/rfd"', response.data)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"PENDING", response.data)
        self.assertIn(b"NeoBid", response.data)
        self.assertIn(b"REQUEST ACCESS", response.data)
        self.assertNotIn('class="portal-install-section"', html)
        self.assertNotIn("data-install-button", html)
        self.assertNotIn("beforeinstallprompt", html)

    def test_portal_dashboard_hides_install_section_and_keeps_app_cards(self):
        user, _membership = self._approved_user("installcards", "installcards@example.com")
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="pending",
                role="watcher",
                is_active=True,
            )
        )
        db.session.commit()

        self._login(user.username)
        response = self.client.get("/portal")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('class="portal-app-card '), 3)
        app_card_section = html.split('<section class="portal-app-grid"', 1)[1].split("</section>", 1)[0]
        self.assertIn('src="/static/images/icons/neogateway/inapp/neogateway-inapp-128.png"', app_card_section)
        self.assertIn('src="/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png"', app_card_section)
        self.assertIn('src="/static/images/icons/neobid/icon_192.png"', app_card_section)
        self.assertNotIn('class="portal-install-section"', html)
        self.assertNotIn("portal-install-help", html)
        self.assertNotIn("Open in Safari.", html)
        self.assertNotIn("Add to Home Screen.", html)
        self.assertNotIn('data-manifest-url="/manifest/neogateway.webmanifest"', html)
        self.assertNotIn('data-start-url="/rfd"', html)
        self.assertNotIn("Gateway operations and NeoNode systems.", html)
        self.assertNotIn("Ballmat counts, routing, and discharge operations.", html)
        self.assertNotIn("Outbound door, lineup, and pull visibility.", html)
        self.assertNotIn('data-manifest-url="/manifest/sektor.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/ermac.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/scorpion.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/reptile.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/subzero.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/rain.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/motherbrain.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/neostaffing.webmanifest"', html)
        self.assertNotIn('data-manifest-url="/manifest/neobid.webmanifest"', html)

    def test_portal_dashboard_keeps_install_ui_hidden_for_accessible_apps_and_nodes(self):
        user = self._admin("installmaster", "simulator")
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role="master",
                is_active=True,
            )
        )
        db.session.commit()

        self._login(user.username)
        response = self.client.get("/portal")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="portal-app-card portal-app-neogateway is-approved"', html)
        self.assertIn('class="portal-app-card portal-app-neostaffing is-approved"', html)
        self.assertIn('class="portal-app-card portal-app-neobid is-', html)
        self.assertNotIn('class="portal-install-section"', html)
        self.assertNotIn('data-manifest-url="/manifest/motherbrain.webmanifest"', html)
        self.assertNotIn('data-start-url="/motherbrain"', html)
        self.assertNotIn('data-manifest-url="/manifest/neostaffing.webmanifest"', html)
        self.assertNotIn('data-start-url="/neostaffing"', html)

    def test_portal_dashboard_does_not_render_install_prompt_script(self):
        user, _membership = self._approved_user("installscript", "installscript@example.com")
        db.session.commit()

        self._login(user.username)
        response = self.client.get("/portal")
        html = response.get_data(as_text=True)

        self.assertNotIn("beforeinstallprompt", html)
        self.assertNotIn("data-install-default-label=\"How to Install\"", html)
        self.assertNotIn("Install prompt completed.", html)
        self.assertNotIn("This browser does not currently expose a native install prompt.", html)
        self.assertNotIn("data-install-button", html)
        self.assertNotIn("window.location.assign(targetStartUrl)", html)
        self.assertNotIn("AERODATABOX_API_KEY", html)
        self.assertNotIn("BREVO_API_KEY", html)

    def test_grandmaster_can_approve_portal_app_access_and_assign_role(self):
        grandmaster = self._admin("portal_grandmaster", "grandmaster")
        target = self._user("staffingrequest", email="staffingrequest@example.com", verified=True)
        access = PortalAppAccess(
            user_id=target.id,
            app_code="neostaffing",
            status="pending",
            role="watcher",
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.post(
            f"/portal/manage/app-access/{access.id}/update",
            data={"action": "approve", "role": "master", "notes": "Cleared."},
            follow_redirects=False,
        )

        updated = db.session.get(PortalAppAccess, access.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal/manage")
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.role, "master")

    def test_neogateway_app_approval_seeds_rfd_node_roles_from_selected_role(self):
        grandmaster = self._admin("gateway_portal_grandmaster", "grandmaster")
        target = self._user("gatewayrequest", email="gatewayrequest@example.com", verified=True)
        access = PortalAppAccess(
            user_id=target.id,
            app_code="neogateway",
            status="pending",
            role="watcher",
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
        self._login(grandmaster.username)

        with patch(
            "app.auth.routes.email_service.send_access_approved",
            return_value={"sent": False},
        ):
            response = self.client.post(
                f"/portal/manage/app-access/{access.id}/update",
                data={"action": "approve", "role": "simulator", "notes": "Seed simulator."},
                follow_redirects=False,
            )

        updated = db.session.get(PortalAppAccess, access.id)
        membership = GatewayMembership.query.filter_by(user_id=target.id).one()
        seeded_roles = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            is_active=True,
        ).all()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal/manage")
        self.assertEqual(updated.status, "approved")
        self.assertEqual(updated.role, "simulator")
        self.assertEqual(membership.status, "approved")
        self.assertEqual(len(seeded_roles), len(DEFAULT_NEONODES))
        self.assertEqual({role.role for role in seeded_roles}, {"simulator"})
        self.assertTrue(user_can_access_node(target, "RFD", "motherbrain", "simulator"))

    def test_gateway_access_request_approval_seeds_selected_role_to_all_nodes(self):
        grandmaster = self._admin("gateway_request_grandmaster", "grandmaster")
        target, membership = self._pending_user(
            "gatewayoldrequest",
            "gatewayoldrequest@example.com",
            verified=True,
        )
        db.session.commit()
        self._login(grandmaster.username)

        with patch(
            "app.auth.routes.email_service.send_access_approved",
            return_value={"sent": False},
        ):
            response = self.client.post(
                f"/admin/access-requests/{membership.id}/approve",
                data={"role": "master", "approval_notes": "Seed master."},
                follow_redirects=False,
            )

        seeded_roles = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            is_active=True,
        ).all()
        app_access = PortalAppAccess.query.filter_by(
            user_id=target.id,
            app_code="neogateway",
        ).one()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(GatewayMembership, membership.id).status, "approved")
        self.assertEqual(app_access.status, "approved")
        self.assertEqual(app_access.role, "master")
        self.assertEqual(len(seeded_roles), len(DEFAULT_NEONODES))
        self.assertEqual({role.role for role in seeded_roles}, {"master"})

    def _account_form(self, **overrides):
        values = {
            "first_name": "New",
            "last_name": "User",
            "employee_id": "E12345",
            "supervisor_name": "Boss Person",
            "email": "new@example.com",
            "work_area": "Ramp",
            "access_reason": "Need operational visibility.",
            "password": "AccountPass123!",
            "confirm_password": "AccountPass123!",
        }
        values.update(overrides)
        return values

    def _user(self, username, email=None, verified=False):
        user = User(
            username=username,
            email=email or f"{username}@example.com",
            first_name=username.title(),
            last_name="User",
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
