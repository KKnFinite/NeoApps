from datetime import date
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingUnit,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes


class NeoStaffingAttendanceCountsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "AttendanceCountsConfig",
            (),
            {
                "SECRET_KEY": "attendance-counts-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        self.night = StaffingUnit(unit_type="sort", name="Night")
        self.ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=self.night)
        self.shift = StaffingUnit(unit_type="department", name="Shift", parent=self.ramp)
        self.door = StaffingUnit(unit_type="work_area", name="Door 1", parent=self.shift)
        self.ballmat = StaffingUnit(unit_type="work_area", name="Ballmat 1", parent=self.shift)
        self.west_ballmat = StaffingUnit(unit_type="work_area", name="West Ballmat", parent=self.shift)
        self.east_ballmat = StaffingUnit(unit_type="work_area", name="East Ballmat", parent=self.shift)
        self.discharge = StaffingUnit(unit_type="work_area", name="Discharge", parent=self.shift)
        self.outside = StaffingUnit(unit_type="work_area", name="Door Outside", parent=self.ramp)
        db.session.add_all(
            [self.night, self.ramp, self.shift, self.door, self.ballmat,
             self.west_ballmat, self.east_ballmat, self.discharge, self.outside]
        )
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 21),
            gateway_code=self.gateway.code,
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_counts_use_each_canonical_status_once_and_leave_unmarked_on_payroll(self):
        people = {}
        for index, status in enumerate(
            staffing_service.STAFFING_DAILY_ATTENDANCE_STATUSES,
            start=1,
        ):
            person = self._person(f"AC{index:03d}", self.door)
            people[status] = person
            db.session.add(
                StaffingDailyAttendance(
                    attendance_date=self.operation.sort_date,
                    sort_unit_id=self.night.id,
                    sort_date_operation_id=self.operation.id,
                    person_id=person.id,
                    work_area_unit_id=self.door.id,
                    status=status,
                )
            )
        self._person("AC999", self.door)
        self._person("ACOUT", self.outside)
        db.session.commit()

        counts = staffing_service.attendance_staffing_counts(
            self.shift,
            self.operation,
        )

        self.assertEqual(
            counts["on_payroll"],
            len(staffing_service.STAFFING_DAILY_ATTENDANCE_STATUSES) + 1,
        )
        self.assertEqual(counts["working"], 1)
        self.assertEqual(counts["called_in"], 1)
        self.assertEqual(counts["no_call"], 1)
        self.assertEqual(counts["scheduled_off"], 1)
        self.assertEqual(counts["anniversary_day"], 1)
        self.assertEqual(counts["vacation"], 1)
        self.assertEqual(counts["opt_day"], 1)
        self.assertEqual(counts["disability"], 1)
        self.assertEqual(counts["work_comp"], 1)
        self.assertEqual(counts["funeral"], 1)
        self.assertEqual(counts["jury"], 1)
        self.assertEqual(counts["fmla"], 1)
        self.assertEqual(counts["military"], 1)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(counts["personal_leave"], 1)
        self.assertEqual(counts["unmarked"], 1)
        self.assertEqual(
            counts["canonical_status_counts"],
            {status: 1 for status in staffing_service.STAFFING_DAILY_ATTENDANCE_STATUSES},
        )
        self.assertEqual(
            counts["on_payroll"],
            counts["unmarked"] + sum(counts["canonical_status_counts"].values()),
        )

    def test_phase_two_writes_new_statuses_through_shared_attendance_paths(self):
        person = self._person("PHASE200", self.door)
        second_person = self._person("PHASE201", self.door)
        staffing_service.create_shift_flow_plan(
            person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.door.id),
                "shift_flow_ballmat_transition": "",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.door,
        )
        staffing_service.create_shift_flow_plan(
            second_person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.door.id),
                "shift_flow_ballmat_transition": "",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.door,
        )
        db.session.commit()
        writable_values = [
            value for value, _label in staffing_service.attendance_status_choices()
        ]
        self.assertIn("scheduled_off", writable_values)
        self.assertIn("personal_leave", writable_values)
        self.assertLess(writable_values.index("no_call"), writable_values.index("scheduled_off"))
        self.assertLess(writable_values.index("vacation"), writable_values.index("personal_leave"))

        with patch.object(
            staffing_service,
            "current_night_attendance_operation",
            return_value=self.operation,
        ):
            saved = staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(self.operation.id),
                    "work_area_ids": [str(self.door.id)],
                    f"status_{person.id}": "scheduled_off",
                },
                None,
            )
            self.assertEqual(saved, 1)
            db.session.commit()
            record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
            self.assertEqual(record.status, "scheduled_off")

            saved = staffing_service.save_operational_manage_attendance(
                {
                    "sort_date_operation_id": str(self.operation.id),
                    f"status_{person.id}": "personal_leave",
                    f"status_{second_person.id}": "scheduled_off",
                },
                None,
                [self.door.id],
            )
            self.assertEqual(saved, 2)
            db.session.commit()
            self.assertEqual(
                StaffingDailyAttendance.query.filter_by(person_id=person.id).count(),
                1,
            )
            self.assertEqual(record.status, "personal_leave")
            self.assertEqual(
                StaffingDailyAttendance.query.filter_by(person_id=second_person.id).one().status,
                "scheduled_off",
            )

            context = staffing_service.operational_manage_employees_context([self.door.id])
            rows = {row["person"].id: row for row in context["here"]}
            self.assertEqual(rows[person.id]["status_label"], "Personal Leave")
            self.assertTrue(rows[person.id]["status_writable"])
            self.assertEqual(rows[second_person.id]["status_label"], "Scheduled Off")
            self.assertTrue(rows[second_person.id]["status_writable"])
            counts = staffing_service.attendance_staffing_counts(self.shift, self.operation)
            self.assertEqual(counts["personal_leave"], 1)
            self.assertEqual(counts["scheduled_off"], 1)

    def test_final_door_grouping_uses_existing_sort_attendance_not_attendance_location(self):
        person = self._person("FLOW100", self.ballmat)
        staffing_service.create_shift_flow_plan(
            person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.ballmat.id),
                "shift_flow_ballmat_transition": "1",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.ballmat,
        )
        db.session.add(
            StaffingDailyAttendance(
                attendance_date=self.operation.sort_date,
                sort_unit_id=self.night.id,
                sort_date_operation_id=self.operation.id,
                person_id=person.id,
                work_area_unit_id=self.ballmat.id,
                status="here",
            )
        )
        db.session.commit()

        counts = staffing_service.attendance_staffing_counts(
            self.shift,
            self.operation,
            group_by_person_id={person.id: self.door.id},
        )

        self.assertEqual(counts["working"], 1)
        self.assertEqual(counts["groups"][self.door.id]["on_payroll"], 1)
        self.assertEqual(counts["groups"][self.door.id]["working"], 1)
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(person_id=person.id).one().work_area_unit_id,
            self.ballmat.id,
        )

    def test_count_queries_are_bounded_for_the_scope(self):
        for index in range(60):
            self._person(f"BOUND{index:03d}", self.door)
        db.session.commit()
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            counts = staffing_service.attendance_staffing_counts(
                self.shift,
                self.operation,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(counts["on_payroll"], 60)
        self.assertLessEqual(len(statements), 4)

    def test_operational_context_uses_snapshot_then_shift_flow_and_never_relocates_history(self):
        person = self._person("OPS100", self.door)
        plan = staffing_service.create_shift_flow_plan(
            person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.west_ballmat.id),
                "shift_flow_ballmat_transition": "1",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.door,
        )
        db.session.commit()
        with patch.object(staffing_service, "current_night_attendance_operation", return_value=self.operation):
            unmarked = staffing_service.operational_manage_employees_context([self.west_ballmat.id])
            self.assertEqual([row["person"].id for row in unmarked["here"]], [person.id])
            self.assertEqual(unmarked["counts"]["unmarked"], 1)

            db.session.add(
                StaffingDailyAttendance(
                    attendance_date=self.operation.sort_date,
                    sort_unit_id=self.night.id,
                    sort_date_operation_id=self.operation.id,
                    person_id=person.id,
                    work_area_unit_id=self.door.id,
                    status="here",
                )
            )
            db.session.commit()
            plan.sort_start_work_area = self.discharge
            plan.ballmat_transition = None
            db.session.commit()

            door_context = staffing_service.operational_manage_employees_context([self.door.id])
            discharge_context = staffing_service.operational_manage_employees_context([self.discharge.id])
            self.assertEqual([row["person"].id for row in door_context["here"]], [person.id])
            self.assertEqual(discharge_context["here"], [])
            staffing_service.save_operational_manage_attendance(
                {
                    "sort_date_operation_id": str(self.operation.id),
                    f"status_{person.id}": "vacation",
                },
                None,
                [self.door.id],
            )
            db.session.commit()
            record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
            self.assertEqual(record.work_area_unit_id, self.door.id)
            self.assertEqual(record.status, "vacation")
            self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 1)
            with self.assertRaisesRegex(ValueError, "outside the selected attendance areas"):
                staffing_service.save_operational_manage_attendance(
                    {
                        "sort_date_operation_id": str(self.operation.id),
                        f"status_{person.id}": "here",
                    },
                    None,
                    [self.discharge.id],
                )

    def test_operational_context_separates_here_and_coming_and_formats_flow(self):
        here = self._person("OPS200", self.door)
        coming = self._person("OPS201", self.west_ballmat)
        staffing_service.create_shift_flow_plan(
            here,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.door.id),
                "shift_flow_ballmat_transition": "",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.door,
        )
        coming_plan = staffing_service.create_shift_flow_plan(
            coming,
            {
                "shift_flow_setup_work_area_id": str(self.door.id),
                "shift_flow_sort_start_work_area_id": str(self.west_ballmat.id),
                "shift_flow_ballmat_transition": "1",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.door,
        )
        discharge_person = self._person("OPS202", self.discharge)
        discharge_plan = staffing_service.create_shift_flow_plan(
            discharge_person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.discharge.id),
                "shift_flow_ballmat_transition": "",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.discharge,
        )
        db.session.commit()
        with patch.object(staffing_service, "current_night_attendance_operation", return_value=self.operation):
            context = staffing_service.operational_manage_employees_context(
                [self.door.id], later_final_area_ids=[self.door.id]
            )
        self.assertEqual([row["person"].id for row in context["here"]], [here.id])
        self.assertEqual({row["person"].id for row in context["coming"]}, {coming.id, discharge_person.id})
        self.assertEqual(staffing_service.operational_flow_shorthand(coming_plan), "SET D1 → WBM → W1 → D1")
        self.assertEqual(staffing_service.operational_flow_shorthand(discharge_plan), "DIS → D1")

    def test_neosektor_default_uses_supervisor_assignment_and_context_queries_are_bounded(self):
        east_person = self._person("SUP-E", self.east_ballmat)
        west_person = self._person("SUP-W", self.west_ballmat)
        staffing_service.create_shift_flow_plan(
            east_person,
            {
                "shift_flow_setup_work_area_id": "",
                "shift_flow_sort_start_work_area_id": str(self.east_ballmat.id),
                "shift_flow_ballmat_transition": "2",
                "shift_flow_final_door_work_area_id": str(self.door.id),
            },
            self.east_ballmat,
        )
        db.session.commit()
        self.assertEqual(
            staffing_service.neosektor_manage_default_area(
                SimpleNamespace(employee_id=east_person.employee_id)
            ),
            "ebm",
        )
        self.assertEqual(
            staffing_service.neosektor_manage_default_area(
                SimpleNamespace(employee_id=west_person.employee_id)
            ),
            "wbm",
        )
        self.assertEqual(
            staffing_service.neosektor_manage_default_area(
                SimpleNamespace(employee_id="missing")
            ),
            "ebm",
        )
        statements = []
        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(staffing_service, "current_night_attendance_operation", return_value=self.operation):
                staffing_service.operational_manage_employees_context([self.east_ballmat.id])
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(len(statements), 4)

    def _person(self, employee_id, work_area):
        person = staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": "Count",
                "last_name": employee_id,
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "employee_status": "active",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        return person


if __name__ == "__main__":
    unittest.main()
