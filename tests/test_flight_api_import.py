from datetime import date, datetime, time, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlparse

from app import create_app
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    Gateway,
    GatewayMembership,
    GatewaySortMatrix,
    MasterFlightSchedule,
    PermissionRule,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineUsageCounter,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.permission_rules import ensure_default_permission_rules
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services import flight_api as flight_api_service
from app.neomotherbrain import routes as neomotherbrain_routes
from app.services.flight_api import (
    API_STATUS_ASSUMED_ARRIVED,
    API_STATUS_IN_AIR,
    API_STATUS_ON_GROUND,
    API_STATUS_SCHEDULED,
    FlightApiConfigurationError,
    FlightApiProviderError,
    RAPIDAPI_ACCEPT,
    RAPIDAPI_QUERY_PARAMS,
    RAPIDAPI_USER_AGENT,
    RapidApiFlightClient,
    accept_review_item,
    current_sort_operation,
    flight_api_auto_poll_status,
    flight_api_operational_time_utc,
    format_flight_api_local_time,
    ignore_review_item,
    import_api_flights_for_operation,
    map_api_status,
    pending_review_items_for_operation,
    run_flight_api_import,
    run_flight_api_replay,
)
from app.services.sort_timeline import ensure_sort_timeline_settings, sort_timeline_context


class FakeFlightClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch_fids(self, gateway_code, start_local, end_local, api_key):
        self.calls.append(
            {
                "gateway_code": gateway_code,
                "start_local": start_local,
                "end_local": end_local,
                "api_key": api_key,
            }
        )
        return self.payload


class ErrorFlightClient:
    def __init__(self, message="Provider returned 429 Too Many Requests."):
        self.message = message
        self.calls = []

    def fetch_fids(self, gateway_code, start_local, end_local, api_key):
        self.calls.append(
            {
                "gateway_code": gateway_code,
                "start_local": start_local,
                "end_local": end_local,
                "api_key": api_key,
            }
        )
        raise FlightApiConfigurationError(self.message)


