from datetime import date, datetime
import json
import unittest

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
)
from app.services.google_rain_sheets import (
    GOOGLE_RAIN_LOCKED_SPREADSHEET_ID,
    GoogleRainWriterError,
    write_google_rain_departure_milestone,
)


SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "neoapps-test",
    "private_key_id": "test-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\\ntest\\n-----END PRIVATE KEY-----\\n",
    "client_email": "rain-writer@example.test",
    "client_id": "123456789",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class GoogleRainMilestoneWriterTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "GoogleRainMilestoneWriterTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-writer-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 6, 18),
        )
        db.session.add(self.operation)
        db.session.flush()
        self.mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            mission_source="master",
            flight_number="UPS0910",
            origin="RFD",
            destination="LAX",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 19, 2, 24),
            departure_status="scheduled",
        )
        db.session.add(self.mission)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_each_rain_field_writes_only_its_mapped_cell(self):
        expected = {
            "ramp_load_complete": ("M3", "02:37"),
            "crew_load_complete": ("N3", "02:37"),
            "official_block_out": ("O3", "02:37"),
            "no_return": ("S3", "TRUE"),
        }
        canonical_time = datetime(2026, 6, 19, 7, 37)
        before = self._mission_snapshot()

        for field, (expected_cell, expected_value) in expected.items():
            with self.subTest(field=field):
                spreadsheet = _FakeSpreadsheet(self._identity_values())
                result = write_google_rain_departure_milestone(
                    self.mission,
                    field,
                    True if field == "no_return" else canonical_time,
                    operation=self.operation,
                    config=_writer_config(),
                    client_factory=lambda _credentials: _FakeClient(spreadsheet),
                )
                self.assertEqual(result["cell"], expected_cell)
                self.assertEqual(result["value"], expected_value)
                self.assertEqual(spreadsheet.worksheet_object.updates, [(expected_cell, expected_value)])
                self.assertEqual(
                    spreadsheet.batch_calls[0][0],
                    ["Outbound!A3:A50", "Outbound!C3:C50", "Outbound!E3:E50"],
                )
                self.assertNotIn("Outbound!M3:M50", spreadsheet.batch_calls[0][0])
                self.assertNotIn("Outbound!N3:N50", spreadsheet.batch_calls[0][0])
                self.assertNotIn("Outbound!O3:O50", spreadsheet.batch_calls[0][0])
                self.assertNotIn("Outbound!S3:S50", spreadsheet.batch_calls[0][0])
                self.assertFalse(spreadsheet.worksheet_object.updates[0][0].startswith("L"))

        self.assertEqual(self._mission_snapshot(), before)

    def test_clear_writes_blank_time_or_false_checkbox(self):
        for field, value, expected in (
            ("ramp_load_complete", None, ("M3", "")),
            ("no_return", False, ("S3", "FALSE")),
        ):
            with self.subTest(field=field):
                spreadsheet = _FakeSpreadsheet(self._identity_values())
                write_google_rain_departure_milestone(
                    self.mission,
                    field,
                    value,
                    operation=self.operation,
                    config=_writer_config(),
                    client_factory=lambda _credentials: _FakeClient(spreadsheet),
                )
                self.assertEqual(spreadsheet.worksheet_object.updates, [expected])

    def test_ambiguous_or_missing_identity_rejects_without_write(self):
        ambiguous = _FakeSpreadsheet(
            self._identity_values(
                flights=[["UPS0910"], ["UPS0910"]],
                destinations=[["LAX"], ["LAX"]],
                stds=[["2:24"], ["2:24"]],
            )
        )
        with self.assertRaisesRegex(GoogleRainWriterError, "multiple matching"):
            write_google_rain_departure_milestone(
                self.mission,
                "ramp_load_complete",
                datetime(2026, 6, 19, 7, 37),
                operation=self.operation,
                config=_writer_config(),
                client_factory=lambda _credentials: _FakeClient(ambiguous),
            )
        self.assertEqual(ambiguous.worksheet_object.updates, [])

        missing = _FakeSpreadsheet(self._identity_values(flights=[["UPS9999"]]))
        with self.assertRaisesRegex(GoogleRainWriterError, "No Google Rain"):
            write_google_rain_departure_milestone(
                self.mission,
                "ramp_load_complete",
                datetime(2026, 6, 19, 7, 37),
                operation=self.operation,
                config=_writer_config(),
                client_factory=lambda _credentials: _FakeClient(missing),
            )
        self.assertEqual(missing.worksheet_object.updates, [])

    def test_planned_std_disambiguates_same_flight_and_destination(self):
        spreadsheet = _FakeSpreadsheet(
            self._identity_values(
                flights=[["UPS0910"], ["UPS0910"]],
                destinations=[["LAX"], ["LAX"]],
                stds=[["1:15"], ["2:24"]],
            )
        )

        result = write_google_rain_departure_milestone(
            self.mission,
            "crew_load_complete",
            datetime(2026, 6, 19, 7, 37),
            operation=self.operation,
            config=_writer_config(),
            client_factory=lambda _credentials: _FakeClient(spreadsheet),
        )

        self.assertEqual(result["sheet_row"], 4)
        self.assertEqual(spreadsheet.worksheet_object.updates, [("N4", "02:37")])

    def test_elmac_writes_to_column_l(self):
        spreadsheet = _FakeSpreadsheet(self._identity_values())
        result = write_google_rain_departure_milestone(
            self.mission,
            "elmac",
            datetime(2026, 6, 19, 7, 37),
            operation=self.operation,
            config=_writer_config(),
            client_factory=lambda _credentials: _FakeClient(spreadsheet),
        )
        self.assertEqual(result["cell"], "L3")
        self.assertEqual(spreadsheet.worksheet_object.updates, [("L3", "02:37")])

    @staticmethod
    def _identity_values(
        flights=None,
        destinations=None,
        stds=None,
    ):
        return {
            "Outbound!A3:A50": flights or [["UPS0910"]],
            "Outbound!C3:C50": destinations or [["LAX"]],
            "Outbound!E3:E50": stds or [["2:24"]],
        }

    def _mission_snapshot(self):
        return (
            self.mission.ramp_load_completed_at_utc,
            self.mission.ramp_load_completed_source,
            self.mission.crew_load_completed_at_utc,
            self.mission.crew_load_completed_source,
            self.mission.actual_block_out_datetime_utc,
            self.mission.actual_block_out_source,
            self.mission.departure_status,
            self.mission.departure_status_source,
        )


