import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import MotherBrainGoogleLivePollState
from app.services.google_motherbrain_live_poll_schema import (
    GOOGLE_LIVE_POLL_SCHEMA_LOCK_KEY,
    _is_postgresql,
    ensure_google_motherbrain_live_poll_state_table,
)


class GoogleMotherBrainLivePollSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "GoogleLivePollSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "google-live-poll-schema-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_sqlite_does_not_run_the_postgresql_repair(self):
        with patch(
            "app.services.google_motherbrain_live_poll_schema.db.session.connection"
        ) as connection:
            self.assertFalse(
                ensure_google_motherbrain_live_poll_state_table(self.app)
            )

        connection.assert_not_called()

    def test_testing_mode_does_not_run_the_postgresql_repair(self):
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://example.test/neoapps"

        with patch(
            "app.services.google_motherbrain_live_poll_schema.db.session.connection"
        ) as connection:
            self.assertFalse(
                ensure_google_motherbrain_live_poll_state_table(self.app)
            )

        connection.assert_not_called()

    def test_postgresql_driver_uris_are_recognized_without_matching_sqlite(self):
        self.app.config["SQLALCHEMY_DATABASE_URI"] = (
            "postgresql+psycopg2://example.test/neoapps"
        )
        self.assertTrue(_is_postgresql(self.app))

        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.assertFalse(_is_postgresql(self.app))

    def test_postgresql_repair_uses_only_the_poll_state_model_table(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(MotherBrainGoogleLivePollState.__table__, "create") as create,
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(
                ensure_google_motherbrain_live_poll_state_table(self.app)
            )

        create.assert_called_once_with(bind=connection, checkfirst=True)
        commit.assert_called_once_with()
        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            GOOGLE_LIVE_POLL_SCHEMA_LOCK_KEY,
        )

    def test_repeated_postgresql_execution_remains_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(MotherBrainGoogleLivePollState.__table__, "create") as create,
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_google_motherbrain_live_poll_state_table(self.app))
            self.assertTrue(ensure_google_motherbrain_live_poll_state_table(self.app))

        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.kwargs["checkfirst"] for call in create.call_args_list))
        self.assertEqual(commit.call_count, 2)

    def test_repair_failure_rolls_back_without_calling_broad_schema_sync(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        connection.execute.side_effect = RuntimeError("database unavailable")

        with (
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.google_motherbrain_live_poll_schema.db.session.rollback"
            ) as rollback,
            patch("app.services.schema_sync.sync_database_schema") as broad_sync,
            self.assertLogs("app", level="ERROR") as logs,
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                ensure_google_motherbrain_live_poll_state_table(self.app)

        rollback.assert_called_once_with()
        broad_sync.assert_not_called()
        self.assertIn("Google live-poll state table ensure failed safely", "\n".join(logs.output))

    def test_factory_invokes_the_targeted_startup_ensure(self):
        with patch("app.ensure_google_motherbrain_live_poll_state_table") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
