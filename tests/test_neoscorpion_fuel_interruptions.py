import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    complete_fueled_assignment,
    confirm_assignment_tail,
    end_fuel_work_early,
    fuel_dispatch_context,
    mark_fueler_off,
    resume_held_fuel_assignment,
    save_dispatch_row,
    save_fueler_entry,
    save_truck,
    swap_assignment_fueler,
    swap_assignment_truck,
)
from app.services.neoscorpion_assets import (
    mark_nightly_truck_topping_off,
    remove_nightly_fueler,
    remove_nightly_truck,
    update_nightly_truck,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionFuelInterruptionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-interruption-test",
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
        self.fueler = self._add_user("interrupt_fueler", "operator")
        self.replacement = self._add_user("interrupt_replacement", "operator")
        self.dispatcher = self._add_user("interrupt_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_sync_adds_fields_and_expands_audit_actions(self):
        assignment_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_assignments"
            )
        }
        work_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_work_states"
            )
        }
        self.assertTrue(
            {
                "confirmed_tail_number",
                "operational_status",
                "hold_reason",
                "hold_at_utc",
                "hold_by_user_id",
            }.issubset(assignment_columns)
        )
        self.assertTrue(
            {
                "truck_segment_started_at_utc",
                "ended_early_at_utc",
                "ended_early_by_user_id",
                "ended_early_reason",
            }.issubset(work_columns)
        )
        self.assertIn(
            "operational_status",
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"],
        )
        self.assertIn(
            "ended_early_reason",
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_work_states"],
        )

        NeoScorpionFuelAuditEntry.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE neoscorpion_fuel_audit_entries (
                    id INTEGER PRIMARY KEY,
                    sort_date_operation_id INTEGER NOT NULL,
                    fuel_assignment_id INTEGER NOT NULL,
                    fuel_work_state_id INTEGER,
                    action VARCHAR(32) NOT NULL,
                    field_name VARCHAR(80),
                    old_value TEXT,
                    new_value TEXT,
                    reason TEXT NOT NULL,
                    changed_by_user_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT ck_neoscorpion_fuel_audit_entry_action
                        CHECK (action IN ('reopen_off', 'correct_actual'))
                )
                """
            )
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)
        create_sql = db.session.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='neoscorpion_fuel_audit_entries'"
            )
        ).scalar()
        self.assertIn("'auto_hold'", create_sql)
        self.assertIn("'end_early'", create_sql)

    def test_fueler_removal_holds_and_resume_is_explicit(self):
        operation, _mission, assignment = self._assignment()
        self._select_fueler(operation, self.fueler)
        db.session.commit()

        removed = remove_nightly_fueler(
            operation,
            self.fueler,
            changed_by_user=self.dispatcher,
            now_utc=datetime(2026, 8, 19, 3, 0),
        )
        self.assertTrue(removed.changed)
        self.assertEqual(removed.revision, 1)
        db.session.commit()
        db.session.refresh(assignment)
        self.assertEqual(assignment.operational_status, "hold_review")
        self.assertEqual(NeoScorpionFuelAuditEntry.query.one().action, "auto_hold")

        self._select_fueler(operation, self.fueler)
        db.session.commit()
        db.session.refresh(assignment)
        self.assertEqual(assignment.operational_status, "hold_review")

        resumed = resume_held_fuel_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        self.assertTrue(resumed.changed)
        db.session.commit()
        self.assertEqual(assignment.operational_status, "active")
        self.assertEqual(
            [entry.action for entry in NeoScorpionFuelAuditEntry.query.order_by(
                NeoScorpionFuelAuditEntry.id
            )],
            ["auto_hold", "resume_hold"],
        )

    def test_truck_disruption_holds_does_not_auto_clear_and_blocks_removal(self):
        operation, _mission, assignment = self._assignment()
        self._select_fueler(operation, self.fueler)
        old_truck, nightly = self._truck(operation, "INT-1", current_gallons=100)
        assignment.assigned_truck_id = old_truck.id
        db.session.commit()

        unavailable = update_nightly_truck(
            operation,
            old_truck,
            status="unavailable_oos",
            changed_by_user=self.dispatcher,
        )
        self.assertTrue(unavailable.changed)
        db.session.commit()
        self.assertEqual(assignment.operational_status, "hold_review")

        update_nightly_truck(
            operation,
            old_truck,
            status="available",
            starting_gallons=nightly.starting_gallons,
            current_gallons=nightly.current_gallons,
            changed_by_user=self.dispatcher,
        )
        db.session.commit()
        self.assertEqual(assignment.operational_status, "hold_review")
        resume_held_fuel_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()

        save_truck(
            self.gateway,
            {
                "truck_id": str(old_truck.id),
                "truck_number": old_truck.truck_number,
                "capacity_gallons": str(old_truck.capacity_gallons),
                "remaining_fuel_gallons": str(old_truck.remaining_fuel_gallons),
                "is_active": "1",
                "is_out_of_service": "1",
            },
            self.dispatcher,
        )
        db.session.commit()
        self.assertEqual(assignment.operational_status, "hold_review")

        save_truck(
            self.gateway,
            {
                "truck_id": str(old_truck.id),
                "truck_number": old_truck.truck_number,
                "capacity_gallons": str(old_truck.capacity_gallons),
                "remaining_fuel_gallons": str(old_truck.remaining_fuel_gallons),
                "is_active": "1",
            },
            self.dispatcher,
        )
        db.session.commit()
        self.assertEqual(assignment.operational_status, "hold_review")
        resume_held_fuel_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()

        topping = mark_nightly_truck_topping_off(
            operation,
            old_truck,
            changed_by_user=self.dispatcher,
        )
        self.assertTrue(topping.changed)
        db.session.commit()
        self.assertEqual(assignment.operational_status, "hold_review")
        with self.assertRaisesRegex(ValueError, "assigned to active fuel work"):
            remove_nightly_truck(operation, old_truck)
        db.session.rollback()
        self.assertIsNotNone(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation.id,
                fuel_truck_id=old_truck.id,
            ).first()
        )

    def test_held_work_blocks_fueler_and_swap_preserves_work(self):
        operation, _mission, assignment = self._assignment()
        self._select_fueler(operation, self.fueler)
        self._select_fueler(operation, self.replacement)
        work = self._save_work(assignment)
        db.session.commit()
        remove_nightly_fueler(
            operation,
            self.fueler,
            changed_by_user=self.dispatcher,
        )
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "HOLD / REVIEW REQUIRED"):
            save_fueler_entry(
                self.gateway,
                self.fueler,
                self._fuel_form(assignment, actual_left="12.0"),
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "HOLD / REVIEW REQUIRED"):
            mark_fueler_off(self.gateway, self.fueler, assignment.id)
        db.session.rollback()

        swapped = swap_assignment_fueler(
            self.gateway,
            self.dispatcher,
            assignment.id,
            self.replacement.id,
        )
        self.assertTrue(swapped.changed)
        self.assertEqual(swapped.fuel_work_state.id, work.id)
        self.assertIsNone(swapped.fueling_event)
        db.session.commit()
        self.assertEqual(assignment.assigned_fueler_user_id, self.replacement.id)
        self.assertEqual(assignment.operational_status, "active")
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual(
            NeoScorpionFuelAuditEntry.query.order_by(
                NeoScorpionFuelAuditEntry.id.desc()
            ).first().action,
            "swap_fueler",
        )

    def test_truck_swap_before_movement_has_no_event_or_deduction(self):
        operation, _mission, assignment = self._assignment()
        old_truck, old_nightly = self._truck(operation, "INT-OLD", 100)
        new_truck, new_nightly = self._truck(operation, "INT-NEW", 120)
        assignment.assigned_truck_id = old_truck.id
        work = self._save_work(
            assignment,
            actual_left="",
            actual_ctr="",
            actual_right="",
        )
        db.session.commit()

        swap_time = datetime(2026, 8, 19, 4, 0)
        result = swap_assignment_truck(
            self.gateway,
            self.dispatcher,
            assignment.id,
            new_truck.id,
            now_utc=swap_time,
        )
        self.assertTrue(result.changed)
        self.assertIsNone(result.fueling_event)
        db.session.commit()
        self.assertEqual(assignment.assigned_truck_id, new_truck.id)
        self.assertEqual(old_nightly.current_gallons, 100)
        self.assertEqual(new_nightly.current_gallons, 120)
        self.assertEqual(work.truck_segment_started_at_utc, swap_time)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_moved_truck_swap_and_later_complete_create_distinct_segments(self):
        operation, _mission, assignment = self._assignment()
        old_truck, old_nightly = self._truck(operation, "INT-M1", 100)
        new_truck, new_nightly = self._truck(operation, "INT-M2", 100)
        assignment.assigned_truck_id = old_truck.id
        work = self._save_work(assignment, transfer_gallons="10")
        db.session.commit()

        swap_time = datetime(2026, 8, 19, 4, 30)
        swapped = swap_assignment_truck(
            self.gateway,
            self.dispatcher,
            assignment.id,
            new_truck.id,
            now_utc=swap_time,
        )
        self.assertEqual(swapped.fueling_event.sequence_number, 1)
        db.session.commit()
        self.assertEqual(old_nightly.current_gallons, 90)
        self.assertIsNone(assignment.transfer_fuel_gallons)
        self.assertEqual(work.truck_segment_started_at_utc, swap_time)

        save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(assignment, transfer_gallons="7"),
        )
        db.session.commit()
        mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 19, 5, 30),
        )
        db.session.commit()
        completed = complete_fueled_assignment(
            self.gateway,
            self.dispatcher,
            assignment.id,
            now_utc=datetime(2026, 8, 19, 5, 45),
        )
        self.assertEqual(completed.fueling_event.sequence_number, 2)
        db.session.commit()
        events = NeoScorpionFuelingEvent.query.order_by(
            NeoScorpionFuelingEvent.sequence_number
        ).all()
        self.assertEqual([event.fuel_truck_id for event in events], [old_truck.id, new_truck.id])
        self.assertEqual([event.transfer_fuel_gallons for event in events], [10, 7])
        self.assertEqual(old_nightly.current_gallons, 90)
        self.assertEqual(new_nightly.current_gallons, 93)

    def test_tail_change_before_work_requires_confirmation(self):
        operation, mission, assignment = self._assignment()
        mission.assigned_tail_number = "N413UP"
        db.session.commit()

        row = fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(row["tail_safety_label"], "NEEDS RECONFIRMATION")
        with self.assertRaisesRegex(ValueError, "NEEDS RECONFIRMATION"):
            save_fueler_entry(
                self.gateway,
                self.fueler,
                self._fuel_form(assignment),
            )
        db.session.rollback()
        result = confirm_assignment_tail(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        self.assertTrue(result.changed)
        db.session.commit()
        self.assertEqual(assignment.confirmed_tail_number, "N413UP")
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)

    def test_midfuel_tail_change_end_early_then_new_tail_uses_new_work_state(self):
        operation, mission, assignment = self._assignment()
        self._select_fueler(operation, self.fueler)
        db.session.commit()
        old_work = self._save_work(
            assignment,
            actual_left="",
            actual_ctr="",
            actual_right="",
        )
        db.session.commit()
        mission.assigned_tail_number = "N413UP"
        db.session.commit()

        row = fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(row["tail_safety_label"], "HOLD / STOP & REVIEW")
        self.assertEqual(row["fuel_work_state"].id, old_work.id)
        with self.assertRaisesRegex(ValueError, "END EARLY"):
            confirm_assignment_tail(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        ended = end_fuel_work_early(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Aircraft changed during fueling.",
            now_utc=datetime(2026, 8, 19, 4, 30),
        )
        self.assertTrue(ended.changed)
        self.assertIsNone(ended.fueling_event)
        db.session.commit()
        self.assertIsNotNone(old_work.ended_early_at_utc)
        self.assertNotEqual(assignment.review_status, "complete")
        self.assertNotEqual(mission.fuel_status, "complete")

        confirm_assignment_tail(
            self.gateway,
            self.dispatcher,
            assignment.id,
        )
        db.session.commit()
        new_work = self._save_work(assignment)
        db.session.commit()
        self.assertNotEqual(new_work.id, old_work.id)
        self.assertEqual(new_work.tail_number, "N413UP")
        self.assertEqual(old_work.tail_number, "N412UP")

    def test_end_early_moved_deducts_once_and_partial_actual_blocks(self):
        operation, mission, assignment = self._assignment()
        truck, nightly = self._truck(operation, "INT-END", 100)
        assignment.assigned_truck_id = truck.id
        self._save_work(assignment, transfer_gallons="12")
        db.session.commit()
        mission.assigned_tail_number = "N413UP"
        db.session.commit()

        result = end_fuel_work_early(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Tail changed after fuel moved.",
            now_utc=datetime(2026, 8, 19, 4, 45),
        )
        self.assertEqual(result.fueling_event.sequence_number, 1)
        db.session.commit()
        self.assertEqual(nightly.current_gallons, 88)
        self.assertIsNone(assignment.completed_at_utc)
        self.assertEqual(assignment.operational_status, "hold_review")

        operation2, mission2, assignment2 = self._assignment(
            day=date(2026, 8, 18),
            flight_number="UPS1302",
        )
        self._save_work(
            assignment2,
            actual_left="11.0",
            actual_ctr="",
            actual_right="",
        )
        db.session.commit()
        mission2.assigned_tail_number = "N413UP"
        db.session.commit()
        revision = self._revision(operation2)
        with self.assertRaisesRegex(ValueError, "cannot be determined safely"):
            end_fuel_work_early(
                self.gateway,
                self.dispatcher,
                assignment2.id,
                "Partial readings.",
            )
        db.session.rollback()
        self.assertEqual(self._revision(operation2), revision)
        self.assertEqual(
            NeoScorpionFuelAuditEntry.query.filter_by(
                fuel_assignment_id=assignment2.id
            ).count(),
            0,
        )

    def test_generic_save_cannot_bypass_swap_and_completed_paths_are_immutable(self):
        operation, mission, assignment = self._assignment()
        self._save_work(assignment)
        self._select_fueler(operation, self.replacement)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "dedicated FUELER SWAP"):
            save_dispatch_row(
                self.gateway,
                self._dispatch_form(
                    mission,
                    assignment,
                    assigned_fueler_user_id=self.replacement.id,
                ),
            )
        db.session.rollback()

        assignment.completed_at_utc = datetime(2026, 8, 19, 6, 0)
        assignment.completed_by_user_id = self.dispatcher.id
        assignment.review_status = "complete"
        mission.fuel_status = "complete"
        db.session.commit()
        revision = self._revision(operation)
        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            swap_assignment_fueler(
                self.gateway,
                self.dispatcher,
                assignment.id,
                self.replacement.id,
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            end_fuel_work_early(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "Invalid completion mutation.",
            )
        db.session.rollback()
        self.assertEqual(self._revision(operation), revision)

    def test_gets_render_safety_controls_without_writes(self):
        operation, mission, assignment = self._assignment()
        self._save_work(assignment)
        db.session.commit()
        mission.assigned_tail_number = "N413UP"
        db.session.commit()
        self._login(self.dispatcher)
        audit_count = NeoScorpionFuelAuditEntry.query.count()
        revision = self._revision(operation)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fuel-dispatch")
            self.assertEqual(commit.call_count, 0)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"HOLD / STOP &amp; REVIEW", response.data)
        self.assertIn(b"CONFIRM NEW TAIL", response.data)
        self.assertIn(b"END EARLY", response.data)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), audit_count)
        self.assertEqual(self._revision(operation), revision)

    def _assignment(
        self,
        *,
        day=date(2026, 8, 17),
        flight_number="UPS1301",
        tail_number="N412UP",
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
            confirmed_tail_number=tail_number,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=day,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number=tail_number,
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return operation, mission, assignment

    def _select_fueler(self, operation, user):
        selection = NeoScorpionSortFueler(
            sort_date_operation_id=operation.id,
            user_id=user.id,
        )
        db.session.add(selection)
        return selection

    def _truck(self, operation, truck_number, current_gallons):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=truck_number,
            capacity_gallons=1000,
            remaining_fuel_gallons=900,
        )
        db.session.add(truck)
        db.session.flush()
        nightly = NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status="available",
            starting_gallons=current_gallons,
            current_gallons=current_gallons,
        )
        db.session.add(nightly)
        return truck, nightly

    def _save_work(self, assignment, **overrides):
        result = save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(assignment, **overrides),
            now_utc=datetime(2026, 8, 19, 3, 30),
        )
        return result.fuel_work_state

    @staticmethod
    def _fuel_form(
        assignment,
        *,
        actual_left="11.0",
        actual_ctr="10.0",
        actual_right="10.0",
        transfer_gallons="",
    ):
        return {
            "assignment_id": str(assignment.id),
            "apu_running": "no",
            "remaining_left": "10.0",
            "actual_left": actual_left,
            "remaining_ctr": "10.0",
            "actual_ctr": actual_ctr,
            "remaining_right": "10.0",
            "actual_right": actual_right,
            "transfer_fuel_gallons": transfer_gallons,
            "notes": "",
        }

    @staticmethod
    def _dispatch_form(mission, assignment, *, assigned_fueler_user_id=None):
        return {
            "mission_id": str(mission.id),
            "expected_assigned_fueler_user_id": str(
                assignment.assigned_fueler_user_id or ""
            ),
            "expected_assigned_truck_id": str(assignment.assigned_truck_id or ""),
            "assigned_fueler_user_id": str(assigned_fueler_user_id or ""),
            "assigned_truck_id": str(assignment.assigned_truck_id or ""),
            "required_fuel": "50.0",
            "inbound_fuel": "",
            "apu_lbs": "",
            "review_status": assignment.review_status,
            "load_planning_note": assignment.load_planning_note,
        }

    @staticmethod
    def _revision(operation):
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
