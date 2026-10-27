"""Versioned weather correction; old evidence must remain replayable."""
import csv
import io
import unittest
import copy
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from app.services import frost_weather as weather
from app.services import frost_model as model,frost_dataset_contract as contract,frost_vault as vault
from scripts import frost_hrrr as hrrr,frost_shadow_collect as collector,frost_weather_correction as correction


class VisibilityTest(unittest.TestCase):
    def parse(self, code='VV ', height='1400', metar='VV014', **layers):
        at=datetime(2025,1,1,tzinfo=timezone.utc)
        row=dict(station='RFD',valid='2025-01-01 00:00',lat='42.19151',lon='-89.08760',
            tmpf='30',dwpf='29',skyc1=code,skyl1=height,metar='KRFD 010000Z 00000KT 1SM '+metar+' M01/M02')
        row.update(layers)
        out=io.StringIO();writer=csv.DictWriter(out,fieldnames=list(row));writer.writeheader();writer.writerow(row)
        return out.getvalue().encode(),at,at+timedelta(days=1)

    def test_vv014_and_whitespace(self):
        for code,token in [('VV ','VV014'),(' VV\t','VV014'),('VV','VV 014')]:
            raw,start,end=self.parse(code=code,metar=token)
            row=weather.parse(raw,start,end)[0]
            self.assertEqual(row['cloud_layers'],['VV'])
            self.assertEqual(row['ceiling_ft'],1400)
            self.assertIn(token,row['raw_metar'])

    def test_unknown_and_encoded_heights(self):
        for token,expected in [('VV000',0),('VV002',200),('VV100',10000),('VV///',None)]:
            raw,start,end=self.parse(height='M',metar=token)
            self.assertEqual(weather.parse(raw,start,end)[0]['ceiling_ft'],expected)
        raw,start,end=self.parse(code='M',height='M',metar='SKC')
        self.assertIsNone(weather.parse(raw,start,end)[0]['ceiling_ft'])

    def test_ordinary_multiple_layers_and_no_remark_fabrication(self):
        raw,start,end=self.parse(code='FEW ',height='500',metar='FEW005 BKN020 OVC040 RMK VV001',
            skyc2=' BKN ',skyl2='2000',skyc3='OVC',skyl3='4000')
        row=weather.parse(raw,start,end)[0]
        self.assertEqual(row['ceiling_ft'],2000)
        self.assertEqual(row['cloud_layers'],['FEW','BKN','OVC'])

    def test_old_version_replays_without_silently_correcting_it(self):
        raw,start,end=self.parse()
        old=weather.parse(raw,start,end,version='iem-rfd-routine-special-v1')[0]
        self.assertEqual(old['cloud_layers'],['VV ']);self.assertIsNone(old['ceiling_ft'])
        self.assertNotEqual(weather.VERSION,'iem-rfd-routine-special-v1')
        with self.assertRaises(ValueError):weather.parse(raw,start,end,version='invented')


