import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from app import create_app
from app.services.neoscorpion_spear_schema import (
    NEOSCORPION_SPEAR_SCHEMA_LOCK_KEY,
    SPEAR_SETTINGS_COLUMNS,
    SPEAR_ASSIGNMENT_COLUMNS,
    ensure_neoscorpion_spear_schema_compatibility,
)
from app.models import NeoScorpionSpearAuditEntry, NeoScorpionSpearCalibrationReset


class NeoScorpionSpearProductionSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoScorpionSpearProductionSchemaConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-spear-schema-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )

    def test_factory_runs_the_targeted_spear_compatibility_ensure(self):
        with patch(
            "app.services.neoscorpion_spear_schema."
            "ensure_neoscorpion_spear_schema_compatibility"
        ) as ensure:
            app = create_app(self.config, auto_bootstrap=False)

        ensure.assert_called_once_with(app)

    def test_postgresql_ensure_is_narrow_locked_and_idempotent(self):
        app = create_app(self.config, auto_bootstrap=False)
        app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.neoscorpion_spear_schema.db.session.connection",
                    return_value=connection,
                )
            )
            commit = stack.enter_context(
                patch("app.services.neoscorpion_spear_schema.db.session.commit")
            )
            stack.enter_context(
                patch(
                    "app.services.neoscorpion_spear_schema."
                    "_verify_spear_schema_contract"
                )
            )
            create = stack.enter_context(
                patch.object(NeoScorpionSpearAuditEntry.__table__, "create")
            )
            reset_create = stack.enter_context(
                patch.object(NeoScorpionSpearCalibrationReset.__table__, "create")
            )

            self.assertTrue(ensure_neoscorpion_spear_schema_compatibility(app))
            self.assertTrue(ensure_neoscorpion_spear_schema_compatibility(app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSCORPION_SPEAR_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(
            statements.count("ADD COLUMN IF NOT EXISTS"),
            (len(SPEAR_SETTINGS_COLUMNS) + len(SPEAR_ASSIGNMENT_COLUMNS)) * 2,
        )
        self.assertIn("neoscorpion_fuel_assignments", statements)
        create.assert_called_with(bind=connection, checkfirst=True)
        reset_create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(create.call_count, 2)
        self.assertEqual(commit.call_count, 2)

    def test_testing_and_sqlite_skip_the_targeted_ensure(self):
        app = create_app(self.config, auto_bootstrap=False)
        with patch(
            "app.services.neoscorpion_spear_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neoscorpion_spear_schema_compatibility(app))
            app.config.update(
                TESTING=False,
                SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            )
            self.assertFalse(ensure_neoscorpion_spear_schema_compatibility(app))

        connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
