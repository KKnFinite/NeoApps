from datetime import date, datetime, time, timedelta, timezone
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    MotherBrainGoogleLivePollState,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_motherbrain_live_poll_health import (
    google_motherbrain_live_poll_health,
)
from app.services.google_motherbrain_live_polling import (
    set_google_motherbrain_live_polling_enabled,
)
from app.services.operation_lifecycle import ensure_operational_sort_operations
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class GoogleMotherBrainLivePollHealthTest(unittest.TestCase):
    NOW_UTC = datetime(2026, 6, 19, 3, 30, tzinfo=timezone.utc)

    def setUp(self):
        TestConfig = type(
            "GoogleMotherBrainLivePollHealthTestConfig",
            (),
            {
                "SECRET_KEY": "google-live-poll-health-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(settings)
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week="thursday",
                sort_name="night",
                is_active=True,
            )
        )
        db.session.add(
            SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                sort_window_start_local=time(14, 0),
                sort_window_end_local=time(5, 0),
                polling_start_local=time(18, 0),
                polling_end_local=time(4, 0),
            )
        )
        db.session.commit()
        self.operation = ensure_operational_sort_operations(
            self.gateway,
            now=self.NOW_UTC,
        )["eligible"][0]["operation"]
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_off_does_not_report_current_sync_state(self):
        health = google_motherbrain_live_poll_health(self.gateway, now=self.NOW_UTC)

        self.assertFalse(health["enabled"])
        self.assertEqual(health["status"], "off")
        self.assertEqual(health["label"], "OFF")

    def test_recent_success_is_current(self):
        self._enable()
        self._state(last_success_at_utc=self._utc_now() - timedelta(minutes=2))

        health = google_motherbrain_live_poll_health(self.gateway, now=self.NOW_UTC)

        self.assertEqual(health["status"], "current")
        self.assertEqual(health["label"], "Current")
        self.assertEqual(health["last_success_label"], "22:28")

    def test_last_failed_poll_reports_safe_error_only(self):
        self._enable()
        self._state(last_error="RuntimeError")

        health = google_motherbrain_live_poll_health(self.gateway, now=self.NOW_UTC)

        self.assertEqual(health["status"], "error")
        self.assertEqual(health["label"], "Error")
        self.assertEqual(health["last_success_label"], "Never")
        self.assertNotIn("RuntimeError", health.values())

    def test_missing_or_old_success_is_stale_while_polling_window_is_active(self):
        self._enable()
        self._state(last_success_at_utc=self._utc_now() - timedelta(minutes=3))

        health = google_motherbrain_live_poll_health(self.gateway, now=self.NOW_UTC)

        self.assertTrue(health["polling_window_active"])
        self.assertEqual(health["status"], "stale")
        self.assertEqual(health["last_success_label"], "22:27")

    def test_no_success_displays_never(self):
        self._enable()

        health = google_motherbrain_live_poll_health(self.gateway, now=self.NOW_UTC)

        self.assertEqual(health["status"], "stale")
        self.assertEqual(health["last_success_label"], "Never")

    def test_outside_polling_window_is_not_stale(self):
        self._enable()
        self._state(last_success_at_utc=self._utc_now() - timedelta(hours=1))

        health = google_motherbrain_live_poll_health(
            self.gateway,
            now=datetime(2026, 6, 18, 16, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(health["polling_window_active"])
        self.assertEqual(health["status"], "outside_window")
        self.assertEqual(health["label"], "Outside Polling Window")

    def test_cross_midnight_health_uses_previous_operational_date(self):
        self._enable()
        self._state(last_success_at_utc=self._utc_now() - timedelta(minutes=1))

        health = google_motherbrain_live_poll_health(
            self.gateway,
            now=datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(health["operation_id"], self.operation.id)
        self.assertEqual(health["sort_date"], date(2026, 6, 18))

    def test_historical_operation_state_cannot_choose_the_health_scope(self):
        self._enable()
        self._state(last_success_at_utc=self._utc_now() - timedelta(minutes=1))
        historical = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 6, 11),
            window_minutes=0,
        )
        db.session.add(historical)
        db.session.flush()
        db.session.add(
            MotherBrainGoogleLivePollState(
                gateway_id=self.gateway.id,
                sort_name="night",
                sort_date=historical.sort_date,
                last_error="historical failure",
            )
        )
        db.session.commit()

        health = google_motherbrain_live_poll_health(
            self.gateway,
            now=self.NOW_UTC,
        )

        self.assertEqual(health["operation_id"], self.operation.id)
        self.assertEqual(health["status"], "current")

    def test_health_renders_only_in_motherbrain_google_controls(self):
        self._enable()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            18,
            22,
            30,
        )
        user = User(username="google-live-health", role="operator")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="operator")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        detail = client.get(f"/motherbrain/operations/{self.operation.id}")
        dashboard = client.get("/motherbrain")

        self.assertEqual(detail.status_code, 200)
        reader_panel = detail.data.split(b"data-google-current-sort-reader", 1)[1]
        self.assertIn(b"data-google-live-poll-health=", reader_panel)
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b"data-google-live-poll-health=", dashboard.data)

    def _enable(self):
        set_google_motherbrain_live_polling_enabled(self.gateway, "night", True)
        db.session.commit()

    def _state(self, **values):
        state = MotherBrainGoogleLivePollState(
            gateway_id=self.gateway.id,
            sort_name="night",
            sort_date=self.operation.sort_date,
            **values,
        )
        db.session.add(state)
        db.session.commit()
        return state

    def _utc_now(self):
        return self.NOW_UTC.replace(tzinfo=None)


if __name__ == "__main__":
    unittest.main()
