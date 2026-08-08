from datetime import date, datetime, time, timedelta
import re
import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    MotherBrainGoogleLivePollState,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_motherbrain_live_poll_execution import (
    execute_google_motherbrain_live_poll,
)
from app.services.google_motherbrain_live_poll_lease import (
    acquire_google_motherbrain_live_poll_lease,
    complete_google_motherbrain_live_poll_success,
)
from app.services.google_motherbrain_live_polling import (
    set_google_motherbrain_live_polling_enabled,
)
from app.services.operation_lifecycle import ensure_operational_sort_operations
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class GoogleMotherBrainLivePollExecutionTest(unittest.TestCase):
    NOW = datetime(2026, 6, 18, 22, 30)

    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-live-poll-execution-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(self.settings)
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week="thursday",
                sort_name="night",
                is_active=True,
            )
        )
        db.session.add(
            SortTimelineSortSetting(
                timeline_settings=self.settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                sort_window_start_local=time(14, 0),
                sort_window_end_local=time(5, 0),
                ops_window_start_local=time(20, 0),
                ops_window_end_local=time(3, 0),
                polling_start_local=time(18, 0),
                polling_end_local=time(4, 0),
            )
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_outside_polling_window_does_not_read_or_lease(self):
        reader = Mock()

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=datetime(2026, 6, 18, 15, 0),
            reader=reader,
        )

        self.assertEqual(result["status"], "outside_window")
        reader.assert_not_called()
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 0)

    def test_disabled_polling_does_not_read_or_create_lease_state(self):
        reader = Mock()

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )

        self.assertEqual(result["status"], "disabled")
        reader.assert_not_called()
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 0)

    def test_successful_poll_applies_live_rows_then_marks_lease_success(self):
        self._enable()
        reader = Mock(
            return_value={
                "inbound_rows": [
                    self._inbound(4, "947", "N947UP", origin="SDF", status="DEP")
                ],
                "outbound_rows": [],
            }
        )

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["applied_count"], 1)
        reader.assert_called_once_with()
        operation = db.session.get(SortDateOperation, result["operation_id"])
        self.assertEqual(operation.sort_date, date(2026, 6, 18))
        self.assertEqual(SortDateMission.query.count(), 1)
        state = self._state(operation)
        self.assertEqual(state.last_success_at_utc, self.NOW)
        self.assertEqual(state.lease_token, "")
        self.assertIsNone(state.last_error)

    def test_cross_midnight_poll_uses_previous_operational_sort_date(self):
        self._enable()
        reader = Mock(return_value={"inbound_rows": [], "outbound_rows": []})

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=datetime(2026, 6, 19, 0, 30),
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(
            db.session.get(SortDateOperation, result["operation_id"]).sort_date,
            date(2026, 6, 18),
        )

    def test_lease_not_due_and_in_progress_are_propagated_without_google_read(self):
        self._enable()
        operation = self._ensure_operation()
        active = acquire_google_motherbrain_live_poll_lease(operation, now=self.NOW)
        reader = Mock()

        in_progress = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )
        self.assertEqual(in_progress["status"], "in_progress")
        reader.assert_not_called()

        self.assertTrue(complete_google_motherbrain_live_poll_success(active.lease, self.NOW))
        not_due = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW + timedelta(seconds=10),
            reader=reader,
        )
        self.assertEqual(not_due["status"], "not_due")
        reader.assert_not_called()

    def test_fetch_or_application_failure_rolls_back_and_marks_failure(self):
        self._enable()

        def failing_applier(*_args, **_kwargs):
            db.session.add(
                SortDateMission(
                    sort_date=date(2026, 6, 18),
                    gateway_code="RFD",
                    sort_name="night",
                    mission_type="arrival",
                    mission_source="google_motherbrain",
                    flight_number="ROLLBACK",
                )
            )
            raise RuntimeError("application failed")

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=lambda: {"inbound_rows": [], "outbound_rows": []},
            applier=failing_applier,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(SortDateMission.query.filter_by(flight_number="ROLLBACK").count(), 0)
        state = self._state(db.session.get(SortDateOperation, result["operation_id"]))
        self.assertEqual(state.last_error, "RuntimeError")
        self.assertIsNone(state.last_success_at_utc)
        self.assertEqual(state.lease_token, "")

    def test_reader_failure_records_safe_failure_without_creating_missions(self):
        self._enable()

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=Mock(side_effect=RuntimeError("reader failed")),
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(SortDateMission.query.count(), 0)
        state = self._state(db.session.get(SortDateOperation, result["operation_id"]))
        self.assertEqual(state.last_error, "RuntimeError")
        self.assertEqual(state.lease_token, "")

    def test_malformed_row_and_outbound_canx_do_not_block_valid_work(self):
        self._enable()
        reader = Mock(
            return_value={
                "inbound_rows": [
                    self._inbound(4, "", "NINVALID", origin="SDF"),
                    self._inbound(5, "948", "N948UP", origin="ONT"),
                ],
                "outbound_rows": [
                    self._outbound(4, "1000", "N100UP", destination="CANX")
                ],
            }
        )

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(SortDateMission.query.count(), 1)
        self.assertEqual(SortDateMission.query.one().flight_number, "UPS0948")

    def test_endpoint_uses_server_resolved_current_operation_and_csrf(self):
        self._enable()
        self.app.config["CSRF_PROTECT_TESTING"] = True
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = self.NOW
        user = User(username="live-poll-executor", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        historical = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(historical)
        db.session.commit()
        client = self.app.test_client()
        login_page = client.get("/login")
        token = self._csrf_token(login_page)
        client.post(
            "/login",
            data={
                "username": user.username,
                "password": "TestPassword123!",
                "csrf_token": token,
            },
        )
        page = client.get("/rfd")
        csrf_token = self._csrf_token(page)

        missing = client.post("/motherbrain/google-live-poll/execute")
        with patch(
            "app.services.google_motherbrain_live_poll_execution.read_google_motherbrain_live_rows",
            return_value={"inbound_rows": [], "outbound_rows": []},
        ):
            accepted = client.post(
                "/motherbrain/google-live-poll/execute",
                data={
                    "csrf_token": csrf_token,
                    "operation_id": historical.id,
                    "sort_date": historical.sort_date.isoformat(),
                },
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["status"], "success")
        self.assertNotEqual(accepted.get_json()["operation_id"], historical.id)

    def _enable(self):
        set_google_motherbrain_live_polling_enabled(self.gateway, "night", True)
        db.session.commit()

    def _ensure_operation(self):
        return ensure_operational_sort_operations(self.gateway, now=self.NOW)["eligible"][0][
            "operation"
        ]

    def _state(self, operation):
        return MotherBrainGoogleLivePollState.query.filter_by(
            gateway_id=self.gateway.id,
            sort_name="night",
            sort_date=operation.sort_date,
        ).one()

    @staticmethod
    def _inbound(row, flight, tail, *, origin, status=""):
        return {
            "source_sheet": "Inbound",
            "sheet_row": row,
            "P": flight,
            "Q": tail,
            "R": origin,
            "S": "",
            "T": "22:45",
            "U": "",
            "W": status,
        }

    @staticmethod
    def _outbound(row, flight, tail, *, destination):
        return {
            "source_sheet": "Outbound",
            "sheet_row": row,
            "P": flight,
            "Q": tail,
            "R": destination,
            "S": "",
            "T": "01:20",
            "U": "",
            "W": "",
            "X": "",
            "Y": "",
            "Z": "",
        }

    @staticmethod
    def _csrf_token(response):
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if match is None:
            raise AssertionError("Expected CSRF token in response.")
        return match.group(1).decode()


if __name__ == "__main__":
    unittest.main()
