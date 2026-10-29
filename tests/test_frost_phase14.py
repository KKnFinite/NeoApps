"""Bounded point-only decoding, exact research registration and source proofs."""
import copy
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch

from app.services import frost_hrrr_point as light, frost_vault as vault, frost_auto as auto
from scripts import frost_prospective as study


def native_fixture(at):
    """Original-format synthetic provider bytes, never a fabricated production event."""
    import numpy as np
    from numcodecs import Blosc
    root='https://hrrrzarr.s3.amazonaws.com/'+(at.replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)).strftime('sfc/%Y%m%d/%Y%m%d_%Hz_fcst.zarr/')
    cycle=at.replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)
    bodies={}
    def definition(shape,dtype='<f8',compressed=False):
        return dict(chunks=shape,shape=shape,dtype=dtype,order='C',filters=None,fill_value=-9999,
            compressor=dict(id='blosc',cname='lz4',shuffle=1) if compressed else None)
    values=dict(TMP=273.15,DPT=272.15,UGRD=1,VGRD=2,GUST=3,TCDC=10,HGT=2000,VIS=16000,APCP_1hr_acc_fcst=0,CRAIN=0,CSNOW=0,CFRZR=0,CICEP=0)
    for level,name,unit in light.VARIABLES:
        url=root+level+'/'+name;meta={}
        for key,data,dtype,units in [('forecast_period',list(range(1,19)),'<i8','hours'),
                ('forecast_reference_time',[cycle.timestamp()/3600],'<f8','hours since 1970-01-01'),
                ('time',[cycle.timestamp()/3600+h for h in range(1,19)],'<f8','hours since 1970-01-01'),
                ('projection_x_coordinate',[692479.8574780696+(i-1130)*3000 for i in range(1799)],'<f8','m'),
                ('projection_y_coordinate',[443693.8474433364+(i-677)*3000 for i in range(1059)],'<f8','m')]:
            meta[key+'/.zarray']=definition([] if key=='forecast_reference_time' else [len(data)],dtype)
            meta[key+'/.zattrs']=dict(units=units)
            bodies[url+'/'+key+'/0']=np.array(data,dtype=dtype).tobytes()
        meta[level+'/'+name+'/.zarray']=dict(definition([18,150,150],'<f4',True),shape=[18,1059,1799])
        meta[level+'/'+name+'/.zattrs']=dict(units=unit)
        bodies[url+'/.zmetadata']=vault.canonical(dict(metadata=meta))
        bodies[url+'/'+level+'/'+name+'/0.4.7']=Blosc(cname='lz4',shuffle=1).encode(np.full(18*22500,values[name],dtype='<f4'))
    class Client:
        receipts=[]
        def get(self,url,head=False):
            raw=b'' if head else bodies[url]
            receipt=dict(url=url,method='HEAD' if head else 'GET',bytes=len(raw),sha256=vault.digest(raw),
                retrieved_at_utc=at.isoformat(),last_modified=(cycle+timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT'))
            self.receipts.append(receipt);return raw,receipt
    point,forecasts=light.collect(Client(),at)
    return point,forecasts,bodies


class DecoderTest(unittest.TestCase):
    def test_transfer_reserve_never_relaxes_container_threshold(self):
        from flask import Flask
        from app.services import frost_history as jobs
        app=Flask(__name__)
        with app.app_context(),patch.object(jobs,'memory_allows_work',return_value=True):
            for usage,expected in [(330,True),(355,False),(390,False)]:
                with patch.object(jobs,'memory_budget',return_value=dict(safe=usage<=384,usage=usage*1024**2,limit=512*1024**2)):
                    self.assertEqual(light.memory_available(),expected)

    def test_point_only_matches_reference_all_scalar_precisions_filters_blocks(self):
        from numcodecs import Blosc
        import numpy as np
        for dtype,code in [('<f2','e'),('<f4','f'),('<f8','d'),('<i8','q')]:
            values=[i%100 for i in range(20000)]
            raw=struct.pack('<'+code*len(values),*values)
            for shuffle in (0,1):
                compressed=Blosc(cname='lz4',shuffle=shuffle,blocksize=4096).encode(np.frombuffer(raw,dtype=dtype))
                meta=dict(dtype=dtype,order='C',filters=None,chunks=[len(values)],compressor=dict(id='blosc',cname='lz4',shuffle=shuffle))
                indices=[0,3,500,8193,len(values)-1]
                self.assertEqual(light.numbers(compressed,meta,indices),[values[i] for i in indices])
        # Actual source missing values are not replaced with meteorological zero.
        point=dict(forecast_hours=[1],point=light.POINT,series={'TMP':[273.15],'DPT':[272.15]})
        result=light.normalized(point,1)
        self.assertEqual(result['temperature_c'],0)
        self.assertIsNone(result['precipitation_1h_mm']);self.assertIsNone(result['weather_codes'])

    def test_rejects_codec_size_bombs_invalid_offsets_and_filters(self):
        from numcodecs import Blosc
        raw=struct.pack('<1000f',*range(1000));compressed=Blosc(cname='lz4').encode(raw)
        meta=dict(dtype='<f4',order='C',filters=None,chunks=[1000],compressor=dict(id='blosc',cname='lz4',shuffle=1))
        for index,value in [(0,99),(2,0xff),(3,8),(4,255),(12,0)]:
            bad=bytearray(compressed);bad[index]=value
            with self.assertRaises((ValueError,RuntimeError)):light.numbers(bytes(bad),meta,[500])
        for key,value in [('chunks',[10**12]),('filters',[{}]),('dtype','object')]:
            with self.assertRaises(ValueError):light.numbers(compressed,dict(meta,**{key:value}),[1])

    def test_no_scientific_imports_in_clean_runtime(self):
        code="from app.services import frost_hrrr_point; import lz4.block,sys; assert not set(('numpy','pandas','sklearn','pyproj','eccodes','numcodecs')).intersection(sys.modules)"
        subprocess.run([sys.executable,'-c',code],check=True,capture_output=True)

    def test_source_allowlist_memory_timeout_and_closed_response(self):
        at=datetime.now(timezone.utc);retain=lambda raw,r:r
        source=light.Source(lambda:at,retain,lambda:False)
        with self.assertRaises(RuntimeError):source.get('https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20261028/conus/hrrr.t21z.wrfsfcf01.grib2',head=True)
        with self.assertRaises(ValueError):source.get('https://localhost/private')
        source=light.Source(lambda:at,retain,lambda:True);source.deadline=0
        with self.assertRaises(TimeoutError):source.get('https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20261028/conus/hrrr.t21z.wrfsfcf01.grib2',head=True)

    def test_source_proof_redecode_units_timing_and_tampering(self):
        at=datetime.now(timezone.utc);point,forecasts,bodies=native_fixture(at)
        source=dict(native_point=point,native_forecasts=forecasts)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'hrrr-sources';root.mkdir()
            for raw in bodies.values():(root/(vault.digest(raw)+'.source')).write_bytes(raw)
            study.verify_auto_hrrr(folder,source,at)
            changed=copy.deepcopy(source);changed['native_forecasts'][0]['values']['temperature_c']=99
            with self.assertRaises(ValueError):study.verify_auto_hrrr(folder,changed,at)
            with self.assertRaises(ValueError):study.verify_auto_hrrr(folder,source,at-timedelta(minutes=1))
            receipt=next(r for r in point['source_receipts'] if r['method']=='GET')
            (root/(receipt['sha256']+'.source')).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):study.verify_auto_hrrr(folder,source,at)

    def test_documented_missing_archive_field_remains_missing_during_local_replay(self):
        from urllib.error import HTTPError
        at=datetime.now(timezone.utc);_,_,bodies=native_fixture(at)
        class Client:
            receipts=[]
            def get(self,url,head=False):
                if '/TCDC/TCDC/' in url or '/entire_atmosphere/TCDC/entire_atmosphere/TCDC/' in url:
                    raise HTTPError(url,404,'Absent source',{},None)
                raw=b'' if head else bodies[url]
                r=dict(url=url,method='HEAD' if head else 'GET',bytes=len(raw),sha256=vault.digest(raw),retrieved_at_utc=at.isoformat(),
                    last_modified=(at-timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT'))
                self.receipts.append(r);return raw,r
        point,forecasts=light.collect(Client(),at)
        self.assertIsNone(forecasts[0]['values']['cloud_cover_percent'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'hrrr-sources';root.mkdir()
            for raw in bodies.values():(root/(vault.digest(raw)+'.source')).write_bytes(raw)
            study.verify_auto_hrrr(folder,dict(native_point=point,native_forecasts=forecasts),at)


class RegistrationTest(unittest.TestCase):
    def test_cli_preflights_all_before_upload_and_exact_idempotent_requests(self):
        from tests.test_frost_phase12 import ProspectiveTest
        helper=ProspectiveTest();helper.setUp();self.addCleanup(helper.doCleanups)
        class Remote:
            csrf='test-csrf'
            calls=[]
            def request(self,method,url,**kwargs):
                self.calls.append((method,url,kwargs));return b'{"ok":true,"message":"NOT approved"}'
        remote=Remote()
        with patch.object(auto,'frozen_models',return_value=helper.baseline['models']):
            result=study.register_models(remote,helper.frozen)
        self.assertEqual(result['registered'],8);self.assertFalse(result['approved'])
        for entry,call in zip(helper.baseline['models'],remote.calls):
            self.assertEqual(call[1],'/neosubzero/frost-vault/research-models')
            self.assertEqual(call[2]['data']['csrf_token'],'test-csrf')
            self.assertEqual(vault.digest(call[2]['files']['model'][1]),entry['sha256'])
        with patch.object(auto,'frozen_models',return_value=[]):
            with self.assertRaises(ValueError):study.register_models(remote,helper.frozen)
        self.assertEqual(len(remote.calls),8)


from tests.test_frost_phase13 import AutoTest
class CaptureTest(AutoTest):
    def test_all_eight_real_frozen_contracts_archive_scores_and_refresh(self):
        from tests.test_frost_phase12 import NOW
        helper=self.model_fixture()
        self.enterContext(patch.object(auto.frost_research,'prospective_summary',return_value=dict(baseline=helper.baseline['fingerprint'],models=helper.baseline['models'])))
        source=helper.source(None)
        delta=self.t-NOW
        for item in source['observations']+source['forecasts']:
            for key in ('utc','received_at_utc','cycle_at_utc','available_by_utc','valid_at_utc'):
                if item.get(key):item[key]=(datetime.fromisoformat(item[key])+delta).isoformat()
        self.source=dict(source,native_forecasts=source['forecasts'])
        for entry in auto.frozen_models():
            raw=(helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
            auto.register_research(self.f.gateway.id,self.fixture.actor.id,raw)
        auto.db.session.commit()
        self.assertEqual(self.tick(),1);self.assertEqual(self.tick(),0)
        state=auto.status(self.f.gateway.id)
        self.assertEqual((state['scored'],state['research_models'],state['unavailable']),(8,8,0))
        self.assertEqual(state['last_scored'],self.t.isoformat())
        from app.models import FrostModelRelease
        self.assertEqual(FrostModelRelease.query.count(),0)

    def test_private_source_export_and_scored_local_roundtrip(self):
        helper=self.model_fixture()
        self.enterContext(patch.object(auto.frost_research,'prospective_summary',return_value=dict(baseline=helper.baseline['fingerprint'],models=helper.baseline['models'])))
        point,forecasts,bodies=native_fixture(self.t)
        for r in point['source_receipts']:
            if r['method']=='GET':
                import base64
                r['source_key']=vault.put_json('weather',self.f.gateway.id,'hrrr-source-'+r['sha256'],
                    dict(schema=light.VERSION,sha256=r['sha256'],raw_base64=base64.b64encode(bodies[r['url']]).decode()))
        point['fingerprint']=vault.digest(vault.canonical({k:v for k,v in point.items() if k!='fingerprint'}))
        for f in forecasts:f['source_sha256']=point['fingerprint']
        original=helper.source(None);delta=self.t-study.clock.instant(original['observations'][0]['utc'])
        for item in original['observations']:
            for key in ('utc','received_at_utc'):item[key]=(datetime.fromisoformat(item[key])+delta).isoformat()
        self.source=dict(original,sources=[],native_point=point,native_forecasts=forecasts)
        for entry in auto.frozen_models():auto.register_research(self.f.gateway.id,self.fixture.actor.id,(helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes())
        auto.db.session.commit();self.assertEqual(self.tick(),1)
        client=self.fixture.fixture.client('master')
        class Remote:
            def request(self,method,url):
                response=client.get(url)
                if response.status_code!=200:raise ValueError('Unexpected transfer: '+str(response.status_code))
                return response.data
        with tempfile.TemporaryDirectory() as folder:
            study.refresh_auto(Remote(),folder)
            data=study.assemble_auto(helper.frozen,folder,Path(folder)/'analysis')
            self.assertEqual(len(data['predictions']),8)
            self.assertTrue(all(p['state']=='SCORED' and not p['outcomes'] for p in data['predictions']))
        from flask import g
        watcher=self.fixture.fixture.client('watcher');g.pop('_login_user',None)
        self.assertEqual(watcher.get('/neosubzero/frost-vault/auto-evidence/1/hrrr/'+'a'*64).status_code,403)


if __name__=='__main__':unittest.main()
