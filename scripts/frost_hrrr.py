"""LOCAL bounded KRFD HRRR point research, not operational forecast validation.

The Utah NOAA-derived Zarr archive permits one spatial chunk, not a CONUS
download. Original NOAA GRIB object metadata separately establishes a
conservative available-by bound. A model cycle is NEVER an issue timestamp.
"""
import argparse
from collections import Counter
import json
import math
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError
from app.services import frost_weather as weather,frost_forecast as forecast,frost_vault as vault
from scripts.frost_local import local_only,read_json,write_json,write_bytes

UTC=timezone.utc
VERSION='krfd-hrrr-point-research-v1'
VARIABLES=(('2m_above_ground','TMP','K'),('2m_above_ground','DPT','K'),
    ('10m_above_ground','UGRD','m s-1'),('10m_above_ground','VGRD','m s-1'),
    ('surface','GUST','m s-1'),('entire_atmosphere','TCDC','%'),
    ('cloud_ceiling','HGT','m'),('surface','VIS','m'),('surface','APCP_1hr_acc_fcst','kg m-2'),
    ('surface','CRAIN','1'),('surface','CSNOW','1'),('surface','CFRZR','1'),('surface','CICEP','1'))
GRIB_UNITS={(2,0,6,1):'%',(2,0,19,0):'m',(2,0,1,8):'kg m-2',
    (2,0,1,33):'1',(2,0,1,34):'1',(2,0,1,35):'1',(2,0,1,36):'1'}


@contextmanager
def cache_lock(root):
    from scripts.frost_shadow_collect import exclusive
    deadline=time.monotonic()+10
    while True:
        lock=exclusive(root)
        try:lock.__enter__();break
        except PermissionError:
            if time.monotonic()>=deadline:raise RuntimeError('Local source cache is busy; retained checkpoints are safe to resume.')
            time.sleep(.05)
    try:yield
    finally:lock.__exit__(None,None,None)


def available(receipt,cycle,cutoff):
    """Last-Modified is an archive-write bound, not exact operator receipt."""
    stamp=receipt.get('last_modified')
    if not stamp:return False
    try:at=parsedate_to_datetime(stamp)
    except (ValueError,TypeError,OverflowError):return False
    return at.tzinfo is not None and cycle<=at<=cutoff


class Sources:
    def __init__(self,root,budget_mib=512):
        local_only()
        self.root=Path(root);self.session=self.new_session();self.receipts=[]
        self._threads=threading.local();self._lock=threading.Lock();self._sessions=[]
        self.downloaded=0;self.budget=budget_mib*1024**2
    @staticmethod
    def new_session():
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        session=requests.Session()
        session.mount('https://',HTTPAdapter(max_retries=Retry(total=2,connect=2,read=2,status=2,
            backoff_factor=1,backoff_max=30,status_forcelist=(429,500,502,503,504),
            allowed_methods=('GET','HEAD'),respect_retry_after_header=True)))
        return session
    def close(self):
        self.session.close()
        for session in self._sessions:session.close()
    def prefetch(self,urls,*,head=False):
        def retrieve(url):
            try:self.get(url,head=head)
            except HTTPError as error:
                if error.code not in (403,404):raise
        # Four bounded LOCAL network clients. No app worker or cloud compute.
        previous=list(self._sessions)
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(retrieve,sorted(set(urls))))
        finally:
            for session in self._sessions:
                if session not in previous:session.close()
            self._sessions=previous
    def get(self,url,*,head=False,byte_range=None):
        if not url.startswith(('https://hrrrzarr.s3.amazonaws.com/','https://noaa-hrrr-bdp-pds.s3.amazonaws.com/')):raise ValueError('Unapproved forecast source.')
        method='HEAD' if head else 'RANGE '+byte_range if byte_range else 'GET'
        key=vault.digest((method+' '+url).encode());path=self.root/(key+'.json')
        if path.exists():
            receipt=read_json(path)
            digest=receipt.get('sha256','')
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Unsafe source receipt identity.')
            raw=b''
            if not head:
                with (self.root/(digest+'.source')).open('rb') as handle:raw=handle.read(8*1024**2+1)
            if len(raw)>8*1024**2 or len(raw)!=receipt['bytes'] or receipt['url']!=url or receipt['method']!=method or vault.digest(raw)!=digest:raise ValueError('Forecast source cache hash mismatch.')
            self.receipts.append(receipt);return raw,receipt
        if self.downloaded>=self.budget:raise RuntimeError('Local forecast download budget exhausted; verified checkpoints retained.')
        session=self.session
        if threading.current_thread() is not threading.main_thread():
            if not getattr(self._threads,'session',None):
                self._threads.session=self.new_session()
                with self._lock:self._sessions.append(self._threads.session)
            session=self._threads.session
        with session.request('HEAD' if head else 'GET',url,headers={'Range':byte_range} if byte_range else {},timeout=(10,30),stream=True) as r:
            if r.status_code!=(206 if byte_range else 200):raise HTTPError(url,r.status_code,'Forecast source unavailable',r.headers,None)
            if byte_range and not r.headers.get('Content-Range','').startswith(byte_range.replace('=',' ')+'/'):raise ValueError('Server did not honor the bounded GRIB range.')
            chunks=[];length=0
            for chunk in (() if head else r.iter_content(65536)):
                length+=len(chunk)
                with self._lock:
                    if length>8*1024**2 or self.downloaded+len(chunk)>self.budget:raise RuntimeError('Bounded local forecast retrieval limit reached.')
                    self.downloaded+=len(chunk)
                chunks.append(chunk)
            raw=b''.join(chunks)
            receipt=dict(url=url,method=method,retrieved_at_utc=datetime.now(UTC).isoformat(),
                sha256=vault.digest(raw),bytes=len(raw),last_modified=r.headers.get('Last-Modified'),etag=r.headers.get('ETag'))
        with self._lock:
            self.receipts.append(receipt)
            with cache_lock(self.root):
                if not head:
                    destination=self.root/(receipt['sha256']+'.source')
                    if destination.exists():
                        if destination.read_bytes()!=raw:raise ValueError('Immutable provider object changed.')
                    else:write_bytes(destination,raw)
                write_json(path,receipt)
        return raw,receipt


