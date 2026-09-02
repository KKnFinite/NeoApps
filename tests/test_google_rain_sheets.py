import json
import unittest

from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
)
from app.services.google_rain_sheets import (
    GOOGLE_RAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_RAIN_OUTBOUND_RANGE_SPECS,
    read_google_rain_outbound_milestones,
)


SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "neoapps-test",
    "private_key_id": "test-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\\ntest\\n-----END PRIVATE KEY-----\\n",
    "client_email": "rain-reader@example.test",
    "client_id": "123456789",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class GoogleRainSheetsTest(unittest.TestCase):
    def test_reader_fetches_the_locked_m_n_o_s_outbound_bundle_in_one_batch(self):
        spreadsheet = _FakeSpreadsheet(
            {
                "Outbound!A3:A50": [["UPS9992"], ["UPS7831"]],
                "Outbound!C3:C50": [["SPARE"], ["SDF"]],
                "Outbound!E3:E50": [[""], ["1:15"]],
                "Outbound!M3:M50": [[""], ["2:22"]],
                "Outbound!N3:N50": [[""], ["2:24"]],
                "Outbound!O3:O50": [[""], ["2:29"]],
                "Outbound!S3:S50": [["FALSE"], ["TRUE"]],
            }
        )
        client = _FakeClient(spreadsheet)

        rows = read_google_rain_outbound_milestones(
            _reader_config(),
            client_factory=lambda _credentials: client,
        )

        self.assertEqual(client.opened_ids, [GOOGLE_RAIN_LOCKED_SPREADSHEET_ID])
        self.assertNotEqual(
            GOOGLE_RAIN_LOCKED_SPREADSHEET_ID,
            GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
        )
        self.assertEqual(len(spreadsheet.batch_calls), 1)
        self.assertEqual(
            spreadsheet.batch_calls[0][0],
            [spec[1] for spec in GOOGLE_RAIN_OUTBOUND_RANGE_SPECS],
        )
        self.assertEqual(
            spreadsheet.batch_calls[0][1]["valueRenderOption"],
            "FORMATTED_VALUE",
        )
        self.assertEqual(
            rows[1],
            {
                "source_sheet": "Outbound",
                "sheet_row": 4,
                "flight_number": "UPS7831",
                "destination": "SDF",
                "std": "1:15",
                "ramp_load_complete": "2:22",
                "crew_load_complete": "2:24",
                "block": "2:29",
                "no_return": "TRUE",
            },
        )
        self.assertNotIn("Outbound!L3:L50", spreadsheet.batch_calls[0][0])
        self.assertNotIn("elmac", rows[1])
        self.assertNotIn("tail", rows[1])

    def test_reader_source_has_no_google_write_calls(self):
        import inspect

        source = inspect.getsource(read_google_rain_outbound_milestones)
        for method in (
            "update",
            "batch_update",
            "append_row",
            "clear",
            "values_batch_clear",
        ):
            self.assertNotIn(f".{method}(", source)

    def test_reader_ignores_checkbox_only_false_rows_but_keeps_meaningful_rows(self):
        spreadsheet = _FakeSpreadsheet(
            {
                "Outbound!A3:A50": [[], [], []],
                "Outbound!C3:C50": [[], [], []],
                "Outbound!E3:E50": [[], [], []],
                "Outbound!M3:M50": [[], [], []],
                "Outbound!N3:N50": [[], [], []],
                "Outbound!O3:O50": [[], [], ["03:10"]],
                "Outbound!S3:S50": [["FALSE"], ["TRUE"], ["FALSE"]],
            }
        )

        rows = read_google_rain_outbound_milestones(
            _reader_config(),
            client_factory=lambda _credentials: _FakeClient(spreadsheet),
        )

        self.assertEqual(
            rows,
            [
                {
                    "source_sheet": "Outbound",
                    "sheet_row": 4,
                    "flight_number": "",
                    "destination": "",
                    "std": "",
                    "ramp_load_complete": "",
                    "crew_load_complete": "",
                    "block": "",
                    "no_return": "TRUE",
                },
                {
                    "source_sheet": "Outbound",
                    "sheet_row": 5,
                    "flight_number": "",
                    "destination": "",
                    "std": "",
                    "ramp_load_complete": "",
                    "crew_load_complete": "",
                    "block": "03:10",
                    "no_return": "FALSE",
                },
            ],
        )


def _reader_config():
    return {
        "GOOGLE_MOTHERBRAIN_READER_ENABLED": True,
        "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT),
        "GOOGLE_SERVICE_ACCOUNT_JSON": None,
        "GOOGLE_MOTHERBRAIN_SPREADSHEET_ID": (
            GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID
        ),
    }


class _FakeSpreadsheet:
    def __init__(self, values_by_range):
        self.values_by_range = values_by_range
        self.batch_calls = []

    def values_batch_get(self, ranges, params=None):
        self.batch_calls.append((list(ranges), dict(params or {})))
        return {
            "valueRanges": [
                {"range": range_name, "values": self.values_by_range[range_name]}
                for range_name in ranges
            ]
        }


class _FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened_ids = []

    def open_by_key(self, spreadsheet_id):
        self.opened_ids.append(spreadsheet_id)
        return self.spreadsheet


if __name__ == "__main__":
    unittest.main()
