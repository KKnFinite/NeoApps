import unittest
from datetime import date, datetime
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
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
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoScorpionAssignmentIntegrationTest(unittest.TestCase):
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
        self.dispatcher = self._login_user("dispatcher", "simulator")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_dispatch_choices_are_current_nightly_assets_and_preserve_stale_assignments(self):
        operation, mission = self._add_operation_with_mission(date(2026, 8, 17))
        available_fueler = self._add_approved_user(
            "selected_fueler",
            "operator",
            first_name="Selected Fueler",
        )
        stale_fueler = self._add_approved_user(
            "removed_fueler",
            "operator",
            first_name="Removed Fueler",
        )
        unselected_fueler = self._add_approved_user(
            "unselected_fueler",
            "operator",
            first_name="Unselected Fueler",
        )
        available_truck = self._add_truck("TRUCK AVAILABLE")
        oos_truck = self._add_truck("TRUCK OOS")
        topping_truck = self._add_truck("TRUCK TOPPING")
        unselected_truck = self._add_truck("TRUCK UNSELECTED")
        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=available_fueler.id,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation.id,
                    fuel_truck_id=available_truck.id,
                    status="available",
                    starting_gallons=6000,
                    current_gallons=6000,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation.id,
                    fuel_truck_id=oos_truck.id,
                    status="unavailable_oos",
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation.id,
                    fuel_truck_id=topping_truck.id,
                    status="topping_off",
                    starting_gallons=5000,
                    current_gallons=4500,
                ),
                NeoScorpionFuelAssignment(
                    sort_date_operation_id=operation.id,
                    sort_date_mission_id=mission.id,
                    assigned_fueler_user_id=stale_fueler.id,
                    assigned_truck_id=oos_truck.id,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get("/neoscorpion/fuel-dispatch")
        dispatch_table = response.data.split(b"<tbody>", 1)[1].split(b"</tbody>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Selected Fueler", dispatch_table)
        self.assertNotIn(b"Unselected Fueler", dispatch_table)
        self.assertIn(b"Removed Fueler (Current - unavailable)", dispatch_table)
        self.assertIn(b"TRUCK AVAILABLE", dispatch_table)
        self.assertNotIn(b"TRUCK TOPPING", dispatch_table)
        self.assertNotIn(b"TRUCK UNSELECTED", dispatch_table)
        self.assertIn(b"TRUCK OOS (Current - unavailable)", dispatch_table)
        assignment = NeoScorpionFuelAssignment.query.filter_by(
            sort_date_mission_id=mission.id
        ).one()
        self.assertEqual(assignment.assigned_fueler_user_id, stale_fueler.id)
        self.assertEqual(assignment.assigned_truck_id, oos_truck.id)
        self.assertIsNotNone(unselected_fueler.id)
        self.assertIsNotNone(unselected_truck.id)

    def test_fueler_and_truck_are_independent_and_revision_changes_once(self):
        operation, mission = self._add_operation_with_mission(date(2026, 8, 17))
        fueler = self._add_approved_user("night_fueler", "operator")
        truck = self._add_truck("TRUCK 12", remaining=4321)
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=10,
        )
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
        )
        db.session.add_all(
            [
                state,
                assignment,
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation.id,
                    user_id=fueler.id,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation.id,
                    fuel_truck_id=truck.id,
                    status="available",
                    starting_gallons=6000,
                    current_gallons=6000,
                ),
                NeoScorpionTailFuelState(
                    sort_date_operation_id=operation.id,
                    tail_number=mission.assigned_tail_number,
                    inbound_fuel_lbs=13600,
                    apu_lbs=300,
                ),
            ]
        )
        db.session.commit()

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, fueler_id=fueler.id),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertEqual(assignment.assigned_fueler_user_id, fueler.id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(state.revision, 11)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                expected_fueler_id=fueler.id,
            ),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(state.revision, 12)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, truck_id=truck.id),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertEqual(assignment.assigned_truck_id, truck.id)
        self.assertEqual(state.revision, 13)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, expected_truck_id=truck.id),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(state.revision, 14)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                fueler_id=fueler.id,
                truck_id=truck.id,
            ),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertEqual(assignment.assigned_fueler_user_id, fueler.id)
        self.assertEqual(assignment.assigned_truck_id, truck.id)
        self.assertEqual(state.revision, 15)

        stale = self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                fueler_id=fueler.id,
                expected_fueler_id=fueler.id,
            ),
        )
        self.assertEqual(stale.status_code, 400)
        self.assertIn(b"Fuel assignment changed", stale.data)
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertEqual(assignment.assigned_truck_id, truck.id)
        self.assertEqual(state.revision, 15)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                expected_fueler_id=fueler.id,
                expected_truck_id=truck.id,
                fueler_id=fueler.id,
            ),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertEqual(assignment.assigned_fueler_user_id, fueler.id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(state.revision, 16)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                fueler_id=fueler.id,
                truck_id=truck.id,
                expected_fueler_id=fueler.id,
            ),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertEqual(assignment.assigned_fueler_user_id, fueler.id)
        self.assertEqual(assignment.assigned_truck_id, truck.id)
        self.assertEqual(state.revision, 17)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission,
                truck_id=truck.id,
                expected_fueler_id=fueler.id,
                expected_truck_id=truck.id,
            ),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertEqual(assignment.assigned_truck_id, truck.id)
        self.assertEqual(state.revision, 18)

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, expected_truck_id=truck.id),
        )
        db.session.refresh(assignment)
        db.session.refresh(state)
        self.assertIsNone(assignment.assigned_fueler_user_id)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(state.revision, 19)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            self.client.post(
                "/neoscorpion/fuel-dispatch",
                data=self._dispatch_form(mission),
            )
            self.assertEqual(commit.call_count, 0)
        db.session.refresh(state)
        db.session.refresh(truck)
        self.assertEqual(state.revision, 19)
        self.assertEqual(truck.remaining_fuel_gallons, 4321)

    def test_assignment_immediately_appears_only_for_assigned_operator(self):
        operation, mission = self._add_operation_with_mission(date(2026, 8, 17))
        assigned = self._add_approved_user("assigned_operator", "operator")
        other = self._add_approved_user("other_operator", "operator")
        db.session.add(
            NeoScorpionSortFueler(
                sort_date_operation_id=operation.id,
                user_id=assigned.id,
            )
        )
        db.session.commit()

        self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, fueler_id=assigned.id),
        )

        self._login_existing_user(assigned)
        assigned_page = self.client.get("/neoscorpion/fueler")
        self.assertEqual(assigned_page.status_code, 200)
        self.assertIn(mission.flight_number.encode(), assigned_page.data)
        self.assertNotIn(b"START JOB", assigned_page.data)

        self._login_existing_user(other)
        other_page = self.client.get("/neoscorpion/fueler")
        self.assertEqual(other_page.status_code, 200)
        self.assertNotIn(mission.flight_number.encode(), other_page.data)

    def test_stale_nightly_resources_are_rejected_without_revision_change(self):
        operation, mission = self._add_operation_with_mission(date(2026, 8, 17))
        fueler = self._add_approved_user("stale_fueler", "operator")
        truck = self._add_truck("TRUCK STALE")
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=7,
        )
        fueler_selection = NeoScorpionSortFueler(
            sort_date_operation_id=operation.id,
            user_id=fueler.id,
        )
        truck_selection = NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status="available",
            starting_gallons=5000,
            current_gallons=5000,
        )
        db.session.add_all([state, fueler_selection, truck_selection])
        db.session.commit()

        truck_selection.status = "topping_off"
        db.session.commit()
        stale_truck = self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, truck_id=truck.id),
        )
        self.assertEqual(stale_truck.status_code, 400)
        self.assertIn(b"currently topping off", stale_truck.data)
        self.assertIsNone(
            NeoScorpionFuelAssignment.query.filter_by(
                sort_date_mission_id=mission.id
            ).first()
        )
        db.session.refresh(state)
        self.assertEqual(state.revision, 7)

        db.session.delete(fueler_selection)
        db.session.commit()
        stale_fueler = self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, fueler_id=fueler.id),
        )
        self.assertEqual(stale_fueler.status_code, 400)
        self.assertIn(b"no longer selected for tonight", stale_fueler.data)
        db.session.refresh(state)
        self.assertEqual(state.revision, 7)

        membership = GatewayMembership.query.filter_by(
            user_id=fueler.id,
            gateway_id=self.gateway.id,
        ).one()
        GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=NeoNode.query.filter_by(code="scorpion").one().id,
        ).one().role = "watcher"
        db.session.add(
            NeoScorpionSortFueler(
                sort_date_operation_id=operation.id,
                user_id=fueler.id,
            )
        )
        db.session.commit()
        ineligible_fueler = self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(mission, fueler_id=fueler.id),
        )
        self.assertEqual(ineligible_fueler.status_code, 400)
        self.assertIn(b"no longer has Fuel Assignments access", ineligible_fueler.data)
        db.session.refresh(state)
        self.assertEqual(state.revision, 7)

    def test_assets_cannot_cross_sort_operations(self):
        operation_a, _mission_a = self._add_operation_with_mission(
            date(2026, 8, 17),
            flight_number="UPS401",
            tail_number="N401UP",
        )
        fueler = self._add_approved_user("sort_a_fueler", "operator")
        truck = self._add_truck("TRUCK A")
        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation_a.id,
                    user_id=fueler.id,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation_a.id,
                    fuel_truck_id=truck.id,
                    status="available",
                    starting_gallons=5000,
                    current_gallons=5000,
                ),
            ]
        )
        operation_b, mission_b = self._add_operation_with_mission(
            date(2026, 8, 18),
            flight_number="UPS402",
            tail_number="N402UP",
        )
        db.session.commit()

        response = self.client.post(
            "/neoscorpion/fuel-dispatch",
            data=self._dispatch_form(
                mission_b,
                fueler_id=fueler.id,
                truck_id=truck.id,
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"no longer selected for tonight", response.data)
        self.assertIsNone(
            NeoScorpionFuelAssignment.query.filter_by(
                sort_date_mission_id=mission_b.id
            ).first()
        )
        self.assertIsNone(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation_b.id
            ).first()
        )

    def _add_operation_with_mission(
        self,
        sort_date,
        *,
        flight_number="UPS400",
        tail_number="N400UP",
    ):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=sort_date,
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
            assigned_tail_number=tail_number,
            tail_source="manual",
            planned_fuel_load=50500,
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.add(
            SortDateTailState(
                sort_date=sort_date,
                gateway_code=self.gateway.code,
                sort_name="night",
                tail_number=tail_number,
                aircraft_type="A300",
                aircraft_type_source="derived",
            )
        )
        db.session.commit()
        return operation, mission

    def _add_truck(self, number, *, remaining=3000):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=number,
            capacity_gallons=8000,
            remaining_fuel_gallons=remaining,
            is_active=True,
        )
        db.session.add(truck)
        db.session.flush()
        return truck

    def _dispatch_form(
        self,
        mission,
        *,
        fueler_id=None,
        truck_id=None,
        expected_fueler_id=None,
        expected_truck_id=None,
    ):
        return {
            "mission_id": str(mission.id),
            "expected_assigned_fueler_user_id": str(expected_fueler_id or ""),
            "expected_assigned_truck_id": str(expected_truck_id or ""),
            "required_fuel": "50.5",
            "inbound_fuel": "13.6",
            "apu_lbs": "300",
            "assigned_fueler_user_id": str(fueler_id or ""),
            "assigned_truck_id": str(truck_id or ""),
            "review_status": "pending",
            "load_planning_note": "",
        }

    def _login_user(self, username, role):
        user = self._add_approved_user(username, role)
        db.session.commit()
        self._login_existing_user(user)
        return user

    def _login_existing_user(self, user):
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )

    def _add_approved_user(self, username, role, *, first_name=None):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name=first_name or username.replace("_", " ").title(),
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


if __name__ == "__main__":
    unittest.main()
