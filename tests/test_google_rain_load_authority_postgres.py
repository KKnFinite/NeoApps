"""Current operator overrides versus real, separately locked Google imports."""
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from datetime import datetime

from sqlalchemy import text

from app.extensions import db
from app.models import SortDateMission, SortDateOperation, NeoRainMissionOverride
from app.neomotherbrain.routes import _apply_mission_form, _mission_form_from_model
from app.services import google_rain_live_milestones as rain
from app.services.neorain_overrides import edit_override
from app.services.live_collaboration import entity_version
from tests import test_google_rain_load_authority as contracts
from tests.test_motherbrain_block_out_authority_postgres import postgres_setup


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable loopback PostgreSQL')
class RainLoadAuthorityPostgresTest(contracts.RainLoadAuthorityTest):
    def setUp(self):
        postgres_setup(self, contracts.fixtures.fixtures, super().setUp)

    def _import_race(self, *, change_identity=False):
        mid, oid = self.departure.id, self.operation.id
        loaded, proceed, locking = Event(), Event(), Event()
        backend = []
        original = rain.lock_current_departure_for_google_import
        def delayed_lock(*args):
            loaded.set()
            if not proceed.wait(timeout=10):
                raise RuntimeError('Import not released by fixture')
            locking.set()
            return original(*args)
        def import_row():
            with self.app.app_context():
                try:
                    backend.append(db.session.execute(text('SELECT pg_backend_pid()')).scalar_one())
                    operation = db.session.get(SortDateOperation, oid)
                    result = rain.apply_google_rain_departure_milestones(operation, rows=[dict(
                        sheet_row=4, flight_number='UPS948', destination='SDF',
                        ramp_load_complete='2:30', crew_load_complete='2:30', elmac='2:30')])
                    db.session.commit()
                    return result
                finally:
                    db.session.remove()
        with patch.object(rain, 'lock_current_departure_for_google_import', delayed_lock):
            with ThreadPoolExecutor(1) as pool:
                job = pool.submit(import_row)
                try:
                    self.assertTrue(loaded.wait(timeout=5))
                    mission = SortDateMission.query.filter_by(id=mid,
                        sort_date_operation_id=oid).populate_existing().with_for_update().one()
                    if change_identity:
                        form = _mission_form_from_model(mission)
                        form.update(wave='1', assigned_tail_number='N456UP')
                        _apply_mission_form(mission, self.operation, form)
                    else:
                        # Same canonical writer as /neorain/override, inside
                        # the route's mission-first lock/transaction convention.
                        for field in contracts.LOAD_FIELDS:
                            edit_override(mission, self.operation, field, '0200', self.actor)
                    db.session.flush()
                    proceed.set()
                    self.assertTrue(locking.wait(timeout=5))
                    waiting = False
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with self.admin.connect() as conn:
                            waiting = conn.execute(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),
                                {'pid': backend[0]}).scalar()
                        if waiting:
                            break
                        time.sleep(.01)
                    self.assertTrue(waiting, 'Google must wait for the canonical edit transaction')
                    db.session.commit()
                finally:
                    db.session.rollback(); proceed.set()
                result = job.result(timeout=10)
        db.session.expire_all()
        current = db.session.get(SortDateMission, mid)
        if change_identity:
            self.assertEqual(result['skipped_count'], 1)
            self.assertEqual(current.assigned_tail_number, 'N456UP')
            self.assertIsNone(current.ramp_load_completed_at_utc)
        else:
            self.assertEqual(result['applied_count'], 1)
            for field, (timestamp, source) in contracts.LOAD_FIELDS.items():
                self.assertEqual(getattr(current, timestamp), datetime(2026, 8, 31, 7))
                self.assertEqual(getattr(current, source), 'neorain')
                row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=mid, field=field).one()
                self.assertTrue(row.active)
                self.assertEqual(row.upstream[timestamp], '2026-08-31T07:30:00')

    def test_import_loaded_before_native_commit_waits_and_preserves_all_three_edits(self):
        self._import_race()

    def test_changed_tail_identity_skips_stale_load_import(self):
        self._import_race(change_identity=True)

    def test_poll_update_rejects_stale_operator_version_without_partial_mutation(self):
        version = entity_version(self.departure)
        self.import_fields(ramp_load_complete='2:30')
        response = self.client.post('/neorain/override', json=dict(mission_id=self.departure.id,
            field='ramp_load_complete', value='0200', expected_version=version))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.departure.ramp_load_completed_at_utc, datetime(2026, 8, 31, 7, 30))
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)
