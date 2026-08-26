from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, Mock, patch

from app import create_app
from app.extensions import db
from app.models import SortDateMission
from app.services.sort_date_mission_schema import (
    DEPARTURE_STATUS_CONSTRAINT_NAME,
    GOOGLE_RAIN_MILESTONE_COLUMNS,
    SORT_DATE_MISSION_SCHEMA_LOCK_KEY,
    _model_constraint_sql,
    _schema_state,
    ensure_sort_date_mission_departure_status_constraint,
)


class SortDateMissionSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "SortDateMissionSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "sort-date-mission-schema-test-secret",
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

    def test_model_departure_status_constraint_includes_full_progression(self):
        constraint = next(
            constraint
            for constraint in SortDateMission.__table__.constraints
            if constraint.name == DEPARTURE_STATUS_CONSTRAINT_NAME
        )
        constraint_sql = str(constraint.sqltext)

        for status in (
            "scheduled",
            "loading",
            "last_uld_enroute",
            "ramp_load_complete",
            "crew_load_complete",
            "blocked_out",
            "departed",
            "cancelled",
        ):
            with self.subTest(status=status):
                self.assertIn(f"'{status}'", constraint_sql)

    def test_model_exposes_google_rain_milestone_and_source_columns(self):
        model_columns = SortDateMission.__table__.columns

        for column_name in GOOGLE_RAIN_MILESTONE_COLUMNS:
            with self.subTest(column_name=column_name):
                self.assertIn(column_name, model_columns)

    def test_testing_and_sqlite_skip_targeted_postgresql_ensure(self):
        with patch(
            "app.services.sort_date_mission_schema.db.session.connection"
        ) as connection:
            self.assertFalse(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        connection.assert_not_called()

    def test_fully_current_postgresql_schema_executes_no_ddl_or_lock(self):
        read_connection = Mock()
        locked_connection = Mock()
        with self._postgresql_ensure_patches(
            read_connection,
            locked_connection,
            schema_states=[((), True)],
        ) as calls:
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        calls["session_connection"].assert_not_called()
        locked_connection.execute.assert_not_called()
        calls["commit"].assert_not_called()
        calls["rollback"].assert_not_called()

    def test_one_missing_rain_column_alters_only_that_column(self):
        missing = "elmac_completed_at_utc"
        connection = Mock()
        with self._postgresql_ensure_patches(
            Mock(),
            connection,
            schema_states=[((missing,), True), ((missing,), True)],
        ) as calls:
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = self._statements(connection)
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            SORT_DATE_MISSION_SCHEMA_LOCK_KEY,
        )
        self.assertIn(f"ADD COLUMN {missing} TIMESTAMP", statements)
        for column_name in set(GOOGLE_RAIN_MILESTONE_COLUMNS) - {missing}:
            self.assertNotIn(f"ADD COLUMN {column_name}", statements)
        self.assertNotIn("DROP CONSTRAINT", statements)
        calls["commit"].assert_called_once_with()

    def test_multiple_missing_rain_columns_alter_only_those_columns(self):
        missing = ("elmac_completed_source", "crew_load_completed_source")
        connection = Mock()
        with self._postgresql_ensure_patches(
            Mock(),
            connection,
            schema_states=[(missing, True), (missing, True)],
        ):
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = self._statements(connection)
        for column_name in missing:
            self.assertIn(f"ADD COLUMN {column_name}", statements)
        for column_name in set(GOOGLE_RAIN_MILESTONE_COLUMNS) - set(missing):
            self.assertNotIn(f"ADD COLUMN {column_name}", statements)

    def test_outdated_or_missing_constraint_is_repaired_without_column_ddl(self):
        connection = Mock()
        with self._postgresql_ensure_patches(
            Mock(),
            connection,
            schema_states=[((), False), ((), False)],
        ) as calls:
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = self._statements(connection)
        self.assertNotIn("ADD COLUMN", statements)
        self.assertIn("DROP CONSTRAINT IF EXISTS", statements)
        self.assertIn(
            "ADD CONSTRAINT ck_sort_date_missions_departure_status",
            statements,
        )
        self.assertIn("'scheduled'", statements)
        calls["commit"].assert_called_once_with()

    def test_recheck_after_lock_prevents_duplicate_ddl(self):
        connection = Mock()
        with self._postgresql_ensure_patches(
            Mock(),
            connection,
            schema_states=[
                (("elmac_completed_at_utc",), False),
                ((), True),
            ],
        ) as calls:
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = self._statements(connection)
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements)
        self.assertEqual(statements.count("pg_advisory_xact_lock"), 1)
        self.assertNotIn("ALTER TABLE", statements)
        calls["commit"].assert_not_called()
        calls["rollback"].assert_called_once_with()

    def test_catalog_state_detects_current_outdated_and_missing_constraints(self):
        current_definition = f"CHECK ({_model_constraint_sql()})"
        for definition, expected_current in (
            (current_definition, True),
            ("CHECK (departure_status IN ('loading', 'departed'))", False),
            (None, False),
        ):
            with self.subTest(definition=definition):
                columns_result = Mock()
                columns_result.scalars.return_value.all.return_value = list(
                    GOOGLE_RAIN_MILESTONE_COLUMNS
                )
                definition_result = Mock()
                definition_result.scalar.return_value = definition
                connection = Mock()
                connection.execute.side_effect = [
                    columns_result,
                    definition_result,
                ]

                missing_columns, constraint_is_current = _schema_state(
                    connection
                )

                self.assertEqual(missing_columns, ())
                self.assertEqual(constraint_is_current, expected_current)
                statements = self._statements(connection)
                self.assertIn("FROM pg_attribute", statements)
                self.assertIn("pg_get_constraintdef", statements)

    def test_failure_rolls_back_without_retry(self):
        connection = Mock()
        connection.execute.side_effect = RuntimeError("lock failed")
        with self._postgresql_ensure_patches(
            Mock(),
            connection,
            schema_states=[(("elmac_completed_at_utc",), True)],
        ) as calls:
            with self.assertRaisesRegex(RuntimeError, "lock failed"):
                ensure_sort_date_mission_departure_status_constraint(self.app)

        self.assertEqual(connection.execute.call_count, 1)
        calls["commit"].assert_not_called()
        calls["rollback"].assert_called_once_with()

    def test_factory_invokes_targeted_departure_status_ensure(self):
        with patch(
            "app.ensure_sort_date_mission_departure_status_constraint"
        ) as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)

    def _postgresql_ensure_patches(
        self,
        read_connection,
        locked_connection,
        *,
        schema_states,
    ):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        engine_context = MagicMock()
        engine_context.__enter__.return_value = read_connection
        stack = ExitStack()
        stack.enter_context(
            patch(
                "app.services.sort_date_mission_schema.db.engine.connect",
                return_value=engine_context,
            )
        )
        session_connection = stack.enter_context(
            patch(
                "app.services.sort_date_mission_schema.db.session.connection",
                return_value=locked_connection,
            )
        )
        commit = stack.enter_context(
            patch("app.services.sort_date_mission_schema.db.session.commit")
        )
        rollback = stack.enter_context(
            patch("app.services.sort_date_mission_schema.db.session.rollback")
        )
        stack.enter_context(
            patch(
                "app.services.sort_date_mission_schema._schema_state",
                side_effect=schema_states,
            )
        )
        return _PatchResults(stack, session_connection, commit, rollback)

    @staticmethod
    def _statements(connection):
        return "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )


class _PatchResults:
    def __init__(self, stack, session_connection, commit, rollback):
        self.stack = stack
        self.results = {
            "session_connection": session_connection,
            "commit": commit,
            "rollback": rollback,
        }

    def __enter__(self):
        return self.results

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


if __name__ == "__main__":
    unittest.main()
