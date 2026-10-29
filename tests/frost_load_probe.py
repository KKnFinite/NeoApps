"""LOCAL loopback-only concurrency probe; client stays outside server cgroup.

python -m tests.frost_load_probe --directory instance/phase17-baseline-load
Start tests.frost_load_fixture:app with one non-preloaded sync Gunicorn worker
inside a 512 MiB/50% CPU systemd service. No production URL option is exposed.
"""
import argparse
import concurrent.futures
import json
import re
import statistics
import threading
import time
import subprocess
import sys
from pathlib import Path
import requests

ROUTES = ('/motherbrain', '/neorain/inbound', '/neorain/outbound',
          '/neoscorpion/fuel-dispatch', '/neostaffing', '/neoermac', '/neosektor',
          '/neosubzero/operations', '/neosubzero/ucc')
ORIGIN = 'http://127.0.0.1:5197'


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--cgroup', default='/sys/fs/cgroup/system.slice/neoapps-phase17-baseline.service')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--priority', action='store_true')
    parser.add_argument('--writes', action='store_true', help='Synthetic NeoRain add/update/delete with CSRF/version checks')
    parser.add_argument('--soak-seconds',type=int,default=0,choices=range(0,601),metavar='0..600')
    args = parser.parse_args(); directory = args.directory.resolve()
    assert directory.is_relative_to(Path('instance').resolve()) and 'phase17' in directory.name
    if args.start:
        subprocess.run(['systemd-run', '--unit=neoapps-phase17-baseline', '--collect',
            '--property=MemoryMax=536870912', '--property=CPUQuota=50%', '--property=RuntimeMaxSec=900',
            '--property=WorkingDirectory='+str(Path.cwd()), '--setenv=FROST_LOAD_DIRECTORY='+str(directory),
            '--setenv=FROST_LOAD_PRIORITY='+str(int(args.priority)),
            sys.executable, '-m', 'gunicorn', 'tests.frost_load_fixture:app', '--bind=127.0.0.1:5197',
            '--workers=1', '--timeout=120'], check=True)
        for _ in range(120):
            if subprocess.run(['systemctl','is-failed','--quiet','neoapps-phase17-baseline']).returncode == 0:
                raise RuntimeError('Isolated fixture failed; inspect its journal')
            try:
                if requests.get(ORIGIN+'/healthz', timeout=2).status_code == 200: break
            except requests.RequestException: pass
            time.sleep(1)
        else: raise RuntimeError('Isolated server did not start')
    samples = []; stop = threading.Event(); cgroup = Path(args.cgroup)
    def sample():
        while not stop.wait(.25):
            try:
                processes = []
                for pid in (cgroup / 'cgroup.procs').read_text().split():
                    fields = {}
                    for line in Path('/proc', pid, 'smaps_rollup').read_text().splitlines():
                        if line.startswith(('Rss:', 'Pss:')):
                            key, value, _ = line.split(); fields[key.rstrip(':')] = int(value)*1024
                    processes.append(dict(pid=int(pid), **fields))
                samples.append(dict(at=time.time(), memory=int((cgroup/'memory.current').read_text()),
                    cpu=(cgroup/'cpu.stat').read_text(), pressure=(cgroup/'cpu.pressure').read_text(), processes=processes))
            except (OSError, ValueError): pass
    threading.Thread(target=sample, daemon=True).start()
    sessions = []; tokens=[]
    for i in range(20):
        session = requests.Session()
        response = session.get(ORIGIN + '/login', timeout=30)
        token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text)
        response = session.post(ORIGIN + '/login', data=dict(email=f'load-{i}@example.test',
            password='LoadFixture123!', csrf_token=token[1] if token else ''), timeout=30)
        assert response.status_code == 200 and '/login' not in response.url, response.status_code
        sessions.append(session)
        # Authentication rotates the CSRF session; use the post-login form token.
        token=re.search(r'name="csrf_token"[^>]*value="([^"]+)"',response.text)
        assert token, 'Authenticated page did not expose CSRF token'
        tokens.append(token[1] if token else '')
    missions=json.loads((directory/'fixture-missions.json').read_text()) if args.writes else []
    report = dict(synthetic=True, capacity_guarantee=False, database='isolated SQLite',
                  server_limits=dict(memory_mib=512, cpu=0.5, workers=1, preload=False),
                  provider='offline verified HRRR bytes + simulated 10ms/request', writes=args.writes, runs=[])
    for active in (False, True):
        flag = directory / 'active'
        if active: flag.touch()
        for users in (5, 10, 20):
            started_at=time.time()
            deadline = time.monotonic() + args.seconds
            results = []
            def run(index):
                n = index; entry=None; update=False
                while time.monotonic() < deadline:
                    path = ROUTES[n % len(ROUTES)]; started = time.monotonic()
                    try:
                        response = sessions[index].get(ORIGIN + path, timeout=45)
                        code = 401 if '/login' in response.url else response.status_code
                    except requests.RequestException: code = 0
                    results.append(dict(path=path, status=code, seconds=time.monotonic() - started))
                    if args.writes and n % 5 == 0:
                        mission=missions[index]
                        action='add' if entry is None else 'delete' if update else 'update'
                        payload=dict(mission_id=mission['id'],board=mission['board'],action=action,
                            minutes='2',code='RS',notes='ISOLATED LOAD '+str(n),csrf_token=tokens[index])
                        if entry:payload.update(delay_id=entry['id'],expected_version=entry['version'])
                        started=time.monotonic()
                        try:
                            response=sessions[index].post(ORIGIN+'/neorain/delay-info',data=payload,
                                headers={'X-Requested-With':'XMLHttpRequest'},timeout=45)
                            value=response.json();code=response.status_code if value.get('ok') else 0
                            detail='' if code==200 else str(value.get('error','Request rejected'))[:200]
                            if code==200:
                                if action=='delete':entry=None;update=False
                                else:
                                    version=re.search(r'name="expected_version" value="([^"]+)"',value['html'])[1]
                                    entry=dict(id=value['delay_id'],version=version);update=action=='update'
                        except (requests.RequestException,ValueError,TypeError) as error:code=0;detail=type(error).__name__
                        results.append(dict(path='/neorain/delay-info',action=action,status=code,error=detail,seconds=time.monotonic()-started))
                    n += 1; time.sleep(.25)
            with concurrent.futures.ThreadPoolExecutor(max_workers=users) as pool:
                list(pool.map(run, range(users)))
            values = sorted(r['seconds'] for r in results)
            result = dict(active=active, users=users, requests=len(values),started_at=started_at,ended_at=time.time(),
                p50=statistics.median(values), p95=values[min(len(values)-1,int(.95*len(values)))],
                p99=values[min(len(values)-1,int(.99*len(values)))],
                failures=[r for r in results if r['status'] != 200],
                mutations=sum(r['path']=='/neorain/delay-info' for r in results),
                routes={p:sum(r['path']==p for r in results) for p in ROUTES})
            report['runs'].append(result)
            (directory / 'load-report.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(dict(result,failures=result['failures'][:3],failure_count=len(result['failures']))), flush=True)
        if flag.exists(): flag.unlink()
    # Recovery is measured without request traffic, not inferred from deferrals.
    before=(directory/'cycles.jsonl').read_text().splitlines()
    flag.touch();time.sleep(30);flag.unlink()
    after=(directory/'cycles.jsonl').read_text().splitlines()
    report['quiet_recovery_cycles']=[json.loads(v) for v in after[len(before):]]
    if args.soak_seconds:
        flag.touch();deadline=time.monotonic()+args.soak_seconds;soak=[];n=0
        report['soak_started_at']=time.time()
        while time.monotonic()<deadline:
            started=time.monotonic()
            try:
                response=sessions[0].get(ORIGIN+ROUTES[n%len(ROUTES)],timeout=45)
                code=401 if '/login' in response.url else response.status_code
            except requests.RequestException:code=0
            soak.append(dict(status=code,seconds=time.monotonic()-started));n+=1;time.sleep(3)
        flag.unlink();report['soak_ended_at']=time.time();report['soak_requests']=soak
    (directory/'load-report.json').write_text(json.dumps(report,indent=2))
    for session in sessions: session.close()
    stop.set()
    (directory / 'resource-samples.json').write_text(json.dumps(samples))
    if args.start: subprocess.run(['systemctl','stop','neoapps-phase17-baseline'], check=True)


if __name__ == '__main__': main()
