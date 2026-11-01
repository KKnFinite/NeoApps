"""Real SQL/services under deterministic clocks; no provider or production I/O."""
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import heapq
import unittest
from unittest.mock import patch

from flask import g
from sqlalchemy import event, select

from app.extensions import db
from app.models import (FrostHistoryTask as Task, FrostDocument, User, Gateway,
    SortDateOperation, NeoSubZeroArchiveDelivery as Delivery)
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup
from app.services import background_deadlines as scheduling
from app.services import neostaffing_employee_recovery as recovery, neostaffing_employee_recovery_worker as expiry
from app.services import frost_auto as auto, frost_documents as documents, frost_departures as departures
from app.services import neoscorpion_spear_decisions as spear, neosubzero_archive as subzero
from app.services import neosubzero_archive_reconciliation as discovery
from tests import test_google_motherbrain_parking as fixtures
from tests.test_frost_vault import MemoryR2, sample


class DeadlineTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GoogleMotherBrainParkingTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app = self.fixture.app
        self.app.config.update(NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED=False,
            NEOSCORPION_LEARNING_VAULT_PROVIDER='r2', NEOSCORPION_LEARNING_VAULT_BUCKET='isolated',
            NEOSCORPION_LEARNING_VAULT_ENDPOINT='https://isolated.invalid', NEOSCORPION_LEARNING_VAULT_ACCESS_KEY_ID='x',
            NEOSCORPION_LEARNING_VAULT_SECRET_ACCESS_KEY='x', FROST_AUTO_COLLECTION=True)
        self.gateway_id = self.fixture.gateway.id
        db.session.delete(self.fixture.operation)
        self.actor = User(username='deadline-fixture', password_hash='no-login-fixture')
        db.session.add(self.actor)
        db.session.commit()
        self.actor_id = self.actor.id
        self.at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
        self.store, self.captures = MemoryR2(), []
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(scheduling, 'utcnow', side_effect=lambda: self.naive()))
        stack.enter_context(patch.object(auto, 'now', side_effect=lambda: self.at))
        stack.enter_context(patch.object(recovery, 'now_utc', side_effect=lambda: self.naive()))
        fixture = self
        class Clock(datetime):
            @classmethod
            def utcnow(cls): return fixture.naive()
        for module in (spear, subzero, documents, departures, discovery):
            stack.enter_context(patch.object(module, 'datetime', Clock))
        stack.enter_context(patch('app.services.neoscorpion_learning_vault._r2_client', return_value=self.store))
        stack.enter_context(patch('app.services.neoscorpion_learning_vault._require_r2_config', return_value={'bucket':'isolated'}))
        from app.services.frost_weather import STATION
        def acquire():
            self.captures.append(self.at)
            return dict(station=STATION, cutoff_at_utc=self.at.isoformat(), observations=[], sources=[],
                native_forecasts=[], nws_forecasts=[])
        stack.enter_context(patch.object(auto, 'acquire', side_effect=acquire))
        scheduling.install_commit_signals()
        self.timer_queue, self.timer_sequence = [], 0
        self.worker = None
        self.addCleanup(self.app.extensions.pop, scheduling.EXTENSION, None)

    def naive(self): return self.at.replace(tzinfo=None)

    def timer(self, seconds, callback):
        fixture = self
        class Timer:
            canceled = False
            daemon = True
            def start(self):
                fixture.timer_sequence += 1
                heapq.heappush(fixture.timer_queue, (fixture.at+timedelta(seconds=seconds), fixture.timer_sequence, self))
            def cancel(self): self.canceled = True
            def fire(self): callback()
        return Timer()

    def start_worker(self):
        if self.worker is not None:
            self.worker.timer.cancel()
        self.worker = scheduling.ArchiveDeadlines(self.app, self.timer)
        self.app.extensions[scheduling.EXTENSION] = self.worker
        return self.worker

    def seed_capture(self):
        self.assertEqual(auto.tick(self.gateway_id), 1)
        self.captures.clear()
        db.session.remove()

    def backup(self, deadline):
        row = Backup(person_id=90001, actor_id=self.actor_id, created_at=deadline-timedelta(hours=24),
            expires_at=deadline, state='deleted', encrypted_payload=recovery.seal(dict(files=[])))
        db.session.add(row)
        db.session.commit()
        return row.id

    def upload(self, identifier):
        document, created = documents.stage(db.session.get(Gateway,self.gateway_id), db.session.get(User, self.actor_id),
            f'evidence-{identifier}.csv', sample(RecordID=str(identifier)))
        self.assertTrue(created)
        document.imported_at_utc = self.naive()
        db.session.commit()
        return document.id

    def simulate(self, *, seconds=3600, actions=None, restart=None):
        start = self.at
        self.start_worker()
        end, next_recovery = start+timedelta(seconds=seconds), start+timedelta(seconds=10)
        recovery_clock = next_recovery
        actions = sorted((start+timedelta(seconds=t), action) for t, action in (actions or {}).items())
        if restart is not None:
            actions.append((start+timedelta(seconds=restart), self.start_worker)); actions.sort(key=lambda item:item[0])
        counts, moments = Counter(), []
        engine = db.engine
        def sql(conn, cursor, statement, parameters, context, many):
            counts[statement.split(None, 1)[0].upper()] += 1
            moments.append((self.at-start).total_seconds())
        def checkout(*args): counts['connections'] += 1
        def transaction(*args): counts['transactions'] += 1
        def commit(*args): counts['commits'] += 1
        hooks = [('before_cursor_execute',sql),('checkout',checkout),('begin',transaction),('commit',commit)]
        for name, callback in hooks: event.listen(engine, name, callback)
        try:
            while True:
                while self.timer_queue and self.timer_queue[0][2].canceled: heapq.heappop(self.timer_queue)
                timer_at = self.timer_queue[0][0] if self.timer_queue else end
                action_at = actions[0][0] if actions else end
                due = min(timer_at, recovery_clock, action_at)
                if due >= end: break
                self.at = due
                if actions and action_at == due:
                    actions.pop(0)[1]()
                if timer_at == due:
                    _, _, timer = heapq.heappop(self.timer_queue)
                    before = len(moments)
                    counts['clock_wakes'] += 1
                    timer.fire()
                    counts['database_wakes'] += len(moments) > before
                if recovery_clock == due:
                    counts['clock_wakes'] += 1
                    if self.at >= next_recovery:
                        g.background_deadline_work = True
                        try:
                            next_recovery = expiry.recovery_cycle().replace(tzinfo=timezone.utc)
                        finally:
                            g.background_deadline_work = False
                            db.session.remove()
                        counts['database_wakes'] += 1
                    recovery_clock = min(next_recovery, self.at+timedelta(seconds=60))
        finally:
            for name, callback in hooks: event.remove(engine, name, callback)
            self.at = end
        timeline = [0, *sorted(moments), seconds]
        return dict(sql=dict(counts), longest_database_free_seconds=max(b-a for a,b in zip(timeline,timeline[1:])),
            captures=[(at-start).total_seconds() for at in self.captures])

    def test_idle_hour_has_real_database_free_intervals_over_suspend_threshold(self):
        self.seed_capture()
        result = self.simulate()
        self.assertEqual(result['captures'], [])
        self.assertGreaterEqual(result['longest_database_free_seconds'], 600)
        self.assertLess(result['sql']['SELECT'], 250)
        self.assertEqual(result['sql'].get('UPDATE',0), 0)
        self.assertEqual(result['sql'].get('INSERT',0), 0)
        self.assertEqual(result['sql'].get('commits',0), 0)

    def test_hour_boundary_capture_is_exact_and_not_dependent_on_sort_or_browser(self):
        self.at += timedelta(minutes=30)
        self.seed_capture()
        result = self.simulate()
        self.assertEqual(result['captures'], [1800])
        self.assertGreater(result['longest_database_free_seconds'], 300)
        self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),2)

    def test_recovery_24_hour_deadline_purges_at_exact_boundary(self):
        self.seed_capture()
        deadline = self.naive()+timedelta(seconds=1800)
        identifier = self.backup(deadline)
        result = self.simulate()
        row = db.session.get(Backup,identifier)
        self.assertEqual(row.purged_at,deadline)
        self.assertIsNone(row.encrypted_payload)
        self.assertGreater(result['longest_database_free_seconds'],300)

    def test_future_recovery_uses_safety_scan_without_early_purge(self):
        self.seed_capture()
        identifier = self.backup(self.naive()+timedelta(hours=24))
        result = self.simulate()
        self.assertEqual(db.session.get(Backup,identifier).state,'deleted')
        self.assertEqual(result['sql'].get('UPDATE',0),0)

    def test_new_uploads_wake_promptly_and_deliver_verified_originals(self):
        self.seed_capture()
        self.simulate(actions={900:lambda:self.upload(101),2700:lambda:self.upload(102)})
        rows = FrostDocument.query.all()
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertIsNotNone(row.archived_at_utc)
            self.assertLessEqual((row.archived_at_utc-row.imported_at_utc).total_seconds(),30)

    def test_missed_commit_notification_recovers_from_durable_document(self):
        self.seed_capture()
        def other_process():
            with patch.object(scheduling,'notify_archive_worker'):
                self.upload(103)
        self.simulate(actions={900:other_process},restart=1000)
        row=FrostDocument.query.one()
        self.assertIsNotNone(row.archived_at_utc)
        self.assertLessEqual((row.archived_at_utc-row.imported_at_utc).total_seconds(),600)

    def test_staged_forecast_archive_retry_never_reacquires_source(self):
        start=self.at
        original=self.store.get_object
        failed=[False]
        def read(**kwargs):
            if not failed[0]:
                failed[0]=True
                raise OSError('isolated temporary archive failure')
            return original(**kwargs)
        with patch.object(self.store,'get_object',side_effect=read):
            self.simulate(seconds=600)
        self.assertEqual(len(self.captures),1)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.status,'ready')
        self.assertEqual(auto.clock.instant(row.summary['captured_at_utc']),start+timedelta(seconds=10))

    def test_commit_hints_only_follow_outer_commit_not_rollback_or_reads(self):
        self.start_worker()
        with patch.object(self.worker,'notify') as notify:
            documents.stage(self.fixture.gateway,db.session.get(User,self.actor_id),'rollback.csv',sample())
            db.session.rollback()
            notify.assert_not_called()
            documents.stage(self.fixture.gateway,db.session.get(User,self.actor_id),'commit.csv',sample())
            with db.session.begin_nested(): pass
            notify.assert_not_called()
            db.session.commit()
            notify.assert_called_once()
            notify.reset_mock()
            db.session.scalar(select(User.id).limit(1));db.session.commit()
            notify.assert_not_called()

    def test_cancelled_timer_is_fenced_and_only_one_timer_remains_live(self):
        worker=self.start_worker()
        generation=worker.generation
        worker.notify()
        with patch.object(scheduling,'plan') as planner:
            worker.wake(generation)
            planner.assert_not_called()
        self.assertEqual(sum(not timer.canceled for _,_,timer in self.timer_queue),1)

    def test_background_outage_keeps_bounded_retry_and_releases_session(self):
        db.session.remove()
        worker=self.start_worker()
        self.at+=timedelta(seconds=10)
        with patch.object(scheduling,'plan',side_effect=OSError('isolated database outage')):
            worker.wake(worker.generation)
        self.assertEqual(worker.deadline,self.naive()+timedelta(seconds=30))
        self.assertFalse(db.session.registry.has())

    def test_three_hour_otherwise_idle_period_preserves_every_hour_boundary(self):
        self.at+=timedelta(minutes=30)
        self.seed_capture()
        result=self.simulate(seconds=10800)
        self.assertEqual(result['captures'],[1800,5400,9000])
        self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),4)
        self.assertGreaterEqual(result['longest_database_free_seconds'],600)

    def test_subzero_delivery_uses_receipt_retry_without_unnecessary_history_scan(self):
        self.seed_capture()
        op=SortDateOperation(gateway_id=self.gateway_id,gateway_code='RFD',sort_name='night',sort_date=self.at.date())
        db.session.add(op);db.session.flush()
        due=self.naive()+timedelta(seconds=137)
        db.session.add(Delivery(gateway_id=self.gateway_id,operation_id=op.id,source_id='synthetic-scheduling-only',
            content_checksum='a'*64,retry_at=due))
        db.session.commit()
        kinds,deadline=scheduling.plan()
        self.assertNotIn('subzero_delivery',kinds.get(self.gateway_id,set()))
        self.assertEqual(deadline,due)
        with patch.object(subzero,'drain') as drain:
            scheduling.perform(self.gateway_id,{'subzero_delivery'})
        drain.assert_called_once_with(self.gateway_id,reconcile_first=False)

    def test_busy_shared_lease_retains_minute_retry_for_unstarted_current_slot(self):
        batch=Task(gateway_id=self.gateway_id,kind='worker',identity='memory-budget',version='frost-worker-v1',
            lease_token='other-process',retry_at=self.naive()+timedelta(minutes=15),summary={})
        db.session.add(batch);db.session.commit()
        kinds,deadline=scheduling.plan()
        self.assertNotIn('auto',kinds.get(self.gateway_id,set()))
        self.assertEqual(deadline,self.naive()+timedelta(seconds=60))
        batch.lease_token=None;db.session.commit()
        kinds,_=scheduling.plan()
        self.assertIn('auto',kinds[self.gateway_id])

    def test_backward_clock_step_recomputes_instead_of_waiting_for_old_wall_time(self):
        worker=self.start_worker()
        self.at-=timedelta(hours=1)
        with patch.object(scheduling,'plan',return_value=({},self.naive()+timedelta(seconds=600))) as planner:
            worker.wake(worker.generation)
        self.assertEqual(planner.call_count,1)
        self.assertEqual(worker.deadline,scheduling.safety_deadline(self.naive()))

    def test_forward_clock_gap_is_recorded_without_replaying_old_predictions(self):
        self.seed_capture()
        worker=self.start_worker()
        self.at+=timedelta(hours=3)
        worker.wake(worker.generation)
        self.assertEqual(len(self.captures),1)
        self.assertEqual(self.captures[0],self.at)
        self.assertEqual(Task.query.filter_by(kind='auto_gap',status='missed').one().summary['intervals'],2)
        self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),2)

    def test_page_reads_and_wake_notifications_never_acquire_a_database_connection(self):
        worker=self.start_worker()
        self.app.testing=False
        try:
            with patch.object(worker,'notify') as notify, patch.object(db.session,'execute') as query:
                with self.app.test_request_context('/neoscorpion/fuel-dispatch',method='GET'):
                    spear.schedule_delivery(self.app,self.gateway_id)
                notify.assert_not_called()
                with self.app.test_request_context('/neoscorpion/fuel-dispatch',method='POST'):
                    spear.schedule_delivery(self.app,self.gateway_id)
                notify.assert_called_once()
                query.assert_not_called()
        finally:self.app.testing=True

    def test_recovery_created_by_another_process_is_found_before_24_hour_expiry(self):
        self.seed_capture()
        ids=[]
        def other_process():
            with patch.object(expiry,'notify_recovery_worker'):
                ids.append(self.backup(self.naive()+timedelta(hours=24)))
        result=self.simulate(actions={305:other_process})
        self.assertEqual(db.session.get(Backup,ids[0]).state,'deleted')
        self.assertGreater(result['longest_database_free_seconds'],300)

    def test_commit_hints_coalesce_without_postponing_earlier_capture_deadline(self):
        worker=self.start_worker()
        worker.last_started=self.naive()
        worker.deadline=self.naive()+timedelta(seconds=5)
        worker.notify()
        self.assertEqual(worker.deadline,self.naive()+timedelta(seconds=5))
        worker.deadline=self.naive()+timedelta(seconds=600)
        worker.notify()
        self.assertEqual(worker.deadline,self.naive()+timedelta(seconds=30))

    def test_failed_commit_notification_cannot_change_successful_canonical_commit(self):
        worker=self.start_worker()
        with patch.object(worker,'notify',side_effect=RuntimeError('isolated timer unavailable')):
            identifier=self.upload(104)
        self.assertIsNotNone(db.session.get(FrostDocument,identifier))
        self.assertIsNotNone(db.session.get(FrostDocument,identifier).staged_bytes)

    def test_timer_allocation_failure_preserves_existing_safety_callback(self):
        worker=self.start_worker()
        previous,generation=worker.timer,worker.generation
        def unavailable(*args):
            from unittest.mock import Mock
            return Mock(start=Mock(side_effect=RuntimeError('isolated allocation failure')))
        with patch.object(worker,'timer_factory',unavailable):
            worker.notify()
        self.assertIs(worker.timer,previous)
        self.assertEqual(worker.generation,generation)
        self.assertFalse(previous.canceled)
        self.assertTrue(worker.arm_failed)

    def test_failed_callback_rearm_is_recoverable_without_a_new_worker(self):
        from unittest.mock import Mock
        self.seed_capture()
        worker=self.start_worker()
        self.at+=timedelta(seconds=10)
        with patch.object(worker,'timer_factory',side_effect=RuntimeError('isolated allocation failure')):
            worker.wake(worker.generation)
        self.assertTrue(worker.arm_failed)
        worker.timer=Mock(is_alive=Mock(return_value=False))
        self.assertTrue(scheduling.repair_failed_timer(self.app))
        self.assertFalse(worker.arm_failed)

    def test_existing_clock_repairs_only_a_failed_finished_timer_without_sql(self):
        from unittest.mock import Mock
        worker=self.start_worker()
        with patch.object(worker,'arm') as arm:
            self.assertFalse(scheduling.repair_failed_timer(self.app))
            arm.assert_not_called()
        worker.timer.cancel()
        worker.timer=Mock(is_alive=Mock(return_value=False))
        worker.arm_failed=True
        with patch.object(db.session,'execute') as query:
            self.assertTrue(scheduling.repair_failed_timer(self.app))
            query.assert_not_called()
        self.assertFalse(worker.arm_failed)

    def test_dst_fall_back_does_not_duplicate_utc_hour_slots(self):
        self.at=datetime(2027,11,7,5,30,tzinfo=timezone.utc)
        self.seed_capture()
        result=self.simulate(seconds=10800)
        self.assertEqual(result['captures'],[1800,5400,9000])
        self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),4)

    def test_platform_health_checks_remain_database_free(self):
        statements=[]
        engine=db.engine
        def captured(*args):statements.append(True)
        event.listen(engine,'before_cursor_execute',captured)
        try:
            self.assertEqual(self.app.test_client().get('/healthz').status_code,200)
        finally:event.remove(engine,'before_cursor_execute',captured)
        self.assertEqual(statements,[])

    def test_cached_historical_operations_do_not_restart_fast_empty_scans(self):
        from tests.test_subzero_archive_reconciliation import ArchiveReconciliationTest
        helper=ArchiveReconciliationTest()
        helper.gateway_id=self.gateway_id
        for index in range(24):helper.operation(index,spray=False)
        db.session.commit()
        helper.recover_all(24)  # Durable historical checkpoints already exist.
        self.seed_capture()
        result=self.simulate()
        self.assertGreaterEqual(result['longest_database_free_seconds'],600)
        self.assertLessEqual(result['sql']['database_wakes'],12)

    def test_independent_safety_checks_share_utc_grid_after_different_wake_times(self):
        self.at=self.at.replace(minute=3,second=15)
        self.seed_capture()
        boundary=scheduling.safety_deadline(self.naive())
        self.assertEqual(expiry.recovery_cycle(),boundary)
        worker=self.start_worker()
        self.at+=timedelta(seconds=10)
        worker.wake(worker.generation)
        self.assertEqual(worker.deadline,boundary)
        self.at+=timedelta(minutes=2,seconds=7)
        self.assertEqual(expiry.recovery_cycle(),boundary)
        self.assertEqual(scheduling.plan()[1],boundary)

    def test_irregular_expiration_wins_without_drifting_future_safety_scans(self):
        self.seed_capture()
        deadline=self.naive()+timedelta(seconds=1807)
        identifier=self.backup(deadline)
        self.simulate()
        self.assertEqual(db.session.get(Backup,identifier).purged_at,deadline)
        self.assertEqual(expiry.recovery_cycle(),scheduling.safety_deadline(self.naive()))


