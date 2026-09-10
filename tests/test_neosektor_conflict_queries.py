"""Canonical pre-mutation conflicts reuse locked state, never staged repairs."""
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from sqlalchemy import event, select

from app.extensions import db
from app.models import NeoSektorOperationalSetting, NeoSektorSortState
from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
from app.services.neosektor_sheets_compat import NEO_PRIMARY_GOOGLE_MIRROR
from tests import test_neosektor_routes as fixtures


class BallmatConflictQueriesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoSektorRoutesTest()
        self.fixture.setUp()
        self.fixture._login_approved_user(role="simulator")
        self.day = date.today()
        self.fixture._add_sort_operation(self.day, "night")
        self.fixture._set_sort_window("night", time(0), time(23, 59, 59))
        self.fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(self.day, time(23))
        self.client = self.fixture.client
        self.assertEqual(self.client.post('/neosektor/tunnel-conductor/wave',
            json={"wave": "first", "value": 10}).status_code, 200)
        self.assertEqual(self.client.post('/neosektor/tunnel-conductor/spotter-mode', json={
            "side": "east", "action": "set", "mode": 2,
            "expected_mode": 1, "expected_mode_version": 0}).status_code, 200)
        self.assertEqual(self.client.post('/neosektor/ballmat/update?side=east',
            json=self.count_command()).status_code, 200)
        self.assertEqual(self.client.post('/neosektor/ballmat/mode-request?side=east', json={
            "side": "east", "mode": 1, "expected_mode": 2, "expected_mode_version": 1}).status_code, 200)

    def tearDown(self):
        self.fixture.tearDown()

    def count_command(self, **changes):
        return {"side": "east", "spotter": {"expected_mode": 2, "expected_mode_version": 1,
                "metric": "first", "position": "right", "delta": 1, **changes}}

    def stored_state(self):
        return {table.name: list(db.session.execute(select(table).order_by(table.c.id)).tuples())
                for table in db.metadata.sorted_tables if table.name.startswith('neosektor_')}

    def fresh(self, path, payload, *, legacy=False):
        sql, commits, rollbacks = [], [], []
        with self.fixture.app.app_context():
            engine = db.engine
            def capture(conn, cursor, statement, parameters, context, executemany):
                sql.append(" ".join(statement.lower().split()))
            def committed(conn):
                commits.append(True)
            def rolled_back(conn):
                rollbacks.append(True)
            event.listen(engine, "before_cursor_execute", capture)
            event.listen(engine, "commit", committed)
            event.listen(engine, "rollback", rolled_back)
            try:
                with (patch.object(Bundle, 'conflict_read_only_snapshot', return_value=None) if legacy else nullcontext()):
                    response = self.client.post(path, json=payload)
            finally:
                event.remove(engine, "before_cursor_execute", capture)
                event.remove(engine, "commit", committed)
                event.remove(engine, "rollback", rolled_back)
        return response, sql, commits, rollbacks

    def test_conflict_budgets_equivalence_no_writes_and_following_valid_request(self):
        mode = {"side": "east", "mode": 1, "expected_mode": 2, "expected_mode_version": 1}
        cases = [
            ('stale_mode', 'ballmat/update?side=east', self.count_command(expected_mode=1)),
            ('stale_generation', 'ballmat/update?side=east', self.count_command(expected_mode_version=0)),
            ('invalid_position', 'ballmat/update?side=east', self.count_command(position='total')),
            ('request_generation', 'ballmat/mode-request?side=east', {**mode, 'expected_mode_version': 0}),
            ('conductor_generation', 'tunnel-conductor/spotter-mode', {**mode, 'action': 'set', 'expected_mode_version': 0}),
            ('pending_version', 'tunnel-conductor/spotter-mode', {**mode, 'action': 'approve', 'request_version': 0}),
            ('confirm_direct', 'tunnel-conductor/spotter-mode', {**mode, 'action': 'set'}),
            ('confirm_approve', 'tunnel-conductor/spotter-mode', {**mode, 'action': 'approve', 'request_version': 1}),
        ]
        for name, path, command in cases:
            with self.subTest(conflict=name):
                stored = self.stored_state()
                before, old_sql, _, _ = self.fresh('/neosektor/' + path, command, legacy=True)
                after, sql, commits, rollbacks = self.fresh('/neosektor/' + path, command)
                self.assertEqual(before.status_code, 409)
                self.assertEqual(after.status_code, 409)
                self.assertEqual(after.json, before.json)
                self.assertEqual(self.stored_state(), stored)
                self.assertFalse(commits)
                self.assertTrue(rollbacks)
                self.assertEqual([s for s in sql if s.startswith(('insert ', 'delete ', 'update '))],
                                 ['update gateways set id=id where id=?'])
                counts = sum(s.startswith('select ') for s in old_sql), sum(s.startswith('select ') for s in sql)
                self.assertLess(counts[1], counts[0])
                if path.startswith('ballmat/update'):
                    self.assertEqual(counts, (26, 22))
                else:
                    self.assertEqual(counts, (27, 23))
                if name.startswith('confirm_'):
                    self.assertTrue(after.json['confirmation_required'])
                print(f"Conflict {name}: SELECTs {counts[0]} -> {counts[1]}")
                # The rejection releases the lock and does not poison the session.
                valid, valid_sql, valid_commits, _ = self.fresh('/neosektor/ballmat/update?side=east', self.count_command())
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(sum(s.startswith('select ') for s in valid_sql), 22)
                self.assertEqual(len(valid_commits), 1)
                canonical = self.client.get('/neosektor/ballmat/state?side=east').json['state']
                # Existing POST status uses the shared default; GET applies the
                # EBM screen override. All operational state must be identical.
                self.assertEqual(valid.json['state']['refresh']['screen_key'], 'neosektor.live_counts')
                self.assertEqual(canonical['refresh']['screen_key'], 'neosektor.ebm')
                self.assertEqual({k: v for k, v in valid.json['state'].items() if k != 'refresh'},
                                 {k: v for k, v in canonical.items() if k != 'refresh'})

    def test_first_use_conflict_discards_initialization_before_canonical_response(self):
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime.combine(self.day + timedelta(days=1), time(23))
        before = self.stored_state()
        old, _, _, _ = self.fresh('/neosektor/ballmat/update?side=east', self.count_command(), legacy=True)
        new, _, commits, rollbacks = self.fresh('/neosektor/ballmat/update?side=east', self.count_command())
        self.assertEqual(new.status_code, 409)
        self.assertEqual(new.json, old.json)
        self.assertEqual(new.json['state']['spotters']['mode'], 1)
        self.assertEqual(new.json['state']['spotters']['counts']['first']['total'], 0)
        self.assertEqual(before, self.stored_state())
        self.assertFalse(commits)
        self.assertTrue(rollbacks)

    def test_flushed_mirror_repairs_force_rollback_reload_not_snapshot(self):
        NeoSektorOperationalSetting.query.one().integration_mode = NEO_PRIMARY_GOOGLE_MIRROR
        NeoSektorSortState.query.one().unloaded_total = 999
        db.session.commit()
        before = self.stored_state()
        original = Bundle.conflict_read_only_snapshot
        snapshots = []
        def observe(bundle):
            self.assertTrue(bundle.persistent_state_changed)
            result = original(bundle)
            snapshots.append(result)
            return result
        with patch.object(Bundle, 'conflict_read_only_snapshot', autospec=True, side_effect=observe):
            response, _, commits, rollbacks = self.fresh('/neosektor/ballmat/update?side=east',
                self.count_command(expected_mode_version=0))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(snapshots, [None])
        self.assertEqual(before, self.stored_state())
        self.assertFalse(commits)
        self.assertTrue(rollbacks)
