import unittest
from datetime import datetime, timedelta
from pathlib import Path

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
    update_uld_request,
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
        self.assertIn(b"Live Counts", response.data)
        self.assertIn(b"data-live-counts", response.data)
        self.assertNotIn(b"Operations Menu", response.data)
        self.assertNotIn(b"neosektor-menu-link", response.data)
        self.assertNotIn(b'href="/neosektor/live-counts"', response.data)
        self.assertNotIn(b"Tunnel Conductor</a>", response.data)
        self.assertNotIn(b"motherbrain-header-nav", response.data)
        self.assertIn(b"data-neosektor-internal-menu", response.data)

    def test_neosektor_internal_menu_filters_links_by_role(self):
        expectations = {
            "watcher": {
                b'href="/neosektor"',
                b'href="/neosektor/driver-routing"',
            },
            "operator": {
                b'href="/neosektor"',
                b'href="/neosektor/ebm"',
                b'href="/neosektor/wbm"',
                b'href="/neosektor/driver-routing"',
                b'href="/neosektor/discharge"',
            },
            "simulator": {
                b'href="/neosektor"',
                b'href="/neosektor/tunnel-conductor"',
                b'href="/neosektor/ebm"',
                b'href="/neosektor/wbm"',
                b'href="/neosektor/driver-routing"',
                b'href="/neosektor/discharge"',
            },
        }
        expected_labels = {
            "watcher": (b"Live Counts", b"Driver Routing"),
            "operator": (
                b"Live Counts",
                b"East Ballmat",
                b"West Ballmat",
                b"Driver Routing",
                b"Discharge",
            ),
            "simulator": (
                b"Live Counts",
                b"Tunnel Conductor",
                b"East Ballmat",
                b"West Ballmat",
                b"Driver Routing",
                b"Discharge",
            ),
        }
        blocked = {
            "watcher": (
                b'href="/neosektor/tunnel-conductor"',
                b'href="/neosektor/ebm"',
                b'href="/neosektor/wbm"',
                b'href="/neosektor/discharge"',
            ),
            "operator": (b'href="/neosektor/tunnel-conductor"',),
            "simulator": (),
        }

        for role, expected_links in expectations.items():
            with self.subTest(role=role):
                self._login_approved_user(role=role)
                response = self.client.get("/neosektor")

                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"motherbrain-header-nav", response.data)
                self.assertIn(b"data-neosektor-internal-menu", response.data)
                for label in expected_labels[role]:
                    self.assertIn(label, response.data)
                for link in expected_links:
                    self.assertIn(link, response.data)
                self.assertNotIn(b'href="/neosektor/live-counts"', response.data)
                self.assertNotIn(b"NeoSektor Menu", response.data)
                for link in blocked[role]:
                    self.assertNotIn(link, response.data)

    def test_neosektor_internal_menu_appears_on_all_screens(self):
        self._login_approved_user(role="simulator")

        standalone_menu_paths = {
            "/neosektor",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
        }
        fullscreen_paths = {
            "/neosektor/driver-routing",
        }

        for path in (
            "/neosektor",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/driver-routing",
            "/neosektor/discharge",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                if path in standalone_menu_paths:
                    self.assertEqual(response.data.count(b"data-neosektor-internal-menu"), 1)
                    self.assertIn(b"neosektor-internal-menu-trigger", response.data)
                    self.assertNotIn(b"motherbrain-header-nav", response.data)
                    self.assertNotIn(b"NeoGateway - RFD", response.data)
                elif path in fullscreen_paths:
                    self.assertEqual(response.data.count(b"data-neosektor-internal-menu"), 0)
                    self.assertNotIn(b"motherbrain-header-nav", response.data)
                    self.assertIn(b'href="/neosektor"', response.data)
                    self.assertIn(b"Back", response.data)
                    self.assertNotIn(b"Change Characters", response.data)
                    continue
                else:
                    self.assertEqual(response.data.count(b"data-neosektor-internal-menu"), 0)
                    self.assertIn(b"motherbrain-header-nav", response.data)
                for label in (
                    b"Live Counts",
                    b"Tunnel Conductor",
                    b"East Ballmat",
                    b"West Ballmat",
                    b"Driver Routing",
                    b"Discharge",
                ):
                    if label == b"Live Counts":
                        if path in standalone_menu_paths:
                            self.assertIn(label, response.data)
                        else:
                            self.assertIn(b'aria-label="NeoSektor menu"', response.data)
                    elif path in standalone_menu_paths:
                        self.assertIn(label, response.data)
                    else:
                        self.assertIn(label.upper(), response.data)
                for href in (
                    b'href="/neosektor"',
                    b'href="/neosektor/tunnel-conductor"',
                    b'href="/neosektor/ebm"',
                    b'href="/neosektor/wbm"',
                    b'href="/neosektor/driver-routing"',
                    b'href="/neosektor/discharge"',
                ):
                    self.assertIn(href, response.data)
                self.assertNotIn(b"BACK TO NeoGateway", response.data)

    def test_neosektor_role_access_matrix_matches_permission_defaults(self):
        expectations = {
            "watcher": {
                "/neosektor": 200,
                "/neosektor/live-counts": 302,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 302,
                "/neosektor/ebm": 302,
                "/neosektor/wbm": 302,
                "/neosektor/discharge": 302,
            },
            "operator": {
                "/neosektor": 200,
                "/neosektor/live-counts": 302,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 302,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "simulator": {
                "/neosektor": 200,
                "/neosektor/live-counts": 302,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 200,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "master": {
                "/neosektor": 200,
                "/neosektor/live-counts": 302,
                "/neosektor/driver-routing": 200,
                "/neosektor/tunnel-conductor": 200,
                "/neosektor/ebm": 200,
                "/neosektor/wbm": 200,
                "/neosektor/discharge": 200,
            },
            "grandmaster": {
                "/neosektor": 200,
                "/neosektor/live-counts": 302,
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

    def test_neosektor_subpages_use_header_navigation_without_bottom_return(self):
        self._login_approved_user(role="simulator")

        for path in (
            "/neosektor",
            "/neosektor/driver-routing",
            "/neosektor/discharge",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'href="/neosektor"', response.data)
                self.assertNotIn(b'aria-label="BACK TO NeoSektor MENU"', response.data)

    def test_standalone_operator_pages_include_change_characters_control(self):
        self._login_approved_user(role="simulator")

        for path in (
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/tunnel-conductor",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"neosektor-standalone-header", response.data)
                self.assertIn(b"character-switcher-standalone", response.data)
                self.assertEqual(response.data.count(b'<details class="character-switcher'), 1)
                self.assertIn(b"Change Characters", response.data)
                switcher = response.data.split(b"data-character-switcher", 1)[1].split(
                    b"</details>",
                    1,
                )[0]
                self.assertNotIn(b'href="/rfd"', switcher)
                self.assertNotIn(b'href="/neosektor"', switcher)
                self.assertNotIn(b"Placeholder Shell", response.data)

    def test_discharge_page_loads_for_operator(self):
        self._login_approved_user(role="operator")
        self._add_uld_request("D34", a2_count=2, a1_count=1, amp_count=0, setup_needed=True)

        response = self.client.get("/neosektor/discharge")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DISCHARGE", response.data)
        self.assertIn(b"ACTIVE REQUESTS", response.data)
        self.assertIn(b"D34", response.data)
        self.assertIn(b"A2", response.data)
        self.assertIn(b"SETUP", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_discharge_selected_request_renders_compact_send_form(self):
        request_record = self._add_uld_request("D34", a2_count=2, a1_count=1, amp_count=3)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get(f"/neosektor/discharge?request_id={request_record.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BACK TO QUEUE", response.data)
        self.assertIn(b"RESPOT", response.data)
        self.assertIn(b"SEND ON THE WAY", response.data)
        self.assertIn(b'name="send_a2_count"', response.data)
        self.assertIn(b'name="send_a1_count"', response.data)
        self.assertIn(b'name="send_amp_count"', response.data)

    def test_door_request_same_door_and_setup_combines_and_updates_timestamp(self):
        first_time = datetime(2026, 6, 12, 12, 0)
        second_time = datetime(2026, 6, 12, 12, 7)

        update_uld_request(
            self.gateway,
            "D34",
            {"A2": 1, "A1": 2, "AMP": 0},
            setup_needed=True,
            now=first_time,
        )
        update_uld_request(
            self.gateway,
            "D34",
            {"A2": 3, "A1": 0, "AMP": 1},
            setup_needed=True,
            now=second_time,
        )
        db.session.commit()

        request_record = NeoErmacUldRequest.query.filter_by(door="D34").one()
        self.assertEqual(request_record.a2_count, 4)
        self.assertEqual(request_record.a1_count, 2)
        self.assertEqual(request_record.amp_count, 1)
        self.assertTrue(request_record.setup_needed)
        self.assertEqual(request_record.updated_at, second_time)

    def test_door_request_same_door_different_setup_stays_separate(self):
        update_uld_request(
            self.gateway,
            "D34",
            {"A2": 1, "A1": 0, "AMP": 0},
            setup_needed=False,
            now=datetime(2026, 6, 12, 12, 0),
        )
        update_uld_request(
            self.gateway,
            "D34",
            {"A2": 0, "A1": 1, "AMP": 0},
            setup_needed=True,
            now=datetime(2026, 6, 12, 12, 5),
        )
        db.session.commit()

        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").count(), 2)
        self.assertEqual(
            NeoErmacUldRequest.query.filter_by(door="D34", setup_needed=False).one().a2_count,
            1,
        )
        self.assertEqual(
            NeoErmacUldRequest.query.filter_by(door="D34", setup_needed=True).one().a1_count,
            1,
        )

    def test_discharge_view_only_user_cannot_send_ulds(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.discharge.edit"
        ).one()
        edit_rule.minimum_role = "simulator"
        self._add_uld_request("D34", a2_count=2)
        db.session.commit()
        self._login_approved_user(role="operator")

        request_record = NeoErmacUldRequest.query.filter_by(door="D34").one()
        page = self.client.get(f"/neosektor/discharge?request_id={request_record.id}")
        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 1},
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"VIEW ONLY", page.data)
        self.assertIn(b"View-only access", page.data)
        self.assertNotIn(b"SEND ON THE WAY", page.data)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.count(), 0)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").one().a2_count, 2)

    def test_discharge_selected_request_form_sends_all_uld_types(self):
        request_record = self._add_uld_request("D34", a2_count=3, a1_count=1, amp_count=0)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/discharge/send",
            data={
                "door": "D34",
                "request_id": str(request_record.id),
                "send_a2_count": "2",
                "send_a1_count": "1",
                "send_amp_count": "4",
            },
            follow_redirects=False,
        )

        saved = NeoErmacUldRequest.query.filter_by(door="D34").one()
        events = NeoSektorUldOnTheWayEvent.query.order_by(
            NeoSektorUldOnTheWayEvent.id.asc()
        ).all()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neosektor/discharge")
        self.assertEqual(saved.a2_count, 1)
        self.assertEqual(saved.a1_count, 0)
        self.assertEqual(saved.amp_count, 0)
        self.assertEqual(
            [(event.uld_type, event.quantity) for event in events],
            [("A2", 2), ("A1", 1), ("AMP", 4)],
        )

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

        events = NeoSektorUldOnTheWayEvent.query.order_by(
            NeoSektorUldOnTheWayEvent.id.asc()
        ).all()
        self.assertEqual(a1_response.status_code, 200)
        self.assertEqual(amp_response.status_code, 200)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").count(), 0)
        self.assertEqual(
            [(event.uld_type, event.quantity) for event in events],
            [("A1", 1), ("AMP", 2)],
        )

    def test_discharge_oversend_records_actual_sent_and_closes_request(self):
        self._add_uld_request("D34", a2_count=1)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 5},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["event"]["quantity"], 5)
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").count(), 0)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.one().quantity, 5)

    def test_discharge_partial_send_keeps_remaining_request(self):
        self._add_uld_request("D34", a2_count=3, a1_count=2)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neosektor/discharge/send",
            json={"door": "D34", "uld_type": "A2", "quantity": 1},
        )

        saved = NeoErmacUldRequest.query.filter_by(door="D34").one()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(saved.a2_count, 2)
        self.assertEqual(saved.a1_count, 2)
        self.assertEqual(saved.amp_count, 0)

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

    def test_discharge_fulfilled_request_disappears_from_queue(self):
        sent_at = datetime.utcnow()
        self._add_uld_request("D34", a2_count=1)
        send_uld_on_the_way(self.gateway, "D34", "A2", 1, now=sent_at)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/discharge")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"D34", response.data)
        self.assertIn(b"NO ACTIVE ULD REQUESTS", response.data)

    def test_discharge_sorting_puts_setup_needed_requests_first(self):
        normal = self._add_uld_request("D34", a2_count=1, setup_needed=False)
        setup = self._add_uld_request("D1", a1_count=1, setup_needed=True)
        normal.updated_at = datetime(2026, 6, 12, 12, 0)
        setup.updated_at = datetime(2026, 6, 12, 12, 5)
        db.session.commit()

        views = active_request_views(self.gateway, now=datetime(2026, 6, 12, 12, 10))

        self.assertEqual([row["door"] for row in views], ["D1", "D34"])

    def test_discharge_sorting_uses_oldest_timestamp_within_priority_group(self):
        newer = self._add_uld_request("D34", a2_count=1, setup_needed=False)
        older = self._add_uld_request("D1", a1_count=1, setup_needed=False)
        newer.updated_at = datetime(2026, 6, 12, 12, 5)
        older.updated_at = datetime(2026, 6, 12, 12, 0)
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
        self._add_uld_request("D34", a2_count=1)
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
        self.assertIn(b"neosektor-driver-page", response.data)
        self.assertIn(b'href="/neosektor"', response.data)
        self.assertIn(b"Back", response.data)
        self.assertNotIn(b"Change Characters", response.data)
        self.assertNotIn(b"Logged in", response.data)
        self.assertNotIn(b"Logout", response.data)
        self.assertNotIn(b"data-neosektor-internal-menu", response.data)
        self.assertNotIn(b"motherbrain-header-nav", response.data)
        self.assertNotIn(b"VIEW ONLY", response.data)
        self.assertNotIn(b"EDIT ENABLED", response.data)
        self.assertNotIn(b"driver-wave-context", response.data)
        self.assertNotIn(b"data-driver-east-count", response.data)
        self.assertNotIn(b"data-driver-west-count", response.data)
        self.assertNotIn(b"data-driver-offset-input", response.data)
        self.assertNotIn(b"West Offset", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)
        self.assertLess(response.data.index(b"driver-wave-first"), response.data.index(b"driver-bay-priority"))
        self.assertLess(response.data.index(b"driver-bay-priority"), response.data.index(b"driver-wave-second"))
        self.assertIn(b'<span class="driver-target-node">', response.data)
        self.assertNotIn(b"East Ballmat <span", response.data)
        self.assertNotIn(b"West Ballmat <span", response.data)
        self.assertIn(b"targetNode.textContent = targetLabel;", response.data)
        self.assertIn(b"instruction.textContent = instructionLabel;", response.data)
        self.assertNotIn(b"targetNode.textContent = route.target;", response.data)

    def test_driver_routing_css_uses_wide_arrows_without_sidebars(self):
        css = Path("app/static/css/base.css").read_text()
        legacy_card_bar_block = css.split(
            ".neosektor-driver-wave-card::before {",
            1,
        )[1].split("}", 1)[0]
        wave_bar_block = css.split(
            ".blueprint-neosektor .driver-wave::before {",
            1,
        )[1].split("}", 1)[0]
        arrow_block = css.split(
            ".blueprint-neosektor .driver-arrow {",
            1,
        )[1].split("}", 1)[0]
        target_block = css.split(
            ".blueprint-neosektor .driver-target strong {",
            1,
        )[1].split("}", 1)[0]
        instruction_block = css.split(
            ".blueprint-neosektor .driver-instruction {",
            1,
        )[1].split("}", 1)[0]
        shaft_block = css.split(
            ".blueprint-neosektor .driver-arrow::before {",
            1,
        )[1].split("}", 1)[0]
        head_block = css.rsplit(
            ".blueprint-neosektor .driver-arrow::after {",
            1,
        )[1].split("}", 1)[0]
        mobile_arrow_block = css.rsplit(
            ".blueprint-neosektor .driver-arrow {",
            1,
        )[1].split("}", 1)[0]

        self.assertIn("content: none;", legacy_card_bar_block)
        self.assertIn("content: none;", wave_bar_block)
        self.assertIn("width: min(96%, 700px);", arrow_block)
        self.assertIn("font-size: 0;", arrow_block)
        self.assertIn("order: 1;", arrow_block)
        self.assertIn("order: 2;", target_block)
        self.assertIn("font-size: 0.72em;", instruction_block)
        self.assertIn("width: 100%;", shaft_block)
        self.assertIn("clip-path: polygon", shaft_block)
        self.assertIn("linear-gradient(90deg, #720812", shaft_block)
        self.assertIn("content: none;", head_block)
        self.assertNotIn("border-right:", head_block)
        self.assertIn("scaleX(-1)", css)
        self.assertIn("font-size: clamp(1.08rem, 3.55vw, 1.82rem);", css)
        self.assertIn("font-size: clamp(1.52rem, 4.65vw, 2.85rem);", css)
        self.assertIn("font-size: 0.92rem;", css)
        self.assertIn("width: 98%;", mobile_arrow_block)

    def test_driver_routing_blocks_user_without_view_permission(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.driver_routing.view"
        ).one()
        view_rule.minimum_role = "simulator"
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
        self.assertEqual(payload["state"]["routing"]["routes"]["first"]["east_count"], 7)
        self.assertEqual(payload["state"]["routing"]["routes"]["second"]["east_count"], 2)
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
        self.assertIn(b'href="/neosektor"', driver_routing.data)
        self.assertIn(b"Back", driver_routing.data)
        self.assertNotIn(b'href="/neosektor/driver-routing"', driver_routing.data)
        self.assertNotIn(b'aria-current="page"', driver_routing.data)

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
        ballmat_response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {"first": {"count": 6}},
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        self.assertEqual(ballmat_response.status_code, 403)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

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

    def test_tunnel_conductor_can_update_shared_ballmat_counts(self):
        self._login_approved_user(role="simulator")

        east_response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 4},
                    "second": {"count": 2},
                },
                "open_bays": 3,
                "bay_statuses": {},
            },
        )
        west_response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 7},
                    "second": {"count": 1},
                },
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        self.assertEqual(east_response.status_code, 200)
        self.assertEqual(west_response.status_code, 200)
        state = west_response.get_json()["state"]
        self.assertEqual(state["sides"]["east"]["waves"][0]["count"], 4)
        self.assertEqual(state["sides"]["east"]["waves"][1]["count"], 2)
        self.assertEqual(state["sides"]["east"]["open_bays"], 3)
        self.assertEqual(state["sides"]["west"]["waves"][0]["count"], 7)
        self.assertEqual(state["sides"]["west"]["waves"][1]["count"], 1)
        self.assertEqual(state["sides"]["west"]["open_bays"], 2)
        self.assertEqual(
            NeoSektorBallmatWaveCount.query.filter_by(
                side="EAST",
                wave_name="1ST WAVE",
            ).one().count,
            4,
        )
        self.assertEqual(
            NeoSektorOpenBayState.query.filter_by(side="WEST").one().open_count,
            2,
        )

    def test_ebm_and_wbm_updates_propagate_to_tunnel_conductor(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 5, "status": "Light"},
                    "second": {"count": 6, "status": "Moderate"},
                },
                "open_bays": 4,
                "bay_statuses": {},
            },
        )
        self.client.post(
            "/neosektor/ballmat/update?side=west",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 8, "status": "Moderate"},
                    "second": {"count": 9, "status": "Full"},
                },
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        response = self.client.get("/neosektor/tunnel-conductor/state")

        self.assertEqual(response.status_code, 200)
        state = response.get_json()["state"]
        self.assertEqual(state["sides"]["east"]["waves"][0]["count"], 5)
        self.assertEqual(state["sides"]["east"]["waves"][1]["count"], 6)
        self.assertEqual(state["sides"]["east"]["open_bays"], 4)
        self.assertEqual(state["sides"]["west"]["waves"][0]["count"], 8)
        self.assertEqual(state["sides"]["west"]["waves"][1]["count"], 9)
        self.assertEqual(state["sides"]["west"]["open_bays"], 1)

    def test_tunnel_conductor_updates_propagate_to_ballmat_live_counts_and_driver_routing(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 11},
                    "second": {"count": 3},
                },
                "open_bays": 0,
                "bay_statuses": {},
            },
        )
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {
                    "first": {"count": 6},
                    "second": {"count": 4},
                },
                "open_bays": 2,
                "bay_statuses": {},
            },
        )

        ballmat_state = self.client.get("/neosektor/ballmat/state").get_json()["state"]
        live_counts = self.client.get("/neosektor")
        driver_state = self.client.get("/neosektor/driver-routing/state").get_json()["state"]
        ebm_page = self.client.get("/neosektor/ebm")
        wbm_page = self.client.get("/neosektor/wbm")

        self.assertEqual(ballmat_state["sides"]["east"]["waves"][0]["count"], 11)
        self.assertEqual(ballmat_state["sides"]["west"]["waves"][0]["count"], 6)
        self.assertEqual(ballmat_state["waves"][0]["unloaded"], 17)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b">11<", live_counts.data)
        self.assertIn(b">6<", live_counts.data)
        self.assertEqual(driver_state["routing"]["routes"]["first"]["east_count"], 11)
        self.assertEqual(driver_state["routing"]["routes"]["first"]["west_count"], 6)
        self.assertIn(b'value="11"', ebm_page.data)
        self.assertIn(b'value="6"', wbm_page.data)

    def test_tunnel_conductor_no_longer_exposes_ballmat_count_delta_endpoint(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/delta",
            json={"side": "east", "wave": "first", "delta": 1},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 0)

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
        self.assertIn(b"Live Counts", ebm.data)
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

    def test_ballmat_main_counts_and_open_bays_clamp_to_99(self):
        self._login_approved_user(role="operator")
        self.client.get("/neosektor/ebm")

        response = self.client.post(
            "/neosektor/ballmat/update?side=east",
            json={
                "side": "east",
                "waves": {
                    "first": {"count": 250, "status": "Light"},
                    "second": {"count": 125, "status": "Moderate"},
                },
                "open_bays": 150,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        east_state = response.get_json()["state"]["sides"]["east"]
        self.assertEqual(east_state["waves"][0]["count"], 99)
        self.assertEqual(east_state["waves"][1]["count"], 99)
        self.assertEqual(east_state["open_bays"], 99)

    def test_tunnel_left_to_arrive_clamps_to_999_and_offset_to_20(self):
        self._login_approved_user(role="simulator")

        wave_response = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "first", "delta": 1500},
        )
        offset_response = self.client.post(
            "/neosektor/tunnel-conductor/offset",
            json={"west_offset": 99},
        )

        self.assertEqual(wave_response.status_code, 200)
        self.assertEqual(wave_response.get_json()["state"]["waves"][0]["planned"], 999)
        self.assertEqual(offset_response.status_code, 200)
        self.assertEqual(offset_response.get_json()["state"]["routing"]["west_offset"], 20)

    def test_left_to_unload_matches_standalone_open_bay_modifier_math(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "first", "delta": 10},
        )
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {"first": {"count": 3}, "second": {"count": 0}},
                "open_bays": 2,
                "bay_statuses": {},
            },
        )
        response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {"first": {"count": 4}, "second": {"count": 0}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        first_wave = response.get_json()["state"]["waves"][0]
        self.assertEqual(first_wave["left"], 59)

    def test_all_up_transitions_to_down_after_15_minutes(self):
        self._login_approved_user(role="watcher")
        initial_response = self.client.get("/neosektor/live-counts/state")
        self.assertEqual(initial_response.status_code, 200)
        self.assertEqual(initial_response.get_json()["state"]["waves"][0]["left"], "ALL UP")

        first_wave = NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one()
        first_wave.all_up_started_at = datetime.utcnow() - timedelta(minutes=16)
        db.session.commit()

        response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["waves"][0]["left"], "DOWN")

    def test_second_wave_waits_on_first_wave_all_up_timer(self):
        self._login_approved_user(role="simulator")
        self.client.get("/neosektor/live-counts/state")
        self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "second", "delta": 5},
        )

        response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["waves"][1]["left"], "-")

    def test_second_wave_uses_open_bays_and_modifier_after_first_wave_down(self):
        self._login_approved_user(role="simulator")
        self.client.get("/neosektor/live-counts/state")
        first_wave = NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one()
        first_wave.all_up_started_at = datetime.utcnow() - timedelta(minutes=16)
        db.session.commit()
        self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "second", "delta": 10},
        )
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {"first": {"count": 0}, "second": {"count": 5}},
                "open_bays": 2,
                "bay_statuses": {},
            },
        )
        response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {"first": {"count": 0}, "second": {"count": 5}},
                "open_bays": 1,
                "bay_statuses": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["waves"][0]["left"], "DOWN")
        self.assertEqual(response.get_json()["state"]["waves"][1]["left"], 54)

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

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Live Counts", response.data)
        self.assertIn(b"data-live-counts", response.data)
        self.assertIn(b"ALL IN", response.data)
        self.assertIn(b"ALL UP", response.data)
        self.assertEqual(response.data.count(b'class="is-word"'), 4)
        self.assertIn(b"Left to Unload", response.data)
        self.assertIn(b"1ST WAVE", response.data)
        self.assertIn(b"2ND WAVE", response.data)
        self.assertIn(b"East Ballmat", response.data)
        self.assertIn(b"West Ballmat", response.data)
        self.assertIn(b"East Bays", response.data)
        self.assertIn(b"West Bays", response.data)
        self.assertIn(b"Empty", response.data)
        self.assertIn(b'href="/logout"', response.data)
        self.assertIn(b'data-state-url="/neosektor/live-counts/state"', response.data)
        self.assertNotIn(b'href="/neosektor/live-counts"', response.data)
        self.assertNotIn(b"Operations Menu", response.data)
        self.assertNotIn(b"view-bay-dashboard", response.data)
        self.assertNotIn(b"header-link-static", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)
        self.assertEqual(NeoSektorSortState.query.count(), 1)
        self.assertEqual(NeoSektorWaveState.query.count(), 2)
        self.assertEqual(NeoSektorBallmatCount.query.count(), 2)
        self.assertEqual(NeoSektorBallmatWaveCount.query.count(), 4)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 2)
        self.assertEqual(NeoSektorBayStatus.query.count(), 5)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 3)

    def test_live_counts_state_endpoint_is_available_to_watcher(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("waves", payload["state"])
        self.assertIn("sides", payload["state"])
        self.assertEqual(payload["state"]["sides"]["east"]["bays"][0]["bay_name"], "Bay 1")

    def test_live_counts_css_keeps_bay_status_cards_readable(self):
        css = Path("app/static/css/base.css").read_text()
        wave_metric_block = css.split(
            ".blueprint-neosektor .neosektor-live-wave-row .wave-metrics div {",
            1,
        )[1].split("}", 1)[0]
        live_label_block = css.split(
            ".blueprint-neosektor .neosektor-live-ballmat-row .counter-card > span,",
            1,
        )[1].split("}", 1)[0]
        ballmat_column_block = css.split(
            ".blueprint-neosektor .neosektor-live-ballmat-row .ops-column {\n"
            "    display: grid;",
            1,
        )[1].split("}", 1)[0]
        ballmat_card_block = css.split(
            ".blueprint-neosektor .neosektor-live-ballmat-row .counter-card {",
            1,
        )[1].split("}", 1)[0]
        ballmat_count_block = css.split(
            ".blueprint-neosektor .neosektor-live-ballmat-row .readonly-count {",
            1,
        )[1].split("}", 1)[0]
        column_bays_block = css.split(
            ".blueprint-neosektor .neosektor-live-column-bays {",
            1,
        )[1].split("}", 1)[0]
        bay_card_block = css.split(
            ".blueprint-neosektor .neosektor-live-column-bays .bay-card {\n"
            "    box-sizing: border-box;",
            1,
        )[1].split("}", 1)[0]
        bay_status_block = css.split(
            ".blueprint-neosektor .neosektor-live-column-bays .bay-card strong {",
            1,
        )[1].split("}", 1)[0]

        self.assertIn("--neosektor-live-board-width: 590px;", css)
        self.assertIn("width: min(100%, var(--neosektor-live-board-width));", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", css)
        self.assertIn("min-height: calc(100svh - 84px);", css)
        self.assertIn(
            ".blueprint-neosektor .neosektor-live-wave-row .wave-metrics strong",
            css,
        )
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertNotIn("neosektor-live-bay-row", css)
        self.assertIn("grid-template-rows: auto repeat(3, minmax(56px, auto)) minmax(0, 1fr);", ballmat_column_block)
        self.assertIn("min-height: 58px;", ballmat_card_block)
        self.assertIn("line-height: 0.9;", ballmat_count_block)
        self.assertIn("display: flex;", wave_metric_block)
        self.assertIn("flex-direction: row;", wave_metric_block)
        self.assertIn("align-items: center;", wave_metric_block)
        self.assertIn("justify-content: space-between;", wave_metric_block)
        self.assertIn("width: 100%;", bay_card_block)
        self.assertIn("overflow: hidden;", bay_card_block)
        self.assertIn("align-content: start;", column_bays_block)
        self.assertIn("width: 100%;", column_bays_block)
        self.assertIn("color: var(--neo-silver);", live_label_block)
        self.assertIn("width: 100%;", ballmat_count_block)
        self.assertIn("text-align: center;", ballmat_count_block)
        self.assertIn("white-space: nowrap;", bay_status_block)
        self.assertIn("overflow-wrap: normal;", bay_status_block)
        self.assertIn("font-size: clamp(0.72rem, 1.8vw, 0.96rem);", bay_status_block)
        self.assertNotIn("overflow-wrap: anywhere;", bay_status_block)
        self.assertIn("text-transform: none;", bay_status_block)

    def test_neosektor_mobile_header_css_uses_compact_text_controls(self):
        css = Path("app/static/css/base.css").read_text()
        topbar_block = css.rsplit(
            "body.blueprint-neosektor.neosektor-fixed-header .topbar {",
            1,
        )[1].split("}", 1)[0]
        logo_block = css.rsplit(
            "body.blueprint-neosektor.neosektor-fixed-header "
            ".motherbrain-header-logo-link {",
            1,
        )[1].split("}", 1)[0]
        switcher_block = css.rsplit(
            "body.blueprint-neosektor.neosektor-fixed-header .character-switcher {",
            1,
        )[1].split("}", 1)[0]
        logout_block = css.rsplit(
            "body.blueprint-neosektor.neosektor-fixed-header .logout-link {",
            1,
        )[1].split("}", 1)[0]
        menu_button_block = css.rsplit(
            "body.blueprint-neosektor.neosektor-fixed-header .motherbrain-menu-button {",
            1,
        )[1].split("}", 1)[0]
        operator_header_block = css.split(
            "body.blueprint-neosektor.neosektor-ballmat-operator-page "
            ".neosektor-standalone-header.app-header,",
            1,
        )[1].split("}", 1)[0]
        operator_switcher_block = css.split(
            "body.blueprint-neosektor.neosektor-ballmat-operator-page "
            ".character-switcher-standalone .character-switcher-trigger,",
            1,
        )[1].split("}", 1)[0]

        self.assertIn("grid-template-rows: auto;", topbar_block)
        self.assertIn("display: none;", logo_block)
        self.assertIn("grid-column: 3;", switcher_block)
        self.assertIn("grid-row: 1;", switcher_block)
        self.assertIn("width: auto;", switcher_block)
        self.assertIn("display: none;", logout_block)
        self.assertIn("grid-column: 4;", menu_button_block)
        self.assertIn("height: var(--mobile-node-banner-button-height);", menu_button_block)
        self.assertIn("display: grid;", operator_header_block)
        self.assertIn("grid-template-columns: minmax(94px, 1fr)", operator_header_block)
        self.assertIn("minmax(86px, auto) auto;", operator_header_block)
        self.assertIn("grid-column: 3;", css)
        self.assertIn(".mobile-banner-logout {\n        display: none !important;", css)
        self.assertIn("white-space: nowrap;", operator_switcher_block)

    def test_ballmat_operator_css_keeps_open_bays_equal_to_wave_rows(self):
        css = Path("app/static/css/base.css").read_text()
        variables_block = css.split(
            ".blueprint-neosektor.neosektor-ballmat-operator-page {",
            1,
        )[1].split("}", 1)[0]
        counter_card_block = css.split(
            ".blueprint-neosektor.neosektor-ballmat-operator-page .counter-card {",
            1,
        )[1].split("}", 1)[0]
        counter_control_block = css.split(
            ".blueprint-neosektor.neosektor-ballmat-operator-page .counter-control {",
            1,
        )[1].split("}", 1)[0]
        open_bay_control_block = css.split(
            ".blueprint-neosektor.neosektor-ballmat-operator-page "
            ".neosektor-open-bay-control .counter-control {",
            1,
        )[1].split("}", 1)[0]
        count_height_block = css.split(
            ".blueprint-neosektor.neosektor-ballmat-operator-page .counter-control button,\n"
            ".blueprint-neosektor.neosektor-ballmat-operator-page .counter-number,\n"
            ".blueprint-neosektor.neosektor-ballmat-operator-page .readonly-count,\n"
            ".blueprint-neosektor.neosektor-ballmat-operator-page "
            ".neosektor-open-bay-control .readonly-count {",
            1,
        )[1].split("}", 1)[0]
        count_size_block = css.rsplit(
            ".blueprint-neosektor.neosektor-ballmat-operator-page .counter-number,\n"
            ".blueprint-neosektor.neosektor-ballmat-operator-page .readonly-count,\n"
            ".blueprint-neosektor.neosektor-ballmat-operator-page "
            ".neosektor-open-bay-control .readonly-count {",
            1,
        )[1].split("}", 1)[0]

        self.assertIn("--neosektor-ballmat-count-card-height: 92px;", variables_block)
        self.assertIn("--neosektor-ballmat-count-control-height: 58px;", variables_block)
        self.assertIn("--neosektor-ballmat-count-size: clamp(1.82rem, 6vw, 2.42rem);", variables_block)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr);", counter_card_block)
        self.assertIn("min-height: var(--neosektor-ballmat-count-card-height);", counter_card_block)
        self.assertIn("min-height: var(--neosektor-ballmat-count-control-height);", counter_control_block)
        self.assertIn("min-height: var(--neosektor-ballmat-count-control-height);", count_height_block)
        self.assertIn("grid-template-columns: 44px minmax(0, 1fr) 44px;", open_bay_control_block)
        self.assertIn("font-size: var(--neosektor-ballmat-count-size);", count_size_block)
        self.assertIn("line-height: 0.9;", count_size_block)

    def test_neosektor_index_is_live_counts_and_compat_route_redirects(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Live Counts", dashboard.data)
        self.assertIn(b"data-live-counts", dashboard.data)
        self.assertIn(b'href="/neosektor"', dashboard.data)
        self.assertIn(b'aria-current="page"', dashboard.data)
        self.assertNotIn(b'href="/neosektor/live-counts"', dashboard.data)
        self.assertEqual(live_counts.status_code, 302)
        self.assertEqual(live_counts.location, "/neosektor")

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
        self.assertEqual(live_counts.status_code, 302)
        self.assertEqual(live_counts.location, "/neosektor")

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
