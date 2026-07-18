import unittest
from unittest.mock import Mock, patch

from sqlalchemy.exc import OperationalError

from app import create_app, sync_existing_local_schema
from app.extensions import db
from app.services.database_bootstrap import bootstrap_database
from app.services.database_startup_retry import (
    StartupDatabaseConnectionError,
    _reset_database_connections,
    run_startup_database_action,
)


class StartupDatabaseRetryTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "StartupDatabaseRetryTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
                "DATABASE_STARTUP_RETRY_ATTEMPTS_TESTING": 3,
                "DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS": 0,
                "DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS": 0,
            },
        )
        self.app = create_app(TestConfig, auto_bootstrap=False)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_transient_failure_retries_resets_pool_and_later_succeeds(self):
        events = []
        failures_remaining = 2
        self.app.config["DATABASE_STARTUP_RETRY_INITIAL_DELAY_SECONDS"] = 0.25
        self.app.config["DATABASE_STARTUP_RETRY_MAX_DELAY_SECONDS"] = 1

        def action():
            nonlocal failures_remaining
            events.append("action")
            if failures_remaining:
                failures_remaining -= 1
                raise self._transient_error()
            return "connected"

        with (
            patch(
                "app.services.database_startup_retry._reset_database_connections",
                side_effect=lambda: events.append("reset"),
            ) as reset_connections,
            patch(
                "app.services.database_startup_retry.time.sleep",
                side_effect=lambda _delay: events.append("sleep"),
            ) as sleep,
        ):
            result = run_startup_database_action(
                self.app,
                action,
                action_name="schema metadata inspection",
            )

        self.assertEqual(result, "connected")
        self.assertEqual(events, ["action", "reset", "sleep", "action", "reset", "sleep", "action"])
        self.assertEqual(reset_connections.call_count, 2)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            [0.25, 0.5],
        )

    def test_local_schema_startup_path_uses_the_shared_retry_wrapper(self):
        self.app.config["TESTING"] = False

        with (
            patch("app.services.schema_sync.sync_local_sqlite_schema") as sync_schema,
            patch(
                "app.services.database_startup_retry.run_startup_database_action"
            ) as run_with_retry,
        ):
            self.assertTrue(sync_existing_local_schema(self.app))

        sync_schema.assert_not_called()
        run_with_retry.assert_called_once()
        self.assertEqual(
            run_with_retry.call_args.kwargs["action_name"],
            "local schema synchronization",
        )

    def test_bootstrap_uses_retry_boundary_for_transient_schema_failure(self):
        result = {"username": "Kessler"}
        initialize = Mock(side_effect=[self._transient_error(), result])

        with (
            patch(
                "app.services.database_bootstrap._bootstrap_database_once",
                initialize,
            ),
            patch("app.services.database_startup_retry._reset_database_connections") as reset_connections,
            patch("app.services.database_startup_retry.time.sleep"),
        ):
            self.assertEqual(bootstrap_database(self.app), result)

        self.assertEqual(initialize.call_count, 2)
        reset_connections.assert_called_once_with()

    def test_transient_retries_stop_at_configured_limit(self):
        action = Mock(side_effect=self._transient_error())

        with (
            patch("app.services.database_startup_retry._reset_database_connections") as reset_connections,
            patch("app.services.database_startup_retry.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                StartupDatabaseConnectionError,
                "startup database initialization failed after 3 attempts",
            ):
                run_startup_database_action(
                    self.app,
                    action,
                    action_name="schema metadata inspection",
                )

        self.assertEqual(action.call_count, 3)
        self.assertEqual(reset_connections.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_schema_and_permission_errors_fail_immediately_without_retry(self):
        for message in (
            "duplicate column name: employee_status",
            "permission denied for table staffing_people",
        ):
            with self.subTest(message=message):
                action = Mock(
                    side_effect=OperationalError("ALTER TABLE", {}, RuntimeError(message))
                )
                with (
                    patch("app.services.database_startup_retry._reset_database_connections") as reset_connections,
                    patch("app.services.database_startup_retry.time.sleep") as sleep,
                ):
                    with self.assertRaises(OperationalError):
                        run_startup_database_action(
                            self.app,
                            action,
                            action_name="schema metadata inspection",
                        )

                action.assert_called_once_with()
                reset_connections.assert_not_called()
                sleep.assert_not_called()

    def test_reset_disposes_the_sqlalchemy_engine_pool(self):
        with patch.object(db.engine, "dispose") as dispose:
            _reset_database_connections()

        dispose.assert_called_once_with()

    def test_retry_logs_do_not_include_database_credentials(self):
        credentialed_url = "postgresql://neo_user:database-password@example.test/neoapps"
        self.app.config["SQLALCHEMY_DATABASE_URI"] = credentialed_url
        action = Mock(side_effect=[self._transient_error(), "connected"])

        with (
            patch("app.services.database_startup_retry._reset_database_connections"),
            patch("app.services.database_startup_retry.time.sleep"),
            self.assertLogs("app", level="WARNING") as logs,
        ):
            self.assertEqual(
                run_startup_database_action(
                    self.app,
                    action,
                    action_name="schema metadata inspection",
                ),
                "connected",
            )

        output = "\n".join(logs.output)
        self.assertIn("Retrying transient database startup failure", output)
        self.assertNotIn(credentialed_url, output)
        self.assertNotIn("database-password", output)
        self.assertNotIn("SSL connection has been closed unexpectedly", output)

    @staticmethod
    def _transient_error():
        return OperationalError(
            "SELECT 1",
            {},
            RuntimeError("SSL connection has been closed unexpectedly"),
        )
