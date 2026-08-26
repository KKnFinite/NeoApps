import re
from pathlib import Path
import unittest

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, User
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password


class SecurityHeadersTest(unittest.TestCase):
    def setUp(self):
        ProductionConfig = type(
            "ProductionConfig",
            (),
            {
                "SECRET_KEY": "security-headers-test-secret-key-with-enough-length",
                "TESTING": True,
                "NEOAPPS_ENV": "production",
                "APP_BASE_URL": "https://neoapps.example.test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(ProductionConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_production_https_response_has_hsts_csp_and_existing_security_headers(self):
        response = self.client.get(
            "/login",
            base_url="https://neoapps.example.test",
        )
        policy = response.headers["Content-Security-Policy"]
        nonce_match = re.search(r"script-src 'self' 'nonce-([^']+)'", policy)

        self.assertEqual(
            response.headers["Strict-Transport-Security"],
            "max-age=31536000; includeSubDomains",
        )
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'self'", policy)
        self.assertIn("frame-ancestors 'self'", policy)
        self.assertIn("form-action 'self'", policy)
        self.assertIn("style-src-attr 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertIsNotNone(nonce_match)
        self.assertIn(
            f'<script nonce="{nonce_match.group(1)}">'.encode(),
            response.data,
        )
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(
            response.headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertEqual(
            response.headers["Permissions-Policy"],
            "camera=(), microphone=(), geolocation=()",
        )

    def test_confirmed_inline_style_conflicts_use_csp_safe_markup_and_state(self):
        root = Path(__file__).resolve().parents[1]
        shift_flow = (root / "app/templates/neostaffing/shift_flow.html").read_text(
            encoding="utf-8"
        )
        fuel_dispatch = (
            root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html"
        ).read_text(encoding="utf-8")
        copy_script = (
            root / "app/static/js/neoscorpion_fuel_assignments_copy.js"
        ).read_text(encoding="utf-8")
        live_updates = (root / "app/static/js/live_updates.js").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("style=", shift_flow)
        self.assertIn(
            "grid-auto-columns",
            (root / "app/static/css/base.css").read_text(encoding="utf-8"),
        )
        self.assertNotIn("style=", fuel_dispatch)
        self.assertIn('<progress class="neoscorpion-truck-gauge', fuel_dispatch)
        self.assertNotIn(".style.", copy_script)
        self.assertIn('input.className = "neoscorpion-copy-fallback"', copy_script)
        self.assertIn("replacement.hidden = current.hidden", live_updates)
        self.assertNotIn("replacement.style.display", live_updates)

    def test_local_http_does_not_enable_hsts_or_csp_by_default(self):
        DevelopmentConfig = type(
            "DevelopmentConfig",
            (),
            {
                "SECRET_KEY": "security-headers-test-secret-key-with-enough-length",
                "TESTING": True,
                "NEOAPPS_ENV": "development",
                "APP_BASE_URL": "http://127.0.0.1:5000",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        local_app = create_app(DevelopmentConfig)
        response = local_app.test_client().get("/login")

        self.assertNotIn("Strict-Transport-Security", response.headers)
        self.assertNotIn("Content-Security-Policy", response.headers)

    def test_production_hsts_requires_https_app_base_url(self):
        InsecureProductionConfig = type(
            "InsecureProductionConfig",
            (),
            {
                "SECRET_KEY": "security-headers-test-secret-key-with-enough-length",
                "TESTING": True,
                "NEOAPPS_ENV": "production",
                "APP_BASE_URL": "http://neoapps.example.test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )

        with self.assertRaisesRegex(RuntimeError, "production HSTS requires"):
            create_app(InsecureProductionConfig)

    def test_representative_portal_gateway_auth_and_staffing_pages_render_with_csp(self):
        user = self._approved_user("headers_operator")
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            base_url="https://neoapps.example.test",
            follow_redirects=False,
        )

        for path in ("/portal", "/rfd", "/neosektor", "/neostaffing"):
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    base_url="https://neoapps.example.test",
                )
                self.assertEqual(response.status_code, 200)
                self.assertIn("Content-Security-Policy", response.headers)
                self.assertIn(b'<script nonce="', response.data)
                self.assertNotIn(b"<script>", response.data)

    def _approved_user(self, username):
        user = User(
            username=username,
            email=f"{username}@example.test",
            role="grandmaster",
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role="grandmaster",
                is_active=True,
            )
        )
        db.session.flush()
        return user
