"""Run Delay Info authorization/persistence contracts in disposable PostgreSQL."""
import os
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import NeoRainDelayInfo, SortDateOperation
from app.services.live_collaboration import entity_version
from app.services.neorain_delay_info_schema import sync_neorain_delay_info_schema
from tests import test_neorain_delay_entry as contracts

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class RainDelayEntryPostgresTest(contracts.RainDelayEntryTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema = 'rain_delay_' + uuid4().hex
        admin = create_engine(URI)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def cleanup():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {'connect_args':{'options':f'-csearch_path={schema}'}}
            return create_app(config)
        with patch.object(contracts.fixtures, 'create_app', side_effect=pg_app):
            super().setUp()

    def test_legacy_schema_retains_native_delay_and_exact_historical_note(self):
        self.assertEqual(self.post().status_code, 200)
        historical = self.missions["inbound"]
        self.operation.archived_at_utc = self.operation.created_at + timedelta(days=1)
        db.session.flush()
        conn = db.session.connection()
        # Reproduce old production shapes inside this test's private schema.
        conn.exec_driver_sql("ALTER TABLE neorain_mission_notes DROP COLUMN minutes, DROP COLUMN code")
        conn.exec_driver_sql("ALTER TABLE neorain_delay_info ALTER COLUMN minutes SET NOT NULL, ALTER COLUMN code SET NOT NULL")
        old_cols = "id, sort_date_mission_id, text, created_by_user_id, updated_by_user_id, created_at, updated_at, version"
        original = conn.execute(text(f"""
            INSERT INTO neorain_mission_notes (sort_date_mission_id, text, created_by_user_id, updated_by_user_id, created_at, updated_at, version)
            VALUES (:mission_id, :note, :actor, :actor, '2026-10-23 04:05:02.500001', '2026-10-23 04:09:34.600002', 7)
            RETURNING {old_cols}
        """), dict(mission_id=historical.id, note="Aircraft reassigned\r\nto East ramp — prior Sort", actor=self.user.id)).one()
        coded = conn.exec_driver_sql("SELECT * FROM neorain_delay_info").one()
        for _ in range(2):
            sync_neorain_delay_info_schema(conn)
        self.assertEqual(conn.exec_driver_sql(f"SELECT {old_cols} FROM neorain_mission_notes").one(), original)
        self.assertEqual(conn.exec_driver_sql("SELECT * FROM neorain_delay_info").one(), coded)
        self.assertEqual(conn.exec_driver_sql("SELECT minutes, code FROM neorain_mission_notes").one(), (None, None))
        self.assertTrue(all(column["nullable"] for column in inspect(conn).get_columns("neorain_delay_info")
                            if column["name"] in ("minutes", "code")))
        db.session.commit()
        detail = self.client.get(f"/neorain/daily-briefing/state?operation_id={self.operation.id}").json["briefing_detailed"]
        self.assertEqual(detail.count("Aircraft reassigned\r\nto East ramp — prior Sort"), 1)
        self.assertIn("10 RS - Canonical mission delay", detail)
        self.assertEqual(self.post(minutes="1", code="", notes="Archived writes rejected").status_code, 409)

    def test_concurrent_corrections_preserve_one_winner(self):
        self.assertEqual(self.post().status_code, 200)
        row = NeoRainDelayInfo.query.one()
        payload = dict(board='inbound', mission_id=self.missions['inbound'].id,
            action='update', delay_id=row.id, expected_version=entity_version(row), minutes='10', code='RS')
        operation_id = self.operation.id
        with self.client.session_transaction() as session:
            cookies = dict(session)
        barrier = Barrier(2)
        def write(notes):
            client = self.app.test_client()
            with client.session_transaction() as session:
                session.update(cookies)
            barrier.wait(timeout=5)
            return client.post('/neorain/delay-info', data={**payload,'notes':notes},
                headers={'X-Requested-With':'XMLHttpRequest'}).status_code
        with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
                   side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)):
            with ThreadPoolExecutor(2) as pool:
                jobs = [pool.submit(write, value) for value in ('First correction','Second correction')]
                self.assertCountEqual([job.result(timeout=20) for job in jobs], [200,409])
        db.session.expire_all()
        self.assertIn(NeoRainDelayInfo.query.one().notes, ('First correction','Second correction'))
