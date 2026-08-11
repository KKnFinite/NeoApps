import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import SortDateOperation
from app.services.sort_date_operation_schema import (
    SORT_DATE_OPERATION_SCHEMA_LOCK_KEY,
    WINDOW_CONSTRAINT_NAME,
    ensure_sort_date_operation_window_nullable,
)


class SortDateOperationSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "SortDateOperationSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "sort-date-operation-schema-test-secret",
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

    def test_model_global_window_is_nullable_without_a_zero_default(self):
        column = SortDateOperation.__table__.c.window_minutes
        constraint = next(
            constraint
            for constraint in SortDateOperation.__table__.constraints
            if constraint.name == WINDOW_CONSTRAINT_NAME
        )

        self.assertTrue(column.nullable)
        self.assertIsNone(column.default)
        self.assertIn("window_minutes IS NULL", str(constraint.sqltext))

    def test_testing_and_sqlite_skip_targeted_postgresql_ensure(self):
        with patch(
            "app.services.sort_date_operation_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_sort_date_operation_window_nullable(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(ensure_sort_date_operation_window_nullable(self.app))

        connection.assert_not_called()

    def test_postgresql_ensure_targets_only_global_window_nullability(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        column_result = Mock()
        column_result.mappings.return_value.one_or_none.return_value = {
            "is_nullable": "NO",
            "column_default": "0",
        }
        definition_result = Mock()
        definition_result.scalar.return_value = "CHECK ((window_minutes >= 0))"
        connection.execute.side_effect = [
            Mock(),
            Mock(),
            column_result,
            Mock(),
            definition_result,
            Mock(),
            Mock(),
        ]

        with (
            patch(
                "app.services.sort_date_operation_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.sort_date_operation_schema.db.session.commit"
            ) as commit,
            patch("app.services.schema_sync.sync_database_schema") as broad_sync,
        ):
            self.assertTrue(ensure_sort_date_operation_window_nullable(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            SORT_DATE_OPERATION_SCHEMA_LOCK_KEY,
        )
        self.assertIn("ALTER COLUMN window_minutes DROP NOT NULL", statements)
        self.assertIn("ALTER COLUMN window_minutes DROP DEFAULT", statements)
        self.assertIn(f"DROP CONSTRAINT IF EXISTS {WINDOW_CONSTRAINT_NAME}", statements)
        self.assertIn(f"ADD CONSTRAINT {WINDOW_CONSTRAINT_NAME}", statements)
        self.assertNotIn("UPDATE sort_date_operations", statements)
        self.assertNotIn("CREATE TABLE", statements)
        broad_sync.assert_not_called()
        commit.assert_called_once_with()

    def test_current_postgresql_constraint_is_left_unchanged(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        column_result = Mock()
        column_result.mappings.return_value.one_or_none.return_value = {
            "is_nullable": "YES",
            "column_default": None,
        }
        definition_result = Mock()
        definition_result.scalar.return_value = (
            "CHECK (((window_minutes IS NULL) OR (window_minutes >= 0)))"
        )
        connection.execute.side_effect = [Mock(), Mock(), column_result, definition_result]

        with (
            patch(
                "app.services.sort_date_operation_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.sort_date_operation_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_sort_date_operation_window_nullable(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertNotIn("DROP NOT NULL", statements)
        self.assertNotIn("DROP DEFAULT", statements)
        self.assertNotIn("DROP CONSTRAINT", statements)
        self.assertNotIn("ADD CONSTRAINT", statements)
        commit.assert_called_once_with()

    def test_factory_invokes_targeted_operation_window_ensure(self):
        with patch("app.ensure_sort_date_operation_window_nullable") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
