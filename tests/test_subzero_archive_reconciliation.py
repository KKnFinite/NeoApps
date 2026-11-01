"""Synthetic historical evidence only; no provider requests or live records."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import time
import unittest
from unittest.mock import patch
from sqlalchemy import event, inspect, text

from app import create_app
from app.extensions import db
from app.models import (Gateway, SortDateOperation, SortDateMission, NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroAuditEntry as Audit, NeoSubZeroArchiveDelivery as Delivery,
    NeoSubZeroArchiveCursor as Cursor, NeoSubZeroArchiveCheckpoint as Checkpoint)
from app.services import neosubzero_archive as archive
from app.services import neosubzero_archive_reconciliation as discovery


@contextmanager
def measured():
    counts = dict(selects=0, updates=0, inserts=0, receipt_updates=0, receipt_inserts=0, payloads=0)
    def statement(_conn, _cursor, sql, _params, _context, _many):
        lower = sql.lstrip().lower()
        for verb, name in (('select', 'selects'), ('update', 'updates'), ('insert', 'inserts')):
            if lower.startswith(verb):
                counts[name] += 1
                if 'neosubzero_archive_deliveries' in lower and verb != 'select':
                    counts['receipt_' + name] += 1
    engine = db.engine
    event.listen(engine, 'before_cursor_execute', statement)
    original = archive.payloads_for_operation
    def payloads(op):
        counts['payloads'] += 1
        return original(op)
    start = time.perf_counter()
    try:
        with patch.object(archive, 'payloads_for_operation', side_effect=payloads):
            yield counts
    finally:
        counts['seconds'] = round(time.perf_counter() - start, 6)
        event.remove(engine, 'before_cursor_execute', statement)


class ArchiveReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type('ArchiveReconcileConfig', (), dict(SECRET_KEY='isolated', TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False,
            AUTO_BOOTSTRAP_DATABASE=False)))
        self.context = self.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)
        engine = db.engine
        self.addCleanup(engine.dispose)
        def remove():
            db.session.remove()
            db.drop_all()
        self.addCleanup(remove)
        db.create_all()
        self.gateway = Gateway(code='RFD', name='Synthetic RFD')
        db.session.add(self.gateway)
        db.session.commit()
        self.gateway_id = self.gateway.id

    def operation(self, number, *, gateway=None, legacy=False, spray=True):
        gateway = gateway or db.session.get(Gateway, self.gateway_id)
        day = date(2020, 1, 1) + timedelta(days=number)
        op = SortDateOperation(gateway_id=None if legacy else gateway.id, gateway_code=gateway.code,
                               sort_date=day, sort_name='night')
        db.session.add(op)
        db.session.flush()
        if spray:
            at = datetime.combine(day, datetime.min.time())
            mission = SortDateMission(sort_date_operation_id=op.id, gateway_code=gateway.code, sort_date=day,
                sort_name='night', mission_type='departure', mission_source='manual', flight_number=f'TEST-{number}',
                origin=gateway.code, destination='SDF', assigned_tail_number=f'N-TEST-{number}',
                timezone='America/Chicago', planned_datetime_utc=at, planned_datetime_local=at, planned_source='manual')
            db.session.add(mission)
            db.session.flush()
            db.session.add(NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op.id, sort_date_mission_id=mission.id,
                tail_number=mission.assigned_tail_number, status='configured', treatment_plan='one_type_i',
                pass1_started_at_utc=at, pass1_ended_at_utc=at + timedelta(minutes=5), updated_at=at))
        return op

    def history(self, count):
        ids = [self.operation(i).id for i in range(count)]
        db.session.commit()
        return ids

    def hint(self, op_id, **values):
        row = Audit(sort_date_operation_id=op_id, action='historical_correction', **values)
        db.session.add(row)
        return row

    def recover_all(self, count):
        for _ in range(count // discovery.HISTORY_BATCH + 2):
            archive.reconcile(self.gateway_id)

    def test_no_evidence_and_repeated_idle_have_no_writes(self):
        archive.reconcile(self.gateway_id)
        with measured() as counts:
            result = archive.reconcile(self.gateway_id)
        self.assertEqual(result['reconstructed'], 0)
        self.assertEqual((counts['inserts'], counts['updates']), (0, 0))
        self.assertEqual(Delivery.query.count(), 0)

    def test_one_operation_and_unchanged_verified_receipt_are_not_rebuilt(self):
        self.history(1)
        self.assertEqual(archive.reconcile(self.gateway_id)['reconstructed'], 1)
        row = Delivery.query.one()
        row.delivered_checksum = row.content_checksum
        row.delivered_at = row.verified_at = datetime.utcnow()
        row.vault_key, row.learning_key = 'fixture/spray', 'fixture/context'
        db.session.commit()
        with measured() as counts:
            result = archive.reconcile(self.gateway_id)
        self.assertEqual(result['reconstructed'], 0)
        self.assertEqual(counts['payloads'], 0)
        self.assertEqual((counts['updates'], counts['inserts']), (0, 0))
        self.assertIsNotNone(row.verified_at)

    def test_hundreds_of_operations_are_bounded_and_eventually_all_recovered(self):
        ids = self.history(240)
        for _ in range(62):
            with measured() as counts:
                result = archive.reconcile(self.gateway_id)
            self.assertLessEqual(result['examined'], discovery.MAX_OPERATIONS)
            self.assertLessEqual(counts['payloads'], discovery.MAX_OPERATIONS)
        self.assertEqual({row.operation_id for row in Delivery.query.all()}, set(ids))
        with measured() as idle:
            archive.reconcile(self.gateway_id)
        self.assertEqual(idle['payloads'], 0)
        self.assertEqual((idle['receipt_updates'], idle['receipt_inserts']), (0, 0))

    def test_gateway_and_legacy_code_ownership_are_preserved(self):
        other = Gateway(code='MDW', name='Other synthetic gateway')
        db.session.add(other)
        db.session.flush()
        own = self.operation(0)
        legacy = self.operation(1, legacy=True)
        legacy.gateway_code = 'rfd'
        foreign = self.operation(2, gateway=other)
        misleading = self.operation(3, gateway=other)
        misleading.gateway_code = 'RFD'  # Explicit FK remains authoritative.
        db.session.commit()
        own_ids, foreign_ids, other_id = {own.id, legacy.id}, {foreign.id, misleading.id}, other.id
        archive.reconcile(self.gateway_id)
        self.assertEqual({r.operation_id for r in Delivery.query.all()}, own_ids)
        archive.reconcile(other_id)
        self.assertEqual({r.operation_id for r in Delivery.query.filter_by(gateway_id=other_id)}, foreign_ids)

    def test_cursor_restart_wrap_and_new_operations_do_not_starve_old_work(self):
        ids = self.history(12)
        archive.reconcile(self.gateway_id)
        self.assertEqual(db.session.get(Cursor, self.gateway_id).operation_after, ids[3])
        db.session.remove()
        archive.reconcile(self.gateway_id)
        self.assertEqual(db.session.get(Cursor, self.gateway_id).operation_after, ids[7])
        for i in range(3):
            new = self.operation(100 + i)
            self.hint(new.id)
            db.session.commit()
            archive.reconcile(self.gateway_id)
        self.assertTrue(set(ids).issubset({r.operation_id for r in Delivery.query.all()}))
        self.assertLessEqual(Delivery.query.count(), 15)

    def test_old_correction_and_new_sort_are_prioritized_during_sweep(self):
        ids = self.history(20)
        archive.reconcile(self.gateway_id)
        row = Delivery.query.filter_by(operation_id=ids[0]).one()
        old_checksum = row.content_checksum
        parent = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_operation_id=ids[0]).one()
        parent.reason_for_application = 'Audited historical correction'
        self.hint(ids[0], tail_number=parent.tail_number, event_id=parent.id)
        new = self.operation(100)
        self.hint(new.id)
        db.session.commit()
        new_id = new.id
        archive.reconcile(self.gateway_id)
        self.assertNotEqual(row.content_checksum, old_checksum)
        self.assertIsNotNone(Delivery.query.filter_by(operation_id=new_id).first())

    def test_safety_sweep_finds_missing_signal_and_missing_receipt(self):
        ids = self.history(12)
        self.recover_all(12)
        parent = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_operation_id=ids[0]).one()
        receipt = Delivery.query.filter_by(operation_id=ids[0]).one()
        previous = receipt.content_checksum
        parent.reason_for_application = 'Managed source revision with a missed audit/enqueue signal'
        lost = Delivery.query.filter_by(operation_id=ids[1]).one()
        db.session.delete(lost)
        db.session.commit()
        self.recover_all(12)
        self.assertNotEqual(receipt.content_checksum, previous)
        self.assertEqual(Delivery.query.count(), 12)

    def test_failed_old_operation_cannot_starve_siblings_or_cursor(self):
        ids = self.history(12)
        original = archive.payloads_for_operation
        def fail(op):
            if op.id == ids[0]:
                raise RuntimeError('Isolated reconstruction failure')
            return original(op)
        with patch.object(archive, 'payloads_for_operation', side_effect=fail):
            self.recover_all(12)
        checkpoint = db.session.get(Checkpoint, ids[0])
        self.assertTrue(checkpoint.last_error)
        self.assertGreater(checkpoint.retry_at, datetime.utcnow())
        self.assertEqual(Delivery.query.count(), 11)
        checkpoint.retry_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        archive.reconcile(self.gateway_id)
        self.assertEqual(Delivery.query.count(), 12)
        self.assertIsNone(checkpoint.last_error)

    def test_crash_before_commit_rolls_back_cursor_and_receipt_then_recovers(self):
        self.history(8)
        with patch.object(db.session, 'commit', side_effect=RuntimeError('Crash before commit')):
            with self.assertRaises(RuntimeError):
                archive.reconcile(self.gateway_id)
        db.session.rollback()
        self.assertEqual(Cursor.query.count(), 0)
        self.assertEqual(Checkpoint.query.count(), 0)
        self.assertEqual(Delivery.query.count(), 0)
        archive.reconcile(self.gateway_id)
        self.assertEqual(Delivery.query.count(), discovery.HISTORY_BATCH + discovery.RECENT_BATCH)

    def test_pending_delivery_retries_when_operation_is_not_in_discovery_batch(self):
        ids = self.history(16)
        archive.reconcile(self.gateway_id)
        target = Delivery.query.filter_by(operation_id=ids[0]).one()
        target.retry_at = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        # The next real discovery batch is later history + newest operations;
        # this old receipt is delivered independently of that selection.
        with patch(
                'app.services.neoscorpion_learning_vault.export_subzero_pair',
                return_value=(dict(key='fixture/spray', verified=True), dict(key='fixture/context', verified=True))):
            self.assertEqual(archive.drain(self.gateway_id, limit=1), 1)
        self.assertEqual(target.content_checksum, target.delivered_checksum)
        self.assertIsNotNone(target.verified_at)

    def test_correction_during_upload_is_fenced_before_discovery_runs(self):
        ids = self.history(1)
        archive.reconcile(self.gateway_id)
        receipt = Delivery.query.one()
        parent = NeoSubZeroDepartureDeiceEvent.query.one()
        def correction(payload):
            self.assertFalse(db.session().in_transaction())
            parent.reason_for_application = 'Correction committed while provider was unavailable'
            self.hint(ids[0], event_id=parent.id, tail_number=parent.tail_number)
            db.session.commit()
            return dict(key='fixture/old-spray', verified=True), dict(key='fixture/old-context', verified=True)
        with patch('app.services.neoscorpion_learning_vault.export_subzero_pair', side_effect=correction):
            self.assertEqual(archive.drain(self.gateway_id), 0)
        self.assertIsNone(receipt.verified_at)
        self.assertIsNone(receipt.delivered_checksum)
        with patch('app.services.neoscorpion_learning_vault.export_subzero_pair', return_value=(
                dict(key='fixture/new-spray', verified=True), dict(key='fixture/new-context', verified=True))):
            self.assertEqual(archive.drain(self.gateway_id), 1)
        self.assertEqual(receipt.content_checksum, receipt.delivered_checksum)

    def test_receipt_attempt_fence_never_resets_on_correction(self):
        self.history(1)
        archive.reconcile(self.gateway_id)
        receipt = Delivery.query.one()
        receipt.attempts = 7
        parent = NeoSubZeroDepartureDeiceEvent.query.one()
        parent.reason_for_application = 'First correction'
        db.session.commit()
        archive.reconcile(self.gateway_id)
        self.assertEqual(receipt.attempts, 8)
        parent.reason_for_application = 'Second correction'
        db.session.commit()
        archive.reconcile(self.gateway_id)
        self.assertEqual(receipt.attempts, 9)

    def test_lower_audit_id_commit_during_delivery_is_also_fenced(self):
        ids = self.history(1)
        self.hint(ids[0], id=100)
        db.session.commit()
        archive.reconcile(self.gateway_id)
        def correction(_payload):
            self.hint(ids[0], id=50)
            db.session.commit()
            return dict(key='fixture/old', verified=True), dict(key='fixture/context', verified=True)
        with patch('app.services.neoscorpion_learning_vault.export_subzero_pair', side_effect=correction):
            self.assertEqual(archive.drain(self.gateway_id), 0)
        self.assertIsNone(Delivery.query.one().verified_at)

    def test_additive_bootstrap_retains_evidence_and_legacy_verification_state(self):
        from app.services.schema_sync import sync_database_schema
        self.history(1)
        archive.reconcile(self.gateway_id)
        receipt = Delivery.query.one()
        digest = receipt.content_checksum
        receipt.delivered_checksum = digest
        receipt.delivered_at = datetime.utcnow()
        db.session.commit()
        db.session.remove()
        with db.engine.begin() as connection:
            connection.execute(text('DROP TABLE neosubzero_archive_checkpoints'))
            connection.execute(text('DROP TABLE neosubzero_archive_cursors'))
        for _ in range(2):
            sync_database_schema(self.app)
            db.session.commit()
        self.assertTrue(inspect(db.engine).has_table('neosubzero_archive_cursors'))
        self.assertTrue(inspect(db.engine).has_table('neosubzero_archive_checkpoints'))
        self.assertIsNone(Delivery.query.one().verified_at)
        self.assertEqual(Delivery.query.one().content_checksum, digest)
        with measured() as counts:
            archive.reconcile(self.gateway_id)
        self.assertEqual((counts['receipt_updates'], counts['receipt_inserts']), (0, 0))


if __name__ == '__main__':
    unittest.main()
