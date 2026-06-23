from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.services import neostaffing as staffing_service


class NeoStaffingDataFoundationTest(unittest.TestCase):
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

    def test_staffing_person_required_fields_unique_employee_id_and_classification(self):
        person = staffing_service.create_person(
            {
                "employee_id": "E100",
                "first_name": "First",
                "last_name": "Worker",
                "seniority_date": "2020-01-02",
                "classification": "part_time",
            }
        )
        db.session.commit()

        self.assertEqual(person.employee_id, "E100")
        self.assertEqual(person.seniority_date, date(2020, 1, 2))

        with self.assertRaisesRegex(ValueError, "First name is required"):
            staffing_service.create_person(
                {
                    "employee_id": "E101",
                    "first_name": "",
                    "last_name": "Worker",
                    "seniority_date": "2020-01-02",
                    "classification": "part_time",
                }
            )

        with self.assertRaisesRegex(ValueError, "Employee ID already exists"):
            staffing_service.create_person(
                {
                    "employee_id": "E100",
                    "first_name": "Second",
                    "last_name": "Worker",
                    "seniority_date": "2020-01-02",
                    "classification": "part_time",
                }
            )

        with self.assertRaisesRegex(ValueError, "Unsupported classification"):
            staffing_service.create_person(
                {
                    "employee_id": "E102",
                    "first_name": "Bad",
                    "last_name": "Class",
                    "seniority_date": "2020-01-02",
                    "classification": "pilot",
                }
            )

    def test_staffing_unit_hierarchy_validation(self):
        sort = staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Shift Operation", "parent_id": sort.id}
        )
        department = staffing_service.create_unit(
            {"unit_type": "department", "name": "East Shift Department", "parent_id": operation.id}
        )
        work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "EBM",
                "parent_id": department.id,
                "required_headcount": "3",
            }
        )

        self.assertEqual(work_area.parent, department)
        self.assertEqual(work_area.required_headcount, 3)

        with self.assertRaisesRegex(ValueError, "Operation parent must be a Sort"):
            staffing_service.create_unit(
                {"unit_type": "operation", "name": "Bad Operation", "parent_id": department.id}
            )

        with self.assertRaisesRegex(ValueError, "Department parent must be a Operation"):
            staffing_service.create_unit(
                {"unit_type": "department", "name": "Bad Department", "parent_id": sort.id}
            )

        with self.assertRaisesRegex(ValueError, "Work Area parent must be a Department"):
            staffing_service.create_unit(
                {"unit_type": "work_area", "name": "Bad Work Area", "parent_id": operation.id}
            )

        with self.assertRaisesRegex(ValueError, "Sort units cannot have a parent"):
            staffing_service.create_unit(
                {"unit_type": "sort", "name": "Bad Sort", "parent_id": sort.id}
            )

    def test_work_assignment_allows_only_non_management_people_to_work_areas(self):
        _sort, _operation, department, work_area = self._hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        employee = self._person("E200", "part_time")
        combo = self._person("E201", "full_time_combo")
        supervisor = self._person("E202", "part_time_supervisor")

        assignment = staffing_service.assign_work_area(employee, work_area, "2026-06-23")
        self.assertEqual(assignment.work_area, work_area)
        self.assertTrue(assignment.active)
        self.assertEqual(assignment.effective_date, date(2026, 6, 23))
        staffing_service.assign_work_area(employee, second_work_area)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=employee.id).count(), 1)
        self.assertEqual(employee.work_assignment.work_area, second_work_area)
        self.assertTrue(employee.work_assignment.active)

        self.assertEqual(staffing_service.assign_work_area(combo, work_area).work_area, work_area)

        with self.assertRaisesRegex(ValueError, "Only part time and full time combo"):
            staffing_service.assign_work_area(supervisor, work_area)

        with self.assertRaisesRegex(ValueError, "Work Area units"):
            staffing_service.assign_work_area(employee, department)

    def test_leadership_assignment_rules_duplicates_and_multiple_scopes(self):
        sort, operation, department, work_area = self._hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        part_time_supervisor = self._person("E300", "part_time_supervisor")
        full_time_supervisor = self._person("E301", "full_time_supervisor")
        specialist = self._person("E302", "full_time_specialist")
        manager = self._person("E303", "manager")
        division_manager = self._person("E304", "division_manager")

        first = staffing_service.create_leadership_assignment(part_time_supervisor, work_area)
        second = staffing_service.create_leadership_assignment(part_time_supervisor, second_work_area)
        self.assertEqual(first.leadership_level, "work_area")
        self.assertEqual(second.leadership_level, "work_area")
        self.assertEqual(
            StaffingLeadershipAssignment.query.filter_by(person_id=part_time_supervisor.id).count(),
            2,
        )
        self.assertEqual(
            staffing_service.create_leadership_assignment(full_time_supervisor, department).leadership_level,
            "department",
        )
        self.assertEqual(
            staffing_service.create_leadership_assignment(specialist, department).leadership_level,
            "department",
        )
        self.assertEqual(
            staffing_service.create_leadership_assignment(specialist, operation).leadership_level,
            "operation",
        )
        self.assertEqual(
            staffing_service.create_leadership_assignment(manager, operation).leadership_level,
            "operation",
        )
        self.assertEqual(
            staffing_service.create_leadership_assignment(division_manager, sort).leadership_level,
            "sort",
        )

        with self.assertRaisesRegex(ValueError, "already exists"):
            staffing_service.create_leadership_assignment(part_time_supervisor, work_area)

        with self.assertRaisesRegex(ValueError, "cannot lead"):
            staffing_service.create_leadership_assignment(manager, work_area)

        with self.assertRaisesRegex(ValueError, "must match"):
            staffing_service.create_leadership_assignment(
                full_time_supervisor,
                department,
                "operation",
            )

    def test_classification_change_removes_invalid_assignments(self):
        _sort, operation, department, work_area = self._hierarchy()
        employee = self._person("E400", "part_time")
        staffing_service.assign_work_area(employee, work_area)

        staffing_service.update_person(
            employee,
            {
                "employee_id": employee.employee_id,
                "first_name": employee.first_name,
                "last_name": employee.last_name,
                "seniority_date": employee.seniority_date,
                "classification": "manager",
            },
        )
        self.assertFalse(employee.work_assignment.active)

        staffing_service.create_leadership_assignment(employee, operation)
        staffing_service.update_person(
            employee,
            {
                "employee_id": employee.employee_id,
                "first_name": employee.first_name,
                "last_name": employee.last_name,
                "seniority_date": employee.seniority_date,
                "classification": "part_time",
            },
        )
        self.assertTrue(all(not assignment.active for assignment in employee.leadership_assignments))

    def test_assignment_deactivation_and_reactivation(self):
        _sort, operation, department, work_area = self._hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        employee = self._person("E450", "part_time")
        manager = self._person("E451", "manager")

        work_assignment = staffing_service.assign_work_area(employee, work_area)
        staffing_service.clear_work_assignment(employee)
        self.assertFalse(work_assignment.active)

        reactivated = staffing_service.assign_work_area(employee, second_work_area)
        self.assertEqual(reactivated.id, work_assignment.id)
        self.assertTrue(reactivated.active)
        self.assertEqual(reactivated.work_area, second_work_area)

        leadership = staffing_service.create_leadership_assignment(manager, operation)
        staffing_service.delete_leadership_assignment(leadership)
        self.assertFalse(leadership.active)
        reactivated_leadership = staffing_service.create_leadership_assignment(manager, operation)
        self.assertEqual(reactivated_leadership.id, leadership.id)
        self.assertTrue(reactivated_leadership.active)

    def test_delete_unit_protections(self):
        _sort, _operation, department, work_area = self._hierarchy()
        employee = self._person("E500", "part_time")
        supervisor = self._person("E501", "part_time_supervisor")
        staffing_service.assign_work_area(employee, work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)

        with self.assertRaisesRegex(ValueError, "child units"):
            staffing_service.delete_unit(department)

        with self.assertRaisesRegex(ValueError, "work assignments"):
            staffing_service.delete_unit(work_area)

        staffing_service.clear_work_assignment(employee)
        with self.assertRaisesRegex(ValueError, "leadership assignments"):
            staffing_service.delete_unit(work_area)

        assignment = work_area.leadership_assignments[0]
        staffing_service.delete_leadership_assignment(assignment)
        staffing_service.delete_unit(work_area)
        db.session.flush()
        self.assertIsNone(db.session.get(StaffingUnit, work_area.id))

    def test_seniority_context_traverses_operation_and_ranks_by_seniority(self):
        _sort, operation, department, work_area = self._hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        junior = self._person_with_name("E602", "part_time", "Junior", "Worker", "2022-01-01")
        senior = self._person_with_name("E601", "full_time_combo", "Senior", "Worker", "2019-01-01")
        tie = self._person_with_name("E600", "part_time", "Tie", "Worker", "2019-01-01")
        staffing_service.assign_work_area(junior, work_area)
        staffing_service.assign_work_area(senior, second_work_area)
        staffing_service.assign_work_area(tie, work_area)

        context = staffing_service.seniority_context({"operation_id": str(operation.id)})

        self.assertEqual([row["person"].employee_id for row in context["rows"]], ["E600", "E601", "E602"])
        self.assertEqual([row["rank"] for row in context["rows"]], [1, 2, 3])
        self.assertEqual(context["counts"]["total"], 3)
        self.assertEqual(context["counts"]["part_time"], 2)
        self.assertEqual(context["counts"]["combo"], 1)

    def test_seniority_context_filters_by_classification_work_area_and_search(self):
        _sort, operation, department, work_area = self._hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        avery = self._person_with_name("E610", "part_time", "Avery", "Spotter", "2020-01-01")
        morgan = self._person_with_name("E611", "full_time_combo", "Morgan", "Loader", "2021-01-01")
        staffing_service.assign_work_area(avery, work_area)
        staffing_service.assign_work_area(morgan, second_work_area)

        classification = staffing_service.seniority_context(
            {"operation_id": str(operation.id), "classification": "full_time_combo"}
        )
        self.assertEqual([row["person"].employee_id for row in classification["rows"]], ["E611"])

        work_area_filtered = staffing_service.seniority_context(
            {"operation_id": str(operation.id), "work_area_id": str(work_area.id)}
        )
        self.assertEqual([row["person"].employee_id for row in work_area_filtered["rows"]], ["E610"])

        search_filtered = staffing_service.seniority_context(
            {"operation_id": str(operation.id), "search": "avery"}
        )
        self.assertEqual([row["person"].employee_id for row in search_filtered["rows"]], ["E610"])

    def test_seniority_context_excludes_and_includes_management(self):
        _sort, operation, department, work_area = self._hierarchy()
        employee = self._person_with_name("E620", "part_time", "Worker", "One", "2020-01-01")
        supervisor = self._person_with_name(
            "E621",
            "part_time_supervisor",
            "Supervisor",
            "One",
            "2018-01-01",
        )
        staffing_service.assign_work_area(employee, work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)

        excluded = staffing_service.seniority_context({"operation_id": str(operation.id)})
        self.assertEqual([row["person"].employee_id for row in excluded["rows"]], ["E620"])
        self.assertEqual(excluded["counts"]["supervisors"], 0)

        included = staffing_service.seniority_context(
            {"operation_id": str(operation.id), "include_management": "1"}
        )
        self.assertEqual([row["person"].employee_id for row in included["rows"]], ["E621", "E620"])
        self.assertEqual(included["counts"]["supervisors"], 1)

    def test_seniority_context_ignores_inactive_assignments_by_default(self):
        _sort, operation, _department, work_area = self._hierarchy()
        employee = self._person("E630", "part_time")
        staffing_service.assign_work_area(employee, work_area)
        staffing_service.clear_work_assignment(employee)

        context = staffing_service.seniority_context({"operation_id": str(operation.id)})

        self.assertEqual(context["rows"], [])

    def test_seniority_context_defaults_only_when_one_operation_is_available(self):
        sort, operation, _department, _work_area = self._hierarchy()

        single = staffing_service.seniority_context({"sort_id": str(sort.id)})
        self.assertEqual(single["selected_operation"], operation)

        second_operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Ramp Operation", "parent_id": sort.id}
        )
        db.session.flush()

        multiple = staffing_service.seniority_context({"sort_id": str(sort.id)})
        self.assertIsNone(multiple["selected_operation"])
        self.assertIn(operation, multiple["operations"])
        self.assertIn(second_operation, multiple["operations"])

    def _hierarchy(self):
        sort = staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Shift Operation", "parent_id": sort.id}
        )
        department = staffing_service.create_unit(
            {"unit_type": "department", "name": "East Shift Department", "parent_id": operation.id}
        )
        work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "EBM", "parent_id": department.id}
        )
        return sort, operation, department, work_area

    def _person(self, employee_id, classification):
        return staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": "Test",
                "last_name": employee_id,
                "seniority_date": "2020-01-02",
                "classification": classification,
            }
        )

    def _person_with_name(self, employee_id, classification, first_name, last_name, seniority_date):
        return staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": first_name,
                "last_name": last_name,
                "seniority_date": seniority_date,
                "classification": classification,
            }
        )


if __name__ == "__main__":
    unittest.main()
