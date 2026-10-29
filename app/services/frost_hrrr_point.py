"""Bounded HRRR point reader. No scientific arrays, projection library or fitting.

Reads the SAME Utah NOAA-derived chunks as LOCAL research. Only blocks holding
KRFD values are decoded. Original NOAA Last-Modified remains an availability
bound, not a fabricated issue timestamp. Source bytes are retained by the caller.
"""
import math
import re
import struct
import time
from datetime import timedelta
from email.utils import parsedate_to_datetime
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError

from app.services import frost_forecast as clock, frost_weather as weather, frost_vault as vault

VERSION='krfd-hrrr-light-v1'
VARIABLES=(('2m_above_ground','TMP','K'),('2m_above_ground','DPT','K'),
    ('10m_above_ground','UGRD','m s-1'),('10m_above_ground','VGRD','m s-1'),
    ('surface','GUST','m s-1'),('entire_atmosphere','TCDC','%'),
    ('cloud_ceiling','HGT','m'),('surface','VIS','m'),('surface','APCP_1hr_acc_fcst','kg m-2'),
    ('surface','CRAIN','1'),('surface','CSNOW','1'),('surface','CFRZR','1'),('surface','CICEP','1'))
GRIB_UNITS={(2,0,6,1):'%',(2,0,19,0):'m',(2,0,1,8):'kg m-2',
    (2,0,1,33):'1',(2,0,1,34):'1',(2,0,1,35):'1',(2,0,1,36):'1'}
POINT=dict(x_index=1130,y_index=677,latitude=42.20308995815746,longitude=-89.09953425960873,
    distance_m=1623.5300823240623,grid='NOAA HRRR 1799x1059 Lambert 3km',convergence_degrees=5.2294128779809945)
MAX_SOURCE=5*1024**2
MAX_DECODED=9*1024**2
RESERVE=32*1024**2


def memory_available():
    """Retain the hard guard AND reserve peak transfer/codec allocation space."""
    from flask import current_app
    from app.services import frost_history as jobs
    if not jobs.memory_allows_work():return False
    if current_app.config.get('TESTING'):return True
    budget=jobs.memory_budget()
    return bool(budget['safe'] and budget['usage']+RESERVE<=budget['limit']*.75 and
        budget['limit']-budget['usage']>=128*1024**2+RESERVE)


