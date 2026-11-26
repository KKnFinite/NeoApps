"""Version conflicts and native report contracts on disposable PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import NeoRainMissionNote, SortDateOperation
from app.services.live_collaboration import entity_version
from tests import test_neorain_full_native as contracts

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@skipUnless(URI, "Requires disposable loopback PostgreSQL")
class NativeRainPostgresTest(contracts.FullNativeRainTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ("localhost", "127.0.0.1") or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing to run against a non-disposable database.")
        schema = "rain_notes_" + uuid4().hex
        engine = create_engine(URI)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()

        self.addCleanup(cleanup)

        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": f"-csearch_path={schema}"}}
            return create_app(config)

        with patch.object(contracts.fixtures.fixtures, "create_app", side_effect=pg_app):
            super().setUp()

    def test_concurrent_note_update_preserves_the_winning_version(self):
        note = NeoRainMissionNote(sort_date_mission_id=self.departure.id, text="Original")
        db.session.add(note)
        db.session.commit()
        operation_id = self.operation.id
        payload = {"board": "outbound", "operation_id": operation_id, "mission_id": self.departure.id,
                   "action": "update", "delay_id": f"note-{note.id}", "expected_version": entity_version(note)}
        with self.client.session_transaction() as session:
            cookies = dict(session)
        barrier = Barrier(2)

        def write(value):
            client = self.app.test_client()
            with client.session_transaction() as session:
                session.update(cookies)
            barrier.wait(timeout=5)
            return client.post("/neorain/delay-info", data={**payload, "notes": value}, headers={"X-Requested-With": "XMLHttpRequest"}).status_code

        with patch("app.neonodes.neorain.routes.current_neorain_outbound_operation",
                   side_effect=lambda _gateway: db.session.get(SortDateOperation, operation_id)):
            with ThreadPoolExecutor(2) as pool:
                jobs = [pool.submit(write, value) for value in ("First writer", "Second writer")]
                self.assertCountEqual([job.result(timeout=20) for job in jobs], [200, 409])
        db.session.expire_all()
        self.assertIn(note.text, ("First writer", "Second writer"))
        self.assertEqual(note.version, 2)