class VersionSafetyTest(unittest.TestCase):
    def test_old_model_cannot_silently_use_corrected_production_inputs(self):
        from app.services import frost_inference as inference
        from types import SimpleNamespace
        with patch.object(inference,'approved',return_value=(SimpleNamespace(model_version='legacy'),dict(feature_version=model.LEGACY_FEATURE_VERSION))),patch.object(inference,'current_observations',side_effect=AssertionError('Unvalidated weather access')):
            result=inference.predict(1)
        self.assertFalse(result['enabled']);self.assertEqual(result['state'],'WEATHER CONTRACT REVALIDATION REQUIRED')

    def test_assembly_cannot_relabel_a_legacy_cache_as_corrected(self):
        from scripts.frost_dataset import assemble
        from scripts.frost_local import write_json
        with tempfile.TemporaryDirectory() as folder:
            write_json(Path(folder)/'manifest.json',dict(weather_version=weather.LEGACY_VERSION))
            with self.assertRaisesRegex(ValueError,'Preserve the legacy dataset'):
                assemble(folder,{},'not-opened')

    def test_legacy_remote_refresh_preserves_version_but_does_not_enable_new_training(self):
        from scripts import frost_local as local
        from types import SimpleNamespace
        from tests.test_frost_models import synthetic
        from app.services import frost_analysis as analysis
        manifest=synthetic()['manifest'];manifest.update(version='frost-history-v2')
        payload=dict(schema_version=weather.LEGACY_VERSION,station=weather.STATION,observations=[])
        raw=vault.canonical(payload);sha=vault.digest(raw)
        manifest['weather']=[dict(status='ready',month='2025-01',version=weather.LEGACY_VERSION,sha256=sha,url='/neosubzero/frost-vault/weather/1')]
        manifest=contract.seal(manifest)
        remote=SimpleNamespace(request=lambda method,path:vault.canonical(dict(manifest=manifest)) if path.endswith('dataset-manifest') else raw)
        with tempfile.TemporaryDirectory() as folder:
            local.refresh(remote,folder,with_weather=True)
            self.assertEqual(local.read_json(Path(folder)/'objects'/(sha+'.json'))['schema_version'],weather.LEGACY_VERSION)
            with self.assertRaises(ValueError):local.validate_manifest(manifest)

    def test_old_artifact_is_decodable_but_cannot_be_reinterpreted_as_new_contract(self):
        from tests.test_frost_models import candidate
        old=copy.deepcopy(candidate());old['feature_version']=model.LEGACY_FEATURE_VERSION
        old['dataset']['version']=old['classification_version']='frost-history-v2'
        raw=vault.canonical(old);self.assertEqual(model.decode(raw)['feature_version'],model.LEGACY_FEATURE_VERSION)
        old['feature_version']=model.FEATURE_VERSION
        with self.assertRaises(ValueError):model.decode(vault.canonical(old))

    def test_legacy_manifest_is_read_only_not_a_new_training_readiness_bypass(self):
        from tests.test_frost_dataset import local_manifest
        manifest=local_manifest();manifest.update(version='frost-history-v2',weather_version=weather.LEGACY_VERSION,
            implementation_sha256='a533954b09bf8f4edc51ccac39471e484ccbee5604d27271b27f1fce131b8c23')
        manifest=contract.seal(manifest);contract.validate(manifest,historical=True)
        with self.assertRaises(ValueError):contract.validate(manifest,historical=True,training=True)
        with self.assertRaises(ValueError):contract.validate(manifest,training=True)
        manifest['implementation_sha256']='0'*64;manifest=contract.seal(manifest)
        with self.assertRaises(ValueError):contract.validate(manifest,historical=True)

    def test_research_work_forbidden_on_render_before_file_or_network_access(self):
        with patch.dict(os.environ,{'RENDER':'true'}):
            for call in (lambda:hrrr.Sources('unused'),lambda:collector.once('unused','unused'),lambda:correction.rebuild('unused','unused')):
                with self.assertRaises(RuntimeError):call()


