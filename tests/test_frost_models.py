"""Synthetic/isolated proofs only; never production historical model training."""
import copy
import io
import json
import os
import tempfile
import tracemalloc
import unittest
import importlib.util
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from flask import g
from sqlalchemy import text,inspect

from app.extensions import db
from app.models import FrostModelArtifact,FrostModelRelease,FrostHistoryTask,NeoSubZeroSprayRecord
from app.services import frost_analysis as analysis,frost_model as model,frost_vault as vault,frost_model_registry as registry,frost_inference as inference
from scripts import frost_local as local
from tests import test_frost_history as history_tests,test_frost_vault as contracts,test_neosubzero_ucc_postgres as pg

UTC=timezone.utc
SKLEARN_AVAILABLE=importlib.util.find_spec('sklearn') is not None


def synthetic():
    examples=[]
    seasons=[f'{year}-{year+1}' for year in range(2018,2023)]
    for season in seasons:
        for index in range(120):
            label=index%2
            values=[None]*len(model.FEATURES)
            values[:5]=[-1,-2,1,95,1] if label else [10,-1,11,45,12]
            examples.append(dict(group_id=season+str(index),season=season,night=(datetime(int(season[:4])+1,1,1)+timedelta(days=index)).date().isoformat(),
                at=(datetime(int(season[:4])+1,1,1,tzinfo=UTC)+timedelta(days=index,hours=6)).isoformat(),label=label,features=values,sources=[dict(document_id=1,source_row=index+2)]))
    manifest=dict(schema='frost-local-dataset-v1',version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,
        source_origin='authenticated_frost_vault',ready=True,state='DATA READY',
        catalog=dict(deduplicated_records=len(examples),seasons=seasons),partitions=[],totals={},errors=[])
    manifest['fingerprint']=vault.digest(vault.canonical(manifest))
    return dict(schema=local.PIPELINE_VERSION,feature_version=model.FEATURE_VERSION,manifest=manifest,
        examples=examples,source_records=len(examples),excluded={})


_candidate=None
def candidate():
    global _candidate
    if _candidate is None:
        if SKLEARN_AVAILABLE:
            _candidate=local.train(synthetic(),'fixture-v1')
        else:
            # Server-side tests must run without optional workstation tooling.
            # A frozen numerical fixture is not a trained production artifact.
            data=synthetic();seasons=data['manifest']['catalog']['seasons']
            numerical=dict(model=dict(kind='logistic',coefficients=[-2.0]+[0.0]*(2*len(model.FEATURES)-1),intercept=0.0),
                preprocessing=dict(means=[0.0]*len(model.FEATURES),scales=[1.0]*len(model.FEATURES)),calibration=dict(kind='none'))
            sample=data['examples'][-120:];scores=[model.score(numerical,r['features']) for r in sample]
            final=local.metrics([r['label'] for r in sample],scores);final.update(climatology_brier=.25,weather_brier=.04)
            _candidate=dict(schema=model.VERSION,feature_version=model.FEATURE_VERSION,features=list(model.FEATURES),feature_definitions=model.FEATURE_DEFINITIONS,
                model_version='fixture-v1',target=model.TARGET,physical_frost_probability=False,
                dataset={k:data['manifest'][k] for k in ('version','time_contract','fingerprint','source_origin')},classification_version=analysis.VERSION,
                seasons=seasons,trained_at_utc='2026-01-01T00:00:00+00:00',runtime=dict(python_min='3.11',format='bounded_json',training_dependencies=False),
                limitations=list(model.LIMITATIONS),forecast_validated=False,parking_adjustments=None,
                training=dict(examples=600,source_records=600,pipeline='frozen-unit-fixture'),
                evaluation=dict(split='forward_seasons_calibration_then_untouched_test',seasons=seasons,fit_seasons=seasons[:-2],calibration_season=seasons[-2],test_season=seasons[-1],test=final,lead_hours_validated=[0]),
                parity_checks=[dict(features=r['features'],score=p) for r,p in zip(sample[:2],scores[:2])],**numerical)
    return copy.deepcopy(_candidate)


