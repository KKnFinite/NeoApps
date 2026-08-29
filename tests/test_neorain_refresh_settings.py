import unittest
from datetime import datetime

from app import create_app
from app.extensions import db
from app.models import LiveScreenRefreshSetting, User
from app.neonodes.neorain.services import (
    NEORAIN_OUTBOUND_REFRESH_KEY,
    neorain_outbound_refresh_status,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainRefreshSettingsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainRefreshSettingsTestConfig",
            (),
            {
                "SECRET_KEY": "neorain-refresh-settings-test-secret",
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

    def test_settings_view_shows_outbound_value_without_edit_control(self):
        self._login("operator")

        response = self.client.get("/neorain/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"LIVE AUTO-REFRESH", response.data)
        self.assertIn(b"neorain.outbound", response.data)
        self.assertIn(b"Render default", response.data)
        self.assertRegex(
            response.data,
            rb'name="refresh_interval_seconds"[^>]*disabled',
        )
        self.assertNotIn(b">SAVE<", response.data)

    def test_viewer_cannot_save_refresh_override(self):
        self._login("operator")

        response = self.client.post(
            "/neorain/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": NEORAIN_OUTBOUND_REFRESH_KEY,
                "refresh_interval_seconds": "10",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(
            LiveScreenRefreshSetting.query.filter_by(
                gateway_id=self.gateway.id,
                screen_key=NEORAIN_OUTBOUND_REFRESH_KEY,
            ).one_or_none()
        )

    def test_grandmaster_saves_only_allowed_outbound_override(self):
        self._login("grandmaster")

        response = self.client.post(
            "/neorain/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": NEORAIN_OUTBOUND_REFRESH_KEY,
                "refresh_interval_seconds": "10",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        setting = LiveScreenRefreshSetting.query.filter_by(
            gateway_id=self.gateway.id,
            screen_key=NEORAIN_OUTBOUND_REFRESH_KEY,
        ).one()
        self.assertEqual(setting.interval_seconds, 10)
        self.assertIn(b"LIVE REFRESH SETTING SAVED", response.data)

    def test_non_rain_screen_key_is_rejected(self):
        self._login("grandmaster")

        response = self.client.post(
            "/neorain/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": "neoscorpion.fuel_dispatch",
                "refresh_interval_seconds": "10",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"select a supported live screen", response.data.lower())
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)

    def test_saved_override_is_consumed_by_outbound_refresh_status(self):
        self._login("grandmaster")
        self.client.post(
            "/neorain/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": NEORAIN_OUTBOUND_REFRESH_KEY,
                "refresh_interval_seconds": "15",
            },
        )

        status = neorain_outbound_refresh_status(self.gateway, operation=None)

        self.assertEqual(status["live_screen_refresh_interval_ms"], 15000)

    def _login(self, role):
        username = f"rain_refresh_{role}"
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="Refresh",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
