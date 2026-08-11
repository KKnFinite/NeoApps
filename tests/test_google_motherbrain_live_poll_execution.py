from datetime import date, datetime, time, timedelta
import re
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

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
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    user_can_access_node,
)
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
        self.sort_setting = SortTimelineSortSetting(
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
            google_polling_start_local=time(18, 0),
            google_polling_end_local=time(4, 0),
        )
        db.session.add(self.sort_setting)
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

    def test_google_polling_window_start_is_inclusive(self):
        self._enable()
        reader = Mock(return_value={"inbound_rows": [], "outbound_rows": []})

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=datetime(2026, 6, 18, 18, 0),
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        reader.assert_called_once_with()

    def test_google_polling_window_end_is_exclusive(self):
        self._enable()
        reader = Mock()

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=datetime(2026, 6, 19, 4, 0),
            reader=reader,
        )

        self.assertEqual(result["status"], "outside_window")
        reader.assert_not_called()
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 0)

    def test_missing_google_polling_bound_does_not_fall_back_to_api_window(self):
        self._enable()
        reader = Mock()

        for missing_field in (
            "google_polling_start_local",
            "google_polling_end_local",
        ):
            with self.subTest(missing_field=missing_field):
                self.sort_setting.google_polling_start_local = time(18, 0)
                self.sort_setting.google_polling_end_local = time(4, 0)
                setattr(self.sort_setting, missing_field, None)
                db.session.commit()

                result = execute_google_motherbrain_live_poll(
                    self.gateway,
                    now=self.NOW,
                    reader=reader,
                )

                self.assertEqual(result["status"], "outside_window")

        reader.assert_not_called()
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 0)

    def test_api_polling_window_cannot_make_google_polling_eligible(self):
        self._enable()
        self.sort_setting.polling_start_local = time(14, 0)
        self.sort_setting.polling_end_local = time(5, 0)
        self.sort_setting.google_polling_start_local = time(23, 0)
        self.sort_setting.google_polling_end_local = time(1, 0)
        db.session.commit()
        reader = Mock()

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )

        self.assertEqual(result["status"], "outside_window")
        reader.assert_not_called()

    def test_existing_operation_uses_google_window_outside_lifecycle_window(self):
        self._enable()
        self.sort_setting.sort_window_start_local = time(14, 0)
        self.sort_setting.sort_window_end_local = time(17, 0)
        self.sort_setting.google_polling_start_local = time(18, 0)
        self.sort_setting.google_polling_end_local = time(20, 0)
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 6, 18),
        )
        db.session.add(operation)
        db.session.commit()
        reader = Mock(return_value={"inbound_rows": [], "outbound_rows": []})

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=datetime(2026, 6, 18, 19, 0),
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["operation_id"], operation.id)
        reader.assert_called_once_with()

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

    def test_successful_poll_applies_formatted_google_datetime_rows(self):
        self._enable()
        reader = Mock(
            return_value={
                "inbound_rows": [
                    self._inbound(
                        4,
                        "947",
                        "N947UP",
                        origin="SDF",
                        status="DEP",
                        planned="6/18 22:20",
                        operational="6/19 0:04",
                    )
                ],
                "outbound_rows": [
                    self._outbound(
                        4,
                        "755",
                        "N755UP",
                        destination="SDF",
                        planned="6/18 23:20",
                        operational="6/19 3:38",
                    )
                ],
            }
        )

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=self.NOW,
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["applied_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(SortDateMission.query.count(), 2)

    def test_future_formatted_block_out_remains_loading_at_poll_time(self):
        self._enable()
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week="monday",
                sort_name="night",
                is_active=True,
            )
        )
        db.session.commit()
        poll_now = datetime(
            2026,
            8,
            10,
            22,
            10,
            tzinfo=ZoneInfo("America/Chicago"),
        )
        reader = Mock(
            return_value={
                "inbound_rows": [],
                "outbound_rows": [
                    self._outbound(
                        4,
                        "755",
                        "N755UP",
                        destination="SDF",
                        planned="8/11 02:25",
                        operational="8/11 02:39",
                    )
                ],
            }
        )

        result = execute_google_motherbrain_live_poll(
            self.gateway,
            now=poll_now,
            reader=reader,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["applied_count"], 1)
        mission = SortDateMission.query.filter_by(flight_number="UPS0755").one()
        self.assertEqual(mission.sort_date, date(2026, 8, 10))
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 8, 11, 2, 25))
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.departure_status, "loading")

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
                headers={"X-CSRF-Token": csrf_token},
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["status"], "success")
        self.assertNotEqual(accepted.get_json()["operation_id"], historical.id)

    def test_shared_heartbeat_renders_once_on_active_operational_pages_only(self):
        user = User(username="operational-heartbeat-render", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        db.session.commit()

        client = self.app.test_client()
        self._login(client, user)
        for path in (
            "/rfd",
            "/motherbrain",
            "/neoermac",
            "/neosektor",
            "/neoscorpion",
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.data.count(b"data-google-live-poll-heartbeat"),
                    1,
                )

        portal = client.get("/portal")
        self.assertEqual(portal.status_code, 200)
        self.assertNotIn(b"data-google-live-poll-heartbeat", portal.data)

    def test_gateway_node_users_can_trigger_without_motherbrain_operator_role(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = self.NOW
        user = User(username="operational-live-poll-user", role="watcher")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="watcher")
        db.session.commit()

        for node_code in ("ermac", "sektor", "scorpion"):
            with self.subTest(node_code=node_code):
                self.assertTrue(
                    user_can_access_node(user, self.gateway.code, node_code)
                )
                self.assertFalse(
                    user_can_access_node(
                        user,
                        self.gateway.code,
                        "motherbrain",
                        minimum_role="operator",
                    )
                )

        client = self.app.test_client()
        self._login(client, user)
        response = client.post("/motherbrain/google-live-poll/execute")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "disabled")

    def test_endpoint_rejects_unauthenticated_and_gateway_denied_requests(self):
        endpoint = "/motherbrain/google-live-poll/execute"
        self.assertEqual(self.app.test_client().post(endpoint).status_code, 401)

        user = User(username="no-gateway-live-poll-user", role="watcher")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, user)

        response = client.post(endpoint)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["status"], "access_denied")

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
    def _login(client, user):
        response = client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )
        if response.status_code != 302:
            raise AssertionError("Expected login to succeed.")

    @staticmethod
    def _inbound(
        row,
        flight,
        tail,
        *,
        origin,
        status="",
        planned="22:45",
        operational="",
    ):
        return {
            "source_sheet": "Inbound",
            "sheet_row": row,
            "P": flight,
            "Q": tail,
            "R": origin,
            "S": "",
            "T": planned,
            "U": operational,
            "W": status,
        }

    @staticmethod
    def _outbound(
        row,
        flight,
        tail,
        *,
        destination,
        planned="01:20",
        operational="",
    ):
        return {
            "source_sheet": "Outbound",
            "sheet_row": row,
            "P": flight,
            "Q": tail,
            "R": destination,
            "S": "",
            "T": planned,
            "U": operational,
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
