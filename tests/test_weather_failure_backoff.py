import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.services import neosubzero_weather as weather


class WeatherFailureBackoffTest(unittest.TestCase):
    def setUp(self):
        weather.clear_neosubzero_weather_cache()
        self.now = datetime(2026, 11, 1, tzinfo=timezone.utc)

    def tearDown(self):
        weather.clear_neosubzero_weather_cache()

    def fetch(self, key, provider, seconds=0, ttl=10, stale=100):
        return weather._cached_fetch(key, ttl, stale, provider, now=self.now + timedelta(seconds=seconds))

    def test_cold_failure_cooldown_recovery_and_reset(self):
        provider = Mock(side_effect=[RuntimeError(), {'report': 'original'}, RuntimeError()])
        self.assertIsNone(self.fetch('metar', provider)['value'])
        for seconds in (1, 10, 59):
            self.assertIsNone(self.fetch('metar', provider, seconds)['value'])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.fetch('metar', provider, 60)['value'], {'report': 'original'})
        self.assertNotIn('metar', weather._FAILURES)
        self.fetch('metar', provider, 71)
        failure = weather._FAILURES['metar']
        self.assertEqual(failure['attempts'], 1)
        self.assertEqual(failure['next_retry_at'] - failure['last_failed_at'], 60)

    def test_exponential_backoff_is_bounded(self):
        provider = Mock(side_effect=RuntimeError())
        elapsed = 0
        for delay in (60, 120, 240, 480, 900, 900, 900):
            self.fetch('metar', provider, elapsed)
            failure = weather._FAILURES['metar']
            self.assertEqual(failure['next_retry_at'] - failure['last_failed_at'], delay)
            elapsed += delay
        self.assertEqual(provider.call_count, 7)

    def test_failures_never_extend_stale_evidence(self):
        provider = Mock(side_effect=[{'reportTime': 'unchanged'}, RuntimeError(), RuntimeError()])
        self.fetch('metar', provider)
        original = weather._CACHE['metar']['fetched_at']
        self.assertTrue(self.fetch('metar', provider, 11)['stale'])
        self.assertTrue(self.fetch('metar', provider, 99)['stale'])
        self.assertIsNone(self.fetch('metar', provider, 100)['value'])
        self.assertEqual(weather._CACHE['metar']['fetched_at'], original)

    def test_provider_failure_states_are_independent(self):
        failed = Mock(side_effect=RuntimeError())
        good = Mock(return_value={'forecast': 'valid'})
        self.fetch('metar', failed)
        self.fetch('points', failed)
        self.assertEqual(self.fetch('forecast', good)['value'], {'forecast': 'valid'})
        self.assertNotIn('forecast', weather._FAILURES)
        self.assertEqual(set(weather._FAILURES), {'metar', 'points'})

    def test_concurrent_callers_do_not_duplicate_or_wait_for_network(self):
        entered, release = threading.Event(), threading.Event()
        def provider(**_):
            entered.set()
            if not release.wait(3):
                raise AssertionError('Test failed to release provider')
            return {'report': 'valid'}
        provider = Mock(side_effect=provider)
        thread = threading.Thread(target=lambda: self.fetch('metar', provider))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertIsNone(self.fetch('metar', provider)['value'])
            independent = Mock(return_value={'points': 'valid'})
            self.assertIsNotNone(self.fetch('points', independent)['value'])
            self.assertEqual(provider.call_count, 1)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.fetch('metar', provider)['value'], {'report': 'valid'})

    def test_epoch_seconds_frost_caller_compatibility(self):
        provider = Mock(return_value=[])
        for seconds in (0, 1):
            weather._cached_fetch('frost_recent_metar', 300, 900, provider, now=self.now.timestamp() + seconds)
        self.assertEqual(provider.call_count, 1)

    def test_revision_changes_only_for_evidence_or_display_state(self):
        value = [{'reportTime': '2026-11-01T00:00:00Z'}]
        snapshot = dict(metar=dict(value=value, stale=False), forecast=dict(value=None, stale=False))
        revision = weather._weather_revision(snapshot)
        for attempt in range(3):
            snapshot['metar']['error'] = str(attempt)
            self.assertEqual(weather._weather_revision(snapshot), revision)
        snapshot['metar']['stale'] = True
        self.assertNotEqual(weather._weather_revision(snapshot), revision)


if __name__ == '__main__':
    unittest.main()
