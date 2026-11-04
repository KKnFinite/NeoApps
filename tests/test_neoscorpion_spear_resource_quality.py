"""Resource truth, not a new scheduling policy or historical attribution."""
from dataclasses import replace
import unittest

from tests import test_neoscorpion_spear as scenarios
from tests import test_neoscorpion_fueler_handoff as canonical
from app.services.neoscorpion_spear import _current_resource_owners
from app.services import neoscorpion as fuel


class SpearResourceQualityTest(unittest.TestCase):
    def test_independent_fueler_origin_matches_timing_explanation(self):
        settings = replace(scenarios.SpearSettings(), truck_begins_at='Charlie', fueler_begins_at='Alpha')
        step = scenarios._plan([scenarios._row()], settings=settings).steps[0]
        self.assertEqual(step.explanation['truck_travel_minutes'], '0 min')
        self.assertEqual(step.explanation['fueler_travel_minutes'], '2 min')
        self.assertEqual(step.fueler_location_provenance['location'], 'Alpha')

    def occupied(self, base, reserved, demand):
        owner = scenarios._row(200, assignment=scenarios.NeoScorpionSpearPlanningTest().commitment())
        owner.update(inventory_projection_bases={10:base}, inventory_demand_gallons=reserved)
        return scenarios._plan([scenarios._row(100,demand=demand),owner]).occupied_truck_next[10]

    def test_next_forecast_respects_already_reserved_verified_inventory(self):
        # Raw truck reading 2000, but only 1000 remains after committed work.
        # An 800-gallon next job would violate the existing 500-gallon reserve.
        self.assertIsNone(self.occupied(1000,0,800).step)

    def test_next_forecast_does_not_double_reserve_current_work(self):
        # Existing projection already accounts for this 500-gallon job. Another
        # subtraction incorrectly hides a feasible 1100-gallon next job.
        result = self.occupied(1700,0,1100)
        self.assertIsNotNone(result.step)
        self.assertFalse(result.step.automatic_eligible)
        self.assertTrue(result.step.requires_resource_release)

    def test_active_canonical_handoff_reserves_current_fueler_only(self):
        case=canonical.FuelerHandoffTest();case.setUp()
        try:
            case.prepare();replacement=case.replacement();case.swap(replacement)
            context=fuel.fuel_dispatch_context(case.gateway,include_spear=False)
            fuelers,trucks=_current_resource_owners(context['rows'])
            self.assertNotIn(case.fueler.id,fuelers)
            self.assertEqual(fuelers[replacement.id],{case.mission.id})
            self.assertEqual(trucks[case.truck.id],{case.mission.id})
            self.assertEqual(case.work.physical_fueler_user_id,case.fueler.id)
        finally:case.doCleanups()
