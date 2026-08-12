import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import NeoErmacDoorSupervision
from app.services.neoermac_door_supervision_schema import (
    NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_KEY,
    ensure_neoermac_door_supervision_table,
)


class NeoErmacDoorSupervisionSchemaTest(unittest.TestCase):
    def setUp(self):
        Config = type(
            "NeoErmacDoorSupervisionSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "door-supervision-schema-test",
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
            "app.services.neoermac_door_supervision_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neoermac_door_supervision_table(self.app))
        connection.assert_not_called()

    def test_postgresql_ensure_targets_only_the_supervision_table(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neoermac_door_supervision_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(NeoErmacDoorSupervision.__table__, "create") as create,
            patch(
                "app.services.neoermac_door_supervision_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neoermac_door_supervision_table(self.app))

        create.assert_called_once_with(bind=connection, checkfirst=True)
        commit.assert_called_once_with()
        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOERMAC_DOOR_SUPERVISION_SCHEMA_LOCK_KEY,
        )

    def test_model_has_user_operation_uniqueness_and_unbounded_text_storage(self):
        columns = NeoErmacDoorSupervision.__table__.columns
        self.assertIsInstance(columns.selected_doors_json.type, db.Text)
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in NeoErmacDoorSupervision.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn(("user_id", "sort_date_operation_id"), unique_columns)


if __name__ == "__main__":
    unittest.main()
