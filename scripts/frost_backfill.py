"""Local historical weather backfill. No production DB/storage credentials.

Legacy monthly-package reader/writer, retained for existing local evidence.
ZIPs stay local and are consumed by frost_local assemble. Do not publish rows
to Render. New evidence uses frost_local refresh / assemble instead.
"""
import argparse
import io
import json
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from scripts.frost_local import local_only,read_json,write_json,write_bytes
from app.services import frost_analysis as analysis,frost_weather as weather,frost_vault as vault
from app.services.frost_local_backfill import VERSION,implementation_sha,months_needed,MAX_ZIP,MAX_EXPANDED
from app.services.frost_history import month_bounds


def compute(envelope,directory):
    local_only();job=envelope['job'];root=Path(directory)
    if job['schema']!=VERSION or envelope['sha256']!=vault.digest(vault.canonical(job)) or job['implementation_sha256']!=implementation_sha():
        raise ValueError('Source package/code integrity mismatch. Update checkout and download again.')
    observed=[];files={};evidence=[]
    for month in months_needed(job['records']):
        if month in job['weather']:
            payload=job['weather'][month]['payload'];key=job['weather'][month]['key']
            if not key.endswith('/'+vault.digest(vault.canonical(payload))+'.json.gz'):raise ValueError('Cached weather hash mismatch.')
            write_json(root/'weather'/(month+'.json'),payload)
        else:
            cache=root/'weather'/(month+'.json');raw_path=root/'weather'/(month+'.csv')
            start,end=month_bounds(month)
            if cache.exists() and raw_path.exists():
                payload=read_json(cache);raw=raw_path.read_bytes()
                if payload['raw_sha256']!=vault.digest(raw):raise ValueError('Local weather cache is corrupt.')
            else:
                raw,url=weather.fetch(start,end);rows=weather.parse(raw,start,end)
                if not rows:raise ValueError('No historical weather observations returned; no result generated.')
                raw_key=vault.namespace(job['gateway_id'],'weather')+weather.VERSION+'/'+month+'/'+vault.digest(raw)+'.csv'
                payload=dict(schema_version=weather.VERSION,station=weather.STATION,request_url=url,start_utc=start.isoformat(),end_utc=end.isoformat(),raw_key=raw_key,raw_sha256=vault.digest(raw),
                    fetched_at_utc=datetime.now(timezone.utc).isoformat(),observations=rows,quality='as-is archive; missing fields retained')
                write_bytes(raw_path,raw);write_json(cache,payload)
            files['weather-'+month+'.json']=vault.canonical(payload);files['weather-'+month+'.csv']=raw
        observed.extend(payload['observations'])
        print(f'LOCAL {job["month"]}: reused/downloaded weather {month} ({len(payload["observations"])} observations)',flush=True)
    result=analysis.analyze(job['records'],observed)
    files.update({'job.json':vault.canonical(envelope),'result.json':vault.canonical(result)})
    if sum(map(len,files.values()))>MAX_EXPANDED:raise ValueError('Monthly result exceeds bounded publication limit.')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,raw in files.items():archive.writestr(name,raw)
    raw=stream.getvalue()
    if len(raw)>MAX_ZIP:raise ValueError('Compressed monthly result exceeds publication limit.')
    destination=root/('result-'+job['month']+'-'+envelope['sha256'][:12]+'.zip')
    write_bytes(destination,raw)
    print(json.dumps(dict(location='LOCAL WORKSTATION',month=job['month'],records=len(result['records']),summary=result['summary'],output=str(destination),sha256=vault.digest(raw))),flush=True)
    return destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true',required=True)
    parser.add_argument('--job',required=True)
    parser.add_argument('--directory',default='instance/frost-local/backfill')
    args=parser.parse_args();local_only()
    root=Path(__file__).resolve().parents[1]
    try:relative=Path(args.directory).resolve().relative_to(root)
    except ValueError:relative=None
    if relative is not None and (not relative.parts or relative.parts[0] not in ('instance','_local_archive')):
        raise ValueError('Keep private local data under ignored instance/ or an external private directory.')
    compute(read_json(args.job),args.directory)


if __name__=='__main__':main()
