import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services.neosektor_sheets_compat_schema import (
    NEOSEKTOR_SHEETS_SCHEMA_LOCK_KEY,
    ensure_neosektor_sheets_compat_columns,
)
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
)


class NeoSektorSheetsCompatSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoSektorSheetsCompatSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "neosektor-sheets-schema-test-secret",
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

    def test_model_and_schema_maps_include_transition_columns(self):
        table = NeoSektorOperationalSetting.__table__.c

        self.assertFalse(table.integration_mode.nullable)
        self.assertFalse(table.google_mirror_sync_needed.nullable)
        self.assertTrue(table.google_mirror_last_error.nullable)
        self.assertTrue(table.google_mirror_failed_at_utc.nullable)
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neosektor_operational_settings"][
                "integration_mode"
            ],
            "VARCHAR(40) NOT NULL DEFAULT 'google_primary'",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neosektor_operational_settings"][
                "google_mirror_sync_needed"
            ],
            "BOOLEAN NOT NULL DEFAULT FALSE",
        )

    def test_testing_and_sqlite_skip_postgresql_column_ensure(self):
        with patch(
            "app.services.neosektor_sheets_compat_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neosektor_sheets_compat_columns(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(ensure_neosektor_sheets_compat_columns(self.app))

        connection.assert_not_called()

    def test_postgresql_ensure_targets_only_additive_transition_columns(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.neosektor_sheets_compat_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neosektor_sheets_compat_schema.db.session.commit"
            ) as commit,
            patch("app.services.schema_sync.sync_database_schema") as broad_sync,
        ):
            self.assertTrue(ensure_neosektor_sheets_compat_columns(self.app))
            self.assertTrue(ensure_neosektor_sheets_compat_columns(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn(
            "ALTER TABLE neosektor_operational_settings ADD COLUMN IF NOT EXISTS "
            "last_google_read_at_utc TIMESTAMP",
            statements,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS integration_mode VARCHAR(40) "
            "NOT NULL DEFAULT 'google_primary'",
            statements,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS google_mirror_sync_needed BOOLEAN "
            "NOT NULL DEFAULT FALSE",
            statements,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS google_mirror_last_error VARCHAR(255)",
            statements,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS google_mirror_failed_at_utc TIMESTAMP",
            statements,
        )
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSEKTOR_SHEETS_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(commit.call_count, 2)
        broad_sync.assert_not_called()

    def test_factory_invokes_targeted_column_ensure(self):
        with patch("app.ensure_neosektor_sheets_compat_columns") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
