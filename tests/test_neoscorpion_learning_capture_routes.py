"""Completed snapshots and delivery receipts share the existing outbox."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from app.extensions import db
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services.neoscorpion_spear_learning_capture import capture_completed_learning_outcome as capture
from app.services import neoscorpion_spear_decisions as delivery
from tests import test_neoscorpion_spear_learning_capture as fixtures


class SpearLearningCaptureRouteSafetyTest(unittest.TestCase):
    setUp = fixtures.SpearLearningCaptureTest.setUp
    tearDown = fixtures.SpearLearningCaptureTest.tearDown

    def queue(self):
        self.gateway_id = self.gateway.id
        capture(self.gateway, self.assignment.id)
        db.session.commit()
        return Observation.query.filter_by(kind='completed').one()

    def drain(self, export):
        with patch.object(delivery, 'enabled', return_value=True), patch.object(delivery, 'reconcile'), \
                patch.object(delivery, 'reconcile_fleet'), \
                patch('app.services.neoscorpion_learning_vault.export_learning_record', side_effect=export):
            return delivery.drain(self.gateway_id)

    def test_queue_and_completion_commit_without_contacting_r2(self):
        with patch('app.services.neoscorpion_learning_vault._r2_client', side_effect=RuntimeError('offline')) as remote:
            row = self.queue()
            remote.assert_not_called()
        self.assertEqual(self.assignment.review_status, 'complete')
        self.assertIsNone(row.delivered_at)
        self.assertIsNone(row.verified_at)
        self.assertEqual(row.payload['assignment']['id'], self.assignment.id)

    def test_atomic_rollback_removes_intent_and_completion_change(self):
        before = self.assignment.completed_at_utc
        self.assignment.completed_at_utc = before + timedelta(seconds=1)
        capture(self.gateway, self.assignment.id)
        self.assertEqual(Observation.query.count(), 1)
        db.session.rollback()
        self.assertEqual(Observation.query.count(), 0)
        self.assertEqual(self.assignment.completed_at_utc, before)

    def test_intent_database_failure_is_atomic_and_does_not_expose_payload(self):
        before = self.assignment.completed_at_utc
        self.assignment.completed_at_utc = before + timedelta(seconds=1)
        failure = OperationalError('private payload statement', {'payload': 'private employee evidence'}, RuntimeError())
        from sqlalchemy import event
        def fail(_mapper, _connection, _row):
            raise failure
        event.listen(Observation, 'before_insert', fail)
        try:
            with self.assertRaises(ValueError) as error:
                capture(self.gateway, self.assignment.id)
            self.assertNotIn('private', str(error.exception))
        finally:
            event.remove(Observation, 'before_insert', fail)
            db.session.rollback()
        self.assertEqual(Observation.query.count(), 0)
        self.assertEqual(self.assignment.completed_at_utc, before)

    def test_original_payload_is_delivered_without_later_mission_state_or_transaction(self):
        row = self.queue()
        frozen = deepcopy(row.payload)
        self.mission.assigned_tail_number = 'DIFFERENT'
        self.assignment.transfer_fuel_gallons = 999
        db.session.commit()
        def export(payload):
            self.assertFalse(db.session().in_transaction())
            self.assertEqual(payload, frozen)
            return dict(key='isolated/verified', verified=True)
        self.assertEqual(self.drain(export), 1)
        self.assertIsNotNone(row.verified_at)

    def test_idempotency_and_corrections_keep_separate_immutable_revisions(self):
        first = self.queue()
        again = capture(self.gateway, self.assignment.id)
        db.session.commit()
        self.assertTrue(again.already_saved)
        self.assertEqual(Observation.query.count(), 1)
        original = deepcopy(first.payload)
        self.assignment.transfer_fuel_gallons = 1300
        capture(self.gateway, self.assignment.id)
        db.session.commit()
        self.assertEqual(Observation.query.count(), 2)
        self.assertEqual(first.payload, original)

    def test_unverified_response_never_completes_and_later_retry_is_idempotent(self):
        row = self.queue()
        self.assertEqual(self.drain(lambda payload: dict(key='accepted-only')), 0)
        self.assertIsNone(row.delivered_at)
        self.assertIsNone(row.verified_at)
        self.assertEqual(row.attempts, 1)
        row.retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self.drain(lambda payload: dict(key='verified', verified=True)), 1)
        self.assertEqual(self.drain(lambda payload: self.fail('Duplicate delivery')), 0)

    def test_crash_after_claim_recovers_frozen_payload_after_lease_expiry(self):
        row = self.queue()
        def crash(_payload):
            raise SystemExit('simulated process termination')
        with self.assertRaises(SystemExit):
            self.drain(crash)
        db.session.remove()
        row = Observation.query.one()
        self.assertEqual(row.attempts, 1)
        self.assertIsNone(row.delivered_at)
        self.assertGreater(row.retry_at, datetime.utcnow())
        row.retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(self.drain(lambda payload: dict(key='recovered', verified=True)), 1)

    def test_replaced_attempt_cannot_write_receipt(self):
        row = self.queue()
        identifier = row.id
        def replaced(_payload):
            with db.engine.begin() as connection:
                connection.execute(text('UPDATE neoscorpion_spear_observations SET attempts=attempts+1 WHERE id=:id'), {'id': identifier})
            return dict(key='stale', verified=True)
        self.assertEqual(self.drain(replaced), 0)
        db.session.expire_all()
        self.assertIsNone(row.verified_at)
        self.assertIsNone(row.vault_key)

    def test_legacy_delivered_receipt_is_not_relabelled_or_mass_reexported(self):
        row = self.queue()
        row.delivered_at = datetime.utcnow()
        row.vault_key = 'legacy/provider-accepted'
        db.session.commit()
        self.assertEqual(self.drain(lambda payload: self.fail('Legacy export')), 0)
        self.assertIsNone(row.verified_at)
