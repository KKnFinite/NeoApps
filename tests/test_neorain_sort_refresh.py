"""Physical Sort boundaries for NeoRain's automatic revision and report state reads."""
from datetime import datetime, time, timedelta
from unittest import TestCase
from unittest.mock import patch

from app.extensions import db
from app.models import SortDateOperation
from app.neonodes.neorain.services import neorain_refresh_status
from app.services.google_rain_integration_mode import NEO_ONLY, set_rain_integration_mode
from app.services.sort_timeline import ensure_sort_timeline_settings
from tests import test_neorain_overrides as fixtures


def configure_sort_window(gateway, sort_date):
    timeline = ensure_sort_timeline_settings(gateway)
    night = next(setting for setting in timeline.sort_settings if setting.sort_name == "night")
    night.sort_window_start_local, night.sort_window_end_local = time(21), time(5)
    night.planning_start_local = time(17)
    night.ops_window_start_local, night.ops_window_end_local = time(18), time(7)
    db.session.commit()
    return night, datetime.combine(sort_date, time(21))


class NeoRainSortRefreshTest(TestCase):
    def setUp(self):
        self.case = fixtures.RainOverrideTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.app, self.client = self.case.app, self.case.client
        self.operation = self.case.operation
        self.night, self.start = configure_sort_window(self.case.gateway, self.operation.sort_date)
        set_rain_integration_mode(self.case.gateway, "night", NEO_ONLY)
        db.session.commit()

    def test_all_automatic_endpoints_honor_the_overnight_window(self):
        paths = ("inbound/revision", "outbound/revision", "rainrock/state", "daily-briefing/state")
        for offset, active in ((-1, False), (0, True), (180, True), (479, True), (480, False), (510, False)):
            self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = self.start + timedelta(minutes=offset)
            for path in paths:
                with self.subTest(offset=offset, path=path):
                    response = self.client.get(f"/neorain/{path}?automatic=1&operation_id={self.operation.id}")
                    self.assertEqual(response.status_code, 200)
                    self.assertIs(response.json["refresh"]["auto_refresh_enabled"], active)
                    if not active:
                        self.assertEqual(set(response.json), {"ok", "changed", "refresh"})

    def test_incomplete_invalid_and_archived_windows_fail_closed(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = self.start
        for start, end in ((None, time(5)), (time(21), None), (time(21), time(21))):
            self.night.sort_window_start_local, self.night.sort_window_end_local = start, end
            db.session.commit()
            status = neorain_refresh_status(self.case.gateway, self.operation, is_current=True)
            self.assertFalse(status["auto_refresh_enabled"])
        self.operation.archived_at_utc = self.start
        self.night.sort_window_start_local, self.night.sort_window_end_local = time(21), time(5)
        db.session.commit()
        status = neorain_refresh_status(self.case.gateway, self.operation, is_current=True)
        self.assertIsNone(status["sort_end_ms"])

    def test_historical_state_is_manual_only_and_rollover_never_reuses_a_board(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = self.start
        old = SortDateOperation(gateway_id=self.case.gateway.id, gateway_code="RFD",
                                sort_date=self.operation.sort_date - timedelta(days=1), sort_name="night")
        db.session.add(old)
        db.session.commit()
        for path in ("rainrock/state", "daily-briefing/state"):
            url = f"/neorain/{path}?operation_id={old.id}"
            self.assertIn("fields", self.client.get(url).json)
            self.assertNotIn("fields", self.client.get(url + "&automatic=1").json)
        with patch("app.neonodes.neorain.routes.neorain_outbound_revision") as revision:
            response = self.client.get(f"/neorain/outbound/revision?automatic=1&operation_id={old.id}")
            self.assertFalse(response.json["refresh"]["auto_refresh_enabled"])
            revision.assert_not_called()
