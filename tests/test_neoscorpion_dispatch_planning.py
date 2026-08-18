import unittest
from datetime import date, datetime, timedelta

from sqlalchemy import event, inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortTruck,
    NeoScorpionTailFuelState,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import fuel_dispatch_context, settings_context
from app.services.neoscorpion_dispatch_planning import (
    DEFAULT_PLANNING_INBOUND_FALLBACK_LBS,
    estimate_fuel_demand_gallons,
    project_truck_remaining,
)
from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionDispatchPlanningTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "dispatch-planning-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 18),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(self.operation)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_estimated_fuel_uses_actual_or_fallback_and_handles_incomplete(self):
        actual = estimate_fuel_demand_gallons(50_000, 13_600, 6.7)
        fallback = estimate_fuel_demand_gallons(50_000, None, 6.7)
        floored = estimate_fuel_demand_gallons(10_000, 12_000, 6.7)

        self.assertEqual(actual.gallons, 5_433)
        self.assertEqual(actual.source_label, "ACTUAL INBOUND")
        self.assertEqual(fallback.gallons, 5_672)
        self.assertEqual(fallback.effective_inbound_lbs, 12_000)
        self.assertEqual(fallback.source_label, "12.0K ASSUMPTION")
        self.assertEqual(floored.gallons, 0)
        self.assertIsNone(estimate_fuel_demand_gallons(None, 10_000, 6.7).gallons)
        self.assertIsNone(estimate_fuel_demand_gallons(50_000, 10_000, None).gallons)
        self.assertIsNone(estimate_fuel_demand_gallons(50_000, 10_000, 0).gallons)

    def test_projection_is_cumulative_and_propagates_incomplete_or_short(self):
        projected = project_truck_remaining(
            {1: 10_000, 2: 1_000, 3: 5_000},
            (
                ("a", 1, 2_000),
                ("b", 1, 3_000),
                ("zero", 1, 0),
                ("short", 2, 2_000),
                ("missing", 3, None),
                ("after_missing", 3, 500),
            ),
        )

        self.assertEqual(projected["a"].gallons, 8_000)
        self.assertEqual(projected["b"].gallons, 5_000)
        self.assertEqual(projected["zero"].gallons, 5_000)
        self.assertEqual(projected["short"].gallons, -1_000)
        self.assertTrue(projected["short"].short)
        self.assertIsNone(projected["missing"].gallons)
        self.assertIsNone(projected["after_missing"].gallons)

    def test_fallback_setting_is_virtual_configurable_and_schema_safe(self):
        context = settings_context(self.gateway)
        self.assertEqual(context["planning_inbound_fallback_display"], "12.0")
        self.assertEqual(NeoScorpionSettings.query.count(), 0)

        user = self._login_user("planner_master", "master")
        response = self.client.post(
            "/neoscorpion/settings",
            data={
                "fuel_density_lbs_per_gallon": "6.7",
                "planning_inbound_fuel_fallback": "15.5",
                "fob_difference_threshold_lbs": "",
                "tf_vs_estimated_threshold_lbs": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        setting = NeoScorpionSettings.query.one()
        self.assertEqual(setting.planning_inbound_fuel_fallback_lbs, 15_500)
        self.assertEqual(setting.updated_by_user_id, user.id)
        mission = self._mission("UPS155", "N415UP", 22_200, 1)
        db.session.commit()
        row = fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(row["mission"].id, mission.id)
        self.assertEqual(row["estimated_fuel_gallons"], 1_000)
        self.assertEqual(row["estimated_fuel_source_label"], "15.5K ASSUMPTION")
        for columns in (LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS):
            self.assertEqual(
                columns["neoscorpion_settings"][
                    "planning_inbound_fuel_fallback_lbs"
                ],
                "INTEGER",
            )
        self.assertEqual(
            NEOSCORPION_ADDITIVE_COLUMNS["neoscorpion_settings"][
                "planning_inbound_fuel_fallback_lbs"
            ],
            "INTEGER",
        )
        self.assertEqual(DEFAULT_PLANNING_INBOUND_FALLBACK_LBS, 12_000)

    def test_local_schema_sync_adds_missing_planning_fallback_column(self):
        db.session.execute(
            text(
                "ALTER TABLE neoscorpion_settings "
                "DROP COLUMN planning_inbound_fuel_fallback_lbs"
            )
        )
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("neoscorpion_settings")
        }
        self.assertIn("planning_inbound_fuel_fallback_lbs", columns)

    def test_dispatch_projection_uses_tf_and_skips_completed_demand(self):
        truck = self._nightly_truck(current_gallons=10_000)
        completed = self._mission("UPS100", "N410UP", 40_000, 0)
        first = self._mission("UPS200", "N411UP", 25_400, 1)
        second = self._mission("UPS300", "N412UP", 32_100, 2)
        self._tail_fuel(first, inbound_lbs=12_000)
        completed_assignment = self._assignment(completed, truck, transfer=4_000)
        completed_assignment.review_status = "complete"
        completed.fuel_status = "complete"
        self._assignment(first, truck)
        self._assignment(second, truck, transfer=2_500)
        db.session.commit()

        rows = {
            row["mission"].flight_number: row
            for row in fuel_dispatch_context(self.gateway)["rows"]
        }
        self.assertEqual(rows["UPS200"]["estimated_fuel_gallons"], 2_000)
        self.assertEqual(rows["UPS200"]["estimated_fuel_source"], "actual_inbound")
        self.assertEqual(rows["UPS200"]["projected_truck_gallons"], 8_000)
        self.assertEqual(rows["UPS300"]["estimated_fuel_gallons"], 3_000)
        self.assertEqual(rows["UPS300"]["estimated_fuel_source"], "fallback")
        self.assertEqual(rows["UPS300"]["planning_demand_gallons"], 2_500)
        self.assertEqual(rows["UPS300"]["projected_truck_gallons"], 5_500)
        self.assertIsNone(rows["UPS100"]["projected_truck_gallons"])
        self.assertEqual(rows["UPS200"]["truck_remaining_fuel"], 10_000)

    def test_incomplete_demand_propagates_and_shortage_is_explicit(self):
        first_truck = self._nightly_truck("21", 5_000)
        second_truck = self._nightly_truck("22", 1_000)
        missing = self._mission("UPS401", "N413UP", None, 1)
        later = self._mission("UPS402", "N414UP", 15_350, 2)
        short = self._mission("UPS403", "N415UP", 25_400, 3)
        self._assignment(missing, first_truck)
        self._assignment(later, first_truck)
        self._assignment(short, second_truck)
        db.session.commit()

        rows = {
            row["mission"].flight_number: row
            for row in fuel_dispatch_context(self.gateway)["rows"]
        }
        self.assertEqual(rows["UPS401"]["projected_truck_display"], "INCOMPLETE")
        self.assertEqual(rows["UPS402"]["projected_truck_display"], "INCOMPLETE")
        self.assertEqual(rows["UPS403"]["projected_truck_gallons"], -1_000)
        self.assertTrue(rows["UPS403"]["projected_truck_short"])

    def test_load_planning_output_uses_neo_and_final_a300_center_actual(self):
        a300 = self._mission("UPS0952", "N160UP", 70_000, 1, destination="MHR")
        assignment = self._assignment(a300)
        work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number="N160UP",
            apu_running=False,
            apu_allowance_lbs=0,
        )
        db.session.add(work)
        db.session.flush()
        actual = {
            "l_out": 8_000,
            "l_in": 20_000,
            "ctr": 4_700,
            "r_in": 20_000,
            "r_out": 8_000,
            "tt": 0,
        }
        for code, value in actual.items():
            db.session.add(
                NeoScorpionFuelTankState(
                    fuel_work_state_id=work.id,
                    tank_code=code,
                    remaining_lbs=value,
                    actual_lbs=value,
                )
            )
        non_a300 = self._mission("UPS777", "N412UP", 50_000, 2, destination="SDF")
        non_a300_assignment = self._assignment(non_a300)
        non_a300_work = NeoScorpionFuelWorkState(
            fuel_assignment_id=non_a300_assignment.id,
            tail_number="N412UP",
            apu_running=False,
            apu_allowance_lbs=0,
        )
        db.session.add(non_a300_work)
        db.session.flush()
        for code, value in (("left", 10_000), ("ctr", 20_000), ("right", 20_000)):
            db.session.add(
                NeoScorpionFuelTankState(
                    fuel_work_state_id=non_a300_work.id,
                    tank_code=code,
                    remaining_lbs=value,
                    actual_lbs=value,
                )
            )
        db.session.commit()

        rows = {
            row["mission"].flight_number: row
            for row in fuel_dispatch_context(self.gateway)["rows"]
        }
        self.assertEqual(
            rows["UPS0952"]["load_planning_output"],
            "UPS0952 MHR N160UP NEO > 60.7 CF > 4700",
        )
        self.assertEqual(
            rows["UPS777"]["load_planning_output"],
            "UPS777 SDF N412UP NEO > 50.0",
        )
        self._login_user("load_planning_simulator", "simulator")
        rendered = self.client.get("/neoscorpion/fuel-dispatch")
        self.assertIn(
            b'data-copy-value="UPS0952 MHR N160UP NEO &gt; 60.7 CF &gt; 4700"',
            rendered.data,
        )

        next(
            state
            for state in rows["UPS0952"]["fuel_work_state"].tank_states
            if state.tank_code == "ctr"
        ).actual_lbs = None
        db.session.flush()
        refreshed = {
            row["mission"].flight_number: row
            for row in fuel_dispatch_context(self.gateway)["rows"]
        }
        self.assertFalse(refreshed["UPS0952"]["load_planning_ready"])
        rendered_incomplete = self.client.get("/neoscorpion/fuel-dispatch")
        self.assertNotIn(
            b'data-copy-value="UPS0952',
            rendered_incomplete.data,
        )

    def test_dispatch_get_renders_planning_copy_and_performs_no_writes(self):
        self._login_user("dispatch_simulator", "simulator")
        mission = self._mission("UPS888", "N412UP", 25_400, 1)
        self._tail_fuel(mission, inbound_lbs=12_000)
        db.session.commit()
        counts_before = self._row_counts()

        response = self.client.get("/neoscorpion/fuel-dispatch")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2,000 gal", response.data)
        self.assertIn(b"ACTUAL INBOUND", response.data)
        self.assertIn(b"Projected Truck", response.data)
        self.assertEqual(self._row_counts(), counts_before)

    def test_dispatch_queries_remain_bounded_for_representative_fixture(self):
        truck = self._nightly_truck(current_gallons=50_000)
        for index in range(150):
            mission = self._mission(
                f"UPS{1000 + index}",
                f"N4{100 + index}UP",
                25_400,
                index,
            )
            self._assignment(mission, truck, transfer=100 if index % 2 else None)
        db.session.commit()

        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            context = fuel_dispatch_context(self.gateway)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(len(context["rows"]), 150)
        self.assertLessEqual(len(statements), 20)

    def _mission(
        self,
        flight_number,
        tail_number,
        required_lbs,
        departure_offset,
        *,
        destination="SDF",
    ):
        departure = datetime(2026, 8, 19, 3, 0) + timedelta(
            minutes=departure_offset
        )
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight_number,
            origin=self.gateway.code,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=departure - timedelta(hours=5),
            planned_datetime_utc=departure,
            planned_source="manual",
            planned_fuel_load=required_lbs,
            assigned_tail_number=tail_number,
            tail_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        db.session.add(
            SortDateTailState(
                sort_date=self.operation.sort_date,
                gateway_code=self.gateway.code,
                sort_name="night",
                tail_number=tail_number,
                aircraft_type="A300" if tail_number.startswith("N1") else "757",
                aircraft_type_source="derived",
            )
        )
        return mission

    def _assignment(self, mission, truck=None, transfer=None):
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=self.operation.id,
            sort_date_mission_id=mission.id,
            assigned_truck_id=truck.id if truck else None,
            transfer_fuel_gallons=transfer,
            confirmed_tail_number=mission.assigned_tail_number,
        )
        db.session.add(assignment)
        db.session.flush()
        return assignment

    def _nightly_truck(self, number="20", current_gallons=10_000):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=number,
            capacity_gallons=20_000,
            remaining_fuel_gallons=777,
        )
        db.session.add(truck)
        db.session.flush()
        db.session.add(
            NeoScorpionSortTruck(
                sort_date_operation_id=self.operation.id,
                fuel_truck_id=truck.id,
                status="available",
                starting_gallons=current_gallons,
                current_gallons=current_gallons,
            )
        )
        return truck

    def _tail_fuel(self, mission, *, inbound_lbs):
        state = NeoScorpionTailFuelState(
            sort_date_operation_id=self.operation.id,
            tail_number=mission.assigned_tail_number,
            inbound_fuel_lbs=inbound_lbs,
        )
        db.session.add(state)
        return state

    def _login_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Dispatch",
            last_name="Planner",
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
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=scorpion.id,
                role=role,
                is_active=True,
            )
        )
        db.session.commit()
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
        )
        return user

    @staticmethod
    def _row_counts():
        return (
            NeoScorpionSettings.query.count(),
            NeoScorpionTailFuelState.query.count(),
            NeoScorpionFuelAssignment.query.count(),
            NeoScorpionFuelWorkState.query.count(),
            NeoScorpionFuelTankState.query.count(),
        )


if __name__ == "__main__":
    unittest.main()
