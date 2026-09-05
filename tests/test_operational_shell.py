import unittest
from pathlib import Path

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class OperationalShellTest(unittest.TestCase):
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
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.user = User(username="operational-shell", role="grandmaster")
        set_user_password(self.user, "TestPassword123!")
        db.session.add(self.user)
        db.session.flush()
        backfill_default_gateway_node_roles(self.user, role="grandmaster")
        db.session.add(
            PortalAppAccess(
                user_id=self.user.id,
                app_code="neostaffing",
                status="approved",
                role="grandmaster",
                is_active=True,
            )
        )
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "operational-shell", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_operational_nodes_render_shared_header_sidebar_and_mobile_drawer(self):
        for path, node in (
            ("/motherbrain", b"NeoMotherBrain"),
            ("/neosektor", b"NeoSektor"),
            ("/neoermac", b"NeoErmac"),
            ("/neoscorpion", b"NeoScorpion"),
            ("/neorain/inbound", b"NeoRain"),
            ("/neosubzero/pretreat", b"NeoSub-Zero"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-operational-shell", response.data)
                self.assertIn(b"data-operational-topbar", response.data)
                self.assertIn(b"data-operational-sidebar", response.data)
                self.assertIn(b"data-operational-mobile-header", response.data)
                self.assertIn(b"operational-mobile-bottom-nav", response.data)
                self.assertIn(b"NeoGateway", response.data)
                self.assertIn(b"NeoPortal", response.data)
                self.assertIn(node, response.data)

    def test_operational_shell_uses_locked_logos_and_board_opt_in(self):
        response = self.client.get("/neoscorpion/fuel-dispatch")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"images/logos/newlogo_scorpion.png", response.data)
        self.assertIn(b"data-operational-board-toggle", response.data)
        self.assertIn(b"js/operational_shell.js", response.data)
        self.assertNotIn(b"data-dispatch-board-toggle", response.data)
        drawer = response.data.split(b'data-mobile-drawer', 1)[1].split(b'</aside>', 1)[0]
        self.assertNotIn(b'data-operational-board-toggle', drawer)
        mobile_template = Path(self.app.root_path, 'templates', 'neonodes', '_operational_mobile_shell.html').read_text(encoding='utf-8')
        self.assertNotIn('operational-board-exit', mobile_template)

        settings = self.client.get("/neoscorpion/settings")
        self.assertEqual(settings.status_code, 200)
        self.assertNotIn(b"data-operational-board-toggle", settings.data)

    def test_shared_mobile_shell_uses_safe_area_for_header_content_and_drawer(self):
        css = Path(self.app.root_path, "static", "css", "base.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("--neo-safe-top: env(safe-area-inset-top, 0px)", css)
        self.assertIn("--operational-mobile-header-height:calc(var(--operational-mobile-controls-height) + var(--neo-safe-top))", css)
        self.assertIn("padding:calc(7px + var(--neo-safe-top))", css)
        drawer_css = Path(self.app.root_path, "static", "css", "mobile_drawer.css").read_text(encoding="utf-8")
        self.assertIn("padding:calc(12px + var(--neo-safe-top))", drawer_css)
        self.assertIn("padding:calc(var(--operational-mobile-header-height) + 14px)", css)
        script = Path(self.app.root_path, "static", "js", "mobile_drawer.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const headerPopoverOpen", script)
        self.assertIn("operational-mobile-header-hidden", script)

    def test_staffing_is_not_marked_as_operational_shell(self):
        for path in ("/neostaffing", "/rfd", "/portal"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"data-operational-shell", response.data)
                self.assertNotIn(b"data-operational-mobile-header", response.data)

    def test_sidebar_reuses_permission_filtered_node_menu(self):
        watcher = User(username="operational-watcher", role="watcher")
        set_user_password(watcher, "TestPassword123!")
        db.session.add(watcher)
        db.session.flush()
        backfill_default_gateway_node_roles(watcher, role="watcher")
        db.session.commit()

        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"username": "operational-watcher", "password": "TestPassword123!"},
        )
        response = self.client.get("/neoscorpion")
        sidebar = response.data.split(b"data-operational-sidebar", 1)[1].split(b"</aside>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Fuel Dispatch", sidebar)
        self.assertIn(b'href="/neoscorpion/settings"', sidebar)
