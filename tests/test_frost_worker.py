"""No extra polling service: wake the existing bounded archive worker."""
import unittest
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
from app.services import frost_history as history


class WorkerTest(unittest.TestCase):
    def test_worker_hook_reentry_starts_only_one_existing_loop_and_timer(self):
        app=MagicMock();app.testing=False
        app.extensions={'sqlalchemy':object()}
        app.config={'TESTING':False,'SQLALCHEMY_DATABASE_URI':'postgresql://fixture'}
        hooks=runpy.run_path('gunicorn.conf.py')
        with patch('app.services.neostaffing_employee_recovery_worker.Thread') as thread,\
            patch('app.services.neoscorpion_spear_decisions.threading.Timer') as timer:
            worker=SimpleNamespace(wsgi=app,pid=123)
            hooks['post_worker_init'](worker)
            wake=app.extensions['employee_recovery_worker']
            hooks['post_worker_init'](worker)
            self.assertIs(app.extensions['employee_recovery_worker'],wake)
            self.assertEqual(thread.call_count,1);self.assertEqual(timer.call_count,1)
            thread.return_value.start.assert_called_once();timer.return_value.start.assert_called_once()

    def test_preload_freezes_only_once_at_master_start_not_each_worker_fork(self):
        hooks=runpy.run_path('gunicorn.conf.py')
        for preload in (False,True):
            server=MagicMock();server.cfg.preload_app=preload
            with patch('gc.collect') as collect,patch('gc.freeze') as freeze,\
                patch('app.services.memory_diagnostics.record_process_memory_checkpoint'):
                hooks['on_starting'](server)
                self.assertEqual(collect.call_count,int(preload));self.assertEqual(freeze.call_count,int(preload))
                self.assertEqual(server.app.wsgi.call_count,int(preload))
        self.assertNotIn('pre_fork',hooks)

    def test_gunicorn_parent_defers_timer_and_worker_hook_uses_existing_app(self):
        app=MagicMock()
        with patch('app.create_app',return_value=app),patch('app.services.neostaffing_employee_recovery_worker.start_recovery_worker') as recovery,\
            patch('app.services.neoscorpion_spear_decisions.initialize_delivery_heartbeat') as initialize,\
            patch.dict(sys.modules,{'gunicorn':MagicMock()}):
            runpy.run_path('run.py')
            initialize.assert_not_called();recovery.assert_not_called()
            hooks=runpy.run_path('gunicorn.conf.py')
            hooks['post_worker_init'](SimpleNamespace(wsgi=app,pid=123))
            initialize.assert_called_once_with(app)
            recovery.assert_called_once_with(app)

    def test_other_wsgi_hosts_preserve_existing_startup(self):
        app=MagicMock()
        modules={key:value for key,value in sys.modules.items() if key!='gunicorn'}
        with patch('app.create_app',return_value=app),patch('app.services.neostaffing_employee_recovery_worker.start_recovery_worker') as recovery,\
            patch('app.services.neoscorpion_spear_decisions.initialize_delivery_heartbeat') as initialize,patch.dict(sys.modules,modules,clear=True):
            runpy.run_path('run.py');initialize.assert_called_once_with(app)
            recovery.assert_called_once_with(app)

    def test_memory_guard_uses_container_headroom_and_fails_closed(self):
        from unittest.mock import mock_open
        with patch('pathlib.Path.read_text',side_effect=['300000000','536870912']):
            self.assertTrue(history.memory_budget()['safe'])
        with patch('pathlib.Path.read_text',side_effect=['500000000','536870912']):
            self.assertFalse(history.memory_budget()['safe'])
        with patch('pathlib.Path.read_text',side_effect=OSError('unavailable')):
            self.assertFalse(history.memory_budget()['safe'])

    def test_memory_pressure_does_not_open_storage_or_claim_jobs(self):
        with patch.object(history,'memory_allows_work',return_value=False),patch.object(history.vault,'transport_scope') as transport:
            self.assertEqual(history.drain(1),0)
            transport.assert_not_called()

    def test_memory_threshold_and_minimum_headroom_boundaries(self):
        mib=1024*1024
        for usage,limit,safe in ((384*mib,512*mib,True),(384*mib+1,512*mib,False),
                (128*mib,256*mib,True),(128*mib+1,256*mib,False)):
            with self.subTest(usage=usage,limit=limit):
                with patch('pathlib.Path.read_text',side_effect=[str(usage),str(limit)]):
                    self.assertEqual(history.memory_budget()['safe'],safe)

    def test_testing_apps_never_start_background_network_or_database_work(self):
        app=MagicMock();app.config={'TESTING':True};app.extensions={}
        with patch('app.services.neoscorpion_spear_decisions.threading.Timer') as timer:
            initialize_delivery_heartbeat(app);timer.assert_not_called()

    def test_initialization_is_once_and_defers_existing_worker_until_after_startup(self):
        app=MagicMock();app.config={'TESTING':False,'SQLALCHEMY_DATABASE_URI':'postgresql://fixture'};app.extensions={}
        with patch('app.services.neoscorpion_spear_decisions.threading.Timer') as timer:
            initialize_delivery_heartbeat(app);initialize_delivery_heartbeat(app)
            self.assertEqual(timer.call_count,1);self.assertEqual(timer.call_args.args[0],10)
            timer.return_value.start.assert_called_once()
