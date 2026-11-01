"""Bounded two-worker races in disposable local PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
from unittest.mock import patch
import os
import unittest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from app import create_app
from app.extensions import db
from app.models import NeoSubZeroArchiveDelivery as Delivery, NeoSubZeroDepartureDeiceEvent as Departure, SortDateOperation
from app.services import neosubzero_archive as archive
from tests import test_subzero_archive_reconciliation as cases

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class ArchiveReconciliationPostgresTest(cases.ArchiveReconciliationTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {'localhost', '127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema = 'subzero_discovery_' + uuid4().hex
        self.admin = create_engine(URI, poolclass=NullPool)
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def drop():
            with self.admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
        self.addCleanup(drop)
        def local(config):
            return create_app(type('DiscoveryPostgresConfig', (config,), dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'poolclass': NullPool, 'connect_args': {'options':
                    f'-csearch_path={self.schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
        with patch.object(cases, 'create_app', local):
            super().setUp()

    def test_two_workers_skip_an_existing_gateway_claim_without_duplicate_receipts(self):
        archive.reconcile(self.gateway_id)  # Durable cursor exists before the race.
        self.history(8)
        original, other_done, start = archive.payloads_for_operation, Event(), Barrier(2)
        def payload(op):
            self.assertTrue(other_done.wait(5), 'Other worker must skip the discovery lock')
            return original(op)
        def worker(_):
            with self.app.app_context():
                start.wait(5)
                try:
                    result = archive.reconcile(self.gateway_id)
                    if not result['examined']:
                        other_done.set()
                    return result
                finally:
                    db.session.remove()
        with patch.object(archive, 'payloads_for_operation', side_effect=payload):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(row['examined'] for row in results), [0, 6])
        self.assertEqual(Delivery.query.count(), 6)
        archive.reconcile(self.gateway_id)
        self.assertEqual(Delivery.query.count(), 8)

    def test_two_workers_initializing_missing_cursor_remain_idempotent(self):
        self.history(8)
        start = Barrier(2)
        def worker(_):
            with self.app.app_context():
                start.wait(5)
                try:
                    return archive.reconcile(self.gateway_id)
                finally:
                    db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(worker, range(2)))
        self.recover_all(8)
        self.assertEqual(Delivery.query.count(), 8)

    def test_operational_correction_commits_while_discovery_builds_payload(self):
        ids = self.history(1)
        ready, changed = Event(), Event()
        original = archive.payloads_for_operation
        def correction():
            with self.app.app_context():
                try:
                    self.assertTrue(ready.wait(5))
                    parent = Departure.query.filter_by(sort_date_operation_id=ids[0]).one()
                    parent.reason_for_application = 'Actual historical correction'
                    self.hint(ids[0], event_id=parent.id, tail_number=parent.tail_number)
                    db.session.commit()
                    changed.set()
                finally:
                    db.session.remove()
        def payload(op):
            value = original(op)
            ready.set()
            self.assertTrue(changed.wait(5), 'Operational save must not wait for discovery')
            return value
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(correction)
            with patch.object(archive, 'payloads_for_operation', side_effect=payload):
                result = archive.reconcile(self.gateway_id)
            future.result(5)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(Delivery.query.count(), 0)
        archive.reconcile(self.gateway_id)
        self.assertEqual(Delivery.query.count(), 1)

    def test_no_canonical_fk_locks_are_held_during_later_payload_reconstruction(self):
        ids = self.history(4)
        ready, changed = Event(), Event()
        original = archive.payloads_for_operation
        def correction():
            with self.app.app_context():
                try:
                    self.assertTrue(ready.wait(5))
                    db.session.query(SortDateOperation).filter_by(id=ids[0]).with_for_update().one()
                    parent = Departure.query.filter_by(sort_date_operation_id=ids[0]).one()
                    parent.reason_for_application = 'Live save during historical reconstruction'
                    self.hint(ids[0], event_id=parent.id)
                    db.session.commit()
                    changed.set()
                finally:
                    db.session.remove()
        def payload(op):
            if op.id == ids[1]:
                ready.set()
                self.assertTrue(changed.wait(5), 'Earlier receipt must not hold a canonical FK lock during reconstruction')
            return original(op)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(correction)
            with patch.object(archive, 'payloads_for_operation', side_effect=payload):
                archive.reconcile(self.gateway_id)
            future.result(5)
        self.assertTrue(changed.is_set())
        archive.reconcile(self.gateway_id)
        self.assertEqual(Delivery.query.count(), 4)


if __name__ == '__main__':
    unittest.main()