def _writer_config():
    return {
        "GOOGLE_MOTHERBRAIN_READER_ENABLED": True,
        "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT),
        "GOOGLE_SERVICE_ACCOUNT_JSON": None,
        "GOOGLE_MOTHERBRAIN_SPREADSHEET_ID": GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    }


class _FakeSpreadsheet:
    def __init__(self, values_by_range):
        self.values_by_range = values_by_range
        self.batch_calls = []
        self.worksheet_object = _FakeWorksheet()

    def values_batch_get(self, ranges, params=None):
        self.batch_calls.append((list(ranges), dict(params or {})))
        return {
            "valueRanges": [
                {"range": range_name, "values": self.values_by_range[range_name]}
                for range_name in ranges
            ]
        }

    def worksheet(self, name):
        if name != "Outbound":
            raise AssertionError(f"Unexpected worksheet {name}")
        return self.worksheet_object


class _FakeWorksheet:
    def __init__(self):
        self.updates = []

    def update_acell(self, cell, value):
        self.updates.append((cell, value))


class _FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened_ids = []

    def open_by_key(self, spreadsheet_id):
        self.opened_ids.append(spreadsheet_id)
        if spreadsheet_id != GOOGLE_RAIN_LOCKED_SPREADSHEET_ID:
            raise AssertionError("Writer opened the wrong spreadsheet")
        return self.spreadsheet


if __name__ == "__main__":
    unittest.main()
