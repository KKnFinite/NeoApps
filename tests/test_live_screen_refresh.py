from datetime import date, datetime, time
from pathlib import Path
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.gateway_matrix import sort_lookup_window_for_operation
from app.services.node_refresh import (
    node_auto_refresh_status,
    sort_window_auto_refresh_status,
)


class LiveScreenRefreshTest(unittest.TestCase):
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
        db.create_all()
        self.gateway = Gateway.query.filter_by(code="RFD").first()
        if self.gateway is None:
            self.gateway = Gateway(code="RFD", name="Rockford")
            db.session.add(self.gateway)
            db.session.flush()
        self.settings = SortTimelineSettings.query.filter_by(
            gateway_id=self.gateway.id
        ).first()
        if self.settings is None:
            self.settings = SortTimelineSettings(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
            )
            db.session.add(self.settings)
            db.session.flush()
        self.sort_setting = SortTimelineSortSetting.query.filter_by(
            gateway_id=self.gateway.id,
            sort_name="night",
        ).first()
        if self.sort_setting is None:
            self.sort_setting = SortTimelineSortSetting(
                timeline_settings=self.settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
            )
            db.session.add(self.sort_setting)
        self.sort_setting.sort_window_start_local = time(14, 0)
        self.sort_setting.sort_window_end_local = time(5, 0)
        self.sort_setting.ops_window_start_local = time(20, 0)
        self.sort_setting.ops_window_end_local = time(3, 0)
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 10),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_current_sort_inside_ops_window_is_active(self):
        status = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 10, 21, 0),
        )

        self.assertTrue(status["auto_refresh_enabled"])
        self.assertEqual(status["reason"], "active")
        self.assertEqual(status["window_start_local"], "20:00")
        self.assertEqual(status["window_end_local"], "03:00")

    def test_before_and_after_ops_window_are_inactive(self):
        before = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 10, 15, 0),
        )
        after = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 11, 3, 30),
        )

        self.assertFalse(before["auto_refresh_enabled"])
        self.assertEqual(before["reason"], "before_ops_window")
        self.assertFalse(after["auto_refresh_enabled"])
        self.assertEqual(after["reason"], "outside_ops_window")

    def test_cross_midnight_ops_window_uses_previous_operational_date(self):
        status = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 11, 1, 0),
        )

        self.assertTrue(status["auto_refresh_enabled"])
        self.assertEqual(status["sort_date"], "2026-08-10")

    def test_historical_sort_is_never_live(self):
        status = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 12, 21, 0),
        )

        self.assertFalse(status["auto_refresh_enabled"])
        self.assertEqual(status["reason"], "historical_sort")
        self.assertEqual(status["live_status_label"], "Live updates off - historical sort")

    def test_ops_window_changes_do_not_change_sort_lookup_semantics(self):
        original_sort_window = sort_lookup_window_for_operation(
            self.operation,
            self.gateway,
        )
        self.sort_setting.ops_window_start_local = time(22, 0)
        self.sort_setting.ops_window_end_local = time(2, 0)
        db.session.commit()

        status = node_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 10, 21, 0),
        )

        self.assertEqual(
            sort_lookup_window_for_operation(self.operation, self.gateway),
            original_sort_window,
        )
        self.assertFalse(status["auto_refresh_enabled"])
        self.assertEqual(status["reason"], "before_ops_window")

    def test_missing_partial_or_complete_ops_window_falls_back_to_sort_window(self):
        for start, end in ((None, None), (time(20, 0), None)):
            with self.subTest(start=start, end=end):
                self.sort_setting.ops_window_start_local = start
                self.sort_setting.ops_window_end_local = end
                db.session.commit()

                status = node_auto_refresh_status(
                    self.gateway,
                    operation=self.operation,
                    now=datetime(2026, 8, 10, 15, 0),
                )

                self.assertTrue(status["auto_refresh_enabled"])
                self.assertEqual(status["window_start_local"], "14:00")
                self.assertEqual(status["window_end_local"], "05:00")

    def test_sort_window_refresh_ignores_ops_end_and_uses_physical_boundaries(self):
        status = sort_window_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 11, 3, 30),
        )

        self.assertTrue(status["auto_refresh_enabled"])
        self.assertEqual(status["reason"], "active")
        self.assertEqual(status["window_start_local"], "14:00")
        self.assertEqual(status["window_end_local"], "05:00")
        self.assertEqual(status["window_label"], "14:00-05:00")

    def test_sort_window_refresh_keeps_historical_operation_inactive(self):
        status = sort_window_auto_refresh_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 8, 12, 15, 0),
        )

        self.assertFalse(status["auto_refresh_enabled"])
        self.assertEqual(status["reason"], "historical_sort")
        self.assertEqual(status["live_status_label"], "Live updates off - historical sort")

    def test_shared_refresh_interval_defaults_to_absolute_5000_floor(self):
        self.assertEqual(self.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"], 5000)

    def test_operational_templates_use_shared_controller_without_local_5000_cadence(self):
        templates = (
            "app/templates/neonodes/neosektor/live_counts.html",
            "app/templates/neonodes/neosektor/ballmat.html",
            "app/templates/neonodes/neosektor/tunnel_conductor.html",
            "app/templates/neonodes/neosektor/driver_routing.html",
            "app/templates/neonodes/neosektor/discharge.html",
            "app/templates/neonodes/neoermac/view_outbound.html",
            "app/templates/neonodes/neoermac/door_view.html",
            "app/templates/neonodes/neoermac/upcoming_pulls.html",
        )
        for template_path in templates:
            with self.subTest(template=template_path):
                source = Path(template_path).read_text(encoding="utf-8")
                self.assertIn("live_screen_refresh_interval_ms", source)
                self.assertIn("NeoLiveUpdates", source)
                self.assertNotIn("5000", source)

    def test_shared_client_has_visibility_and_reconnect_behavior(self):
        source = Path("app/static/js/live_updates.js").read_text(encoding="utf-8")

        self.assertIn('document.addEventListener("visibilitychange"', source)
        self.assertIn("document.hidden", source)
        self.assertIn("this.refreshNow({force: true});", source)
        self.assertIn("DEFAULT_FAILURE_THRESHOLD = 3", source)
        self.assertIn("Live updates paused - reconnecting...", source)
        self.assertIn("this.failures = 0", source)
        self.assertIn("row.dataset.liveDirty", source)
        self.assertIn("preserveLocalControls", source)
        self.assertIn("scrollHost.scrollTop = scrollTop", source)
        self.assertIn("focus({ preventScroll: true })", source)
        self.assertIn("data-live-confirmation-active", source)

    def test_shared_client_has_foreground_inactivity_and_monitor_mode(self):
        source = Path("app/static/js/live_updates.js").read_text(encoding="utf-8")
        css = Path("app/static/css/base.css").read_text(encoding="utf-8")

        self.assertIn("INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000", source)
        for event_name in (
            "pointerdown",
            "mousedown",
            "touchstart",
            "keydown",
            "scroll",
            "input",
            "change",
        ):
            self.assertIn(f'["{event_name}", true]', source)
        self.assertIn("this.inactivityPaused = true", source)
        self.assertIn("LIVE UPDATES PAUSED \\u2014 INACTIVE", source)
        self.assertIn("KEEP LIVE / MONITOR MODE", source)
        self.assertIn("this.monitorMode", source)
        self.assertNotIn("localStorage", source)
        # Browser storage must not persist, override, or otherwise bypass the
        # server-resolved live-screen refresh cadence.  The People form keeps
        # its own unsaved add-person fields in session storage, which is not a
        # live-refresh concern and is intentionally permitted.
        self.assertEqual(source.count("sessionStorage."), 3)
        self.assertIn('sessionStorage.setItem("neostaffing.people.single-add"', source)
        self.assertIn('[data-live-update-state="inactive"]', css)
        self.assertIn(".live-update-monitor-toggle", css)

    def test_shared_idle_handling_is_inherited_by_representative_consumers(self):
        consumers = (
            "app/templates/neomotherbrain/_planning_live_updates.html",
            "app/static/js/parking_plan_live.js",
            "app/templates/neonodes/neoermac/upcoming_pulls.html",
            "app/templates/neonodes/neoermac/door_view.html",
            "app/templates/neonodes/neoermac/view_outbound.html",
            "app/templates/neonodes/neosektor/live_counts.html",
            "app/templates/neonodes/neosektor/ballmat.html",
            "app/templates/neonodes/neosektor/tunnel_conductor.html",
            "app/templates/neonodes/neosektor/driver_routing.html",
            "app/templates/neonodes/neosektor/discharge.html",
        )

        for consumer_path in consumers:
            with self.subTest(consumer=consumer_path):
                source = Path(consumer_path).read_text(encoding="utf-8")
                self.assertIn("NeoLiveUpdates.create", source)
                self.assertNotIn("INACTIVITY_TIMEOUT_MS", source)


if __name__ == "__main__":
    unittest.main()
