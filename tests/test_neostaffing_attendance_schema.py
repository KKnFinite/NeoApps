import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingDailyAttendance
from app.services.neostaffing_attendance_schema import (
    NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS,
    NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY,
    ensure_neostaffing_attendance_columns,
)
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoStaffingAttendanceSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingAttendanceSchemaConfig",
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

    def test_model_and_schema_maps_define_nullable_additive_columns(self):
        for column_name in NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS:
            with self.subTest(column_name=column_name):
                column = StaffingDailyAttendance.__table__.c[column_name]
                self.assertTrue(column.nullable)
                self.assertEqual(
                    LOCAL_SQLITE_OPTIONAL_COLUMNS["staffing_daily_attendance"][column_name],
                    "INTEGER",
                )
                self.assertEqual(
                    POSTGRES_OPTIONAL_COLUMNS["staffing_daily_attendance"][column_name],
                    "INTEGER",
                )
        self.assertTrue(
            StaffingDailyAttendance.__table__.c.sort_date_operation_id.foreign_keys
        )

    def test_sqlite_schema_sync_adds_columns_without_changing_legacy_rows(self):
        db.session.execute(text("DROP TABLE staffing_daily_attendance"))
        db.session.execute(
            text(
                """
                CREATE TABLE staffing_daily_attendance (
                    id INTEGER PRIMARY KEY,
                    attendance_date DATE NOT NULL,
                    sort_unit_id INTEGER NOT NULL,
                    person_id INTEGER NOT NULL,
                    work_area_unit_id INTEGER,
                    status VARCHAR(32) NOT NULL,
                    note TEXT,
                    recorded_by_user_id INTEGER,
                    recorded_at DATETIME NOT NULL,
                    updated_by_user_id INTEGER,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_staffing_daily_attendance_person_date_sort
                        UNIQUE (person_id, attendance_date, sort_unit_id)
                )
                """
            )
        )
        db.session.execute(
            text(
                """
                INSERT INTO staffing_daily_attendance (
                    id, attendance_date, sort_unit_id, person_id,
                    work_area_unit_id, status, recorded_at, updated_at
                ) VALUES (
                    1, '2026-08-14', 10, 20, 30, 'here',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        row = db.session.execute(
            text("SELECT * FROM staffing_daily_attendance WHERE id = 1")
        ).mappings().one()
        self.assertEqual(row["status"], "here")
        self.assertEqual(row["work_area_unit_id"], 30)
        for column_name in NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS:
            self.assertIsNone(row[column_name])
        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("staffing_daily_attendance")
        }
        self.assertTrue(set(NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS).issubset(columns))

    def test_postgresql_startup_repair_is_targeted_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_attendance_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_attendance_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neostaffing_attendance_columns(self.app))
            self.assertTrue(ensure_neostaffing_attendance_columns(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY,
        )
        for column_name in NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS:
            self.assertIn(
                "ALTER TABLE staffing_daily_attendance ADD COLUMN IF NOT EXISTS "
                f"{column_name} INTEGER",
                statements,
            )
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_attendance_repair(self):
        with patch("app.ensure_neostaffing_attendance_columns") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
