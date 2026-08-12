import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.services.neoermac_door_pull_schema import (
    LEGACY_DOOR_PULL_BOOLEAN_COLUMNS,
    NEOERMAC_DOOR_PULL_SCHEMA_LOCK_KEY,
    ensure_neoermac_door_pull_legacy_defaults,
)


class NeoErmacDoorPullCompatibilitySchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoErmacDoorPullCompatibilitySchemaTestConfig",
            (),
            {
                "SECRET_KEY": "neoermac-door-pull-compat-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)

    def test_postgresql_startup_ensure_targets_only_legacy_boolean_defaults(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.neoermac_door_pull_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neoermac_door_pull_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neoermac_door_pull_legacy_defaults(self.app))
            self.assertTrue(ensure_neoermac_door_pull_legacy_defaults(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOERMAC_DOOR_PULL_SCHEMA_LOCK_KEY,
        )
        for column_name in LEGACY_DOOR_PULL_BOOLEAN_COLUMNS:
            self.assertIn(
                f"ALTER COLUMN {column_name} SET DEFAULT FALSE",
                statements,
            )
        self.assertNotIn("sync_database_schema", statements)
        self.assertEqual(commit.call_count, 2)

    def test_testing_and_sqlite_skip_the_postgresql_repair(self):
        with patch(
            "app.services.neoermac_door_pull_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neoermac_door_pull_legacy_defaults(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(ensure_neoermac_door_pull_legacy_defaults(self.app))

        connection.assert_not_called()

    def test_factory_invokes_the_targeted_compatibility_ensure(self):
        with patch("app.ensure_neoermac_door_pull_legacy_defaults") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
