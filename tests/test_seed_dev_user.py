from datetime import date
import os
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, SortDateOperation, User
from app.services.access_control import user_can_access_node
from scripts.seed_dev_user import (
    LOCAL_SQLITE_FALLBACK_PASSWORD,
    seed_dev_grandmaster,
)


class SeedDevUserTest(unittest.TestCase):
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

    def test_seed_creates_kessler_grandmaster_with_env_password(self):
        with patch.dict(
            os.environ,
            {"NEOAPPS_DEV_GRANDMASTER_PASSWORD": "SeedPassword123!"},
            clear=False,
        ):
            result = seed_dev_grandmaster(self.app)

        user = User.query.filter_by(username="Kessler").first()
        self.assertTrue(result["created"])
        self.assertFalse(result["used_fallback_password"])
        self.assertEqual(user.role, "grandmaster")
        self.assertTrue(user.is_active)
        self.assertFalse(user.mfa_required)
        self.assertTrue(user.check_password("SeedPassword123!"))
        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))

    def test_seed_updates_existing_user_password_role_and_active_state(self):
        db.create_all()
        user = User(username="Kessler", role="watcher", is_active=False)
        user.set_password("OldPassword123!")
        db.session.add(user)
        db.session.commit()

        with patch.dict(
            os.environ,
            {"NEOAPPS_DEV_GRANDMASTER_PASSWORD": "NewPassword123!"},
            clear=False,
        ):
            result = seed_dev_grandmaster(self.app)

        updated = User.query.filter_by(username="Kessler").first()
        self.assertFalse(result["created"])
        self.assertEqual(updated.role, "grandmaster")
        self.assertTrue(updated.is_active)
        self.assertTrue(updated.check_password("NewPassword123!"))
        self.assertTrue(user_can_access_node(updated, "RFD", "motherbrain", "grandmaster"))

    def test_seed_uses_local_sqlite_fallback_when_env_password_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            result = seed_dev_grandmaster(self.app)

        user = User.query.filter_by(username="Kessler").first()
        self.assertTrue(result["used_fallback_password"])
        self.assertTrue(user.check_password(LOCAL_SQLITE_FALLBACK_PASSWORD))

    def test_seed_backfills_approved_rfd_membership_and_node_roles(self):
        with patch.dict(os.environ, {}, clear=True):
            seed_dev_grandmaster(self.app)

        user = User.query.filter_by(username="Kessler").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()

        self.assertEqual(membership.gateway.code, "RFD")
        self.assertEqual(membership.status, "approved")
        self.assertTrue(membership.is_active)
        self.assertEqual(
            GatewayNodeRole.query.filter_by(
                gateway_membership_id=membership.id,
                role="grandmaster",
                is_active=True,
            ).count(),
            NeoNode.query.filter_by(is_active=True).count(),
        )

    def test_seeded_grandmaster_can_access_motherbrain_pages(self):
        with patch.dict(
            os.environ,
            {"NEOAPPS_DEV_GRANDMASTER_PASSWORD": "SeedPassword123!"},
            clear=False,
        ):
            seed_dev_grandmaster(self.app)

        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        client = self.app.test_client()
        login_response = client.post(
            "/login",
            data={"username": "Kessler", "password": "SeedPassword123!"},
            follow_redirects=False,
        )
        self.assertEqual(login_response.status_code, 302)

        paths = (
            "/motherbrain",
            "/motherbrain/operations",
            "/motherbrain/master-schedule",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
