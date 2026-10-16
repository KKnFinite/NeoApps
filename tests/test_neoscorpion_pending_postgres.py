"""Pending uplift promotion/review atomicity on disposable PostgreSQL."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelingEvent,NeoScorpionFuelCycleHistory,NeoScorpionFuelAssignment,NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as pending
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_pending_uplift as cases

@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class PendingPostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    baseline=cases.PendingUpliftTest.baseline
    submit=cases.PendingUpliftTest.submit
    row=cases.PendingUpliftTest.row
    queue=cases.PendingUpliftTest.queue
    edit=cases.PendingUpliftTest.edit
    complete_parent=cases.PendingUpliftTest.complete_parent
    test_postgres_promotion_exact_parent_history=cases.PendingUpliftTest.test_promotes_exact_parent_actuals_resets_evidence_and_posts_inventory_once
    test_postgres_resource_review_and_retry=cases.PendingUpliftTest.test_unavailable_resource_retains_completed_parent_then_correct_retry
    test_postgres_cancel_promoted_cycle=cases.PendingUpliftTest.test_cancellation_after_promotion_uses_existing_cycle_rollback
    test_postgres_sort_rollover_rejection=cases.PendingUpliftTest.test_rolled_forward_sort_rejects_old_pending_mutation
    def test_additive_pending_table_creation_preserves_started_work(self):
        from app.models import NeoScorpionPendingUplift
        from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
        from sqlalchemy import inspect
        identity=(self.work.id,self.work.manual_on_at_utc,self.assignment.current_cycle_number)
        db.session.commit()
        NeoScorpionPendingUplift.__table__.drop(bind=db.engine)
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        self.assertTrue(inspect(db.engine).has_table('neoscorpion_pending_uplifts'))
        self.assertEqual((self.work.id,self.work.manual_on_at_utc,self.assignment.current_cycle_number),identity)
    def test_concurrent_dispatch_complete_promotes_once_and_inventory_once(self):
        self.queue();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        aid=self.assignment.id
        def complete(i,g,f,d):
            try:return fuel.complete_fueled_assignment(g,d,aid).changed
            except ValueError:return 'stale'
        results=self.concurrent(complete)
        self.assertEqual(results.count(True),1);self.assertEqual(results.count('stale'),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1);self.assertEqual(NeoScorpionFuelCycleHistory.query.count(),1)
        self.assertEqual(db.session.get(NeoScorpionFuelAssignment,aid).current_cycle_number,2)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one().current_gallons,7000)
    def test_savepoint_failure_after_new_cycle_allocation_preserves_parent(self):
        self.queue()
        with patch.object(fuel,'new_work',side_effect=ValueError('Failure after cycle allocation')):self.complete_parent()
        self.assertEqual(self.pending.status,'review');self.assertIn('after cycle allocation',self.pending.review_reason)
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertEqual(self.assignment.current_cycle_number,1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1);self.assertEqual(self.selection.current_gallons,7000)
        self.edit(action='retry');self.assertEqual(self.pending.status,'promoted',self.pending.review_reason)
