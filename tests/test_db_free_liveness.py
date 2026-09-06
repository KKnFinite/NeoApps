"""No external database, user fixture, or integration is needed for liveness."""
import importlib
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from flask import Flask

from app import create_app
from app.config import Config
from app.extensions import db, login_manager


class ProductionConfig:
    TESTING = False
    NEOAPPS_ENV = "production"
    SECRET_KEY = "isolated-production-startup-secret-0123456789"
    APP_BASE_URL = "https://neoapps.example.test"
    SQLALCHEMY_DATABASE_URI = "postgresql://neo@example.invalid/never-connect"
    AUTO_BOOTSTRAP_DATABASE = True  # The config flag alone must not bootstrap.


class DatabaseFreeLivenessTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.blocked = []
        for target in (
            "psycopg2.connect",
            "sqlalchemy.engine.Engine.connect",
            "sqlalchemy.engine.Connection.execute",
            "sqlalchemy.orm.Session.connection",
            "sqlalchemy.orm.Session.execute",
            "app.maybe_auto_bootstrap_database",
            "app.services.schema_sync.sync_database_schema",
            "app.services.schema_sync.sync_local_sqlite_schema",
            "app.services.database_startup_retry.run_startup_database_action",
            "app.services.neoermac_door_pull_schema.ensure_neoermac_door_pull_legacy_defaults",
            "app.services.neoscorpion_spear_schema.ensure_neoscorpion_spear_schema_compatibility",
            "app.services.google_rain_integration_schema.ensure_google_rain_integration_mode_column",
            "app.services.neorain_fuel_authority_schema.ensure_neorain_fuel_authority_schema",
        ):
            self.blocked.append(self.stack.enter_context(patch(
                target, side_effect=AssertionError(f"Unexpected database/startup work: {target}"),
            )))
        self.loader = self.stack.enter_context(patch.object(
            login_manager, "_user_callback", side_effect=AssertionError("Unexpected user load"),
        ))

    def assert_no_work(self):
        for blocked in self.blocked:
            blocked.assert_not_called()
        self.loader.assert_not_called()

    def test_non_testing_postgres_factory_never_attempts_database_or_schema_work(self):
        app = create_app(ProductionConfig)
        self.assertIsInstance(app, Flask)
        self.assertFalse(app.testing)
        with app.app_context():
            self.assertTrue(db.engine.pool._pre_ping)
            self.assertEqual(app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"], {"connect_timeout": 5})
            db.engine.dispose()
        self.assert_no_work()

    def test_fresh_run_import_uses_real_non_testing_factory_without_database(self):
        previous = sys.modules.pop("run", None)
        try:
            for key in ("TESTING", "SECRET_KEY", "APP_BASE_URL", "SQLALCHEMY_DATABASE_URI", "AUTO_BOOTSTRAP_DATABASE"):
                self.stack.enter_context(patch.object(Config, key, getattr(ProductionConfig, key), create=True))
            with patch.dict("os.environ", {"NEOAPPS_ENV": "production"}):
                module = importlib.import_module("run")
            self.assertIsInstance(module.app, Flask)
            self.assertFalse(module.app.testing)
            with module.app.app_context():
                db.engine.dispose()
            self.assert_no_work()
        finally:
            sys.modules.pop("run", None)
            if previous is not None:
                sys.modules["run"] = previous

    def test_health_get_head_skip_all_request_guards_with_or_without_login_cookie(self):
        app = create_app(ProductionConfig)
        # Also fail on non-DB guard work, including CSRF and live-refresh lookup.
        guards = app.before_request_funcs[None]
        guards[1:] = [Mock(side_effect=AssertionError("Health ran an application guard")) for _ in guards[1:]]
        for logged_in in (False, True):
            client = app.test_client()
            if logged_in:
                # Signed fixture cookie only; no user creation/database setup.
                with client.session_transaction() as session:
                    session["_user_id"] = "123"
                    session["_fresh"] = True
                    session["auth_session_version"] = -1
            for method in ("GET", "HEAD"):
                response = client.open("/healthz", method=method)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"ok" if method == "GET" else b"")
                self.assertEqual(response.mimetype, "text/plain")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertNotIn("Set-Cookie", response.headers)
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertIn("Content-Security-Policy", response.headers)
                self.assertIn("Strict-Transport-Security", response.headers)
                for guard in guards[1:]:
                    guard.assert_not_called()
                self.assert_no_work()
        with app.app_context():
            db.engine.dispose()

    def test_health_exemption_does_not_include_private_qr_or_application_routes(self):
        app = create_app(ProductionConfig)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "123"
        for path in ("/portal", "/share/neoapps-qr.svg"):
            # Normal production error behavior remains intact, not anonymous access.
            response = client.get(path)
            self.assertEqual(response.status_code, 500)
        self.assertEqual(self.loader.call_count, 2)
        with app.app_context():
            db.engine.dispose()
