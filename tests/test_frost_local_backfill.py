"""Isolated local compute/authenticated publish; never production transactions."""
import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime,timedelta
from unittest.mock import patch,Mock
from flask import g
from app.extensions import db
from app.models import FrostHistoryTask as Task,NeoSubZeroSprayRecord
from app.services import frost_history as history,frost_local_backfill as local,frost_vault as vault
from scripts import frost_backfill as cli
from tests import test_frost_history as fixtures,test_neosubzero_ucc_postgres as pg


class LocalBackfillTest(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.PipelineTest();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.f=self.base.f;self.setup_flow()

    def setup_flow(self):
        self.store=self.base.storage();self.base.fetcher();self.doc=self.base.stage(Reason='F')
        self.gw=self.f.gateway.id;self.actor=self.base.actor()
        history.discover(self.gw)
        self.month=Task.query.filter_by(gateway_id=self.gw,kind='partition').one()
        self.month.retry_at=datetime.utcnow()+timedelta(days=1);db.session.commit()

    def package(self):
        self.envelope=local.export(self.gw,self.actor.id,self.month.id)
        with tempfile.TemporaryDirectory() as folder:
            path=cli.compute(self.envelope,folder);return path.read_bytes()

    def test_local_compute_publishes_canonical_evidence_during_server_pause_and_retries(self):
        paused=self.month.retry_at;raw=self.package()
        self.assertEqual(db.session.get(Task,self.month.id).retry_at,paused)
        with patch.object(history.weather,'fetch',side_effect=AssertionError('Render fetched weather')),patch.object(history.analysis,'analyze',side_effect=AssertionError('Render computed features')):
            first=local.publish(self.gw,self.actor.id,raw);again=local.publish(self.gw,self.actor.id,raw)
        self.assertEqual(first.id,again.id);self.assertEqual(first.result_key,again.result_key)
        self.assertEqual(first.status,'ready');self.assertEqual(first.summary['processing_location'],'local')
        self.assertEqual(history.health(self.gw)['totals']['weather_matched'],1)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_reuses_existing_weather_and_no_completed_month_is_reprocessed(self):
        raw=self.package();row=local.publish(self.gw,self.actor.id,raw)
        with self.assertRaisesRegex(ValueError,'not pending'):local.export(self.gw,self.actor.id,row.id)
        self.assertEqual(Task.query.filter_by(kind='weather',status='ready').count(),1)

    def test_render_cannot_discover_fetch_analyze_or_start_a_batch_even_after_pause(self):
        with patch.dict(os.environ,{'RENDER':'true'}),patch.object(history.vault,'transport_scope') as transport:
            self.assertEqual(history.drain(self.gw),0);self.assertFalse(history.discover(self.gw));self.assertFalse(history.process(self.month))
            with self.assertRaisesRegex(RuntimeError,'workstation'):history.observations(self.gw,'2025-09')
            with self.assertRaises(RuntimeError):cli.compute({},'never-opened')
            transport.assert_not_called()

    def test_tampering_stale_sources_and_other_operator_cannot_publish(self):
        raw=self.package()
        with self.assertRaisesRegex(ValueError,'operator'):local.publish(self.gw,self.actor.id+1,raw)
        self.month.input_hash='0'*64;db.session.commit()
        with self.assertRaisesRegex(ValueError,'source changed'):local.publish(self.gw,self.actor.id,raw)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_malformed_zip_traversal_and_changed_readings_rejected(self):
        for raw in (b'not zip',b'x'*(local.MAX_ZIP+1)):
            with self.assertRaises(ValueError):local.publish(self.gw,self.actor.id,raw)
        original=self.package()
        with zipfile.ZipFile(io.BytesIO(original)) as archive:files={n:archive.read(n) for n in archive.namelist()}
        changed=json.loads(files['result.json']);changed['records'][0]['tail']='NOTHER'
        files['result.json']=vault.canonical(changed)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for n,raw in files.items():archive.writestr(n,raw)
        with self.assertRaisesRegex(ValueError,'identity'):local.publish(self.gw,self.actor.id,stream.getvalue())
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr('../escape','x')
        with self.assertRaisesRegex(ValueError,'path'):local.unpack(stream.getvalue())

    def test_archive_failure_leaves_task_pending_and_safe_retry_closes_clients(self):
        raw=self.package();self.store.close=Mock()
        with patch.object(vault,'put_json',side_effect=RuntimeError('outage')):
            with self.assertRaises(RuntimeError):local.publish(self.gw,self.actor.id,raw)
        self.assertEqual(db.session.get(Task,self.month.id).status,'pending')
        local.publish(self.gw,self.actor.id,raw);self.store.close.assert_called()

    def test_authenticated_routes_permission_and_file_feedback(self):
        master=self.base.client('master');watcher=self.base.client('watcher')
        g.pop('_login_user',None);self.assertEqual(watcher.get('/neosubzero/frost-vault/backfill/'+str(self.month.id)).status_code,403)
        g.pop('_login_user',None);response=master.get('/neosubzero/frost-vault/backfill/'+str(self.month.id))
        self.assertEqual(response.status_code,200);self.assertIn('private',response.headers['Cache-Control'])
        g.pop('_login_user',None);response=master.post('/neosubzero/frost-vault/backfill/publish',data={'result':(io.BytesIO(b'bad'),'result.zip')},content_type='multipart/form-data')
        self.assertEqual(response.status_code,409);self.assertFalse(response.json['ok'])

    def test_orphaned_month_token_does_not_turn_retry_pause_into_active_server_work(self):
        self.month.lease_token='orphaned-before-pause';db.session.commit();paused=self.month.retry_at
        self.assertTrue(local.active_lease(self.month))
        with patch.dict(self.f.app.config,{'FROST_HISTORY_EXECUTION':'local'}):
            self.assertFalse(local.active_lease(self.month))
            raw=self.package();self.assertEqual(db.session.get(Task,self.month.id).retry_at,paused)
            worker=history.task(self.gw,'worker','memory-budget','frost-worker-v1')
            worker.lease_token='actually-running';worker.retry_at=datetime.utcnow()+timedelta(minutes=5);db.session.commit()
            with self.assertRaisesRegex(ValueError,'processor owns'):local.export(self.gw,self.actor.id,self.month.id)
            with self.assertRaisesRegex(ValueError,'conflict'):local.publish(self.gw,self.actor.id,raw)
            worker.lease_token=None;db.session.commit()
            result=local.publish(self.gw,self.actor.id,raw)
            self.assertEqual(result.status,'ready');self.assertIsNone(result.lease_token)

    def test_partial_batch_is_explicit_and_identical_successful_files_are_safe_to_retry(self):
        raw=self.package();master=self.f.app.test_client();self.f._login(master,self.actor);g.pop('_login_user',None)
        response=master.post('/neosubzero/frost-vault/backfill/publish',data={'result':[
            (io.BytesIO(raw),'good.zip'),(io.BytesIO(b'broken'),'bad.zip')]},content_type='multipart/form-data')
        self.assertEqual(response.status_code,409);self.assertIn('Already published:',response.json['error'])
        self.assertEqual(db.session.get(Task,self.month.id).status,'ready')
        g.pop('_login_user',None)
        response=master.post('/neosubzero/frost-vault/backfill/publish',data={'result':[
            (io.BytesIO(raw),'good.zip'),(io.BytesIO(raw),'again.zip')]},content_type='multipart/form-data')
        self.assertEqual(response.status_code,200);self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_content_addressed_original_proof_needs_no_server_history_download_or_ticket(self):
        raw=self.package()
        envelope,dataset,files=local.unpack(raw)
        source=vault.read_json(self.month.source_key,self.gw)
        proof=dict(source=source,fragments={p['fragment_key']:vault.read_json(p['fragment_key'],self.gw) for p in source['record_pointers']})
        envelope['ticket']='NOT_A_SERVER_TICKET'
        def pack(proof):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for name,value in dict(files,**{'job.json':vault.canonical(envelope),'result.json':vault.canonical(dataset),'proof.json':vault.canonical(proof)}).items():z.writestr(name,value)
            return stream.getvalue()
        altered=copy.deepcopy(proof);next(iter(altered['fragments'].values()))['records'][0]['tail']='WRONG'
        with self.assertRaisesRegex(ValueError,'content hash'):local.publish(self.gw,self.actor.id,pack(altered))
        with patch.object(vault,'read_json',side_effect=AssertionError('Server downloaded history')):
            result=local.publish(self.gw,self.actor.id,pack(proof))
        self.assertEqual(result.status,'ready');self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class LocalBackfillPostgresTest(LocalBackfillTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture
        self.base=fixtures.PipelineTest();self.base.f=self.f;self.base.storage=self.storage
        self.addCleanup(self.base.doCleanups)
        self.setup_flow()

    storage=fixtures.PipelineTest.storage

    def test_two_simultaneous_identical_publications_are_idempotent(self):
        from concurrent.futures import ThreadPoolExecutor
        raw=self.package();actor_id=self.actor.id;db.session.remove()
        def publish(_):
            with self.f.app.app_context():
                try:return local.publish(self.gw,actor_id,raw).result_key
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:keys=list(pool.map(publish,range(2)))
        self.assertEqual(keys[0],keys[1]);self.assertEqual(Task.query.filter_by(kind='partition',status='ready').count(),1)
