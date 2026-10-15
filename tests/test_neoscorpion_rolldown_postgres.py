"""Real PostgreSQL additive bootstrap, immutable history and duplicate actions."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import text, inspect
from sqlalchemy.exc import DBAPIError
from app.extensions import db
from app.models import NeoScorpionTopOffEvent, SortDateOperation, NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionFuelingEvent
from app.services import neoscorpion_assets as assets, neoscorpion as service
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from app.services.neoscorpion_rolldown import report_context
from tests import test_neoscorpion_segments_postgres as segments


@unittest.skipUnless(segments.URI,'Requires disposable local PostgreSQL')
class RolldownPostgresTest(unittest.TestCase):
    setUp=segments.PhysicalSegmentPostgresTest.setUp
    drop_schema=segments.PhysicalSegmentPostgresTest.drop_schema
    prepare=segments.PhysicalSegmentPostgresTest.prepare
    concurrent=segments.PhysicalSegmentPostgresTest.concurrent
    save=segments.PhysicalSegmentPostgresTest.save
    off=segments.PhysicalSegmentPostgresTest.off
    final_data=segments.PhysicalSegmentPostgresTest.final_data

    def release(self):
        self.off();self.assignment.assigned_truck_id=None;db.session.commit()

    def test_concurrent_sent_and_return_create_one_trip_and_inventory_verification(self):
        self.release();operation_id,truck_id=self.operation.id,self.truck.id
        def sent(index,gateway,fueler,dispatcher):
            result=assets.mark_nightly_truck_topping_off(db.session.get(SortDateOperation,operation_id),
                db.session.get(NeoScorpionFuelTruck,truck_id),changed_by_user=dispatcher)
            return result.changed
        self.assertEqual(sorted(self.concurrent(sent)),[False,True])
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),1)
        def returned(index,gateway,fueler,dispatcher):
            try:
                assets.complete_nightly_truck_top_off(db.session.get(SortDateOperation,operation_id),
                    db.session.get(NeoScorpionFuelTruck,truck_id),15000,changed_by_user=dispatcher)
                return 'returned'
            except ValueError:
                db.session.rollback();return 'already returned'
        self.assertEqual(sorted(self.concurrent(returned)),['already returned','returned'])
        self.assertEqual(NeoScorpionTopOffEvent.query.count(),2)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,15000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_additive_bootstrap_repeat_preserves_physical_events_and_immutable_trip(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        facts=(event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc)
        self.assignment.assigned_truck_id=None;db.session.commit()
        # Simulate the pre-release production schema: only the new empty table
        # is absent; existing physical records and foreign keys are untouched.
        db.session.execute(text('DROP TABLE neoscorpion_top_off_events'))
        db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(self.fixture.app)
        self.assertIn('neoscorpion_top_off_events',inspect(db.engine).get_table_names())
        assets.mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher)
        db.session.commit()
        before=NeoScorpionTopOffEvent.query.one().gallons
        with self.assertRaises(DBAPIError):
            db.session.execute(text('UPDATE neoscorpion_top_off_events SET gallons=1'));db.session.commit()
        db.session.rollback()
        self.assertEqual(NeoScorpionTopOffEvent.query.one().gallons,before)
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc),facts)
        self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,7000)
        count=NeoScorpionFuelingEvent.query.count()
        report=report_context(self.gateway,self.operation)
        self.assertEqual(report['fleet']['uplift'],3000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),count)
