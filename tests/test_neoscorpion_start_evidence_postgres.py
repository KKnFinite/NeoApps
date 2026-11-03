"""R03I PostgreSQL proofs: provenance, atomic rejection and exclusive starts."""
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionFuelAuditEntry,NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_dispatch_planning as factory
from tests import test_neoscorpion_fueling_start as starts
from tests import test_neoscorpion_start_evidence_contract as contracts
from tests import test_neoscorpion_fob_evidence_postgres as concurrency
from tests import neoscorpion_start_workflow as workflow


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class StartContractPostgresTest(contracts.StartEvidenceContractTest):
    def setUp(self):
        pg.postgres_setup(self,factory,lambda:contracts.StartEvidenceContractTest.setUp(self))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class OriginalStartPostgresTest(unittest.TestCase):
    def run_case(self):
        case=starts.FuelingStartHotfixTest(self._testMethodName)
        def exercise():
            self.addCleanup(case.doCleanups);case.setUp();db.session.commit()
            with patch.dict(case.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(case.fixture.app)
            getattr(case,self._testMethodName)()
        pg.postgres_setup(self,factory,exercise)

    def test_legacy_premature_on_preserved_but_never_used_for_overdue_estimate(self):self.run_case()
    def test_manual_on_overrides_inferred_on_and_predicted_travel_not_busy_truck(self):self.run_case()
    def test_partial_or_truckless_non_fob_does_not_start_and_complete_valid_save_does(self):self.run_case()
    def test_preassigned_truck_release_travel_and_expiry_never_auto_create_on(self):self.run_case()


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PhysicalStartConcurrencyPostgresTest(unittest.TestCase):
    setUp=StartContractPostgresTest.setUp
    prepare=contracts.StartEvidenceContractTest.prepare
    save=contracts.StartEvidenceContractTest.save
    readings=contracts.StartEvidenceContractTest.readings
    run_concurrent=concurrency.FobConcurrencyPostgresTest.run_concurrent

    def test_duplicate_physical_on_records_one_identity_timestamp_and_audit(self):
        self.prepare();work=self.save(**self.readings()).fuel_work_state
        wid,truck_id,now=work.id,self.truck.id,self.now
        results=self.run_concurrent(lambda i,g,f,d,aid:fuel.confirm_fueler_truck_on(g,f,aid,
            expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck_id,
            expected_work_ref=wid,require_work_ref=True,now_utc=now)[0])
        self.assertEqual(sorted(results),[False,True])
        work=db.session.get(NeoScorpionFuelWorkState,wid)
        self.assertEqual(work.manual_on_at_utc,now);self.assertIsNone(work.on_at_utc)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='truck_on',fuel_work_state_id=wid).count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,10000)

    def test_competing_missions_cannot_physically_start_the_same_truck(self):
        self.prepare();self.save(**self.readings())
        other=workflow.plan_other(self,at=self.now);other_id=other.id
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=str(other_id),**self.readings()),now_utc=self.now);db.session.commit()
        other_work=other.active_work_state_id
        current_work=self.assignment.active_work_state_id;truck_id,now=self.truck.id,self.now
        def action(i,g,f,d,aid):
            try:
                fuel.confirm_fueler_truck_on(g,f,other_id if i else aid,expected_cycle=1,
                    expected_tail='N492UP' if i else 'N491UP',expected_truck_id=truck_id,
                    expected_work_ref=other_work if i else current_work,require_work_ref=True,now_utc=now)
                return 'started'
            except ValueError as exc:
                self.assertIn('TRUCK STILL FUELING',str(exc));db.session.rollback();return 'blocked'
        self.assertEqual(sorted(self.run_concurrent(action)),['blocked','started'])
        self.assertEqual(NeoScorpionFuelWorkState.query.filter(NeoScorpionFuelWorkState.manual_on_at_utc.is_not(None)).count(),1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='truck_on').count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