def array(raw,metadata):
    import numpy as np
    from numcodecs import Blosc
    shape=metadata['chunks'];dtype=np.dtype(metadata['dtype'])
    if dtype.str not in ('<f2','<f4','<f8','<i8') or metadata['order']!='C' or metadata.get('filters') or math.prod(shape or [1])*dtype.itemsize>9*1024**2:
        raise ValueError('Unsupported or unbounded forecast array.')
    compressor=metadata['compressor']
    if compressor and (compressor.get('id')!='blosc' or compressor.get('cname') not in ('lz4','zstd')):raise ValueError('Unsupported source compression.')
    # Validate the Blosc header before allocating/decompressing.
    expected=math.prod(shape or [1])*dtype.itemsize
    if compressor and (len(raw)<16 or int.from_bytes(raw[4:8],'little')!=expected):raise ValueError('Forecast decompression size mismatch.')
    decoded=Blosc(**{k:v for k,v in compressor.items() if k!='id'}).decode(raw) if compressor else raw
    if len(decoded)!=expected:raise ValueError('Forecast array size mismatch.')
    return np.frombuffer(decoded,dtype=dtype).reshape(shape or ())


def cycle_point(client,cycle):
    import numpy as np
    from pyproj import Proj
    root='https://hrrrzarr.s3.amazonaws.com/'+cycle.strftime('sfc/%Y%m%d/%Y%m%d_%Hz_fcst.zarr/')
    series={};missing=[];point=None;times=None
    projection=Proj(proj='lcc',lat_0=38.5,lon_0=262.5,lat_1=38.5,lat_2=38.5,a=6371229,b=6371229)
    first=root+'2m_above_ground/TMP'
    first_meta=json.loads(client.get(first+'/.zmetadata')[0])['metadata']
    x=array(client.get(first+'/projection_x_coordinate/0')[0],first_meta['projection_x_coordinate/.zarray'])
    y=array(client.get(first+'/projection_y_coordinate/0')[0],first_meta['projection_y_coordinate/.zarray'])
    px,py=projection(weather.STATION['longitude'],weather.STATION['latitude'])
    ix=int(np.argmin(abs(x-px)));iy=int(np.argmin(abs(y-py)))
    chunk=f'0.{iy//150}.{ix//150}'
    client.prefetch([root+level+'/'+name+suffix for level,name,_ in VARIABLES for suffix in (
        '/.zmetadata','/forecast_period/0','/forecast_reference_time/0','/time/0','/'+level+'/'+name+'/'+chunk)])
    for level,name,unit in VARIABLES:
        group=root+level+'/'+name
        try:
            meta=json.loads(client.get(group+'/.zmetadata')[0])['metadata']
            definition=meta[level+'/'+name+'/.zarray'];attrs=meta[level+'/'+name+'/.zattrs']
            # Some Utah arrays omit CF units. Only the exact documented GRIB2
            # parameter identity can supply them (NCEP HRRR product inventory).
            actual_unit=attrs.get('units') or GRIB_UNITS.get(tuple(attrs.get('GRIB_PARAM',[])))
            if actual_unit!=unit:raise ValueError('Forecast units changed: '+name+' '+str(actual_unit))
            if point is None:
                x=array(client.get(group+'/projection_x_coordinate/0')[0],meta['projection_x_coordinate/.zarray'])
                y=array(client.get(group+'/projection_y_coordinate/0')[0],meta['projection_y_coordinate/.zarray'])
                px,py=projection(weather.STATION['longitude'],weather.STATION['latitude'])
                ix=int(np.argmin(abs(x-px)));iy=int(np.argmin(abs(y-py)))
                if math.hypot(x[ix]-px,y[iy]-py)>2200:raise ValueError('KRFD grid distance exceeds half-cell diagonal.')
                lon,lat=projection(float(x[ix]),float(y[iy]),inverse=True)
                point=dict(x_index=ix,y_index=iy,latitude=lat,longitude=lon,
                    distance_m=math.hypot(x[ix]-px,y[iy]-py),grid='NOAA HRRR 1799x1059 Lambert 3km',
                    convergence_degrees=projection.get_factors(lon,lat).meridian_convergence)
            periods=array(client.get(group+'/forecast_period/0')[0],meta['forecast_period/.zarray']).tolist()
            if meta['forecast_period/.zattrs']['units']!='hours':raise ValueError('Forecast time units changed.')
            reference=float(array(client.get(group+'/forecast_reference_time/0')[0],meta['forecast_reference_time/.zarray']))
            if meta['forecast_reference_time/.zattrs']['units']!='hours since 1970-01-01' or datetime.fromtimestamp(reference*3600,UTC)!=cycle:raise ValueError('Forecast reference cycle mismatch.')
            valid=array(client.get(group+'/time/0')[0],meta['time/.zarray']).tolist()
            if meta['time/.zattrs']['units']!='hours since 1970-01-01' or any(abs(t-reference-h)>1e-8 for t,h in zip(valid,periods)):raise ValueError('Forecast valid times mismatch.')
            if times is not None and times!=periods:raise ValueError('Variables disagree on forecast hours.')
            times=periods
            if definition['chunks'][1:]!=[150,150] or definition['shape'][1:]!=[1059,1799] or definition['shape'][0]!=len(periods):raise ValueError('HRRR grid/time contract changed.')
            chunk=f"0.{point['y_index']//150}.{point['x_index']//150}"
            values=array(client.get(group+'/'+level+'/'+name+'/'+chunk)[0],definition)[:len(periods),point['y_index']%150,point['x_index']%150]
            series[name]=[float(v) if np.isfinite(v) and v!=definition.get('fill_value') else None for v in values]
        except HTTPError as error:
            if error.code not in (403,404):raise
            missing.append(dict(variable=name,http_status=error.code,reason='missing archive array; never assume zero'))
    if point is None or times is None:raise ValueError('No verified KRFD forecast grid.')
    return dict(schema=VERSION,cycle_at_utc=cycle.isoformat(),issued_at_utc=None,
        issue_time_quality='GRIB reference cycle only; original-object archive timestamp checked separately',
        station=weather.STATION,point=point,forecast_hours=times,series=series,missing=missing,
        source_receipts=sorted({(r['url'],r['method']):r for r in client.receipts}.values(),key=lambda r:(r['url'],r['method'])),
        derived_archive='University of Utah NOAA HRRR Zarr; source float precision retained')


