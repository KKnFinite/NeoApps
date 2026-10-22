"""True concurrent completions and additive bootstrap in disposable PG."""
import unittest
from unittest.mock import patch
from sqlalchemy import inspect
from app.extensions import db
from app.models import NeoScorpionDirectEntry, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent
from app.services import neoscorpion_direct_entry as direct, neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as fixtures


@unittest.skipUnless(fixtures.URI,'Requires disposable loopback PostgreSQL')
class DirectEntryPostgresTest(unittest.TestCase):
    setUp=fixtures.PhysicalSegmentPostgresTest.setUp
    drop_schema=fixtures.PhysicalSegmentPostgresTest.drop_schema
    prepare=fixtures.PhysicalSegmentPostgresTest.prepare
    concurrent=fixtures.PhysicalSegmentPostgresTest.concurrent

    def fresh(self):
        mission=self.fixture._mission('UPS-DIRECT','N492UP',25000,1)
        db.session.commit();return mission

    def form(self,mission):
        return dict(direct_entry_token=direct.token_for(self.operation,mission,self.dispatcher),
            actual_left='14',actual_ctr='0',actual_right='14')

    def test_concurrent_dispatchers_get_one_completion_and_one_conflict(self):
        mission=self.fresh();mid=mission.id
        other=self.fixture._login_user('direct-second-dispatcher','simulator')
        actor_ids=[self.dispatcher.id,other.id]
        forms=[self.form(mission),{**self.form(mission),
            'direct_entry_token':direct.token_for(self.operation,mission,other)}]
        before=(NeoScorpionFuelWorkState.query.count(),NeoScorpionFuelingEvent.query.count(),self.selection.current_gallons)
        def save(i,gateway,_fueler,dispatcher):
            try:
                from app.models import User
                record,changed=direct.complete_entry(gateway,db.session.get(User,actor_ids[i]),mid,forms[i])
                db.session.commit();return 'complete' if changed else 'duplicate'
            except direct.DirectEntryConflict:
                db.session.rollback();return 'conflict'
        self.assertEqual(sorted(self.concurrent(save)),['complete','conflict'])
        self.assertEqual(NeoScorpionDirectEntry.query.count(),1)
        self.assertEqual(before,(NeoScorpionFuelWorkState.query.count(),NeoScorpionFuelingEvent.query.count(),self.selection.current_gallons))

    def test_identical_concurrent_retry_recovers_same_receipt(self):
        mission=self.fresh();mid=mission.id;form=self.form(mission)
        def save(i,gateway,_fueler,dispatcher):
            record,changed=direct.complete_entry(gateway,dispatcher,mid,form)
            db.session.commit();return record.id,changed
        result=self.concurrent(save)
        self.assertEqual(result[0][0],result[1][0]);self.assertEqual(sorted(r[1] for r in result),[False,True])
        self.assertEqual(NeoScorpionDirectEntry.query.count(),1)

    def test_bootstrap_adds_only_new_table_and_preserves_existing_work(self):
        before=(self.work.id,self.work.manual_on_at_utc,self.selection.current_gallons)
        db.session.commit()
        NeoScorpionDirectEntry.__table__.drop(db.engine)
        self.assertNotIn('neoscorpion_direct_entries',inspect(db.engine).get_table_names())
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(self.fixture.app)
        self.assertIn('neoscorpion_direct_entries',inspect(db.engine).get_table_names())
        self.assertEqual(before,(self.work.id,self.work.manual_on_at_utc,self.selection.current_gallons))

    def test_assignment_after_entry_open_rejects_without_partial_completion(self):
        mission=self.fresh();form=self.form(mission)
        assignment=self.fixture._assignment(mission)
        assignment.assigned_fueler_user_id=self.fueler.id;db.session.commit()
        with self.assertRaises(direct.DirectEntryConflict):direct.complete_entry(self.gateway,self.dispatcher,mission.id,form)
        db.session.rollback();self.assertEqual(NeoScorpionDirectEntry.query.count(),0)
        self.assertNotEqual(mission.fuel_status,'complete')

    def test_completion_racing_resource_assignment_cannot_create_both_states(self):
        mission=self.fresh();mid=mission.id;form=self.form(mission);truck_id=self.truck.id;fueler_id=self.fueler.id
        def save(i,gateway,_fueler,dispatcher):
            try:
                if i==0:
                    direct.complete_entry(gateway,dispatcher,mid,form)
                else:
                    fuel.save_dispatch_assignment(gateway,dict(mission_id=mid,
                        expected_work_ref='',expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
                        assigned_fueler_user_id=fueler_id,assigned_truck_id=truck_id))
                db.session.commit();return 'complete' if i==0 else 'assigned'
            except ValueError:
                db.session.rollback();return 'conflict'
        outcomes=self.concurrent(save)
        self.assertEqual(outcomes.count('conflict'),1,outcomes)
        from app.models import NeoScorpionFuelAssignment
        assignment=NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mid).first()
        record=NeoScorpionDirectEntry.query.filter_by(sort_date_mission_id=mid).first()
        self.assertNotEqual(bool(record),bool(assignment and assignment.assigned_truck_id))
