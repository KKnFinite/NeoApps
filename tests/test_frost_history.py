"""Phase 5 source/time/meteorology and durable incremental processing proofs."""
import copy
import csv
import io
import os
import unittest
import tracemalloc
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from flask import g
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from sqlalchemy import text,inspect
from app.extensions import db
from app.models import FrostDocument,FrostHistoryTask,NeoSubZeroSprayRecord,User
from app.services import frost_analysis as analysis,frost_history as history,frost_weather as weather,frost_documents as documents,frost_vault as vault
from tests import test_frost_vault as contracts,test_neosubzero_ucc_postgres as pg

UTC=timezone.utc


def weather_csv(start,end,*,missing=False):
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=['station','valid','lon','lat','tmpf','dwpf','relh','drct','sknt','p01i','vsby','gust','skyc1','skyl1','wxcodes','metar'])
    writer.writeheader()
    at=start
    while at<end:
        writer.writerow(dict(station='RFD',valid=at.strftime('%Y-%m-%d %H:%M'),lon='-89.0876',lat='42.19151',
            tmpf='M' if missing else '30',dwpf='M' if missing else '29',relh='M',drct='180',sknt='4',p01i='0',vsby='10',gust='M',
            skyc1='CLR',skyl1='M',wxcodes='M',metar='KRFD '+at.strftime('%d%H%M')+'Z 18004KT 10SM CLR M01/M02'))
        at+=timedelta(hours=1)
    return stream.getvalue().encode()


def compact(**values):
    from app.services.frost_xchange import extract
    row=extract(contracts.sample(**values),gateway_code='RFD')['rows'][0]
    return analysis.compact_row(row,SimpleNamespace(id=1,sha256='a'*64,extracted_key='source'))


