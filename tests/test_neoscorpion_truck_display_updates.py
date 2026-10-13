"""Read-only truck activity, menu ordering and independent Actual/APU display."""
import unittest
from datetime import datetime

from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionSortTruck, SortDateParkingAssignment
from app.services import neoscorpion as service
from tests import test_neoscorpion_fueling_status as fixtures
from tests.html_contracts import document


class ScorpionDisplayUpdatesTest(unittest.TestCase):
    setUp = fixtures.FuelingStatusIntegrationTest.setUp
    login = fixtures.FuelingStatusIntegrationTest.login
    work = fixtures.FuelingStatusIntegrationTest.work
    row = fixtures.FuelingStatusIntegrationTest.row

    def test_actual_and_apu_independent_display_all_missing_zero_and_override_cases(self):
        self.login(self.dispatcher)
        work = self.work(remaining=None, actual=None, apu=None, allowance=None)
        for running, automatic, override, expected in (
            (None, 500, None, "-"), (False, 0, None, "0.0"),
            (True, 500, None, "0.5"), (True, None, 1200, "1.2"),
            (True, None, None, "-"), (True, 0, None, "0.0"),
            (True, 500, 0, "0.0"),
        ):
            with self.subTest(running=running, automatic=automatic, override=override):
                work.apu_running = running
                work.automatic_apu_allowance_lbs = automatic
                work.apu_allowance_lbs = automatic
                work.apu_override_enabled = override is not None
                work.apu_override_allowance_lbs = override
                work.apu_source_tank_code = None
                db.session.commit()
                row = self.row()
                self.assertEqual(row["actual_fuel_display"], "INCOMPLETE")
                if running is True:
                    self.assertFalse(row["apu_source_valid"])
                    self.assertFalse(row["off_ready"])
                response = self.client.get("/neoscorpion/fuel-dispatch")
                self.assertEqual(response.status_code, 200)
                cell = document(response).one(cls="neoscorpion-dispatch-actual-apu")
                self.assertEqual(cell.one(**{"data-dispatch-actual": None}).text, "ACTUAL: -")
                self.assertEqual(cell.one(**{"data-dispatch-apu-effective": None}).text, f"APU: {expected}")
        for tank in work.tank_states:
            tank.actual_lbs = 10000
        db.session.commit()
        cell = document(self.client.get("/neoscorpion/fuel-dispatch")).one(cls="neoscorpion-dispatch-actual-apu")
        self.assertEqual(cell.one(**{"data-dispatch-actual": None}).text, "ACTUAL: 30.0")

    def setup_shared_truck(self):
        truck = self.fixture._nightly_truck()
        self.assignment.assigned_truck_id = truck.id
        self.assignment.assigned_fueler_user_id = self.fueler.id
        mission = self.fixture._mission("UPS902", "N492UP", 25000, 2)
        other = self.fixture._assignment(mission, truck=truck)
        other.assigned_fueler_user_id = self.dispatcher.id
        work = NeoScorpionFuelWorkState(fuel_assignment_id=other.id,
            tail_number="N492UP", on_at_utc=datetime(2026, 8, 19, 2))
        parking = SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
            tail_number="N492UP", ramp_code="A", position_code="A4")
        db.session.add_all([work, parking]); db.session.commit()
        return truck, other, work, parking

    def current_status(self):
        db.session.commit()
        context = service.fueler_context(self.gateway, self.fueler)
        self.assertEqual([row["mission"].id for row in context["rows"]], [self.mission.id])
        return context["rows"][0]["assigned_truck_current_status"]

    def test_shared_truck_real_on_and_excluded_released_completed_fob_future_work(self):
        truck, other, work, parking = self.setup_shared_truck()
        self.assertEqual(self.current_status(), "CURRENTLY FUELING ON A4")
        for target, field, value in (
            (work, "on_at_utc", None), (work, "off_at_utc", datetime.now()),
            (work, "ended_early_at_utc", datetime.now()), (other, "completed_at_utc", datetime.now()),
            (other, "fuel_on_board_at_utc", datetime.now()), (other, "review_status", "complete"),
            (other, "assigned_truck_id", None), (other.sort_date_mission, "fuel_status", "complete"),
        ):
            with self.subTest(field=field):
                original = getattr(target, field)
                setattr(target, field, value)
                self.assertEqual(self.current_status(), "")
                setattr(target, field, original)
        parking.position_code = None
        self.assertEqual(self.current_status(), "CURRENTLY FUELING · LOCATION UNKNOWN")

    def test_conflicting_activity_top_off_and_live_panel(self):
        truck, other, work, parking = self.setup_shared_truck()
        self.login(self.fueler)
        panel = self.client.get("/neoscorpion/fueler/live-panel")
        self.assertEqual(panel.status_code, 200)
        self.assertIn("CURRENTLY FUELING ON A4", panel.json["html"])
        selection = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck.id).one()
        selection.status = "topping_off"
        selection.top_off_sent_at_utc = None
        work.off_at_utc = datetime.now()
        self.assertEqual(self.current_status(), "CURRENTLY TOPPING OFF")
        panel = self.client.get("/neoscorpion/fueler/live-panel")
        self.assertNotIn("TOP OFF SENT", panel.json["html"])
        work.off_at_utc = None
        selection.top_off_sent_at_utc = datetime(2026, 8, 19, 3, 42)
        self.assertEqual(self.current_status(), "CURRENT STATUS UNKNOWN")
        work.off_at_utc = datetime.now()
        self.assertEqual(self.current_status(), "CURRENTLY TOPPING OFF")
        panel = self.client.get("/neoscorpion/fueler/live-panel")
        self.assertIn("CURRENTLY TOPPING OFF", panel.json["html"])
        self.assertIn("TOP OFF SENT 22:42", panel.json["html"])
        selection.status = "available"
        work.off_at_utc = None
        own_work = self.work(actual=None)
        own_work.on_at_utc = datetime.now()
        self.assertEqual(self.current_status(), "CURRENT STATUS UNKNOWN")

    def test_menu_order_shared_between_navigation_dashboard_and_permission_filtering(self):
        all_items = service.visible_neoscorpion_menu_items(lambda permission: True, "neoscorpion.index")
        labels = [item.label for item in all_items]
        self.assertEqual(labels[labels.index("Fueler") + 1], "Quick Fuel")
        filtered = service.visible_neoscorpion_menu_items(
            lambda permission: permission == "neoscorpion.fuel_assignments.view", "neoscorpion.fueler")
        self.assertEqual([item.label for item in filtered], ["Fueler", "Quick Fuel"])
        self.login(self.dispatcher)
        response = self.client.get("/neoscorpion")
        root = document(response)
        tiles = root.one(cls="neoscorpion-dashboard-grid").findall("a")
        tile_labels = [tile.one("strong").text.strip() for tile in tiles]
        self.assertEqual(tile_labels[tile_labels.index("Fueler") + 1], "Quick Fuel")
        # Screen menus also consume the same canonical list on a subpage.
        screen = document(self.client.get("/neoscorpion/fuel-dispatch")).one(cls="neoscorpion-screen-menu")
        menu_labels = [link.text.strip() for link in screen.findall("a")]
        self.assertEqual(menu_labels[menu_labels.index("Fueler") + 1], "Quick Fuel")
