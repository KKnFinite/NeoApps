"""Focused additive-schema and operation-lock proof in disposable PostgreSQL."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy import text
from app.extensions import db
from app.models import Gateway, NeoScorpionSortFueler, NeoScorpionFuelingEvent, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState
from app.services import neoscorpion as fuel
from app.services.neoscorpion_assets import set_nightly_fueler_break
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as fixture


@unittest.skipUnless(fixture.URI,'Requires disposable local PostgreSQL')
class BreakPostgresTest(unittest.TestCase):
    setUp=fixture.PhysicalSegmentPostgresTest.setUp
    drop_schema=fixture.PhysicalSegmentPostgresTest.drop_schema
    prepare=fixture.PhysicalSegmentPostgresTest.prepare
    concurrent=fixture.PhysicalSegmentPostgresTest.concurrent

    def test_concurrent_duplicate_break_advances_revision_once(self):
        before=fuel.neoscorpion_live_revision(self.gateway)['revision']
        oid=self.operation.id
        result=self.concurrent(lambda i,g,f,d:set_nightly_fueler_break(oid,f,'1','0').changed)
        self.assertEqual(sorted(result),[False,True])
        self.assertEqual(fuel.neoscorpion_live_revision(db.session.get(Gateway,self.ids[0]))['revision'],before+1)
        self.assertTrue(NeoScorpionSortFueler.query.one().on_break)
        self.assertEqual(db.session.get(NeoScorpionFuelAssignment,self.ids[3]).assigned_fueler_user_id,self.ids[1])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_break_and_new_assignment_serialize_without_lost_physical_work(self):
        mission=self.fixture._mission('UPS902','N492UP',25000,2);db.session.commit();mid=mission.id
        before=self.work.manual_on_at_utc;oid=self.operation.id
        def action(index,g,f,d):
            if index==0:
                set_nightly_fueler_break(oid,f,'1','0');return 'break'
            try:
                fuel.save_dispatch_assignment(g,dict(mission_id=mid,expected_work_ref='',
                    expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
                    assigned_fueler_user_id=f.id,assigned_truck_id=''))
                return 'assigned_before_break'
            except ValueError as exc:
                self.assertIn('ON BREAK',str(exc));db.session.rollback();return 'blocked'
        result=self.concurrent(action)
        self.assertEqual(result[0],'break')
        assigned=NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mid).first()
        self.assertEqual(bool(assigned and assigned.assigned_fueler_user_id),result[1]=='assigned_before_break')
        self.assertEqual(db.session.get(NeoScorpionFuelWorkState,self.ids[4]).manual_on_at_utc,before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_production_additive_bootstrap_defaults_preserves_and_rolls_back(self):
        identity=NeoScorpionSortFueler.query.one().id
        db.session.execute(text('ALTER TABLE neoscorpion_sort_fuelers DROP COLUMN on_break'));db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(NeoScorpionSortFueler.query.one().id,identity)
        self.assertFalse(NeoScorpionSortFueler.query.one().on_break)
        set_nightly_fueler_break(self.operation,self.fueler,'1','0');db.session.rollback()
        self.assertFalse(NeoScorpionSortFueler.query.one().on_break)

    def test_assignment_rechecks_break_after_another_transaction_changes_preloaded_roster(self):
        mission=self.fixture._mission('UPS903','N493UP',25000,3);db.session.commit()
        mid=mission.id;oid=self.operation.id;uid=self.ids[1]
        selected=NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=oid,user_id=uid).one()
        self.assertFalse(selected.on_break)
        def pause():
            with self.fixture.app.app_context():
                try:set_nightly_fueler_break(oid,uid,'1','0');db.session.commit()
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=1) as executor:executor.submit(pause).result(timeout=10)
        self.assertFalse(selected.on_break)  # Deliberately retain the old ORM identity.
        with self.assertRaisesRegex(ValueError,'ON BREAK'):
            fuel.save_dispatch_assignment(self.gateway,dict(mission_id=mid,expected_work_ref='',
                expected_assigned_fueler_user_id='',expected_assigned_truck_id='',
                assigned_fueler_user_id=uid,assigned_truck_id=''))
        db.session.rollback()
        self.assertTrue(NeoScorpionSortFueler.query.one().on_break)
