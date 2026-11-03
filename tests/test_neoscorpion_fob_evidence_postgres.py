"""R03H isolated PostgreSQL ownership, rollback and competing FOB requests."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import text
from app.extensions import db
from app.models import Gateway,User,NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_dispatch_planning as factory
from tests import test_neoscorpion_fueling_start as starts
from tests import test_neoscorpion_fueling_event_foundation as foundation
from tests import test_neoscorpion_fob_evidence_contract as contracts


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class FobContractPostgresTest(contracts.FobEvidenceContractTest):
    def setUp(self):
        pg.postgres_setup(self,factory,lambda:contracts.FobEvidenceContractTest.setUp(self))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):
            ensure_neoscorpion_production_schema(self.fixture.app)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class OriginalEvidencePostgresTest(unittest.TestCase):
    def run_case(self,cls,module):
        case=cls(self._testMethodName)
        def exercise():
            self.addCleanup(case.doCleanups);case.setUp()
            if not hasattr(case,'fixture'):self.addCleanup(case.tearDown)
            app=case.fixture.app if hasattr(case,'fixture') else case.app
            db.session.commit()
            with patch.dict(app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(app)
            getattr(case,self._testMethodName)()
        pg.postgres_setup(self,module,exercise)

    def test_fuel_on_board_without_transfer_creates_no_events(self):
        self.run_case(foundation.NeoScorpionFuelingEventFoundationTest,foundation)

    def test_identified_truckless_fob_verification_is_preserved(self):
        self.run_case(starts.FuelingStartHotfixTest,factory)

    def test_legacy_legitimate_and_completed_timestamps_are_not_rewritten(self):
        self.run_case(starts.FuelingStartHotfixTest,factory)

    def test_uncertain_legacy_readings_never_slide_a_legitimate_on_forward(self):
        self.run_case(starts.FuelingStartHotfixTest,factory)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class FobConcurrencyPostgresTest(unittest.TestCase):
    setUp=FobContractPostgresTest.setUp
    prepare=contracts.FobEvidenceContractTest.prepare
    save=contracts.FobEvidenceContractTest.save
    ready=contracts.FobEvidenceContractTest.ready
    assert_no_physical_posting=contracts.FobEvidenceContractTest.assert_no_physical_posting

    def run_concurrent(self,callback):
        ids=self.gateway.id,self.fueler.id,self.dispatcher.id,self.assignment.id
        app=self.fixture.app;db.session.commit();db.session.remove();together=Barrier(2)
        def run(index):
            with app.app_context():
                try:
                    db.session.execute(text("SET LOCAL lock_timeout='5s'"))
                    g,f,d=(db.session.get(cls,identity) for cls,identity in zip((Gateway,User,User),ids[:3]))
                    together.wait(timeout=5);result=callback(index,g,f,d,ids[3]);db.session.commit();return result
                finally:db.session.remove()
        with ThreadPoolExecutor(2) as pool:result=list(pool.map(run,(0,1)))
        self.assignment=db.session.get(NeoScorpionFuelAssignment,ids[3]);return result

    def test_simultaneous_fob_verification_is_exactly_once_without_physical_posting(self):
        work=self.ready();wid=work.id
        result=self.run_concurrent(lambda i,g,f,d,aid:fuel.complete_fuel_on_board(g,d,aid).changed)
        self.assertEqual(sorted(result),[False,True])
        self.assert_no_physical_posting(db.session.get(NeoScorpionFuelWorkState,wid))
        self.assertIsNotNone(self.assignment.fuel_on_board_at_utc)

    def test_competing_measurement_change_cannot_leave_insufficient_fuel_verified(self):
        self.ready()
        def action(i,g,f,d,aid):
            try:
                if i==0:
                    fuel.save_fueler_entry(g,f,dict(assignment_id=str(aid),actual_left='10',actual_right='10'))
                    return 'corrected'
                return 'verified' if fuel.complete_fuel_on_board(g,d,aid).changed else 'duplicate'
            except ValueError as exc:
                self.assertTrue('Insufficient verified fuel' in str(exc) or 'Completed fuel assignments' in str(exc),str(exc))
                db.session.rollback();return 'rejected'
        result=self.run_concurrent(action)
        self.assertEqual(result.count('rejected'),1)
        work=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        total=sum(t.actual_lbs for t in work.tank_states)
        self.assertEqual((total,self.assignment.fuel_on_board_at_utc is not None),
            (30000,True) if 'verified' in result else (20000,False))
        self.assert_no_physical_posting(work)
