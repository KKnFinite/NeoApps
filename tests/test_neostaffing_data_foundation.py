from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    StaffingDailyAttendance,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
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
        self.assertEqual(person.roster_status, "active")

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

        with self.assertRaisesRegex(ValueError, "Unsupported roster status"):
            staffing_service.create_person(
                {
                    "employee_id": "E103",
                    "first_name": "Bad",
                    "last_name": "Status",
                    "seniority_date": "2020-01-02",
                    "classification": "part_time",
                    "roster_status": "daily_call_in",
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
        direct_work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "Load Planning",
                "parent_id": operation.id,
            }
        )
        self.assertEqual(direct_work_area.parent, operation)

        with self.assertRaisesRegex(ValueError, "Operation parent must be a Sort"):
            staffing_service.create_unit(
                {"unit_type": "operation", "name": "Bad Operation", "parent_id": department.id}
            )

        with self.assertRaisesRegex(ValueError, "Department parent must be a Operation"):
            staffing_service.create_unit(
                {"unit_type": "department", "name": "Bad Department", "parent_id": sort.id}
            )

        with self.assertRaisesRegex(ValueError, "Work Area parent must be a Department or Operation"):
            staffing_service.create_unit(
                {"unit_type": "work_area", "name": "Bad Work Area", "parent_id": sort.id}
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

    def test_dashboard_context_calculates_coverage_and_required_defaults(self):
        _sort, _operation, department, work_area = self._hierarchy()
        staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": department.id,
                "required_headcount": "4",
            },
        )
        default_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        for index in range(2):
            staffing_service.assign_work_area(self._person(f"E55{index}", "part_time"), work_area)
        staffing_service.assign_work_area(self._person("E553", "full_time_combo"), default_area)

        context = staffing_service.dashboard_context()
        cards = {card["unit"].name: card for card in context["work_area_cards"]}

        self.assertEqual(cards["EBM"]["assigned"], 2)
        self.assertEqual(cards["EBM"]["required"], 4)
        self.assertEqual(cards["EBM"]["open"], 2)
        self.assertEqual(cards["EBM"]["coverage"], 50)
        self.assertEqual(cards["EBM"]["status"], "Understaffed")
        self.assertEqual(cards["EBM"]["status_color"], "red")
        self.assertTrue(cards["EBM"]["required_configured"])
        self.assertEqual(cards["WBM"]["required"], 1)
        self.assertEqual(cards["WBM"]["open"], 0)
        self.assertEqual(cards["WBM"]["coverage"], 100)
        self.assertFalse(cards["WBM"]["required_configured"])
        self.assertEqual(context["summary"]["total_employees"], 3)
        self.assertEqual(context["summary"]["total_required"], 5)
        self.assertEqual(context["summary"]["total_open"], 2)
        self.assertEqual(context["summary"]["understaffed_work_areas"], 1)

    def test_dashboard_context_rolls_up_department_operation_and_sort(self):
        sort, operation, department, work_area = self._hierarchy()
        staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": department.id,
                "required_headcount": "2",
            },
        )
        second_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "WBM",
                "parent_id": department.id,
                "required_headcount": "3",
            }
        )
        staffing_service.assign_work_area(self._person("E560", "part_time"), work_area)
        for index in range(3):
            staffing_service.assign_work_area(self._person(f"E57{index}", "part_time"), second_area)

        context = staffing_service.dashboard_context({"operation_id": str(operation.id)})

        for rollup_key in ("sorts", "operations", "departments"):
            rollup = context["rollups"][rollup_key][0]
            self.assertEqual(rollup["assigned"], 4)
            self.assertEqual(rollup["required"], 5)
            self.assertEqual(rollup["open"], 1)
            self.assertEqual(rollup["coverage"], 80)
        self.assertEqual(context["rollups"]["sorts"][0]["unit"], sort)
        self.assertEqual(context["rollups"]["operations"][0]["unit"], operation)
        self.assertEqual(context["rollups"]["departments"][0]["unit"], department)

    def test_dashboard_context_detects_leadership_and_filters_board_cards(self):
        sort, operation, department, work_area = self._hierarchy()
        second_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "WBM",
                "parent_id": department.id,
                "required_headcount": "3",
            }
        )
        staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": department.id,
                "required_headcount": "1",
            },
        )
        staffing_service.assign_work_area(self._person("E580", "part_time"), work_area)
        staffing_service.assign_work_area(self._person("E581", "part_time"), second_area)
        staffing_service.create_leadership_assignment(self._person("E582", "part_time_supervisor"), work_area)
        staffing_service.create_leadership_assignment(self._person("E583", "full_time_supervisor"), department)
        staffing_service.create_leadership_assignment(self._person("E584", "manager"), operation)
        staffing_service.create_leadership_assignment(self._person("E585", "division_manager"), sort)

        context = staffing_service.dashboard_context()
        cards = {card["unit"].name: card for card in context["work_area_cards"]}

        self.assertFalse(cards["EBM"]["has_missing_leadership"])
        self.assertEqual(cards["EBM"]["leadership"]["pt_supervisors"], 1)
        self.assertEqual(cards["EBM"]["leadership"]["ft_supervisors"], 1)
        self.assertEqual(cards["EBM"]["leadership"]["managers"], 1)
        self.assertEqual(cards["EBM"]["leadership"]["division_managers"], 1)
        self.assertTrue(cards["WBM"]["has_missing_leadership"])
        self.assertIn("PT Supervisor", cards["WBM"]["missing_leadership"])

        missing_only = staffing_service.dashboard_context({"missing_leadership_only": "1"})
        self.assertEqual([card["unit"].name for card in missing_only["work_area_cards"]], ["WBM"])

        understaffed_only = staffing_service.dashboard_context({"understaffed_only": "1"})
        self.assertEqual([card["unit"].name for card in understaffed_only["work_area_cards"]], ["WBM"])

        searched = staffing_service.dashboard_context({"search": "ebm"})
        self.assertEqual([card["unit"].name for card in searched["work_area_cards"]], ["EBM"])

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

    def test_people_context_filters_by_hierarchy_classification_and_status(self):
        sort, operation, department, work_area = self._hierarchy()
        second_operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Ramp Operation", "parent_id": sort.id}
        )
        ramp_department = staffing_service.create_unit(
            {"unit_type": "department", "name": "Ramp Department", "parent_id": second_operation.id}
        )
        ramp_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "Ramp", "parent_id": ramp_department.id}
        )
        east_employee = self._person_with_name("E700", "part_time", "Avery", "East", "2020-01-01")
        combo_employee = self._person_with_name("E701", "full_time_combo", "Morgan", "Combo", "2020-01-02")
        inactive_employee = self._person_with_name("E702", "part_time", "Inactive", "Worker", "2020-01-03")
        ramp_employee = self._person_with_name("E703", "part_time", "Ramp", "Worker", "2020-01-04")
        inactive_employee.active = False
        staffing_service.assign_work_area(east_employee, work_area)
        staffing_service.assign_work_area(combo_employee, work_area)
        staffing_service.assign_work_area(inactive_employee, work_area)
        staffing_service.assign_work_area(ramp_employee, ramp_area)

        operation_context = staffing_service.people_context({"operation_id": str(operation.id)})
        self.assertEqual(
            [row["person"].employee_id for row in operation_context["rows"]],
            ["E701", "E700"],
        )
        self.assertEqual(operation_context["counts"]["active"], 2)

        combo_context = staffing_service.people_context(
            {"operation_id": str(operation.id), "classification": "full_time_combo"}
        )
        self.assertEqual([row["person"].employee_id for row in combo_context["rows"]], ["E701"])

        inactive_context = staffing_service.people_context(
            {"operation_id": str(operation.id), "active": "inactive"}
        )
        self.assertEqual([row["person"].employee_id for row in inactive_context["rows"]], ["E702"])
        self.assertEqual(inactive_context["counts"]["inactive"], 1)

        work_area_context = staffing_service.people_context({"work_area_id": str(work_area.id)})
        self.assertEqual(
            [row["person"].employee_id for row in work_area_context["rows"]],
            ["E701", "E700"],
        )
        self.assertEqual(work_area_context["selected_sort"], sort)

    def test_people_context_searches_by_employee_id_and_name(self):
        _sort, operation, _department, work_area = self._hierarchy()
        avery = self._person_with_name("E710", "part_time", "Avery", "Spotter", "2020-01-01")
        morgan = self._person_with_name("E711", "part_time", "Morgan", "Loader", "2020-01-02")
        staffing_service.assign_work_area(avery, work_area)
        staffing_service.assign_work_area(morgan, work_area)

        by_id = staffing_service.people_context({"operation_id": str(operation.id), "search": "E710"})
        by_name = staffing_service.people_context({"operation_id": str(operation.id), "search": "loader"})

        self.assertEqual([row["person"].employee_id for row in by_id["rows"]], ["E710"])
        self.assertEqual([row["person"].employee_id for row in by_name["rows"]], ["E711"])

    def test_people_context_leadership_only_and_detail_data(self):
        sort, operation, department, work_area = self._hierarchy()
        employee = self._person_with_name("E720", "part_time", "Worker", "One", "2020-01-01")
        supervisor = self._person_with_name(
            "E721",
            "part_time_supervisor",
            "Supervisor",
            "One",
            "2019-01-01",
        )
        manager = self._person_with_name("E722", "manager", "Manager", "One", "2018-01-01")
        staffing_service.assign_work_area(employee, work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)
        staffing_service.create_leadership_assignment(manager, operation)

        context = staffing_service.people_context(
            {"sort_id": str(sort.id), "leadership_only": "1", "person_id": str(supervisor.id)}
        )

        self.assertEqual([row["person"].employee_id for row in context["rows"]], ["E722", "E721"])
        self.assertEqual(context["counts"]["supervisors"], 1)
        self.assertEqual(context["counts"]["managers"], 1)
        self.assertEqual(context["selected_person"]["person"], supervisor)
        self.assertEqual(context["selected_person"]["leadership_labels"][0]["label"], "Work Area Supervisor")
        self.assertEqual(context["selected_person"]["leadership_labels"][0]["unit"], work_area)
        self.assertEqual(context["selected_person"]["seniority_operation"], operation)

    def test_daily_attendance_is_separate_from_roster_status_and_unique(self):
        sort, _operation, _department, work_area = self._hierarchy()
        employee = self._person_with_name("E730", "part_time", "Attend", "Worker", "2020-01-01")
        employee.roster_status = "fmla"
        staffing_service.assign_work_area(employee, work_area)
        user = User(username="recorder", employee_id="REC1", role="watcher", password_hash="x")
        db.session.add(user)
        db.session.flush()

        saved = staffing_service.save_attendance(
            {
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                f"status_{employee.id}": "call_in",
                f"note_{employee.id}": "Called supervisor",
            },
            user,
        )
        second_saved = staffing_service.save_attendance(
            {
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                f"status_{employee.id}": "here",
            },
            user,
        )
        db.session.commit()

        self.assertEqual(saved, 1)
        self.assertEqual(second_saved, 1)
        self.assertEqual(employee.roster_status, "fmla")
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(person_id=employee.id).count(),
            1,
        )
        record = StaffingDailyAttendance.query.filter_by(person_id=employee.id).first()
        self.assertEqual(record.status, "here")
        self.assertEqual(record.attendance_date, date(2026, 7, 3))
        self.assertEqual(record.sort_unit_id, sort.id)
        self.assertEqual(record.work_area_unit_id, work_area.id)

    def test_management_attendance_shortcut_resolves_user_person_assignment(self):
        _sort, _operation, _department, work_area = self._hierarchy()
        supervisor = self._person_with_name(
            "M100",
            "part_time_supervisor",
            "Pat",
            "Supervisor",
            "2018-01-01",
        )
        staffing_service.create_leadership_assignment(supervisor, work_area)
        user = User(
            username="ptsup",
            employee_id="M100",
            role="watcher",
            password_hash="x",
            is_management=True,
            management_level="part_time_supervisor",
        )
        db.session.add(user)
        db.session.flush()

        context = staffing_service.management_attendance_context_for_user(user)
        missing = staffing_service.management_attendance_context_for_user(
            User(
                username="missing-person",
                employee_id="M404",
                role="watcher",
                password_hash="x",
                is_management=True,
                management_level="manager",
            )
        )

        self.assertTrue(context["is_management"])
        self.assertEqual(context["person"], supervisor)
        self.assertEqual(context["assignments"][0]["unit"], work_area)
        self.assertEqual(context["assignments"][0]["scope_key"], "work_area_id")
        self.assertIn("Create a matching PEOPLE record", missing["message"])

    def test_seniority_report_operation_includes_direct_and_department_work_areas(self):
        sort = staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Shift Operation", "parent_id": sort.id}
        )
        department = staffing_service.create_unit(
            {"unit_type": "department", "name": "East Department", "parent_id": operation.id}
        )
        nested_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "EBM", "parent_id": department.id}
        )
        direct_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "Load Planning", "parent_id": operation.id}
        )
        senior = self._person_with_name("E740", "part_time", "Senior", "Direct", "2019-01-01")
        junior = self._person_with_name("E741", "part_time", "Junior", "Nested", "2020-01-01")
        staffing_service.assign_work_area(junior, nested_area)
        staffing_service.assign_work_area(senior, direct_area)

        context = staffing_service.seniority_context({"operation_id": str(operation.id)})

        self.assertEqual([row["person"].employee_id for row in context["rows"]], ["E740", "E741"])
        self.assertEqual({row["work_area"].name for row in context["rows"]}, {"Load Planning", "EBM"})

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
