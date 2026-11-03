"""R03J real PostgreSQL control eligibility, rollback and OFF races."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_fueler_off as original
from tests import test_neoscorpion_off_presentation_contract as contracts
from tests import test_neoscorpion_fob_evidence_postgres as concurrent
from tests import neoscorpion_off_workflow as workflow


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class OffPresentationPostgresTest(contracts.OffPresentationContractTest):
    def setUp(self):
        pg.postgres_setup(self,original,lambda:contracts.OffPresentationContractTest.setUp(self))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class OriginalOffPostgresTest(unittest.TestCase):
    def run_case(self):
        case=original.NeoScorpionFuelerOffTest(self._testMethodName)
        def exercise():
            case.setUp();self.addCleanup(case.tearDown);db.session.commit()
            with patch.dict(case.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(case.app)
            getattr(case,self._testMethodName)()
        pg.postgres_setup(self,original,exercise)
    def test_get_never_marks_off_and_ready_card_offers_action(self):self.run_case()
    def test_physical_off_does_not_require_final_tf_and_repeat_is_noop(self):self.run_case()


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class OffPresentationConcurrencyTest(unittest.TestCase):
    setUp=OffPresentationPostgresTest.setUp
    ready=contracts.OffPresentationContractTest.ready
    run_concurrent=concurrent.FobConcurrencyPostgresTest.run_concurrent

    def prepared(self):
        self.ready();self.fueler=self.user
        self.expected=fuel.fueler_context(self.gateway,self.user,assignment_id=self.assignment.id,include_timing=False)['rows'][0]['edit_baseline']
        return self.work.id,self.truck.id,self.user.id,workflow.revision(self.operation)

    def test_duplicate_off_records_one_timestamp_revision_and_availability_change(self):
        wid,tid,uid,revision=self.prepared();at=datetime(2026,8,18,4);expected=self.expected
        results=self.run_concurrent(lambda i,g,f,d,aid:fuel.mark_fueler_off(g,f,aid,expected=expected,now_utc=at).changed)
        self.assertEqual(sorted(results),[False,True])
        work=db.session.get(NeoScorpionFuelWorkState,wid)
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(at,uid))
        self.assertEqual(workflow.revision(work.fuel_assignment.sort_date_operation),revision+1)
        selection=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one()
        self.assertEqual(sum(r.get('source')=='physical_off' for r in selection.availability_history),1)
        self.assertEqual(selection.current_gallons,1000);self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_concurrent_final_entry_and_physical_off_preserve_both_valid_changes(self):
        wid,tid,uid,revision=self.prepared();expected=self.expected;at=datetime(2026,8,18,4)
        def action(i,g,f,d,aid):
            if i:return fuel.save_fueler_entry(g,f,dict(assignment_id=str(aid),expected=json.dumps(expected),actual_left='12',transfer_fuel_gallons='300'),require_expected=True,now_utc=datetime(2026,8,18,4,1)).changed
            return fuel.mark_fueler_off(g,f,aid,expected=expected,now_utc=at).changed
        self.assertEqual(self.run_concurrent(action),[True,True])
        work=db.session.get(NeoScorpionFuelWorkState,wid)
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(at,uid))
        self.assertEqual(next(t.actual_lbs for t in work.tank_states if t.tank_code=='left'),12000)
        self.assertEqual(work.physical_transfer_gallons,300)
        self.assertIsNone(work.fueler_submitted_at_utc)
        self.assertEqual(workflow.revision(work.fuel_assignment.sort_date_operation),revision+2)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
