import unittest
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingDailyAttendance, StaffingPerson, StaffingUnit
from app.models.staffing_daily_attendance import (
    STAFFING_DAILY_ATTENDANCE_STATUSES,
    STAFFING_DAILY_ATTENDANCE_WRITABLE_STATUSES,
)
from app.services.neostaffing_attendance_schema import (
    NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS,
    NEOSTAFFING_ATTENDANCE_SCHEMA_LOCK_KEY,
    NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT,
    NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT,
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
        staffing_sort = StaffingUnit(unit_type="sort", name="Night")
        work_area = StaffingUnit(
            unit_type="work_area",
            name="Door 1",
            parent=staffing_sort,
        )
        person = StaffingPerson(
            employee_id="SCHEMA100",
            first_name="Schema",
            last_name="Fixture",
            seniority_date=date(2020, 1, 1),
            classification="part_time",
            employee_status="active",
            active=True,
        )
        db.session.add_all([staffing_sort, work_area, person])
        db.session.commit()
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
                    1, '2026-08-14', :sort_id, :person_id, :work_area_id, 'here',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "sort_id": staffing_sort.id,
                "person_id": person.id,
                "work_area_id": work_area.id,
            },
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
        self.assertEqual(row["work_area_unit_id"], work_area.id)
        for column_name in NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS:
            self.assertIsNone(row[column_name])
        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns("staffing_daily_attendance")
        }
        self.assertTrue(set(NEOSTAFFING_ATTENDANCE_ADDITIVE_COLUMNS).issubset(columns))

    def test_model_constraint_accepts_the_shared_writable_status_set(self):
        self.assertEqual(
            STAFFING_DAILY_ATTENDANCE_STATUSES,
            STAFFING_DAILY_ATTENDANCE_WRITABLE_STATUSES,
        )
        for index, status in enumerate(STAFFING_DAILY_ATTENDANCE_STATUSES, start=1):
            db.session.execute(
                text(
                    "INSERT INTO staffing_daily_attendance "
                    "(attendance_date, sort_unit_id, person_id, status, "
                    "recorded_at, updated_at) "
                    "VALUES ('2026-08-15', 100, :person_id, :status, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"person_id": index, "status": status},
            )
        db.session.commit()
        self.assertEqual(
            StaffingDailyAttendance.query.count(),
            len(STAFFING_DAILY_ATTENDANCE_STATUSES),
        )

    def test_postgresql_startup_repair_is_targeted_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        full_constraint = "CHECK (status IN (" + ", ".join(
            f"'{status}'" for status in STAFFING_DAILY_ATTENDANCE_STATUSES
        ) + "))"
        connection.execute.return_value.scalar.side_effect = [
            "CHECK (status IN ('here'))",
            full_constraint,
        ]
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
        self.assertEqual(
            statements.count(
                f"ADD CONSTRAINT {NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT}"
            ),
            1,
        )
        self.assertIn("NOT VALID", statements)
        self.assertIn(
            f"VALIDATE CONSTRAINT {NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT}",
            statements,
        )
        self.assertIn(
            f"DROP CONSTRAINT IF EXISTS {NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT}",
            statements,
        )
        self.assertIn(
            f"RENAME CONSTRAINT {NEOSTAFFING_ATTENDANCE_STATUS_TRANSITION_CONSTRAINT} "
            f"TO {NEOSTAFFING_ATTENDANCE_STATUS_CONSTRAINT}",
            statements,
        )
        self.assertNotIn("DROP TABLE", statements)
        self.assertNotIn("INSERT INTO", statements)
        self.assertNotIn("UPDATE staffing_daily_attendance", statements)
        self.assertNotIn("DELETE FROM staffing_daily_attendance", statements)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_attendance_repair(self):
        with patch("app.ensure_neostaffing_attendance_columns") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