class AnalysisTest(unittest.TestCase):
    def test_public_provider_requests_routine_special_and_honors_rate_limit(self):
        from urllib.error import HTTPError
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=1)
        response=SimpleNamespace(read=lambda count:weather_csv(start,end))
        from contextlib import nullcontext
        with patch.object(weather,'urlopen',return_value=nullcontext(response)) as request:
            with patch.object(weather,'_last',0):raw,url=weather.fetch(start,end)
        self.assertIn('report_type=3&report_type=4',url)
        self.assertIn('tz=UTC',url);self.assertIn('station=RFD',url)
        self.assertEqual(request.call_args.kwargs['timeout'],40)
        with patch.object(weather,'urlopen',side_effect=HTTPError(url,429,'limit',{'Retry-After':'600'},None)):
            with patch.object(weather,'_last',0):
                with self.assertRaises(weather.WeatherUnavailable) as error:weather.fetch(start,end)
        self.assertEqual(error.exception.retry_after,600)

    def test_local_calendar_dst_and_midnight(self):
        self.assertEqual(analysis.local_instant('2025-01-01','00:30'),('2025-01-01T06:30:00+00:00','verified_local_calendar'))
        self.assertEqual(analysis.local_instant('2025-07-01','00:30')[0],'2025-07-01T05:30:00+00:00')
        self.assertEqual(analysis.local_instant('2025-11-02','01:30')[1],'ambiguous_dst')
        self.assertEqual(analysis.local_instant('2025-03-09','02:30')[1],'nonexistent_dst')
        self.assertEqual(analysis.local_instant('2025-01-01',None)[1],'missing_clock')
        row=compact(ApplicationDate='1/01/2025',StartTime1='23:59',EndTime1='00:03')
        self.assertEqual(row['fluids'][0]['ended_at_utc'],'2025-01-02T06:03:00+00:00')

    def test_classification_reuses_codes_and_never_infers_negatives(self):
        for reason,label in [('F','LIKELY FROST PRESENT'),('Frost','LIKELY FROST PRESENT'),('P','ANTICIPATED FROST RISK'),
                ('SN','NON-FROST TREATMENT'),('FZRA','NON-FROST TREATMENT'),('CS','NON-FROST TREATMENT'),
                ('','UNKNOWN'),('Something ambiguous','UNKNOWN')]:
            self.assertEqual(analysis.classification(reason)['label'],label)
        self.assertEqual(analysis.classification('F',actual_spray=False)['label'],'UNKNOWN')
        self.assertEqual(analysis.classification(None,actual_spray=False,verified_not_sprayed=True)['label'],'UNKNOWN')
        self.assertEqual(analysis.classification(None,actual_spray=False,verified_not_sprayed=True,prior_pretreat=True)['label'],'ANTICIPATED FROST RISK')
        self.assertEqual(analysis.classification(None,actual_spray=False,verified_not_sprayed=True,prior_pretreat=False)['label'],'LIKELY FROST ABSENT')

    def test_overlap_identity_provenance_and_revision_order_not_import_order(self):
        first=compact(LastModifiedUTC='1/01/2025 01:00')
        second=compact(LastModifiedUTC='1/02/2025 01:00',Reason='F')
        duplicate=copy.deepcopy(first);duplicate['source']['document_id']=2
        rows,stats=analysis.deduplicate([second,duplicate,first])
        self.assertEqual((stats['deduplicated_records'],stats['overlapping_rows'],stats['revisions']),(1,1,1))
        self.assertEqual(rows[0]['reason_code'],'F')
        self.assertEqual(len(rows[0]['sources']),3)
        self.assertEqual(len(rows[0]['revision_evidence']),2)
        self.assertFalse(rows[0]['revision_conflict'])

    def test_ambiguous_correction_is_review_not_arbitrary_label_or_weather(self):
        rows,stats=analysis.deduplicate([compact(),compact(Reason='F')])
        self.assertTrue(rows[0]['revision_conflict']);self.assertEqual(stats['revision_conflicts'],1)
        self.assertEqual(rows[0]['classification']['label'],'UNKNOWN');self.assertEqual(rows[0]['fluids'],[])
        # Missing RecordIDs are never grouped by a guessed tail/date identity.
        a=compact(RecordID='');b=copy.deepcopy(a);b['source']['document_id']=2
        self.assertEqual(len(analysis.deduplicate([a,b])[0]),2)

    def test_observation_units_station_quality_and_no_nonfinite_values(self):
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=1)
        raw=weather_csv(start,end)
        rows=weather.parse(raw,start,end)
        self.assertAlmostEqual(rows[0]['temperature_c'],-1.11111111)
        self.assertGreater(rows[0]['relative_humidity'],90)
        self.assertIsNone(rows[0]['gust_kt']);self.assertTrue(rows[0]['clear_sky'])
        self.assertEqual(rows[0]['wind_direction_degrees'],180)
        for replacement in (raw.replace(b'RFD,',b'ORD,'),raw.replace(b'-89.0876',b'-90.0876'),b'<html>outage</html>'):
            with self.assertRaises(ValueError):weather.parse(replacement,start,end)
        row=weather.parse(raw.replace(b',30,',b',Infinity,'),start,end)[0]
        self.assertIsNone(row['temperature_c'])

    def test_preceding_window_no_forecast_leakage_and_missing_coverage(self):
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=2)
        rows=weather.parse(weather_csv(start,end),start,end)
        target=start+timedelta(hours=16)
        features=analysis.features(target.isoformat(),rows)
        self.assertTrue(features['matched']);self.assertFalse(features['incomplete_window'])
        self.assertEqual(features['observed_interval_hours_12h'],12)
        self.assertEqual(features['below_freezing_proxy_hours'],12)
        self.assertEqual(features['cooling_rate_3h_c_per_hour'],0)
        self.assertLessEqual(features['observation']['utc'],target.isoformat())
        missing=analysis.features(target.isoformat(),rows[:2])
        self.assertFalse(missing['matched']);self.assertTrue(missing['incomplete_window'])
        self.assertIsNone(missing['residual_moisture_proxy'])
        partial=copy.deepcopy(rows)
        for row in partial:
            if row['utc']<target.isoformat():row['temperature_c']=None
        self.assertTrue(analysis.features(target.isoformat(),partial)['incomplete_window'])

    def test_solar_is_geometry_and_aircraft_parking_are_not_invented(self):
        at=datetime(2025,1,1,6,tzinfo=UTC)
        self.assertTrue(analysis.solar(at)['night'])
        self.assertFalse(analysis.solar(at.replace(hour=18))['night'])
        row=compact()
        self.assertIsNone(row['parking']);self.assertIsNone(row['aircraft_surface_temperature'])
        self.assertEqual(row['prior_pretreatment'],'unknown')
        self.assertIn('not measured',analysis.solar(at)['basis'])

    def test_report_cohorts_do_not_count_trucks_or_fluids_as_independent_aircraft(self):
        a=compact(ApplicationDate='1/01/2025',Reason='F');b=compact(ApplicationDate='1/01/2025',Reason='F',RecordID='2',TruckNumber='other')
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=2)
        dataset=analysis.analyze([a,b],weather.parse(weather_csv(start,end),start,end))
        self.assertEqual(dataset['summary']['LIKELY FROST PRESENT'],2)
        self.assertEqual(dataset['descriptive_cohorts']['LIKELY FROST PRESENT']['applications'],1)
        self.assertEqual(dataset['learning_status'],'research_only_no_validated_model')


