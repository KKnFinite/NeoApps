import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    StaffingChangeRequest,
    StaffingChangeRequestEvent,
    StaffingChangeRequestItem,
)
from app.services.neostaffing_change_request_schema import (
    NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_KEY,
    ensure_neostaffing_change_request_tables,
)
from app.services.schema_sync import sync_local_sqlite_schema


class NeoStaffingChangeRequestSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingChangeRequestSchemaConfig",
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

    def test_models_are_additive_and_pending_field_guard_is_partial(self):
        self.assertEqual(StaffingChangeRequest.__table__.name, "staffing_change_requests")
        self.assertEqual(
            StaffingChangeRequestItem.__table__.name,
            "staffing_change_request_items",
        )
        self.assertEqual(
            StaffingChangeRequestEvent.__table__.name,
            "staffing_change_request_events",
        )
        index = next(
            row
            for row in StaffingChangeRequestItem.__table__.indexes
            if row.name == "uq_staffing_change_request_items_pending_field"
        )
        self.assertTrue(index.unique)
        self.assertIsNotNone(index.dialect_options["postgresql"]["where"])
        self.assertIsNotNone(index.dialect_options["sqlite"]["where"])

    def test_local_schema_sync_recreates_only_missing_change_request_tables(self):
        db.session.execute(text("DROP TABLE staffing_change_request_events"))
        db.session.execute(text("DROP TABLE staffing_change_request_items"))
        db.session.execute(text("DROP TABLE staffing_change_requests"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        table_names = inspect(db.engine).get_table_names()
        for table_name in (
            "staffing_change_requests",
            "staffing_change_request_items",
            "staffing_change_request_events",
        ):
            self.assertIn(table_name, table_names)

    def test_postgresql_targeted_ensure_is_bounded_idempotent_and_seeds_permissions(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_change_request_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_change_request_schema.db.session.commit"
            ) as commit,
            patch.object(StaffingChangeRequest.__table__, "create") as create_header,
            patch.object(StaffingChangeRequestItem.__table__, "create") as create_item,
            patch.object(StaffingChangeRequestEvent.__table__, "create") as create_event,
        ):
            self.assertTrue(ensure_neostaffing_change_request_tables(self.app))
            self.assertTrue(ensure_neostaffing_change_request_tables(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("ON CONFLICT (permission_key) DO NOTHING", statements)
        self.assertIn("neostaffing.change_requests.approve", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_CHANGE_REQUEST_SCHEMA_LOCK_KEY,
        )
        for create in (create_header, create_item, create_event):
            self.assertEqual(create.call_count, 2)
            create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_change_request_table_ensure(self):
        with patch("app.ensure_neostaffing_change_request_tables") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
