from copy import deepcopy
from datetime import date, datetime, timezone
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
    GOOGLE_MOTHERBRAIN_RANGE_SPECS,
    GOOGLE_MOTHERBRAIN_READONLY_SCOPES,
    GoogleMotherBrainReaderError,
    google_motherbrain_reader_status,
    read_google_motherbrain_envelope,
)
from app.services.password_policy import set_user_password


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "google_motherbrain_current_sort_v1.json"
)
SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "neoapps-test",
    "private_key_id": "test-key-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\\ntest\\n-----END PRIVATE KEY-----\\n",
    "client_email": "motherbrain-reader@example.test",
    "client_id": "123456789",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def reader_config(**overrides):
    config = {
        "GOOGLE_MOTHERBRAIN_READER_ENABLED": True,
        "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": json.dumps(SERVICE_ACCOUNT),
        "GOOGLE_SERVICE_ACCOUNT_JSON": None,
        "GOOGLE_MOTHERBRAIN_SPREADSHEET_ID": (
            GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID
        ),
    }
    config.update(overrides)
    return config


def range_response(values_by_key):
    return {
        "valueRanges": [
            {"range": a1, "values": values_by_key.get(key, [])}
            for key, a1, _rows, _columns, _start in GOOGLE_MOTHERBRAIN_RANGE_SPECS
        ]
    }


class FakeSpreadsheet:
    def __init__(self, raw_by_key=None, formatted_by_key=None, metadata=None):
        self.raw_by_key = raw_by_key or {}
        self.formatted_by_key = formatted_by_key or {}
        self.metadata = metadata or {
            "properties": {
                "title": "RFD-N-sim: Mother Brain",
                "timeZone": "America/Chicago",
            },
            "sheets": [
                {"properties": {"title": "Inbound"}},
                {"properties": {"title": "Outbound"}},
                {"properties": {"title": "Parking Plan"}},
            ],
        }
        self.batch_calls = []
        self.metadata_calls = []

    def fetch_sheet_metadata(self, params=None):
        self.metadata_calls.append(params)
        return self.metadata

    def values_batch_get(self, ranges, params=None):
        self.batch_calls.append((list(ranges), dict(params or {})))
        source = (
            self.raw_by_key
            if params.get("valueRenderOption") == "UNFORMATTED_VALUE"
            else self.formatted_by_key
        )
        return range_response(source)


class FakeClient:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.opened_ids = []

    def open_by_key(self, spreadsheet_id):
        self.opened_ids.append(spreadsheet_id)
        return self.spreadsheet


class FakeGoogleError(Exception):
    def __init__(self, status_code):
        self.response = SimpleNamespace(status_code=status_code)
        super().__init__(f"provider failure {status_code}")


