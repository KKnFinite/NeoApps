from datetime import datetime
import unittest

from app.models import SortDateMission, SortDateOperation
from app.services.departure_labels import departure_occurrences, destination_label


class DepartureLabelsTest(unittest.TestCase):
    def flight(self, mission_id, destination, wave, etd, kind="departure"):
        return SortDateMission(
            id=mission_id, flight_number=f"UPS{mission_id}", mission_type=kind,
            destination=destination, wave=wave, planned_datetime_utc=datetime.fromisoformat(etd),
        )

    def labels(self, *missions):
        ranks = departure_occurrences(missions, SortDateOperation(window_minutes=0))
        return {m.id: destination_label(m.destination, ranks.get(m.id)) for m in missions}

    def test_two_unassigned_sdf_departures(self):
        first = self.flight(1, "SDF", None, "2026-11-23T23:50")
        second = self.flight(2, "SDF", "", "2026-11-24T00:05")
        self.assertEqual(self.labels(first, second), {1: "SDF", 2: "SDF"})

    def test_wave_assigned_ewr_departures_follow_effective_etd_across_midnight(self):
        first = self.flight(8, "EWR", "1", "2026-11-23T23:50")
        second = self.flight(4, "EWR", "2", "2026-11-24T00:05")
        self.assertEqual(self.labels(second, first), {8: "1st EWR", 4: "2nd EWR"})
        first.eta_datetime_utc = datetime(2026, 11, 24, 0, 15)
        self.assertEqual(self.labels(second, first), {4: "1st EWR", 8: "2nd EWR"})

    def test_only_assigned_departures_take_places_in_mixed_group(self):
        missions = [
            self.flight(1, "EWR", " ", "2026-11-23T21:10"),
            self.flight(2, "EWR", "1", "2026-11-23T21:20"),
            self.flight(3, "EWR", "1", "2026-11-23T21:30", kind="arrival"),
            self.flight(4, "EWR", "2", "2026-11-23T21:40"),
            self.flight(5, "EWR", "2", "2026-11-23T21:50"),
        ]
        self.assertEqual(self.labels(*missions),
                         {1: "EWR", 2: "1st EWR", 3: "EWR", 4: "2nd EWR", 5: "3rd EWR"})

    def test_single_assigned_departure_stays_plain_with_or_without_unassigned_peer(self):
        assigned = self.flight(1, "SDF", "2", "2026-11-24T00:10")
        unassigned = self.flight(2, "SDF", None, "2026-11-24T00:20")
        self.assertEqual(self.labels(assigned), {1: "SDF"})
        self.assertEqual(self.labels(assigned, unassigned), {1: "SDF", 2: "SDF"})