class FlightApiImportTest(unittest.TestCase):
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
        self.gateway = Gateway(code="RFD", name="Rockford")
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = self._operation()
        db.session.add(self.operation)
        db.session.flush()
        self.settings = ensure_sort_timeline_settings(self.gateway)
        self.settings.provider_enabled = True
        self.settings.units_per_poll = 2
        self.settings.taxi_to_ramp_minutes = 10
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_provider_disabled_blocks_poll(self):
        self.settings.provider_enabled = False
        db.session.commit()
        client = FakeFlightClient({"arrivals": [self._api_flight()]})

        result = run_flight_api_import(self.gateway, self.operation, client=client)

        self.assertFalse(result["provider_enabled"])
        self.assertFalse(result["attempted"])
        self.assertEqual(client.calls, [])
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)

    def test_missing_api_key_returns_safe_failure_without_usage(self):
        previous = os.environ.pop("AERODATABOX_API_KEY", None)
        try:
            result = run_flight_api_import(self.gateway, self.operation)
        finally:
            if previous is not None:
                os.environ["AERODATABOX_API_KEY"] = previous

        self.assertFalse(result["attempted"])
        self.assertTrue(result["provider_error"])
        self.assertIn("AERODATABOX_API_KEY", result["message"])
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_rapidapi_client_builds_fids_request_shape(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return b'{"arrivals": [], "departures": []}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            captured["timeout"] = timeout
            return FakeResponse()

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        try:
            payload = RapidApiFlightClient().fetch_fids(
                "RFD",
                datetime(2026, 6, 1, 1, 15),
                datetime(2026, 6, 1, 3, 45),
                "RAPIDAPI-KEY",
            )
        finally:
            flight_api_service.urlopen = original_urlopen

        self.assertEqual(payload, {"arrivals": [], "departures": []})
        parsed_url = urlparse(captured["url"])
        query_params = parse_qsl(parsed_url.query)
        self.assertEqual(parsed_url.netloc, "aerodatabox.p.rapidapi.com")
        self.assertIn(
            "/flights/airports/iata/RFD/2026-06-01T01:15/2026-06-01T03:45",
            parsed_url.path,
        )
        self.assertEqual(query_params, list(RAPIDAPI_QUERY_PARAMS))
        self.assertEqual(captured["headers"]["user-agent"], RAPIDAPI_USER_AGENT)
        self.assertEqual(captured["headers"]["accept"], RAPIDAPI_ACCEPT)
        self.assertEqual(captured["headers"]["x-rapidapi-key"], "RAPIDAPI-KEY")
        self.assertEqual(captured["headers"]["x-rapidapi-host"], "aerodatabox.p.rapidapi.com")
        self.assertEqual(captured["timeout"], 20)

    def test_rapidapi_client_403_has_safe_diagnostics(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=None)

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        try:
            with self.assertRaises(FlightApiProviderError) as raised:
                RapidApiFlightClient().fetch_fids(
                    "RFD",
                    datetime(2026, 6, 1, 1, 15),
                    datetime(2026, 6, 1, 3, 45),
                    "SUPER-SECRET-RAPIDAPI-KEY",
                )
        finally:
            flight_api_service.urlopen = original_urlopen

        error = raised.exception
        self.assertIn("Provider returned 403 Forbidden", str(error))
        self.assertIn("RapidAPI playground may work", str(error))
        self.assertEqual(error.diagnostics["provider_status_code"], 403)
        self.assertEqual(error.diagnostics["request_host"], "aerodatabox.p.rapidapi.com")
        self.assertIn("/flights/airports/iata/RFD/", error.diagnostics["request_path_query"])
        self.assertTrue(error.diagnostics["user_agent_sent"])
        self.assertTrue(error.diagnostics["accept_header_sent"])
        self.assertTrue(error.diagnostics["api_key_present"])
        self.assertNotIn("SUPER-SECRET-RAPIDAPI-KEY", str(error))
        self.assertNotIn("SUPER-SECRET-RAPIDAPI-KEY", str(error.diagnostics))
        self.assertEqual(captured["headers"]["user-agent"], RAPIDAPI_USER_AGENT)
        self.assertEqual(captured["headers"]["accept"], RAPIDAPI_ACCEPT)
        self.assertEqual(captured["headers"]["x-rapidapi-key"], "SUPER-SECRET-RAPIDAPI-KEY")

    def test_rapidapi_client_strips_key_whitespace_before_sending(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return b'{"arrivals": [], "departures": []}'

        def fake_urlopen(request, timeout):
            captured["headers"] = {
                key.lower(): value for key, value in request.header_items()
            }
            return FakeResponse()

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        try:
            RapidApiFlightClient().fetch_fids(
                "RFD",
                datetime(2026, 6, 1, 1, 15),
                datetime(2026, 6, 1, 3, 45),
                "  RAPIDAPI-KEY\r\n",
            )
        finally:
            flight_api_service.urlopen = original_urlopen

        self.assertEqual(captured["headers"]["x-rapidapi-key"], "RAPIDAPI-KEY")
        self.assertEqual(captured["headers"]["user-agent"], RAPIDAPI_USER_AGENT)
        self.assertEqual(captured["headers"]["accept"], RAPIDAPI_ACCEPT)

    def test_rapidapi_client_reports_normalized_and_quoted_key_safely(self):
        def fake_urlopen(request, timeout):
            body = BytesIO(b'{"message":"invalid key"}')
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=body)

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        try:
            with self.assertRaises(FlightApiProviderError) as raised:
                RapidApiFlightClient().fetch_fids(
                    "RFD",
                    datetime(2026, 6, 1, 1, 15),
                    datetime(2026, 6, 1, 3, 45),
                    ' "SUPER-SECRET-RAPIDAPI-KEY" \n',
                )
        finally:
            flight_api_service.urlopen = original_urlopen

        diagnostics = raised.exception.diagnostics
        self.assertTrue(diagnostics["api_key_normalized"])
        self.assertTrue(diagnostics["api_key_appears_quoted"])
        self.assertNotIn("SUPER-SECRET-RAPIDAPI-KEY", str(diagnostics))

    def test_provider_error_body_is_safely_truncated_and_redacted(self):
        def fake_urlopen(request, timeout):
            body = BytesIO(
                (
                    b'{"message":"bad key", "X-RapidAPI-Key":"SUPER-SECRET-RAPIDAPI-KEY", '
                    b'"detail":"' + (b"x" * 500) + b'"}'
                )
            )
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=body)

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        try:
            with self.assertRaises(FlightApiProviderError) as raised:
                RapidApiFlightClient().fetch_fids(
                    "RFD",
                    datetime(2026, 6, 1, 1, 15),
                    datetime(2026, 6, 1, 3, 45),
                    "SUPER-SECRET-RAPIDAPI-KEY",
                )
        finally:
            flight_api_service.urlopen = original_urlopen

        snippet = raised.exception.diagnostics["provider_response_snippet"]
        self.assertIn("[redacted]", snippet)
        self.assertLessEqual(len(snippet), 303)
        self.assertTrue(snippet.endswith("..."))
        self.assertNotIn("SUPER-SECRET-RAPIDAPI-KEY", snippet)

    def test_provider_error_returns_safe_failure_and_records_usage(self):
        mission = self._mission("arrival", "5X123", eta_datetime_utc=datetime(2026, 6, 1, 7, 5))
        db.session.add(mission)
        db.session.commit()
        client = ErrorFlightClient("Provider returned 429 Too Many Requests.")

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=client,
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result["attempted"])
        self.assertTrue(result["provider_error"])
        self.assertIn("429", result["message"])
        self.assertEqual(result["usage_units_consumed"], 2)
        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(SortDateMission.query.count(), 1)
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 1, 7, 5))
        self.assertEqual(SortTimelineUsageCounter.query.one().units_consumed, 2)
        self.assertEqual(
            self.operation.flight_api_last_attempted_poll_at_utc,
            datetime(2026, 6, 1, 12, 0),
        )
        self.assertEqual(
            self.operation.flight_api_last_failed_poll_at_utc,
            datetime(2026, 6, 1, 12, 0),
        )
        self.assertEqual(self.operation.flight_api_last_poll_status, "failed")
        self.assertIn("429", self.operation.flight_api_last_poll_summary)

    def test_successful_manual_poll_tracks_poll_state(self):
        client = FakeFlightClient({"arrivals": [], "departures": []})

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=client,
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result["attempted"])
        self.assertFalse(result.get("provider_error", False))
        self.assertEqual(
            self.operation.flight_api_last_attempted_poll_at_utc,
            datetime(2026, 6, 1, 12, 0),
        )
        self.assertEqual(
            self.operation.flight_api_last_successful_poll_at_utc,
            datetime(2026, 6, 1, 12, 0),
        )
        self.assertIsNone(self.operation.flight_api_last_failed_poll_at_utc)
        self.assertEqual(self.operation.flight_api_last_poll_status, "success")
        self.assertEqual(
            self.operation.flight_api_next_auto_poll_eligible_at_utc,
            datetime(2026, 6, 1, 12, 10),
        )

    def test_replay_mode_does_not_update_poll_state(self):
        result = run_flight_api_replay(
            self.gateway,
            self.operation,
            payload_text=json.dumps({"arrivals": [], "departures": []}),
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(result["replay_preview"])
        self.assertIsNone(self.operation.flight_api_last_attempted_poll_at_utc)
        self.assertIsNone(self.operation.flight_api_last_successful_poll_at_utc)
        self.assertIsNone(self.operation.flight_api_last_failed_poll_at_utc)
        self.assertIsNone(self.operation.flight_api_next_auto_poll_eligible_at_utc)
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)

    def test_auto_poll_status_is_eligible_inside_window_without_previous_attempt(self):
        self._configure_api_ready_sort()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(status["eligible"])
        self.assertEqual(status["reason"], "eligible")
        self.assertEqual(status["actual_interval_minutes"], 10)
        self.assertEqual(status["next_eligible_time_utc"], datetime(2026, 6, 1, 19, 0))
        self.assertEqual(status["operation_id"], self.operation.id)

    def test_auto_poll_minimum_interval_blocks_until_enough_time_passes(self):
        self._configure_api_ready_sort(minimum_interval=15)
        self.operation.flight_api_last_attempted_poll_at_utc = datetime(2026, 6, 1, 18, 50)
        db.session.commit()

        blocked = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )
        allowed = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 5, tzinfo=timezone.utc),
        )

        self.assertFalse(blocked["eligible"])
        self.assertEqual(blocked["reason"], "waiting for auto poll interval")
        self.assertEqual(blocked["next_eligible_time_utc"], datetime(2026, 6, 1, 19, 5))
        self.assertTrue(allowed["eligible"])

    def test_failed_attempt_also_blocks_immediate_auto_poll_retry(self):
        self._configure_api_ready_sort(minimum_interval=20)
        self.operation.flight_api_last_attempted_poll_at_utc = datetime(2026, 6, 1, 18, 50)
        self.operation.flight_api_last_failed_poll_at_utc = datetime(2026, 6, 1, 18, 50)
        self.operation.flight_api_last_poll_status = "failed"
        db.session.commit()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(status["eligible"])
        self.assertEqual(status["reason"], "waiting for auto poll interval")
        self.assertEqual(status["next_eligible_time_utc"], datetime(2026, 6, 1, 19, 10))

    def test_auto_poll_without_previous_attempt_waits_for_polling_window_start(self):
        self._configure_api_ready_sort()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 12, 30, tzinfo=timezone.utc),
        )

        self.assertFalse(status["eligible"])
        self.assertEqual(status["reason"], "before API Polling Window")
        self.assertEqual(status["next_eligible_time_utc"], datetime(2026, 6, 1, 13, 0))

    def test_auto_poll_outside_polling_window_is_not_eligible(self):
        self._configure_api_ready_sort()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 22, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(status["eligible"])
        self.assertEqual(status["reason"], "outside API Polling Window")

    def test_auto_poll_disabled_provider_or_api_schedule_is_not_eligible(self):
        self._configure_api_ready_sort()
        self.settings.provider_enabled = False
        db.session.commit()

        provider_disabled = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(provider_disabled["eligible"])
        self.assertEqual(provider_disabled["reason"], "provider disabled")

        db.session.query(GatewaySortMatrix).delete()
        self.settings.provider_enabled = True
        db.session.commit()
        schedule_disabled = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(schedule_disabled["eligible"])
        self.assertEqual(schedule_disabled["reason"], "API polling disabled for this sort/day")

    def test_auto_poll_no_monthly_units_remaining_is_not_eligible(self):
        self._configure_api_ready_sort()
        db.session.add(
            SortTimelineUsageCounter(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                month_key="2026-06",
                attempted_call_count=300,
                units_consumed=600,
            )
        )
        db.session.commit()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(status["eligible"])
        self.assertEqual(status["reason"], "monthly API budget exhausted")
        self.assertEqual(status["polls_remaining"], 0)

    def test_auto_poll_overnight_operation_remains_eligible_after_midnight(self):
        self.operation.sort_date = date(2026, 6, 18)
        self._configure_api_ready_sort(
            schedule_day="thursday",
            sort_start=time(22, 0),
            sort_end=time(4, 0),
            poll_start=time(22, 30),
            poll_end=time(3, 30),
        )

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 19, 5, 30, tzinfo=timezone.utc),
        )

        self.assertTrue(status["eligible"])
        self.assertEqual(status["reason"], "eligible")
        self.assertEqual(status["polling_window_start_local"], datetime(2026, 6, 18, 22, 30))
        self.assertEqual(status["polling_window_end_local"], datetime(2026, 6, 19, 3, 30))

    def test_auto_poll_readiness_helper_does_not_call_provider_or_log_usage(self):
        self._configure_api_ready_sort()

        status = flight_api_auto_poll_status(
            self.gateway,
            operation=self.operation,
            now=datetime(2026, 6, 1, 19, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(status["eligible"])
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertIsNone(self.operation.flight_api_last_attempted_poll_at_utc)

    def test_provider_request_uses_sort_lookup_window_not_polling_window(self):
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.sort_window_start_local = time(22, 15)
        night_setting.sort_window_end_local = time(4, 30)
        night_setting.polling_start_local = time(1, 15)
        night_setting.polling_end_local = time(3, 45)
        db.session.commit()
        client = FakeFlightClient({"arrivals": []})

        run_flight_api_import(self.gateway, self.operation, client=client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["gateway_code"], "RFD")
        self.assertEqual(client.calls[0]["start_local"], datetime(2026, 6, 1, 22, 15))
        self.assertEqual(client.calls[0]["end_local"], datetime(2026, 6, 2, 4, 30))

    def test_current_operation_resolver_returns_previous_day_overnight_sort_after_midnight(self):
        self.operation.sort_date = date(2026, 6, 18)
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.sort_window_start_local = time(22, 0)
        night_setting.sort_window_end_local = time(4, 0)
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 19, 0, 30)
        db.session.commit()

        selected_operation = current_sort_operation(self.gateway)

        self.assertEqual(selected_operation.id, self.operation.id)
        self.assertEqual(selected_operation.sort_date, date(2026, 6, 18))

    def test_flight_api_default_selection_uses_active_overnight_sort_after_midnight(self):
        self.operation.sort_date = date(2026, 6, 18)
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.sort_window_start_local = time(22, 0)
        night_setting.sort_window_end_local = time(4, 0)
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 19, 0, 30)
        db.session.commit()
        client = FakeFlightClient({"arrivals": []})

        result = run_flight_api_import(self.gateway, client=client)

        self.assertEqual(result["operation"].id, self.operation.id)
        self.assertEqual(client.calls[0]["start_local"], datetime(2026, 6, 18, 22, 0))
        self.assertEqual(client.calls[0]["end_local"], datetime(2026, 6, 19, 4, 0))

    def test_rapidapi_path_uses_local_sort_lookup_window_not_utc(self):
        self.operation.sort_date = date(2026, 6, 18)
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.sort_window_start_local = time(22, 0)
        night_setting.sort_window_end_local = time(4, 0)
        db.session.commit()
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return b'{"arrivals": [], "departures": []}'

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            return FakeResponse()

        previous = os.environ.get("AERODATABOX_API_KEY")
        original_urlopen = flight_api_service.urlopen
        os.environ["AERODATABOX_API_KEY"] = "RAPIDAPI-KEY"
        flight_api_service.urlopen = fake_urlopen
        try:
            run_flight_api_import(self.gateway, self.operation)
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        parsed_url = urlparse(captured["url"])
        self.assertIn(
            "/flights/airports/iata/RFD/2026-06-18T22:00/2026-06-19T04:00",
            parsed_url.path,
        )
        self.assertNotIn("Z", parsed_url.path)
        self.assertNotIn("+00:00", parsed_url.path)
        self.assertNotIn("2026-06-19T03:00", parsed_url.path)
        self.assertNotIn("2026-06-19T09:00", parsed_url.path)

    def test_repeated_manual_polls_use_same_full_sort_lookup_window(self):
        self.operation.sort_date = date(2026, 6, 18)
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.sort_window_start_local = time(22, 0)
        night_setting.sort_window_end_local = time(4, 0)
        night_setting.polling_start_local = time(0, 30)
        night_setting.polling_end_local = time(2, 0)
        night_setting.ops_window_start_local = time(23, 0)
        night_setting.ops_window_end_local = time(3, 0)
        db.session.commit()
        client = FakeFlightClient({"arrivals": [], "departures": []})

        for poll_time in (
            datetime(2026, 6, 19, 5, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
            datetime(2026, 6, 19, 7, 30, tzinfo=timezone.utc),
        ):
            run_flight_api_import(
                self.gateway,
                self.operation,
                client=client,
                now=poll_time,
            )

        self.assertEqual(len(client.calls), 3)
        for call in client.calls:
            self.assertEqual(call["start_local"], datetime(2026, 6, 18, 22, 0))
            self.assertEqual(call["end_local"], datetime(2026, 6, 19, 4, 0))

    def test_default_budget_preview_uses_tier_two_units_per_poll(self):
        context = sort_timeline_context(
            self.gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["monthly_api_units"], 600)
        self.assertEqual(context["summary"]["units_per_poll"], 2)
        self.assertEqual(context["summary"]["monthly_poll_limit"], 300)

    def test_ups_only_filter_ignores_non_ups_flights(self):
        client = FakeFlightClient(
            {
                "arrivals": [
                    self._api_flight(number="5X999", call_sign="UPS999"),
                    self._api_flight(number="AA123", call_sign="AAL123", airline_icao="AAL", airline_iata="AA"),
                ]
            }
        )

        result = run_flight_api_import(self.gateway, self.operation, client=client)

        self.assertEqual(result["non_ups_ignored"], 1)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(FlightApiReviewItem.query.count(), 1)
        self.assertEqual(FlightApiReviewItem.query.first().flight_number, "5X999")

    def test_ups_numeric_identity_variants_match_together(self):
        variants = ("UPS0673", "UPS673", "5X0673", "5X673", "0673", "673")

        for stored_number in variants:
            for provider_number in variants:
                with self.subTest(stored=stored_number, provider=provider_number):
                    SortDateMission.query.delete()
                    FlightApiReviewItem.query.delete()
                    db.session.commit()
                    mission = self._mission("arrival", stored_number)
                    db.session.add(mission)
                    db.session.commit()

                    result = run_flight_api_import(
                        self.gateway,
                        self.operation,
                        client=FakeFlightClient(
                            {
                                "arrivals": [
                                    self._api_flight(number=provider_number, call_sign="")
                                ]
                            }
                        ),
                    )

                    self.assertEqual(len(result["matched"]), 1)
                    self.assertEqual(result["matched"][0]["mission"].id, mission.id)
                    self.assertEqual(len(result["review_items"]), 0)

    def test_ups_provider_and_mission_padding_examples_match(self):
        examples = (
            ("5X673", "UPS673", "UPS0673"),
            ("5X673", "UPS673", "0673"),
            ("5X0673", "", "UPS673"),
            ("", "UPS673", "5X0673"),
            ("5X947", "UPS947", "UPS0947"),
            ("5X909", "UPS909", "0909"),
            ("5X853", "UPS853", "UPS0853"),
            ("5X616", "UPS616", "UPS0616"),
        )

        for provider_number, call_sign, mission_number in examples:
            with self.subTest(
                provider_number=provider_number,
                call_sign=call_sign,
                mission_number=mission_number,
            ):
                SortDateMission.query.delete()
                FlightApiReviewItem.query.delete()
                db.session.commit()
                mission = self._mission("arrival", mission_number)
                db.session.add(mission)
                db.session.commit()

                result = run_flight_api_import(
                    self.gateway,
                    self.operation,
                    client=FakeFlightClient(
                        {
                            "arrivals": [
                                self._api_flight(
                                    number=provider_number,
                                    call_sign=call_sign,
                                )
                            ]
                        }
                    ),
                )

                self.assertEqual(len(result["matched"]), 1)
                self.assertEqual(result["matched"][0]["mission"].id, mission.id)
                self.assertEqual(len(result["review_items"]), 0)

    def test_non_ups_same_number_does_not_match(self):
        mission = self._mission("arrival", "UPS0673")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(
                            number="AA0673",
                            call_sign="AAL673",
                            airline_icao="AAL",
                            airline_iata="AA",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(result["non_ups_ignored"], 1)

    def test_callsign_can_match_when_provider_flight_number_differs(self):
        mission = self._mission("arrival", "673")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X999", call_sign="UPS0673")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(len(result["review_items"]), 0)

    def test_callsign_fallback_runs_when_provider_flight_has_no_match(self):
        mission = self._mission("arrival", "UPS753")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X755", call_sign="UPS753")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(result["matched"][0]["api_flight"]["normalized_cores_tried"], ["0755", "0753"])

    def test_callsign_wins_when_provider_flight_number_matches_other_mission(self):
        provider_mission = self._mission("arrival", "0755")
        callsign_mission = self._mission("arrival", "UPS753")
        db.session.add_all([provider_mission, callsign_mission])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X755", call_sign="UPS753")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, callsign_mission.id)
        self.assertEqual(len(result["review_items"]), 0)

    def test_callsign_priority_for_1075_when_provider_flight_differs(self):
        provider_mission = self._mission("arrival", "1075")
        callsign_mission = self._mission("arrival", "UPS1085")
        db.session.add_all([provider_mission, callsign_mission])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X1075", call_sign="UPS1085")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, callsign_mission.id)
        self.assertEqual(len(result["review_items"]), 0)

    def test_callsign_priority_for_616_when_provider_flight_differs(self):
        provider_mission = self._mission("arrival", "UPS0616")
        callsign_mission = self._mission("arrival", "UPS0612")
        db.session.add_all([provider_mission, callsign_mission])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X616", call_sign="UPS612")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, callsign_mission.id)
        self.assertEqual(len(result["review_items"]), 0)

    def test_callsign_priority_for_755_when_provider_flight_differs(self):
        provider_mission = self._mission("arrival", "UPS0755")
        callsign_mission = self._mission("arrival", "UPS0753")
        db.session.add_all([provider_mission, callsign_mission])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(number="5X755", call_sign="UPS753")
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, callsign_mission.id)
        self.assertEqual(len(result["review_items"]), 0)

    def test_duplicate_callsign_key_stays_unmatched(self):
        db.session.add_all(
            [
                self._mission("arrival", "UPS0673"),
                self._mission("arrival", "5X673"),
            ]
        )
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X0673", call_sign="UPS673")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].review_reason, "ambiguous callsign match")
        pending_items = pending_review_items_for_operation(self.operation)
        self.assertEqual(pending_items[0].review_reason, "ambiguous callsign match")

    def test_duplicate_callsign_fallback_key_stays_unmatched(self):
        db.session.add_all(
            [
                self._mission("arrival", "UPS0753"),
                self._mission("arrival", "5X753"),
            ]
        )
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="", call_sign="UPS753")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].review_reason, "ambiguous callsign match")

    def test_unsupported_blank_ups_identity_stays_unmatched_with_reason(self):
        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="", call_sign="UPS")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(
            result["review_items"][0].review_reason,
            "unsupported/blank flight identity",
        )

    def test_matching_is_current_sort_only(self):
        other_operation = self._operation()
        other_operation.sort_date = date(2026, 6, 2)
        db.session.add(other_operation)
        db.session.flush()
        other_mission = self._mission("arrival", "UPS0673", sort_date_operation=other_operation)
        other_mission.sort_date = other_operation.sort_date
        db.session.add(other_mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X673", call_sign="UPS673")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].review_reason, "no matching mission")

    def test_master_schedule_is_not_edited_by_api_match(self):
        master_row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
            mission_type="arrival",
            wave="1",
            flight_number="UPS0947",
            origin="SDF",
            destination="RFD",
            active=True,
            active_days="monday",
            planned_time_local=time(2, 0),
            timezone="America/Chicago",
        )
        db.session.add(master_row)
        db.session.flush()
        mission = self._mission(
            "arrival",
            "UPS0947",
            master_flight_schedule_id=master_row.id,
            eta_datetime_utc=datetime(2026, 6, 1, 7, 0),
        )
        db.session.add(mission)
        db.session.commit()
        original_master_flight = master_row.flight_number
        original_master_time = master_row.planned_time_local

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X947", call_sign="UPS947")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        db.session.refresh(master_row)
        db.session.refresh(mission)
        self.assertEqual(MasterFlightSchedule.query.count(), 1)
        self.assertEqual(master_row.flight_number, original_master_flight)
        self.assertEqual(master_row.planned_time_local, original_master_time)
        self.assertEqual(mission.flight_number, "UPS0947")

    def test_zero_padded_api_match_does_not_overwrite_manual_arrived(self):
        mission = self._mission("arrival", "UPS0673", arrival_status="arrived")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(
                            number="5X673",
                            call_sign="UPS673",
                            runway_time="2026-06-01T02:30:00",
                            status="Arrived",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(mission.arrival_status, "arrived")

    def test_import_result_includes_arrival_departure_count_diagnostics(self):
        arrival_mission = self._mission("arrival", "5X123")
        departure_mission = self._mission("departure", "5X456")
        db.session.add_all([arrival_mission, departure_mission])
        db.session.commit()
        client = FakeFlightClient(
            {
                "arrivals": [
                    self._api_flight(number="5X123", call_sign="UPS123"),
                    self._api_flight(number="5X999", call_sign="UPS999"),
                    self._api_flight(number="AA123", call_sign="AAL123", airline_icao="AAL", airline_iata="AA"),
                ],
                "departures": [
                    self._api_flight(
                        mission_type="departure",
                        number="5X456",
                        call_sign="UPS456",
                        origin="RFD",
                        destination="SDF",
                    ),
                    self._api_flight(
                        mission_type="departure",
                        number="DL456",
                        call_sign="DAL456",
                        airline_icao="DAL",
                        airline_iata="DL",
                        origin="RFD",
                        destination="ATL",
                    ),
                ],
            }
        )

        result = run_flight_api_import(self.gateway, self.operation, client=client)

        self.assertEqual(result["raw_arrivals_count"], 3)
        self.assertEqual(result["raw_departures_count"], 2)
        self.assertEqual(result["ups_arrivals_count"], 2)
        self.assertEqual(result["ups_departures_count"], 1)
        self.assertEqual(result["matched_arrivals_count"], 1)
        self.assertEqual(result["matched_departures_count"], 1)
        self.assertEqual(result["unmatched_arrivals_count"], 1)
        self.assertEqual(result["unmatched_departures_count"], 0)
        self.assertEqual(result["non_ups_ignored_arrivals_count"], 1)
        self.assertEqual(result["non_ups_ignored_departures_count"], 1)

    def test_departures_parse_into_diagnostics_for_matched_and_unmatched(self):
        departure_mission = self._mission("departure", "UPS0456")
        db.session.add(departure_mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X456",
                            call_sign="UPS456",
                            origin="RFD",
                            destination="SDF",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="5X999",
                            call_sign="UPS999",
                            origin="RFD",
                            destination="ONT",
                        ),
                    ]
                }
            ),
        )

        self.assertEqual(result["raw_departures_count"], 2)
        self.assertEqual(result["ups_departures_count"], 2)
        self.assertEqual(result["matched_departures_count"], 1)
        self.assertEqual(result["unmatched_departures_count"], 1)
        self.assertEqual(result["non_ups_ignored_departures_count"], 0)
        self.assertEqual(result["review_items"][0].mission_type, "departure")
        self.assertEqual(result["review_items"][0].review_reason, "no matching mission")

    def test_empty_provider_response_is_safe(self):
        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient({"arrivals": None, "departures": []}),
        )

        self.assertTrue(result["attempted"])
        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(result["non_ups_ignored"], 0)

    def test_realish_nested_payload_shape_updates_matched_arrival(self):
        mission = self._mission("arrival", "5X321")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "data": {
                        "arrivals": [
                            {
                                "flight": {
                                    "iataNumber": "5X321",
                                    "icaoNumber": "UPS321",
                                    "airline": {"icao": "UPS", "iata": "5X"},
                                },
                                "departure": {"airport": "SDF"},
                                "arrival": {
                                    "airport": "RFD",
                                    "revisedTimeLocal": "2026-06-01 02:35",
                                    "runwayTimeLocal": "2026-06-01T02:40:00",
                                },
                                "aircraft": {"registration": "N321UP", "modelCode": "B763"},
                                "status": "Departed",
                            }
                        ]
                    }
                }
            ),
            now=datetime(2026, 6, 1, 7, 45, tzinfo=timezone.utc),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 1, 7, 35))
        self.assertEqual(mission.api_runway_time_utc, datetime(2026, 6, 1, 7, 40))
        self.assertEqual(mission.api_status, API_STATUS_ON_GROUND)
        self.assertEqual(mission.assigned_tail_number, "N321UP")
        self.assertEqual(mission.api_aircraft_model, "B763")

    def test_ups_filter_works_with_nested_flight_airline_shape(self):
        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "flights": [
                        {
                            "direction": "Arrival",
                            "flight": {
                                "iataNumber": "5X555",
                                "icaoNumber": "UPS555",
                                "airline": {"icao": "UPS", "iata": "5X"},
                            },
                            "arrival": {"airport": "RFD", "revisedTimeLocal": "2026-06-01T02:25:00"},
                            "departure": {"airport": "SDF"},
                        },
                        {
                            "direction": "Departure",
                            "flight": {
                                "iataNumber": "AA123",
                                "icaoNumber": "AAL123",
                                "airline": {"icao": "AAL", "iata": "AA"},
                            },
                        },
                    ]
                }
            ),
        )

        self.assertEqual(result["non_ups_ignored"], 1)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].flight_number, "5X555")

    def test_matched_arrival_updates_api_fields_without_overwriting_manual_truth(self):
        mission = self._mission(
            "arrival",
            "5X123",
            arrival_status="arrived",
            planned_datetime_local=datetime(2026, 6, 1, 2, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 0),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "arrivals": [
                        self._api_flight(
                            revised_time="2026-06-01T02:25:00",
                            runway_time="2026-06-01T02:30:00",
                            status="Arrived",
                            tail="N123UP",
                            model="A300",
                        )
                    ]
                }
            ),
            now=datetime(2026, 6, 1, 7, 32, tzinfo=timezone.utc),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 1, 7, 25))
        self.assertEqual(mission.eta_source, "api")
        self.assertEqual(mission.api_runway_time_utc, datetime(2026, 6, 1, 7, 30))
        self.assertEqual(mission.api_assumed_arrived_time_utc, datetime(2026, 6, 1, 7, 40))
        self.assertEqual(mission.api_status, API_STATUS_ON_GROUND)
        self.assertEqual(mission.assigned_tail_number, "N123UP")
        self.assertEqual(mission.api_aircraft_model, "A300")
        self.assertEqual(mission.arrival_status, "arrived")
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 6, 1, 2, 0))
        self.assertEqual(mission.planned_datetime_utc, datetime(2026, 6, 1, 7, 0))

    def test_departure_match_does_not_overwrite_std_or_pull_times(self):
        mission = self._mission(
            "departure",
            "5X456",
            planned_datetime_local=datetime(2026, 6, 1, 3, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 8, 0),
            pure_pull_time_local=time(1, 30),
            first_mix_pull_time_local=time(1, 45),
            final_mix_pull_time_local=time(2, 0),
            destination="SDF",
            wave="2",
        )
        db.session.add(mission)
        db.session.commit()

        run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X456",
                            call_sign="UPS456",
                            origin="RFD",
                            destination="SDF",
                            revised_time="2026-06-01T03:25:00",
                            status="Expected",
                            tail="N456UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(mission.assigned_tail_number, "N456UP")
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 6, 1, 3, 0))
        self.assertEqual(mission.planned_datetime_utc, datetime(2026, 6, 1, 8, 0))
        self.assertEqual(mission.pure_pull_time_local, time(1, 30))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 45))
        self.assertEqual(mission.final_mix_pull_time_local, time(2, 0))
        self.assertEqual(mission.destination, "SDF")
        self.assertEqual(mission.wave, "2")

    def test_api_departure_examples_match_by_flight_number_and_fill_tails(self):
        missions = [
            self._mission("departure", "UPS0910", destination="SDF"),
            self._mission("departure", "UPS0856", destination="ONT"),
            self._mission("departure", "0928", destination="PHX"),
            self._mission("departure", "UPS0637", destination="DFW"),
        ]
        db.session.add_all(missions)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            tail="N910UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="856",
                            call_sign="",
                            destination="ONT",
                            tail="N856UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="5X928",
                            call_sign="UPS928",
                            destination="PHX",
                            tail="N928UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="UPS637",
                            call_sign="UPS637",
                            destination="DFW",
                            tail="N637UP",
                        ),
                    ]
                }
            ),
        )

        self.assertEqual(result["matched_departures_count"], 4)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(missions[0].assigned_tail_number, "N910UP")
        self.assertEqual(missions[1].assigned_tail_number, "N856UP")
        self.assertEqual(missions[2].assigned_tail_number, "N928UP")
        self.assertEqual(missions[3].assigned_tail_number, "N637UP")
        self.assertTrue(all(mission.tail_source == "api" for mission in missions))

    def test_departure_match_uses_callsign_as_alternate_flight_source(self):
        mission = self._mission("departure", "UPS0637", destination="DFW")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X999",
                            call_sign="UPS637",
                            destination="DFW",
                            tail="N637UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(result["matched"][0]["match_diagnostic"], "matched by callsign")
        self.assertEqual(mission.assigned_tail_number, "N637UP")
        self.assertEqual(len(result["review_items"]), 0)

    def test_departure_match_does_not_overwrite_master_or_manual_tail(self):
        master_row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            wave="1",
            flight_number="UPS0910",
            origin="RFD",
            destination="SDF",
            active=True,
            active_days="monday",
            planned_time_local=time(2, 0),
            timezone="America/Chicago",
            pure_pull_time_local=time(0, 30),
        )
        mission = self._mission(
            "departure",
            "UPS0910",
            master_flight_schedule_id=1,
            assigned_tail_number="NMANUAL",
            tail_source="manual",
        )
        db.session.add_all([master_row, mission])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        db.session.refresh(master_row)
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(mission.assigned_tail_number, "NMANUAL")
        self.assertEqual(mission.tail_source, "manual")
        self.assertEqual(master_row.flight_number, "UPS0910")
        self.assertEqual(master_row.planned_time_local, time(2, 0))
        self.assertEqual(master_row.pure_pull_time_local, time(0, 30))

    def test_departure_api_owned_tail_can_update(self):
        mission = self._mission(
            "departure",
            "UPS0910",
            assigned_tail_number="NOLDUP",
            tail_source="api",
        )
        db.session.add(mission)
        db.session.commit()

        run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(mission.assigned_tail_number, "N910UP")
        self.assertEqual(mission.tail_source, "api")

    def test_departure_destination_mismatch_stays_unmatched(self):
        mission = self._mission("departure", "UPS0910", destination="SDF")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="ONT",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].review_reason, "destination mismatch")
        self.assertEqual(result["departure_match_audit"][0]["current_flight_key"], "0910")
        self.assertTrue(result["departure_match_audit"][0]["api_candidate_found"])
        self.assertFalse(result["departure_match_audit"][0]["matched"])
        self.assertEqual(result["departure_match_audit"][0]["reason"], "destination mismatch")
        self.assertIsNone(mission.assigned_tail_number)

    def test_departure_time_mismatch_stays_unmatched(self):
        mission = self._mission(
            "departure",
            "UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 2, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 0),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            revised_time="2026-06-01T11:00:00",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(result["review_items"][0].review_reason, "departure time mismatch")
        self.assertEqual(result["departure_match_audit"][0]["reason"], "departure time mismatch")
        self.assertEqual(result["departure_match_audit"][0]["minute_difference"], 540)
        self.assertEqual(result["departure_match_audit"][0]["time_tolerance_minutes"], 480)
        self.assertFalse(result["departure_match_audit"][0]["inside_departure_match_window"])
        self.assertIsNone(mission.assigned_tail_number)

    def test_departure_seven_to_eight_hours_out_matches_and_fills_tail(self):
        mission = self._mission(
            "departure",
            "UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 9, 45),
            planned_datetime_utc=datetime(2026, 6, 1, 14, 45),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            revised_time="2026-06-01T02:00:00",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(result["matched_departures_count"], 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(mission.assigned_tail_number, "N910UP")
        self.assertEqual(result["departure_match_audit"][0]["minute_difference"], 465)
        self.assertEqual(result["departure_match_audit"][0]["time_tolerance_minutes"], 480)
        self.assertTrue(result["departure_match_audit"][0]["inside_departure_match_window"])
        self.assertTrue(result["api_ups_departures"][0]["inside_departure_match_window"])
        self.assertEqual(len(result["review_items"]), 0)

    def test_departure_matching_still_rejects_non_ups_flights(self):
        mission = self._mission(
            "departure",
            "UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 9, 45),
            planned_datetime_utc=datetime(2026, 6, 1, 14, 45),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="AAL910",
                            airline_icao="AAL",
                            airline_iata="AA",
                            destination="SDF",
                            revised_time="2026-06-01T02:00:00",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(result["raw_departures_count"], 1)
        self.assertEqual(result["ups_departures_count"], 0)
        self.assertEqual(result["non_ups_ignored_departures_count"], 1)
        self.assertEqual(result["matched_departures_count"], 0)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertIsNone(mission.assigned_tail_number)

    def test_departure_destination_time_fallback_matches_exactly_one_candidate(self):
        mission = self._mission(
            "departure",
            "UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 2, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 0),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X999",
                            call_sign="UPS999",
                            destination="SDF",
                            revised_time="2026-06-01T02:10:00",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(
            result["matched"][0]["match_diagnostic"],
            "matched by destination/time fallback",
        )
        self.assertEqual(mission.assigned_tail_number, "N910UP")
        self.assertEqual(len(result["review_items"]), 0)

    def test_departure_destination_time_fallback_stays_unmatched_when_ambiguous(self):
        first = self._mission(
            "departure",
            "UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 2, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 0),
        )
        second = self._mission(
            "departure",
            "UPS0856",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 1, 2, 30),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 30),
        )
        db.session.add_all([first, second])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X999",
                            call_sign="UPS999",
                            destination="SDF",
                            revised_time="2026-06-01T02:10:00",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(
            result["review_items"][0].review_reason,
            "ambiguous destination/time fallback",
        )
        self.assertEqual(
            result["api_ups_departures"][0]["unmatched_reason"],
            "ambiguous destination/time fallback",
        )

    def test_departure_matching_is_current_sort_operation_only(self):
        other_operation = self._operation()
        other_operation.sort_date = date(2026, 6, 2)
        db.session.add(other_operation)
        db.session.flush()
        other_mission = self._mission(
            "departure",
            "UPS0910",
            sort_date_operation=other_operation,
            sort_date=other_operation.sort_date,
        )
        db.session.add(other_mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            tail="N910UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(len(result["matched"]), 0)
        self.assertEqual(len(result["review_items"]), 1)
        self.assertEqual(other_mission.assigned_tail_number, None)

    def test_departure_match_audit_reports_missing_provider_candidate(self):
        mission = self._mission("departure", "UPS0910", destination="SDF")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient({"departures": []}),
        )

        self.assertEqual(result["matched_departures_count"], 0)
        self.assertEqual(result["api_ups_departures"], [])
        self.assertEqual(result["departure_match_audit"][0]["current_flight_key"], "0910")
        self.assertFalse(result["departure_match_audit"][0]["api_candidate_found"])
        self.assertEqual(
            result["departure_match_audit"][0]["reason"],
            "provider did not return this departure",
        )

    def test_departure_match_audit_reports_parser_candidate_gap(self):
        mission = self._mission("departure", "UPS0910", destination="SDF")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="AA910",
                            call_sign="AAL910",
                            airline_icao="AAL",
                            airline_iata="AA",
                            destination="SDF",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(result["raw_departures_count"], 1)
        self.assertEqual(result["ups_departures_count"], 0)
        self.assertEqual(
            result["departure_match_audit"][0]["reason"],
            "parser did not produce departure candidate",
        )

    def test_departure_match_audit_reports_tail_update_states(self):
        blank_tail = self._mission("departure", "UPS0910", destination="SDF")
        manual_tail = self._mission(
            "departure",
            "UPS0856",
            destination="ONT",
            assigned_tail_number="NMANUAL",
            tail_source="manual",
        )
        api_tail = self._mission(
            "departure",
            "UPS0928",
            destination="PHX",
            assigned_tail_number="NOLDUP",
            tail_source="api",
        )
        no_api_tail = self._mission("departure", "UPS0637", destination="DFW")
        db.session.add_all([blank_tail, manual_tail, api_tail, no_api_tail])
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="5X910",
                            call_sign="UPS910",
                            destination="SDF",
                            tail="N910UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="5X856",
                            call_sign="UPS856",
                            destination="ONT",
                            tail="N856UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="5X928",
                            call_sign="UPS928",
                            destination="PHX",
                            tail="N928UP",
                        ),
                        self._api_flight(
                            mission_type="departure",
                            number="5X637",
                            call_sign="UPS637",
                            destination="DFW",
                            tail="",
                        ),
                    ]
                }
            ),
        )

        audit_by_key = {
            row["current_flight_key"]: row for row in result["departure_match_audit"]
        }
        self.assertEqual(audit_by_key["0910"]["tail_update_reason"], "tail updated")
        self.assertEqual(blank_tail.assigned_tail_number, "N910UP")
        self.assertEqual(
            audit_by_key["0856"]["tail_update_reason"],
            "tail update blocked because current tail is manual/non-API",
        )
        self.assertEqual(manual_tail.assigned_tail_number, "NMANUAL")
        self.assertEqual(
            audit_by_key["0928"]["tail_update_reason"],
            "API-owned tail refreshed",
        )
        self.assertEqual(api_tail.assigned_tail_number, "N928UP")
        self.assertEqual(
            audit_by_key["0637"]["tail_update_reason"],
            "matched but no API tail/registration available",
        )
        self.assertIsNone(no_api_tail.assigned_tail_number)

    def test_departure_match_audit_handles_midnight_time_comparison(self):
        mission = self._mission(
            "departure",
            "UPS0637",
            destination="DFW",
            planned_datetime_local=datetime(2026, 6, 2, 0, 30),
            planned_datetime_utc=datetime(2026, 6, 2, 5, 30),
        )
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {
                    "departures": [
                        self._api_flight(
                            mission_type="departure",
                            number="UPS637",
                            call_sign="UPS637",
                            destination="DFW",
                            revised_time="2026-06-02T00:20:00",
                            tail="N637UP",
                        )
                    ]
                }
            ),
        )

        self.assertEqual(result["matched_departures_count"], 1)
        self.assertEqual(result["departure_match_audit"][0]["minute_difference"], 10)
        self.assertEqual(result["departure_match_audit"][0]["time_tolerance_minutes"], 480)
        self.assertTrue(result["departure_match_audit"][0]["inside_departure_match_window"])
        self.assertEqual(mission.assigned_tail_number, "N637UP")

    def test_api_status_mapping(self):
        scheduled = {"api_status_raw": "Expected", "runway_time_utc": None}
        in_air = {"api_status_raw": "En Route", "runway_time_utc": None}
        on_ground = {
            "api_status_raw": "Arrived",
            "runway_time_utc": datetime(2026, 6, 1, 7, 30),
        }

        self.assertEqual(map_api_status(scheduled, self.settings), API_STATUS_SCHEDULED)
        self.assertEqual(map_api_status(in_air, self.settings), API_STATUS_IN_AIR)
        self.assertEqual(
            map_api_status(
                on_ground,
                self.settings,
                now=datetime(2026, 6, 1, 7, 31, tzinfo=timezone.utc),
            ),
            API_STATUS_ON_GROUND,
        )
        self.assertEqual(
            map_api_status(
                on_ground,
                self.settings,
                now=datetime(2026, 6, 1, 7, 41, tzinfo=timezone.utc),
            ),
            API_STATUS_ASSUMED_ARRIVED,
        )

    def test_block_in_estimate_uses_runway_time_plus_taxi_minutes(self):
        self.settings.taxi_to_ramp_minutes = 12
        normalized = {
            "mission_type": "arrival",
            "runway_time_utc": datetime(2026, 6, 1, 7, 30),
            "revised_time_utc": datetime(2026, 6, 1, 7, 25),
            "scheduled_time_utc": datetime(2026, 6, 1, 7, 0),
            "api_status_raw": "Arrived",
        }

        self.assertEqual(
            flight_api_operational_time_utc(normalized, self.settings),
            datetime(2026, 6, 1, 7, 42),
        )
        self.assertEqual(
            map_api_status(
                normalized,
                self.settings,
                now=datetime(2026, 6, 1, 7, 41, tzinfo=timezone.utc),
            ),
            API_STATUS_ON_GROUND,
        )
        self.assertEqual(
            map_api_status(
                normalized,
                self.settings,
                now=datetime(2026, 6, 1, 7, 42, tzinfo=timezone.utc),
            ),
            API_STATUS_ASSUMED_ARRIVED,
        )

    def test_rfd_local_time_display_includes_overnight_date_context(self):
        self.assertEqual(
            format_flight_api_local_time(
                datetime(2026, 6, 19, 4, 4),
                self.gateway,
            ),
            "23:04 Local Jun 18",
        )

    def test_manual_unloaded_is_not_overwritten(self):
        mission = self._mission("arrival", "5X123", arrival_status="unloaded")
        db.session.add(mission)
        db.session.commit()

        run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient({"arrivals": [self._api_flight(status="En Route")]}),
        )

        self.assertEqual(mission.arrival_status, "unloaded")
        self.assertEqual(mission.api_status, API_STATUS_IN_AIR)

    def test_unmatched_ups_creates_review_item_and_ignore_prevents_reappearing(self):
        payload = {"arrivals": [self._api_flight(number="5X777", call_sign="UPS777")]}
        first = run_flight_api_import(self.gateway, self.operation, client=FakeFlightClient(payload))
        review_item = first["review_items"][0]

        ignore_review_item(review_item)
        db.session.commit()
        second = run_flight_api_import(self.gateway, self.operation, client=FakeFlightClient(payload))

        self.assertEqual(review_item.review_status, "ignored")
        self.assertEqual(second["ignored_count"], 1)
        self.assertEqual(len(second["review_items"]), 0)
        self.assertEqual(
            FlightApiReviewItem.query.filter_by(review_status="pending").count(),
            0,
        )

        future_operation = self._operation()
        future_operation.sort_date = date(2026, 6, 2)
        db.session.add(future_operation)
        db.session.commit()
        future = run_flight_api_import(self.gateway, future_operation, client=FakeFlightClient(payload))

        self.assertEqual(len(future["review_items"]), 1)
        self.assertEqual(future["review_items"][0].sort_date_operation_id, future_operation.id)

    def test_successful_poll_replaces_current_sort_active_unmatched_queue(self):
        self._review_item(flight_number="5X111", call_sign="UPS111")
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X222", call_sign="UPS222")]}
            ),
        )

        self.assertTrue(result["review_queue_replaced"])
        self.assertEqual(result["replaced_review_count"], 1)
        pending_items = pending_review_items_for_operation(self.operation)
        self.assertEqual(len(pending_items), 1)
        self.assertEqual(pending_items[0].flight_number, "5X222")
        self.assertEqual(
            FlightApiReviewItem.query.filter_by(
                sort_date_operation_id=self.operation.id,
                flight_number="5X111",
                review_status="pending",
            ).count(),
            0,
        )

    def test_stale_unmatched_item_removed_when_later_matches(self):
        mission = self._mission("arrival", "UPS1075")
        self._review_item(flight_number="5X1075", call_sign="UPS1085")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X1075", call_sign="UPS1085")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(pending_review_items_for_operation(self.operation), [])

    def test_ignored_item_stays_suppressed_after_successful_queue_refresh(self):
        payload = {"arrivals": [self._api_flight(number="5X888", call_sign="UPS888")]}
        review_item = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(payload),
        )["review_items"][0]
        ignore_review_item(review_item)
        db.session.commit()

        result = run_flight_api_import(self.gateway, self.operation, client=FakeFlightClient(payload))

        self.assertEqual(result["ignored_count"], 1)
        self.assertEqual(result["suppressed_review_count"], 1)
        self.assertEqual(len(result["review_items"]), 0)
        db.session.refresh(review_item)
        self.assertEqual(review_item.review_status, "ignored")
        self.assertEqual(pending_review_items_for_operation(self.operation), [])

    def test_accepted_item_stays_suppressed_after_successful_queue_refresh(self):
        payload = {"arrivals": [self._api_flight(number="5X889", call_sign="UPS889")]}
        review_item = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(payload),
        )["review_items"][0]
        accept_review_item(review_item, self.settings)
        db.session.commit()

        result = run_flight_api_import(self.gateway, self.operation, client=FakeFlightClient(payload))

        self.assertEqual(result["ignored_count"], 0)
        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(len(result["review_items"]), 0)
        db.session.refresh(review_item)
        self.assertEqual(review_item.review_status, "accepted")
        self.assertEqual(pending_review_items_for_operation(self.operation), [])

    def test_failed_provider_poll_does_not_wipe_existing_review_queue(self):
        existing_item = self._review_item(flight_number="5X333", call_sign="UPS333")
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=ErrorFlightClient("Provider returned 403 Forbidden."),
        )

        self.assertTrue(result["provider_error"])
        self.assertEqual(db.session.get(FlightApiReviewItem, existing_item.id).review_status, "pending")
        self.assertEqual(pending_review_items_for_operation(self.operation)[0].flight_number, "5X333")

    def test_provider_flight_fallback_only_when_callsign_has_no_match(self):
        mission = self._mission("arrival", "UPS1075")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X1075", call_sign="UPS1085")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(
            result["matched"][0]["match_diagnostic"],
            "matched by provider flight fallback",
        )
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(pending_review_items_for_operation(self.operation), [])

    def test_provider_flight_number_fallback_with_blank_callsign(self):
        mission = self._mission("arrival", "UPS0616")
        db.session.add(mission)
        db.session.commit()

        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient(
                {"arrivals": [self._api_flight(number="5X616", call_sign="")]}
            ),
        )

        self.assertEqual(len(result["matched"]), 1)
        self.assertEqual(result["matched"][0]["mission"].id, mission.id)
        self.assertEqual(len(result["review_items"]), 0)
        self.assertEqual(pending_review_items_for_operation(self.operation), [])

    def test_replay_preview_uses_live_matching_without_provider_usage_or_mutation(self):
        self.operation.sort_date = date(2026, 6, 18)
        self.settings.taxi_to_ramp_minutes = 12
        provider_1075 = self._mission("arrival", "UPS1075")
        callsign_1085 = self._mission(
            "arrival",
            "UPS1085",
            eta_datetime_utc=datetime(2026, 6, 19, 4, 0),
            arrival_status="arrived",
        )
        provider_616 = self._mission("arrival", "UPS0616")
        callsign_612 = self._mission("arrival", "UPS0612")
        provider_755 = self._mission("arrival", "UPS0755")
        callsign_753 = self._mission("arrival", "UPS0753")
        departure = self._mission(
            "departure",
            "UPS0900",
            planned_datetime_local=datetime(2026, 6, 19, 2, 30),
            planned_datetime_utc=datetime(2026, 6, 19, 7, 30),
        )
        existing_review = self._review_item(flight_number="5X333", call_sign="UPS333")
        master_row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
            mission_type="arrival",
            wave="1",
            flight_number="UPS1085",
            origin="EWR",
            destination="RFD",
            active=True,
            active_days="thursday",
            planned_time_local=time(0, 30),
            timezone="America/Chicago",
        )
        db.session.add_all(
            [
                provider_1075,
                callsign_1085,
                provider_616,
                callsign_612,
                provider_755,
                callsign_753,
                departure,
                master_row,
            ]
        )
        db.session.commit()

        def fail_urlopen(_request, _timeout):
            raise AssertionError("Replay mode must not call the external provider")

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        flight_api_service.urlopen = fail_urlopen
        try:
            result = run_flight_api_replay(
                self.gateway,
                self.operation,
                payload_text=json.dumps(self._replay_payload()),
                now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
            )
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        matched_ids = {row["mission"].id for row in result["matched"]}
        self.assertIn(callsign_1085.id, matched_ids)
        self.assertIn(callsign_612.id, matched_ids)
        self.assertIn(callsign_753.id, matched_ids)
        self.assertIn(departure.id, matched_ids)
        self.assertNotIn(provider_1075.id, matched_ids)
        self.assertNotIn(provider_616.id, matched_ids)
        self.assertNotIn(provider_755.id, matched_ids)
        self.assertEqual(result["raw_arrivals_count"], 7)
        self.assertEqual(result["raw_departures_count"], 1)
        self.assertEqual(result["ups_arrivals_count"], 6)
        self.assertEqual(result["ups_departures_count"], 1)
        self.assertEqual(result["matched_arrivals_count"], 3)
        self.assertEqual(result["matched_departures_count"], 1)
        self.assertEqual(result["unmatched_arrivals_count"], 3)
        self.assertEqual(result["non_ups_ignored_arrivals_count"], 1)
        self.assertEqual(result["usage_units_consumed"], 0)
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertFalse(result["review_queue_replaced"])
        self.assertEqual(result["request_path_query"], "Replay mode: no external request")
        self.assertEqual(FlightApiReviewItem.query.count(), 1)
        self.assertEqual(
            db.session.get(FlightApiReviewItem, existing_review.id).review_status,
            "pending",
        )
        db.session.refresh(callsign_1085)
        db.session.refresh(master_row)
        self.assertEqual(callsign_1085.eta_datetime_utc, datetime(2026, 6, 19, 4, 0))
        self.assertEqual(callsign_1085.arrival_status, "arrived")
        self.assertIsNone(callsign_1085.assigned_tail_number)
        self.assertEqual(master_row.flight_number, "UPS1085")
        replay_1085 = next(
            row for row in result["matched"] if row["mission"].id == callsign_1085.id
        )
        self.assertEqual(replay_1085["match_reason"], "matched by callsign")
        self.assertEqual(
            flight_api_operational_time_utc(replay_1085["api_flight"], self.settings),
            datetime(2026, 6, 19, 5, 16),
        )

    def test_replay_invalid_json_is_safe_and_does_not_log_usage(self):
        result = run_flight_api_replay(
            self.gateway,
            self.operation,
            payload_text="{not-json",
            now=datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc),
        )

        self.assertTrue(result["provider_error"])
        self.assertIn("Replay JSON parse error", result["message"])
        self.assertEqual(result["usage_units_consumed"], 0)
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_accepted_review_item_adds_current_sort_only_mission_not_master_row(self):
        result = run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient({"arrivals": [self._api_flight(number="5X888", call_sign="UPS888")]}),
        )
        review_item = result["review_items"][0]

        mission = accept_review_item(review_item, self.settings)

        self.assertEqual(mission.mission_source, "api")
        self.assertTrue(mission.api_added_current_sort_only)
        self.assertIsNone(mission.master_flight_schedule_id)
        self.assertEqual(MasterFlightSchedule.query.count(), 0)
        self.assertEqual(review_item.review_status, "accepted")
        self.assertEqual(review_item.accepted_mission_id, mission.id)

    def test_usage_tracking_increments_by_units_per_poll(self):
        self.settings.units_per_poll = 3
        db.session.commit()

        run_flight_api_import(
            self.gateway,
            self.operation,
            client=FakeFlightClient({"arrivals": [self._api_flight(number="5X999")]}),
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.settings.units_per_poll = 5
        db.session.commit()
        context = sort_timeline_context(
            self.gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        counter = SortTimelineUsageCounter.query.one()
        self.assertEqual(counter.attempted_call_count, 1)
        self.assertEqual(counter.units_consumed, 3)
        self.assertEqual(context["summary"]["polls_used"], 1)
        self.assertEqual(context["summary"]["units_used"], 3)

    def _review_item(
        self,
        mission_type="arrival",
        flight_number="5X555",
        call_sign="UPS555",
        review_key=None,
    ):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type=mission_type,
            review_key=review_key or f"{mission_type}:{flight_number}:{call_sign}",
            review_status="pending",
            flight_number=flight_number,
            call_sign=call_sign,
            origin="SDF" if mission_type == "arrival" else "RFD",
            destination="RFD" if mission_type == "arrival" else "SDF",
            revised_time_utc=datetime(2026, 6, 1, 7, 25),
            tail_number="N555UP",
            aircraft_model="A300",
            api_status="Expected",
        )
        db.session.add(item)
        return item

    def _configure_api_ready_sort(
        self,
        schedule_day="monday",
        sort_name="night",
        sort_start=time(0, 0),
        sort_end=time(23, 59),
        poll_start=time(8, 0),
        poll_end=time(16, 0),
        minimum_interval=10,
    ):
        self.operation.sort_name = sort_name
        self.settings.provider_enabled = True
        self.settings.monthly_api_units = 600
        self.settings.units_per_poll = 2
        self.settings.minimum_auto_poll_interval_minutes = minimum_interval
        sort_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == sort_name
        )
        sort_setting.sort_window_start_local = sort_start
        sort_setting.sort_window_end_local = sort_end
        sort_setting.polling_start_local = poll_start
        sort_setting.polling_end_local = poll_end
        existing = GatewaySortMatrix.query.filter_by(
            gateway_id=self.gateway.id,
            day_of_week=schedule_day,
            sort_name=sort_name,
        ).first()
        if not existing:
            existing = GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week=schedule_day,
                sort_name=sort_name,
            )
            db.session.add(existing)
        existing.gateway_code = self.gateway.code
        existing.is_active = True
        db.session.commit()

    def _operation(self):
        return SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
            window_minutes=0,
        )

    def _mission(self, mission_type, flight_number, **overrides):
        values = {
            "sort_date_operation": self.operation,
            "sort_date": self.operation.sort_date,
            "gateway_code": self.operation.gateway_code,
            "sort_name": self.operation.sort_name,
            "mission_type": mission_type,
            "mission_source": "master",
            "wave": "1",
            "flight_number": flight_number,
            "origin": "SDF" if mission_type == "arrival" else "RFD",
            "destination": "RFD" if mission_type == "arrival" else "SDF",
            "timezone": "America/Chicago",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 0),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 0),
            "planned_source": "master",
            "arrival_status": "scheduled" if mission_type == "arrival" else None,
        }
        values.update(overrides)
        return SortDateMission(**values)

    def _api_flight(
        self,
        mission_type="arrival",
        number="5X123",
        call_sign="UPS123",
        airline_icao="UPS",
        airline_iata="5X",
        origin="SDF",
        destination="RFD",
        revised_time="2026-06-01T02:25:00",
        runway_time=None,
        status="Expected",
        tail="N123UP",
        model="A300",
    ):
        departure_airport = {"iata": origin, "icao": origin}
        arrival_airport = {"iata": destination, "icao": destination}
        if mission_type == "departure":
            departure_airport = {"iata": origin, "icao": origin}
            arrival_airport = {"iata": destination, "icao": destination}
        flight = {
            "_mission_type": mission_type,
            "number": number,
            "callSign": call_sign,
            "airline": {"icao": airline_icao, "iata": airline_iata},
            "departure": {
                "airport": departure_airport,
                "revisedTime": {"local": revised_time},
                "scheduledTime": {"local": revised_time},
            },
            "arrival": {
                "airport": arrival_airport,
                "revisedTime": {"local": revised_time},
                "scheduledTime": {"local": revised_time},
            },
            "aircraft": {"reg": tail, "model": model},
            "status": status,
        }
        if runway_time:
            flight["arrival"]["runwayTime"] = {"local": runway_time}
        return flight

    def _replay_payload(self):
        return {
            "arrivals": [
                self._api_flight(
                    number="5X1075",
                    call_sign="UPS1085",
                    origin="EWR",
                    destination="RFD",
                    revised_time="2026-06-19T00:52:00",
                    runway_time="2026-06-19T00:04:00",
                    status="Arrived",
                    tail="N1085U",
                ),
                self._api_flight(
                    number="5X616",
                    call_sign="UPS612",
                    origin="SDF",
                    destination="RFD",
                    revised_time="2026-06-18T23:28:00",
                    runway_time="2026-06-18T23:04:00",
                    status="Arrived",
                    tail="N612UP",
                ),
                self._api_flight(
                    number="5X613",
                    call_sign="UPS613",
                    origin="SDF",
                    destination="RFD",
                    revised_time="2026-06-18T23:39:00",
                    runway_time="2026-06-18T23:14:00",
                    status="Arrived",
                ),
                self._api_flight(
                    number="5X755",
                    call_sign="UPS753",
                    origin="DFW",
                    destination="RFD",
                    revised_time="2026-06-19T00:39:00",
                    runway_time="2026-06-19T00:15:00",
                    status="Arrived",
                    tail="N753UP",
                ),
                self._api_flight(
                    number="5X754",
                    call_sign="UPS754",
                    origin="DFW",
                    destination="RFD",
                    revised_time="2026-06-19T00:49:00",
                    runway_time="2026-06-19T00:20:00",
                    status="Arrived",
                ),
                self._api_flight(
                    number="5X601",
                    call_sign="UPS601",
                    origin="SDF",
                    destination="RFD",
                    revised_time="2026-06-18T23:50:00",
                    status="En Route",
                ),
                self._api_flight(
                    number="AA123",
                    call_sign="AAL123",
                    airline_icao="AAL",
                    airline_iata="AA",
                    origin="ORD",
                    destination="RFD",
                    revised_time="2026-06-19T00:05:00",
                ),
            ],
            "departures": [
                self._api_flight(
                    mission_type="departure",
                    number="5X900",
                    call_sign="UPS900",
                    origin="RFD",
                    destination="SDF",
                    revised_time="2026-06-19T02:30:00",
                    status="Expected",
                )
            ],
        }


