import re
import unittest
from datetime import datetime, time
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier

from app import create_app
from unittest.mock import patch

from flask import g
from sqlalchemy import event

from app.extensions import db
from app.models import NeoErmacDoorPull, SortDateOperation
from app.services.neoermac_dashboard import neoermac_dashboard_context
from tests import test_neoermac_routes as fixtures


class UpcomingActualTest(unittest.TestCase):
    _assign_lineup_destination = fixtures.NeoErmacRoutesTest._assign_lineup_destination
    _add_operation_departure = fixtures.NeoErmacRoutesTest._add_operation_departure
    _login_approved_user = fixtures.NeoErmacRoutesTest._login_approved_user
    tearDown = fixtures.NeoErmacRoutesTest.tearDown

    def setUp(self):
        fixtures.NeoErmacRoutesTest.setUp(self)
        for runout, slot in (("green_runout", "east_destination_1"),
                             ("runout_10", "east_destination_1"),
                             ("runout_10", "west_destination_1"),
                             ("runout_10", "west_destination_2")):
            self._assign_lineup_destination(runout, slot, "SDF")
        self.mission = self._add_operation_departure("UPS501", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

    def form(self, **overrides):
        return dict(operation_id=self.mission.sort_date_operation_id, mission_id=self.mission.id,
                    destination="SDF", pull_key="pure", actual_pull="12:18", **overrides)

    def post(self, **overrides):
        data = {**self.form(), **overrides}
        g.__dict__.clear()
        db.session.expire_all()
        return self.client.post("/neoermac/upcoming-pulls/actual", data=data)

    def record(self, door, **values):
        row = NeoErmacDoorPull(gateway_id=self.gateway.id,
            sort_date_operation_id=self.mission.sort_date_operation_id,
            sort_date_mission_id=self.mission.id, destination="SDF", door=door, **values)
        db.session.add(row)
        db.session.commit()
        return row

    def test_gap_fill_preserves_actual_no_and_other_field_across_sides(self):
        self.record("D32", actual_pure_pull_time_local=time(12,14), no_mix_pull=True)
        self.record("D1", no_pure_pull=True, actual_mix_pull_time_local=time(12,10))
        response = self.post()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.json["filled"], 1)
        rows = {r.door:r for r in NeoErmacDoorPull.query.all()}
        self.assertEqual(set(rows), {"D1", "D32", "D34"})
        self.assertEqual(rows["D32"].actual_pure_pull_time_local, time(12,14))
        self.assertTrue(rows["D32"].no_mix_pull)
        self.assertTrue(rows["D1"].no_pure_pull)
        self.assertIsNone(rows["D1"].actual_pure_pull_time_local)
        self.assertEqual(rows["D1"].actual_mix_pull_time_local, time(12,10))
        self.assertEqual(rows["D34"].actual_pure_pull_time_local, time(12,18))
        self.assertIsNone(rows["D34"].actual_mix_pull_time_local)
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            self.assertEqual(self.post(actual_pull="13:00").json["filled"], 0)
            commit.assert_not_called()
        self.assertEqual(self.post(pull_key="mix", actual_pull="12:25").json["filled"], 1)
        db.session.expire_all()
        self.assertEqual(rows["D34"].actual_pure_pull_time_local, time(12,18))
        self.assertEqual(rows["D34"].actual_mix_pull_time_local, time(12,25))

    def test_all_sides_deduped_preference_ignored_and_completed_pull_removed(self):
        self.client.post('/neoermac/door-view/supervision', data={'doors': ['D32'], 'active_door': 'D32'})
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        self.assertEqual(context["west"][0]["location"], "D32 \u00b7 D34")
        self.assertEqual(context["east"][0]["location"], "D1")
        self.assertEqual(self.post().json["filled"], 3)
        self.assertEqual(NeoErmacDoorPull.query.count(), 3)
        db.session.expire_all()
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        for side in ("east", "west"):
            self.assertEqual([r["pull_key"] for r in context[side]], ["mix"])
        self.assertEqual(self.mission.actual_pure_pull_time_local, time(12,18))
        self.assertEqual(self.post(pull_key="mix").status_code, 200)
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        self.assertEqual(context["east"] + context["west"], [])

    def test_partial_pull_remains_and_newer_value_at_save_is_preserved(self):
        self.client.get('/neoermac/upcoming-pulls')
        self.record("D32", actual_pure_pull_time_local=time(12,14))
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        self.assertIn("pure", [r["pull_key"] for r in context["west"]])
        self.assertEqual(self.post().json["filled"], 2)
        self.assertEqual(NeoErmacDoorPull.query.filter_by(door="D32").one().actual_pure_pull_time_local, time(12,14))

    def test_canonical_mission_with_repeated_destination_is_isolated(self):
        other = self._add_operation_departure("UPS502", "SDF")
        db.session.commit()
        self.assertEqual(self.post(mission_id=other.id).json["filled"], 3)
        self.assertEqual({r.sort_date_mission_id for r in NeoErmacDoorPull.query.all()}, {other.id})
        self.assertIsNone(self.mission.actual_pure_pull_time_local)
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        self.assertIn(self.mission.id, [r["mission_id"] for r in context["west"] if r["pull_key"] == "pure"])

    def test_invalid_identity_time_and_type_never_write(self):
        other = self._add_operation_departure("UPS502", "BOS")
        db.session.commit()
        for data, status in (({"operation_id":999999},409), ({"mission_id":999999},409),
                             ({"mission_id":other.id},409), ({"destination":"BOS"},409),
                             ({"mission_id":""},409), ({"actual_pull":""},400),
                             ({"actual_pull":"24:00"},400), ({"pull_key":"bogus"},400)):
            with self.subTest(data=data):
                self.assertEqual(self.post(**data).status_code, status)
                self.assertEqual(NeoErmacDoorPull.query.count(), 0)
        with patch('app.services.neoermac_door_view.current_door_view_operation', return_value=None):
            self.assertEqual(self.post().status_code, 409)

    def test_both_permissions_required_and_readonly_has_no_inputs(self):
        for view, edit in ((False,True), (True,False), (False,False)):
            with self.subTest(view=view, edit=edit), patch('app.neonodes.neoermac.routes.user_can',
                    side_effect=lambda key: {'neoermac.upcoming_pulls.view':view, 'neoermac.door_view.edit':edit}.get(key, False)):
                self.assertEqual(self.post().status_code, 403)
        with patch('app.neonodes.neoermac.routes.user_can', return_value=False):
            page = self.client.get('/neoermac/upcoming-pulls')
            self.assertNotIn(b'data-upcoming-actual', page.data)
            state = self.client.get('/neoermac/upcoming-pulls/state?revision=old')
            self.assertNotIn('data-upcoming-actual', state.json['board_html'])
        html = self.client.get('/neoermac/upcoming-pulls').data
        self.assertIn(b'data-upcoming-actual', html)
        self.assertIn(b'inputmode="numeric"', html)
        self.assertIn(b'placeholder="HHMM"', html)

    def test_csrf_enforced_and_refreshed_fragment_contains_token(self):
        self.app.config['CSRF_PROTECT_TESTING'] = True
        self.assertEqual(self.post().status_code, 400)
        state = self.client.get('/neoermac/upcoming-pulls/state?revision=old')
        token = re.search(r'name="csrf_token" value="([^"]+)"', state.json['board_html']).group(1)
        self.assertEqual(self.post(csrf_token=token).status_code, 200)

    def test_failed_aggregation_rolls_back_all_doors(self):
        with patch('app.services.neoermac_upcoming_pulls.recompute_current_sort_door_pull_aggregates', side_effect=RuntimeError('test')), self.assertLogs(self.app.logger, level='ERROR'):
            self.assertEqual(self.post().status_code, 500)
        self.assertEqual(NeoErmacDoorPull.query.count(), 0)

    def test_legacy_counterpart_preserved_and_canonical_completion_wins(self):
        legacy = self.record('D34', no_mix_pull=True)
        legacy.sort_date_mission_id = None
        db.session.commit()
        self.assertEqual(self.post().json['filled'], 3)
        canonical = NeoErmacDoorPull.query.filter_by(door='D34', sort_date_mission_id=self.mission.id).one()
        self.assertTrue(canonical.no_mix_pull)
        context = neoermac_dashboard_context(self.gateway, initialize_lineup=False)
        self.assertNotIn('pure', [r['pull_key'] for r in context['west']])

    def test_concurrent_catchall_saves_serialize_and_second_is_noop(self):
        self.client.get('/neoermac/upcoming-pulls')
        db.session.commit()
        cookie = self.client.get_cookie('session').value
        forms = [self.form(), {**self.form(), 'actual_pull':'12:22'}]
        db.session.commit()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'pulls.sqlite'
            with db.engine.connect() as connection, closing(sqlite3.connect(path)) as target:
                connection.connection.driver_connection.backup(target)
            config = type('ConcurrentUpcomingConfig', (), {
                'SECRET_KEY':'test', 'TESTING':True,
                'SQLALCHEMY_DATABASE_URI':'sqlite:///' + path.as_posix(),
                'SQLALCHEMY_TRACK_MODIFICATIONS':False,
                'CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE':datetime(2026,6,11,1),
            })
            app = create_app(config, auto_bootstrap=False)
            barrier = Barrier(2)
            def submit(form):
                client = app.test_client()
                client.set_cookie('session', cookie)
                barrier.wait(timeout=10)
                response = client.post('/neoermac/upcoming-pulls/actual', data=form)
                return response.status_code, response.json
            try:
                with ThreadPoolExecutor(max_workers=2) as workers:
                    results = list(workers.map(submit, forms))
                self.assertEqual([status for status, _ in results], [200,200])
                self.assertEqual(sorted(payload['filled'] for _, payload in results), [0,3])
                with app.app_context():
                    rows = NeoErmacDoorPull.query.all()
                    self.assertEqual(len(rows), 3)
                    self.assertEqual(len({row.actual_pure_pull_time_local for row in rows}), 1)
            finally:
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()

    def test_save_bulk_loads_pulls_without_per_door_selects(self):
        statements = []
        def capture(_conn, _cursor, statement, *_args):
            statements.append(' '.join(statement.lower().split()))
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            self.assertEqual(self.post().json['filled'], 3)
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        pull_selects = [s for s in statements if s.startswith('select') and 'from neoermac_door_pulls' in s]
        # The shared bundle reads all canonical pull records in a single query.
        self.assertEqual(len(pull_selects), 1, statements)
