"""Pending uplift promotion/review atomicity on disposable PostgreSQL."""
import unittest
import json
import subprocess
import sys
from datetime import datetime, timedelta
from unittest.mock import patch
from app import create_app
from app.extensions import db
from app.models import Gateway, User, NeoScorpionFuelingEvent,NeoScorpionFuelCycleHistory,NeoScorpionFuelAssignment,NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as pending
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_pending_uplift as cases


def retry_in_fresh_process(schema, clock, gateway_id, dispatcher_id, form):
    class RestartConfig:
        TESTING = True
        AUTO_BOOTSTRAP_DATABASE = False
        SECRET_KEY = 'disposable-uplift-test'
        SQLALCHEMY_DATABASE_URI = pg.URI
        SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options': f'-csearch_path={schema}'}}
        CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE = datetime.fromisoformat(clock)

    with create_app(RestartConfig).app_context():
        result = pending.save_pending_uplift(
            db.session.get(Gateway, gateway_id), db.session.get(User, dispatcher_id), form)
        db.session.commit()
        return result.pending.status

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
    login=cases.PendingUpliftTest.login
    complete_apu_parent=cases.PendingUpliftTest.complete_apu_parent
    test_postgres_apu_shortfall_promotes=cases.PendingUpliftTest.test_apu_shortfall_below_target_promotes_without_false_review
    test_postgres_existing_apu_review_retry=cases.PendingUpliftTest.test_existing_false_review_can_retry_after_fix_without_reposting_parent
    test_postgres_net_target_and_missing_apu=cases.PendingUpliftTest.test_net_target_equality_and_missing_apu_still_preserve_completed_parent
    test_postgres_apu_reserve=cases.PendingUpliftTest.test_apu_shortfall_reserve_uses_net_fuel_not_raw_actual
    test_postgres_promotion_exact_parent_history=cases.PendingUpliftTest.test_promotes_exact_parent_actuals_resets_evidence_and_posts_inventory_once
    test_postgres_resource_review_and_retry=cases.PendingUpliftTest.test_unavailable_resource_retains_completed_parent_then_correct_retry
    test_postgres_cancel_promoted_cycle=cases.PendingUpliftTest.test_cancellation_after_promotion_uses_existing_cycle_rollback
    test_postgres_sort_rollover_rejection=cases.PendingUpliftTest.test_rolled_forward_sort_rejects_old_pending_mutation

    def test_excess_precision_review_and_retry_after_restart_posts_once(self):
        self.queue('40.12')
        self.complete_parent()
        self.assertEqual(self.pending.status, 'review')
        self.assertIn('one decimal', self.pending.review_reason)
        self.assertEqual(self.pending.required_fuel_lbs, 40120)
        self.assertEqual(self.mission.planned_fuel_load, 25000)
        self.assertEqual(self.assignment.current_cycle_number, 1)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.count(), 0)
        event = NeoScorpionFuelingEvent.query.one()
        evidence = (event.id, event.fuel_work_state_id, event.performance_snapshot,
                    self.work.physical_finalized_at_utc, self.selection.current_gallons)
        self.assertEqual(self.selection.current_gallons, 7000)

        self.edit(target='40.0')
        form = dict(assignment_id=self.assignment.id, pending_id=self.pending.id,
                    expected_version=self.pending.version, pending_action='retry')
        gateway_id, dispatcher_id = self.gateway.id, self.dispatcher.id
        clock = self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']
        db.session.commit()
        command = ("import json, sys; from tests.test_neoscorpion_pending_postgres "
                   "import retry_in_fresh_process; print(retry_in_fresh_process(*json.load(sys.stdin)))")
        restarted = subprocess.run(
            [sys.executable, '-c', command], text=True, capture_output=True, timeout=45,
            input=json.dumps([self.schema, clock.isoformat(), gateway_id, dispatcher_id, form]))
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertEqual(restarted.stdout.strip().splitlines()[-1], 'promoted')
        with self.assertRaises(pending.PendingUpliftConflict):
            pending.save_pending_uplift(self.gateway, self.dispatcher, form)
        db.session.rollback()
        self.assertEqual(self.assignment.current_cycle_number, 2)
        self.assertEqual(self.mission.planned_fuel_load, 40000)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.count(), 1)
        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.id, event.fuel_work_state_id, event.performance_snapshot,
                          self.work.physical_finalized_at_utc, self.selection.current_gallons), evidence)

    def test_smallest_valid_increment_above_verified_parent(self):
        self.queue('30.0')
        for invalid in ('0.0', '-0.1', '', 'fuel'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.edit(target=invalid)
            db.session.rollback()
            self.assertEqual(self.pending.required_fuel_lbs, 30000)
        self.complete_parent()
        self.assertIn('meets or exceeds', self.pending.review_reason)
        self.edit(target='30.1')
        self.edit(action='retry')
        self.assertEqual(self.pending.status, 'promoted', self.pending.review_reason)
        self.assertEqual(self.mission.planned_fuel_load, fuel.parse_authoritative_fuel_load('30.1'))
        self.assertEqual(self.selection.current_gallons, 7000)

    def test_explicit_zero_uplift_cannot_change_completed_parent(self):
        self.complete_parent()
        with self.assertRaisesRegex(ValueError, 'positive'):
            fuel.start_follow_up_fuel_cycle(
                self.gateway, self.dispatcher, self.assignment.id, 'uplift', '0.0', None, None,
                expected_cycle=1, expected_tail=self.mission.assigned_tail_number)
        db.session.rollback()
        self.assertEqual(self.assignment.current_cycle_number, 1)
        self.assertEqual(self.mission.planned_fuel_load, 25000)
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        self.assertEqual(self.selection.current_gallons, 7000)

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
