"""Real PostgreSQL override persistence, bootstrap and stale-write protection."""
import os
import unittest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from tests import test_neorain_overrides as contracts
from tests import test_neorain_outbound as fixtures
from app import create_app
from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit
from app.services.live_collaboration import entity_version

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class RainOverridePostgresTest(contracts.RainOverrideTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema='rain_override_'+uuid4().hex
        self.admin=create_engine(URI)
        with self.admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def cleanup():
            with self.admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            self.admin.dispose()
        self.addCleanup(cleanup)
        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI=URI
            config.SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema}'}}
            return create_app(config)
        with patch.object(fixtures,'create_app',side_effect=pg_app):super().setUp()

    def test_bootstrap_twice_preserves_mission_and_history(self):
        from app.services.schema_sync import _create_missing_application_tables
        before=(self.arrival.id,self.arrival.flight_number,self.arrival.planned_datetime_utc)
        NeoRainOverrideAudit.__table__.drop(db.session.connection())
        NeoRainMissionOverride.__table__.drop(db.session.connection())
        db.session.commit()
        for _ in range(2):
            _create_missing_application_tables(set(inspect(db.session.connection()).get_table_names()))
            db.session.commit()
        self.assertEqual(before,(self.arrival.id,self.arrival.flight_number,self.arrival.planned_datetime_utc))
        self.assertEqual(self.edit(self.arrival,'sta','0235').status_code,200)
        self.assertEqual(NeoRainMissionOverride.query.count(),1)
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='EDIT').count(),1)

    def test_concurrent_same_version_only_one_edit(self):
        barrier=Barrier(2)
        with self.client.session_transaction() as session:cookie_session=dict(session)
        payload={'mission_id':self.arrival.id,'field':'sta','expected_version':entity_version(self.arrival)}
        operation_id=self.operation.id
        from app.models import SortDateOperation
        def write(value):
            client=self.app.test_client()
            with client.session_transaction() as session:session.update(cookie_session)
            barrier.wait(timeout=5)
            return client.post('/neorain/override',json={**payload,'value':value}).status_code
        with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',side_effect=lambda gateway:db.session.get(SortDateOperation,operation_id)):
            with ThreadPoolExecutor(2) as pool:
                jobs=[pool.submit(write,v) for v in ('0235','0335')]
                self.assertCountEqual([j.result(timeout=20) for j in jobs],[200,409])
        db.session.expire_all()
        self.assertEqual(NeoRainMissionOverride.query.count(),1)
        self.assertEqual(NeoRainOverrideAudit.query.filter_by(action='EDIT').count(),1)
