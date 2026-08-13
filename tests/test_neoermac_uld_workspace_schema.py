import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import NeoErmacUldRequest, NeoSektorUldOnTheWayEvent
from app.services.neoermac_uld_workspace_schema import (
    NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_KEY,
    ensure_neoermac_uld_workspace_columns,
)


class NeoErmacUldWorkspaceSchemaTest(unittest.TestCase):
    def setUp(self):
        Config = type(
            "NeoErmacUldWorkspaceSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "uld-workspace-schema-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(Config)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        self.context.pop()

    def test_testing_sqlite_skips_targeted_production_repair(self):
        with patch(
            "app.services.neoermac_uld_workspace_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neoermac_uld_workspace_columns(self.app))
        connection.assert_not_called()

    def test_postgresql_repair_targets_only_uld_workspace_provenance(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neoermac_uld_workspace_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neoermac_uld_workspace_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neoermac_uld_workspace_columns(self.app))

        commit.assert_called_once_with()
        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("ALTER TABLE neoermac_uld_requests", statements)
        self.assertIn("ALTER TABLE neosektor_uld_on_the_way_events", statements)
        self.assertIn("requested_by_user_id", statements)
        self.assertIn("uq_neoermac_uld_request_scope_requester", statements)
        self.assertNotIn("master_flight_schedules", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOERMAC_ULD_WORKSPACE_SCHEMA_LOCK_KEY,
        )

    def test_models_store_requester_provenance_and_requester_scoped_uniqueness(self):
        self.assertIn("requested_by_user_id", NeoErmacUldRequest.__table__.columns)
        self.assertIn(
            "requested_by_user_id",
            NeoSektorUldOnTheWayEvent.__table__.columns,
        )
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in NeoErmacUldRequest.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(
            (
                "gateway_id",
                "sort_date_operation_id",
                "door",
                "setup_needed",
                "requested_by_user_id",
            ),
            unique_columns,
        )


if __name__ == "__main__":
    unittest.main()
