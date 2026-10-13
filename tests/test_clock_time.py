import unittest
from datetime import time

from app.services.clock_time import normalize_clock_time, parse_clock_time
from app.services.neostaffing_timecards import clock_value
from app.services.neorain_outbound_service_fields import parse_outbound_service_field
from app.services.neoermac_door_view import _parse_optional_time


class ClockTimeTest(unittest.TestCase):
    def test_valid_compact_shorthand_and_colon(self):
        for raw, expected in (
            ("0500", "05:00"), ("0830", "08:30"), ("1230", "12:30"),
            ("2100", "21:00"), ("2359", "23:59"), ("500", "05:00"),
            ("930", "09:30"), ("21:00", "21:00"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_clock_time(raw), expected)
                self.assertEqual(parse_clock_time(raw), time.fromisoformat(expected))

    def test_optional_blank_and_invalid_values(self):
        self.assertIsNone(normalize_clock_time(""))
        self.assertIsNone(parse_clock_time(None))
        for raw in ("2400", "2360", "9999", "abc", "0", "05", "5:00"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_clock_time(raw)
        with self.assertRaises(ValueError):
            normalize_clock_time("", optional=False)

    def test_staffing_timecard_clock_normalization_preserves_full_timestamp(self):
        self.assertEqual(clock_value("500"), "05:00")
        self.assertEqual(clock_value("2330"), "23:30")
        self.assertEqual(clock_value("2026-10-13T23:30"), "2026-10-13T23:30")
        with self.assertRaises(ValueError):
            clock_value("2400")

    def test_rain_js_in_uses_same_parser(self):
        self.assertEqual(parse_outbound_service_field("js_in", "930"), time(9, 30))
        self.assertIsNone(parse_outbound_service_field("js_in", ""))
        with self.assertRaises(ValueError):
            parse_outbound_service_field("js_in", "2360")

    def test_ermac_pull_uses_same_parser(self):
        self.assertEqual(_parse_optional_time("500"), time(5, 0))
        self.assertIsNone(_parse_optional_time(""))
        with self.assertRaises(ValueError):
            _parse_optional_time("2400")


if __name__ == "__main__":
    unittest.main()
