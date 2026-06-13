import unittest
from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoErmacUldRequest,
    NeoNode,
    NeoSektorBallmatCount,
    NeoSektorBallmatWaveCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorSortState,
    NeoSektorUldOnTheWayEvent,
    NeoSektorWaveState,
    PermissionRule,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from app.services.uld_requests import (
    active_on_the_way_events,
    active_request_views,
    send_uld_on_the_way,
)


class NeoSektorRoutesTest(unittest.TestCase):
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
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_neosektor_dashboard_requires_login(self):
        response = self.client.get("/neosektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_operator_can_open_neosektor_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        for label in (
            b"TUNNEL CONDUCTOR",
            b"EBM",
            b"WBM",
            b"DISCHARGE",
            b"VIEW LIVE COUNTS",
            b"DRIVER ROUTING",
        ):
            self.assertIn(label, response.data)

    def test_neosektor_role_access_matrix_matches_permission_defaults(self):
        expectations = {
            "watcher": {
                "/neosektor": 200,
                "/neosektor/live-counts": 200,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 302,
                "/neosektor/ebm": 302,
                "/neosektor/wbm": 302,
                "/neosektor/discharge": 302,
            },
            "operator": {
                "/neosektor": 200,
                "/neosektor/live-counts": 200,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 302,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "simulator": {
                "/neosektor": 200,
                "/neosektor/live-counts": 200,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 200,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "master": {
                "/neosektor": 200,
                "/neosektor/live-counts": 200,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 200,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "grandmaster": {
                "/neosektor": 200,
                "/neosektor/live-counts": 200,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 200,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
        }

        for role, route_expectations in expectations.items():
            with self.subTest(role=role):
                self._login_approved_user(role=role)
                for path, expected_status in route_expectations.items():
                    response = self.client.get(path, follow_redirects=False)
                    self.assertEqual(
                        response.status_code,
                        expected_status,
                        f"{role} unexpected status for {path}",
                    )
                    if expected_status == 302:
                        self.assertEqual(response.location, "/neosektor")

    def test_neosektor_subpages_include_consistent_menu_return_navigation(self):
        self._login_approved_user(role="simulator")

        for path in (
            "/neosektor/live-counts",
            "/neosektor/driver-routing",
            "/neosektor/discharge",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'href="/neosektor"', response.data)
                self.assertIn(b'aria-label="BACK TO NeoSektor MENU"', response.data)

    def test_discharge_page_loads_for_operator(self):
        self._login_approved_user(role="operator")
        self._add_uld_request("D34", a2_count=2, a1_count=1, amp_count=0, setup_needed=True)

        response = self.client.get("/neosektor/discharge")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DISCHARGE", response.data)
        self.assertIn(b"D34", response.data)
        self.assertIn(b"A2 REQUESTED", response.data)
        self.assertIn(b"NEEDED FOR SETUP", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_discharge_view_only_user_cannot_send_ulds(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.discharge.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        self._add_uld_request("D34", a2_count=2)
        db.session.commit()
        self._login_approved_user(role="operator")

        page = self.client.get("/neosektor/discharge")
        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 1},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"VIEW ONLY", page.data)
        self.assertIn(b"disabled", page.data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.count(), 0)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").one().a2_count, 2)

    def test_discharge_edit_user_can_send_ulds_and_reduce_requested_count(self):
        self._add_uld_request("D34", a2_count=3, a1_count=1)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 2},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["event"]["door"], "D34")
        self.assertEqual(payload["event"]["uld_type"], "A2")
        self.assertEqual(payload["event"]["quantity"], 2)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").one().a2_count, 1)
        event = NeoSektorUldOnTheWayEvent.query.one()
        self.assertEqual(event.quantity, 2)
        self.assertEqual(event.uld_type, "A2")

    def test_discharge_edit_user_can_send_a1_and_amp_ulds(self):
        self._add_uld_request("D34", a1_count=1, amp_count=2)
        db.session.commit()
        self._login_approved_user(role="operator")

        a1_response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A1", "quantity": 1},
        )
        amp_response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "AMP", "quantity": 2},
        )

        saved = NeoErmacUldRequest.query.filter_by(door="D34").one()
        events = NeoSektorUldOnTheWayEvent.query.order_by(
            NeoSektorUldOnTheWayEvent.id.asc()
        ).all()
        self.assertEqual(a1_response.status_code, 200)
        self.assertEqual(amp_response.status_code, 200)
        self.assertEqual(saved.a1_count, 0)
        self.assertEqual(saved.amp_count, 0)
        self.assertEqual(
            [(event.uld_type, event.quantity) for event in events],
            [("A1", 1), ("AMP", 2)],
        )

    def test_discharge_send_quantity_clamps_to_requested_count(self):
        self._add_uld_request("D34", a2_count=1)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 5},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["event"]["quantity"], 1)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").one().a2_count, 0)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.one().quantity, 1)

    def test_each_discharge_send_creates_separate_expiring_event(self):
        base_time = datetime(2026, 6, 12, 12, 4)
        self._add_uld_request("D34", a2_count=3)
        first_event = send_uld_on_the_way(self.gateway, "D34", "A2", 2, now=base_time)
        second_event = send_uld_on_the_way(
            self.gateway,
            "D34",
            "A2",
            1,
            now=base_time + timedelta(minutes=3),
        )
        db.session.commit()

        self.assertNotEqual(first_event.id, second_event.id)
        self.assertEqual(first_event.expires_at_utc, base_time + timedelta(minutes=5))
        self.assertEqual(
            second_event.expires_at_utc,
            base_time + timedelta(minutes=8),
        )
        self.assertEqual(
            [event.quantity for event in active_on_the_way_events(self.gateway, now=base_time + timedelta(minutes=4, seconds=59))],
            [2, 1],
        )
        self.assertEqual(
            [event.quantity for event in active_on_the_way_events(self.gateway, now=base_time + timedelta(minutes=5))],
            [1],
        )
        self.assertEqual(
            active_on_the_way_events(self.gateway, now=base_time + timedelta(minutes=8)),
            [],
        )

    def test_discharge_keeps_on_the_way_event_visible_after_count_reaches_zero(self):
        sent_at = datetime.utcnow()
        self._add_uld_request("D34", a2_count=1)
        send_uld_on_the_way(self.gateway, "D34", "A2", 1, now=sent_at)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/discharge")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"D34", response.data)
        self.assertIn(b"0", response.data)
        self.assertIn(f"1 A2 sent at {sent_at:%H:%M}".encode(), response.data)

    def test_discharge_sorting_puts_setup_needed_requests_first(self):
        normal = self._add_uld_request("D34", a2_count=1, setup_needed=False)
        setup = self._add_uld_request("D1", a1_count=1, setup_needed=True)
        normal.updated_at = datetime(2026, 6, 12, 12, 0)
        setup.updated_at = datetime(2026, 6, 12, 12, 5)
        db.session.commit()

        views = active_request_views(self.gateway, now=datetime(2026, 6, 12, 12, 10))

        self.assertEqual([row["door"] for row in views], ["D1", "D34"])

    def test_discharge_state_reflects_request_changes_from_another_update_cycle(self):
        request_record = self._add_uld_request("D34", a2_count=1, a1_count=0, amp_count=0)
        db.session.commit()
        self._login_approved_user(role="operator")

        first_response = self.client.get("/neosektor/discharge/state")
        request_record.a2_count = 0
        request_record.a1_count = 3
        request_record.amp_count = 1
        request_record.setup_needed = True
        db.session.commit()
        second_response = self.client.get("/neosektor/discharge/state")

        first_payload = first_response.get_json()
        second_payload = second_response.get_json()
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_payload["state"]["requests"][0]["counts"]["A2"], 1)
        self.assertFalse(first_payload["state"]["requests"][0]["setup_needed"])
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            second_payload["state"]["requests"][0]["counts"],
            {"A2": 0, "A1": 3, "AMP": 1},
        )
        self.assertTrue(second_payload["state"]["requests"][0]["setup_needed"])

    def test_discharge_state_reflects_active_on_the_way_events_and_excludes_expired(self):
        now = datetime.utcnow()
        self._add_uld_request("D34", a2_count=0)
        db.session.add_all(
            [
                NeoSektorUldOnTheWayEvent(
                    gateway_id=self.gateway.id,
                    door="D34",
                    uld_type="A2",
                    quantity=1,
                    sent_at_utc=now,
                    expires_at_utc=now + timedelta(minutes=5),
                ),
                NeoSektorUldOnTheWayEvent(
                    gateway_id=self.gateway.id,
                    door="D34",
                    uld_type="AMP",
                    quantity=2,
                    sent_at_utc=now - timedelta(minutes=10),
                    expires_at_utc=now - timedelta(minutes=5),
                ),
            ]
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/discharge/state")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["state"]["requests"]), 1)
        self.assertEqual(
            [event["uld_type"] for event in payload["state"]["requests"][0]["on_the_way_events"]],
            ["A2"],
        )
        self.assertIn(
            "1 A2 sent at",
            payload["state"]["requests"][0]["on_the_way_events"][0]["label"],
        )

    def test_discharge_state_sorting_puts_setup_needed_first_after_refresh(self):
        normal = self._add_uld_request("D34", a2_count=1, setup_needed=False)
        setup = self._add_uld_request("D1", a1_count=1, setup_needed=True)
        normal.updated_at = datetime(2026, 6, 12, 12, 0)
        setup.updated_at = datetime(2026, 6, 12, 12, 5)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/discharge/state")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [request_row["door"] for request_row in payload["state"]["requests"]],
            ["D1", "D34"],
        )

    def test_discharge_state_requires_view_permission(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/discharge/state")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["ok"])

    def test_driver_routing_loads_for_view_authorized_user(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/driver-routing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DRIVER ROUTING", response.data)
        self.assertIn(b"driver-wave", response.data)
        self.assertIn(b"driver-bay-priority", response.data)
        self.assertIn(b"data-driver-routing", response.data)
        self.assertIn(b"VIEW ONLY", response.data)
        self.assertNotIn(b"data-driver-offset-input", response.data)
        self.assertNotIn(b"West Offset", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_driver_routing_blocks_user_without_view_permission(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.driver_routing.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.driver_routing.edit"
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/driver-routing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_driver_routing_no_longer_updates_offset(self):
        self._login_approved_user(role="watcher")

        response = self.client.post(
            "/neosektor/driver-routing/update",
            json={"west_offset": 4},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 0)

    def test_edit_authorized_tunnel_conductor_user_can_update_driver_route_offset(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/offset",
            json={"west_offset": 4},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["routing"]["west_offset"], 4)
        self.assertEqual(
            NeoSektorDriverRouteSetting.query.filter_by(
                route_name="WEST OFFSET",
            ).one().route_value,
            "4",
        )

    def test_tunnel_conductor_route_offset_clamps_to_non_negative_standalone_behavior(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/offset",
            json={"west_offset": -5},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["routing"]["west_offset"], 0)
        self.assertEqual(
            NeoSektorDriverRouteSetting.query.filter_by(
                route_name="WEST OFFSET",
            ).one().route_value,
            "0",
        )

    def test_driver_routing_reflects_shared_neosektor_state(self):
        self._login_approved_user(role="simulator")
        self.client.get("/neosektor/ebm")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 7, "status": "Light"},
                    "second": {"count": 2, "status": "Light"},
                },
                "open_bays": 3,
                "bay_statuses": {"Bay 1": "Moderate"},
            },
        )
        self.client.post(
            "/neosektor/tunnel-conductor/offset",
            json={"west_offset": 3},
        )

        page = self.client.get("/neosektor/driver-routing")
        state_response = self.client.get("/neosektor/driver-routing/state")

        payload = state_response.get_json()
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"West Ballmat Stay Left", page.data)
        self.assertIn(b"East Ballmat Stay Right", page.data)
        self.assertEqual(payload["state"]["routing"]["west_offset"], 3)
        self.assertEqual(
            payload["state"]["routing"]["routes"]["first"]["target"],
            "West Ballmat Stay Left",
        )
        self.assertEqual(
            payload["state"]["routing"]["routes"]["second"]["target"],
            "East Ballmat Stay Right",
        )
        self.assertEqual(payload["state"]["sides"]["east"]["open_bays"], 3)

    def test_driver_routing_zero_counts_use_standalone_open_bay_tiebreaker(self):
        self._login_approved_user(role="operator")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 0, "status": "Empty"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": 1,
                "bay_statuses": {},
            },
        )
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 0, "status": "Empty"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        response = self.client.get("/neosektor/driver-routing/state")

        self.assertEqual(response.status_code, 200)
        routing = response.get_json()["state"]["routing"]
        self.assertEqual(routing["routes"]["first"]["target"], "West Ballmat Stay Left")
        self.assertEqual(routing["routes"]["second"]["target"], "West Ballmat Stay Left")

    def test_driver_routing_west_offset_changes_standalone_threshold_output(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 6, "status": "Moderate"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": 0,
                "bay_statuses": {},
            },
        )
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 4, "status": "Light"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": 0,
                "bay_statuses": {},
            },
        )

        before_offset = self.client.get("/neosektor/driver-routing/state").get_json()
        self.client.post("/neosektor/tunnel-conductor/offset", json={"west_offset": 2})
        after_offset = self.client.get("/neosektor/driver-routing/state").get_json()

        self.assertEqual(
            before_offset["state"]["routing"]["routes"]["first"]["target"],
            "West Ballmat Stay Left",
        )
        self.assertEqual(
            after_offset["state"]["routing"]["routes"]["first"]["target"],
            "East Ballmat Stay Right",
        )
        self.assertEqual(after_offset["state"]["routing"]["west_offset"], 2)

    def test_driver_bay_priority_matches_standalone_status_then_bay_number(self):
        self._login_approved_user(role="operator")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {},
                "open_bays": 0,
                "bay_statuses": {
                    "Bay 1": "Full",
                    "Bay 2": "Moderate",
                    "Bay 3": "Full",
                },
            },
        )
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {},
                "open_bays": 0,
                "bay_statuses": {
                    "Bay 4": "Overflowing",
                    "Bay 5": "Full",
                },
            },
        )

        response = self.client.get("/neosektor/driver-routing/state")

        self.assertEqual(response.status_code, 200)
        priority = response.get_json()["state"]["routing"]["bay_priority"]
        self.assertEqual(
            [bay["bay_name"] for bay in priority],
            ["Bay 4", "Bay 5", "Bay 3", "Bay 1", "Bay 2"],
        )
        self.assertEqual(
            [bay["rank_label"] for bay in priority],
            ["1st", "2nd", "3rd", "4th", "5th"],
        )

    def test_neosektor_dashboard_and_header_link_to_real_driver_routing(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        driver_routing = self.client.get("/neosektor/driver-routing")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/driver-routing"', dashboard.data)
        self.assertEqual(driver_routing.status_code, 200)
        self.assertIn(b'href="/neosektor/driver-routing"', driver_routing.data)
        self.assertIn(b'aria-current="page"', driver_routing.data)

    def test_tunnel_conductor_loads_for_view_authorized_user(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TUNNEL CONDUCTOR", response.data)
        self.assertIn(b"Ballmat Counts", response.data)
        self.assertIn(b"Driver Route Offset", response.data)
        self.assertIn(b"data-tunnel-offset-input", response.data)
        self.assertIn(b'href="/logout"', response.data)
        self.assertNotIn(b'aria-label="BACK TO NeoSektor MENU"', response.data)
        self.assertNotIn(b"motherbrain-header-nav", response.data)
        self.assertIn(b"data-tunnel-conductor", response.data)
        self.assertIn(b"data-can-edit=\"true\"", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_tunnel_conductor_blocks_user_without_view_permission(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/tunnel-conductor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_view_only_tunnel_conductor_user_cannot_update_counts(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.tunnel_conductor.edit"
        ).one()
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

        wave_response = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "first", "delta": 1},
        )
        offset_response = self.client.post(
            "/neosektor/tunnel-conductor/offset",
            json={"west_offset": 4},
        )

        self.assertEqual(wave_response.status_code, 403)
        self.assertEqual(offset_response.status_code, 403)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 0)

    def test_tunnel_conductor_wave_delta_updates_left_to_arrive(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "first", "delta": 6},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"]["waves"][0]["planned"], 6)
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().planned_count,
            6,
        )

    def test_tunnel_conductor_delta_updates_east_first_wave(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_east_second_wave(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "second", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="2ND WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_west_first_wave(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="WEST",
                wave_name="1ST WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_updates_west_second_wave(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "second", "delta": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="WEST",
                wave_name="2ND WAVE",
            ).one().count,
            1,
        )

    def test_tunnel_conductor_delta_counts_cannot_go_below_zero(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": -1},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"]["sides"]["east"]["waves"][0]["count"], 0)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            0,
        )

    def test_tunnel_conductor_delta_updates_shared_live_state(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "west", "wave": "second", "delta": 4},
        )

        self.assertEqual(response.status_code, 200)
        state_response = self.client.get("/neosektor/ballmat/state")
        live_counts = self.client.get("/neosektor/live-counts")

        payload = state_response.get_json()
        self.assertEqual(payload["state"]["sides"]["west"]["total_count"], 4)
        self.assertEqual(payload["state"]["waves"][1]["unloaded"], 4)
        self.assertEqual(NeoSektorSortState.query.one().unloaded_total, 4)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b'data-count-field="west_second_wave">4</strong>', live_counts.data)

    def test_neosektor_dashboard_and_header_link_to_real_tunnel_conductor(self):
        self._login_approved_user(role="simulator")

        dashboard = self.client.get("/neosektor")
        tunnel = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/tunnel-conductor"', dashboard.data)
        self.assertEqual(tunnel.status_code, 200)
        self.assertIn(b"neosektor-tunnel-operator-page", tunnel.data)
        self.assertIn(b"data-wave-url=\"/neosektor/tunnel-conductor/wave\"", tunnel.data)
        self.assertIn(b"data-offset-url=\"/neosektor/tunnel-conductor/offset\"", tunnel.data)

    def test_ebm_and_wbm_open_shared_ballmat_operations_screen(self):
        self._login_approved_user(role="operator")

        ebm = self.client.get("/neosektor/ebm")
        wbm = self.client.get("/neosektor/wbm")
        east_compat = self.client.get("/neosektor/ballmat?side=east", follow_redirects=False)
        west_compat = self.client.get("/neosektor/ballmat?side=west", follow_redirects=False)

        self.assertEqual(ebm.status_code, 200)
        self.assertIn(b"Live Ballmat Counts", ebm.data)
        self.assertIn(b"EBM | EDIT ENABLED", ebm.data)
        self.assertIn(b"data-selected-side=\"east\"", ebm.data)
        self.assertIn(b"East Ballmat", ebm.data)
        self.assertIn(b"West Ballmat", ebm.data)
        self.assertIn(b"data-can-edit=\"true\"", ebm.data)
        self.assertIn(b'href="/logout"', ebm.data)
        self.assertNotIn(b'aria-label="BACK TO NeoSektor MENU"', ebm.data)
        self.assertNotIn(b"motherbrain-header-nav", ebm.data)
        self.assertIn(b"neosektor-ballmat-operator-page", ebm.data)
        self.assertIn(b"data-open-bays", ebm.data)
        self.assertEqual(wbm.status_code, 200)
        self.assertIn(b"WBM | EDIT ENABLED", wbm.data)
        self.assertIn(b"data-selected-side=\"west\"", wbm.data)
        self.assertEqual(east_compat.status_code, 302)
        self.assertEqual(east_compat.location, "/neosektor/ebm")
        self.assertEqual(west_compat.status_code, 302)
        self.assertEqual(west_compat.location, "/neosektor/wbm")

    def test_view_only_ballmat_user_cannot_update_counts(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {"first": {"count": 12, "status": "Light"}},
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

    def test_ebm_view_permission_controls_screen_access(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.view"
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.ebm.edit"
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/ebm", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor")

    def test_wbm_view_only_user_sees_disabled_controls_and_cannot_update(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.wbm.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        db.session.commit()
        self._login_approved_user(role="operator")

        page = self.client.get("/neosektor/wbm")
        update = self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {"first": {"count": 5, "status": "Light"}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"data-can-edit=\"false\"", page.data)
        self.assertIn(b"VIEW ONLY", page.data)
        self.assertEqual(update.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 4)
        self.assertEqual(
            sum(row.count for row in NeoSektorBallmatWaveCount.query.all()),
            0,
        )

    def test_edit_authorized_user_updates_selected_side_only(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 12, "status": "Light"},
                    "second": {"count": 4, "status": "Moderate"},
                },
                "open_bays": 3,
                "bay_statuses": {"Bay 1": "Full", "Bay 2": "Light"},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        east_state = payload["state"]["sides"]["east"]
        west_state = payload["state"]["sides"]["west"]
        self.assertEqual(east_state["total_count"], 16)
        self.assertEqual(east_state["open_bays"], 3)
        self.assertEqual(west_state["total_count"], 0)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            12,
        )
        self.assertEqual(
            NeoSektorOpenBayState.query.filter_by(side="EAST").one().open_count,
            3,
        )
        self.assertEqual(
            NeoSektorBayStatus.query.filter_by(bay_name="Bay 1").one().status,
            "Full",
        )
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().unloaded_count,
            12,
        )
        self.assertEqual(NeoSektorSortState.query.one().unloaded_total, 16)

    def test_edit_authorized_user_cannot_update_unselected_side(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "west",
                "waves": {"first": {"count": 99, "status": "Full"}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            sum(row.count for row in NeoSektorBallmatWaveCount.query.all()),
            0,
        )

    def test_ballmat_update_counts_clamp_at_zero(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": -4, "status": "Light"},
                    "second": {"count": 0, "status": "Empty"},
                },
                "open_bays": -1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        east_state = payload["state"]["sides"]["east"]
        self.assertEqual(east_state["waves"][0]["count"], 0)
        self.assertEqual(east_state["open_bays"], 0)

    def test_live_json_endpoint_returns_updated_ballmat_state(self):
        self._login_approved_user(role="operator")
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 7, "status": "Light"},
                    "second": {"count": 8, "status": "Moderate"},
                },
                "open_bays": 2,
                "bay_statuses": {"Bay 4": "Full"},
            },
        )

        response = self.client.get("/neosektor/ballmat/state")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["sides"]["west"]["total_count"], 15)
        self.assertEqual(payload["state"]["sides"]["west"]["open_bays"], 2)
        self.assertEqual(payload["state"]["waves"][0]["unloaded"], 7)

    def test_live_counts_loads_default_database_backed_state(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/live-counts")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", response.data)
        self.assertIn(b"Live Ballmat Counts", response.data)
        self.assertIn(b"Left to Unload", response.data)
        self.assertIn(b"1ST WAVE", response.data)
        self.assertIn(b"2ND WAVE", response.data)
        self.assertIn(b"East Ballmat", response.data)
        self.assertIn(b"West Ballmat", response.data)
        self.assertIn(b"Empty", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)
        self.assertEqual(NeoSektorSortState.query.count(), 1)
        self.assertEqual(NeoSektorWaveState.query.count(), 2)
        self.assertEqual(NeoSektorBallmatCount.query.count(), 2)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 4)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 2)
        self.assertEqual(NeoSektorBayStatus.query.count(), 5)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 3)

    def test_neosektor_dashboard_and_header_link_to_real_live_counts(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', dashboard.data)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', live_counts.data)
        self.assertIn(b'aria-current="page"', live_counts.data)

    def test_watcher_can_open_dashboard_and_live_counts_without_special_view_keys(self):
        self._login_approved_user(role="watcher")

        self.assertIsNone(
            PermissionRule.query.filter_by(
                permission_key="neosektor.dashboard.view",
            ).first()
        )
        self.assertIsNone(
            PermissionRule.query.filter_by(
                permission_key="neosektor.live_counts.view",
            ).first()
        )

        dashboard = self.client.get("/neosektor", follow_redirects=False)
        conductor = self.client.get("/neosektor/tunnel-conductor", follow_redirects=False)
        ebm = self.client.get("/neosektor/ebm", follow_redirects=False)
        wbm = self.client.get("/neosektor/wbm", follow_redirects=False)
        discharge = self.client.get("/neosektor/discharge", follow_redirects=False)
        live_counts = self.client.get("/neosektor/live-counts", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"NeoSektor", dashboard.data)
        self.assertEqual(conductor.status_code, 302)
        self.assertEqual(conductor.location, "/neosektor")
        self.assertEqual(ebm.status_code, 302)
        self.assertEqual(ebm.location, "/neosektor")
        self.assertEqual(wbm.status_code, 302)
        self.assertEqual(wbm.location, "/neosektor")
        self.assertEqual(discharge.status_code, 302)
        self.assertEqual(discharge.location, "/neosektor")
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", live_counts.data)

    def test_rfd_sektor_still_points_to_standalone_service(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd/sektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://neosektor.onrender.com/")

    def test_rfd_hub_neosektor_tile_points_to_internal_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        self.assertIn(b'href="/neosektor"', response.data)
        self.assertNotIn(b'href="/rfd/sektor"', response.data)

    def _add_uld_request(
        self,
        door,
        a2_count=0,
        a1_count=0,
        amp_count=0,
        setup_needed=False,
    ):
        request_record = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door=door,
            a2_count=a2_count,
            a1_count=a1_count,
            amp_count=amp_count,
            setup_needed=setup_needed,
        )
        db.session.add(request_record)
        db.session.flush()
        return request_record

    def _login_approved_user(self, role):
        user = User(
            username=f"sektor_{role}_user",
            email=f"sektor_{role}@example.test",
            role="watcher",
            is_active=True,
        )
        user.set_password("TestPassword123!")
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

        sektor = NeoNode.query.filter_by(code="sektor").one()
        if role != "watcher":
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=sektor.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()

        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
