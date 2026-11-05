"""Configured windows, resource retries and truthful research availability."""
import io
import tracemalloc
import unittest
from datetime import datetime, timedelta, time, timezone
from unittest.mock import patch, Mock
from sqlalchemy import event

from app.extensions import db
from app.models import FrostHistoryTask as Task, SortTimelineSortSetting, GatewaySortMatrix
from app.services import frost_auto as auto, frost_resources as priority, frost_hrrr_point as hrrr
from app.services import background_deadlines as deadlines
from app.services.frost_schedule import Schedule
from tests import test_frost_phase13 as fixtures
from tests.frost_schedule_fixture import configure

UTC = timezone.utc


class FrostSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.AutoTest();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.gateway = self.case.f.gateway
        configure(self.gateway,days=('monday','tuesday','wednesday','thursday'),start=time(21),end=time(5))
        self.case.t = datetime(2026,11,4,3,tzinfo=UTC)
        # SQLAlchemy's column default holds the real clock. New synthetic
        # receipt deadlines must use this test's fixed acquisition clock too.
        def receipt_clock(_mapper, _connection, row):
            if row.retry_at is None:row.retry_at=auto.naive(self.case.t)
        event.listen(Task,'before_insert',receipt_clock)
        self.addCleanup(event.remove,Task,'before_insert',receipt_clock)

    def test_database_sort_window_weekdays_overnight_and_exact_boundaries(self):
        schedule = Schedule.load(self.gateway)
        for at, expected in [('2026-11-03T02:59:59',False),('2026-11-03T03:00:00',True),
                ('2026-11-03T10:59:59',True),('2026-11-03T11:00:00',False),
                ('2026-11-06T10:59:59',True),('2026-11-07T03:00:00',False)]:
            with self.subTest(at=at):
                self.assertEqual(schedule.eligible(datetime.fromisoformat(at).replace(tzinfo=UTC)),expected)
        # OPS ends at 04:00; actual Sort continues to 05:00.
        row = SortTimelineSortSetting.query.filter_by(gateway_id=self.gateway.id,sort_name='night').one()
        row.ops_window_start_local, row.ops_window_end_local = time(21),time(4)
        row.sort_window_end_local = time(6);db.session.commit()
        self.assertTrue(Schedule.load(self.gateway).eligible(datetime(2026,11,4,11,30,tzinfo=UTC)))

    def test_missing_configuration_and_foreign_gateway_fail_closed(self):
        row = SortTimelineSortSetting.query.filter_by(gateway_id=self.gateway.id,sort_name='night').one()
        row.sort_window_end_local=None;db.session.commit()
        self.assertFalse(Schedule.load(self.gateway).eligible(self.case.t))
        self.assertEqual(self.case.tick(),0);self.case.provider.assert_not_called()
        self.assertEqual(Task.query.filter_by(kind='auto_capture').count(),0)
        self.assertIsNone(auto.status(self.gateway.id)['next_capture'])
        self.gateway.code='OTHER';db.session.commit()
        self.assertEqual(self.case.tick(),0)

    def test_closed_hours_do_not_create_capture_and_deadline_targets_next_opening(self):
        self.case.t=datetime(2026,11,7,3,tzinfo=UTC)  # Friday night is closed.
        self.assertEqual(self.case.tick(),0);self.case.provider.assert_not_called()
        self.assertEqual(Task.query.filter(Task.kind.in_(('auto_capture','auto_control'))).count(),0)
        self.assertEqual(auto.status(self.gateway.id)['next_capture'],'2026-11-10T03:00:00+00:00')
        self.case.f.app.config['FROST_AUTO_COLLECTION']=True
        due, _ = deadlines.plan(self.case.t.replace(tzinfo=None))
        self.assertNotIn('auto',due.get(self.gateway.id,set()))

    def test_one_capture_per_hour_and_closed_hours_are_not_missing_predictions(self):
        self.case.t=datetime(2026,11,6,10,tzinfo=UTC)
        self.assertEqual(self.case.tick(),1);self.assertEqual(self.case.tick(),0)
        self.case.t=datetime(2026,11,10,3,tzinfo=UTC)
        self.assertEqual(self.case.tick(),1)
        self.assertEqual(auto.status(self.gateway.id)['missed'],0)
        self.assertEqual(Task.query.filter_by(kind='auto_capture').count(),2)
        self.assertEqual(self.case.provider.call_count,2)

    def test_dst_uses_distinct_utc_slots_and_configured_timezone(self):
        configure(self.gateway,days=('saturday',),start=time(21),end=time(5))
        schedule = Schedule.load(self.gateway)
        first=datetime(2026,11,1,6,tzinfo=UTC);second=first+timedelta(hours=1)
        self.assertTrue(schedule.eligible(first));self.assertTrue(schedule.eligible(second))
        self.assertNotEqual(auto.identity(first),auto.identity(second))
        self.assertEqual(schedule.next_capture(first),second)
        with patch('app.services.frost_schedule.gateway_timezone',return_value='UTC'):
            self.assertFalse(Schedule.load(self.gateway).eligible(first))

    def test_archive_retry_outside_window_never_reacquires_or_rescores(self):
        self.case.t=datetime(2026,11,6,10,55,tzinfo=UTC)
        original=auto.predictions
        with patch.object(auto,'predictions',wraps=original) as predictions:
            with patch.object(auto.vault,'put_json',side_effect=OSError('isolated archive outage')):
                self.assertEqual(self.case.tick(),0)
            row=Task.query.filter_by(kind='auto_capture').one()
            original_time=row.summary['captured_at_utc']
            self.case.t+=timedelta(minutes=10)  # Sort is closed now.
            self.assertEqual(self.case.tick(),1)
            self.case.provider.assert_called_once();predictions.assert_called_once()
            self.assertEqual(row.summary['captured_at_utc'],original_time)
            self.assertEqual(Task.query.filter_by(kind='auto_capture').count(),1)

    def test_resource_retry_within_hour_recovers_without_burning_attempts(self):
        original=self.case.provider.side_effect
        self.case.provider.side_effect=priority.Deferred('HRRR resource contention')
        self.assertEqual(self.case.tick(),0)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual((row.status,row.attempts),('retry',0))
        self.assertNotIn('staged',row.summary)
        self.case.t+=timedelta(minutes=2);self.case.provider.side_effect=original
        self.assertEqual(self.case.tick(),1);self.assertEqual(self.case.tick(),0)
        self.assertEqual(row.status,'ready');self.assertEqual(row.attempts,1)

    def test_inflight_acquisition_crossing_window_never_scores_historical_slot(self):
        self.case.t=datetime(2026,11,6,10,59,tzinfo=UTC)
        original=self.case.provider.side_effect
        def late():
            self.case.t+=timedelta(minutes=2)
            return original()
        self.case.provider.side_effect=late
        with patch.object(auto,'predictions',side_effect=AssertionError('Expired window must not score')):
            self.assertEqual(self.case.tick(),0)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertIsNone(row.result_key);self.assertNotIn('staged',row.summary)

    def test_scoring_contention_is_retried_instead_of_committed_as_unavailable(self):
        with patch.object(priority,'checkpoint',side_effect=priority.Deferred('Operational request')):
            with self.assertRaises(priority.Deferred):
                auto.predictions(self.gateway.id,self.case.source)


