"""Workstation-only repeatable prospective capture, with explicit offline gaps.

Use --once from Windows Task Scheduler, or a bounded --count foreground run.
This module does not install a schedule, run on Render, or approve models.
"""
import argparse
import contextlib
import os
import time
from datetime import datetime,timedelta,timezone
from pathlib import Path
from app.services import frost_vault as vault
from scripts.frost_local import local_only,read_json,write_json
from scripts.frost_forecast_validation import Evidence,shadow


@contextlib.contextmanager
def exclusive(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'collector.lock').open('a+b') as handle:
        if handle.seek(0,2)==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle,fcntl.LOCK_UN)


def once(directory,output,*,interval=3600,now=None):
    local_only();root=Path(output);now=now or datetime.now(timezone.utc)
    if now.tzinfo is None or not 300<=interval<=86400:raise ValueError('Use an aware clock and a bounded 5-minute to daily interval.')
    with exclusive(root):
        state_path=root/'collector-state.json'
        prior=read_json(state_path) if state_path.exists() else {}
        if prior.get('last_success_utc') and (now-datetime.fromisoformat(prior['last_success_utc'])).total_seconds()<interval:
            return dict(state='ALREADY CAPTURED',last_success_utc=prior['last_success_utc'])
        event=dict(schema='frost-local-shadow-collector-v1',attempted_at_utc=now.isoformat(),interval_seconds=interval,
            previous_success_utc=prior.get('last_success_utc'),approved=False,operationally_validated=False)
        if prior.get('last_success_utc'):
            expected=datetime.fromisoformat(prior['last_success_utc'])+timedelta(seconds=interval)
            if now>expected+timedelta(seconds=60):
                event['gap']=dict(from_utc=expected.isoformat(),to_utc=now.isoformat(),
                    reason='No successful workstation capture; offline/failed intervals are unknown, not reconstructed')
        try:
            snapshot=shadow(Evidence(directory),root)
            event.update(state='CAPTURED',snapshot_sha256=vault.digest(vault.canonical(snapshot)),
                last_success_utc=snapshot['captured_at_utc'])
        except Exception as error:
            # Never put credentials/provider response bodies into the journal.
            event.update(state='FAILED',error_type=type(error).__name__,last_success_utc=prior.get('last_success_utc'))
            write_json(root/'collector-events'/(vault.digest(vault.canonical(event))+'.json'),event)
            write_json(state_path,event)
            raise
        write_json(root/'collector-events'/(vault.digest(vault.canonical(event))+'.json'),event)
        write_json(state_path,event);return event


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',required=True,action='store_true')
    p.add_argument('--directory',default='instance/frost-local/phase8/dataset')
    p.add_argument('--output',default='instance/frost-local/phase8/prospective')
    p.add_argument('--interval-seconds',type=int,default=3600)
    group=p.add_mutually_exclusive_group(required=True);group.add_argument('--once',action='store_true');group.add_argument('--count',type=int)
    args=p.parse_args(argv);local_only();count=1 if args.once else args.count
    if not 1<=count<=168 or not 300<=args.interval_seconds<=86400:raise ValueError('Bound the local collection interval/count.')
    failed=False
    for index in range(count):
        try:print(once(args.directory,args.output,interval=args.interval_seconds)['state'],flush=True)
        except Exception as error:
            failed=True
            print('Capture failed: '+type(error).__name__+'. Check local collector journal; no operational prediction made.',flush=True)
        if index+1<count:
            # Responsive Ctrl-C and no long blocking wait inside the app agent.
            end=time.monotonic()+args.interval_seconds
            while True:
                remaining=end-time.monotonic()
                if remaining<=0:break
                time.sleep(min(1,remaining))
    return 2 if failed else 0


if __name__=='__main__':raise SystemExit(main())