class CommitSignalContractTest(unittest.TestCase):
    def test_completed_spear_snapshot_signals_only_after_durable_commit(self):
        from unittest.mock import Mock
        from tests.test_neoscorpion_spear_learning_capture import SpearLearningCaptureTest
        from app.services.neoscorpion_spear_learning_capture import capture_completed_learning_outcome
        helper=SpearLearningCaptureTest();helper.setUp()
        self.addCleanup(helper.tearDown)
        scheduling.install_commit_signals()
        notifier=Mock()
        helper.app.extensions[scheduling.EXTENSION]=notifier
        with patch('app.services.neoscorpion_learning_vault._r2_client') as remote:
            capture_completed_learning_outcome(helper.gateway,helper.assignment.id)
            notifier.notify.assert_not_called()
            db.session.commit()
            notifier.notify.assert_called_once()
            remote.assert_not_called()

    def test_new_subzero_spray_is_a_commit_hint_without_external_io(self):
        from unittest.mock import Mock
        from tests.test_subzero_archive_reconciliation import ArchiveReconciliationTest
        helper=ArchiveReconciliationTest();helper.setUp()
        self.addCleanup(helper.doCleanups)
        scheduling.install_commit_signals()
        notifier=Mock()
        helper.app.extensions[scheduling.EXTENSION]=notifier
        with patch('app.services.neoscorpion_learning_vault._r2_client') as remote:
            helper.operation(1)
            notifier.notify.assert_not_called()
            db.session.commit()
            notifier.notify.assert_called_once()
            remote.assert_not_called()
