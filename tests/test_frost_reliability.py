"""Bounded cooperative capture, verified-source reuse and honest score status."""
import io
import json
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from flask import Flask, g
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_auto as auto, frost_resources as priority, frost_hrrr_point as hrrr
from tests import test_frost_phase13 as fixtures, test_frost_phase14 as native_cases


class CooperativeTest(unittest.TestCase):
    def test_actual_request_finishes_before_research_resumes_without_restarting(self):
        app=Flask(__name__);priority.initialize(app)
        entered, release = threading.Event(), threading.Event()
        @app.get('/operation')
        def operation():
            entered.set();release.wait(2);return 'ok'
        thread=threading.Thread(target=lambda:app.test_client().get('/operation'))
        thread.start();self.assertTrue(entered.wait(1))
        timer=threading.Timer(.05,release.set);timer.start()
        try:
            with app.app_context(),priority.cooperate(max_wait=1) as budget:
                priority.checkpoint()
                self.assertEqual(app.extensions['frost_request_activity'].active,0)
                self.assertGreater(budget['waited'],0)
                self.assertGreater(budget['waits'],0)
                self.assertIsNone(priority.reason())
        finally:release.set();thread.join(2);timer.join(1)

    def test_wait_allowance_is_cumulative_bounded_and_other_jobs_still_fail_fast(self):
        app=Flask(__name__);priority.initialize(app)
        state=app.extensions['frost_request_activity'];state.active=1
        with app.app_context():
            with self.assertRaises(priority.Deferred):priority.checkpoint()
            started=time.monotonic()
            with priority.cooperate(max_wait=.03) as budget:
                with self.assertRaises(priority.Deferred):priority.checkpoint()
                self.assertLessEqual(budget['remaining'],0)
                with self.assertRaises(priority.Deferred):priority.checkpoint()
            self.assertLess(time.monotonic()-started,.5)

    def test_foreground_request_cannot_wait_for_itself(self):
        app=Flask(__name__);priority.initialize(app)
        with app.test_request_context('/operation'),priority.cooperate(max_wait=1):
            app.extensions['frost_request_activity'].active=1
            started=time.monotonic()
            with self.assertRaises(priority.Deferred):priority.checkpoint()
            self.assertLess(time.monotonic()-started,.2)

    def test_window_closing_during_pause_prevents_new_provider_work(self):
        app=Flask(__name__);priority.initialize(app)
        state=app.extensions['frost_request_activity'];state.active=1
        valid=Mock(side_effect=[True,False])
        with app.app_context(),priority.cooperate(max_wait=.5):
            priority.limit_to_window(valid)
            with self.assertRaisesRegex(priority.Deferred,'window closed'):priority.checkpoint()

    def test_deadline_after_yield_is_retryable_and_scope_does_not_leak(self):
        with priority.cooperate(max_wait=1) as budget:
            budget['waits']=1
            self.assertIsInstance(priority.deadline_error('deadline'),priority.Deferred)
            self.assertTrue(priority.wait_metrics())
        self.assertIsInstance(priority.deadline_error('deadline'),TimeoutError)
        self.assertEqual(priority.wait_metrics(),{})


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.AutoTest();self.case.setUp();self.addCleanup(self.case.doCleanups)

    def test_archived_weather_without_scores_is_not_prediction_success(self):
        self.assertEqual(self.case.tick(),1)
        status=auto.status(self.case.f.gateway.id)
        self.assertEqual(status['weather_state'],'CAPTURED')
        self.assertEqual(status['archive_state'],'VERIFIED')
        self.assertEqual(status['prediction_state'],'UNAVAILABLE 0/8')
        self.assertFalse(status['prediction_ready'])
        self.assertIsNone(status['last_fully_scored'])
        self.assertNotIn('PREDICTIONS SCORED',status['state'])
        self.assertEqual(status['recent'][0]['prediction_state'],'UNAVAILABLE 0/8')

    def test_all_cooperative_checkpoints_release_database_reads_first(self):
        checkpoint=priority.checkpoint
        def no_transaction():
            self.assertFalse(db.session().in_transaction())
            return checkpoint()
        with patch.object(priority,'checkpoint',side_effect=no_transaction) as checked:
            self.assertEqual(self.case.tick(),1)
        self.assertGreater(checked.call_count,8)

    def test_lease_is_not_stolen_and_expired_owner_cannot_finish_new_attempt(self):
        row=auto.jobs.task(self.case.f.gateway.id,'worker','memory-budget','frost-worker-v1')
        at=auto.naive(self.case.t)
        first=auto.jobs.claim(row,at=at)
        self.assertIsNotNone(first)
        self.assertIsNone(auto.jobs.claim(row,at=at+timedelta(minutes=1)))
        self.assertFalse(auto.jobs.finish(row.id,'foreign',status='ready'))
        second=auto.jobs.claim(row,at=at+timedelta(minutes=16))
        self.assertIsNotNone(second);self.assertNotEqual(first,second)
        self.assertFalse(auto.jobs.finish(row.id,first,status='ready'))
        self.assertTrue(auto.jobs.finish(row.id,second,status='ready'))


