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
                self.assertEqual(cell.one(**{"data-dispatch-actual": None}).text, "-")
                self.assertEqual(cell.one(**{"data-dispatch-apu-effective": None}).text, f"APU: {expected}")
        for tank in work.tank_states:
            tank.actual_lbs = 10000
        db.session.commit()
        cell = document(self.client.get("/neoscorpion/fuel-dispatch")).one(cls="neoscorpion-dispatch-actual-apu")
        self.assertEqual(cell.one(**{"data-dispatch-actual": None}).text, "30.0")

    def setup_shared_truck(self):
        from tests.neoscorpion_evidence_fixture import FuelCycle
        truck=self.fixture._nightly_truck()
        mission=self.fixture._mission('UPS902','N492UP',25000,2)
        self.other_cycle=FuelCycle(self.gateway,self.operation,mission,self.dispatcher,self.dispatcher,truck)
        self.other_cycle.assign(datetime(2026,8,19,2))
        self.other_cycle.park(datetime(2026,8,19,2),'A','A04')
        self.other_cycle.on(datetime(2026,8,19,2,1))
        # Current Fueler has the same planned truck, but no second physical ON.
        from app.services import neoscorpion_assets as assets
        assets.select_nightly_fueler(self.operation,self.fueler)
        self.assignment.assigned_truck_id=truck.id
        self.assignment.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        parking=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=self.operation.id,tail_number='N492UP').one()
        return truck,self.other_cycle.assignment,self.other_cycle.work,parking

    def current_status(self):
        db.session.commit()
        context = service.fueler_context(self.gateway, self.fueler)
        self.assertEqual([row["mission"].id for row in context["rows"]], [self.mission.id])
        return context["rows"][0]["assigned_truck_current_status"]

    def test_shared_truck_real_on_and_excluded_released_completed_fob_future_work(self):
        from tests.neoscorpion_evidence_fixture import transfer_readings
        from app.models import NeoScorpionFuelingEvent
        truck,other,work,parking=self.setup_shared_truck()
        self.assertEqual(self.current_status(),'CURRENTLY FUELING ON A04')
        self.other_cycle.off(datetime(2026,8,19,2,10))
        self.assertEqual(self.current_status(),'COMING FROM ALPHA · ~2 MIN')  # Verified physical release supplies travel provenance.
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.other_cycle.save(datetime(2026,8,19,2,11),**transfer_readings(gallons='500'))
        self.other_cycle.submit(datetime(2026,8,19,2,12));self.other_cycle.complete(datetime(2026,8,19,2,13))
        self.assertEqual(self.current_status(),'COMING FROM ALPHA · ~2 MIN')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,9500)
        # Future planned assignment still cannot claim actual physical occupancy.
        self.assertIsNone(self.assignment.active_work_state_id)
        self.assertIsNone(self.assignment.completed_at_utc)

    def test_conflicting_activity_top_off_and_live_panel(self):
        from app.services import neoscorpion_assets as assets
        from tests.neoscorpion_evidence_fixture import transfer_readings
        truck,other,work,parking=self.setup_shared_truck()
        self.login(self.fueler)
        panel=self.client.get('/neoscorpion/fueler/live-panel')
        self.assertEqual(panel.status_code,200)
        self.assertIn('CURRENTLY FUELING ON A04',panel.json['html'])
        with self.assertRaisesRegex(ValueError,'actively fueling'):
            assets.mark_nightly_truck_topping_off(self.operation,truck,changed_by_user=self.dispatcher)
        db.session.rollback()
        self.assertEqual(self.current_status(),'CURRENTLY FUELING ON A04')
        # Stored legacy contradiction is a diagnostic negative fixture, never a supported action.
        selection=NeoScorpionSortTruck.query.one();selection.status='topping_off'
        self.assertEqual(self.current_status(),'CURRENT STATUS UNKNOWN')
        selection.status='available';db.session.commit()
        self.other_cycle.save(datetime(2026,8,19,2,10),**transfer_readings(gallons='500'))
        self.other_cycle.off(datetime(2026,8,19,2,11));self.other_cycle.submit(datetime(2026,8,19,2,12))
        self.other_cycle.complete(datetime(2026,8,19,2,13))
        # An actual future assignment must be withdrawn before TOP OFF.
        from tests.neoscorpion_evidence_fixture import FuelCycle
        own=FuelCycle(self.gateway,self.operation,self.mission,self.fueler,self.dispatcher,truck,assignment=self.assignment)
        with own.scope(datetime(2026,8,19,2,14),actor=self.dispatcher):
            service.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
                expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id=truck.id,
                assigned_fueler_user_id=self.fueler.id,assigned_truck_id=''))
            db.session.commit()
        assets.mark_nightly_truck_topping_off(self.operation,truck,changed_by_user=self.dispatcher,now_utc=datetime(2026,8,19,3,42));db.session.commit()
        self.assertEqual(NeoScorpionSortTruck.query.one().status,'topping_off')
        # Re-attach only a read-only legacy planned reference to inspect unavailable truck presentation.
        self.assignment.assigned_truck_id=truck.id;db.session.commit()
        self.assertEqual(self.current_status(),'CURRENTLY TOPPING OFF')
        panel=self.client.get('/neoscorpion/fueler/live-panel')
        self.assertIn('CURRENTLY TOPPING OFF',panel.json['html'])
        self.assertIn('TOP OFF SENT 22:42',panel.json['html'])

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
