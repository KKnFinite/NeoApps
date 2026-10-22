"""Real transactions, immutable history and concurrent recovery on disposable PG."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import User,StaffingPerson
from app.models.staffing_employee_record import StaffingEmployeeRecord as Record
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup,StaffingEmployeeRecoveryAudit as Audit
from app.services import neostaffing_employee_recovery as recovery
from tests import test_neostaffing_employee_recovery as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class EmployeeRecoveryPostgresTest(fixtures.EmployeeRecoveryTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in ('localhost','127.0.0.1') or not (url.database or '').startswith('neostaffing_test_'):
            raise ValueError('Disposable loopback PostgreSQL required.')
        self.schema='employee_recovery_'+uuid4().hex
        self.admin=create_engine(URI)
        with self.admin.begin() as connection:connection.execute(text('CREATE SCHEMA '+self.schema))
        def factory(config,**_kwargs):
            config.SQLALCHEMY_DATABASE_URI=URI
            config.SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':'-csearch_path='+self.schema+' -clock_timeout=10000 -cstatement_timeout=30000'}}
            return create_app(config,auto_bootstrap=False)
        with patch('tests.test_neosektor_routes.create_app',side_effect=factory):
            super().setUp()
        # Parent registers an SQLite fixture cleanup. PostgreSQL cleanup is
        # intentionally limited to this generated, loopback-only schema.
        self._cleanups=[]
        self.addCleanup(self.cleanup_schema)

    def cleanup_schema(self):
        db.session.remove();db.engine.dispose();self.fixture.context.pop()
        with self.admin.begin() as connection:connection.execute(text('DROP SCHEMA '+self.schema+' CASCADE'))
        self.admin.dispose()

    def test_two_dispatchers_delete_once_and_preserve_every_backup_row(self):
        self.rich_data();values=self.confirmation();uid=self.actor.id;pid=self.pid
        barrier=Barrier(2)
        def run(_):
            with self.app.app_context():
                actor=db.session.get(User,uid);barrier.wait(timeout=10)
                try:
                    recovery.delete_employee(actor,pid,values);db.session.commit();return 'deleted'
                except recovery.RecoveryConflict:
                    db.session.rollback();return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run,range(2))),['conflict','deleted'])
        self.assertEqual(Backup.query.count(),1);self.assertEqual(Audit.query.filter_by(action='deleted').count(),1)
        self.assertIsNone(db.session.get(StaffingPerson,pid,populate_existing=True))

    def test_two_administrators_restore_idempotently(self):
        self.rich_data();backup=self.delete();bid=backup.id;uid=self.actor.id;pid=self.pid
        barrier=Barrier(2)
        def run(_):
            with self.app.app_context():
                actor=db.session.get(User,uid);barrier.wait(timeout=10)
                result=recovery.restore_employee(actor,bid);db.session.commit();return result
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(list(pool.map(run,range(2))),[pid,pid])
        self.assertEqual(Audit.query.filter_by(action='restored').count(),1)

    def test_old_backup_purge_cannot_race_newer_restore_private_files(self):
        rid,key,raw=self.signed_record()
        first=self.delete();first_id=first.id
        recovery.restore_employee(self.actor,first_id);db.session.commit()
        second=self.delete();second_id=second.id
        from datetime import timedelta
        first=db.session.get(Backup,first_id);first.expires_at=recovery.now_utc()-timedelta(seconds=1)
        db.session.commit();uid=self.actor.id
        entered,release=Event(),Event()
        adapter=self.files;original_get=adapter.get
        def held_get(*args,**kwargs):
            entered.set()
            if not release.wait(10):raise RuntimeError('Fixture restore timeout')
            return original_get(*args,**kwargs)
        def restore():
            with self.app.app_context():
                result=recovery.restore_employee(db.session.get(User,uid),second_id);db.session.commit();return result
        with patch.object(adapter,'get',side_effect=held_get),ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(restore)
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(recovery.purge_expired(isolated=True),0);db.session.commit()
                self.assertEqual(adapter.objects[key],raw)
            finally:release.set()
            self.assertEqual(pending.result(timeout=15),self.pid)
        self.assertEqual(recovery.purge_expired(isolated=True),1);db.session.commit()
        self.assertEqual(adapter.objects[key],raw)
        self.assertIsNotNone(db.session.get(Record,rid))
