"""Synthetic PostgreSQL bootstrap, durable commit and competing delivery claims."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import StaffingPerson
from app.models.staffing_sheet_sync import StaffingSheetSync as Receipt
from app.services import neostaffing_sheet_sync as sync
from app.services.neostaffing_seniority_schema import sync_seniority_schema
from tests import test_neostaffing_sheet_sync as base
from tests import test_neostaffing_shift_flow as fixtures

URI=os.getenv('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires isolated loopback PostgreSQL')
class StaffingSheetPostgresTest(base.StaffingSheetSyncTest):
    def setUp(self):
        u=make_url(URI)
        if u.host not in {'127.0.0.1','localhost'} or not u.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-test database')
        self.schema='staffing_sheet_'+uuid4().hex;self.engine=create_engine(URI)
        with self.engine.begin() as c:c.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def app(config):
            config=type('SheetPostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema}'}}))
            return create_app(config)
        with patch.object(fixtures,'create_app',app):super().setUp()

    def tearDown(self):
        db.session.remove();db.engine.dispose();self.context.pop();self.engine.dispose()

    def test_bootstrap_widens_legacy_check_without_rewriting_employees(self):
        person=self.employee();before=person.employee_id,person.first_name,person.seniority_date
        from app.models.staffing_person import STAFFING_DATABASE_CLASSIFICATIONS
        old=','.join("'"+v+"'" for v in STAFFING_DATABASE_CLASSIFICATIONS if v!='pre_seniority')
        db.session.execute(text('ALTER TABLE staffing_people DROP CONSTRAINT ck_staffing_people_classification'))
        db.session.execute(text(f'ALTER TABLE staffing_people ADD CONSTRAINT ck_staffing_people_classification CHECK (classification IN ({old}))'))
        db.session.commit();sync_seniority_schema();sync_seniority_schema();db.session.commit()
        person.classification='pre_seniority';db.session.commit()
        self.assertEqual((person.employee_id,person.first_name,person.seniority_date),before)

    def test_simultaneous_commits_do_not_lose_dirty_revisions(self):
        person=self.employee();pid=person.id
        self.app.config['NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED']=True
        sync._dirty(db.session(),initial=True);db.session.commit();ready=Barrier(2)
        def commit(index):
            with self.app.app_context():
                try:
                    p=db.session.get(StaffingPerson,pid);ready.wait(5)
                    p.first_name=f'Worker {index}';db.session.commit()
                finally:db.session.remove()
        with ThreadPoolExecutor(2) as pool:list(pool.map(commit,(0,1)))
        self.assertEqual(db.session.get(Receipt,1,populate_existing=True).requested_revision,3)

    def test_two_workers_claim_once_and_never_hold_db_during_google(self):
        self.employee();self.app.config['NEOSTAFFING_TEST_SHEETS_SYNC_ENABLED']=True
        sync._dirty(db.session(),initial=True);db.session.commit();ready=Barrier(2);calls=[];lock=Lock()
        class Provider(base.Adapter):
            def __enter__(provider):
                self.assertFalse(db.session().in_transaction())
                with lock:calls.append(1)
                return provider
        def run(_index):
            with self.app.app_context():
                try:ready.wait(5);return sync.drain(adapter_factory=Provider)
                finally:db.session.remove()
        db.session.remove()
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(run,(0,1)))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(calls,[1])
