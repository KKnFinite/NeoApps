from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import PermissionRule, User, UserToken
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.services.user_tokens import (
    EMAIL_VERIFICATION,
    PASSWORD_RESET,
    create_user_token,
    get_valid_token_record,
)


RESEND_PERMISSION = "neoapps.email_verification.resend"
USER_MANAGEMENT_VIEW_PERMISSION = "neoapps.user_management.view"
DEFAULT_EMAIL = object()


class EmailVerificationResendTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "email-verification-resend-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "APP_BASE_URL": "https://neoapps.example.test",
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

    def test_permission_defaults_to_grandmaster_and_renders_in_permission_rules(self):
        administrator = self._user("permission_grandmaster", role="grandmaster", verified=True)
        backfill_default_gateway_node_roles(administrator, role="grandmaster")
        db.session.commit()
        self._login(administrator.username)

        rule = PermissionRule.query.filter_by(permission_key=RESEND_PERMISSION).one()
        response = self.client.get("/admin/permissions")

        self.assertEqual(rule.minimum_role, "grandmaster")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Resend Verification Email", response.data)
        self.assertIn(RESEND_PERMISSION.encode(), response.data)

    def test_unverified_detail_shows_button_only_to_authorized_administrators(self):
        administrator = self._user("detail_grandmaster", role="grandmaster", verified=True)
        master = self._user("detail_master", role="master", verified=True)
        unverified = self._user("unverified_detail", verified=False)
        verified = self._user("verified_detail", verified=True)
        PermissionRule.query.filter_by(
            permission_key=USER_MANAGEMENT_VIEW_PERMISSION
        ).one().minimum_role = "master"
        db.session.commit()

        self._login(administrator.username)
        authorized = self.client.get(f"/admin/users/{unverified.id}")
        verified_response = self.client.get(f"/admin/users/{verified.id}")
        self.client.post("/logout")
        self._login(master.username)
        unauthorized = self.client.get(f"/admin/users/{unverified.id}")

        self.assertIn(b"RESEND VERIFICATION EMAIL", authorized.data)
        self.assertNotIn(b"RESEND VERIFICATION EMAIL", verified_response.data)
        self.assertEqual(unauthorized.status_code, 200)
        self.assertNotIn(b"RESEND VERIFICATION EMAIL", unauthorized.data)

    def test_authorized_resend_revokes_old_verification_tokens_and_sends_once(self):
        administrator = self._user("resend_grandmaster", role="grandmaster", verified=True)
        target = self._user("resend_target", verified=False)
        old_one, old_one_record = create_user_token(target, EMAIL_VERIFICATION)
        old_two, old_two_record = create_user_token(target, EMAIL_VERIFICATION)
        reset_token, _reset_record = create_user_token(target, PASSWORD_RESET)
        db.session.commit()
        self._login(administrator.username)

        with patch(
            "app.auth.routes.email_service.send_email_verification",
            return_value={"sent": True},
        ) as send_verification:
            response = self.client.post(
                f"/admin/users/{target.id}/resend-verification",
                follow_redirects=True,
            )

        sent_token = send_verification.call_args.args[1]
        tokens = UserToken.query.filter_by(
            user_id=target.id,
            token_type=EMAIL_VERIFICATION,
        ).order_by(UserToken.id).all()
        active_tokens = [token for token in tokens if not token.is_used]

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Verification email sent.", response.data)
        self.assertEqual(send_verification.call_count, 1)
        self.assertEqual(len(active_tokens), 1)
        self.assertNotEqual(active_tokens[0].token_hash, sent_token)
        self.assertTrue(db.session.get(UserToken, old_one_record.id).is_used)
        self.assertTrue(db.session.get(UserToken, old_two_record.id).is_used)
        self.assertIsNone(get_valid_token_record(old_one, EMAIL_VERIFICATION))
        self.assertIsNone(get_valid_token_record(old_two, EMAIL_VERIFICATION))
        self.assertIsNotNone(get_valid_token_record(sent_token, EMAIL_VERIFICATION))
        self.assertIsNotNone(get_valid_token_record(reset_token, PASSWORD_RESET))
        self.assertIsNone(db.session.get(User, target.id).email_verified_at)

    def test_admin_route_aliases_share_the_resend_behavior(self):
        administrator = self._user("alias_grandmaster", role="grandmaster", verified=True)
        target = self._user("alias_target", verified=False)
        db.session.commit()
        self._login(administrator.username)

        with patch(
            "app.auth.routes.email_service.send_email_verification",
            return_value={"sent": True},
        ) as send_verification:
            response = self.client.post(
                f"/portal/manage/users/{target.id}/resend-verification",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(send_verification.call_count, 1)
        self.assertEqual(
            UserToken.query.filter_by(
                user_id=target.id,
                token_type=EMAIL_VERIFICATION,
            ).count(),
            1,
        )

    def test_unauthorized_post_creates_no_token_and_sends_no_email(self):
        master = self._user("resend_master", role="master", verified=True)
        target = self._user("unauthorized_target", verified=False)
        db.session.commit()
        self._login(master.username)

        with patch("app.auth.routes.email_service.send_email_verification") as send_verification:
            response = self.client.post(
                f"/admin/users/{target.id}/resend-verification",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")
        self.assertEqual(
            UserToken.query.filter_by(
                user_id=target.id,
                token_type=EMAIL_VERIFICATION,
            ).count(),
            0,
        )
        send_verification.assert_not_called()

    def test_resend_permission_can_be_lowered_to_master_independently(self):
        master = self._user("lowered_master", role="master", verified=True)
        target = self._user("lowered_target", verified=False)
        PermissionRule.query.filter_by(permission_key=RESEND_PERMISSION).one().minimum_role = "master"
        PermissionRule.query.filter_by(
            permission_key=USER_MANAGEMENT_VIEW_PERMISSION
        ).one().minimum_role = "master"
        db.session.commit()
        self._login(master.username)

        with patch(
            "app.auth.routes.email_service.send_email_verification",
            return_value={"sent": True},
        ) as send_verification:
            detail = self.client.get(f"/admin/users/{target.id}")
            response = self.client.post(
                f"/admin/users/{target.id}/resend-verification",
                follow_redirects=False,
            )

        self.assertTrue(user_can(RESEND_PERMISSION, master))
        self.assertIn(b"RESEND VERIFICATION EMAIL", detail.data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(send_verification.call_count, 1)

    def test_verified_or_missing_email_user_creates_no_token_and_sends_no_email(self):
        administrator = self._user("no_send_grandmaster", role="grandmaster", verified=True)
        verified = self._user("no_send_verified", verified=True)
        missing_email = self._user("no_send_missing", email=None, verified=False)
        db.session.commit()
        self._login(administrator.username)

        with patch("app.auth.routes.email_service.send_email_verification") as send_verification:
            verified_response = self.client.post(
                f"/admin/users/{verified.id}/resend-verification",
                follow_redirects=True,
            )
            missing_response = self.client.post(
                f"/admin/users/{missing_email.id}/resend-verification",
                follow_redirects=True,
            )

        self.assertIn(b"already verified", verified_response.data)
        self.assertIn(b"has no email address", missing_response.data)
        self.assertEqual(
            UserToken.query.filter_by(user_id=verified.id, token_type=EMAIL_VERIFICATION).count(),
            0,
        )
        self.assertEqual(
            UserToken.query.filter_by(
                user_id=missing_email.id,
                token_type=EMAIL_VERIFICATION,
            ).count(),
            0,
        )
        send_verification.assert_not_called()

    def test_delivery_failure_does_not_claim_success(self):
        administrator = self._user("delivery_grandmaster", role="grandmaster", verified=True)
        target = self._user("delivery_target", verified=False)
        db.session.commit()
        self._login(administrator.username)

        with patch(
            "app.auth.routes.email_service.send_email_verification",
            return_value={"sent": False, "reason": "send_failed"},
        ) as send_verification:
            response = self.client.post(
                f"/admin/users/{target.id}/resend-verification",
                follow_redirects=True,
            )

        self.assertEqual(send_verification.call_count, 1)
        self.assertIn(b"could not be delivered", response.data)
        self.assertNotIn(b"Verification email sent.", response.data)
        self.assertEqual(
            UserToken.query.filter_by(user_id=target.id, token_type=EMAIL_VERIFICATION).count(),
            1,
        )
        self.assertIsNone(db.session.get(User, target.id).email_verified_at)

    def test_new_verification_tokens_default_to_seven_days_while_reset_tokens_keep_one_hour(self):
        user = self._user("expiration_target", verified=False)
        before = datetime.utcnow()
        _verification_token, verification_record = create_user_token(user, EMAIL_VERIFICATION)
        _reset_token, reset_record = create_user_token(user, PASSWORD_RESET)
        after = datetime.utcnow()

        verification_seconds = (verification_record.expires_at - before).total_seconds()
        reset_seconds = (reset_record.expires_at - before).total_seconds()

        self.assertGreaterEqual(verification_seconds, timedelta(hours=168).total_seconds() - 1)
        self.assertLessEqual(verification_record.expires_at, after + timedelta(hours=168, seconds=1))
        self.assertGreaterEqual(reset_seconds, timedelta(hours=1).total_seconds() - 1)
        self.assertLessEqual(reset_record.expires_at, after + timedelta(hours=1, seconds=1))

    def _user(self, username, *, email=DEFAULT_EMAIL, role="watcher", verified=False):
        if email is DEFAULT_EMAIL:
            email = f"{username}@example.test"
        user = User(
            username=username,
            email=email,
            first_name=username.title(),
            last_name="User",
            full_name=username.title(),
            employee_id=f"EMP-{username}",
            role=role,
            is_active=True,
        )
        if verified:
            user.email_verified_at = datetime.utcnow()
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _login(self, username):
        response = self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        return response


if __name__ == "__main__":
    unittest.main()
