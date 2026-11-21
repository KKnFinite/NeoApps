from datetime import date, datetime
import re
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import SortDateOperation, User
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
from app.services.gateway_matrix import save_gateway_matrix
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from tests.test_neorain_sort_refresh import configure_sort_window


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
        configure_sort_window(self.gateway, date(2026, 11, 19))
        save_gateway_matrix(self.gateway, {("thursday", "night")})
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 20, 21, 30)
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

    def test_authenticated_manual_test_sort_archive_and_cutover(self):
        self._login("grandmaster")
        operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code="RFD",
                                      sort_date=date(2026, 11, 20), sort_name="night",
                                      generated_by_user_id=User.query.filter_by(role="grandmaster").one().id)
        db.session.add(operation)
        db.session.commit()
        path = f"/motherbrain/operations/{operation.id}"
        control = path + "/archive-manual-test"
        self._login("operator")
        self.client.post(control, data={"reason": "Fixture"})
        self.assertIsNone(operation.archived_at_utc)
        self._login("grandmaster")
        self.assertIn(control.encode(), self.client.get(path).data)
        response = self.client.post(control, data={"reason": "Fixture"}, follow_redirects=True)
        self.assertIn(b"Test Sort archived.", response.data)
        self.assertIsNotNone(operation.archived_at_utc)
        self.assertNotIn(control.encode(), response.data)
        result = self.client.post("/motherbrain/system-settings/integrations", data={
            "action": "set_neorain_mode", "integration_mode": NEO_ONLY,
        }, follow_redirects=True)
        self.assertIn(b"NeoRain integration mode is now NEO ONLY", result.data)
        self.assertIn(b'data-neorain-integration-mode="neo_only"', self.client.get("/neorain/outbound").data)

    def test_viewer_sees_disabled_selector_and_cannot_change_mode(self):
        self._login("operator")
        page = self.client.get("/motherbrain/system-settings/integrations")
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

    def test_grandmaster_cannot_change_mode_through_either_url_during_sort(self):
        self._login("grandmaster")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 20, 0, 30)
        for url in ("/motherbrain/system-settings", "/motherbrain/system-settings/integrations"):
            with self.subTest(url=url):
                with patch("app.services.google_motherbrain_live_poll_execution.google_motherbrain_live_poll_preflight",
                           return_value={"status": "eligible"}):
                    response = self.client.post(url, data={"action": "set_neorain_mode", "integration_mode": NEO_ONLY})
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(b"data-google-live-poll-heartbeat", response.data)
                self.assertIn(b"cannot change during configured Sort operating hours", response.data)
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

    def test_mirror_switch_is_separate_default_off_scoped_and_audited(self):
        from app.models.motherbrain_google_integration_setting import NeoRainIntegrationEvent
        from app.services.google_rain_integration_mode import NEO_PRIMARY_GOOGLE_MIRROR, rain_integration_status
        url = "/motherbrain/system-settings/integrations"
        enable = {"action": "set_neorain_google_mirror_writes", "enabled": "1"}
        self._login("operator")
        self.assertEqual(self.client.post(url, data=enable).status_code, 403)
        self.assertEqual(NeoRainIntegrationEvent.query.count(), 0)
        self._login("grandmaster")
        for mode in (GOOGLE_PRIMARY, NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY):
            self.client.post(url, data={"action": "set_neorain_mode", "integration_mode": mode})
            page = self.client.get(url)
            self.assertEqual(page.status_code, 200)
            self.assertIn(b"data-rain-mirror-state>OFF", page.data)
            self.assertIn(b'value="1" disabled aria-describedby="rain-mirror-reason"', page.data)
            response = self.client.post(url, data=enable)
            self.assertEqual(response.status_code, 400)
            state = rain_integration_status(self.gateway, "night")
            self.assertEqual(state["mode"], mode)
            self.assertFalse(state["mirror_requested"])
            self.assertIn(state["mirror_blocker"].encode(), response.data)
            event = state["last_activation_refusal"]
            self.assertEqual(event.actor_user_id, User.query.filter_by(role="grandmaster").one().id)
            self.assertEqual((event.mode, event.previous_writes, event.writes), (mode, False, False))
            self.assertIsNone(rain_integration_status(self.gateway, "day")["last_activation_refusal"])
        self.assertEqual(NeoRainIntegrationEvent.query.filter_by(action="mirror_rejected").count(), 3)

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
