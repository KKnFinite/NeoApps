import unittest
from datetime import date, datetime, time
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewaySortMatrix,
    GatewayNodeRole,
    LiveScreenRefreshSetting,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoScorpionLiveAssignmentsTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_revision_endpoint_authorization_and_no_current_sort(self):
        operator = self._add_user("fueler_operator", "operator")
        watcher = self._add_user("fueler_watcher", "watcher")
        db.session.commit()

        self._login(operator)
        response = self.client.get("/neoscorpion/fuel-assignments/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "current_operation": False,
                "operation_id": None,
                "revision": 0,
            },
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        self._login(watcher)
        response = self.client.get("/neoscorpion/fuel-assignments/revision")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["ok"], False)

    def test_revision_endpoint_is_one_fingerprint_query_and_never_writes(self):
        operator = self._add_user("query_operator", "operator")
        self._configure_active_night_sort()
        operation, _mission = self._add_operation_with_mission()
        db.session.commit()
        self._login(operator)

        statements = []

        def capture_statement(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip())

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
                response = self.client.get(
                    "/neoscorpion/fuel-assignments/revision"
                )
                self.assertEqual(commit.call_count, 0)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation_id"], operation.id)
        self.assertEqual(response.get_json()["revision"], 0)
        self.assertIsNone(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).first()
        )
        fingerprint_queries = [
            statement
            for statement in statements
            if "neoscorpion_sort_asset_states" in statement.lower()
        ]
        self.assertEqual(len(fingerprint_queries), 1)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE")
                )
                for statement in statements
            )
        )

    def test_fuel_dispatch_revision_authorization_and_no_current_sort(self):
        operator = self._add_user("dispatch_operator", "operator")
        watcher = self._add_user("dispatch_watcher", "watcher")
        db.session.commit()

        self._login(operator)
        response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "current_operation": False,
                "operation_id": None,
                "revision": 0,
            },
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        self._login(watcher)
        response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "Access denied.")

    def test_fuel_dispatch_revision_active_operation_is_read_only_and_bounded(self):
        operator = self._add_user("dispatch_query_operator", "operator")
        self._configure_active_night_sort()
        operation, _mission = self._add_operation_with_mission()
        db.session.commit()
        self._login(operator)
        statements = []

        def capture_statement(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip())

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            response = self.client.get("/neoscorpion/fuel-dispatch/revision")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["operation_id"], operation.id)
        self.assertEqual(response.get_json()["revision"], 0)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE")
                )
                for statement in statements
            )
        )
        self.assertLessEqual(
            sum(
                statement.lstrip().upper().startswith("SELECT")
                for statement in statements
            ),
            8,
        )

    def test_revision_and_assignment_identifiers_render_for_current_fueler(self):
        operator = self._add_user("assigned_operator", "operator")
        self._configure_active_night_sort()
        operation, mission = self._add_operation_with_mission()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=operator.id,
        )
        db.session.add_all(
            [
                assignment,
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation.id,
                    revision=8,
                ),
                LiveScreenRefreshSetting(
                    gateway_id=self.gateway.id,
                    screen_key="neoscorpion.fuel_assignments",
                    interval_seconds=15,
                ),
            ]
        )
        db.session.commit()
        self._login(operator)

        response = self.client.get("/neoscorpion/fueler")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(f'data-operation-id="{operation.id}"', body)
        self.assertIn('data-revision="8"', body)
        self.assertIn(f'data-current-user-id="{operator.id}"', body)
        self.assertIn('data-refresh-interval-ms="15000"', body)
        self.assertIn('data-refresh-source="override"', body)
        self.assertIn(f'data-fuel-assignment-id="{assignment.id}"', body)
        self.assertIn("NEW ASSIGNMENT", body)
        self.assertIn("neoscorpion_fuel_assignments_live.js", body)
        self.assertIn(mission.flight_number, body)
        self.assertNotIn("KEEP LIVE / MONITOR MODE", body)

        revision = self.client.get(
            "/neoscorpion/fuel-assignments/revision"
        ).get_json()
        self.assertEqual(revision["operation_id"], operation.id)
        self.assertEqual(revision["revision"], 8)

    def test_live_script_uses_effective_visible_polling_and_session_alert_state(self):
        with open(
            "app/static/js/neoscorpion_fuel_assignments_live.js",
            encoding="utf-8",
        ) as source:
            script = source.read()

        self.assertIn("root.dataset.refreshIntervalMs", script)
        self.assertIn("continuousWhileVisible: true", script)
        self.assertNotIn("setMonitorMode", script)
        self.assertIn("sessionStorage", script)
        self.assertIn("data-new-assignment-marker", script)
        self.assertIn("AudioContext", script)
        self.assertIn('window.addEventListener("pagehide"', script)
        self.assertIn("window.location.reload()", script)

    def _add_operation_with_mission(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 17),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS500",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            assigned_tail_number="N500UP",
            tail_source="manual",
            planned_fuel_load=50500,
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add_all(
            [
                mission,
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number="N500UP",
                    aircraft_type="A300",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.flush()
        return operation, mission

    def _configure_active_night_sort(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 8, 17, 22, 0
        )
        settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(settings)
        db.session.flush()
        db.session.add_all(
            [
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    day_of_week="monday",
                    sort_name="night",
                    is_active=True,
                ),
                SortTimelineSortSetting(
                    timeline_settings=settings,
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    planning_start_local=time(18, 0),
                    sort_window_start_local=time(20, 0),
                    sort_window_end_local=time(4, 0),
                ),
            ]
        )

    def _add_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name=username.replace("_", " ").title(),
            role="watcher",
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        db.session.add_all(
            [
                PortalAppAccess(
                    user_id=user.id,
                    app_code="neogateway",
                    status="approved",
                    role=role,
                    is_active=True,
                ),
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=scorpion.id,
                    role=role,
                    is_active=True,
                ),
            ]
        )
        return user

    def _login(self, user):
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
