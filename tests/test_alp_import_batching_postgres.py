"""Real PostgreSQL ALP locks versus canonical manual edits, in disposable schemas."""
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from sqlalchemy import text

from app.extensions import db
from app.models import SortDateMission, SortDateOperation
from app.neomotherbrain.routes import _mission_or_404, _mission_form_from_model, _apply_mission_form
from app.services import alp_import
from tests import test_motherbrain_block_out_authority as authority
from tests.test_motherbrain_block_out_authority_postgres import postgres_setup
from tests import test_alp_import_batching as contracts


@unittest.skipUnless(os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL"), "Requires disposable loopback PostgreSQL")
class AlpAuthorityPostgresTest(contracts.AlpAuthorityTest):
    def setUp(self):
        postgres_setup(self, authority.fixtures, super().setUp)

    def _race(self, change_identity):
        mission = self._mission("UPS0910", "LAX")
        authority.manual_block_out(mission, self.operation, "")
        mid, oid = mission.id, self.operation.id
        loaded, proceed, locking = Event(), Event(), Event()
        original = alp_import._lock_matched_missions
        backend = []
        def delayed_lock(*args):
            loaded.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError("Import not released by fixture")
            locking.set()
            return original(*args)
        def import_row():
            with self.app.app_context():
                try:
                    backend.append(db.session.execute(text("SELECT pg_backend_pid()")).scalar_one())
                    operation = db.session.get(SortDateOperation, oid)
                    return alp_import.apply_alp_paste(operation, "departure", contracts.departure_paste())
                finally:
                    db.session.rollback()
                    db.session.remove()
        with patch.object(alp_import, "_lock_matched_missions", delayed_lock):
            with ThreadPoolExecutor(1) as pool:
                job = pool.submit(import_row)
                try:
                    self.assertTrue(loaded.wait(timeout=5))
                    locked = _mission_or_404(self.operation, mid, for_update=True)
                    if change_identity:
                        form = _mission_form_from_model(locked)
                        form.update(wave="1", assigned_tail_number="N456UP")
                        _apply_mission_form(locked, self.operation, form)
                    else:
                        authority.manual_block_out(locked, self.operation, "2026-08-11 07:00", commit=False)
                    db.session.flush()
                    proceed.set()
                    self.assertTrue(locking.wait(timeout=5))
                    waiting = False
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with self.admin.connect() as conn:
                            waiting = conn.execute(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": backend[0]}).scalar()
                        if waiting:
                            break
                        time.sleep(.01)
                    self.assertTrue(waiting, "ALP must wait for the canonical writer's lock")
                    db.session.commit()
                finally:
                    db.session.rollback()
                    proceed.set()
                if change_identity:
                    with self.assertRaisesRegex(ValueError, "Mission identity changed"):
                        job.result(timeout=10)
                else:
                    self.assertEqual(job.result(timeout=10)["applied_count"], 1)
        db.session.expire_all()
        current = db.session.get(SortDateMission, mid)
        if change_identity:
            self.assertEqual(current.assigned_tail_number, "N456UP")
            self.assertIsNone(current.actual_block_out_datetime_utc)
        else:
            self.assertEqual(current.actual_block_out_source, "manual")
            self.assertEqual(current.actual_block_out_datetime_utc.hour, 7)

    def test_import_started_before_manual_commit_preserves_fresh_authority(self):
        self._race(False)

    def test_import_rejects_concurrent_identity_change(self):
        self._race(True)
