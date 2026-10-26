"""No extra polling service: wake the existing bounded archive worker."""
import unittest
from unittest.mock import patch,MagicMock
from app.services.neoscorpion_spear_decisions import initialize_delivery_heartbeat


class WorkerTest(unittest.TestCase):
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
