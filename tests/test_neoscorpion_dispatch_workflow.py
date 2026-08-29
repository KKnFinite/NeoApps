import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    NeoScorpionTailFuelState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoScorpionDispatchWorkflowTest(unittest.TestCase):
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
        self.dispatcher = self._add_user("dispatch_workflow", "simulator")
        db.session.commit()
        self._login(self.dispatcher)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_required_and_inbound_autosave_preserve_active_cycle_and_adopt_revision(self):
        operation, mission = self._operation_and_mission()
        fueler = self._add_user("autosave_fueler", "operator")
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=fueler.id,
            review_status="assigned",
        )
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=4,
        )
        tail_state = NeoScorpionTailFuelState(
            sort_date_operation_id=operation.id,
            tail_number=mission.assigned_tail_number,
            inbound_fuel_lbs=12000,
        )
        db.session.add_all([assignment, state, tail_state])
        db.session.flush()
        work_state = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number=mission.assigned_tail_number,
            on_at_utc=datetime(2026, 8, 19, 1, 0),
        )
        db.session.add(work_state)
        db.session.commit()

        required = self._autosave(
            mission,
            "required_fuel",
            "51.2",
            expected="50.5",
        )
        self.assertEqual(required.status_code, 200)
        self.assertEqual(
            required.get_json()["revision"],
            5,
        )
        self.assertTrue(required.get_json()["changed"])
        self.assertEqual(required.get_json()["display_value"], "51.2")
        db.session.refresh(mission)
        db.session.refresh(assignment)
        db.session.refresh(work_state)
        self.assertEqual(mission.planned_fuel_load, 51200)
        self.assertEqual(work_state.on_at_utc, datetime(2026, 8, 19, 1, 0))
        self.assertEqual(assignment.fueler_update_version, 1)
        self.assertIn("Required Fuel: 50.5 K LBS -> 51.2 K LBS", assignment.fueler_update_message)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            unchanged = self._autosave(
                mission,
                "required_fuel",
                "51.2",
                expected="51.2",
            )
            self.assertEqual(commit.call_count, 0)
        self.assertFalse(unchanged.get_json()["changed"])
        self.assertEqual(unchanged.get_json()["revision"], 5)

        inbound = self._autosave(
            mission,
            "inbound_fuel",
            "13.0",
            expected="12.0",
        )
        self.assertEqual(inbound.status_code, 200)
        self.assertEqual(inbound.get_json()["revision"], 6)
        db.session.refresh(tail_state)
        db.session.refresh(assignment)
        self.assertEqual(tail_state.inbound_fuel_lbs, 13000)
        self.assertEqual(assignment.fueler_update_version, 2)
        self.assertIn("Inbound Fuel: 12.0 K LBS -> 13.0 K LBS", assignment.fueler_update_message)

    @patch("app.services.neoscorpion.current_sort_operation")
    def test_assign_and_update_assignment_use_json_without_navigation(
        self,
        current_sort_operation,
    ):
        operation, mission = self._operation_and_mission()
        current_sort_operation.return_value = operation
        fueler = self._add_user("assignment_fueler", "operator")
        truck_a = self._truck("TRUCK A")
        truck_b = self._truck("TRUCK B")
        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=fueler.id,
                ),
                self._nightly_truck(operation, truck_a),
                self._nightly_truck(operation, truck_b),
            ]
        )
        db.session.commit()

        page = self.client.get("/neoscorpion/fuel-dispatch").get_data(as_text=True)
        self.assertIn("data-dispatch-autosave", page)
        self.assertIn(">ASSIGN</button>", page)
        assigned = self._save_assignment(mission, fueler_id=fueler.id)
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.get_json()["button_label"], "UPDATE ASSIGNMENT")
        self.assertEqual(assigned.get_json()["revision"], 1)
        assignment = NeoScorpionFuelAssignment.query.filter_by(
            sort_date_mission_id=mission.id
        ).one()
        self.assertEqual(assignment.assigned_fueler_user_id, fueler.id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(assignment.fueler_update_version, 0)

        added_truck = self._save_assignment(
            mission,
            assignment=assignment,
            fueler_id=fueler.id,
            truck_id=truck_a.id,
        )
        self.assertEqual(added_truck.status_code, 200)
        self.assertEqual(added_truck.get_json()["revision"], 2)
        db.session.refresh(assignment)
        self.assertEqual(assignment.assigned_truck_id, truck_a.id)

        updated = self._save_assignment(
            mission,
            assignment=assignment,
            fueler_id=fueler.id,
            truck_id=truck_b.id,
            review_status="review",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["revision"], 3)
        db.session.refresh(assignment)
        self.assertEqual(assignment.assigned_truck_id, truck_b.id)
        self.assertEqual(assignment.review_status, "review")
        self.assertEqual(assignment.fueler_update_version, 2)
        self.assertIn("Truck:", assignment.fueler_update_message)

    @patch("app.services.neoscorpion.current_sort_operation")
    def test_read_only_user_cannot_update_a_sent_assignment(
        self,
        current_sort_operation,
    ):
        operation, mission = self._operation_and_mission()
        current_sort_operation.return_value = operation
        fueler = self._add_user("sent_assignment_fueler", "operator")
        truck = self._truck("SENT ASSIGNMENT TRUCK")
        read_only_user = self._add_user("sent_assignment_viewer", "watcher")
        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=fueler.id,
                ),
                self._nightly_truck(operation, truck),
            ]
        )
        db.session.commit()

        sent = self._save_assignment(mission, fueler_id=fueler.id)
        self.assertEqual(sent.status_code, 200)
        assignment = NeoScorpionFuelAssignment.query.filter_by(
            sort_date_mission_id=mission.id
        ).one()

        self._login(read_only_user)
        denied = self._save_assignment(
            mission,
            assignment=assignment,
            fueler_id=fueler.id,
            truck_id=truck.id,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.get_json()["error"], "Access denied.")
        db.session.refresh(assignment)
        self.assertIsNone(assignment.assigned_truck_id)

    def test_fueler_acknowledges_current_update_and_later_change_alerts_again(self):
        operation, mission = self._operation_and_mission()
        fueler = self._add_user("ack_fueler", "operator")
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=fueler.id,
            review_status="assigned",
            fueler_update_version=1,
            fueler_update_message="Required Fuel: 50.0 K LBS -> 50.5 K LBS",
            fueler_update_at_utc=datetime(2026, 8, 19, 1, 0),
        )
        db.session.add_all(
            [
                assignment,
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation.id,
                    revision=1,
                ),
            ]
        )
        db.session.commit()
        self._login(fueler)

        page = self.client.get("/neoscorpion/fueler").get_data(as_text=True)
        self.assertIn("ASSIGNMENT UPDATED", page)
        self.assertIn("ACKNOWLEDGE CHANGES", page)
        acknowledged = self.client.post(
            "/neoscorpion/fuel-assignments/acknowledge-update",
            data={
                "assignment_id": str(assignment.id),
                "update_version": "1",
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self.assertEqual(acknowledged.status_code, 200)
        self.assertTrue(acknowledged.get_json()["changed"])
        db.session.refresh(assignment)
        self.assertEqual(assignment.fueler_update_acknowledged_version, 1)
        self.assertNotIn(
            "ASSIGNMENT UPDATED",
            self.client.get("/neoscorpion/fueler").get_data(as_text=True),
        )

        self._login(self.dispatcher)
        later = self._autosave(
            mission,
            "required_fuel",
            "51.0",
            expected="50.5",
        )
        self.assertEqual(later.status_code, 200)
        db.session.refresh(assignment)
        self.assertEqual(assignment.fueler_update_version, 2)
        self.assertEqual(assignment.fueler_update_acknowledged_version, 1)
        self._login(fueler)
        self.assertIn(
            "ASSIGNMENT UPDATED",
            self.client.get("/neoscorpion/fueler").get_data(as_text=True),
        )

    def test_dispatcher_apu_override_updates_the_assigned_fueler(self):
        operation, mission = self._operation_and_mission()
        fueler = self._add_user("override_fueler", "operator")
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=fueler.id,
            review_status="assigned",
        )
        db.session.add(assignment)
        db.session.flush()
        db.session.add_all(
            [
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation.id,
                    revision=2,
                ),
                NeoScorpionTailFuelState(
                    sort_date_operation_id=operation.id,
                    tail_number=mission.assigned_tail_number,
                    apu_lbs=450,
                ),
                NeoScorpionFuelWorkState(
                    fuel_assignment_id=assignment.id,
                    tail_number=mission.assigned_tail_number,
                    apu_running=True,
                    apu_confirmed_at_utc=datetime(2026, 8, 20, 1, 0),
                    apu_allowance_lbs=450,
                    automatic_apu_allowance_lbs=450,
                    applied_apu_rate_thousand_lbs_per_hour=Decimal("0.30"),
                ),
            ]
        )
        db.session.commit()

        response = self._save_assignment(
            mission,
            assignment=assignment,
            fueler_id=fueler.id,
            extra={
                "apu_override_present": "1",
                "apu_override_enabled": "1",
                "apu_override_allowance": "0.60",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["revision"], 3)
        db.session.refresh(assignment)
        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id,
            tail_number=mission.assigned_tail_number,
        ).one()
        self.assertTrue(work.apu_override_enabled)
        self.assertEqual(work.apu_override_allowance_lbs, 600)
        self.assertEqual(work.apu_allowance_lbs, 600)
        self.assertEqual(assignment.fueler_update_version, 1)
        self.assertIn("APU Override", assignment.fueler_update_message)

    def test_fueler_reassignment_routes_job_without_old_update_notice(self):
        operation, mission = self._operation_and_mission()
        old_fueler = self._add_user("old_assignment_fueler", "operator")
        new_fueler = self._add_user("new_assignment_fueler", "operator")
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=old_fueler.id,
            review_status="assigned",
            fueler_update_version=3,
            fueler_update_message="Older pending update",
        )
        db.session.add_all(
            [
                assignment,
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=old_fueler.id,
                ),
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=new_fueler.id,
                ),
            ]
        )
        db.session.commit()

        response = self._save_assignment(
            mission,
            assignment=assignment,
            fueler_id=new_fueler.id,
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(assignment)
        self.assertEqual(assignment.assigned_fueler_user_id, new_fueler.id)
        self.assertEqual(
            assignment.fueler_update_acknowledged_version,
            assignment.fueler_update_version,
        )
        self.assertIsNone(assignment.fueler_update_message)

        self._login(old_fueler)
        self.assertNotIn(
            mission.flight_number,
            self.client.get("/neoscorpion/fueler").get_data(as_text=True),
        )
        self._login(new_fueler)
        new_page = self.client.get("/neoscorpion/fueler").get_data(as_text=True)
        self.assertIn(mission.flight_number, new_page)
        self.assertNotIn("ASSIGNMENT UPDATED", new_page)

    def test_stale_field_and_completed_sort_state_fail_without_revision_change(self):
        operation, mission = self._operation_and_mission()
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=7,
        )
        db.session.add(state)
        db.session.commit()

        stale = self._autosave(
            mission,
            "required_fuel",
            "51.0",
            expected="49.0",
        )
        self.assertEqual(stale.status_code, 400)
        self.assertIn("Live data changed", stale.get_json()["error"])
        db.session.refresh(state)
        db.session.refresh(mission)
        self.assertEqual(state.revision, 7)
        self.assertEqual(mission.planned_fuel_load, 50500)

        mission.fuel_status = "complete"
        db.session.commit()
        completed = self._autosave(
            mission,
            "required_fuel",
            "51.0",
            expected="50.5",
        )
        self.assertEqual(completed.status_code, 400)
        self.assertIn("Completed fuel assignments cannot be edited", completed.get_json()["error"])
        db.session.refresh(state)
        self.assertEqual(state.revision, 7)

    def test_autosave_uses_bounded_current_row_queries(self):
        _operation, mission = self._operation_and_mission()
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            response = self._autosave(
                mission,
                "required_fuel",
                "51.0",
                expected="50.5",
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(response.status_code, 200)
        assignment_reads = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
            and "neoscorpion_fuel_assignments" in statement.lower()
        ]
        mission_reads = [
            statement
            for statement in statements
            if statement.lstrip().upper().startswith("SELECT")
            and "sort_date_missions" in statement.lower()
        ]
        self.assertLessEqual(len(assignment_reads), 1)
        self.assertLessEqual(len(mission_reads), 2)

    def _operation_and_mission(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 19),
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
            flight_number="UPS519",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 19, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 20, 4, 30),
            planned_source="manual",
            assigned_tail_number="N159UP",
            tail_source="manual",
            planned_fuel_load=50500,
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.commit()
        return operation, mission

    def _autosave(self, mission, field_name, value, *, expected):
        return self.client.post(
            "/neoscorpion/fuel-dispatch/autosave",
            data={
                "mission_id": str(mission.id),
                "field_name": field_name,
                "value": value,
                "expected_value": expected,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    def _save_assignment(
        self,
        mission,
        *,
        assignment=None,
        fueler_id=None,
        truck_id=None,
        review_status=None,
        extra=None,
    ):
        data = {
            "mission_id": str(mission.id),
            "assignment_id": str(assignment.id if assignment else ""),
            "expected_assigned_fueler_user_id": str(
                assignment.assigned_fueler_user_id
                if assignment and assignment.assigned_fueler_user_id
                else ""
            ),
            "expected_assigned_truck_id": str(
                assignment.assigned_truck_id
                if assignment and assignment.assigned_truck_id
                else ""
            ),
            "assigned_fueler_user_id": str(fueler_id or ""),
            "assigned_truck_id": str(truck_id or ""),
            "review_status": review_status or ("assigned" if fueler_id or truck_id else "pending"),
            "load_planning_note": "",
        }
        data.update(extra or {})
        return self.client.post(
            "/neoscorpion/fuel-dispatch/assignment",
            data=data,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    def _truck(self, number):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=number,
            capacity_gallons=8000,
            is_active=True,
        )
        db.session.add(truck)
        db.session.flush()
        return truck

    @staticmethod
    def _nightly_truck(operation, truck):
        return NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status="available",
            starting_gallons=6000,
            current_gallons=6000,
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
        node = NeoNode.query.filter_by(code="scorpion").one()
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
                    node_id=node.id,
                    role=role,
                    is_active=True,
                ),
            ]
        )
        return user

    def _login(self, user):
        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