class HrrrTest(unittest.TestCase):
    def test_archive_available_by_is_not_relabelled_as_issue_or_future_valid_time(self):
        from app.services import frost_forecast as forecast
        at=datetime(2025,12,1,tzinfo=timezone.utc)
        selected=dict(issued_at_utc=None,available_by_utc=at.isoformat(),availability_basis='NOAA_original_object_Last-Modified',
            target_at_utc=(at+timedelta(hours=1)).isoformat(),valid_at_utc=at.isoformat(),observation=dict(temperature_c=-1))
        values=forecast.research_vector([],selected,cutoff=at,target=at+timedelta(hours=1))
        self.assertEqual(values[0],-1);self.assertIsNone(selected['issued_at_utc'])
        selected['valid_at_utc']=(at+timedelta(hours=2)).isoformat()
        with self.assertRaises(ValueError):forecast.research_vector([],selected,cutoff=at,target=at+timedelta(hours=1))
        selected.update(valid_at_utc=at.isoformat(),availability_basis='unknown')
        with self.assertRaises(ValueError):forecast.research_vector([],selected,cutoff=at,target=at+timedelta(hours=1))

    def test_cycle_is_not_publication_and_later_archive_writes_are_rejected(self):
        at=datetime(2025,12,1,tzinfo=timezone.utc)
        self.assertFalse(hrrr.available({},at,at+timedelta(hours=1)))
        self.assertFalse(hrrr.available({'last_modified':'Mon, 01 Dec 2025 01:01:00 GMT'},at,at+timedelta(hours=1)))
        self.assertFalse(hrrr.available({'last_modified':'Sun, 30 Nov 2025 23:59:00 GMT'},at,at+timedelta(hours=1)))
        self.assertTrue(hrrr.available({'last_modified':'Mon, 01 Dec 2025 00:53:05 GMT'},at,at+timedelta(hours=1)))

    def test_point_units_missing_values_and_grid_wind_rotation(self):
        point=dict(forecast_hours=[1],point=dict(convergence_degrees=5),series=dict(TMP=[273.15],DPT=[272.15],UGRD=[0],VGRD=[-10],TCDC=[75]))
        ob=hrrr.normalized(point,1)
        self.assertEqual(ob['temperature_c'],0);self.assertAlmostEqual(ob['relative_humidity'],92.9729,places=2)
        self.assertAlmostEqual(ob['wind_kt'],19.43844492);self.assertEqual(ob['wind_direction_degrees'],5)
        self.assertEqual(ob['cloud_cover_percent'],75);self.assertEqual(ob['cloud_layers'],[])
        self.assertIsNone(ob['weather_codes']);self.assertIsNone(ob['precipitation_since_hourly_reset_in'])
        self.assertIsNone(ob['precipitation_1h_mm']);self.assertIsNone(ob['ceiling_ft'])
        for key in ('CRAIN','CSNOW','CFRZR','CICEP'):point['series'][key]=[0]
        self.assertEqual(hrrr.normalized(point,1)['weather_codes'],'')

    def test_decompression_bound_before_allocating_and_unsupported_types(self):
        meta=dict(chunks=[1000,150,150],dtype='<f4',order='C',filters=None,compressor=None)
        with self.assertRaises(ValueError):hrrr.array(b'',meta)
        meta.update(chunks=[1],dtype='|O')
        with self.assertRaises(ValueError):hrrr.array(b'',meta)
        meta.update(dtype='<f4',compressor={'id':'blosc','cname':'lz4'})
        with self.assertRaises(ValueError):hrrr.array(b'bad',meta)

    def test_cache_integrity_and_server_range_enforcement(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as folder:
            source=hrrr.Sources(folder,1);response=MagicMock()
            response.__enter__.return_value=response;response.status_code=200
            response.headers={'Last-Modified':'Mon, 01 Dec 2025 00:53:05 GMT'}
            response.iter_content.return_value=iter([b'original'])
            try:
                with patch.object(source.session,'request',return_value=response) as request:
                    raw,receipt=source.get('https://hrrrzarr.s3.amazonaws.com/test')
                    self.assertEqual(source.get(receipt['url'])[0],raw);self.assertEqual(request.call_count,1)
                    Path(folder,receipt['sha256']+'.source').write_bytes(b'corrupt')
                    with self.assertRaises(ValueError):source.get(receipt['url'])
                    with self.assertRaises(Exception):source.get('https://hrrrzarr.s3.amazonaws.com/range',byte_range='bytes=0-1')
                response.__exit__.assert_called()
            finally:source.close()


class CollectorTest(unittest.TestCase):
    def test_current_taf_acquisition_uses_each_capture_not_a_day_long_cache(self):
        import json
        from types import SimpleNamespace
        from scripts import frost_forecast_validation as cli
        at=datetime(2025,12,1,tzinfo=timezone.utc)
        points=dict(geometry=dict(type='Point',coordinates=[-89.0876,42.19151]),properties=dict(forecastGridData='https://api.weather.gov/gridpoints/LOT/25,85'))
        candidate=dict(model_version='fixture',evaluation=dict(lead_hours_validated=[0]))
        evidence=SimpleNamespace(candidates=lambda:[(candidate,'a'*64)])
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(cli,'datetime') as clock,patch.object(cli,'cached_get',side_effect=[(b'[]',{}),(json.dumps(points).encode(),{}),(b'{}',{})]*2),patch('app.services.frost_inference.normalize_metars',return_value=[]),patch.object(cli,'taf_day',return_value=([],{})) as taf,patch.object(cli,'taf_case',return_value=None),patch.object(cli.forecast,'nws_grid',side_effect=lambda grid,cutoff,target:dict(issued_at_utc=cutoff.isoformat(),target_at_utc=target.isoformat(),observation={})),patch.object(model,'score',return_value=.5):
                clock.now.side_effect=[at]*3+[at+timedelta(hours=1)]*3
                cli.shadow(evidence,folder);cli.shadow(evidence,folder)
            self.assertNotEqual(taf.call_args_list[0].args[0],taf.call_args_list[1].args[0])
            self.assertEqual(len(list((Path(folder)/'shadow').glob('*.json'))),2)

    def test_success_skip_gap_and_failed_save_recovery_without_unknown_negatives(self):
        at=datetime(2025,12,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            snapshot=dict(captured_at_utc=at.isoformat(),approved=False,outcome=None)
            with patch.object(collector,'Evidence'),patch.object(collector,'shadow',return_value=snapshot) as capture:
                first=collector.once('dataset',folder,now=at)
                self.assertEqual(first['state'],'CAPTURED')
                self.assertEqual(collector.once('dataset',folder,now=at+timedelta(minutes=30))['state'],'ALREADY CAPTURED')
                self.assertEqual(capture.call_count,1)
            with patch.object(collector,'Evidence'),patch.object(collector,'shadow',side_effect=TimeoutError('private error')):
                with self.assertRaises(TimeoutError):collector.once('dataset',folder,now=at+timedelta(hours=5))
            state=collector.read_json(Path(folder)/'collector-state.json')
            self.assertEqual(state['last_success_utc'],at.isoformat());self.assertIn('gap',state)
            self.assertNotIn('private error',str(state));self.assertFalse(state['approved'])
            with patch.object(collector,'Evidence'),patch.object(collector,'shadow',return_value=dict(snapshot,captured_at_utc=(at+timedelta(hours=6)).isoformat())):
                self.assertEqual(collector.once('dataset',folder,now=at+timedelta(hours=6))['state'],'CAPTURED')

    def test_overlapping_local_collectors_are_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            with collector.exclusive(folder):
                with self.assertRaises(OSError):
                    with collector.exclusive(folder):pass


from tests.test_frost_forecast import ResearchRouteTest


class ArchiveCompatibilityTest(ResearchRouteTest):
    def test_previous_evidence_remains_visible_and_downloadable_without_relabeling(self):
        from flask import g
        from app.extensions import db
        from app.models import FrostHistoryTask
        from app.services import frost_history as history
        client=self.client('master');g.pop('_login_user',None)
        old=FrostHistoryTask(gateway_id=self.f.gateway.id,kind='partition',identity='2025-01',version='frost-history-v2',status='ready',
            result_key='retained-history',summary=dict(weather_matched=1,weather_unmatched=0))
        metar=FrostHistoryTask(gateway_id=self.f.gateway.id,kind='weather',identity='2025-01',version=weather.LEGACY_VERSION,status='ready',result_key='retained-weather')
        db.session.add_all([old,metar]);db.session.commit()
        self.assertEqual(history.health(self.f.gateway.id)['version'],'frost-history-v2')
        self.assertEqual(history.health(self.f.gateway.id)['totals']['weather_matched'],1)
        with patch.object(vault,'read_json',return_value={'schema_version':'unchanged-original'}):
            response=client.get('/neosubzero/frost-vault/history/'+str(old.id))
            self.assertEqual(response.status_code,200)
            self.assertIn('frost-history-v2',response.headers['Content-Disposition'])
            self.assertEqual(client.get('/neosubzero/frost-vault/weather/'+str(metar.id)).status_code,200)


from tests import test_neosubzero_ucc_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class Phase8PostgresTest(ArchiveCompatibilityTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture
