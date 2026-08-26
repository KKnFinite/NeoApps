from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    MasterFlightSchedule,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.neoermac_building_lineup import (
    _current_sort_destination_pull_times,
)
from app.services.neoermac_dashboard import current_upcoming_pulls_operation
from app.services.neoermac_door_view import current_door_view_operation
from app.services.neoermac_pull_aggregation import (
    recompute_current_sort_door_pull_aggregates,
)
from app.services.neoermac_view_outbound import current_view_outbound_operation
from app.services.operation_lifecycle import ensure_operational_sort_operations
from app.services.operation_scope import current_operational_sort_operation
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class OperationLifecycleTest(unittest.TestCase):
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
        self.gateway = Gateway(code="RFD", name="RFD", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(self.settings)
        db.session.flush()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_inside_sort_window_creates_operation(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0].sort_date, date(2026, 6, 18))
        self.assertEqual(result["eligible"][0]["window_source"], "sort")

    def test_inside_sort_window_reuses_existing_operation(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        operation = self._operation(date(2026, 6, 18), "night")
        db.session.add(operation)
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(result["created"], [])
        self.assertEqual(result["existing"], [operation])
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_before_planning_start_does_not_create_operation(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(22, 0), time(4, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 15, 59),
        )

        self.assertEqual(result["eligible"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_at_planning_start_creates_operation(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(22, 0), time(4, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 16, 0),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["eligible"][0]["window_source"], "planning")
        self.assertEqual(
            result["eligible"][0]["window_start_local"],
            datetime(2026, 6, 18, 16, 0),
        )

    def test_planning_start_creates_before_sort_window_start(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(22, 0), time(4, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0].sort_date, date(2026, 6, 18))

    def test_planning_window_after_midnight_targets_previous_sort_date(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(22, 0), time(4, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 19, 3, 0),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["created"][0].sort_date, date(2026, 6, 18))
        self.assertEqual(
            result["eligible"][0]["window_end_local"],
            datetime(2026, 6, 19, 4, 0),
        )

    def test_planning_window_excludes_sort_window_end(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(22, 0), time(4, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 19, 4, 0),
        )

        self.assertEqual(result["eligible"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_planning_start_without_sort_window_end_does_not_create(self):
        self._activate("thursday", "night")
        setting = self._timeline("night", planning=time(16, 0))
        setting.sort_window_start_local = time(22, 0)
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(result["eligible"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_outside_sort_window_does_not_create_even_inside_ops_window(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            sort=(time(14, 0), time(5, 0)),
            ops=(time(6, 0), time(12, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 6, 30),
        )

        self.assertEqual(result["created"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_sort_window_creates_before_ops_window(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            sort=(time(14, 0), time(5, 0)),
            ops=(time(20, 0), time(3, 0)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 15, 0),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["eligible"][0]["window_source"], "sort")

    def test_other_operational_and_polling_windows_do_not_control_creation(self):
        self._activate("thursday", "night")
        setting = self._timeline(
            "night",
            planning=time(14, 0),
            sort=(time(22, 0), time(5, 0)),
            ops=(time(20, 0), time(3, 0)),
            polling=(time(18, 0), time(4, 0)),
        )
        setting.google_polling_start_local = time(19, 0)
        setting.google_polling_end_local = time(2, 0)
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 15, 0),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["eligible"][0]["window_source"], "planning")

    def test_sort_window_creates_when_ops_window_missing(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(22, 0), time(4, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 22, 15),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["eligible"][0]["window_source"], "sort")

    def test_incomplete_ops_window_does_not_affect_complete_sort_window(self):
        self._activate("thursday", "night")
        setting = self._timeline("night", sort=(time(22, 0), time(4, 0)))
        setting.ops_window_start_local = time(18, 0)
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 22, 15),
        )

        self.assertEqual(len(result["created"]), 1)
        self.assertEqual(result["eligible"][0]["window_source"], "sort")

    def test_at_window_end_operation_is_not_created(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 23, 0),
        )

        self.assertEqual(result["created"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_unconfigured_sort_window_does_not_fall_back_to_ops_or_polling(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            ops=(time(0, 0), time(23, 59)),
            polling=(time(0, 0), time(23, 59)),
        )
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 22, 15),
        )

        self.assertEqual(result["eligible"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_after_midnight_crossing_window_targets_previous_sort_date(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 19, 0, 30),
        )

        operation = result["created"][0]
        self.assertEqual(operation.sort_date, date(2026, 6, 18))
        self.assertEqual(
            result["eligible"][0]["window_end_local"],
            datetime(2026, 6, 19, 4, 0),
        )
        self.assertEqual(
            SortDateOperation.query.filter_by(sort_date=date(2026, 6, 19)).count(),
            0,
        )

    def test_inactive_matrix_sort_is_not_created(self):
        self._activate("thursday", "night", active=False)
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(20, 0), time(23, 0)),
        )
        db.session.commit()

        ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_inactive_gateway_does_not_create(self):
        self.gateway.is_active = False
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(result["eligible"], [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_multiple_eligible_sorts_are_created(self):
        self._activate("thursday", "twilight")
        self._activate("thursday", "night")
        self._timeline("twilight", sort=(time(18, 0), time(22, 0)))
        self._timeline("night", sort=(time(19, 0), time(4, 0)))
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 0),
        )

        self.assertEqual(
            {operation.sort_name for operation in result["created"]},
            {"twilight", "night"},
        )

    def test_generation_copies_active_master_missions(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        self._master("UPS0947", active_days="thursday")
        self._master("UPS9999", active_days="friday")
        db.session.commit()

        result = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(
            [mission.flight_number for mission in result["created"][0].missions],
            ["UPS0947"],
        )

    def test_system_generation_has_no_user_provenance(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        operation = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )["created"][0]

        self.assertIsNone(operation.generated_by_user_id)

    def test_duplicate_race_reloads_winning_operation(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        def concurrent_insert(**kwargs):
            db.session.add(
                self._operation(kwargs["sort_date"], kwargs["sort_name"])
            )
            db.session.commit()
            raise IntegrityError("insert", {}, Exception("duplicate"))

        with patch(
            "app.services.operation_lifecycle.generate_sort_date_operation_from_master",
            side_effect=concurrent_insert,
        ):
            result = ensure_operational_sort_operations(
                self.gateway,
                now=datetime(2026, 6, 18, 20, 30),
            )

        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["existing"]), 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_repeated_ensure_is_idempotent(self):
        self._activate("thursday", "night")
        self._timeline(
            "night",
            planning=time(16, 0),
            sort=(time(20, 0), time(23, 0)),
        )
        db.session.commit()

        first = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )
        second = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 31),
        )

        self.assertEqual(len(first["created"]), 1)
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["existing"]), 1)
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_gateway_scoping_does_not_create_another_gateway_sort(self):
        dfw = Gateway(code="DFW", name="DFW", is_active=True)
        db.session.add(dfw)
        db.session.flush()
        self._activate("thursday", "night", gateway=dfw)
        db.session.commit()

        ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )

        self.assertEqual(
            SortDateOperation.query.filter_by(gateway_code="DFW").count(),
            0,
        )

    def test_historical_operation_is_not_changed(self):
        historical = self._operation(date(2026, 5, 1), "night")
        historical.window_minutes = 37
        db.session.add(historical)
        db.session.flush()
        historical_mission = SortDateMission(
            sort_date_operation=historical,
            sort_date=historical.sort_date,
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            mission_source="manual",
            flight_number="KEEP001",
            origin="RFD",
            destination="SDF",
            timezone="America/Chicago",
        )
        db.session.add(historical_mission)
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 20, 30),
        )
        db.session.refresh(historical)
        db.session.refresh(historical_mission)

        self.assertEqual(historical.window_minutes, 37)
        self.assertEqual(historical_mission.flight_number, "KEEP001")
        self.assertEqual(historical_mission.mission_source, "manual")

    def test_historical_route_does_not_regenerate_or_change_historical_operation(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 6, 18, 20, 30
        )
        historical = self._operation(date(2026, 5, 1), "night")
        historical.window_minutes = 37
        db.session.add(historical)
        db.session.flush()
        db.session.add(
            SortDateMission(
                sort_date_operation=historical,
                sort_date=historical.sort_date,
                gateway_code="RFD",
                sort_name="night",
                mission_type="departure",
                mission_source="manual",
                flight_number="KEEP001",
                origin="RFD",
                destination="SDF",
                timezone="America/Chicago",
            )
        )
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        user = User(username="historical-user", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        ensure_default_permission_rules()
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        response = self.client.get(f"/motherbrain/operations/{historical.id}")

        db.session.refresh(historical)
        historical_missions = SortDateMission.query.filter_by(
            sort_date_operation_id=historical.id
        ).all()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(historical.window_minutes, 37)
        self.assertEqual(len(historical_missions), 1)
        self.assertEqual(historical_missions[0].flight_number, "KEEP001")
        self.assertEqual(historical_missions[0].mission_source, "manual")

    def test_lifecycle_does_not_invoke_google_or_live_polling(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        with (
            patch(
                "app.services.google_motherbrain_sheets.read_google_motherbrain_envelope"
            ) as reader,
            patch(
                "app.services.google_motherbrain_live_missions.apply_google_motherbrain_live_rows"
            ) as writer,
            patch(
                "app.services.google_motherbrain_live_polling.google_motherbrain_live_polling_enabled"
            ) as polling,
        ):
            ensure_operational_sort_operations(
                self.gateway,
                now=datetime(2026, 6, 18, 20, 30),
            )

        reader.assert_not_called()
        writer.assert_not_called()
        polling.assert_not_called()

    def test_authenticated_gateway_get_does_not_create_operation(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 6, 18, 20, 30
        )
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        user = User(username="lifecycle-user", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        ensure_default_permission_rules()
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_public_and_auth_requests_do_not_run_lifecycle(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 6, 18, 20, 30
        )
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        db.session.commit()

        self.client.get("/")
        self.client.get("/login")

        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_authenticated_ermac_request_does_not_create_operation(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 6, 18, 20, 30
        )
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(23, 0)))
        user = User(username="node-lifecycle-user", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        ensure_default_permission_rules()
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_ermac_resolvers_use_existing_active_window_operation(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        operation = self._operation(date(2026, 6, 18), "night")
        db.session.add(operation)
        db.session.commit()

        resolved = self._resolve_ermac_operations(datetime(2026, 6, 18, 22, 0))

        self.assertEqual(resolved, [operation, operation, operation, operation])

    def test_ermac_resolvers_reject_previous_unarchived_operation_after_window(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        old_operation = self._operation(date(2026, 6, 18), "night")
        db.session.add(old_operation)
        db.session.commit()

        resolved = self._resolve_ermac_operations(datetime(2026, 6, 19, 5, 0))

        self.assertEqual(resolved, [None, None, None, None])
        self.assertFalse(old_operation.archived_at_utc)
        with (
            self.app.test_request_context("/neoermac/door-view"),
            patch(
                "app.services.operation_lifecycle.current_gateway_local_datetime",
                return_value=datetime(2026, 6, 19, 5, 0),
            ),
        ):
            self.assertEqual(_current_sort_destination_pull_times(self.gateway), {})
            self.assertEqual(
                recompute_current_sort_door_pull_aggregates(self.gateway),
                {},
            )

    def test_ermac_resolvers_keep_previous_date_night_current_after_midnight(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        operation = self._operation(date(2026, 6, 18), "night")
        db.session.add(operation)
        db.session.commit()

        resolved = self._resolve_ermac_operations(datetime(2026, 6, 19, 2, 0))

        self.assertEqual(resolved, [operation, operation, operation, operation])

    def test_ermac_resolvers_return_no_current_sort_when_operation_is_missing(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        db.session.commit()

        resolved = self._resolve_ermac_operations(datetime(2026, 6, 18, 22, 0))

        self.assertEqual(resolved, [None, None, None, None])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_ermac_current_resolution_is_read_only_and_bounded(self):
        self._activate("thursday", "night")
        self._timeline("night", sort=(time(20, 0), time(4, 0)))
        operation = self._operation(date(2026, 6, 18), "night")
        db.session.add(operation)
        db.session.commit()
        statements = []

        def capture(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip().lower())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            resolved = self._resolve_ermac_operations(
                datetime(2026, 6, 18, 22, 0)
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(resolved, [operation, operation, operation, operation])
        self.assertFalse(
            any(row.startswith(("insert", "update", "delete")) for row in statements)
        )
        self.assertLessEqual(
            sum(row.startswith("select") for row in statements),
            6,
        )

    def _resolve_ermac_operations(self, local_now):
        with (
            self.app.test_request_context("/neoermac/door-view"),
            patch(
                "app.services.operation_lifecycle.current_gateway_local_datetime",
                return_value=local_now,
            ),
        ):
            return [
                current_operational_sort_operation(self.gateway),
                current_door_view_operation(self.gateway),
                current_upcoming_pulls_operation(self.gateway),
                current_view_outbound_operation(self.gateway),
            ]

    def _activate(self, day, sort_name, active=True, gateway=None):
        gateway = gateway or self.gateway
        row = GatewaySortMatrix(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            day_of_week=day,
            sort_name=sort_name,
            is_active=active,
        )
        db.session.add(row)
        return row

    def _timeline(self, sort_name, ops=None, sort=None, polling=None, planning=None):
        row = SortTimelineSortSetting(
            timeline_settings=self.settings,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name=sort_name,
        )
        if ops:
            row.ops_window_start_local, row.ops_window_end_local = ops
        if sort:
            row.sort_window_start_local, row.sort_window_end_local = sort
        if planning:
            row.planning_start_local = planning
        if polling:
            row.polling_start_local, row.polling_end_local = polling
        db.session.add(row)
        return row

    def _operation(self, sort_date, sort_name):
        return SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name=sort_name,
        )

    def _master(self, flight_number, active_days):
        row = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            wave="1",
            flight_number=flight_number,
            aircraft_type="757",
            origin="RFD",
            destination="SDF",
            active=True,
            active_days=active_days,
            planned_time_local=time(2, 0),
            timezone="America/Chicago",
        )
        db.session.add(row)
        return row


if __name__ == "__main__":
    unittest.main()
