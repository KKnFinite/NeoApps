import json
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoErmacBuildingLineup,
    NeoErmacDoorPull,
    NeoErmacDoorSupervision,
    NeoErmacUldRequest,
    NeoNode,
    NeoSektorUldOnTheWayEvent,
    SortDateMission,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoErmacDoorSupervisionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "NeoErmacDoorSupervisionTestConfig",
            (),
            {
                "SECRET_KEY": "door-supervision-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(2026, 6, 11, 23, 0),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.operation = self._add_operation(date(2026, 6, 11))
        self.user = self._add_user("first")
        db.session.commit()
        self.client = self.app.test_client()
        self._login(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_multiple_doors_and_last_active_persist_across_navigation_and_login(self):
        self.client.get("/neoermac/door-view?door=D1")
        selected = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(selected.status_code, 200)
        record = NeoErmacDoorSupervision.query.one()
        self.assertEqual(json.loads(record.selected_doors_json), ["D1", "D34"])
        self.assertEqual(record.active_door, "D34")
        self.assertIn(b">D1</a>", selected.data)
        self.assertIn(b'class="neoermac-door-tab is-active"', selected.data)

        self.client.get("/neoermac/building-lineup")
        self.client.post("/logout")
        self._login(self.user)
        restored = self.client.get("/neoermac/door-view")

        self.assertEqual(restored.status_code, 200)
        self.assertIn(b'data-state-url="/neoermac/door-view/state?door=D34"', restored.data)
        self.assertIn(b">D1</a>", restored.data)
        self.assertIn(b">D34</a>", restored.data)

    def test_door_supervision_is_isolated_per_user(self):
        self.client.get("/neoermac/door-view?door=D1")
        self.client.post("/logout")
        second_user = self._add_user("second")
        db.session.commit()
        self._login(second_user)

        empty = self.client.get("/neoermac/door-view")
        self.assertIn(b"Select a door.", empty.data)
        self.assertNotIn(b'data-door-view', empty.data)

        self.client.get("/neoermac/door-view?door=D4")
        rows = NeoErmacDoorSupervision.query.order_by(
            NeoErmacDoorSupervision.user_id.asc()
        ).all()
        self.assertEqual(len(rows), 2)
        selections = {
            row.user_id: json.loads(row.selected_doors_json)
            for row in rows
        }
        self.assertEqual(selections[self.user.id], ["D1"])
        self.assertEqual(selections[second_user.id], ["D4"])

    def test_new_sort_starts_with_a_fresh_selection(self):
        self.client.get("/neoermac/door-view?door=D9")
        next_operation = self._add_operation(date(2026, 6, 12))
        db.session.commit()

        fresh = self.client.get("/neoermac/door-view")

        self.assertEqual(fresh.status_code, 200)
        self.assertIn(b"Select a door.", fresh.data)
        self.assertNotIn(b'data-door-view', fresh.data)
        self.client.get("/neoermac/door-view?door=D13")
        rows = NeoErmacDoorSupervision.query.order_by(
            NeoErmacDoorSupervision.sort_date_operation_id.asc()
        ).all()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].sort_date_operation_id, self.operation.id)
        self.assertEqual(json.loads(rows[0].selected_doors_json), ["D9"])
        self.assertEqual(rows[1].sort_date_operation_id, next_operation.id)
        self.assertEqual(json.loads(rows[1].selected_doors_json), ["D13"])

    def test_add_remove_and_removing_active_door_selects_another(self):
        added = self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": ["D1", "D4", "D34"], "active_door": "D4"},
            follow_redirects=False,
        )
        self.assertEqual(added.status_code, 302)
        self.assertTrue(added.location.endswith("/neoermac/door-view?door=D4"))

        removed = self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": ["D1", "D34"], "active_door": "D4"},
            follow_redirects=False,
        )
        self.assertEqual(removed.status_code, 302)
        self.assertTrue(removed.location.endswith("/neoermac/door-view?door=D1"))
        record = NeoErmacDoorSupervision.query.one()
        self.assertEqual(json.loads(record.selected_doors_json), ["D1", "D34"])
        self.assertEqual(record.active_door, "D1")

        pull = NeoErmacDoorPull(
            gateway_id=self.gateway.id,
            sort_date_operation_id=self.operation.id,
            door="D4",
            destination="SDF",
            actual_pure_pull_time_local=time(1, 44),
        )
        db.session.add(pull)
        db.session.commit()
        self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": ["D34"], "active_door": "D1"},
        )
        self.assertEqual(NeoErmacDoorPull.query.count(), 1)
        self.assertEqual(NeoErmacDoorPull.query.one().door, "D4")

    def test_existing_single_door_pull_autosave_still_works_with_tabs(self):
        db.session.add(
            NeoErmacBuildingLineup(
                gateway_id=self.gateway.id,
                runout_key="runout_10",
                runout_name="D32-D34 Belts",
                west_destination_1="SDF",
            )
        )
        db.session.add(
            SortDateMission(
                sort_date=self.operation.sort_date,
                gateway_code=self.gateway.code,
                sort_name=self.operation.sort_name,
                sort_date_operation_id=self.operation.id,
                mission_type="departure",
                mission_source="master",
                flight_number="UPS123",
                origin=self.gateway.code,
                destination="SDF",
                timezone="America/Chicago",
                planned_datetime_local=datetime(2026, 6, 12, 2, 0),
                planned_datetime_utc=datetime(2026, 6, 12, 7, 0),
                pure_pull_time_local=time(1, 50),
                mix_pull_time_local=time(2, 0),
                departure_status="scheduled",
            )
        )
        db.session.commit()
        self.client.get("/neoermac/door-view?door=D34")

        response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "01:44",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        self.assertEqual(saved.actual_pure_pull_time_local, time(1, 44))

    def test_uld_request_can_target_unsupervised_door_without_changing_active_tab(self):
        self.client.get("/neoermac/door-view?door=D1")

        response = self.client.post(
            "/neoermac/door-view?door=D1",
            data={
                "active_door": "D1",
                "request_door": "D13",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "1",
                "uld_amp_count": "0",
                "setup_needed": "on",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/neoermac/door-view?door=D1"))
        request_record = NeoErmacUldRequest.query.one()
        self.assertEqual(request_record.door, "D13")
        self.assertEqual(request_record.requested_by_user_id, self.user.id)
        self.assertTrue(request_record.setup_needed)
        supervision = NeoErmacDoorSupervision.query.one()
        self.assertEqual(json.loads(supervision.selected_doors_json), ["D1"])
        self.assertEqual(supervision.active_door, "D1")

        page = self.client.get("/neoermac/door-view?door=D1")
        rendered = page.data.split(
            b'const root = document.querySelector("[data-door-view]");',
            1,
        )[0]
        self.assertIn(b'name="request_door"', rendered)
        self.assertIn(b'data-uld-door-link="D13"', page.data)
        self.assertIn(b"D13", page.data)
        self.assertIn(b"SETUP", page.data)
        self.assertLess(
            rendered.index(b"OUTBOUND PULLS"),
            rendered.index(b"data-uld-workspace"),
        )

    def test_uld_workspace_combines_supervised_activity_and_own_unsupervised_activity(self):
        self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": ["D1", "D4"], "active_door": "D1"},
        )
        other_user = self._add_user("workspace-other")
        now = datetime.utcnow()
        requests = [
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D4",
                a2_count=2,
                requested_by_user_id=other_user.id,
            ),
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D13",
                a1_count=1,
                requested_by_user_id=self.user.id,
            ),
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D17",
                amp_count=3,
                requested_by_user_id=other_user.id,
            ),
        ]
        events = [
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D4",
                uld_type="A2",
                quantity=2,
                requested_by_user_id=other_user.id,
                sent_at_utc=now,
                expires_at_utc=now + timedelta(minutes=5),
            ),
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D13",
                uld_type="A1",
                quantity=1,
                requested_by_user_id=self.user.id,
                sent_at_utc=now,
                expires_at_utc=now + timedelta(minutes=5),
            ),
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D17",
                uld_type="AMP",
                quantity=3,
                requested_by_user_id=other_user.id,
                sent_at_utc=now,
                expires_at_utc=now + timedelta(minutes=5),
            ),
        ]
        db.session.add_all([*requests, *events])
        db.session.commit()

        first_state = self.client.get(
            "/neoermac/door-view/state?door=D1"
        ).get_json()["state"]["uld_workspace"]
        first_request_doors = [row["door"] for row in first_state["requests"]]
        first_event_doors = [row["door"] for row in first_state["on_the_way_events"]]
        self.assertEqual(first_request_doors, ["D4", "D13"])
        self.assertEqual(first_event_doors, ["D4", "D13"])

        self.client.get("/neoermac/door-view?door=D4")
        second_state = self.client.get(
            "/neoermac/door-view/state?door=D4"
        ).get_json()["state"]["uld_workspace"]
        self.assertEqual(
            [row["id"] for row in second_state["requests"]],
            [row["id"] for row in first_state["requests"]],
        )
        self.assertEqual(
            [row["id"] for row in second_state["on_the_way_events"]],
            [row["id"] for row in first_state["on_the_way_events"]],
        )

    def test_uld_door_links_activate_supervised_or_add_unsupervised_door(self):
        self.client.post(
            "/neoermac/door-view/supervision",
            data={"doors": ["D1", "D4"], "active_door": "D1"},
        )
        db.session.add(
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                sort_date_operation_id=self.operation.id,
                door="D13",
                a2_count=1,
                requested_by_user_id=self.user.id,
            )
        )
        db.session.commit()

        page = self.client.get("/neoermac/door-view?door=D1")
        self.assertIn(b'href="/neoermac/door-view?door=D13"', page.data)

        supervised = self.client.get("/neoermac/door-view?door=D4")
        self.assertEqual(supervised.status_code, 200)
        record = NeoErmacDoorSupervision.query.one()
        self.assertEqual(json.loads(record.selected_doors_json), ["D1", "D4"])
        self.assertEqual(record.active_door, "D4")

        added = self.client.get("/neoermac/door-view?door=D13")
        self.assertEqual(added.status_code, 200)
        db.session.refresh(record)
        self.assertEqual(json.loads(record.selected_doors_json), ["D1", "D4", "D13"])
        self.assertEqual(record.active_door, "D13")
        self.assertIn(b'data-door-tab="D13"', added.data)

    def test_mobile_tabs_scroll_inside_the_viewport(self):
        css = Path("app/static/css/base.css").read_text()

        self.client.post(
            "/neoermac/door-view/supervision",
            data={
                "doors": [
                    "D1", "D4", "D6", "D9", "D13", "D17", "D21",
                    "D24", "D26", "D29", "D32", "D34", "D37",
                ],
                "active_door": "D1",
            },
        )
        response = self.client.get("/neoermac/door-view")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'role="tab"'), 13)
        self.assertIn(".neoermac-door-tabs {", css)
        self.assertIn("overflow-x: auto;", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", css)
        self.assertIn("max-width: 100%;", css)
        self.assertIn(".neoermac-door-shell {", css)
        self.assertIn(".neoermac-door-fixed-controls {", css)
        self.assertIn("position: sticky;", css)

    def _add_operation(self, sort_date):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _add_user(self, suffix):
        user = User(
            username=f"door_supervision_{suffix}",
            email=f"door-supervision-{suffix}@example.test",
            role="watcher",
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
        ermac = NeoNode.query.filter_by(code="ermac").one()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=ermac.id,
                role="operator",
                is_active=True,
            )
        )
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