class ModelTest(unittest.TestCase):
    @unittest.skipUnless(SKLEARN_AVAILABLE,'Install tests/requirements-frost-local.txt on the workstation')
    def test_reproducible_local_training_calibration_and_untouched_seasons(self):
        first=candidate();second=local.train(synthetic(),'fixture-v2')
        self.assertEqual(first['model'],second['model']);self.assertEqual(first['calibration'],second['calibration'])
        self.assertTrue(all(model.quality_gates(first['evaluation']).values()))
        self.assertNotIn(first['evaluation']['test_season'],first['evaluation']['fit_seasons'])
        self.assertNotIn(first['evaluation']['calibration_season'],first['evaluation']['fit_seasons'])
        self.assertLess(len(vault.canonical(first)),model.MAX_BYTES)

    def test_season_split_purges_preceding_day_and_never_randomly_splits_nights(self):
        data=synthetic();seasons=data['manifest']['catalog']['seasons']
        fit,test=local.forward_split(data['examples'],seasons[:-1],seasons[-1])
        self.assertFalse({r['night'] for r in fit}&{r['night'] for r in test})
        self.assertLess(max(datetime.fromisoformat(r['at']) for r in fit),min(datetime.fromisoformat(r['at']) for r in test)-timedelta(hours=24))

    def test_render_and_web_request_training_is_rejected_before_fitting(self):
        with patch.dict(os.environ,{'RENDER':'true'}):
            with self.assertRaises(RuntimeError):local.train(synthetic(),'forbidden')
            with self.assertRaises(RuntimeError):local.prepare('not-opened')
        from flask import Flask
        with Flask(__name__).test_request_context():
            with self.assertRaises(RuntimeError):local.fit([], 'logistic')

    def test_no_training_import_or_execution_path_in_application(self):
        for path in (Path('app/services/frost_model.py'),Path('app/services/frost_inference.py'),Path('app/services/frost_model_registry.py')):
            source=path.read_text()
            for forbidden in ('import sklearn','import scripts.frost_local','from scripts','pickle.load','joblib.load','.fit('):self.assertNotIn(forbidden,source)
        self.assertNotIn('scikit-learn',Path('requirements.txt').read_text())

    def test_classification_is_conditional_treatment_not_physical_negatives(self):
        for reason,wanted in [('F',1),('SN',0),('FZRA',0),('P',None),('',None)]:
            row=history_tests.compact(Reason=reason)
            self.assertEqual(model.training_label(row),wanted)
        row=history_tests.compact(Reason='F');row['classification']=analysis.classification(None,actual_spray=False,verified_not_sprayed=True,prior_pretreat=False)
        self.assertIsNone(model.training_label(row))

    def test_feature_parity_units_missingness_and_no_future_weather(self):
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=2);at=start+timedelta(hours=16)
        observations=history_tests.weather.parse(history_tests.weather_csv(start,end),start,end)
        features=analysis.features(at.isoformat(),observations)
        self.assertEqual(model.vector(features),model.from_observations(at.isoformat(),observations))
        self.assertAlmostEqual(model.vector(features)[0],-1.11111111111)
        self.assertIsNone(model.vector(features)[5])
        artifact=candidate();self.assertTrue(0<=model.score(artifact,model.vector(features))<=1)
        future=copy.deepcopy(observations)
        for row in future:
            if datetime.fromisoformat(row['utc'])>at:row['temperature_c']=999
        self.assertEqual(model.from_observations(at.isoformat(),future),model.vector(features))

    def test_artifact_rejects_hash_tampering_nonfinite_dimensions_and_untrusted_formats(self):
        good=candidate();raw=vault.canonical(good)
        self.assertEqual(model.decode(raw,vault.digest(raw))['model_version'],'fixture-v1')
        for bad in (b'not json',b'\x80pickle',raw+b' ',b'{"a":NaN}',b'{"a":1,"a":2}',b'x'*(model.MAX_BYTES+1)):
            with self.assertRaises(ValueError):model.decode(bad,vault.digest(raw))
        for mutate in (lambda a:a.update(physical_frost_probability=True),lambda a:a['preprocessing']['scales'].__setitem__(0,0),
                lambda a:a['evaluation'].update(test_season=a['evaluation']['fit_seasons'][0]),lambda a:a.update(forecast_validated=True)):
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):model.decode(vault.canonical(bad))

    @unittest.skipUnless(SKLEARN_AVAILABLE,'Install tests/requirements-frost-local.txt on the workstation')
    def test_forest_export_matches_estimator_and_rejects_cycles(self):
        rows=synthetic()['examples'];forest=local.fit(rows,'forest')
        for row in rows[:10]:self.assertEqual(round(model.score(forest,row['features'])),row['label'])
        artifact=candidate();artifact.update(forest)
        model.decode(vault.canonical(artifact))
        artifact['model']['trees'][0][0]['left']=0
        with self.assertRaises(ValueError):model.decode(vault.canonical(artifact))

    def test_forecast_issue_time_is_distinct_and_future_domain_is_not_validated(self):
        at=datetime(2025,1,1,tzinfo=UTC)
        with self.assertRaises(ValueError):inference.forecast_features([],[],issued_at=at+timedelta(hours=1),as_of=at,target=at+timedelta(hours=2))
        self.assertFalse(candidate()['forecast_validated'])

    def test_current_observation_station_units_stale_cache_and_bounded_reuse(self):
        from flask import Flask
        from app.services import neosubzero_weather as weather
        at=datetime(2025,1,1,tzinfo=UTC)
        rows=[dict(icaoId='KRFD',reportTime=at.isoformat(),temp=-1,dewp=-2,wspd=5,wdir=180,wgst=None,
            clouds=[dict(cover='BKN',base=2000)],visib='10',rawOb='KRFD fixture')]
        weather.clear_neosubzero_weather_cache()
        with Flask(__name__).app_context(),patch.object(weather,'_request_json',return_value=rows) as fetch:
            first=inference.current_observations(at);second=inference.current_observations(at+timedelta(seconds=10))
            self.assertEqual(first,second);self.assertEqual(fetch.call_count,1)
            self.assertEqual(fetch.call_args.kwargs['max_bytes'],512*1024)
            self.assertEqual(first[0]['temperature_c'],-1);self.assertEqual(first[0]['wind_kt'],5)
            self.assertEqual(first[0]['ceiling_ft'],2000);self.assertFalse(first[0]['clear_sky'])
            with patch.object(weather,'_request_json',side_effect=RuntimeError('network')):
                with self.assertRaisesRegex(ValueError,'stale'):inference.current_observations(at+timedelta(minutes=6))
        wrong=copy.deepcopy(rows);wrong[0]['icaoId']='KORD'
        with self.assertRaisesRegex(ValueError,'station'):inference.normalize_metars(wrong)
        weather.clear_neosubzero_weather_cache()

    def test_incremental_download_reuses_hashes_and_rejects_changed_content(self):
        payload=dict(schema_version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,records=[])
        raw=vault.canonical(payload);digest=vault.digest(raw)
        manifest=synthetic()['manifest'];manifest['partitions']=[dict(id=1,month='2025-01',status='ready',sha256=digest,url='/neosubzero/frost-vault/history/1',records=0)]
        manifest.pop('fingerprint');manifest['fingerprint']=vault.digest(vault.canonical(manifest))
        class Remote:
            calls=0
            def request(self,method,path):
                if path.endswith('dataset-manifest'):return vault.canonical(dict(manifest=manifest))
                self.calls+=1;return raw
        remote=Remote()
        with tempfile.TemporaryDirectory() as directory:
            local.refresh(remote,directory);local.refresh(remote,directory)
            self.assertEqual(remote.calls,1)
            (Path(directory)/'objects'/(digest+'.json')).write_bytes(b'corrupt')
            local.refresh(remote,directory);self.assertEqual(remote.calls,2)
            self.assertEqual((Path(directory)/'objects'/(digest+'.json')).read_bytes(),raw)

    def test_phase5_incomplete_blocks_preparation_and_training(self):
        data=synthetic();data['manifest']['ready']=False;data['manifest']['state']='WEATHER INCOMPLETE'
        data['manifest'].pop('fingerprint');data['manifest']['fingerprint']=vault.digest(vault.canonical(data['manifest']))
        with tempfile.TemporaryDirectory() as directory:
            local.write_json(Path(directory)/'manifest.json',data['manifest'])
            with self.assertRaisesRegex(ValueError,'Phase 5'):local.prepare(directory)
        with patch.dict('sys.modules',{'sklearn':None}):
            with self.assertRaisesRegex(ValueError,'Phase 5'):local.train(data,'blocked')

    def test_prepare_deduplicates_aircraft_truck_contributions_and_recomputes_lead_features(self):
        start=datetime(2024,12,31,tzinfo=UTC);end=datetime(2025,1,4,tzinfo=UTC)
        observed=history_tests.weather.parse(history_tests.weather_csv(start,end),start,end)
        rows,_=analysis.deduplicate([history_tests.compact(ApplicationDate='1/02/2025',Reason='F'),
            history_tests.compact(ApplicationDate='1/02/2025',Reason='F',RecordID='second-truck',TruckNumber='other')])
        payload=analysis.analyze(rows,observed);raw=vault.canonical(payload);digest=vault.digest(raw)
        weather_payload=dict(schema_version=history_tests.weather.VERSION,station=history_tests.weather.STATION,observations=observed)
        weather_sha=vault.digest(vault.canonical(weather_payload))
        manifest=synthetic()['manifest'];manifest.update(catalog=dict(deduplicated_records=2),
            partitions=[dict(id=1,month='2025-01',status='ready',sha256=digest,url='/neosubzero/frost-vault/history/1',records=2)],
            weather=[dict(month='2025-01',sha256=weather_sha)])
        manifest.pop('fingerprint');manifest['fingerprint']=vault.digest(vault.canonical(manifest))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);local.write_json(root/'manifest.json',manifest)
            local.write_json(root/'objects'/(digest+'.json'),payload);local.write_json(root/'objects'/(weather_sha+'.json'),weather_payload)
            present=local.prepare(root);ahead=local.prepare(root,lead_hours=1)
            self.assertEqual(len(ahead['examples']),1);self.assertEqual(len(ahead['examples'][0]['sources']),2)
            self.assertEqual(datetime.fromisoformat(present['examples'][0]['at'])-datetime.fromisoformat(ahead['examples'][0]['at']),timedelta(hours=1))
            expected=model.from_observations(ahead['examples'][0]['at'],observed)
            self.assertEqual(ahead['examples'][0]['features'],expected)


