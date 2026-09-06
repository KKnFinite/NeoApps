from tests.css_contracts import stylesheet_source
from datetime import date
import importlib
import os
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from flask import Flask, g
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from tests.html_contracts import document, assert_mobile_drawer

from app import create_app
from app.extensions import db, login_manager
from app.models import SortDateOperation, User
from app.services.gateway_matrix import current_gateway_local_date
from scripts.seed_dev_user import LOCAL_SQLITE_FALLBACK_PASSWORD, seed_dev_grandmaster


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

    def _asset_test_login(self):
        seed_dev_grandmaster(self.app)
        response = self.client.post('/login', data={
            'email': 'kessler@local.neoapps', 'password': LOCAL_SQLITE_FALLBACK_PASSWORD,
        })
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertIn('_user_id', session)
        g.pop('_login_user', None)

    def _assert_public_assets_without_auth_work(self, *, broken_loader=False):
        self._asset_test_login()
        paths = [
            '/static/css/base.css', '/static/js/mobile_drawer.js',
            '/static/fonts/neofont/NeoFont.woff2',
            '/static/images/icons/neoapps/inapp/neoapps-inapp-128.png',
            '/manifest.webmanifest', '/manifest/neosektor.webmanifest',
            '/service-worker.js', '/apple-touch-icon.png',
            '/apple-touch-icon-precomposed.png', '/favicon-32x32.png',
            '/favicon-16x16.png', '/favicon.ico',
        ]
        calls = []
        def record(*args):
            calls.append(args[2])
        loader_options = ({'side_effect': OperationalError('simulated unavailable DB', {}, None)}
                          if broken_loader else {'wraps': login_manager._user_callback})
        with patch.object(login_manager, '_user_callback', **loader_options) as loader:
            for method in ('GET', 'HEAD'):
                for path in paths + ['/static/missing-asset.png', '/manifest/missing.webmanifest']:
                    with self.subTest(method=method, path=path, broken=broken_loader):
                        # The fixture holds an app context; remove its cached user
                        # so every measured request behaves like a fresh WSGI request.
                        g.pop('_login_user', None)
                        calls.clear()
                        event.listen(db.engine, 'before_cursor_execute', record)
                        try:
                            response = self.client.open(path, method=method)
                        finally:
                            event.remove(db.engine, 'before_cursor_execute', record)
                        self.assertEqual(response.status_code, 404 if 'missing' in path else 200)
                        self.assertEqual(calls, [])
                        loader.assert_not_called()
                        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                        response.close()

    def test_public_assets_with_login_cookie_do_no_auth_or_database_work(self):
        self._assert_public_assets_without_auth_work()

    def test_public_assets_survive_user_loader_database_failure(self):
        self._assert_public_assets_without_auth_work(broken_loader=True)
        g.pop('_login_user', None)
        with patch.object(login_manager, '_user_callback', side_effect=OperationalError(
            'simulated unavailable DB', {}, None,
        )) as loader:
            with self.assertRaises(OperationalError):
                self.client.get('/share/neoapps-qr.svg')
            loader.assert_called_once()

    def test_asset_exemption_preserves_session_invalidation_and_private_qr(self):
        self._asset_test_login()
        user = User.query.filter_by(username='Kessler').one()
        user.auth_session_version += 1
        db.session.commit()
        response = self.client.get('/static/css/base.css')
        self.assertEqual(response.status_code, 200)
        response.close()
        g.pop('_login_user', None)
        response = self.client.get('/portal')
        self.assertEqual(response.location, '/login')
        g.pop('_login_user', None)
        private = self.client.get('/share/neoapps-qr.svg')
        self.assertEqual(private.status_code, 302)
        self.assertIn('/login', private.location)

    def test_share_qr_has_opaque_background_black_modules_and_four_module_quiet_zone(self):
        from reportlab.graphics.barcode.qr import QrCodeWidget

        self._asset_test_login()
        with patch('reportlab.graphics.barcode.qr.QrCodeWidget', wraps=QrCodeWidget) as widget:
            response = self.client.get('/share/neoapps-qr.svg')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/svg+xml')
        widget.assert_called_once_with('http://localhost/nodes/')
        self.assertEqual(response.headers['Cache-Control'], 'private, max-age=300')
        svg = ET.fromstring(response.data)
        self.assertEqual(svg.attrib['viewBox'], '0 0 220 220')
        rects = svg.findall('.//{http://www.w3.org/2000/svg}g//{http://www.w3.org/2000/svg}rect')
        background = rects[0]
        self.assertIn('fill: rgb(100%,100%,100%);', background.attrib['style'])
        self.assertEqual([float(background.attrib[k]) for k in ('x','y','width','height')], [0,0,220,220])
        self.assertNotIn('opacity', response.get_data(as_text=True))
        modules = [r for r in rects if 'fill: rgb(0%,0%,0%);' in r.get('style','')]
        self.assertGreater(len(modules), 20)
        unit = min(float(r.attrib['height']) for r in modules)
        for r in modules:
            x, y, w, h = (float(r.attrib[k]) for k in ('x','y','width','height'))
            self.assertGreaterEqual(min(x, y, 220-x-w, 220-y-h), 4*unit-1e-8)
            self.assertNotIn('rx', r.attrib)
        for r in rects:
            self.assertTrue(any(fill in r.get('style','') for fill in (
                'fill: none;', 'fill: rgb(0%,0%,0%);', 'fill: rgb(100%,100%,100%);')))

    def test_asset_exemption_preserves_forced_password_change_on_private_routes(self):
        self._asset_test_login()
        user = User.query.filter_by(username='Kessler').one()
        user.password_policy_update_required = True
        db.session.commit()
        self.assertEqual(self.client.get('/manifest.webmanifest').status_code, 200)
        for path in ('/portal', '/share/neoapps-qr.svg'):
            g.pop('_login_user', None)
            self.assertEqual(self.client.get(path).location, '/change-password')

    def test_run_py_imports_current_flask_app(self):
        with patch.dict(os.environ, {"NEOAPPS_ENV": "development"}, clear=False):
            run_module = importlib.import_module("run")

        self.assertIsInstance(run_module.app, Flask)

    def test_root_app_py_is_intentionally_absent(self):
        self.assertFalse(Path("app.py").exists())

    def test_default_gateway_branding_config_preserves_rfd_context_with_neogateway_logo(self):
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_CODE"], "RFD")
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_NAME"], "NeoGateway")
        self.assertEqual(
            self.app.config["DEFAULT_GATEWAY_LOGO"],
            "images/icons/neogateway/inapp/neogateway-inapp-128.png",
        )
        self.assertIn("STATIC_ASSET_VERSION", self.app.config)
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["REMEMBER_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(self.app.config["REMEMBER_COOKIE_HTTPONLY"])

    def test_default_neogateway_logo_asset_exists_with_render_safe_casing(self):
        logo_path = Path("app/static/images/icons/neogateway/inapp/neogateway-inapp-128.png")

        self.assertTrue(logo_path.is_file())
        self.assertEqual(logo_path.name, "neogateway-inapp-128.png")
        self.assertGreater(logo_path.stat().st_size, 0)

    def test_rfd_launcher_uses_all_seven_locked_node_logos(self):
        template = Path("app/templates/neomotherbrain/rfd_hub.html").read_text()
        expected_icons = {
            "motherbrain": "newlogo_motherbrain_small.png", "sektor": "newlogo_sektor_small.png",
            "ermac": "newlogo_ermac_small.png", "scorpion": "newlogo_scorpion.png",
            "rain": "newlogo_rain_small.png", "subzero": "newlogo_subzero_small.png",
            "reptile": "newlogo_reptile_small.png",
        }
        self.assertIn('filename=node_logos[slug]', template)
        for slug, filename in expected_icons.items():
            with self.subTest(slug=slug):
                self.assertIn(f'"{slug}": "images/logos/{filename}"', template)
                self.assertTrue(Path('app/static/images/logos', filename).is_file())

    def test_base_css_uses_cyber_topbar_without_vertical_grid_background(self):
        css = stylesheet_source()

        self.assertIn(".centered-command-page", css)
        self.assertIn(".centered-command-page .operation-form", css)
        self.assertIn(".centered-command-page .user-search-form", css)
        self.assertIn("text-align-last: center;", css)
        self.assertIn(".user-edit-role-field select,", css)
        self.assertIn(".role-select-wrap::after", css)
        self.assertIn("border-right: 2px solid var(--node-highlight);", css)
        self.assertIn("width: min(100%, 240px);", css)
        self.assertIn(".user-chip", css)
        self.assertIn(".topbar::after", css)
        self.assertIn(".rfd-node-card-icon-wrap", css)
        self.assertIn(".rfd-node-card-icon", css)
        self.assertIn(".rfd-node-card-main", css)
        self.assertNotIn("../images/neobutton1_medium.png", css)
        self.assertIn(".rfd-node-prefix", css)
        self.assertIn(".rfd-node-suffix", css)
        self.assertIn("width: min(100% - 20px, 1440px);", css)
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
        self.assertIn(".motherbrain-main-menu-return", css)
        self.assertIn(".motherbrain-fixed-header .topbar", css)
        self.assertIn("position: fixed;", css)
        self.assertIn("grid-template-columns: 92px minmax(0, 1fr);", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto;", css)
        self.assertIn("grid-template-columns: auto minmax(0, 1fr) auto;", css)
        self.assertIn("grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));", css)
        self.assertIn(".motherbrain-fixed-header .topbar-user-row", css)
        self.assertIn("--mobile-node-banner-button-height: 36px;", css)
        self.assertIn("grid-template-columns: minmax(92px, 1fr) minmax(58px, 76px) minmax(68px, 84px) 40px;", css)
        self.assertIn("grid-template-columns: minmax(82px, 1fr) 36px minmax(52px, 68px) minmax(64px, 82px) 40px;", css)
        self.assertIn(".motherbrain-fixed-header .character-switcher-trigger::after", css)
        self.assertIn("white-space: normal;", css)
        self.assertIn(".mobile-banner-logout {\n        display: none !important;", css)
        self.assertIn(".motherbrain-header-identity .neo-node-name", css)
        self.assertIn(".motherbrain-menu-button", css)
        self.assertIn("top: calc(100% + 6px);", css)
        self.assertIn(".topbar.is-menu-open .motherbrain-header-nav", css)
        self.assertIn(".character-switcher", css)
        self.assertIn("padding-top: 76px;", css)
        self.assertIn(".motherbrain-fixed-header .content", css)
        self.assertIn(".mobile-topbar,\n.mobile-bottom-nav {\n    display: none;", css)
        self.assertIn("body.mobile-app-chrome.has-mobile-topbar .shell > .topbar", css)
        self.assertIn("body.mobile-app-chrome.has-mobile-bottom-nav .content", css)
        self.assertIn(".mobile-account-menu", css)
        self.assertIn(".mobile-bottom-nav", css)
        self.assertIn("env(safe-area-inset-bottom)", css)
        self.assertIn("backdrop-filter: blur(16px);", css)
        self.assertNotIn('content: ">";', css)
        self.assertNotIn("42px 42px", css)
        self.assertNotIn("linear-gradient(90deg, rgba(201, 208, 214, 0.035) 1px", css)

    def test_base_css_prevents_accidental_mobile_zoom(self):
        css = stylesheet_source()

        self.assertIn("-webkit-text-size-adjust: 100%;", css)
        self.assertIn("text-size-adjust: 100%;", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("font-size: max(16px, 1rem);", css)
        self.assertIn("touch-action: manipulation;", css)

    def test_base_template_cache_busts_stylesheet(self):
        template = Path("app/templates/base.html").read_text()

        self.assertIn("for stylesheet in page_stylesheets", template)
        self.assertIn("filename=stylesheet, v=config.STATIC_ASSET_VERSION", template)
        self.assertIn(
            "url_for('pwa_manifest_by_key', manifest_key=current_pwa_manifest_key(), v=config.STATIC_ASSET_VERSION)",
            template,
        )
        self.assertIn("url_for('service_worker', v=config.STATIC_ASSET_VERSION)", template)
        self.assertIn(
            '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">',
            template,
        )
        self.assertIn('name="theme-color" content="#d95a1f"', template)
        self.assertIn('name="apple-mobile-web-app-title" content="NeoApps"', template)
        self.assertIn("url_for('apple_touch_icon')", template)
        self.assertIn("url_for('apple_touch_icon_precomposed')", template)
        self.assertIn("url_for('favicon_32')", template)
        self.assertIn("url_for('favicon_16')", template)

    def test_neoapps_manifest_uses_current_branding(self):
        response = self.client.get("/manifest.webmanifest")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/manifest+json")
        self.assertIn("no-cache", response.headers["Cache-Control"])
        manifest = response.get_json()
        self.assertEqual(manifest["id"], "/manifest/neoapps.webmanifest")
        self.assertEqual(manifest["name"], "NeoApps")
        self.assertEqual(manifest["short_name"], "NeoApps")
        self.assertEqual(manifest["start_url"], "/portal")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        icon_map = {
            (icon["src"], icon["sizes"], icon["purpose"])
            for icon in manifest["icons"]
        }
        self.assertIn(
            ("/static/images/icons/neoapps/pwa/neoapps-icon-192.png", "192x192", "any"),
            icon_map,
        )
        self.assertIn(
            ("/static/images/icons/neoapps/pwa/neoapps-icon-512.png", "512x512", "any"),
            icon_map,
        )
        self.assertIn(
            (
                "/static/images/icons/neoapps/pwa/neoapps-maskable-192.png",
                "192x192",
                "maskable",
            ),
            icon_map,
        )
        self.assertIn(
            (
                "/static/images/icons/neoapps/pwa/neoapps-maskable-512.png",
                "512x512",
                "maskable",
            ),
            icon_map,
        )
        manifest_text = response.get_data(as_text=True)
        self.assertNotIn("neogateway_icon", manifest_text)
        self.assertNotIn("NeoRFD", manifest_text)
        self.assertNotIn("neorfd", manifest_text.lower())

    def test_app_and_node_manifests_have_independent_branding_and_icons(self):
        expected_manifests = {
            "neoapps": (
                "NeoApps",
                "NeoApps",
                "/portal",
                "#d9362e",
                [
                    ("/static/images/icons/neoapps/pwa/neoapps-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neoapps/pwa/neoapps-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neoapps/pwa/neoapps-maskable-192.png", "192x192", "maskable"),
                    ("/static/images/icons/neoapps/pwa/neoapps-maskable-512.png", "512x512", "maskable"),
                ],
            ),
            "neoportal": (
                "NeoApps",
                "NeoApps",
                "/portal",
                "#d9362e",
                [
                    ("/static/images/icons/neoapps/pwa/neoapps-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neoapps/pwa/neoapps-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neoapps/pwa/neoapps-maskable-192.png", "192x192", "maskable"),
                    ("/static/images/icons/neoapps/pwa/neoapps-maskable-512.png", "512x512", "maskable"),
                ],
            ),
            "neogateway": (
                "NeoGateway",
                "NeoGateway",
                "/rfd",
                "#d95a1f",
                [
                    ("/static/images/icons/neogateway/pwa/neogateway-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neogateway/pwa/neogateway-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neogateway/pwa/neogateway-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "neostaffing": (
                "NeoStaffing",
                "NeoStaffing",
                "/neostaffing",
                "#27d0c2",
                [
                    ("/static/images/icons/neostaffing/pwa/neostaffing-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neostaffing/pwa/neostaffing-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neostaffing/pwa/neostaffing-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "neobid": ("NeoBid", "NeoBid", "/neobid", "#4db7ff", None),
            "neomotherbrain": (
                "NeoMotherBrain",
                "MotherBrain",
                "/motherbrain",
                "#cf6a6e",
                [
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "motherbrain": (
                "NeoMotherBrain",
                "MotherBrain",
                "/motherbrain",
                "#cf6a6e",
                [
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neomotherbrain/pwa/neomotherbrain-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "neosektor": (
                "NeoSektor",
                "NeoSektor",
                "/neosektor",
                "#b5121b",
                [
                    ("/static/images/icons/neosektor/pwa/android-chrome-192x192.png", "192x192", "any"),
                    ("/static/images/icons/neosektor/pwa/android-chrome-512x512.png", "512x512", "any"),
                    ("/static/images/icons/neosektor/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                    ("/static/images/icons/neosektor/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
                ],
            ),
            "sektor": (
                "NeoSektor",
                "NeoSektor",
                "/neosektor",
                "#b5121b",
                [
                    ("/static/images/icons/neosektor/pwa/android-chrome-192x192.png", "192x192", "any"),
                    ("/static/images/icons/neosektor/pwa/android-chrome-512x512.png", "512x512", "any"),
                    ("/static/images/icons/neosektor/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                    ("/static/images/icons/neosektor/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
                ],
            ),
            "neoermac": (
                "NeoErmac",
                "NeoErmac",
                "/neoermac",
                "#8f1826",
                [
                    ("/static/images/icons/neoermac/pwa/neoermac-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neoermac/pwa/neoermac-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neoermac/pwa/neoermac-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "ermac": (
                "NeoErmac",
                "NeoErmac",
                "/neoermac",
                "#8f1826",
                [
                    ("/static/images/icons/neoermac/pwa/neoermac-icon-192.png", "192x192", "any"),
                    ("/static/images/icons/neoermac/pwa/neoermac-icon-512.png", "512x512", "any"),
                    ("/static/images/icons/neoermac/pwa/neoermac-maskable-512.png", "512x512", "any maskable"),
                ],
            ),
            "neoscorpion": (
                "NeoScorpion",
                "NeoScorpion",
                "/neoscorpion",
                "#f4c21f",
                [
                    ("/static/images/icons/neoscorpion/pwa/icon-192x192.png", "192x192", "any"),
                    ("/static/images/icons/neoscorpion/pwa/icon-512x512.png", "512x512", "any"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
                ],
            ),
            "scorpion": (
                "NeoScorpion",
                "NeoScorpion",
                "/neoscorpion",
                "#f4c21f",
                [
                    ("/static/images/icons/neoscorpion/pwa/icon-192x192.png", "192x192", "any"),
                    ("/static/images/icons/neoscorpion/pwa/icon-512x512.png", "512x512", "any"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
                ],
            ),
            "reptile": ("NeoReptile", "NeoReptile", "/nodes/", "#70e13b", None),
            "subzero": ("NeoSub-Zero", "Sub-Zero", "/neosubzero", "#4db7ff", None),
            "rain": ("NeoRain", "NeoRain", "/neorain", "#7f4dff", None),
        }

        for manifest_key, (name, short_name, start_url, theme_color, expected_icons) in expected_manifests.items():
            with self.subTest(manifest_key=manifest_key):
                response = self.client.get(f"/manifest/{manifest_key}.webmanifest")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "application/manifest+json")
                self.assertIn("no-cache", response.headers["Cache-Control"])
                manifest = response.get_json()
                self.assertEqual(manifest["id"], f"/manifest/{manifest_key}.webmanifest")
                self.assertEqual(manifest["name"], name)
                self.assertEqual(manifest["short_name"], short_name)
                self.assertEqual(manifest["start_url"], start_url)
                self.assertEqual(manifest["scope"], "/")
                self.assertEqual(manifest["display"], "standalone")
                self.assertEqual(manifest["theme_color"], theme_color)
                if expected_icons is not None:
                    for src, sizes, purpose in expected_icons:
                        self.assertIn(
                            {
                                "src": src,
                                "sizes": sizes,
                                "type": "image/png",
                                "purpose": purpose,
                            },
                            manifest["icons"],
                        )
                    for icon in manifest["icons"]:
                        icon_response = self.client.get(icon["src"])
                        self.assertEqual(icon_response.status_code, 200)
                        self.assertEqual(icon_response.mimetype, "image/png")

        missing_response = self.client.get("/manifest/not-real.webmanifest")
        self.assertEqual(missing_response.status_code, 404)

    def test_pwa_root_icon_routes_serve_neoapps_images(self):
        icon_routes = {
            "/apple-touch-icon.png": Path("app/static/images/icons/neoapps/pwa/apple-touch-icon.png"),
            "/apple-touch-icon-precomposed.png": Path("app/static/images/icons/neoapps/pwa/apple-touch-icon.png"),
            "/favicon-32x32.png": Path("app/static/images/icons/neoapps/favicon/favicon-32.png"),
            "/favicon-16x16.png": Path("app/static/images/icons/neoapps/favicon/favicon-16.png"),
            "/favicon.ico": Path("app/static/images/icons/neoapps/favicon/favicon-32.png"),
        }

        for route, source_path in icon_routes.items():
            with self.subTest(route=route):
                response = self.client.get(route)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, "image/png")
                self.assertIn("no-cache", response.headers["Cache-Control"])
                self.assertEqual(response.data, source_path.read_bytes())

    def test_locked_app_and_node_icon_structure_exists(self):
        icon_root = Path("app/static/images/icons")
        expected_files = {
            "neoapps": (
                "pwa/neoapps-icon-192.png",
                "pwa/neoapps-icon-512.png",
                "pwa/neoapps-maskable-192.png",
                "pwa/neoapps-maskable-512.png",
                "pwa/apple-touch-icon.png",
                "favicon/favicon-32.png",
                "favicon/favicon-16.png",
            ),
            "neogateway": (
                "pwa/neogateway-icon-192.png",
                "pwa/neogateway-icon-512.png",
                "pwa/neogateway-maskable-512.png",
            ),
            "neostaffing": (
                "pwa/neostaffing-icon-192.png",
                "pwa/neostaffing-icon-512.png",
                "pwa/neostaffing-maskable-512.png",
            ),
            "neomotherbrain": (
                "pwa/neomotherbrain-icon-192.png",
                "pwa/neomotherbrain-icon-512.png",
                "pwa/neomotherbrain-maskable-512.png",
            ),
            "neoermac": (
                "pwa/neoermac-icon-192.png",
                "pwa/neoermac-icon-512.png",
                "pwa/neoermac-maskable-512.png",
            ),
            "neosektor": (
                "pwa/android-chrome-192x192.png",
                "pwa/android-chrome-512x512.png",
                "pwa/maskable-icon-192x192.png",
                "pwa/maskable-icon-512x512.png",
            ),
            "neoscorpion": (
                "pwa/icon-192x192.png",
                "pwa/icon-512x512.png",
                "pwa/maskable-icon-192x192.png",
                "pwa/maskable-icon-512x512.png",
            ),
        }

        for folder, files in expected_files.items():
            for filename in files:
                with self.subTest(folder=folder, filename=filename):
                    self.assertTrue((icon_root / folder / filename).exists())

    def test_service_worker_is_conservative_and_uses_current_logo_assets(self):
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/javascript")
        self.assertIn("no-cache", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        service_worker = response.get_data(as_text=True)
        self.assertIn("CACHE_NAME", service_worker)
        self.assertIn('CACHE_PREFIX = "neogateway-"', service_worker)
        configured_version = self.app.config["STATIC_ASSET_VERSION"]
        self.assertIn(
            f'const STATIC_ASSET_VERSION = "{configured_version}";',
            service_worker,
        )
        self.assertIn(
            "const CACHE_NAME = `neogateway-static-v${STATIC_ASSET_VERSION}`;",
            service_worker,
        )
        self.assertIn(
            "`/static/css/base.css?v=${STATIC_ASSET_VERSION}`",
            service_worker,
        )
        self.assertNotIn("__STATIC_ASSET_VERSION__", service_worker)
        self.assertIn('request.mode === "navigate"', service_worker)
        self.assertIn('event.respondWith(fetch(request, { cache: "no-store" }));', service_worker)
        self.assertIn("caches.delete(cacheName)", service_worker)
        self.assertNotIn('caches.match(request))', service_worker.split('request.mode === "navigate"', 1)[1].split('if (!requestUrl.pathname.startsWith("/static/"))', 1)[0])
        self.assertNotIn("/neoermac/door-view", service_worker)
        self.assertIn("/static/images/icons/neogateway/inapp/neogateway-inapp-128.png", service_worker)
        self.assertIn("/static/images/icons/neogateway/inapp/neogateway-inapp-256.png", service_worker)
        self.assertIn("/static/images/icons/neoapps/inapp/neoapps-inapp-128.png", service_worker)
        self.assertNotIn("/static/images/neogateway_logo3_small.png", service_worker)
        self.assertNotIn("/static/images/neogateway_logo3_medium.png", service_worker)
        self.assertNotIn("/static/images/neogateway_logo3_large.png", service_worker)
        self.assertNotIn("NeoRFD", service_worker)
        self.assertNotIn("neorfd", service_worker.lower())

    def test_current_versioned_static_asset_has_immutable_cache_control(self):
        version = self.app.config["STATIC_ASSET_VERSION"]

        response = self.client.get(f"/static/css/base.css?v={version}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )

    def test_unversioned_and_old_static_assets_are_not_immutable(self):
        for path in (
            "/static/css/base.css",
            "/static/css/base.css?v=old-version",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("immutable", response.headers.get("Cache-Control", ""))

    def test_service_worker_uses_custom_configured_static_asset_version(self):
        self.app.config["STATIC_ASSET_VERSION"] = "custom-test-version"

        response = self.client.get("/service-worker.js")
        service_worker = response.get_data(as_text=True)

        self.assertIn(
            'const STATIC_ASSET_VERSION = "custom-test-version";',
            service_worker,
        )
        self.assertIn("no-cache", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")

    def test_version_query_does_not_change_pwa_route_cache_behavior(self):
        version = self.app.config["STATIC_ASSET_VERSION"]

        for path in (
            "/manifest/neoapps.webmanifest",
            "/service-worker.js",
            "/favicon.ico",
            "/apple-touch-icon.png",
        ):
            with self.subTest(path=path):
                response = self.client.get(f"{path}?v={version}")
                self.assertEqual(response.status_code, 200)
                self.assertIn("no-cache", response.headers["Cache-Control"])
                self.assertNotIn("immutable", response.headers["Cache-Control"])

    def test_security_headers_are_applied(self):
        response = self.client.get("/login")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=()",
        )

    def test_neonode_button_asset_exists_with_render_safe_casing(self):
        button_path = Path("app/static/images/icons/neogateway/inapp/neogateway-inapp-128.png")

        self.assertTrue(button_path.is_file())
        self.assertEqual(button_path.name, "neogateway-inapp-128.png")
        self.assertGreater(button_path.stat().st_size, 0)

    def test_public_home_uses_enter_login_form_without_separate_login_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="portal-login-hero neo-auth-environment"', response.data)
        self.assertIn(b'src="/static/images/hero/neoapps_login_desktop.png"', response.data)
        self.assertIn(b'srcset="/static/images/hero/neoapps_login_mobile.png"', response.data)
        self.assertNotIn(b'class="topbar"', response.data)
        self.assertNotIn(b"mobile-account-trigger", response.data)
        self.assertNotIn(b"data-mobile-topbar", response.data)
        self.assertNotIn(b"<strong>PORTAL</strong>", response.data)
        self.assertNotIn(b"Sign in once", response.data)
        self.assertNotIn(b"NeoRFD", response.data)
        self.assertNotIn(b"Powered by", response.data)
        self.assertNotIn(b"Gateway Command Layer", response.data)
        self.assertNotIn(b'class="gateway-context"', response.data)
        self.assertNotIn(b'class="platform-brand"', response.data)
        self.assertNotIn(b'class="powered-by"', response.data)
        self.assertNotIn(b"NeoRFD / RFD Gateway Workspace", response.data)
        self.assertNotIn(b"Gateway Workspace</p>", response.data)
        self.assertNotIn(b'src="/static/images/neogateway_logo3_large.png"', response.data)
        self.assertNotIn(b'neogateway_logo3_small.png', response.data)
        self.assertNotIn(b'neogateway_logo3_medium.png', response.data)
        self.assertNotIn(b"motherbrain_logo1.png", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login" data-interaction-form>', response.data)
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
        self.assertNotIn(b"Change Characters", response.data)
        self.assertNotIn(b"NeoSektor", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b'href="https://neosektor.onrender.com/"', response.data)
        self.assertNotIn(b'src="/static/images/neosektor_logo1.png"', response.data)

    def test_login_route_is_reachable(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="portal-login-hero neo-auth-environment"', response.data)
        self.assertIn(b'src="/static/images/hero/neoapps_login_desktop.png"', response.data)
        self.assertIn(b'srcset="/static/images/hero/neoapps_login_mobile.png"', response.data)
        self.assertNotIn(b'class="topbar"', response.data)
        self.assertNotIn(b"mobile-account-trigger", response.data)
        self.assertNotIn(b"data-mobile-topbar", response.data)
        self.assertNotIn(b"<strong>PORTAL</strong>", response.data)
        self.assertNotIn(b"Sign in once", response.data)
        self.assertNotIn(b"Change Characters", response.data)
        self.assertNotIn(b"NeoRFD", response.data)
        self.assertNotIn(b'src="/static/images/neogateway_logo3_large.png"', response.data)
        self.assertNotIn(b'neogateway_logo3_small.png', response.data)
        self.assertNotIn(b'neogateway_logo3_medium.png', response.data)
        self.assertNotIn(b"Gateway Command Layer", response.data)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login" data-interaction-form>', response.data)
        self.assertIn(b"ENTER", response.data)

    def test_seeded_kessler_login_is_case_insensitive_and_enters_rfd_hub(self):
        seed_dev_grandmaster(self.app)

        response = self.client.post(
            "/login",
            data={"email": " kessler@local.neoapps ", "password": LOCAL_SQLITE_FALLBACK_PASSWORD},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_logged_in_header_shows_last_name_only(self):
        seed_dev_grandmaster(self.app)
        user = User.query.filter_by(username="Kessler").first()
        user.first_name = "Khris"
        user.last_name = "Kessler"
        user.full_name = "Khris Kessler"
        db.session.commit()

        self.client.post(
            "/login",
            data={"username": "Kessler", "password": LOCAL_SQLITE_FALLBACK_PASSWORD},
        )
        response = self.client.get("/portal")
        html = response.data.decode()
        user_chip = html.split('aria-label="Logged in user"', 1)[1].split("</div>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("<strong>Kessler</strong>", user_chip)
        self.assertNotIn("Khris Kessler", user_chip)

    def test_logged_in_header_falls_back_when_last_name_is_missing(self):
        seed_dev_grandmaster(self.app)
        user = User.query.filter_by(username="Kessler").first()
        user.first_name = ""
        user.last_name = ""
        user.full_name = "Fallback Display"
        db.session.commit()

        self.client.post(
            "/login",
            data={"username": "Kessler", "password": LOCAL_SQLITE_FALLBACK_PASSWORD},
        )
        response = self.client.get("/portal")
        html = response.data.decode()
        user_chip = html.split('aria-label="Logged in user"', 1)[1].split("</div>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("<strong>Fallback Display</strong>", user_chip)

    def test_shared_desktop_shell_uses_compact_neofont_utility_controls(self):
        self._asset_test_login()
        for path, marker in [('/motherbrain', 'data-operational-topbar'), ('/neoermac', 'data-operational-topbar'),
                             ('/neosektor', 'data-operational-topbar'), ('/neoscorpion', 'data-operational-topbar'),
                             ('/rfd', 'data-gateway-shell-header')]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                bar = document(response).one('header', **{marker: None})
                self.assertEqual(bar.findall('img')[0].attrs['src'], '/static/images/icons/neoapps/inapp/neoapps-inapp-128.png')
                self.assertIn('Gateway', bar.text)
                self.assertIn('Sort', bar.text)
                self.assertEqual(len(bar.findall(**{'data-character-switcher': None})), 1)
                self.assertIn('KESSLER', bar.text)
                self.assertEqual(bar.one('form', action='/logout').attrs['method'], 'post')

    def test_shared_desktop_character_menu_uses_compact_neofont_labels(self):
        seed_dev_grandmaster(self.app)
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": LOCAL_SQLITE_FALLBACK_PASSWORD},
        )

        response = self.client.get("/neoermac")
        menu = response.data.decode().split('aria-label="Accessible NeoNodes"', 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="character-switcher-label neo-menu-text"', menu)
        self.assertIn("MotherBrain", menu)
        self.assertIn("Scorpion", menu)

        css = stylesheet_source()
        self.assertIn("width: min(238px, calc(100vw - 32px));", css)
        self.assertIn("grid-template-columns: 20px minmax(0, 1fr) auto;", css)
        self.assertIn('.character-switcher-label {\n        min-width: 0;', css)
        self.assertIn('font-family: "NeoFont", Arial, sans-serif;', css)
        self.assertIn("font-size: 0.54rem;", css)

    def test_mobile_shell_renders_motherbrain_topbar_alerts_and_bottom_nav(self):
        self._asset_test_login()
        response = self.client.get('/motherbrain/manage-sort')
        root, drawer, dock = assert_mobile_drawer(self, response)
        header = root.one('header', **{'data-operational-mobile-header': None})
        self.assertEqual(header.one('img').attrs['src'], '/static/images/logos/newlogo_motherbrain_small.png')
        self.assertEqual('NeoMotherBrain', header.one('strong', 'neo-mobile-product-name').text.strip())
        self.assertEqual(len(header.findall(cls='motherbrain-alert-tray')), 1)
        self.assertEqual(dock.one('a').attrs['href'], '/motherbrain')
        self.assertEqual(drawer.one('form', action='/logout').attrs['method'], 'post')

    def test_mobile_motherbrain_landing_is_tile_menu_only(self):
        self._asset_test_login()
        response = self.client.get('/motherbrain/manage-sort')
        root, drawer, dock = assert_mobile_drawer(self, response)
        self.assertIn(b'data-motherbrain-mobile-nav', response.data)
        menu = drawer.one(**{'data-drawer-view': 'menu'})
        for label in ('Manage Sort', 'Arrival Planning', 'Departure Planning', 'Parking Plan',
                      'Parking Rules', 'Master Schedule', 'System Settings', 'Unmatched Queue'):
            self.assertIn(label, menu.text)
        for retired in ('Gateway Matrix', 'Sort Timeline', 'Manage API', 'Permission Rules'):
            self.assertNotIn(retired, menu.text)
        self.assertIn('NeoPortal', menu.text)

    def test_mobile_gateway_node_topbar_standard_order_and_account_menu(self):
        self._asset_test_login()
        user = User.query.filter_by(username='Kessler').one()
        user.first_name, user.last_name = 'Alpha', 'Zulu'
        db.session.commit()
        response = self.client.get('/motherbrain/manage-sort')
        root, drawer, dock = assert_mobile_drawer(self, response)
        header = root.one('header', **{'data-operational-mobile-header': None})
        identity = header.one('a', 'operational-mobile-identity')
        self.assertEqual(identity.attrs['href'], '/motherbrain')
        self.assertEqual(identity.one('img').attrs['src'], '/static/images/logos/newlogo_motherbrain_small.png')
        self.assertEqual(header.one('small').text.strip(), 'Manage Sort')
        self.assertEqual(drawer.one(cls='neo-drawer-user').text, user.header_display_name)
        self.assertEqual(drawer.one('form', action='/logout').attrs['method'], 'post')
        self.assertFalse(header.findall('details', 'mobile-account-menu'))

    def test_mobile_topbar_never_uses_ellipsis_for_page_titles(self):
        css = stylesheet_source()
        topbar_css = css[
            css.index("    .mobile-topbar {", css.index("@media (max-width: 760px) {", 20000)):
            css.index("    .mobile-account-menu {", css.index("@media (max-width: 760px) {", 20000))
        ]

        self.assertNotIn("text-overflow: ellipsis", topbar_css)
        self.assertIn(".mobile-topbar-page-name", topbar_css)
        self.assertIn("text-overflow: clip", topbar_css)
        self.assertIn(".mobile-topbar-title > span,", topbar_css)

    def test_mobile_gateway_preserves_launch_routes_and_shared_bottom_dock(self):
        self._asset_test_login()
        operation = SortDateOperation(sort_date=current_gateway_local_date(), gateway_code='RFD', sort_name='night')
        db.session.add(operation)
        db.session.commit()
        with patch('app.neomotherbrain.routes._current_sort_state', return_value={'operations': [operation]}):
            response = self.client.get(f'/rfd?operation_id={operation.id}')
        root, drawer, dock = assert_mobile_drawer(self, response)
        header = root.one('header', **{'data-gateway-mobile-header': None})
        self.assertEqual(header.one('img').attrs['src'], '/static/images/icons/neoapps/inapp/neoapps-inapp-128.png')
        self.assertEqual(header.one('small').text, 'RFD Operations')
        launcher = root.one(cls='gateway-node-launcher')
        self.assertEqual(launcher.one('a', 'gateway-launch-desktop').attrs['href'], f'/motherbrain?operation_id={operation.id}')
        self.assertEqual(launcher.one('a', 'gateway-launch-mobile').attrs['href'], f'/motherbrain/manage-sort?operation_id={operation.id}')
        for node in ('sektor', 'ermac', 'scorpion'):
            self.assertEqual(launcher.one('a', f'gateway-node-{node}').attrs['href'], f'/neo{node}?operation_id={operation.id}')
        self.assertEqual(dock.one('a').attrs['href'], '/rfd')
        self.assertFalse(root.findall(**{'data-operational-sidebar': None}))

    def test_desktop_gateway_landing_uses_compact_application_launcher(self):
        self._asset_test_login()
        response = self.client.get('/rfd')
        self.assertEqual(response.status_code, 200)
        root = document(response)
        launcher = root.one(cls='gateway-node-launcher')
        grid = launcher.one(cls='gateway-node-grid')
        cards = grid.findall(cls='gateway-node-card')
        self.assertEqual([c.attrs['class'].split()[1] for c in cards],
                         ['gateway-node-' + n for n in ('sektor', 'ermac', 'scorpion', 'rain', 'subzero', 'reptile')])
        self.assertEqual(len(launcher.findall(cls='gateway-node-motherbrain')), 2)
        self.assertEqual(len(grid.findall(cls='gateway-node-coming-soon')), 1)
        self.assertFalse(root.findall(**{'data-operational-sidebar': None}))
        self.assertIn(b'images/hero/hero_gateway_small.png', response.data)
        self.assertNotIn(b'rfd-node-column-left', response.data)

    def test_reptile_is_coming_soon_while_rain_and_subzero_launch(self):
        self._asset_test_login()
        response = self.client.get('/rfd')
        self.assertEqual(response.status_code, 200)
        grid = document(response).one(cls='gateway-node-grid')
        reptile = grid.one('article', 'gateway-node-reptile')
        self.assertEqual(reptile.attrs['aria-disabled'], 'true')
        self.assertIn('Coming Soon', reptile.text)
        self.assertFalse(reptile.findall('a'))
        self.assertFalse(reptile.findall('button'))
        for node in ('reptile', 'rain', 'subzero'):
            card = grid.one(cls=f'gateway-node-{node}')
            self.assertEqual(card.one('img').attrs['src'], f'/static/images/logos/newlogo_{node}_small.png')
        for node in ('rain', 'subzero'):
            self.assertEqual(grid.one('a', f'gateway-node-{node}').attrs['href'], '/neosubzero/' if node == 'subzero' else '/neorain')

    def test_mobile_portal_header_is_icon_only(self):
        self._asset_test_login()
        response = self.client.get('/portal')
        root, drawer, dock = assert_mobile_drawer(self, response)
        bar = root.one('header', **{'data-mobile-topbar': None})
        identity = bar.one('a', **{'aria-label': 'NeoApps Portal'})
        self.assertEqual(identity.attrs['href'], '/portal')
        self.assertEqual(identity.one('img').attrs['src'], '/static/images/icons/neoapps/inapp/neoapps-inapp-128.png')
        self.assertEqual(identity.text.strip(), '')
        self.assertFalse(identity.findall('strong'))

    def test_motherbrain_mobile_short_titles_and_full_menu_labels(self):
        self._asset_test_login()
        for path, title in (('/motherbrain/parking-plan', 'Parking Plan'),
                            ('/motherbrain/gateway-matrix', 'Gateway Matrix'),
                            ('/motherbrain/parking-rules', 'Parking Rules')):
            with self.subTest(path=path):
                response = self.client.get(path)
                root, drawer, dock = assert_mobile_drawer(self, response)
                header = root.one('header', **{'data-operational-mobile-header': None})
                self.assertEqual(header.one('small').text, title)
                menu = drawer.one(**{'data-drawer-view': 'menu'})
                for label in ('Manage Sort', 'Arrival Planning', 'Departure Planning', 'Parking Plan',
                              'Parking Rules', 'Master Schedule', 'System Settings', 'Unmatched Queue'):
                    self.assertIn(label, menu.text)
                self.assertIn('NeoPortal', menu.text)

    def test_mobile_nodes_and_menu_share_one_drawer(self):
        self._asset_test_login()
        response = self.client.get('/neosektor')
        root, drawer, dock = assert_mobile_drawer(self, response)
        nodes = drawer.one(**{'data-drawer-view': 'nodes'})
        menu = drawer.one(**{'data-drawer-view': 'menu'})
        self.assertTrue(nodes.findall('a', 'neo-drawer-node-link'))
        self.assertFalse(menu.findall('a', 'neo-drawer-node-link'))
        self.assertFalse(nodes.findall('form'))
        self.assertEqual(drawer.one('button', **{'data-drawer-close': None}).attrs['type'], 'button')
        self.assertEqual(len(root.findall(**{'data-drawer-backdrop': None})), 1)
        self.assertNotIn(b'data-mobile-popover-trigger', response.data)
        self.assertIn(b'js/mobile_drawer.js', response.data)

    def test_global_press_feedback_styles_and_hook_render(self):
        response = self.client.get("/")
        html = response.data.decode()
        css = stylesheet_source()

        self.assertEqual(response.status_code, 200)
        self.assertIn("@keyframes neo-press-feedback", css)
        self.assertIn(".is-press-feedback", css)
        self.assertIn("-webkit-tap-highlight-color: rgba(var(--node-highlight-rgb, 77, 183, 255), 0.22);", css)
        self.assertIn(".mobile-bottom-nav-button", css)
        self.assertIn(".portal-app-card", css)
        self.assertIn("const pressableSelector", html)
        self.assertIn("flashPressFeedback", html)
        self.assertIn("is-press-feedback", html)
        self.assertIn(".mobile-topbar-node-icon-link", html)
        self.assertIn(".mobile-topbar-page-link", html)

    def test_neobid_theme_stays_blue(self):
        css = stylesheet_source()
        manifest = self.client.get("/manifest/neobid.webmanifest").get_json()

        self.assertIn("--node-bid-primary: #4db7ff;", css)
        self.assertIn("--node-bid-highlight: #c8f4ff;", css)
        self.assertEqual(manifest["theme_color"], "#4db7ff")

    def test_portal_branding_uses_red_purple_without_pink(self):
        css = stylesheet_source()
        manifest = self.client.get("/manifest/neoportal.webmanifest").get_json()

        self.assertIn("--node-portal-primary: #d9362e;", css)
        self.assertIn("--node-portal-secondary: #5a2db8;", css)
        self.assertIn("--node-portal-highlight: #8b5cf6;", css)
        self.assertIn("--node-apps-primary: #d9362e;", css)
        self.assertIn("--node-apps-secondary: #5a2db8;", css)
        self.assertIn("--node-apps-highlight: #8b5cf6;", css)
        self.assertIn(".portal-shell-page .topbar", css)
        self.assertIn(".portal-page .action-button", css)
        self.assertIn("rgba(8, 7, 12, 0.99)", css)
        self.assertIn("linear-gradient(180deg, #7b121b 0%, #3b143a 100%)", css)
        self.assertNotIn("#d73f7d", css)
        self.assertNotIn("#ff75b7", css)
        self.assertNotIn("215, 63, 125", css)
        self.assertNotIn("255, 117, 183", css)
        self.assertEqual(manifest["theme_color"], "#d9362e")

    def test_mobile_duplicate_neosektor_body_title_is_hidden_by_css(self):
        css = stylesheet_source()

        self.assertIn(
            "body.mobile-app-chrome .mobile-shell-duplicate-title {\n"
            "        display: none !important;",
            css,
        )
        self.assertIn(
            "body.mobile-app-chrome .neosektor-standalone-header.app-header {\n"
            "        display: none;",
            css,
        )

    def test_mobile_account_moves_into_drawer_and_preserves_node_icons(self):
        self._asset_test_login()
        user = User.query.filter_by(username='Kessler').one()
        user.first_name, user.last_name = 'Alpha', 'Zulu'
        db.session.commit()
        for path, icon in (('/motherbrain/manage-sort', 'motherbrain'), ('/neoermac', 'ermac'), ('/neosektor', 'sektor')):
            with self.subTest(path=path):
                response = self.client.get(path)
                root, drawer, dock = assert_mobile_drawer(self, response)
                header = root.one('header', **{'data-operational-mobile-header': None})
                self.assertEqual(header.one('img').attrs['src'], f'/static/images/logos/newlogo_{icon}_small.png')
                self.assertEqual(drawer.one(cls='neo-drawer-user').text, user.header_display_name)
                self.assertFalse(header.findall(cls='mobile-account-trigger'))

    def test_portal_account_uses_shared_drawer_without_second_avatar_menu(self):
        self._asset_test_login()
        response = self.client.get('/portal')
        root, drawer, dock = assert_mobile_drawer(self, response)
        self.assertEqual(dock.one('a').attrs['href'], '/portal')
        self.assertEqual(drawer.one(cls='neo-drawer-user').text, 'Kessler')
        self.assertEqual(drawer.one('form', action='/logout').attrs['method'], 'post')
        self.assertFalse(root.findall(cls='mobile-account-trigger'))

    def test_public_pages_do_not_render_authenticated_mobile_shell(self):
        response = self.client.get("/")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("data-mobile-topbar", html)
        self.assertNotIn("data-mobile-bottom-nav", html)

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
            data={"username": "Kessler", "password": LOCAL_SQLITE_FALLBACK_PASSWORD},
            follow_redirects=False,
        )

        self.assertEqual(login_response.status_code, 302)

        motherbrain_home = self.client.get("/motherbrain", follow_redirects=False)
        self.assertEqual(motherbrain_home.status_code, 200)
        self.assertIn(b"data-motherbrain-dashboard", motherbrain_home.data)

        direct_paths = (
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
        self.assertEqual(hub_response.status_code, 200)
        root, drawer, dock = assert_mobile_drawer(self, hub_response)
        launcher = root.one(cls='gateway-node-launcher')
        for node in ('motherbrain', 'sektor', 'ermac', 'scorpion', 'rain', 'subzero'):
            self.assertTrue(launcher.findall('a', f'gateway-node-{node}'))
        menu = drawer.one(**{'data-drawer-view': 'menu'})
        self.assertEqual(menu.one('form', action='/logout').attrs['method'], 'post')
        self.assertFalse(menu.findall(cls='gateway-node-card'))
        for label in ('Master Schedule', 'User Management', 'Access Requests'):
            self.assertNotIn(label, menu.text)


if __name__ == "__main__":
    unittest.main()
