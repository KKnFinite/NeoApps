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
    NeoScorpionFuelingEvent,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortTruck,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    complete_fuel_on_board,
    complete_fueled_assignment,
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


class NeoScorpionDispatcherCompleteTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-complete-test",
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
        self.fueler = self._add_user("complete_fueler", "operator")
        self.dispatcher = self._add_user("complete_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_sync_adds_normal_completion_audit_columns(self):
        NeoScorpionFuelingEvent.__table__.drop(bind=db.engine)
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
                    fuel_on_board_at_utc DATETIME,
                    fuel_on_board_by_user_id INTEGER,
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
        self.assertIn("completed_at_utc", columns)
        self.assertIn("completed_by_user_id", columns)
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "completed_at_utc"
            ],
            "DATETIME",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "completed_by_user_id"
            ],
            "INTEGER",
        )

    def test_off_and_neo_fuel_are_required(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._save_tanks(assignment, movement="not_moved")
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "MARK OFF"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        left = NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=work.id,
            tank_code="left",
        ).one()
        left.actual_lbs = None
        work.off_at_utc = datetime(2026, 8, 18, 5, 0)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Complete Actual fuel"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self._assert_not_completed(operation, assignment, expected_revision=1)

    def test_unknown_movement_blocks_completion(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number="N412UP",
            on_at_utc=datetime(2026, 8, 18, 4, 0),
            off_at_utc=datetime(2026, 8, 18, 5, 0),
            off_by_user_id=self.fueler.id,
            apu_running=False,
            apu_confirmed_at_utc=datetime(2026, 8, 18, 4, 0),
            apu_allowance_lbs=0,
        )
        work.tank_states.extend(
            [
                NeoScorpionFuelTankState(
                    tank_code="left", remaining_lbs=10000, actual_lbs=10000
                ),
                NeoScorpionFuelTankState(
                    tank_code="ctr", remaining_lbs=None, actual_lbs=10000
                ),
                NeoScorpionFuelTankState(
                    tank_code="right", remaining_lbs=10000, actual_lbs=10000
                ),
            ]
        )
        db.session.add(work)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "cannot yet be determined"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self._assert_not_completed(operation, assignment, expected_revision=0)

    def test_moved_completion_validates_truck_tf_and_gallons(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._ready_work(assignment, movement="moved", transfer_gallons=None)

        with self.assertRaisesRegex(ValueError, "Assign the truck"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        truck, _nightly = self._assign_truck(
            operation,
            assignment,
            current_gallons=50,
        )
        for transfer_gallons in (None, 0):
            assignment.transfer_fuel_gallons = transfer_gallons
            db.session.commit()
            with self.assertRaisesRegex(ValueError, "positive T/F"):
                complete_fueled_assignment(
                    self.gateway,
                    self.dispatcher,
                    assignment.id,
                )
            db.session.rollback()

        nightly_truck = NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
        ).one()
        db.session.delete(nightly_truck)
        assignment.transfer_fuel_gallons = 10
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "missing from tonight's assets"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        db.session.add(
            NeoScorpionSortTruck(
                sort_date_operation_id=operation.id,
                fuel_truck_id=truck.id,
                status="topping_off",
                starting_gallons=100,
                current_gallons=None,
            )
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "current gallons are unknown"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        nightly_truck = NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
        ).one()
        nightly_truck.current_gallons = 5
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "exceeds"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self._assert_not_completed(operation, assignment, expected_revision=2)

    def test_moved_success_creates_event_deducts_once_and_preserves_master(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation)
        work = self._ready_work(
            assignment,
            movement="moved",
            transfer_gallons=75,
        )
        truck, nightly_truck = self._assign_truck(
            operation,
            assignment,
            status="topping_off",
            starting_gallons=500,
            current_gallons=400,
            legacy_gallons=975,
        )
        completed_at = datetime(2026, 8, 18, 5, 20)

        result = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
            now_utc=completed_at,
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.movement_status, "moved")
        self.assertEqual(result.revision, 3)
        db.session.commit()

        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event.sequence_number, 1)
        self.assertEqual(event.sort_date_operation_id, operation.id)
        self.assertEqual(event.fuel_assignment_id, assignment.id)
        self.assertEqual(event.fuel_work_state_id, work.id)
        self.assertEqual(event.tail_number, "N412UP")
        self.assertEqual(event.fuel_truck_id, truck.id)
        self.assertEqual(event.started_at_utc, work.on_at_utc)
        self.assertEqual(event.ended_at_utc, work.off_at_utc)
        self.assertEqual(event.transfer_fuel_gallons, 75)
        self.assertEqual(nightly_truck.starting_gallons, 500)
        self.assertEqual(nightly_truck.current_gallons, 325)
        self.assertEqual(truck.remaining_fuel_gallons, 975)
        self.assertEqual(assignment.completed_at_utc, completed_at)
        self.assertEqual(assignment.completed_by_user_id, self.dispatcher.id)
        self.assertEqual(assignment.review_status, "complete")
        self.assertEqual(mission.fuel_status, "complete")
        self.assertEqual(mission.fuel_completed_at_utc, completed_at)

        repeated = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 6, 0),
        )
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision, 3)
        db.session.commit()
        db.session.refresh(nightly_truck)
        self.assertEqual(nightly_truck.current_gallons, 325)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        self.assertEqual(assignment.completed_at_utc, completed_at)

    def test_not_moved_completes_without_event_or_truck_deduction(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation)
        self._ready_work(assignment, movement="not_moved", transfer_gallons=None)
        truck, nightly_truck = self._assign_truck(
            operation,
            assignment,
            status="unavailable_oos",
            starting_gallons=300,
            current_gallons=250,
        )

        result = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()

        self.assertTrue(result.changed)
        self.assertEqual(result.movement_status, "not_moved")
        self.assertIsNone(result.fueling_event)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(nightly_truck.current_gallons, 250)
        self.assertEqual(truck.remaining_fuel_gallons, 900)
        self.assertEqual(assignment.review_status, "complete")
        self.assertEqual(mission.fuel_status, "complete")

    def test_topping_off_and_oos_nightly_trucks_may_complete_moved_work(self):
        operation = self._operation()
        for index, status in enumerate(("topping_off", "unavailable_oos"), start=1):
            _mission, assignment = self._assignment(
                operation,
                flight_number=f"UPS90{index}",
                tail_number=f"N41{index + 2}UP",
            )
            self._ready_work(
                assignment,
                movement="moved",
                transfer_gallons=10,
            )
            _truck, nightly_truck = self._assign_truck(
                operation,
                assignment,
                status=status,
                truck_number=f"STATUS-{index}",
                starting_gallons=100,
                current_gallons=80,
            )
            result = complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
            db.session.commit()
            self.assertEqual(result.movement_status, "moved")
            self.assertEqual(nightly_truck.current_gallons, 70)

    def test_existing_event_and_fuel_on_board_block_normal_complete(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        work = self._ready_work(
            assignment,
            movement="not_moved",
            transfer_gallons=None,
        )
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="EVENT-BLOCK",
        )
        db.session.add(truck)
        db.session.flush()
        db.session.add(
            NeoScorpionFuelingEvent(
                sort_date_operation_id=operation.id,
                fuel_assignment_id=assignment.id,
                fuel_work_state_id=work.id,
                tail_number="N412UP",
                fuel_truck_id=truck.id,
                sequence_number=1,
            )
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "REVIEW REQUIRED"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        NeoScorpionFuelingEvent.query.delete()
        assignment.assigned_truck_id = None
        db.session.commit()
        complete_fuel_on_board(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "do not use normal COMPLETE"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

    def test_stale_cross_sort_attempt_and_route_permission_do_not_complete(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._ready_work(assignment, movement="not_moved", transfer_gallons=None)
        self._operation(day=date(2026, 8, 18))

        with self.assertRaisesRegex(ValueError, "current sort operation"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        self._login(self.fueler)
        response = self.client.post(
            "/neoscorpion/fuel-dispatch/complete",
            data={"assignment_id": str(assignment.id)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(assignment.completed_at_utc)

    def test_active_fueler_filter_and_read_only_dispatch_render(self):
        operation = self._operation()
        _mission, completed_assignment = self._assignment(operation)
        self._ready_work(
            completed_assignment,
            movement="not_moved",
            transfer_gallons=None,
        )
        result = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            completed_assignment.id,
        )
        self.assertTrue(result.changed)

        _mission, off_only_assignment = self._assignment(
            operation,
            flight_number="UPS902",
            tail_number="N413UP",
        )
        self._ready_work(
            off_only_assignment,
            movement="not_moved",
            transfer_gallons=None,
        )
        db.session.commit()

        active_ids = {
            row["assignment"].id
            for row in fueler_context(self.gateway, self.fueler)["rows"]
        }
        self.assertNotIn(completed_assignment.id, active_ids)
        self.assertIn(off_only_assignment.id, active_ids)

        self._login(self.dispatcher)
        event_count = NeoScorpionFuelingEvent.query.count()
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fuel-dispatch")
            self.assertEqual(commit.call_count, 0)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<th>Movement</th>", response.data)
        self.assertIn(b"neoscorpion-table-wrap--sticky", response.data)
        self.assertIn(b"OFF", response.data.upper())
        self.assertIn(b"COMPLETE", response.data.upper())
        self.assertNotIn(b'<option value="complete"', response.data)
        self.assertIn(b"/neoscorpion/fuel-dispatch/complete", response.data)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), event_count)

    def test_complete_route_commits_once(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._ready_work(
            assignment,
            movement="not_moved",
            transfer_gallons=None,
        )
        self._login(self.dispatcher)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fuel-dispatch/complete",
                data={"assignment_id": str(assignment.id)},
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(assignment)
        self.assertIsNotNone(assignment.completed_at_utc)

    def _operation(self, *, day=date(2026, 8, 17)):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(operation)
        db.session.commit()
        return operation

    def _assignment(
        self,
        operation,
        *,
        flight_number="UPS901",
        tail_number="N412UP",
    ):
        mission = SortDateMission(
            sort_date=operation.sort_date,
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
            assigned_tail_number=tail_number,
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
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number=tail_number,
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return mission, assignment

    def _save_tanks(self, assignment, *, movement, transfer_gallons=None):
        actual_left = "11.0" if movement == "moved" else "10.0"
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            {
                "assignment_id": str(assignment.id),
                "apu_running": "no",
                "remaining_left": "10.0",
                "actual_left": actual_left,
                "remaining_ctr": "10.0",
                "actual_ctr": "10.0",
                "remaining_right": "10.0",
                "actual_right": "10.0",
                "transfer_fuel_gallons": (
                    "" if transfer_gallons is None else str(transfer_gallons)
                ),
                "notes": "",
            },
        )

    def _ready_work(self, assignment, *, movement, transfer_gallons):
        off_transfer_gallons = (
            transfer_gallons
            if transfer_gallons is not None and transfer_gallons > 0
            else 1
        )
        self._save_tanks(
            assignment,
            movement=movement,
            transfer_gallons=off_transfer_gallons,
        )
        db.session.commit()
        result = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 0),
        )
        db.session.commit()
        if transfer_gallons != off_transfer_gallons:
            assignment.transfer_fuel_gallons = transfer_gallons
            db.session.commit()
        return result.fuel_work_state

    def _assign_truck(
        self,
        operation,
        assignment,
        *,
        status="available",
        truck_number="COMPLETE-1",
        starting_gallons=500,
        current_gallons=400,
        legacy_gallons=900,
    ):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=truck_number,
            capacity_gallons=1000,
            remaining_fuel_gallons=legacy_gallons,
        )
        db.session.add(truck)
        db.session.flush()
        nightly_truck = NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status=status,
            starting_gallons=starting_gallons,
            current_gallons=current_gallons,
        )
        assignment.assigned_truck_id = truck.id
        db.session.add(nightly_truck)
        db.session.commit()
        return truck, nightly_truck

    def _assert_not_completed(self, operation, assignment, *, expected_revision):
        db.session.refresh(assignment)
        self.assertIsNone(assignment.completed_at_utc)
        self.assertIsNone(assignment.completed_by_user_id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).first()
        self.assertEqual(int(state.revision if state else 0), expected_revision)

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
