"""Contracts for independent, operator-owned NeoRain Outbound service fields."""
from datetime import date, datetime
from pathlib import Path
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import NeoRainOutboundServiceState, SortDateMission, SortDateOperation, User
from app.neonodes.neorain.services import neorain_outbound_context, neorain_outbound_revision
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules, get_permission_rule


class NeoRainOutboundServiceFieldsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "RainServiceFieldTestConfig", (),
            {
                "SECRET_KEY": "test",
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
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 10, 8),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self.departure = self._mission(self.operation, "departure", "UPS811")
        self.arrival = self._mission(self.operation, "arrival", "UPS812")
        db.session.commit()
        self.client = self.app.test_client()
        self.operator = self._user("rain_service_operator", "operator")
        self._login(self.operator)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _mission(self, operation, mission_type, number):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            mission_type=mission_type,
            mission_source="master",
            flight_number=number,
            origin="RFD",
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 10, 9, 1),
            departure_status="scheduled" if mission_type == "departure" else None,
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
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )

    def _post(self, field, value, *, version=0, mission_id=None, operation=...):
        resolved = self.operation if operation is ... else operation
        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=resolved,
        ):
            return self.client.post(
                "/neorain/outbound/service-field",
                json={
                    "mission_id": self.departure.id if mission_id is None else mission_id,
                    "field": field,
                    "value": value,
                    "expected_version": version,
                },
            )

    def test_operator_default_can_edit_while_google_primary_owns_milestones(self):
        self.assertEqual(
            get_permission_rule("neorain.outbound.service_fields.edit").minimum_role,
            "operator",
        )
        original_mission_version = self.departure.updated_at
        before = neorain_outbound_revision(self.gateway, operation=self.operation)
        meal = self._post("meal", True)
        self.assertEqual(meal.status_code, 200)
        self.assertEqual(meal.get_json()["service"]["service_version"], 1)
        self.assertTrue(meal.get_json()["service"]["meal"])
        self.assertNotEqual(meal.get_json()["revision"], before)

        jump = self._post("jump", "0", version=1)
        self.assertEqual(jump.status_code, 200)
        self.assertEqual(jump.get_json()["service"]["jump"], 0)
        self.assertEqual(jump.get_json()["service"]["service_version"], 2)

        js_in = self._post("js_in", "2359", version=2)
        self.assertEqual(js_in.status_code, 200)
        self.assertEqual(js_in.get_json()["service"]["js_in"], "23:59")
        self.assertEqual(js_in.get_json()["service"]["service_version"], 3)
        db.session.refresh(self.departure)
        self.assertEqual(self.departure.updated_at, original_mission_version)

        row = neorain_outbound_context(self.gateway, operation=self.operation)["rows"][0]
        self.assertTrue(row["meal"])
        self.assertEqual(row["jump"], 0)
        self.assertEqual(row["js_in"], "23:59")
        self.assertEqual(row["service_version"], 3)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=self.operation,
        ):
            response = self.client.get("/neorain/outbound")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        for field in ("meal", "jump", "js_in"):
            self.assertIn(f'data-neorain-service-field="{field}"', page)

    def test_js_in_midnight_and_clearing_and_jump_nine(self):
        saved = self._post("js_in", "00:00")
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["service"]["js_in"], "00:00")
        cleared = self._post("js_in", "", version=1)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json()["service"]["js_in"], "")
        jump = self._post("jump", "9", version=2)
        self.assertEqual(jump.status_code, 200)
        self.assertEqual(jump.get_json()["service"]["jump"], 9)

    def test_invalid_service_fields_never_create_state(self):
        invalid = (
            ("meal", "true"),
            ("meal", 1),
            ("jump", "10"),
            ("jump", -1),
            ("jump", True),
            ("jump", "1.0"),
            ("js_in", "24:00"),
            ("js_in", "13:60"),
            ("js_in", "1:30"),
            ("js_in", "12:345"),
            ("js_in", 1234),
            ("no_return", True),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                response = self._post(field, value)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(NeoRainOutboundServiceState.query.count(), 0)

    def test_conflict_and_scope_protection(self):
        initial = self._post("jump", "3")
        self.assertEqual(initial.status_code, 200)
        stale = self._post("meal", True, version=0)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.get_json()["code"], "stale_version")
        self.assertEqual(stale.get_json()["service"]["jump"], 3)
        self.assertFalse(stale.get_json()["service"]["meal"])

        arrival = self._post("meal", True, mission_id=self.arrival.id)
        self.assertEqual(arrival.status_code, 404)
        other_operation = SortDateOperation(
            gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_date=date(2026, 10, 9), sort_name="night",
        )
        db.session.add(other_operation)
        db.session.flush()
        other_mission = self._mission(other_operation, "departure", "UPS999")
        db.session.commit()
        foreign = self._post("jump", "2", mission_id=other_mission.id)
        self.assertEqual(foreign.status_code, 404)
        missing_sort = self._post("meal", True, operation=None)
        self.assertEqual(missing_sort.status_code, 409)

        # A no-op does not increment the service-only revision.
        noop = self._post("jump", "3", version=1)
        self.assertEqual(noop.status_code, 200)
        self.assertFalse(noop.get_json()["changed"])
        self.assertEqual(noop.get_json()["service"]["service_version"], 1)

    def test_watcher_remains_read_only(self):
        watcher = self._user("rain_service_watcher", "watcher")
        self.client.get("/logout")
        self._login(watcher)
        denied = self._post("meal", True)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(NeoRainOutboundServiceState.query.count(), 0)
        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=self.operation,
        ):
            board = self.client.get("/neorain/outbound")
        self.assertEqual(board.status_code, 200)
        html = board.get_data(as_text=True)
        self.assertIn('data-neorain-service-display="meal"', html)
        self.assertNotIn('data-neorain-service-field="meal"', html)

    def test_both_templates_include_desktop_columns_and_mobile_details(self):
        templates = Path(__file__).resolve().parents[1] / "app" / "templates" / "neonodes" / "neorain"
        desktop = (templates / "_outbound_content.html").read_text()
        mobile = (templates / "_outbound_mobile_content.html").read_text()
        page = (templates / "outbound.html").read_text()
        self.assertIn("<th>Meal</th><th>Jump</th><th>JS In</th>", desktop)
        self.assertIn('colspan="21"', desktop)
        self.assertIn("<dt>MEAL</dt>", mobile)
        self.assertIn("<dt>JUMP</dt>", mobile)
        self.assertIn("<dt>JS IN</dt>", mobile)
        self.assertIn("can_edit_service_fields", page)
        self.assertIn("neorain_outbound_service_fields.js", page)


if __name__ == "__main__":
    unittest.main()
