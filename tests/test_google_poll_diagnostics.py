import json
import unittest
from types import SimpleNamespace

from app.services.google_motherbrain_sheets import _google_call, safe_poll_failure, GoogleMotherBrainReaderError
from app.services.google_motherbrain_live_poll_lease import _minimal_error


class DiagnosticsTest(unittest.TestCase):
    def test_provider_categories_and_stage(self):
        for status, code in [(401, 'spreadsheet_access_denied'), (403, 'spreadsheet_access_denied'),
                             (429, 'google_rate_limited'), (500, 'google_server_failure'), (503, 'google_server_failure')]:
            with self.subTest(status=status):
                error = RuntimeError('SECRET provider contents')
                error.response = SimpleNamespace(status_code=status)
                self.assert_failure(error, code, status)
        self.assert_failure(TimeoutError('SECRET'), 'google_timeout', None)
        self.assert_failure(ConnectionError('SECRET'), 'google_connection_failure', None)

    def assert_failure(self, error, code, status):
        def fail(): raise error
        with self.assertRaises(GoogleMotherBrainReaderError) as caught:
            _google_call('read_live_ranges', fail)
        detail = safe_poll_failure(caught.exception)
        self.assertEqual(detail, dict(code=code, stage='read_live_ranges', provider_status=status))
        stored = _minimal_error(caught.exception)
        self.assertLessEqual(len(stored), 255)
        self.assertNotIn('SECRET', stored)
        self.assertEqual(json.loads(stored), detail)

    def test_gspread_wrapped_permission_preserves_status(self):
        cause = RuntimeError('SECRET')
        cause.response = SimpleNamespace(status_code=403)
        error = PermissionError('SECRET')
        error.__cause__ = cause
        self.assert_failure(error, 'spreadsheet_access_denied', 403)

    def test_no_arbitrary_error_data_logged(self):
        error = GoogleMotherBrainReaderError('SECRET', 'SECRET', stage='SECRET', provider_status='SECRET')
        self.assertNotIn('SECRET', json.dumps(safe_poll_failure(error)))

    def test_success_unchanged_and_single_call(self):
        calls=[]
        self.assertEqual(_google_call('read_live_ranges', lambda: calls.append(1) or {'ok': True}), {'ok':True})
        self.assertEqual(calls, [1])
