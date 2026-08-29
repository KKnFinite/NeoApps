from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, Mock, patch

from app import create_app
from app.extensions import db
from app.models import MotherBrainGoogleIntegrationSetting
from app.services.google_rain_integration_schema import (
    RAIN_INTEGRATION_MODE_COLUMN,
    RAIN_INTEGRATION_SCHEMA_LOCK_KEY,
    ensure_google_rain_integration_mode_column,
)


class GoogleRainIntegrationSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "GoogleRainIntegrationSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-schema-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_model_and_sqlite_schema_have_non_null_google_primary_default(self):
        column = MotherBrainGoogleIntegrationSetting.__table__.columns[
            RAIN_INTEGRATION_MODE_COLUMN
        ]
        self.assertFalse(column.nullable)
        self.assertEqual(str(column.server_default.arg), "google_primary")

    def test_testing_and_sqlite_skip_postgresql_ensure(self):
        with patch(
            "app.services.google_rain_integration_schema.db.engine.connect"
        ) as connection:
            self.assertFalse(ensure_google_rain_integration_mode_column(self.app))
        connection.assert_not_called()

    def test_current_schema_executes_no_lock_or_ddl(self):
        with self._postgres_patches([True]) as calls:
            self.assertTrue(ensure_google_rain_integration_mode_column(self.app))

        calls["session_connection"].assert_not_called()
        calls["locked_connection"].execute.assert_not_called()
        calls["commit"].assert_not_called()
        calls["rollback"].assert_not_called()

    def test_missing_column_is_added_once_with_default(self):
        with self._postgres_patches([False, False]) as calls:
            self.assertTrue(ensure_google_rain_integration_mode_column(self.app))

        statements = self._statements(calls["locked_connection"])
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("ADD COLUMN rain_integration_mode", statements)
        self.assertIn("NOT NULL DEFAULT 'google_primary'", statements)
        self.assertEqual(
            calls["locked_connection"].execute.call_args_list[1].args[1][
                "lock_key"
            ],
            RAIN_INTEGRATION_SCHEMA_LOCK_KEY,
        )
        calls["commit"].assert_called_once_with()

    def test_recheck_after_lock_prevents_duplicate_ddl(self):
        with self._postgres_patches([False, True]) as calls:
            self.assertTrue(ensure_google_rain_integration_mode_column(self.app))

        statements = self._statements(calls["locked_connection"])
        self.assertNotIn("ALTER TABLE", statements)
        calls["commit"].assert_not_called()
        calls["rollback"].assert_called_once_with()

    def test_failure_rolls_back_without_retry(self):
        with self._postgres_patches([False, False]) as calls:
            calls["locked_connection"].execute.side_effect = RuntimeError("lock failed")
            with self.assertRaisesRegex(RuntimeError, "lock failed"):
                ensure_google_rain_integration_mode_column(self.app)

        self.assertEqual(calls["locked_connection"].execute.call_count, 1)
        calls["commit"].assert_not_called()
        calls["rollback"].assert_called_once_with()

    def test_factory_invokes_targeted_ensure(self):
        with patch("app.ensure_google_rain_integration_mode_column") as ensure:
            create_app(self.config)
        ensure.assert_called_once()

    def _postgres_patches(self, column_states):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        read_connection = Mock()
        locked_connection = Mock()
        engine_context = MagicMock()
        engine_context.__enter__.return_value = read_connection
        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.services.google_rain_integration_schema.db.engine.connect",
                return_value=engine_context,
            )
        )
        session_connection = stack.enter_context(
            patch(
                "app.services.google_rain_integration_schema.db.session.connection",
                return_value=locked_connection,
            )
        )
        commit = stack.enter_context(
            patch("app.services.google_rain_integration_schema.db.session.commit")
        )
        rollback = stack.enter_context(
            patch("app.services.google_rain_integration_schema.db.session.rollback")
        )
        stack.enter_context(
            patch(
                "app.services.google_rain_integration_schema._column_exists",
                side_effect=column_states,
            )
        )
        return _SchemaPatchResults(
            stack,
            read_connection,
            locked_connection,
            session_connection,
            commit,
            rollback,
        )

    @staticmethod
    def _statements(connection):
        return "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )


class _SchemaPatchResults:
    def __init__(
        self,
        stack,
        read_connection,
        locked_connection,
        session_connection,
        commit,
        rollback,
    ):
        self.stack = stack
        self.calls = {
            "read_connection": read_connection,
            "locked_connection": locked_connection,
            "session_connection": session_connection,
            "commit": commit,
            "rollback": rollback,
        }

    def __enter__(self):
        return self.calls

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


if __name__ == "__main__":
    unittest.main()
