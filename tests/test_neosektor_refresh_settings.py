import unittest
from datetime import datetime

from app import create_app
from app.extensions import db
from app.models import LiveScreenRefreshSetting, NeoSektorOperationalSetting, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.neosektor_live_counts import (
    NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
    NEOSEKTOR_REFRESH_KEYS,
    NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
    driver_routing_refresh_status,
    neosektor_refresh_status,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoSektorRefreshSettingsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neosektor-refresh-settings-test-secret",
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
        db.session.add(
            NeoSektorOperationalSetting(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                integration_mode="neo_only",
            )
        )
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_settings_render_all_six_gateway_scoped_screens(self):
        self._login("master")

        response = self.client.get("/neosektor/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Live Auto-Refresh", response.data)
        for screen_key in NEOSEKTOR_REFRESH_KEYS:
            self.assertIn(screen_key.encode(), response.data)

    def test_master_saves_only_supported_screen_override(self):
        self._login("master")

        response = self.client.post(
            "/neosektor/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
                "refresh_interval_seconds": "15",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        setting = LiveScreenRefreshSetting.query.filter_by(
            gateway_id=self.gateway.id,
            screen_key=NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
        ).one()
        self.assertEqual(setting.interval_seconds, 15)
        self.assertIn(b"LIVE REFRESH SETTING SAVED", response.data)
        status = neosektor_refresh_status(
            self.gateway,
            screen_key=NEOSEKTOR_TUNNEL_CONDUCTOR_REFRESH_KEY,
        )
        self.assertEqual(status["live_screen_refresh_interval_ms"], 15_000)

    def test_unsupported_screen_key_is_rejected(self):
        self._login("master")

        response = self.client.post(
            "/neosektor/settings",
            data={
                "action": "save_live_refresh",
                "screen_key": "neorain.outbound",
                "refresh_interval_seconds": "10",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)

    def test_driver_routing_override_preserves_window_wake_metadata(self):
        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key=NEOSEKTOR_DRIVER_ROUTING_REFRESH_KEY,
                interval_seconds=30,
            )
        )
        db.session.commit()

        status = driver_routing_refresh_status(
            self.gateway,
            now=datetime(2026, 6, 18, 12, 0),
        )

        self.assertEqual(status["live_screen_refresh_interval_ms"], 30_000)
        self.assertIn("next_check_seconds", status)

    def _login(self, role):
        username = f"sektor_refresh_{role}"
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Sektor",
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