class GoogleMotherBrainSheetsReaderTest(unittest.TestCase):
    def _spreadsheet(self):
        serial = (date(2026, 8, 5) - date(1899, 12, 30)).days
        raw = {
            "sort_date": [[serial]],
            "inbound_manual": [
                [serial, "UPS9998", "ord", "n123up", "a01", "HERE", ""]
            ],
            "inbound_alp": [
                [serial, "UPS1487", "dtw", "n152up", "b01", "ARR", "ignored raw"]
            ],
            "outbound_manual": [
                [serial, "UPS9329", "hot", "n445up", "a05", "HOT", ""]
            ],
            "outbound_alp": [
                [serial, "UPS7831", "sdf", "n303up", "e06", "", "ignored raw"]
            ],
        }
        formatted = {
            "sort_date": [["8/5/2026"]],
            "inbound_manual": [
                ["8/5/2026", "UPS9998", "ORD", "N123UP", "A01", "HERE", ""]
            ],
            "inbound_alp": [
                ["8/5/2026", "UPS1487", "DTW", "N152UP", "B01", "ARR", "03:09 (A)"]
            ],
            "inbound_official_order": [["UPS1487"], [""]],
            "outbound_manual": [
                ["8/5/2026", "UPS9329", "HOT", "N445UP", "A05", "HOT", ""]
            ],
            "outbound_alp": [
                ["8/5/2026", "UPS7831", "SDF", "N303UP", "E06", "", "06:15 (S)"]
            ],
            "outbound_official_order": [["UPS7831"]],
            "outbound_tail_swaps": [["UPS7831", "SDF", "N999UP", "ACK"]],
            "parking_assignments": [["N303UP", "E06-b"]],
        }
        return FakeSpreadsheet(raw, formatted)

    def test_reader_defaults_disabled_and_requires_credentials(self):
        disabled = google_motherbrain_reader_status(
            reader_config(GOOGLE_MOTHERBRAIN_READER_ENABLED=False)
        )
        missing = google_motherbrain_reader_status(
            reader_config(
                GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON=None,
                GOOGLE_SERVICE_ACCOUNT_JSON=None,
            )
        )

        self.assertFalse(disabled["enabled"])
        self.assertFalse(missing["credentials_configured"])
        with self.assertRaisesRegex(GoogleMotherBrainReaderError, "disabled"):
            read_google_motherbrain_envelope(
                reader_config(GOOGLE_MOTHERBRAIN_READER_ENABLED=False)
            )

    def test_invalid_credentials_report_only_safe_configuration_state(self):
        status = google_motherbrain_reader_status(
            reader_config(
                GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON='{"client_email":"secret@example.test"}'
            )
        )

        self.assertTrue(status["credentials_configured"])
        self.assertFalse(status["credentials_valid"])
        self.assertIsNone(status["service_account_email"])
        with self.assertRaisesRegex(GoogleMotherBrainReaderError, "invalid"):
            read_google_motherbrain_envelope(
                reader_config(
                    GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON="not-json"
                )
            )

    def test_dedicated_credentials_take_precedence_and_fallback_is_supported(self):
        dedicated = dict(SERVICE_ACCOUNT, client_email="dedicated@example.test")
        fallback = dict(SERVICE_ACCOUNT, client_email="fallback@example.test")
        status = google_motherbrain_reader_status(
            reader_config(
                GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON=json.dumps(dedicated),
                GOOGLE_SERVICE_ACCOUNT_JSON=json.dumps(fallback),
            )
        )
        fallback_status = google_motherbrain_reader_status(
            reader_config(
                GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON=None,
                GOOGLE_SERVICE_ACCOUNT_JSON=json.dumps(fallback),
            )
        )

        self.assertEqual(status["credential_source"], "dedicated")
        self.assertEqual(status["service_account_email"], "dedicated@example.test")
        self.assertEqual(fallback_status["credential_source"], "fallback")
        self.assertEqual(fallback_status["service_account_email"], "fallback@example.test")

    def test_reader_uses_locked_workbook_two_batch_reads_and_schema_v1_mapping(self):
        spreadsheet = self._spreadsheet()
        client = FakeClient(spreadsheet)

        envelope = read_google_motherbrain_envelope(
            reader_config(),
            client_factory=lambda _credentials: client,
            now=datetime(2026, 8, 5, 7, 15, tzinfo=timezone.utc),
        )

        expected_ranges = [spec[1] for spec in GOOGLE_MOTHERBRAIN_RANGE_SPECS]
        self.assertEqual(client.opened_ids, [GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID])
        self.assertEqual(len(spreadsheet.batch_calls), 2)
        self.assertEqual(spreadsheet.batch_calls[0][0], expected_ranges)
        self.assertEqual(spreadsheet.batch_calls[1][0], expected_ranges)
        self.assertEqual(
            spreadsheet.batch_calls[0][1],
            {
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "SERIAL_NUMBER",
                "majorDimension": "ROWS",
            },
        )
        self.assertEqual(
            spreadsheet.batch_calls[1][1]["valueRenderOption"],
            "FORMATTED_VALUE",
        )
        self.assertEqual(envelope["schema_version"], 1)
        self.assertEqual(envelope["sort_date"], "2026-08-05")
        self.assertEqual(envelope["gateway_code"], "RFD")
        self.assertEqual(envelope["sort_name"], "night")
        self.assertEqual(envelope["timezone"], "America/Chicago")
        self.assertEqual(
            envelope["snapshot"]["inbound"]["alp_rows"][0]["time"],
            "03:09 (A)",
        )
        self.assertEqual(
            envelope["snapshot"]["outbound"]["tail_swaps"][0]["new_tail"],
            "N999UP",
        )
        self.assertEqual(
            envelope["snapshot"]["parking"]["assignments"][0]["position"],
            "E06-b",
        )

    def test_reader_preserves_apps_script_row_filters_and_padding(self):
        spreadsheet = self._spreadsheet()
        spreadsheet.formatted_by_key["inbound_manual"] = [
            ["8/5/2026", "UPS1000", "ORD", "", "", "", ""],
            ["8/5/2026", "UPS1001", "ORD", "", "", "CNL", ""],
        ]
        spreadsheet.raw_by_key["inbound_manual"] = [
            [46239, "UPS1000"],
            [46239, "UPS1001"],
        ]
        spreadsheet.formatted_by_key["inbound_alp"] = [
            ["", "", "", "", "", "", ""],
            ["", "UPS1002", "ORD", "", "", "", ""],
        ]
        spreadsheet.raw_by_key["inbound_alp"] = [[], []]

        envelope = read_google_motherbrain_envelope(
            reader_config(), client_factory=lambda _credentials: FakeClient(spreadsheet)
        )
        inbound = envelope["snapshot"]["inbound"]

        self.assertEqual([row["flight_number"] for row in inbound["manual_rows"]], ["UPS1001"])
        self.assertEqual(inbound["manual_rows"][0]["sheet_row"], 5)
        self.assertEqual([row["flight_number"] for row in inbound["alp_rows"]], ["UPS1002"])
        self.assertEqual(inbound["alp_rows"][0]["sheet_row"], 17)

    def test_reader_rejects_wrong_locked_identity_tabs_and_ranges(self):
        cases = []
        wrong_title = self._spreadsheet()
        wrong_title.metadata["properties"]["title"] = "Wrong"
        cases.append((wrong_title, "title"))
        wrong_timezone = self._spreadsheet()
        wrong_timezone.metadata["properties"]["timeZone"] = "UTC"
        cases.append((wrong_timezone, "timezone"))
        missing_tab = self._spreadsheet()
        missing_tab.metadata["sheets"] = missing_tab.metadata["sheets"][:2]
        cases.append((missing_tab, "required"))

        for spreadsheet, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(GoogleMotherBrainReaderError, expected):
                    read_google_motherbrain_envelope(
                        reader_config(),
                        client_factory=lambda _credentials, sheet=spreadsheet: FakeClient(sheet),
                    )

        incomplete = self._spreadsheet()
        incomplete.values_batch_get = lambda _ranges, params=None: {"valueRanges": []}
        with self.assertRaisesRegex(GoogleMotherBrainReaderError, "required MotherBrain range"):
            read_google_motherbrain_envelope(
                reader_config(),
                client_factory=lambda _credentials: FakeClient(incomplete),
            )

    def test_reader_rejects_configured_workbook_other_than_locked_id(self):
        with self.assertRaisesRegex(GoogleMotherBrainReaderError, "locked"):
            read_google_motherbrain_envelope(
                reader_config(GOOGLE_MOTHERBRAIN_SPREADSHEET_ID="wrong")
            )

    def test_gspread_client_uses_only_readonly_scope(self):
        captured = {}
        fake_gspread = SimpleNamespace(
            service_account_from_dict=lambda credentials, scopes: captured.update(
                {"credentials": credentials, "scopes": scopes}
            )
            or object()
        )
        from app.services import google_motherbrain_sheets as service

        with patch.object(service, "gspread", fake_gspread):
            service._create_gspread_client(dict(SERVICE_ACCOUNT))

        self.assertEqual(captured["scopes"], GOOGLE_MOTHERBRAIN_READONLY_SCOPES)
        self.assertNotIn("drive", " ".join(captured["scopes"]).lower())

    def test_reader_source_contains_no_google_mutation_calls(self):
        from app.services import google_motherbrain_sheets as service

        source = inspect.getsource(service)
        for method in (
            "update",
            "update_acell",
            "batch_update",
            "append_row",
            "clear",
            "add_worksheet",
            "del_worksheet",
        ):
            self.assertNotIn(f".{method}(", source)

    def test_provider_access_and_timeout_errors_are_actionable_and_secret_safe(self):
        secret_fragment = SERVICE_ACCOUNT["private_key"]
        for error, expected in (
            (FakeGoogleError(403), "shared"),
            (TimeoutError("timed out"), "timed out"),
        ):
            client = SimpleNamespace(open_by_key=lambda _key, exc=error: (_ for _ in ()).throw(exc))
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(GoogleMotherBrainReaderError) as raised:
                    read_google_motherbrain_envelope(
                        reader_config(), client_factory=lambda _credentials, value=client: value
                    )
                self.assertIn(expected, raised.exception.message)
                self.assertNotIn(secret_fragment, raised.exception.message)


class GoogleMotherBrainSheetsRouteTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-reader-route-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                **reader_config(),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.user = User(username="Kessler", role="grandmaster")
        set_user_password(self.user, "TestPassword123!")
        db.session.add(self.user)
        db.session.flush()
        backfill_default_gateway_node_roles(self.user, role="grandmaster")
        self.gateway = Gateway.query.filter_by(code="RFD").one()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 5),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "TestPassword123!"},
        )
        self.envelope = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.endpoint = (
            f"/motherbrain/operations/{self.operation.id}"
            "/google-current-sort/preview"
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_operation_detail_status_does_not_call_google(self):
        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            side_effect=AssertionError("operation detail must not call Google"),
        ):
            response = self.client.get(
                f"/motherbrain/operations/{self.operation.id}"
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"READ GOOGLE CURRENT SORT", response.data)
        self.assertIn(b'data-google-reader-status="ready"', response.data)

    def test_operation_detail_reports_disabled_or_missing_credentials_without_button(self):
        cases = (
            (
                {"GOOGLE_MOTHERBRAIN_READER_ENABLED": False},
                b'data-google-reader-status="disabled"',
            ),
            (
                {
                    "GOOGLE_MOTHERBRAIN_READER_ENABLED": True,
                    "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": None,
                    "GOOGLE_SERVICE_ACCOUNT_JSON": None,
                },
                b'data-google-reader-status="missing-credentials"',
            ),
        )
        original = {
            key: self.app.config.get(key)
            for key in (
                "GOOGLE_MOTHERBRAIN_READER_ENABLED",
                "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON",
                "GOOGLE_SERVICE_ACCOUNT_JSON",
            )
        }
        for config, marker in cases:
            with self.subTest(marker=marker):
                self.app.config.update(original)
                self.app.config.update(config)
                response = self.client.get(
                    f"/motherbrain/operations/{self.operation.id}"
                )
                reader_panel = response.data.split(
                    b'data-google-current-sort-reader', 1
                )[1]
                self.assertIn(marker, reader_panel)
                self.assertNotIn(b"READ GOOGLE CURRENT SORT</button>", reader_panel)
        self.app.config.update(original)

    def test_successful_preview_renders_all_sections_and_changes_no_neo_data(self):
        mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            mission_type="arrival",
            mission_source="manual",
            flight_number="UPS1487",
            origin="DTW",
            destination="RFD",
            planned_datetime_local=datetime(2026, 8, 5, 23, 9),
            planned_datetime_utc=datetime(2026, 8, 6, 4, 9),
        )
        db.session.add(mission)
        db.session.commit()
        before = self._database_snapshot()
        envelope = deepcopy(self.envelope)
        envelope["snapshot"]["inbound"]["alp_rows"] = [
            {
                "sheet_row": 16,
                "date": "2026-08-05",
                "flight_number": "UPS1487",
                "origin": "DTW",
                "tail_number": "N152UP",
                "parking": "B01",
                "status": "ARR",
                "time": "03:09 (A)",
            }
        ]

        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            return_value=envelope,
        ):
            response = self.client.post(self.endpoint)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn(b"PREVIEW ONLY", response.data)
        self.assertIn(b"NO NEO DATA WAS CHANGED", response.data)
        self.assertIn(b'data-preview-section="inbound"', response.data)
        self.assertIn(b'data-preview-section="outbound"', response.data)
        self.assertIn(b'data-preview-section="tail-swaps"', response.data)
        self.assertIn(b'data-preview-section="parking"', response.data)
        self.assertIn(b"READ GOOGLE AGAIN", response.data)
        self.assertNotIn(b">APPLY<", response.data)
        self.assertEqual(self._database_snapshot(), before)

    def test_selected_operation_mismatch_is_safe_and_does_not_mutate(self):
        envelope = deepcopy(self.envelope)
        envelope["sort_date"] = "2026-08-06"
        before = self._database_snapshot()

        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            return_value=envelope,
        ):
            response = self.client.post(self.endpoint, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"The Google workbook sort date does not match the selected Neo operation.",
            response.data,
        )
        self.assertEqual(self._database_snapshot(), before)

    def test_preview_validation_failure_does_not_mutate(self):
        envelope = deepcopy(self.envelope)
        envelope["timezone"] = "UTC"
        before = self._database_snapshot()

        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            return_value=envelope,
        ):
            response = self.client.post(self.endpoint, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"timezone", response.data.lower())
        self.assertEqual(self._database_snapshot(), before)

    def test_google_and_validation_failures_are_safe_and_do_not_mutate(self):
        before = self._database_snapshot()
        failures = (
            GoogleMotherBrainReaderError(
                "spreadsheet_access_denied",
                "The locked Google workbook was not found or is not shared with the configured service account as Viewer.",
            ),
            GoogleMotherBrainReaderError(
                "google_timeout",
                "Google Sheets did not respond before the request timed out.",
            ),
        )
        for error in failures:
            with self.subTest(code=error.code), patch(
                "app.neomotherbrain.routes.read_google_motherbrain_envelope",
                side_effect=error,
            ):
                response = self.client.post(self.endpoint, follow_redirects=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(error.message.encode(), response.data)
                self.assertNotIn(SERVICE_ACCOUNT["private_key"].encode(), response.data)
                self.assertEqual(self._database_snapshot(), before)

    def test_route_requires_login_and_manage_sort_edit_permission(self):
        with patch("app.neomotherbrain.routes.user_can", return_value=False):
            denied = self.client.post(self.endpoint, follow_redirects=False)
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.headers["Location"], "/rfd")

        anonymous = self.app.test_client().post(self.endpoint)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login", anonymous.headers["Location"])

    def test_route_uses_normal_csrf_protection(self):
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get(f"/motherbrain/operations/{self.operation.id}")
        token_match = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            page.data,
        )
        self.assertIsNotNone(token_match)
        missing = self.client.post(self.endpoint)
        self.assertEqual(missing.status_code, 400)

        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            return_value=deepcopy(self.envelope),
        ):
            accepted = self.client.post(
                self.endpoint,
                data={"csrf_token": token_match.group(1).decode()},
            )
        self.assertEqual(accepted.status_code, 200)

    def _database_snapshot(self):
        return {
            "operations": SortDateOperation.query.count(),
            "missions": SortDateMission.query.count(),
            "mission_values": [
                (
                    mission.id,
                    mission.flight_number,
                    mission.assigned_tail_number,
                    mission.arrival_status,
                    mission.departure_status,
                )
                for mission in SortDateMission.query.order_by(SortDateMission.id).all()
            ],
        }


if __name__ == "__main__":
    unittest.main()
