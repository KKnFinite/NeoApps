from datetime import date, datetime, timedelta
from pathlib import Path
import unittest

from flask import g
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingDailyAttendanceTest(unittest.TestCase):
    OPERATION_DATE = date(2026, 8, 15)

    def setUp(self):
        config = type(
            "NeoStaffingDailyAttendanceConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(2026, 8, 15, 21, 0),
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_missing_night_operation_is_disabled_and_does_not_create_one(self):
        self._hierarchy()
        before = SortDateOperation.query.count()

        context = staffing_service.attendance_context()

        self.assertFalse(context["ready"])
        self.assertEqual(context["message"], "NIGHT SORT HAS NOT BEEN CREATED YET.")
        self.assertEqual(SortDateOperation.query.count(), before)
        self.assertFalse(db.session.new)

    def test_missing_night_operation_route_performs_no_persistent_write(self):
        self._hierarchy()
        viewer = self._user("missing_operation_viewer", "watcher")
        db.session.commit()
        self._login(viewer.username)
        dml = []

        def capture_dml(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE"}:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_dml)
        try:
            response = self.client.get("/neostaffing/attendance")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_dml)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NIGHT SORT HAS NOT BEEN CREATED YET.", response.data)
        self.assertEqual(dml, [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_existing_current_night_operation_and_its_date_are_authoritative(self):
        self._hierarchy()
        operation = self._night_operation()

        context = staffing_service.attendance_context(
            {"attendance_date": "1999-01-01"}
        )

        self.assertTrue(context["ready"])
        self.assertIs(context["sort_date_operation"], operation)
        self.assertEqual(context["attendance_date"], operation.sort_date)

    def test_ambiguous_staffing_sort_mapping_disables_attendance(self):
        self._hierarchy(sort_name="Night")
        staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        self._night_operation()

        context = staffing_service.attendance_context()

        self.assertFalse(context["ready"])
        self.assertIn("Multiple active NeoStaffing Sorts", context["message"])

    def test_unmarked_summary_rollups_and_placement_snapshots(self):
        staffing_sort, operation_unit, department, nested_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        nested = self._person("DS100", nested_area)
        direct = self._person("DS101", direct_area)
        recorder = self._user("snapshot_recorder", "operator")
        db.session.commit()

        before = staffing_service.attendance_context(
            {"operation_id": str(operation_unit.id)}
        )
        self.assertEqual(before["summary"]["total_roster"], 2)
        self.assertEqual(before["summary"]["here"], 0)
        self.assertEqual(before["summary"]["absent"], 0)
        self.assertEqual(before["summary"]["unmarked"], 2)
        self.assertEqual({row["status"] for row in before["rows"]}, {""})

        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "attendance_date": "1999-01-01",
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{nested.id}": "here",
                f"status_{direct.id}": "call_in",
            },
            recorder,
        )
        db.session.commit()

        records = {
            record.person_id: record for record in StaffingDailyAttendance.query.all()
        }
        self.assertEqual(records[nested.id].attendance_date, operation.sort_date)
        self.assertEqual(records[nested.id].sort_date_operation_id, operation.id)
        self.assertEqual(records[nested.id].work_area_unit_id, nested_area.id)
        self.assertEqual(records[nested.id].department_unit_id, department.id)
        self.assertEqual(records[nested.id].operation_unit_id, operation_unit.id)
        self.assertEqual(records[direct.id].work_area_unit_id, direct_area.id)
        self.assertIsNone(records[direct.id].department_unit_id)
        self.assertEqual(records[direct.id].operation_unit_id, operation_unit.id)

        after = staffing_service.attendance_context(
            {"operation_id": str(operation_unit.id)}
        )
        self.assertEqual(after["summary"]["here"], 1)
        self.assertEqual(after["summary"]["absent"], 1)
        self.assertEqual(after["summary"]["unmarked"], 0)
        self.assertEqual(after["rollups"]["department"][0]["total_roster"], 1)
        self.assertEqual(after["rollups"]["operation"][0]["total_roster"], 2)

    def test_status_edit_preserves_original_placement_snapshot(self):
        staffing_sort, operation_unit, department, first_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        person = self._person("HS100", first_area)
        recorder = self._user("history_recorder", "operator")
        db.session.commit()
        payload = {
            "sort_date_operation_id": str(operation.id),
            "sort_id": str(staffing_sort.id),
            "operation_id": str(operation_unit.id),
            f"status_{person.id}": "here",
        }
        staffing_service.save_attendance(payload, recorder)
        db.session.commit()

        staffing_service.assign_work_area(person, direct_area)
        staffing_service.save_attendance(
            {**payload, f"status_{person.id}": "vacation"},
            recorder,
        )
        db.session.commit()

        record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
        self.assertEqual(record.status, "vacation")
        self.assertEqual(record.work_area_unit_id, first_area.id)
        self.assertEqual(record.department_unit_id, department.id)
        self.assertEqual(record.operation_unit_id, operation_unit.id)

        direct_person = self._person("HS101", direct_area)
        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{direct_person.id}": "here",
            },
            recorder,
        )
        db.session.flush()
        staffing_service.assign_work_area(direct_person, first_area)
        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{direct_person.id}": "call_in",
            },
            recorder,
        )
        direct_record = StaffingDailyAttendance.query.filter_by(
            person_id=direct_person.id
        ).one()
        self.assertEqual(direct_record.work_area_unit_id, direct_area.id)
        self.assertIsNone(direct_record.department_unit_id)

    def test_all_here_and_clear_to_unmarked_are_operation_scoped(self):
        staffing_sort, operation_unit, _department, work_area, _direct_area = self._hierarchy()
        operation = self._night_operation()
        first = self._person("AH200", work_area)
        second = self._person("AH201", work_area)
        recorder = self._user("all_here_recorder", "operator")
        historical = StaffingDailyAttendance(
            person_id=first.id,
            attendance_date=operation.sort_date - timedelta(days=1),
            sort_unit_id=staffing_sort.id,
            work_area_unit_id=work_area.id,
            status="vacation",
        )
        db.session.add(historical)
        db.session.commit()

        saved = staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(work_area.id),
                "bulk_status": "here",
            },
            recorder,
        )
        db.session.commit()
        self.assertEqual(saved, 2)
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(
                sort_date_operation_id=operation.id,
                status="here",
            ).count(),
            2,
        )

        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(work_area.id),
                f"status_{first.id}": "",
            },
            recorder,
        )
        db.session.commit()
        self.assertIsNone(
            StaffingDailyAttendance.query.filter_by(
                person_id=first.id,
                sort_date_operation_id=operation.id,
            ).first()
        )
        self.assertIsNotNone(db.session.get(StaffingDailyAttendance, historical.id))

    def test_legacy_null_operation_record_remains_readable(self):
        staffing_sort, _operation_unit, department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        person = self._person("LG100", work_area)
        db.session.add(
            StaffingDailyAttendance(
                person_id=person.id,
                attendance_date=operation.sort_date,
                sort_unit_id=staffing_sort.id,
                work_area_unit_id=work_area.id,
                department_unit_id=department.id,
                sort_date_operation_id=None,
                status="call_in",
            )
        )
        db.session.commit()

        context = staffing_service.attendance_context(
            {"work_area_id": str(work_area.id)}
        )

        self.assertEqual(context["rows"][0]["status"], "call_in")
        self.assertEqual(context["summary"]["absent"], 1)

    def test_post_rejects_noncurrent_operation_and_out_of_scope_person(self):
        staffing_sort, operation_unit, _department, work_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        current_person = self._person("SP100", work_area)
        outside_person = self._person("SP101", direct_area)
        recorder = self._user("spoof_recorder", "operator")
        stale_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date - timedelta(days=1),
            sort_name="night",
        )
        db.session.add(stale_operation)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "no longer current"):
            staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(stale_operation.id),
                    "sort_id": str(staffing_sort.id),
                    f"status_{current_person.id}": "here",
                },
                recorder,
            )
        with self.assertRaisesRegex(ValueError, "outside the selected scope"):
            staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(operation.id),
                    "sort_id": str(staffing_sort.id),
                    "work_area_id": str(work_area.id),
                    f"status_{outside_person.id}": "here",
                },
                recorder,
            )
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_take_attendance_permission_controls_cross_area_write_dynamically(self):
        staffing_sort, _operation_unit, department, first_area, _direct_area = self._hierarchy()
        second_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "WBM",
                "parent_id": department.id,
            }
        )
        operation = self._night_operation()
        person = self._person("XA100", second_area)
        operator = self._user("cross_area_operator", "operator")
        db.session.commit()
        self._login(operator.username)

        page = self.client.get(
            f"/neostaffing/attendance?work_area_id={second_area.id}"
        )
        saved = self.client.post(
            "/neostaffing/attendance",
            data={
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(second_area.id),
                f"status_{person.id}": "here",
            },
            follow_redirects=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"XA100", page.data)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 1)

        record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
        db.session.delete(record)
        rule = PermissionRule.query.filter_by(
            permission_key="neostaffing.attendance.take"
        ).one()
        rule.minimum_role = "simulator"
        db.session.commit()
        denied = self.client.post(
            "/neostaffing/attendance",
            data={
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(second_area.id),
                f"status_{person.id}": "here",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Take Attendance permission", denied.data)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 0)
        self.assertNotEqual(first_area.id, second_area.id)

    def test_existing_attendance_unique_contract_is_unchanged(self):
        staffing_sort, _operation_unit, _department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        person = self._person("UQ100", work_area)
        db.session.add_all(
            [
                StaffingDailyAttendance(
                    person_id=person.id,
                    attendance_date=operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=operation.id,
                    status="here",
                ),
                StaffingDailyAttendance(
                    person_id=person.id,
                    attendance_date=operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=None,
                    status="call_in",
                ),
            ]
        )
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_large_roster_context_and_save_use_bounded_selects(self):
        staffing_sort, operation_unit, _department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        recorder = self._user("large_batch_recorder", "operator")
        people = []
        assignments = []
        for index in range(1500):
            person = StaffingPerson(
                employee_id=f"L{index:04d}",
                first_name="Large",
                last_name=f"Roster {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time",
                active=True,
            )
            people.append(person)
            assignments.append(
                StaffingWorkAssignment(
                    person=person,
                    work_area=work_area,
                    active=True,
                )
            )
        db.session.add_all(people + assignments)
        db.session.commit()
        db.session.expire_all()

        context_selects = self._count_selects(
            lambda: staffing_service.attendance_context(
                {"operation_id": str(operation_unit.id)}
            )
        )
        self.assertLessEqual(context_selects, 10)

        save_selects = self._count_selects(
            lambda: staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(operation.id),
                    "sort_id": str(staffing_sort.id),
                    "operation_id": str(operation_unit.id),
                    "bulk_status": "here",
                },
                recorder,
            )
        )
        self.assertLessEqual(save_selects, 10)
        db.session.commit()
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(
                sort_date_operation_id=operation.id,
                status="here",
            ).count(),
            1500,
        )

    def test_attendance_route_renders_full_width_console_and_multi_area_scope(self):
        staffing_sort, operation_unit, department, nested_area, direct_area = self._hierarchy()
        self._night_operation()
        nested = self._person("UI100", nested_area)
        direct = self._person("UI101", direct_area)
        operator = self._user("attendance_console_operator", "operator")
        db.session.commit()
        self._login(operator.username)

        response = self.client.get(
            "/neostaffing/attendance",
            query_string=[
                ("work_area_ids", str(nested_area.id)),
                ("work_area_ids", str(direct_area.id)),
            ],
        )
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="neostaffing-attendance-console theme-staffing"', page)
        self.assertIn('data-attendance-tree-scroll', page)
        self.assertIn('data-attendance-tree', page)
        self.assertIn('data-attendance-tree-toggle', page)
        self.assertIn('neostaffing.attendance.hierarchy.v1', page)
        self.assertIn("2 AREAS", page)
        self.assertIn(nested_area.name, page)
        self.assertIn(direct_area.name, page)
        self.assertIn(f'name="work_area_ids" value="{nested_area.id}"', page)
        self.assertIn(f'name="work_area_ids" value="{direct_area.id}"', page)
        self.assertIn(nested.employee_id, page)
        self.assertIn(direct.employee_id, page)
        self.assertIn("Attendance Area", page)
        for label in (
            "On Payroll", "Working", "Unmarked", "Called In", "No Call",
            "Scheduled Off", "Vacation", "Opt Day", "Anniversary Day",
            "Disability", "Work Comp", "Funeral", "Jury", "FMLA",
            "Military", "Personal Leave", "Cleared",
        ):
            self.assertIn(label, page)
        self.assertNotIn("neostaffing-dashboard-shell", page)
        self.assertNotIn("neostaffing-data-card", page)

        css = Path("app/static/css/base.css").read_text()
        attendance_css = css.split(
            "/* NeoStaffing Attendance full-width operations console. */", 1
        )[1]
        self.assertIn("grid-template-columns: clamp(144px, 10vw, 184px)", attendance_css)
        self.assertIn("width: calc(100vw - 32px);", attendance_css)
        self.assertIn("overflow-x: clip", attendance_css)
        self.assertIn("min-height: 27px", attendance_css)

        context = staffing_service.attendance_context(
            {"work_area_ids": [str(nested_area.id), str(direct_area.id)]}
        )
        self.assertEqual(
            [area.id for area in context["selected_work_areas"]],
            sorted([nested_area.id, direct_area.id]),
        )
        self.assertEqual(context["scope_tree"][0]["unit"].id, staffing_sort.id)
        self.assertEqual(context["scope_tree"][0]["children"][0]["unit"].id, operation_unit.id)
        self.assertEqual(
            context["scope_tree"][0]["children"][0]["children"][0]["unit"].id,
            department.id,
        )

    def _count_selects(self, action):
        count = 0

        def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal count
            if statement.lstrip().upper().startswith("SELECT"):
                count += 1

        event.listen(db.engine, "before_cursor_execute", count_statement)
        try:
            action()
        finally:
            event.remove(db.engine, "before_cursor_execute", count_statement)
        return count

    def _hierarchy(self, sort_name="Night"):
        staffing_sort = staffing_service.create_unit(
            {"unit_type": "sort", "name": sort_name}
        )
        operation = staffing_service.create_unit(
            {
                "unit_type": "operation",
                "name": "Hub Operations",
                "parent_id": staffing_sort.id,
            }
        )
        department = staffing_service.create_unit(
            {
                "unit_type": "department",
                "name": "Outbound",
                "parent_id": operation.id,
            }
        )
        work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "EBM",
                "parent_id": department.id,
            }
        )
        direct_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "Load Planning",
                "parent_id": operation.id,
            }
        )
        return staffing_sort, operation, department, work_area, direct_area

    def _night_operation(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.OPERATION_DATE,
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _person(self, employee_id, work_area):
        person = staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": "Test",
                "last_name": employee_id,
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        return person

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name="Staffing",
            last_name="User",
            full_name="Staffing User",
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role=role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.flush()
        return user

    def _login(self, username):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
