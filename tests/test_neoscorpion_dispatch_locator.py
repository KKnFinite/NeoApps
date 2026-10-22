"""Read-only card forecasts are tied to the exact active immutable SENT record."""
import unittest
from datetime import datetime, timedelta
from app.extensions import db
from app.models import NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionTopOffEvent
from app.services.neoscorpion import _dispatch_truck_visuals
from tests import test_neoscorpion_dispatch_planning as fixture_module


class DispatchTopOffEstimateTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp(); self.addCleanup(self.fixture.tearDown)
        self.at = datetime(2026,10,22,4,55)  # Gateway-local midnight crossing.
        self.truck = NeoScorpionFuelTruck(gateway_id=self.fixture.gateway.id,
            truck_number='ALERT',capacity_gallons=20000)
        db.session.add(self.truck); db.session.flush()
        self.selection = NeoScorpionSortTruck(sort_date_operation_id=self.fixture.operation.id,
            fuel_truck_id=self.truck.id,status='topping_off',top_off_sent_at_utc=self.at,
            current_gallons=8000,inventory_verified=True)
        db.session.add(self.selection); db.session.flush()

    def sent(self, at=None, estimate=True):
        event = NeoScorpionTopOffEvent(sort_date_operation_id=self.fixture.operation.id,
            fuel_truck_id=self.truck.id,kind='sent',occurred_at_utc=at or self.at,
            inventory_verified=True,inventory_epoch=0,
            spear_predicted_return_at_utc=(at or self.at)+timedelta(minutes=30) if estimate else None)
        db.session.add(event); db.session.flush(); return event

    def visual(self):
        return _dispatch_truck_visuals([dict(truck=self.truck,selection=self.selection)],[])[0]

    def test_exact_active_sent_forecast_with_explicit_utc_and_no_mutation(self):
        self.sent()
        self.assertEqual(self.visual()['top_off_return_utc'],'2026-10-22T05:25:00+00:00')
        self.assertFalse(db.session.dirty)
        self.assertEqual(self.selection.current_gallons,8000)

    def test_missing_legacy_forecast_never_invented(self):
        self.sent(estimate=False)
        self.assertEqual(self.visual()['top_off_return_utc'],'')

    def test_previous_trip_never_used_for_current_trip(self):
        self.sent(at=self.at-timedelta(hours=3))
        self.assertEqual(self.visual()['top_off_return_utc'],'')

    def test_returned_or_non_sent_status_never_warns(self):
        event = self.sent()
        db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=self.fixture.operation.id,
            fuel_truck_id=self.truck.id,kind='returned',sent_event_id=event.id,
            occurred_at_utc=self.at+timedelta(minutes=40),inventory_verified=True,inventory_epoch=1))
        db.session.flush()
        self.assertEqual(self.visual()['top_off_return_utc'],'')
        for status in ('available','needs_sump','unavailable_oos'):
            self.selection.status=status
            self.assertEqual(self.visual()['top_off_return_utc'],'')
