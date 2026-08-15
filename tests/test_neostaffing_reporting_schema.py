import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingReportingRelationship
from app.services.neostaffing_reporting_schema import (
    NEOSTAFFING_REPORTING_SCHEMA_LOCK_KEY,
    ensure_neostaffing_reporting_relationship_table,
)
from app.services.schema_sync import sync_local_sqlite_schema


class NeoStaffingReportingSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingReportingSchemaConfig",
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

    def test_model_is_additive_and_has_active_relationship_guard(self):
        table = StaffingReportingRelationship.__table__
        self.assertEqual(table.name, "staffing_reporting_relationships")
        self.assertTrue(table.c.person_id.foreign_keys)
        self.assertTrue(table.c.reports_to_person_id.foreign_keys)
        self.assertTrue(table.c.effective_end.nullable)
        indexes = {index.name: index for index in table.indexes}
        active_index = indexes[
            "uq_staffing_reporting_relationships_active_person"
        ]
        self.assertTrue(active_index.unique)
        self.assertIsNotNone(active_index.dialect_options["postgresql"]["where"])
        self.assertIsNotNone(active_index.dialect_options["sqlite"]["where"])

    def test_local_schema_sync_recreates_only_missing_reporting_table(self):
        db.session.execute(text("DROP TABLE staffing_reporting_relationships"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        self.assertIn(
            "staffing_reporting_relationships",
            inspect(db.engine).get_table_names(),
        )

    def test_postgresql_targeted_ensure_is_lock_bounded_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_reporting_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_reporting_schema.db.session.commit"
            ) as commit,
            patch.object(
                StaffingReportingRelationship.__table__,
                "create",
            ) as create_table,
        ):
            self.assertTrue(
                ensure_neostaffing_reporting_relationship_table(self.app)
            )
            self.assertTrue(
                ensure_neostaffing_reporting_relationship_table(self.app)
            )

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_REPORTING_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(create_table.call_count, 2)
        create_table.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_reporting_table_ensure(self):
        with patch(
            "app.ensure_neostaffing_reporting_relationship_table"
        ) as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
