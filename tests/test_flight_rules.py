import unittest

from app.models import Crew, SortDateCrewAssignment, SortDateTailState
from app.services.flight_rules import (
    crew_sections_for_tail_swap,
    default_required_crew_sections,
    derive_aircraft_type_from_tail_number,
    is_deice_complete,
    is_mission_crew_covered,
    match_api_flight_number,
    resolve_aircraft_type_for_tail_state,
)


class FlightRulesTest(unittest.TestCase):
    def test_aircraft_type_derivation(self):
        self.assertEqual(derive_aircraft_type_from_tail_number("N123UP"), "A300")
        self.assertEqual(derive_aircraft_type_from_tail_number("N345UP"), "767")
        self.assertEqual(derive_aircraft_type_from_tail_number("N456UP"), "757")
        self.assertEqual(derive_aircraft_type_from_tail_number("N654UP"), "747")
        self.assertEqual(derive_aircraft_type_from_tail_number("N254UP"), "unknown")
        self.assertEqual(derive_aircraft_type_from_tail_number("LEASED1"), "unknown")

    def test_manual_aircraft_type_is_preserved(self):
        tail_state = SortDateTailState(
            tail_number="N123UP",
            aircraft_type="A330",
            aircraft_type_source="manual",
        )

        self.assertEqual(resolve_aircraft_type_for_tail_state(tail_state), "A330")

    def test_exact_flight_number_match_wins(self):
        stored = ["0123", "123"]

        self.assertEqual(match_api_flight_number("123", stored), "123")

    def test_leading_zero_fallback_preserves_stored_number(self):
        stored = ["0123"]

        self.assertEqual(match_api_flight_number("123", stored), "0123")

    def test_api_leading_zero_does_not_fallback(self):
        stored = ["123"]

        self.assertIsNone(match_api_flight_number("0123", stored))

    def test_default_crew_sections_by_aircraft_type(self):
        self.assertEqual(
            default_required_crew_sections("A300"),
            ("topside", "front_p", "rear_p", "ab"),
        )
        self.assertEqual(
            default_required_crew_sections("757"),
            ("topside", "belly_31", "belly_34"),
        )
        self.assertEqual(default_required_crew_sections("unknown"), ("topside", "other"))

    def test_tail_swap_same_family_keeps_all_sections(self):
        result = crew_sections_for_tail_swap(("topside", "front_p", "ab"), "A300", "767")

        self.assertEqual(result["keep"], ("topside", "front_p", "ab"))
        self.assertEqual(result["rebuild"], ())

    def test_tail_swap_cross_family_keeps_topside_only(self):
        result = crew_sections_for_tail_swap(
            ("topside", "front_p", "rear_p", "ab"),
            "A300",
            "757",
        )

        self.assertEqual(result["keep"], ("topside",))
        self.assertEqual(result["rebuild"], ("front_p", "rear_p", "ab"))

    def test_tail_swap_unknown_keeps_topside_only(self):
        result = crew_sections_for_tail_swap(("topside", "belly_31"), "unknown", "757")

        self.assertEqual(result["keep"], ("topside",))
        self.assertEqual(result["rebuild"], ("belly_31",))

    def test_deice_complete_derivation(self):
        self.assertTrue(is_deice_complete(SortDateTailState(pretreat_status=True)))
        self.assertTrue(is_deice_complete(SortDateTailState(deice_status="cleared")))
        self.assertFalse(is_deice_complete(SortDateTailState(deice_status="required")))

        tail_state = SortDateTailState(pretreat_status=True)
        self.assertTrue(tail_state.deice_complete)

    def test_mission_crew_covered_requires_active_crew_for_required_sections(self):
        covered_assignments = [
            SortDateCrewAssignment(required=True, crew=Crew(name="Topside", active=True)),
            SortDateCrewAssignment(required=False, crew=None),
        ]
        uncovered_assignments = [
            SortDateCrewAssignment(required=True, crew=Crew(name="Topside", active=False)),
        ]

        self.assertTrue(is_mission_crew_covered(covered_assignments))
        self.assertFalse(is_mission_crew_covered(uncovered_assignments))


if __name__ == "__main__":
    unittest.main()