def normalized(point,hour):
    i=point['forecast_hours'].index(hour)
    def v(name):return point['series'].get(name,[None]*len(point['forecast_hours']))[i]
    t,d=v('TMP'),v('DPT');u,n=v('UGRD'),v('VGRD')
    t=t-273.15 if t is not None else None;d=d-273.15 if d is not None else None
    wind=math.hypot(u,n)*1.943844492 if u is not None and n is not None else None
    direction=((math.degrees(math.atan2(-u,-n))+point['point']['convergence_degrees'])%360) if wind else None
    flags=[code for key,code in [('CRAIN','RA'),('CSNOW','SN'),('CFRZR','FZRA'),('CICEP','PL')] if v(key) is not None and v(key)>0]
    complete_types=all(v(key) is not None for key in ('CRAIN','CSNOW','CFRZR','CICEP'))
    ceiling=v('HGT');visibility=v('VIS');gust=v('GUST')
    return dict(temperature_c=t,dewpoint_c=d,spread_c=t-d if t is not None and d is not None else None,
        relative_humidity=100*math.exp(17.625*d/(243.04+d)-17.625*t/(243.04+t)) if t is not None and d is not None else None,
        wind_kt=wind,wind_direction_degrees=direction,gust_kt=gust*1.943844492 if gust is not None else None,
        cloud_cover_percent=v('TCDC'),model_cloud_ceiling_height_m=ceiling,ceiling_ft=None,
        visibility_sm=visibility/1609.344 if visibility is not None else None,
        precipitation_1h_mm=v('APCP_1hr_acc_fcst'),weather_codes=' '.join(flags) if complete_types else None,
        cloud_layers=[],clear_sky=None,precipitation_since_hourly_reset_in=None)


