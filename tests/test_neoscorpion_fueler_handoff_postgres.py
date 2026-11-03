"""Independent PostgreSQL transactions: personnel handoff vs saves/submission."""
import json
from datetime import timedelta
import unittest

from app.extensions import db
from app.models import User,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_segments_postgres as fixtures


@unittest.skipUnless(fixtures.URI,'Requires disposable loopback PostgreSQL')
class FuelerHandoffPostgresTest(unittest.TestCase):
    setUp=fixtures.PhysicalSegmentPostgresTest.setUp
    drop_schema=fixtures.PhysicalSegmentPostgresTest.drop_schema
    prepare=fixtures.PhysicalSegmentPostgresTest.prepare
    save=fixtures.PhysicalSegmentPostgresTest.save
    off=fixtures.PhysicalSegmentPostgresTest.off
    final_data=fixtures.PhysicalSegmentPostgresTest.final_data
    concurrent=fixtures.PhysicalSegmentPostgresTest.concurrent

    def replacement(self,name):
        user=self.fixture._login_user(name,'operator')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=user.id));db.session.commit()
        return user.id

    def baseline(self):
        return fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)['rows'][0]['edit_baseline']

    def test_inflight_original_autosave_cannot_overwrite_a_committed_handoff(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        replacement=self.replacement('pg-handoff-b');expected=self.baseline();aid=self.ids[3]
        def action(i,g,f,d):
            if i==0:
                fuel.swap_assignment_fueler(g,d,aid,replacement);return 'swapped'
            try:
                fuel.save_fueler_entry(g,f,dict(assignment_id=aid,expected=json.dumps(expected),actual_left='16'))
                return 'saved-before-swap'
            except ValueError:
                db.session.rollback();return 'rejected'
        result=self.concurrent(action)
        self.assertEqual(result[0],'swapped')
        self.assertIn(result[1],('saved-before-swap','rejected','conflict'))
        w=db.session.get(NeoScorpionFuelWorkState,self.ids[4])
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertIn(next(t.actual_lbs for t in w.tank_states if t.tank_code=='left'),(15000,16000))
        self.assertEqual(w.physical_fueler_user_id,self.ids[1]);self.assertEqual(w.physical_transfer_gallons,3000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(w.fuel_assignment.assigned_fueler_user_id,replacement)

    def test_simultaneous_dispatcher_swaps_accept_one_stale_baseline(self):
        bids=[self.replacement('pg-handoff-b'),self.replacement('pg-handoff-c')]
        expected=dict(expected_work_ref=self.ids[4],expected_assigned_fueler_user_id=self.ids[1],
                      expected_assigned_truck_id=self.truck.id)
        result=self.concurrent(lambda i,g,f,d: fuel.swap_assignment_fueler(g,d,self.ids[3],bids[i],expected=expected).changed)
        self.assertEqual(result.count(True),1);self.assertEqual(result.count('conflict'),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def test_submission_and_handoff_serialize_without_reopening_or_lost_data(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        replacement=self.replacement('pg-handoff-b');expected=self.baseline();aid=self.ids[3]
        def action(i,g,f,d):
            try:
                if i==0:
                    fuel.swap_assignment_fueler(g,d,aid,replacement);return 'swapped'
                fuel.submit_fueler_assignment(g,f,aid,expected=expected);return 'submitted'
            except ValueError:
                db.session.rollback();return 'rejected'
        results=self.concurrent(action)
        self.assertEqual(sum(r in ('swapped','submitted') for r in results),1)
        w=db.session.get(NeoScorpionFuelWorkState,self.ids[4])
        self.assertIsNotNone(w.off_at_utc)
        self.assertEqual(w.physical_transfer_gallons,3000)
        self.assertEqual(len(w.tank_states),3)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_existing_audited_source_handoff_denies_old_actor_and_preserves_replacement_save(self):
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        replacement=self.replacement('pg-existing-handoff')
        fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,replacement);db.session.commit()
        facts=dict(self.work.performance_context);facts.pop('participation')
        facts['exclusions']=['Fueler changed during physical segment']
        self.work.performance_context=facts;db.session.commit()
        identity=fixtures.PhysicalSegmentPostgresTest.swap_tail(self)
        rows_before=NeoScorpionFuelWorkState.query.count()
        def save(i,g,f,d):
            actor=f if i==0 else db.session.get(User,replacement)
            try:
                fuel.save_fueler_entry(g,actor,dict(assignment_id=self.ids[3],**identity,notes='Replacement retains original work'))
                return 'saved'
            except ValueError:
                db.session.rollback();return 'rejected'
        self.assertEqual(self.concurrent(save),['rejected','saved'])
        w=db.session.get(NeoScorpionFuelWorkState,self.ids[4])
        self.assertEqual(w.notes,'Replacement retains original work')
        self.assertEqual(w.physical_fueler_user_id,self.ids[1])
        self.assertEqual(w.performance_context['participation']['handoff_fueler_id'],replacement)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),rows_before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
