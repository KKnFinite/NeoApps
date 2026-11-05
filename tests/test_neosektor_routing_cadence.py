"""The existing per-user Driver guard remains authoritative and bounded."""
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from flask import Flask
from app.services import live_refresh_guard as guard


class RoutingCadenceTest(unittest.TestCase):
    def test_real_throttled_request_cost_stops_after_fresh_authentication(self):
        from app.extensions import db
        from tests import test_neosektor_poll_queries as fixtures
        case = fixtures.PollQueriesTest()
        case.setUp()
        self.addCleanup(case.tearDown)
        fixture = case.fixture
        fixture.app.config['TEST_LIVE_REFRESH_RATE_LIMIT_ENABLED'] = True
        guard.reset_live_refresh_guard_for_testing()
        self.addCleanup(guard.reset_live_refresh_guard_for_testing)

        def fresh():
            db.session.remove()
            fixture.context.pop()
            try:
                with fixture.app.app_context():
                    return fixture._capture_get_metrics('/neosektor/driver-routing/state')
            finally:
                fixture.context.push()

        with patch.object(guard, 'monotonic') as clock:
            clock.return_value = 100
            response, _, _, _ = fresh()
            self.assertEqual(response.status_code, 200)
            clock.return_value = 101.5
            response, sql, commits, _ = fresh()
            self.assertEqual(response.status_code, 429)
        selects = [s for s in sql if s.startswith(('select ', 'with '))]
        self.assertEqual(len(selects), 1)
        self.assertIn('from users', selects[0])
        self.assertFalse(any(s.startswith(('insert ', 'update ', 'delete ')) for s in sql))
        self.assertEqual(commits, 0)
        evidence = Path('instance/refresh-efficiency-20261105')
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence/'throttle-sql.json').write_text(json.dumps(dict(
            selects=len(selects), writes=0, commits=commits, response_bytes=len(response.data),
        ), indent=2), encoding='utf-8')

    def test_driver_cadence_is_per_user_and_rejection_does_not_extend_cooldown(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEST_LIVE_REFRESH_RATE_LIMIT_ENABLED=True)
        app.before_request(guard.enforce_live_refresh_request_cadence)
        downstream = []

        def state():
            downstream.append(True)
            return {'ok': True}

        app.add_url_rule('/state', 'neosektor.driver_routing_state', state)
        app.add_url_rule('/version', 'neosektor.driver_routing_version', lambda: {'ok': True})
        guard.reset_live_refresh_guard_for_testing()
        self.addCleanup(guard.reset_live_refresh_guard_for_testing)
        user = SimpleNamespace(id=10)
        with patch.object(guard, 'current_user', user), patch.object(guard, 'monotonic') as clock:
            client = app.test_client()
            clock.return_value = 100
            self.assertEqual(client.get('/state').status_code, 200)
            clock.return_value = 101.5
            rejected = client.get('/state?revision=another-tab')
            self.assertEqual(rejected.status_code, 429)
            self.assertEqual(rejected.headers['Retry-After'], '4')
            self.assertEqual(rejected.get_json()['error'], 'Live refresh is cooling down.')
            self.assertEqual(len(downstream), 1)  # No canonical state/SQL processing on rejection.
            self.assertEqual(client.get('/version').status_code, 200)
            self.assertEqual(client.get('/version').status_code, 200)
            user.id = 11
            self.assertEqual(client.get('/state').status_code, 200)
            user.id = 10
            clock.return_value = 104.9
            self.assertEqual(client.get('/state').status_code, 429)
            clock.return_value = 105
            self.assertEqual(client.get('/state').status_code, 200)
            self.assertEqual(len(downstream), 3)
