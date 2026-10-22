"""Expiry scheduling is independent of user traffic and survives process restart."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from app.extensions import db
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup
from app.services import neostaffing_employee_recovery as recovery
from app.services import neostaffing_employee_recovery_worker as worker
from tests import test_neostaffing_employee_recovery as fixtures


class RecoveryWorkerTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.EmployeeRecoveryTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app=self.fixture.app

    def test_starts_without_database_work_and_schedules_persisted_deadline(self):
        backup=self.fixture.delete();expires=backup.expires_at
        targets=[];delays=[];signals=[]
        class Wake:
            def wait(self,delay):
                delays.append(delay)
                if len(delays)>1:raise StopIteration()
            def clear(self):pass
            def set(self):signals.append(True)
        class Thread:
            def __init__(self,**kwargs):targets.append(kwargs['target'])
            def start(self):pass
        with patch.object(worker,'Thread',Thread),patch.object(worker,'Event',Wake):
            worker.start_recovery_worker(self.app);self.assertFalse(targets)  # TESTING is opt-in/off.
            self.app.testing=False
            try:
                worker.start_recovery_worker(self.app);worker.start_recovery_worker(self.app)
                self.assertEqual(len(targets),1)
                worker.notify_recovery_worker(self.app);self.assertEqual(signals,[True])
                with patch.object(recovery,'now_utc',return_value=expires-timedelta(seconds=2)):
                    with self.assertRaises(StopIteration):targets[0]()
                self.assertEqual(delays,[10,2])
            finally:
                self.app.testing=True;self.app.extensions.pop('employee_recovery_worker',None)

    def test_restart_sweep_purges_expired_even_without_a_page_visit(self):
        backup=self.fixture.delete();bid=backup.id;expires=backup.expires_at
        self.assertEqual(recovery.purge_expired(at=expires,isolated=True),1);db.session.commit()
        self.assertEqual(db.session.get(Backup,bid).state,'purged')
        self.assertIsNone(db.session.get(Backup,bid).encrypted_payload)

    def test_one_failed_file_does_not_block_other_expired_backups(self):
        self.fixture.signed_record();first=self.fixture.delete();first_id=first.id;expires=first.expires_at
        self.fixture.pid=self.fixture.fixture.peer.id
        second=self.fixture.delete();second_id=second.id
        at=max(expires,second.expires_at)
        self.fixture.files.fail=True
        self.assertEqual(recovery.purge_expired(at=at,isolated=True),1);db.session.commit()
        self.assertEqual(db.session.get(Backup,first_id).state,'deleted')
        self.assertEqual(db.session.get(Backup,second_id).state,'purged')
        self.fixture.files.fail=False
        self.assertEqual(recovery.purge_expired(at=at,isolated=True),1);db.session.commit()