def collect(directory,output,*,nights=120,budget_mib=512,season=None,native_hours=False):
    local_only();from scripts.frost_forecast_validation import Evidence
    root=Path(output);evidence=Evidence(directory);prepared=read_json(evidence.root/'prepared.json')
    latest=season or max(r['season'] for r in prepared['examples']);pool=[r for r in prepared['examples'] if r['season']==latest]
    if not pool:raise ValueError('Requested season has no eligible historical treatment evidence.')
    existing=root/'cases.json'
    prior=read_json(existing) if existing.exists() else None
    if prior and any(r['season']!=latest for r in prior['cases']):
        raise ValueError('Use a separate output directory for each season; preserve earlier cases.')
    if prior and prior['source_dataset_fingerprint']!=evidence.manifest['fingerprint']:
        from app.services.frost_dataset_contract import validate
        previous=validate(read_json(evidence.root/'manifests'/(prior['source_dataset_fingerprint']+'.json')),historical=True)
        if any(previous[k]!=evidence.manifest[k] for k in ('partitions','weather','source_catalog','version','weather_version','time_contract')):
            raise ValueError('Incremental forecast selection uses different historical sources. Retain it separately.')
    if prior:
        write_json(root/'case-manifests'/(vault.digest(vault.canonical(prior))+'.json'),prior)
    grouped={}
    for row in sorted(pool,key=lambda r:(r['event_at'],r['group_id'])):grouped.setdefault(row['night'],row)
    all_nights=sorted(grouped);selected=[all_nights[i] for i in sorted({round(i*(len(all_nights)-1)/max(1,min(nights,len(all_nights))-1)) for i in range(min(nights,len(all_nights)))})]
    client=Sources(root/'sources',budget_mib)
    case_key=lambda r:(r['group_id'],r['warning_hours'],r['input_lead_hours'])
    cases={case_key(r):r for r in (prior or {}).get('cases',[])}
    gaps={(r['night'],r['warning_hours']):r for r in (prior or {}).get('gaps',[])}
    def checkpoint(*,immutable=False):
        result=dict(schema=VERSION,source_dataset_fingerprint=evidence.manifest['fingerprint'],
            selection='first application per independent evenly spaced operational night; no score/label selection',
            cases=sorted(cases.values(),key=lambda r:(r['night'],r['warning_hours'],r['input_lead_hours'])),
            gaps=sorted(gaps.values(),key=lambda r:(r['night'],r['warning_hours'])),
            operationally_validated=False,availability_limit='NOAA original-object Last-Modified is an available-by archive bound, not exact issuance or operator receipt')
        write_json(existing,result)
        if immutable:write_json(root/'case-manifests'/(vault.digest(vault.canonical(result))+'.json'),result)
    try:
        for night in selected:
            event=grouped[night];at=forecast.instant(event['event_at'])
            for warning in (1,3,6):
                cutoff=at-timedelta(hours=warning);cycle=cutoff.replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)
                path=root/'cycles'/(cycle.strftime('%Y%m%d%H')+'.json')
                if path.exists():
                    point=read_json(path)
                    if vault.digest(vault.canonical({k:v for k,v in point.items() if k!='fingerprint'}))!=point['fingerprint']:raise ValueError('Point checkpoint integrity mismatch.')
                else:
                    client.receipts=[]
                    try:point=cycle_point(client,cycle)
                    except HTTPError as error:
                        if error.code not in (403,404):raise
                        gaps[(night,warning)]=dict(night=night,cycle_at_utc=cycle.isoformat(),warning_hours=warning,
                            http_status=error.code,reason='Forecast cycle unavailable; no substitute observation')
                        continue
                    point['fingerprint']=vault.digest(vault.canonical(point));write_json(path,point)
                gaps.pop((night,warning),None)
                if native_hours:
                    final_hour=int((at.replace(minute=0,second=0,microsecond=0)-cycle).total_seconds()/3600)
                    proofs=[]
                    for hour in range(max(1,final_hour-6),final_hour+1):
                        if hour not in point['forecast_hours']:continue
                        url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf')+f'{hour:02d}.grib2'
                        proofs.append(url)
                    # Same existing four LOCAL clients/budget, not a larger
                    # worker pool. Metadata only; no nationwide forecast bodies.
                    client.prefetch(proofs,head=True)
                for lead in (0,1):
                    target=at-timedelta(hours=lead);valid=target.replace(minute=0,second=0,microsecond=0)
                    hour=int((valid-cycle).total_seconds()/3600)
                    if hour<1 or hour not in point['forecast_hours']:continue
                    url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf')+f'{hour:02d}.grib2'
                    try:_,receipt=client.get(url,head=True)
                    except HTTPError as error:
                        if error.code not in (403,404):raise
                        receipt=dict(url=url,last_modified=None,http_status=error.code)
                    record=dict(group_id=event['group_id'],night=night,season=event['season'],label=event['label'],sources=event['sources'],
                        event_at_utc=at.isoformat(),warning_hours=warning,input_lead_hours=lead,prediction_cutoff_utc=cutoff.isoformat(),
                        target_at_utc=target.isoformat(),forecast_valid_at_utc=valid.isoformat(),cycle_at_utc=cycle.isoformat(),
                        archive_availability=receipt,available_before_cutoff=available(receipt,cycle,cutoff),
                        point_fingerprint=point['fingerprint'],observation=normalized(point,hour))
                    cases[case_key(record)]=record
                print('LOCAL HRRR point checkpoint',night,warning,cycle.isoformat(),flush=True)
            checkpoint()
    finally:
        client.close();checkpoint(immutable=True)
    return list(cases.values())


