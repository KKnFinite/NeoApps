from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_rain_integration_mode import (
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    set_rain_integration_mode,
)
from app.services.google_rain_sheets import GoogleRainWriterError
from app.services.live_collaboration import entity_version
from app.services.password_policy import set_user_password


class NeoRainOutboundMutationEndpointTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainOutboundMutationEndpointTestConfig",
            (),
            {
                "SECRET_KEY": "neorain-outbound-endpoint-test-secret",
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
        self.operation = self._operation(date(2026, 6, 18))
        self.mission = self._mission(self.operation, "UPS0910")
        db.session.commit()
        self.client = self.app.test_client()
        self.simulator = self._user("rain_simulator", "simulator")
        self._login(self.simulator)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_google_primary_rejects_without_mutation_or_google_write(self):
        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.mutate_neorain_departure_milestone"
        ) as mutate, patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone"
        ) as writer:
            response = self._post("ramp_load_complete", "0237")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "google_primary")
        mutate.assert_not_called()
        writer.assert_not_called()
        self.assertIsNone(self.mission.ramp_load_completed_at_utc)

    def test_edit_permission_and_current_sort_membership_are_required(self):
        self.client.get("/logout")
        watcher = self._user("rain_watcher", "watcher")
        self._login(watcher)
        with self._current_operation():
            denied = self._post("ramp_load_complete", "0237")
        self.assertEqual(denied.status_code, 403)

        self.client.get("/logout")
        self._login(self.simulator)
        self._set_mode(NEO_ONLY)
        other_operation = self._operation(date(2026, 6, 19))
        other_mission = self._mission(other_operation, "UPS0999")
        db.session.commit()
        with self._current_operation():
            wrong_sort = self._post(
                "ramp_load_complete",
                "0237",
                mission_id=other_mission.id,
            )
        self.assertEqual(wrong_sort.status_code, 404)
        self.assertEqual(wrong_sort.get_json()["code"], "mission_not_found")

        with self._current_operation(None):
            no_sort = self._post("ramp_load_complete", "0237")
        self.assertEqual(no_sort.status_code, 409)
        self.assertEqual(no_sort.get_json()["code"], "no_current_sort")

    def test_phase_two_writes_canonical_value_then_commits_neo(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        expected_version = entity_version(self.mission)
        mirrored = []

        def capture_write(mission, field, value, *, operation):
            mirrored.append((field, value, operation.id))
            self.assertEqual(value, mission.ramp_load_completed_at_utc)
            return {"field": field}

        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone",
            side_effect=capture_write,
        ):
            response = self._post(
                "ramp_load_complete",
                "0237",
                expected_version=expected_version,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], NEO_PRIMARY_GOOGLE_MIRROR)
        self.assertEqual(payload["row"]["ramp_load_complete"], "02:37")
        self.assertEqual(payload["row"]["departure_status"], "ramp_load_complete")
        self.assertEqual(payload["version"], payload["row"]["version"])
        self.assertNotEqual(payload["version"], expected_version)
        self.assertEqual(len(mirrored), 1)
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNotNone(persisted.ramp_load_completed_at_utc)
        self.assertEqual(persisted.ramp_load_completed_source, "neorain")

    def test_phase_two_google_failure_rolls_back_neo(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone",
            side_effect=GoogleRainWriterError("google_failure", "safe failure"),
        ):
            response = self._post("ramp_load_complete", "0237")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "google_mirror_failed")
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNone(persisted.ramp_load_completed_at_utc)
        self.assertEqual(persisted.departure_status, "scheduled")

    def test_phase_three_commits_neo_without_google_calls(self):
        self._set_mode(NEO_ONLY)
        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone"
        ) as writer:
            response = self._post("crew_load_complete", "0237")

        self.assertEqual(response.status_code, 200)
        writer.assert_not_called()
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNotNone(persisted.crew_load_completed_at_utc)
        self.assertEqual(persisted.crew_load_completed_source, "neorain")

    def test_block_out_mutation_response_includes_derived_variance(self):
        self._set_mode(NEO_ONLY)
        self.mission.planned_datetime_utc = datetime(2026, 6, 19, 7, 30)
        db.session.commit()

        with self._current_operation():
            response = self._post("official_block_out", "0237")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["row"]["departure_variance"], "+7")

    def test_stale_version_returns_current_row_without_neo_or_google_changes(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        expected_version = entity_version(self.mission)
        self.mission.destination = "ONT"
        db.session.commit()

        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.mutate_neorain_departure_milestone"
        ) as mutate, patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone"
        ) as writer:
            response = self._post(
                "ramp_load_complete",
                "0237",
                expected_version=expected_version,
            )

        self.assertEqual(response.status_code, 409)
        payload = response.get_json()
        self.assertEqual(payload["code"], "stale_version")
        self.assertEqual(payload["row"]["destination"], "ONT")
        self.assertNotEqual(payload["row"]["version"], expected_version)
        mutate.assert_not_called()
        writer.assert_not_called()
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNone(persisted.ramp_load_completed_at_utc)

    def test_missing_expected_version_is_rejected(self):
        self._set_mode(NEO_ONLY)
        with self._current_operation():
            response = self.client.post(
                "/neorain/outbound/milestone",
                json={
                    "mission_id": self.mission.id,
                    "field": "ramp_load_complete",
                    "value": "0237",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_request")

    def test_service_validation_and_elmac_rejection_are_safe(self):
        self._set_mode(NEO_ONLY)
        with self._current_operation():
            invalid = self._post("official_block_out", "2560")
            elmac = self._post("elmac", "0237")

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "invalid_milestone")
        self.assertEqual(elmac.status_code, 400)
        self.assertEqual(elmac.get_json()["code"], "unsupported_field")
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNone(persisted.actual_block_out_datetime_utc)
        self.assertIsNone(persisted.elmac_completed_at_utc)

    def test_phase_two_commit_failure_attempts_google_compensation(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        mirrored_values = []

        def capture_write(_mission, _field, value, *, operation):
            mirrored_values.append(value)
            return {"operation_id": operation.id}

        with self._current_operation(), patch(
            "app.neonodes.neorain.routes.write_google_rain_departure_milestone",
            side_effect=capture_write,
        ), patch(
            "app.neonodes.neorain.routes.db.session.commit",
            side_effect=RuntimeError("commit failed"),
        ):
            response = self._post("ramp_load_complete", "0237")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["code"], "save_failed")
        self.assertEqual(len(mirrored_values), 2)
        self.assertIsNotNone(mirrored_values[0])
        self.assertIsNone(mirrored_values[1])
        db.session.expire_all()
        persisted = db.session.get(SortDateMission, self.mission.id)
        self.assertIsNone(persisted.ramp_load_completed_at_utc)

    def _post(self, field, value, *, mission_id=None, expected_version=None):
        target_id = mission_id or self.mission.id
        if expected_version is None:
            target = db.session.get(SortDateMission, target_id)
            expected_version = entity_version(target)
        return self.client.post(
            "/neorain/outbound/milestone",
            json={
                "mission_id": target_id,
                "field": field,
                "value": value,
                "expected_version": expected_version,
            },
        )

    def _set_mode(self, mode):
        set_rain_integration_mode(self.gateway, self.operation.sort_name, mode)
        db.session.commit()

    def _current_operation(self, operation=...):
        resolved = self.operation if operation is ... else operation
        return patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=resolved,
        )

    def _operation(self, sort_date):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(self, operation, flight_number):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            mission_type="departure",
            mission_source="master",
            flight_number=flight_number,
            origin="RFD",
            destination="LAX",
            timezone="America/Chicago",
            planned_datetime_local=datetime.combine(
                operation.sort_date, datetime.min.time()
            ),
            departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
