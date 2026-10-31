from tests.css_contracts import stylesheet_source
import re
import os
import runpy
import sys
from types import ModuleType
from pathlib import Path
import unittest
from unittest.mock import patch

from flask import render_template_string

from app import create_app
from app.config import runtime_environment
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

        @self.app.get('/_security-probe')
        def probe():
            return render_template_string('''
                <script nonce="{{ csp_nonce() }}">window.trusted=true;</script>
                {{ untrusted|safe }}
            ''', untrusted='<script id="untrusted">window.untrusted=true;</script>'
                '<button onclick="window.injected=true">Untrusted</button>')

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
            stylesheet_source(),
        )
        self.assertNotIn("style=", fuel_dispatch)
        summary = (root / "app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html").read_text(encoding="utf-8")
        self.assertIn('<progress class="neoscorpion-truck-gauge', summary)
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

        # An attacker-controlled Host/forwarded scheme cannot enable a
        # production environment on a local app.
        spoofed = local_app.test_client().get('/healthz', base_url='https://neoapps.onrender.com',
            headers={'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'neoapps.onrender.com'})
        self.assertNotIn('Strict-Transport-Security', spoofed.headers)

    def test_render_runtime_without_optional_environment_names_enforces_headers(self):
        with patch.dict(os.environ, {'RENDER': 'true', 'NEOAPPS_ENV': '', 'FLASK_ENV': ''}):
            environment = runtime_environment()
        self.assertEqual(environment, 'production')
        # Reproduce run.py's production path, rather than relying only on an
        # explicitly production-labelled test config as the original test did.
        self.app.config['NEOAPPS_ENV'] = environment
        for path in ('/login', '/healthz', '/not-a-route'):
            response = self.client.get(path)
            self.assertIn('Content-Security-Policy', response.headers)
            self.assertNotIn('Content-Security-Policy-Report-Only', response.headers)
            self.assertEqual(response.headers['Strict-Transport-Security'],
                             'max-age=31536000; includeSubDomains')

    def test_run_entrypoint_uses_the_same_runtime_environment_resolver(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'run.py').read_text(encoding='utf-8')
        self.assertIn('NEOAPPS_ENV = runtime_environment()', source)
        with patch.dict(os.environ, {'RENDER': 'false', 'NEOAPPS_ENV': 'development'}):
            self.assertEqual(runtime_environment(), 'development')

    def test_actual_wsgi_entrypoint_on_render_enables_headers_without_database_startup(self):
        root = Path(__file__).resolve().parents[1]
        observed = []

        def isolated_factory(runtime_config, **kwargs):
            observed.append(runtime_config.NEOAPPS_ENV)
            config = type('RenderFixture', (runtime_config,), {
                'TESTING': True, 'SECRET_KEY': 'isolated-render-fixture-secret-0123456789',
                'APP_BASE_URL': 'https://neoapps.onrender.com',
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'SQLALCHEMY_TRACK_MODIFICATIONS': False,
            })
            return create_app(config, **kwargs)

        # Import the real WSGI entrypoint. Prevent background loop startup, as
        # Gunicorn does before post_worker_init; health requires no DB tables.
        with patch.dict(os.environ, {'RENDER': 'true', 'NEOAPPS_ENV': '', 'FLASK_ENV': ''}), \
                patch.dict(sys.modules, {'gunicorn': ModuleType('gunicorn')}), \
                patch('app.create_app', side_effect=isolated_factory):
            runtime = runpy.run_path(str(root / 'run.py'), run_name='isolated_wsgi')
        response = runtime['app'].test_client().get('/healthz')
        self.assertEqual(observed, ['production'])
        self.assertEqual(response.status_code, 200)
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertEqual(response.headers['Strict-Transport-Security'],
                         'max-age=31536000; includeSubDomains')

    def test_each_response_nonce_is_fresh_and_only_explicit_template_scripts_receive_it(self):
        nonces = []
        for _ in range(3):
            response = self.client.get('/_security-probe')
            nonce = re.search(r"'nonce-([^']+)'", response.headers['Content-Security-Policy'])[1]
            nonces.append(nonce)
            self.assertIn(f'<script nonce="{nonce}">'.encode(), response.data)
            self.assertIn(b'<script id="untrusted">', response.data)
            self.assertNotIn(f'nonce="{nonce}" id="untrusted"'.encode(), response.data)
            self.assertIn("script-src-attr 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual(len(set(nonces)), 3)

    def test_owned_templates_have_no_inline_handlers_styles_or_untrusted_script_grants(self):
        root = Path(__file__).resolve().parents[1] / 'app/templates'
        for path in root.rglob('*.html'):
            source = path.read_text(encoding='utf-8')
            with self.subTest(template=str(path.relative_to(root))):
                self.assertNotRegex(source, r'<[^>]+\s(?:on\w+|style)\s*=')
                self.assertNotRegex(source, r'<style\b')
                for match in re.finditer(r'<script\b[^>]*>', source):
                    if not re.search(r'\bsrc\s*=', match[0]):
                        self.assertIn('nonce="{{ csp_nonce() }}"', match[0])

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
