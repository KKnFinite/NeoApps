"""Exact projection regressions; no posting or historical-record mutation."""
from datetime import datetime, timedelta
import unittest
from app.extensions import db
from app.models import NeoScorpionSortTruck, NeoScorpionInventoryReading, NeoScorpionFuelWorkState
from tests import test_neoscorpion_apu_truck_projections as fixtures


class FutureInventoryTest(unittest.TestCase):
    setUp=fixtures.ApuTruckProjectionsTest.setUp
    context=fixtures.ApuTruckProjectionsTest.context
    row=fixtures.ApuTruckProjectionsTest.row

    def second(self, truck=None):
        mission=self.fixture._mission('UPS902','N492UP',25400,2)
        self.fixture._tail_fuel(mission,inbound_lbs=12000)
        self.fixture._assignment(mission,truck or self.truck)
        return mission

    def closed(self):
        self.work.on_at_utc=datetime.utcnow()-timedelta(minutes=10)
        self.work.off_at_utc=datetime.utcnow()-timedelta(minutes=1)
        self.work.physical_truck_id=self.truck.id
        self.work.physical_cycle_type='fuel'
        self.work.physical_transfer_gallons=2000
        self.work.off_inventory_epoch=0
        self.assignment.transfer_fuel_gallons=2000

    def test_reading_covers_off_transfer_never_subtracted_again(self):
        self.closed();self.second()
        selection=NeoScorpionSortTruck.query.one();selection.inventory_epoch=1
        db.session.add(NeoScorpionInventoryReading(sort_date_operation_id=self.fixture.operation.id,
            fuel_truck_id=self.truck.id,epoch=1,mode='physical',gallons=10000,
            covered_work_ids=[self.work.id],actor_user_id=self.fixture.operation.generated_by_user_id))
        rows=self.context()['rows']
        self.assertEqual([r['projected_truck_gallons'] for r in rows],[10000,8000])
        self.assertEqual(rows[0]['planning_demand_gallons'],2000)

    def test_off_unposted_balance_reserved_before_future_work(self):
        self.closed();self.second()
        selection=NeoScorpionSortTruck.query.one();selection.inventory_verified=False
        selection.inventory_pending_epoch=selection.inventory_epoch
        rows=self.context()['rows']
        self.assertEqual([r['projected_truck_gallons'] for r in rows],[8000,6000])
        self.assertEqual(rows[0]['inventory_projection_bases'][self.truck.id],8000)

    def test_previous_truck_unposted_work_survives_current_resource_change(self):
        self.closed();self.second()
        other=self.fixture._nightly_truck('OTHER')
        self.assignment.assigned_truck_id=other.id
        fresh=NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id,tail_number=self.mission.assigned_tail_number,
            visit_number=2,physical_sequence_number=2,physical_cycle_number=1,physical_truck_id=other.id)
        db.session.add(fresh);db.session.flush();self.assignment.active_work_state_id=fresh.id
        rows=self.context()['rows']
        self.assertEqual(rows[1]['projected_truck_gallons'],6000)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one().current_gallons,10000)

    def test_live_etd_order_and_fleet_final_projection(self):
        second=self.second();second.eta_datetime_utc=self.mission.planned_datetime_utc-timedelta(minutes=20)
        context=self.context();first,later=context['rows']
        self.assertEqual(later['projected_truck_gallons'],8000)
        self.assertEqual(first['projected_truck_gallons'],5900)
        self.assertEqual(context['truck_visuals'][0]['projected_gallons'],5900)

    def test_unknown_inventory_and_unverified_top_off_return_are_not_estimates(self):
        selection=NeoScorpionSortTruck.query.one()
        for status,verified in (('available',False),('topping_off',True),('needs_sump',True)):
            selection.status=status;selection.inventory_verified=verified
            self.assertIsNone(self.row()['projected_truck_gallons'])

    def test_unknown_old_transfer_cannot_be_discarded(self):
        self.closed();self.work.physical_transfer_gallons=None;self.assignment.transfer_fuel_gallons=None
        self.second()
        self.assertTrue(all(r['projected_truck_gallons'] is None for r in self.context()['rows']))

    def test_receiving_defuel_does_not_become_available_inventory_before_recovery(self):
        self.closed();self.work.physical_cycle_type='defuel';self.assignment.current_cycle_type='defuel'
        self.second()
        self.assertTrue(all(r['projected_truck_gallons'] is None for r in self.context()['rows']))

    def test_opposite_tank_movement_is_not_a_precise_stock_forecast(self):
        from app.services.neoscorpion_projection import inventory_reservations
        self.closed()
        selection=NeoScorpionSortTruck.query.one()
        row={'fuel_work_state':self.work,'assignment':self.assignment,'cycle_type':'fuel',
             'direction_mismatch':True,'planning_demand_gallons':2000}
        balances=inventory_reservations(self.fixture.operation,{self.truck.id:selection},[row],[self.work])
        self.assertIsNone(balances[self.truck.id])

    def test_queued_truck_without_verified_handoff_remains_incomplete(self):
        from app.models import NeoScorpionPendingUplift
        next_truck=self.fixture._nightly_truck('NEXT')
        actor=self.fixture.operation.generated_by_user_id
        for truck in (next_truck,self.truck,next_truck):
            db.session.add(NeoScorpionPendingUplift(sort_date_operation_id=self.fixture.operation.id,
                mission_id=self.mission.id,fuel_assignment_id=self.assignment.id,parent_cycle_number=1,
                tail_number=self.mission.assigned_tail_number,required_fuel_lbs=25400,
                proposed_truck_id=truck.id,created_by_user_id=actor,updated_by_user_id=actor,
                plan_kind='continuation'))
        context=self.context()
        self.assertEqual(len(context['rows'][0]['pending_truck_plans']),3)
        self.assertTrue(all(v['projected_incomplete'] and v['projected_gallons'] is None
                            for v in context['truck_visuals']))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one().current_gallons,10000)
