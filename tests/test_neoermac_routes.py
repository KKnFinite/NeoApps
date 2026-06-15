import re
import unittest
from datetime import date, datetime, time, timedelta

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    MasterFlightSchedule,
    NeoErmacBuildingLineup,
    NeoErmacDoorPull,
    NeoErmacUldRequest,
    NeoNode,
    NeoSektorUldOnTheWayEvent,
    PermissionRule,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


class NeoErmacRoutesTest(unittest.TestCase):
    REAL_OUTBOUND_DOORS = (
        b"D1",
        b"D4",
        b"D6",
        b"D9",
        b"D13",
        b"D17",
        b"D21",
        b"D24",
        b"D26",
        b"D29",
        b"D32",
        b"D34",
        b"D37",
    )

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

    def test_unauthenticated_users_cannot_access_neoermac_pages(self):
        for path in self._neoermac_paths():
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_authenticated_user_with_neoermac_access_can_access_menu(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoErmac", response.data)
        self.assertIn(b'src="/static/images/neoermac_logo1_large.png"', response.data)
        self.assertIn(b'srcset="/static/images/neoermac_logo1_medium.png"', response.data)
        self.assertIn(b'srcset="/static/images/neoermac_logo1_small.png"', response.data)
        self.assertIn(b"UPCOMING OUTBOUND PULLS", response.data)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"VIEW OUTBOUND", response.data)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"TUG ASSIGNMENTS", response.data)
        self.assertIn(b'<a class="neoermac-menu-link" href="/neoermac/door-view">DOOR VIEW</a>', response.data)
        self.assertNotIn(b"OPERATIONAL OVERVIEW", response.data)
        self.assertNotIn(b"ACTIVE GATEWAY", response.data)
        self.assertNotIn(b"OUTBOUND</span>", response.data)
        self.assertNotIn(b'<strong>OPEN</strong>', response.data)
        self.assertNotIn(b"COMING SOON", response.data)
        self.assertIn(b"BACK TO", response.data)
        self.assertIn(b'class="brand-inline-name neo-node-name node-gateway"', response.data)
        self.assertNotIn(b"RFD NEONODE", response.data)
        self.assertNotIn(b'<nav class="neoermac-menu"', response.data)

    def test_neoermac_menu_links_work(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/building-lineup"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/view-outbound"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/door-view"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/tug-assignments"'), 1)
        self.assertIn(b'href="/rfd"', response.data)

    def test_neoermac_menu_shows_no_current_sort_state_without_operation(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No current sort operation", response.data)
        self.assertNotIn(b"neoermac-upcoming-row", response.data)

    def test_neoermac_menu_shows_west_and_east_upcoming_pull_lists(self):
        self._assign_lineup_destination("runout_4", "east_destination_1", "BOS")
        self._assign_lineup_destination("runout_10", "west_destination_2", "SDF")
        self._add_operation_departure(
            "UPS701",
            "BOS",
            tail="N701UP",
            parking="D13",
            pure_pull_time_local=time(1, 10),
            first_mix_pull_time_local=time(1, 20),
            final_mix_pull_time_local=time(1, 30),
        )
        self._add_operation_departure(
            "UPS702",
            "SDF",
            tail="N702UP",
            parking="D32",
            pure_pull_time_local=time(1, 15),
            first_mix_pull_time_local=time(1, 25),
            final_mix_pull_time_local=time(1, 35),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac")

        west_html = self._upcoming_side_html(response, "West")
        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b"West upcoming pulls"), response.data.index(b"East upcoming pulls"))
        self.assertIn(b"SDF / N702UP / D32", west_html)
        self.assertNotIn(b"UPS702 / SDF", west_html)
        self.assertIn(b"D32-D34 BRN/WHT BELT", west_html)
        self.assertNotIn(b"D32-D34 WEST BRN/WHT BELT", west_html)
        self.assertNotIn(b"BOS / N701UP / D13", west_html)
        self.assertIn(b"BOS / N701UP / D13", east_html)
        self.assertNotIn(b"UPS701 / BOS", east_html)
        self.assertIn(b"D13-D17 BRN/ORG BELT", east_html)
        self.assertNotIn(b"D13-D17 EAST BRN/ORG BELT", east_html)
        self.assertNotIn(b"SDF / N702UP / D32", east_html)

    def test_neoermac_menu_combines_duplicate_belt_side_entries(self):
        self._assign_lineup_destination("runout_3", "east_destination_2", "DEN")
        self._assign_lineup_destination("runout_3", "west_destination_2", "DEN")
        self._add_operation_departure(
            "UPS810",
            "DEN",
            pure_pull_time_local=time(1, 49),
            first_mix_pull_time_local=time(2, 0),
            final_mix_pull_time_local=time(2, 11),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac")

        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(east_html.count(b"DEN / - / -"), 3)
        self.assertNotIn(b"UPS810 / DEN", east_html)
        self.assertEqual(east_html.count(b"D9-D13 BRN/WHT BELT"), 3)
        self.assertNotIn(b"D9-D13 EAST BRN/WHT BELT", east_html)
        self.assertNotIn(b"D9-D13 WEST BRN/WHT BELT", east_html)

    def test_neoermac_menu_keeps_different_destinations_on_same_belt(self):
        self._assign_lineup_destination("runout_3", "east_destination_2", "DEN")
        self._assign_lineup_destination("runout_3", "west_destination_2", "OMA")
        self._add_operation_departure("UPS811", "DEN")
        self._add_operation_departure("UPS812", "OMA")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac")

        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEN / - / -", east_html)
        self.assertIn(b"OMA / - / -", east_html)
        self.assertNotIn(b"UPS811 / DEN", east_html)
        self.assertNotIn(b"UPS812 / OMA", east_html)
        self.assertEqual(east_html.count(b"D9-D13 BRN/WHT BELT"), 5)

    def test_neoermac_menu_removes_actual_and_no_pull_items(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure(
            "UPS703",
            "SDF",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        save_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:25",
                "no_first_mix_0": "on",
            },
            follow_redirects=False,
        )
        response = self.client.get("/neoermac")

        west_html = self._upcoming_side_html(response, "West")
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"01:20", west_html)
        self.assertNotIn(b"Pure", west_html)
        self.assertNotIn(b"01:40", west_html)
        self.assertNotIn(b"1st Mix", west_html)
        self.assertIn(b"01:55", west_html)
        self.assertIn(b"2nd Mix", west_html)

    def test_neoermac_menu_sorts_upcoming_pulls_and_limits_each_side(self):
        assignments = (
            ("runout_10", "east_destination_1", "AAA"),
            ("runout_10", "east_destination_2", "BBB"),
            ("runout_11", "west_destination_1", "CCC"),
        )
        for index, (_runout_key, _field_name, destination) in enumerate(assignments):
            self._assign_lineup_destination(_runout_key, _field_name, destination)
            self._add_operation_departure(
                f"UPS71{index}",
                destination,
                pure_pull_time_local=time(1, 20 + index),
                first_mix_pull_time_local=time(1, 40 + index),
                final_mix_pull_time_local=time(1, 55 + index),
            )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac")

        west_html = self._upcoming_side_html(response, "West")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(west_html.count(b"neoermac-upcoming-row"), 5)
        self.assertLess(west_html.index(b"01:20"), west_html.index(b"01:21"))
        self.assertLess(west_html.index(b"01:21"), west_html.index(b"01:22"))
        self.assertNotIn(b"01:57", west_html)

    def test_placeholder_pages_render(self):
        self._login_approved_user()
        expected_pages = {
            "/neoermac/tug-assignments": b"TUG ASSIGNMENTS",
        }

        for path, title in expected_pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(title, response.data)
                self.assertIn(b'aria-label="BACK TO NeoErmac"', response.data)
                self.assertIn(b"OPERATIONAL LOGIC WILL BE ADDED IN A LATER PASS.", response.data)

    def test_door_view_route_loads_for_view_authorized_user(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"Select a door.", response.data)
        self.assertIn(b'<option value="D34"', response.data)
        self.assertIn(b'class="neoermac-door-selector"', response.data)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(b"PLACEHOLDER SHELL", response.data)
        self.assertNotIn(b"OPERATIONAL LOGIC WILL BE ADDED IN A LATER PASS.", response.data)

    def test_door_view_dropdown_includes_only_real_outbound_doors(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._door_options(response), list(self.REAL_OUTBOUND_DOORS))
        for fake_door in (b"D2", b"D3", b"D5", b"D7", b"D8", b"D10", b"D11", b"D12"):
            self.assertNotIn(b'<option value="' + fake_door + b'"', response.data)

    def test_door_view_rendered_select_has_exact_real_door_options(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view")

        self.assertEqual(response.status_code, 200)
        select_html = self._door_select_html(response)
        self.assertEqual(self._door_options(response), list(self.REAL_OUTBOUND_DOORS))
        self.assertIn(b'data-canonical-doors=', select_html)
        for fake_door in (b"D2", b"D3", b"D5", b"D7", b"D8", b"D10", b"D11", b"D12"):
            self.assertNotIn(b'value="' + fake_door + b'"', select_html)

    def test_door_view_invalid_door_query_shows_empty_state(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=DX")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._door_options(response), list(self.REAL_OUTBOUND_DOORS))
        self.assertIn(b"Select a door.", response.data)
        self.assertNotIn(b"SELECTED DOOR", response.data)
        self.assertNotIn(b"DX", response.data)

    def test_door_view_unauthorized_user_is_blocked_by_view_permission(self):
        view_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.view").one()
        edit_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.edit").one()
        view_rule.minimum_role = "master"
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/neoermac", response.location)

    def test_door_view_selected_door_shows_building_lineup_destinations(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._assign_lineup_destination("runout_11", "west_destination_2", "ONT")
        self._add_operation_departure("UPS301", "SDF", tail="N123UP", parking="A12")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<strong>D34</strong>', response.data)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b"ONT", response.data)
        self.assertIn(b"UPS301", response.data)
        self.assertIn(b"LIVE SORT", response.data)
        self.assertIn(b"NO FLIGHT DATA", response.data)
        self.assertIn(b"N123UP", response.data)
        self.assertIn(b"A12", response.data)
        self.assertIn(b"PLANNED Pure", response.data)
        self.assertIn(b"WINDOW TBD", response.data)
        self.assertIn(b"01:20", response.data)
        self.assertLess(response.data.index(b"UPS301"), response.data.index(b"ONT"))
        self.assertIn(b"No tugs assigned yet.", response.data)
        self.assertIn(b"No active on-the-way events.", response.data)

    def test_door_view_displays_window_adjusted_planned_pull_times(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure("UPS401", "SDF", window_minutes=20)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS401", response.data)
        self.assertIn(b"WINDOW Pure", response.data)
        self.assertIn(b"WINDOW 20 MIN", response.data)
        self.assertIn(b"01:40", response.data)
        self.assertIn(b"BASE 01:20 +20 MIN", response.data)
        self.assertIn(b"02:00", response.data)
        self.assertIn(b"02:15", response.data)

    def test_door_view_view_only_user_cannot_save_pulls_or_uld_requests(self):
        edit_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.edit").one()
        edit_rule.minimum_role = "simulator"
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        pull_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:15",
            },
            follow_redirects=False,
        )
        uld_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "1",
                "uld_amp_count": "3",
            },
            follow_redirects=False,
        )

        self.assertEqual(pull_response.status_code, 403)
        self.assertEqual(uld_response.status_code, 403)
        self.assertEqual(NeoErmacDoorPull.query.count(), 0)
        self.assertEqual(NeoErmacUldRequest.query.count(), 0)

    def test_door_view_edit_user_can_save_actual_pull_and_no_pull_states(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:15",
                "actual_first_mix_0": "01:30",
                "no_first_mix_0": "on",
                "actual_second_mix_0": "01:55",
            },
            follow_redirects=False,
        )

        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved.actual_pure_pull_time_local, time(1, 15))
        self.assertTrue(saved.no_first_mix_pull)
        self.assertIsNone(saved.actual_first_mix_pull_time_local)
        self.assertFalse(saved.no_second_mix_pull)
        self.assertEqual(saved.actual_second_mix_pull_time_local, time(1, 55))

        reload_response = self.client.get("/neoermac/door-view?door=D34")
        self.assertIn(b'value="01:15"', reload_response.data)
        self.assertIn(b"checked", reload_response.data)

    def test_door_view_edit_user_can_create_and_update_uld_requested_counts(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        create_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "1",
                "uld_amp_count": "3",
                "setup_needed": "on",
            },
            follow_redirects=False,
        )
        update_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "4",
                "uld_a1_count": "0",
                "uld_amp_count": "1",
            },
            follow_redirects=False,
        )

        saved = NeoErmacUldRequest.query.filter_by(door="D34").one()
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(saved.a2_count, 4)
        self.assertEqual(saved.a1_count, 0)
        self.assertEqual(saved.amp_count, 1)
        self.assertFalse(saved.setup_needed)

    def test_door_view_state_reflects_request_changes_from_another_update_cycle(self):
        request_record = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D34",
            a2_count=1,
            a1_count=0,
            amp_count=2,
            setup_needed=False,
        )
        db.session.add(request_record)
        db.session.commit()
        self._login_approved_user(role="operator")

        first_response = self.client.get("/neoermac/door-view/state?door=D34")
        request_record.a2_count = 4
        request_record.a1_count = 2
        request_record.amp_count = 0
        request_record.setup_needed = True
        db.session.commit()
        second_response = self.client.get("/neoermac/door-view/state?door=D34")

        first_payload = first_response.get_json()
        second_payload = second_response.get_json()
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(first_payload["state"]["request"]["counts"]["A2"], 1)
        self.assertFalse(first_payload["state"]["request"]["setup_needed"])
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(
            second_payload["state"]["request"]["counts"],
            {"A2": 4, "A1": 2, "AMP": 0},
        )
        self.assertTrue(second_payload["state"]["request"]["setup_needed"])

    def test_door_view_state_reflects_active_on_the_way_events_and_excludes_expired(self):
        now = datetime.utcnow()
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

        response = self.client.get("/neoermac/door-view/state?door=D34")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["state"]["on_the_way_events"]), 1)
        self.assertEqual(payload["state"]["on_the_way_events"][0]["uld_type"], "A2")
        self.assertIn("1 A2 sent at", payload["state"]["on_the_way_events"][0]["label"])

    def test_door_view_state_requires_view_permission(self):
        view_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.view").one()
        edit_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.edit").one()
        view_rule.minimum_role = "master"
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view/state?door=D34")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["ok"])

    def test_door_view_displays_active_on_the_way_events(self):
        sent_at = datetime.utcnow()
        db.session.add(
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                door="D34",
                uld_type="A2",
                quantity=2,
                sent_at_utc=sent_at,
                expires_at_utc=sent_at + timedelta(minutes=5),
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f"2 A2s sent at {sent_at:%H:%M}".encode(), response.data)

    def test_door_view_uld_request_edits_do_not_clear_on_the_way_events(self):
        sent_at = datetime.utcnow()
        db.session.add(
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                door="D34",
                uld_type="AMP",
                quantity=1,
                sent_at_utc=sent_at,
                expires_at_utc=sent_at + timedelta(minutes=5),
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "1",
                "uld_a1_count": "0",
                "uld_amp_count": "0",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.count(), 1)
        reload_response = self.client.get("/neoermac/door-view?door=D34")
        self.assertIn(f"1 AMP sent at {sent_at:%H:%M}".encode(), reload_response.data)

    def test_building_lineup_page_renders_belt_map(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"neoermac-sequence-door", response.data)
        self.assertIn(b"Orange", response.data)
        self.assertIn(b"White/Blue", response.data)
        self.assertIn(b"Blue/Black", response.data)
        self.assertIn(b"neoermac-pull-edge", response.data)
        self.assertIn(b"Pure", response.data)
        self.assertIn(b"1st Mix", response.data)
        self.assertIn(b"2nd Mix", response.data)
        self.assertEqual(response.data.count(b"neoermac-belt-group"), 12)
        self.assertEqual(response.data.count(b"neoermac-sequence-door"), 13)
        self.assertIn(b"neoermac-belt-block", response.data)
        self.assertIn(b"neoermac-belt-destination-row", response.data)
        self.assertIn(b"D1", response.data)
        self.assertIn(b"D37", response.data)
        self.assertNotIn(b"BELT SECTION", response.data)
        self.assertNotIn(b"NORTH BELT", response.data)
        self.assertNotIn(b"SOUTH BELT", response.data)
        self.assertNotIn(b"EAST SIDE", response.data)
        self.assertNotIn(b"WEST SIDE", response.data)
        self.assertNotIn(b"Belt to", response.data)
        self.assertNotIn(b"BELT TO", response.data)
        self.assertNotIn(b"Green Runout", response.data)
        self.assertNotIn(b"Runout 1", response.data)
        self.assertNotIn(b"RUNOUT DESTINATION CONTROL", response.data)
        self.assertNotIn(b"EAST SIDE DESTINATIONS", response.data)
        self.assertNotIn(b"WEST SIDE DESTINATIONS", response.data)
        self.assertNotIn(b'<span class="neoermac-kicker"', response.data)
        self.assertIn(b"View Only", response.data)
        self.assertNotIn(b"SAVE BUILDING LINEUP", response.data)

    def test_building_lineup_displays_planned_pull_times_for_assigned_destination(self):
        self._add_master_departure(
            "UPS105",
            "SDF",
            pure_pull_time_local=time(1, 10),
            first_mix_pull_time_local=time(1, 25),
            final_mix_pull_time_local=time(1, 40),
        )
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"01:10", response.data)
        self.assertIn(b"01:25", response.data)
        self.assertIn(b"01:40", response.data)

    def test_building_lineup_displays_current_sort_mission_pull_times(self):
        self._assign_lineup_destination("green_runout", "east_destination_2", "SDF")
        self._add_operation_departure(
            "UPS205",
            "SDF",
            pure_pull_time_local=time(0, 55),
            first_mix_pull_time_local=time(1, 10),
            final_mix_pull_time_local=time(1, 25),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"00:55", response.data)
        self.assertIn(b"01:10", response.data)
        self.assertIn(b"01:25", response.data)

    def test_building_lineup_renders_pull_times_for_each_destination_side(self):
        self._add_master_departure("UPS210", "SDF")
        self._add_master_departure("UPS211", "ONT")
        self._assign_lineup_destination("green_runout", "east_destination_2", "SDF")
        self._assign_lineup_destination("green_runout", "west_destination_2", "ONT")
        self._add_operation_departure(
            "UPS210",
            "SDF",
            pure_pull_time_local=time(0, 55),
            first_mix_pull_time_local=time(1, 10),
            final_mix_pull_time_local=time(1, 25),
        )
        self._add_operation_departure(
            "UPS211",
            "ONT",
            pure_pull_time_local=time(2, 5),
            first_mix_pull_time_local=time(2, 20),
            final_mix_pull_time_local=time(2, 35),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")
        html = response.data.decode()
        upper_card = html.split('name="lineup_green_runout_east_destination_2"', 1)[
            1
        ].split("</label>", 1)[0]
        lower_card = html.split('name="lineup_green_runout_west_destination_2"', 1)[
            1
        ].split("</label>", 1)[0]

        self.assertEqual(response.status_code, 200)
        for expected_time in ("00:55", "01:10", "01:25"):
            self.assertIn(expected_time, upper_card)
            self.assertNotIn(expected_time, lower_card)
        for expected_time in ("02:05", "02:20", "02:35"):
            self.assertIn(expected_time, lower_card)
            self.assertNotIn(expected_time, upper_card)

    def test_building_lineup_missing_pull_times_show_clean_blanks(self):
        self._add_master_departure("UPS212", "PHX")
        self._assign_lineup_destination("green_runout", "east_destination_1", "PHX")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")
        html = response.data.decode()
        card = html.split('name="lineup_green_runout_east_destination_1"', 1)[1].split(
            "</label>",
            1,
        )[0]

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(card.count("<strong>--</strong>"), 3)

    def test_building_lineup_destination_options_come_from_master_departures(self):
        self._add_master_departure("UPS101", "sdf")
        self._add_master_departure("UPS102", "ont")
        self._add_master_arrival("UPS201", "dfw")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="SDF"', response.data)
        self.assertIn(b'<option value="ONT"', response.data)
        self.assertNotIn(b'<option value="DFW"', response.data)

    def test_user_with_building_lineup_edit_can_save_destinations(self):
        self._add_master_departure("UPS301", "sdf")
        self._add_master_departure("UPS302", "ont")
        self._add_master_departure("UPS303", "phx")
        self._add_master_departure("UPS304", "lax")
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "sdf",
                "lineup_green_runout_east_destination_2": "phx",
                "lineup_green_runout_west_destination_1": "ont",
                "lineup_green_runout_west_destination_2": "lax",
            },
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved.east_destination_1, "SDF")
        self.assertEqual(saved.east_destination_2, "PHX")
        self.assertEqual(saved.west_destination_1, "ONT")
        self.assertEqual(saved.west_destination_2, "LAX")

        reload_response = self.client.get("/neoermac/building-lineup")
        self.assertIn(b'<option value="SDF" selected', reload_response.data)
        self.assertIn(b'<option value="PHX" selected', reload_response.data)
        self.assertIn(b'<option value="ONT" selected', reload_response.data)
        self.assertIn(b'<option value="LAX" selected', reload_response.data)

    def test_building_lineup_destination_two_controls_are_editable(self):
        self._add_master_departure("UPS311", "SDF")
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="lineup_green_runout_east_destination_2"', response.data)
        self.assertIn(b'name="lineup_green_runout_west_destination_2"', response.data)
        self.assertNotIn(
            b'name="lineup_green_runout_east_destination_2" disabled',
            response.data,
        )
        self.assertNotIn(
            b'name="lineup_green_runout_west_destination_2" disabled',
            response.data,
        )

    def test_building_lineup_save_allows_blank_slots_and_clears_destinations(self):
        self._add_master_departure("UPS351", "SDF")
        db.session.commit()
        self._login_approved_user(role="simulator")

        self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "SDF",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "",
                "lineup_green_runout_west_destination_2": "",
            },
        )
        response = self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "",
                "lineup_green_runout_west_destination_2": "",
            },
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(saved.east_destination_1)
        self.assertIsNone(saved.east_destination_2)
        self.assertIsNone(saved.west_destination_1)
        self.assertIsNone(saved.west_destination_2)

    def test_user_with_building_lineup_view_can_open_read_only(self):
        self._add_master_departure("UPS401", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        read_only_response = self.client.get("/neoermac/building-lineup")
        self.assertEqual(read_only_response.status_code, 200)
        self.assertIn(b"View Only", read_only_response.data)
        self.assertIn(b"disabled", read_only_response.data)
        self.assertNotIn(b"SAVE BUILDING LINEUP", read_only_response.data)

    def test_view_only_user_cannot_post_building_lineup(self):
        self._add_master_departure("UPS402", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        self.client.get("/neoermac/building-lineup")

        save_response = self.client.post(
            "/neoermac/building-lineup",
            data={"lineup_green_runout_east_destination_1": "SDF"},
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(save_response.status_code, 403)
        self.assertIsNone(saved.east_destination_1)

    def test_user_without_building_lineup_view_cannot_open_page(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neoermac/building-lineup", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/neoermac", response.location)

    def test_ermac_route_is_not_used(self):
        self._login_approved_user()

        menu = self.client.get("/neoermac")
        response = self.client.get("/ermac")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'href="/ermac"', menu.data)

    def test_outbound_legacy_route_redirects_to_view_outbound(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neoermac/outbound", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/neoermac/view-outbound", response.location)

    def test_view_outbound_loads_for_watcher(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neoermac/view-outbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VIEW OUTBOUND", response.data)
        self.assertIn(b"WINDOW", response.data)
        self.assertNotIn(b"DISCHARGE", response.data)
        self.assertNotIn(b"SAVE", response.data)

    def test_view_outbound_renders_summary_and_door_actuals(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure(
            "UPS501",
            "SDF",
            tail="N501UP",
            parking="A14",
            window_minutes=20,
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        save_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:45",
                "actual_first_mix_0": "02:00",
                "no_first_mix_0": "on",
                "actual_second_mix_0": "02:20",
            },
            follow_redirects=False,
        )
        response = self.client.get("/neoermac/view-outbound")

        mission = SortDateMission.query.filter_by(destination="SDF").one()
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 45))
        self.assertIsNone(mission.actual_first_mix_pull_time_local)
        self.assertEqual(mission.actual_second_mix_pull_time_local, time(2, 20))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b"UPS501", response.data)
        self.assertIn(b"02:15", response.data)
        self.assertIn(b"D32-D34", response.data)
        self.assertIn(b"EAST BLU/BLU BELT", response.data)
        self.assertIn(b"01:20", response.data)
        self.assertIn(b"01:40", response.data)
        self.assertIn(b"BASE 01:20 +20 MIN", response.data)
        self.assertIn(b"01:45", response.data)
        self.assertIn(b"NO 1ST MIX", response.data)
        self.assertIn(b"02:20", response.data)
        self.assertIn(b"20 MIN", response.data)

    def test_view_outbound_sorts_by_planned_pull_time_and_handles_missing_data(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "east_destination_2", "ONT")
        self._assign_lineup_destination("runout_11", "west_destination_2", "PHX")
        self._add_operation_departure(
            "UPS601",
            "SDF",
            window_minutes=20,
            planned_datetime_local=datetime(2026, 6, 11, 2, 15),
            planned_datetime_utc=datetime(2026, 6, 11, 7, 15),
            pure_pull_time_local=time(1, 20),
        )
        self._add_operation_departure(
            "UPS602",
            "ONT",
            window_minutes=20,
            planned_datetime_local=datetime(2026, 6, 11, 1, 45),
            planned_datetime_utc=datetime(2026, 6, 11, 6, 45),
            pure_pull_time_local=time(0, 50),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/view-outbound")

        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b"UPS602"), response.data.index(b"UPS601"))
        self.assertLess(response.data.index(b"UPS601"), response.data.index(b"PHX"))
        self.assertIn(b"NO CURRENT SORT MISSION FOR PHX.", response.data)

    def _login_approved_user(self, role="watcher"):
        user = User(
            username=f"neoermac_{role}_user",
            email=f"neoermac_{role}@example.test",
            role="watcher",
        )
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()

        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=self.gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.flush()

        if role != "watcher":
            ermac = NeoNode.query.filter_by(code="ermac").one()
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=user.gateway_memberships[0].id,
                    node_id=ermac.id,
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

    def _add_master_departure(
        self,
        flight_number,
        destination,
        pure_pull_time_local=None,
        first_mix_pull_time_local=None,
        final_mix_pull_time_local=None,
    ):
        db.session.add(
            MasterFlightSchedule(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                mission_type="departure",
                flight_number=flight_number,
                origin=self.gateway.code,
                destination=destination,
                active=True,
                active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                planned_time_local=time(23, 0),
                timezone="America/Chicago",
                pure_pull_time_local=pure_pull_time_local,
                first_mix_pull_time_local=first_mix_pull_time_local,
                final_mix_pull_time_local=final_mix_pull_time_local,
            )
        )

    def _assign_lineup_destination(self, runout_key, field_name, destination):
        row = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key=runout_key,
        ).first()
        if row is None:
            row = NeoErmacBuildingLineup(
                gateway_id=self.gateway.id,
                runout_key=runout_key,
                runout_name=runout_key.replace("_", " ").title(),
            )
            db.session.add(row)
        setattr(row, field_name, destination)

    def _add_operation_departure(
        self,
        flight_number,
        destination,
        tail=None,
        parking=None,
        window_minutes=0,
        departure_status=None,
        wave="1",
        planned_datetime_local=None,
        planned_datetime_utc=None,
        pure_pull_time_local=None,
        first_mix_pull_time_local=None,
        final_mix_pull_time_local=None,
    ):
        operation = SortDateOperation.query.filter_by(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
        ).first()
        if operation is None:
            operation = SortDateOperation(
                gateway_id=self.gateway.id,
                sort_date=date(2026, 6, 11),
                gateway_code=self.gateway.code,
                sort_name="night",
                window_minutes=window_minutes,
            )
            db.session.add(operation)
            db.session.flush()
        else:
            operation.window_minutes = window_minutes

        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=operation.sort_name,
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="master",
            wave=wave,
            flight_number=flight_number,
            origin=self.gateway.code,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=planned_datetime_local or datetime(2026, 6, 11, 2, 15),
            planned_datetime_utc=planned_datetime_utc or datetime(2026, 6, 11, 7, 15),
            planned_source="master",
            assigned_tail_number=tail,
            departure_status=departure_status,
            pure_pull_time_local=pure_pull_time_local or time(1, 20),
            first_mix_pull_time_local=first_mix_pull_time_local or time(1, 40),
            final_mix_pull_time_local=final_mix_pull_time_local or time(1, 55),
        )
        db.session.add(mission)
        db.session.flush()

        if tail and parking:
            db.session.add(
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name=operation.sort_name,
                    tail_number=tail,
                    parking_position=parking,
                )
            )
        return mission

    def _add_master_arrival(self, flight_number, origin):
        db.session.add(
            MasterFlightSchedule(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                mission_type="arrival",
                flight_number=flight_number,
                origin=origin,
                destination=self.gateway.code,
                active=True,
                active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                planned_time_local=time(22, 0),
                timezone="America/Chicago",
            )
        )

    def _neoermac_paths(self):
        return (
            "/neoermac",
            "/neoermac/building-lineup",
            "/neoermac/outbound",
            "/neoermac/view-outbound",
            "/neoermac/door-view",
            "/neoermac/tug-assignments",
        )

    def _door_options(self, response):
        return re.findall(rb'<option value="(D\d+)"', response.data)

    def _door_select_html(self, response):
        match = re.search(rb'<select id="door-select".*?</select>', response.data, re.S)
        self.assertIsNotNone(match)
        return match.group(0)

    def _upcoming_side_html(self, response, side_name):
        side_label = f'aria-label="{side_name} upcoming pulls"'.encode()
        start = response.data.index(side_label)
        if side_name == "West":
            end = response.data.index(b'aria-label="East upcoming pulls"', start)
        else:
            end = response.data.index(b'<div class="neoermac-menu', start)
        return response.data[start:end]


if __name__ == "__main__":
    unittest.main()