class PipelineTest(unittest.TestCase):
    setUp=contracts.FrostVaultTest.setUp
    storage=contracts.FrostVaultTest.storage
    client=contracts.FrostVaultTest.client
    flow=contracts.FrostVaultTest.flow

    def actor(self):
        return User.query.filter_by(username='frost-admin').first() or self.f._user('frost-admin','master')

    def stage(self,filename='season.csv',**values):
        doc,_=documents.stage(self.f.gateway,self.actor(),filename,contracts.sample(**values))
        db.session.commit();documents.drain(self.f.gateway.id)
        return doc

    def fetcher(self):
        return self.enterContext(patch('app.services.frost_weather.fetch',side_effect=lambda start,end:(weather_csv(start,end),'https://fixture.invalid')))

    def test_incremental_verified_only_idempotent_with_no_live_writes(self):
        store=self.storage();fetch=self.fetcher();gw=self.f.gateway.id
        first=self.stage(Reason='F')
        self.assertEqual(history.drain(gw),1)
        self.assertEqual(history.health(gw)['state'],'DATA READY')
        self.assertEqual(history.health(gw)['totals']['LIKELY FROST PRESENT'],1)
        count,writes=fetch.call_count,store.writes
        self.assertEqual(history.drain(gw),0);self.assertEqual(fetch.call_count,count);self.assertEqual(store.writes,writes)
        # Overlapping export adds provenance but reuses monthly weather.
        self.stage('overlap.csv',Reason='F',**{'ExtraExportColumn':'different envelope'})
        history.drain(gw)
        self.assertEqual(history.health(gw)['catalog']['deduplicated_records'],1)
        self.assertEqual(history.health(gw)['catalog']['revision_conflicts'],1)  # Changed raw content, no modification evidence.
        self.assertEqual(fetch.call_count,count)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        self.assertEqual(documents.original(first),contracts.sample(Reason='F'))

    def test_bounded_batch_reuses_and_closes_one_transport_on_success_and_failure(self):
        store=self.storage();self.fetcher();self.stage();gw=self.f.gateway.id
        from unittest.mock import Mock
        store.close=Mock()
        with patch('app.services.neoscorpion_learning_vault._r2_client',return_value=store) as factory:
            self.assertEqual(history.drain(gw),1)
            self.assertEqual(factory.call_count,1)
            store.close.assert_called_once()
        store.close.reset_mock()
        key=FrostHistoryTask.query.filter_by(kind='partition').one().result_key
        def fail(*args,**kwargs):
            vault.read_json(key,gw)
            raise RuntimeError('fixture failure')
        with patch('app.services.frost_history._drain',side_effect=fail):
            with self.assertRaises(RuntimeError):history.drain(gw)
        store.close.assert_called_once()

    def test_paused_jobs_are_not_claimed_or_resumed_by_normal_polling(self):
        self.storage();self.fetcher();self.stage();gw=self.f.gateway.id;history.discover(gw)
        row=FrostHistoryTask.query.filter_by(kind='partition').one()
        row.retry_at=datetime.utcnow()+timedelta(hours=24);db.session.commit()
        with patch('app.services.frost_weather.fetch') as fetch,patch('app.services.neoscorpion_learning_vault._r2_client') as client:
            self.assertEqual(history.drain(gw),0)
            fetch.assert_not_called()
            client.assert_not_called()
        self.assertIsNone(row.result_key)

    def test_overlap_same_row_and_source_correction_changes_only_affected_partition(self):
        self.storage();self.fetcher();gw=self.f.gateway.id
        self.stage('one.csv',Reason='F',LastModifiedUTC='9/04/2025 09:00')
        history.drain(gw)
        row=FrostHistoryTask.query.filter_by(kind='partition').one();old=row.result_key
        # A different document envelope carrying identical row plus an extra row.
        raw=contracts.sample(Reason='F',LastModifiedUTC='9/04/2025 09:00')
        raw2=contracts.sample(Reason='P',RecordID='2',ApplicationDate='10/02/2025',LastModifiedUTC='10/02/2025 09:00')
        combined=raw+raw2.split(b'\n',1)[1]
        document,_=documents.stage(self.f.gateway,self.actor(),'two-months.csv',combined);db.session.commit();documents.drain(gw)
        history.drain(gw,limit=10)
        self.assertEqual(history.health(gw)['catalog']['overlapping_rows'],1)
        self.assertEqual(history.health(gw)['catalog']['deduplicated_records'],2)
        self.assertNotEqual(row.result_key,old) # Same evidence, new provenance receipt.
        self.assertEqual(vault.read_json(old,gw)['records'][0]['reason_code'],'F')
        self.stage('corrected.csv',Reason='SN',LastModifiedUTC='9/05/2025 09:00')
        history.drain(gw,limit=10)
        self.assertEqual(history.health(gw)['catalog']['revisions'],1)
        self.assertEqual(vault.read_json(row.result_key,gw)['records'][0]['reason_code'],'SN')

    def test_weather_failure_backoff_restart_and_recovery(self):
        self.storage();self.stage();gw=self.f.gateway.id
        with patch('app.services.frost_weather.fetch',side_effect=weather.WeatherUnavailable('IEM HTTP 429',120)) as fetch:
            self.assertEqual(history.drain(gw),0)
            self.assertEqual(history.health(gw)['state'],'REQUIRES REVIEW')
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(history.drain(gw),0);self.assertEqual(fetch.call_count,1)
        for row in FrostHistoryTask.query.all():row.retry_at=datetime.utcnow()-timedelta(seconds=1)
        db.session.commit();self.fetcher()
        self.assertEqual(history.drain(gw),1)
        self.assertEqual(history.health(gw)['state'],'DATA READY')

    def test_raw_weather_checkpoint_survives_derived_archive_failure(self):
        self.storage();self.stage();fetch=self.fetcher();gw=self.f.gateway.id
        with patch('app.services.frost_vault.put_json',side_effect=ValueError('derived archive failed')):
            with self.assertRaises(ValueError):history.observations(gw,'2025-01')
        job=FrostHistoryTask.query.filter_by(kind='weather').one()
        self.assertTrue(job.source_key);self.assertIsNone(job.result_key)
        job.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        rows,key=history.observations(gw,'2025-01')
        self.assertTrue(rows);self.assertTrue(key);self.assertEqual(fetch.call_count,1)

    def test_partial_weather_stays_incomplete_not_false_ready(self):
        self.storage();self.stage();gw=self.f.gateway.id
        with patch('app.services.frost_weather.fetch',side_effect=lambda start,end:(weather_csv(start,end,missing=True),'https://fixture.invalid')):
            history.drain(gw)
        self.assertEqual(history.health(gw)['state'],'WEATHER INCOMPLETE')
        self.assertEqual(history.health(gw)['totals']['weather_matched'],0)

    def test_staging_sources_do_not_look_fully_analyzed(self):
        self.storage();self.fetcher();self.stage();gw=self.f.gateway.id;history.drain(gw)
        doc,_=documents.stage(self.f.gateway,self.actor(),'later.csv',contracts.sample(RecordID='later'))
        db.session.commit()
        self.assertEqual(history.health(gw)['state'],'WEATHER INCOMPLETE')
        self.assertEqual(history.health(gw)['pending_sources'],1)

    def test_stale_worker_cannot_publish_over_newer_partition(self):
        self.storage();self.fetcher();self.stage();gw=self.f.gateway.id;history.discover(gw)
        row=FrostHistoryTask.query.filter_by(kind='partition').one()
        original=analysis.analyze
        def invalidate(*args):
            dataset=original(*args)
            current=db.session.get(FrostHistoryTask,row.id)
            current.input_hash='b'*64;current.lease_token=None;db.session.commit()
            return dataset
        with patch.object(analysis,'analyze',side_effect=invalidate):self.assertFalse(history.process(row))
        self.assertIsNone(db.session.get(FrostHistoryTask,row.id).result_key)

    def test_decade_catalog_is_month_fragmented_with_bounded_working_memory(self):
        self.storage();actor=self.actor();gw=self.f.gateway.id
        for year in range(2015,2026):
            template=list(csv.DictReader(io.StringIO(contracts.sample().decode())))[0]
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(template));writer.writeheader()
            for index in range(3000):
                writer.writerow(dict(template,RecordID=str(year*10000+index),ApplicationDate=f'9/04/{year}',Notes='Source notes '*35))
            documents.stage(self.f.gateway,actor,f'{year}.csv',stream.getvalue().encode());db.session.commit()
            documents.drain(gw,limit=20)
        tracemalloc.start()
        try:
            self.assertTrue(history.discover(gw))
            _,peak=tracemalloc.get_traced_memory()
        finally:tracemalloc.stop()
        self.assertLess(peak,160*1024*1024)
        self.assertEqual(history.health(gw)['catalog']['deduplicated_records'],33000)
        partitions=FrostHistoryTask.query.filter_by(kind='partition').all()
        self.assertEqual(len(partitions),11)
        source=vault.read_json(partitions[0].source_key,gw)
        self.assertIn('record_pointers',source);self.assertNotIn('records',source)
        self.assertNotIn('fluids',source['record_pointers'][0])

    def test_missing_weather_dst_and_source_error_do_not_become_data_ready(self):
        self.storage();gw=self.f.gateway.id
        self.stage(ApplicationDate='11/02/2025',StartTime1='01:30',EndTime1='01:35',Type4Used='0')
        self.fetcher();history.drain(gw)
        self.assertEqual(history.health(gw)['state'],'REQUIRES REVIEW')
        dataset=vault.read_json(FrostHistoryTask.query.filter_by(kind='partition').one().result_key,gw)
        self.assertFalse(dataset['records'][0]['fluids'][0]['weather']['matched'])
        self.assertIsNone(dataset['records'][0]['fluids'][0]['occurred_at_utc'])

    def test_permissions_private_dataset_health_and_no_stale_download(self):
        self.storage();self.fetcher();self.stage();gw=self.f.gateway.id;history.drain(gw)
        admin=self.client('master');watcher=self.client('watcher')
        g.pop('_login_user',None)
        page=admin.get('/neosubzero/frost-vault');self.assertEqual(page.status_code,200)
        self.assertIn(b'HISTORICAL INTELLIGENCE',page.data)
        status=admin.get('/neosubzero/frost-vault/status').json
        self.assertEqual(status['history']['catalog']['deduplicated_records'],1)
        row=FrostHistoryTask.query.filter_by(kind='partition').one();url='/neosubzero/frost-vault/history/'+str(row.id)
        g.pop('_login_user',None);self.assertEqual(watcher.get(url).status_code,403)
        g.pop('_login_user',None)
        response=admin.get(url);self.assertEqual(response.status_code,200);self.assertIn('no-store',response.headers['Cache-Control'])
        row.status='pending';db.session.commit();self.assertEqual(admin.get(url).status_code,409)

    def test_versioned_reprocessing_uses_existing_weather_and_keeps_old_evidence(self):
        starting_version=analysis.VERSION
        self.storage();fetch=self.fetcher();self.stage();gw=self.f.gateway.id;history.drain(gw)
        old=FrostHistoryTask.query.filter_by(kind='partition').one().result_key;count=fetch.call_count
        with patch.object(analysis,'VERSION','frost-history-test-v2'):
            self.assertEqual(history.drain(gw),1)
            self.assertEqual(history.health(gw)['version'],'frost-history-test-v2')
        self.assertEqual(fetch.call_count,count)
        self.assertEqual(vault.read_json(old,gw)['schema_version'],starting_version)


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires disposable loopback PostgreSQL')
class HistoryPostgresTest(PipelineTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture

    def test_concurrent_claims_have_one_winner(self):
        gw=self.f.gateway.id
        task=history.task(gw,'partition','2025-01');db.session.commit();identifier=task.id
        db.session.remove()
        def worker(_):
            with self.f.app.app_context():
                try:return history.claim(db.session.get(FrostHistoryTask,identifier))
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,range(2)))
        self.assertEqual(sum(value is not None for value in results),1)

    def test_additive_bootstrap_populated_schema_idempotent(self):
        from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table,verify_neosubzero_schema_contract
        self.storage();doc=self.stage();identifier=doc.id
        db.session.execute(text('DROP TABLE frost_history_tasks'));db.session.commit()
        self.f.app.config['TESTING']=False
        try:
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
        finally:self.f.app.config['TESTING']=True
        self.assertEqual(db.session.get(FrostDocument,identifier).sha256,doc.sha256)
        verify_neosubzero_schema_contract(db.session.connection())

    def test_simultaneous_month_creation_preserves_unique_identity(self):
        gw=self.f.gateway.id;db.session.remove()
        def worker(_):
            with self.f.app.app_context():
                try:
                    row=history.task(gw,'weather','2025-01',weather.VERSION)
                    identifier=row.id;db.session.commit();return identifier
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,range(2)))
        self.assertEqual(results[0],results[1])

    def test_two_process_equivalent_batches_cannot_spend_the_same_memory_budget(self):
        self.storage();gw=self.f.gateway.id;db.session.remove()
        entered,release=Event(),Event()
        def slow(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(10));return 1
        def worker():
            with self.f.app.app_context():
                try:return history.drain(gw)
                finally:db.session.remove()
        with patch.object(history,'_drain',side_effect=slow):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(worker)
                try:
                    self.assertTrue(entered.wait(10))
                    self.assertEqual(pool.submit(worker).result(timeout=10),0)
                finally:release.set()
                self.assertEqual(first.result(timeout=10),1)
