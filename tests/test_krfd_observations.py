"""Cold, shared and bounded KRFD observation tests with isolated paths."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from flask import Flask
from app.services import frost_auto, krfd_observations as shared, neosubzero_weather as weather
from app.services.frost_schedule import Schedule


class SharedKrfdTest(TestCase):
    def setUp(self):
        self.directory = self.enterContext(TemporaryDirectory())
        self.app = self.new_app()
        self.enterContext(self.app.app_context())
        self.at = datetime(2026, 4, 13, 2, tzinfo=timezone.utc)
        self.schedule = Schedule(ZoneInfo("America/Chicago"), (("sunday", time(21), time(22)),))
        self.gateway = type("Gateway", (), {"code": "RFD"})()

    def new_app(self):
        app = Flask(__name__)
        app.config.update(NEOAPPS_METAR_CACHE_DIR=self.directory, NEOAPPS_SHARED_METAR_ENABLED=True,
                          NEOSUBZERO_WEATHER_ENABLED=True)
        return app

    def clock(self):
        return self.at

    def response(self, stamp, **changes):
        row = dict(icaoId="KRFD", obsTime=stamp.timestamp(), reportTime=stamp.isoformat(),
                   temp=-17.78, dewp=-20, wdir=0, wspd=0, visib=0, wxString="SN")
        row.update(changes)
        raw = json.dumps([row]).encode()
        return raw, dict(url=shared.METAR_URL, retrieved_at_utc=self.at.isoformat(),
                         sha256=sha256(raw).hexdigest(), bytes=len(raw))

    def test_cold_read_and_off_hours_remain_passive_cross_worker_read_after_open(self):
        self.at -= timedelta(seconds=1)
        self.assertIsNone(weather.cached_metar_observation(self.gateway, now=self.at))
        self.assertEqual(shared.next_deadline(self.schedule, self.at), self.at + timedelta(seconds=1))
        with patch.object(frost_auto, "fetch", return_value=self.response(self.at)) as fetch:
            shared.refresh(self.schedule, self.clock)
            fetch.assert_not_called()
            self.at += timedelta(seconds=1)
            shared.refresh(self.schedule, self.clock)
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once_with(shared.METAR_URL, shared.MAX_BYTES)
            # A separately initialized worker has no cached state from the writer.
            with self.new_app().app_context(), patch.object(weather, "_CACHE", {}):
                value = weather.cached_metar_observation(self.gateway, now=self.at)
                self.assertEqual((value["temperature"], value["visibility"]), ("0°F", "0 SM"))
                self.assertFalse(value["stale"])
                self.assertIsNone(weather.cached_metar_observation(type("Gateway", (), {"code":"DEN"})(), now=self.at))
                raw, receipt = shared.source(self.at)
                self.assertEqual(sha256(raw).hexdigest(), receipt["sha256"])
            self.at += timedelta(hours=1)
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once()
            self.assertTrue(weather.cached_metar_observation(self.gateway, now=self.at)["stale"])
            self.at += timedelta(hours=5)
            self.assertIsNone(weather.cached_metar_observation(self.gateway, now=self.at))

    def test_competing_workers_share_one_attempt_and_one_backoff(self):
        started, release = Event(), Event()
        payload = self.response(self.at)
        def provider(*_args):
            started.set()
            self.assertTrue(release.wait(5))
            return payload
        def worker():
            with self.new_app().app_context():
                shared.refresh(self.schedule, self.clock)
        with patch.object(frost_auto, "fetch", side_effect=provider) as fetch, ThreadPoolExecutor(2) as pool:
            first = pool.submit(worker)
            try:
                self.assertTrue(started.wait(5))
                pool.submit(worker).result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
            fetch.assert_called_once()
        self.at += shared.CADENCE
        with patch.object(frost_auto, "fetch", side_effect=OSError("Temporary outage")) as fetch:
            worker()
            worker()
            self.at += timedelta(seconds=59)
            worker()
            fetch.assert_called_once()
            self.assertTrue(weather.cached_metar_observation(self.gateway, now=self.at)["stale"])
            self.at += timedelta(seconds=1)
            worker()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(shared.next_deadline(self.schedule, self.at), self.at + timedelta(minutes=2))

    def test_valid_epoch_fallback_and_invalid_or_foreign_report(self):
        with patch.object(frost_auto, "fetch", return_value=self.response(
                self.at, icaoId="KORD")) as fetch:
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once()
        self.assertIsNone(shared.latest(self.at))
        self.at += timedelta(minutes=1)
        with patch.object(frost_auto, "fetch", return_value=self.response(self.at, reportTime="invalid")):
            shared.refresh(self.schedule, self.clock)
        self.assertEqual(weather.cached_metar_observation(self.gateway, now=self.at)["observed_at"], self.at.isoformat())
        self.at += timedelta(minutes=5)
        with patch.object(frost_auto, "fetch", return_value=self.response(
                self.at + timedelta(minutes=1), reportTime="2030-01-01T00:00:00Z")):
            shared.refresh(self.schedule, self.clock)
        self.assertTrue(weather.cached_metar_observation(self.gateway, now=self.at)["stale"])

    def test_forecast_and_frost_read_shared_bytes_without_second_metar_call(self):
        with patch.object(frost_auto, "fetch", return_value=self.response(self.at)) as fetch:
            shared.refresh(self.schedule, self.clock)
            from app.services.frost_inference import current_observations
            self.assertTrue(current_observations(self.at))
            with patch.object(weather, "_fetch_forecast", return_value={}) as forecast:
                observed = weather._weather_snapshot(gateway=self.gateway, now=self.at)["metar"]
                self.assertEqual(observed["value"][0]["icaoId"], "KRFD")
                forecast.assert_called_once()
            fetch.assert_called_once()
