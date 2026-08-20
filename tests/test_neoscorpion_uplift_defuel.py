import unittest
from datetime import date, datetime

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelingEventTankSnapshot,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    NeoScorpionTailFuelState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    complete_fueled_assignment,
    fuel_dispatch_context,
    mark_fueler_off,
    save_fueler_entry,
    start_follow_up_fuel_cycle,
)
from app.services.neoscorpion_assets import mark_nightly_truck_sumped
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionUpliftDefuelTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-uplift-defuel-test",
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
        self.fueler = self._add_user("cycle_fueler", "operator")
        self.dispatcher = self._add_user("cycle_dispatcher", "simulator")
        self.other_fueler = self._add_user("cycle_other", "operator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_contract_and_local_sync_cover_cycle_additions(self):
        tables = set(inspect(db.engine).get_table_names())
        self.assertIn("neoscorpion_fueling_event_tank_snapshots", tables)
        self.assertIn("event_type", NeoScorpionFuelingEvent.__table__.columns)
        self.assertIn("current_cycle_type", NeoScorpionFuelAssignment.__table__.columns)

        NeoScorpionFuelingEventTankSnapshot.__table__.drop(bind=db.engine)
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)
        self.assertIn(
            "neoscorpion_fueling_event_tank_snapshots",
            inspect(db.engine).get_table_names(),
        )

        create_sql = db.session.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'neoscorpion_sort_trucks'"
            )
        ).scalar()
        db.session.execute(text("DROP TABLE neoscorpion_sort_trucks"))
        db.session.execute(text(create_sql.replace(", 'needs_sump'", "")))
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        repaired_sql = db.session.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'neoscorpion_sort_trucks'"
            )
        ).scalar()
        self.assertIn("'needs_sump'", repaired_sql)

    def test_initial_completion_creates_fuel_event_with_immutable_snapshots(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation)
        truck, nightly = self._truck(operation, assignment, "FUEL-1", 400)
        work = self._complete_cycle(
            operation,
            assignment,
            remaining=(10, 10, 10),
            actual=(11, 10, 10),
            transfer=75,
        )

        event = NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event.event_type, "fuel")
        self.assertEqual(event.cycle_number, 1)
        self.assertEqual(event.sequence_number, 1)
        self.assertEqual(event.fueler_user_id, self.fueler.id)
        self.assertEqual(event.fuel_truck_id, truck.id)
        self.assertEqual(event.required_fuel_lbs, mission.planned_fuel_load)
        self.assertFalse(event.apu_running)
        self.assertEqual(event.apu_allowance_lbs, 0)
        self.assertIsNone(event.apu_source_tank_code)
        self.assertEqual(event.neo_fuel_lbs, 31_000)
        self.assertIsNone(event.center_fuel_lbs)
        self.assertEqual(event.started_at_utc, work.on_at_utc)
        self.assertEqual(event.ended_at_utc, work.off_at_utc)
        snapshots = {row.tank_code: row for row in event.tank_snapshots}
        self.assertEqual(set(snapshots), {"left", "ctr", "right"})
        self.assertEqual(snapshots["left"].remaining_lbs, 10_000)
        self.assertEqual(snapshots["left"].actual_lbs, 11_000)
        self.assertEqual(snapshots["left"].planned_lbs, 14_600)
        self.assertEqual(nightly.current_gallons, 325)

    def test_uplift_prefills_same_tail_remaining_and_completes_next_event_once(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        _truck, nightly = self._truck(operation, assignment, "UP-1", 500)
        self._complete_cycle(
            operation,
            assignment,
            remaining=(10, 10, 10),
            actual=(11, 10, 10),
            transfer=50,
        )
        first_event = NeoScorpionFuelingEvent.query.one()
        first_snapshot_values = [
            (row.tank_code, row.remaining_lbs, row.planned_lbs, row.actual_lbs)
            for row in first_event.tank_snapshots
        ]
        revision = self._revision(operation)

        started = start_follow_up_fuel_cycle(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "uplift",
            "55.0",
            self.fueler.id,
            nightly.fuel_truck_id,
            now_utc=datetime(2026, 8, 18, 6, 0),
        )
        self.assertEqual(started.revision, revision + 1)
        db.session.commit()
        self.assertEqual(assignment.current_cycle_type, "uplift")
        self.assertEqual(assignment.current_cycle_number, 2)
        self.assertIsNone(assignment.transfer_fuel_gallons)
        self.assertIsNone(assignment.completed_at_utc)
        self.assertIsNone(started.fuel_work_state.on_at_utc)
        self.assertIsNone(started.fuel_work_state.off_at_utc)
        self.assertIsNone(started.fuel_work_state.apu_running)
        self.assertEqual(
            {state.tank_code: state.remaining_lbs for state in started.fuel_work_state.tank_states},
            {"left": 11000, "ctr": 10000, "right": 10000},
        )
        self.assertTrue(all(state.actual_lbs is None for state in started.fuel_work_state.tank_states))
        self._login(self.fueler)
        fueler_page = self.client.get("/neoscorpion/fueler")
        self.assertEqual(fueler_page.status_code, 200)
        self.assertIn(b">UPLIFT<", fueler_page.data)
        self.assertIn(b'name="remaining_left" value="11.0"', fueler_page.data)

        revision = self._revision(operation)
        self._save_cycle(
            assignment,
            remaining=(10, 10, 10),
            actual=(12, 10, 10),
            transfer=40,
        )
        db.session.commit()
        mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()
        completed = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        self.assertEqual(completed.revision, revision + 3)
        db.session.commit()

        events = NeoScorpionFuelingEvent.query.order_by(
            NeoScorpionFuelingEvent.sequence_number
        ).all()
        self.assertEqual([(row.sequence_number, row.event_type) for row in events], [(1, "fuel"), (2, "uplift")])
        self.assertEqual(nightly.current_gallons, 410)
        self.assertEqual(
            [
                (row.tank_code, row.remaining_lbs, row.planned_lbs, row.actual_lbs)
                for row in events[0].tank_snapshots
            ],
            first_snapshot_values,
        )
        repeated_revision = self._revision(operation)
        repeated = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()
        self.assertFalse(repeated.changed)
        self.assertEqual(self._revision(operation), repeated_revision)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 2)
        self.assertEqual(nightly.current_gallons, 410)

    def test_tail_swapped_uplift_does_not_copy_old_tail_actuals(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation)
        _truck, nightly = self._truck(operation, assignment, "UP-TAIL", 500)
        self._complete_cycle(
            operation,
            assignment,
            remaining=(10, 10, 10),
            actual=(11, 10, 10),
            transfer=50,
        )
        mission.assigned_tail_number = "N422UP"
        db.session.commit()

        started = start_follow_up_fuel_cycle(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "uplift",
            "55.0",
            self.fueler.id,
            nightly.fuel_truck_id,
        )
        self.assertEqual(started.fuel_work_state.tail_number, "N422UP")
        self.assertEqual(tuple(started.fuel_work_state.tank_states), ())

    def test_defuel_adds_gallons_sets_sump_and_holds_other_assignment(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        truck, nightly = self._truck(operation, assignment, "DEF-1", 300)
        self._complete_cycle(
            operation,
            assignment,
            remaining=(10, 10, 10),
            actual=(11, 10, 10),
            transfer=25,
        )
        _other_mission, other_assignment = self._assignment(
            operation,
            flight="UPS902",
            tail="N422UP",
        )
        other_assignment.assigned_truck_id = truck.id
        db.session.commit()

        start_follow_up_fuel_cycle(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "defuel",
            "25.0",
            self.fueler.id,
            truck.id,
        )
        db.session.commit()
        self._save_cycle(
            assignment,
            remaining=(10, 10, 10),
            actual=(8, 10, 10),
            transfer=40,
        )
        db.session.commit()
        mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()
        before_complete = self._revision(operation)
        completed = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        self.assertEqual(completed.revision, before_complete + 1)
        db.session.commit()

        event = NeoScorpionFuelingEvent.query.order_by(
            NeoScorpionFuelingEvent.sequence_number.desc()
        ).first()
        self.assertEqual(event.event_type, "defuel")
        self.assertEqual(event.transfer_fuel_gallons, 40)
        self.assertEqual(nightly.current_gallons, 315)
        self.assertEqual(nightly.status, "needs_sump")
        self.assertEqual(other_assignment.operational_status, "hold_review")
        context = fuel_dispatch_context(self.gateway, include_asset_choices=True)
        self.assertNotIn(truck.id, context["nightly_assignment_truck_ids"])
        other_row = next(
            row for row in context["rows"] if row["assignment"].id == other_assignment.id
        )
        self.assertEqual(other_row["projected_truck_display"], "-")
        self._login(self.dispatcher)
        asset_page = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertEqual(asset_page.status_code, 200)
        self.assertIn(b"NEEDS SUMP", asset_page.data)
        self.assertIn(b"MARK SUMPED", asset_page.data)

    def test_mark_sumped_requires_confirmed_gallons_and_does_not_auto_resume(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        truck, nightly = self._truck(operation, assignment, "SUMP-1", 200)
        nightly.status = "needs_sump"
        assignment.operational_status = "hold_review"
        db.session.commit()
        revision = self._revision(operation)

        with self.assertRaisesRegex(ValueError, "confirmed current gallons"):
            mark_nightly_truck_sumped(operation, truck, "")
        db.session.rollback()
        self.assertEqual(self._revision(operation), revision)

        self._login(self.dispatcher)
        manager_page = self.client.get("/neoscorpion/truck-manager")
        self.assertEqual(manager_page.status_code, 200)
        self.assertIn(b"NEEDS SUMP", manager_page.data)
        self.assertIn(b"MARK SUMPED", manager_page.data)
        response = self.client.post(
            "/neoscorpion/truck-manager",
            data={
                "action": "mark_sumped",
                "truck_id": truck.id,
                "current_gallons": "225",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        nightly = db.session.get(NeoScorpionSortTruck, nightly.id)
        assignment = db.session.get(NeoScorpionFuelAssignment, assignment.id)
        self.assertEqual(self._revision(operation), revision + 1)
        self.assertEqual(nightly.status, "available")
        self.assertEqual(nightly.current_gallons, 225)
        self.assertEqual(assignment.operational_status, "hold_review")

    def test_direction_mismatch_and_capacity_failure_are_atomic(self):
        for cycle_type, actual in (("uplift", (8, 10, 10)), ("defuel", (12, 10, 10))):
            operation = self._operation(day=date(2026, 8, 17 if cycle_type == "uplift" else 18))
            _mission, assignment = self._assignment(
                operation,
                flight=f"UPS-{cycle_type}",
                tail="N432UP" if cycle_type == "uplift" else "N442UP",
            )
            truck, nightly = self._truck(
                operation,
                assignment,
                f"DIR-{cycle_type}",
                990 if cycle_type == "defuel" else 300,
                capacity=1000,
            )
            self._complete_cycle(
                operation,
                assignment,
                remaining=(10, 10, 10),
                actual=(11, 10, 10),
                transfer=10,
            )
            start_follow_up_fuel_cycle(
                self.gateway,
                self.dispatcher,
                assignment.id,
                cycle_type,
                "40.0",
                self.fueler.id,
                truck.id,
            )
            db.session.commit()
            self._save_cycle(
                assignment,
                remaining=(10, 10, 10),
                actual=actual,
                transfer=20,
            )
            db.session.commit()
            mark_fueler_off(self.gateway, self.fueler, assignment.id)
            db.session.commit()
            revision = self._revision(operation)
            event_count = NeoScorpionFuelingEvent.query.count()
            gallons = nightly.current_gallons
            with self.assertRaisesRegex(ValueError, "opposite fuel direction"):
                complete_fueled_assignment(
                    self.gateway,
                    self.dispatcher,
                    assignment.id,
                )
            db.session.rollback()
            self.assertEqual(self._revision(operation), revision)
            self.assertEqual(NeoScorpionFuelingEvent.query.count(), event_count)
            self.assertEqual(nightly.current_gallons, gallons)

        assignment.current_cycle_type = "defuel"
        assignment.transfer_fuel_gallons = 30
        tank_states = NeoScorpionFuelTankState.query.all()
        for state in tank_states:
            state.actual_lbs = 8_000 if state.tank_code == "left" else 10_000
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "capacity"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

    def test_negative_transfer_fuel_is_rejected_without_revision(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            self._save_cycle(
                assignment,
                remaining=(10, 10, 10),
                actual=(11, 10, 10),
                transfer=-1,
            )
        db.session.rollback()
        self.assertEqual(self._revision(operation), 0)
        self.assertEqual(NeoScorpionFuelTankState.query.count(), 0)

    def test_a300_latest_center_actual_drives_load_planning_and_ui_hooks(self):
        operation = self._operation()
        mission, assignment = self._assignment(
            operation,
            flight="UPS0952",
            tail="N160UP",
            required=80_000,
            aircraft_type="A300",
        )
        truck, _nightly = self._truck(operation, assignment, "A300-1", 800)
        self._complete_a300_cycle(assignment, center_actual=12, transfer=25)
        start_follow_up_fuel_cycle(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "defuel",
            "70.0",
            self.fueler.id,
            truck.id,
        )
        db.session.commit()
        self._save_a300(assignment, center_actual=4, transfer=20, remaining=6)
        db.session.commit()
        mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()
        complete_fueled_assignment(self.gateway, self.dispatcher, assignment.id)
        db.session.commit()

        tail_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N160UP",
        ).one()
        self.assertEqual(tail_state.center_fuel_lbs, 4_000)
        row = next(
            row for row in fuel_dispatch_context(self.gateway)["rows"]
            if row["mission"].id == mission.id
        )
        self.assertIn("CF > 4000", row["load_planning_output"])

        self._login(self.dispatcher)
        dispatch = self.client.get("/neoscorpion/fuel-dispatch")
        self.assertEqual(dispatch.status_code, 200)
        self.assertIn(b"START UPLIFT", dispatch.data)
        self.assertIn(b"START DEFUEL", dispatch.data)

    def _operation(self, *, day=date(2026, 8, 17)):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(operation)
        db.session.flush()
        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=user.id,
                )
                for user in (self.fueler, self.other_fueler)
            ]
        )
        db.session.commit()
        return operation

    def _assignment(
        self,
        operation,
        *,
        flight="UPS901",
        tail="N412UP",
        required=50_000,
        aircraft_type="757",
    ):
        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight,
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            planned_fuel_load=required,
            assigned_tail_number=tail,
            tail_source="manual",
            fuel_status="assigned",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.fueler.id,
            confirmed_tail_number=tail,
            review_status="assigned",
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number=tail,
                    aircraft_type=aircraft_type,
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return mission, assignment

    def _truck(
        self,
        operation,
        assignment,
        number,
        current,
        *,
        capacity=1000,
    ):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=number,
            capacity_gallons=capacity,
            remaining_fuel_gallons=777,
        )
        db.session.add(truck)
        db.session.flush()
        nightly = NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status="available",
            starting_gallons=current,
            current_gallons=current,
        )
        assignment.assigned_truck_id = truck.id
        db.session.add(nightly)
        db.session.commit()
        return truck, nightly

    def _save_cycle(self, assignment, *, remaining, actual, transfer):
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            {
                "assignment_id": str(assignment.id),
                "apu_running": "no",
                "remaining_left": str(remaining[0]),
                "actual_left": str(actual[0]),
                "remaining_ctr": str(remaining[1]),
                "actual_ctr": str(actual[1]),
                "remaining_right": str(remaining[2]),
                "actual_right": str(actual[2]),
                "transfer_fuel_gallons": str(transfer),
                "notes": "",
            },
        )

    def _complete_cycle(self, operation, assignment, *, remaining, actual, transfer):
        self._save_cycle(
            assignment,
            remaining=remaining,
            actual=actual,
            transfer=transfer,
        )
        db.session.commit()
        off = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 0),
        )
        db.session.commit()
        complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 10),
        )
        db.session.commit()
        return off.fuel_work_state

    def _save_a300(self, assignment, *, center_actual, transfer, remaining=5):
        data = {
            "assignment_id": str(assignment.id),
            "apu_running": "no",
            "transfer_fuel_gallons": str(transfer),
            "notes": "",
        }
        for code in ("l_out", "l_in", "ctr", "r_in", "r_out", "tt"):
            data[f"remaining_{code}"] = str(remaining)
            data[f"actual_{code}"] = str(
                center_actual if code == "ctr" else remaining
            )
        return save_fueler_entry(self.gateway, self.fueler, data)

    def _complete_a300_cycle(self, assignment, *, center_actual, transfer):
        self._save_a300(
            assignment,
            center_actual=center_actual,
            transfer=transfer,
            remaining=10,
        )
        db.session.commit()
        mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.commit()
        complete_fueled_assignment(self.gateway, self.dispatcher, assignment.id)
        db.session.commit()

    def _revision(self, operation):
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).first()
        return int(state.revision if state else 0)

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
