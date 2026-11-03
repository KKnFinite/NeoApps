"""Current-visit corrections, rollback and competing completion in real PostgreSQL."""
import unittest
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_fuel_corrections as cases
from tests import test_motherbrain_block_out_authority_postgres as pg
from tests import test_neoscorpion_lifecycle_postgres as lifecycle


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class FuelCorrectionPostgresTest(unittest.TestCase):
    def run_case(self, name):
        case = cases.NeoScorpionFuelCorrectionTest(name)

        def exercise():
            self.addCleanup(case.doCleanups)
            case.setUp()
            self.addCleanup(case.tearDown)
            with patch.dict(case.app.config, {'TESTING': False}):
                ensure_neoscorpion_production_schema(case.app)
            getattr(case, name)()
            self.assertEqual(db.engine.dialect.name, 'postgresql')

        pg.postgres_setup(self, cases, exercise)

    def compete(self, action):
        case = lifecycle.LifecyclePostgresTest(
            'test_concurrent_dispatcher_verification_posts_once_after_submission')
        self.addCleanup(case.doCleanups)
        case.setUp()
        case.save(case.start + timedelta(minutes=10), **case.final_data())
        case.off()
        case.submit()
        stock_id, before = db.session.query(
            NeoScorpionSortTruck.id, NeoScorpionSortTruck.current_gallons).filter_by(
                sort_date_operation_id=case.operation.id, fuel_truck_id=case.truck.id).one()
        assignment_id = case.ids[3]
        together = Barrier(2)

        def mutate(index, gateway, fueler, dispatcher):
            together.wait(timeout=5)
            try:
                if index == 0:
                    result = fuel.complete_fueled_assignment(gateway, dispatcher, assignment_id)
                elif action == 'correct':
                    result = fuel.correct_fuel_actuals(gateway, dispatcher, dict(
                        assignment_id=assignment_id, correct_actual_left='16',
                        correction_reason='Verified gauge correction.'))
                else:
                    result = fuel.reopen_fueler_off(gateway, dispatcher, assignment_id,
                                                  'Physical work must resume.')
                return result.changed
            except ValueError as exc:
                db.session.rollback()
                self.assertRegex(str(exc), 'Completed fuel assignments' if index else
                                 'MARK OFF|Fueler COMPLETE ASSIGNMENT')
                return False

        results = case.concurrent(mutate)
        self.assertEqual(sorted(results), [False, True])
        db.session.expire_all()
        events = NeoScorpionFuelingEvent.query.all()
        audits = NeoScorpionFuelAuditEntry.query.filter_by(
            action='correct_actual' if action == 'correct' else 'reopen_off').all()
        stock = db.session.get(NeoScorpionSortTruck, stock_id)
        if results[0]:
            self.assertEqual(len(events), 1)
            self.assertEqual(audits, [])
            self.assertEqual(stock.current_gallons, before-events[0].transfer_fuel_gallons)
        else:
            self.assertEqual(events, [])
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0].fuel_work_state_id,case.ids[4])
            self.assertEqual(audits[0].changed_by_user_id,case.ids[2])
            self.assertEqual(stock.current_gallons, before)
            self.assertFalse(case.row()['normal_completion_ready'])
        self.assertEqual(db.engine.dialect.name, 'postgresql')

    def test_correction_and_completion_serialize(self):
        self.compete('correct')

    def test_reopen_and_completion_serialize(self):
        self.compete('reopen')


def postgres_case(name):
    def test(self):
        self.run_case(name)
    test.__name__ = name
    return test


# The schema-sync method explicitly tests SQLite DDL; all other module cases,
# including the seven original IDs and existing no-op/delta audits, run on PG.
for name in unittest.defaultTestLoader.getTestCaseNames(cases.NeoScorpionFuelCorrectionTest):
    if name != 'test_audit_table_bootstrap_and_dispatch_get_are_read_only':
        setattr(FuelCorrectionPostgresTest, name, postgres_case(name))