class AcquisitionSafetyTest(unittest.TestCase):
    def test_hrrr_resource_deferral_does_not_finalize_eight_unavailable_scores(self):
        from flask import Flask, g
        import json
        at=datetime.now(UTC)
        metar=[dict(icaoId='KRFD',reportTime=at.isoformat(),temp=0,dewp=-1)]
        app=Flask(__name__)
        with app.app_context(),patch.object(auto,'fetch',side_effect=[
                (json.dumps(metar).encode(),dict(retrieved_at_utc=at.isoformat())),TimeoutError()]),\
                patch.object(hrrr,'collect',side_effect=priority.Deferred('HRRR memory reserve')):
            g.frost_capture_gateway=1
            with self.assertRaisesRegex(priority.Deferred,'HRRR memory reserve'):auto.acquire()

    def test_nws_resource_deferral_is_not_swallowed_as_missing_weather(self):
        at=datetime.now(UTC)
        metar=[dict(icaoId='KRFD',reportTime=at.isoformat(),temp=0,dewp=-1)]
        import json
        with patch.object(auto,'fetch',side_effect=[(json.dumps(metar).encode(),dict(retrieved_at_utc=at.isoformat())),
                priority.Deferred('Operational request')]):
            with self.assertRaises(priority.Deferred):auto.acquire()

    def test_hrrr_body_memory_is_released_before_private_archival(self):
        raw=b'x'*(3*1024*1024)
        response=io.BufferedReader(io.BytesIO(raw))
        response.status=200;response.headers={}
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        observed=[]
        def retain(body,receipt):
            observed.append(tracemalloc.get_traced_memory()[0])
            self.assertEqual(body,raw)
            return receipt
        source=hrrr.Source(lambda:datetime.now(UTC),retain,lambda:True)
        tracemalloc.start()
        try:
            with patch.object(hrrr,'build_opener') as opener:
                opener.return_value.open.return_value=context
                result,_=source.get('https://hrrrzarr.s3.amazonaws.com/sfc/20261104/20261104_01z_fcst.zarr/surface/TMP/0.4.7')
            self.assertEqual(result,raw)
            self.assertLess(observed[0],len(raw)*1.5)
        finally:tracemalloc.stop();response.close()
        context.__exit__.assert_called_once()
