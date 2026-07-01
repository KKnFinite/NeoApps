from datetime import date
import importlib
from pathlib import Path
import unittest

from flask import Flask

from app import create_app
from app.extensions import db
from app.models import SortDateOperation, User
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

    def test_default_gateway_branding_config_preserves_rfd_context_with_neogateway_logo(self):
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_CODE"], "RFD")
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_NAME"], "NeoGateway")
        self.assertEqual(self.app.config["DEFAULT_GATEWAY_LOGO"], "images/neogateway_logo3_small.png")
        self.assertIn("STATIC_ASSET_VERSION", self.app.config)
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(self.app.config["REMEMBER_COOKIE_SAMESITE"], "Lax")
        self.assertTrue(self.app.config["REMEMBER_COOKIE_HTTPONLY"])

    def test_default_neogateway_logo_asset_exists_with_render_safe_casing(self):
        logo_path = Path("app/static/images/neogateway_logo3_small.png")

        self.assertTrue(logo_path.is_file())
        self.assertEqual(logo_path.name, "neogateway_logo3_small.png")
        self.assertGreater(logo_path.stat().st_size, 0)

    def test_base_css_uses_cyber_topbar_without_vertical_grid_background(self):
        css = Path("app/static/css/base.css").read_text()

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
        self.assertNotIn("../images/neobutton1_medium.png", css)
        self.assertIn(".rfd-node-prefix", css)
        self.assertIn(".rfd-node-suffix", css)
        self.assertIn(".rfd-gateway-brand-strip", css)
        self.assertIn(".rfd-gateway-brand-icon", css)
        self.assertIn(".rfd-gateway-brand-title", css)
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
        css = Path("app/static/css/base.css").read_text()

        self.assertIn("-webkit-text-size-adjust: 100%;", css)
        self.assertIn("text-size-adjust: 100%;", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("font-size: max(16px, 1rem);", css)
        self.assertIn("touch-action: manipulation;", css)

    def test_base_template_cache_busts_stylesheet(self):
        template = Path("app/templates/base.html").read_text()

        self.assertIn("filename='css/base.css', v=config.STATIC_ASSET_VERSION", template)
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
                "/nodes/",
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
                "/nodes/",
                "#f4c21f",
                [
                    ("/static/images/icons/neoscorpion/pwa/icon-192x192.png", "192x192", "any"),
                    ("/static/images/icons/neoscorpion/pwa/icon-512x512.png", "512x512", "any"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-192x192.png", "192x192", "maskable"),
                    ("/static/images/icons/neoscorpion/pwa/maskable-icon-512x512.png", "512x512", "maskable"),
                ],
            ),
            "reptile": ("NeoReptile", "NeoReptile", "/nodes/", "#70e13b", None),
            "subzero": ("NeoSub-Zero", "Sub-Zero", "/nodes/", "#4db7ff", None),
            "rain": ("NeoRain", "NeoRain", "/nodes/", "#7f4dff", None),
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
        self.assertIn("neogateway-static-v20260623-3", service_worker)
        self.assertIn("/static/css/base.css?v=20260623-3", service_worker)
        self.assertIn('request.mode === "navigate"', service_worker)
        self.assertIn('event.respondWith(fetch(request, { cache: "no-store" }));', service_worker)
        self.assertIn("caches.delete(cacheName)", service_worker)
        self.assertNotIn('caches.match(request))', service_worker.split('request.mode === "navigate"', 1)[1].split('if (!requestUrl.pathname.startsWith("/static/"))', 1)[0])
        self.assertNotIn("/neoermac/door-view", service_worker)
        self.assertIn("/static/images/neogateway_logo3_small.png", service_worker)
        self.assertIn("/static/images/neogateway_logo3_medium.png", service_worker)
        self.assertIn("/static/images/neogateway_logo3_large.png", service_worker)
        self.assertNotIn("NeoRFD", service_worker)
        self.assertNotIn("neorfd", service_worker.lower())

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
        button_path = Path("app/static/images/neobutton1_medium.png")

        self.assertTrue(button_path.is_file())
        self.assertEqual(button_path.name, "neobutton1_medium.png")
        self.assertGreater(button_path.stat().st_size, 0)

    def test_public_home_uses_enter_login_form_without_separate_login_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"portal-brand-logo portal-login-logo", response.data)
        self.assertIn(b'src="/static/images/neoapps_logo_transparent.png"', response.data)
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
        self.assertNotIn(b"Change Characters", response.data)
        self.assertNotIn(b"NeoSektor", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b'href="https://neosektor.onrender.com/"', response.data)
        self.assertNotIn(b'src="/static/images/neosektor_logo1.png"', response.data)

    def test_login_route_is_reachable(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"portal-brand-logo portal-login-logo", response.data)
        self.assertIn(b'src="/static/images/neoapps_logo_transparent.png"', response.data)
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
            data={"username": "Kessler", "password": "1313"},
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
            data={"username": "Kessler", "password": "1313"},
        )
        response = self.client.get("/portal")
        html = response.data.decode()
        user_chip = html.split('aria-label="Logged in user"', 1)[1].split("</div>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("<strong>Fallback Display</strong>", user_chip)

    def test_mobile_shell_renders_motherbrain_topbar_alerts_and_bottom_nav(self):
        seed_dev_grandmaster(self.app)
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
        )

        response = self.client.get("/motherbrain")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="mobile-topbar node-motherbrain"', html)
        self.assertIn("data-mobile-bottom-nav", html)
        self.assertIn("data-mobile-alert-nav", html)
        self.assertIn("data-mobile-shell-menu-button", html)
        self.assertIn("account-motherbrain-128.png", html)
        self.assertIn("Back to", html)
        self.assertIn("neo-brand--apps", html)
        self.assertNotIn("neo-brand--portal", html)
        self.assertIn('href="/logout"', html)
        self.assertIn("<span>Home</span>", html)
        self.assertIn("<span>Alerts</span>", html)
        self.assertIn("<span>Switch</span>", html)
        self.assertIn("<span>Menu</span>", html)

    def test_mobile_bottom_popovers_anchor_to_switch_and_menu_buttons(self):
        seed_dev_grandmaster(self.app)
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
        )

        response = self.client.get("/neosektor")
        html = response.data.decode()
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-mobile-popover-trigger="switch"', html)
        self.assertIn('data-mobile-popover-anchor="switch"', html)
        self.assertIn("data-mobile-switcher-button", html)
        self.assertIn('aria-expanded="false" data-mobile-switcher-button', html)
        self.assertIn("mobile-bottom-popover--switch", html)
        self.assertIn('data-mobile-popover-trigger="menu"', html)
        self.assertIn('data-mobile-popover-anchor="menu"', html)
        self.assertIn('aria-controls="mobile-bottom-menu-panel"', html)
        self.assertIn('id="mobile-bottom-menu-panel"', html)
        self.assertIn("mobile-bottom-popover--menu", html)
        self.assertIn("mobile-bottom-menu-panel mobile-shell-menu-panel", html)
        self.assertIn("Back to", html)
        self.assertIn("neo-brand--apps", html)
        self.assertNotIn("neo-brand--portal", html)
        self.assertIn('src="/static/images/icons/neoapps/inapp/neoapps-inapp-128.png"', html)
        self.assertNotIn("images/icons/neoportal/icon_192.png", html)
        self.assertIn("mobile-bottom-switcher-label neo-menu-text", html)
        self.assertIn("@keyframes mobile-bottom-pop", css)
        self.assertIn("@keyframes mobile-bottom-pop-close", css)
        self.assertIn(".mobile-bottom-popover.is-opening", css)
        self.assertIn(".mobile-bottom-popover.is-closing", css)
        self.assertIn("transform-origin: var(--mobile-popover-origin-x, calc(100% - 24px)) 100%;", css)
        self.assertIn("--mobile-popover-origin-x: 50%;", css)
        self.assertIn("--mobile-popover-origin-x: calc(100% - 24px);", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", css)
        self.assertIn(".mobile-bottom-menu-panel.is-open", css)

    def test_global_press_feedback_styles_and_hook_render(self):
        response = self.client.get("/")
        html = response.data.decode()
        css = Path("app/static/css/base.css").read_text()

        self.assertEqual(response.status_code, 200)
        self.assertIn("@keyframes neo-press-feedback", css)
        self.assertIn(".is-press-feedback", css)
        self.assertIn("-webkit-tap-highlight-color: rgba(var(--node-highlight-rgb, 77, 183, 255), 0.22);", css)
        self.assertIn(".mobile-bottom-nav-button", css)
        self.assertIn(".portal-app-card", css)
        self.assertIn("const pressableSelector", html)
        self.assertIn("flashPressFeedback", html)
        self.assertIn("is-press-feedback", html)

    def test_neobid_theme_stays_blue(self):
        css = Path("app/static/css/base.css").read_text()
        manifest = self.client.get("/manifest/neobid.webmanifest").get_json()

        self.assertIn("--node-bid-primary: #4db7ff;", css)
        self.assertIn("--node-bid-highlight: #c8f4ff;", css)
        self.assertEqual(manifest["theme_color"], "#4db7ff")

    def test_portal_branding_uses_red_purple_without_pink(self):
        css = Path("app/static/css/base.css").read_text()
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
        css = Path("app/static/css/base.css").read_text()

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

    def test_mobile_account_icon_mapping_uses_node_specific_128px_assets(self):
        seed_dev_grandmaster(self.app)
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
        )

        expected_icons = {
            "/motherbrain": "account-motherbrain-128.png",
            "/neoermac": "ninja-ermac-128.png",
            "/neosektor": "ninja-sektor-128.png",
        }

        for path, icon_name in expected_icons.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                html = response.data.decode()

                self.assertEqual(response.status_code, 200)
                self.assertIn("data-mobile-topbar", html)
                self.assertIn("data-mobile-bottom-nav", html)
                self.assertIn(icon_name, html)
                self.assertNotIn("-1024.png", html)
                icon_response = self.client.get(f"/static/images/account/{icon_name}")
                self.assertEqual(icon_response.status_code, 200)
                self.assertEqual(icon_response.mimetype, "image/png")

    def test_mobile_shell_uses_safe_fallback_account_avatar_on_portal(self):
        seed_dev_grandmaster(self.app)
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
        )

        response = self.client.get("/portal")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-mobile-topbar", html)
        self.assertIn("data-mobile-bottom-nav", html)
        self.assertIn("mobile-account-fallback", html)
        self.assertNotIn('role="menuitem">Back to', html)

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
        self.assertNotIn(b'neogateway_logo3_large.png', hub_response.data)
        self.assertNotIn(b'neogateway_logo3_medium.png', hub_response.data)
        self.assertNotIn(b'neogateway_logo3_small.png', hub_response.data)
        self.assertIn(b'class="rfd-gateway-brand-strip"', hub_response.data)
        self.assertIn(b'src="/static/images/icons/neogateway/inapp/neogateway-inapp-128.png"', hub_response.data)
        self.assertIn(b'src="/static/images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png"', hub_response.data)
        self.assertIn(b'src="/static/images/icons/neosektor/inapp/neosektor-icon-128x128.png"', hub_response.data)
        self.assertIn(b'src="/static/images/icons/neoermac/inapp/neoermac-inapp-128.png"', hub_response.data)
        self.assertIn(b'src="/static/images/icons/neoscorpion/inapp/neoscorpion-128x128.png"', hub_response.data)
        self.assertIn(b"NeoMotherBrain", hub_response.data)
        self.assertIn(b"NeoSektor", hub_response.data)
        self.assertIn(b'href="/neoermac"', hub_response.data)
        for node_name in (
            b"NeoScorpion",
            b"NeoReptile",
            b"NeoErmac",
            b"NeoSub-Zero",
            b"NeoRain",
        ):
            self.assertIn(node_name, hub_response.data)
        self.assertNotIn(b"Placeholder", hub_response.data)
        self.assertNotIn(b"Launch", hub_response.data)
        self.assertNotIn(b"Gateway Command Layer", hub_response.data)
        self.assertLess(hub_html.index('aria-label="NeoMotherBrain"'), hub_html.index('class="rfd-node-grid"'))
        self.assertLess(hub_html.index('rfd-node-column-left"'), hub_html.index('rfd-node-column-right"'))
        left_order = (
            "NeoSektor",
            "NeoReptile",
            "NeoRain",
        )
        right_order = (
            "NeoErmac",
            "NeoSub-Zero",
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
