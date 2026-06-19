import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app import create_app, maybe_auto_bootstrap_database
from app.config import resolve_database_uri
from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, User
from app.services.access_control import DEFAULT_NEONODES, user_can_access_node
from app.services.database_bootstrap import bootstrap_database


class DatabaseBootstrapTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_database_url_env_is_used_when_present(self):
        neon_url = "postgresql://neo_user:dbpass@example.neon.tech/neogateway"

        with patch.dict(os.environ, {"DATABASE_URL": neon_url}, clear=False):
            self.assertEqual(resolve_database_uri(), neon_url)

    def test_sqlite_fallback_is_used_when_database_url_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            database_uri = resolve_database_uri()

        self.assertTrue(database_uri.startswith("sqlite:///"))
        self.assertIn("neoapps.sqlite", database_uri)

    def test_bootstrap_creates_gateway_nodes_admin_membership_and_roles(self):
        result = self._bootstrap()

        user = User.query.filter_by(username="Kessler").first()
        gateway = Gateway.query.filter_by(code="RFD").first()
        membership = GatewayMembership.query.filter_by(
            user_id=user.id,
            gateway_id=gateway.id,
        ).first()

        self.assertEqual(result["username"], "Kessler")
        self.assertEqual(gateway.name, "NeoGateway")
        self.assertTrue(gateway.is_active)
        self.assertEqual(
            {node.code for node in NeoNode.query.filter_by(is_active=True).all()},
            {code for code, _name, _sort_order in DEFAULT_NEONODES},
        )
        self.assertEqual(user.email, "bootstrap-admin@local.neoapps")
        self.assertEqual(user.role, "grandmaster")
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified_at)
        self.assertFalse(user.password_reset_required)
        self.assertTrue(user.check_password("1313"))
        self.assertTrue(result["created_user"])
        self.assertTrue(result["password_applied"])
        self.assertEqual(membership.status, "approved")
        self.assertTrue(membership.is_active)
        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))
        self.assertEqual(
            GatewayNodeRole.query.filter_by(
                gateway_membership_id=membership.id,
                role="grandmaster",
                is_active=True,
            ).count(),
            len(DEFAULT_NEONODES),
        )
        self.assertEqual(
            {
                rule.permission_key: rule.minimum_role
                for rule in PermissionRule.query.order_by(PermissionRule.permission_key).all()
            },
            {
                "neomotherbrain.dashboard.view": "operator",
                "neomotherbrain.flight_api_auto_poll.trigger": "simulator",
                "neomotherbrain.flight_api_review.edit": "simulator",
                "neomotherbrain.flight_api_review.view": "simulator",
                "neomotherbrain.gateway_matrix.view": "operator",
                "neomotherbrain.manage_sort.view": "operator",
                "neomotherbrain.master_schedule.view": "operator",
                "neoermac.building_lineup.edit": "simulator",
                "neoermac.building_lineup.view": "operator",
                "neoermac.door_view.edit": "operator",
                "neoermac.door_view.view": "operator",
                "neoermac.tug_assignments.edit": "master",
                "neoermac.view_outbound.view": "watcher",
                "neosektor.conductor.view": "simulator",
                "neosektor.discharge.edit": "operator",
                "neosektor.discharge.view": "operator",
                "neosektor.driver_routing.view": "watcher",
                "neosektor.ebm.edit": "operator",
                "neosektor.ebm.view": "operator",
                "neosektor.tunnel_conductor.edit": "simulator",
                "neosektor.wbm.edit": "operator",
                "neosektor.wbm.view": "operator",
            },
        )

    def test_bootstrap_updates_existing_kessler_user_without_overwriting_password(self):
        db.create_all()
        user = User(
            username="Kessler",
            email="old@example.com",
            role="watcher",
            is_active=False,
        )
        user.set_password("OldPassword123!")
        db.session.add(user)
        db.session.commit()

        with patch.dict(
            os.environ,
            {
                "BOOTSTRAP_ADMIN_EMAIL": "bootstrap@example.com",
                "BOOTSTRAP_ADMIN_PASSWORD": "NewBootstrapPassword123!",
            },
            clear=False,
        ):
            bootstrap_database(self.app)

        updated = User.query.filter_by(username="Kessler").first()
        self.assertEqual(User.query.count(), 1)
        self.assertEqual(updated.email, "bootstrap@example.com")
        self.assertEqual(updated.role, "grandmaster")
        self.assertTrue(updated.is_active)
        self.assertTrue(updated.email_verified_at)
        self.assertTrue(updated.check_password("OldPassword123!"))
        self.assertFalse(updated.check_password("NewBootstrapPassword123!"))
        self.assertTrue(user_can_access_node(updated, "RFD", "motherbrain", "grandmaster"))

    def test_bootstrap_can_run_twice_without_duplicates(self):
        first_result = self._bootstrap()
        second_result = self._bootstrap()
        user = User.query.filter_by(username="Kessler").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()

        self.assertEqual(first_result["username"], second_result["username"])
        self.assertTrue(first_result["password_applied"])
        self.assertFalse(second_result["password_applied"])
        self.assertEqual(Gateway.query.filter_by(code="RFD").count(), 1)
        self.assertEqual(NeoNode.query.count(), len(DEFAULT_NEONODES))
        self.assertEqual(PermissionRule.query.count(), 22)
        self.assertEqual(User.query.filter_by(username="Kessler").count(), 1)
        self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).count(), 1)
        self.assertEqual(
            GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(),
            len(DEFAULT_NEONODES),
        )

    def test_non_sqlite_bootstrap_requires_password_env(self):
        TestConfig = type(
            "PostgresConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@example/db",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        postgres_app = create_app(TestConfig)

        with postgres_app.app_context(), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                bootstrap_database(postgres_app)

    def _bootstrap(self):
        with patch.dict(os.environ, {}, clear=True):
            return bootstrap_database(self.app)


class AutoDatabaseBootstrapTest(unittest.TestCase):
    def test_auto_bootstrap_disabled_does_not_run_bootstrap(self):
        with patch.dict(os.environ, self._render_env(), clear=True):
            app = create_app(self._config(auto_bootstrap=False))

        with app.app_context():
            db.create_all()
            self.assertEqual(Gateway.query.count(), 0)
            self.assertEqual(NeoNode.query.count(), 0)
            db.drop_all()

    def test_auto_bootstrap_enabled_runs_bootstrap(self):
        with patch.dict(os.environ, self._render_env(), clear=True):
            with self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True))

        output = "\n".join(logs.output)
        self.assertIn("Auto bootstrap enabled", output)
        self.assertIn("Bootstrap completed", output)

        with app.app_context():
            user = User.query.filter_by(username="Kessler").first()
            gateway = Gateway.query.filter_by(code="RFD").first()
            membership = GatewayMembership.query.filter_by(
                user_id=user.id,
                gateway_id=gateway.id,
            ).first()

            self.assertIsNotNone(user)
            self.assertTrue(user.email_verified_at)
            self.assertEqual(membership.status, "approved")
            self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))
            db.drop_all()

    def test_auto_bootstrap_true_without_database_url_skips_bootstrap(self):
        env = self._render_env()
        env.pop("DATABASE_URL")

        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True))

        self.assertIn("Bootstrap skipped", "\n".join(logs.output))
        with app.app_context():
            db.create_all()
            self.assertEqual(Gateway.query.count(), 0)
            db.drop_all()

    def test_auto_bootstrap_can_run_twice_without_duplicates_or_password_overwrite(self):
        first_env = self._render_env(password="FirstBootstrapPassword123!")
        second_env = self._render_env(password="SecondBootstrapPassword123!")

        with patch.dict(os.environ, first_env, clear=True):
            app = create_app(self._config(auto_bootstrap=True))

        with patch.dict(os.environ, second_env, clear=True):
            maybe_auto_bootstrap_database(app)

        with app.app_context():
            user = User.query.filter_by(username="Kessler").first()
            membership = GatewayMembership.query.filter_by(user_id=user.id).first()

            self.assertEqual(Gateway.query.filter_by(code="RFD").count(), 1)
            self.assertEqual(NeoNode.query.count(), len(DEFAULT_NEONODES))
            self.assertEqual(User.query.filter_by(username="Kessler").count(), 1)
            self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).count(), 1)
            self.assertEqual(
                GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(),
                len(DEFAULT_NEONODES),
            )
            self.assertTrue(user.check_password("FirstBootstrapPassword123!"))
            self.assertFalse(user.check_password("SecondBootstrapPassword123!"))
            db.drop_all()

    def test_auto_bootstrap_does_not_log_secrets(self):
        env = self._render_env(
            database_url="postgresql://neo_user:dbpass-placeholder@example.neon.tech/neogateway",
            password="BootstrapPasswordPlaceholder123!",
        )
        env["BREVO_API_KEY"] = "brevo-api-key-placeholder"

        stdout = io.StringIO()
        with patch.dict(os.environ, env, clear=True):
            with redirect_stdout(stdout), self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True))

        output = stdout.getvalue() + "\n".join(logs.output)
        self.assertIn("Auto bootstrap enabled", output)
        self.assertIn("Bootstrap completed", output)
        self.assertNotIn("BootstrapPasswordPlaceholder123!", output)
        self.assertNotIn("dbpass-placeholder", output)
        self.assertNotIn(env["DATABASE_URL"], output)
        self.assertNotIn("brevo-api-key-placeholder", output)

        with app.app_context():
            db.drop_all()

    def _config(self, auto_bootstrap):
        return type(
            "AutoBootstrapTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": auto_bootstrap,
            },
        )

    def _render_env(
        self,
        database_url="postgresql://neo_user:dbpass@example.neon.tech/neogateway",
        password="BootstrapPassword123!",
    ):
        return {
            "DATABASE_URL": database_url,
            "AUTO_BOOTSTRAP_DATABASE": "true",
            "BOOTSTRAP_ADMIN_USERNAME": "Kessler",
            "BOOTSTRAP_ADMIN_EMAIL": "bootstrap@example.com",
            "BOOTSTRAP_ADMIN_PASSWORD": password,
            "BREVO_API_KEY": "test-brevo-key",
            "MAIL_FROM_NAME": "NeoGateway",
            "MAIL_FROM_EMAIL": "no-reply@example.com",
            "APP_BASE_URL": "https://example.com",
        }


if __name__ == "__main__":
    unittest.main()
