import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingNotification
from app.services.neostaffing_notification_schema import (
    NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_KEY,
    ensure_neostaffing_notification_table,
)
from app.services.schema_sync import sync_local_sqlite_schema


class NeoStaffingNotificationSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingNotificationSchemaConfig",
            (),
            {
                "SECRET_KEY": "test",
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

    def test_notification_model_is_additive_and_has_stable_dedupe_key(self):
        self.assertEqual(StaffingNotification.__table__.name, "staffing_notifications")
        self.assertFalse(StaffingNotification.__table__.c.recipient_user_id.nullable)
        self.assertFalse(StaffingNotification.__table__.c.change_request_id.nullable)
        self.assertFalse(StaffingNotification.__table__.c.dedupe_key.nullable)
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in StaffingNotification.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("dedupe_key",), unique_columns)

    def test_local_schema_sync_recreates_only_missing_notification_table(self):
        db.session.execute(text("DROP TABLE staffing_notifications"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        self.assertIn(
            "staffing_notifications",
            inspect(db.engine).get_table_names(),
        )

    def test_postgresql_targeted_ensure_is_bounded_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_notification_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_notification_schema.db.session.commit"
            ) as commit,
            patch.object(StaffingNotification.__table__, "create") as create,
        ):
            self.assertTrue(ensure_neostaffing_notification_table(self.app))
            self.assertTrue(ensure_neostaffing_notification_table(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_NOTIFICATION_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(create.call_count, 2)
        create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_notification_table_ensure(self):
        with patch("app.ensure_neostaffing_notification_table") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
