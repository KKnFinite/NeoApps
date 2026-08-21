import inspect
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.neoscorpion_dispatch_planning import (
    ResourceBusyWindow,
    ResourceCalendar,
    UnknownResourceCommitment,
    recommend_assignment_resources,
)


NOW = datetime(2026, 8, 20, 1, 0)


def _timing(*, feasible=True, available=True):
    return SimpleNamespace(
        available=available,
        deadline_feasible=feasible,
        aircraft_ready_utc=NOW,
        total_duration_minutes=Decimal("10"),
        fuel_complete_deadline_utc=NOW + timedelta(minutes=60),
    )


def _assignment(identifier=99, fueler=None, truck=None):
    return SimpleNamespace(id=identifier, assigned_fueler_user_id=fueler, assigned_truck_id=truck)


def _window(kind, identifier, assignment_id, start, finish):
    return ResourceBusyWindow(kind, identifier, assignment_id, assignment_id, start, finish, "x", True)


def _calendar(kind, identifier, windows=(), unknown=()):
    return ResourceCalendar(kind, identifier, tuple(windows), tuple(unknown), False)


class NeoScorpionAssignmentRecommendationsTest(unittest.TestCase):
    def test_recommends_earliest_feasible_pair_with_deterministic_tie_break(self):
        result = recommend_assignment_resources(
            assignment=_assignment(), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=({"id": 2, "sort_key": "B"}, {"id": 1, "sort_key": "A"}),
            truck_candidates=({"id": 20, "sort_key": "B"}, {"id": 10, "sort_key": "A"}),
            resource_calendars={},
        )
        self.assertTrue(result.available)
        self.assertEqual((result.fueler_id, result.truck_id), (1, 10))
        self.assertEqual(result.predicted_finish_utc, NOW + timedelta(minutes=10))

    def test_busy_resource_can_be_used_later_and_overlap_pair_is_excluded(self):
        calendars = {
            ("fueler", 1): _calendar("fueler", 1, [_window("fueler", 1, 1, NOW, NOW + timedelta(minutes=20))]),
            ("truck", 10): _calendar("truck", 10, [_window("truck", 10, 2, NOW, NOW + timedelta(minutes=55))]),
        }
        result = recommend_assignment_resources(
            assignment=_assignment(), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars=calendars,
        )
        self.assertFalse(result.available)
        self.assertEqual(result.unavailable_reason, "no_feasible_pair")
        calendars[("truck", 10)] = _calendar("truck", 10, [_window("truck", 10, 2, NOW, NOW + timedelta(minutes=15))])
        later = recommend_assignment_resources(
            assignment=_assignment(), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars=calendars,
        )
        self.assertTrue(later.available)
        self.assertEqual(later.feasible_start_utc, NOW + timedelta(minutes=20))

    def test_unknown_and_partial_assignments_are_handled_safely(self):
        unknown = UnknownResourceCommitment("fueler", 1, 1, 1, "existing_commitment_timing_unavailable")
        calendars = {("fueler", 1): _calendar("fueler", 1, unknown=(unknown,))}
        blocked = recommend_assignment_resources(
            assignment=_assignment(), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars=calendars,
        )
        self.assertEqual(blocked.unavailable_reason, "no_feasible_pair")
        fixed_fueler = recommend_assignment_resources(
            assignment=_assignment(fueler=2), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars={},
        )
        self.assertEqual((fixed_fueler.fueler_id, fixed_fueler.truck_id), (2, 10))
        fixed_truck = recommend_assignment_resources(
            assignment=_assignment(truck=20), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars={},
        )
        self.assertEqual((fixed_truck.fueler_id, fixed_truck.truck_id), (1, 20))
        both = recommend_assignment_resources(
            assignment=_assignment(fueler=2, truck=20), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars={},
        )
        self.assertEqual(both.unavailable_reason, "already_assigned")

    def test_self_exclusion_and_no_result_reasons(self):
        calendars = {("fueler", 1): _calendar("fueler", 1, [_window("fueler", 1, 99, NOW, NOW + timedelta(minutes=50))])}
        result = recommend_assignment_resources(
            assignment=_assignment(99), mission_timing=_timing(), now_utc=NOW,
            fueler_candidates=(1,), truck_candidates=(10,), resource_calendars=calendars,
        )
        self.assertTrue(result.available)
        self.assertEqual(
            recommend_assignment_resources(assignment=_assignment(), mission_timing=_timing(available=False), now_utc=NOW, fueler_candidates=(1,), truck_candidates=(10,), resource_calendars={}).unavailable_reason,
            "mission_timing_unavailable",
        )
        self.assertEqual(
            recommend_assignment_resources(assignment=_assignment(), mission_timing=_timing(), now_utc=NOW, fueler_candidates=(), truck_candidates=(10,), resource_calendars={}).unavailable_reason,
            "no_eligible_fueler",
        )
        self.assertEqual(
            recommend_assignment_resources(assignment=_assignment(), mission_timing=_timing(), now_utc=NOW, fueler_candidates=(1,), truck_candidates=(), resource_calendars={}).unavailable_reason,
            "no_eligible_truck",
        )

    def test_recommendation_helper_is_pure(self):
        source = inspect.getsource(recommend_assignment_resources)
        self.assertNotIn("query", source)
        self.assertNotIn("session", source)


if __name__ == "__main__":
    unittest.main()
