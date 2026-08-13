import re
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, text

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
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password
from app.services.sort_timeline import ensure_sort_timeline_settings
from app.services.time_display import format_local_hhmm


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
        self.assertIn(b"node-desktop-nav-page", response.data)
        self.assertIn(b"data-node-desktop-side-nav", response.data)
        self.assertIn(b'data-node-desktop-shell="ermac"', response.data)
        desktop_sidebar = response.data.split(b"data-node-desktop-side-nav", 1)[1].split(b"</aside>", 1)[0]
        self.assertIn(b'neoermac-inapp-256.png', desktop_sidebar)
        self.assertNotIn(b'neoermac-inapp-128.png', desktop_sidebar)
        sidebar_css = Path("app/static/css/base.css").read_text()
        self.assertIn("grid-template-rows: 220px auto;", sidebar_css)
        self.assertIn("width: 220px;", sidebar_css)
        self.assertIn("border-radius: 0;", sidebar_css)
        self.assertIn("mix-blend-mode: screen;", sidebar_css)
        self.assertIn("container-type: inline-size;", sidebar_css)
        self.assertIn("flex-wrap: wrap;", sidebar_css)
        self.assertIn("font-size: clamp(1.05rem, 7.5cqi, 1.45rem);", sidebar_css)
        self.assertIn("white-space: normal;", sidebar_css)
        self.assertIn(b'<span class="neo-page-title motherbrain-desktop-top-title-text">DASHBOARD</span>', response.data)
        self.assertIn(b'class="neoermac-dashboard-brand"', response.data)
        self.assertIn(b"neoermac-dashboard-title neo-brand-title", response.data)
        self.assertIn(b"neo-brand-title__neo", response.data)
        self.assertIn(b"neo-brand-title__node--ermac", response.data)
        self.assertNotIn(b"<h1>NeoErmac</h1>", response.data)
        self.assertIn(b'src="/static/images/icons/neoermac/inapp/neoermac-inapp-256.png"', response.data)
        self.assertNotIn(b"neoermac_logo1_large.png", response.data)
        self.assertNotIn(b"neoermac_logo1_medium.png", response.data)
        self.assertNotIn(b"neoermac_logo1_small.png", response.data)
        self.assertIn(b'src="/static/images/icons/neoermac/inapp/neoermac-inapp-128.png"', response.data)
        self.assertIn(b"neoermac-header-title", response.data)
        self.assertIn(b"data-node-desktop-dashboard", response.data)
        self.assertIn(b'data-node-dashboard="ermac"', response.data)
        self.assertIn(b'data-node-dashboard-tile="door-view"', response.data)
        self.assertIn(b'data-node-dashboard-tile="building-lineup"', response.data)
        self.assertIn(b'data-node-dashboard-tile="view-outbound"', response.data)
        self.assertIn(b'data-node-dashboard-tile="upcoming-pulls"', response.data)
        self.assertIn(b'data-node-dashboard-tile="tug-assignments"', response.data)
        self.assertIn(b"data-neoermac-mobile-dashboard", response.data)
        self.assertIn(b'data-neoermac-mobile-tile="door-view"', response.data)
        self.assertIn(b'data-neoermac-mobile-tile="building-lineup"', response.data)
        self.assertIn(b'data-neoermac-mobile-tile="view-outbound"', response.data)
        self.assertIn(b'data-neoermac-mobile-tile="upcoming-pulls"', response.data)
        self.assertIn(b'data-neoermac-mobile-tile="tug-assignments"', response.data)
        self.assertIn(b"UPCOMING PULLS", response.data)
        self.assertNotIn(b"UPCOMING OUTBOUND PULLS", response.data)
        self.assertNotIn(b"neoermac-upcoming-board", response.data)
        self.assertNotIn(b"System Status", response.data)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"VIEW OUTBOUND", response.data)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"TUG ASSIGNMENTS", response.data)
        self.assertNotIn(b"Door pulls, ULD requests, and on-the-way state.", response.data)
        self.assertNotIn(b"Assign destinations across doors and belts.", response.data)
        self.assertNotIn(b"Outbound flight, pull, and door context.", response.data)
        self.assertNotIn(b"Next 5 pulls by east and west side.", response.data)
        self.assertNotIn(b"Placeholder route for future tug workflow.", response.data)
        self.assertIn(b'href="/neoermac/upcoming-pulls"', response.data)
        self.assertNotIn(b"OPERATIONAL OVERVIEW", response.data)
        self.assertNotIn(b"ACTIVE GATEWAY", response.data)
        self.assertNotIn(b"OUTBOUND</span>", response.data)
        self.assertNotIn(b'<strong>OPEN</strong>', response.data)
        self.assertNotIn(b"COMING SOON", response.data)
        self.assertIn(b"Change Characters", response.data)
        self.assertNotIn(b"BACK TO NeoGateway", response.data)
        self.assertNotIn(b"RFD NEONODE", response.data)
        self.assertNotIn(b'<nav class="neoermac-menu"', response.data)

    def test_neoermac_menu_links_work(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/building-lineup"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/view-outbound"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/door-view"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/upcoming-pulls"'), 1)
        self.assertGreaterEqual(response.data.count(b'href="/neoermac/tug-assignments"'), 1)
        self.assertNotIn(b"BACK TO NeoGateway", response.data)

    def test_neoermac_mobile_dashboard_css_hides_duplicate_body_title(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"class=\"neoermac-shell neoermac-dashboard-shell neoermac-dashboard-home\"", response.data)
        self.assertIn(b"neoermac-dashboard-hero mobile-shell-duplicate-title", response.data)
        self.assertIn(b"data-neoermac-mobile-dashboard", response.data)
        self.assertIn("body.mobile-app-chrome .mobile-shell-duplicate-title", css)
        self.assertIn(
            ".neoermac-dashboard-home .neoermac-dashboard-hero {\n"
            "        display: none;",
            css,
        )

    def test_neoermac_dashboard_cards_use_dark_red_with_small_green_accent(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".neoermac-dashboard-tile {", css)
        self.assertIn("rgba(var(--node-sektor-primary-rgb), 0.5)", css)
        self.assertIn("rgba(var(--node-sektor-highlight-rgb), 0.38)", css)
        self.assertIn("linear-gradient(180deg, rgba(18, 10, 13, 0.98), rgba(6, 7, 10, 0.98))", css)
        self.assertIn(".neoermac-dashboard-tile::before", css)
        self.assertIn("rgba(var(--node-ermac-secondary-rgb), 0.74)", css)

    def test_neoermac_upcoming_pulls_shows_no_current_sort_state_without_operation(self):
        self._login_approved_user()

        response = self.client.get("/neoermac/upcoming-pulls")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPCOMING OUTBOUND PULLS", response.data)
        self.assertIn(b"No current sort operation", response.data)
        self.assertNotIn(b"neoermac-upcoming-row", response.data)
        self.assertNotIn(b"NeoErmac pull actions", response.data)
        self.assertNotIn(b"neoermac-dashboard-menu", response.data)
        self.assertIn(b'href="/neoermac"', response.data)

    def test_neoermac_auto_refresh_is_limited_to_live_operation_pages(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 12, 1, 0)
        self._add_operation_departure("UPS701", "BOS", tail="N701UP", parking="D13")
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        landing_response = self.client.get("/neoermac/door-view")
        self.assertEqual(landing_response.status_code, 200)
        self.assertIn(b"Select a door.", landing_response.data)
        self.assertNotIn(b"data-door-view", landing_response.data)
        self.assertNotIn(b"data-state-url", landing_response.data)
        self.assertNotIn(b"data-refresh-active", landing_response.data)
        self.assertNotIn(b"data-operation-refresh-reload", landing_response.data)
        self.assertNotIn(b"neoermac-refresh-paused", landing_response.data)
        self.assertNotIn(b"window.NeoLiveUpdates.create", landing_response.data)

        reload_pages = ("/neoermac/upcoming-pulls",)
        for path in reload_pages:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-operation-refresh-reload", response.data)
                self.assertIn(b'data-refresh-active="true"', response.data)
                self.assertIn(b"window.NeoLiveUpdates.create", response.data)
                self.assertIn(b"intervalMs: 5000", response.data)
                self.assertIn(b"immediate: false", response.data)

        outbound_response = self.client.get("/neoermac/view-outbound")
        self.assertEqual(outbound_response.status_code, 200)
        self.assertIn(b"data-neoermac-outbound-refresh", outbound_response.data)
        self.assertIn(b'data-refresh-active="true"', outbound_response.data)
        self.assertIn(b"window.NeoLiveUpdates.create", outbound_response.data)
        self.assertIn(b"intervalMs: 5000", outbound_response.data)
        self.assertNotIn(b"data-operation-refresh-reload", outbound_response.data)
        self.assertNotIn(b"window.location.reload()", outbound_response.data)

        door_response = self.client.get("/neoermac/door-view?door=D34")
        self.assertEqual(door_response.status_code, 200)
        self.assertIn(b'data-state-url="/neoermac/door-view/state?door=D34"', door_response.data)
        self.assertIn(b'data-refresh-active="true"', door_response.data)
        self.assertIn(b"window.NeoLiveUpdates.create", door_response.data)
        self.assertIn(b"intervalMs: 5000", door_response.data)
        self.assertNotIn(b"window.setInterval(refreshState", door_response.data)
        self.assertNotIn(b"neoermac-door-launcher-grid", door_response.data)

        non_refresh_pages = (
            "/neoermac",
            "/neoermac/building-lineup",
            "/neoermac/tug-assignments",
        )
        for path in non_refresh_pages:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"data-operation-refresh-reload", response.data)
                self.assertNotIn(b"window.NeoLiveUpdates.create", response.data)

    def test_neoermac_auto_refresh_pauses_before_sort_window(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 11, 10, 0)
        mission = self._add_operation_departure("UPS701", "BOS")
        operation = db.session.get(SortDateOperation, mission.sort_date_operation_id)
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        state_response = self.client.get("/neoermac/door-view/state?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-refresh-active="false"', response.data)
        self.assertIn(b"neoermac-refresh-paused", response.data)
        self.assertIn(b"Live updates off - outside Sort window", response.data)
        self.assertIn(b"22:00-04:00", response.data)
        payload = state_response.get_json()
        self.assertFalse(payload["state"]["refresh"]["auto_refresh_enabled"])
        self.assertEqual(payload["state"]["refresh"]["reason"], "outside_sort_window")
        self.assertEqual(payload["state"]["refresh"]["operation_id"], operation.id)
        self.assertEqual(payload["state"]["refresh"]["window_label"], "22:00-04:00")
        self.assertIsNone(payload["state"]["refresh"]["next_check_seconds"])
        self.assertNotIn(b"window.setInterval(refreshState", response.data)
        self.assertNotIn(b"resumeTimer", response.data)

        landing_response = self.client.get("/neoermac/door-view")
        self.assertEqual(landing_response.status_code, 200)
        self.assertIn(b"neoermac-refresh-paused", landing_response.data)
        self.assertIn(b"Live updates off - outside Sort window", landing_response.data)
        self.assertIn(b'data-door-view', landing_response.data)
        self.assertIn(b'class="neoermac-door-tab is-active"', landing_response.data)
        self.assertNotIn(b"data-operation-refresh-reload", landing_response.data)
        self.assertNotIn(b"window.setInterval", landing_response.data)

        reload_response = self.client.get("/neoermac/view-outbound")
        self.assertEqual(reload_response.status_code, 200)
        self.assertIn(b'data-refresh-active="false"', reload_response.data)
        self.assertIn(b'data-refresh-reason="outside_sort_window"', reload_response.data)
        self.assertNotIn(b"data-next-check-seconds", reload_response.data)
        self.assertNotIn(b"() => window.location.reload()", reload_response.data)

        upcoming_response = self.client.get("/neoermac/upcoming-pulls")
        self.assertEqual(upcoming_response.status_code, 200)
        self.assertIn(b'data-refresh-active="false"', upcoming_response.data)
        self.assertIn(b"Live updates off - outside Sort window", upcoming_response.data)

    def test_neoermac_live_views_stay_on_after_ops_end_until_sort_end(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            12,
            3,
            30,
        )
        self._add_operation_departure("UPS701", "BOS")
        settings = ensure_sort_timeline_settings(self.gateway)
        sort_setting = next(
            row for row in settings.sort_settings if row.sort_name == "night"
        )
        sort_setting.sort_window_start_local = time(14, 0)
        sort_setting.sort_window_end_local = time(5, 0)
        sort_setting.ops_window_start_local = time(20, 0)
        sort_setting.ops_window_end_local = time(3, 0)
        db.session.commit()
        self._login_approved_user(role="operator")

        for path in (
            "/neoermac/door-view?door=D34",
            "/neoermac/view-outbound",
            "/neoermac/upcoming-pulls",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-refresh-active="true"', response.data)
                self.assertIn(b"Live updates on", response.data)
                self.assertIn(b"14:00-05:00", response.data)

        state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertTrue(state["refresh"]["auto_refresh_enabled"])
        self.assertEqual(state["refresh"]["window_label"], "14:00-05:00")

        outbound_template = Path(
            "app/templates/neonodes/neoermac/view_outbound.html"
        ).read_text()
        self.assertIn('newRoot.dataset.refreshReason || "outside_sort_window"', outbound_template)
        self.assertIn("currentBanner.hidden = refreshEnabled", outbound_template)

    def test_neoermac_live_views_stop_at_sort_end(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            12,
            5,
            0,
        )
        self._add_operation_departure("UPS701", "BOS")
        settings = ensure_sort_timeline_settings(self.gateway)
        sort_setting = next(
            row for row in settings.sort_settings if row.sort_name == "night"
        )
        sort_setting.sort_window_start_local = time(14, 0)
        sort_setting.sort_window_end_local = time(5, 0)
        sort_setting.ops_window_start_local = time(20, 0)
        sort_setting.ops_window_end_local = time(3, 0)
        db.session.commit()
        self._login_approved_user(role="operator")

        for path in (
            "/neoermac/door-view?door=D34",
            "/neoermac/view-outbound",
            "/neoermac/upcoming-pulls",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-refresh-active="false"', response.data)
                self.assertIn(b"Live updates off - outside Sort window", response.data)

        state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertFalse(state["refresh"]["auto_refresh_enabled"])
        self.assertEqual(state["refresh"]["reason"], "outside_sort_window")

    def test_neoermac_live_views_use_the_shared_operation_refresh_banner(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 11, 10, 0)
        self._add_operation_departure("UPS701", "BOS")
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        for path, hook in (
            ("/neoermac/door-view?door=D34", b"data-neoermac-refresh-paused"),
            ("/neoermac/view-outbound", b"data-neoermac-outbound-refresh-paused"),
            ("/neoermac/upcoming-pulls", b"data-operation-refresh-reload"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    b'class="operation-refresh-banner neoermac-refresh-paused"',
                    response.data,
                )
                self.assertIn(b"data-operation-refresh-banner", response.data)
                self.assertIn(hook, response.data)

        landing_response = self.client.get("/neoermac/door-view")
        self.assertIn(b"data-operation-refresh-banner", landing_response.data)
        self.assertIn(b'class="neoermac-door-tab is-active"', landing_response.data)

    def test_neoermac_upcoming_pulls_shows_west_and_east_pull_lists(self):
        self._assign_lineup_destination("runout_4", "east_destination_1", "BOS")
        self._assign_lineup_destination("runout_10", "west_destination_2", "SDF")
        self._add_operation_departure(
            "UPS701",
            "BOS",
            tail="N701UP",
            parking="D13",
            pure_pull_time_local=time(1, 10),
            mix_pull_time_local=time(1, 30),
        )
        self._add_operation_departure(
            "UPS702",
            "SDF",
            tail="N702UP",
            parking="D32",
            pure_pull_time_local=time(1, 15),
            mix_pull_time_local=time(1, 35),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/upcoming-pulls")

        west_html = self._upcoming_side_html(response, "West")
        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertLess(response.data.index(b"West upcoming pulls"), response.data.index(b"East upcoming pulls"))
        self.assertIn(b"SDF / N702UP / D32", west_html)
        self.assertNotIn(b"UPS702 / SDF", west_html)
        self.assertIn(b"D32-D34 BELT 2 WEST SLOT 1", west_html)
        self.assertNotIn(b"BOS / N701UP / D13", west_html)
        self.assertIn(b"BOS / N701UP / D13", east_html)
        self.assertNotIn(b"UPS701 / BOS", east_html)
        self.assertIn(b"D13-D17 BELT 1 EAST SLOT 1", east_html)
        self.assertNotIn(b"SDF / N702UP / D32", east_html)

    def test_neoermac_upcoming_pulls_combines_duplicate_belt_side_entries(self):
        self._assign_lineup_destination("runout_3", "east_destination_2", "DEN")
        self._assign_lineup_destination("runout_3", "west_destination_2", "DEN")
        self._add_operation_departure(
            "UPS810",
            "DEN",
            pure_pull_time_local=time(1, 49),
            mix_pull_time_local=time(2, 11),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/upcoming-pulls")

        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(east_html.count(b"DEN / - / -"), 2)
        self.assertNotIn(b"UPS810 / DEN", east_html)
        self.assertEqual(east_html.count(b"D9-D13 BELT 2 EAST SLOT 1"), 2)
        self.assertEqual(east_html.count(b"D9-D13 BELT 2 WEST SLOT 1"), 2)

    def test_neoermac_upcoming_pulls_keeps_different_destinations_on_same_belt(self):
        self._assign_lineup_destination("runout_3", "east_destination_2", "DEN")
        self._assign_lineup_destination("runout_3", "west_destination_2", "OMA")
        self._add_operation_departure("UPS811", "DEN")
        self._add_operation_departure("UPS812", "OMA")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/upcoming-pulls")

        east_html = self._upcoming_side_html(response, "East")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEN / - / -", east_html)
        self.assertIn(b"OMA / - / -", east_html)
        self.assertNotIn(b"UPS811 / DEN", east_html)
        self.assertNotIn(b"UPS812 / OMA", east_html)
        self.assertEqual(east_html.count(b"D9-D13 BELT 2 EAST SLOT 1"), 2)
        self.assertEqual(east_html.count(b"D9-D13 BELT 2 WEST SLOT 1"), 2)

    def test_neoermac_upcoming_pulls_removes_actual_and_no_pull_items(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure(
            "UPS703",
            "SDF",
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        save_response = self.client.post(
            "/neoermac/door-view?door=D32",
            data={
                "door": "D32",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "SDF",
                "actual_pure_0": "01:25",
            },
            follow_redirects=False,
        )
        response = self.client.get("/neoermac/upcoming-pulls")

        west_html = self._upcoming_side_html(response, "West")
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"01:20", west_html)
        self.assertNotIn(b"Pure", west_html)
        self.assertNotIn(b"01:40", west_html)
        self.assertIn(b"01:55", west_html)
        self.assertIn(b"Mix Pull", west_html)

    def test_neoermac_menu_keeps_multi_door_pull_until_all_required_doors_addressed(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "LAX")
        self._assign_lineup_destination("runout_10", "west_destination_1", "LAX")
        self._add_operation_departure(
            "UPS704",
            "LAX",
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        first_side_response = self.client.post(
            "/neoermac/door-view?door=D32",
            data={
                "door": "D32",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "LAX",
                "no_mix_0": "on",
            },
            follow_redirects=False,
        )
        one_side_dashboard = self.client.get("/neoermac/upcoming-pulls")
        one_side_west_html = self._upcoming_side_html(one_side_dashboard, "West")

        self.assertEqual(first_side_response.status_code, 302)
        self.assertEqual(one_side_dashboard.status_code, 200)
        self.assertIn(b"LAX / - / -", one_side_west_html)
        self.assertIn(b"01:55", one_side_west_html)
        self.assertIn(b"Mix Pull", one_side_west_html)

        second_side_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_pulls",
                "destination_count": "1",
                "destination_0": "LAX",
                "no_mix_0": "on",
            },
            follow_redirects=False,
        )
        complete_dashboard = self.client.get("/neoermac/upcoming-pulls")
        complete_west_html = self._upcoming_side_html(complete_dashboard, "West")

        self.assertEqual(second_side_response.status_code, 302)
        self.assertEqual(complete_dashboard.status_code, 200)
        self.assertNotIn(b"01:55", complete_west_html)
        self.assertNotIn(b"Mix Pull", complete_west_html)

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
                mix_pull_time_local=time(1, 55 + index),
            )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/upcoming-pulls")

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
                self.assertIn(b"Coming Soon", response.data)
                self.assertNotIn(b'aria-label="BACK TO NeoErmac"', response.data)
                self.assertNotIn(b"OPERATIONAL LOGIC WILL BE ADDED IN A LATER PASS.", response.data)
                self.assertNotIn(b"PLACEHOLDER SHELL", response.data)

    def test_door_view_route_loads_for_view_authorized_user(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"SHIFT OUTBOUND", response.data)
        self.assertIn(b"Select a door.", response.data)
        self.assertIn(b"neoermac-door-launcher", response.data)
        self.assertIn(b"neoermac-door-launcher-grid", response.data)
        self.assertEqual(
            response.data.count(b"neoermac-door-launcher-button"),
            len(self.REAL_OUTBOUND_DOORS),
        )
        self.assertIn(b'<option value="D34"', response.data)
        self.assertIn(b'class="neoermac-door-selector"', response.data)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(b"RFD DOOR OPERATIONS", response.data)
        self.assertNotIn(b"DOOR OPERATIONS", response.data)
        self.assertNotIn(b'<label for="door-select">DOOR</label>', response.data)
        self.assertNotIn(b"PLACEHOLDER SHELL", response.data)
        self.assertNotIn(b"OPERATIONAL LOGIC WILL BE ADDED IN A LATER PASS.", response.data)

    def test_door_view_landing_has_compact_desktop_launcher_hooks(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view")
        css = Path("app/static/css/base.css").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"neoermac-door-launcher-grid", response.data)
        for door in self.REAL_OUTBOUND_DOORS:
            self.assertIn(
                b'href="/neoermac/door-view?door=' + door + b'"',
                response.data,
            )
        self.assertIn(
            ".neoermac-door-shell.neoermac-door-launcher",
            css,
        )
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr));", css)
        self.assertIn("min-height: 50px;", css)

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
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_11", "east_destination_2", "ONT")
        self._add_operation_departure("UPS301", "SDF", tail="N123UP", parking="A01")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<strong>D34</strong>', response.data)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b"ONT", response.data)
        self.assertIn(b"Scheduled", response.data)
        self.assertNotIn(b"LIVE SORT", response.data)
        self.assertIn(b"NO FLIGHT DATA", response.data)
        self.assertIn(b"N123UP", response.data)
        self.assertIn(b"A01", response.data)
        self.assertIn(b"OUTBOUND PULLS", response.data)
        self.assertNotIn(b"DESTINATION PULLS", response.data)
        self.assertNotIn(b"neoermac-door-belt-list", response.data)
        self.assertNotIn(b"EAST BLU/BLU BELT", response.data)
        self.assertNotIn(b"WEST BRN/WHT BELT", response.data)
        self.assertIn(b"PLANNED Pure", response.data)
        self.assertIn(b"WINDOW TBD", response.data)
        self.assertIn(b"01:20", response.data)
        row_html = self._door_flight_info_row_html(response.data)
        self.assertEqual(row_html.count(b"data-door-flight-info-cell"), 4)
        self.assertNotIn(b">DESTINATION<", row_html)
        self.assertNotIn(b">PARKING<", row_html)
        self.assertNotIn(b">TAIL<", row_html)
        self.assertNotIn(b">STATUS<", row_html)
        self.assertIn(b"SDF", row_html)
        self.assertIn(b"A01", row_html)
        self.assertIn(b"N123UP", row_html)
        self.assertIn(b"Scheduled", row_html)
        self.assertNotIn(b"UPS301", row_html)
        self.assertLess(row_html.index(b"SDF"), row_html.index(b"A01"))
        self.assertLess(row_html.index(b"A01"), row_html.index(b"N123UP"))
        self.assertLess(row_html.index(b"N123UP"), row_html.index(b"Scheduled"))
        self.assertNotIn(b'class="neoermac-door-flight"', response.data)
        self.assertLess(response.data.index(b"N123UP"), response.data.index(b"ONT"))
        self.assertIn(b"data-door-fixed-controls", response.data)
        self.assertIn(b"data-door-pull-content", response.data)
        self.assertIn(b"neoermac-door-support-stack", response.data)
        self.assertLess(response.data.index(b"data-door-fixed-controls"), response.data.index(b"OUTBOUND PULLS"))
        self.assertLess(response.data.index(b"OUTBOUND PULLS"), response.data.index(b"data-uld-workspace"))
        self.assertLess(response.data.index(b"REQUEST ULDS"), response.data.index(b"ACTIVE REQUESTS"))
        self.assertLess(response.data.index(b"ACTIVE REQUESTS"), response.data.index(b"ON THE WAY"))
        self.assertIn(b"REQUEST ULDS", response.data)
        self.assertIn(b"No tugs assigned yet.", response.data)
        self.assertIn(b"No relevant on-the-way events.", response.data)

    def test_door_view_outbound_destination_cards_have_prominent_scan_markup(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "CID")
        self._assign_lineup_destination("runout_11", "east_destination_2", "EWR")
        self._add_operation_departure("UPS401", "CID", tail="N440UP", parking="D07")
        self._add_operation_departure("UPS402", "EWR", tail="N441UP", parking="D08")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data.count(b"data-door-destination-card"), 2)
        self.assertIn(b"neoermac-door-destination-frame", response.data)
        self.assertIn(b'class="neoermac-door-destination-title">CID</strong>', response.data)
        self.assertIn(b'class="neoermac-door-destination-title">EWR</strong>', response.data)
        self.assertIn(b"data-hhmm-input", response.data)
        self.assertIn(b"neo-page-title neoermac-operation-title", response.data)
        self.assertIn(b'class="neoermac-label-desktop">NO Pure</span>', response.data)
        self.assertIn(b'class="neoermac-label-mobile">NO</span>', response.data)

        css = Path("app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn(".neoermac-door-destination-card {", css)
        self.assertIn("border: 1px solid rgba(var(--node-rgb), 0.58)", css)
        self.assertIn(".neoermac-door-card-head .neoermac-door-destination", css)
        self.assertIn("background: transparent;", css)
        self.assertIn(".neoermac-door-planned {", css)
        self.assertIn(".neoermac-door-toggle {", css)
        self.assertIn(".neoermac-door-actual input", css)
        self.assertIn(".neoermac-door-pull-row .neoermac-door-actual {", css)
        self.assertIn(".neoermac-door-destination .neoermac-door-destination-title", css)
        self.assertIn("color: var(--node-ermac-secondary)", css)
        self.assertIn("content: none;", css)
        self.assertNotIn('.neoermac-door-destination::before {\n    content: "";', css)
        self.assertIn(".neoermac-door-pull-row.is-pull-due-soon", css)
        self.assertIn(".neoermac-door-pull-row.is-pull-due-now", css)
        self.assertIn(".neoermac-door-pull-row.is-pull-late", css)
        self.assertIn("--neoermac-pull-alert-rgb: 255, 203, 87;", css)
        self.assertIn("--neoermac-pull-alert-rgb: 54, 226, 123;", css)
        self.assertIn("--neoermac-pull-alert-rgb: 255, 62, 86;", css)
        self.assertGreaterEqual(
            css.count("animation: neoermac-pull-critical-pulse 1.15s ease-in-out infinite;"),
            3,
        )
        self.assertIn("@keyframes neoermac-pull-critical-pulse", css)

    def test_door_view_initial_render_shows_parking_plan_assignment(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=mission.sort_date_operation_id,
                tail_number="N316UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"N316UP", response.data)
        self.assertIn(b"<strong data-door-parking>A01</strong>", response.data)
        row_html = self._door_flight_info_row_html(response.data)
        self.assertIn(b"SDF", row_html)
        self.assertIn(b"A01", row_html)
        self.assertIn(b"N316UP", row_html)
        self.assertNotIn(b"UPS948", row_html)

    def test_door_view_state_shows_parking_plan_assignment(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=mission.sort_date_operation_id,
                tail_number="N316UP",
                ramp_code="A",
                position_code="A01",
                lane_number=2,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view/state?door=D34")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["state"]["destinations"][0]["destination"], "SDF")
        self.assertEqual(payload["state"]["destinations"][0]["tail"], "N316UP")
        self.assertEqual(payload["state"]["destinations"][0]["parking"], "A01")

    def test_door_view_state_supports_selective_live_card_reconciliation(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure(
            "UPS948",
            "SDF",
            tail="N316UP",
            departure_status="loading",
            window_minutes=15,
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        state_response = self.client.get("/neoermac/door-view/state?door=D34")

        card = state_response.get_json()["state"]["destinations"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(card["destination"], "SDF")
        self.assertEqual(card["status"], "Loading")
        self.assertEqual(card["window_minutes"], 15)
        self.assertEqual(card["planned"], {"pure": "01:35", "mix": "02:10"})
        self.assertEqual(card["base_planned"], {"pure": "01:20", "mix": "01:55"})
        self.assertEqual(card["actual"], {"pure": "", "mix": ""})
        self.assertEqual(card["no_pull"], {"pure": False, "mix": False})
        self.assertFalse(card["pulls_complete"])
        self.assertEqual(card["order_index"], 0)
        self.assertIn(b"data-door-destination-list", response.data)
        self.assertIn(b"dataset.localDirty", response.data)
        self.assertIn(b"neoErmacApplyDoorCardState", response.data)
        self.assertIn(b"neoErmacReconcileDoorDestinations", response.data)
        self.assertNotIn(b"window.location.reload", response.data)

    def test_door_view_orders_unfinished_by_effective_pure_then_tbd_and_completed(self):
        self._set_sort_window("night", time(22, 0), time(4, 0))
        assignments = (
            ("west_destination_1", "SDF"),
            ("west_destination_2", "ONT"),
            ("west_destination_1_slot_2", "LAX"),
            ("west_destination_2_slot_2", "MEM"),
        )
        for field_name, destination in assignments:
            self._assign_lineup_destination("runout_10", field_name, destination)

        sdf = self._add_operation_departure(
            "UPS401",
            "SDF",
            pure_pull_time_local=time(1, 0),
        )
        ont = self._add_operation_departure(
            "UPS402",
            "ONT",
            pure_pull_time_local=time(0, 30),
        )
        lax = self._add_operation_departure("UPS403", "LAX")
        lax.pure_pull_time_local = None
        mem = self._add_operation_departure(
            "UPS404",
            "MEM",
            pure_pull_time_local=time(0, 15),
        )
        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mem.sort_date_operation_id,
                door="D34",
                destination="MEM",
                actual_pure_pull_time_local=time(0, 16),
                no_mix_pull=True,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"]

        self.assertEqual(
            [row["destination"] for row in payload],
            ["ONT", "SDF", "LAX", "MEM"],
        )
        self.assertEqual([row["order_index"] for row in payload], [0, 1, 2, 3])
        self.assertFalse(payload[0]["pulls_complete"])
        self.assertFalse(payload[1]["pulls_complete"])
        self.assertFalse(payload[2]["pulls_complete"])
        self.assertTrue(payload[3]["pulls_complete"])
        self.assertEqual(sdf.destination, "SDF")
        self.assertEqual(ont.destination, "ONT")

    def test_door_view_uses_destination_as_stable_tie_breaker(self):
        self._set_sort_window("night", time(22, 0), time(4, 0))
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ATL")
        self._add_operation_departure(
            "UPS402",
            "SDF",
            pure_pull_time_local=time(1, 0),
        )
        self._add_operation_departure(
            "UPS401",
            "ATL",
            pure_pull_time_local=time(1, 0),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"]

        self.assertEqual([row["destination"] for row in payload], ["ATL", "SDF"])

    def test_door_view_completion_autosave_moves_card_to_completed_bottom(self):
        self._set_sort_window("night", time(22, 0), time(4, 0))
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        self._add_operation_departure(
            "UPS401",
            "SDF",
            pure_pull_time_local=time(0, 30),
        )
        self._add_operation_departure(
            "UPS402",
            "ONT",
            pure_pull_time_local=time(1, 0),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        pure_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "00:31",
                "no_pull": "0",
            },
        )
        mix_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "mix",
                "actual_pull": "",
                "no_pull": "1",
            },
        )

        self.assertEqual(pure_response.status_code, 200)
        self.assertFalse(pure_response.get_json()["card"]["pulls_complete"])
        self.assertEqual(mix_response.status_code, 200)
        self.assertTrue(mix_response.get_json()["card"]["pulls_complete"])
        self.assertEqual(
            [
                row["destination"]
                for row in mix_response.get_json()["state"]["destinations"]
            ],
            ["ONT", "SDF"],
        )

    def test_door_view_shared_position_slots_both_display_position_only(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        first = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        second = self._add_operation_departure("UPS949", "ONT", tail="N317UP")
        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=first.sort_date_operation_id,
                    tail_number="N316UP",
                    ramp_code="A",
                    position_code="A01",
                    lane_number=1,
                ),
                SortDateParkingAssignment(
                    sort_date_operation_id=second.sort_date_operation_id,
                    tail_number="N317UP",
                    ramp_code="A",
                    position_code="A01",
                    lane_number=2,
                ),
            ]
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        payload = self.client.get("/neoermac/door-view/state?door=D34").get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b"<strong data-door-parking>A01</strong>"), 2)
        self.assertNotIn(b"A01-1", response.data)
        self.assertNotIn(b"A01-2", response.data)
        self.assertEqual(
            [row["parking"] for row in payload["state"]["destinations"]],
            ["A01", "A01"],
        )

    def test_door_view_parking_is_current_sort_operation_only(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        current_mission = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        other_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 6, 10),
            window_minutes=0,
        )
        db.session.add(other_operation)
        db.session.flush()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=other_operation.id,
                tail_number="N316UP",
                ramp_code="B",
                position_code="B02",
                lane_number=1,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(current_mission.assigned_tail_number, "N316UP")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"N316UP", response.data)
        self.assertIn(b"<strong data-door-parking>-</strong>", response.data)
        self.assertNotIn(b"B02", response.data)

    def test_door_view_parking_returns_to_dash_after_unassign(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        assignment = SortDateParkingAssignment(
            sort_date_operation_id=mission.sort_date_operation_id,
            tail_number="N316UP",
            ramp_code="A",
            position_code="A01",
            lane_number=1,
        )
        db.session.add(assignment)
        db.session.commit()
        self._login_approved_user(role="operator")

        assigned_response = self.client.get("/neoermac/door-view?door=D34")
        assignment.position_code = None
        assignment.ramp_code = None
        assignment.lane_number = None
        db.session.commit()
        unassigned_response = self.client.get("/neoermac/door-view?door=D34")
        state_response = self.client.get("/neoermac/door-view/state?door=D34")

        self.assertIn(b"<strong data-door-parking>A01</strong>", assigned_response.data)
        self.assertIn(b"<strong data-door-parking>-</strong>", unassigned_response.data)
        self.assertEqual(state_response.get_json()["state"]["destinations"][0]["parking"], "-")

    def test_door_view_parking_assignment_does_not_mutate_master_schedule(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        master = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            flight_number="UPS948",
            origin=self.gateway.code,
            destination="SDF",
            active=True,
            active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
            planned_time_local=time(2, 15),
            timezone="America/Chicago",
            preferred_parking="OLD",
        )
        mission = self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        db.session.add_all(
            [
                master,
                SortDateParkingAssignment(
                    sort_date_operation_id=mission.sort_date_operation_id,
                    tail_number="N316UP",
                    ramp_code="A",
                    position_code="A01",
                    lane_number=1,
                ),
            ]
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        db.session.refresh(master)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A01", response.data)
        self.assertEqual(master.preferred_parking, "OLD")

    def test_door_view_renders_actual_flight_status(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS402", "SDF", departure_status="blocked_out")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Blocked Out", response.data)
        self.assertNotIn(b"LIVE SORT", response.data)

    def test_door_view_displays_window_adjusted_planned_pull_times(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS401", "SDF", window_minutes=20)
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PLANNED Pure", response.data)
        self.assertIn(b"neoermac-door-pull-row", response.data)
        self.assertIn(b"WINDOW 20 MIN", response.data)
        self.assertEqual(response.data.count(b"WINDOW 20 MIN"), 1)
        self.assertIn(b"01:40", response.data)
        self.assertIn(b"BASE 01:20 +20 MIN", response.data)
        self.assertIn(b"02:15", response.data)

        css = Path("app/static/css/base.css").read_text()
        self.assertRegex(
            css,
            r"\.neoermac-door-window \{[^}]*font-size: 1rem;",
        )
        self.assertIn("font-size: 0.88rem;", css)

    def test_door_view_distinguishes_tbd_zero_and_positive_windows(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure(
            "UPS401",
            "SDF",
            window_minutes=None,
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        tbd = self.client.get("/neoermac/door-view?door=D34")
        operation = db.session.get(SortDateOperation, mission.sort_date_operation_id)
        operation.window_minutes = 0
        db.session.commit()
        zero = self.client.get("/neoermac/door-view?door=D34")
        operation.window_minutes = 12
        db.session.commit()
        positive = self.client.get("/neoermac/door-view?door=D34")

        self.assertIn(b"WINDOW TBD", tbd.data)
        self.assertIn(b"WINDOW 0 MIN", zero.data)
        self.assertNotIn(b"BASE 01:20 +0 MIN", zero.data)
        self.assertIn(b"WINDOW 12 MIN", positive.data)
        self.assertIn(b"BASE 01:20 +12 MIN", positive.data)

    def test_door_view_uses_independent_wave_windows_when_global_is_blank(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        first_wave = self._add_operation_departure(
            "UPS401",
            "SDF",
            window_minutes=None,
            wave="1st Wave",
        )
        self._add_operation_departure(
            "UPS402",
            "ONT",
            window_minutes=None,
            wave="2nd Wave",
        )
        operation = db.session.get(SortDateOperation, first_wave.sort_date_operation_id)
        operation.first_wave_window_minutes = 10
        operation.second_wave_window_minutes = None
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view/state?door=D34")

        cards = {
            card["destination"]: card
            for card in response.get_json()["state"]["destinations"]
        }
        self.assertEqual(cards["SDF"]["window_minutes"], 10)
        self.assertEqual(cards["SDF"]["planned"]["pure"], "01:30")
        self.assertIsNone(cards["ONT"]["window_minutes"])
        self.assertEqual(cards["ONT"]["planned"]["pure"], "01:20")

    def test_door_view_global_zero_overrides_stored_wave_window(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure(
            "UPS401",
            "SDF",
            window_minutes=0,
            wave="1",
        )
        operation = db.session.get(SortDateOperation, mission.sort_date_operation_id)
        operation.first_wave_window_minutes = 25
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view/state?door=D34")
        card = response.get_json()["state"]["destinations"][0]

        self.assertEqual(card["window_minutes"], 0)
        self.assertEqual(card["planned"]["pure"], "01:20")

    def test_door_view_warns_for_pull_due_within_five_minutes(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 12, 1, 16)
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS401", "SDF")
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        state_response = self.client.get("/neoermac/door-view/state?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"is-pull-due-soon", response.data)
        self.assertIn(b'data-pull-alert-state="due_soon"', response.data)
        self.assertIn(f'op-{mission.sort_date_operation_id}:D34:SDF:pure:202606120120'.encode(), response.data)
        self.assertIn(b"DUE SOON", response.data)
        payload = state_response.get_json()
        pure_alert = payload["state"]["destinations"][0]["pull_alerts"]["pure"]
        self.assertEqual(pure_alert["state"], "due_soon")
        self.assertEqual(
            pure_alert["key"],
            f"op-{mission.sort_date_operation_id}:D34:SDF:pure:202606120120",
        )

    def test_door_view_marks_late_pull_critical_until_resolved(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 12, 1, 26)
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS401", "SDF")
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        late_response = self.client.get("/neoermac/door-view?door=D34")
        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mission.sort_date_operation_id,
                door="D34",
                destination="SDF",
                actual_pure_pull_time_local=time(1, 27),
            )
        )
        db.session.commit()
        resolved_response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(late_response.status_code, 200)
        self.assertIn(b"is-pull-late", late_response.data)
        self.assertIn(b'data-pull-alert-state="late"', late_response.data)
        self.assertIn(b"neoermac-pull-alert-badge", late_response.data)
        self.assertIn(b"LATE", late_response.data)
        self.assertNotIn(b'data-pull-alert-state="late"', resolved_response.data)

    def test_door_view_pull_urgency_uses_exact_yellow_green_red_boundaries(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure(
            "UPS401",
            "SDF",
            pure_pull_time_local=time(2, 0),
            mix_pull_time_local=time(2, 30),
        )
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        cases = (
            (datetime(2026, 6, 12, 1, 54, 59), ""),
            (datetime(2026, 6, 12, 1, 55, 0), "due_soon"),
            (datetime(2026, 6, 12, 1, 59, 0), "due_soon"),
            (datetime(2026, 6, 12, 2, 0, 0), "due_now"),
            (datetime(2026, 6, 12, 2, 4, 59), "due_now"),
            (datetime(2026, 6, 12, 2, 5, 0), "late"),
            (datetime(2026, 6, 12, 2, 20, 0), "late"),
        )
        for local_now, expected_state in cases:
            with self.subTest(local_now=local_now, expected_state=expected_state):
                self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = local_now
                payload = self.client.get(
                    "/neoermac/door-view/state?door=D34"
                ).get_json()
                pure_alert = payload["state"]["destinations"][0]["pull_alerts"]["pure"]
                self.assertEqual(pure_alert["state"], expected_state)

    def test_door_view_pull_urgency_is_independent_and_resolves_per_pull(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            12,
            2,
            1,
        )
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure(
            "UPS401",
            "SDF",
            pure_pull_time_local=time(2, 0),
            mix_pull_time_local=time(2, 30),
        )
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mission.sort_date_operation_id,
                door="D34",
                destination="SDF",
                actual_pure_pull_time_local=time(2, 1),
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        actual_payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]
        self.assertEqual(actual_payload["pull_alerts"]["pure"]["state"], "")
        self.assertEqual(actual_payload["pull_alerts"]["mix"]["state"], "")

        pull = NeoErmacDoorPull.query.filter_by(destination="SDF").one()
        pull.actual_pure_pull_time_local = None
        pull.no_pure_pull = True
        db.session.commit()
        no_payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()["state"]["destinations"][0]
        self.assertEqual(no_payload["pull_alerts"]["pure"]["state"], "")
        self.assertEqual(no_payload["pull_alerts"]["mix"]["state"], "")

    def test_door_view_pull_urgency_handles_cross_midnight_planned_times(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            12,
            0,
            1,
        )
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure(
            "UPS401",
            "SDF",
            pure_pull_time_local=time(0, 2),
        )
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        payload = self.client.get(
            "/neoermac/door-view/state?door=D34"
        ).get_json()

        pure_alert = payload["state"]["destinations"][0]["pull_alerts"]["pure"]
        self.assertEqual(pure_alert["state"], "due_soon")
        self.assertEqual(
            pure_alert["key"],
            f"op-{SortDateOperation.query.one().id}:D34:SDF:pure:202606120002",
        )

    def test_door_view_does_not_alert_for_pull_time_outside_operation_window(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 12, 1, 16)
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS401", "SDF", pure_pull_time_local=time(18, 0))
        self._set_sort_window("night", time(22, 0), time(4, 0))
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        payload = self.client.get("/neoermac/door-view/state?door=D34").get_json()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-pull-alert-state="due_soon"', response.data)
        self.assertNotIn(b'data-pull-alert-state="late"', response.data)
        self.assertEqual(payload["state"]["destinations"][0]["pull_alerts"]["pure"]["state"], "")

    def test_door_view_view_only_user_cannot_save_pulls_or_uld_requests(self):
        edit_rule = PermissionRule.query.filter_by(permission_key="neoermac.door_view.edit").one()
        edit_rule.minimum_role = "simulator"
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
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
        autosave_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "01:15",
                "no_pull": "0",
            },
        )

        self.assertEqual(pull_response.status_code, 403)
        self.assertEqual(uld_response.status_code, 403)
        self.assertEqual(autosave_response.status_code, 403)
        self.assertEqual(NeoErmacDoorPull.query.count(), 0)
        self.assertEqual(NeoErmacUldRequest.query.count(), 0)

        db.session.add(
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                door="D34",
                a2_count=2,
                setup_needed=False,
                created_at=datetime(2026, 6, 24, 19, 1),
                updated_at=datetime(2026, 6, 24, 19, 1),
            )
        )
        db.session.commit()
        page = self.client.get("/neoermac/door-view?door=D34")
        rendered_page = page.data.split(
            b'const root = document.querySelector("[data-door-view]");',
            1,
        )[0]
        self.assertIn(b"View-only access. ULD request controls are disabled.", page.data)
        self.assertNotIn(b"data-uld-request-form", rendered_page)
        self.assertIn(b"data-uld-request-row", rendered_page)
        self.assertNotIn(b"data-uld-request-edit", rendered_page)
        self.assertNotIn(b"neoermac-uld-request-cancel", rendered_page)

    def test_door_view_edit_user_can_save_actual_pull_and_no_pull_states(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
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
                "actual_mix_0": "01:55",
            },
            follow_redirects=False,
        )

        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved.actual_pure_pull_time_local, time(1, 15))
        self.assertFalse(saved.no_mix_pull)
        self.assertEqual(saved.actual_mix_pull_time_local, time(1, 55))

        reload_response = self.client.get("/neoermac/door-view?door=D34")
        self.assertIn(b'value="01:15"', reload_response.data)
        self.assertIn(b'type="text"', reload_response.data)
        self.assertIn(b'pattern="([01][0-9]|2[0-3]):[0-5][0-9]"', reload_response.data)
        self.assertIn(b"checked", reload_response.data)
        self.assertNotIn(b" AM", reload_response.data)
        self.assertNotIn(b" PM", reload_response.data)

    def test_door_view_pull_autosave_saves_valid_hhmm_actual_pull(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "14:05",
                "no_pull": "0",
            },
        )

        payload = response.get_json()
        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        mission = SortDateMission.query.filter_by(destination="SDF").one()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["card"]["actual"]["pure"], "14:05")
        self.assertFalse(payload["card"]["pulls_complete"])
        self.assertEqual(saved.actual_pure_pull_time_local, time(14, 5))
        self.assertFalse(saved.no_pure_pull)
        self.assertEqual(mission.actual_pure_pull_time_local, time(14, 5))

    def test_door_view_pull_autosave_succeeds_outside_ops_with_departed_mission(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            11,
            19,
            0,
        )
        self._assign_lineup_destination("runout_3", "east_destination_2", "DEN")
        mission = self._add_operation_departure(
            "UPS0810",
            "DEN",
            departure_status="departed",
            pure_pull_time_local=time(1, 49),
        )
        settings = ensure_sort_timeline_settings(self.gateway)
        sort_setting = next(row for row in settings.sort_settings if row.sort_name == "night")
        sort_setting.sort_window_start_local = time(14, 0)
        sort_setting.sort_window_end_local = time(5, 0)
        sort_setting.ops_window_start_local = time(20, 0)
        sort_setting.ops_window_end_local = time(3, 0)
        db.session.execute(
            text(
                "ALTER TABLE neoermac_door_pulls "
                "ADD COLUMN no_first_mix_pull BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        db.session.execute(
            text(
                "ALTER TABLE neoermac_door_pulls "
                "ADD COLUMN no_second_mix_pull BOOLEAN NOT NULL DEFAULT 0"
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D9",
                "destination": "DEN",
                "pull_key": "pure",
                "actual_pull": "01:45",
                "no_pull": "0",
            },
        )

        payload = response.get_json()
        saved = NeoErmacDoorPull.query.filter_by(door="D9", destination="DEN").one()
        db.session.refresh(mission)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["state"]["refresh"]["auto_refresh_enabled"])
        self.assertEqual(payload["state"]["refresh"]["reason"], "active")
        self.assertEqual(payload["state"]["refresh"]["window_label"], "14:00-05:00")
        self.assertEqual(saved.actual_pure_pull_time_local, time(1, 45))
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 45))
        self.assertEqual(mission.departure_status, "departed")

        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            6,
            11,
            10,
            0,
        )
        outside_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D9",
                "destination": "DEN",
                "pull_key": "pure",
                "actual_pull": "01:46",
                "no_pull": "0",
            },
        )
        outside_payload = outside_response.get_json()
        db.session.refresh(saved)
        db.session.refresh(mission)
        self.assertEqual(outside_response.status_code, 200)
        self.assertTrue(outside_payload["ok"])
        self.assertFalse(outside_payload["state"]["refresh"]["auto_refresh_enabled"])
        self.assertEqual(
            outside_payload["state"]["refresh"]["reason"],
            "outside_sort_window",
        )
        self.assertEqual(saved.actual_pure_pull_time_local, time(1, 46))
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 46))
        self.assertEqual(mission.departure_status, "departed")

    def test_door_view_pull_autosave_creates_and_updates_pure_and_mix_records(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        self._add_operation_departure("UPS302", "SDF")
        self._add_operation_departure("UPS303", "ONT")
        db.session.commit()
        self._login_approved_user(role="operator")

        responses = (
            self.client.post(
                "/neoermac/door-view/pull-autosave",
                data={
                    "door": "D34",
                    "destination": "SDF",
                    "pull_key": "pure",
                    "actual_pull": "01:44",
                    "no_pull": "0",
                },
            ),
            self.client.post(
                "/neoermac/door-view/pull-autosave",
                data={
                    "door": "D34",
                    "destination": "ONT",
                    "pull_key": "mix",
                    "actual_pull": "02:14",
                    "no_pull": "0",
                },
            ),
            self.client.post(
                "/neoermac/door-view/pull-autosave",
                data={
                    "door": "D34",
                    "destination": "SDF",
                    "pull_key": "pure",
                    "actual_pull": "01:49",
                    "no_pull": "0",
                },
            ),
            self.client.post(
                "/neoermac/door-view/pull-autosave",
                data={
                    "door": "D34",
                    "destination": "ONT",
                    "pull_key": "mix",
                    "actual_pull": "02:19",
                    "no_pull": "0",
                },
            ),
        )

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(NeoErmacDoorPull.query.count(), 2)
        sdf_pull = NeoErmacDoorPull.query.filter_by(destination="SDF").one()
        ont_pull = NeoErmacDoorPull.query.filter_by(destination="ONT").one()
        self.assertEqual(sdf_pull.actual_pure_pull_time_local, time(1, 49))
        self.assertEqual(ont_pull.actual_mix_pull_time_local, time(2, 19))
        self.assertEqual(
            SortDateMission.query.filter_by(destination="SDF").one().actual_pure_pull_time_local,
            time(1, 49),
        )
        self.assertEqual(
            SortDateMission.query.filter_by(destination="ONT").one().actual_mix_pull_time_local,
            time(2, 19),
        )

    def test_door_view_pull_autosave_saves_no_pure_and_no_mix(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        self._add_operation_departure("UPS302", "SDF")
        self._add_operation_departure("UPS303", "ONT")
        db.session.commit()
        self._login_approved_user(role="operator")

        pure_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "",
                "no_pull": "1",
            },
        )
        mix_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "ONT",
                "pull_key": "mix",
                "actual_pull": "",
                "no_pull": "1",
            },
        )

        self.assertEqual(pure_response.status_code, 200)
        self.assertEqual(mix_response.status_code, 200)
        sdf_pull = NeoErmacDoorPull.query.filter_by(destination="SDF").one()
        ont_pull = NeoErmacDoorPull.query.filter_by(destination="ONT").one()
        self.assertTrue(sdf_pull.no_pure_pull)
        self.assertIsNone(sdf_pull.actual_pure_pull_time_local)
        self.assertTrue(ont_pull.no_mix_pull)
        self.assertIsNone(ont_pull.actual_mix_pull_time_local)
        self.assertIsNone(
            SortDateMission.query.filter_by(destination="SDF").one().actual_pure_pull_time_local
        )
        self.assertIsNone(
            SortDateMission.query.filter_by(destination="ONT").one().actual_mix_pull_time_local
        )

    def test_door_view_pull_autosave_returns_structured_safe_server_error(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        with patch(
            "app.neonodes.neoermac.routes.save_single_door_pull",
            side_effect=RuntimeError("database details must stay private"),
        ), self.assertLogs(self.app.logger, level="ERROR") as captured:
            response = self.client.post(
                "/neoermac/door-view/pull-autosave",
                data={
                    "door": "D34",
                    "destination": "SDF",
                    "pull_key": "pure",
                    "actual_pull": "01:44",
                    "no_pull": "0",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 500)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "pull_save_failed")
        self.assertNotIn("database details", payload["error"])
        self.assertIn("gateway_id=", "\n".join(captured.output))

    def test_door_view_pull_entries_render_autosave_without_manual_save_button(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-pull-save-url=", response.data)
        self.assertIn(b"data-door-pull-form", response.data)
        self.assertIn(b"data-hhmm-input", response.data)
        self.assertIn(b"data-pull-key=\"pure\"", response.data)
        self.assertIn(b"data-pull-autosave-error", response.data)
        self.assertIn(b"data-pull-autosave-status", response.data)
        self.assertNotIn(b"SAVE PULLS", response.data)
        self.assertNotIn(b"data-manual-pull-save", response.data)

    def test_door_view_pull_autosave_rejects_invalid_hhmm_without_overwriting_saved_value(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")
        ok_response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "14:05",
                "no_pull": "0",
            },
        )

        response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "pure",
                "actual_pull": "2:05 PM",
                "no_pull": "0",
            },
        )

        payload = response.get_json()
        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        mission = SortDateMission.query.filter_by(destination="SDF").one()
        self.assertEqual(ok_response.status_code, 200)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("HH:MM", payload["error"])
        self.assertEqual(payload["error_code"], "invalid_pull_time")
        self.assertEqual(payload["field"], "actual_pull")
        self.assertEqual(saved.actual_pure_pull_time_local, time(14, 5))
        self.assertEqual(mission.actual_pure_pull_time_local, time(14, 5))

    def test_door_view_pull_autosave_saves_no_checkbox_state(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS302", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view/pull-autosave",
            data={
                "door": "D34",
                "destination": "SDF",
                "pull_key": "mix",
                "actual_pull": "14:07",
                "no_pull": "1",
            },
        )

        payload = response.get_json()
        saved = NeoErmacDoorPull.query.filter_by(door="D34", destination="SDF").one()
        mission = SortDateMission.query.filter_by(destination="SDF").one()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["card"]["no_pull"]["mix"])
        self.assertTrue(saved.no_mix_pull)
        self.assertIsNone(saved.actual_mix_pull_time_local)
        self.assertIsNone(mission.actual_mix_pull_time_local)

    def test_door_view_completed_pull_card_collapses_with_summary(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS302", "SDF", tail="N302UP", parking="A01")
        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mission.sort_date_operation_id,
                door="D34",
                destination="SDF",
                actual_pure_pull_time_local=time(14, 5),
                no_mix_pull=True,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"is-pulls-complete is-pulls-collapsed", response.data)
        summary_match = re.search(
            rb'<div class="neoermac-door-complete-summary"[^>]*>.*?</div>',
            response.data,
            re.S,
        )
        self.assertIsNotNone(summary_match)
        summary_html = summary_match.group(0)
        self.assertIn(b"SDF A01 COMPLETE", summary_html)
        self.assertNotIn(b"PULLS COMPLETE", summary_html)
        self.assertNotIn(b"Parking", summary_html)
        self.assertIn("PURE 14:05 · MIX NONE".encode(), summary_html)
        self.assertIn(b"EDIT PULLS", response.data)
        self.assertIn(b"data-pull-edit-toggle", response.data)

    def test_door_view_partial_pull_card_does_not_collapse(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure("UPS302", "SDF")
        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mission.sort_date_operation_id,
                door="D34",
                destination="SDF",
                actual_pure_pull_time_local=time(14, 5),
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        rendered_html = response.data.split(
            b'const root = document.querySelector("[data-door-view]");',
            1,
        )[0]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"is-pulls-complete", rendered_html)
        self.assertNotIn(b"SDF - COMPLETE", rendered_html)

    def test_door_view_edit_user_can_create_and_update_uld_requested_counts(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
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
                "setup_needed": "on",
            },
            follow_redirects=False,
        )
        separate_response = self.client.post(
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

        setup_request = NeoErmacUldRequest.query.filter_by(
            door="D34",
            setup_needed=True,
        ).one()
        standard_request = NeoErmacUldRequest.query.filter_by(
            door="D34",
            setup_needed=False,
        ).one()
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(separate_response.status_code, 302)
        self.assertEqual(setup_request.a2_count, 6)
        self.assertEqual(setup_request.a1_count, 1)
        self.assertEqual(setup_request.amp_count, 4)
        self.assertEqual(standard_request.a2_count, 1)
        self.assertEqual(standard_request.a1_count, 0)
        self.assertEqual(standard_request.amp_count, 0)

    def test_door_view_cancel_request_deletes_only_selected_request(self):
        standard_request = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D34",
            a2_count=2,
            setup_needed=False,
            created_at=datetime(2026, 6, 24, 19, 1),
            updated_at=datetime(2026, 6, 24, 19, 1),
        )
        setup_request = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D34",
            a2_count=3,
            setup_needed=True,
            created_at=datetime(2026, 6, 24, 19, 2),
            updated_at=datetime(2026, 6, 24, 19, 2),
        )
        other_door_request = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D35",
            a2_count=4,
            setup_needed=False,
            created_at=datetime(2026, 6, 24, 19, 3),
            updated_at=datetime(2026, 6, 24, 19, 3),
        )
        db.session.add_all([standard_request, setup_request, other_door_request])
        db.session.commit()
        standard_id = standard_request.id
        setup_id = setup_request.id
        other_id = other_door_request.id
        self._login_approved_user(role="operator")

        cancel_standard = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "delete_uld_request",
                "request_id": str(standard_id),
            },
            follow_redirects=False,
        )

        self.assertEqual(cancel_standard.status_code, 302)
        self.assertIsNone(db.session.get(NeoErmacUldRequest, standard_id))
        self.assertIsNotNone(db.session.get(NeoErmacUldRequest, setup_id))
        self.assertIsNotNone(db.session.get(NeoErmacUldRequest, other_id))

        cancel_setup = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "delete_uld_request",
                "request_id": str(setup_id),
            },
            follow_redirects=False,
        )

        self.assertEqual(cancel_setup.status_code, 302)
        self.assertIsNone(db.session.get(NeoErmacUldRequest, setup_id))
        self.assertIsNotNone(db.session.get(NeoErmacUldRequest, other_id))

    def test_door_view_edit_request_sets_counts_and_updates_timestamp(self):
        original_time = datetime(2026, 6, 24, 19, 1)
        edit_time = datetime(2026, 6, 24, 19, 45)
        request_record = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D34",
            a2_count=2,
            a1_count=1,
            amp_count=3,
            setup_needed=False,
            created_at=original_time,
            updated_at=original_time,
        )
        other_request = NeoErmacUldRequest(
            gateway_id=self.gateway.id,
            door="D35",
            a2_count=5,
            setup_needed=False,
            created_at=original_time,
            updated_at=original_time,
        )
        db.session.add_all([request_record, other_request])
        db.session.commit()
        request_id = request_record.id
        other_id = other_request.id
        self._login_approved_user(role="operator")

        with patch("app.services.uld_requests.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = edit_time
            response = self.client.post(
                "/neoermac/door-view?door=D34",
                data={
                    "door": "D34",
                    "action": "edit_uld_request",
                    "request_id": str(request_id),
                    "uld_a2_count": "4",
                    "uld_a1_count": "0",
                    "uld_amp_count": "1",
                },
                follow_redirects=False,
            )

        db.session.refresh(request_record)
        db.session.refresh(other_request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request_record.a2_count, 4)
        self.assertEqual(request_record.a1_count, 0)
        self.assertEqual(request_record.amp_count, 1)
        self.assertEqual(request_record.updated_at, edit_time)
        self.assertEqual(other_request.id, other_id)
        self.assertEqual(other_request.a2_count, 5)
        self.assertEqual(other_request.updated_at, original_time)

    def test_door_view_legacy_clear_request_still_scopes_selected_door_context(self):
        db.session.add_all(
            [
                NeoErmacUldRequest(
                    gateway_id=self.gateway.id,
                    door="D34",
                    a2_count=2,
                    setup_needed=False,
                    created_at=datetime(2026, 6, 24, 19, 1),
                    updated_at=datetime(2026, 6, 24, 19, 1),
                ),
                NeoErmacUldRequest(
                    gateway_id=self.gateway.id,
                    door="D34",
                    a2_count=3,
                    setup_needed=True,
                    created_at=datetime(2026, 6, 24, 19, 2),
                    updated_at=datetime(2026, 6, 24, 19, 2),
                ),
                NeoErmacUldRequest(
                    gateway_id=self.gateway.id,
                    door="D35",
                    a2_count=4,
                    setup_needed=False,
                    created_at=datetime(2026, 6, 24, 19, 3),
                    updated_at=datetime(2026, 6, 24, 19, 3),
                ),
            ]
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        clear_respot = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "clear_uld_request": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(clear_respot.status_code, 302)
        self.assertIsNone(
            NeoErmacUldRequest.query.filter_by(door="D34", setup_needed=False).first()
        )
        self.assertIsNotNone(
            NeoErmacUldRequest.query.filter_by(door="D34", setup_needed=True).first()
        )
        self.assertIsNotNone(
            NeoErmacUldRequest.query.filter_by(door="D35", setup_needed=False).first()
        )

        clear_setup = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "clear_uld_request": "1",
                "setup_needed": "on",
            },
            follow_redirects=False,
        )

        self.assertEqual(clear_setup.status_code, 302)
        self.assertIsNone(
            NeoErmacUldRequest.query.filter_by(door="D34", setup_needed=True).first()
        )
        self.assertIsNotNone(
            NeoErmacUldRequest.query.filter_by(door="D35", setup_needed=False).first()
        )

    def test_door_view_request_form_renders_clean_mobile_markup(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"neoermac-door-mobile-tight", response.data)
        self.assertIn(b"data-uld-request-form", response.data)
        self.assertIn(b'name="uld_a2_count" min="0" step="1" inputmode="numeric" value="0"', response.data)
        self.assertIn(b'name="uld_a1_count" min="0" step="1" inputmode="numeric" value="0"', response.data)
        self.assertIn(b'name="uld_amp_count" min="0" step="1" inputmode="numeric" value="0"', response.data)
        self.assertIn(b'class="neoermac-label-mobile">PURE</span>', response.data)
        self.assertIn(b'class="neoermac-label-mobile">MIX</span>', response.data)
        self.assertNotIn(b'class="neoermac-label-mobile">1ST</span>', response.data)
        self.assertNotIn(b'class="neoermac-label-mobile">2ND</span>', response.data)
        self.assertIn(b"neoermac-setup-toggle neoermac-large-checkbox-toggle", response.data)
        self.assertIn(b"neoermac-none-toggle neoermac-large-checkbox-toggle", response.data)
        self.assertIn(b'class="neoermac-label-mobile">NO</span>', response.data)
        self.assertNotIn(b'class="neoermac-label-mobile">NONE</span>', response.data)
        self.assertIn(b"data-door-fixed-controls", response.data)
        self.assertIn(b"data-operation-refresh-banner", response.data)
        self.assertIn(b"data-door-supervision", response.data)
        self.assertIn(b"data-door-supervision-open", response.data)
        self.assertEqual(response.data.count(b"neoermac-uld-type-label"), 3)
        self.assertIn(b'<span class="neoermac-uld-type-label">A2</span>', response.data)
        self.assertIn(b'<span class="neoermac-uld-type-label">A1</span>', response.data)
        self.assertIn(b'<span class="neoermac-uld-type-label">AMP</span>', response.data)
        self.assertNotIn(b"CLEAR REQUEST", response.data)
        self.assertNotIn(b"SAVE PULLS", response.data)
        self.assertGreaterEqual(response.data.count(b"neoermac-ios-safe-input"), 6)
        css = Path("app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn(".neoermac-door-actual input.neoermac-ios-safe-input", css)
        self.assertIn(".neoermac-uld-grid input.neoermac-ios-safe-input", css)
        self.assertIn(".neoermac-uld-grid .neoermac-uld-type-label", css)
        self.assertIn(".neoermac-uld-request-edit-button", css)
        self.assertIn(".neoermac-uld-request-cancel", css)
        self.assertIn(".neoermac-door-autosave-status", css)
        self.assertIn(".neoermac-large-checkbox-toggle input", css)
        self.assertIn(".neoermac-none-toggle", css)
        self.assertIn("font-size: 16px", css)
        self.assertIn("font-size: 0.74rem", css)
        self.assertIn("font-size: 0.84rem", css)
        door_shell_block = css.split(".neoermac-door-shell {", 1)[1].split("}", 1)[0]
        self.assertIn("border: 0;", door_shell_block)
        self.assertIn("border-radius: 0;", door_shell_block)
        self.assertIn("background: transparent;", door_shell_block)
        self.assertIn("box-shadow: none;", door_shell_block)
        self.assertIn(
            "body.blueprint-neoermac.mobile-app-chrome .neoermac-door-fixed-controls {",
            css,
        )
        sticky_controls_block = css.split(
            "body.blueprint-neoermac.mobile-app-chrome .neoermac-door-fixed-controls {",
            1,
        )[1].split("}", 1)[0]
        self.assertIn("position: sticky;", sticky_controls_block)
        self.assertIn("max-width: 100%;", sticky_controls_block)

    def test_door_view_request_inputs_remain_clean_after_submission(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "0",
                "uld_amp_count": "1",
                "setup_needed": "on",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SETUP", response.data)
        self.assertIn(b"name=\"uld_a2_count\" min=\"0\" step=\"1\" inputmode=\"numeric\" value=\"0\"", response.data)
        self.assertIn(b"name=\"uld_a1_count\" min=\"0\" step=\"1\" inputmode=\"numeric\" value=\"0\"", response.data)
        self.assertIn(b"name=\"uld_amp_count\" min=\"0\" step=\"1\" inputmode=\"numeric\" value=\"0\"", response.data)
        form_html = self._element_html(response.data, b"neoermac-uld-form")
        self.assertIn(b"name=\"setup_needed\"", form_html)
        self.assertNotIn(b"checked", form_html)

    def test_door_view_displays_setup_and_respot_requests_for_same_door(self):
        setup_time = datetime(2026, 6, 24, 19, 5)
        standard_time = datetime(2026, 6, 24, 19, 2)
        db.session.add_all(
            [
                NeoErmacUldRequest(
                    gateway_id=self.gateway.id,
                    door="D34",
                    a2_count=2,
                    a1_count=0,
                    amp_count=1,
                    setup_needed=True,
                    created_at=setup_time,
                    updated_at=setup_time,
                ),
                NeoErmacUldRequest(
                    gateway_id=self.gateway.id,
                    door="D34",
                    a2_count=0,
                    a1_count=3,
                    amp_count=0,
                    setup_needed=False,
                    created_at=standard_time,
                    updated_at=standard_time,
                ),
            ]
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")
        rendered_html = response.data.split(
            b'const root = document.querySelector("[data-door-view]");',
            1,
        )[0]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rendered_html.count(b"data-uld-request-row"), 2)
        self.assertEqual(rendered_html.count(b"data-uld-request-edit"), 2)
        self.assertEqual(rendered_html.count(b"class=\"neoermac-uld-request-cancel\""), 2)
        self.assertIn(b"name=\"action\" value=\"edit_uld_request\"", rendered_html)
        self.assertIn(b"name=\"action\" value=\"delete_uld_request\"", rendered_html)
        self.assertIn(b"SETUP", response.data)
        self.assertIn(b"RESPOT", response.data)
        self.assertNotIn(b"STANDARD", response.data)
        self.assertLess(response.data.index(b"SETUP"), response.data.index(b"RESPOT"))
        self.assertIn(b"14:05", response.data)
        self.assertIn(b"14:02", response.data)

    def test_door_view_uld_requests_are_scoped_to_current_sort_operation(self):
        current_sort_date = current_gateway_local_datetime(self.gateway).date()
        old_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=current_sort_date - timedelta(days=1),
            sort_name="night",
        )
        current_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=current_sort_date,
            sort_name="night",
        )
        db.session.add_all([old_operation, current_operation])
        db.session.flush()
        db.session.add(
            NeoErmacUldRequest(
                gateway_id=self.gateway.id,
                sort_date_operation_id=old_operation.id,
                door="D34",
                a2_count=9,
                setup_needed=False,
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")
        self._grant_node_role("neoermac_operator_user", "sektor", "operator")

        initial_response = self.client.get("/neoermac/door-view?door=D34")
        create_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "0",
                "uld_amp_count": "0",
            },
            follow_redirects=False,
        )
        door_state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        discharge_state = self.client.get("/neosektor/discharge/state").get_json()["state"]

        requests = NeoErmacUldRequest.query.filter_by(door="D34").order_by(
            NeoErmacUldRequest.sort_date_operation_id.asc(),
            NeoErmacUldRequest.id.asc(),
        ).all()

        self.assertEqual(initial_response.status_code, 200)
        self.assertNotIn(b"A2 <strong>9</strong>", initial_response.data)
        self.assertIn(b"No relevant active ULD requests.", initial_response.data)
        self.assertEqual(create_response.status_code, 302)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].sort_date_operation_id, old_operation.id)
        self.assertEqual(requests[1].sort_date_operation_id, current_operation.id)
        self.assertEqual(requests[1].a2_count, 2)
        self.assertEqual(door_state["operation_id"], current_operation.id)
        self.assertEqual(len(door_state["requests"]), 1)
        self.assertEqual(door_state["requests"][0]["sort_date_operation_id"], current_operation.id)
        self.assertEqual(door_state["requests"][0]["counts"]["A2"], 2)
        self.assertEqual([row["id"] for row in discharge_state["requests"]], [requests[1].id])

    def test_door_view_discharge_end_to_end_request_send_and_expiry_flow(self):
        self._login_approved_user(role="operator")
        self._grant_node_role("neoermac_operator_user", "sektor", "operator")

        standard_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "2",
                "uld_a1_count": "1",
                "uld_amp_count": "0",
            },
            follow_redirects=False,
        )
        setup_response = self.client.post(
            "/neoermac/door-view?door=D34",
            data={
                "door": "D34",
                "action": "save_uld_request",
                "uld_a2_count": "1",
                "uld_a1_count": "0",
                "uld_amp_count": "2",
                "setup_needed": "on",
            },
            follow_redirects=False,
        )

        self.assertEqual(standard_response.status_code, 302)
        self.assertEqual(setup_response.status_code, 302)
        requests = NeoErmacUldRequest.query.filter_by(door="D34").order_by(
            NeoErmacUldRequest.setup_needed.desc(),
            NeoErmacUldRequest.id.asc(),
        ).all()
        self.assertEqual(len(requests), 2)
        setup_request = requests[0]
        standard_request = requests[1]
        self.assertTrue(setup_request.setup_needed)
        self.assertFalse(standard_request.setup_needed)

        door_state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertEqual(len(door_state["requests"]), 2)
        self.assertTrue(door_state["requests"][0]["setup_needed"])
        self.assertEqual(
            door_state["requests"][0]["counts"],
            {"A2": 1, "A1": 0, "AMP": 2},
        )
        self.assertEqual(
            door_state["requests"][1]["counts"],
            {"A2": 2, "A1": 1, "AMP": 0},
        )

        discharge_state = self.client.get("/neosektor/discharge/state").get_json()["state"]
        self.assertEqual([row["id"] for row in discharge_state["requests"]], [setup_request.id, standard_request.id])

        partial_response = self.client.post(
            "/neosektor/discharge/send",
            data={
                "door": "D34",
                "request_id": str(setup_request.id),
                "send_a2_count": "1",
                "send_a1_count": "0",
                "send_amp_count": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(partial_response.status_code, 302)
        db.session.refresh(setup_request)
        self.assertEqual(setup_request.a2_count, 0)
        self.assertEqual(setup_request.a1_count, 0)
        self.assertEqual(setup_request.amp_count, 1)

        after_partial_state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertEqual(
            [(event["uld_type"], event["quantity"]) for event in after_partial_state["on_the_way_events"]],
            [("A2", 1), ("AMP", 1)],
        )

        oversend_response = self.client.post(
            "/neosektor/discharge/send",
            data={
                "door": "D34",
                "request_id": str(setup_request.id),
                "send_a2_count": "0",
                "send_a1_count": "0",
                "send_amp_count": "4",
            },
            follow_redirects=False,
        )
        self.assertEqual(oversend_response.status_code, 302)
        self.assertIsNone(db.session.get(NeoErmacUldRequest, setup_request.id))
        remaining_request = NeoErmacUldRequest.query.filter_by(door="D34").one()
        self.assertEqual(remaining_request.id, standard_request.id)

        discharge_after_oversend = self.client.get("/neosektor/discharge/state").get_json()["state"]
        self.assertEqual([row["id"] for row in discharge_after_oversend["requests"]], [standard_request.id])
        final_door_state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertEqual(len(final_door_state["requests"]), 1)
        self.assertFalse(final_door_state["requests"][0]["setup_needed"])
        self.assertEqual(
            [(event["uld_type"], event["quantity"]) for event in final_door_state["on_the_way_events"]],
            [("A2", 1), ("AMP", 1), ("AMP", 4)],
        )

        for event in NeoSektorUldOnTheWayEvent.query.filter_by(door="D34").all():
            event.expires_at_utc = datetime(2026, 6, 24, 0, 0)
        db.session.commit()
        expired_state = self.client.get("/neoermac/door-view/state?door=D34").get_json()["state"]
        self.assertEqual(expired_state["on_the_way_events"], [])
        self.assertEqual(NeoErmacUldRequest.query.filter_by(door="D34").count(), 1)

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
        self.assertIn(format_local_hhmm(now), payload["state"]["on_the_way_events"][0]["label"])
        self.assertEqual(payload["state"]["requests"], [])

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

    def test_door_view_state_short_circuits_an_unchanged_revision(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure("UPS948", "SDF", tail="N316UP")
        db.session.commit()
        self._login_approved_user(role="operator")

        page = self.client.get("/neoermac/door-view?door=D34")
        self.assertIn(b"data-door-view-revision=", page.data)
        self.assertIn(b'pollUrl.searchParams.set("revision", currentRevision)', page.data)
        first = self.client.get("/neoermac/door-view/state?door=D34").get_json()
        with patch("app.neonodes.neoermac.routes.door_view_uld_state") as full_state:
            response = self.client.get(
                "/neoermac/door-view/state?door=D34&revision=" + first["revision"]
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["changed"])
        self.assertEqual(payload["revision"], first["revision"])
        self.assertIn("refresh", payload)
        self.assertNotIn("state", payload)
        full_state.assert_not_called()

    def test_door_view_state_revision_change_runs_full_payload(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        mission = self._add_operation_departure(
            "UPS948",
            "SDF",
            tail="N316UP",
            departure_status="scheduled",
        )
        db.session.commit()
        self._login_approved_user(role="operator")
        first = self.client.get("/neoermac/door-view/state?door=D34").get_json()

        mission.departure_status = "loading"
        db.session.commit()
        response = self.client.get(
            "/neoermac/door-view/state?door=D34&revision=" + first["revision"]
        )

        payload = response.get_json()
        self.assertTrue(payload["changed"])
        self.assertNotEqual(payload["revision"], first["revision"])
        self.assertEqual(payload["state"]["destinations"][0]["status"], "Loading")

        db.session.add(
            NeoErmacDoorPull(
                gateway_id=self.gateway.id,
                sort_date_operation_id=mission.sort_date_operation_id,
                door="D34",
                destination="SDF",
                no_pure_pull=True,
            )
        )
        db.session.commit()
        after_pull = self.client.get(
            "/neoermac/door-view/state?door=D34&revision=" + payload["revision"]
        ).get_json()
        self.assertTrue(after_pull["changed"])
        self.assertNotEqual(after_pull["revision"], payload["revision"])
        self.assertTrue(after_pull["state"]["destinations"][0]["no_pull"]["pure"])

    def test_door_view_changed_state_bulk_loads_door_pulls_once(self):
        from app.services.neoermac_door_view import door_view_uld_state

        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._assign_lineup_destination("runout_10", "west_destination_2", "ONT")
        first = self._add_operation_departure("UPS948", "SDF")
        self._add_operation_departure("UPS949", "ONT")
        db.session.add_all(
            [
                NeoErmacDoorPull(
                    gateway_id=self.gateway.id,
                    sort_date_operation_id=first.sort_date_operation_id,
                    door="D34",
                    destination=destination,
                    no_pure_pull=True,
                )
                for destination in ("SDF", "ONT")
            ]
        )
        db.session.commit()
        statements = []

        def capture_statement(_conn, _cursor, statement, _params, _context, _many):
            normalized = " ".join(statement.lower().split())
            if "from neoermac_door_pulls" in normalized:
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            state = door_view_uld_state(
                self.gateway,
                "D34",
                supervised_doors=("D34",),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        self.assertEqual(len(state["destinations"]), 2)
        self.assertEqual(len(statements), 1)

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
        self.assertIn(
            f"2 A2s sent at {format_local_hhmm(sent_at)}".encode(),
            response.data,
        )
        self.assertNotIn(f"2 A2s sent at {sent_at:%H:%M}".encode(), response.data)

    def test_door_view_displays_oversent_on_the_way_totals_exactly(self):
        sent_at = datetime.utcnow()
        db.session.add(
            NeoSektorUldOnTheWayEvent(
                gateway_id=self.gateway.id,
                door="D34",
                uld_type="AMP",
                quantity=5,
                sent_at_utc=sent_at,
                expires_at_utc=sent_at + timedelta(minutes=5),
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/door-view?door=D34")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"5 AMPs sent at {format_local_hhmm(sent_at)}".encode(),
            response.data,
        )

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
        self.assertIn(
            f"1 AMP sent at {format_local_hhmm(sent_at)}".encode(),
            reload_response.data,
        )

    def test_building_lineup_page_renders_belt_map(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"neo-page-title neoermac-operation-title", response.data)
        self.assertIn(b"neoermac-sequence-door", response.data)
        self.assertIn(b"Orange", response.data)
        self.assertIn(b"White/Blue", response.data)
        self.assertIn(b"Blue/Black", response.data)
        self.assertNotIn(b"neoermac-pull-edge", response.data)
        self.assertIn(b"PURE", response.data)
        self.assertIn(b"MIX", response.data)
        self.assertNotIn(b"1ST MIX", response.data)
        self.assertNotIn(b"2ND MIX", response.data)
        self.assertEqual(response.data.count(b"neoermac-belt-group"), 12)
        self.assertEqual(response.data.count(b"data-door-pair="), 12)
        self.assertEqual(response.data.count(b"neoermac-sequence-door"), 24)
        self.assertEqual(response.data.count(b'data-door-position="top"'), 12)
        self.assertEqual(response.data.count(b'data-door-position="bottom"'), 12)
        self.assertEqual(response.data.count(b"data-physical-belt="), 24)
        self.assertEqual(response.data.count(b"data-belt-axis"), 24)
        self.assertEqual(response.data.count(b'data-belt-side="east"'), 24)
        self.assertEqual(response.data.count(b'data-belt-side="west"'), 24)
        self.assertIn(b"neoermac-belt-block", response.data)
        self.assertIn(b"neoermac-belt-destination-stack", response.data)
        self.assertIn(b"neoermac-belt-destination-card", response.data)
        self.assertIn(b"neoermac-belt-destination-row", response.data)
        self.assertIn(b"neoermac-belt-block--blue", response.data)
        self.assertIn(b"neoermac-belt-block--brown", response.data)
        self.assertIn(b"neoermac-belt-block--white", response.data)
        self.assertIn(b'data-belt-color="blue"', response.data)
        self.assertIn(b"data-lineup-autosave-url", response.data)
        self.assertIn(b"data-lineup-destination-select", response.data)
        self.assertIn(b'data-pull-time-key="pure"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="east_destination_1"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="east_destination_2"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="east_destination_1_slot_2"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="east_destination_2_slot_2"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="west_destination_1_slot_2"', response.data)
        self.assertIn(b'data-lineup-assignment-slot="west_destination_2_slot_2"', response.data)
        self.assertEqual(
            response.data.count(b"data-lineup-assignment-slot="),
            96,
        )
        self.assertIn(b'data-belt-side="east" data-supervising-door="D1"', response.data)
        self.assertIn(b'data-belt-side="west" data-supervising-door="D4"', response.data)
        self.assertIn(b'data-belt-side="east" data-supervising-door="D34"', response.data)
        self.assertIn(b'data-belt-side="west" data-supervising-door="D37"', response.data)
        self.assertIn(b"FACES D1", response.data)
        self.assertIn(b"FACES D37", response.data)
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

        html = response.data.decode()
        first_pair = html.split('data-door-pair="D1-D4"', 1)[1].split(
            "</article>",
            1,
        )[0]
        first_belt = first_pair.split('data-physical-belt="1"', 1)[1].split(
            'data-physical-belt="2"',
            1,
        )[0]
        self.assertEqual(first_pair.count('data-door-position="top"'), 1)
        self.assertEqual(first_pair.count('data-door-position="bottom"'), 1)
        self.assertEqual(first_pair.count("data-physical-belt="), 2)
        self.assertEqual(first_pair.count("data-lineup-assignment-slot="), 8)
        self.assertLess(
            first_pair.index('data-door-position="top"'),
            first_pair.index('data-physical-belt="1"'),
        )
        self.assertLess(
            first_pair.index('data-physical-belt="2"'),
            first_pair.index('data-door-position="bottom"'),
        )
        self.assertLess(
            first_belt.index('data-belt-side="east"'),
            first_belt.index("data-belt-axis"),
        )
        self.assertLess(
            first_belt.index("data-belt-axis"),
            first_belt.index('data-belt-side="west"'),
        )
        self.assertEqual(first_belt.count("data-lineup-assignment-slot="), 4)
        self.assertIn(
            'data-belt-side="east" data-supervising-door="D1"',
            first_belt,
        )
        self.assertIn(
            'data-belt-side="west" data-supervising-door="D4"',
            first_belt,
        )

    def test_building_lineup_displays_planned_pull_times_for_assigned_destination(self):
        self._add_master_departure(
            "UPS105",
            "SDF",
            pure_pull_time_local=time(1, 10),
            mix_pull_time_local=time(1, 40),
        )
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"01:10", response.data)
        self.assertIn(b"01:40", response.data)

    def test_building_lineup_displays_current_sort_mission_pull_times(self):
        self._assign_lineup_destination("green_runout", "east_destination_2", "SDF")
        self._add_operation_departure(
            "UPS205",
            "SDF",
            pure_pull_time_local=time(0, 55),
            mix_pull_time_local=time(1, 25),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"00:55", response.data)
        self.assertIn(b"01:25", response.data)

    def test_building_lineup_displays_effective_window_adjusted_pull_times(self):
        self._assign_lineup_destination("green_runout", "east_destination_2", "SDF")
        self._add_operation_departure(
            "UPS205",
            "SDF",
            window_minutes=10,
            pure_pull_time_local=time(0, 55),
            mix_pull_time_local=time(1, 25),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"01:05", response.data)
        self.assertIn(b"01:35", response.data)

    def test_building_lineup_renders_pull_times_for_each_destination_side(self):
        self._add_master_departure("UPS210", "SDF")
        self._add_master_departure("UPS211", "ONT")
        self._assign_lineup_destination("green_runout", "east_destination_2", "SDF")
        self._assign_lineup_destination("green_runout", "west_destination_2", "ONT")
        self._add_operation_departure(
            "UPS210",
            "SDF",
            pure_pull_time_local=time(0, 55),
            mix_pull_time_local=time(1, 25),
        )
        self._add_operation_departure(
            "UPS211",
            "ONT",
            pure_pull_time_local=time(2, 5),
            mix_pull_time_local=time(2, 35),
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
        for expected_time in ("00:55", "01:25"):
            self.assertIn(expected_time, upper_card)
            self.assertNotIn(expected_time, lower_card)
        for expected_time in ("02:05", "02:35"):
            self.assertIn(expected_time, lower_card)
            self.assertNotIn(expected_time, upper_card)
        self.assertIn("neoermac-slot-pull-times", upper_card)
        self.assertIn("neoermac-slot-pull-times", lower_card)
        self.assertNotIn("neoermac-pull-edge", html)

    def test_building_lineup_renders_pure_and_mix_pull_cards_inside_belts(self):
        self._add_master_departure("UPS216", "SDF")
        self._add_master_departure("UPS217", "ONT")
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        self._assign_lineup_destination("green_runout", "east_destination_2", "ONT")
        self._add_operation_departure(
            "UPS216",
            "SDF",
            pure_pull_time_local=time(0, 45),
            mix_pull_time_local=time(1, 15),
        )
        self._add_operation_departure(
            "UPS217",
            "ONT",
            pure_pull_time_local=time(2, 5),
            mix_pull_time_local=time(2, 35),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")
        html = response.data.decode()
        first_card = html.split('name="lineup_green_runout_east_destination_1"', 1)[
            1
        ].split("</label>", 1)[0]
        second_card = html.split('name="lineup_green_runout_east_destination_2"', 1)[
            1
        ].split("</label>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertLess(
            html.index('name="lineup_green_runout_east_destination_1"'),
            html.index('name="lineup_green_runout_east_destination_2"'),
        )
        self.assertIn("neoermac-slot-pull-times", first_card)
        self.assertIn("00:45", first_card)
        self.assertIn("01:15", first_card)
        self.assertIn("neoermac-slot-pull-times", second_card)
        self.assertIn("02:05", second_card)
        self.assertIn("02:35", second_card)

    def test_building_lineup_mobile_preserves_physical_orientation_and_scrolls(self):
        self._add_master_departure("UPS213", "SDF")
        self._add_master_departure("UPS214", "ONT")
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        self._assign_lineup_destination("green_runout", "west_destination_1", "ONT")
        self._add_operation_departure(
            "UPS213",
            "SDF",
            pure_pull_time_local=time(0, 45),
            mix_pull_time_local=time(1, 15),
        )
        self._add_operation_departure(
            "UPS214",
            "ONT",
            pure_pull_time_local=time(2, 5),
            mix_pull_time_local=time(2, 35),
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")
        html = response.data.decode()
        css = Path("app/static/css/base.css").read_text(encoding="utf-8")
        left_pair = html.split('name="lineup_green_runout_east_destination_1"', 1)[
            1
        ].split("</label>", 1)[0]
        right_pair = html.split('name="lineup_green_runout_west_destination_1"', 1)[
            1
        ].split("</label>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("neoermac-mobile-pull-strip", html)
        self.assertNotIn("neoermac-mobile-belt-topline", html)
        self.assertIn('data-mobile-destination-slot="1"', html)
        self.assertIn('data-mobile-destination-slot="2"', html)
        self.assertIn('data-door-pair="D1-D4"', html)
        self.assertIn('data-door-position="top" data-facing-side="east"', html)
        self.assertIn('data-door-position="bottom" data-facing-side="west"', html)
        self.assertIn("neoermac-belt-destination-stack", html)
        self.assertGreaterEqual(html.count("neoermac-belt-destination-card"), 4)
        self.assertIn("neoermac-slot-pull-times", left_pair)
        self.assertIn("00:45", left_pair)
        self.assertIn("01:15", left_pair)
        self.assertIn("neoermac-slot-pull-times", right_pair)
        self.assertIn("02:05", right_pair)
        self.assertIn("02:35", right_pair)
        self.assertNotIn("neoermac-mobile-slot-belt-name", html)
        self.assertIn(
            ".neoermac-lineup-shell .neoermac-runout-list {\n"
            "        display: flex;",
            css,
        )
        self.assertIn("overflow-x: auto;", css)
        self.assertIn("scroll-snap-type: x proximity;", css)
        self.assertIn("flex: 0 0 clamp(460px, 128vw, 520px);", css)
        self.assertIn(
            ".neoermac-lineup-shell .neoermac-belt-group "
            ".neoermac-belt-grid {\n"
            "        grid-template-columns: repeat(2, minmax(0, 1fr));",
            css,
        )

        first_pair = html.split('data-door-pair="D1-D4"', 1)[1].split(
            "</article>",
            1,
        )[0]
        first_belt = first_pair.split('data-physical-belt="1"', 1)[1].split(
            'data-physical-belt="2"',
            1,
        )[0]
        self.assertLess(
            first_belt.index('data-belt-side="east"'),
            first_belt.index("data-belt-axis"),
        )
        self.assertLess(
            first_belt.index("data-belt-axis"),
            first_belt.index('data-belt-side="west"'),
        )

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
        self.assertGreaterEqual(card.count('data-pull-time-key="'), 2)
        self.assertGreaterEqual(card.count(">--</strong>"), 2)

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

    def test_building_lineup_excludes_standalone_spare_tail(self):
        self._add_master_departure("UPS101", "sdf")
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 6, 11),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=0,
        )
        db.session.add(operation)
        db.session.add(
            SortDateTailState(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number="N555UP",
                aircraft_type="767",
                aircraft_type_source="manual",
                operational_status="spare",
            )
        )
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="SDF"', response.data)
        self.assertNotIn(b"N555UP", response.data)
        self.assertNotIn(b"STANDALONE SPARE", response.data)

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
        self.assertIn(
            b'name="lineup_green_runout_east_destination_1_slot_2"',
            response.data,
        )
        self.assertIn(
            b'name="lineup_green_runout_west_destination_2_slot_2"',
            response.data,
        )
        self.assertIn(b"data-lineup-autosave-status", response.data)
        self.assertIn(b"data-lineup-autosave-error", response.data)
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

    def test_building_lineup_destination_autosave_saves_one_field_and_returns_pull_times(self):
        self._add_master_departure("UPS411", "SDF")
        self._add_master_departure("UPS412", "ONT")
        self._add_master_departure("UPS413", "PHX")
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        self._assign_lineup_destination("green_runout", "west_destination_1", "PHX")
        self._add_operation_departure(
            "UPS412",
            "ONT",
            pure_pull_time_local=time(2, 5),
            mix_pull_time_local=time(2, 35),
        )
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoermac/building-lineup/destination",
            data={
                "field": "lineup_green_runout_east_destination_1",
                "destination": "ont",
            },
        )

        payload = response.get_json()
        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["destination"], "ONT")
        self.assertEqual(payload["pull_times"]["pure"], "02:05")
        self.assertEqual(payload["pull_times"]["mix"], "02:35")
        self.assertEqual(saved.east_destination_1, "ONT")
        self.assertEqual(saved.west_destination_1, "PHX")

    def test_building_lineup_destination_autosave_can_clear_destination(self):
        self._add_master_departure("UPS414", "SDF")
        self._assign_lineup_destination("green_runout", "east_destination_1", "SDF")
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoermac/building-lineup/destination",
            data={
                "field": "lineup_green_runout_east_destination_1",
                "destination": "",
            },
        )

        payload = response.get_json()
        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["destination"], "")
        self.assertEqual(payload["pull_times"]["pure"], "--")
        self.assertIsNone(saved.east_destination_1)

    def test_view_only_user_cannot_autosave_building_lineup_destination(self):
        self._add_master_departure("UPS415", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.post(
            "/neoermac/building-lineup/destination",
            data={
                "field": "lineup_green_runout_east_destination_1",
                "destination": "SDF",
            },
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).first()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.get_json()["ok"])
        self.assertIsNone(saved)

    def test_building_lineup_styles_include_dark_pull_time_backgrounds(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".neoermac-belt-block--blue", css)
        self.assertIn(".neoermac-belt-block--red", css)
        self.assertIn(".neoermac-belt-block--white", css)
        self.assertIn(".neoermac-belt-destination-card .neoermac-slot-pull-times", css)
        self.assertIn(".neoermac-belt-destination-stack {\n        grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn("linear-gradient(180deg, rgba(22, 9, 13, 0.98), rgba(6, 8, 12, 0.98))", css)
        self.assertIn("color: #fff8f9;", css)
        self.assertNotIn("#d9dde4", css)
        self.assertIn(".neoermac-lineup-autosave-status", css)

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
        self.assertIn(b"Outbound View", response.data)
        self.assertIn(b"neo-page-title neoermac-operation-title", response.data)
        self.assertIn(b"WINDOW", response.data)
        self.assertNotIn(b"DISCHARGE", response.data)
        self.assertNotIn(b"SAVE", response.data)

    def test_view_outbound_renders_summary_and_door_actuals(self):
        self._assign_lineup_destination("runout_10", "west_destination_1", "SDF")
        self._add_operation_departure(
            "UPS501",
            "SDF",
            tail="N501UP",
            parking="A14",
            window_minutes=20,
            pure_pull_time_local=time(1, 20),
            mix_pull_time_local=time(1, 55),
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
                "actual_mix_0": "02:20",
            },
            follow_redirects=False,
        )
        response = self.client.get("/neoermac/view-outbound")

        mission = SortDateMission.query.filter_by(destination="SDF").one()
        self.assertEqual(save_response.status_code, 302)
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 45))
        self.assertEqual(mission.actual_mix_pull_time_local, time(2, 20))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-neoermac-outbound-layout="pull-table"', response.data)
        self.assertIn(b"data-neoermac-outbound-table", response.data)
        self.assertIn(b"data-neoermac-outbound-row", response.data)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b"UPS501", response.data)
        self.assertIn(b"N501UP", response.data)
        self.assertIn(b"A14", response.data)
        self.assertIn(b"02:35", response.data)
        self.assertIn(b"D34", response.data)
        self.assertIn(b"D32-D34 BELT 1 WEST SLOT 1", response.data)
        self.assertNotIn(b"EAST BLU/BLU BELT", response.data)
        self.assertIn(b"PURE PLAN", response.data)
        self.assertIn(b"PURE ACT", response.data)
        self.assertIn(b"MIX PLAN", response.data)
        self.assertIn(b"MIX ACT", response.data)
        self.assertLess(response.data.index(b"PURE PLAN"), response.data.index(b"PURE ACT"))
        self.assertLess(response.data.index(b"MIX PLAN"), response.data.index(b"MIX ACT"))
        self.assertIn(b"01:40", response.data)
        self.assertIn(b"02:15", response.data)
        self.assertIn(b"01:45", response.data)
        self.assertIn(b"02:20", response.data)
        self.assertIn(b"20 MIN", response.data)

    def test_view_outbound_mobile_uses_single_scan_row_per_mission_without_horizontal_scroll(self):
        self._assign_lineup_destination("runout_10", "east_destination_1", "SDF")
        self._add_operation_departure("UPS501", "SDF", tail="N501UP", parking="A14")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/view-outbound")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'<span class="mobile-topbar-page-name neo-page-title">OUTBOUND</span>',
            response.data,
        )
        self.assertIn(
            b'<span class="neo-page-title motherbrain-desktop-top-title-text">VIEW OUTBOUND</span>',
            response.data,
        )
        self.assertIn(b"data-neoermac-outbound-mobile-list", response.data)
        self.assertIn(b"data-neoermac-outbound-mobile-row", response.data)
        self.assertIn(b'data-neoermac-outbound-layout="pull-table"', response.data)
        mobile_fields = (
            b'data-neoermac-outbound-mobile-field="flight"',
            b'data-neoermac-outbound-mobile-field="tail"',
            b'data-neoermac-outbound-mobile-field="destination"',
            b'data-neoermac-outbound-mobile-field="position"',
            b'data-neoermac-outbound-mobile-field="doors"',
            b'data-neoermac-outbound-mobile-field="pull-times"',
            b'data-neoermac-outbound-mobile-field="etd"',
            b'data-neoermac-outbound-mobile-field="delay"',
        )
        positions = [response.data.index(field) for field in mobile_fields]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(b">Delay<", response.data)
        self.assertIn(b"data-neoermac-outbound-mobile-header", response.data)
        self.assertNotIn(b"neoermac-outbound-mobile-fields", response.data)
        self.assertIn(b'<b>P</b>', response.data)
        self.assertIn(b'<b>M</b>', response.data)
        self.assertNotIn(b'<b>1</b>', response.data)
        self.assertIn(".neoermac-outbound-table-wrap {\n        display: none;", css)
        self.assertIn(".neoermac-outbound-mobile-row {\n        display: grid;", css)
        self.assertIn(
            "grid-template-columns: 1.25fr 0.88fr 0.55fr 0.45fr 0.9fr 1.65fr 0.72fr 0.45fr;",
            css,
        )
        self.assertIn("body.mobile-app-chrome .neoermac-shell.neoermac-outbound-shell {", css)
        self.assertIn("padding: 0;", css)
        self.assertIn("background: none;", css)
        self.assertIn("height: 40px;", css)
        self.assertNotIn(".neoermac-outbound-table-wrap {\n        overflow-x: auto;", css)

    def test_mobile_topbar_uses_complete_short_labels_without_ellipsis(self):
        self._login_approved_user(role="simulator")

        expected_labels = {
            "/neoermac/building-lineup": "LINEUP",
            "/neoermac/door-view": "DOORS",
            "/neoermac/view-outbound": "OUTBOUND",
            "/neoermac/upcoming-pulls": "PULLS",
            "/neoermac/tug-assignments": "TUGS",
        }

        for path, label in expected_labels.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f'<span class="mobile-topbar-page-name neo-page-title">{label}</span>'.encode(),
                    response.data,
                )

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
        set_user_password(user, "TestPassword123!")
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

    def _grant_node_role(self, username, node_code, role):
        user = User.query.filter_by(username=username).one()
        membership = GatewayMembership.query.filter_by(
            user_id=user.id,
            gateway_id=self.gateway.id,
        ).one()
        node = NeoNode.query.filter_by(code=node_code).one()
        existing = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
        ).first()
        if existing:
            existing.role = role
            existing.is_active = True
        else:
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=node.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()

    def _add_master_departure(
        self,
        flight_number,
        destination,
        pure_pull_time_local=None,
        mix_pull_time_local=None,
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
                mix_pull_time_local=mix_pull_time_local,
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
        window_minutes=None,
        departure_status=None,
        wave="1",
        planned_datetime_local=None,
        planned_datetime_utc=None,
        pure_pull_time_local=None,
        mix_pull_time_local=None,
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
            mix_pull_time_local=mix_pull_time_local or time(1, 55),
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
            db.session.add(
                SortDateParkingAssignment(
                    sort_date_operation_id=operation.id,
                    tail_number=tail.strip().upper(),
                    ramp_code=parking[0].upper() if parking else None,
                    position_code=parking.strip().upper(),
                    lane_number=1,
                )
            )
        return mission

    def _set_sort_window(self, sort_name, start_time, end_time):
        settings = ensure_sort_timeline_settings(self.gateway)
        sort_setting = next(
            row
            for row in settings.sort_settings
            if row.sort_name == sort_name
        )
        sort_setting.sort_window_start_local = start_time
        sort_setting.sort_window_end_local = end_time
        db.session.commit()

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
            "/neoermac/upcoming-pulls",
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

    def _element_html(self, html, class_name):
        class_name = class_name if isinstance(class_name, bytes) else class_name.encode()
        pattern = (
            rb'<(?P<tag>[a-zA-Z0-9]+)[^>]*class="[^"]*'
            + re.escape(class_name)
            + rb'[^"]*"[^>]*>.*?</(?P=tag)>'
        )
        match = re.search(pattern, html, re.S)
        self.assertIsNotNone(match)
        return match.group(0)

    def _door_flight_info_row_html(self, html):
        marker = b"data-door-flight-info-row"
        start = html.index(marker)
        row_start = html.rfind(b"<div", 0, start)
        row_end = html.index(b'<div class="neoermac-door-window-row"', start)
        return html[row_start:row_end]

    def _upcoming_side_html(self, response, side_name):
        side_label = f'aria-label="{side_name} upcoming pulls"'.encode()
        start = response.data.index(side_label)
        if side_name == "West":
            end = response.data.index(b'aria-label="East upcoming pulls"', start)
        else:
            end = response.data.index(b"</section>", start)
        return response.data[start:end]


if __name__ == "__main__":
    unittest.main()
