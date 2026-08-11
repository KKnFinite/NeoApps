import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import SortTimelineSortSetting
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)
from app.services.sort_timeline_schema import (
    SORT_TIMELINE_ADDITIVE_TIME_COLUMNS,
    SORT_TIMELINE_SCHEMA_LOCK_KEY,
    ensure_sort_timeline_sort_setting_columns,
)


class SortTimelineSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "SortTimelineSchemaTestConfig",
            (),
            {
                "SECRET_KEY": "sort-timeline-schema-test-secret",
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

    def test_model_and_repair_maps_include_the_nullable_time_fields(self):
        model_columns = {column.name for column in SortTimelineSortSetting.__table__.columns}

        for column_name in SORT_TIMELINE_ADDITIVE_TIME_COLUMNS:
            with self.subTest(column_name=column_name):
                self.assertIn(column_name, model_columns)
                self.assertTrue(SortTimelineSortSetting.__table__.c[column_name].nullable)
                self.assertEqual(
                    LOCAL_SQLITE_OPTIONAL_COLUMNS["sort_timeline_sort_settings"][
                        column_name
                    ],
                    "TIME",
                )
                self.assertEqual(
                    POSTGRES_OPTIONAL_COLUMNS["sort_timeline_sort_settings"][
                        column_name
                    ],
                    "TIME",
                )

    def test_sqlite_schema_sync_adds_missing_time_columns_idempotently(self):
        db.session.execute(text("DROP TABLE sort_timeline_sort_settings"))
        db.session.execute(
            text(
                """
                CREATE TABLE sort_timeline_sort_settings (
                    id INTEGER PRIMARY KEY,
                    settings_id INTEGER NOT NULL,
                    gateway_id INTEGER NOT NULL,
                    gateway_code VARCHAR(8) NOT NULL,
                    sort_name VARCHAR(32) NOT NULL
                )
                """
            )
        )
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        column_names = {
            column["name"]
            for column in inspect(db.engine).get_columns("sort_timeline_sort_settings")
        }
        self.assertTrue(set(SORT_TIMELINE_ADDITIVE_TIME_COLUMNS).issubset(column_names))

    def test_postgresql_startup_ensure_targets_only_the_three_additive_columns(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.sort_timeline_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.sort_timeline_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_sort_timeline_sort_setting_columns(self.app))
            self.assertTrue(ensure_sort_timeline_sort_setting_columns(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            SORT_TIMELINE_SCHEMA_LOCK_KEY,
        )
        for column_name in SORT_TIMELINE_ADDITIVE_TIME_COLUMNS:
            self.assertIn(
                f"ALTER TABLE sort_timeline_sort_settings ADD COLUMN IF NOT EXISTS {column_name} TIME",
                statements,
            )
        self.assertEqual(commit.call_count, 2)

    def test_testing_and_sqlite_skip_the_postgresql_startup_ensure(self):
        with patch(
            "app.services.sort_timeline_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_sort_timeline_sort_setting_columns(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://example.test/neoapps"
            self.assertFalse(ensure_sort_timeline_sort_setting_columns(self.app))

        connection.assert_not_called()

    def test_factory_invokes_the_targeted_startup_ensure(self):
        with patch("app.ensure_sort_timeline_sort_setting_columns") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
