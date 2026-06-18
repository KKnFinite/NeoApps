from datetime import date, datetime, time, timezone
import os
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    Gateway,
    GatewayMembership,
    MasterFlightSchedule,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineUsageCounter,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services import flight_api as flight_api_service
from app.services.flight_api import (
    API_STATUS_ASSUMED_ARRIVED,
    API_STATUS_IN_AIR,
    API_STATUS_ON_GROUND,
    API_STATUS_SCHEDULED,
    FlightApiConfigurationError,
    RapidApiFlightClient,
    accept_review_item,
    ignore_review_item,
    import_api_flights_for_operation,
    map_api_status,
    run_flight_api_import,
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
        self.assertIn(
            "/flights/airports/icao/RFD/2026-06-01T01:15/2026-06-01T03:45",
            captured["url"],
        )
        self.assertIn("direction=Both", captured["url"])
        self.assertIn("withLeg=true", captured["url"])
        self.assertEqual(captured["headers"]["x-rapidapi-key"], "RAPIDAPI-KEY")
        self.assertEqual(captured["headers"]["x-rapidapi-host"], "aerodatabox.p.rapidapi.com")
        self.assertEqual(captured["timeout"], 20)

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

    def test_poll_uses_current_sort_api_window(self):
        night_setting = next(
            setting for setting in self.settings.sort_settings if setting.sort_name == "night"
        )
        night_setting.polling_start_local = time(1, 15)
        night_setting.polling_end_local = time(3, 45)
        db.session.commit()
        client = FakeFlightClient({"arrivals": []})

        run_flight_api_import(self.gateway, self.operation, client=client)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["gateway_code"], "RFD")
        self.assertEqual(client.calls[0]["start_local"], datetime(2026, 6, 1, 1, 15))
        self.assertEqual(client.calls[0]["end_local"], datetime(2026, 6, 1, 3, 45))

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
                        )
                    ]
                }
            ),
        )

        self.assertEqual(mission.planned_datetime_local, datetime(2026, 6, 1, 3, 0))
        self.assertEqual(mission.planned_datetime_utc, datetime(2026, 6, 1, 8, 0))
        self.assertEqual(mission.pure_pull_time_local, time(1, 30))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 45))
        self.assertEqual(mission.final_mix_pull_time_local, time(2, 0))

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

    def test_flight_api_test_page_loads_for_grandmaster(self):
        response = self.client.get("/motherbrain/flight-api-test")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"FLIGHT API TEST", response.data)
        self.assertIn(b"NO SCHEDULED POLLING IS ENABLED", response.data)

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


if __name__ == "__main__":
    unittest.main()
