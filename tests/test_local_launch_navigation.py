from datetime import date
import importlib
from pathlib import Path
import unittest

from flask import Flask

from app import create_app
from app.extensions import db
from app.models import SortDateOperation
from scripts.seed_dev_user import seed_dev_grandmaster


class LocalLaunchNavigationTest(unittest.TestCase):
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
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_run_py_imports_current_flask_app(self):
        run_module = importlib.import_module("run")

        self.assertIsInstance(run_module.app, Flask)

    def test_root_app_py_is_intentionally_absent(self):
        self.assertFalse(Path("app.py").exists())

    def test_default_gateway_branding_config_is_neorfd(self):
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_CODE"], "RFD")
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_NAME"], "NeoRFD")
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_LOGO"], "images/neorfd_logo1.png")
        self.assertIn("STATIC_ASSET_VERSION", self.app.config)

    def test_neorfd_logo_asset_exists_with_render_safe_casing(self):
        logo_path = Path("app/static/images/neorfd_logo1.png")

        self.assertTrue(logo_path.is_file())
        self.assertEqual(logo_path.name, "neorfd_logo1.png")
        self.assertGreater(logo_path.stat().st_size, 0)

    def test_base_css_uses_cyber_topbar_without_vertical_grid_background(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".centered-command-page", css)
        self.assertIn(".centered-command-page .operation-form", css)
        self.assertIn(".centered-command-page .user-search-form", css)
        self.assertIn("text-align-last: center;", css)
        self.assertIn(".user-edit-role-field select,", css)
        self.assertIn(".role-select-wrap::after", css)
        self.assertIn("border-right: 2px solid #ff3b46;", css)
        self.assertIn("width: min(100%, 240px);", css)
        self.assertIn(".user-chip", css)
        self.assertIn(".topbar::after", css)
        self.assertIn("../images/neobutton1_medium.png", css)
        self.assertIn(".rfd-node-prefix", css)
        self.assertIn(".rfd-node-suffix", css)
        self.assertIn(".rfd-mobile-logo", css)
        self.assertIn("width: min(100% - 20px, 1440px);", css)
        self.assertIn(".rfd-hub-logo {\n        display: none;", css)
        self.assertIn(".rfd-motherbrain-launch {\n        order: 2;", css)
        self.assertIn(".rfd-node-sektor {\n        order: 3;", css)
        self.assertIn(".rfd-node-ermac {\n        order: 4;", css)
        self.assertIn(".rfd-node-reptile {\n        order: 5;", css)
        self.assertIn(".rfd-node-subzero {\n        order: 6;", css)
        self.assertIn(".rfd-node-rain {\n        order: 7;", css)
        self.assertIn(".rfd-node-scorpion {\n        order: 8;", css)
        self.assertIn(".motherbrain-menu {\n        align-items: stretch;\n        flex-direction: column;", css)
        self.assertIn(".motherbrain-dashboard", css)
        self.assertIn(".motherbrain-dashboard-card", css)
        self.assertIn(".motherbrain-fixed-header .topbar", css)
        self.assertIn("position: fixed;", css)
        self.assertIn("grid-template-columns: 72px minmax(0, 1fr);", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", css)
        self.assertIn("grid-template-columns: repeat(5, minmax(104px, 1fr));", css)
        self.assertIn("grid-template-columns: 48px minmax(68px, 1fr) minmax(68px, auto) auto auto;", css)
        self.assertIn(".motherbrain-menu-button", css)
        self.assertIn("top: calc(100% + 6px);", css)
        self.assertIn(".topbar.is-menu-open .motherbrain-header-nav", css)
        self.assertIn("padding-top: 72px;", css)
        self.assertIn(".motherbrain-fixed-header .content", css)
        self.assertNotIn("42px 42px", css)
        self.assertNotIn("linear-gradient(90deg, rgba(201, 208, 214, 0.035) 1px", css)

    def test_base_template_cache_busts_stylesheet(self):
        template = Path("app/templates/base.html").read_text()

        self.assertIn("filename='css/base.css', v=config.STATIC_ASSET_VERSION", template)

    def test_neonode_button_asset_exists_with_render_safe_casing(self):
        button_path = Path("app/static/images/neobutton1_medium.png")

        self.assertTrue(button_path.is_file())
        self.assertEqual(button_path.name, "neobutton1_medium.png")
        self.assertGreater(button_path.stat().st_size, 0)

    def test_public_home_uses_enter_login_form_without_separate_login_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoRFD", response.data)
        self.assertIn(b"NeoGateway", response.data)
        self.assertIn(b"Powered by NeoApps", response.data)
        self.assertNotIn(b"Gateway Command Layer", response.data)
        self.assertNotIn(b'class="gateway-context"', response.data)
        self.assertNotIn(b'class="platform-brand"', response.data)
        self.assertNotIn(b'class="powered-by"', response.data)
        self.assertNotIn(b"NeoRFD / RFD Gateway Workspace", response.data)
        self.assertNotIn(b"Gateway Workspace</p>", response.data)
        self.assertIn(b'src="/static/images/neorfd_logo1.png"', response.data)
        self.assertNotIn(b"motherbrain_logo1.png", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login">', response.data)
        self.assertIn(b'<label for="dashboard-email">Email</label>', response.data)
        self.assertIn(b'name="email"', response.data)
        self.assertNotIn(b'name="username"', response.data)
        self.assertIn(b'name="password"', response.data)
        self.assertIn(b'<button class="command-access-panel command-enter-button" type="submit">', response.data)
        self.assertIn(b"ENTER", response.data)
        self.assertIn(b'href="/create-account"', response.data)
        self.assertIn(b'href="/forgot-password"', response.data)
        self.assertNotIn(b">Login<", response.data)
        self.assertNotIn(b"Authorize Access", response.data)

    def test_public_home_does_not_render_node_tiles_or_links(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"NeoSektor", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b'href="https://neosektor.onrender.com/"', response.data)
        self.assertNotIn(b'src="/static/images/neosektor_logo1.png"', response.data)

    def test_login_route_is_reachable(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoRFD", response.data)
        self.assertIn(b'src="/static/images/neorfd_logo1.png"', response.data)
        self.assertNotIn(b"Gateway Command Layer", response.data)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login">', response.data)
        self.assertIn(b"ENTER", response.data)

    def test_seeded_kessler_login_is_case_insensitive_and_enters_rfd_hub(self):
        seed_dev_grandmaster(self.app)

        response = self.client.post(
            "/login",
            data={"email": " kessler@local.neoapps ", "password": "1313"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/rfd")

    def test_seeded_kessler_grandmaster_accesses_motherbrain_routes(self):
        seed_dev_grandmaster(self.app)
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        login_response = self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
            follow_redirects=False,
        )

        self.assertEqual(login_response.status_code, 302)

        direct_paths = (
            "/motherbrain",
            "/motherbrain/operations",
            "/motherbrain/master-schedule",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
        )
        for path in direct_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

        hub_response = self.client.get("/rfd")
        nav_html = hub_response.data.decode().split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
        hub_html = hub_response.data.decode()
        left_column = hub_html.split('rfd-node-column-left"', 1)[1].split("</div>", 1)[0]
        right_column = hub_html.split('rfd-node-column-right"', 1)[1].split("</div>", 1)[0]
        self.assertIn(b'src="/static/images/neorfd_logo1.png"', hub_response.data)
        self.assertIn(b'class="rfd-mobile-logo"', hub_response.data)
        self.assertIn(b"NeoMotherBrain", hub_response.data)
        self.assertIn(b"NeoSektor", hub_response.data)
        for node_name in (
            b"NeoScorpion",
            b"NeoReptile",
            b"NeoErmac",
            b"NeoSubZero",
            b"NeoRain",
        ):
            self.assertIn(node_name, hub_response.data)
        self.assertNotIn(b"Placeholder", hub_response.data)
        self.assertNotIn(b"Launch", hub_response.data)
        self.assertNotIn(b"Gateway Command Layer", hub_response.data)
        self.assertLess(hub_html.index('aria-label="NeoMotherBrain"'), hub_html.index('class="rfd-node-grid"'))
        self.assertLess(hub_html.index('rfd-node-column-left"'), hub_html.index('rfd-hub-logo"'))
        self.assertLess(hub_html.index('rfd-hub-logo"'), hub_html.index('rfd-node-column-right"'))
        left_order = (
            "NeoSektor",
            "NeoReptile",
            "NeoRain",
        )
        right_order = (
            "NeoErmac",
            "NeoSubZero",
            "NeoScorpion",
        )
        left_positions = [left_column.index(f'aria-label="{node}"') for node in left_order]
        right_positions = [right_column.index(f'aria-label="{node}"') for node in right_order]
        self.assertEqual(left_positions, sorted(left_positions))
        self.assertEqual(right_positions, sorted(right_positions))
        self.assertIn(b'href="/logout"', hub_response.data)
        self.assertIn("Logout", nav_html)
        self.assertNotIn("NeoRFD", nav_html)
        self.assertNotIn("NeoMotherBrain", nav_html)
        self.assertNotIn("NeoSektor", nav_html)
        self.assertNotIn("Nightly Operations", nav_html)
        self.assertNotIn("Master Schedule", nav_html)
        self.assertNotIn("Access Requests", nav_html)
        self.assertNotIn("User Management", nav_html)
        self.assertNotIn(b"Nightly Operations", hub_response.data)
        self.assertNotIn(b"Master Schedule", hub_response.data)
        self.assertNotIn(b"Access Requests", hub_response.data)
        self.assertNotIn(b"User Management", hub_response.data)


if __name__ == "__main__":
    unittest.main()
