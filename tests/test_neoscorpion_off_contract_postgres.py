"""Real PostgreSQL identity/rollback guards and competing physical OFF actions."""
import unittest
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_fuel_planning as planning
from tests import test_neoscorpion_fueler_off as off
from tests import test_neoscorpion_fueling_event_foundation as events
from tests import test_neoscorpion_off_contract as safety
from tests import test_neoscorpion_lifecycle_postgres as lifecycle


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PhysicalOffPostgresTest(unittest.TestCase):
    def run_case(self,module,cls,name,*,factory=None):
        case=cls(name)
        def exercise():
            self.addCleanup(case.doCleanups)
            case.setUp();self.addCleanup(case.tearDown)
            app=case.fixture.app if module is safety else case.app
            with patch.dict(app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(app)
            getattr(case,name)()
            self.assertEqual(db.engine.dialect.name,'postgresql')
        pg.postgres_setup(self,factory or module,exercise)

    def test_apu_yes_without_source_blocks_off(self):
        self.run_case(planning,planning.NeoScorpionFuelPlanningTest,self._testMethodName)

    def test_source_aware_fueler_workflow_preserves_source_through_off(self):
        self.run_case(planning,planning.NeoScorpionFuelPlanningTest,self._testMethodName)

    def test_incomplete_actual_and_unconfirmed_apu_block_off(self):
        self.run_case(off,off.NeoScorpionFuelerOffTest,self._testMethodName)

    def test_existing_save_and_off_create_no_events(self):
        self.run_case(events,events.NeoScorpionFuelingEventFoundationTest,self._testMethodName)

    def test_truck_on_identity_csrf_and_foreign_requests(self):
        self.run_case(safety,safety.PhysicalOffContractTest,self._testMethodName,factory=off)

    def test_physical_off_failure_rolls_back_and_retries_once(self):
        self.run_case(safety,safety.PhysicalOffContractTest,self._testMethodName,factory=off)

    def compete(self,submission):
        case=lifecycle.LifecyclePostgresTest('test_concurrent_dispatcher_verification_posts_once_after_submission')
        self.addCleanup(case.doCleanups);case.setUp()
        case.save(case.start+timedelta(minutes=10),**case.final_data())
        expected=case.baseline()
        together=Barrier(2)
        at=case.start+timedelta(minutes=15)
        def mutate(index,gateway,fueler,dispatcher):
            together.wait(timeout=5)
            if index==0 or not submission:
                return fuel.mark_fueler_off(gateway,fueler,case.ids[3],now_utc=at).changed
            try:
                return fuel.submit_fueler_assignment(gateway,fueler,case.ids[3],expected=expected,
                    now_utc=at+timedelta(minutes=1)).changed
            except fuel.FuelerDataConflict:
                db.session.rollback();return 'conflict'
            except ValueError as exc:
                db.session.rollback();self.assertIn('Record physical TRUCK OFF first',str(exc));return False
        result=case.concurrent(mutate)
        if submission:
            self.assertIs(result[0],True);self.assertIn(result[1],(True,False,'conflict'))
        else:self.assertEqual(sorted(result),[False,True])
        db.session.expire_all()
        work=db.session.get(NeoScorpionFuelWorkState,case.ids[4])
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(at,case.ids[1]))
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=case.operation.id,
            fuel_truck_id=case.truck.id).one().current_gallons,10000)
        self.assertEqual(bool(case.row()['fueler_submitted']),submission and result[1] is True)
        self.assertEqual(db.engine.dialect.name,'postgresql')

    def test_competing_off_is_idempotent(self):
        self.compete(False)

    def test_off_and_submission_keep_distinct_boundaries(self):
        self.compete(True)