def quantization_tolerance(dtype,value):
    """Half one stored ULP plus original-message packing tolerance, not clamping."""
    import numpy as np
    if dtype not in ('<f2','<f4','<f8') or not math.isfinite(value):raise ValueError('Unverified forecast precision.')
    encoded=np.array(value,dtype=np.dtype(dtype))
    if not np.isfinite(encoded):raise ValueError('Forecast value exceeds verified stored precision.')
    tolerance=float(abs(np.spacing(encoded)))/2+.001
    if not math.isfinite(tolerance):raise ValueError('Unbounded forecast precision tolerance.')
    return tolerance


def verify_grib(output,cycle_text='2025-12-01T00:00:00+00:00'):
    """Cross-check the derivative grid/precision against two original messages."""
    local_only();import eccodes
    cycle=forecast.instant(cycle_text);root=Path(output);client=Sources(root/'sources',32)
    try:
        client.receipts=[];point=cycle_point(client,cycle)
        base='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf01.grib2')
        index,receipt=client.get(base+'.idx');lines=index.decode().splitlines();checks=[]
        for parameter,level in [('TMP','2 m above ground'),('UGRD','10 m above ground')]:
            matches=[i for i,line in enumerate(lines) if ':'+parameter+':'+level+':1 hour fcst:' in line]
            if len(matches)!=1 or matches[0]+1>=len(lines):raise ValueError('Original GRIB index parameter is missing or ambiguous.')
            i=matches[0];start=int(lines[i].split(':')[1]);end=int(lines[i+1].split(':')[1])-1
            if end-start>4*1024**2:raise ValueError('Selected original parameter exceeds range limit.')
            raw,source=client.get(base,byte_range=f'bytes={start}-{end}')
            gid=eccodes.codes_new_from_message(raw)
            try:
                nearest=eccodes.codes_grib_find_nearest(gid,weather.STATION['latitude'],weather.STATION['longitude'])[0]
                grid=dict(kind=eccodes.codes_get(gid,'gridType'),nx=eccodes.codes_get(gid,'Ni'),ny=eccodes.codes_get(gid,'Nj'),
                    latitude=nearest['lat'],longitude=(nearest['lon']+180)%360-180,index=nearest['index'])
                if grid['kind']!='lambert' or (grid['nx'],grid['ny'])!=(1799,1059) or abs(grid['latitude']-point['point']['latitude'])>1e-6 or abs(grid['longitude']-point['point']['longitude'])>1e-6:raise ValueError('Original and derivative grid disagree at KRFD.')
                value=point['series'][parameter][point['forecast_hours'].index(1)]
                level='2m_above_ground' if parameter=='TMP' else '10m_above_ground'
                metadata=json.loads(client.get('https://hrrrzarr.s3.amazonaws.com/'+cycle.strftime('sfc/%Y%m%d/%Y%m%d_%Hz_fcst.zarr/')+level+'/'+parameter+'/.zmetadata')[0])['metadata']
                dtype=metadata[level+'/'+parameter+'/.zarray']['dtype']
                tolerance=quantization_tolerance(dtype,nearest['value'])
                if value is None or abs(value-nearest['value'])>tolerance:raise ValueError('Original and derivative forecast value disagree beyond documented precision.')
                relative=eccodes.codes_get(gid,'uvRelativeToGrid')
                if parameter=='UGRD' and relative!=1:raise ValueError('Wind rotation convention changed.')
                checks.append(dict(parameter=parameter,grid=grid,original_value=nearest['value'],zarr_value=value,
                    units=eccodes.codes_get(gid,'units'),uv_relative_to_grid=relative,source=source,
                    derivative_dtype=dtype,absolute_error=abs(value-nearest['value']),quantization_tolerance=tolerance))
            finally:eccodes.codes_release(gid)
        result=dict(schema=VERSION,cycle_at_utc=cycle.isoformat(),point=point['point'],index_receipt=receipt,
            checks=checks,precision='Original GRIB values compared with half one actual stored ULP plus .001 packing tolerance; raw values retained, not forced identical',
            wind_rotation='Earth-relative direction = grid-relative meteorological direction + Lambert meridian convergence')
        write_json(root/'grib-verifications'/(cycle.strftime('%Y%m%d%H')+'.json'),result)
        if cycle_text=='2025-12-01T00:00:00+00:00':write_json(root/'original-grib-verification.json',result)
        return result
    finally:client.close()


