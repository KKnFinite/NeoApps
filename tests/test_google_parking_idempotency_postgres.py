"""Parking synchronization races, using disposable loopback PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import create_app
from app.extensions import db
from app.models import SortDateOperation, SortDateParkingAssignment
from app.services import parking_plan
from app.services.google_motherbrain_parking import apply_google_motherbrain_parking
from app.services.parking_plan_collaboration import (
    assignment_snapshot, validate_parking_move_snapshot, ParkingStateConflict,
)
from tests import test_google_motherbrain_parking as contracts
from tests import test_google_parking_idempotency as idempotency
from tests import test_google_motherbrain_live_poll_execution as polls

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


def postgres_setup(test, module, setup):
    url = make_url(URI)
    if url.host not in ('localhost', '127.0.0.1') or not url.database.startswith('neostaffing_test_'):
        raise RuntimeError('Refusing non-disposable PostgreSQL')
    test.schema = 'google_parking_' + uuid4().hex
    admin = create_engine(URI, poolclass=NullPool)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{test.schema}"'))
    def cleanup():
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{test.schema}" CASCADE'))
        admin.dispose()
    test.addCleanup(cleanup)
    def local(config):
        return create_app(type('ParkingPostgresConfig', (config,), dict(
            SQLALCHEMY_DATABASE_URI=URI, SQLALCHEMY_ENGINE_OPTIONS={
                'poolclass': NullPool, 'connect_args': {'options':
                    f'-csearch_path={test.schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
    with patch.object(module, 'create_app', local):
        setup(test)


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class GoogleParkingPostgresTest(idempotency.GoogleParkingIdempotencyTest):
    def setUp(self):
        postgres_setup(self, contracts, idempotency.GoogleParkingIdempotencyTest.setUp)

    def test_two_unchanged_poll_workers_create_no_duplicates_or_updates(self):
        self._add_tail('N401UP')
        self._park('N401UP', 'A01')
        db.session.commit()
        before, operation_id = self.snapshot(), self.operation.id
        ready = Barrier(2)
        def worker(_):
            with self.app.app_context():
                try:
                    operation = db.session.get(SortDateOperation, operation_id)
                    ready.wait(5)
                    result = apply_google_motherbrain_parking(operation, 'N401UP', 'A1')
                    db.session.commit()
                    return result['status']
                finally:
                    db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(worker, range(2))), ['applied', 'applied'])
        db.session.expire_all()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(SortDateParkingAssignment.query.count(), 1)

    def test_local_edit_commits_after_google_read_without_being_overwritten(self):
        self._add_tail('N401UP')
        row = self._park('N401UP', 'A01')
        db.session.commit()
        assignment_id, engine = row.id, db.engine
        read, edited = Event(), Event()
        original = parking_plan.ParkingPlanOperationalStateBundle.load
        def loaded(*args, **kwargs):
            bundle = original(*args, **kwargs)
            read.set()
            self.assertTrue(edited.wait(5))
            return bundle
        def local_edit():
            self.assertTrue(read.wait(5))
            # Independent connection commits while the Google transaction holds
            # its original assignment object. A no-op must not flush stale data.
            with engine.begin() as connection:
                connection.execute(update(SortDateParkingAssignment).where(
                    SortDateParkingAssignment.id == assignment_id).values(
                        note='Concurrent authorized edit', assigned_at='2026-08-07 23:00:00'))
            edited.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(local_edit)
            with patch.object(parking_plan.ParkingPlanOperationalStateBundle, 'load', loaded):
                apply_google_motherbrain_parking(self.operation, 'N401UP', 'A1')
                db.session.commit()
            future.result(timeout=5)
        db.session.expire_all()
        self.assertEqual(self._assignment('N401UP').note, 'Concurrent authorized edit')
        self.assertEqual(str(self._assignment('N401UP').assigned_at), '2026-08-07 23:00:00')

    def test_concurrent_initial_placement_keeps_one_assignment_and_sessions_usable(self):
        self._add_tail('N401UP')
        db.session.commit()
        operation_id, ready, worker_state = self.operation.id, Barrier(2), local()
        original = parking_plan.ParkingPlanOperationalStateBundle.load
        def loaded(*args, **kwargs):
            bundle = original(*args, **kwargs)
            if not getattr(worker_state, 'loaded', False):
                worker_state.loaded = True
                ready.wait(5)  # Both observe the same initially missing assignment.
            return bundle
        def worker(_):
            with self.app.app_context():
                try:
                    operation = db.session.get(SortDateOperation, operation_id)
                    result = apply_google_motherbrain_parking(operation, 'N401UP', 'A1')
                    db.session.commit()
                    self.assertEqual(SortDateParkingAssignment.query.count(), 1)
                    return result['status']
                finally:
                    db.session.remove()
        with patch.object(parking_plan.ParkingPlanOperationalStateBundle, 'load', loaded):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(results), ['applied', 'skipped'])
        self.assertEqual(self._assignment('N401UP').position_code, 'A01')
        self.assertEqual(SortDateParkingAssignment.query.count(), 1)

    def test_real_google_move_invalidates_stale_manual_snapshot_safely(self):
        self._add_tail('N401UP')
        row = self._park('N401UP', 'A01')
        db.session.commit()
        before = assignment_snapshot(row)
        self._apply('N401UP', 'B1')
        db.session.commit()
        with self.assertRaises(ParkingStateConflict):
            validate_parking_move_snapshot(self.operation, tail_number='N401UP',
                ramp_code='C', position_code='C01', lane_number=1, expected={
                    'enabled': True, 'source_location': before['location'],
                    'source_version': before['version'], 'target_tail': '', 'target_version': 'empty'})
        db.session.rollback()
        self.assertEqual(self._assignment('N401UP').position_code, 'B01')

    def test_outer_poll_rollback_restores_changed_parking_and_occupant(self):
        for tail, position in (('N401UP', 'A01'), ('N402UP', 'B01')):
            self._add_tail(tail)
            self._park(tail, position)
        db.session.commit()
        before = self.snapshot()
        self._apply('N401UP', 'B1')
        db.session.rollback()  # Lease loss/failing poll owns the outer rollback.
        self.assertEqual(self.snapshot(), before)


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class GoogleParkingPollPostgresTest(unittest.TestCase):
    NOW = polls.GoogleMotherBrainLivePollExecutionTest.NOW
    _enable = polls.GoogleMotherBrainLivePollExecutionTest._enable
    _ensure_operation = polls.GoogleMotherBrainLivePollExecutionTest._ensure_operation
    _inbound = staticmethod(polls.GoogleMotherBrainLivePollExecutionTest._inbound)
    _outbound = staticmethod(polls.GoogleMotherBrainLivePollExecutionTest._outbound)
    tearDown = polls.GoogleMotherBrainLivePollExecutionTest.tearDown
    test_complete_polls_and_real_move = polls.GoogleMotherBrainLivePollExecutionTest.test_ten_identical_complete_polls_do_not_update_parking_including_hot_and_spare
    test_lease_loss_restores_parking = polls.GoogleMotherBrainLivePollExecutionTest.test_lease_loss_rolls_back_real_parking_application

    def setUp(self):
        postgres_setup(self, polls, polls.GoogleMotherBrainLivePollExecutionTest.setUp)