class NativeWorkTest(unittest.TestCase):
    def test_eight_real_models_reuse_four_vectors_without_changing_frozen_contracts(self):
        case=native_cases.CaptureTest();case.setUp();self.addCleanup(case.doCleanups)
        original=auto.native.build
        with patch.object(auto.native,'build',wraps=original) as built:
            case.test_all_eight_real_frozen_contracts_archive_scores_and_refresh()
        self.assertEqual(built.call_count,4)
        status=auto.status(case.f.gateway.id)
        self.assertTrue(status['prediction_ready'])
        self.assertEqual(status['last_fully_scored'],case.t.isoformat())

    def test_actual_source_bytes_are_archived_once_per_hash_not_once_per_url(self):
        case=fixtures.AutoTest();case.setUp();self.addCleanup(case.doCleanups)
        at=case.t
        point, forecasts, bodies=native_cases.native_fixture(at)
        metars=[dict(icaoId='KRFD',reportTime=at.isoformat(),temp=0,dewp=-1,wspd=2)]
        class Response(io.BytesIO):
            status=200
            headers={'Last-Modified':(at-timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT')}
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        def response(request,**_):
            return Response(b'' if request.method=='HEAD' else bodies[request.full_url])
        # Recover the actual acquisition function: this fixture normally mocks
        # it so unrelated capture tests never access external weather providers.
        case.acquire.stop()
        with patch.object(auto,'fetch',side_effect=[
                (json.dumps(metars).encode(),dict(retrieved_at_utc=at.isoformat())),TimeoutError()]),\
                patch.object(hrrr,'build_opener') as opener,patch.object(auto.vault,'put_json',wraps=auto.vault.put_json) as put:
            opener.return_value.open.side_effect=response
            g.frost_capture_gateway=case.f.gateway.id
            source=auto.acquire()
        gets=[r for r in source['native_point']['source_receipts'] if r['method']=='GET']
        unique={r['sha256'] for r in gets}
        self.assertEqual(put.call_count,len(unique))
        self.assertLess(put.call_count,len(gets))
        self.assertEqual(source['native_resources']['archived_source_objects'],len(unique))
        self.assertEqual(source['native_resources']['reused_source_objects'],len(gets)-len(unique))
        for actual, expected in zip(source['native_forecasts'],forecasts):
            self.assertEqual(actual['values'],expected['values'])
        for receipt in gets:
            evidence=auto.vault.read_json(receipt['source_key'],case.f.gateway.id)
            import base64
            self.assertEqual(base64.b64decode(evidence['raw_base64']),bodies[receipt['url']])
        from pathlib import Path
        directory=Path('instance/frost-reliability-20261105');directory.mkdir(parents=True,exist_ok=True)
        (directory/'source-work.json').write_text(json.dumps(dict(
            body_gets=len(gets),before_verified_source_calls=len(gets),after_verified_source_calls=put.call_count,
            downloaded_bytes=source['native_resources']['downloaded_bytes']),indent=2),encoding='utf-8')
