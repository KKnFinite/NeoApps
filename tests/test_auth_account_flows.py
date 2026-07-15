from datetime import datetime, timedelta
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import g

from app import create_app
from create_grandmaster import create_grandmaster_user
from app.extensions import db
from app.models import (
    AuthRateLimitState,
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    PortalAppAccess,
    User,
    UserToken,
)
from app.services.access_control import (
    DEFAULT_NEONODES,
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
    user_can_access_node,
)
from app.services.auth_session_security import (
    AUTH_SESSION_VERSION_SESSION_KEY,
    FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY,
    FORCED_PASSWORD_CHANGE_SESSION_KEY,
    FORCED_PASSWORD_CHANGE_SESSION_TTL_SECONDS,
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
        self.assertFalse(user.password_policy_update_required)

    def test_account_registration_enforces_shared_password_policy(self):
        rejected_cases = (
            ("elevenchars", "elevenchars", b"at least 12 characters"),
            ("x" * 129, "x" * 129, b"no more than 128 characters"),
            (" " * 12, " " * 12, b"only of whitespace"),
            ("twelvechars!", "different value", b"Passwords do not match"),
            ("password123!", "password123!", b"commonly compromised"),
            ("neogateway passphrase", "neogateway passphrase", b"NeoApps or NeoGateway"),
            ("safe-new@example.com-password", "safe-new@example.com-password", b"account information"),
            ("safe-E12345-passphrase", "safe-E12345-passphrase", b"account information"),
        )

        for password, confirm_password, message in rejected_cases:
            with self.subTest(password=password):
                response = self.client.post(
                    "/create-account",
                    data=self._account_form(
                        password=password,
                        confirm_password=confirm_password,
                    ),
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.data)

        accepted = self.client.post(
            "/create-account",
            data=self._account_form(
                email="passphrase@example.com",
                employee_id="PASS12",
                password="violet river lantern",
                confirm_password="violet river lantern",
            ),
        )

        user = User.query.filter_by(email="passphrase@example.com").one()
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(user.check_password("violet river lantern"))

        exact_minimum = self.client.post(
            "/create-account",
            data=self._account_form(
                email="minimum@example.com",
                employee_id="MIN12",
                password="twelvechars!",
                confirm_password="twelvechars!",
            ),
        )
        self.assertEqual(exact_minimum.status_code, 200)

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
        self.client.post("/logout")
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

    def test_login_rate_limit_applies_to_repeated_failures_from_one_ip(self):
        self._configure_rate_limits(
            login_ip_max_failures=2,
            login_identifier_max_failures=10,
        )

        first = self._login_attempt(
            "unknown-one",
            "wrong password",
            remote_addr="198.51.100.10",
        )
        second = self._login_attempt(
            "unknown-two",
            "wrong password",
            remote_addr="198.51.100.10",
        )
        with patch("app.auth.routes._find_user_by_login", return_value=None) as find_user:
            limited = self._login_attempt(
                "unknown-three",
                "wrong password",
                remote_addr="198.51.100.10",
            )

        self.assertEqual(first.status_code, 401)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(limited.status_code, 401)
        self.assertIn(b"Invalid email or password", limited.data)
        self.assertEqual(find_user.call_count, 0)
        self.assertTrue(
            AuthRateLimitState.query.filter_by(action="login", subject_type="ip")
            .one()
            .blocked_until
        )

    def test_login_rate_limit_applies_to_one_identifier_across_ips(self):
        user = self._user("shared_login", verified=True)
        db.session.commit()
        self._configure_rate_limits(
            login_ip_max_failures=10,
            login_identifier_max_failures=2,
        )

        self._login_attempt(user.username, "wrong password", remote_addr="198.51.100.11")
        self._login_attempt(user.username, "wrong password", remote_addr="198.51.100.12")
        limited = self._login_attempt(
            user.username,
            "Password123!",
            remote_addr="198.51.100.13",
        )

        self.assertEqual(limited.status_code, 401)
        self.assertIn(b"Invalid email or password", limited.data)

    def test_successful_login_clears_failure_state(self):
        user = self._user("clear_login", verified=True)
        db.session.commit()
        self._configure_rate_limits(
            login_ip_max_failures=10,
            login_identifier_max_failures=10,
        )

        self._login_attempt(user.username, "wrong password", remote_addr="198.51.100.14")
        successful = self._login_attempt(
            user.username,
            "Password123!",
            remote_addr="198.51.100.14",
        )

        self.assertEqual(successful.status_code, 302)
        self.assertEqual(
            AuthRateLimitState.query.filter_by(action="login").count(),
            0,
        )

    def test_login_cooldown_expires_without_permanent_lockout(self):
        user = self._user("temporary_login_limit", verified=True)
        db.session.commit()
        self._configure_rate_limits(
            login_ip_max_failures=1,
            login_identifier_max_failures=10,
        )

        self._login_attempt(user.username, "wrong password", remote_addr="198.51.100.15")
        state = AuthRateLimitState.query.filter_by(
            action="login",
            subject_type="ip",
        ).one()
        state.blocked_until = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        successful = self._login_attempt(
            user.username,
            "Password123!",
            remote_addr="198.51.100.15",
        )

        self.assertEqual(successful.status_code, 302)

    def test_forgot_password_rate_limit_applies_to_repeated_requests_from_one_ip(self):
        user = self._user("reset_ip_limit", email="reset-ip@example.com", verified=True)
        db.session.commit()
        self._configure_rate_limits(
            password_reset_ip_max_attempts=2,
            password_reset_identifier_max_attempts=10,
        )

        with patch(
            "app.auth.routes.email_service.send_password_reset",
            return_value={"sent": False, "reason": "test"},
        ) as send_reset:
            responses = [
                self._forgot_password_attempt(
                    user.email,
                    remote_addr="198.51.100.16",
                )
                for _ in range(3)
            ]

        self.assertEqual(send_reset.call_count, 2)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(
            AuthRateLimitState.query.filter_by(
                action="password_reset",
                subject_type="ip",
            )
            .one()
            .blocked_until
        )

    def test_forgot_password_rate_limit_applies_to_one_email_across_ips(self):
        user = self._user("reset_identifier_limit", email="reset-email@example.com", verified=True)
        db.session.commit()
        self._configure_rate_limits(
            password_reset_ip_max_attempts=10,
            password_reset_identifier_max_attempts=2,
        )

        with patch(
            "app.auth.routes.email_service.send_password_reset",
            return_value={"sent": False, "reason": "test"},
        ) as send_reset:
            self._forgot_password_attempt(user.email, remote_addr="198.51.100.17")
            self._forgot_password_attempt(user.email, remote_addr="198.51.100.18")
            limited = self._forgot_password_attempt(
                user.email,
                remote_addr="198.51.100.19",
            )

        self.assertEqual(send_reset.call_count, 2)
        self.assertEqual(limited.status_code, 200)
        self.assertIn(b"If an account exists", limited.data)

    def test_login_and_forgot_password_responses_do_not_reveal_account_existence(self):
        user = self._user("response_privacy", email="response@example.com", verified=True)
        db.session.commit()

        existing_login = self._login_attempt(
            user.username,
            "wrong password",
            remote_addr="198.51.100.20",
        )
        missing_login = self._login_attempt(
            "missing-response-user",
            "wrong password",
            remote_addr="198.51.100.21",
        )
        with patch(
            "app.auth.routes.email_service.send_password_reset",
            return_value={"sent": False, "reason": "test"},
        ):
            existing_reset = self._forgot_password_attempt(
                user.email,
                remote_addr="198.51.100.22",
            )
            missing_reset = self._forgot_password_attempt(
                "missing-response@example.com",
                remote_addr="198.51.100.23",
            )

        self.assertEqual(existing_login.status_code, missing_login.status_code)
        self.assertEqual(existing_login.data, missing_login.data)
        self.assertEqual(existing_reset.status_code, missing_reset.status_code)
        self.assertEqual(existing_reset.data, missing_reset.data)

    def test_forwarded_client_ip_is_used_only_for_a_configured_trusted_proxy(self):
        self._configure_rate_limits(
            login_ip_max_failures=1,
            login_identifier_max_failures=10,
        )
        untrusted_proxy = "198.51.100.24"

        with patch("app.auth.routes._find_user_by_login", return_value=None) as find_user:
            self._login_attempt(
                "proxy-one",
                "wrong password",
                remote_addr=untrusted_proxy,
                forwarded_for="203.0.113.10",
            )
            self._login_attempt(
                "proxy-two",
                "wrong password",
                remote_addr=untrusted_proxy,
                forwarded_for="203.0.113.11",
            )

        self.assertEqual(find_user.call_count, 1)

        AuthRateLimitState.query.delete()
        self.app.config.update(
            AUTH_TRUST_PROXY_HEADERS=True,
            AUTH_TRUSTED_PROXY_IPS=(untrusted_proxy,),
        )
        with patch("app.auth.routes._find_user_by_login", return_value=None) as find_user:
            self._login_attempt(
                "trusted-proxy-one",
                "wrong password",
                remote_addr=untrusted_proxy,
                forwarded_for="203.0.113.12",
            )
            self._login_attempt(
                "trusted-proxy-two",
                "wrong password",
                remote_addr=untrusted_proxy,
                forwarded_for="203.0.113.13",
            )

        self.assertEqual(find_user.call_count, 2)

    def test_reset_change_and_emergency_password_writers_enforce_shared_policy(self):
        user = self._user("writer", email="writer@example.com", verified=True)
        target = self._user("writer_target", email="target@example.com", verified=True)
        grandmaster = self._admin("writer_admin", "grandmaster")
        raw_token, _token_record = create_user_token(user, PASSWORD_RESET)
        db.session.commit()

        reset_response = self.client.post(
            f"/reset-password/{raw_token}",
            data={"password": "password123!", "confirm_password": "password123!"},
        )

        self._login(user.username)
        change_response = self.client.post(
            "/change-password",
            data={
                "current_password": "Password123!",
                "password": "password123!",
                "confirm_password": "password123!",
            },
        )
        self.client.post("/logout")

        self._login(grandmaster.username)
        emergency_response = self.client.post(
            f"/admin/users/{target.id}/emergency-reset",
            data={
                "reason": "Support call.",
                "password": "password123!",
                "confirm_password": "password123!",
            },
        )

        self.assertEqual(reset_response.status_code, 400)
        self.assertEqual(change_response.status_code, 400)
        self.assertEqual(emergency_response.status_code, 400)
        self.assertTrue(user.check_password("Password123!"))
        self.assertTrue(target.check_password("Password123!"))

    def test_existing_user_policy_update_waits_for_login_then_blocks_until_changed(self):
        user, _membership = self._approved_user("policy_user", "policy@example.com")
        user.password_policy_update_required = True
        db.session.commit()

        login_response = self._login(user.username)
        with self.client.session_transaction() as login_session:
            self.assertEqual(
                login_session[FORCED_PASSWORD_CHANGE_SESSION_KEY], user.id
            )
            self.assertIn(
                FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY,
                login_session,
            )
        blocked_response = self.client.get("/motherbrain", follow_redirects=False)
        changed_response = self.client.post(
            "/change-password",
            data={
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
            follow_redirects=False,
        )

        updated = db.session.get(User, user.id)
        self.assertEqual(login_response.location, "/change-password")
        self.assertEqual(blocked_response.location, "/change-password")
        self.assertEqual(changed_response.location, "/portal")
        self.assertFalse(updated.password_policy_update_required)
        self.assertFalse(updated.password_reset_required)
        self.assertTrue(updated.password_changed_at)
        self.assertTrue(updated.check_password("violet river lantern"))
        with self.client.session_transaction() as changed_session:
            self.assertNotIn(FORCED_PASSWORD_CHANGE_SESSION_KEY, changed_session)
            self.assertNotIn(
                FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY,
                changed_session,
            )

    def test_existing_session_cannot_bypass_forced_change_after_flag_is_set(self):
        user, _membership = self._approved_user("active_session", "active@example.com")
        db.session.commit()
        self._login(user.username)

        user.password_policy_update_required = True
        db.session.commit()
        blocked_response = self.client.get("/portal", follow_redirects=False)
        bypass_response = self.client.post(
            "/change-password",
            data={
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
        )
        changed_response = self.client.post(
            "/change-password",
            data={
                "current_password": "Password123!",
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
            follow_redirects=False,
        )

        self.assertEqual(blocked_response.location, "/change-password")
        self.assertEqual(bypass_response.status_code, 400)
        self.assertIn(b"Current password is incorrect", bypass_response.data)
        self.assertEqual(changed_response.location, "/portal")
        self.assertTrue(db.session.get(User, user.id).check_password("violet river lantern"))

    def test_emergency_reset_invalidates_existing_user_sessions(self):
        grandmaster = self._admin("session_reset_admin", "grandmaster")
        target = self._user("session_reset_target", verified=True)
        db.session.commit()
        target_id = target.id
        initial_session_version = target.auth_session_version

        active_login = self._login(target.username)
        with self.client.session_transaction() as active_session:
            self.assertEqual(
                active_session[AUTH_SESSION_VERSION_SESSION_KEY],
                initial_session_version,
            )
            stale_target_session = dict(active_session)

        self.client.post("/logout")
        self._login(grandmaster.username)
        reset_response = self.client.post(
            f"/admin/users/{target_id}/emergency-reset",
            data={
                "reason": "Security response.",
                "password": "twilight harbor signal",
                "confirm_password": "twilight harbor signal",
            },
            follow_redirects=False,
        )
        db.session.expire_all()
        updated_target = db.session.get(User, target_id)
        with self.client.session_transaction() as active_session:
            active_session.clear()
            active_session.update(stale_target_session)
            self.assertEqual(
                active_session[AUTH_SESSION_VERSION_SESSION_KEY],
                initial_session_version,
            )
        g.pop("_login_user", None)
        invalidated_response = self.client.get(
            "/change-password",
            follow_redirects=False,
        )

        self.assertEqual(active_login.status_code, 302)
        self.assertEqual(reset_response.status_code, 302)
        self.assertEqual(
            reset_response.location,
            f"/portal/manage/users/{target_id}",
        )
        self.assertEqual(
            updated_target.auth_session_version,
            initial_session_version + 1,
        )
        self.assertEqual(invalidated_response.location, "/login")
        with self.client.session_transaction() as invalidated_session:
            self.assertNotIn(AUTH_SESSION_VERSION_SESSION_KEY, invalidated_session)
            self.assertNotIn(FORCED_PASSWORD_CHANGE_SESSION_KEY, invalidated_session)

    def test_forced_change_marker_is_cleared_on_logout(self):
        user = self._user("forced_marker_logout", verified=True)
        user.password_reset_required = True
        db.session.commit()

        login_response = self._login(user.username)
        with self.client.session_transaction() as login_session:
            self.assertIn(FORCED_PASSWORD_CHANGE_SESSION_KEY, login_session)

        logout_response = self.client.post("/logout", follow_redirects=False)
        with self.client.session_transaction() as logged_out_session:
            self.assertNotIn(AUTH_SESSION_VERSION_SESSION_KEY, logged_out_session)
            self.assertNotIn(FORCED_PASSWORD_CHANGE_SESSION_KEY, logged_out_session)
            self.assertNotIn(
                FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY,
                logged_out_session,
            )

        self.assertEqual(login_response.location, "/change-password")
        self.assertEqual(logout_response.location, "/login")

    def test_expired_forced_change_marker_requires_current_password(self):
        user = self._user("expired_forced_marker", verified=True)
        user.password_reset_required = True
        db.session.commit()

        self._login(user.username)
        with self.client.session_transaction() as login_session:
            login_session[FORCED_PASSWORD_CHANGE_AUTHENTICATED_AT_SESSION_KEY] -= (
                FORCED_PASSWORD_CHANGE_SESSION_TTL_SECONDS + 1
            )
        response = self.client.post(
            "/change-password",
            data={
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Current password is incorrect", response.data)

    def test_voluntary_password_change_requires_current_password(self):
        user = self._user("voluntary", email="voluntary@example.com", verified=True)
        db.session.commit()
        self._login(user.username)

        missing_current = self.client.post(
            "/change-password",
            data={
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
        )
        changed = self.client.post(
            "/change-password",
            data={
                "current_password": "Password123!",
                "password": "violet river lantern",
                "confirm_password": "violet river lantern",
            },
            follow_redirects=False,
        )

        self.assertEqual(missing_current.status_code, 400)
        self.assertIn(b"Current password is incorrect", missing_current.data)
        self.assertEqual(changed.location, "/portal")
        self.assertTrue(db.session.get(User, user.id).check_password("violet river lantern"))

    def test_grandmaster_creation_uses_shared_password_policy(self):
        with self.assertRaisesRegex(ValueError, "commonly compromised"):
            create_grandmaster_user(
                "policy_grandmaster",
                "password123!",
                "password123!",
                app=self.app,
            )

        created = create_grandmaster_user(
            "policy_grandmaster",
            "twilight harbor signal",
            "twilight harbor signal",
            app=self.app,
        )

        self.assertEqual(created.username, "policy_grandmaster")
        self.assertTrue(
            User.query.filter_by(username="policy_grandmaster").one().check_password(
                "twilight harbor signal"
            )
        )

    def test_emergency_reset_updates_password_changed_at(self):
        grandmaster = self._admin("timestamp_admin", "grandmaster")
        target = self._user("timestamp_target", verified=True)
        self.assertIsNone(target.password_changed_at)
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.post(
            f"/admin/users/{target.id}/emergency-reset",
            data={
                "reason": "Support call.",
                "password": "twilight harbor signal",
                "confirm_password": "twilight harbor signal",
            },
            follow_redirects=False,
        )

        updated = db.session.get(User, target.id)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(updated.password_reset_required)
        self.assertTrue(updated.password_changed_at)

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
        self.client.post("/logout")

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
        self.client.post("/logout")

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
        self.assertIn(b'minlength="12"', response.data)
        self.assertIn(b'maxlength="128"', response.data)
        self.assertIn(b"Common or compromised passwords are not allowed.", response.data)
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
        self.client.post("/logout")
        legacy_username_login = self.client.post(
            "/login",
            data={"username": "loginchoice", "password": "Password123!"},
            follow_redirects=False,
        )
        self.client.post("/logout")
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
        backfill_default_gateway_node_roles(user, role="simulator")
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
        self.assertIn('<span class="portal-header-word neo-menu-text">PORTAL</span>', html)
        self.assertIn('class="character-switcher-trigger neo-menu-text"', html)
        self.assertIn("Change Characters", html)
        self.assertIn("character-switcher-link node-motherbrain", html)
        self.assertIn("character-switcher-label neo-menu-text", html)
        self.assertNotIn("portal-dashboard-logo", html)
        self.assertNotIn("Choose an approved", html)
        self.assertNotIn("Gateway operations and NeoNode systems.", app_card_section)
        self.assertNotIn("Staffing operations and workforce planning.", app_card_section)
        self.assertNotIn("Bid tools placeholder for future buildout.", app_card_section)
        self.assertIn('class="portal-app-icon"', app_card_section)
        self.assertIn('src="/static/images/icons/neogateway/inapp/neogateway-inapp-128.png"', app_card_section)
        self.assertIn('src="/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png"', app_card_section)
        self.assertIn('class="portal-app-icon portal-app-icon-fallback node-bid"', app_card_section)
        self.assertNotIn('src="/static/images/icons/neobid/icon_192.png"', app_card_section)
        self.assertIn(b"NeoGateway", response.data)
        self.assertIn(b"portal-app-title neo-brand-title", response.data)
        self.assertIn(b"neo-brand-title__node--gateway", response.data)
        self.assertIn(b"neo-brand-title__node--staffing", response.data)
        self.assertIn(b"neo-brand-title__node--bid", response.data)
        self.assertIn(b"neo-brand--gateway", response.data)
        self.assertIn(b"neo-brand__neo neo-word", response.data)
        self.assertIn(b"neo-brand__node node-word", response.data)
        self.assertNotIn(b"<h2>NeoGateway</h2>", response.data)
        self.assertIn(b"Approved", response.data)
        self.assertIn(b"Launch", response.data)
        self.assertNotIn(b">OPEN</a>", response.data)
        self.assertIn(b'href="/rfd"', response.data)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"PENDING", response.data)
        self.assertIn(b"NeoBid", response.data)
        self.assertIn(b"REQUEST ACCESS", response.data)
        self.assertNotIn('class="action-row"', html)
        self.assertNotIn(">LOGOUT</a>", html)
        self.assertNotIn('class="portal-install-section"', html)
        self.assertNotIn("data-install-button", html)
        self.assertNotIn("beforeinstallprompt", html)

    def test_portal_desktop_branding_css_widens_cards_and_scopes_neofont_menu_text(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".portal-app-grid {\n        grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));", css)
        self.assertIn(".portal-header-management-link", css)
        self.assertIn("background: transparent;", css)
        self.assertIn("box-shadow: none;", css)
        self.assertIn(".portal-app-icon-fallback", css)
        self.assertIn(".neo-menu-text", css)
        self.assertIn('font-family: "NeoFont", Arial, sans-serif;', css)
        self.assertNotIn("body {\n  font-family: \"NeoFont\"", css)

    def test_portal_management_renders_in_top_banner_for_grandmaster(self):
        user = self._admin("portalmenu", "grandmaster")
        db.session.commit()

        self._login(user.username)
        response = self.client.get("/portal")
        html = response.get_data(as_text=True)
        topbar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="portal-header-management-link neo-menu-text"', topbar)
        self.assertLess(topbar.index("Portal Management"), topbar.index("Change Characters"))
        self.assertNotIn('class="action-row"', html)
        self.assertNotIn(">LOGOUT</a>", html)

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
        self.assertIn('class="portal-app-icon portal-app-icon-fallback node-bid"', app_card_section)
        self.assertNotIn('src="/static/images/icons/neobid/icon_192.png"', app_card_section)
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

    def _login_attempt(self, identifier, password, *, remote_addr, forwarded_for=None):
        headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else None
        return self.client.post(
            "/login",
            data={"username": identifier, "password": password},
            headers=headers,
            environ_overrides={"REMOTE_ADDR": remote_addr},
            follow_redirects=False,
        )

    def _forgot_password_attempt(self, email, *, remote_addr):
        return self.client.post(
            "/forgot-password",
            data={"email": email},
            environ_overrides={"REMOTE_ADDR": remote_addr},
        )

    def _configure_rate_limits(
        self,
        *,
        login_ip_max_failures=10,
        login_identifier_max_failures=5,
        password_reset_ip_max_attempts=5,
        password_reset_identifier_max_attempts=3,
    ):
        self.app.config.update(
            AUTH_RATE_LIMIT_ENABLED=True,
            AUTH_LOGIN_WINDOW_SECONDS=900,
            AUTH_LOGIN_IP_MAX_FAILURES=login_ip_max_failures,
            AUTH_LOGIN_IDENTIFIER_MAX_FAILURES=login_identifier_max_failures,
            AUTH_LOGIN_BASE_COOLDOWN_SECONDS=30,
            AUTH_LOGIN_MAX_COOLDOWN_SECONDS=900,
            AUTH_PASSWORD_RESET_WINDOW_SECONDS=3600,
            AUTH_PASSWORD_RESET_IP_MAX_ATTEMPTS=password_reset_ip_max_attempts,
            AUTH_PASSWORD_RESET_IDENTIFIER_MAX_ATTEMPTS=(
                password_reset_identifier_max_attempts
            ),
            AUTH_PASSWORD_RESET_BASE_COOLDOWN_SECONDS=300,
            AUTH_PASSWORD_RESET_MAX_COOLDOWN_SECONDS=3600,
            AUTH_TRUST_PROXY_HEADERS=False,
            AUTH_TRUSTED_PROXY_IPS=(),
        )


if __name__ == "__main__":
    unittest.main()
