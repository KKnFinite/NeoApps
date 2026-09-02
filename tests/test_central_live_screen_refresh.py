import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app import create_app
from app.extensions import db
from app.models import LiveScreenRefreshSetting, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.live_refresh_guard import (
    enforce_live_refresh_request_cadence,
    reset_live_refresh_guard_for_testing,
)
from app.services.live_screen_refresh import (
    live_screen_refresh_value,
)
from app.services.live_screen_registry import (
    LIVE_SCREEN_REGISTRY,
    registered_live_screen_keys,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class CentralLiveScreenRefreshTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "CentralLiveScreenRefreshConfig",
            (),
            {
                "SECRET_KEY": "central-live-refresh-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "LIVE_SCREEN_REFRESH_INTERVAL_MS": 1000,
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

    def test_registry_is_authoritative_and_covers_repeating_operational_pages(self):
        expected_keys = {
            "neomotherbrain.arrival_planning",
            "neomotherbrain.departure_planning",
            "neomotherbrain.parking_plan",
            "neosektor.live_counts",
            "neosektor.tunnel_conductor",
            "neosektor.ebm",
            "neosektor.wbm",
            "neosektor.discharge",
            "neosektor.driver_routing",
            "neoermac.upcoming_pulls",
            "neoermac.building_lineup",
            "neoermac.view_outbound",
            "neoermac.door_view",
            "neoscorpion.fuel_dispatch",
            "neoscorpion.fuel_assignments",
            "neoscorpion.hanzo",
            "neorain.inbound",
            "neorain.outbound",
            "neosubzero.pretreat",
            "neosubzero.outbound",
            "neosubzero.coordinator",
            "neosubzero.ucc",
            "neosubzero.deicer_mobile",
        }
        self.assertEqual(set(registered_live_screen_keys()), expected_keys)
        with self.app.test_request_context():
            unregistered = live_screen_refresh_value(
                self.gateway, "neorain.static-history", fallback_ms=30_000
            )
        self.assertFalse(unregistered.enabled)
        self.assertEqual(unregistered.source, "unregistered")

        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        for screen in LIVE_SCREEN_REGISTRY:
            with self.subTest(screen=screen.screen_key):
                self.assertIn(screen.route_endpoint, endpoints)
                self.assertIn(screen.refresh_endpoint, endpoints)

    def test_central_dashboard_and_refresh_page_render_registered_destinations(self):
        self._login("grandmaster")

        dashboard = self.client.get("/motherbrain/system-settings")
        timings = self.client.get("/motherbrain/system-settings/node-refresh-timings")

        self.assertEqual(dashboard.status_code, 200)
        for label in (
            b"Node Refresh Timings",
            b"Node Permissions",
            b"Integrations &amp; Migration",
            b"Gateway Matrix",
            b"Sort Timeline",
            b"Manage API",
            b"Portal Manage",
        ):
            self.assertIn(label, dashboard.data)
        self.assertEqual(timings.status_code, 200)
        for key in registered_live_screen_keys():
            self.assertIn(key.encode(), timings.data)

    def test_node_settings_keep_business_controls_but_not_refresh_admin(self):
        expected_operational_copy = {
            "neosektor": "Unload modifiers",
            "neoermac": "OPERATIONAL SETTINGS",
            "neoscorpion": "Fuel Settings Shell",
            "neorain": "GROUND TIME THRESHOLD",
            "neosubzero": "DEICE FLUIDS",
        }
        for node, expected in expected_operational_copy.items():
            with self.subTest(node=node):
                source = Path(
                    f"app/templates/neonodes/{node}/settings.html"
                ).read_text(encoding="utf-8")
                self.assertNotIn("refresh_interval_seconds", source)
                self.assertIn(expected, source)

    def test_sub_five_second_default_is_clamped_and_off_stays_off(self):
        with self.app.test_request_context():
            value = live_screen_refresh_value(self.gateway, "neorain.inbound")
        self.assertEqual(value.effective_interval_ms, 5_000)

        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key="neorain.inbound",
                interval_seconds=0,
            )
        )
        db.session.commit()
        with self.app.test_request_context():
            value = live_screen_refresh_value(self.gateway, "neorain.inbound")
        self.assertFalse(value.enabled)
        self.assertEqual(value.effective_interval_ms, 0)

    def test_legacy_ermac_override_seeds_effective_per_screen_values(self):
        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key="neoermac.all",
                interval_seconds=30,
            )
        )
        db.session.commit()

        with self.app.test_request_context():
            inherited = live_screen_refresh_value(
                self.gateway, "neoermac.upcoming_pulls"
            )
        self.assertEqual(inherited.effective_interval_ms, 30_000)
        self.assertEqual(inherited.source, "legacy_neoermac_override")

        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key="neoermac.upcoming_pulls",
                interval_seconds=10,
            )
        )
        db.session.commit()
        with self.app.test_request_context():
            own = live_screen_refresh_value(self.gateway, "neoermac.upcoming_pulls")
        self.assertEqual(own.effective_interval_ms, 10_000)
        self.assertEqual(own.source, "override")

    def test_server_rejects_modified_client_refresh_burst_with_bounded_guard(self):
        app = Flask(__name__)
        app.config.update(
            TESTING=True,
            TEST_LIVE_REFRESH_RATE_LIMIT_ENABLED=True,
            LIVE_REFRESH_SERVER_MIN_INTERVAL_SECONDS=5,
        )
        app.before_request(enforce_live_refresh_request_cadence)
        app.add_url_rule("/state", "test_state", lambda: {"ok": True})
        reset_live_refresh_guard_for_testing()

        with patch(
            "app.services.live_refresh_guard.live_screen_for_refresh_request",
            return_value=SimpleNamespace(screen_key="neorain.inbound"),
        ):
            client = app.test_client()
            first = client.get("/state")
            second = client.get("/state")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["error"], "Live refresh is cooling down.")
        self.assertIn("Retry-After", second.headers)

    def _login(self, role):
        username = f"central_refresh_{role}"
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Central",
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
