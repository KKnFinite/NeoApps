import unittest
from datetime import date, datetime
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTankState,
    NeoScorpionFuelWorkState,
    NeoScorpionSortAssetState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    complete_fueled_assignment,
    fueler_context,
    mark_fueler_off,
    save_fueler_entry,
)
from app.services.neoscorpion_fuel_planning import plan_fuel_by_tank
from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
)


class NeoScorpionFuelPlanningTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-planning-test",
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
        self.user = self._add_user("planning_fueler", "grandmaster")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_hanzo_base_plans_for_all_supported_aircraft(self):
        cases = (
            (
                "B757",
                50000,
                {"ctr": 5000},
                {},
                {"left": "14.600", "ctr": "20.800", "right": "14.600"},
            ),
            (
                "B767ER",
                100000,
                {"ctr": 10000},
                {},
                {"left": "40.200", "ctr": "19.600", "right": "40.200"},
            ),
            (
                "A300",
                70000,
                {"ctr": 10000},
                {},
                {
                    "l_out": "8.200",
                    "l_in": "21.800",
                    "ctr": "10.000",
                    "r_in": "21.800",
                    "r_out": "8.200",
                    "tt": "0.000",
                },
            ),
            (
                "B747-400",
                150000,
                {},
                {"main_l_in": 20000},
                {
                    "main_l_out": "29.292",
                    "main_l_in": "20.000",
                    "main_r_in": "20.000",
                    "main_r_out": "29.292",
                    "reserve_2_l": "25.708",
                    "reserve_3_r": "25.708",
                    "center_wing": "0.000",
                },
            ),
            (
                "B747-8",
                180000,
                {},
                {"main_l_out": 20000, "main_l_in": 50000},
                {
                    "main_l_out": "20.000",
                    "main_l_in": "50.000",
                    "main_r_in": "50.000",
                    "main_r_out": "20.000",
                    "reserve_1_l": "10.278",
                    "reserve_4_r": "10.278",
                    "center_wing": "19.444",
                },
            ),
        )
        for aircraft_type, required, remaining, actual, expected in cases:
            with self.subTest(aircraft_type=aircraft_type):
                planned = plan_fuel_by_tank(
                    aircraft_type,
                    required,
                    remaining_lbs_by_tank=remaining,
                    actual_lbs_by_tank=actual,
                    apu_running=False,
                    apu_allowance_lbs=0,
                )
                self.assertEqual(
                    {
                        code: f"{value / Decimal('1000'):.3f}"
                        for code, value in planned.items()
                    },
                    expected,
                )
                self.assertEqual(sum(planned.values()), Decimal(required))

    def test_apu_allowance_is_applied_once_and_moves_between_sources(self):
        center_source = plan_fuel_by_tank(
            "B757",
            50000,
            remaining_lbs_by_tank={"ctr": 5000},
            apu_running=True,
            apu_allowance_lbs=500,
            apu_source_tank_code="ctr",
        )
        left_source = plan_fuel_by_tank(
            "B757",
            50000,
            remaining_lbs_by_tank={"ctr": 5000},
            apu_running=True,
            apu_allowance_lbs=500,
            apu_source_tank_code="left",
        )

        self.assertEqual(center_source["ctr"], Decimal("21300"))
        self.assertEqual(center_source["left"], Decimal("14600"))
        self.assertEqual(left_source["left"], Decimal("15100"))
        self.assertEqual(left_source["ctr"], Decimal("20800"))
        self.assertEqual(sum(center_source.values()), Decimal("50500"))
        self.assertEqual(sum(left_source.values()), Decimal("50500"))

    def test_apu_source_validation_persistence_and_revision(self):
        operation, _mission, assignment = self._assignment()
        confirmation_time = datetime(2026, 8, 18, 3, 45)

        for source in ("", "foreign_tank"):
            with self.subTest(source=source), self.assertRaisesRegex(
                ValueError,
                "valid APU source tank",
            ):
                save_fueler_entry(
                    self.gateway,
                    self.user,
                    self._form(
                        assignment,
                        apu_running="yes",
                        apu_source_tank_code=source,
                    ),
                    now_utc=confirmation_time,
                )
            db.session.rollback()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)

        first = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="yes",
                apu_source_tank_code="left",
            ),
            now_utc=confirmation_time,
        )
        self.assertEqual(first.revision, 1)
        db.session.commit()
        original_confirmation = first.fuel_work_state.apu_confirmed_at_utc
        original_allowance = first.fuel_work_state.apu_allowance_lbs

        loaded = fueler_context(self.gateway, self.user)["rows"][0]
        self.assertEqual(loaded["apu_source_tank_code"], "left")
        self.assertEqual(loaded["planned_total_display"], "50.4")

        moved = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="yes",
                apu_source_tank_code="ctr",
            ),
            now_utc=datetime(2026, 8, 18, 4, 15),
        )
        self.assertEqual(moved.revision, 2)
        self.assertEqual(moved.fuel_work_state.apu_source_tank_code, "ctr")
        self.assertEqual(
            moved.fuel_work_state.apu_confirmed_at_utc,
            original_confirmation,
        )
        self.assertEqual(moved.fuel_work_state.apu_allowance_lbs, original_allowance)
        db.session.commit()

        cleared = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="no",
                apu_source_tank_code="right",
            ),
            now_utc=datetime(2026, 8, 18, 4, 30),
        )
        self.assertEqual(cleared.revision, 3)
        self.assertIsNone(cleared.fuel_work_state.apu_source_tank_code)
        self.assertIsNotNone(operation.id)

    def test_apu_yes_without_source_blocks_off(self):
        _operation, _mission, assignment = self._assignment()
        saved = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                remaining_ctr="20.0",
                remaining_right="20.0",
                actual_left="10.0",
                actual_ctr="20.0",
                actual_right="20.0",
            ),
        )
        saved.fuel_work_state.apu_running = True
        saved.fuel_work_state.apu_allowance_lbs = 400
        saved.fuel_work_state.apu_source_tank_code = None
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "APU source tank before OFF"):
            mark_fueler_off(self.gateway, self.user, assignment.id)
        self.assertIsNone(saved.fuel_work_state.off_at_utc)

    def test_blank_and_explicit_zero_render_distinctly_with_planned_column(self):
        _operation, _mission, assignment = self._assignment()
        self._login()

        blank = self.client.get("/neoscorpion/fueler")
        self.assertEqual(blank.status_code, 200)
        self.assertIn(b'<span role="columnheader">Planned</span>', blank.data)
        self.assertIn(b'name="remaining_left" value=""', blank.data)
        self.assertIn(b'name="actual_left" value=""', blank.data)
        self.assertNotIn(b'placeholder="0.0"', blank.data)

        save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="no",
                remaining_left="0",
                remaining_ctr="0",
                remaining_right="0",
                actual_left="0",
                actual_ctr="0",
                actual_right="0",
            ),
        )
        db.session.commit()
        explicit_zero = self.client.get("/neoscorpion/fueler")
        self.assertIn(b'name="remaining_left" value="0.0"', explicit_zero.data)
        self.assertIn(b'name="actual_left" value="0.0"', explicit_zero.data)
        row = fueler_context(self.gateway, self.user)["rows"][0]
        self.assertEqual(row["remaining_total_display"], "0.0")
        self.assertEqual(row["actual_total_display"], "0.0")

    def test_dispatch_cleanup_and_authenticated_local_route_smoke(self):
        self._assignment()
        self._login()

        dispatch = self.client.get("/neoscorpion/fuel-dispatch")
        self.assertEqual(dispatch.status_code, 200)
        self.assertNotIn(b"<th>Movement</th>", dispatch.data)
        self.assertIn(b"neoscorpion-table-wrap--sticky", dispatch.data)

        for path in (
            "/neoscorpion",
            "/neoscorpion/fuel-dispatch",
            "/neoscorpion/fueler",
            "/neoscorpion/truck-manager",
            "/neoscorpion/settings",
            "/neoscorpion/history",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_source_aware_fueler_to_dispatcher_complete_workflow(self):
        _operation, mission, assignment = self._assignment()
        saved = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="yes",
                apu_source_tank_code="ctr",
                remaining_left="10.0",
                remaining_ctr="20.0",
                remaining_right="20.0",
                actual_left="9.6",
                actual_ctr="20.0",
                actual_right="20.0",
            ),
            now_utc=datetime(2026, 8, 18, 3, 45),
        )
        self.assertEqual(saved.revision, 1)
        db.session.commit()

        off = mark_fueler_off(
            self.gateway,
            self.user,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 4, 0),
        )
        self.assertEqual(off.revision, 2)
        db.session.commit()

        completed = complete_fueled_assignment(
            self.gateway,
            self.user,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 4, 5),
        )
        self.assertTrue(completed.changed)
        self.assertEqual(completed.movement_status, "not_moved")
        self.assertEqual(completed.revision, 3)
        self.assertEqual(assignment.review_status, "complete")
        self.assertEqual(mission.fuel_status, "complete")

    def test_apu_source_schema_contract_is_additive(self):
        self.assertIn(
            "apu_source_tank_code",
            NeoScorpionFuelWorkState.__table__.columns,
        )
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_work_states"][
                "apu_source_tank_code"
            ],
            "VARCHAR(32)",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_work_states"][
                "apu_source_tank_code"
            ],
            "VARCHAR(32)",
        )
        self.assertEqual(
            NEOSCORPION_ADDITIVE_COLUMNS["neoscorpion_fuel_work_states"][
                "apu_source_tank_code"
            ],
            "VARCHAR(32)",
        )

    def _assignment(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 17),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
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
            flight_number="UPS1501",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 0),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 0),
            planned_source="manual",
            planned_fuel_load=50000,
            assigned_tail_number="N412UP",
            tail_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.user.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number="N412UP",
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return operation, mission, assignment

    @staticmethod
    def _form(assignment, **values):
        return {
            "assignment_id": str(assignment.id),
            "transfer_fuel_gallons": "",
            "notes": "",
            **values,
        }

    def _add_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Planning",
            last_name="Fueler",
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

    def _login(self):
        self.client.post(
            "/login",
            data={"email": self.user.email, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
