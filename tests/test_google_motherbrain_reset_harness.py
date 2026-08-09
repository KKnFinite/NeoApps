from contextlib import redirect_stderr
import io
import json
import unittest
from unittest.mock import Mock

from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE,
)
from scripts.test_google_motherbrain_reset import (
    GoogleMotherBrainResetTestHarnessError,
    RESET_TEST_COPY_TITLE_MARKER,
    parse_args,
    run_reset_test_harness,
)


SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "neoapps-test",
    "private_key_id": "test-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\\ntest\\n-----END PRIVATE KEY-----\\n",
    "client_email": "motherbrain-reset-test@example.test",
    "client_id": "123456789",
    "token_uri": "https://oauth2.googleapis.com/token",
}
TEST_SPREADSHEET_ID = "1LLPh-IjVSM8hB1YfPEKn524V-iiBAA7IgaH1AK44tp8"


def harness_config():
    return {
        "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT),
        "GOOGLE_SERVICE_ACCOUNT_JSON": None,
    }


class FakeSpreadsheet:
    def __init__(self, title=f"RFD MotherBrain {RESET_TEST_COPY_TITLE_MARKER}"):
        self.metadata = {
            "properties": {"title": title},
            "sheets": [
                {"properties": {"title": "Inbound"}},
                {"properties": {"title": "Outbound"}},
                {"properties": {"title": "Parking Plan"}},
            ],
        }
        self.clear_calls = []
        self.formula_calls = []

    def fetch_sheet_metadata(self, params=None):
        return self.metadata

    def values_batch_get(self, ranges, params=None):
        self.formula_calls.append((list(ranges), dict(params or {})))
        return {"valueRanges": [{"values": [["=U13"], ["=AC13"]]}]}

    def values_batch_clear(self, clear_ranges):
        self.clear_calls.append(list(clear_ranges))


class FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened_ids = []

    def open_by_key(self, spreadsheet_id):
        self.opened_ids.append(spreadsheet_id)
        return self.spreadsheet


class GoogleMotherBrainResetHarnessTest(unittest.TestCase):
    def _run(self, spreadsheet, *, execute=False, spreadsheet_id=TEST_SPREADSHEET_ID):
        client = FakeClient(spreadsheet)
        result = run_reset_test_harness(
            spreadsheet_id,
            execute=execute,
            config=harness_config(),
            client_factory=lambda _credentials: client,
        )
        return result, client

    def test_requires_an_explicit_spreadsheet_id(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parse_args([])

    def test_refuses_the_locked_production_workbook_id(self):
        client_factory = Mock()

        with self.assertRaisesRegex(
            GoogleMotherBrainResetTestHarnessError,
            "locked production MotherBrain workbook",
        ):
            run_reset_test_harness(
                GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
                config=harness_config(),
                client_factory=client_factory,
            )

        client_factory.assert_not_called()

    def test_refuses_a_workbook_without_the_test_copy_title_marker(self):
        spreadsheet = FakeSpreadsheet(title="RFD-N-sim: Mother Brain")

        with self.assertRaisesRegex(
            GoogleMotherBrainResetTestHarnessError,
            "must contain RESET TEST COPY",
        ):
            self._run(spreadsheet)

        self.assertEqual(spreadsheet.formula_calls, [])
        self.assertEqual(spreadsheet.clear_calls, [])

    def test_default_dry_run_reads_formulas_and_never_clears(self):
        spreadsheet = FakeSpreadsheet()

        result, client = self._run(spreadsheet)

        self.assertFalse(result["executed"])
        self.assertEqual(client.opened_ids, [TEST_SPREADSHEET_ID])
        self.assertEqual(
            spreadsheet.formula_calls,
            [
                (
                    [GOOGLE_MOTHERBRAIN_RESET_PARKING_FORMULA_RANGE],
                    {
                        "valueRenderOption": "FORMULA",
                        "dateTimeRenderOption": "FORMATTED_STRING",
                        "majorDimension": "ROWS",
                    },
                )
            ],
        )
        self.assertEqual(spreadsheet.clear_calls, [])
        self.assertIn("Parking Plan!U13", result["clear_ranges"])
        self.assertIn("Parking Plan!AC13", result["clear_ranges"])

    def test_execute_clears_only_the_existing_approved_reset_plan(self):
        spreadsheet = FakeSpreadsheet()

        result, _client = self._run(spreadsheet, execute=True)

        self.assertTrue(result["executed"])
        self.assertEqual(spreadsheet.clear_calls, [list(result["clear_ranges"])])
        self.assertIn("Inbound!A16:G100", result["clear_ranges"])
        self.assertIn("Outbound!A16:G100", result["clear_ranges"])
        self.assertFalse(
            any(
                clear_range.startswith(("Inbound!A15:", "Outbound!A15:"))
                for clear_range in result["clear_ranges"]
            )
        )


if __name__ == "__main__":
    unittest.main()
