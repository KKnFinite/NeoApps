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
        self.assertIn(b"OPERATIONAL OVERVIEW", response.data)
        self.assertIn(b"ACTIVE GATEWAY", response.data)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"VIEW OUTBOUND", response.data)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"TUG ASSIGNMENTS", response.data)
        self.assertIn(b'<a class="neoermac-menu-link" href="/neoermac/door-view">DOOR VIEW</a>', response.data)
        self.assertIn(b'<strong>OPEN</strong>', response.data)
        self.assertNotIn(b"COMING SOON", response.data)
        self.assertIn(b"BACK TO", response.data)
        self.assertIn(b'class="brand-inline-name neo-node-name node-gateway"', response.data)
        self.assertNotIn(b"RFD NEONODE", response.data)
        self.assertNotIn(b'<nav class="neoermac-menu"', response.data)

    def test_neoermac_menu_links_work(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/building-lineup"'), 2)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/outbound"'), 2)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/door-view"'), 2)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/tug-assignments"'), 2)
        self.assertIn(b'href="/rfd"', response.data)

    def test_placeholder_pages_render(self):
        self._login_approved_user()
        expected_pages = {
            "/neoermac/outbound": b"VIEW OUTBOUND",
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
        self.assertIn(b"N123UP", response.data)
        self.assertIn(b"A12", response.data)
        self.assertIn(b"PLANNED Pure", response.data)
        self.assertIn(b"01:20", response.data)
        self.assertIn(b"No tugs assigned yet.", response.data)
        self.assertIn(b"No active on-the-way events.", response.data)

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
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "sdf",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "ont",
                "lineup_green_runout_west_destination_2": "",
            },
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved.east_destination_1, "SDF")
        self.assertEqual(saved.west_destination_1, "ONT")

        reload_response = self.client.get("/neoermac/building-lineup")
        self.assertIn(b'<option value="SDF" selected', reload_response.data)
        self.assertIn(b'<option value="ONT" selected', reload_response.data)

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

    def _add_operation_departure(self, flight_number, destination, tail=None, parking=None):
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
            )
            db.session.add(operation)
            db.session.flush()

        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=operation.sort_name,
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number=flight_number,
            origin=self.gateway.code,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 11, 2, 15),
            planned_datetime_utc=datetime(2026, 6, 11, 7, 15),
            planned_source="master",
            assigned_tail_number=tail,
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
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
            "/neoermac/door-view",
            "/neoermac/tug-assignments",
        )

    def _door_options(self, response):
        return re.findall(rb'<option value="(D\d+)"', response.data)

    def _door_select_html(self, response):
        match = re.search(rb'<select id="door-select".*?</select>', response.data, re.S)
        self.assertIsNotNone(match)
        return match.group(0)


if __name__ == "__main__":
    unittest.main()
