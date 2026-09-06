"""Exercise gspread's real HTTPClient with a local mocked transport only."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gspread
from requests.exceptions import Timeout

from app.services import google_motherbrain_sheets as sheets
from app.services import google_rain_sheets as rain
from tests.test_google_motherbrain_sheets import reader_config


class GoogleSheetsTimeoutTest(unittest.TestCase):
    def test_defaults_overrides_and_invalid_values_reach_http_transport(self):
        for configured, expected in ((None, 5), ('2.5', 2.5), (0, 5), (-1, 5),
                                     ('bad', 5), ('nan', 5), ('inf', 5)):
            for factory in (sheets._create_gspread_client, sheets._create_gspread_writer):
                with self.subTest(configured=configured, factory=factory.__name__):
                    transport = Mock()
                    transport.request.return_value = Mock(ok=True)
                    transport.request.return_value.json.return_value = {
                        'properties': {'id': 'test', 'title': 'test'}, 'sheets': [],
                    }
                    client = gspread.Client(auth=None, session=transport)
                    config = {} if configured is None else {'GOOGLE_SHEETS_REQUEST_TIMEOUT_SECONDS': configured}
                    with patch.object(sheets.gspread, 'service_account_from_dict', return_value=client):
                        actual = factory({}, config)
                    self.assertIs(type(actual.http_client), gspread.HTTPClient)
                    book = actual.open_by_key('test')
                    book.fetch_sheet_metadata()
                    book.values_batch_get(['A1'])
                    book.values_update('A1', params={'valueInputOption': 'RAW'}, body={'values': [[1]]})
                    book.values_batch_clear(['A1'])
                    self.assertEqual(transport.request.call_count, 5)
                    for call in transport.request.call_args_list:
                        self.assertEqual(call.kwargs['timeout'], expected)

    def test_motherbrain_and_rain_defaults_use_shared_timeout_without_retry(self):
        calls = (
            lambda c: sheets.read_google_motherbrain_live_rows(c),
            lambda c: sheets.read_google_motherbrain_envelope(c),
            lambda c: sheets.read_google_motherbrain_reset_parking_formulas(c),
            lambda c: sheets._clear_google_motherbrain_reset_ranges(['Inbound!A4'], c),
            lambda c: rain.read_google_rain_outbound_milestones(c),
            lambda c: rain.write_google_rain_departure_milestone(
                SimpleNamespace(mission_type='departure'), 'no_return', True, config=c),
        )
        for invoke in calls:
            with self.subTest(invoke=invoke):
                transport = Mock()
                transport.request.side_effect = Timeout('synthetic Google timeout')
                client = gspread.Client(auth=None, session=transport)
                with patch.object(sheets.gspread, 'service_account_from_dict', return_value=client):
                    with self.assertRaises((sheets.GoogleMotherBrainReaderError, rain.GoogleRainWriterError)) as error:
                        invoke(reader_config(GOOGLE_SHEETS_REQUEST_TIMEOUT_SECONDS='3'))
                self.assertIn(error.exception.code, ('google_timeout', 'google_failure'))
                self.assertEqual(transport.request.call_count, 1)
                self.assertEqual(transport.request.call_args.kwargs['timeout'], 3)
