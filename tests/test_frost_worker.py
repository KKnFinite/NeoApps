"""No extra polling service: wake the existing bounded archive worker."""
import unittest
from unittest.mock import patch,MagicMock
from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat
from app.services import frost_history as history


class WorkerTest(unittest.TestCase):
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
