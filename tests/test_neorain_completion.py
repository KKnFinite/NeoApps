from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest

from app.neonodes.neorain.services import _inbound_late_summary, _outbound_late_summary


def flight(direction, status, seconds, *, included=None, wave="1"):
    scheduled = datetime(2026, 11, 20, 4)
    actual = scheduled + timedelta(seconds=seconds) if seconds is not None else None
    return SimpleNamespace(
        mission_type=direction, wave=wave, late_metrics_included_override=included,
        planned_datetime_utc=scheduled,
        arrival_status=status if direction == "arrival" else None,
        departure_status=status if direction == "departure" else None,
        actual_block_in_datetime_utc=actual if direction == "arrival" else None,
        actual_block_out_datetime_utc=actual if direction == "departure" else None,
    )


class NeoRainCompletionTest(unittest.TestCase):
    def test_stale_actuals_never_register_as_late(self):
        for direction, summary, statuses in (
            ("arrival", _inbound_late_summary, ("cancelled", "scheduled", "en_route", "on_ground")),
            ("departure", _outbound_late_summary, ("cancelled", "scheduled", "blocked_out", "crew_load_complete")),
        ):
            for status in statuses:
                with self.subTest(direction=direction, status=status):
                    total = summary([flight(direction, status, 540, included=True)])["total"]
                    self.assertEqual((total["aircraft_late"], total["late_minutes"]), (0, 0))

    def test_november_19_audit_readback(self):
        # The ten matched, completed flight lines from the November 19 RFD
        # audit. Pending/cancelled no-op rows cannot add minutes to this replay.
        arrivals = [flight("arrival", "arrived", minutes * 60) for minutes in (43, 13, 9)]
        arrivals.append(flight("arrival", "cancelled", 540))
        departures = [flight("departure", "departed", minutes * 60) for minutes in (9, 10, 3, 11, 1, 15, 15)]
        departures.append(flight("departure", "blocked_out", 540))
        for summary, count, minutes in ((_inbound_late_summary(arrivals), 3, 65),
                                        (_outbound_late_summary(departures), 7, 64)):
            self.assertEqual((summary["total"]["aircraft_late"], summary["total"]["late_minutes"]), (count, minutes))

    def test_completed_timing_keeps_whole_minute_and_inclusion_rules(self):
        for direction, status, summary in (
            ("arrival", "arrived", _inbound_late_summary),
            ("departure", "departed", _outbound_late_summary),
        ):
            missions = [flight(direction, status, seconds) for seconds in (-90, 0, 59, 90, 540, None)]
            missions.append(flight(direction, status, 240, included=False))
            missions.append(flight(direction, status, 180, wave=None))
            missions.append(flight(direction, status, 120, included=True, wave=None))
            total = summary(missions)["total"]
            self.assertEqual((total["aircraft_late"], total["late_minutes"]), (3, 12))
