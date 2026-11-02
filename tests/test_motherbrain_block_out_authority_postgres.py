"""R07 row locks with real, disposable, loopback PostgreSQL transactions."""
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import SortDateMission, SortDateOperation
from app.neomotherbrain.routes import _mission_or_404
from app.services import google_motherbrain_live_missions as primary
from app.services import google_rain_live_milestones as rain
from tests import test_motherbrain_block_out_authority as contracts

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


def postgres_setup(case, module, setup):
    url = make_url(URI)
    if url.host not in ("127.0.0.1", "localhost") or not url.database.startswith("neostaffing_test_"):
        raise RuntimeError("Refusing non-disposable PostgreSQL")
    schema = "blockout_authority_" + uuid4().hex
    case.admin = create_engine(URI)
    with case.admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    def cleanup():
        with case.admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        case.admin.dispose()
    case.addCleanup(cleanup)
    def app(config):
        config.SQLALCHEMY_DATABASE_URI = URI
        config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": f"-csearch_path={schema}"}}
        return create_app(config)
    with patch.object(module, "create_app", side_effect=app):
        setup()


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class BlockOutAuthorityPostgresTest(contracts.MotherBrainBlockOutAuthorityTest):
    def setUp(self):
        postgres_setup(self, contracts.fixtures, super().setUp)

    def test_import_started_before_manual_commit_waits_and_preserves_fresh_authority(self):
        for module in (rain, primary):
            with self.subTest(importer=module.__name__):
                mission = self._mission("UPS0910" if module is rain else "UPS0948", "LAX")
                # Canonicalize the fixture's initially absent UTC schedule first,
                # so the concurrent edit changes Block-Out, not match identity.
                contracts.manual_block_out(mission, self.operation, "")
                mission_id, operation_id = mission.id, self.operation.id
                loaded, proceed, locking = Event(), Event(), Event()
                pid = []
                original = module.lock_current_departure_for_google_import
                def delayed_lock(operation, candidate):
                    loaded.set()
                    if not proceed.wait(timeout=10):
                        raise RuntimeError("Test did not release import")
                    locking.set()
                    return original(operation, candidate)
                def import_row():
                    with self.app.app_context():
                        try:
                            pid.append(db.session.execute(text("SELECT pg_backend_pid()")).scalar_one())
                            operation = db.session.get(SortDateOperation, operation_id)
                            candidate = db.session.get(SortDateMission, mission_id)
                            if module is rain:
                                result = rain.apply_google_rain_departure_milestones(operation,
                                    rows=[self._row(candidate.flight_number, block="2:45")])
                            else:
                                result = primary.apply_google_motherbrain_live_mission_batch(operation,
                                    "departure", [contracts.primary_row(candidate)], now=datetime(2026, 8, 11, 10))
                            db.session.commit()
                            return result
                        finally:
                            db.session.remove()
                with patch.object(module, "lock_current_departure_for_google_import", delayed_lock):
                    with ThreadPoolExecutor(1) as pool:
                        job = pool.submit(import_row)
                        try:
                            self.assertTrue(loaded.wait(timeout=5))
                            # The actual editor's row lock and canonical writer.
                            locked = _mission_or_404(self.operation, mission_id, for_update=True)
                            contracts.manual_block_out(locked, self.operation, "2026-08-11 07:00", commit=False)
                            db.session.flush()
                            proceed.set()
                            self.assertTrue(locking.wait(timeout=5))
                            deadline = time.monotonic() + 5
                            waiting = False
                            while time.monotonic() < deadline:
                                with self.admin.connect() as conn:
                                    waiting = conn.execute(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid[0]}).scalar()
                                if waiting:
                                    break
                                time.sleep(.01)
                            self.assertTrue(waiting, "Google must wait on the editor's canonical row lock")
                            db.session.commit()
                        finally:
                            db.session.rollback()
                            proceed.set()
                        result = job.result(timeout=10)
                db.session.expire_all()
                self.assert_native(db.session.get(SortDateMission, mission_id))
                self.assertEqual(result["applied_count"], 1)

    def test_stale_matched_identity_is_rejected_without_applying_google_time(self):
        mission = self._mission("UPS0910", "LAX")
        mission_id, operation_id = mission.id, self.operation.id
        loaded, proceed = Event(), Event()
        original = rain.lock_current_departure_for_google_import
        def delayed_lock(operation, candidate):
            loaded.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError("Test did not release import")
            return original(operation, candidate)
        def import_row():
            with self.app.app_context():
                try:
                    operation = db.session.get(SortDateOperation, operation_id)
                    result = rain.apply_google_rain_departure_milestones(operation,
                        rows=[self._row("UPS0910", block="2:45")])
                    db.session.commit()
                    return result
                finally:
                    db.session.remove()
        with patch.object(rain, "lock_current_departure_for_google_import", delayed_lock):
            with ThreadPoolExecutor(1) as pool:
                job = pool.submit(import_row)
                try:
                    self.assertTrue(loaded.wait(timeout=5))
                    locked = _mission_or_404(self.operation, mission_id, for_update=True)
                    form = contracts._mission_form_from_model(locked)
                    form.update(wave="1", assigned_tail_number="N456UP")
                    contracts._apply_mission_form(locked, self.operation, form)
                    db.session.commit()
                finally:
                    db.session.rollback()
                    proceed.set()
                result = job.result(timeout=10)
        db.session.expire_all()
        current = db.session.get(SortDateMission, mission_id)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(current.assigned_tail_number, "N456UP")
        self.assertIsNone(current.actual_block_out_datetime_utc)


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class BlockOutOverridePostgresTest(contracts.BlockOutExplicitOverrideTest):
    def setUp(self):
        postgres_setup(self, contracts.override_fixtures.fixtures, super().setUp)
