"""Focused disposable PostgreSQL evidence, concurrency and rollback checks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from sqlalchemy import text
from app.extensions import db
from app.models import Gateway, User, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionTruckInventoryAudit
from app.services import neoscorpion as fuel
from tests import test_fob_uplift_optional_reasons as contracts
from tests import test_motherbrain_block_out_authority_postgres as pg


@skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class FobUpliftPostgresTest(contracts.FobUpliftTest):
    def setUp(self):
        pg.postgres_setup(self, contracts.visit_fixtures, super().setUp)

    def test_simultaneous_uplifts_create_one_cycle_and_no_phantom_fob_event(self):
        operation, mission, assignment, original = self.fob()
        ids = self.gateway.id, self.dispatcher.id, assignment.id
        db.session.commit()
        db.session.remove()
        barrier = Barrier(2)
        app = self.app

        def start(_):
            with app.app_context():
                try:
                    db.session.execute(text("SET LOCAL lock_timeout='5s'"))
                    gateway = db.session.get(Gateway, ids[0])
                    actor = db.session.get(User, ids[1])
                    barrier.wait(5)
                    fuel.start_follow_up_fuel_cycle(gateway, actor, ids[2], 'uplift', '32.0', None, None,
                        expected_cycle=1, expected_tail='N412UP')
                    db.session.commit()
                    return 'started'
                except ValueError:
                    db.session.rollback()
                    return 'rejected'
                finally:
                    db.session.remove()

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(start, range(2))), ['rejected', 'started'])
        assignment = db.session.get(NeoScorpionFuelAssignment, ids[2])
        self.assertEqual(assignment.current_cycle_number, 2)
        self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=ids[2]).count(), 2)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(), 0)
        snapshot = fuel.neoscorpion_live_revision(db.session.get(Gateway, ids[0]), include_remaining=True)
        self.assertEqual(snapshot["remaining_fueling_assignments"], 1)


@skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class OptionalReasonPostgresTest(contracts.OptionalDecisionReasonTest):
    def setUp(self):
        # The account fixture's .admin is its actor, not a database engine.
        from types import SimpleNamespace
        scope = SimpleNamespace(addCleanup=self.addCleanup)
        pg.postgres_setup(scope, contracts.account_fixtures, super().setUp)
