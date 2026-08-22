from datetime import date
import unittest

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
        self.outside = StaffingUnit(unit_type="work_area", name="Door Outside", parent=self.ramp)
        db.session.add_all(
            [self.night, self.ramp, self.shift, self.door, self.ballmat, self.outside]
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

        self.assertEqual(counts["on_payroll"], 14)
        self.assertEqual(counts["working"], 1)
        self.assertEqual(counts["called_in"], 1)
        self.assertEqual(counts["no_call"], 1)
        self.assertEqual(counts["scheduled_off"], 1)
        self.assertEqual(counts["vacation"], 1)
        self.assertEqual(counts["opt_day"], 1)
        self.assertEqual(counts["disability"], 1)
        self.assertEqual(counts["work_comp"], 1)
        self.assertEqual(counts["funeral"], 1)
        self.assertEqual(counts["jury"], 1)
        self.assertEqual(counts["fmla"], 1)
        self.assertEqual(counts["military"], 1)
        self.assertEqual(counts["cleared"], 1)
        self.assertEqual(counts["personal_leave"], 0)
        self.assertEqual(counts["unmarked"], 1)
        self.assertEqual(
            counts["canonical_status_counts"],
            {status: 1 for status in staffing_service.STAFFING_DAILY_ATTENDANCE_STATUSES},
        )
        self.assertEqual(
            counts["on_payroll"],
            counts["unmarked"] + sum(counts["canonical_status_counts"].values()),
        )

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
