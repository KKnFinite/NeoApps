"""Package 1 real row-lock races, in UUID-named local test schemas only."""
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionTopOffEvent, SortDateOperation, NeoScorpionFuelAssignment
from app.services import neoscorpion as fuel
from app.services import neoscorpion_assets as assets
from tests import test_neoscorpion_segments_postgres as fixtures


@unittest.skipUnless(fixtures.URI, 'Requires disposable local PostgreSQL')
class Package1PostgresTest(unittest.TestCase):
    setUp = fixtures.PhysicalSegmentPostgresTest.setUp
    drop_schema = fixtures.PhysicalSegmentPostgresTest.drop_schema
    prepare = fixtures.PhysicalSegmentPostgresTest.prepare
    concurrent = fixtures.PhysicalSegmentPostgresTest.concurrent
    off = fixtures.PhysicalSegmentPostgresTest.off

    def test_capacity_return_race_preserves_trip_inventory_and_opening(self):
        # A separate idle truck, not a fabricated release of the fixture's ON.
        truck = NeoScorpionFuelTruck(gateway_id=self.gateway.id, truck_number='RACE', capacity_gallons=12000)
        db.session.add(truck); db.session.flush(); tid = truck.id; oid = self.operation.id
        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid, fuel_truck_id=tid,
            starting_gallons=8000, current_gallons=8000, status='available'))
        db.session.commit()
        assets.mark_nightly_truck_topping_off(self.operation, truck); db.session.commit()
        def action(i, gateway, _f, dispatcher):
            op = db.session.get(SortDateOperation, oid)
            truck = db.session.get(NeoScorpionFuelTruck, tid)
            if i == 0:
                try:
                    with patch.object(fuel, 'current_sort_operation', return_value=op):
                        fuel.save_truck(gateway, dict(truck_id=tid, truck_number='RACE', capacity_gallons='9000',
                            is_active='1', expected_capacity_gallons='12000'), dispatcher)
                except ValueError:
                    db.session.rollback(); return 'capacity rejected'
                return 'unsafe capacity accepted'
            assets.complete_nightly_truck_top_off(op, truck, 11000, changed_by_user=dispatcher)
            return 'returned'
        self.assertEqual(sorted(self.concurrent(action)), ['capacity rejected', 'returned'])
        row = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one()
        self.assertEqual((row.starting_gallons, row.current_gallons, row.status), (8000, 11000, 'available'))
        self.assertEqual(db.session.get(NeoScorpionFuelTruck, tid).capacity_gallons, 12000)
        self.assertEqual(NeoScorpionTopOffEvent.query.filter_by(fuel_truck_id=tid).count(), 2)

    def test_concurrent_capacity_edit_rejects_stale_baseline(self):
        tid = self.truck.id; oid = self.operation.id; before = self.truck.capacity_gallons
        number = self.truck.truck_number
        def action(i, gateway, _f, dispatcher):
            try:
                with patch.object(fuel, 'current_sort_operation', return_value=db.session.get(SortDateOperation, oid)):
                    fuel.save_truck(gateway, dict(truck_id=tid, truck_number=number,
                        capacity_gallons=str(before+1000+i*1000), expected_capacity_gallons=str(before), is_active='1'), dispatcher)
                return 'saved'
            except ValueError:
                db.session.rollback(); return 'rejected'
        self.assertEqual(sorted(self.concurrent(action)), ['rejected', 'saved'])

    def test_nonfinite_density_cannot_persist_in_postgres(self):
        for density in ('Infinity', '-Infinity', 'NaN', '1e9999', '1e-9999'):
            with self.subTest(density=density), self.assertRaises(ValueError):
                fuel.save_settings(self.gateway, dict(fuel_density_lbs_per_gallon=density,
                    red_assignment_alert_threshold_minutes='30'))
            db.session.rollback()
        fuel.save_settings(self.gateway, dict(fuel_density_lbs_per_gallon='6.7', red_assignment_alert_threshold_minutes='30'))
        db.session.commit()
        self.assertEqual(fuel.lbs_to_gallons(6700, self.settings.fuel_density_lbs_per_gallon), 1000)

    def test_concurrent_assignment_winner_persists_loser_cannot_overwrite(self):
        from flask_login import login_user
        mission = self.fixture._mission('UPS8888', 'N492UP', 25000, 2)
        mid = mission.id
        trucks = [self.fixture._nightly_truck('CONCURRENT-'+str(i)).id for i in range(2)]
        db.session.commit()
        def action(i, gateway, fueler, dispatcher):
            try:
                with self.fixture.app.test_request_context():
                    login_user(dispatcher)
                    fuel.save_dispatch_assignment(gateway, dict(mission_id=mid,
                        expected_work_ref='', expected_assigned_fueler_user_id='', expected_assigned_truck_id='',
                        assigned_fueler_user_id=fueler.id, assigned_truck_id=trucks[i]))
                return 'saved'
            except ValueError as exc:
                db.session.rollback()
                self.assertIn('changed', str(exc))
                return 'rejected'
        self.assertEqual(sorted(self.concurrent(action)), ['rejected', 'saved'])
        row = NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mid).one()
        self.assertIn(row.assigned_truck_id, trucks)

    def test_duplicate_returns_have_one_inventory_epoch_and_one_return(self):
        truck = self.fixture._nightly_truck('RETURNS')
        tid, oid = truck.id, self.operation.id
        db.session.commit()
        assets.mark_nightly_truck_topping_off(self.operation, truck); db.session.commit()
        selection = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one()
        token = assets.top_off_action_state(selection); epoch = selection.inventory_epoch
        def action(_i, _g, _f, dispatcher):
            try:
                assets.complete_nightly_truck_top_off(db.session.get(SortDateOperation, oid), tid, 11000,
                    changed_by_user=dispatcher, expected_state=token)
                return 'returned'
            except ValueError:
                db.session.rollback(); return 'rejected'
        self.assertEqual(sorted(self.concurrent(action)), ['rejected', 'returned'])
        row = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one()
        self.assertEqual((row.current_gallons, row.inventory_epoch), (11000, epoch+1))
        self.assertEqual(NeoScorpionTopOffEvent.query.filter_by(fuel_truck_id=tid, kind='returned').count(), 1)
