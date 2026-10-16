"""Continuation handoff, immutable evidence and posting under PostgreSQL locks."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionPendingUplift,NeoScorpionFuelAuditEntry
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests.test_neoscorpion_continuations import ContinuationTest
from tests import test_neoscorpion_segments_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class ContinuationPostgresTest(ContinuationTest):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    prepare=pg.PhysicalSegmentPostgresTest.prepare

    def test_concurrent_source_submits_activate_only_one_next_visit(self):
        self.target('40');p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        expected=self.baseline();aid,wid,seq=self.assignment.id,self.work.id,self.work.physical_sequence_number
        result=self.concurrent(lambda i,g,f,d:fuel.submit_fueler_assignment(g,f,aid,expected=expected,
            physical_work_id=wid,physical_sequence=seq).changed)
        self.assertEqual(sorted(result),[False,True])
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assertEqual(NeoScorpionPendingUplift.query.one().status,'promoted')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='fueler_submission').count(),1)

    def test_concurrent_source_verification_posts_exactly_once_with_child_active(self):
        p,child=self.handoff();aid,wid,seq=self.assignment.id,self.work.id,self.work.physical_sequence_number
        initial=self.selection.current_gallons
        result=self.concurrent(lambda i,g,f,d:fuel.complete_fueled_assignment(g,d,aid,
            physical_work_id=wid,physical_sequence=seq).changed)
        self.assertEqual(sorted(result),[False,True])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        from app.models import NeoScorpionSortTruck,NeoScorpionFuelAssignment
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one().current_gallons,initial-3000)
        self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,aid).completed_at_utc)
        self.assertEqual(db.session.get(NeoScorpionFuelAssignment,aid).active_work_state_id,child.id)

    def test_baseline_racing_valid_source_verification_preserves_posted_transfer(self):
        from app.models import SortDateOperation,NeoScorpionSortTruck
        from app.services.neoscorpion_inventory import correct_baseline
        p,child=self.handoff();oid,tid,aid,wid,seq=self.operation.id,self.truck.id,self.assignment.id,self.work.id,self.work.physical_sequence_number
        def action(index,g,f,d):
            if index==0:
                try:correct_baseline(g,db.session.get(SortDateOperation,oid),d,tid,5500,0,0);return 'corrected'
                except ValueError:db.session.rollback();return 'stale'
            fuel.complete_fueled_assignment(g,d,aid,physical_work_id=wid,physical_sequence=seq);return 'posted'
        results=self.concurrent(action)
        self.assertIn('posted',results)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,
            2500 if 'corrected' in results else 7000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_two_dispatcher_plan_edits_accept_one_version_without_duplicate_work(self):
        from app.services import neoscorpion_pending_uplift as plans
        self.target('40');p=self.plan();aid,pid,version=self.assignment.id,p.id,p.version
        choices=[self.truck.id,p.proposed_truck_id]
        def action(index,g,f,d):
            try:
                plans.plan_continuation(g,d,dict(assignment_id=aid,pending_id=pid,expected_version=version,
                    expected_cycle=1,expected_tail='N491UP',pending_action='save',proposed_truck_id=choices[index]));return 'saved'
            except plans.PendingUpliftConflict:db.session.rollback();return 'conflict'
        self.assertEqual(sorted(self.concurrent(action)),['conflict','saved'])
        self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1);self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_source_correction_racing_verification_has_one_authoritative_outcome(self):
        p,child=self.handoff();aid,wid,seq=self.assignment.id,self.work.id,self.work.physical_sequence_number
        def action(index,g,f,d):
            try:
                if index==0:
                    fuel.save_fueler_entry(g,f,dict(assignment_id=aid,physical_work_id=wid,physical_sequence=seq,actual_left='16'));return 'corrected'
                fuel.complete_fueled_assignment(g,d,aid,physical_work_id=wid,physical_sequence=seq);return 'verified'
            except ValueError:db.session.rollback();return 'blocked'
        results=self.concurrent(action)
        self.assertEqual(results.count('blocked'),1)
        source=db.session.get(NeoScorpionFuelWorkState,wid)
        if 'verified' in results:
            self.assertIsNotNone(source.physical_finalized_at_utc);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        else:
            self.assertEqual(results[0],'corrected');self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
            self.assertTrue(db.session.get(NeoScorpionFuelWorkState,child.id).continuation_review_reason)

    def test_additive_bootstrap_preserves_legacy_plan_and_completed_event(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one();eid,wid,tf,on,off=event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc
        balance=self.selection.current_gallons
        legacy=NeoScorpionPendingUplift(sort_date_operation_id=self.operation.id,mission_id=self.mission.id,
            fuel_assignment_id=self.assignment.id,parent_cycle_number=1,parent_work_state_id=wid,
            tail_number='N491UP',required_fuel_lbs=40000,created_by_user_id=self.dispatcher.id,
            updated_by_user_id=self.dispatcher.id,status='cancelled')
        db.session.add(legacy);db.session.commit();pid=legacy.id
        # Simulate the production schema before these additive columns existed.
        for table,columns in (('neoscorpion_pending_uplifts',('plan_kind','handoff_snapshot')),
                ('neoscorpion_fuel_work_states',('target_frozen','continuation_review_reason')),
                ('neoscorpion_dispatch_preferences',('board_truck_cards_collapsed','statistics_collapsed'))):
            for column in columns:db.session.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all();event=db.session.get(NeoScorpionFuelingEvent,eid)
        self.assertEqual((event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc),(wid,tf,on,off))
        self.assertEqual(self.selection.current_gallons,balance)
        self.assertFalse(db.session.get(NeoScorpionFuelWorkState,wid).target_frozen)
        legacy=db.session.get(NeoScorpionPendingUplift,pid)
        self.assertEqual((legacy.plan_kind,legacy.status,legacy.parent_work_state_id),('uplift','cancelled',wid))
