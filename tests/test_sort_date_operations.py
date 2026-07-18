from datetime import date, datetime, time
import unittest

from app.models import SortDateMission, SortDateOperation
from app.services.sort_date_operations import (
    create_sort_date_operation,
    mission_display_timing_data,
    normalize_window_minutes,
)


class SortDateOperationsTest(unittest.TestCase):
    def test_operation_default_window_minutes_is_zero(self):
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        self.assertEqual(operation.window_minutes, 0)

    def test_negative_window_minutes_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_window_minutes(-1)

        with self.assertRaises(ValueError):
            create_sort_date_operation(
                sort_date=date(2026, 6, 1),
                gateway_code="RFD",
                sort_name="night",
                window_minutes=-1,
            )

        with self.assertRaises(ValueError):
            SortDateOperation(
                sort_date=date(2026, 6, 1),
                gateway_code="RFD",
                sort_name="night",
                window_minutes=-1,
            )

    def test_positive_window_adjusts_departure_times_forward(self):
        operation = self._operation(window_minutes=20)
        mission = self._mission(
            mission_type="departure",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
        )

        data = mission_display_timing_data(mission, operation)

        self.assertEqual(data["adjusted_planned_departure_time"], datetime(2026, 6, 1, 2, 30))
        self.assertEqual(data["adjusted_pure_pull_time"], time(1, 40))
        self.assertEqual(data["adjusted_mix_pull_time"], time(2, 15))

    def test_wave_specific_window_overrides_default_window(self):
        operation = self._operation(
            window_minutes=20,
            first_wave_window_minutes=5,
            second_wave_window_minutes=30,
        )
        first_wave_mission = self._mission(
            mission_type="departure",
            wave="1",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
            pure_pull_time_local=time(1, 20),
        )
        second_wave_mission = self._mission(
            mission_type="departure",
            wave="2",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
            pure_pull_time_local=time(1, 20),
        )

        first_wave_data = mission_display_timing_data(first_wave_mission, operation)
        second_wave_data = mission_display_timing_data(second_wave_mission, operation)

        self.assertEqual(first_wave_data["effective_window_minutes"], 5)
        self.assertEqual(first_wave_data["adjusted_planned_departure_time"], datetime(2026, 6, 1, 2, 15))
        self.assertEqual(first_wave_data["adjusted_pure_pull_time"], time(1, 25))
        self.assertEqual(second_wave_data["effective_window_minutes"], 30)
        self.assertEqual(second_wave_data["adjusted_planned_departure_time"], datetime(2026, 6, 1, 2, 40))
        self.assertEqual(second_wave_data["adjusted_pure_pull_time"], time(1, 50))

    def test_wave_window_falls_back_to_default_when_blank(self):
        operation = self._operation(
            window_minutes=20,
            first_wave_window_minutes=None,
            second_wave_window_minutes=35,
        )
        mission = self._mission(
            mission_type="departure",
            wave="1",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
        )

        data = mission_display_timing_data(mission, operation)

        self.assertEqual(data["effective_window_minutes"], 20)
        self.assertEqual(data["adjusted_planned_departure_time"], datetime(2026, 6, 1, 2, 30))

    def test_blank_wave_uses_default_window_not_wave_specific_windows(self):
        operation = self._operation(
            window_minutes=20,
            first_wave_window_minutes=5,
            second_wave_window_minutes=35,
        )
        mission = self._mission(
            mission_type="departure",
            wave=None,
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
        )

        data = mission_display_timing_data(mission, operation)

        self.assertIsNone(data["wave"])
        self.assertEqual(data["effective_window_minutes"], 20)
        self.assertEqual(data["adjusted_planned_departure_time"], datetime(2026, 6, 1, 2, 30))

    def test_window_does_not_mutate_base_mission_times(self):
        operation = self._operation(window_minutes=20)
        mission = self._mission(
            mission_type="departure",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
        )

        mission_display_timing_data(mission, operation)

        self.assertEqual(mission.planned_datetime_local, datetime(2026, 6, 1, 2, 10))
        self.assertEqual(mission.pure_pull_time_local, time(1, 20))
        self.assertEqual(mission.mix_pull_time_local, time(1, 55))

    def test_window_does_not_apply_to_arrivals(self):
        operation = self._operation(window_minutes=20)
        mission = self._mission(
            mission_type="arrival",
            planned_datetime_local=datetime(2026, 6, 1, 2, 10),
            eta_datetime_utc=datetime(2026, 6, 1, 7, 15),
            actual_block_in_datetime_utc=datetime(2026, 6, 1, 7, 20),
        )

        data = mission_display_timing_data(mission, operation)

        self.assertEqual(data["adjusted_planned_arrival_time"], datetime(2026, 6, 1, 2, 10))
        self.assertEqual(data["adjusted_eta_time_utc"], datetime(2026, 6, 1, 7, 15))
        self.assertEqual(data["adjusted_actual_block_in_time_utc"], datetime(2026, 6, 1, 7, 20))

    def test_operation_can_have_many_missions(self):
        operation = self._operation()
        arrival = self._mission(mission_type="arrival", flight_number="5X123")
        departure = self._mission(mission_type="departure", flight_number="5X456")

        operation.missions.extend([arrival, departure])

        self.assertEqual(operation.missions, [arrival, departure])
        self.assertIs(arrival.sort_date_operation, operation)
        self.assertIs(departure.sort_date_operation, operation)

    def test_mission_belongs_to_operation(self):
        operation = self._operation()
        mission = self._mission(mission_type="departure")

        mission.sort_date_operation = operation

        self.assertIs(mission.sort_date_operation, operation)
        self.assertIn(mission, operation.missions)

    def _operation(self, window_minutes=0, **overrides):
        values = {
            "sort_date": date(2026, 6, 1),
            "gateway_code": "RFD",
            "sort_name": "night",
            "window_minutes": window_minutes,
        }
        values.update(overrides)
        return SortDateOperation(
            **values,
        )

    def _mission(self, mission_type, flight_number="5X123", **overrides):
        values = {
            "sort_date": date(2026, 6, 1),
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": mission_type,
            "flight_number": flight_number,
            "origin": "SDF" if mission_type == "arrival" else "RFD",
            "destination": "RFD" if mission_type == "arrival" else "SDF",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 10),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 10),
        }
        values.update(overrides)
        return SortDateMission(**values)


if __name__ == "__main__":
    unittest.main()
