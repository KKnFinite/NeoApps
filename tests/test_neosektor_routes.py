import os
import re
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

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
    NeoSektorOperationalSetting,
    NeoSektorSortState,
    NeoSektorUldOnTheWayEvent,
    NeoSektorWaveState,
    PermissionRule,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password
from app.services.sort_timeline import ensure_sort_timeline_settings
from app.services.uld_requests import (
    active_on_the_way_events,
    active_request_views,
    send_uld_on_the_way,
    update_uld_request,
)


class _FakeNeoSektorWorksheet:
    def __init__(self):
        self.updates = []

    def update_acell(self, cell, value):
        self.updates.append((cell, value))


FAKE_SHEETS_ENV = {
    "GOOGLE_SHEETS_ID": "test-sheet-id",
    "GOOGLE_SHEETS_TAB": "Live Counts",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "{}",
}


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
        self.assertIn(b"node-desktop-nav-page", response.data)
        self.assertIn(b"data-node-desktop-side-nav", response.data)
        self.assertIn(b'data-node-desktop-shell="sektor"', response.data)
        desktop_sidebar = response.data.split(b"data-node-desktop-side-nav", 1)[1].split(b"</aside>", 1)[0]
        self.assertIn(b"neosektor-icon-256x256.png", desktop_sidebar)
        self.assertNotIn(b"neosektor-icon-128x128.png", desktop_sidebar)
        self.assertIn(b'<span class="neo-page-title motherbrain-desktop-top-title-text">DASHBOARD</span>', response.data)
        self.assertIn(b"neo-brand--sektor", response.data)
        self.assertIn(b"neo-brand__neo neo-word", response.data)
        self.assertIn(b"neo-brand__node node-word", response.data)
        self.assertIn(
            b'src="/static/images/icons/neosektor/inapp/neosektor-icon-128x128.png"',
            response.data,
        )
        self.assertIn(b"neosektor-header-title neo-brand-title", response.data)
        self.assertIn(b"neosektor-page-brand neo-brand-title", response.data)
        self.assertIn(b"neo-brand-title__node--sektor", response.data)
        self.assertNotIn(b'src="/static/images/neosektor_logo1.png"', response.data)
        self.assertNotIn(b"<h1>NeoSektor</h1>", response.data)
        self.assertIn(b"Live Counts", response.data)
        self.assertIn(b"data-node-desktop-dashboard", response.data)
        self.assertIn(b'data-node-dashboard="sektor"', response.data)
        self.assertIn(b'data-node-dashboard-tile="live-counts"', response.data)
        self.assertIn(b'data-node-dashboard-tile="ebm"', response.data)
        self.assertIn(b'data-node-dashboard-tile="wbm"', response.data)
        self.assertIn(b'data-node-dashboard-tile="discharge"', response.data)
        self.assertIn(b'data-node-dashboard-tile="driver-routing"', response.data)
        self.assertNotIn(b"data-live-counts", response.data)
        self.assertIn(b"Operations Menu", response.data)
        self.assertIn(b"neosektor-standalone-header mobile-shell-duplicate-title", response.data)
        self.assertNotIn(b"class=\"readonly-count\"", response.data)
        self.assertIn(b'href="/neosektor/live-counts"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="ebm"', response.data)
        self.assertIn(b"data-node-desktop-side-nav", response.data)
        self.assertIn(b'data-node-desktop-shell="sektor"', response.data)
        self.assertNotIn(b"motherbrain-header-nav", response.data)
        self.assertNotIn(b"data-neosektor-internal-menu", response.data)

    def test_neosektor_desktop_dashboard_uses_compact_tiles_without_sidebar_context_card(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"node-desktop-side-context", response.data)
        self.assertNotIn(b"RFD OPERATIONS", response.data)
        self.assertIn('grid-template-columns: repeat(3, minmax(0, 1fr));', css)
        self.assertIn('aspect-ratio: 1.18 / 1;', css)
        self.assertIn('height: calc(100vh - 140px);', css)
        self.assertIn('grid-template-rows: auto minmax(0, 1fr);', css)

    def test_node_desktop_shell_portal_return_precedes_character_switcher(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")
        html = response.data.decode()
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="node-desktop-portal-link neo-menu-text" href="/portal"', html)
        self.assertIn("Back to NeoPortal", html)
        self.assertLess(html.index("node-desktop-portal-link"), html.index("data-character-switcher"))
        self.assertIn(".node-desktop-portal-link {\n    display: none;", css)
        desktop_portal_link = css.rsplit(".node-desktop-portal-link {", 1)[1].split("}", 1)[0]
        self.assertIn("display: inline-flex;", desktop_portal_link)
        self.assertIn("font-size: 0.58rem;", desktop_portal_link)

    def test_desktop_ballmat_and_character_switcher_compaction_rules_are_present(self):
        css = Path("app/static/css/base.css").read_text()
        desktop_switcher_panel = css.rsplit(".character-switcher-panel {", 1)[1].split("}", 1)[0]
        desktop_switcher_link = css.rsplit(".character-switcher-link {", 1)[1].split("}", 1)[0]
        desktop_switcher_icon = css.rsplit(".character-switcher-icon {", 1)[1].split("}", 1)[0]

        self.assertIn('body.blueprint-neosektor.node-desktop-nav-page.neosektor-ballmat-operator-page', css)
        self.assertIn('font-size: clamp(1.32rem, 1.9vw, 1.7rem);', css)
        self.assertIn('width: min(238px, calc(100vw - 32px));', desktop_switcher_panel)
        self.assertIn('min-height: 30px;', desktop_switcher_link)
        self.assertIn('width: 20px;', desktop_switcher_icon)
        self.assertIn('height: 20px;', desktop_switcher_icon)

    def test_tunnel_conductor_marks_duplicate_title_for_mobile_shell(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"tunnel-header neosektor-standalone-header mobile-shell-duplicate-title",
            response.data,
        )
        self.assertIn(b"neosektor-page-brand neo-brand-title", response.data)
        self.assertIn(b'id="neosektor-tunnel-title">Tunnel Conductor</h1>', response.data)

    def test_tunnel_mobile_unload_metric_uses_the_outer_wave_card(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="tunnel-metric tunnel-unload-metric"', response.data)
        self.assertIn(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-unload-metric {\n"
            "        min-height: 26px;\n"
            "        padding-block: 2px;\n"
            "        border: 0;\n"
            "        border-radius: 0;\n"
            "        background: transparent;\n"
            "        box-shadow: none;\n"
            "    }",
            css,
        )

    def test_tunnel_mobile_frames_only_direct_edit_controls(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-tunnel-wave-input="first"', response.data)
        self.assertIn(b'data-wave-count="first"', response.data)
        self.assertIn(b'data-tunnel-setting="first_modifier"', response.data)
        self.assertIn(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-arrive-control.tunnel-editable,\n"
            "    body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-arrive-control .counter-control,\n"
            "    body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-ballmat-card,\n"
            "    body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-bay-card,\n"
            "    body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-operations-card .tunnel-setting-card {\n"
            "        border: 0;\n"
            "        border-radius: 0;\n"
            "        background: transparent;\n"
            "        box-shadow: none;\n"
            "    }",
            css,
        )

    def test_tunnel_mobile_header_keeps_the_full_page_title_visible(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b">Tunnel Conductor<", response.data)
        self.assertIn(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .mobile-topbar-page-name",
            css,
        )
        self.assertIn("font-size: clamp(0.76rem, 3.2vw, 0.86rem);", css)

    def test_mobile_topbar_uses_complete_short_labels_without_ellipsis(self):
        self._login_approved_user(role="simulator")

        expected_labels = {
            "/neosektor/tunnel-conductor": "TUNNEL",
            "/neosektor/live-counts": "COUNTS",
            "/neosektor/ebm": "EBM",
            "/neosektor/wbm": "WBM",
            "/neosektor/discharge": "DISCHARGE",
        }

        for path, label in expected_labels.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f'<span class="mobile-topbar-page-name neo-page-title">{label}</span>'.encode(),
                    response.data,
                )

        driver_routing = self.client.get("/neosektor/driver-routing")
        self.assertEqual(driver_routing.status_code, 200)
        self.assertIn(b'<h1 id="neosektor-driver-title">Driver Routing</h1>', driver_routing.data)

    def test_neosektor_page_headers_use_locked_title_branding(self):
        self._login_approved_user(role="simulator")

        for path in (
            "/neosektor",
            "/neosektor/live-counts",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/discharge",
            "/neosektor/driver-routing",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"neosektor-page-brand neo-brand-title", response.data)
                self.assertIn(b"neo-brand-title__neo", response.data)
                self.assertIn(b"neo-brand-title__node--sektor", response.data)

    def test_live_counts_uses_balanced_count_number_sizing(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor/live-counts")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"class=\"readonly-count\"", response.data)
        self.assertIn(b"data-live-counts", response.data)
        self.assertIn(b"neosektor-count-screen-compact", response.data)
        self.assertIn("font-size: clamp(1.3rem, 3.9vw, 1.9rem);", css)
        self.assertIn("font-size: clamp(1.9rem, 5.7vw, 2.95rem);", css)

    def test_neosektor_mobile_dashboard_tiles_render(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-neosektor-mobile-dashboard", response.data)
        self.assertIn(b"data-node-desktop-dashboard", response.data)
        self.assertIn(b'data-node-dashboard="sektor"', response.data)
        self.assertIn(b'data-node-dashboard-tile="live-counts"', response.data)
        self.assertIn(b'data-node-dashboard-tile="tunnel"', response.data)
        self.assertIn(b'data-node-dashboard-tile="ebm"', response.data)
        self.assertIn(b'data-node-dashboard-tile="wbm"', response.data)
        self.assertIn(b'data-node-dashboard-tile="discharge"', response.data)
        self.assertIn(b'data-node-dashboard-tile="driver-routing"', response.data)
        self.assertIn(b"neosektor-mobile-dashboard-grid", response.data)
        self.assertIn(b'data-neosektor-mobile-tile="live-counts"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="tunnel"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="ebm"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="wbm"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="discharge"', response.data)
        self.assertIn(b'data-neosektor-mobile-tile="driver-routing"', response.data)
        self.assertIn(b'href="/neosektor/live-counts"', response.data)
        self.assertIn(b'href="/neosektor/tunnel-conductor"', response.data)
        self.assertIn(b'href="/neosektor/ebm"', response.data)
        self.assertIn(b'href="/neosektor/wbm"', response.data)
        self.assertIn(b'href="/neosektor/discharge"', response.data)
        self.assertIn(b'href="/neosektor/driver-routing"', response.data)
        self.assertNotIn(b"System Status", response.data)
        self.assertNotIn(b"data-live-counts", response.data)
        self.assertNotIn(b"class=\"readonly-count\"", response.data)
        self.assertIn(".blueprint-neosektor .neosektor-mobile-dashboard {\n    display: none;", css)
        self.assertIn(
            "body.blueprint-neosektor.neosektor-fixed-header .neosektor-dashboard-shell .neosektor-mobile-dashboard {\n"
            "        display: grid;",
            css,
        )

    def test_neosektor_mobile_menu_is_compact_single_column_list(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor")
        html = response.data.decode()
        css = Path("app/static/css/base.css").read_text()
        menu_html = html.split('data-mobile-shell-menu-panel', 1)[1].split("</div>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("mobile-bottom-menu-panel", html)
        self.assertIn("Live Counts", menu_html)
        self.assertIn("Tunnel Conductor", menu_html)
        self.assertIn("East Ballmat", menu_html)
        self.assertIn("West Ballmat", menu_html)
        self.assertIn("Driver Routing", menu_html)
        self.assertIn("Discharge", menu_html)
        self.assertNotIn("OPEN", menu_html)
        self.assertNotIn("Inbound operations", menu_html)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)

    def test_neosektor_mobile_dashboard_tiles_do_not_render_subtitles(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-neosektor-mobile-dashboard", response.data)
        self.assertNotIn(b"<small>", response.data)
        self.assertNotIn(b"Live counts overview.", response.data)
        self.assertNotIn(b"Tunnel conductor controls.", response.data)

    def test_neosektor_mobile_subpages_back_to_dashboard(self):
        self._login_approved_user(role="simulator")

        for path in (
            "/neosektor/live-counts",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/discharge",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b'class="mobile-topbar-back"', response.data)
                self.assertIn(b'href="/neosektor"', response.data)
                self.assertIn(b'data-mobile-back-target="/neosektor"', response.data)
                self.assertIn(b'aria-label="Back to NeoSektor"', response.data)

    def test_neosektor_dashboard_mobile_back_points_to_gateway(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="mobile-topbar-back"', response.data)
        self.assertIn(b'href="/rfd"', response.data)
        self.assertIn(b'data-mobile-back-target="/rfd"', response.data)
        self.assertIn(b'aria-label="Back to Gateway"', response.data)

    def test_neosektor_internal_menu_filters_links_by_role(self):
        expectations = {
            "watcher": {
                b'href="/neosektor/live-counts"',
                b'href="/neosektor/driver-routing"',
            },
            "operator": {
                b'href="/neosektor/live-counts"',
                b'href="/neosektor/ebm"',
                b'href="/neosektor/wbm"',
                b'href="/neosektor/driver-routing"',
                b'href="/neosektor/discharge"',
            },
            "simulator": {
                b'href="/neosektor/live-counts"',
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
                self.assertIn(b"data-node-desktop-side-nav", response.data)
                self.assertIn(b'data-node-desktop-shell="sektor"', response.data)
                self.assertNotIn(b"motherbrain-header-nav", response.data)
                self.assertNotIn(b"data-neosektor-internal-menu", response.data)
                for label in expected_labels[role]:
                    self.assertIn(label, response.data)
                for link in expected_links:
                    self.assertIn(link, response.data)
                self.assertNotIn(b"NeoSektor Menu", response.data)
                for link in blocked[role]:
                    self.assertNotIn(link, response.data)

    def test_neosektor_internal_menu_appears_on_all_screens(self):
        self._login_approved_user(role="simulator")

        standalone_menu_paths = {
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
                    self.assertIn(b"data-node-desktop-side-nav", response.data)
                    self.assertIn(b'data-node-desktop-shell="sektor"', response.data)
                    self.assertNotIn(b"motherbrain-header-nav", response.data)
                for label in (
                    b"Live Counts",
                    b"Tunnel Conductor",
                    b"East Ballmat",
                    b"West Ballmat",
                    b"Driver Routing",
                    b"Discharge",
                ):
                    self.assertIn(label, response.data)
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
                self.assertIn(b"character-switcher-header", response.data)
                self.assertNotIn(b"character-switcher-standalone", response.data)
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
        self.assertNotIn(b"EDIT ENABLED", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_discharge_uld_labels_use_larger_label_hook(self):
        self._login_approved_user(role="operator")
        self._add_uld_request("D34", a2_count=2, a1_count=1, amp_count=3)
        db.session.commit()

        response = self.client.get("/neosektor/discharge")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="neosektor-discharge-uld-label">A2</span>', response.data)
        self.assertIn(b'class="neosektor-discharge-uld-label">A1</span>', response.data)
        self.assertIn(b'class="neosektor-discharge-uld-label">AMP</span>', response.data)
        self.assertIn(".neosektor-discharge-uld-label", css)
        self.assertIn("font-size: 0.62rem;", css)

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
        self.assertIn(
            b'<h1 id="neosektor-driver-title">Driver Routing</h1>',
            response.data,
        )
        self.assertIn(b"<title>Driver Routing | NeoSektor</title>", response.data)
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
        self.assertIn(b"Unload Settings", response.data)
        self.assertIn(b"data-tunnel-setting=\"first_modifier\"", response.data)
        self.assertIn(b"data-settings-url=\"/neosektor/tunnel-conductor/settings\"", response.data)
        self.assertIn(b'action="/logout"', response.data)
        self.assertNotIn(b'aria-label="BACK TO NeoSektor MENU"', response.data)
        self.assertNotIn(b"motherbrain-header-nav", response.data)
        self.assertIn(b"data-tunnel-conductor", response.data)
        self.assertIn(b"data-can-edit=\"true\"", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_tunnel_conductor_left_to_arrive_renders_typeable_numeric_controls(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-tunnel-wave-input=\"first\"", response.data)
        self.assertIn(b"data-tunnel-wave-input=\"second\"", response.data)
        self.assertIn(b"data-neosektor-edit-key=\"wave:first\"", response.data)
        self.assertIn(b"data-neosektor-edit-key=\"routing:west_offset\"", response.data)
        self.assertIn(b"neosektor-inline-edit-error", response.data)

    def test_tunnel_conductor_bay_status_uses_compact_layout_rules(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bay Status", response.data)
        self.assertIn(b"class=\"tunnel-bay-card\"", response.data)
        self.assertIn("grid-template-rows: repeat(2, auto);", css)
        self.assertIn("grid-template-columns: repeat(5, minmax(0, 1fr));", css)
        self.assertIn("align-content: start;", css)
        self.assertIn("min-height: 44px;", css)

    def test_tunnel_conductor_has_desktop_workspace_and_mobile_fallback_hooks(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-tunnel-desktop-workspace", response.data)
        self.assertIn(b"data-tunnel-desktop-left", response.data)
        self.assertIn(b"data-tunnel-bay-column", response.data)
        self.assertIn(b"data-tunnel-operations-card", response.data)
        self.assertNotIn(b"1ST WAVE East Ballmat Count", response.data)
        self.assertNotIn(b"2ND WAVE West Ballmat Count", response.data)
        self.assertEqual(response.data.count(b"East Ballmat Count"), 2)
        self.assertEqual(response.data.count(b"West Ballmat Count"), 2)
        self.assertIn(b"East Ballmat Open Bays", response.data)
        self.assertIn(b"West Ballmat Open Bays", response.data)
        self.assertIn(b"class=\"tunnel-mobile-section-title\">Ballmat Counts", response.data)
        self.assertIn(b"class=\"tunnel-mobile-label\">West Offset", response.data)
        self.assertIn(b"data-tunnel-wave-input=\"first\"", response.data)
        self.assertIn(b"data-tunnel-wave-input=\"second\"", response.data)
        self.assertIn(b"data-open-bays", response.data)
        self.assertIn(
            "grid-template-columns: minmax(0, 1fr) minmax(210px, 0.3fr);",
            css,
        )
        self.assertIn(
            "grid-template-areas:\n"
            "            \"wave-first east-first west-first\"\n"
            "            \"wave-second east-second west-second\"\n"
            "            \"operations east-open west-open\";",
            css,
        )
        self.assertIn("grid-column: 2;", css)
        self.assertIn("grid-auto-rows: minmax(0, 1fr);", css)
        self.assertIn(".blueprint-neosektor .tunnel-operations-card {\n    display: contents;", css)
        self.assertIn("@media (min-width: 901px)", css)
        self.assertIn("@media (max-width: 900px)", css)

    def test_tunnel_conductor_desktop_count_boxes_and_square_buttons_use_consistent_rules(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()
        desktop_start = css.index("@media (min-width: 901px) {", css.index("/* Tunnel Conductor keeps"))
        desktop_end = css.index("@media (max-width: 900px)", desktop_start)
        desktop_css = css[desktop_start:desktop_end]

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="tunnel-wave-workspace"', response.data)
        self.assertIn(b'class="tunnel-desktop-wave-heading"', response.data)
        self.assertIn("color: var(--neo-bright-silver);", desktop_css)
        self.assertIn("font-size: 0.94rem;", desktop_css)
        self.assertIn("font-size: 0.8rem;", desktop_css)
        self.assertIn("font-size: 0.78rem;", desktop_css)
        self.assertIn("grid-template-columns: auto auto;", desktop_css)
        self.assertIn("justify-content: center;", desktop_css)
        self.assertIn("column-gap: 12px;", desktop_css)
        self.assertIn("font-size: clamp(0.9rem, 1.15vw, 1.08rem);", desktop_css)
        self.assertIn("font-size: clamp(0.98rem, 1.3vw, 1.24rem);", desktop_css)
        self.assertIn("width: min(100%, 240px);", desktop_css)
        self.assertIn("grid-template-columns: 40px minmax(112px, 1fr) 40px;", desktop_css)
        self.assertIn("width: min(100%, 184px);", desktop_css)
        self.assertIn("grid-template-columns: 40px minmax(64px, 80px) 40px;", desktop_css)
        self.assertIn("inline-size: 40px;", desktop_css)
        self.assertIn("block-size: 40px;", desktop_css)
        self.assertIn("justify-content: center;", desktop_css)
        self.assertIn("gap: 12px;", desktop_css)
        self.assertIn("background: transparent;", desktop_css)
        self.assertIn("background: rgba(5, 7, 11, 0.88);", desktop_css)
        self.assertIn("min-height: 96px;", desktop_css)
        self.assertIn("min-width: 112px;", desktop_css)
        self.assertIn("grid-row: 1 / -1;", desktop_css)
        self.assertIn("font-size: clamp(5rem, 5.5vw, 6.2rem);", desktop_css)
        self.assertIn("font-size: clamp(1.05rem, 1.7vw, 1.5rem);", desktop_css)
        self.assertIn(
            ".tunnel-arrive-control input.counter-number[data-metric=\"left_to_arrive\"]",
            css,
        )
        self.assertIn("color: var(--neo-bright-silver);", css)

    def test_neosektor_mobile_console_css_locks_viewport_and_compacts_operator_views(self):
        self._login_approved_user(role="simulator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts")
        tunnel = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(live_counts.status_code, 200)
        self.assertEqual(tunnel.status_code, 200)
        self.assertIn(b"data-neosektor-mobile-dashboard", dashboard.data)
        self.assertIn(b"data-live-counts", live_counts.data)
        self.assertIn(b"data-tunnel-conductor", tunnel.data)
        self.assertIn("html:has(body.blueprint-neosektor)", css)
        self.assertIn("max-width: 100vw;", css)
        self.assertIn("overscroll-behavior: none;", css)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", css)
        self.assertIn("grid-template-rows: repeat(3, minmax(0, 1fr));", css)
        self.assertIn("min-height: clamp(40px, 6.7svh, 56px);", css)
        self.assertIn("min-height: clamp(56px, 8.7svh, 68px);", css)
        self.assertIn("minmax(0, 1.22fr)", css)
        self.assertIn(".tunnel-unload-metric {\n        min-height: 26px;", css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", css)
        self.assertIn(".tunnel-bay-card {\n        min-height: 34px;", css)
        self.assertIn("grid-template-rows: 14px repeat(3, minmax(0, 1fr));", css)

    def test_neosektor_mobile_shell_paints_the_safe_area_dark(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in (
                "/neosektor",
                "/neosektor/live-counts",
                "/neosektor/ebm",
                "/neosektor/wbm",
                "/neosektor/tunnel-conductor",
                "/neosektor/driver-routing",
                "/neosektor/discharge",
            )
        ]
        css = Path("app/static/css/base.css").read_text()

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"blueprint-neosektor", response.data)

        self.assertIn(
            "html:has(body.blueprint-neosektor),\n"
            "    body.blueprint-neosektor {\n"
            "        height: 100svh;",
            css,
        )
        self.assertIn("background-color: #050506;", css)
        self.assertIn("overscroll-behavior: none;", css)

    def test_mobile_ebm_and_wbm_use_normal_flow_visible_bay_tracks(self):
        self._login_approved_user(role="simulator")

        ebm = self.client.get("/neosektor/ebm")
        wbm = self.client.get("/neosektor/wbm")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(ebm.status_code, 200)
        self.assertEqual(wbm.status_code, 200)
        for response in (ebm, wbm):
            self.assertIn(b"East Bays", response.data)
            self.assertIn(b"West Bays", response.data)
            for bay_name in (b"Bay 1", b"Bay 2", b"Bay 3", b"Bay 4", b"Bay 5"):
                self.assertIn(bay_name, response.data)

        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", mobile_layout)
        self.assertIn("grid-auto-flow: row;", mobile_layout)
        self.assertIn(
            "grid-template-areas:\n"
            "            \"waves\"\n"
            "            \"ballmats\";",
            mobile_layout,
        )
        self.assertIn(
            "grid-template-areas:\n"
            "            \"refresh\"\n"
            "            \"waves\"\n"
            "            \"ballmats\";",
            mobile_layout,
        )
        self.assertIn(
            "grid-template-rows: minmax(66px, 0.15fr) minmax(0, 0.85fr);",
            mobile_layout,
        )
        self.assertIn(
            "grid-template-rows: minmax(32px, auto) minmax(66px, 0.15fr) minmax(0, 0.85fr);",
            mobile_layout,
        )
        self.assertIn(".counts-wrap.has-refresh-notice", mobile_layout)
        self.assertIn(
            "grid-template-rows: 20px repeat(3, minmax(52px, 1fr)) minmax(174px, 1.9fr);",
            mobile_layout,
        )
        self.assertIn("grid-template-rows: 18px repeat(3, minmax(48px, 1fr));", mobile_layout)
        self.assertIn("grid-template-rows: 12px minmax(30px, 1fr);", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

    def test_mobile_ebm_and_wbm_reclaim_safe_space_for_equal_card_tracks(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data.count(b'class="counter-card"'), 4)
            self.assertEqual(
                response.data.count(b'class="counter-card neosektor-open-bay-control"'),
                2,
            )
            self.assertEqual(response.data.count(b'class="bay-card"'), 5)

        self.assertIn(
            "body.blueprint-neosektor.neosektor-ballmat-operator-page.mobile-app-chrome"
            ".has-mobile-bottom-nav .content {\n"
            "        padding-bottom: calc(76px + env(safe-area-inset-bottom));",
            css,
        )
        self.assertIn(
            "grid-template-rows: 20px repeat(3, minmax(52px, 1fr)) minmax(174px, 1.9fr);",
            mobile_layout,
        )
        self.assertIn("grid-template-rows: 18px repeat(3, minmax(48px, 1fr));", mobile_layout)
        self.assertIn("height: 100%;", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

    def test_mobile_ebm_and_wbm_center_status_values_in_equal_taller_bay_tracks(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'data-wave="first" data-metric="left_to_arrive"', response.data)
            self.assertIn(b'data-wave="second" data-metric="left_to_arrive"', response.data)
            self.assertIn(b'data-wave="first" data-metric="left_to_unload"', response.data)
            self.assertIn(b'data-wave="second" data-metric="left_to_unload"', response.data)
            for bay_name in (b"Bay 1", b"Bay 2", b"Bay 3", b"Bay 4", b"Bay 5"):
                self.assertIn(bay_name, response.data)

        self.assertIn("grid-template-rows: auto auto;", mobile_layout)
        self.assertIn("place-content: center;", mobile_layout)
        self.assertIn("justify-items: center;", mobile_layout)
        self.assertIn("grid-template-rows: 18px minmax(0, 1fr);", mobile_layout)
        self.assertIn("grid-template-rows: 20px repeat(3, minmax(52px, 1fr)) minmax(174px, 1.9fr);", mobile_layout)
        self.assertIn("grid-template-rows: 18px repeat(3, minmax(48px, 1fr));", mobile_layout)
        self.assertIn("padding: 6px 7px;", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

    def test_mobile_ebm_and_wbm_enlarge_centered_section_headings_and_bay_sliders(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            for heading in (
                b"1ST WAVE",
                b"2ND WAVE",
                b"East Ballmat",
                b"West Ballmat",
                b"East Bays",
                b"West Bays",
            ):
                self.assertIn(heading, response.data)

        self.assertGreaterEqual(mobile_layout.count("place-items: center;"), 3)
        self.assertIn("font-size: 0.74rem;", mobile_layout)
        self.assertIn("font-size: 0.72rem;", mobile_layout)
        self.assertIn("grid-template-rows: minmax(0, 1fr) 18px;", mobile_layout)
        self.assertIn("min-height: 16px;", mobile_layout)
        self.assertIn("height: 16px;", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

    def test_mobile_ebm_and_wbm_wave_labels_use_reserved_normal_flow_rows(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.data.count(b'class="wave-metric-label">Left to Arrive'),
                2,
            )
            self.assertEqual(
                response.data.count(b'class="wave-metric-label">Left to Unload'),
                2,
            )

        self.assertIn("grid-template-rows: auto auto;", mobile_layout)
        self.assertIn("gap: 4px;", mobile_layout)
        self.assertIn("padding: 5px 3px;", mobile_layout)
        self.assertIn("font-size: 0.54rem;", mobile_layout)
        self.assertIn("font-weight: 800;", mobile_layout)
        self.assertIn("white-space: nowrap;", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

    def test_mobile_ebm_and_wbm_frame_only_edit_controls(self):
        self._login_approved_user(role="simulator")

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        css = Path("app/static/css/base.css").read_text()

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'class="counter-number neosektor-numeric-input"', response.data)
            self.assertIn(b'data-bay-status=', response.data)
            self.assertIn(b'class="readonly-count"', response.data)

        self.assertIn(
            "body.blueprint-neosektor.neosektor-ballmat-operator-page .wave-card .wave-metrics div,\n"
            "    body.blueprint-neosektor.neosektor-ballmat-operator-page .counter-card,\n"
            "    body.blueprint-neosektor.neosektor-ballmat-operator-page .counter-control,\n"
            "    body.blueprint-neosektor.neosektor-ballmat-operator-page .bay-card,\n"
            "    body.blueprint-neosektor.neosektor-ballmat-operator-page .readonly-count {\n"
            "        border: 0;\n"
            "        border-radius: 0;\n"
            "        background: transparent;\n"
            "        box-shadow: none;\n"
            "    }",
            css,
        )

    def test_mobile_ebm_and_wbm_reserve_an_operation_notice_row(self):
        self._login_approved_user(role="simulator")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 29, 10, 0)
        self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        responses = [
            self.client.get(path)
            for path in ("/neosektor/ebm", "/neosektor/wbm")
        ]
        template = Path("app/templates/neonodes/neosektor/ballmat.html").read_text()
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index("/* NeoSektor EBM/WBM mobile normal-flow layout. */")
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-wrap",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'class="counts-wrap has-refresh-notice"', response.data)
            self.assertIn(b"Auto-refresh paused", response.data)
            self.assertIn(b"RFD NIGHT 6/29/26", response.data)

        self.assertIn(
            'countsWrap?.classList.toggle("has-refresh-notice", !isActive);',
            template,
        )
        self.assertIn("grid-area: refresh;", mobile_layout)
        self.assertIn(
            "minmax(32px, auto) minmax(66px, 0.15fr) minmax(0, 0.85fr);",
            mobile_layout,
        )

    def test_tunnel_mobile_workspace_assigns_each_panel_a_normal_flow_row(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data.count(b'class="tunnel-readonly-card tunnel-ballmat-card'),
            6,
        )
        self.assertEqual(
            response.data.count(b'<span class="tunnel-mobile-label">1ST WAVE</span>'),
            2,
        )
        self.assertEqual(
            response.data.count(b'<span class="tunnel-mobile-label">2ND WAVE</span>'),
            2,
        )
        self.assertEqual(
            response.data.count(b'<span class="tunnel-mobile-label">Open Bays</span>'),
            2,
        )
        layout_start = css.index(
            "The desktop workspace is flattened for mobile.  Assign every resulting"
        )
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-operations-card",
            layout_start,
        )
        mobile_layout = css[layout_start:layout_end]

        self.assertIn(
            '"notice"\n            "waves"\n            "counts"\n            "bays"\n            "operations";',
            mobile_layout,
        )
        self.assertIn("grid-area: notice;", mobile_layout)
        self.assertIn("grid-area: waves;", mobile_layout)
        self.assertIn("grid-area: counts;", mobile_layout)
        self.assertIn("grid-area: bays;", mobile_layout)
        self.assertIn("padding-bottom: calc(76px + env(safe-area-inset-bottom));", mobile_layout)
        self.assertNotIn("position: absolute", mobile_layout)
        self.assertNotIn("transform:", mobile_layout)
        self.assertNotIn("margin-top: -", mobile_layout)

        count_layout_start = css.index(
            "/* Equal normal-flow tracks keep the six editable count cards legible. */"
        )
        count_layout_end = css.index(
            "body.blueprint-neosektor.neosektor-tunnel-operator-page .tunnel-operations-card",
            count_layout_start,
        )
        count_layout = css[count_layout_start:count_layout_end]
        self.assertIn("grid-template-rows: 16px minmax(0, 1fr);", count_layout)
        self.assertIn("grid-auto-flow: row;", count_layout)
        self.assertIn("grid-template-rows: 14px repeat(3, minmax(0, 1fr));", count_layout)
        self.assertIn("grid-template-rows: 11px minmax(28px, 1fr);", count_layout)
        self.assertIn("place-items: center;", count_layout)

    def test_tunnel_mobile_option_row_uses_shared_label_and_control_tracks(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor")
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="tunnel-mobile-label">West Offset', response.data)
        self.assertIn(b'data-tunnel-setting="first_modifier"', response.data)
        self.assertIn(b'data-tunnel-setting="second_modifier"', response.data)
        self.assertIn(b'data-tunnel-setting="down_timer_minutes"', response.data)
        self.assertIn(b"<span>DOWN TIMER <em>MIN</em></span>", response.data)
        self.assertIn('grid-template-areas: "offset modifier-one modifier-two timer";', css)
        self.assertIn("grid-template-rows: 11px minmax(30px, 1fr);", css)
        self.assertIn("grid-area: offset;", css)
        self.assertIn("grid-area: modifier-one;", css)
        self.assertIn("grid-area: modifier-two;", css)
        self.assertIn("grid-area: timer;", css)
        self.assertIn("grid-row: 1;\n        align-self: start;", css)
        self.assertIn("grid-row: 2;\n        align-self: center;", css)
        self.assertIn("vertical-align: baseline;", css)

    def test_neosektor_numeric_inputs_render_no_spinner_class_and_css(self):
        self._login_approved_user(role="simulator")

        tunnel = self.client.get("/neosektor/tunnel-conductor")
        self.client.get("/neosektor/ebm")
        ballmat = self.client.get("/neosektor/ebm")
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(b"neosektor-numeric-input", tunnel.data)
        self.assertIn(b"neosektor-numeric-input", ballmat.data)
        self.assertIn(".neosektor-numeric-input::-webkit-inner-spin-button", css)
        self.assertIn("-moz-appearance: textfield;", css)

    def test_neosektor_edit_scripts_guard_pending_values_from_stale_polling(self):
        tunnel_template = Path(
            "app/templates/neonodes/neosektor/tunnel_conductor.html"
        ).read_text()
        ballmat_template = Path(
            "app/templates/neonodes/neosektor/ballmat.html"
        ).read_text()

        for template in (tunnel_template, ballmat_template):
            self.assertIn("const pendingEdits = new Map();", template)
            self.assertIn("const setEditableValue = (input, value)", template)
            self.assertIn("restoreAfterFailure", template)
            self.assertIn("Restored the latest server value", template)

        self.assertIn("queueOffsetSave", tunnel_template)
        self.assertIn("queueWaveSave", tunnel_template)
        self.assertIn("hasPending(control)", ballmat_template)

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

    def test_tunnel_conductor_wave_value_sets_left_to_arrive_from_typed_input(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/wave",
            json={"wave": "first", "value": 42},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["state"]["waves"][0]["planned"], 42)
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().planned_count,
            42,
        )

    def test_neogateway_update_commits_database_and_mirrors_standalone_sheet_cells(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)
        worksheet = _FakeNeoSektorWorksheet()

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 7, "status": "Full"}},
                    "open_bays": 2,
                    "bay_statuses": {"Bay 1": "Full"},
                },
            )

        self.assertEqual(response.status_code, 200)
        first_wave = NeoSektorBallmatWaveCount.query.filter_by(
            side="EAST",
            wave_name="1ST WAVE",
        ).one()
        self.assertEqual(first_wave.count, 7)
        self.assertEqual(worksheet.updates, [("B2", 7), ("B4", 2), ("B6", "Full")])

    def test_neogateway_tunnel_update_mirrors_the_standalone_left_to_arrive_cell(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)
        worksheet = _FakeNeoSektorWorksheet()

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/neosektor/tunnel-conductor/wave",
                json={"wave": "first", "value": 12},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(worksheet.updates, [("D2", 12)])
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().planned_count,
            12,
        )

    def test_neogateway_settings_mirror_existing_standalone_modifier_cells(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)
        worksheet = _FakeNeoSektorWorksheet()

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            response = self.client.post(
                "/neosektor/tunnel-conductor/settings",
                json={
                    "first_modifier": 52,
                    "second_modifier": 31,
                    "down_timer_minutes": 20,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(worksheet.updates, [("B13", 52), ("B14", 31)])

    def test_neogateway_sheet_bridge_skips_page_loads_and_polling(self):
        self._login_approved_user(role="simulator")

        with patch(
            "app.neonodes.neosektor.routes.mirror_neosektor_sheet_update"
        ) as mirror_update:
            for path in (
                "/neosektor/live-counts",
                "/neosektor/live-counts/state",
                "/neosektor/tunnel-conductor",
                "/neosektor/tunnel-conductor/state",
                "/neosektor/ebm",
                "/neosektor/ballmat/state",
                "/neosektor/driver-routing/state",
            ):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)

        mirror_update.assert_not_called()

    def test_neosektor_sheets_compatibility_defaults_off_even_with_credentials(self):
        from app.services.neosektor_sheets_compat import (
            sheets_compatibility_enabled,
            sheets_compatibility_status,
        )

        with patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False):
            status = sheets_compatibility_status(self.gateway)

        self.assertFalse(sheets_compatibility_enabled(self.gateway))
        self.assertFalse(status["enabled"])
        self.assertTrue(status["credentials_configured"])
        self.assertEqual(NeoSektorOperationalSetting.query.count(), 0)

    def test_neosektor_sheets_env_flag_does_not_auto_enable_compatibility(self):
        from app.services.neosektor_sheets_compat import sheets_compatibility_enabled

        env = {
            **FAKE_SHEETS_ENV,
            "NEOSEKTOR_SHEETS_COMPAT_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(sheets_compatibility_enabled(self.gateway))

    def test_neosektor_sheets_compat_off_prevents_google_client_but_allows_db_update(self):
        self._login_approved_user(role="simulator")

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch("app.services.neosektor_sheets_compat._get_worksheet") as worksheet,
        ):
            response = self.client.post(
                "/neosektor/ballmat/update?side=east",
                json={
                    "side": "east",
                    "waves": {"first": {"count": 5, "status": "Light"}},
                    "open_bays": 3,
                    "bay_statuses": {"Bay 1": "Light"},
                },
            )

        self.assertEqual(response.status_code, 200)
        worksheet.assert_not_called()
        first_wave = NeoSektorBallmatWaveCount.query.filter_by(
            side="EAST",
            wave_name="1ST WAVE",
        ).one()
        self.assertEqual(first_wave.count, 5)

    def test_neosektor_master_can_enable_and_disable_sheets_compatibility(self):
        self._login_approved_user(role="master")

        page = self.client.get("/neosektor/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Google Sheets Compatibility", page.data)
        self.assertIn(b"OFF", page.data)

        enabled = self.client.post(
            "/neosektor/settings",
            data={"action": "enable"},
            follow_redirects=True,
        )
        self.assertEqual(enabled.status_code, 200)
        self.assertIn(b"ON", enabled.data)
        self.assertTrue(
            NeoSektorOperationalSetting.query.filter_by(
                gateway_id=self.gateway.id
            ).one().google_sheets_compat_enabled
        )

        disabled = self.client.post(
            "/neosektor/settings",
            data={"action": "disable"},
            follow_redirects=True,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertIn(b"OFF", disabled.data)
        self.assertFalse(
            NeoSektorOperationalSetting.query.filter_by(
                gateway_id=self.gateway.id
            ).one().google_sheets_compat_enabled
        )

    def test_neosektor_settings_desktop_page_label_is_settings(self):
        self._login_approved_user(role="master")

        response = self.client.get("/neosektor/settings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'<span class="neo-page-title motherbrain-desktop-top-title-text">SETTINGS</span>',
            response.data,
        )
        self.assertNotIn(
            b'<span class="neo-page-title motherbrain-desktop-top-title-text">DASHBOARD</span>',
            response.data,
        )

    def test_neosektor_unauthorized_user_cannot_toggle_sheets_compatibility(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/settings",
            data={"action": "enable"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)
        settings = NeoSektorOperationalSetting.query.filter_by(
            gateway_id=self.gateway.id
        ).first()
        self.assertFalse(settings and settings.google_sheets_compat_enabled)

    def test_neosektor_sheets_compatibility_csrf_protected_post_accepts_token(self):
        self._login_approved_user(role="master")
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get("/neosektor/settings")
        token = self._csrf_token(page)

        without_token = self.client.post(
            "/neosektor/settings",
            data={"action": "enable"},
            follow_redirects=False,
        )
        with_token = self.client.post(
            "/neosektor/settings",
            data={"action": "enable", "csrf_token": token},
            follow_redirects=False,
        )

        self.assertEqual(without_token.status_code, 400)
        self.assertEqual(with_token.status_code, 302)
        self.assertTrue(
            NeoSektorOperationalSetting.query.filter_by(
                gateway_id=self.gateway.id
            ).one().google_sheets_compat_enabled
        )

    def test_neosektor_sheets_missing_credentials_when_on_do_not_rollback_database_update(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("app.services.neosektor_sheets_compat._get_worksheet") as worksheet,
            self.assertLogs("app.services.neosektor_sheets_compat", level="WARNING") as logs,
        ):
            response = self.client.post(
                "/neosektor/tunnel-conductor/offset",
                json={"west_offset": 3},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["routing"]["west_offset"], 3)
        worksheet.assert_not_called()
        self.assertTrue(any("configuration" in line for line in logs.output))

    def test_neosektor_disabling_sheets_compatibility_stops_future_writes(self):
        self._login_approved_user(role="simulator")
        worksheet = _FakeNeoSektorWorksheet()

        self._set_sheets_compat_enabled(True)
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            self.assertEqual(
                self.client.post(
                    "/neosektor/tunnel-conductor/wave",
                    json={"wave": "first", "value": 21},
                ).status_code,
                200,
            )

        self.assertEqual(worksheet.updates, [("D2", 21)])
        worksheet.updates.clear()
        self._set_sheets_compat_enabled(False)
        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch("app.services.neosektor_sheets_compat._get_worksheet") as get_worksheet,
        ):
            self.assertEqual(
                self.client.post(
                    "/neosektor/tunnel-conductor/wave",
                    json={"wave": "first", "value": 22},
                ).status_code,
                200,
            )

        get_worksheet.assert_not_called()
        self.assertEqual(
            NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one().planned_count,
            22,
        )

    def test_neogateway_sheet_bridge_does_not_repeat_unchanged_updates(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)
        worksheet = _FakeNeoSektorWorksheet()
        payload = {
            "side": "east",
            "waves": {"first": {"count": 8, "status": "Light"}},
            "open_bays": 1,
            "bay_statuses": {"Bay 1": "Light"},
        }

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                return_value=worksheet,
            ),
        ):
            self.assertEqual(
                self.client.post("/neosektor/ballmat/update?side=east", json=payload).status_code,
                200,
            )
            worksheet.updates.clear()
            self.assertEqual(
                self.client.post("/neosektor/ballmat/update?side=east", json=payload).status_code,
                200,
            )

        self.assertEqual(worksheet.updates, [])

    def test_neogateway_sheet_failure_does_not_rollback_database_update(self):
        self._login_approved_user(role="simulator")
        self._set_sheets_compat_enabled(True)

        with (
            patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False),
            patch(
                "app.services.neosektor_sheets_compat._get_worksheet",
                side_effect=RuntimeError("sheet unavailable"),
            ),
            self.assertLogs(
                "app.services.neosektor_sheets_compat",
                level="WARNING",
            ) as logs,
        ):
            response = self.client.post(
                "/neosektor/tunnel-conductor/offset",
                json={"west_offset": 4},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["routing"]["west_offset"], 4)
        self.assertTrue(any("exception_class" in line for line in logs.output))

    def test_neogateway_sheet_bridge_uses_existing_standalone_cell_contract(self):
        from app.services.neosektor_sheets_compat import SHEET_CELL_ORDER

        self.assertEqual(
            SHEET_CELL_ORDER,
            (
                "B2", "C2", "D2", "B3", "C3", "D3", "B4", "C4",
                "B6", "B8", "B10", "C6", "C8", "B13", "B14", "B15",
            ),
        )

    def test_neosektor_operational_settings_default_when_missing(self):
        self._login_approved_user(role="simulator")

        response = self.client.get("/neosektor/tunnel-conductor/state")

        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["state"]["operational_settings"]
        self.assertEqual(settings["first_modifier"], 45)
        self.assertEqual(settings["second_modifier"], 37)
        self.assertEqual(settings["down_timer_minutes"], 15)
        self.assertEqual(NeoSektorOperationalSetting.query.count(), 1)

    def test_neosektor_operational_settings_save_persists_values(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": 52,
                "second_modifier": 31,
                "down_timer_minutes": 22,
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["state"]["operational_settings"]
        self.assertEqual(settings["first_modifier"], 52)
        self.assertEqual(settings["second_modifier"], 31)
        self.assertEqual(settings["down_timer_minutes"], 22)
        saved = NeoSektorOperationalSetting.query.one()
        self.assertEqual(saved.first_wave_unload_modifier, 52)
        self.assertEqual(saved.second_wave_unload_modifier, 31)
        self.assertEqual(saved.all_up_to_down_minutes, 22)

    def test_neosektor_operational_settings_clamp_invalid_values(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": -10,
                "second_modifier": 5000,
                "down_timer_minutes": 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        settings = response.get_json()["state"]["operational_settings"]
        self.assertEqual(settings["first_modifier"], 0)
        self.assertEqual(settings["second_modifier"], 999)
        self.assertEqual(settings["down_timer_minutes"], 1)

    def test_view_only_tunnel_conductor_user_cannot_update_settings(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.tunnel_conductor.edit"
        ).one()
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": 12,
                "second_modifier": 13,
                "down_timer_minutes": 14,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(NeoSektorOperationalSetting.query.count(), 0)

    def test_left_to_unload_uses_custom_first_modifier(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": 20,
                "second_modifier": 37,
                "down_timer_minutes": 15,
            },
        )
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
        self.assertEqual(response.get_json()["state"]["waves"][0]["left"], 34)

    def test_second_wave_uses_custom_second_modifier_after_first_down(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": 45,
                "second_modifier": 11,
                "down_timer_minutes": 15,
            },
        )
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
        self.assertEqual(response.get_json()["state"]["waves"][1]["left"], 28)

    def test_all_up_to_down_transition_uses_custom_timer_minutes(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/settings",
            json={
                "first_modifier": 45,
                "second_modifier": 37,
                "down_timer_minutes": 20,
            },
        )
        self.client.get("/neosektor/live-counts/state")
        first_wave = NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one()
        first_wave.all_up_started_at = datetime.utcnow() - timedelta(minutes=19)
        db.session.commit()

        early_response = self.client.get("/neosektor/live-counts/state")
        self.assertEqual(early_response.get_json()["state"]["waves"][0]["left"], "ALL UP")

        first_wave.all_up_started_at = datetime.utcnow() - timedelta(minutes=21)
        db.session.commit()
        late_response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(late_response.get_json()["state"]["waves"][0]["left"], "DOWN")

    def test_first_wave_does_not_all_up_while_ballmat_back_row_has_count(self):
        self._login_approved_user(role="simulator")
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {"first": {"count": 0}, "second": {"count": 0}},
                "open_bays": 0,
                "bay_statuses": {},
            },
        )
        response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {"first": {"count": 3}, "second": {"count": 0}},
                "open_bays": 3,
                "bay_statuses": {},
            },
        )

        first_wave = response.get_json()["state"]["waves"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(first_wave["left_to_arrive"], "ALL IN")
        self.assertEqual(first_wave["left"], 45)
        self.assertNotEqual(first_wave["left"], "ALL UP")

    def test_second_wave_does_not_all_up_while_ballmat_back_row_has_count(self):
        self._login_approved_user(role="simulator")
        self.client.get("/neosektor/live-counts/state")
        first_wave = NeoSektorWaveState.query.filter_by(wave_name="1ST WAVE").one()
        first_wave.all_up_started_at = datetime.utcnow() - timedelta(minutes=16)
        db.session.commit()
        self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "east",
                "waves": {"first": {"count": 0}, "second": {"count": 0}},
                "open_bays": 0,
                "bay_statuses": {},
            },
        )
        response = self.client.post(
            "/neosektor/tunnel-conductor/ballmat",
            json={
                "side": "west",
                "waves": {"first": {"count": 0}, "second": {"count": 3}},
                "open_bays": 3,
                "bay_statuses": {},
            },
        )

        second_wave = response.get_json()["state"]["waves"][1]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"]["waves"][0]["left"], "DOWN")
        self.assertEqual(second_wave["left_to_arrive"], "ALL IN")
        self.assertEqual(second_wave["left"], 37)
        self.assertNotEqual(second_wave["left"], "ALL UP")

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
        live_counts = self.client.get("/neosektor/live-counts")
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
        self.assertIn(b'action="/logout"', ebm.data)
        self.assertNotIn(b'aria-label="BACK TO NeoSektor MENU"', ebm.data)
        self.assertNotIn(b"motherbrain-header-nav", ebm.data)
        self.assertIn(b"neosektor-ballmat-operator-page", ebm.data)
        self.assertIn(b"neosektor-count-screen-compact", ebm.data)
        self.assertIn(b"data-open-bays", ebm.data)
        self.assertEqual(wbm.status_code, 200)
        self.assertIn(b"WBM | EDIT ENABLED", wbm.data)
        self.assertIn(b"data-selected-side=\"west\"", wbm.data)
        self.assertIn(b"neosektor-count-screen-compact", wbm.data)
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

        response = self.client.get("/neosektor/live-counts")

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
        self.assertIn(b'action="/logout"', response.data)
        self.assertIn(b'data-state-url="/neosektor/live-counts/state"', response.data)
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
        self.assertIn("refresh", payload["state"])

    def test_neosektor_refresh_pauses_outside_operation_window(self):
        self._login_approved_user(role="watcher")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 29, 10, 0)
        operation = self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        response = self.client.get("/neosektor/live-counts")
        state_response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-refresh-active="false"', response.data)
        self.assertIn(b"neosektor-refresh-paused", response.data)
        self.assertIn(b"Auto-refresh paused", response.data)
        self.assertIn(b"RFD NIGHT 6/29/26", response.data)
        self.assertIn(b"22:00-04:00", response.data)
        self.assertIn(b"window.clearInterval(refreshTimer)", response.data)
        self.assertIn(b"setRefreshStatus(initialRefreshStatus)", response.data)

        payload = state_response.get_json()
        self.assertFalse(payload["state"]["refresh"]["auto_refresh_enabled"])
        self.assertEqual(payload["state"]["refresh"]["operation_id"], operation.id)
        self.assertIsNone(payload["state"]["refresh"]["next_check_seconds"])
        self.assertNotIn(b"setTimeout(refreshState", response.data)
        self.assertNotIn(b"resumeTimer", response.data)

    def test_neosektor_live_views_use_the_shared_operation_refresh_banner(self):
        self._login_approved_user(role="simulator")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 29, 10, 0)
        self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        for path in (
            "/neosektor/live-counts",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/driver-routing",
            "/neosektor/discharge",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"operation-refresh-banner", response.data)
                self.assertIn(b"neosektor-refresh-paused", response.data)
                self.assertIn(b"data-operation-refresh-banner", response.data)
                self.assertIn(b"data-neosektor-refresh-paused", response.data)

        stylesheet = Path("app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn(".operation-refresh-banner {", stylesheet)
        self.assertIn("border: 1px solid rgba(var(--node-rgb), 0.42);", stylesheet)
        self.assertNotIn(".blueprint-neosektor .neosektor-refresh-paused {", stylesheet)

    def test_live_counts_and_discharge_use_the_tunnel_refresh_banner_standard(self):
        self._login_approved_user(role="simulator")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 29, 10, 0)
        self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        for path in ("/neosektor/live-counts", "/neosektor/discharge"):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-operation-refresh-banner", response.data)
                self.assertIn(
                    b"operation-refresh-banner--neosektor-tunnel-standard",
                    response.data,
                )
                self.assertNotIn(b"<div class=\"neosektor-refresh-paused\"", response.data)

        templates_root = Path("app/templates/neonodes/neosektor")
        for template_name in ("live_counts.html", "discharge.html"):
            template = (templates_root / template_name).read_text(encoding="utf-8")
            self.assertIn('operation_refresh_variant = "operation-refresh-banner--neosektor-tunnel-standard"', template)
            self.assertIn('include "neonodes/_operation_refresh_banner.html"', template)

        stylesheet = Path("app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn(".operation-refresh-banner--neosektor-tunnel-standard {", stylesheet)
        self.assertIn("padding: 5px 8px;", stylesheet)
        self.assertIn("font-size: 0.58rem;", stylesheet)
        self.assertNotIn("neosektor-live-counts-grid > .neosektor-refresh-paused", stylesheet)
        self.assertNotIn("neosektor-discharge-wrap > .neosektor-refresh-paused", stylesheet)

    def test_neosektor_auto_refresh_is_limited_to_live_operation_pages(self):
        self._login_approved_user(role="simulator")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 30, 1, 0)
        self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        refresh_pages = (
            "/neosektor/live-counts",
            "/neosektor/tunnel-conductor",
            "/neosektor/ebm",
            "/neosektor/wbm",
            "/neosektor/driver-routing",
            "/neosektor/discharge",
        )
        for path in refresh_pages:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-refresh-active="true"', response.data)
                self.assertIn(b"window.setInterval(refreshState, 5000)", response.data)
                self.assertNotIn(b"setTimeout(refreshState", response.data)

        dashboard_response = self.client.get("/neosektor")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertNotIn(b"data-state-url=", dashboard_response.data)
        self.assertNotIn(b"window.setInterval(refreshState, 5000)", dashboard_response.data)

    def test_neosektor_refresh_active_for_midnight_crossing_operation_window(self):
        self._login_approved_user(role="watcher")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 30, 1, 0)
        operation = self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        response = self.client.get("/neosektor/live-counts/state")

        self.assertEqual(response.status_code, 200)
        refresh = response.get_json()["state"]["refresh"]
        self.assertTrue(refresh["auto_refresh_enabled"])
        self.assertEqual(refresh["operation_id"], operation.id)
        self.assertEqual(refresh["window_start_local"], "22:00")
        self.assertEqual(refresh["window_end_local"], "04:00")
        self.assertEqual(refresh["reason"], "active")

    def test_mobile_live_counts_wave_cards_match_the_ballmat_two_card_structure(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/live-counts")
        css = Path("app/static/css/base.css").read_text()
        layout_start = css.index(
            "body.blueprint-neosektor.neosektor-live-counts-page .neosektor-live-counts-grid {\n"
            "        grid-template-rows: minmax(78px, 0.26fr) minmax(0, 1.74fr);"
        )
        layout_end = css.index(
            "body.blueprint-neosektor.neosektor-live-counts-page .neosektor-live-ballmat-row .ops-column",
            layout_start,
        )
        mobile_wave_layout = css[layout_start:layout_end]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data.count(b'class="wave-metric-label">Left to Arrive'),
            2,
        )
        self.assertEqual(
            response.data.count(b'class="wave-metric-label">Left to Unload'),
            2,
        )
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr));", mobile_wave_layout)
        self.assertIn("grid-template-rows: 18px minmax(0, 1fr);", mobile_wave_layout)
        self.assertIn("grid-template-rows: auto auto;", mobile_wave_layout)
        self.assertIn("place-content: center;", mobile_wave_layout)
        self.assertIn("justify-items: center;", mobile_wave_layout)
        self.assertIn("font-size: 0.54rem;", mobile_wave_layout)
        self.assertIn("font-size: clamp(0.9rem, 4.65vw, 1.2rem);", mobile_wave_layout)
        self.assertNotIn("position: absolute", mobile_wave_layout)
        self.assertNotIn("transform:", mobile_wave_layout)
        self.assertNotIn("margin-top: -", mobile_wave_layout)

    def test_discharge_auto_refresh_uses_operation_window(self):
        self._login_approved_user(role="operator")
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 30, 1, 0)
        operation = self._add_sort_operation(date(2026, 6, 29), "night")
        self._set_sort_window("night", time(22, 0), time(4, 0))

        response = self.client.get("/neosektor/discharge")
        state_response = self.client.get("/neosektor/discharge/state")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-state-url="/neosektor/discharge/state"', response.data)
        self.assertIn(b'data-refresh-active="true"', response.data)
        self.assertIn(b"window.setInterval(refreshState, 5000)", response.data)
        refresh = state_response.get_json()["state"]["refresh"]
        self.assertTrue(refresh["auto_refresh_enabled"])
        self.assertEqual(refresh["operation_id"], operation.id)

    def test_neosektor_dashboard_does_not_auto_refresh(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"data-state-url=", response.data)
        self.assertNotIn(b"window.setInterval(refreshState, 5000)", response.data)

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

    def test_neosektor_mobile_viewport_layout_hooks_cover_all_operation_screens(self):
        self._login_approved_user(role="simulator")

        responses = {
            "dashboard": self.client.get("/neosektor"),
            "live": self.client.get("/neosektor/live-counts"),
            "ebm": self.client.get("/neosektor/ebm"),
            "wbm": self.client.get("/neosektor/wbm"),
            "tunnel": self.client.get("/neosektor/tunnel-conductor"),
        }
        css = Path("app/static/css/base.css").read_text()

        for response in responses.values():
            self.assertEqual(response.status_code, 200)

        self.assertIn(b"neosektor-mobile-dashboard-grid", responses["dashboard"].data)
        self.assertIn(b"id=\"neosektor-live-title\">Live Counts</h1>", responses["live"].data)
        self.assertIn(b"data-live-bay=\"Bay 1\"", responses["live"].data)
        self.assertIn(b"data-live-bay=\"Bay 5\"", responses["live"].data)
        self.assertIn(b"data-ballmat-side=\"east\"", responses["ebm"].data)
        self.assertIn(b"data-ballmat-side=\"west\"", responses["wbm"].data)
        self.assertIn(b"data-tunnel-wave-key=\"first\"", responses["tunnel"].data)
        self.assertIn(b"data-tunnel-wave-key=\"second\"", responses["tunnel"].data)
        self.assertIn(b"data-tunnel-setting=\"down_timer_minutes\"", responses["tunnel"].data)

        self.assertIn("/* NeoSektor mobile viewport balance: preserve the locked console shell. */", css)
        self.assertIn("overflow: hidden;", css)
        self.assertIn("grid-template-columns: 42px minmax(0, 1fr);", css)
        self.assertIn("width: 38px;", css)
        self.assertIn("font-size: clamp(0.8rem, 3.55vw, 0.94rem);", css)
        self.assertIn(
            "body.blueprint-neosektor.neosektor-live-counts-page .mobile-topbar-page-name {",
            css,
        )
        self.assertIn("text-overflow: clip;", css)
        self.assertIn(
            "grid-template-rows: minmax(78px, 0.26fr) minmax(0, 1.74fr);",
            css,
        )
        self.assertIn("grid-auto-rows: minmax(0, 1fr);", css)
        self.assertIn(
            "grid-template-rows: auto repeat(3, minmax(0, 1fr)) minmax(0, 1.42fr);",
            css,
        )
        self.assertIn("grid-template-columns: 30px minmax(0, 1fr) 30px;", css)
        self.assertIn("minmax(0, 0.9fr)", css)
        self.assertIn("minmax(0, 1.42fr)", css)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) minmax(0, 0.68fr);", css)
        self.assertIn("min-height: 32px;", css)
        self.assertIn("height: 30px;", css)

    def test_neosektor_visual_standardization_colors_are_scoped(self):
        self._login_approved_user(role="simulator")
        tunnel = self.client.get("/neosektor/tunnel-conductor")
        live_counts = self.client.get("/neosektor/live-counts")
        css = Path("app/static/css/base.css").read_text()
        settings_panel_block = css.rsplit(
            ".blueprint-neosektor .tunnel-settings-panel {",
            1,
        )[1].split("}", 1)[0]
        live_container_block = css.rsplit(
            ".blueprint-neosektor .neosektor-live-ballmat-row .ops-column,",
            1,
        )[1].split("}", 1)[0]
        data_entry_block = css.split(
            ".blueprint-neosektor .counter-control,",
            1,
        )[1].split("}", 1)[0]

        self.assertEqual(tunnel.status_code, 200)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b'data-metric="left_to_arrive"', tunnel.data)
        self.assertIn(b'data-metric="left_to_unload"', tunnel.data)
        self.assertIn(b'class="counter-number neosektor-numeric-input"', tunnel.data)
        self.assertIn(b'data-live-counts', live_counts.data)
        self.assertIn(b'class="readonly-count"', live_counts.data)
        self.assertIn(
            ".blueprint-neosektor [data-metric=\"left_to_arrive\"],",
            css,
        )
        self.assertIn(
            ".blueprint-neosektor [data-metric=\"left_to_unload\"]",
            css,
        )
        self.assertIn("color: var(--node-sektor-highlight);", css)
        self.assertIn(
            ".blueprint-neosektor .neosektor-live-ballmat-row .readonly-count,",
            css,
        )
        self.assertIn(
            ".blueprint-neosektor .neosektor-live-column-bays .bay-card strong,",
            css,
        )
        self.assertIn("color: #fff;", css)
        self.assertIn("linear-gradient(180deg, rgba(18, 22, 28, 0.92), rgba(4, 7, 11, 0.98));", data_entry_block)
        self.assertIn("border: 0;", settings_panel_block)
        self.assertIn("background: transparent;", settings_panel_block)
        self.assertIn("box-shadow: none;", settings_panel_block)
        self.assertIn("linear-gradient(180deg, rgba(12, 15, 20, 0.96), rgba(3, 5, 8, 0.98));", live_container_block)
        self.assertIn(
            ".blueprint-neosektor .neosektor-live-ballmat-row .readonly-count {\n"
            "    color: #fff;",
            css,
        )
        self.assertIn(
            ".blueprint-neosektor .tunnel-wave-panel .tunnel-arrive-control "
            ".counter-number[data-metric=\"left_to_arrive\"],",
            css,
        )
        self.assertIn("font-size: 2.35rem;", css)

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
        self.assertIn("grid-template-columns: minmax(92px, 1fr) minmax(58px, 76px) minmax(68px, 84px) 40px;", topbar_block)
        self.assertIn("display: none;", logo_block)
        self.assertIn("grid-column: 3;", switcher_block)
        self.assertIn("grid-row: 1;", switcher_block)
        self.assertIn("width: auto;", switcher_block)
        self.assertIn("display: none;", logout_block)
        self.assertIn("grid-column: 4;", menu_button_block)
        self.assertIn("height: var(--mobile-node-banner-button-height);", menu_button_block)
        self.assertIn("min-width: 40px;", menu_button_block)
        self.assertIn("text-wrap: balance;", css)
        self.assertIn("white-space: normal;", css)
        self.assertIn("display: grid;", operator_header_block)
        self.assertIn("grid-template-columns: minmax(92px, 1fr)", operator_header_block)
        self.assertIn("minmax(68px, 84px) 40px;", operator_header_block)
        self.assertIn("grid-column: 3;", css)
        self.assertIn(".character-switcher-standalone .character-switcher-trigger::after", css)
        self.assertIn(".mobile-banner-logout {\n        display: none !important;", css)
        self.assertIn("text-wrap: balance;", operator_switcher_block)
        self.assertIn("white-space: normal;", operator_switcher_block)

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

    def test_neosektor_dashboard_and_live_counts_are_separate_pages(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Operations Menu", dashboard.data)
        self.assertIn(b"data-neosektor-mobile-dashboard", dashboard.data)
        self.assertIn(b'href="/neosektor/live-counts"', dashboard.data)
        self.assertNotIn(b"data-live-counts", dashboard.data)
        self.assertNotIn(b"class=\"readonly-count\"", dashboard.data)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b"data-live-counts", live_counts.data)
        self.assertIn(b"neosektor-count-screen-compact", live_counts.data)
        self.assertNotIn(b"data-neosektor-mobile-dashboard", live_counts.data)

    def test_watcher_can_open_dashboard_and_live_counts_with_live_counts_view_default(self):
        self._login_approved_user(role="watcher")

        dashboard_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.dashboard.view",
        ).one()
        live_counts_rule = PermissionRule.query.filter_by(
            permission_key="neosektor.live_counts.view",
        ).one()
        self.assertEqual(dashboard_rule.minimum_role, "watcher")
        self.assertEqual(live_counts_rule.minimum_role, "watcher")

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
        self.assertIn(b"data-live-counts", live_counts.data)

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

    def _add_sort_operation(self, sort_date, sort_name="night"):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name=sort_name,
        )
        db.session.add(operation)
        db.session.commit()
        return operation

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

    def _set_sheets_compat_enabled(self, enabled):
        from app.services.neosektor_sheets_compat import set_sheets_compatibility_enabled

        set_sheets_compatibility_enabled(self.gateway, enabled)
        db.session.commit()

    def _csrf_token(self, response):
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        if not match:
            self.fail("CSRF token not found in response.")
        return match.group(1).decode()

    def _login_approved_user(self, role):
        user = User(
            username=f"sektor_{role}_user",
            email=f"sektor_{role}@example.test",
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
