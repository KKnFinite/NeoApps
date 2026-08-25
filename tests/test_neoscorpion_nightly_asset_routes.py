import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    GatewaySortMatrix,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    PermissionRule,
    PortalAppAccess,
    SortDateOperation,
    SortDateMission,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoScorpionNightlyAssetRoutesTest(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_read_only_dispatch_shows_unset_without_creating_assets(self):
        self._login_user("dispatcher", "simulator")
        self._add_operation(date(2026, 8, 17))
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip().upper())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
                response = self.client.get("/neoscorpion/fuel-dispatch")
                self.assertEqual(commit.call_count, 0)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MANAGE TONIGHT'S ASSETS", response.data)
        self.assertIn(b"ASSETS NOT SET", response.data)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)
        self.assertEqual(NeoScorpionSortFueler.query.count(), 0)
        self.assertEqual(NeoScorpionSortTruck.query.count(), 0)
        self.assertFalse(
            any(statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
        )

    def test_readiness_zero_complete_setup_and_sort_isolation(self):
        user = self._login_user("ready_dispatcher", "simulator")
        operation_a = self._add_operation(date(2026, 8, 17))
        truck = self._add_truck("TRUCK 7", capacity=8000)
        db.session.add(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operation_a.id,
                fuel_island_count=0,
                revision=1,
            )
        )
        db.session.commit()

        partial = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertIn(b"ASSETS PARTIALLY SET", partial.data)
        self.assertIn(b">0<", partial.data)

        db.session.add_all(
            [
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation_a.id,
                    user_id=user.id,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation_a.id,
                    fuel_truck_id=truck.id,
                    status="available",
                    starting_gallons=6000,
                    current_gallons=6000,
                ),
            ]
        )
        db.session.commit()
        complete = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertIn(b"ASSETS SET", complete.data)
        self.assertIn(b"TRUCK 7", complete.data)

        self._add_operation(date(2026, 8, 18))
        next_sort = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertIn(b"ASSETS NOT SET", next_sort.data)
        self.assertNotIn(b"6000 gal", next_sort.data)
        self.assertEqual(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation_a.id,
            ).count(),
            1,
        )

    def test_operator_cannot_mutate_nightly_assets(self):
        self._login_user("operator", "operator")
        self._add_operation(date(2026, 8, 17))

        response = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "set_islands", "fuel_island_count": "2"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)

    def test_simulator_island_mutation_commits_once_and_rejects_invalid_value(self):
        self._login_user("island_dispatcher", "simulator")
        operation = self._add_operation(date(2026, 8, 17))

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data={"action": "set_islands", "fuel_island_count": "0"},
            )
            self.assertEqual(commit.call_count, 1)

        self.assertEqual(response.status_code, 302)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id,
        ).one()
        self.assertEqual(state.fuel_island_count, 0)
        self.assertEqual(state.revision, 1)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data={"action": "set_islands", "fuel_island_count": "0"},
            )
            self.assertEqual(commit.call_count, 0)

        invalid = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "set_islands", "fuel_island_count": "5"},
            follow_redirects=True,
        )
        db.session.refresh(state)
        self.assertIn(b"between 0 and 4", invalid.data)
        self.assertEqual(state.fuel_island_count, 0)
        self.assertEqual(state.revision, 1)

    def test_fueler_choices_follow_permission_and_add_remove_is_bounded(self):
        self._login_user("fueler_dispatcher", "simulator")
        operation = self._add_operation(date(2026, 8, 17))
        eligible = self._add_approved_user("eligible_fueler", "operator")
        ineligible = self._add_approved_user("watcher_fueler", "watcher")
        db.session.commit()

        page = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertIn(b"eligible_fueler", page.data)
        self.assertNotIn(b"watcher_fueler", page.data)

        added = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "add_fueler", "user_id": str(eligible.id)},
            follow_redirects=True,
        )
        self.assertIn(b"TONIGHT&#39;S ASSETS UPDATED", added.data)
        self.assertIsNotNone(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=operation.id,
                user_id=eligible.id,
            ).first()
        )

        denied = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "add_fueler", "user_id": str(ineligible.id)},
            follow_redirects=True,
        )
        self.assertIn(b"eligible NeoScorpion fueler", denied.data)
        self.assertIsNone(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=operation.id,
                user_id=ineligible.id,
            ).first()
        )

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "remove_fueler", "user_id": str(eligible.id)},
        )
        self.assertIsNone(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=operation.id,
                user_id=eligible.id,
            ).first()
        )

        rule = PermissionRule.query.filter_by(
            permission_key="neoscorpion.fuel_assignments.view",
        ).one()
        rule.minimum_role = "simulator"
        db.session.commit()
        raised_threshold = self.client.get("/neoscorpion/fuel-dispatch?assets=open")
        self.assertNotIn(b"eligible_fueler", raised_threshold.data)

    def test_truck_selection_validation_top_off_and_removal(self):
        self._login_user("truck_dispatcher", "simulator")
        operation = self._add_operation(date(2026, 8, 17))
        truck = self._add_truck("TRUCK 8", capacity=8000, remaining=4321)
        oos_truck = self._add_truck("TRUCK 9", capacity=7000, remaining=3000)
        db.session.commit()

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "add_truck",
                "fuel_truck_id": str(truck.id),
                "status": "available",
                "starting_gallons": "6000",
                "current_gallons": "6000",
            },
        )
        selected = NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
        ).one()
        self.assertEqual(selected.status, "available")
        self.assertEqual(selected.starting_gallons, 6000)
        self.assertEqual(selected.current_gallons, 6000)

        over_capacity = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "add_truck",
                "fuel_truck_id": str(oos_truck.id),
                "status": "available",
                "starting_gallons": "9000",
                "current_gallons": "9000",
            },
            follow_redirects=True,
        )
        self.assertIn(b"cannot exceed truck capacity", over_capacity.data)
        self.assertIsNone(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation.id,
                fuel_truck_id=oos_truck.id,
            ).first()
        )

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "add_truck",
                "fuel_truck_id": str(oos_truck.id),
                "status": "unavailable_oos",
                "starting_gallons": "",
                "current_gallons": "",
            },
        )
        self.assertEqual(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation.id,
                fuel_truck_id=oos_truck.id,
            ).one().status,
            "unavailable_oos",
        )

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "mark_topping_off", "fuel_truck_id": str(truck.id)},
        )
        db.session.refresh(selected)
        self.assertEqual(selected.status, "topping_off")

        missing_gallons = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "complete_top_off",
                "fuel_truck_id": str(truck.id),
                "current_gallons": "",
            },
            follow_redirects=True,
        )
        db.session.refresh(selected)
        self.assertIn(b"Enter current gallons", missing_gallons.data)
        self.assertEqual(selected.status, "topping_off")

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "complete_top_off",
                "fuel_truck_id": str(truck.id),
                "current_gallons": "7000",
            },
        )
        db.session.refresh(selected)
        self.assertEqual(selected.status, "available")
        self.assertEqual(selected.starting_gallons, 6000)
        self.assertEqual(selected.current_gallons, 7000)

        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "remove_truck", "fuel_truck_id": str(truck.id)},
        )
        self.assertIsNone(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation.id,
                fuel_truck_id=truck.id,
            ).first()
        )
        db.session.refresh(truck)
        self.assertEqual(truck.remaining_fuel_gallons, 4321)
        self.assertTrue(truck.is_active)

    def test_dispatch_truck_card_actions_stay_on_dispatch_and_are_safe_to_repeat(self):
        self._login_user("card_truck_dispatcher", "simulator")
        operation = self._add_operation(date(2026, 8, 17))
        timeline_settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add_all(
            [
                timeline_settings,
                SortTimelineSortSetting(
                    timeline_settings=timeline_settings,
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    planning_start_local=time(18, 0),
                    sort_window_end_local=time(8, 0),
                ),
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    day_of_week="monday",
                    sort_name="night",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026, 8, 17, 22, 0
        )
        truck = self._add_truck("TRUCK CARD", capacity=9500)
        db.session.commit()
        self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={
                "action": "add_truck",
                "fuel_truck_id": str(truck.id),
                "status": "available",
                "starting_gallons": "9500",
                "current_gallons": "9500",
            },
        )
        selected = NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
        ).one()
        card_data = {
            "dispatch_truck_card": "1",
            "fuel_truck_id": str(truck.id),
        }
        headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }

        topped_off = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "mark_topping_off"},
        )
        self.assertEqual(topped_off.status_code, 200)
        self.assertEqual(topped_off.content_type, "application/json")
        self.assertNotIn("Location", topped_off.headers)
        self.assertEqual(topped_off.json, {"ok": True, "changed": True, "revision": 2})
        db.session.refresh(selected)
        self.assertEqual(selected.status, "topping_off")

        duplicate_top_off = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "mark_topping_off"},
            headers=headers,
        )
        self.assertEqual(duplicate_top_off.status_code, 200)
        self.assertEqual(
            duplicate_top_off.json,
            {"ok": True, "changed": False, "revision": 2},
        )

        missing_gallons = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "complete_top_off", "current_gallons": ""},
            headers=headers,
        )
        self.assertEqual(missing_gallons.status_code, 400)
        self.assertIn("Enter current gallons", missing_gallons.json["error"])
        over_capacity = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "complete_top_off", "current_gallons": "9501"},
            headers=headers,
        )
        self.assertEqual(over_capacity.status_code, 400)
        self.assertIn("capacity", over_capacity.json["error"])

        returned = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "complete_top_off", "current_gallons": "7000"},
            headers=headers,
        )
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.json, {"ok": True, "changed": True, "revision": 3})
        db.session.refresh(selected)
        self.assertEqual(selected.status, "available")
        self.assertEqual(selected.current_gallons, 7000)

        duplicate_return = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "complete_top_off", "current_gallons": "7000"},
            headers=headers,
        )
        self.assertEqual(duplicate_return.status_code, 400)
        self.assertIn("not currently topping off", duplicate_return.json["error"])

        completed_mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS CARD",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_source="manual",
            fuel_status="waiting",
        )
        db.session.add(completed_mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=completed_mission.id,
            assigned_truck_id=truck.id,
            review_status="assigned",
        )
        db.session.add(assignment)
        db.session.commit()

        future_assignment = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "mark_topping_off"},
            headers=headers,
        )
        self.assertEqual(future_assignment.status_code, 400)
        self.assertEqual(
            future_assignment.json,
            {"ok": False, "error": "Truck has a future assigned job."},
        )

        db.session.add(
            NeoScorpionFuelWorkState(
                fuel_assignment_id=assignment.id,
                tail_number="N901UP",
                on_at_utc=datetime(2026, 8, 18, 2, 0),
            )
        )
        db.session.commit()
        actively_fueling = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "mark_topping_off"},
            headers=headers,
        )
        self.assertEqual(actively_fueling.status_code, 400)
        self.assertEqual(
            actively_fueling.json,
            {"ok": False, "error": "Truck is actively fueling."},
        )

        assignment.completed_at_utc = datetime(2026, 8, 18, 3, 0)
        assignment.review_status = "complete"
        completed_mission.fuel_status = "complete"
        db.session.commit()
        completed_prior = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={**card_data, "action": "mark_topping_off"},
        )
        self.assertEqual(completed_prior.status_code, 200)
        self.assertEqual(
            completed_prior.json,
            {"ok": True, "changed": True, "revision": 4},
        )
        self.assertNotIn("Location", completed_prior.headers)

        oos_truck = self._add_truck("TRUCK OOS", capacity=8000)
        sump_truck = self._add_truck("TRUCK SUMP", capacity=8000)
        db.session.commit()
        for blocked_truck, blocked_status, expected_error in (
            (oos_truck, "unavailable_oos", "Truck is unavailable / OOS."),
            (sump_truck, "needs_sump", "MARK SUMPED before changing this truck's status."),
        ):
            db.session.add(
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation.id,
                    fuel_truck_id=blocked_truck.id,
                    status=blocked_status,
                )
            )
            db.session.commit()
            blocked = self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data={
                    "action": "mark_topping_off",
                    "dispatch_truck_card": "1",
                    "fuel_truck_id": str(blocked_truck.id),
                },
                headers=headers,
            )
            self.assertEqual(blocked.status_code, 400)
            self.assertEqual(blocked.json, {"ok": False, "error": expected_error})

        ordinary_assets = self.client.post(
            "/neoscorpion/fuel-dispatch/assets",
            data={"action": "set_islands", "fuel_island_count": "1"},
        )
        self.assertEqual(ordinary_assets.status_code, 302)
        self.assertEqual(
            ordinary_assets.headers["Location"],
            "/neoscorpion/fuel-dispatch?assets=open#manage-tonights-assets",
        )

    def test_dispatch_truck_card_failures_keep_the_json_contract(self):
        self._login_user("card_truck_failures", "simulator")
        operation = self._add_operation(date(2026, 8, 17))
        card_data = {
            "action": "mark_topping_off",
            "dispatch_truck_card": "1",
            "fuel_truck_id": "41",
        }

        with patch(
            "app.neonodes.neoscorpion.routes.permission_access",
            return_value={"can_view": False, "can_edit": False},
        ):
            denied = self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data=card_data,
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.content_type, "application/json")
        self.assertEqual(denied.json, {"ok": False, "error": "Access denied."})
        self.assertNotIn("Location", denied.headers)

        with patch(
            "app.neonodes.neoscorpion.routes.permission_access",
            return_value={"can_view": True, "can_edit": False},
        ):
            read_only = self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data=card_data,
            )
        self.assertEqual(read_only.status_code, 403)
        self.assertEqual(read_only.content_type, "application/json")
        self.assertEqual(read_only.json, {"ok": False, "error": "Access denied."})
        self.assertNotIn("Location", read_only.headers)

        with (
            patch(
                "app.neonodes.neoscorpion.routes.current_sort_operation",
                return_value=operation,
            ),
            patch(
                "app.neonodes.neoscorpion.routes._apply_nightly_asset_action",
                side_effect=RuntimeError("production-like database failure"),
            ),
            self.assertLogs(self.app.logger.name, level="ERROR") as logs,
        ):
            unexpected = self.client.post(
                "/neoscorpion/fuel-dispatch/assets",
                data=card_data,
            )
        self.assertEqual(unexpected.status_code, 500)
        self.assertEqual(unexpected.content_type, "application/json")
        self.assertEqual(
            unexpected.json,
            {"ok": False, "error": "Truck update failed on the server."},
        )
        self.assertNotIn("Location", unexpected.headers)
        self.assertIn("operation_id=", logs.output[0])
        self.assertIn("truck_id=41", logs.output[0])
        self.assertIn("action=mark_topping_off", logs.output[0])

    def test_dispatch_truck_card_js_uses_unclobbered_form_action_attribute(self):
        script = Path(
            "app/static/js/neoscorpion_fuel_dispatch_live.js"
        ).read_text(encoding="utf-8")
        truck_card_block = script.split(
            "const submitTruckCardAction", 1
        )[1].split("root.addEventListener", 1)[0]

        self.assertIn('form.getAttribute("action")', truck_card_block)
        self.assertIn("fetch(actionUrl,", truck_card_block)
        self.assertNotIn("fetch(form.action,", truck_card_block)

    def _add_operation(self, sort_date):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.commit()
        return operation

    def _add_truck(self, number, *, capacity, remaining=None):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=number,
            capacity_gallons=capacity,
            remaining_fuel_gallons=remaining,
            is_active=True,
        )
        db.session.add(truck)
        db.session.flush()
        return truck

    def _login_user(self, username, role):
        user = self._add_approved_user(username, role)
        db.session.commit()
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )
        return user

    def _add_approved_user(self, username, role):
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
        db.session.add_all(
            [
                PortalAppAccess(
                    user_id=user.id,
                    app_code="neogateway",
                    status="approved",
                    role=role,
                    is_active=True,
                ),
                *[
                    GatewayNodeRole(
                        gateway_membership_id=membership.id,
                        node_id=node.id,
                        role=role,
                        is_active=True,
                    )
                    for node in NeoNode.query.filter_by(is_active=True).all()
                ],
            ]
        )
        return user


if __name__ == "__main__":
    unittest.main()
