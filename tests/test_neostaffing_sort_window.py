"""Staffing publisher uses the configured Night lookup window, never scheduled-only."""
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from app.services import neostaffing as staffing
from app.services.neostaffing_sort_window import active_night_operation
from tests.test_neostaffing_attendance_counts import NeoStaffingAttendanceCountsTest as Fixture


class NightSortWindowTest(unittest.TestCase):
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown

    def test_overnight_start_end_and_midnight_boundary(self):
        settings = SimpleNamespace(sort_window_start_local=time(21, 0),
                                   sort_window_end_local=time(5, 0))
        today = self.operation.sort_date
        with patch.object(staffing, 'current_night_attendance_operation',
                          return_value=self.operation), patch(
                          'app.services.gateway_matrix._sort_timeline_sort_setting',
                          return_value=settings):
            self.assertIsNone(active_night_operation(
                local_now=datetime.combine(today, time(20, 59))))
            self.assertEqual(active_night_operation(
                local_now=datetime.combine(today, time(21, 0))).id,
                             self.operation.id)
            self.assertEqual(active_night_operation(
                local_now=datetime.combine(today + timedelta(days=1), time(4, 59))).id,
                             self.operation.id)
            self.assertIsNone(active_night_operation(
                local_now=datetime.combine(today + timedelta(days=1), time(5, 0))))

    def test_missing_window_fails_closed_instead_of_legacy_all_day(self):
        settings = SimpleNamespace(sort_window_start_local=None,
                                   sort_window_end_local=time(5, 0))
        with patch.object(staffing, 'current_night_attendance_operation',
                          return_value=self.operation), patch(
                          'app.services.gateway_matrix._sort_timeline_sort_setting',
                          return_value=settings):
            self.assertIsNone(active_night_operation(
                local_now=datetime.combine(self.operation.sort_date,time(22,0))))

    def test_unavailable_or_wrong_gateway_does_not_publish(self):
        with patch.object(staffing, 'current_night_attendance_operation',
                          return_value=None):
            self.assertIsNone(active_night_operation())
        with patch.object(staffing, '_attendance_gateway', return_value=None):
            self.assertIsNone(active_night_operation())


if __name__ == '__main__':
    unittest.main()
