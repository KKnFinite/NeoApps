import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.neosubzero_weather import (
    _cached_fetch,
    _forecast_cards,
    clear_neosubzero_weather_cache,
    parse_aviation_weather_metar,
)


class NeoSubZeroWeatherTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_CODE": "RFD",
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        clear_neosubzero_weather_cache()

    def tearDown(self):
        clear_neosubzero_weather_cache()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_aviation_weather_metar_parsing(self):
        current = parse_aviation_weather_metar(
            [
                {
                    "icaoId": "KRFD",
                    "reportTime": "2026-09-01T01:00:00Z",
                    "temp": 0,
                    "dewp": -2,
                    "wdir": 240,
                    "wspd": 12,
                    "wgst": 20,
                    "visib": "10+",
                    "wxString": "-SN",
                    "clouds": [
                        {"cover": "BKN", "base": 1800},
                        {"cover": "OVC", "base": 3500},
                    ],
                    "fltCat": "MVFR",
                    "rawOb": "METAR KRFD sample",
                }
            ]
        )
        self.assertTrue(current["available"])
        self.assertEqual(current["temperature"], "32°F")
        self.assertEqual(current["dewpoint"], "28°F")
        self.assertEqual(current["spread"], "4°F")
        self.assertEqual(current["relative_humidity"], "86%")
        self.assertEqual(current["wind"], "240° 12 kt G20 kt")
        self.assertEqual(current["visibility"], "10+ SM")
        self.assertEqual(current["conditions"], "-SN")
        self.assertEqual(current["sky"], "BKN 1,800 · OVC 3,500")

    def test_cache_reuses_fresh_data_and_falls_back_to_last_good(self):
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        calls = []

        def loader(*, now):
            calls.append(now)
            if len(calls) == 1:
                return {"value": "good"}
            raise RuntimeError("provider down")

        first = _cached_fetch(
            "test_weather",
            timedelta(minutes=5),
            timedelta(hours=1),
            loader,
            now=now,
        )
        fresh = _cached_fetch(
            "test_weather",
            timedelta(minutes=5),
            timedelta(hours=1),
            loader,
            now=now + timedelta(minutes=4),
        )
        stale = _cached_fetch(
            "test_weather",
            timedelta(minutes=5),
            timedelta(hours=1),
            loader,
            now=now + timedelta(minutes=6),
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(first["value"], {"value": "good"})
        self.assertFalse(fresh["stale"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["value"], {"value": "good"})

    def test_forecast_uses_active_sort_days_and_cross_midnight_windows(self):
        operation = SortDateOperation(
            sort_date=date(2026, 9, 1),
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
        )
        timeline = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
        )
        db.session.add_all([operation, timeline])
        db.session.flush()
        db.session.add(
            SortTimelineSortSetting(
                settings_id=timeline.id,
                gateway_id=self.gateway.id,
                gateway_code="RFD",
                sort_name="night",
                sort_window_start_local=time(18, 0),
                sort_window_end_local=time(6, 0),
            )
        )
        for weekday in ("tuesday", "wednesday", "thursday", "monday"):
            db.session.add(
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code="RFD",
                    day_of_week=weekday,
                    sort_name="night",
                    is_active=True,
                )
            )
        db.session.commit()
        periods = []
        for day in (1, 2, 3, 7):
            periods.append(
                {
                    "startTime": f"2026-09-{day:02d}T18:00:00-05:00",
                    "endTime": f"2026-09-{day:02d}T19:00:00-05:00",
                    "temperature": 30 + day,
                    "temperatureUnit": "F",
                    "dewpoint": {"unitCode": "wmoUnit:degC", "value": -2},
                    "relativeHumidity": {"unitCode": "wmoUnit:percent", "value": 70},
                    "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": day},
                    "windDirection": "NW",
                    "windSpeed": "10 mph",
                    "shortForecast": "Chance Snow",
                }
            )
        payload = {
            "hourly": {"properties": {"periods": periods}},
            "grid": {
                "properties": {
                    "windGust": {
                        "uom": "wmoUnit:km_h-1",
                        "values": [
                            {
                                "validTime": "2026-09-01T23:00:00+00:00/PT1H",
                                "value": 32.187,
                            }
                        ],
                    }
                }
            },
        }
        with self.app.test_request_context("/neosubzero/ucc"):
            cards = _forecast_cards(
                self.gateway,
                operation,
                payload,
                now=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(
            [card["sort_date"] for card in cards],
            [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3), date(2026, 9, 7)],
        )
        self.assertTrue(all(card["window_label"] == "18:00–06:00" for card in cards))
        self.assertTrue(all(len(card["hours"]) == 1 for card in cards))
        self.assertEqual(cards[0]["gust"], "G20 mph")
        self.assertEqual(cards[0]["wind"], "NW 10 mph")
