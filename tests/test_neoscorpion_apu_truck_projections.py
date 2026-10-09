"""APU burn is a projection adjustment, never canonical or physical demand."""
import unittest

from sqlalchemy import event

from app.extensions import db
from app.models import (
    NeoScorpionFuelingEvent, NeoScorpionFuelTankState, NeoScorpionFuelWorkState,
    NeoScorpionSettings, NeoScorpionSortTruck,
)
from app.services import neoscorpion as service
from tests import test_neoscorpion_dispatch_planning as dispatch_fixture


class ApuTruckProjectionsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = dispatch_fixture.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway = self.fixture.gateway
        self.truck = self.fixture._nightly_truck()
        self.mission = self.fixture._mission("UPS901", "N491UP", 25_400, 1)
        self.fixture._tail_fuel(self.mission, inbound_lbs=12_000)
        self.assignment = self.fixture._assignment(self.mission, self.truck)
        self.work = NeoScorpionFuelWorkState(
            fuel_assignment_id=self.assignment.id, tail_number=self.mission.assigned_tail_number,
            apu_running=True, automatic_apu_allowance_lbs=670, apu_allowance_lbs=670,
            apu_source_tank_code="left",
        )
        db.session.add(self.work)
        db.session.commit()

    def context(self):
        db.session.commit()
        return service.fuel_dispatch_context(self.gateway)

    def row(self):
        return self.context()["rows"][0]

    def test_recommended_allowance_changes_only_projected_inventory(self):
        self.work.apu_running = False
        before = self.context()
        report_before = service.fuel_report_context(self.gateway)["sort_fuel_totals"]
        self.work.apu_running = True
        # Effective automatic snapshot, not a stale legacy compatibility value.
        self.work.apu_allowance_lbs = 9999
        after = self.context()
        row = after["rows"][0]
        self.assertEqual(row["estimated_fuel_gallons"], 2000)
        self.assertEqual(row["planning_demand_gallons"], 2000)
        self.assertEqual(row["truck_projection_demand_gallons"], 2100)
        self.assertEqual(row["projected_truck_gallons"], 7900)
        self.assertEqual(after["truck_visuals"][0]["projected_usage_gallons"], 2100)
        self.assertEqual(row["truck_visual"]["projected_display"], "7,900 gal")
        self.assertEqual(after["sort_fuel_totals"], before["sort_fuel_totals"])
        self.assertEqual(service.fuel_report_context(self.gateway)["sort_fuel_totals"], report_before)
        self.assertEqual(after["spear_plan"].token, before["spear_plan"].token)
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons, 10_000)
        self.assertEqual(self.truck.remaining_fuel_gallons, 777)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_manual_allowance_including_zero_overrides_recommendation(self):
        self.work.apu_override_enabled = True
        for pounds, demand in ((1340, 2200), (0, 2000)):
            with self.subTest(pounds=pounds):
                self.work.apu_override_allowance_lbs = pounds
                row = self.row()
                self.assertEqual(row["truck_projection_demand_gallons"], demand)
                self.assertEqual(row["projected_truck_gallons"], 10_000 - demand)
                self.assertEqual(row["planning_demand_gallons"], 2000)
                self.assertEqual(row["estimated_fuel_gallons"], 2000)
        self.work.apu_override_enabled = False
        self.assertEqual(self.row()["projected_truck_gallons"], 7900)

    def test_off_unconfirmed_and_legacy_allowance(self):
        self.work.apu_override_enabled = True
        self.work.apu_override_allowance_lbs = 1340
        for running in (False, None):
            with self.subTest(running=running):
                self.work.apu_running = running
                self.assertEqual(self.row()["projected_truck_gallons"], 8000)
        self.work.apu_running = True
        self.work.apu_override_enabled = False
        self.work.automatic_apu_allowance_lbs = None
        self.assertEqual(self.row()["projected_truck_gallons"], 7900)
        db.session.delete(self.work)
        self.assertEqual(self.row()["projected_truck_gallons"], 8000)

    def test_gateway_density_and_existing_whole_gallon_rounding(self):
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id,
                                          fuel_density_lbs_per_gallon=7.5))
        self.mission.planned_fuel_load = 27_000
        for pounds, demand in ((750, 2100), (754, 2101)):
            with self.subTest(pounds=pounds):
                self.work.automatic_apu_allowance_lbs = pounds
                row = self.row()
                self.assertEqual(row["estimated_fuel_gallons"], 2000)
                self.assertEqual(row["truck_projection_demand_gallons"], demand)

    def test_shared_truck_cumulative_projections_and_visual_bars(self):
        second = self.fixture._mission("UPS902", "N492UP", 25_400, 2)
        assignment = self.fixture._assignment(second, self.truck)
        db.session.add(NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
            tail_number=second.assigned_tail_number, apu_running=True,
            automatic_apu_allowance_lbs=670, apu_override_enabled=True,
            apu_override_allowance_lbs=1340, apu_source_tank_code="left"))
        context = self.context()
        self.assertEqual([r["projected_truck_gallons"] for r in context["rows"]], [7900, 5700])
        self.assertEqual(context["truck_visuals"][0]["projected_gallons"], 5700)
        self.assertEqual(context["truck_visuals"][0]["projected_percent"], 29)
        self.assertEqual(context["truck_visuals"][0]["projected_usage_gallons"], 4300)
        self.assertEqual(context["sort_fuel_totals"]["estimated"]["value"], 4000)

    def test_tf_precedence_defuel_and_completion(self):
        for kind, transfer, projected in (("fuel", 2500, 7500), ("fuel", 0, 10_000),
                ("uplift", 2500, 7500), ("defuel", 200, 10_200), ("defuel", None, None)):
            with self.subTest(kind=kind, transfer=transfer):
                self.assignment.current_cycle_type = kind
                self.assignment.transfer_fuel_gallons = transfer
                row = self.row()
                self.assertEqual(row["projected_truck_gallons"], projected)
                self.assertEqual(row["truck_projection_demand_gallons"], row["planning_demand_gallons"])
        self.assignment.current_cycle_type = "fuel"
        self.assignment.transfer_fuel_gallons = 2500
        self.assignment.review_status = "complete"
        self.mission.fuel_status = "complete"
        context = self.context()
        self.assertIsNone(context["rows"][0]["projected_truck_gallons"])
        self.assertIsNone(context["truck_visuals"][0]["projected_gallons"])

    def test_uplift_uses_current_measured_demand_then_tf(self):
        self.assignment.current_cycle_type = "uplift"
        self.assignment.current_cycle_number = 2
        for code in ("left", "ctr", "right"):
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=self.work.id,
                                                  tank_code=code, remaining_lbs=4000))
        row = self.row()
        self.assertEqual(row["estimated_fuel_source"], "measured_fob")
        self.assertEqual(row["estimated_fuel_gallons"], 2000)
        self.assertEqual(row["projected_truck_gallons"], 7900)
        self.assignment.transfer_fuel_gallons = 2300
        self.assertEqual(self.row()["projected_truck_gallons"], 7700)

    def test_missing_inputs_propagate_without_guessing(self):
        self.work.automatic_apu_allowance_lbs = None
        self.work.apu_allowance_lbs = None
        row = self.row()
        self.assertEqual(row["estimated_fuel_gallons"], 2000)
        self.assertEqual(row["projected_truck_display"], "INCOMPLETE")
        self.work.automatic_apu_allowance_lbs = 670
        settings = NeoScorpionSettings(gateway_id=self.gateway.id, fuel_density_lbs_per_gallon=None)
        db.session.add(settings)
        db.session.flush()
        settings.fuel_density_lbs_per_gallon = None
        self.assertEqual(self.row()["projected_truck_display"], "INCOMPLETE")
        settings.fuel_density_lbs_per_gallon = 6.7
        self.mission.planned_fuel_load = None
        self.assertEqual(self.row()["projected_truck_display"], "INCOMPLETE")

    def test_apu_projection_adds_no_queries_or_writes_to_board_read(self):
        statements = []
        def collect(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", collect)
        self.addCleanup(event.remove, db.engine, "before_cursor_execute", collect)
        counts = []
        for running in (False, True):
            self.work.apu_running = running
            db.session.commit()
            statements.clear()
            service.fuel_dispatch_context(self.gateway)
            self.assertFalse(any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                                 for s in statements))
            counts.append(len(statements))
        self.assertEqual(counts[0], counts[1])
