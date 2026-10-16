"""Latest lifecycle contracts under real PostgreSQL transaction serialization."""
import json
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelAuditEntry
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_lifecycle_audit as cases


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class LifecyclePostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data
    baseline=cases.LifecycleAuditTest.baseline
    submit=cases.LifecycleAuditTest.submit
    row=cases.LifecycleAuditTest.row
    finish=cases.LifecycleAuditTest.finish
    on_current=cases.LifecycleAuditTest.on_current
    start_followup=cases.LifecycleAuditTest.start_followup
    _a_b_a=cases.LifecycleAuditTest._a_b_a
    test_a_b_a=cases.LifecycleAuditTest.test_a_b_a_pending_original_actuals_independent_handoffs_and_postings
    test_a_b_a_different_trucks=cases.LifecycleAuditTest.test_a_b_a_different_truck_has_independent_inventory_and_history
    test_repeated_uplift_cycles=cases.LifecycleAuditTest.test_three_independent_followups_fresh_evidence_and_inventory_once
    test_cancel_cycle_identity=cases.LifecycleAuditTest.test_cancel_both_followups_before_and_after_on_off_preserves_facts

    def test_concurrent_correction_and_submission_cannot_accept_stale_evidence(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        expected=self.baseline();aid=self.assignment.id
        def action(index,g,f,d):
            if index==0:return fuel.submit_fueler_assignment(g,f,aid,expected=expected).changed
            return fuel.save_fueler_entry(g,f,dict(assignment_id=aid,actual_left='16',expected=json.dumps(expected)),require_expected=True).changed
        results=self.concurrent(action)
        self.assertEqual(results.count(True),1);self.assertEqual(results.count('conflict'),1)
        row=self.row();work=db.session.get(NeoScorpionFuelWorkState,self.ids[4])
        if row['fueler_submitted']:
            self.assertEqual(next(t.actual_lbs for t in work.tank_states if t.tank_code=='left'),15000)
        else:self.assertFalse(row['normal_completion_ready'])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_concurrent_dispatcher_verification_posts_once_after_submission(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        results=self.concurrent(lambda i,g,f,d:fuel.complete_fueled_assignment(g,d,self.ids[3]).changed)
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='fueler_submission').count(),1)

    def test_pending_edit_and_dispatcher_completion_serialize_without_stale_promotion(self):
        from app.services import neoscorpion_pending_uplift as pending
        from app.models import NeoScorpionPendingUplift,NeoScorpionFuelAssignment,SortDateMission
        created=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            pending_action='create',expected_cycle=1,expected_tail='N491UP'))
        request=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            pending_id=created.pending.id,expected_version=created.pending.version,pending_action='save',
            required_fuel='40',proposed_fueler_user_id=self.fueler.id,proposed_truck_id=self.truck.id)).pending
        db.session.commit();pid,version,aid,tid=request.id,request.version,self.assignment.id,self.truck.id
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        def action(index,g,f,d):
            if index==0:return fuel.complete_fueled_assignment(g,d,aid).changed
            try:
                return pending.save_pending_uplift(g,d,dict(assignment_id=aid,pending_id=pid,
                    expected_version=version,pending_action='save',required_fuel='41',
                    proposed_fueler_user_id=f.id,proposed_truck_id=tid)).changed
            except ValueError:
                db.session.rollback();return 'stale_pending'
        results=self.concurrent(action)
        self.assertIs(results[0],True);self.assertIn(results[1],(True,'stale_pending'))
        request=db.session.get(NeoScorpionPendingUplift,pid)
        assignment=db.session.get(NeoScorpionFuelAssignment,aid)
        self.assertEqual(request.status,'promoted',request.review_reason)
        self.assertEqual(assignment.current_cycle_number,2)
        self.assertEqual(db.session.get(SortDateMission,assignment.sort_date_mission_id).planned_fuel_load,
            41000 if results[1] is True else 40000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        work=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
        self.assertIsNone(work.fueler_submitted_at_utc);self.assertIsNone(work.manual_on_at_utc)
