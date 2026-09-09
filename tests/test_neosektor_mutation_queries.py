"""Fresh-request Ballmat budgets, including the SQLite Gateway write lock."""
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import NeoSektorSortState, NeoSektorBallmatCount, NeoSektorOpenBayState
from app.neonodes.neosektor import routes
from app.services import gateway_matrix, neosektor_live_counts as live_counts
from app.services.neosektor_routing_signal import advance_routing_signal
from app.services.request_cache import MISSING
from tests import test_neosektor_routes as fixtures


class BallmatMutationQueriesTest(unittest.TestCase):
    def _workflow(self, reuse, *, include_decrements=False):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            fixture._login_approved_user(role="simulator")
            day = date.today()
            fixture._add_sort_operation(day, "night")
            fixture._set_sort_window("night", time(0), time(23, 59, 59))
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(day, time(23))
            self.assertEqual(fixture.client.post('/neosektor/tunnel-conductor/wave',
                json={"wave": "first", "value": 10}).status_code, 200)
            results = {}
            original_get = gateway_matrix.get_request_cached

            def without_reuse(namespace, key):
                if namespace == "neosektor.locked_operation_scope":
                    return MISSING
                return original_get(namespace, key)

            def measure(side, name, mode, position, metric="first", expected_status=200, version=None, **value):
                statements = []
                commits = []
                previous_signal = db.session.query(NeoSektorSortState.updated_at).scalar()

                def capture(conn, cursor, statement, parameters, context, executemany):
                    statements.append(" ".join(statement.lower().split()))

                # A new app context ensures this includes a fresh authenticated
                # user load, not Flask-Login state left on the fixture's g.
                with fixture.app.app_context():
                    engine = db.engine
                    on_commit = lambda conn: commits.append(True)
                    event.listen(engine, "before_cursor_execute", capture)
                    event.listen(engine, "commit", on_commit)
                    try:
                        with (nullcontext() if reuse else patch.object(
                                gateway_matrix, "get_request_cached", side_effect=without_reuse)):
                            response = fixture.client.post('/neosektor/ballmat/update?side=' + side,
                                json={"side": side, "spotter": {
                                    "expected_mode": mode, "expected_mode_version": mode - 1 if version is None else version,
                                    "position": position, "metric": metric, **value}})
                    finally:
                        event.remove(engine, "before_cursor_execute", capture)
                        event.remove(engine, "commit", on_commit)
                self.assertEqual(response.status_code, expected_status, response.json)
                counts = tuple(sum(sql.startswith(verb + " ") for sql in statements)
                               for verb in ("select", "insert", "update", "delete"))
                candidates = [i for i, sql in enumerate(statements)
                              if "from sort_date_operations where sort_date_operations.gateway_code =" in sql]
                if expected_status == 200:
                    self.assertEqual(len(candidates), 1 if reuse else 2)
                lock = next(i for i, sql in enumerate(statements)
                            if sql.startswith("update gateways set id=id"))
                self.assertLess(lock, candidates[0])
                self.assertEqual(len(commits), 1 if expected_status == 200 else 0)
                current_signal = db.session.query(NeoSektorSortState.updated_at).scalar()
                if expected_status == 200:
                    self.assertGreater(current_signal, previous_signal)
                else:
                    self.assertEqual(current_signal, previous_signal)
                results[side, name] = (counts, response.json, statements)

            for side in ("east", "west"):
                measure(side, "one_total", 1, "total", delta=1)
                if include_decrements:
                    measure(side, "one_total_minus", 1, "total", delta=-1)
                self.assertEqual(fixture.client.post('/neosektor/tunnel-conductor/spotter-mode', json={
                    "side": side, "action": "set", "mode": 2, "expected_mode": 1,
                    "expected_mode_version": 0}).status_code, 200)
                measure(side, "two_left", 2, "left", delta=1)
                if include_decrements:
                    measure(side, "two_left_minus", 2, "left", delta=-1)
                measure(side, "two_right", 2, "right", delta=1)
                if include_decrements:
                    measure(side, "two_right_minus", 2, "right", delta=-1)
                measure(side, "absolute", 2, "right", metric="second", value=12)
                if include_decrements:
                    measure(side, "absolute_lower", 2, "right", metric="second", value=7)
                    measure(side, "absolute_equal", 2, "right", metric="second", value=7)
                measure(side, "open", 2, "right", metric="open", delta=1)
                if include_decrements:
                    measure(side, "open_minus", 2, "right", metric="open", delta=-1)
                    measure(side, "stale_mode", 1, "total", delta=1, expected_status=409)
                    measure(side, "stale_version", 2, "right", delta=1, version=0, expected_status=409)
            return results
        finally:
            fixture.tearDown()

    def test_locked_operation_candidates_save_one_read_with_identical_responses(self):
        before = self._workflow(reuse=False)
        after = self._workflow(reuse=True)
        update_budgets = {
            "east": dict(one_total=6, two_left=5, two_right=5, absolute=6, open=4),
            "west": dict(one_total=5, two_left=6, two_right=5, absolute=6, open=4),
        }
        for key, (counts, payload, _) in before.items():
            with self.subTest(side=key[0], mutation=key[1]):
                self.assertEqual(counts[0], 23)
                self.assertEqual(after[key][0], (22, *counts[1:]))
                self.assertEqual(counts[1], 0)
                self.assertEqual(counts[2], update_budgets[key[0]][key[1]])
                self.assertEqual(counts[3], 0)
                self.assertEqual(after[key][1], payload)
                print(f"Ballmat {key}: SELECT/INSERT/UPDATE/DELETE {counts} -> {after[key][0]}")

    def test_rollup_signal_reuse_removes_only_redundant_timestamp_write(self):
        # Reproduce the pre-optimization final signal write, with read reuse on
        # in BOTH runs. The fixture and all other production code are identical.
        with patch.object(routes, "advance_routing_signal",
                          side_effect=lambda bundle, **kwargs: advance_routing_signal(bundle)):
            before = self._workflow(reuse=True, include_decrements=True)
        after = self._workflow(reuse=True, include_decrements=True)
        timestamp_only = "update neosektor_sort_states set updated_at=? where neosektor_sort_states.id = ?"
        for key, (counts, payload, statements) in before.items():
            if key[1].startswith("stale_"):
                self.assertEqual(after[key][:2], (counts, payload))
                continue
            with self.subTest(side=key[0], mutation=key[1]):
                new_counts, new_payload, new_statements = after[key]
                reduction = 0 if key[1] in ("open", "open_minus", "absolute_equal") else 1
                self.assertEqual(new_counts, (22, 0, counts[2] - reduction, 0))
                self.assertEqual(counts[0], 22)  # No extra reads to save a write.
                self.assertEqual(new_payload, payload)
                writes = [sql for sql in statements if sql.startswith("update ")]
                new_writes = [sql for sql in new_statements if sql.startswith("update ")]
                self.assertEqual(writes.count(timestamp_only), 1)
                if reduction:
                    writes.remove(timestamp_only)
                self.assertEqual(new_writes, writes)  # Same tables AND columns.
                print(f"Writes {key}: {counts[2]} -> {new_counts[2]}; "
                      f"sort timestamp-only UPDATE removed: {bool(reduction)}")

    def test_signal_keeps_monotonicity_for_unchanged_and_backward_clock_rollups(self):
        from datetime import timedelta
        baseline = datetime(2099, 1, 1)
        for current in (baseline, baseline - timedelta(seconds=1), None):
            row = SimpleNamespace(updated_at=current)
            advance_routing_signal(SimpleNamespace(sort_state=row), previous_updated_at=baseline)
            self.assertEqual(row.updated_at, baseline + timedelta(microseconds=1))
        advanced = baseline + timedelta(microseconds=5)
        row = SimpleNamespace(updated_at=advanced)
        advance_routing_signal(SimpleNamespace(sort_state=row), previous_updated_at=baseline)
        self.assertEqual(row.updated_at, advanced)

    def test_locked_side_join_saves_one_select_without_changing_writes_or_responses(self):
        def separate(sort_state, *, change_tracker=None):
            return (
                live_counts._get_or_create_ballmats(sort_state, change_tracker=change_tracker),
                live_counts._get_or_create_open_bays(sort_state, change_tracker=change_tracker),
            )
        with patch.object(live_counts, "_get_or_create_ballmat_side_states", side_effect=separate):
            before = self._workflow(reuse=True, include_decrements=True)
        after = self._workflow(reuse=True, include_decrements=True)
        for key, (counts, payload, statements) in before.items():
            with self.subTest(side=key[0], mutation=key[1]):
                new_counts, new_payload, new_statements = after[key]
                self.assertEqual(new_counts, (counts[0] - 1, *counts[1:]))
                if not key[1].startswith("stale_"):
                    self.assertEqual(new_counts[0], 22)
                else:
                    self.assertEqual(new_counts[1:], (0, 1, 0))  # SQLite lock only; rolled back.
                self.assertEqual(new_payload, payload)
                self.assertEqual(
                    [sql for sql in new_statements if sql.startswith("update ")],
                    [sql for sql in statements if sql.startswith("update ")],
                )
                joined = [sql for sql in new_statements if "left outer join neosektor_open_bay_states" in sql]
                self.assertEqual(len(joined), 1)
                lock = next(i for i, sql in enumerate(new_statements) if sql.startswith("update gateways set id=id"))
                self.assertLess(lock, new_statements.index(joined[0]))
                print(f"Side join {key}: SELECT/INSERT/UPDATE/DELETE {counts} -> {new_counts}")

    def test_locked_side_join_preserves_first_use_sparse_rows_identity_and_sort_scope(self):
        fixture = fixtures.NeoSektorRoutesTest()
        fixture.setUp()
        try:
            day = date(2026, 9, 9)
            bundle = live_counts.NeoSektorOperationalStateBundle.load(
                fixture.gateway, sort_date=day, for_update=True,
            )
            db.session.commit()
            sort_id = bundle.sort_state.id
            self.assertEqual([r.side for r in bundle.ballmats], ["EAST", "WEST"])
            self.assertEqual([r.side for r in bundle.open_bays], ["EAST", "WEST"])
            for row in bundle.ballmats + bundle.open_bays:
                self.assertIs(db.session.get(type(row), row.id), row)
            # Opposite missing collections must not hide the surviving row.
            east, west = bundle.ballmats
            east_open, west_open = bundle.open_bays
            west.spotter_mode = 2
            west.mode_version = 3
            west.right_first, west.right_second, west.right_open = 4, 5, 6
            east_open.open_count = 7
            west_id, east_open_id = west.id, east_open.id
            db.session.delete(east)
            db.session.delete(west_open)
            other = live_counts.get_or_create_sort_state(fixture.gateway, date(2026, 9, 8), "night")
            db.session.add(NeoSektorBallmatCount(sort_state_id=other.id, side="EAST", right_first=88))
            db.session.add(NeoSektorOpenBayState(sort_state_id=other.id, side="WEST", open_count=99))
            db.session.commit()
            bundle = live_counts.NeoSektorOperationalStateBundle.load(
                fixture.gateway, sort_date=day, for_update=True,
            )
            db.session.flush()
            east, west = bundle.ballmats
            east_open, west_open = bundle.open_bays
            self.assertEqual(west.id, west_id)
            self.assertIs(west, db.session.get(NeoSektorBallmatCount, west_id))
            self.assertEqual((west.spotter_mode, west.mode_version, west.right_first, west.right_second, west.right_open),
                             (2, 3, 4, 5, 6))
            self.assertEqual((east.spotter_mode, east.mode_version, east.right_first), (1, 0, 0))
            self.assertEqual((east_open.id, east_open.open_count, west_open.open_count), (east_open_id, 7, 0))
            self.assertTrue(all(row.sort_state_id == sort_id for row in bundle.ballmats + bundle.open_bays))
            db.session.commit()
            self.assertEqual(NeoSektorBallmatCount.query.filter_by(sort_state_id=sort_id).count(), 2)
            self.assertEqual(NeoSektorOpenBayState.query.filter_by(sort_state_id=sort_id).count(), 2)
        finally:
            fixture.tearDown()
