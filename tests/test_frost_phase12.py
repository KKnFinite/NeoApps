"""Isolated, frozen prospective evidence. Never mutate production outcomes."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services import frost_forecast_native as native, frost_vault as vault, frost_weather as weather
from scripts import frost_prospective as study
from scripts.frost_forecast_native import sealed
from scripts.frost_local import write_json, read_json

NOW=datetime(2026,10,28,20,tzinfo=timezone.utc)


class ProspectiveTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.candidates=self.root/'candidates';self.reports=self.root/'research'
        self.frozen=self.root/'frozen'/'baseline.json';self.captures=self.root/'prospective'
        n=len(native.FEATURES)
        for h in native.HORIZONS:
            write_json(self.reports/f'rfd-native-h{h}-fixture-experimental-v1.json',sealed(dict(summary='original evaluation',
                runtime=dict(threshold=.5),eligibility=dict(seasons=['2020-2021','2021-2022','2022-2023','2023-2024','2024-2025','2025-2026']))))
            for kind in ('logistic','forest'):
                artifact=dict(schema='frost-native-experimental-candidate-v2',contract=native.contract(h),
                    policy='frost-native-exploratory-v2',target=native.TARGET,approved=False,operationally_validated=False,
                    physical_frost_probability=False,registry_compatible=False,model_version=f'h{h}-{kind}',
                    dataset_fingerprint='b'*64,evaluation=dict(count=200),training=dict(nights=280),
                    preprocessing=dict(means=[0]*n,scales=[1]*n),calibration=dict(kind='none'),
                    model=dict(kind=kind,intercept=0,coefficients=[0]*(2*n)) if kind=='logistic' else
                        dict(kind=kind,trees=[[dict(probability=.5)]]),
                    parity_checks=[dict(features=[None]*n,score=.5)])
                write_json(self.candidates/f'rfd-native-h{h}-fixture-experimental-v1-{kind}.json',sealed(artifact))
        with patch.object(study,'utcnow',return_value=NOW-timedelta(hours=1)):
            self.baseline=study.freeze(self.candidates,self.reports,self.frozen.parent)

    def source(self,root):
        obs=[dict(utc=(NOW-timedelta(hours=h)).isoformat(),received_at_utc=NOW.isoformat(),
            temperature_c=1,dewpoint_c=0,spread_c=1,relative_humidity=93,wind_kt=2,wind_direction_degrees=180,
            gust_kt=None,cloud_layers=['CLR'],ceiling_ft=None,clear_sky=True,visibility_sm=10,
            weather_codes='',precipitation_since_hourly_reset_in=None) for h in range(14)]
        fc=[dict(station=weather.STATION,cycle_at_utc=(NOW-timedelta(hours=2)).isoformat(),
            issued_at_utc=None,available_by_utc=(NOW-timedelta(hours=1)).isoformat(),
            valid_at_utc=(NOW+timedelta(hours=h)).isoformat(),availability_basis='NOAA_original_object_Last-Modified',
            source_sha256='a'*64,values=dict(temperature_c=0,dewpoint_c=-1,spread_c=1,relative_humidity=93,
            wind_kt=3,wind_direction_degrees=180,gust_kt=None,cloud_cover_percent=5,precipitation_1h_mm=0,weather_codes=''))
            for h in range(-1,7)]
        return dict(station=weather.STATION,observations=obs,forecasts=fc,receipts=[],errors=[])

    def capture(self,**kwargs):
        with patch.object(study,'utcnow',return_value=NOW):
            return study.capture(self.frozen,self.captures,provider=kwargs.pop('provider',self.source),**kwargs)

    def catalog(self,items):
        root=self.root/'outcomes';entries=[]
        for item in items:
            raw=vault.canonical(item);sha=vault.digest(raw);study.immutable(root/'objects'/(sha+'.json'),item)
            entries.append(dict(source_id=item['id'],sha256=sha,archived=True))
        write_json(root/'catalog.json',sealed(dict(schema=study.VERSION,observations=entries)))
        return root

    def inspection(self,**kwargs):
        return dict(dict(schema_version='frost-physical-inspection-v1',id='frost:inspection:1:0',tail='NREAL',
            evidence_active=True,observed_at_utc=(NOW+timedelta(hours=1,minutes=20)).isoformat(),
            result='absent',prior_pretreat=None),**kwargs)

    def test_exact_eight_artifacts_hashes_reports_and_thresholds_frozen(self):
        self.assertEqual(len(self.baseline['models']),8)
        self.assertFalse(self.baseline['approved'])
        self.assertEqual(study.freeze(self.candidates,self.reports,self.frozen.parent),self.baseline)
        entry=self.baseline['models'][0];path=self.frozen.parent/'artifacts'/(entry['sha256']+'.json')
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):study.baseline(self.frozen)

    def test_capture_all_horizons_no_outcome_selection_and_duplicate_restart(self):
        first=self.capture();self.assertEqual([p['hours'] for p in first['predictions']],[0,0,1,1,3,3,6,6])
        self.assertTrue(all(p['state']=='SCORED' for p in first['predictions']))
        self.assertTrue(all(p['score']==.5 and p['classification'] for p in first['predictions']))
        with patch.object(study,'acquire',side_effect=AssertionError('No duplicate network request')):
            self.assertEqual(first,self.capture())
        self.assertEqual(len(list((self.captures/'captures').glob('*.json'))),1)
        self.assertTrue((self.captures/'receipts'/(first['fingerprint']+'.json')).exists())

    def test_missing_stale_and_future_weather_unavailable_not_zero(self):
        def missing(root):
            value=self.source(root);value['observations']=[];return value
        first=self.capture(provider=missing)
        self.assertTrue(all(p['state']=='UNAVAILABLE' and p['score'] is None for p in first['predictions']))
        # Immutable failed horizons cannot later be filled using future evidence.
        self.assertEqual(first,self.capture())
        for transform in ('stale','future_receipt','missing_forecast'):
            def bad(root):
                value=self.source(root)
                if transform=='stale':value['observations']=value['observations'][2:]
                elif transform=='future_receipt':
                    for row in value['observations']:row['received_at_utc']=(NOW+timedelta(hours=1)).isoformat()
                else:value['forecasts']=[]
                return value
            with patch.object(study,'utcnow',return_value=NOW):
                value=study.capture(self.frozen,self.root/transform,provider=bad)
            self.assertTrue(all(p['state']=='UNAVAILABLE' for p in value['predictions']))

    def test_clock_station_local_only_guards_and_failed_journal(self):
        with patch.dict(os.environ,RENDER='true'):
            with self.assertRaises(RuntimeError):study.baseline(self.frozen)
            with self.assertRaises(RuntimeError):study.capture(self.frozen,self.captures)
            with self.assertRaises(RuntimeError):study.evaluate({},self.root)
        def wrong(root):return dict(self.source(root),station=dict(weather.STATION,icao='KORD'))
        with self.assertRaises(ValueError):self.capture(provider=wrong)
        self.assertEqual(len(list((self.captures/'attempts').glob('*.json'))),1)
        with patch.object(study,'utcnow',return_value=NOW-timedelta(days=1)):
            with self.assertRaises(ValueError):study.capture(self.frozen,self.captures,provider=self.source)

    def test_offline_gap_and_commit_receipt_recovery(self):
        first=self.capture()
        receipt=self.captures/'receipts'/(first['fingerprint']+'.json');receipt.unlink()
        with patch.object(study,'utcnow',return_value=NOW+timedelta(minutes=1)):
            study.capture(self.frozen,self.captures,provider=self.source)
        self.assertEqual(read_json(receipt)['committed_at_utc'],(NOW+timedelta(minutes=1)).isoformat())
        with patch.object(study,'utcnow',return_value=NOW+timedelta(hours=4)):
            later=study.capture(self.frozen,self.captures,provider=self.source)
        self.assertEqual(later['missed_intervals'],3)

    def test_optional_scheduler_generated_only_no_password_or_training(self):
        path=self.root/'task.xml';result=study.scheduler_xml(self.frozen,self.captures,path,user='WORKSTATION\\Researcher')
        text=path.read_text(encoding='utf-16')
        self.assertIn('NOT INSTALLED',result['state']);self.assertIn('InteractiveToken',text)
        self.assertIn('IgnoreNew',text);self.assertIn('PT3600S',text)
        self.assertIn('scripts.frost_shadow_collect',text);self.assertNotIn('Password',text)
        self.assertNotIn('Register-ScheduledTask',text)

    def test_matching_physical_target_separate_unknown_preserved_and_tail_owned(self):
        self.capture();items=[self.inspection(),self.inspection(id='second',tail='NSECOND',result='present'),
            dict(schema_version='subzero-spray/v1',record_type='actual_fluid_application',activity='departure',
                id='departure:1:pass:1',tail='NREAL',reason='Frost',start_utc=(NOW+timedelta(hours=1,minutes=30)).isoformat())]
        data=study.assemble(self.frozen,self.captures,self.catalog(items),self.root/'dataset')
        matched=[p for p in data['predictions'] if p['outcomes']]
        self.assertEqual(len(matched),2);self.assertTrue(all(p['hours']==1 for p in matched))
        self.assertEqual({e['tail'] for p in matched for e in p['outcomes']},{'NREAL','NSECOND'})
        report=study.evaluate(data,self.root/'evaluation')
        self.assertFalse(report['validation_eligible']);self.assertEqual(report['unknown_predictions'],6)
        physical=[r for r in report['metrics'] if r['cohort']=='physical_inspection']
        self.assertEqual(physical[0]['primary_independent_nights'],1)
        self.assertEqual(physical[0]['aircraft_cases'],2);self.assertIsNone(physical[0]['metrics']['brier'])
        treatment=[r for r in report['metrics'] if r['cohort']=='treatment_reason']
        self.assertEqual(treatment[0]['metrics']['positive'],1)

    def test_no_spray_pretreatment_ambiguous_clear_and_revisions(self):
        sample=dict(schema_version='frost-outcome-evidence-v1',id='x',tail='N1',evidence_active=True,
            observed_at_utc=NOW.isoformat(),learning_eligible=True,prior_pretreat=False,outcome='explicit_not_sprayed')
        self.assertEqual(study.outcome(sample,'a'*64)['label'],0)
        self.assertIsNone(study.outcome(dict(sample,prior_pretreat=None),'a'*64)['label'])
        self.assertTrue(study.outcome(dict(sample,prior_pretreat=True),'a'*64)['confounded'])
        self.assertIsNone(study.outcome(dict(sample,evidence_active=False),'a'*64))
        self.assertIsNone(study.outcome(dict(sample,schema_version='generic_clear'),'a'*64))
        self.capture();data=study.assemble(self.frozen,self.captures,self.catalog([
            self.inspection(id='old',evidence_active=False,result='present'),
            self.inspection(id='corrected',result='uncertain'),
            self.inspection(id='prevented',tail='NPRE',prior_pretreat=True)]),self.root/'dataset')
        report=study.evaluate(data,self.root/'evaluation');self.assertEqual(report['metrics'],[])

    def test_late_commits_cannot_match_earlier_events_and_hash_tampering_rejected(self):
        snapshot=self.capture();receipt=self.captures/'receipts'/(snapshot['fingerprint']+'.json')
        write_json(receipt,dict(snapshot=snapshot['fingerprint'],committed_at_utc=(NOW+timedelta(hours=2)).isoformat()))
        data=study.assemble(self.frozen,self.captures,self.catalog([self.inspection()]),self.root/'dataset')
        self.assertTrue(all(not p['outcomes'] for p in data['predictions']))
        path=next((self.captures/'captures').glob('*.json'));value=read_json(path);value['predictions'][0]['score']=0
        write_json(path,value)
        with self.assertRaisesRegex(ValueError,'fingerprint'):study.assemble(self.frozen,self.captures,self.root/'outcomes',self.root/'dataset')

    def test_dst_central_noon_night_and_midnight(self):
        self.assertEqual(study.night('2026-11-01T06:30:00Z'),study.night('2026-11-01T07:30:00Z'))
        self.assertEqual(study.night('2026-10-29T04:59:00Z'),study.night('2026-10-29T05:01:00Z'))

    def test_existing_canonical_archive_utc_fields_without_suffix(self):
        # Actual authenticated archive format: canonical DB DateTime is naive
        # UTC, and archive_payload names this field start_utc explicitly.
        payload=dict(schema_version='subzero-spray/v1',record_type='actual_fluid_application',activity='departure',
            id='departure:2:pass:1',tail='N460UP',start_utc='2026-10-28T08:27:00',reason=None)
        result=study.outcome(payload,'a'*64)
        self.assertEqual(result['at'],'2026-10-28T08:27:00+00:00')
        self.assertIsNone(result['label']);self.assertEqual(payload['start_utc'],'2026-10-28T08:27:00')
        # Unqualified inspection times must not acquire a guessed timezone.
        with self.assertRaises(ValueError):study.outcome(self.inspection(observed_at_utc='2026-10-28T08:27:00'),'a'*64)

    def test_raw_source_hash_and_retrieval_time_rechecked(self):
        raw=b'KRFD original report';digest=vault.digest(raw);path=self.root/'weather'/(digest+'.source')
        path.parent.mkdir();path.write_bytes(raw)
        source=dict(receipts=[dict(sha256=digest,bytes=len(raw),retrieved_at_utc=NOW.isoformat())],forecasts=[])
        study.verify_sources(self.root,source,NOW)
        path.write_bytes(b'changed original report')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):study.verify_sources(self.root,source,NOW)
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'after prediction cutoff'):study.verify_sources(self.root,source,NOW-timedelta(seconds=1))

    def test_research_summary_bounded_and_cannot_claim_approval(self):
        from app.services import frost_research
        original=frost_research.prospective_summary();self.assertEqual(len(original['models']),8)
        path=self.root/'summary.json';write_json(path,dict(original,approved=True))
        with patch.object(frost_research,'PROSPECTIVE_REPORT',path):
            with self.assertRaises(ValueError):frost_research.prospective_summary()


class ArchiveAccessTest(unittest.TestCase):
    def setUp(self):
        from tests import test_frost_phase10 as fixtures
        self.fixture=fixtures.InspectionTest();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.f

    def test_authenticated_bounded_export_permissions_memory_and_revision(self):
        from app.extensions import db
        from app.models import NeoSubZeroArchiveDelivery,Gateway
        from flask import g
        client=self.fixture.fixture.client('master');watcher=self.fixture.fixture.client('watcher')
        def get(client,url):
            # Fixture holds one application context across multiple identities.
            g.pop('_login_user',None)
            return client.get(url)
        payload=dict(schema_version='subzero-spray/v1',record_type='actual_fluid_application',id='departure:1:pass:1')
        sha=vault.digest(vault.canonical(payload))
        row=NeoSubZeroArchiveDelivery(gateway_id=self.f.gateway.id,operation_id=self.f.operation.id,source_id=payload['id'],
            content_checksum=sha,delivered_checksum=sha,vault_key='isolated/evidence')
        db.session.add(row);db.session.commit()
        url='/neosubzero/frost-vault/prospective-outcomes'
        self.assertEqual(get(watcher,url).status_code,403)
        self.assertEqual(get(watcher,url+'/'+str(row.id)).status_code,403)
        response=get(client,url);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['observations'][0]['sha256'],sha)
        self.assertIn('no-store',response.headers['Cache-Control'])
        for i in range(101):db.session.add(NeoSubZeroArchiveDelivery(gateway_id=self.f.gateway.id,
            operation_id=self.f.operation.id,source_id=f'fixture:page:{i}',content_checksum=sha))
        db.session.commit();page=get(client,url).json
        self.assertEqual(len(page['observations']),100);self.assertIsNotNone(page['next_before'])
        next_page=get(client,url+'?before='+str(page['next_before'])).json
        self.assertEqual(len(next_page['observations']),2)
        self.assertFalse({r['id'] for r in page['observations']} & {r['id'] for r in next_page['observations']})
        with patch('app.services.frost_history.memory_allows_work',return_value=False),patch('app.services.frost_vault.read_json') as read:
            self.assertEqual(get(client,url+'/'+str(row.id)).status_code,503);read.assert_not_called()
        with patch('app.services.frost_history.memory_allows_work',return_value=True),patch('app.services.frost_vault.read_json',return_value=payload):
            response=get(client,url+'/'+str(row.id));self.assertEqual(response.status_code,200);self.assertEqual(response.json,payload)
        other=Gateway(code='OTH',name='Other gateway');db.session.add(other);db.session.commit()
        row.gateway_id=other.id;db.session.commit()
        self.assertEqual(get(client,url+'/'+str(row.id)).status_code,404)


from tests import test_neosubzero_ucc_postgres as pg
@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PostgresArchiveAccessTest(ArchiveAccessTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        from tests import test_neosubzero_release2 as release2
        pg.UccPostgresTest.setUp(self);existing=self.fixture
        with patch.object(release2.fixtures,'NeoSubZeroUccTest',return_value=existing),patch.object(existing,'setUp'),patch.object(existing,'tearDown'):
            ArchiveAccessTest.setUp(self)


if __name__=='__main__':unittest.main()
