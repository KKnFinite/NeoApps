"""Synthetic audit regressions; no provider access, training or production writes."""
import io
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event
from app.extensions import db
from app.models import FrostHistoryTask as Task, FrostModelArtifact, FrostModelRelease
from app.services import archive_transfer_diagnostics as diagnostics
from app.services import frost_auto as auto, frost_departures as departures
from app.services import frost_xchange_links as links, frost_intelligence as intelligence
import app.neonodes.neosubzero  # Initialize the application's route/service import order.
from tests import test_frost_phase13 as fixtures


class TransferTest(unittest.TestCase):
    def test_expected_head_miss_is_separate_from_failed_transfer(self):
        meter = diagnostics._Meter()
        for operation, status in [('HeadObject', 404), ('PutObject', 503), ('GetObject', 404), ('HeadObject', 403)]:
            meter.after_call(SimpleNamespace(status_code=status, headers={'content-length':'0'}),
                model=SimpleNamespace(name=operation))
        self.assertEqual(meter.failures, 3)
        self.assertEqual(meter.http_errors, 4)
        self.assertEqual(meter.head_misses, 1)
        self.assertEqual(meter.transfer_failures, 3)
        meter.requests = 4
        with patch('sys.stderr', new_callable=io.StringIO) as output:
            diagnostics.emit(SimpleNamespace(_neo_archive_transfer_meter=meter), 'frost_archive')
        self.assertIn('expected_head_misses=1', output.getvalue())
        self.assertIn('transfer_failures=3', output.getvalue())

    def test_uploaded_history_joins_dataset_with_live_learning_and_retraining_off(self):
        from tests.test_frost_dataset import DatasetTest
        from scripts import frost_local
        policy = {key:False for key in intelligence.KEYS}
        with patch.object(intelligence, 'read', return_value=policy), \
                patch.object(frost_local, 'fit', side_effect=AssertionError('Ingestion cannot refit models')):
            DatasetTest().test_complete_assembly_reuses_archived_and_local_results_without_weather_or_analysis()


class LearningReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.AutoTest(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.gateway_id = self.case.f.gateway.id

    def measure(self, action):
        statements = []
        def executed(_connection, _cursor, sql, *_args): statements.append(sql.split()[0].upper())
        event.listen(db.engine, 'before_cursor_execute', executed)
        try: result = action()
        finally: event.remove(db.engine, 'before_cursor_execute', executed)
        return result, {key:statements.count(key) for key in ('SELECT','INSERT','UPDATE','DELETE')}

    def test_empty_upload_worker_does_not_claim_or_write(self):
        with patch.object(auto.jobs, 'claim', wraps=auto.jobs.claim) as claimed:
            result, counts = self.measure(lambda: links.drain(self.gateway_id))
        self.assertEqual(result, 0)
        self.assertEqual(sum(counts[k] for k in ('INSERT','UPDATE','DELETE')), 0, counts)
        claimed.assert_not_called()

    def test_empty_departure_worker_does_not_claim_or_rewrite_cursors(self):
        cursor = auto.jobs.task(self.gateway_id, 'departure_control', 'discovery', departures.VERSION)
        cursor.summary = dict(last_mission_id=1000000, discovery_complete=True, last_reconciliation_utc='original')
        db.session.commit()
        with patch.object(auto.jobs, 'claim', wraps=auto.jobs.claim) as claimed:
            result, counts = self.measure(lambda: departures.drain(self.gateway_id))
        self.assertEqual(result, 0)
        self.assertEqual(sum(counts[k] for k in ('INSERT','UPDATE','DELETE')), 0, counts)
        claimed.assert_not_called()
        self.assertEqual(cursor.summary['last_reconciliation_utc'], 'original')

    def test_expired_uncommitted_final_hour_is_missed_without_get_writes(self):
        self.case.t = self.case.t.replace(hour=10, minute=59)
        row = auto.jobs.task(self.gateway_id, 'auto_capture', auto.identity(self.case.t), auto.VERSION)
        row.status = 'retry'; row.retry_at = auto.naive(self.case.t + timedelta(minutes=1))
        row.last_error = 'HRRR memory admission deferred'; db.session.commit()
        self.case.t += timedelta(hours=7)
        status, counts = self.measure(lambda: auto.status(self.gateway_id))
        self.assertEqual(status['missed'], 1)
        self.assertEqual(status['pending'], 0)
        self.assertEqual(status['recent'][0]['state'], 'missed')
        self.assertIn('MISSED', status['recent'][0]['prediction_state'])
        self.assertEqual(sum(counts[k] for k in ('INSERT','UPDATE','DELETE')), 0, counts)
        self.assertEqual(row.status, 'retry')  # Projection only; immutable/source history untouched.

    def test_expired_slot_never_hides_committed_archive_retry_or_live_lease(self):
        row = auto.jobs.task(self.gateway_id, 'auto_capture', auto.identity(self.case.t), auto.VERSION)
        row.status = 'retry'; row.lease_token = 'owner'
        row.retry_at = auto.naive(self.case.t + timedelta(minutes=70)); db.session.commit()
        self.case.t += timedelta(hours=1)
        self.assertTrue(auto.capture_states(row)['in_progress'])
        self.assertNotIn('MISSED', auto.capture_states(row)['prediction_state'])
        row.lease_token = None; row.status = 'archive_retry'
        row.summary = dict(captured_at_utc=auto.stamp(self.case.t-timedelta(hours=1)), scored=8, models=8, staged='committed')
        db.session.commit()
        self.assertEqual(auto.status(self.gateway_id)['pending'], 1)
        self.assertEqual(auto.status(self.gateway_id)['missed'], 0)
        self.assertEqual(auto.capture_states(row)['archive_state'], 'PENDING')

    def test_upload_ingestion_and_matching_ignore_all_off_toggles_and_never_train(self):
        from scripts import frost_prospective as study
        self.assertTrue(all(intelligence.read(self.gateway_id)[key] is False for key in intelligence.KEYS))
        self.case.tick(); self.case.t += timedelta(minutes=30)
        self.case.queue_csv(Reason='F', Type4Used='')
        self.assertEqual(links.drain(self.gateway_id), 1)
        row = Task.query.filter_by(kind='xchange_outcome').one()
        self.assertEqual(row.status, 'matched')
        self.assertEqual(row.summary['classification']['label'], 'LIKELY FROST PRESENT')
        self.assertTrue(row.summary['capture_ids'])
        for reason, cohort, label in [('F','treatment_reason',1), ('CS','treatment_reason',0), ('Pretreat','anticipated_pretreatment',None)]:
            payload = dict(schema_version='xchange-prospective-outcome-v1', id='uploaded', tail='NTEST',
                reason=reason, start_utc=auto.stamp(self.case.t))
            outcome = study.outcome(payload, 'a'*64)
            self.assertEqual(outcome['cohort'], cohort)
            self.assertEqual(outcome['label'], label)
        self.assertEqual(FrostModelArtifact.query.count(), 0)
        self.assertEqual(FrostModelRelease.query.count(), 0)
        self.assertEqual(Task.query.filter_by(kind='research_model').count(), 0)


if __name__ == '__main__': unittest.main()
