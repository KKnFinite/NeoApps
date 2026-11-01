"""Loopback PostgreSQL only; bounded two-worker reconciliation races."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from app import create_app
from app.extensions import db
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services import neoscorpion_spear_decisions as service
from tests import test_neoscorpion_spear_learning_capture as fixtures
from tests import test_spear_reconciliation as cases


URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class SpearReconciliationPostgresTest(cases.SpearReconciliationTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {'localhost', '127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema = 'spear_reconcile_' + uuid4().hex
        self.admin = create_engine(URI, poolclass=NullPool)
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def drop():
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
        self.addCleanup(drop)
        def local(config):
            return create_app(type('ReconcileConfig', (config,), dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'poolclass': NullPool, 'connect_args': {'options':
                    f'-csearch_path={self.schema} -clock_timeout=3000 -cstatement_timeout=10000'}})))
        with patch.object(fixtures, 'create_app', local):
            super().setUp()
        self.app = self.fixture.app

    def test_concurrent_workers_cannot_duplicate_one_final_outcome(self):
        row = self.decision()
        identifier, gateway_id = row.id, self.gateway_id
        entered, other_done, start = Event(), Event(), Barrier(2)
        original = service._enqueue
        def enqueue(*args, **kwargs):
            entered.set()
            self.assertTrue(other_done.wait(5), 'Second worker must skip the locked decision')
            return original(*args, **kwargs)
        def worker(_):
            with self.app.app_context():
                start.wait(5)
                try:
                    service.reconcile(gateway_id)
                finally:
                    other_done.set()
                    db.session.remove()
        with patch.object(service, '_enqueue', side_effect=enqueue) as capture:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(worker, range(2)))
        self.assertTrue(entered.is_set())
        capture.assert_called_once()
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(Observation, identifier).reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 1)

    def test_disconnected_claim_rolls_back_and_next_worker_recovers(self):
        row = self.decision()
        identifier, gateway_id = row.id, self.gateway_id
        connection = db.engine.connect()
        connection.begin()
        connection.execute(text('SELECT id FROM neoscorpion_spear_observations WHERE id=:id FOR UPDATE'), {'id': identifier})
        try:
            self.reconcile()
            self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 0)
        finally:
            connection.close()  # A lost worker connection releases its transaction/row lock.
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)
        self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 1)

    def test_busy_canonical_completion_is_skipped_without_rescheduling_writes(self):
        row = self.decision()
        identifier = row.id
        assignment_id = self.assignment.id
        due = row.reconcile_after
        with db.engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(text('SELECT id FROM neoscorpion_fuel_assignments WHERE id=:id FOR UPDATE'), {'id': assignment_id})
            with cases.sql_counts() as counts:
                self.reconcile()
            self.assertEqual(counts['observation_updates'], 0)
            self.assertEqual(db.session.get(Observation, identifier).reconcile_after, due)
            self.assertEqual(Observation.query.filter_by(kind='outcome').count(), 0)
            transaction.rollback()
        self.reconcile()
        self.assertIsNotNone(row.reconciled_at)

    def test_due_query_has_a_matching_partial_index(self):
        self.decision()
        db.session.execute(text('SET LOCAL enable_seqscan=off'))
        plan = db.session.execute(text("""EXPLAIN SELECT id FROM neoscorpion_spear_observations
            WHERE gateway_id=:gateway AND kind='decision' AND reconciled_at IS NULL
              AND reconcile_after<=CURRENT_TIMESTAMP
            ORDER BY reconcile_after,id LIMIT 40 FOR UPDATE SKIP LOCKED"""), {'gateway': self.gateway_id}).all()
        self.assertIn('ix_spear_decision_unresolved', str(plan))


if __name__ == '__main__':
    unittest.main()
