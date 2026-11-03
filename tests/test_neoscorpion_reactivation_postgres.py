"""R03G original workflows and independent PostgreSQL handoff/cycle races."""
import unittest
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from app.extensions import db
from app.models import Gateway,User,NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_segments_postgres as segment
from tests import test_neoscorpion_dispatch_planning as factory
from tests import test_neoscorpion_dispatch_order as ordering
from tests import test_neoscorpion_truck_driver as driver
from tests import test_neoscorpion_reactivation_contract as contracts
from tests.neoscorpion_reactivation_workflow import prepare,event_snapshot
from tests.neoscorpion_handoff_fixture import submit_fueler_only


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class ReactivationPostgresTest(unittest.TestCase):
    def run_case(self,cls,name):
        case=cls(name)
        def exercise():
            self.addCleanup(case.doCleanups);case.setUp()
            app=case.f.fixture.app if hasattr(case,'f') else case.fixture.app
            # Bootstrap owns another connection; release setup SELECT/FK locks
            # before it installs production constraints in this disposable schema.
            db.session.commit()
            with patch.dict(app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(app)
            getattr(case,name)();self.assertEqual(db.engine.dialect.name,'postgresql')
        pg.postgres_setup(self,factory,exercise)

    def test_valid_followup_returns_completed_mission_to_unfinished(self):
        self.run_case(ordering.DispatchReactivationTest,self._testMethodName)

    def test_current_upcoming_off_and_completed_readings_are_canonical(self):
        self.run_case(driver.TruckDriverTest,self._testMethodName)

    def test_followup_permission_stale_cycle_sort_and_csrf_preserve_closed_parent(self):
        self.run_case(contracts.ReactivationContractTest,self._testMethodName)

    def test_partial_followup_failure_rolls_back_then_retry_allocates_one_cycle(self):
        self.run_case(contracts.ReactivationContractTest,self._testMethodName)

    def test_driver_read_is_operationally_read_only_and_separates_closed_and_upcoming_visits(self):
        self.run_case(contracts.ReactivationContractTest,self._testMethodName)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class HandoffConcurrencyPostgresTest(unittest.TestCase):
    setUp=segment.PhysicalSegmentPostgresTest.setUp
    drop_schema=segment.PhysicalSegmentPostgresTest.drop_schema
    concurrent=segment.PhysicalSegmentPostgresTest.concurrent
    prepare=prepare
    save=segment.PhysicalSegmentPostgresTest.save
    off=segment.PhysicalSegmentPostgresTest.off
    final_data=segment.PhysicalSegmentPostgresTest.final_data

    def ready(self,submitted=True):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        if submitted:submit_fueler_only(self.gateway,self.assignment.id,now_utc=self.start+timedelta(minutes=13));db.session.commit()

    def assert_posted_once(self):
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        truck_id=db.session.get(NeoScorpionFuelWorkState,self.ids[4]).physical_truck_id
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,7000)

    def test_competing_submission_and_dispatcher_completion_cannot_reorder_handoff(self):
        self.ready(False)
        expected=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)['rows'][0]['edit_baseline']
        together=Barrier(2);aid=self.assignment.id
        def action(index,g,f,d):
            together.wait(timeout=5)
            if index==0:return fuel.submit_fueler_assignment(g,f,aid,expected=expected).changed
            try:return fuel.complete_fueled_assignment(g,d,aid).changed
            except ValueError as exc:
                self.assertIn('Fueler COMPLETE ASSIGNMENT required',str(exc));db.session.rollback();return 'blocked'
        result=self.concurrent(action)
        self.assertIs(result[0],True);self.assertIn(result[1],(True,'blocked'))
        db.session.expire_all()
        if result[1]=='blocked':
            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
            truck_id=db.session.get(NeoScorpionFuelWorkState,self.ids[4]).physical_truck_id
            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,10000)
            fuel.complete_fueled_assignment(db.session.get(Gateway,self.ids[0]),db.session.get(User,self.ids[2]),aid);db.session.commit()
        self.assert_posted_once()

    def test_duplicate_verification_preserves_one_event_and_one_deduction(self):
        self.ready();aid=self.assignment.id
        self.assertEqual(sorted(self.concurrent(lambda i,g,f,d:fuel.complete_fueled_assignment(g,d,aid).changed)),[False,True])
        self.assert_posted_once()
        original=event_snapshot(NeoScorpionFuelingEvent.query.one())
        self.assertFalse(fuel.complete_fueled_assignment(db.session.get(Gateway,self.ids[0]),db.session.get(User,self.ids[2]),aid).changed);db.session.commit()
        self.assertEqual(event_snapshot(NeoScorpionFuelingEvent.query.one()),original)

    def test_concurrent_followup_requests_use_one_fresh_cycle_and_reject_stale_parent(self):
        self.ready();aid=self.assignment.id
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,aid);db.session.commit()
        original=event_snapshot(NeoScorpionFuelingEvent.query.one());together=Barrier(2)
        def action(index,g,f,d):
            together.wait(timeout=5)
            try:
                fuel.start_follow_up_fuel_cycle(g,d,aid,'uplift','35',None,None,expected_cycle=1,expected_tail='N491UP');return 'started'
            except ValueError as exc:
                self.assertIn('changed',str(exc).lower());db.session.rollback();return 'stale'
        self.assertEqual(sorted(self.concurrent(action)),['stale','started'])
        db.session.expire_all()
        self.assertEqual(db.session.get(NeoScorpionFuelAssignment,aid).current_cycle_number,2)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assert_posted_once();self.assertEqual(event_snapshot(NeoScorpionFuelingEvent.query.one()),original)
