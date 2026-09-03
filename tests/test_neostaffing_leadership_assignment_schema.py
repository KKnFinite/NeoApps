import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingPerson, StaffingUnit
from app.services.schema_sync import (
    _sync_staffing_leadership_level_constraint_sqlite,
    _sync_staffing_leadership_level_constraint_postgres,
)


class NeoStaffingLeadershipAssignmentSchemaTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_sqlite_sync_maps_legacy_role_levels_to_unit_scopes(self):
        operation = StaffingUnit(unit_type="operation", name="Night Operation")
        department = StaffingUnit(
            unit_type="department", name="Ramp", parent=operation
        )
        work_area = StaffingUnit(
            unit_type="work_area", name="Unload", parent=department
        )
        people = [
            StaffingPerson(
                employee_id=f"LEGACY-{index}",
                first_name="Legacy",
                last_name=str(index),
                classification=classification,
                seniority_date=date(2020, 1, 1),
            )
            for index, classification in enumerate(
                ("part_time_supervisor", "full_time_supervisor", "full_time_specialist"),
                start=1,
            )
        ]
        db.session.add_all([operation, department, work_area, *people])
        db.session.commit()

        db.session.execute(text("DROP TABLE staffing_leadership_assignments"))
        db.session.execute(
            text(
                """
                CREATE TABLE staffing_leadership_assignments (
                    id INTEGER PRIMARY KEY,
                    person_id INTEGER NOT NULL,
                    unit_id INTEGER NOT NULL,
                    leadership_level VARCHAR(40) NOT NULL,
                    active BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT ck_staffing_leadership_assignments_level CHECK (
                        leadership_level IN (
                            'work_area_lead', 'department_lead', 'operation_lead',
                            'sort_lead', 'specialist_support'
                        )
                    ),
                    CONSTRAINT uq_staffing_leadership_assignments_exact UNIQUE (
                        person_id, unit_id, leadership_level
                    )
                )
                """
            )
        )
        db.session.execute(
            text(
                """
                INSERT INTO staffing_leadership_assignments
                    (person_id, unit_id, leadership_level)
                VALUES
                    (:first_person, :work_area, 'work_area_lead'),
                    (:second_person, :department, 'department_lead'),
                    (:third_person, :operation, 'specialist_support')
                """
            ),
            {
                "first_person": people[0].id,
                "second_person": people[1].id,
                "third_person": people[2].id,
                "work_area": work_area.id,
                "department": department.id,
                "operation": operation.id,
            },
        )
        db.session.commit()
        self.assertEqual(
            db.session.execute(
                text("SELECT COUNT(*) FROM staffing_leadership_assignments")
            ).scalar(),
            3,
        )

        self.assertTrue(
            _sync_staffing_leadership_level_constraint_sqlite(
                inspect(db.engine),
                set(inspect(db.engine).get_table_names()),
            )
        )
        db.session.commit()

        levels = db.session.execute(
            text(
                "SELECT leadership_level FROM staffing_leadership_assignments "
                "ORDER BY id"
            )
        ).scalars().all()
        create_sql = db.session.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='staffing_leadership_assignments'"
            )
        ).scalar()
        self.assertEqual(levels, ["work_area", "department", "operation"])
        self.assertIn("'work_area'", create_sql)
        self.assertNotIn("'work_area_lead'", create_sql)
        self.assertIn(
            "ck_staffing_leadership_assignments_level",
            {
                constraint["name"]
                for constraint in inspect(db.engine).get_check_constraints(
                    "staffing_leadership_assignments"
                )
            },
        )

    def test_postgres_sync_replaces_legacy_constraint_transactionally(self):
        legacy_result = MagicMock()
        legacy_result.scalar.return_value = (
            "CHECK (leadership_level IN ('work_area_lead', 'department_lead', "
            "'operation_lead', 'sort_lead', 'specialist_support'))"
        )
        valid_result = MagicMock()
        valid_result.scalar.return_value = 0
        with patch(
            "app.services.schema_sync.db.session.execute",
            side_effect=[
                legacy_result,
                valid_result,
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ],
        ) as execute:
            _sync_staffing_leadership_level_constraint_postgres(
                {"staffing_leadership_assignments"}
            )

        statements = "\n".join(str(call.args[0]) for call in execute.call_args_list)
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS ck_staffing_leadership_assignments_level",
            statements,
        )
        self.assertIn("SET leadership_level = unit.unit_type", statements)
        self.assertIn("NOT VALID", statements)
        self.assertIn(
            "VALIDATE CONSTRAINT ck_staffing_leadership_assignments_level",
            statements,
        )


if __name__ == "__main__":
    unittest.main()
