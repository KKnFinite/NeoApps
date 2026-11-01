"""Receipt and expiry scheduling on disposable loopback PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import create_app
from app.extensions import db
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup, StaffingEmployeeRecoveryAudit as Audit
from app.services import neostaffing_employee_recovery_worker as expiry
from tests import test_background_deadlines as cases
from tests import test_google_motherbrain_parking as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class DeadlinePostgresTest(cases.DeadlineTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema='deadline_'+uuid4().hex
        admin=create_engine(URI,poolclass=NullPool)
        with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def cleanup():
            with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        def local(config):
            return create_app(type('DeadlinePostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'poolclass':NullPool,'connect_args':{'options':
                    f'-csearch_path={self.schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
        with patch.object(fixtures,'create_app',local):super().setUp()

    def test_two_workers_purge_expired_payload_once(self):
        identifier=self.backup(self.naive())
        ready=Barrier(2)
        def worker(_):
            with self.app.app_context():
                try:
                    ready.wait(5)
                    return expiry.recovery_cycle()
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(worker,range(2)))
        db.session.expire_all()
        self.assertEqual(db.session.get(Backup,identifier).state,'purged')
        self.assertIsNone(db.session.get(Backup,identifier).encrypted_payload)
        self.assertEqual(Audit.query.filter_by(recovery_id=identifier,action='purged').count(),1)