def evaluate(directory,output,*,baseline_directory=None):
    """Frozen model transfer diagnostics; no extended-horizon validation claim."""
    local_only();from app.services import frost_model as model,frost_analysis as analysis
    from scripts.frost_local import metrics,weather_reference
    from scripts.frost_forecast_validation import Evidence
    root=Path(output);evidence=Evidence(directory);data=read_json(root/'cases.json');reports=[]
    examples={r['group_id']:r for r in read_json(evidence.root/'prepared.json')['examples']}
    if data['source_dataset_fingerprint']!=evidence.manifest['fingerprint']:
        # H0/H1 preparation seals distinct eligibility manifests over the same
        # immutable evidence. Verify that identity, never simply ignore it.
        from app.services.frost_dataset_contract import validate
        prior=validate(read_json(evidence.root/'manifests'/(data['source_dataset_fingerprint']+'.json')),historical=True)
        if any(prior[k]!=evidence.manifest[k] for k in ('partitions','weather','source_catalog','version','weather_version','time_contract')):
            raise ValueError('Forecast cases belong to a different local dataset.')
    candidates=[(a,s,evidence) for a,s in evidence.candidates()]
    if baseline_directory:
        baseline=Evidence(baseline_directory)
        if baseline.manifest['source_catalog']!=evidence.manifest['source_catalog']:raise ValueError('Frozen baseline uses different source evidence.')
        candidates.extend((a,s,baseline) for a,s in baseline.candidates())
    verified_sources=set()
    for artifact,sha,input_evidence in candidates:
        lead=artifact['evaluation']['lead_hours_validated'][0]
        for hours in (1,3,6):
            selected=[];missing_availability=0;weather_errors=[]
            for case in data['cases']:
                if case['input_lead_hours']!=lead or case['warning_hours']!=hours:continue
                example=examples.get(case['group_id'])
                if not example or any(case[k]!=example[k] for k in ('night','season','label','sources')) or forecast.instant(case['event_at_utc'])!=forecast.instant(example['event_at']):raise ValueError('Forecast outcome/source provenance mismatch.')
                cutoff=forecast.instant(case['prediction_cutoff_utc']);target=forecast.instant(case['target_at_utc'])
                cycle=forecast.instant(case['cycle_at_utc']);valid=forecast.instant(case['forecast_valid_at_utc'])
                if not available(case['archive_availability'],cycle,cutoff):missing_availability+=1;continue
                if not cutoff<=target or not cycle<valid<=target or target-valid>=timedelta(hours=1):raise ValueError('Forecast timing leakage/misalignment.')
                point=read_json(root/'cycles'/(cycle.strftime('%Y%m%d%H')+'.json'))
                if vault.digest(vault.canonical({k:v for k,v in point.items() if k!='fingerprint'}))!=case['point_fingerprint']:raise ValueError('Forecast point checkpoint changed.')
                if point['cycle_at_utc']!=cycle.isoformat() or point['station']!=weather.STATION:raise ValueError('Forecast station/cycle identity mismatch.')
                for receipt in point['source_receipts']:
                    digest=receipt['sha256']
                    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Unsafe source evidence identity.')
                    if receipt['method']=='HEAD' or digest in verified_sources:continue
                    with (root/'sources'/(digest+'.source')).open('rb') as handle:raw=handle.read(8*1024**2+1)
                    if len(raw)!=receipt['bytes'] or vault.digest(raw)!=digest:raise ValueError('Forecast source-byte hash mismatch.')
                    verified_sources.add(digest)
                # Re-normalize derived cases under this exact adapter contract;
                # original point arrays and provider bytes are never changed.
                ob=normalized(point,int((valid-cycle).total_seconds()/3600))
                observations=input_evidence.observations(cutoff)
                vector=forecast.research_vector(observations,dict(issued_at_utc=None,
                    available_by_utc=parsedate_to_datetime(case['archive_availability']['last_modified']).isoformat(),
                    availability_basis='NOAA_original_object_Last-Modified',valid_at_utc=valid.isoformat(),
                    target_at_utc=target.isoformat(),observation=ob),cutoff=cutoff,target=target)
                # Future observed weather is used ONLY as a labeled comparison,
                # never to fill forecast input values or select forecast cases.
                observed=analysis.features(target.isoformat(),input_evidence.observations(target))
                if not observed['matched'] or observed['incomplete_window']:continue
                actual=observed['observation']
                for key in ('temperature_c','dewpoint_c','wind_kt'):
                    if actual.get(key) is not None and ob.get(key) is not None:weather_errors.append(dict(variable=key,error=ob[key]-actual[key]))
                selected.append(dict(case=case,forecast_score=model.score(artifact,vector),observed_score=model.score(artifact,model.vector(observed)),
                    reference_score=weather_reference(vector),missing_features=[n for n,v in zip(model.FEATURES,vector) if v is None]))
            labels=[r['case']['label'] for r in selected]
            warm=[r for r in selected if r['case']['observation'].get('temperature_c') is not None and r['case']['observation']['temperature_c']>0]
            summary=dict(model_version=artifact['model_version'],sha256=sha,input_lead_hours=lead,requested_warning_hours=hours,
                weather_version=input_evidence.manifest['weather_version'],feature_version=artifact['feature_version'],
                nights=len({r['case']['night'] for r in selected}),cases=len(selected),availability_exclusions=missing_availability,
                forecast_diagnostic=metrics(labels,[r['forecast_score'] for r in selected]),paired_observed=metrics(labels,[r['observed_score'] for r in selected]),
                weather_reference=metrics(labels,[r['reference_score'] for r in selected]),
                forecast_above_freezing=metrics([r['case']['label'] for r in warm],[r['forecast_score'] for r in warm]),
                weather_error={k:dict(count=len(v),mae=sum(abs(x) for x in v)/len(v),bias=sum(v)/len(v)) for k in ('temperature_c','dewpoint_c','wind_kt') if (v:=[e['error'] for e in weather_errors if e['variable']==k])},
                missing_feature_counts=dict(Counter(n for r in selected for n in r['missing_features'])),pairs=selected,
                operationally_validated=False,contract_complete_cases=0,
                limitations=['Partial-input/domain-transfer diagnostic, NOT validated forecast skill.',
                    'Cloud fraction is not METAR category; cloud-height datum unresolved; no precipitation intensity or fog forecast equivalence.',
                    'Future interval/trend features unknown; no future observations used to fill them.',
                    'Exact issue/receipt time unknown; NOAA archive-write timestamp is only a conservative available-by bound.',
                    '3/6-hour horizons require separately developed and validated lead-specific models.'])
            reports.append(summary)
    result=dict(schema=VERSION,reports=reports,operationally_validated=False,approved=False,
        verified_source_objects=len(verified_sources),source_cases_sha256=vault.digest((root/'cases.json').read_bytes()))
    write_json(root/'evaluations'/(vault.digest(vault.canonical(result))+'.json'),result)
    write_json(root/'evaluation.json',result)
    for row in reports:print(row['model_version'],row['requested_warning_hours'],row['cases'],row['forecast_diagnostic'],flush=True)
    return result


