"""LOCAL parity proof for the bounded production HRRR reader; no model fitting."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
from app.services import frost_hrrr_point as light, frost_vault as vault
from scripts import frost_hrrr as original
from scripts.frost_local import local_only,write_json


def verify(directory,cycle):
    local_only();client=original.Sources(Path(directory)/'sources',64)
    try:
        point=original.cycle_point(client,cycle)
        client.receipts=[]
        selected=[h for h in (1,2,3,4,5,6,7,8) if h in point['forecast_hours']]
        other=light.read_point(client,cycle,selected)
        for key in ('latitude','longitude','convergence_degrees','distance_m'):
            if abs(point['point'][key]-other['point'][key])>1e-8:raise ValueError('Grid parity failed: '+key)
        checks=[]
        for h in selected:
            a=original.normalized(point,h);b=light.normalized(other,h)
            if a!=b:raise ValueError('Exact normalized parity failed: '+str(h))
            checks.append(dict(hour=h,fields=len(a),exact=True,values=a))
        result=dict(schema=light.VERSION,cycle_at_utc=cycle.isoformat(),point=other['point'],checks=checks,
            original_point_sha256=vault.digest(vault.canonical(point)),light_point_sha256=other['fingerprint'],
            tolerance='Exact equality for all normalized scalar fields; grid coordinates 1e-8 degrees/metres',
            source_receipts=other['source_receipts'])
        write_json(Path(directory)/('parity-'+cycle.strftime('%Y%m%d%H')+'.json'),result)
        return result
    finally:client.close()


def probe(directory):
    """Real current acquisition, bounded bytes and peak process RSS, LOCAL only."""
    import base64
    import sys
    import ctypes
    from ctypes import wintypes
    import time
    local_only();root=Path(directory)
    def rss():
        import os
        if os.name!='nt':
            from app.services.memory_diagnostics import process_memory_snapshot
            return process_memory_snapshot()['rss_bytes']
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ('peak','rss','q1','q2','q3','q4','page','peakpage')]
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        handle=ctypes.windll.kernel32.GetCurrentProcess
        handle.restype=wintypes.HANDLE
        measure=ctypes.windll.psapi.GetProcessMemoryInfo
        measure.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        if not measure(handle(),ctypes.byref(counters),counters.cb):raise OSError('Memory measurement unavailable.')
        return counters.rss
    samples=[rss()]
    start=time.monotonic()
    def retain(raw,receipt):
        if receipt['method']!='HEAD':
            from scripts.frost_local import write_bytes
            write_bytes(root/'sources'/(receipt['sha256']+'.source'),raw)
        samples.append(rss())
        return receipt
    now=lambda:datetime.now(timezone.utc)
    client=light.Source(now,retain,lambda:True);cutoff=now()
    point,forecasts=light.collect(client,cutoff)
    result=dict(schema=light.VERSION,started_at_utc=cutoff.isoformat(),finished_at_utc=now().isoformat(),
        seconds=time.monotonic()-start,requests=client.calls,downloaded_bytes=client.bytes,
        rss_before=samples[0],rss_after=rss(),rss_sample_peak=max(samples),
        heavy_imports=sorted(set(('numpy','pandas','sklearn','pyproj','eccodes','numcodecs')).intersection(sys.modules)),
        point=point,forecasts=forecasts)
    write_json(root/'current-probe.json',result)
    print({k:v for k,v in result.items() if k not in ('point','forecasts')})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',required=True,action='store_true')
    p.add_argument('--directory',required=True);p.add_argument('--cycle')
    p.add_argument('--probe',action='store_true',help='Bounded real current capture with local memory observations.')
    args=p.parse_args();local_only()
    if args.probe:probe(args.directory);return
    if not args.cycle:p.error('--cycle is required for parity verification')
    value=verify(args.directory,datetime.fromisoformat(args.cycle).astimezone(timezone.utc))
    print(value['cycle_at_utc'],len(value['checks']),'hours: exact parity')


if __name__=='__main__':main()
