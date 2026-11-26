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
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(self.at)) as fetch:
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

    def test_operational_metar_still_acquires_under_continuous_foreground_load(self):
        from app.services import frost_resources

        with patch.object(frost_resources, "reason", return_value="Operational request in progress"), \
             patch.object(frost_auto, "fetch_weather_source", return_value=self.response(self.at)) as transport:
            with self.assertRaises(frost_resources.Deferred):
                frost_auto.fetch(shared.METAR_URL, shared.MAX_BYTES)
            shared.refresh(self.schedule, self.clock)
            transport.assert_called_once_with(shared.METAR_URL, shared.MAX_BYTES)
            self.assertEqual(weather.cached_metar_observation(self.gateway, now=self.at)["temperature"], "0°F")

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
        with patch.object(frost_auto, "fetch_weather_source", side_effect=provider) as fetch, ThreadPoolExecutor(2) as pool:
            first = pool.submit(worker)
            try:
                self.assertTrue(started.wait(5))
                pool.submit(worker).result(timeout=5)
            finally:
                release.set()
            first.result(timeout=5)
            fetch.assert_called_once()
        self.at += timedelta(minutes=10)
        with patch.object(frost_auto, "fetch_weather_source", side_effect=OSError("Temporary outage")) as fetch:
            worker()
            worker()
            self.at += timedelta(minutes=14, seconds=59)
            worker()
            fetch.assert_called_once()
            self.assertTrue(weather.cached_metar_observation(self.gateway, now=self.at)["stale"])
            self.at += timedelta(seconds=1)
            worker()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(shared.next_deadline(self.schedule, self.at), self.at + shared.CADENCE)

    def test_fixed_slots_do_not_drift_with_transport_duration_and_restart(self):
        self.at += timedelta(minutes=10)
        starts = []
        def transport(_url, _maximum):
            starts.append(self.at.strftime("%H:%M:%S"))
            self.at += timedelta(seconds=11)
            return self.response(self.at)
        with patch.object(frost_auto, "fetch_weather_source", side_effect=transport):
            while self.at < datetime(2026, 4, 13, 3, tzinfo=timezone.utc):
                with self.new_app().app_context():
                    shared.refresh(self.schedule, self.clock)
                    due = shared.next_deadline(self.schedule, self.at)
                self.at = due
        self.assertEqual(starts, ["02:10:00", "02:25:00", "02:40:00", "02:55:00"])

    def test_cold_start_near_slot_skips_a_second_provider_request(self):
        self.at += timedelta(minutes=8, seconds=1)
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(self.at)) as fetch:
            shared.refresh(self.schedule, self.clock)
            fetch.assert_not_called()
            self.at = shared.next_deadline(self.schedule, self.at)
            self.assertEqual(self.at.minute, 10)
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once()

    def test_valid_observation_timestamp_and_invalid_or_foreign_report(self):
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(
                self.at, icaoId="KORD")) as fetch:
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once()
        self.assertIsNone(shared.latest(self.at))
        self.at += timedelta(minutes=10)
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(self.at, reportTime="invalid")):
            shared.refresh(self.schedule, self.clock)
        self.assertEqual(weather.cached_metar_observation(self.gateway, now=self.at)["observed_at"], self.at.isoformat())
        self.at += shared.CADENCE
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(
                self.at + timedelta(minutes=1), reportTime="2030-01-01T00:00:00Z")):
            shared.refresh(self.schedule, self.clock)
        self.at += timedelta(minutes=2)
        self.assertTrue(weather.cached_metar_observation(self.gateway, now=self.at)["stale"])

    def test_55_acquisition_uses_54_observation_before_nominal_report_hour(self):
        self.at = self.at.replace(minute=55)
        observed = self.at - timedelta(minutes=1)
        report_time = (self.at + timedelta(minutes=5)).isoformat()
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(
                observed, reportTime=report_time)) as fetch:
            shared.refresh(self.schedule, self.clock)
            fetch.assert_called_once()
        self.assertEqual(weather.cached_metar_observation(self.gateway, now=self.at)["observed_at"],
                         observed.isoformat())

    def test_forecast_and_frost_read_shared_bytes_without_second_metar_call(self):
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(self.at)) as fetch:
            shared.refresh(self.schedule, self.clock)
            from app.services.frost_inference import current_observations
            self.assertTrue(current_observations(self.at + timedelta(minutes=14, seconds=59)))
            with patch.object(weather, "_fetch_forecast", return_value={}) as forecast:
                observed = weather._weather_snapshot(gateway=self.gateway, now=self.at)["metar"]
                self.assertEqual(observed["value"][0]["icaoId"], "KRFD")
                forecast.assert_called_once()
            fetch.assert_called_once()

    def test_same_observation_renews_ducc_freshness_revision(self):
        observed = self.at
        with patch.object(frost_auto, "fetch_weather_source", return_value=self.response(observed)), \
                patch.object(weather, "_fetch_forecast", return_value={}):
            shared.refresh(self.schedule, self.clock)
            first = weather._weather_snapshot(gateway=self.gateway, now=self.at)
            self.at += timedelta(minutes=10)
            shared.refresh(self.schedule, self.clock)
            second = weather._weather_snapshot(gateway=self.gateway, now=self.at)
        self.assertEqual(first["metar"]["value"], second["metar"]["value"])
        self.assertFalse(first["metar"]["stale"])
        self.assertFalse(second["metar"]["stale"])
        self.assertNotEqual(weather._weather_revision(first), weather._weather_revision(second))
