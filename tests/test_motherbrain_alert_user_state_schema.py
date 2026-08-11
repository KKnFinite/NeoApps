import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import MotherBrainAlertUserState
from app.services.motherbrain_alert_user_state_schema import (
    MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_KEY,
    ensure_motherbrain_alert_user_state_table,
)


class MotherBrainAlertUserStateSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "MotherBrainAlertUserStateSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "alert-user-state-schema-test-secret",
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
        self.context.pop()

    def test_sqlite_and_testing_do_not_run_postgresql_repair(self):
        with patch(
            "app.services.motherbrain_alert_user_state_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_motherbrain_alert_user_state_table(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(ensure_motherbrain_alert_user_state_table(self.app))

        connection.assert_not_called()

    def test_postgresql_repair_targets_only_user_state_table(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(MotherBrainAlertUserState.__table__, "create") as create,
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.commit"
            ) as commit,
            patch("app.services.schema_sync.sync_database_schema") as broad_sync,
        ):
            self.assertTrue(ensure_motherbrain_alert_user_state_table(self.app))

        create.assert_called_once_with(bind=connection, checkfirst=True)
        commit.assert_called_once_with()
        broad_sync.assert_not_called()
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            MOTHERBRAIN_ALERT_USER_STATE_SCHEMA_LOCK_KEY,
        )

    def test_repeated_execution_is_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(MotherBrainAlertUserState.__table__, "create") as create,
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.commit"
            ),
        ):
            ensure_motherbrain_alert_user_state_table(self.app)
            ensure_motherbrain_alert_user_state_table(self.app)

        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.kwargs["checkfirst"] for call in create.call_args_list))

    def test_failure_rolls_back_and_raises(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        connection.execute.side_effect = RuntimeError("database unavailable")

        with (
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.motherbrain_alert_user_state_schema.db.session.rollback"
            ) as rollback,
            self.assertRaisesRegex(RuntimeError, "database unavailable"),
        ):
            ensure_motherbrain_alert_user_state_table(self.app)

        rollback.assert_called_once_with()

    def test_factory_invokes_targeted_startup_ensure(self):
        with patch("app.ensure_motherbrain_alert_user_state_table") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
