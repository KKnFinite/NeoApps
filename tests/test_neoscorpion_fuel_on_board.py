import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import inspect

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
    NeoScorpionSortAssetState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    complete_fuel_on_board,
    fueler_context,
    mark_fueler_off,
    save_fueler_entry,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionFuelOnBoardTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-fob-test",
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
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id))
        self.fueler = self._add_user("fob_fueler", "operator")
        self.dispatcher = self._add_user("fob_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_sync_adds_fuel_on_board_audit_columns(self):
        NeoScorpionFuelTankState.__table__.drop(bind=db.engine)
        NeoScorpionFuelWorkState.__table__.drop(bind=db.engine)
        NeoScorpionFuelAssignment.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE neoscorpion_fuel_assignments (
                    id INTEGER PRIMARY KEY,
                    sort_date_operation_id INTEGER NOT NULL,
                    sort_date_mission_id INTEGER NOT NULL,
                    assigned_fueler_user_id INTEGER,
                    assigned_truck_id INTEGER,
                    transfer_fuel_gallons INTEGER,
                    estimated_fuel_gallons INTEGER,
                    calculation_status VARCHAR(32) NOT NULL,
                    review_status VARCHAR(32) NOT NULL,
                    load_planning_note TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_neoscorpion_fuel_assignment_mission
                        UNIQUE (sort_date_mission_id)
                )
                """
            )

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_assignments"
            )
        }
        self.assertIn("fuel_on_board_at_utc", columns)
        self.assertIn("fuel_on_board_by_user_id", columns)
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "fuel_on_board_at_utc"
            ],
            "DATETIME",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "fuel_on_board_by_user_id"
            ],
            "INTEGER",
        )

    def test_incomplete_actual_and_unconfirmed_apu_block_fuel_on_board(self):
        operation, _mission, assignment = self._assignment()
        partial = save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fueler_form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                actual_left="9.0",
            ),
        )
        self.assertEqual(partial.revision, 1)
        db.session.commit()

        with self.assertRaisesRegex(
            ValueError,
            "Complete Actual fuel and confirm APU before Fuel On Board",
        ):
            complete_fuel_on_board(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        unconfirmed = save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fueler_form(
                assignment,
                apu_running="not_confirmed",
                remaining_ctr="20.0",
                actual_ctr="18.0",
                remaining_right="30.0",
                actual_right="27.0",
            ),
        )
        self.assertEqual(unconfirmed.revision, 2)
        db.session.commit()

        with self.assertRaisesRegex(
            ValueError,
            "Complete Actual fuel and confirm APU before Fuel On Board",
        ):
            complete_fuel_on_board(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self.assertIsNone(assignment.fuel_on_board_at_utc)
        self.assertEqual(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).one().revision,
            2,
        )

    def test_assigned_truck_and_positive_tf_block_fuel_on_board(self):
        operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="FOB-1",
        )
        db.session.add(truck)
        db.session.flush()
        assignment.assigned_truck_id = truck.id
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Clear the unused truck"):
            complete_fuel_on_board(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        assignment.assigned_truck_id = None
        assignment.transfer_fuel_gallons = 5
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "T/F must be blank or 0"):
            complete_fuel_on_board(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self.assertIsNone(assignment.fuel_on_board_at_utc)
        self.assertEqual(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).one().revision,
            1,
        )

    def test_success_completes_mission_preserves_off_and_repeat_is_noop(self):
        operation, mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        off_time = datetime(2026, 8, 18, 5, 10)
        off = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=off_time,
        )
        self.assertEqual(off.revision, 2)
        db.session.commit()

        self._login(self.dispatcher)
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fuel-dispatch/fuel-on-board",
                data={"assignment_id": str(assignment.id)},
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(response.status_code, 302)

        db.session.refresh(assignment)
        db.session.refresh(mission)
        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        self.assertIsNotNone(assignment.fuel_on_board_at_utc)
        self.assertEqual(assignment.fuel_on_board_by_user_id, self.dispatcher.id)
        self.assertEqual(assignment.transfer_fuel_gallons, 0)
        self.assertEqual(assignment.review_status, "complete")
        self.assertEqual(mission.fuel_status, "complete")
        self.assertIsNotNone(mission.fuel_completed_at_utc)
        self.assertEqual(work.off_at_utc, off_time)
        self.assertEqual(work.off_by_user_id, self.fueler.id)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).one()
        self.assertEqual(state.revision, 3)

        original_fob_at = assignment.fuel_on_board_at_utc
        original_completed_at = mission.fuel_completed_at_utc
        repeated = complete_fuel_on_board(
            self.gateway,
            self.dispatcher,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 6, 0),
        )
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision, 3)
        self.assertEqual(assignment.fuel_on_board_at_utc, original_fob_at)
        self.assertEqual(mission.fuel_completed_at_utc, original_completed_at)

        self.assertEqual(fueler_context(self.gateway, self.fueler)["rows"], [])

    def test_off_only_assignment_remains_active_for_fueler(self):
        _operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()

        rows = fueler_context(self.gateway, self.fueler)["rows"]
        self.assertEqual([row["assignment"].id for row in rows], [assignment.id])

    def test_cross_sort_assignment_is_rejected(self):
        operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        self._assignment(day=date(2026, 8, 18), flight_number="UPS802")

        with self.assertRaisesRegex(ValueError, "current sort operation"):
            complete_fuel_on_board(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self.assertIsNone(assignment.fuel_on_board_at_utc)
        self.assertEqual(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).one().revision,
            1,
        )

    def test_dispatch_get_is_read_only_and_renders_copyable_neo_fuel(self):
        _operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        self._login(self.dispatcher)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fuel-dispatch")
            self.assertEqual(commit.call_count, 0)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NEO FUEL", response.data.upper())
        self.assertIn(b'data-copy-neo-fuel="54.0"', response.data)
        self.assertIn(b">FUEL ON BOARD</button>", response.data)
        self.assertIsNone(assignment.fuel_on_board_at_utc)

    def _save_complete(self, assignment):
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fueler_form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                actual_left="9.0",
                remaining_ctr="20.0",
                actual_ctr="18.0",
                remaining_right="30.0",
                actual_right="27.0",
            ),
        )

    def _assignment(
        self,
        *,
        day=date(2026, 8, 17),
        flight_number="UPS801",
    ):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight_number,
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
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
            assigned_fueler_user_id=self.fueler.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=day,
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
    def _fueler_form(assignment, **values):
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
