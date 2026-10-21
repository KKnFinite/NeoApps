"""Queue creation/promotion and repeated verification on disposable PostgreSQL."""
import unittest
from sqlalchemy import text,inspect
from app.extensions import db
from app.models import NeoScorpionPendingUplift,NeoScorpionFuelWorkState
from app.services import neoscorpion_pending_uplift as plans
from tests import test_neoscorpion_segments_postgres as pg
from tests.test_neoscorpion_truck_queue import TruckQueueTest


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class TruckQueuePostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=TruckQueueTest.save
    off=TruckQueueTest.off
    submit=TruckQueueTest.submit
    baseline=TruckQueueTest.baseline
    final_data=TruckQueueTest.final_data
    row=TruckQueueTest.row
    login=TruckQueueTest.login
    plan=TruckQueueTest.plan
    target=TruckQueueTest.target
    add_plan=TruckQueueTest.add_plan
    finish_visit=TruckQueueTest.finish_visit
    handoff=TruckQueueTest.handoff
    finish_child=TruckQueueTest.finish_child
    test_five_trucks=TruckQueueTest.test_five_trucks_planned_in_advance_each_receipt_handoff_and_reverse_verification
    test_last_verified_first=TruckQueueTest.test_verified_last_visit_retains_complete_line_and_exact_time_until_source_verified
    test_cancel_middle=TruckQueueTest.test_cancel_middle_and_unnecessary_head_keeps_other_plans_explicit

    def test_concurrent_create_accepts_one_queue_tail_then_next_intent_can_append(self):
        b=self.fixture._nightly_truck('B');db.session.commit()
        aid,wid,tid=self.assignment.id,self.work.id,b.id
        def create(index,g,f,d):
            try:
                plans.plan_continuation(g,d,dict(assignment_id=aid,expected_cycle=1,expected_tail='N491UP',
                    expected_work_ref=wid,expected_last_plan_ref='',proposed_truck_id=tid,pending_action='create'))
                return 'created'
            except plans.PendingUpliftConflict:db.session.rollback();return 'stale'
        self.assertEqual(sorted(self.concurrent(create)),['created','stale'])
        self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        # Worker contexts intentionally remove the original ORM session. Read
        # fresh fixture identities before exercising the next intentional edit.
        from app.models import Gateway,User,NeoScorpionFuelAssignment,NeoScorpionFuelTruck
        self.gateway=db.session.get(Gateway,self.ids[0])
        self.dispatcher=db.session.get(User,self.ids[2])
        self.assignment=db.session.get(NeoScorpionFuelAssignment,aid)
        self.truck=db.session.get(NeoScorpionFuelTruck,self.assignment.assigned_truck_id)
        self.add_plan(self.truck)
        self.assertEqual(NeoScorpionPendingUplift.query.count(),2)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def test_legacy_single_slot_migration_is_idempotent_and_preserves_plan(self):
        from app.services.neoscorpion_continuation_schema import migrate_continuation_queue
        first=self.plan();pid=first.id;version=first.version
        db.session.execute(text("DROP INDEX uq_scorpion_pending_legacy_uplift"))
        db.session.execute(text("CREATE UNIQUE INDEX uq_scorpion_pending_uplift_outstanding ON neoscorpion_pending_uplifts (mission_id,parent_cycle_number) WHERE status IN ('pending','review')"));db.session.commit()
        for _ in range(2):migrate_continuation_queue(db.session.connection());db.session.commit()
        self.assertEqual(db.session.get(NeoScorpionPendingUplift,pid).version,version)
        self.add_plan(self.truck)
        indexes={i['name'] for i in inspect(db.session.connection()).get_indexes('neoscorpion_pending_uplifts')}
        self.assertNotIn('uq_scorpion_pending_uplift_outstanding',indexes)
        self.assertIn('uq_scorpion_pending_legacy_uplift',indexes)
