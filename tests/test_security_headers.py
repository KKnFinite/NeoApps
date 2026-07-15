import re
import unittest

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, User
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


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
            data={"username": user.username, "password": "Password123!"},
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
        user.set_password("Password123!")
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
