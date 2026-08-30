from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, Mock, patch

from app import create_app
from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission
from app.services.neorain_load_planner_schema import (
    NEORAIN_LOAD_PLANNER_COLUMNS,
    NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_KEY,
    ensure_neorain_load_planner_columns,
)


class NeoRainLoadPlannerSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoRainLoadPlannerSchemaTestConfig",
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

    def test_models_expose_nullable_staffing_person_references(self):
        self.assertTrue(
            MasterFlightSchedule.__table__.columns["load_planner_person_id"].nullable
        )
        self.assertTrue(
            SortDateMission.__table__.columns["load_planner_person_id"].nullable
        )

    def test_current_postgresql_schema_skips_lock_and_ddl(self):
        read_connection = Mock()
        locked_connection = Mock()
        with self._patch_ensure(read_connection, locked_connection, [()]) as calls:
            self.assertTrue(ensure_neorain_load_planner_columns(self.app))

        calls["session_connection"].assert_not_called()
        locked_connection.execute.assert_not_called()
        calls["commit"].assert_not_called()

    def test_missing_columns_are_added_after_lock_and_recheck(self):
        missing = tuple(NEORAIN_LOAD_PLANNER_COLUMNS.items())
        read_connection = Mock()
        locked_connection = Mock()
        with self._patch_ensure(
            read_connection, locked_connection, [missing, missing]
        ) as calls:
            self.assertTrue(ensure_neorain_load_planner_columns(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in locked_connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            locked_connection.execute.call_args_list[1].args[1]["lock_key"],
            NEORAIN_LOAD_PLANNER_SCHEMA_LOCK_KEY,
        )
        for table_name, column_name in missing:
            self.assertIn(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name}", statements
            )
        calls["commit"].assert_called_once_with()

    def _patch_ensure(self, read_connection, locked_connection, states):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        engine_context = MagicMock()
        engine_context.__enter__.return_value = read_connection
        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.services.neorain_load_planner_schema.db.engine.connect",
                return_value=engine_context,
            )
        )
        session_connection = stack.enter_context(
            patch(
                "app.services.neorain_load_planner_schema.db.session.connection",
                return_value=locked_connection,
            )
        )
        commit = stack.enter_context(
            patch("app.services.neorain_load_planner_schema.db.session.commit")
        )
        stack.enter_context(
            patch("app.services.neorain_load_planner_schema.db.session.rollback")
        )
        stack.enter_context(
            patch(
                "app.services.neorain_load_planner_schema._missing_columns",
                side_effect=states,
            )
        )
        return _EnsurePatches(stack, session_connection, commit)


class _EnsurePatches:
    def __init__(self, stack, session_connection, commit):
        self.stack = stack
        self.values = {"session_connection": session_connection, "commit": commit}

    def __enter__(self):
        return self.values

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


if __name__ == "__main__":
    unittest.main()
