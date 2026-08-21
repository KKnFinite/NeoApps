import inspect
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.neoscorpion import (
    AssignmentPlanningSettings,
    DEFAULT_ASSIGNMENT_ETA_SAFETY_BUFFER_MINUTES,
)
from app.services.neoscorpion_dispatch_planning import assignment_mission_timing


AIRCRAFT_TYPES = ("B757", "A300", "B767ER", "B747-400", "B747-8")


def _settings(*, setup="2.25", finishing="1.25", buffer="5", rates=None):
    configured_rates = {
        aircraft_type: Decimal("100")
        for aircraft_type in AIRCRAFT_TYPES
    }
    configured_rates.update(rates or {})
    return AssignmentPlanningSettings(
        setup_minutes=Decimal(setup) if setup is not None else None,
        finishing_minutes=Decimal(finishing) if finishing is not None else None,
        eta_safety_buffer_minutes=Decimal(buffer),
        pump_rates_gallons_per_minute=configured_rates,
    )


def _mission(*, block_in=None, eta=None, departure=None):
    return SimpleNamespace(
        actual_block_in_datetime_utc=block_in,
        eta_datetime_utc=eta,
        planned_datetime_utc=departure,
    )


class NeoScorpionAssignmentTimingTest(unittest.TestCase):
    def test_duration_retains_fractional_pump_minutes_and_uses_aircraft_rate(self):
        ready = datetime(2026, 8, 20, 1, 0)
        timing = assignment_mission_timing(
            mission=_mission(block_in=ready, departure=datetime(2026, 8, 20, 3, 0)),
            operation=SimpleNamespace(window_minutes=60),
            aircraft_type="B757",
            planning_demand_gallons=650,
            planning_settings=_settings(rates={"B757": Decimal("100")}),
        )

        self.assertTrue(timing.available)
        self.assertEqual(timing.pump_rate_gallons_per_minute, Decimal("100"))
        self.assertEqual(timing.pump_minutes, Decimal("6.5"))
        self.assertEqual(timing.total_duration_minutes, Decimal("10.0"))
        self.assertEqual(timing.earliest_possible_finish_utc, ready + timedelta(minutes=10))

    def test_actual_block_in_is_authoritative_and_eta_buffer_is_used_when_needed(self):
        block_in = datetime(2026, 8, 20, 1, 10)
        eta = datetime(2026, 8, 20, 1, 0)
        common = {
            "operation": SimpleNamespace(window_minutes=60),
            "aircraft_type": "A300",
            "planning_demand_gallons": 100,
            "planning_settings": _settings(buffer="7"),
        }
        with_block = assignment_mission_timing(
            mission=_mission(
                block_in=block_in,
                eta=eta,
                departure=datetime(2026, 8, 20, 3, 0),
            ),
            **common,
        )
        with_eta = assignment_mission_timing(
            mission=_mission(eta=eta, departure=datetime(2026, 8, 20, 3, 0)),
            **common,
        )

        self.assertEqual(with_block.aircraft_ready_utc, block_in)
        self.assertEqual(with_block.aircraft_ready_source, "actual_block_in")
        self.assertEqual(with_eta.aircraft_ready_utc, eta + timedelta(minutes=7))
        self.assertEqual(with_eta.aircraft_ready_source, "eta_plus_buffer")

    def test_default_eta_buffer_comes_from_planning_settings_contract(self):
        settings = AssignmentPlanningSettings(
            setup_minutes=Decimal("1"),
            finishing_minutes=Decimal("1"),
            eta_safety_buffer_minutes=DEFAULT_ASSIGNMENT_ETA_SAFETY_BUFFER_MINUTES,
            pump_rates_gallons_per_minute={aircraft_type: Decimal("100") for aircraft_type in AIRCRAFT_TYPES},
        )
        eta = datetime(2026, 8, 20, 1, 0)
        timing = assignment_mission_timing(
            mission=_mission(eta=eta, departure=datetime(2026, 8, 20, 3, 0)),
            operation=SimpleNamespace(window_minutes=60),
            aircraft_type="B767ER",
            planning_demand_gallons=100,
            planning_settings=settings,
        )
        self.assertEqual(timing.aircraft_ready_utc, eta + timedelta(minutes=5))

    def test_arrival_has_no_planned_time_fallback(self):
        timing = assignment_mission_timing(
            mission=_mission(departure=datetime(2026, 8, 20, 3, 0)),
            operation=SimpleNamespace(window_minutes=60),
            aircraft_type="B757",
            planning_demand_gallons=100,
            planning_settings=_settings(),
        )
        self.assertFalse(timing.available)
        self.assertEqual(timing.unavailable_reason, "arrival_timing_unavailable")

    def test_deadline_offsets_cover_all_supported_aircraft(self):
        departure = datetime(2026, 8, 20, 3, 0)
        offsets = {"B757": 12, "A300": 13, "B767ER": 13, "B747-400": 12, "B747-8": 12}
        for aircraft_type, offset in offsets.items():
            with self.subTest(aircraft_type=aircraft_type):
                timing = assignment_mission_timing(
                    mission=_mission(block_in=datetime(2026, 8, 20, 1, 0), departure=departure),
                    operation=SimpleNamespace(window_minutes=60),
                    aircraft_type=aircraft_type,
                    planning_demand_gallons=100,
                    planning_settings=_settings(),
                )
                self.assertEqual(
                    timing.fuel_complete_deadline_utc,
                    departure + timedelta(minutes=60 - offset),
                )

    def test_deadline_feasibility_includes_exact_deadline_and_risk(self):
        departure = datetime(2026, 8, 20, 3, 0)
        common = {
            "operation": SimpleNamespace(window_minutes=60),
            "aircraft_type": "B757",
            "planning_demand_gallons": 100,
            "planning_settings": _settings(setup="1", finishing="1"),
        }
        feasible = assignment_mission_timing(
            mission=_mission(block_in=datetime(2026, 8, 20, 1, 0), departure=departure),
            **common,
        )
        exact = assignment_mission_timing(
            mission=_mission(block_in=datetime(2026, 8, 20, 3, 45), departure=departure),
            **common,
        )
        infeasible = assignment_mission_timing(
            mission=_mission(block_in=datetime(2026, 8, 20, 3, 46), departure=departure),
            **common,
        )
        self.assertTrue(feasible.deadline_feasible)
        self.assertTrue(exact.deadline_feasible)
        self.assertFalse(infeasible.deadline_feasible)

    def test_unavailable_reasons_and_defuel_duration_are_deterministic(self):
        mission = _mission(block_in=datetime(2026, 8, 20, 1, 0), departure=datetime(2026, 8, 20, 3, 0))
        common = {"mission": mission, "operation": SimpleNamespace(window_minutes=60), "planning_demand_gallons": 100}
        self.assertEqual(
            assignment_mission_timing(aircraft_type="UNCONFIGURED", planning_settings=_settings(), **common).unavailable_reason,
            "unsupported_aircraft",
        )
        self.assertEqual(
            assignment_mission_timing(aircraft_type="B757", planning_settings=_settings(setup=None), **common).unavailable_reason,
            "planning_settings_incomplete",
        )
        self.assertEqual(
            assignment_mission_timing(aircraft_type="B757", planning_settings=_settings(finishing=None), **common).unavailable_reason,
            "planning_settings_incomplete",
        )
        self.assertEqual(
            assignment_mission_timing(aircraft_type="B757", planning_settings=_settings(rates={"B757": None}), **common).unavailable_reason,
            "planning_settings_incomplete",
        )
        self.assertEqual(
            assignment_mission_timing(aircraft_type="B757", planning_settings=_settings(), planning_demand_gallons=None, mission=mission, operation=common["operation"]).unavailable_reason,
            "fuel_demand_unavailable",
        )
        self.assertEqual(
            assignment_mission_timing(aircraft_type="B757", planning_settings=_settings(), planning_demand_gallons=100, mission=_mission(block_in=mission.actual_block_in_datetime_utc), operation=common["operation"]).unavailable_reason,
            "departure_timing_unavailable",
        )

        defuel = assignment_mission_timing(
            mission=mission,
            operation=common["operation"],
            aircraft_type="B757",
            planning_demand_gallons=-650,
            planning_settings=_settings(rates={"B757": Decimal("100")}),
        )
        self.assertEqual(defuel.planning_demand_gallons, Decimal("-650"))
        self.assertEqual(defuel.pump_minutes, Decimal("6.5"))

    def test_timing_helper_is_pure_and_uses_caller_provided_demand(self):
        source = inspect.getsource(assignment_mission_timing)
        self.assertNotIn("query", source)
        self.assertNotIn("session", source)


if __name__ == "__main__":
    unittest.main()
