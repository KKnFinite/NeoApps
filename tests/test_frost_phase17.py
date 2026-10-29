"""Operational priority, mid-transfer admission and private history regressions."""
import struct
import threading
import unittest
from unittest.mock import Mock, patch
from flask import Flask
from app.services import frost_resources as priority, frost_hrrr_point as hrrr
from tests.test_frost_phase13 import AutoTest


class PriorityTest(unittest.TestCase):
    def test_active_request_yields_background_and_teardown_recovers(self):
        app=Flask(__name__);priority.initialize(app)
        entered=threading.Event();release=threading.Event()
        @app.get('/operation')
        def operation():
            entered.set();release.wait(3);return 'ok'
        thread=threading.Thread(target=lambda:app.test_client().get('/operation'))
        thread.start();self.assertTrue(entered.wait(2))
        try:
            with app.app_context():self.assertIn('Operational request',priority.reason())
        finally:release.set();thread.join(3)
        with app.app_context(),patch.object(priority,'cpu_pressure',return_value=0):
            self.assertIsNone(priority.reason())
        self.assertEqual(app.extensions['frost_request_activity'].active,0)

    def test_cpu_pressure_and_slow_response_cooldown(self):
        app=Flask(__name__);priority.initialize(app)
        with app.app_context(),patch.object(priority,'cpu_pressure',return_value=25):
            self.assertIn('CPU pressure',priority.reason())
        state=app.extensions['frost_request_activity'];state.pressure_at=0
        with app.app_context(),patch.object(priority,'cpu_pressure',return_value=0):
            state.slow_until=priority.time.monotonic()+2
            self.assertIn('response recovery',priority.reason())
            state.slow_until=0;self.assertIsNone(priority.reason())

    def test_decoder_checks_between_blocks(self):
        from numcodecs import Blosc
        import numpy as np
        raw=struct.pack('<20000f',*range(20000))
        compressed=Blosc(cname='lz4',shuffle=1,blocksize=4096).encode(np.frombuffer(raw,dtype='<f4'))
        meta=dict(dtype='<f4',order='C',filters=None,chunks=[20000],compressor=dict(id='blosc',cname='lz4',shuffle=1))
        guard=Mock(side_effect=[True,True,False])
        with self.assertRaisesRegex(RuntimeError,'resource protection'):
            hrrr.numbers(compressed,meta,[0,19000],guard=guard)
        self.assertEqual(guard.call_count,3)

    def test_download_stops_after_admission_changes_and_closes_response(self):
        response=Mock(status=200,headers={})
        response.read1.return_value=b'x'*65536
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        guard=Mock(side_effect=[True,True,False])
        from datetime import datetime,timezone
        source=hrrr.Source(lambda:datetime.now(timezone.utc),Mock(),guard)
        with patch.object(hrrr,'build_opener') as opener:
            opener.return_value.open.return_value=context
            with self.assertRaisesRegex(RuntimeError,'resource protection'):
                source.get('https://hrrrzarr.s3.amazonaws.com/sfc/20261028/20261028_21z_fcst.zarr/surface/TMP/0.4.7')
        context.__exit__.assert_called_once();source.retain.assert_not_called()
        response.read1.assert_called_once()


class CapturePriorityTest(AutoTest):
    def test_request_arriving_after_admission_retains_retry_without_burning_attempt(self):
        from app.models import FrostHistoryTask as Task
        self.provider.side_effect=priority.Deferred('Operational request in progress')
        self.assertEqual(self.tick(),0)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.attempts,0)
        self.assertEqual(row.status,'retry')
        self.assertEqual(row.summary['resource_deferrals'],1)

    def test_weather_acquisition_has_no_open_database_transaction(self):
        from app.extensions import db
        original=self.provider.side_effect
        def acquire():
            self.assertFalse(db.session().in_transaction())
            return original()
        self.provider.side_effect=acquire
        self.assertEqual(self.tick(),1)

    def test_busy_request_defers_without_attempt_or_reconstructed_prediction(self):
        from app.models import FrostHistoryTask as Task
        from app.services import frost_auto as auto
        from datetime import timedelta
        with patch.object(priority,'reason',return_value='Operational request in progress'):
            self.assertEqual(self.tick(),0)
        self.provider.assert_not_called()
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.attempts,0);self.assertEqual(row.summary['resource_deferrals'],1)
        self.t+=timedelta(minutes=2)
        self.assertEqual(self.tick(),1)
        recent=auto.recent_captures(self.f.gateway.id)
        self.assertEqual(recent[0]['deferrals'],1)
        self.assertEqual(recent[0]['matches'],0)

    def test_prediction_history_is_authorized_and_no_archive_reads_on_page(self):
        self.tick()
        from app.services import frost_vault as vault
        with patch.object(vault,'read_json',side_effect=AssertionError('No private I/O on page load')):
            response=self.fixture.fixture.client('master').get('/neosubzero/frost-vault')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'RESEARCH PREDICTIONS',response.data)
        self.assertIn(b'OUTCOME UNKNOWN',response.data)
