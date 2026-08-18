import unittest
from datetime import date, datetime

from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

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
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    classify_fuel_movement,
    complete_fuel_on_board,
    mark_fueler_off,
    save_fueler_entry,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionFuelingEventFoundationTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-event-test",
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
        self.fueler = self._add_user("event_fueler", "operator")
        self.dispatcher = self._add_user("event_dispatcher", "simulator")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_bootstrap_and_event_constraints(self):
        NeoScorpionFuelingEvent.__table__.drop(bind=db.engine)
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)
        self.assertIn(
            "neoscorpion_fueling_events",
            set(inspect(db.engine).get_table_names()),
        )

        operation, _mission, assignment = self._assignment()
        work = self._save_complete(assignment).fuel_work_state
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="EVENT-1",
        )
        db.session.add(truck)
        db.session.flush()
        first = self._event(operation, assignment, work, truck, sequence=1)
        db.session.add(first)
        db.session.commit()
        self.assertEqual(first.tail_number, "N412UP")

        self._assert_rejected(
            self._event(operation, assignment, work, truck, sequence=1)
        )
        second = self._event(operation, assignment, work, truck, sequence=2)
        db.session.add(second)
        db.session.commit()
        self.assertEqual(
            NeoScorpionFuelingEvent.query.filter_by(
                fuel_work_state_id=work.id
            ).count(),
            2,
        )

        self._assert_rejected(
            self._event(operation, assignment, work, truck, sequence=0)
        )
        self._assert_rejected(
            self._event(
                operation,
                assignment,
                work,
                truck,
                sequence=3,
                transfer_fuel_gallons=-1,
            )
        )

    def test_fuel_on_board_and_positive_tf_are_authoritative(self):
        assignment = NeoScorpionFuelAssignment(
            fuel_on_board_at_utc=datetime(2026, 8, 18, 5, 0),
            transfer_fuel_gallons=100,
        )
        self.assertEqual(
            classify_fuel_movement(assignment, None),
            "not_moved",
        )

        assignment.fuel_on_board_at_utc = None
        self.assertEqual(classify_fuel_movement(assignment, None), "moved")

    def test_complete_tank_evidence_classifies_movement_and_equality(self):
        assignment = NeoScorpionFuelAssignment(transfer_fuel_gallons=None)
        moved_work = self._movement_work(
            remaining=(10000, 10000, 10000),
            actual=(11000, 10000, 10000),
            apu_running=False,
            apu_allowance_lbs=0,
        )
        self.assertEqual(
            classify_fuel_movement(assignment, moved_work),
            "moved",
        )

        equal_work = self._movement_work(
            remaining=(10000, 10000, 10000),
            actual=(10000, 10000, 10000),
            apu_running=False,
            apu_allowance_lbs=0,
        )
        self.assertEqual(
            classify_fuel_movement(assignment, equal_work),
            "not_moved",
        )

    def test_apu_only_difference_is_not_truck_movement(self):
        assignment = NeoScorpionFuelAssignment(transfer_fuel_gallons=None)
        work = self._movement_work(
            remaining=(10000, 10000, 10000),
            actual=(9000, 10000, 10000),
            apu_running=True,
            apu_allowance_lbs=1000,
        )
        self.assertEqual(
            classify_fuel_movement(assignment, work),
            "not_moved",
        )

    def test_incomplete_data_is_unknown_and_zero_tf_does_not_override_tanks(self):
        incomplete = self._movement_work(
            remaining=(10000, 10000, 10000),
            actual=(9000, None, 10000),
            apu_running=False,
            apu_allowance_lbs=0,
        )
        self.assertEqual(
            classify_fuel_movement(
                NeoScorpionFuelAssignment(transfer_fuel_gallons=None),
                incomplete,
            ),
            "unknown",
        )

        positive_tank_evidence = self._movement_work(
            remaining=(10000, 10000, 10000),
            actual=(11000, 10000, 10000),
            apu_running=False,
            apu_allowance_lbs=0,
        )
        self.assertEqual(
            classify_fuel_movement(
                NeoScorpionFuelAssignment(transfer_fuel_gallons=0),
                positive_tank_evidence,
            ),
            "moved",
        )

    def test_classifier_with_preloaded_state_performs_no_database_work(self):
        _operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        work = (
            NeoScorpionFuelWorkState.query.options(
                joinedload(NeoScorpionFuelWorkState.tank_states)
            )
            .filter_by(fuel_assignment_id=assignment.id)
            .one()
        )
        statements = []

        def capture_statement(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            result = classify_fuel_movement(assignment, work)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(result, "not_moved")
        self.assertEqual(statements, [])
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_existing_save_off_and_fuel_on_board_create_no_events(self):
        _operation, _mission, assignment = self._assignment()
        saved = self._save_complete(assignment)
        db.session.commit()
        self.assertEqual(saved.revision, 1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

        off = mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()
        self.assertEqual(off.revision, 2)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

        completed = complete_fuel_on_board(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()
        self.assertEqual(completed.revision, 3)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def _assignment(self):
        day = date(2026, 8, 17)
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
            flight_number="UPS901",
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

    def _save_complete(self, assignment):
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            {
                "assignment_id": str(assignment.id),
                "apu_running": "no",
                "remaining_left": "10.0",
                "actual_left": "10.0",
                "remaining_ctr": "10.0",
                "actual_ctr": "10.0",
                "remaining_right": "10.0",
                "actual_right": "10.0",
                "transfer_fuel_gallons": "",
                "notes": "",
            },
        )

    @staticmethod
    def _movement_work(
        *,
        remaining,
        actual,
        apu_running,
        apu_allowance_lbs,
    ):
        work = NeoScorpionFuelWorkState(
            fuel_assignment_id=1,
            tail_number="N412UP",
            apu_running=apu_running,
            apu_allowance_lbs=apu_allowance_lbs,
        )
        for tank_code, remaining_lbs, actual_lbs in zip(
            ("left", "ctr", "right"),
            remaining,
            actual,
        ):
            work.tank_states.append(
                NeoScorpionFuelTankState(
                    tank_code=tank_code,
                    remaining_lbs=remaining_lbs,
                    actual_lbs=actual_lbs,
                )
            )
        return work

    @staticmethod
    def _event(
        operation,
        assignment,
        work,
        truck,
        *,
        sequence,
        transfer_fuel_gallons=None,
    ):
        return NeoScorpionFuelingEvent(
            sort_date_operation_id=operation.id,
            fuel_assignment_id=assignment.id,
            fuel_work_state_id=work.id,
            tail_number=" n412up ",
            fuel_truck_id=truck.id,
            sequence_number=sequence,
            transfer_fuel_gallons=transfer_fuel_gallons,
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

    def _assert_rejected(self, model):
        db.session.add(model)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


if __name__ == "__main__":
    unittest.main()
