from datetime import date, datetime, timedelta
import unittest

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingGroup,
    StaffingGroupMembership,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingStaffingGroupsTest(unittest.TestCase):
    OPERATION_DATE = date(2026, 8, 15)

    def setUp(self):
        config = type(
            "NeoStaffingStaffingGroupsConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026,
                    8,
                    15,
                    21,
                    0,
                ),
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

    def test_create_rename_membership_and_deactivate(self):
        _staffing_sort, operation, department, _nested, _direct = self._hierarchy()
        group = staffing_service.create_staffing_group(
            {
                "name": "Outbound Coverage",
                "active": "1",
                "staffing_unit_ids": [operation.id, department.id],
            }
        )
        db.session.commit()

        self.assertEqual(group.name, "Outbound Coverage")
        self.assertTrue(group.active)
        self.assertEqual(
            {
                row.staffing_unit_id
                for row in StaffingGroupMembership.query.filter_by(
                    group_id=group.id
                ).all()
            },
            {operation.id, department.id},
        )

        staffing_service.update_staffing_group(
            group,
            {
                "name": "Outbound Night",
                "active": "0",
                "staffing_unit_ids": [department.id],
            },
        )
        db.session.commit()

        self.assertEqual(group.name, "Outbound Night")
        self.assertFalse(group.active)
        self.assertEqual(
            StaffingGroupMembership.query.filter_by(group_id=group.id).one().staffing_unit_id,
            department.id,
        )

    def test_only_department_and_operation_memberships_are_allowed(self):
        staffing_sort, _operation, _department, work_area, _direct = self._hierarchy()
        db.session.commit()
        for unit in (staffing_sort, work_area):
            with self.assertRaisesRegex(
                ValueError,
                "only Departments and Operations",
            ):
                staffing_service.create_staffing_group(
                    {
                        "name": f"Invalid {unit.id}",
                        "staffing_unit_ids": [unit.id],
                    }
                )
            db.session.rollback()
        self.assertEqual(StaffingGroup.query.count(), 0)

    def test_totals_deduplicate_overlap_and_include_direct_work_areas(self):
        staffing_sort, operation, department, nested_area, direct_area = self._hierarchy()
        current_operation = self._night_operation()
        nested_here = self._person("GR100", nested_area)
        nested_absent = self._person("GR101", nested_area)
        direct_unmarked = self._person("GR102", direct_area)
        db.session.add_all(
            [
                StaffingDailyAttendance(
                    person_id=nested_here.id,
                    attendance_date=current_operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=current_operation.id,
                    work_area_unit_id=nested_area.id,
                    department_unit_id=department.id,
                    operation_unit_id=operation.id,
                    status="here",
                ),
                StaffingDailyAttendance(
                    person_id=nested_absent.id,
                    attendance_date=current_operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=current_operation.id,
                    work_area_unit_id=nested_area.id,
                    department_unit_id=department.id,
                    operation_unit_id=operation.id,
                    status="vacation",
                ),
            ]
        )
        staffing_service.create_staffing_group(
            {
                "name": "Operation Plus Overlapping Department",
                "active": "1",
                "staffing_unit_ids": [operation.id, department.id],
            }
        )
        db.session.commit()

        context = staffing_service.attendance_context(
            {"work_area_id": str(nested_area.id)},
            include_staffing_groups=True,
        )
        group = context["staffing_groups"][0]

        self.assertEqual(context["summary"]["total_roster"], 2)
        self.assertEqual(group["total_roster"], 3)
        self.assertEqual(group["here"], 1)
        self.assertEqual(group["absent"], 1)
        self.assertEqual(group["unmarked"], 1)
        self.assertEqual(direct_unmarked.work_assignment.work_area_unit_id, direct_area.id)

    def test_current_night_operation_is_authoritative_and_inactive_groups_are_hidden(self):
        staffing_sort, operation, _department, _nested, direct_area = self._hierarchy()
        current_operation = self._night_operation()
        person = self._person("NG100", direct_area)
        prior_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=current_operation.sort_date - timedelta(days=1),
            sort_name="night",
        )
        db.session.add(prior_operation)
        db.session.flush()
        db.session.add(
            StaffingDailyAttendance(
                person_id=person.id,
                attendance_date=prior_operation.sort_date,
                sort_unit_id=staffing_sort.id,
                sort_date_operation_id=prior_operation.id,
                work_area_unit_id=direct_area.id,
                operation_unit_id=operation.id,
                status="here",
            )
        )
        active = staffing_service.create_staffing_group(
            {
                "name": "Active Group",
                "active": "1",
                "staffing_unit_ids": [operation.id],
            }
        )
        inactive = staffing_service.create_staffing_group(
            {
                "name": "Inactive Group",
                "active": "0",
                "staffing_unit_ids": [operation.id],
            }
        )
        db.session.commit()

        attendance = staffing_service.attendance_context(
            include_staffing_groups=True
        )
        management = staffing_service.staffing_groups_context()

        self.assertEqual(attendance["sort_date_operation"].id, current_operation.id)
        self.assertEqual(attendance["staffing_groups"][0]["group"].id, active.id)
        self.assertEqual(attendance["staffing_groups"][0]["here"], 0)
        self.assertEqual(attendance["staffing_groups"][0]["unmarked"], 1)
        self.assertEqual(
            {row["group"].id for row in management["groups"]},
            {active.id, inactive.id},
        )

    def test_permission_thresholds_control_view_and_edit(self):
        _staffing_sort, operation, _department, _nested, _direct = self._hierarchy()
        self._night_operation()
        operator = self._user("group_operator", "operator")
        master = self._user("group_master", "master")
        watcher = self._user("group_watcher", "watcher")
        db.session.commit()

        self._login(operator.username)
        page = self.client.get("/neostaffing/staffing-groups")
        denied_edit = self.client.post(
            "/neostaffing/staffing-groups",
            data={
                "action": "create",
                "name": "Operator Group",
                "active": "1",
                "staffing_unit_ids": str(operation.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(b"CREATE GROUP", page.data)
        self.assertIn(b"Edit Staffing Groups permission", denied_edit.data)
        self.assertEqual(StaffingGroup.query.count(), 0)

        self._login(master.username)
        created = self.client.post(
            "/neostaffing/staffing-groups",
            data={
                "action": "create",
                "name": "Master Group",
                "active": "1",
                "staffing_unit_ids": str(operation.id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Staffing Group created", created.data)
        self.assertEqual(StaffingGroup.query.count(), 1)

        self._login(watcher.username)
        denied_view = self.client.get(
            "/neostaffing/staffing-groups",
            follow_redirects=False,
        )
        watcher_attendance = self.client.get("/neostaffing/attendance")
        self.assertEqual(denied_view.status_code, 302)
        self.assertEqual(denied_view.location, "/neostaffing")
        self.assertNotIn(b"Master Group", watcher_attendance.data)

        self._rule("neostaffing.staffing_groups.view").minimum_role = "watcher"
        self._rule("neostaffing.staffing_groups.edit").minimum_role = "operator"
        db.session.commit()
        self._login(operator.username)
        dynamic_edit = self.client.post(
            "/neostaffing/staffing-groups",
            data={
                "action": "create",
                "name": "Dynamic Group",
                "active": "1",
                "staffing_unit_ids": str(operation.id),
            },
            follow_redirects=True,
        )
        self.assertIn(b"Staffing Group created", dynamic_edit.data)
        self.assertEqual(StaffingGroup.query.count(), 2)

    def test_read_only_route_writes_nothing_and_renders_attendance_totals(self):
        _staffing_sort, operation, _department, _nested, _direct = self._hierarchy()
        self._night_operation()
        staffing_service.create_staffing_group(
            {
                "name": "Read Only Group",
                "staffing_unit_ids": [operation.id],
            }
        )
        viewer = self._user("group_viewer", "operator")
        db.session.commit()
        self._login(viewer.username)
        dml = []

        def capture_dml(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().split(None, 1)[0].upper() in {
                "INSERT",
                "UPDATE",
                "DELETE",
            }:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_dml)
        try:
            groups_page = self.client.get("/neostaffing/staffing-groups")
            attendance_page = self.client.get("/neostaffing/attendance")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_dml)

        self.assertEqual(groups_page.status_code, 200)
        self.assertEqual(attendance_page.status_code, 200)
        self.assertIn(b"Read Only Group", groups_page.data)
        self.assertIn(b"Read Only Group", attendance_page.data)
        self.assertEqual(dml, [])

    def test_group_rollups_have_bounded_queries_for_1500_people(self):
        staffing_sort, operation, department, work_area, _direct = self._hierarchy()
        self._night_operation()
        people = []
        assignments = []
        for index in range(1500):
            person = StaffingPerson(
                employee_id=f"SG{index:04d}",
                first_name="Scale",
                last_name=f"Person {index:04d}",
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
        for index in range(20):
            staffing_service.create_staffing_group(
                {
                    "name": f"Scale Group {index:02d}",
                    "staffing_unit_ids": [operation.id, department.id],
                }
            )
        db.session.commit()
        db.session.expire_all()

        selects = self._count_selects(
            lambda: staffing_service.attendance_context(
                {"sort_id": str(staffing_sort.id)},
                include_staffing_groups=True,
            )
        )
        self.assertLessEqual(selects, 12)
        management_selects = self._count_selects(
            staffing_service.staffing_groups_context
        )
        self.assertLessEqual(management_selects, 12)

        context = staffing_service.attendance_context(
            {"sort_id": str(staffing_sort.id)},
            include_staffing_groups=True,
        )
        self.assertEqual(len(context["staffing_groups"]), 20)
        self.assertTrue(
            all(row["total_roster"] == 1500 for row in context["staffing_groups"])
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

    def _hierarchy(self):
        staffing_sort = staffing_service.create_unit(
            {"unit_type": "sort", "name": "Night"}
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
        nested_area = staffing_service.create_unit(
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
        return staffing_sort, operation, department, nested_area, direct_area

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

    def _rule(self, permission_key):
        return PermissionRule.query.filter_by(permission_key=permission_key).one()


if __name__ == "__main__":
    unittest.main()