class RegistryTest(unittest.TestCase):
    storage=contracts.FrostVaultTest.storage
    def setUp(self):
        self.base=history_tests.PipelineTest();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.f=self.base.f;self.setup_registry()

    def setup_registry(self):
        self.storage();self.actor=self.f._user('model-admin','master');self.gw=self.f.gateway.id
        self.manifest=synthetic()['manifest']
        self.enterContext(patch.object(registry,'dataset_manifest',return_value=self.manifest))

    def register(self,version='fixture-v1'):
        artifact=candidate();artifact['model_version']=version
        return registry.register(self.gw,self.actor.id,vault.canonical(artifact))

    def test_candidate_is_not_approved_permissions_and_manifest_auth(self):
        row=self.register();self.assertEqual(row.status,'validated');self.assertIsNone(registry.status(self.gw)['approved_version'])
        with patch.object(vault,'read_json',side_effect=AssertionError('No approved artifact must be fetched')):
            self.assertEqual(inference.predict(self.gw)['state'],'NO APPROVED MODEL')
        admin=self.base.client('master');watcher=self.base.client('watcher')
        for path in ('/neosubzero/frost-vault/models','/neosubzero/frost-vault/dataset-manifest'):
            g.pop('_login_user',None);self.assertEqual(watcher.get(path).status_code,403)
            g.pop('_login_user',None);self.assertEqual(admin.get(path).status_code,200)
        g.pop('_login_user',None);self.assertEqual(watcher.post('/neosubzero/frost-vault/models',data={'action':'approve'}).status_code,403)

    def test_explicit_approval_stale_rejection_rollback_and_no_spray_writes(self):
        first=self.register();release=registry.release(self.gw,self.actor.id,first.id,0,'Isolated approval')
        with self.assertRaisesRegex(ValueError,'changed'):registry.release(self.gw,self.actor.id,first.id,0,'Stale approval')
        second=self.register('fixture-v2')
        with self.assertRaisesRegex(ValueError,'previously approved'):registry.release(self.gw,self.actor.id,second.id,release.id,'Not a previous model',rollback=True)
        release2=registry.release(self.gw,self.actor.id,second.id,release.id,'Second isolated approval')
        release3=registry.release(self.gw,self.actor.id,first.id,release2.id,'Audited rollback',rollback=True)
        self.assertEqual(registry.status(self.gw)['approved_version'],first.model_version)
        self.assertEqual(FrostModelRelease.query.count(),3);self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        self.assertEqual(release3.action,'rollback')

    def test_new_dataset_blocks_approval_not_existing_model_and_duplicate_upload_is_idempotent(self):
        row=self.register();self.assertEqual(self.register().id,row.id)
        self.manifest['ready']=False
        with self.assertRaisesRegex(ValueError,'gate'):registry.release(self.gw,self.actor.id,row.id,0,'Blocked')
        self.assertEqual(FrostModelRelease.query.count(),0)
        self.manifest['ready']=True
        registry.release(self.gw,self.actor.id,row.id,0,'Previously validated model')
        self.manifest['ready']=False
        self.assertEqual(registry.status(self.gw)['approved_version'],row.model_version)
        bad=candidate();bad['limitations'].append('Different documented review note')
        with self.assertRaisesRegex(ValueError,'different'):registry.register(self.gw,self.actor.id,vault.canonical(bad))

    def test_lightweight_inference_no_history_loading_cache_restart_missing_and_future_weather(self):
        row=self.register();registry.release(self.gw,self.actor.id,row.id,0,'Inference fixture')
        start=datetime(2025,1,1,tzinfo=UTC);end=start+timedelta(days=2);at=start+timedelta(hours=16)
        observed=history_tests.weather.parse(history_tests.weather_csv(start,end),start,end)
        read=vault.read_json
        with patch.object(vault,'read_json',wraps=read) as download,patch('app.services.frost_history.health',side_effect=AssertionError('Historical data accessed during prediction')):
            result=inference.predict(self.gw,now=at,observations=observed)
            self.assertTrue(result['enabled']);self.assertFalse(result['physical_frost_probability'])
            self.assertTrue(all(v['score'] is None for v in result['future_windows']))
            for i in range(45):inference.predict(self.gw,now=at+timedelta(minutes=i),observations=observed)
            self.assertEqual(download.call_count,1)
            self.assertLessEqual(len(self.f.app.extensions['frost_inference']['predictions']),32)
            self.f.app.extensions.pop('frost_inference')
            inference.predict(self.gw,now=at,observations=observed);self.assertEqual(download.call_count,2)
        self.assertEqual(inference.predict(self.gw,now=at,observations=[])['state'],'WEATHER INCOMPLETE')

    def test_storage_failure_never_marks_candidate_or_prediction_archived(self):
        with patch.object(vault,'put_json',side_effect=RuntimeError('fixture archive outage')):
            with self.assertRaises(RuntimeError):self.register()
        self.assertEqual(FrostModelArtifact.query.count(),0)

    def test_synthetic_benchmark_artifacts_cannot_be_registered(self):
        artifact=candidate();artifact['training']['test_fixture']=True
        with self.assertRaisesRegex(ValueError,'fixtures'):registry.register(self.gw,self.actor.id,vault.canonical(artifact))
        self.assertEqual(FrostModelArtifact.query.count(),0)

    def test_sqlite_or_postgres_additive_upgrade_and_append_only_model_history(self):
        from app.services.schema_sync import sync_database_schema
        from app.services.frost_schema import verify
        db.session.execute(text('DROP TABLE frost_model_releases'));db.session.execute(text('DROP TABLE frost_model_artifacts'));db.session.commit()
        sync_database_schema(self.f.app);db.session.commit()
        sync_database_schema(self.f.app);db.session.commit();verify(db.session.connection())
        row=self.register();release=registry.release(self.gw,self.actor.id,row.id,0,'Protected approval')
        self.assertEqual(registry.release(self.gw,self.actor.id,row.id,0,'Protected approval').id,release.id)
        with self.assertRaises(Exception):
            db.session.execute(text("UPDATE frost_model_artifacts SET model_version='changed'"));db.session.commit()
        db.session.rollback();self.assertEqual(FrostModelRelease.query.count(),1)

    def test_prediction_archive_failure_is_visible_and_retry_is_content_idempotent(self):
        row=self.register();registry.release(self.gw,self.actor.id,row.id,0,'Archive fixture')
        start=datetime(2025,1,1,tzinfo=UTC);at=start+timedelta(hours=16)
        observed=history_tests.weather.parse(history_tests.weather_csv(start,start+timedelta(days=2)),start,start+timedelta(days=2))
        with patch.object(vault,'put_json',side_effect=RuntimeError('Archive unavailable')):
            with self.assertRaises(RuntimeError):inference.predict(self.gw,now=at,observations=observed,record=True)
        self.assertIsNone(FrostHistoryTask.query.filter_by(kind='forecast-status').first())
        first=inference.predict(self.gw,now=at,observations=observed,record=True)
        second=inference.predict(self.gw,now=at,observations=observed,record=True)
        self.assertEqual(first['evidence_key'],second['evidence_key'])
        self.assertEqual(FrostHistoryTask.query.filter_by(kind='forecast-status').count(),1)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class RegistryPostgresTest(RegistryTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture
        self.base=history_tests.PipelineTest();self.base.f=self.f
        self.setup_registry()

    def test_concurrent_approval_has_one_winner(self):
        row=self.register();identifier,actor_id=row.id,self.actor.id;db.session.remove()
        def worker(_):
            with self.f.app.app_context():
                try:registry.release(self.gw,actor_id,identifier,0,'Concurrent fixture '+str(_));return 'saved'
                except ValueError:return 'conflict'
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,range(2)))
        self.assertCountEqual(results,['saved','conflict']);self.assertEqual(FrostModelRelease.query.count(),1)

    def test_populated_old_schema_upgrade_is_repeatable_and_guards_history(self):
        from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table,verify_neosubzero_schema_contract
        from app.services.frost_schema import install
        before=NeoSubZeroSprayRecord.query.count()
        db.session.execute(text('DROP TABLE frost_model_releases'));db.session.execute(text('DROP TABLE frost_model_artifacts'));db.session.commit()
        with patch.dict(self.f.app.config,{'TESTING':False}):
            self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app));self.assertTrue(ensure_neosubzero_pretreat_table(self.f.app))
        verify_neosubzero_schema_contract(db.session.connection())
        row=self.register();registry.release(self.gw,self.actor.id,row.id,0,'Immutable fixture')
        with self.assertRaises(Exception):db.session.execute(text("UPDATE frost_model_releases SET reason='rewritten'"));db.session.commit()
        db.session.rollback();self.assertEqual(NeoSubZeroSprayRecord.query.count(),before)