class FlightApiTestPageTest(unittest.TestCase):
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
        self.gateway = Gateway.query.filter_by(code="RFD").first() or Gateway(
            code="RFD",
            name="Rockford",
        )
        db.session.add(self.gateway)
        user = User(username="Kessler", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        ensure_default_permission_rules()
        membership = GatewayMembership.query.filter_by(
            user_id=user.id,
            gateway_id=self.gateway.id,
        ).first()
        if membership:
            membership.status = "approved"
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _login_motherbrain_role(self, username, role):
        self.client.get("/logout")
        user = User(username=username, role=role)
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )
        return user

    def _setup_auto_poll_operation(
        self,
        provider_enabled=True,
        sort_name="night",
        sort_start=time(0, 0),
        sort_end=time(23, 59),
        poll_start=time(0, 0),
        poll_end=time(23, 59),
        minimum_interval=10,
    ):
        local_now = current_gateway_local_datetime(self.gateway)
        sort_date = local_now.date()
        day_name = sort_date.strftime("%A").lower()
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = provider_enabled
        settings.monthly_api_units = 600
        settings.units_per_poll = 2
        settings.minimum_auto_poll_interval_minutes = minimum_interval
        sort_setting = next(
            setting for setting in settings.sort_settings if setting.sort_name == sort_name
        )
        sort_setting.sort_window_start_local = sort_start
        sort_setting.sort_window_end_local = sort_end
        sort_setting.polling_start_local = poll_start
        sort_setting.polling_end_local = poll_end
        matrix_entry = GatewaySortMatrix.query.filter_by(
            gateway_id=self.gateway.id,
            day_of_week=day_name,
            sort_name=sort_name,
        ).first()
        if not matrix_entry:
            matrix_entry = GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week=day_name,
                sort_name=sort_name,
            )
            db.session.add(matrix_entry)
        matrix_entry.gateway_code = self.gateway.code
        matrix_entry.is_active = True
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name=sort_name,
            window_minutes=0,
        )
        db.session.add(operation)
        db.session.commit()
        return operation, settings

    def _provider_flight(
        self,
        mission_type,
        number,
        call_sign,
        sort_date,
        airline_icao="UPS",
        airline_iata="5X",
    ):
        local_time = f"{sort_date.isoformat()}T02:25:00"
        if mission_type == "departure":
            return {
                "number": number,
                "callSign": call_sign,
                "airline": {"icao": airline_icao, "iata": airline_iata},
                "departure": {
                    "airport": {"iata": "RFD"},
                    "revisedTime": {"local": local_time},
                    "scheduledTime": {"local": local_time},
                },
                "arrival": {"airport": {"iata": "SDF"}},
                "status": "Expected",
            }
        return {
            "number": number,
            "callSign": call_sign,
            "airline": {"icao": airline_icao, "iata": airline_iata},
            "departure": {"airport": {"iata": "SDF"}},
            "arrival": {
                "airport": {"iata": "RFD"},
                "revisedTime": {"local": local_time},
                "scheduledTime": {"local": local_time},
            },
            "status": "Expected",
        }

    def _install_provider_payload(self, payload):
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls.append({"request": request, "timeout": timeout})
            return FakeResponse()

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fake_urlopen
        return original_urlopen, calls

    def _assert_auto_poll_payload_shape(self, payload):
        expected_keys = {
            "eligible",
            "skipped",
            "reason",
            "current_operation_id",
            "current_operation_name",
            "sort_date",
            "last_attempted_poll",
            "last_successful_poll",
            "last_failed_poll",
            "next_auto_poll_eligible_at",
            "actual_auto_poll_interval_minutes",
            "units_consumed",
            "matched_arrivals",
            "matched_departures",
            "unmatched_arrivals",
            "unmatched_departures",
            "non_ups_ignored_arrivals",
            "non_ups_ignored_departures",
            "review_added",
            "stale_removed",
            "suppressed_review",
            "provider_status",
            "safe_error_text",
        }
        self.assertTrue(expected_keys.issubset(payload.keys()))

    def test_flight_api_test_page_loads_for_grandmaster(self):
        response = self.client.get("/motherbrain/flight-api-test")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FLIGHT API TEST", response.data)
        self.assertIn(b"NO SCHEDULED POLLING IS ENABLED", response.data)
        self.assertIn(b"Sort Flight Lookup Window", response.data)
        self.assertIn(b"Flight API requests pull arrivals/departures for the full sort start-to-end time.", response.data)
        self.assertIn(b"API Polling Window", response.data)
        self.assertIn(b"Automatic API polling may run only during this window.", response.data)
        self.assertIn(b"Ops / Node Online Window", response.data)
        self.assertIn(b"Nodes and live screens auto-refresh only during this window.", response.data)
        self.assertIn(b"AUTO POLL TRIGGER ENDPOINT", response.data)
        self.assertIn(b"Passive only. Scheduled/page-timer polling is not active yet.", response.data)
        self.assertIn(
            b"Scheduled auto polling is not active yet. This shows when the next automatic poll would be eligible once scheduling is enabled.",
            response.data,
        )
        self.assertIn(b"TEST AUTO POLL TRIGGER ONCE", response.data)
        self.assertIn(b"data-auto-poll-result", response.data)
        self.assertIn(b'data-auto-poll-field="eligible"', response.data)
        self.assertIn(b'data-auto-poll-field="review_added"', response.data)
        self.assertIn(b'data-auto-poll-field="safe_error_text"', response.data)
        self.assertIn(b"NEXT AUTO POLL ELIGIBLE AT", response.data)
        self.assertIn(b"CURRENT ELIGIBILITY STATUS", response.data)

    def test_flight_api_test_page_trigger_button_respects_permission_context(self):
        original_user_can = neomotherbrain_routes.user_can

        def deny_auto_poll_trigger(permission_key, *args, **kwargs):
            if permission_key == neomotherbrain_routes.FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION:
                return False
            return original_user_can(permission_key, *args, **kwargs)

        neomotherbrain_routes.user_can = deny_auto_poll_trigger
        try:
            response = self.client.get("/motherbrain/flight-api-test")
        finally:
            neomotherbrain_routes.user_can = original_user_can

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"TEST AUTO POLL TRIGGER ONCE", response.data)
        self.assertIn(
            b"Your current permissions do not allow triggering the passive auto-poll check.",
            response.data,
        )

    def test_flight_api_test_page_trigger_script_has_no_automatic_timer(self):
        template = Path("app/templates/neomotherbrain/flight_api_test.html").read_text()

        self.assertIn("data-auto-poll-trigger", template)
        self.assertIn('method: "POST"', template)
        self.assertNotIn("setInterval", template)
        self.assertNotIn("setTimeout", template)

    def test_auto_poll_page_timer_renders_on_active_motherbrain_pages(self):
        operation, _settings = self._setup_auto_poll_operation()

        responses = [
            self.client.get("/motherbrain"),
            self.client.get("/motherbrain/manage-sort"),
            self.client.get("/motherbrain/flight-api-review"),
            self.client.get(f"/motherbrain/operations/{operation.id}"),
            self.client.get(f"/motherbrain/operations/{operation.id}/arrivals"),
            self.client.get(f"/motherbrain/operations/{operation.id}/departures"),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"data-flight-api-auto-poll-timer", response.data)
            self.assertIn(b"/motherbrain/flight-api-auto-poll/check", response.data)
            self.assertIn(b"AUTO POLL", response.data)

    def test_auto_poll_page_timer_does_not_render_without_trigger_permission(self):
        self._setup_auto_poll_operation()
        original_user_can = neomotherbrain_routes.user_can

        def deny_auto_poll_trigger(permission_key, *args, **kwargs):
            if permission_key == neomotherbrain_routes.FLIGHT_API_AUTO_POLL_TRIGGER_PERMISSION:
                return False
            return original_user_can(permission_key, *args, **kwargs)

        neomotherbrain_routes.user_can = deny_auto_poll_trigger
        try:
            response = self.client.get("/motherbrain")
        finally:
            neomotherbrain_routes.user_can = original_user_can

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"data-flight-api-auto-poll-timer", response.data)
        self.assertNotIn(b"/motherbrain/flight-api-auto-poll/check", response.data)

    def test_auto_poll_page_timer_stays_off_public_admin_and_settings_pages(self):
        self._setup_auto_poll_operation()

        login_response = self.client.get("/login")
        sort_timeline_response = self.client.get("/motherbrain/sort-timeline")
        api_test_response = self.client.get("/motherbrain/flight-api-test")
        permission_response = self.client.get("/admin/permissions")

        for response in (
            login_response,
            sort_timeline_response,
            api_test_response,
            permission_response,
        ):
            self.assertNotIn(b"data-flight-api-auto-poll-timer", response.data)

        self.assertIn(b"data-auto-poll-trigger", api_test_response.data)

    def test_auto_poll_timer_script_uses_only_passive_endpoint_and_no_secrets(self):
        operation, _settings = self._setup_auto_poll_operation()
        response = self.client.get(f"/motherbrain/operations/{operation.id}")
        template = Path(
            "app/templates/neomotherbrain/_flight_api_auto_poll_timer.html"
        ).read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/motherbrain/flight-api-auto-poll/check", response.data)
        self.assertIn('method: "POST"', template)
        self.assertNotIn("AeroDataBox", template)
        self.assertNotIn("aerodatabox", template)
        self.assertNotIn("AERODATABOX_API_KEY", response.get_data(as_text=True))
        self.assertNotIn("rapidapi", template.lower())

    def test_auto_poll_timer_is_self_scheduled_and_not_tight_interval(self):
        template = Path(
            "app/templates/neomotherbrain/_flight_api_auto_poll_timer.html"
        ).read_text()

        self.assertIn("window.setTimeout(runCheck", template)
        self.assertNotIn("setInterval", template)
        self.assertIn("const MIN_DELAY_MS = 60000", template)
        self.assertIn("document.visibilityState === \"hidden\"", template)
        self.assertIn("visibilitychange", template)
        self.assertIn("localStorage", template)

    def test_no_flight_api_background_scheduler_was_added(self):
        paths = [
            Path("app/neomotherbrain/routes.py"),
            Path("app/services/flight_api.py"),
        ]
        combined = "\n".join(path.read_text() for path in paths)

        self.assertNotIn("APScheduler", combined)
        self.assertNotIn("BackgroundScheduler", combined)
        self.assertNotIn("threading.Thread", combined)
        self.assertNotIn("while True", combined)

    def test_sort_timeline_page_explains_window_meanings(self):
        response = self.client.get("/motherbrain/sort-timeline")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Sort Flight Lookup Window", response.data)
        self.assertIn(b"Flight API requests pull arrivals/departures for the full sort start-to-end time.", response.data)
        self.assertIn(b"API Polling Window", response.data)
        self.assertIn(b"Automatic API polling may run only during this window.", response.data)
        self.assertIn(b"Ops / Node Online Window", response.data)
        self.assertIn(b"Nodes and live screens auto-refresh only during this window.", response.data)

    def test_flight_api_test_page_is_grandmaster_only(self):
        self._login_motherbrain_role("api_master", "master")

        response = self.client.get("/motherbrain/flight-api-test", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/rfd")

    def test_flight_api_test_page_does_not_leak_api_key_value(self):
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        try:
            response = self.client.get("/motherbrain/flight-api-test")
        finally:
            os.environ.pop("AERODATABOX_API_KEY", None)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)

    def test_flight_api_test_page_shows_missing_key_without_crashing(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = True
        db.session.add(operation)
        db.session.commit()
        previous = os.environ.pop("AERODATABOX_API_KEY", None)
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={"flight_api_action": "pull", "operation_id": str(operation.id)},
                follow_redirects=True,
            )
        finally:
            if previous is not None:
                os.environ["AERODATABOX_API_KEY"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"API key env var AERODATABOX_API_KEY is not set.", response.data)
        self.assertIn(b"SKIPPED", response.data)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)

    def test_flight_api_test_page_shows_safe_403_diagnostics(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = True
        db.session.add(operation)
        db.session.commit()

        def fake_urlopen(request, timeout):
            body = BytesIO(
                b"error code: 1010; "
                b"X-RapidAPI-Key=SUPER-SECRET-RAPIDAPI-KEY"
            )
            raise HTTPError(request.full_url, 403, "Forbidden", hdrs=None, fp=body)

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = ' "SUPER-SECRET-RAPIDAPI-KEY" \n'
        flight_api_service.urlopen = fake_urlopen
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={"flight_api_action": "pull", "operation_id": str(operation.id)},
                follow_redirects=True,
            )
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Provider returned 403 Forbidden", response.data)
        self.assertIn(b"RapidAPI playground may work", response.data)
        self.assertIn(b"PROVIDER STATUS", response.data)
        self.assertIn(b"403", response.data)
        self.assertIn(b"REQUEST HOST", response.data)
        self.assertIn(b"aerodatabox.p.rapidapi.com", response.data)
        self.assertIn(b"/flights/airports/iata/RFD/", response.data)
        self.assertIn(b"API KEY PRESENT", response.data)
        self.assertIn(b"YES", response.data)
        self.assertIn(b"USER-AGENT SENT", response.data)
        self.assertIn(b"ACCEPT HEADER SENT", response.data)
        self.assertIn(b"API KEY NORMALIZED", response.data)
        self.assertIn(b"API KEY APPEARS QUOTED", response.data)
        self.assertIn(b"PROVIDER RESPONSE", response.data)
        self.assertIn(b"error code: 1010", response.data)
        self.assertIn(b"[redacted]", response.data)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_flight_api_test_page_renders_raw_count_diagnostics(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = True
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date,
            sort_name=operation.sort_name,
            mission_type="departure",
            mission_source="master",
            wave="1",
            flight_number="UPS0456",
            origin="RFD",
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 1, 3, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 8, 0),
            planned_source="master",
        )
        db.session.add(mission)
        db.session.commit()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "arrivals": [
                            {
                                "number": "5X999",
                                "callSign": "UPS999",
                                "airline": {"icao": "UPS", "iata": "5X"},
                                "departure": {"airport": {"iata": "SDF"}, "scheduledTime": {"local": "2026-06-01T02:00:00"}},
                                "arrival": {"airport": {"iata": "RFD"}, "scheduledTime": {"local": "2026-06-01T02:00:00"}},
                            },
                            {
                                "number": "AA123",
                                "callSign": "AAL123",
                                "airline": {"icao": "AAL", "iata": "AA"},
                                "departure": {"airport": {"iata": "ORD"}, "scheduledTime": {"local": "2026-06-01T02:00:00"}},
                                "arrival": {"airport": {"iata": "RFD"}, "scheduledTime": {"local": "2026-06-01T02:00:00"}},
                            },
                        ],
                        "departures": [
                            {
                                "number": "5X456",
                                "callSign": "UPS456",
                                "airline": {"icao": "UPS", "iata": "5X"},
                                "departure": {"airport": {"iata": "RFD"}, "scheduledTime": {"local": "2026-06-01T03:00:00"}},
                                "arrival": {"airport": {"iata": "SDF"}, "scheduledTime": {"local": "2026-06-01T03:00:00"}},
                                "aircraft": {"reg": "N456UP"},
                            }
                        ],
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            return FakeResponse()

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        flight_api_service.urlopen = fake_urlopen
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={"flight_api_action": "pull", "operation_id": str(operation.id)},
                follow_redirects=True,
            )
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RAW ARRIVALS", response.data)
        self.assertIn(b"RAW DEPARTURES", response.data)
        self.assertIn(b"UPS ARRIVALS", response.data)
        self.assertIn(b"UPS DEPARTURES", response.data)
        self.assertIn(b"UNMATCHED ARRIVALS", response.data)
        self.assertIn(b"UNMATCHED DEPARTURES", response.data)
        self.assertIn(b"REVIEW QUEUE", response.data)
        self.assertIn(b"LATEST POLL", response.data)
        self.assertIn(b"STALE REMOVED", response.data)
        self.assertIn(b"SUPPRESSED REVIEW", response.data)
        self.assertIn(b"REQUEST PATH/QUERY", response.data)
        self.assertIn(b"/flights/airports/iata/RFD/", response.data)
        self.assertIn(b"FIRST PROVIDER DEPARTURE", response.data)
        self.assertIn(b"LAST PROVIDER DEPARTURE", response.data)
        self.assertIn(b"DEPARTURE MATCH WINDOW", response.data)
        self.assertIn(b"480 MIN", response.data)
        self.assertIn(b"03:00 Local", response.data)
        self.assertIn(b"DEPARTURE MATCH AUDIT", response.data)
        self.assertIn(b"API UPS DEPARTURES", response.data)
        self.assertIn(b"INSIDE MATCH WINDOW", response.data)
        self.assertIn(b"0456", response.data)
        self.assertIn(b"N456UP", response.data)
        self.assertIn(b"tail updated", response.data)
        self.assertIn(b"NON-UPS IGNORED ARRIVALS", response.data)
        self.assertIn(b"NON-UPS IGNORED DEPARTURES", response.data)
        self.assertIn(b"CORES TRIED", response.data)
        self.assertIn(b"999", response.data)
        self.assertIn(b"no matching mission", response.data)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)

    def test_flight_api_test_page_matched_table_uses_local_block_in_time(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = True
        mission = SortDateMission(
            sort_date_operation=operation,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date,
            sort_name=operation.sort_name,
            mission_type="arrival",
            mission_source="master",
            wave="1",
            flight_number="5X555",
            origin="SDF",
            destination="RFD",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 1, 2, 0),
            planned_datetime_utc=datetime(2026, 6, 1, 7, 0),
            arrival_status="scheduled",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "arrivals": [
                            {
                                "number": "5X555",
                                "callSign": "UPS555",
                                "airline": {"icao": "UPS", "iata": "5X"},
                                "departure": {"airport": {"iata": "SDF"}},
                                "arrival": {
                                    "airport": {"iata": "RFD"},
                                    "revisedTime": {"local": "2026-06-01T02:35:00"},
                                    "runwayTime": {"local": "2026-06-01T02:40:00"},
                                },
                            }
                        ],
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            return FakeResponse()

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        flight_api_service.urlopen = fake_urlopen
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={"flight_api_action": "pull", "operation_id": str(operation.id)},
                follow_redirects=True,
            )
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MATCHED UPS FLIGHTS", response.data)
        self.assertIn(b"02:50 Local Jun 1", response.data)
        self.assertIn(b"PROVIDER 02:40 Local Jun 1", response.data)
        self.assertNotIn(b"2026-06-01 07:40 UTC", response.data)

    def test_flight_api_test_page_replay_preview_does_not_call_provider_or_mutate(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        settings.provider_enabled = True
        mission = SortDateMission(
            sort_date_operation=operation,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date,
            sort_name=operation.sort_name,
            mission_type="arrival",
            mission_source="master",
            wave="1",
            flight_number="UPS1085",
            origin="EWR",
            destination="RFD",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 19, 0, 30),
            planned_datetime_utc=datetime(2026, 6, 19, 5, 30),
            eta_datetime_utc=datetime(2026, 6, 19, 5, 20),
            arrival_status="arrived",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        payload = {
            "arrivals": [
                {
                    "number": "5X1075",
                    "callSign": "UPS1085",
                    "airline": {"icao": "UPS", "iata": "5X"},
                    "departure": {"airport": {"iata": "EWR"}},
                    "arrival": {
                        "airport": {"iata": "RFD"},
                        "revisedTime": {"local": "2026-06-19T00:52:00"},
                        "runwayTime": {"local": "2026-06-19T00:04:00"},
                    },
                    "aircraft": {"reg": "N1085U", "model": "B763"},
                    "status": "Arrived",
                },
                {
                    "number": "5X999",
                    "callSign": "UPS999",
                    "airline": {"icao": "UPS", "iata": "5X"},
                    "departure": {"airport": {"iata": "SDF"}},
                    "arrival": {"airport": {"iata": "RFD"}, "revisedTime": {"local": "2026-06-19T01:00:00"}},
                    "status": "Expected",
                },
                {
                    "number": "AA123",
                    "callSign": "AAL123",
                    "airline": {"icao": "AAL", "iata": "AA"},
                },
            ],
        }

        def fail_urlopen(_request, _timeout):
            raise AssertionError("Replay page must not call the external provider")

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        flight_api_service.urlopen = fail_urlopen
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={
                    "flight_api_action": "replay",
                    "operation_id": str(operation.id),
                    "replay_payload": json.dumps(payload),
                },
                follow_redirects=True,
            )
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        db.session.refresh(operation)
        db.session.refresh(mission)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"REPLAY PROVIDER PAYLOAD", response.data)
        self.assertIn(b"Replay mode preview completed", response.data)
        self.assertIn(b"Replay mode: no external request", response.data)
        self.assertIn(b"PREVIEW ONLY", response.data)
        self.assertIn(b"UPS1085", response.data)
        self.assertIn(b"5X1075", response.data)
        self.assertIn(b"matched by callsign", response.data)
        self.assertIn(b"00:14 Local Jun 19", response.data)
        self.assertIn(b"PROVIDER 00:04 Local Jun 19", response.data)
        self.assertIn(b"UPS999", response.data)
        self.assertNotIn(b">ADD</button>", response.data)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertEqual(FlightApiReviewItem.query.count(), 0)
        self.assertIsNone(operation.flight_api_last_attempted_poll_at_utc)
        self.assertIsNone(operation.flight_api_last_successful_poll_at_utc)
        self.assertIsNone(operation.flight_api_last_failed_poll_at_utc)
        self.assertIsNone(operation.flight_api_next_auto_poll_eligible_at_utc)
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 19, 5, 20))
        self.assertEqual(mission.arrival_status, "arrived")
        self.assertIsNone(mission.assigned_tail_number)

    def test_flight_api_test_page_replay_invalid_json_is_safe(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date.today(),
            sort_name="night",
            window_minutes=0,
        )
        ensure_sort_timeline_settings(self.gateway).provider_enabled = True
        db.session.add(operation)
        db.session.commit()
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        try:
            response = self.client.post(
                "/motherbrain/flight-api-test",
                data={
                    "flight_api_action": "replay",
                    "operation_id": str(operation.id),
                    "replay_payload": "{not-json",
                },
                follow_redirects=True,
            )
        finally:
            os.environ.pop("AERODATABOX_API_KEY", None)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Replay JSON parse error", response.data)
        self.assertIn(b"Replay mode: no external request", response.data)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_flight_api_auto_poll_endpoint_requires_trigger_permission(self):
        self._login_motherbrain_role("auto_poll_operator", "operator")

        response = self.client.post("/motherbrain/flight-api-auto-poll/check")

        self.assertEqual(response.status_code, 403)
        payload = response.get_json()
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["reason"], "Access denied.")

    def test_flight_api_auto_poll_not_eligible_does_not_call_provider(self):
        operation, settings = self._setup_auto_poll_operation(provider_enabled=False)
        calls = []

        def fail_urlopen(_request, _timeout):
            calls.append("called")
            raise AssertionError("Provider should not be called when disabled")

        original_urlopen = flight_api_service.urlopen
        flight_api_service.urlopen = fail_urlopen
        self._login_motherbrain_role("auto_disabled_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            flight_api_service.urlopen = original_urlopen

        payload = response.get_json()
        self._assert_auto_poll_payload_shape(payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["reason"], "provider disabled")
        self.assertEqual(payload["current_operation_id"], operation.id)
        self.assertEqual(calls, [])
        self.assertEqual(SortTimelineUsageCounter.query.count(), 0)

    def test_flight_api_auto_poll_eligible_calls_provider_once_and_returns_counts(self):
        operation, _settings = self._setup_auto_poll_operation()
        mission = self._mission_for_operation(operation, flight_number="UPS0123")
        db.session.add(mission)
        db.session.commit()
        payload = {
            "arrivals": [
                self._provider_flight("arrival", "5X123", "UPS123", operation.sort_date),
                self._provider_flight(
                    "arrival",
                    "AA123",
                    "AAL123",
                    operation.sort_date,
                    airline_icao="AAL",
                    airline_iata="AA",
                ),
            ],
            "departures": [
                self._provider_flight("departure", "5X456", "UPS456", operation.sort_date),
            ],
        }
        original_urlopen, calls = self._install_provider_payload(payload)
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        self._login_motherbrain_role("auto_success_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        db.session.refresh(operation)
        result = response.get_json()
        self._assert_auto_poll_payload_shape(result)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(result["eligible"])
        self.assertFalse(result["skipped"])
        self.assertTrue(result["attempted"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["units_consumed"], 2)
        self.assertEqual(result["matched_arrivals"], 1)
        self.assertEqual(result["matched_departures"], 0)
        self.assertEqual(result["unmatched_arrivals"], 0)
        self.assertEqual(result["unmatched_departures"], 1)
        self.assertEqual(result["non_ups_ignored_arrivals"], 1)
        self.assertEqual(result["review_added"], 1)
        self.assertEqual(result["stale_removed"], 0)
        self.assertIsNotNone(result["last_attempted_poll"])
        self.assertIsNotNone(result["last_successful_poll"])
        self.assertIsNotNone(result["next_auto_poll_eligible_at"])
        self.assertEqual(operation.flight_api_last_poll_status, "success")
        self.assertIsNone(operation.flight_api_auto_poll_in_progress_at_utc)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)

    def test_flight_api_auto_poll_outside_window_returns_not_eligible(self):
        local_now = current_gateway_local_datetime(self.gateway)
        if local_now.hour < 12:
            poll_start, poll_end = time(20, 0), time(21, 0)
        else:
            poll_start, poll_end = time(0, 0), time(1, 0)
        self._setup_auto_poll_operation(poll_start=poll_start, poll_end=poll_end)
        original_run_import = neomotherbrain_routes.run_flight_api_import
        neomotherbrain_routes.run_flight_api_import = lambda *_args, **_kwargs: self.fail(
            "Provider import should not run outside polling window"
        )
        self._login_motherbrain_role("auto_window_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            neomotherbrain_routes.run_flight_api_import = original_run_import

        payload = response.get_json()
        self._assert_auto_poll_payload_shape(payload)
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertIn(payload["reason"], {"before API Polling Window", "outside API Polling Window"})

    def test_flight_api_auto_poll_minimum_interval_not_elapsed_returns_not_eligible(self):
        operation, _settings = self._setup_auto_poll_operation(minimum_interval=30)
        operation.flight_api_last_attempted_poll_at_utc = datetime.utcnow()
        db.session.commit()
        original_run_import = neomotherbrain_routes.run_flight_api_import
        neomotherbrain_routes.run_flight_api_import = lambda *_args, **_kwargs: self.fail(
            "Provider import should not run before interval elapses"
        )
        self._login_motherbrain_role("auto_interval_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            neomotherbrain_routes.run_flight_api_import = original_run_import

        payload = response.get_json()
        self._assert_auto_poll_payload_shape(payload)
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["reason"], "waiting for auto poll interval")
        self.assertIsNotNone(payload["next_auto_poll_eligible_at"])

    def test_flight_api_auto_poll_monthly_units_exhausted_returns_not_eligible(self):
        self._setup_auto_poll_operation()
        month_key = current_gateway_local_datetime(self.gateway).strftime("%Y-%m")
        db.session.add(
            SortTimelineUsageCounter(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                month_key=month_key,
                attempted_call_count=300,
                units_consumed=600,
            )
        )
        db.session.commit()
        original_run_import = neomotherbrain_routes.run_flight_api_import
        neomotherbrain_routes.run_flight_api_import = lambda *_args, **_kwargs: self.fail(
            "Provider import should not run when budget is exhausted"
        )
        self._login_motherbrain_role("auto_budget_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            neomotherbrain_routes.run_flight_api_import = original_run_import

        payload = response.get_json()
        self._assert_auto_poll_payload_shape(payload)
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["reason"], "monthly API budget exhausted")

    def test_flight_api_auto_poll_in_progress_guard_skips_duplicate_request(self):
        operation, _settings = self._setup_auto_poll_operation()
        operation.flight_api_auto_poll_in_progress_at_utc = datetime.utcnow()
        operation.flight_api_auto_poll_lock_token = "active-lock"
        db.session.commit()
        original_run_import = neomotherbrain_routes.run_flight_api_import
        neomotherbrain_routes.run_flight_api_import = lambda *_args, **_kwargs: self.fail(
            "Provider import should not run while another poll is in progress"
        )
        self._login_motherbrain_role("auto_lock_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            neomotherbrain_routes.run_flight_api_import = original_run_import

        payload = response.get_json()
        self._assert_auto_poll_payload_shape(payload)
        self.assertFalse(payload["eligible"])
        self.assertTrue(payload["skipped"])
        self.assertEqual(payload["reason"], "poll already in progress")
        db.session.refresh(operation)
        self.assertEqual(operation.flight_api_auto_poll_lock_token, "active-lock")

    def test_flight_api_auto_poll_provider_failure_clears_lock_and_throttles_retry(self):
        operation, _settings = self._setup_auto_poll_operation()
        calls = []

        def fake_urlopen(request, timeout=20):
            calls.append(request)
            body = BytesIO(b'{"message":"rate limit"}')
            raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=body)

        original_urlopen = flight_api_service.urlopen
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        flight_api_service.urlopen = fake_urlopen
        self._login_motherbrain_role("auto_failure_simulator", "simulator")
        try:
            first = self.client.post("/motherbrain/flight-api-auto-poll/check")
            second = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        db.session.refresh(operation)
        first_payload = first.get_json()
        second_payload = second.get_json()
        self._assert_auto_poll_payload_shape(first_payload)
        self._assert_auto_poll_payload_shape(second_payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertTrue(first_payload["eligible"])
        self.assertFalse(first_payload["skipped"])
        self.assertEqual(first_payload["provider_status"], 429)
        self.assertIn("429", first_payload["safe_error_text"])
        self.assertEqual(operation.flight_api_last_poll_status, "failed")
        self.assertIsNotNone(operation.flight_api_last_attempted_poll_at_utc)
        self.assertIsNotNone(operation.flight_api_last_failed_poll_at_utc)
        self.assertIsNotNone(operation.flight_api_next_auto_poll_eligible_at_utc)
        self.assertIsNone(operation.flight_api_auto_poll_in_progress_at_utc)
        self.assertEqual(operation.flight_api_auto_poll_lock_token, "")
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second_payload["eligible"])
        self.assertTrue(second_payload["skipped"])
        self.assertEqual(second_payload["reason"], "waiting for auto poll interval")
        self.assertEqual(len(calls), 1)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", first.data)

    def test_flight_api_auto_poll_does_not_mutate_master_or_manual_truth(self):
        operation, _settings = self._setup_auto_poll_operation()
        mission = self._mission_for_operation(
            operation,
            flight_number="UPS0999",
            eta_datetime_utc=datetime(2026, 6, 1, 7, 5),
            arrival_status="arrived",
        )
        master_row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name=operation.sort_name,
            mission_type="arrival",
            wave="1",
            flight_number="UPS0999",
            origin="SDF",
            destination="RFD",
            active_days=operation.sort_date.strftime("%A").lower(),
            planned_time_local=time(2, 0),
        )
        db.session.add_all([mission, master_row])
        db.session.commit()
        provider_payload = {
            "arrivals": [
                self._provider_flight("arrival", "5X999", "UPS999", operation.sort_date)
            ]
        }
        original_urlopen, _calls = self._install_provider_payload(provider_payload)
        previous = os.environ.get("AERODATABOX_API_KEY")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        self._login_motherbrain_role("auto_manual_truth_simulator", "simulator")
        try:
            response = self.client.post("/motherbrain/flight-api-auto-poll/check")
        finally:
            flight_api_service.urlopen = original_urlopen
            if previous is None:
                os.environ.pop("AERODATABOX_API_KEY", None)
            else:
                os.environ["AERODATABOX_API_KEY"] = previous

        db.session.refresh(mission)
        db.session.refresh(master_row)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.arrival_status, "arrived")
        self.assertEqual(MasterFlightSchedule.query.count(), 1)
        self.assertEqual(master_row.planned_time_local, time(2, 0))

    def test_flight_api_review_page_link_visible_for_view_permission(self):
        self._login_motherbrain_role("review_simulator_link", "simulator")

        response = self.client.get("/motherbrain")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/motherbrain/flight-api-review"', response.data)
        self.assertIn(b"FLIGHT API REVIEW", response.data)

    def test_flight_api_review_page_permission_can_block_link_and_page(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.flight_api_review.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.flight_api_review.edit"
        ).one()
        view_rule.minimum_role = "master"
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_motherbrain_role("review_blocked_simulator", "simulator")

        home_response = self.client.get("/motherbrain")
        blocked_response = self.client.get(
            "/motherbrain/flight-api-review",
            follow_redirects=False,
        )

        self.assertEqual(home_response.status_code, 200)
        self.assertNotIn(b'href="/motherbrain/flight-api-review"', home_response.data)
        self.assertEqual(blocked_response.status_code, 302)
        self.assertEqual(blocked_response.location, "/rfd")

    def test_flight_api_review_page_renders_unmatched_items_for_simulator(self):
        operation = self._review_operation()
        self._review_item(operation, flight_number="5X555", call_sign="UPS555")
        self._login_motherbrain_role("review_simulator", "simulator")

        response = self.client.get(
            f"/motherbrain/flight-api-review?operation_id={operation.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FLIGHT API REVIEW", response.data)
        self.assertIn(b"5X555", response.data)
        self.assertIn(b"UPS555", response.data)
        self.assertIn(b"SDF / RFD", response.data)
        self.assertIn(b"02:25 Local Jun 1", response.data)
        self.assertNotIn(b"2026-06-01 07:25 UTC", response.data)
        self.assertIn(b"N555UP", response.data)
        self.assertIn(b"A300", response.data)
        self.assertIn(b"Expected", response.data)
        self.assertIn(b">ADD</button>", response.data)
        self.assertIn(b"Ignore 5X555 for this sort operation", response.data)
        self.assertIn(b"&times; IGNORE", response.data)

    def test_flight_api_review_page_does_not_leak_api_key_value(self):
        operation = self._review_operation()
        self._review_item(operation)
        self._login_motherbrain_role("review_key_simulator", "simulator")
        os.environ["AERODATABOX_API_KEY"] = "SUPER-SECRET-RAPIDAPI-KEY"
        try:
            response = self.client.get(
                f"/motherbrain/flight-api-review?operation_id={operation.id}"
            )
        finally:
            os.environ.pop("AERODATABOX_API_KEY", None)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"SUPER-SECRET-RAPIDAPI-KEY", response.data)

    def test_flight_api_review_edit_permission_controls_actions(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.flight_api_review.edit"
        ).one()
        edit_rule.minimum_role = "master"
        operation = self._review_operation()
        review_item = self._review_item(operation)
        db.session.commit()
        self._login_motherbrain_role("review_view_only_simulator", "simulator")

        page_response = self.client.get(
            f"/motherbrain/flight-api-review?operation_id={operation.id}"
        )
        add_response = self.client.post(
            f"/motherbrain/flight-api-review/{review_item.id}/add",
            data={"operation_id": str(operation.id)},
            follow_redirects=False,
        )

        db.session.refresh(review_item)
        self.assertEqual(page_response.status_code, 200)
        self.assertIn(b"VIEW ONLY", page_response.data)
        self.assertNotIn(b">ADD</button>", page_response.data)
        self.assertEqual(add_response.status_code, 302)
        self.assertEqual(add_response.location, "/rfd")
        self.assertEqual(review_item.review_status, "pending")
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_flight_api_review_add_creates_current_sort_only_mission_not_master_row(self):
        operation = self._review_operation()
        review_item = self._review_item(
            operation,
            flight_number="5X777",
            call_sign="UPS777",
            tail_number="N777UP",
        )
        master_row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name=operation.sort_name,
            mission_type="arrival",
            wave="1",
            flight_number="5X111",
            aircraft_type="A300",
            origin="SDF",
            destination="RFD",
            active_days="monday",
            planned_time_local=time(2, 0),
            timezone="America/Chicago",
            active=True,
        )
        manual_mission = self._mission_for_operation(
            operation,
            flight_number="5X999",
            arrival_status="arrived",
        )
        db.session.add_all([master_row, manual_mission])
        db.session.commit()
        self._login_motherbrain_role("review_add_simulator", "simulator")

        response = self.client.post(
            f"/motherbrain/flight-api-review/{review_item.id}/add",
            data={"operation_id": str(operation.id)},
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="5X777").one()
        db.session.refresh(review_item)
        db.session.refresh(manual_mission)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/motherbrain/flight-api-review", response.location)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.mission_source, "api")
        self.assertTrue(mission.api_added_current_sort_only)
        self.assertIsNone(mission.master_flight_schedule_id)
        self.assertEqual(mission.assigned_tail_number, "N777UP")
        self.assertEqual(review_item.review_status, "accepted")
        self.assertEqual(review_item.accepted_mission_id, mission.id)
        self.assertEqual(MasterFlightSchedule.query.count(), 1)
        self.assertEqual(manual_mission.arrival_status, "arrived")

    def test_flight_api_review_add_rejects_item_outside_selected_operation(self):
        selected_operation = self._review_operation(sort_name="night")
        other_operation = self._review_operation(sort_name="day")
        review_item = self._review_item(other_operation, flight_number="5X888")
        db.session.commit()
        self._login_motherbrain_role("review_wrong_op_simulator", "simulator")

        response = self.client.post(
            f"/motherbrain/flight-api-review/{review_item.id}/add",
            data={"operation_id": str(selected_operation.id)},
            follow_redirects=False,
        )

        db.session.refresh(review_item)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(review_item.review_status, "pending")
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_flight_api_review_ignore_hides_same_operation_not_future_operation(self):
        operation = self._review_operation()
        future_operation = self._review_operation(
            sort_name="night",
            sort_date=date.today() + timedelta(days=1),
        )
        current_item = self._review_item(
            operation,
            flight_number="5X444",
            review_key="arrival:ups444",
        )
        future_item = self._review_item(
            future_operation,
            flight_number="5X444",
            review_key="arrival:ups444",
        )
        db.session.commit()
        self._login_motherbrain_role("review_ignore_simulator", "simulator")

        response = self.client.post(
            f"/motherbrain/flight-api-review/{current_item.id}/ignore",
            data={"operation_id": str(operation.id)},
            follow_redirects=False,
        )

        db.session.refresh(current_item)
        db.session.refresh(future_item)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(current_item.review_status, "ignored")
        self.assertEqual(future_item.review_status, "pending")
        self.assertEqual(pending_review_items_for_operation(operation), [])
        self.assertEqual(pending_review_items_for_operation(future_operation), [future_item])

    def _review_operation(self, sort_name="night", sort_date=None):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date or date.today(),
            sort_name=sort_name,
            window_minutes=0,
        )
        db.session.add(operation)
        db.session.commit()
        return operation

    def _review_item(
        self,
        operation,
        mission_type="arrival",
        flight_number="5X555",
        call_sign="UPS555",
        review_key=None,
        tail_number="N555UP",
    ):
        item = FlightApiReviewItem(
            sort_date_operation_id=operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date,
            sort_name=operation.sort_name,
            mission_type=mission_type,
            review_key=review_key or f"{mission_type}:{flight_number}:{operation.id}",
            review_status="pending",
            flight_number=flight_number,
            call_sign=call_sign,
            origin="SDF" if mission_type == "arrival" else "RFD",
            destination="RFD" if mission_type == "arrival" else "SDF",
            revised_time_utc=datetime(2026, 6, 1, 7, 25),
            tail_number=tail_number,
            aircraft_model="A300",
            api_status="Expected",
        )
        db.session.add(item)
        db.session.commit()
        return item

    def _mission_for_operation(self, operation, flight_number="5X999", **overrides):
        values = {
            "sort_date_operation": operation,
            "sort_date": operation.sort_date,
            "gateway_code": operation.gateway_code,
            "sort_name": operation.sort_name,
            "mission_type": "arrival",
            "mission_source": "manual",
            "wave": "1",
            "flight_number": flight_number,
            "origin": "SDF",
            "destination": "RFD",
            "timezone": "America/Chicago",
            "planned_datetime_local": datetime.combine(operation.sort_date, time(2, 0)),
            "planned_datetime_utc": datetime.combine(operation.sort_date, time(7, 0)),
            "planned_source": "manual",
            "arrival_status": "scheduled",
        }
        values.update(overrides)
        return SortDateMission(**values)


if __name__ == "__main__":
    unittest.main()
