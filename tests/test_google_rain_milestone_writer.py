"""The retired writer cannot perform read-then-update against a moving row."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.google_rain_sheets import GoogleRainWriterError, write_google_rain_departure_milestone


class GoogleRainMilestoneWriterTest(unittest.TestCase):
    def test_every_legacy_field_refuses_before_opening_a_client(self):
        client = Mock()
        mission = SimpleNamespace(mission_type="departure", flight_number="UPS1234", destination="SDF")
        for field in ("elmac", "ramp_load_complete", "crew_load_complete", "official_block_out", "no_return"):
            for value in (None, True, "02:01"):
                with self.subTest(field=field, value=value), self.assertRaises(GoogleRainWriterError) as error:
                    write_google_rain_departure_milestone(mission, field, value, client_factory=client)
                self.assertEqual(error.exception.code, "uncertified_destination")
        client.assert_not_called()
