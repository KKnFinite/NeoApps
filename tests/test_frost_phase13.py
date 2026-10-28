"""Autonomous capture and late Xchange evidence, isolated SQLite/PostgreSQL."""
import copy
import io
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from flask import g
from app.extensions import db
from app.models import FrostHistoryTask as Task,NeoSubZeroSprayRecord,FrostModelArtifact,FrostModelRelease
from app.services import frost_auto as auto,frost_xchange_links as links,frost_history as jobs,frost_vault as vault
from tests.test_frost_phase12 import ArchiveAccessTest
from tests.test_frost_vault import MemoryR2,sample
from app.services import frost_documents as documents,frost_xchange as xchange,frost_schema


class AutoTest(ArchiveAccessTest):
    def setUp(self):
        super().setUp();self.store=MemoryR2()
        self.t=datetime.now(timezone.utc).replace(minute=20,second=0,microsecond=0)+timedelta(hours=1)
        self.time=patch.object(auto,'now',side_effect=lambda:self.t);self.time.start();self.addCleanup(self.time.stop)
        self.transport=patch('app.services.neoscorpion_learning_vault._r2_client',return_value=self.store)
        self.transport.start();self.addCleanup(self.transport.stop)
        self.configuration=patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'})
        self.configuration.start();self.addCleanup(self.configuration.stop)
        self.f.app.config.update(NEOSCORPION_LEARNING_VAULT_BUCKET='isolated',NEOSCORPION_LEARNING_VAULT_PROVIDER='r2',
            NEOSCORPION_LEARNING_VAULT_ENDPOINT='https://isolated.invalid',NEOSCORPION_LEARNING_VAULT_ACCESS_KEY_ID='x',
            NEOSCORPION_LEARNING_VAULT_SECRET_ACCESS_KEY='x',FROST_AUTO_COLLECTION=True)
        from app.services.frost_weather import STATION
        self.source=dict(station=STATION,cutoff_at_utc=self.t.isoformat(),observations=[],sources=[],nws_forecasts=[],native_forecasts=[])
        self.acquire=patch.object(auto,'acquire',side_effect=lambda:dict(self.source,cutoff_at_utc=self.t.isoformat()))
        self.provider=self.acquire.start();self.addCleanup(self.acquire.stop)

    def tick(self):return auto.tick(self.f.gateway.id)

    def model_fixture(self):
        from tests.test_frost_phase12 import ProspectiveTest
        helper=ProspectiveTest();helper.setUp();self.addCleanup(helper.doCleanups)
        self.enterContext(patch.object(auto,'frozen_models',return_value=helper.baseline['models']))
        return helper

    def test_hourly_without_sort_or_browser_restart_idempotency_and_unknown(self):
        # Existing canonical fixtures are irrelevant; collection never reads their sort.
        self.assertEqual(self.tick(),1);self.assertEqual(self.tick(),0);self.provider.assert_called_once()
        state=auto.status(self.f.gateway.id)
        self.assertEqual((state['archived'],state['scored'],state['unavailable'],state['awaiting_outcomes']),(1,0,8,1))
        row=Task.query.filter_by(kind='auto_capture').one();payload=vault.read_json(row.result_key,self.f.gateway.id)
        self.assertTrue(all(p['score'] is None and p['state']=='UNAVAILABLE' for p in payload['predictions']))
        self.assertFalse(payload['approved']);self.assertEqual(FrostModelRelease.query.count(),0)
        self.t+=timedelta(hours=4);self.assertEqual(self.tick(),1)
        self.assertEqual(auto.status(self.f.gateway.id)['missed'],3)
        self.assertEqual(Task.query.filter_by(kind='auto_capture').count(),2)

    def test_resource_suspension_does_not_fetch_or_backdate(self):
        with patch.object(jobs,'memory_allows_work',return_value=False):self.assertEqual(self.tick(),0)
        self.provider.assert_not_called();self.assertTrue(auto.status(self.f.gateway.id)['resource_suspended'])
        self.t+=timedelta(hours=1);self.assertEqual(self.tick(),1)
        self.assertEqual(Task.query.filter_by(kind='auto_capture',status='missed').count(),1)

    def test_provider_failures_bounded_retry_and_no_false_archive(self):
        self.provider.side_effect=TimeoutError
        for _ in range(3):self.tick();self.t+=timedelta(minutes=4)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.status,'failed');self.assertIsNone(row.result_key)
        self.assertEqual(auto.status(self.f.gateway.id)['archived'],0)

    def test_archive_outage_preserves_original_capture_and_restart_receipt(self):
        with patch.object(vault,'put_json',side_effect=RuntimeError('offline')):self.assertEqual(self.tick(),0)
        row=Task.query.filter_by(kind='auto_capture').one();payload=auto.unpack(row.summary['staged'])
        original=payload['committed_at_utc'];self.t+=timedelta(minutes=4)
        self.assertEqual(self.tick(),1);self.provider.assert_called_once()
        self.assertEqual(vault.read_json(row.result_key,self.f.gateway.id)['committed_at_utc'],original)

    def test_restart_after_staging_before_commit_receipt_uses_recovery_time(self):
        with patch.object(vault,'put_json',side_effect=RuntimeError('offline')):self.tick()
        row=Task.query.filter_by(kind='auto_capture').one();payload=auto.unpack(row.summary['staged'])
        payload['committed_at_utc']=None
        row.summary=dict(row.summary,staged=auto.pack(payload),committed_at_utc=None)
        row.input_hash=vault.digest(vault.canonical(payload));db.session.commit()
        self.t+=timedelta(hours=2)
        self.assertEqual(self.tick(),1);self.provider.assert_called_once()
        self.assertEqual(vault.read_json(row.result_key,self.f.gateway.id)['committed_at_utc'],self.t.isoformat())

    def queue_csv(self,**changes):
        raw=sample(ApplicationDate=self.t.astimezone(analysis_zone()).strftime('%m/%d/%Y'),
            StartTime1=self.t.astimezone(analysis_zone()).strftime('%H:%M'),**changes)
        document,_=documents.stage(self.f.gateway,self.fixture.actor,'late-export.csv',raw)
        db.session.commit();self.assertEqual(documents.drain(self.f.gateway.id),1)
        return document

    def test_month_late_csv_matches_actual_start_overlaps_and_no_operational_mutations(self):
        self.tick();self.t+=timedelta(hours=1,minutes=10)
        event=self.t
        day=event.astimezone(analysis_zone()).strftime('%m/%d/%Y');clock=event.astimezone(analysis_zone()).strftime('%H:%M')
        self.t+=timedelta(days=30)
        raw=sample(RecordID='late-actual',ApplicationDate=day,StartTime1=clock,Type4Used='',Reason='F')
        document,_=documents.stage(self.f.gateway,self.fixture.actor,'month.csv',raw);db.session.commit()
        self.assertEqual(documents.drain(self.f.gateway.id),1);self.assertEqual(links.drain(self.f.gateway.id),1)
        row=Task.query.filter_by(kind='xchange_outcome').one()
        self.assertEqual(row.status,'matched');self.assertEqual(row.summary['event_at_utc'],event.replace(second=0).isoformat())
        self.assertEqual(row.summary['classification']['label'],'LIKELY FROST PRESENT')
        self.assertEqual(Task.query.filter_by(kind='auto_match',status='active').count(),1)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        self.assertEqual(FrostModelArtifact.query.count(),0)
        # A distinct overlapping file preserves its own source without inflating outcomes.
        parsed=xchange.extract(raw,gateway_code='RFD');second=copy.deepcopy(parsed['rows'][0])
        second['source_row']=20
        item=auto.analysis.compact_row(second,document)
        links.link(self.f.gateway.id,item,'second-immutable-source');db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='xchange_outcome').count(),1)
        self.assertEqual(auto.status(self.f.gateway.id)['xchange_matches'],1)
        # No missing tail is ever inserted as a negative.
        self.assertEqual(auto.status(self.f.gateway.id)['outcome_records'],1)

    def test_post_event_capture_never_matches_and_conflicting_revisions_supersede(self):
        self.tick();row=Task.query.filter_by(kind='auto_capture').one()
        def item(at,sha,modified=None):
            return dict(source_record_id='revision',row_hash=sha,source=dict(document_id=1,source_row=2,document_sha256='a'*64),
                modified_utc=modified,tail='NREAL',classification=auto.analysis.classification('F'),requires_review=False,
                fluids=[dict(occurred_at_utc=at.isoformat())])
        links.link(self.f.gateway.id,item(self.t-timedelta(minutes=1),'a'*64),'page1');db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='xchange_outcome').one().status,'unmatched')
        links.link(self.f.gateway.id,item(self.t+timedelta(minutes=10),'b'*64),'page2');db.session.commit()
        self.assertEqual(Task.query.filter_by(kind='xchange_outcome').one().status,'requires_review')
        self.assertEqual(Task.query.filter_by(kind='auto_match',status='active').count(),0)

    def test_revision_index_agrees_with_existing_documented_latest_contract(self):
        def item(sha,modified):
            return dict(source_record_id='latest-contract',row_hash=sha,source=dict(document_id=1,source_row=2,document_sha256='a'*64),
                modified_utc=modified,tail='NREAL',classification=auto.analysis.classification('F'),requires_review=False,fluids=[])
        links.link(self.f.gateway.id,item('a'*64,None),'undated-page');db.session.commit()
        newer=item('b'*64,self.t.isoformat())
        links.link(self.f.gateway.id,newer,'dated-page');db.session.commit()
        row=Task.query.filter_by(kind='xchange_outcome').one()
        self.assertEqual(row.input_hash,'b'*64);self.assertFalse(row.summary['revision_conflict'])
        links.link(self.f.gateway.id,item('c'*64,None),'later-import-without-modification');db.session.commit()
        self.assertEqual(row.input_hash,'b'*64);self.assertFalse(row.summary['revision_conflict'])
        links.link(self.f.gateway.id,item('d'*64,self.t.isoformat()),'conflicting-same-modification');db.session.commit()
        self.assertTrue(row.summary['revision_conflict'])

    def test_exact_frozen_research_upload_authorization_and_no_approval(self):
        from pathlib import Path
        helper=self.model_fixture();entry=auto.frozen_models()[0]
        raw=(helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
        client=self.fixture.fixture.client('master');watcher=self.fixture.fixture.client('watcher')
        def post(client,raw):
            g.pop('_login_user',None)
            return client.post('/neosubzero/frost-vault/research-models',data=dict(model=(io.BytesIO(raw),'frozen.json')))
        self.assertEqual(post(watcher,raw).status_code,403)
        self.assertEqual(post(client,raw+b' ').status_code,400)
        self.assertEqual(post(client,raw).status_code,200);self.assertEqual(post(client,raw).status_code,200)
        self.assertEqual(Task.query.filter_by(kind='research_model').count(),1)
        self.assertEqual(FrostModelArtifact.query.count(),0);self.assertEqual(FrostModelRelease.query.count(),0)
        with patch.object(jobs,'memory_allows_work',return_value=False):self.assertEqual(post(client,raw).status_code,503)

    def test_authenticated_export_hash_memory_and_ui(self):
        self.tick();client=self.fixture.fixture.client('master');watcher=self.fixture.fixture.client('watcher')
        g.pop('_login_user',None);self.assertEqual(watcher.get('/neosubzero/frost-vault/auto-evidence').status_code,403)
        g.pop('_login_user',None);page=client.get('/neosubzero/frost-vault/auto-evidence').json
        entry=page['evidence'][0];response=client.get(entry['url']);self.assertEqual(response.status_code,200)
        self.assertEqual(vault.digest(vault.canonical(response.json)),entry['sha256'])
        self.assertIn('AUTO COLLECTION',client.get('/neosubzero/frost-vault').text)
        with patch.object(jobs,'memory_allows_work',return_value=False):self.assertEqual(client.get(entry['url']).status_code,503)

    def test_canonical_bootstrap_preserves_populated_receipts_and_is_idempotent(self):
        self.tick()
        with db.engine.begin() as connection:frost_schema.install(connection);frost_schema.install(connection)
        row=Task.query.filter_by(kind='auto_capture').one();row.input_hash='changed'
        with self.assertRaises(Exception):db.session.commit()
        db.session.rollback();self.assertEqual(Task.query.filter_by(kind='auto_capture',status='ready').count(),1)

    def test_registered_native_model_scores_only_compatible_pre_cutoff_features(self):
        from pathlib import Path
        from tests.test_frost_phase12 import ProspectiveTest,NOW
        helper=self.model_fixture()
        self.t=NOW
        source=helper.source(None);source.update(cutoff_at_utc=NOW.isoformat(),native_forecasts=source.pop('forecasts'))
        for entry in auto.frozen_models():
            raw=(helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
            auto.register_research(self.f.gateway.id,self.fixture.actor.id,raw)
        db.session.commit()
        result=auto.predictions(self.f.gateway.id,source)
        self.assertEqual(len(result),8);self.assertTrue(all(p['state']=='SCORED' for p in result))
        for row in source['native_forecasts']:row['available_by_utc']=(NOW+timedelta(hours=1)).isoformat()
        result=auto.predictions(self.f.gateway.id,source)
        self.assertTrue(all(p['state']=='UNAVAILABLE' for p in result))

    def test_local_incremental_download_and_existing_evaluator_adapter(self):
        import tempfile
        from pathlib import Path
        from scripts import frost_prospective as study
        helper=self.model_fixture()
        self.enterContext(patch.object(auto.frost_research,'prospective_summary',return_value=dict(baseline=helper.baseline['fingerprint'],models=helper.baseline['models'])))
        self.tick();entries=[];payloads={}
        for row in Task.query.filter_by(kind='auto_capture',status='ready').all():
            url='/neosubzero/frost-vault/auto-evidence/'+str(row.id)
            entries.append(dict(id=row.id,kind=row.kind,identity=row.identity,sha256=row.input_hash,url=url))
            payloads[url]=vault.canonical(vault.read_json(row.result_key,self.f.gateway.id))
        class Remote:
            downloads=0
            def request(this,method,url):
                if url.endswith('auto-evidence'):return json.dumps(dict(schema=auto.VERSION,evidence=entries,next_before=None)).encode()
                this.downloads+=1;return payloads[url]
        remote=Remote()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);study.refresh_auto(remote,root);study.refresh_auto(remote,root)
            self.assertEqual(remote.downloads,1)
            data=study.assemble_auto(helper.frozen,root,root/'analysis')
            self.assertEqual(len(data['predictions']),8);self.assertEqual(data['unique_outcomes'],0)
            self.assertTrue(all(not p['outcomes'] and p['score'] is None for p in data['predictions']))


def analysis_zone():return auto.analysis.ZONE


class AcquisitionTest(unittest.TestCase):
    def test_actual_provider_contract_hashes_partial_failure_and_wrong_station(self):
        at=datetime.now(timezone.utc)
        metars=[dict(icaoId='KRFD',reportTime=(at-timedelta(minutes=10)).isoformat(),temp=1,dewp=0,wspd=3,
            wdir=180,clouds=[dict(cover='VV',base=1400)],rawOb='KRFD source')]
        def quantity(unit,value):return dict(uom=unit,values=[dict(validTime=at.isoformat()+'/PT12H',value=value)])
        grid=dict(geometry=dict(type='Polygon',coordinates=[[[-89.10,42.18],[-89.07,42.18],[-89.07,42.21],[-89.10,42.21],[-89.10,42.18]]]),
            properties=dict(updateTime=(at-timedelta(minutes=5)).isoformat(),temperature=quantity('wmoUnit:degC',1),dewpoint=quantity('wmoUnit:degC',0)))
        points=dict(geometry=dict(coordinates=[-89.0876,42.1915]),properties=dict(forecastGridData='https://api.weather.gov/gridpoints/LOT/25,85'))
        def fetch(url,maximum):
            value=metars if 'aviationweather' in url else points if '/points/' in url else grid
            raw=vault.canonical(value)
            return raw,dict(url=url,retrieved_at_utc=at.isoformat(),sha256=vault.digest(raw),bytes=len(raw))
        with patch.object(auto,'now',return_value=at),patch.object(auto,'fetch',side_effect=fetch):
            source=auto.acquire()
        self.assertEqual(len(source['nws_forecasts']),4);self.assertEqual(source['observations'][0]['ceiling_ft'],1400)
        self.assertEqual(source['native_forecasts'],[])
        import base64
        for item in source['sources']:self.assertEqual(vault.digest(base64.b64decode(item['raw_base64'])),item['sha256'])
        with patch.object(auto,'now',return_value=at),patch.object(auto,'fetch',side_effect=[fetch('aviationweather',1),TimeoutError()]):
            source=auto.acquire()
        self.assertEqual(len(source['sources']),1);self.assertTrue(source['errors']);self.assertEqual(source['nws_forecasts'],[])
        metars[0]['icaoId']='KORD'
        with patch.object(auto,'fetch',side_effect=fetch):
            with self.assertRaises(ValueError):auto.acquire()

    def test_disallowed_weather_url_and_bounded_staging(self):
        with patch.object(auto,'urlopen') as network:
            with self.assertRaises(ValueError):auto.fetch('http://127.0.0.1/private',100)
            network.assert_not_called()
        with self.assertRaises(ValueError):auto.pack(dict(raw='x'*(auto.MAX_SNAPSHOT+1)))
        with self.assertRaises(ValueError):auto.unpack('not base64')
        with self.assertRaises(ValueError):auto.unpack(auto.pack(dict(value='ok'))[:-4])


from tests import test_neosubzero_ucc_postgres as pg
@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PostgresAutoTest(AutoTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        from tests import test_neosubzero_release2 as release2
        pg.UccPostgresTest.setUp(self);existing=self.fixture
        with patch.object(release2.fixtures,'NeoSubZeroUccTest',return_value=existing),patch.object(existing,'setUp'),patch.object(existing,'tearDown'):
            AutoTest.setUp(self)


if __name__=='__main__':unittest.main()