def availability_audit(directory,output):
    local_only();root=Path(output);prepared=read_json(Path(directory)/'prepared.json')
    first={}
    for row in sorted(prepared['examples'],key=lambda r:(r['event_at'],r['group_id'])):first.setdefault(row['season'],row)
    client=Sources(root/'sources',1);probes=[]
    try:
        for season,row in sorted(first.items()):
            cutoff=forecast.instant(row['event_at'])-timedelta(hours=1)
            cycle=cutoff.replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)
            url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf03.grib2')
            try:
                _,receipt=client.get(url,head=True)
                probes.append(dict(season=season,cycle_at_utc=cycle.isoformat(),cutoff_at_utc=cutoff.isoformat(),
                    source=receipt,available_before_cutoff=available(receipt,cycle,cutoff)))
            except HTTPError as error:probes.append(dict(season=season,url=url,http_status=error.code,available_before_cutoff=False))
        write_json(root/'availability-audit.json',dict(schema=VERSION,probes=probes,
            limitation='One original-object probe per season is not continuous coverage; late archive writes do not prove original timely dissemination.'))
        print([(r['season'],r['available_before_cutoff']) for r in probes],flush=True)
    finally:client.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',action='store_true',required=True)
    p.add_argument('--directory',default='instance/frost-local/phase8/dataset');p.add_argument('--output',default='instance/frost-local/phase8/hrrr')
    p.add_argument('--nights',type=int,default=120);p.add_argument('--budget-mib',type=int,default=512)
    p.add_argument('--season',help='Explicit season, e.g. 2024-2025; use a separate output directory.')
    p.add_argument('--native-hours',action='store_true',help='Verify available-by receipts for intermediate native forecast-trend hours.')
    p.add_argument('--cycle',default='2025-12-01T00:00:00+00:00',help='Explicit UTC cycle for bounded original-GRIB verification.')
    p.add_argument('--baseline-directory')
    p.add_argument('--command',choices=('collect','verify-grib','evaluate','availability'),default='collect')
    args=p.parse_args(argv);local_only()
    if not 1<=args.nights<=366 or not 1<=args.budget_mib<=2048:raise ValueError('Bound the local research request.')
    if args.command=='verify-grib':verify_grib(args.output,args.cycle)
    elif args.command=='evaluate':evaluate(args.directory,args.output,baseline_directory=args.baseline_directory)
    elif args.command=='availability':availability_audit(args.directory,args.output)
    else:collect(args.directory,args.output,nights=args.nights,budget_mib=args.budget_mib,season=args.season,native_hours=args.native_hours)


if __name__=='__main__':main()
