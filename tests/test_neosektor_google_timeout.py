"""Real gspread/Google-auth transports with mocked network, isolated Neo state."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import unquote
import json
import os
import unittest
from unittest.mock import Mock, patch

import gspread
from google.auth.credentials import Credentials
from requests import Response, Session
from requests.exceptions import ConnectionError, Timeout

from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services import neosektor_sheets_compat as sheets
from tests import test_neosektor_integration_modes as fixtures


def response(data, status=200):
    result = Response()
    result.status_code = status
    result._content = json.dumps(data).encode()
    result.headers['Content-Type'] = 'application/json'
    return result


class GoogleFixture:
    """The actual gspread HTTPClient, never an external Google request."""
    def __init__(self):
        self.values = fixtures._complete_sheet_values()
        self.authority = dict(zip(sheets.NEOSEKTOR_SHARED_AUTHORITY_CELLS,
            [sheets.NEO_PRIMARY_AUTHORITY, 7, '2026-10-01T00:00:00Z', 'fixture', '{}']))
        self.calls = []
        self.writes = []
        self.fail_at = None
        self.failure = Timeout('synthetic provider stall')
        self.fail_cell = None
        self.missing = False
        self.transport = Mock()
        self.transport.request.side_effect = self.request
        self.client = gspread.Client(auth=None, session=self.transport)

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.fail_at == len(self.calls):
            if isinstance(self.failure, Response):
                return self.failure
            raise self.failure
        if '/values:batchGet' in url:
            ranges = kwargs['params']['ranges']
            return response({'valueRanges': [dict(range=area, majorDimension='ROWS',
                values=[[self.table(area).get(area.split('!')[-1], '')]]) for area in ranges]})
        if '/values:batchUpdate' in url:
            for update in kwargs['json']['data']:
                area = update['range']
                table = self.table(area)
                start = area.split('!')[-1].split(':')[0]
                row = ''.join(c for c in start if c.isdigit())
                column = ''.join(c for c in start if c.isalpha())
                for index, value in enumerate(update['values'][0]):
                    table[chr(ord(column) + index) + row] = value
                self.writes.append((area, update['values']))
                if self.fail_cell == 'authority':
                    raise Timeout('response lost after authority write')
            return response({'totalUpdatedCells': 5})
        if '/values/' in url:
            area = unquote(url.split('/values/', 1)[1])
            cell = area.split('!')[-1]
            value = kwargs['json']['values'][0][0]
            self.table(area)[cell] = value
            self.writes.append((cell, value))
            if self.fail_cell == cell:
                raise Timeout('response lost after cell write')
            return response({'updatedCells': 1})
        self.assert_book(url)
        tabs = [] if self.missing else [
            {'properties': {'title': title, 'sheetId': index, 'gridProperties': {'rowCount': 100, 'columnCount': 10}}}
            for index, title in enumerate(['Live Counts', 'NeoSektor Authority'])]
        return response({'spreadsheetId': 'test-sheet-id', 'properties': {'title': 'Fixture'}, 'sheets': tabs})

    def table(self, area):
        return self.authority if 'NeoSektor Authority' in area else self.values

    def assert_book(self, url):
        assert url == 'https://sheets.googleapis.com/v4/spreadsheets/test-sheet-id', url


class NeoSektorGoogleTimeoutTest(unittest.TestCase):
    setUp = fixtures.NeoSektorIntegrationModesTest.setUp
    tearDown = fixtures.NeoSektorIntegrationModesTest.tearDown
    _set_mode = fixtures.NeoSektorIntegrationModesTest._set_mode

    def google(self, fixture=None, shared=False):
        fixture = fixture or GoogleFixture()
        environment = fixtures.FAKE_SHARED_AUTHORITY_ENV if shared else fixtures.FAKE_SHEETS_ENV
        context = patch.dict(os.environ, environment)
        context.start()
        self.addCleanup(context.stop)
        factory = patch.object(sheets.gspread, 'service_account_from_dict', return_value=fixture.client)
        mocked = factory.start()
        self.addCleanup(factory.stop)
        return fixture, mocked

    def test_real_http_read_timeout_and_exact_workbook_worksheet_contract(self):
        for configured, expected in [('2.5', 2.5), (0, 5), ('nan', 5), ('inf', 5), ('invalid', 5)]:
            with self.subTest(configured=configured):
                fixture = GoogleFixture()
                with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), \
                        patch.object(sheets.gspread, 'service_account_from_dict', return_value=fixture.client), \
                        patch.dict(self.app.config, {'GOOGLE_SHEETS_REQUEST_TIMEOUT_SECONDS': configured}):
                    actual = sheets.google_primary_operational_values(self.gateway, force=True)
                self.assertEqual(actual, fixture.values)
                self.assertEqual(len(fixture.calls), 3)
                self.assertTrue(all(call[2]['timeout'] == expected for call in fixture.calls))
                self.assertEqual([area.split('!')[-1] for area in fixture.calls[-1][2]['params']['ranges']], list(sheets.SHEET_CELL_ORDER))
                self.assertTrue(all("'Live Counts'!" in area for area in fixture.calls[-1][2]['params']['ranges']))
                fixture.transport.close.assert_called_once()
                self.assertIsNone(sheets._google_operation_state.get())

    def test_stalled_reads_api_errors_and_connection_failures_are_controlled_without_retry(self):
        for stage in (1, 2, 3):
            for failure in (Timeout('stall'), ConnectionError('connection'), response({'error': {'code': 401, 'message': 'unauthorized'}}, 401),
                            response({'error': {'code': 403, 'message': 'denied'}}, 403), response({'error': {'code': 404, 'message': 'missing'}}, 404),
                            response({'error': {'code': 429, 'message': 'rate limited'}}, 429), response({'error': {'code': 503, 'message': 'unavailable'}}, 503)):
                with self.subTest(stage=stage, failure=type(failure).__name__):
                    fixture = GoogleFixture()
                    fixture.fail_at, fixture.failure = stage, failure
                    with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), patch.object(sheets.gspread, 'service_account_from_dict', return_value=fixture.client):
                        with self.assertRaises(sheets.NeoSektorGoogleError):
                            sheets.google_primary_operational_values(self.gateway, force=True)
                    self.assertEqual(len(fixture.calls), stage)
                    self.assertFalse(fixture.writes)
                    fixture.transport.close.assert_called_once()

    def test_authentication_failure_and_timeout_configuration_failure_cleanup(self):
        with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), patch.object(sheets.gspread, 'service_account_from_dict', side_effect=ValueError('private fixture data')):
            with self.assertLogs(sheets.logger, level='WARNING') as logs, self.assertRaises(sheets.NeoSektorGoogleError):
                sheets.google_primary_operational_values(self.gateway, force=True)
        self.assertNotIn('private fixture data', str(logs.output))
        fixture, _factory = self.google()
        with patch.object(fixture.client, 'set_timeout', side_effect=RuntimeError('configuration')):
            with self.assertRaises(sheets.NeoSektorGoogleError):
                sheets.google_primary_operational_values(self.gateway, force=True)
        fixture.transport.close.assert_called_once()
        self.assertFalse(fixture.calls)

    def test_missing_worksheet_is_controlled_and_client_is_closed(self):
        fixture, _factory = self.google()
        fixture.missing = True
        with self.assertRaises(sheets.NeoSektorGoogleError):
            sheets.google_primary_operational_values(self.gateway, force=True)
        fixture.transport.close.assert_called_once()

    def test_shared_authority_read_write_and_verification_reuse_one_bounded_client(self):
        fixture, factory = self.google(shared=True)
        status = sheets.transition_neosektor_shared_authority(self.gateway, sheets.STANDALONE_PRIMARY_AUTHORITY, expected_generation=7)
        self.assertEqual((status['authority'], status['generation']), (sheets.STANDALONE_PRIMARY_AUTHORITY, 8))
        self.assertEqual(len(fixture.writes), 1)
        factory.assert_called_once()
        # open_by_key once, but lookup/read each authority revision afresh.
        self.assertEqual(sum(url.endswith('/test-sheet-id') for _, url, _ in fixture.calls), 4)
        self.assertTrue(all(call[2]['timeout'] == 5 for call in fixture.calls))
        fixture.transport.close.assert_called_once()

    def test_shared_authority_timeout_never_claims_transition_success_or_replays(self):
        fixture, _factory = self.google(shared=True)
        fixture.fail_cell = 'authority'
        with self.assertRaisesRegex(sheets.NeoSektorSharedAuthorityError, 'unverified'):
            sheets.transition_neosektor_shared_authority(self.gateway, sheets.STANDALONE_PRIMARY_AUTHORITY, expected_generation=7)
        self.assertEqual(len(fixture.writes), 1)
        # The simulated provider applied the write despite losing its response.
        self.assertEqual(fixture.authority['B2'], 8)
        fixture.transport.close.assert_called_once()

    def test_shared_authority_initialization_headers_and_record_are_bounded(self):
        fixture, factory = self.google(shared=True)
        fixture.authority.clear()
        status = sheets.initialize_neosektor_shared_authority(self.gateway)
        self.assertEqual(status['generation'], 1)
        self.assertEqual([area.split('!')[-1] for area, _ in fixture.writes], ['A1:E1', 'A2:E2'])
        self.assertTrue(all(call[2]['timeout'] == 5 for call in fixture.calls))
        factory.assert_called_once()
        fixture.transport.close.assert_called_once()

    def test_shared_metadata_timeout_preserves_unverified_outcome_and_cleanup(self):
        fixture, factory = self.google(shared=True)
        fixture.fail_cell = 'authority'
        @sheets._google_operation
        def save():
            worksheet = sheets._get_shared_authority_worksheet()
            sheets._write_shared_authority_metadata(worksheet, {'fixture': 'changed'})
        with self.assertRaisesRegex(sheets.NeoSektorSharedAuthorityError, 'unverified'):
            save()
        self.assertEqual(json.loads(fixture.authority['E2']), {'fixture': 'changed'})
        self.assertEqual(len(fixture.writes), 1)
        self.assertTrue(all(call[2]['timeout'] == 5 for call in fixture.calls))
        factory.assert_called_once()
        fixture.transport.close.assert_called_once()

    def test_write_api_failures_are_not_retried_or_reported_successful(self):
        for status in (401, 403, 429, 503):
            with self.subTest(status=status):
                fixture = GoogleFixture()
                fixture.fail_at = 3
                fixture.failure = response({'error': {'code': status, 'message': 'fixture unavailable'}}, status)
                with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), patch.object(sheets.gspread, 'service_account_from_dict', return_value=fixture.client):
                    with self.assertRaisesRegex(sheets.NeoSektorGoogleError, 'unverified'):
                        sheets.write_google_primary_operational_values(self.gateway, {'B2': 12}, integration_mode=sheets.GOOGLE_PRIMARY)
                self.assertEqual(len(fixture.calls), 3)
                self.assertFalse(fixture.writes)
                fixture.transport.close.assert_called_once()

    def test_google_primary_partial_write_discards_cache_and_never_updates_neo(self):
        fixture, _factory = self.google()
        sheets._store_google_values(self.gateway.id, dict(fixture.values))
        fixture.fail_cell = 'C2'
        with self.assertRaisesRegex(sheets.NeoSektorGoogleError, 'unverified'):
            sheets.write_google_primary_operational_values(self.gateway, {'D2': 30, 'C2': 10, 'B2': 12}, integration_mode=sheets.GOOGLE_PRIMARY)
        self.assertEqual(fixture.writes, [('B2', 12), ('C2', 10)])
        self.assertIsNone(sheets._cached_google_values(self.gateway.id))
        self.assertEqual(fixture.values['D2'], 18)
        fixture.transport.close.assert_called_once()

    def test_partial_timeout_pending_survives_later_delta_until_explicit_sync(self):
        self._set_mode(sheets.NEO_PRIMARY_GOOGLE_MIRROR)
        from app.services.neosektor_live_counts import apply_standalone_compat_values, canonical_neosektor_compat_values
        desired = fixtures._complete_sheet_values(B2=12, C2=10, D2=30)
        apply_standalone_compat_values(self.gateway, desired)
        desired = canonical_neosektor_compat_values(self.gateway)
        setting = db.session.query(NeoSektorOperationalSetting).one()
        setting.google_mirror_writes_enabled = True
        db.session.commit()
        fixture, _factory = self.google()
        fixture.fail_cell = 'C2'
        result = sheets.mirror_neosektor_operational_values(fixtures._complete_sheet_values(), desired, gateway=self.gateway)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(fixture.writes, [('B2', 12), ('C2', 10)])
        self.assertTrue(setting.google_mirror_sync_needed)
        self.assertEqual(canonical_neosektor_compat_values(self.gateway), desired)
        fixture.fail_cell = None
        apply_standalone_compat_values(self.gateway, fixtures._complete_sheet_values(B2=12, C2=10, D2=30, B3=8))
        db.session.commit()
        later = canonical_neosektor_compat_values(self.gateway)
        result = sheets.mirror_neosektor_operational_values(desired, later, gateway=self.gateway)
        self.assertEqual(result['status'], 'mirrored')
        self.assertTrue(setting.google_mirror_sync_needed)
        self.assertEqual(fixture.values['D2'], 18)
        fixture.writes.clear()
        sheets.retry_neosektor_google_mirror(self.gateway)
        self.assertFalse(setting.google_mirror_sync_needed)
        self.assertEqual([cell for cell, _ in fixture.writes], list(sheets.MIRROR_CELL_ORDER))
        self.assertEqual(fixture.values['D2'], 30)
        self.assertEqual(fixture.transport.close.call_count, 3)

    def test_successful_write_order_and_shared_fence_preserve_authority(self):
        fixture, factory = self.google(shared=True)
        result = sheets.write_google_primary_operational_values(self.gateway, {'B3': 9, 'B2': 12}, integration_mode=sheets.GOOGLE_PRIMARY)
        self.assertEqual(result, {'B3': 9, 'B2': 12})
        self.assertEqual(fixture.writes, [('B2', 12), ('B3', 9)])
        factory.assert_called_once()
        self.assertTrue(all(call[2]['timeout'] == 5 for call in fixture.calls))
        fixture.transport.close.assert_called_once()
        fixture.authority['A2'] = sheets.STANDALONE_PRIMARY_AUTHORITY
        with self.assertRaises(sheets.NeoSektorSharedAuthorityError):
            sheets.write_google_primary_operational_values(self.gateway, {'B2': 2}, integration_mode=sheets.GOOGLE_PRIMARY)
        self.assertEqual(fixture.writes, [('B2', 12), ('B3', 9)])

    def test_borrowed_worksheet_and_cache_hits_are_not_closed_or_reauthenticated(self):
        borrowed = fixtures._FakeWorksheet()
        borrowed.close = Mock()
        with patch.object(sheets.gspread, 'service_account_from_dict') as factory:
            self.assertEqual(sheets.read_neosektor_sheet_values(borrowed), borrowed.values)
            factory.assert_not_called()
        borrowed.close.assert_not_called()
        fixture, factory = self.google()
        sheets.google_primary_operational_values(self.gateway, force=True)
        sheets.google_primary_operational_values(self.gateway)
        factory.assert_called_once()
        fixture.transport.close.assert_called_once()

    def test_cleanup_failure_does_not_reclassify_acknowledged_write_or_replay(self):
        fixture, _factory = self.google()
        fixture.transport.close.side_effect = RuntimeError('private cleanup detail')
        with self.assertLogs(sheets.logger, level='WARNING') as logs:
            result = sheets.write_google_primary_operational_values(self.gateway, {'B2': 12}, integration_mode=sheets.GOOGLE_PRIMARY)
        self.assertEqual(result, {'B2': 12})
        self.assertEqual(fixture.writes, [('B2', 12)])
        self.assertNotIn('private cleanup detail', str(logs.output))
        self.assertIsNone(sheets._google_operation_state.get())

    def test_borrowed_gspread_worksheets_are_bounded_without_mutating_or_closing_owner(self):
        for title in ('Live Counts', 'NeoSektor Authority'):
            with self.subTest(title=title):
                fixture = GoogleFixture()
                book = fixture.client.open_by_key('test-sheet-id')
                worksheet = book.worksheet(title)
                fixture.calls.clear()
                with patch.dict(os.environ, fixtures.FAKE_SHARED_AUTHORITY_ENV), patch.object(sheets.gspread, 'service_account_from_dict') as factory:
                    if title == 'Live Counts':
                        self.assertEqual(sheets.read_neosektor_sheet_values(worksheet), fixture.values)
                    else:
                        self.assertEqual(sheets.read_neosektor_shared_authority(self.gateway,
                            force=True, worksheet=worksheet)['generation'], 7)
                    factory.assert_not_called()
                self.assertEqual(len(fixture.calls), 1)
                self.assertEqual(fixture.calls[0][2]['timeout'], 5)
                self.assertIsNone(worksheet.client.timeout)
                fixture.transport.close.assert_not_called()
                fixture.transport.close()  # The external owner retains cleanup.

    def test_separate_thread_operations_do_not_share_or_retain_clients(self):
        clients = [GoogleFixture(), GoogleFixture()]
        with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), patch.object(sheets.gspread, 'service_account_from_dict', side_effect=[value.client for value in clients]):
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = list(pool.map(lambda _: sheets.read_neosektor_sheet_values(), range(2)))
        self.assertEqual(result, [fixtures._complete_sheet_values()] * 2)
        for fixture in clients:
            fixture.transport.close.assert_called_once()
        self.assertIsNone(sheets._google_operation_state.get())

    def test_real_google_auth_initial_refresh_and_api_calls_share_finite_timeout(self):
        self.check_oauth(False)

    def test_real_google_auth_refresh_timeout_closes_oauth_and_api_sessions(self):
        self.check_oauth(True)

    def check_oauth(self, fail):
        fixture = GoogleFixture()
        class LocalCredentials(Credentials):
            def refresh(self, request):
                request(url='https://oauth2.googleapis.com/token', method='POST', body=b'fixture')
                self.token = 'isolated-token'
        if fail:
            credentials = LocalCredentials()
        else:
            # Exercise the installed service-account JWT refresh, not just a
            # mocked token. The throwaway private key stays in test memory.
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from google.oauth2 import service_account
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            credentials = service_account.Credentials.from_service_account_info({
                'type': 'service_account', 'client_email': 'fixture@example.invalid',
                'token_uri': 'https://oauth2.googleapis.com/token',
                'private_key': key.private_bytes(serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii'),
            }, scopes=gspread.auth.DEFAULT_SCOPES)
        seen = []
        closed = []
        iam_sessions = []
        iam_complete = Event()
        original_close = Session.close
        def close(transport):
            closed.append(transport)
            original_close(transport)
            if transport in iam_sessions:
                iam_complete.set()
        def create(_info, *, http_client):
            return gspread.Client(auth=credentials, http_client=http_client)
        def request(transport, method, url, **kwargs):
            seen.append((url, kwargs.get('timeout')))
            if url.startswith('https://oauth2.'):
                if fail:
                    raise Timeout('isolated OAuth stall')
                return response({'access_token': 'isolated-token', 'expires_in': 3600, 'token_type': 'Bearer'})
            if url.startswith('https://iamcredentials.'):
                iam_sessions.append(transport)
                return response({'locations': [], 'encodedLocations': '0x0'})
            return fixture.request(method, url, **kwargs)
        with patch.dict(os.environ, fixtures.FAKE_SHEETS_ENV), \
                patch.object(sheets.gspread, 'service_account_from_dict', side_effect=create), \
                patch('requests.sessions.Session.request', autospec=True, side_effect=request), \
                patch('requests.sessions.Session.close', autospec=True, side_effect=close):
            if fail:
                with self.assertRaises(sheets.NeoSektorGoogleError):
                    sheets.google_primary_operational_values(self.gateway, force=True)
            else:
                self.assertEqual(sheets.google_primary_operational_values(self.gateway, force=True), fixture.values)
                self.assertTrue(iam_complete.wait(2))
        self.assertTrue(all(timeout == 5 for _, timeout in seen),
            [(url, timeout) for url, timeout in seen])
        self.assertEqual(len(seen), 1 if fail else 5)
        # Request's SDK destructor also closes its supplied session. Closing
        # twice is harmless; every distinct API/auth session must be released.
        self.assertEqual(len({id(transport) for transport in closed}), 2 if fail else 3)
