from datetime import date, datetime
import json
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MotherBrainGoogleIntegrationSetting,
    PermissionRule,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_motherbrain_live_polling import (
    ensure_google_motherbrain_live_polling_setting,
    google_motherbrain_live_polling_enabled,
    set_google_motherbrain_live_polling_enabled,
)
from app.services.google_motherbrain_sheets import (
    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class GoogleMotherBrainLivePollingTest(unittest.TestCase):
    def setUp(self):
        service_account = {
            "client_email": "motherbrain-reader@example.test",
            "private_key": "test-private-key",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-live-polling-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "GOOGLE_MOTHERBRAIN_READER_ENABLED": True,
                "GOOGLE_MOTHERBRAIN_SERVICE_ACCOUNT_JSON": json.dumps(
                    service_account
                ),
                "GOOGLE_MOTHERBRAIN_SPREADSHEET_ID": (
                    GOOGLE_MOTHERBRAIN_LOCKED_SPREADSHEET_ID
                ),
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
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 5),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

        self.client = self.app.test_client()
        self.detail_endpoint = f"/motherbrain/operations/{self.operation.id}"
        self.toggle_endpoint = f"{self.detail_endpoint}/google-live-polling"
        self.system_settings_endpoint = "/motherbrain/system-settings"

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_default_state_is_off_and_permission_defaults_to_grandmaster(self):
        rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.google_live_polling.edit"
        ).one()

        self.assertFalse(
            google_motherbrain_live_polling_enabled(self.gateway, "night")
        )
        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 0)
        self.assertEqual(rule.minimum_role, "grandmaster")

    def test_ensure_and_set_persist_gateway_sort_state(self):
        setting = ensure_google_motherbrain_live_polling_setting(self.gateway)
        self.assertFalse(setting.live_polling_enabled)
        set_google_motherbrain_live_polling_enabled(
            self.gateway,
            "night",
            True,
        )
        db.session.commit()
        db.session.remove()

        gateway = Gateway.query.filter_by(code="RFD").one()
        self.assertTrue(google_motherbrain_live_polling_enabled(gateway, "night"))
        self.assertFalse(google_motherbrain_live_polling_enabled(gateway, "day"))
        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 1)

    def test_only_grandmaster_can_turn_polling_on_and_off_from_system_settings(self):
        for role in ("operator", "simulator", "master"):
            with self.subTest(role=role):
                self._login_role(role)
                denied = self.client.post(
                    self.system_settings_endpoint,
                    data={"action": "enable_google_live_polling"},
                )
                self.assertEqual(denied.status_code, 403)
                self.assertFalse(
                    google_motherbrain_live_polling_enabled(self.gateway, "night")
                )

        self._login_role("grandmaster")
        enabled = self.client.post(
            self.system_settings_endpoint,
            data={"action": "enable_google_live_polling"},
            follow_redirects=True,
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertIn(b"Live Google Polling is now ON.", enabled.data)
        self.assertIn(b'data-google-live-polling-state="on"', enabled.data)
        self.assertTrue(google_motherbrain_live_polling_enabled(self.gateway, "night"))

        disabled = self.client.post(
            self.system_settings_endpoint,
            data={"action": "disable_google_live_polling"},
            follow_redirects=True,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertIn(b"Live Google Polling is now OFF.", disabled.data)
        self.assertFalse(google_motherbrain_live_polling_enabled(self.gateway, "night"))

    def test_watcher_and_operator_cannot_change_state_by_direct_post(self):
        ensure_google_motherbrain_live_polling_setting(self.gateway)
        db.session.commit()

        for role in ("watcher", "operator", "simulator", "master"):
            with self.subTest(role=role):
                self._login_role(role)
                response = self.client.post(
                    self.toggle_endpoint,
                    data={"action": "enable"},
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 302)
                self.assertFalse(
                    google_motherbrain_live_polling_enabled(
                        self.gateway,
                        "night",
                    )
                )

    def test_system_settings_is_viewable_and_operator_is_view_only(self):
        self._login_role("operator")

        response = self.client.get(self.system_settings_endpoint)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"GOOGLE LIVE POLLING", response.data)
        self.assertIn(b'data-google-live-polling-state="off"', response.data)
        self.assertIn(b"VIEW ONLY", response.data)
        self.assertNotIn(b"ENABLE LIVE POLLING</button>", response.data)

        detail = self.client.get(self.detail_endpoint)
        self.assertNotIn(b"data-google-live-polling-control", detail.data)

    def test_manual_preview_remains_available_while_live_polling_is_off(self):
        self._login_role("simulator")

        response = self.client.get(self.detail_endpoint)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"READ GOOGLE CURRENT SORT", response.data)

    def test_toggle_does_not_call_google_or_modify_mission_or_parking_data(self):
        user = self._login_role("grandmaster")
        mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            mission_type="arrival",
            mission_source="manual",
            flight_number="UPS1000",
            origin="SDF",
            destination="RFD",
            planned_datetime_local=datetime(2026, 8, 5, 23, 0),
            planned_datetime_utc=datetime(2026, 8, 6, 4, 0),
            arrival_status="scheduled",
        )
        parking = SortDateParkingAssignment(
            sort_date_operation_id=self.operation.id,
            tail_number="N100UP",
            ramp_code="A",
            position_code="A01",
            lane_number=1,
            assigned_by_user_id=user.id,
        )
        db.session.add_all((mission, parking))
        db.session.commit()
        before = self._operational_snapshot()

        with patch(
            "app.neomotherbrain.routes.read_google_motherbrain_envelope",
            side_effect=AssertionError("toggle must not read Google"),
        ) as google_read:
            enable_response = self.client.post(
                self.system_settings_endpoint,
                data={"action": "enable_google_live_polling"},
            )
            disable_response = self.client.post(
                self.system_settings_endpoint,
                data={"action": "disable_google_live_polling"},
            )

        self.assertEqual(enable_response.status_code, 302)
        self.assertEqual(disable_response.status_code, 302)
        google_read.assert_not_called()
        self.assertEqual(self._operational_snapshot(), before)

    def _login_role(self, role):
        self.client.post("/logout")
        username = f"google_poll_{role}"
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(username=username, role=role)
            set_user_password(user, "TestPassword123!")
            db.session.add(user)
            db.session.flush()
            backfill_default_gateway_node_roles(user, role=role)
            db.session.commit()
        self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )
        return user

    def _operational_snapshot(self):
        return {
            "missions": [
                (
                    mission.id,
                    mission.flight_number,
                    mission.arrival_status,
                    mission.assigned_tail_number,
                )
                for mission in SortDateMission.query.order_by(
                    SortDateMission.id
                ).all()
            ],
            "parking": [
                (
                    assignment.id,
                    assignment.tail_number,
                    assignment.ramp_code,
                    assignment.position_code,
                    assignment.lane_number,
                )
                for assignment in SortDateParkingAssignment.query.order_by(
                    SortDateParkingAssignment.id
                ).all()
            ],
        }


if __name__ == "__main__":
    unittest.main()
