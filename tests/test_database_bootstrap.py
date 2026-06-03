import os
import unittest
from unittest.mock import patch

from app import create_app
from app.config import resolve_database_uri
from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, User
from app.services.access_control import DEFAULT_NEONODES, user_can_access_node
from scripts.bootstrap_database import bootstrap_database


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
        self.assertEqual(gateway.name, "NeoRFD")
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

    def test_bootstrap_updates_existing_kessler_user(self):
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
        self.assertTrue(updated.check_password("NewBootstrapPassword123!"))
        self.assertTrue(user_can_access_node(updated, "RFD", "motherbrain", "grandmaster"))

    def test_bootstrap_can_run_twice_without_duplicates(self):
        first_result = self._bootstrap()
        second_result = self._bootstrap()
        user = User.query.filter_by(username="Kessler").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()

        self.assertEqual(first_result["username"], second_result["username"])
        self.assertEqual(Gateway.query.filter_by(code="RFD").count(), 1)
        self.assertEqual(NeoNode.query.count(), len(DEFAULT_NEONODES))
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


if __name__ == "__main__":
    unittest.main()
