"""Real Linux Gunicorn processes; disposable SQLite, no production credentials."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


@unittest.skipUnless(sys.platform=='linux', 'Gunicorn requires Linux')
class GunicornRecoveryLifecycleTest(unittest.TestCase):
    def test_preload_nonpreload_two_workers_and_restart(self):
        for preload in (False,True):
            with self.subTest(preload=preload),tempfile.TemporaryDirectory(prefix='neoapps-runtime-') as directory:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                environment=dict(os.environ,NEOAPPS_LIFECYCLE_SQLITE=directory+'/fixture.db',
                    NEOAPPS_LIFECYCLE_PRELOAD=str(int(preload)),PYTHONUNBUFFERED='1')
                with open(directory+'/gunicorn.log','w+') as log:
                    command=[sys.executable,'-m','gunicorn','tests.frost_runtime_fixture:application',
                        '--bind',f'127.0.0.1:{port}','--workers','2','--timeout','30',
                        '--config','gunicorn.conf.py']
                    if preload:command.append('--preload')
                    process=subprocess.Popen(command,env=environment,stdout=log,stderr=log)
                    try:
                        def collect(excluded=()):
                            result={};deadline=time.monotonic()+45
                            while time.monotonic()<deadline:
                                try:
                                    with urlopen(f'http://127.0.0.1:{port}/runtime',timeout=2) as response:
                                        row=json.load(response)
                                    if row['pid'] not in excluded:result[row['pid']]=row
                                    if len(result)==2:return result
                                except OSError:pass
                                if process.poll() is not None:
                                    log.seek(0);self.fail(log.read())
                                time.sleep(.05)
                            log.seek(0);self.fail('Workers did not respond: '+log.read())
                        workers=collect()
                        for row in workers.values():
                            self.assertEqual(row['expiry_threads'],1)
                            self.assertTrue(row['wake_set']);self.assertTrue(row['heartbeat'])
                            self.assertEqual(row['expiry_checks'],1)
                            self.assertTrue(row['gc_enabled'])
                            self.assertFalse(row['import_started_expiry'])
                        parent_tasks=Path(f'/proc/{process.pid}/task')
                        # Gunicorn 26 also owns its existing control-socket thread.
                        self.assertLessEqual(len(list(parent_tasks.iterdir())),2)
                        victim=next(iter(workers));os.kill(victim,signal.SIGTERM)
                        replacements=collect(excluded=(victim,))
                        self.assertNotIn(victim,replacements)
                        self.assertEqual(len(set(replacements)-set(workers)),1)
                        for row in replacements.values():self.assertEqual(row['expiry_threads'],1)
                    finally:
                        process.terminate()
                        try:process.wait(timeout=15)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
