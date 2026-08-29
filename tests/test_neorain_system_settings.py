import re
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import User
from app.services.access_control import (
    ensure_default_gateway_and_nodes,
    backfill_default_gateway_node_roles,
)
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    RainIntegrationTransitionError,
    rain_integration_mode,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainSystemSettingsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainSystemSettingsTestConfig",
            (),
            {
                "SECRET_KEY": "neorain-system-settings-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_grandmaster_can_change_rain_mode_through_existing_settings_authority(self):
        self._login("grandmaster")

        response = self.client.post(
            "/motherbrain/system-settings",
            data={"action": "set_neorain_mode", "integration_mode": NEO_ONLY},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoRain integration mode is now NEO ONLY", response.data)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), NEO_ONLY)

    def test_viewer_sees_disabled_selector_and_cannot_change_mode(self):
        self._login("operator")
        page = self.client.get("/motherbrain/system-settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'data-neorain-mode="google_primary"', page.data)
        self.assertIn(b'name="action" value="set_neorain_mode"', page.data)
        self.assertRegex(
            page.data,
            rb'name="integration_mode" value="neo_only"[^>]*disabled',
        )

        denied = self.client.post(
            "/motherbrain/system-settings",
            data={"action": "set_neorain_mode", "integration_mode": NEO_ONLY},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def test_transition_failure_is_safe_and_keeps_previous_mode(self):
        self._login("grandmaster")
        with patch(
            "app.neomotherbrain.routes.change_rain_integration_mode",
            side_effect=RainIntegrationTransitionError("raw provider details"),
        ):
            response = self.client.post(
                "/motherbrain/system-settings",
                data={"action": "set_neorain_mode", "integration_mode": NEO_ONLY},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"NeoRain authority change failed; the previous mode remains active.",
            response.data,
        )
        self.assertNotIn(b"raw provider details", response.data)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def _login(self, role):
        username = f"rain_settings_{role}"
        user = User.query.filter_by(username=username).first()
        if user is None:
            user = User(
                username=username,
                email=f"{username}@example.test",
                role=role,
                is_active=True,
            )
            set_user_password(user, "TestPassword123!")
            db.session.add(user)
            db.session.flush()
            backfill_default_gateway_node_roles(user, role=role)
            db.session.commit()
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
