import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import SortDateMission
from app.services.sort_date_mission_schema import (
    DEPARTURE_STATUS_CONSTRAINT_NAME,
    GOOGLE_RAIN_MILESTONE_COLUMNS,
    SORT_DATE_MISSION_SCHEMA_LOCK_KEY,
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

    def test_postgresql_ensure_repairs_only_outdated_departure_constraint(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        definition_result = Mock()
        definition_result.scalar.return_value = (
            "CHECK (departure_status IN ('loading', 'departed', 'cancelled'))"
        )

        def execute(statement, *_args, **_kwargs):
            if "SELECT pg_get_constraintdef" in str(statement):
                return definition_result
            return Mock()

        connection.execute.side_effect = execute

        with (
            patch(
                "app.services.sort_date_mission_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.sort_date_mission_schema.db.session.commit"
            ) as commit,
            patch("app.services.schema_sync.sync_database_schema") as broad_sync,
        ):
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            SORT_DATE_MISSION_SCHEMA_LOCK_KEY,
        )
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS ck_sort_date_missions_departure_status",
            statements,
        )
        for column_name, column_sql in GOOGLE_RAIN_MILESTONE_COLUMNS.items():
            self.assertIn(
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_sql}",
                statements,
            )
        self.assertIn("ADD CONSTRAINT ck_sort_date_missions_departure_status", statements)
        self.assertIn("'scheduled'", statements)
        self.assertNotIn("CREATE TABLE", statements)
        broad_sync.assert_not_called()
        commit.assert_called_once_with()

    def test_current_postgresql_constraint_is_idempotently_left_unchanged(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        definition_result = Mock()
        definition_result.scalar.return_value = (
            "CHECK (departure_status IN ('scheduled', 'loading', 'departed'))"
        )

        def execute(statement, *_args, **_kwargs):
            if "SELECT pg_get_constraintdef" in str(statement):
                return definition_result
            return Mock()

        connection.execute.side_effect = execute

        with (
            patch(
                "app.services.sort_date_mission_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.sort_date_mission_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(
                ensure_sort_date_mission_departure_status_constraint(self.app)
            )

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertNotIn("DROP CONSTRAINT", statements)
        for column_name in GOOGLE_RAIN_MILESTONE_COLUMNS:
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {column_name}", statements)
        commit.assert_called_once_with()

    def test_factory_invokes_targeted_departure_status_ensure(self):
        with patch(
            "app.ensure_sort_date_mission_departure_status_constraint"
        ) as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
