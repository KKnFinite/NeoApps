from datetime import date, datetime, time, timedelta
import json
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    Gateway,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.password_policy import set_user_password


class MotherBrainLiveCollaborationTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026,
                    8,
                    10,
                    21,
                    0,
                ),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.gateway = Gateway.query.filter_by(code="RFD").first()
        if self.gateway is None:
            self.gateway = Gateway(code="RFD", name="Rockford")
            db.session.add(self.gateway)
            db.session.flush()
        user = User(username="live-planner", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        self.user = user

        settings = SortTimelineSettings.query.filter_by(
            gateway_id=self.gateway.id
        ).first()
        if settings is None:
            settings = SortTimelineSettings(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
            )
            db.session.add(settings)
            db.session.flush()
        sort_setting = SortTimelineSortSetting.query.filter_by(
            gateway_id=self.gateway.id,
            sort_name="night",
        ).first()
        if sort_setting is None:
            sort_setting = SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
            )
            db.session.add(sort_setting)
        sort_setting.sort_window_start_local = time(14, 0)
        sort_setting.sort_window_end_local = time(5, 0)
        sort_setting.ops_window_start_local = time(20, 0)
        sort_setting.ops_window_end_local = time(3, 0)

        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 10),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_arrival_and_departure_pages_use_shared_selective_live_collaboration(self):
        for mission_type in ("arrival", "departure"):
            with self.subTest(mission_type=mission_type):
                response = self.client.get(
                    f"/motherbrain/operations/{self.operation.id}/alp/{mission_type}"
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-planning-live-root", response.data)
                self.assertIn(b"data-planning-live-collaboration", response.data)
                self.assertIn(b"data-live-rows=\"review\"", response.data)
                self.assertIn(b"data-live-rows=\"missions\"", response.data)
                self.assertIn(b"window.NeoLiveUpdates.reconcileRows", response.data)
                self.assertIn(b"intervalMs: 5000", response.data)
                self.assertNotIn(b"window.location.reload()", response.data)

    def test_state_payload_has_stable_identity_version_and_remote_values(self):
        mission = self._mission("arrival", "UPS0910", origin="SDF")
        db.session.add(mission)
        db.session.commit()

        first = self.client.get(self._state_url("arrival"))
        first_row = first.get_json()["rows"]["missions"][0]
        first_version = first_row["version"]

        mission.assigned_tail_number = "N910UP"
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()
        second = self.client.get(self._state_url("arrival"))
        second_payload = second.get_json()
        second_row = second_payload["rows"]["missions"][0]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first_row["id"], f"mission:{mission.id}")
        self.assertEqual(first_row["entity_id"], mission.id)
        self.assertNotEqual(second_row["version"], first_version)
        self.assertEqual(second_row["tail_number"], "N910UP")
        self.assertIn(f'data-live-id="mission:{mission.id}"', second_payload["fragments"]["missions"])

    def test_historical_planning_state_is_not_live(self):
        historical = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 8),
            sort_name="night",
        )
        db.session.add(historical)
        db.session.commit()

        response = self.client.get(
            f"/motherbrain/operations/{historical.id}/planning/arrival/state"
        )
        refresh = response.get_json()["refresh"]

        self.assertEqual(response.status_code, 200)
        self.assertFalse(refresh["auto_refresh_enabled"])
        self.assertEqual(refresh["reason"], "historical_sort")

    def test_planning_live_state_skips_global_lifecycle_maintenance(self):
        with (
            patch(
                "app.services.operation_lifecycle.ensure_operational_sort_operations"
            ) as lifecycle,
            patch(
                "app.services.unmatched_review_alerts.expire_unmatched_review_alerts"
            ) as alert_expiration,
        ):
            response = self.client.get(self._state_url("arrival"))

        self.assertEqual(response.status_code, 200)
        lifecycle.assert_not_called()
        alert_expiration.assert_not_called()

    def test_normal_planning_page_keeps_global_lifecycle_maintenance(self):
        with (
            patch(
                "app.services.operation_lifecycle.ensure_operational_sort_operations"
            ) as lifecycle,
            patch(
                "app.services.unmatched_review_alerts.expire_unmatched_review_alerts",
                return_value=False,
            ) as alert_expiration,
        ):
            response = self.client.get(
                f"/motherbrain/operations/{self.operation.id}/alp/arrival"
            )

        self.assertEqual(response.status_code, 200)
        lifecycle.assert_called_once_with(self.gateway)
        alert_expiration.assert_called_once_with(self.gateway)

    def test_stale_mission_save_returns_structured_field_conflict(self):
        mission = self._mission(
            "departure",
            "UPS0856",
            destination="DFW",
            planned_datetime_local=datetime(2026, 8, 10, 23, 42),
        )
        db.session.add(mission)
        db.session.commit()
        expected_version = entity_version(mission)
        original_values = self._original_mission_values(mission)

        mission.planned_datetime_local = datetime(2026, 8, 10, 23, 47)
        mission.planned_datetime_utc = datetime(2026, 8, 11, 4, 47)
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()

        payload = self._mission_form_payload(
            mission,
            planned_time_local="23:50",
            expected_version=expected_version,
            original_values=json.dumps(original_values),
        )
        response = self.client.post(
            self._edit_url(mission),
            data=payload,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        conflict = response.get_json()["conflict"]
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(conflict["type"], "stale_version")
        self.assertEqual(conflict["fields"][0]["field"], "planned_time_local")
        self.assertEqual(conflict["fields"][0]["original"], "23:42")
        self.assertEqual(conflict["fields"][0]["current"], "23:47")
        self.assertEqual(conflict["fields"][0]["submitted"], "23:50")
        self.assertIn("changed from 23:42 to 23:47", conflict["message"])
        self.assertEqual(mission.planned_datetime_local.time(), time(23, 47))

    def test_authorized_overwrite_can_resolve_stale_edit(self):
        mission = self._mission("arrival", "UPS0910", origin="SDF")
        db.session.add(mission)
        db.session.commit()
        expected_version = entity_version(mission)
        original_values = self._original_mission_values(mission)
        mission.assigned_tail_number = "NREMOTE"
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()

        response = self.client.post(
            self._edit_url(mission),
            data=self._mission_form_payload(
                mission,
                assigned_tail_number="NMINE",
                expected_version=expected_version,
                original_values=json.dumps(original_values),
                force_overwrite="1",
            ),
            follow_redirects=False,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.assigned_tail_number, "NMINE")

    def test_force_overwrite_cannot_bypass_existing_edit_permission(self):
        mission = self._mission("arrival", "UPS0911", origin="SDF")
        db.session.add(mission)
        operator = User(username="live-viewer", role="operator")
        set_user_password(operator, "TestPassword123!")
        db.session.add(operator)
        db.session.flush()
        backfill_default_gateway_node_roles(operator, role="operator")
        db.session.commit()

        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"username": operator.username, "password": "TestPassword123!"},
        )
        response = self.client.post(
            self._edit_url(mission),
            data=self._mission_form_payload(
                mission,
                assigned_tail_number="NUNAUTHORIZED",
                expected_version=entity_version(mission),
                original_values=json.dumps(self._original_mission_values(mission)),
                force_overwrite="1",
            ),
            follow_redirects=False,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(mission.assigned_tail_number, "NUNAUTHORIZED")

    def test_resolved_review_item_action_returns_conflict_without_recreating(self):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type="arrival",
            review_key="api:arrival:ups0910",
            review_status="pending",
            flight_number="UPS0910",
            origin="SDF",
            destination="RFD",
            revised_time_utc=datetime(2026, 8, 11, 3, 0),
        )
        db.session.add(item)
        db.session.commit()
        expected_version = entity_version(item)
        item.review_status = "ignored"
        item.updated_at = item.updated_at + timedelta(seconds=1)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/planning/api/{item.id}/add",
            data={
                "mission_type": "arrival",
                "wave": "",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)
        self.assertEqual(item.review_status, "ignored")

    def test_deleted_review_item_action_returns_conflict_without_recreating(self):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type="arrival",
            review_key="api:arrival:ups0948",
            review_status="pending",
            flight_number="UPS0948",
            origin="SDF",
            destination="RFD",
            revised_time_utc=datetime(2026, 8, 11, 3, 15),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
        expected_version = entity_version(item)
        db.session.delete(item)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/planning/api/{item_id}/add",
            data={
                "mission_type": "arrival",
                "wave": "",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_deleted_mission_action_returns_conflict_without_recreating(self):
        mission = self._mission("arrival", "UPS0952", origin="SDF")
        db.session.add(mission)
        db.session.commit()
        mission_id = mission.id
        expected_version = entity_version(mission)
        db.session.delete(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/missions/{mission_id}/cancel",
            data={
                "mission_type": "arrival",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)

    def _state_url(self, mission_type):
        return (
            f"/motherbrain/operations/{self.operation.id}/planning/"
            f"{mission_type}/state"
        )

    def _edit_url(self, mission):
        return (
            f"/motherbrain/operations/{self.operation.id}/missions/"
            f"{mission.id}/edit"
        )

    def _mission(self, mission_type, flight_number, **overrides):
        values = {
            "sort_date_operation_id": self.operation.id,
            "sort_date": self.operation.sort_date,
            "gateway_code": self.gateway.code,
            "sort_name": self.operation.sort_name,
            "mission_type": mission_type,
            "mission_source": "manual",
            "wave": "1",
            "flight_number": flight_number,
            "origin": "RFD" if mission_type == "departure" else "SDF",
            "destination": "SDF" if mission_type == "departure" else "RFD",
            "timezone": "America/Chicago",
            "planned_datetime_local": datetime(2026, 8, 10, 23, 42),
            "planned_datetime_utc": datetime(2026, 8, 11, 4, 42),
            "planned_source": "manual",
            "eta_source": "unknown",
            "actual_block_in_source": "unknown",
            "actual_block_out_source": "unknown",
            "tail_source": "unknown",
            "fuel_status": "waiting",
            "arrival_status": "scheduled" if mission_type == "arrival" else None,
            "departure_status": "scheduled" if mission_type == "departure" else None,
        }
        values.update(overrides)
        return SortDateMission(**values)

    def _original_mission_values(self, mission):
        return {
            "mission_type": mission.mission_type,
            "wave": mission.wave,
            "flight_number": mission.flight_number,
            "origin": mission.origin,
            "destination": mission.destination,
            "assigned_tail_number": mission.assigned_tail_number or "",
            "planned_time_local": mission.planned_datetime_local.strftime("%H:%M"),
            "timezone": mission.timezone,
            "eta_datetime_utc": "",
            "actual_block_in_datetime_utc": "",
            "actual_block_out_datetime_utc": "",
            "planned_fuel_load": "",
            "fuel_status": mission.fuel_status or "",
            "arrival_status": mission.arrival_status or "",
            "departure_status": mission.departure_status or "",
            "pure_pull_time_local": "",
            "mix_pull_time_local": "",
        }

    def _mission_form_payload(self, mission, **overrides):
        values = self._original_mission_values(mission)
        values.update(overrides)
        return values


if __name__ == "__main__":
    unittest.main()
