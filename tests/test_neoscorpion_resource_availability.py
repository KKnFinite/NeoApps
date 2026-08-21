import inspect
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.neoscorpion import AssignmentPlanningSettings
from app.services.neoscorpion_dispatch_planning import (
    ResourceBusyWindow,
    build_resource_calendars,
    find_earliest_resource_gap,
)


NOW = datetime(2026, 8, 20, 1, 0)
TYPES = ("B757", "A300", "B767ER", "B747-400", "B747-8")


def _settings():
    return AssignmentPlanningSettings(
        setup_minutes=Decimal("1"), finishing_minutes=Decimal("1"),
        eta_safety_buffer_minutes=Decimal("5"),
        pump_rates_gallons_per_minute={aircraft: Decimal("100") for aircraft in TYPES},
    )


def _record(identifier, *, fueler=None, truck=None, on=None, off=None, completed=None,
            block_in=NOW, eta=None, departure=None, demand=100, aircraft="B757"):
    return SimpleNamespace(
        assignment=SimpleNamespace(
            id=identifier, assigned_fueler_user_id=fueler, assigned_truck_id=truck,
            completed_at_utc=completed, fuel_on_board_at_utc=None,
        ),
        mission=SimpleNamespace(
            id=identifier, actual_block_in_datetime_utc=block_in,
            eta_datetime_utc=eta, planned_datetime_utc=departure or NOW + timedelta(hours=2),
        ),
        work_state=SimpleNamespace(on_at_utc=on, off_at_utc=off, ended_early_at_utc=None),
        aircraft_type=aircraft,
        planning_demand_gallons=demand,
    )


class NeoScorpionResourceAvailabilityTest(unittest.TestCase):
    def _calendars(self, records, *, now=NOW, exclude=None):
        return build_resource_calendars(
            records, operation=SimpleNamespace(window_minutes=60),
            planning_settings=_settings(), now_utc=now,
            exclude_assignment_id=exclude,
        )

    def test_unstarted_windows_and_resource_calendars_are_independent(self):
        future_ready = NOW + timedelta(minutes=10)
        calendars = self._calendars([
            _record(1, fueler=10, truck=20, block_in=future_ready),
            _record(2, fueler=11, block_in=future_ready),
        ])
        fueler = calendars[("fueler", 10)]
        truck = calendars[("truck", 20)]
        self.assertEqual(fueler.busy_windows[0].start_utc, future_ready)
        self.assertEqual(fueler.busy_windows[0].finish_utc, future_ready + timedelta(minutes=3))
        self.assertEqual(truck.busy_windows[0].assignment_id, 1)
        self.assertNotIn(("truck", 11), calendars)

    def test_active_start_is_authoritative_and_availability_never_ends_in_past(self):
        calendars = self._calendars([_record(1, fueler=10, on=NOW - timedelta(minutes=10))])
        window = calendars[("fueler", 10)].busy_windows[0]
        self.assertEqual(window.start_source, "actual_on")
        self.assertEqual(window.start_utc, NOW - timedelta(minutes=10))
        self.assertEqual(window.finish_utc, NOW)

    def test_finished_assignments_do_not_block(self):
        calendars = self._calendars([_record(1, fueler=10, off=NOW - timedelta(minutes=1))])
        self.assertEqual(calendars, {})

    def test_manual_overlaps_are_preserved_and_union_is_busy(self):
        calendars = self._calendars([
            _record(1, fueler=10, on=NOW),
            _record(2, fueler=10, on=NOW + timedelta(minutes=1)),
        ])
        calendar = calendars[("fueler", 10)]
        self.assertEqual(len(calendar.busy_windows), 2)
        self.assertTrue(calendar.has_manual_overlap)
        gap = find_earliest_resource_gap(
            candidate_earliest_start_utc=NOW, candidate_duration_minutes=Decimal("1"),
            busy_windows=calendar.busy_windows, now_utc=NOW,
        )
        self.assertEqual(gap.start_utc, NOW + timedelta(minutes=4))

    def test_unknown_timing_is_an_explicit_unsafe_commitment(self):
        calendars = self._calendars([_record(1, truck=20, demand=None)])
        calendar = calendars[("truck", 20)]
        self.assertFalse(calendar.busy_windows)
        self.assertEqual(calendar.unknown_commitments[0].reason, "existing_commitment_timing_unavailable")
        gap = find_earliest_resource_gap(
            candidate_earliest_start_utc=NOW, candidate_duration_minutes=1,
            busy_windows=calendar.busy_windows,
            unknown_commitments=calendar.unknown_commitments, now_utc=NOW,
        )
        self.assertFalse(gap.available)
        self.assertEqual(gap.unavailable_reason, "existing_commitment_timing_unavailable")

    def test_gap_finder_allows_boundaries_and_handles_deadlines(self):
        windows = (
            ResourceBusyWindow("fueler", 1, 1, 1, NOW + timedelta(minutes=10), NOW + timedelta(minutes=20), "x", None),
            ResourceBusyWindow("fueler", 1, 2, 2, NOW + timedelta(minutes=30), NOW + timedelta(minutes=40), "x", None),
        )
        before = find_earliest_resource_gap(candidate_earliest_start_utc=NOW, candidate_duration_minutes=10, busy_windows=windows, now_utc=NOW)
        between = find_earliest_resource_gap(candidate_earliest_start_utc=NOW + timedelta(minutes=20), candidate_duration_minutes=10, busy_windows=windows, now_utc=NOW)
        after = find_earliest_resource_gap(candidate_earliest_start_utc=NOW + timedelta(minutes=35), candidate_duration_minutes=5, busy_windows=windows, now_utc=NOW)
        exact = find_earliest_resource_gap(candidate_earliest_start_utc=NOW, candidate_duration_minutes=10, busy_windows=(), now_utc=NOW, completion_deadline_utc=NOW + timedelta(minutes=10))
        late = find_earliest_resource_gap(candidate_earliest_start_utc=NOW, candidate_duration_minutes=11, busy_windows=(), now_utc=NOW, completion_deadline_utc=NOW + timedelta(minutes=10))
        self.assertEqual(before.start_utc, NOW)
        self.assertEqual(between.start_utc, NOW + timedelta(minutes=20))
        self.assertEqual(after.start_utc, NOW + timedelta(minutes=40))
        self.assertTrue(exact.available)
        self.assertFalse(late.available)
        self.assertEqual(late.unavailable_reason, "completion_deadline_infeasible")

    def test_candidate_can_exclude_its_own_assignment(self):
        calendars = self._calendars([_record(1, fueler=10)], exclude=1)
        self.assertEqual(calendars, {})

    def test_resource_helpers_are_pure(self):
        self.assertNotIn("query", inspect.getsource(build_resource_calendars))
        self.assertNotIn("session", inspect.getsource(find_earliest_resource_gap))


if __name__ == "__main__":
    unittest.main()
