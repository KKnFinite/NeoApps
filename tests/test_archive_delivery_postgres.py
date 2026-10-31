"""Durable archive transaction/lease proofs in isolated loopback PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import os
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import Gateway
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services import neoscorpion_spear_decisions as delivery
from app.services.neoscorpion_spear_learning_capture import capture_completed_learning_outcome as capture
from tests import test_neoscorpion_learning_capture_routes as cases
from tests import test_neoscorpion_spear_learning_capture as fixtures


URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class ArchiveDeliveryPostgresTest(cases.SpearLearningCaptureRouteSafetyTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {'localhost', '127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema = 'archive_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def drop():
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
        self.addCleanup(drop)
        def local(config):
            return create_app(type('ArchiveConfig', (config,), dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options':
                    f'-csearch_path={self.schema} -clock_timeout=10000 -cstatement_timeout=15000'}})))
        with patch.object(fixtures, 'create_app', local):
            super().setUp()

    def test_concurrent_workers_claim_once_and_release_transactions_for_r2(self):
        row = self.queue()
        gateway_id = self.gateway_id
        start, other_done = Barrier(2), Event()
        exports = []
        def export(payload):
            self.assertFalse(db.session().in_transaction())
            exports.append(payload)
            self.assertTrue(other_done.wait(10))
            return dict(key='isolated/verified', verified=True)
        def run(_):
            with self.app.app_context():
                start.wait(10)
                count = delivery.drain(gateway_id)
                if count == 0:
                    other_done.set()
                db.session.remove()
                return count
        with patch.object(delivery, 'enabled', return_value=True), patch.object(delivery, 'reconcile'), \
                patch.object(delivery, 'reconcile_fleet'), \
                patch('app.services.neoscorpion_learning_vault.export_learning_record', side_effect=export):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(run, range(2))), [0, 1])
        self.assertEqual(len(exports), 1)
        db.session.expire_all()
        self.assertEqual(row.attempts, 1)
        self.assertIsNotNone(row.verified_at)

    def test_duplicate_intent_insert_race_preserves_caller_transactions(self):
        gateway_id, assignment_id = self.gateway.id, self.assignment.id
        barrier = Barrier(2)
        def before(_mapper, _connection, _row):
            barrier.wait(10)
        event.listen(Observation, 'before_insert', before)
        def run(_):
            with self.app.app_context():
                result = capture(db.session.get(Gateway, gateway_id), assignment_id)
                db.session.commit()
                self.assertEqual(db.session.execute(text('SELECT 1')).scalar_one(), 1)
                return result.already_saved
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sorted(pool.map(run, range(2))), [False, True])
        finally:
            event.remove(Observation, 'before_insert', before)
        self.assertEqual(Observation.query.count(), 1)

    def test_additive_receipt_columns_keep_legacy_rows_unverified_and_repeat_safely(self):
        # This older learning fixture predates physical-visit identities. Seed
        # a current-schema visit so unrelated legacy guards remain enforced.
        self.work.visit_number = 1
        self.assignment.active_work_state_id = self.work.id
        db.session.commit()
        row = self.queue()
        row.delivered_at = datetime.utcnow()
        row.vault_key = 'legacy/accepted'
        db.session.commit()
        accepted = row.delivered_at
        tables = ('neoscorpion_spear_observations', 'neoscorpion_spear_fleet_observations',
                  'neosubzero_archive_deliveries')
        db.session.rollback()
        with db.engine.begin() as connection:
            for table in tables:
                connection.execute(text(f'ALTER TABLE {table} DROP COLUMN verified_at'))
        from app.services.schema_sync import sync_database_schema
        sync_database_schema(self.app)
        db.session.commit()
        sync_database_schema(self.app)
        db.session.commit()
        for table in tables:
            column = next(c for c in inspect(db.engine).get_columns(table) if c['name'] == 'verified_at')
            self.assertTrue(column['nullable'])
        db.session.expire_all()
        self.assertEqual(row.delivered_at, accepted)
        self.assertIsNone(row.verified_at)
