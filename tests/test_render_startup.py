import importlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy.exc import OperationalError

from app import create_app
from app.services.database_bootstrap import bootstrap_database


class RenderStartupTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "RenderStartupTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": True,
                "DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING": 2,
                "DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS": 0,
                "DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS": 0,
            },
        )

    def test_factory_default_does_not_run_database_bootstrap(self):
        with patch("app.maybe_auto_bootstrap_database") as bootstrap:
            app = create_app(self.config)

        self.assertIsInstance(app, Flask)
        bootstrap.assert_not_called()

    def test_gunicorn_import_creates_app_without_worker_bootstrap(self):
        previous_run_module = sys.modules.pop("run", None)
        worker_app = Flask("neoapps-gunicorn-worker")
        try:
            with patch("app.create_app", return_value=worker_app) as factory:
                module = importlib.import_module("run")

            self.assertIs(module.app, worker_app)
            factory.assert_called_once()
            self.assertFalse(factory.call_args.kwargs["auto_bootstrap"])
        finally:
            sys.modules.pop("run", None)
            if previous_run_module is not None:
                sys.modules["run"] = previous_run_module

    def test_deploy_bootstrap_script_runs_once(self):
        from scripts import bootstrap_database as bootstrap_script

        result = {
            "username": "Kessler",
            "email": "bootstrap@example.com",
            "gateway_code": "RFD",
            "node_count": 8,
            "grandmaster_role_count": 8,
            "used_fallback_password": False,
            "password_applied": False,
        }
        app = Flask("deployment-bootstrap")
        with (
            patch.object(
                bootstrap_script,
                "create_deployment_bootstrap_app",
                return_value=app,
            ) as create_app,
            patch.object(bootstrap_script, "bootstrap_database", return_value=result) as bootstrap,
            patch.object(bootstrap_script, "_dispose_bootstrap_engine") as dispose,
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            bootstrap_script.main()

        create_app.assert_called_once_with()
        bootstrap.assert_called_once_with(app)
        dispose.assert_called_once_with(app)
        self.assertIn("phase 1/3", stdout.getvalue())
        self.assertIn("phase 3/3", stdout.getvalue())

    def test_manual_bootstrap_uses_bounded_postgres_timeouts(self):
        from scripts import bootstrap_database as bootstrap_script

        with patch.dict(
            "os.environ",
            {
                "DATABASE_URL": "postgresql://neo_user:password@example.test/neoapps",
                "DATABASE_BOOTSTRAP_CONNECT_TIMEOUT_SECONDS": "6",
                "DATABASE_BOOTSTRAP_LOCK_TIMEOUT_MILLISECONDS": "4000",
                "DATABASE_BOOTSTRAP_STATEMENT_TIMEOUT_MILLISECONDS": "12000",
                "DATABASE_BOOTSTRAP_RETRY_ATTEMPTS": "4",
            },
            clear=False,
        ):
            config = bootstrap_script.deployment_bootstrap_config()

        options = config.SQLALCHEMY_ENGINE_OPTIONS
        self.assertTrue(options["pool_pre_ping"])
        self.assertEqual(options["pool_timeout"], 6)
        self.assertEqual(options["connect_args"]["connect_timeout"], 6)
        self.assertIn("lock_timeout=4000ms", options["connect_args"]["options"])
        self.assertIn("statement_timeout=12000ms", options["connect_args"]["options"])
        self.assertEqual(config.DATABASE_STARTUP_RETRY_ATTEMPTS, 4)

    def test_manual_bootstrap_reports_and_propagates_schema_failure(self):
        from scripts import bootstrap_database as bootstrap_script

        app = Flask("deployment-bootstrap-failure")
        with (
            patch.object(
                bootstrap_script,
                "create_deployment_bootstrap_app",
                return_value=app,
            ),
            patch.object(
                bootstrap_script,
                "bootstrap_database",
                side_effect=RuntimeError("invalid schema"),
            ),
            patch.object(bootstrap_script, "_dispose_bootstrap_engine") as dispose,
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid schema"):
                bootstrap_script.main()

        dispose.assert_called_once_with(app)
        self.assertIn("bootstrap failed during schema synchronization", stderr.getvalue())

    def test_deploy_bootstrap_retries_transient_database_failure(self):
        app = create_app(self.config)
        result = {"username": "Kessler"}
        transient_error = OperationalError(
            "SELECT 1",
            {},
            RuntimeError("server closed the connection unexpectedly"),
        )

        with (
            patch(
                "app.services.database_bootstrap._bootstrap_database_once",
                side_effect=[transient_error, result],
            ) as bootstrap_once,
            patch("app.services.database_startup_retry._reset_database_connections") as reset_connections,
            patch("app.services.database_startup_retry.time.sleep"),
        ):
            self.assertEqual(bootstrap_database(app), result)

        self.assertEqual(bootstrap_once.call_count, 2)
        reset_connections.assert_called_once_with()

    def test_genuine_schema_error_fails_deploy_without_retry(self):
        app = create_app(self.config)
        schema_error = OperationalError(
            "ALTER TABLE staffing_people",
            {},
            RuntimeError("duplicate column name: employee_status"),
        )

        with (
            patch(
                "app.services.database_bootstrap._bootstrap_database_once",
                side_effect=schema_error,
            ) as bootstrap_once,
            patch("app.services.database_startup_retry._reset_database_connections") as reset_connections,
            patch("app.services.database_startup_retry.time.sleep") as sleep,
        ):
            with self.assertRaises(OperationalError):
                bootstrap_database(app)

        bootstrap_once.assert_called_once()
        reset_connections.assert_not_called()
        sleep.assert_not_called()

    def test_render_documentation_uses_separate_deploy_bootstrap_and_gunicorn_start(self):
        deployment_doc = Path("docs/deployment/render.md").read_text()
        procfile = Path("Procfile").read_text()

        self.assertIn("pip install -r requirements.txt", deployment_doc)
        self.assertIn("python scripts/bootstrap_database.py", deployment_doc)
        self.assertIn("gunicorn run:app --bind 0.0.0.0:$PORT", deployment_doc)
        self.assertNotIn(
            "pip install -r requirements.txt && python scripts/bootstrap_database.py",
            deployment_doc,
        )
        self.assertEqual(procfile.strip(), "web: gunicorn run:app --bind 0.0.0.0:$PORT")


if __name__ == "__main__":
    unittest.main()
