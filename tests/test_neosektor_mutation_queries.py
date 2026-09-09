"""Fresh-request Ballmat budgets, including the SQLite Gateway write lock."""
import unittest
from contextlib import nullcontext
from datetime import date, datetime, time
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.services import gateway_matrix
from app.services.request_cache import MISSING
from tests import test_neosektor_routes as fixtures


class BallmatMutationQueriesTest(unittest.TestCase):
    def _workflow(self, reuse):
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

            def measure(side, name, mode, position, metric="first", **value):
                statements = []
                commits = []

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
                                    "expected_mode": mode, "expected_mode_version": mode - 1,
                                    "position": position, "metric": metric, **value}})
                    finally:
                        event.remove(engine, "before_cursor_execute", capture)
                        event.remove(engine, "commit", on_commit)
                self.assertEqual(response.status_code, 200, response.json)
                counts = tuple(sum(sql.startswith(verb + " ") for sql in statements)
                               for verb in ("select", "insert", "update", "delete"))
                candidates = [i for i, sql in enumerate(statements)
                              if "from sort_date_operations where sort_date_operations.gateway_code =" in sql]
                self.assertEqual(len(candidates), 1 if reuse else 2)
                lock = next(i for i, sql in enumerate(statements)
                            if sql.startswith("update gateways set id=id"))
                self.assertLess(lock, candidates[0])
                self.assertEqual(len(commits), 1)
                results[side, name] = (counts, response.json)

            for side in ("east", "west"):
                measure(side, "one_total", 1, "total", delta=1)
                self.assertEqual(fixture.client.post('/neosektor/tunnel-conductor/spotter-mode', json={
                    "side": side, "action": "set", "mode": 2, "expected_mode": 1,
                    "expected_mode_version": 0}).status_code, 200)
                measure(side, "two_left", 2, "left", delta=1)
                measure(side, "two_right", 2, "right", delta=1)
                measure(side, "absolute", 2, "right", metric="second", value=12)
                measure(side, "open", 2, "right", metric="open", delta=1)
            return results
        finally:
            fixture.tearDown()

    def test_locked_operation_candidates_save_one_read_with_identical_responses(self):
        before = self._workflow(reuse=False)
        after = self._workflow(reuse=True)
        update_budgets = {
            "east": dict(one_total=7, two_left=6, two_right=6, absolute=7, open=4),
            "west": dict(one_total=6, two_left=7, two_right=6, absolute=7, open=4),
        }
        for key, (counts, payload) in before.items():
            with self.subTest(side=key[0], mutation=key[1]):
                self.assertEqual(counts[0], 24)
                self.assertEqual(after[key][0], (23, *counts[1:]))
                self.assertEqual(counts[1], 0)
                self.assertEqual(counts[2], update_budgets[key[0]][key[1]])
                self.assertEqual(counts[3], 0)
                self.assertEqual(after[key][1], payload)
                print(f"Ballmat {key}: SELECT/INSERT/UPDATE/DELETE {counts} -> {after[key][0]}")