def numbers(raw,meta,indices):
    """Blosc1/LZ4 byte-shuffle subset. Reject other codecs/filters, never pickle.

    Header/block layout: Blosc/c-blosc README_CHUNK_FORMAT.rst and blosc_d.
    Decode at most one <=1 MiB block at a time; read requested shuffled scalars
    directly instead of constructing the 18/48 x 150 x 150 scientific array.
    """
    dtype=meta['dtype'];formats={'<f2':'e','<f4':'f','<f8':'d','<i8':'q'}
    if dtype not in formats or meta['order']!='C' or meta.get('filters'):raise ValueError('Unsupported HRRR array.')
    size=struct.calcsize(formats[dtype]);expected=math.prod(meta['chunks'] or [1])*size
    if not 0<expected<=MAX_DECODED or any(type(i) is not int or not 0<=i<expected//size for i in indices):raise ValueError('Unbounded HRRR array.')
    compressor=meta['compressor'];out={}
    if not compressor:
        if len(raw)!=expected:raise ValueError('HRRR raw size mismatch.')
        return [struct.unpack_from('<'+formats[dtype],raw,i*size)[0] for i in indices]
    if compressor.get('id')!='blosc' or compressor.get('cname')!='lz4' or compressor.get('shuffle') not in (0,1):raise ValueError('Unverified HRRR compression.')
    if len(raw)<16 or len(raw)>MAX_SOURCE:raise ValueError('Invalid HRRR compressed size.')
    version,codec,flags,typesize,nbytes,blocksize,cbytes=struct.unpack_from('<BBBBIII',raw)
    if version!=2 or codec!=1 or flags&12 or flags>>5!=1 or typesize!=size or nbytes!=expected or cbytes!=len(raw) or not 0<blocksize<=1024**2 or blocksize%size:
        raise ValueError('HRRR compression header mismatch.')
    if flags&2:
        if len(raw)!=16+expected:raise ValueError('Invalid HRRR memcpy chunk.')
        return [struct.unpack_from('<'+formats[dtype],raw,16+i*size)[0] for i in indices]
    count=(nbytes+blocksize-1)//blocksize;table_end=16+4*count
    if table_end>len(raw):raise ValueError('Truncated HRRR block table.')
    from lz4.block import decompress
    for block in sorted({i*size//blocksize for i in indices}):
        length=min(blocksize,nbytes-block*blocksize)
        pos=struct.unpack_from('<I',raw,16+4*block)[0]
        if not table_end<=pos<len(raw):raise ValueError('Invalid HRRR block offset.')
        splits=typesize if not flags&16 and typesize<=16 and length//typesize>=128 and length==blocksize else 1
        if length%splits:raise ValueError('Invalid HRRR split size.')
        partsize=length//splits;parts=[]
        for _ in range(splits):
            if pos+4>len(raw):raise ValueError('Truncated HRRR split.')
            amount=struct.unpack_from('<I',raw,pos)[0];pos+=4
            if not 0<amount<=len(raw)-pos:raise ValueError('Invalid HRRR split.')
            part=raw[pos:pos+amount];pos+=amount
            part=part if amount==partsize else decompress(part,uncompressed_size=partsize)
            if len(part)!=partsize:raise ValueError('HRRR decoded size mismatch.')
            parts.append(part)
        decoded=b''.join(parts);del parts
        for i in (i for i in indices if i*size//blocksize==block):
            offset=i*size-block*blocksize
            scalar=bytes(decoded[j*(length//size)+offset//size] for j in range(size)) if flags&1 else decoded[offset:offset+size]
            out[i]=struct.unpack('<'+formats[dtype],scalar)[0]
        del decoded
    return [out[i] for i in indices]


def normalized(point,hour):
    i=point['forecast_hours'].index(hour)
    def v(name):return point['series'].get(name,[None]*len(point['forecast_hours']))[i]
    t,d=v('TMP'),v('DPT');u,n=v('UGRD'),v('VGRD')
    t=t-273.15 if t is not None else None;d=d-273.15 if d is not None else None
    wind=math.hypot(u,n)*1.943844492 if u is not None and n is not None else None
    direction=((math.degrees(math.atan2(-u,-n))+point['point']['convergence_degrees'])%360) if wind else None
    flags=[code for key,code in [('CRAIN','RA'),('CSNOW','SN'),('CFRZR','FZRA'),('CICEP','PL')] if v(key) is not None and v(key)>0]
    complete=all(v(key) is not None for key in ('CRAIN','CSNOW','CFRZR','CICEP'))
    ceiling=v('HGT');visibility=v('VIS');gust=v('GUST')
    return dict(temperature_c=t,dewpoint_c=d,spread_c=t-d if t is not None and d is not None else None,
        relative_humidity=100*math.exp(17.625*d/(243.04+d)-17.625*t/(243.04+t)) if t is not None and d is not None else None,
        wind_kt=wind,wind_direction_degrees=direction,gust_kt=gust*1.943844492 if gust is not None else None,
        cloud_cover_percent=v('TCDC'),model_cloud_ceiling_height_m=ceiling,ceiling_ft=None,
        visibility_sm=visibility/1609.344 if visibility is not None else None,
        precipitation_1h_mm=v('APCP_1hr_acc_fcst'),weather_codes=' '.join(flags) if complete else None,
        cloud_layers=[],clear_sky=None,precipitation_since_hourly_reset_in=None)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('HRRR redirect rejected.')


class Source:
    """One capture budget; no sessions, caches, threads or retained source bodies."""
    def __init__(self,now,retain,guard):
        self.now=now;self.retain=retain;self.guard=guard;self.deadline=time.monotonic()+90
        self.bytes=0;self.receipts=[];self.calls=0

    def get(self,url,*,head=False):
        import json
        allowed=(re.fullmatch(r'https://hrrrzarr.s3.amazonaws.com/sfc/\d{8}/\d{8}_\d{2}z_fcst.zarr/[A-Za-z0-9_./]+',url) or
            re.fullmatch(r'https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.\d{8}/conus/hrrr.t\d{2}z.wrfsfcf\d{2}.grib2',url))
        if not allowed or '..' in url:raise ValueError('Unverified HRRR URL.')
        if not self.guard():raise RuntimeError('HRRR deferred: 75% / 128 MiB memory protection.')
        remaining=self.deadline-time.monotonic()
        if remaining<=0 or self.calls>=80:raise TimeoutError('HRRR capture request/time budget exhausted.')
        self.calls+=1
        maximum=64*1024 if url.endswith('/.zmetadata') else MAX_SOURCE if url.endswith('/0.4.7') else 32*1024
        with build_opener(NoRedirect).open(Request(url,method='HEAD' if head else 'GET',headers={'User-Agent':'NeoApps KRFD research'}),timeout=min(5,remaining)) as response:
            if response.status!=200:raise ValueError('Unexpected HRRR status.')
            parts=[];length=0
            if response.headers.get('Content-Encoding') not in (None,'identity'):raise ValueError('Unexpected HRRR HTTP encoding.')
            if not head:
                while True:
                    if time.monotonic()>self.deadline:raise TimeoutError('HRRR transfer deadline exceeded.')
                    part=response.read1(min(65536,maximum+1-length))
                    if not part:break
                    length+=len(part)
                    if length>maximum:raise ValueError('HRRR source size exceeded.')
                    parts.append(part)
            raw=b''.join(parts)
            receipt=dict(url=url,method='HEAD' if head else 'GET',retrieved_at_utc=self.now().isoformat(),
                sha256=vault.digest(raw),bytes=len(raw),last_modified=response.headers.get('Last-Modified'),etag=response.headers.get('ETag'))
        self.bytes+=len(raw)
        if len(raw)>maximum or self.bytes>18*1024**2:raise ValueError('HRRR transfer budget exceeded.')
        if time.monotonic()>self.deadline:raise TimeoutError('HRRR capture deadline exceeded.')
        receipt=self.retain(raw,receipt)
        self.receipts.append(receipt)
        return raw,receipt


def read_point(client,cycle,hours):
    import json
    base='https://hrrrzarr.s3.amazonaws.com/'+cycle.strftime('sfc/%Y%m%d/%Y%m%d_%Hz_fcst.zarr/')
    series={};times=None;missing=[]
    for level,name,unit in VARIABLES:
        root=base+level+'/'+name
        try:meta=json.loads(client.get(root+'/.zmetadata')[0])['metadata']
        except HTTPError as error:
            if error.code not in (403,404):raise
            missing.append(dict(variable=name,http_status=error.code));continue
        definition=meta[level+'/'+name+'/.zarray'];attrs=meta[level+'/'+name+'/.zattrs']
        if (attrs.get('units') or GRIB_UNITS.get(tuple(attrs.get('GRIB_PARAM',[]))))!=unit:raise ValueError('HRRR units changed: '+name)
        if definition['chunks'][1:]!=[150,150] or definition['shape'][1:]!=[1059,1799] or not 1<=definition['chunks'][0]<=48 or definition['shape'][0]!=definition['chunks'][0]:raise ValueError('HRRR grid changed.')
        def values(key,indices):return numbers(client.get(root+'/'+key+'/0')[0],meta[key+'/.zarray'],indices)
        n=definition['chunks'][0]
        periods=values('forecast_period',list(range(n)))
        if meta['forecast_period/.zattrs']['units']!='hours' or periods!=list(range(1,n+1)):raise ValueError('HRRR periods changed.')
        reference=values('forecast_reference_time',[0])[0]
        valid=values('time',list(range(n)))
        if (meta['forecast_reference_time/.zattrs']['units']!='hours since 1970-01-01' or meta['time/.zattrs']['units']!='hours since 1970-01-01' or
            reference!=cycle.timestamp()/3600 or any(t!=reference+h for t,h in zip(valid,periods))):raise ValueError('HRRR cycle/valid time mismatch.')
        if times is not None and times!=periods:raise ValueError('HRRR variables disagree on time.')
        times=periods
        if name=='TMP':
            # Exact projected coordinate pins proved by the LOCAL pyproj/ecCodes
            # reader. Check each neighboring axis too, not just array dimensions.
            for axis,index in [('x',1130),('y',677)]:
                if meta['projection_'+axis+'_coordinate/.zattrs']['units']!='m':raise ValueError('HRRR projected units changed.')
                axis_values=values('projection_'+axis+'_coordinate',[index-1,index,index+1])
                center=692479.8574780696 if axis=='x' else 443693.8474433364
                expected=[center+(i-index)*3000 for i in (index-1,index,index+1)]
                if axis_values!=expected:raise ValueError('KRFD projected grid cell changed.')
        try:raw,_=client.get(root+'/'+level+'/'+name+'/0.4.7')
        except HTTPError as error:
            if error.code not in (403,404):raise
            missing.append(dict(variable=name,http_status=error.code));continue
        offsets=[(h-1)*22500+77*150+80 for h in hours]
        data=numbers(raw,definition,offsets)
        series[name]=[value if math.isfinite(value) and value!=definition.get('fill_value') else None for value in data]
    point=dict(schema=VERSION,cycle_at_utc=cycle.isoformat(),station=weather.STATION,point=POINT,
        forecast_hours=hours,series=series,missing=missing,source_receipts=client.receipts)
    point['fingerprint']=vault.digest(vault.canonical(point))
    return point


def collect(client,cutoff):
    # The last fully disseminated run; no probing many cycles or old archives.
    cycle=cutoff.replace(minute=0,second=0,microsecond=0)-timedelta(hours=2)
    last=int(((cutoff+timedelta(hours=6)).replace(minute=0,second=0,microsecond=0)-cycle).total_seconds()/3600)
    hours=list(range(1,last+1));availability={}
    for h in hours:
        url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf')+f'{h:02d}.grib2'
        _,receipt=client.get(url,head=True)
        available=parsedate_to_datetime(receipt['last_modified'])
        if available.tzinfo is None or not cycle<=available<=cutoff:raise ValueError('HRRR original object was unavailable at cutoff.')
        availability[h]=available.isoformat()
    point=read_point(client,cycle,hours)
    forecasts=[dict(station=weather.STATION,cycle_at_utc=cycle.isoformat(),issued_at_utc=None,
        available_by_utc=availability[h],availability_basis='NOAA_original_object_Last-Modified',
        valid_at_utc=(cycle+timedelta(hours=h)).isoformat(),source_sha256=point['fingerprint'],values=normalized(point,h)) for h in hours]
    return point,forecasts
