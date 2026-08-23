from datetime import date
from pathlib import Path
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes


class NeoStaffingAttendanceDeepLinkTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "AttendanceDeepLinkConfig",
            (),
            {
                "SECRET_KEY": "attendance-deep-link-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.gateway = ensure_default_gateway_and_nodes()
        self.night = StaffingUnit(unit_type="sort", name="Night")
        self.ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=self.night)
        self.shift = StaffingUnit(unit_type="department", name="Shift", parent=self.ramp)
        self.door_one = StaffingUnit(unit_type="work_area", name="Door 1", parent=self.shift)
        self.door_two = StaffingUnit(unit_type="work_area", name="Door 2", parent=self.shift)
        self.west_ballmat = StaffingUnit(unit_type="work_area", name="West Ballmat", parent=self.shift)
        self.east_ballmat = StaffingUnit(unit_type="work_area", name="East Ballmat", parent=self.shift)
        self.other_sort = StaffingUnit(unit_type="sort", name="Day")
        self.other_area = StaffingUnit(unit_type="work_area", name="Door 1", parent=self.other_sort)
        db.session.add_all([
            self.night, self.ramp, self.shift, self.door_one, self.door_two,
            self.west_ballmat, self.east_ballmat, self.other_sort, self.other_area,
        ])
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 21),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_one_and_multiple_work_area_ids_scope_only_the_initial_attendance_rows(self):
        first = self._person("DL100", self.door_one)
        second = self._person("DL101", self.door_two)
        self._person("DL102", self.west_ballmat)
        db.session.commit()

        one = staffing_service.attendance_context(
            {"work_area_ids": [str(self.door_one.id)]}
        )
        multiple = staffing_service.attendance_context(
            {"work_area_ids": [str(self.door_one.id), str(self.door_two.id)]}
        )

        self.assertTrue(one["ready"])
        self.assertEqual([row["person"].id for row in one["rows"]], [first.id])
        self.assertEqual(
            {row["person"].id for row in multiple["rows"]},
            {first.id, second.id},
        )
        self.assertEqual(multiple["selected_work_area_ids"], [self.door_one.id, self.door_two.id])

    def test_invalid_or_other_sort_deep_link_is_safe_and_does_not_write_data(self):
        before_assignments = StaffingWorkAssignment.query.count()
        before_attendance = StaffingDailyAttendance.query.count()

        invalid = staffing_service.attendance_context({"work_area_ids": ["not-an-id"]})
        other_sort = staffing_service.attendance_context(
            {"work_area_ids": [str(self.other_area.id)]}
        )

        self.assertFalse(invalid["ready"])
        self.assertFalse(other_sort["ready"])
        self.assertEqual(StaffingWorkAssignment.query.count(), before_assignments)
        self.assertEqual(StaffingDailyAttendance.query.count(), before_attendance)

    def test_ermac_door_and_sektor_ballmat_resolvers_return_current_sort_ids(self):
        self.assertEqual(
            staffing_service.attendance_deep_link_work_area_ids(
                ["D1", "D2"], self.operation
            ),
            [self.door_one.id, self.door_two.id],
        )
        self.assertEqual(
            staffing_service.attendance_deep_link_work_area_ids(
                ["West Ballmat", "East Ballmat"], self.operation
            ),
            [self.west_ballmat.id, self.east_ballmat.id],
        )

    def test_cross_app_links_and_target_authorization_contracts_are_present(self):
        ermac_template = Path("app/templates/neonodes/neoermac/door_view.html").read_text()
        sektor_template = Path("app/templates/neonodes/neosektor/live_counts.html").read_text()
        tunnel_template = Path("app/templates/neonodes/neosektor/tunnel_conductor.html").read_text()
        manage_template = Path("app/templates/neostaffing/operational_manage_employees.html").read_text()
        base_css = Path("app/static/css/base.css").read_text()
        ermac_route = Path("app/neonodes/neoermac/routes.py").read_text()
        sektor_route = Path("app/neonodes/neosektor/routes.py").read_text()

        self.assertIn("MANAGE EMPLOYEES", ermac_template)
        self.assertIn("_current_user_supervised_doors", ermac_route)
        self.assertIn("MANAGE EMPLOYEES", sektor_template)
        self.assertNotIn("BALLMAT ATTENDANCE", sektor_template)
        self.assertIn('names = {"dis": "Discharge", "ebm": "East Ballmat", "wbm": "West Ballmat"}', sektor_route)
        self.assertIn("attendance_deep_link_work_area_ids", sektor_route)
        self.assertIn("can_manage_employees", sektor_template)
        self.assertIn("area='dis'", tunnel_template)
        self.assertIn("_can_manage_employees", sektor_route)
        self.assertIn("current_user.management_level in MANAGEMENT_LEVELS", sektor_route)
        self.assertLess(sektor_route.index('"dis": "Discharge"'), sektor_route.index('"ebm": "East Ballmat"'))
        self.assertLess(sektor_route.index('"ebm": "East Ballmat"'), sektor_route.index('"wbm": "West Ballmat"'))
        self.assertIn("ATTENDANCE HERE", manage_template)
        self.assertIn("COMING TO THESE DOORS", manage_template)
        self.assertIn("attendance.status_choices", manage_template)
        self.assertIn("Employee</th><th>Attendance</th><th>Flow", manage_template)
        self.assertIn("neostaffing-operational-status is-readonly", manage_template)
        self.assertIn("data-operational-manage-employees", manage_template)
        self.assertIn("attendance_scope_label", ermac_route)
        self.assertIn("attendance_scope_label", sektor_route)
        self.assertIn('{"dis": "DISCHARGE", "ebm": "EBM", "wbm": "WBM"}', sektor_route)
        manage_css = base_css.split(
            "/* Shared Ermac/Sektor Manage Employees operations console. */", 1
        )[1]
        self.assertIn(".neostaffing-operational-rosters.has-coming", manage_css)
        self.assertIn("grid-template-columns: minmax(0, 1.08fr) minmax(0, .92fr)", manage_css)
        self.assertIn("width: 100%;", manage_css.split("body:has", 1)[0])
        self.assertNotIn("width: 100vw;", manage_css.split("body:has", 1)[0])
        response = self.client.get(
            f"/neostaffing/attendance?work_area_ids={self.door_one.id}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def _person(self, employee_id, work_area):
        person = staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": "Deep",
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
