from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    PermissionRule,
    PortalAppAccess,
    StaffingDailyAttendance,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neostaffing as staffing_service
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingRoutesTest(unittest.TestCase):
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
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_approved_neostaffing_user_can_open_landing_menu(self):
        user = self._user("staffing_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"neostaffing-primary-menu", response.data)
        self.assertIn(b"PEOPLE", response.data)
        self.assertIn(b"ORG CHART", response.data)
        self.assertIn(b"REPORTS", response.data)
        self.assertIn(b"ATTENDANCE", response.data)
        self.assertIn(b'href="/neostaffing/people"', response.data)
        self.assertIn(b'href="/neostaffing/org-chart"', response.data)
        self.assertIn(b'href="/neostaffing/reports"', response.data)
        self.assertIn(b'href="/neostaffing/attendance"', response.data)
        self.assertIn(b"neo-brand--apps", response.data)
        self.assertIn(b"/static/images/icons/neostaffing/inapp/neostaffing-inapp-128.png", response.data)
        self.assertIn(b"neostaffing-header-title", response.data)
        self.assertIn(b"neo-brand-title__node--staffing", response.data)
        self.assertEqual(response.data.count(b"neostaffing-menu-tile"), 4)
        self.assertNotIn(b'href="/neostaffing/people/attendance" class="neostaffing-menu-tile"', response.data)
        self.assertNotIn(b"APP ROLE", response.data)
        self.assertNotIn(b"neostaffing-home-header", response.data)
        self.assertNotIn(b"Total People", response.data)
        self.assertNotIn(b"Active Roster", response.data)
        self.assertNotIn(b"Assigned", response.data)
        self.assertNotIn(b"Unassigned", response.data)
        self.assertNotIn(b"Work Areas", response.data)
        self.assertNotIn(b"Today Attendance", response.data)
        self.assertNotIn(b"neostaffing-home-summary", response.data)
        self.assertNotIn(b"TOTAL PLANNED STAFFING", response.data)
        self.assertNotIn(b"STAFFING BOARD", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b"Change Characters", response.data)

    def test_neostaffing_section_pages_render_clean_sidebar_navigation(self):
        user = self._user("staffing_sidebar_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        for path, active_label in (
            ("/neostaffing/people", b"People"),
            ("/neostaffing/org-chart", b"Org Chart"),
            ("/neostaffing/reports", b"Reports"),
            ("/neostaffing/attendance", b"Attendance"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'aria-label="NeoStaffing section navigation"', response.data)
                for label in (b"Home", b"People", b"Org Chart", b"Reports", b"Attendance"):
                    self.assertIn(label, response.data)
                self.assertIn(active_label, response.data)
                self.assertIn(b"mobile-bottom-nav", response.data)
                self.assertIn(b"<strong>" + active_label + b"</strong>", response.data)
                self.assertNotIn(b"neostaffing-mobile-tabs", response.data)
                self.assertNotIn(b"<strong>DASHBOARD</strong>", response.data)
                self.assertNotIn(b"APP ROLE", response.data)
                self.assertNotIn(b"neostaffing-rail-brand", response.data)
                self.assertNotIn(b"neostaffing-rail-icon", response.data)
                self.assertNotIn(b"neostaffing-rail-title", response.data)

    def test_legacy_people_attendance_redirects_to_main_attendance(self):
        user = self._user("staffing_attendance_legacy")
        self._grant_app_access(user, "neostaffing", "operator")
        _sort, _operation, _department, work_area = self._staffing_hierarchy()
        db.session.commit()
        self._login(user.username)

        response = self.client.get(f"/neostaffing/people/attendance?work_area_id={work_area.id}", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, f"/neostaffing/attendance?work_area_id={work_area.id}")

    def test_neostaffing_permission_defaults_are_registered(self):
        ensure_default_permission_rules()
        defaults = {
            rule.permission_key: rule.minimum_role
            for rule in PermissionRule.query.filter(
                PermissionRule.permission_key.in_(
                    [
                        "neostaffing.people.view",
                        "neostaffing.people.edit",
                        "neostaffing.people.bulk_actions",
                        "neostaffing.attendance.take",
                        "neostaffing.reports.view",
                        "neostaffing.management.assign",
                        "neostaffing.org_chart.view",
                        "neostaffing.org_chart.edit_structure",
                    ]
                )
            ).all()
        }

        self.assertEqual(defaults["neostaffing.people.view"], "watcher")
        self.assertEqual(defaults["neostaffing.people.edit"], "simulator")
        self.assertEqual(defaults["neostaffing.people.bulk_actions"], "simulator")
        self.assertEqual(defaults["neostaffing.attendance.take"], "operator")
        self.assertEqual(defaults["neostaffing.reports.view"], "operator")
        self.assertEqual(defaults["neostaffing.management.assign"], "simulator")
        self.assertEqual(defaults["neostaffing.org_chart.view"], "watcher")
        self.assertEqual(defaults["neostaffing.org_chart.edit_structure"], "master")

    def test_operator_can_take_attendance_and_view_reports(self):
        user = self._user("staffing_operator_permissions")
        self._grant_app_access(user, "neostaffing", "operator")
        sort, _operation, _department, work_area = self._staffing_hierarchy()
        person = staffing_service.create_person(
            {
                "employee_id": "OP100",
                "first_name": "Opal",
                "last_name": "Operator",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user.username)

        reports = self.client.get("/neostaffing/reports")
        attendance = self.client.post(
            "/neostaffing/attendance",
            data={
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                f"status_{person.id}": "here",
            },
            follow_redirects=True,
        )

        self.assertEqual(reports.status_code, 200)
        self.assertEqual(attendance.status_code, 200)
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(person_id=person.id, status="here").count(),
            1,
        )

    def test_watcher_can_view_but_cannot_take_attendance(self):
        user = self._user("staffing_attendance_watcher")
        self._grant_app_access(user, "neostaffing", "watcher")
        sort, _operation, _department, work_area = self._staffing_hierarchy()
        person = staffing_service.create_person(
            {
                "employee_id": "WA100",
                "first_name": "Watch",
                "last_name": "Viewer",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user.username)

        page = self.client.get(f"/neostaffing/attendance?work_area_id={work_area.id}")
        blocked = self.client.post(
            "/neostaffing/attendance",
            data={
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                f"status_{person.id}": "call_in",
            },
            follow_redirects=True,
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"WA100", page.data)
        self.assertNotIn(b"ALL HERE", page.data)
        self.assertNotIn(b"SAVE ATTENDANCE", page.data)
        self.assertEqual(blocked.status_code, 200)
        self.assertIn(b"Taking NeoStaffing attendance requires Operator access.", blocked.data)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 0)

    def test_landing_attendance_shortcut_resolves_one_or_multiple_scopes(self):
        _sort, _operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        supervisor = staffing_service.create_person(
            {
                "employee_id": "M100",
                "first_name": "Pat",
                "last_name": "Supervisor",
                "seniority_date": "2018-01-01",
                "classification": "part_time_supervisor",
            }
        )
        one_scope_user = self._link_user_for_person(supervisor, "staffing_one_scope")
        one_scope_user.is_management = True
        one_scope_user.management_level = "part_time_supervisor"
        self._grant_app_access(one_scope_user, "neostaffing", "operator")
        staffing_service.create_leadership_assignment(supervisor, work_area)
        db.session.commit()

        self._login(one_scope_user.username)
        one_scope = self.client.get("/neostaffing")

        self.assertEqual(one_scope.status_code, 200)
        self.assertIn(b"TAKE ATTENDANCE", one_scope.data)
        self.assertIn(f"/neostaffing/attendance?work_area_id={work_area.id}".encode(), one_scope.data)
        self.assertNotIn(b"MY AREAS", one_scope.data)

        staffing_service.create_leadership_assignment(supervisor, second_work_area)
        db.session.commit()
        multiple = self.client.get("/neostaffing")

        self.assertEqual(multiple.status_code, 200)
        self.assertIn(b"MY AREAS", multiple.data)
        self.assertIn(f"work_area_id={work_area.id}".encode(), multiple.data)
        self.assertIn(f"work_area_id={second_work_area.id}".encode(), multiple.data)

    def test_landing_hides_attendance_shortcut_when_management_person_is_missing_for_lower_role(self):
        user = self._user("staffing_missing_person")
        user.employee_id = "M404"
        user.is_management = True
        user.management_level = "manager"
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"TAKE ATTENDANCE", response.data)
        self.assertNotIn(b"SETUP NEEDED", response.data)

    def test_org_chart_uses_hierarchy_driven_visual_layout(self):
        user = self._user("staffing_dashboard_master")
        self._grant_app_access(user, "neostaffing", "master")
        sort = StaffingUnit(unit_type="sort", name="Night Sort", display_order=1)
        operation = StaffingUnit(
            unit_type="operation",
            name="Shift Operation",
            parent=sort,
            display_order=1,
        )
        department = StaffingUnit(
            unit_type="department",
            name="East Shift Department",
            parent=operation,
            display_order=1,
        )
        work_area = StaffingUnit(
            unit_type="work_area",
            name="EBM",
            parent=department,
            display_order=1,
            required_headcount=2,
        )
        person = StaffingPerson(
            employee_id="10001",
            first_name="Avery",
            last_name="Spotter",
            seniority_date=date(2021, 5, 17),
            classification="part_time",
            active=True,
        )
        db.session.add_all([sort, operation, department, work_area, person])
        db.session.flush()
        direct_work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "Load Planning",
                "parent_id": operation.id,
                "required_headcount": "1",
            }
        )
        db.session.add(StaffingWorkAssignment(person=person, work_area=work_area))
        db.session.commit()
        self._login(user.username)

        sort_response = self.client.get(f"/neostaffing/org-chart?unit_id={sort.id}")
        response = self.client.get(f"/neostaffing/org-chart?unit_id={operation.id}")
        department_response = self.client.get(f"/neostaffing/org-chart?unit_id={department.id}")
        work_area_response = self.client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")

        self.assertEqual(sort_response.status_code, 200)
        self.assertIn(b"FULL TREE", sort_response.data)
        self.assertIn(b"neostaffing-tree-editor", sort_response.data)
        self.assertIn(b"neostaffing-tree-branch", sort_response.data)
        self.assertIn(b"neostaffing-tree-toggle", sort_response.data)
        self.assertIn(b"data-org-chart-tree", sort_response.data)
        self.assertIn(b"data-org-chart-branch", sort_response.data)
        self.assertIn(b"data-org-chart-state-form", sort_response.data)
        self.assertIn(b"neostaffing-org-chart-tree-state", sort_response.data)
        self.assertIn(b"sessionStorage", sort_response.data)
        self.assertIn(b"scrollTop", sort_response.data)
        self.assertIn(b"+ Operation", sort_response.data)
        self.assertIn(b"Add Operation", sort_response.data)
        self.assertIn(b"Assign Division Manager", sort_response.data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Night Sort", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"Load Planning", response.data)
        self.assertIn(b"EBM", response.data)
        self.assertIn(b"FULL TREE", response.data)
        self.assertIn(b"neostaffing-tree-node", response.data)
        self.assertNotIn(b"Child /", response.data)
        self.assertNotIn(b"0 Assigned", response.data)
        self.assertIn(b"+ Department", response.data)
        self.assertIn(b"+ Work Area", response.data)
        self.assertIn(
            f'href="/neostaffing/org-chart?unit_id={operation.id}#structure-add"'.encode(),
            response.data,
        )
        self.assertIn(b"Add Department", response.data)
        self.assertIn(b"Add Work Area", response.data)
        self.assertIn(b"Assign Manager", response.data)
        self.assertEqual(department_response.status_code, 200)
        self.assertIn(b"EBM", department_response.data)
        self.assertIn(b"+ Work Area", department_response.data)
        self.assertIn(b"Add Work Area", department_response.data)
        self.assertIn(b"Assign FT Supervisor", department_response.data)
        self.assertEqual(work_area_response.status_code, 200)
        self.assertIn(b'data-org-chart-workspace', work_area_response.data)
        self.assertNotIn(b'data-org-chart-workspace-empty', work_area_response.data)
        self.assertIn(
            f'id="neostaffing-org-unit-{work_area.id}" class="neostaffing-tree-branch is-selected'.encode(),
            work_area_response.data,
        )
        self.assertIn(b'aria-current="page"', work_area_response.data)
        self.assertIn(b"Required HC", work_area_response.data)
        self.assertIn(b"Assigned Count", work_area_response.data)
        self.assertNotIn(b"+ People", work_area_response.data)
        self.assertIn(b"+ PT Sup", work_area_response.data)
        self.assertNotIn(b"Add/Assign People", work_area_response.data)
        self.assertIn(b"ASSIGN PT SUPERVISOR", work_area_response.data)
        self.assertIn(b"Set Headcount", work_area_response.data)
        self.assertIn(b"1", work_area_response.data)
        self.assertIsNotNone(direct_work_area)

    def test_org_chart_mutations_preserve_selected_unit_context(self):
        user = self._user("staffing_org_state_master")
        self._grant_app_access(user, "neostaffing", "master")
        _sort, operation, _department, work_area = self._staffing_hierarchy()
        manager = staffing_service.create_person(
            {
                "employee_id": "STATE200",
                "first_name": "State",
                "last_name": "Manager",
                "seniority_date": "2017-01-01",
                "classification": "manager",
            }
        )
        self._link_user_for_person(manager, "staffing_org_state_manager")
        db.session.commit()
        self._login(user.username)

        added = self.client.post(
            "/neostaffing/app-management/hierarchy/units",
            data={
                "unit_type": "work_area",
                "name": "State Child Area",
                "parent_id": str(operation.id),
                "required_headcount": "2",
                "display_order": "0",
                "return_unit_id": str(operation.id),
            },
            follow_redirects=False,
        )
        self.assertEqual(added.location, f"/neostaffing/org-chart?unit_id={operation.id}")
        after_add = self.client.get(added.location)
        self.assertIn(b"State Child Area", after_add.data)
        self.assertIn(
            f'id="neostaffing-org-unit-{operation.id}" class="neostaffing-tree-branch is-selected'.encode(),
            after_add.data,
        )

        updated = self.client.post(
            f"/neostaffing/app-management/hierarchy/units/{work_area.id}/update",
            data={
                "unit_type": "work_area",
                "name": "EBM",
                "parent_id": str(operation.id),
                "required_headcount": "5",
                "display_order": "0",
                "active": "1",
                "return_unit_id": str(work_area.id),
            },
            follow_redirects=False,
        )
        self.assertEqual(updated.location, f"/neostaffing/org-chart?unit_id={work_area.id}")
        self.assertEqual(db.session.get(StaffingUnit, work_area.id).parent_id, operation.id)
        self.assertEqual(db.session.get(StaffingUnit, work_area.id).required_headcount, 5)

        toggled = self.client.post(
            f"/neostaffing/app-management/hierarchy/units/{work_area.id}/toggle-active",
            data={"return_unit_id": str(work_area.id)},
            follow_redirects=False,
        )
        self.assertEqual(toggled.location, f"/neostaffing/org-chart?unit_id={work_area.id}")

        assigned = self.client.post(
            "/neostaffing/app-management/management-assignments",
            data={
                "person_id": str(manager.id),
                "unit_id": str(operation.id),
                "return_unit_id": str(operation.id),
            },
            follow_redirects=False,
        )
        self.assertEqual(assigned.location, f"/neostaffing/org-chart?unit_id={operation.id}")
        assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=manager.id,
            unit_id=operation.id,
        ).one()

        removed_assignment = self.client.post(
            f"/neostaffing/app-management/management-assignments/{assignment.id}/delete",
            data={"return_unit_id": str(operation.id)},
            follow_redirects=False,
        )
        self.assertEqual(removed_assignment.location, f"/neostaffing/org-chart?unit_id={operation.id}")

        headcount = self.client.post(
            f"/neostaffing/app-management/planned-staffing/{work_area.id}/update",
            data={"required_headcount": "6", "return_unit_id": str(work_area.id)},
            follow_redirects=False,
        )
        self.assertEqual(headcount.location, f"/neostaffing/org-chart?unit_id={work_area.id}")

        restored = self.client.get(headcount.location)
        self.assertEqual(restored.status_code, 200)
        self.assertIn(b"FULL TREE", restored.data)
        self.assertIn(b'data-org-chart-workspace', restored.data)
        self.assertNotIn(b'data-org-chart-workspace-empty', restored.data)
        self.assertIn(
            f'id="neostaffing-org-unit-{work_area.id}" class="neostaffing-tree-branch is-selected'.encode(),
            restored.data,
        )
        self.assertIn(f'name="return_unit_id" value="{work_area.id}"'.encode(), restored.data)

    def test_reports_render_staffing_seniority_and_attendance_shells(self):
        user = self._user("staffing_reports")
        self._grant_app_access(user, "neostaffing", "master")
        _sort, operation, department, work_area = self._staffing_hierarchy()
        person = staffing_service.create_person(
            {
                "employee_id": "25001",
                "first_name": "Avery",
                "last_name": "Report",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "phone_number": "555-0100",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user.username)

        staffing = self.client.get("/neostaffing/reports?report_type=staffing")
        seniority = self.client.get(f"/neostaffing/reports?report_type=seniority&operation_id={operation.id}")
        attendance = self.client.get("/neostaffing/reports?report_type=attendance")

        self.assertEqual(staffing.status_code, 200)
        self.assertIn(b"STAFFING REPORT", staffing.data)
        self.assertIn(b"25001", staffing.data)
        self.assertIn(b"Active", staffing.data)
        self.assertEqual(seniority.status_code, 200)
        self.assertIn(b"SENIORITY REPORT", seniority.data)
        self.assertIn(b"Night Sort / Shift Operation / East Shift Department / EBM", seniority.data)
        self.assertEqual(attendance.status_code, 200)
        self.assertIn(b"ATTENDANCE REPORT", attendance.data)

    def test_reports_filter_staffing_and_attendance_by_scope_and_status(self):
        user = self._user("staffing_reports_filters")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": operation.id}
        )
        avery = staffing_service.create_person(
            {
                "employee_id": "RF100",
                "first_name": "Avery",
                "last_name": "Filter",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        morgan = staffing_service.create_person(
            {
                "employee_id": "RF101",
                "first_name": "Morgan",
                "last_name": "Filter",
                "seniority_date": "2021-01-01",
                "classification": "full_time_combo",
            }
        )
        staffing_service.assign_work_area(avery, work_area)
        staffing_service.assign_work_area(morgan, second_work_area)
        recorder = self._user("attendance_report_recorder")
        db.session.flush()
        staffing_service.save_attendance(
            {
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                f"status_{avery.id}": "call_in",
                f"status_{morgan.id}": "here",
            },
            recorder,
        )
        db.session.commit()
        self._login(user.username)

        staffing = self.client.get(f"/neostaffing/reports?report_type=staffing&work_area_id={work_area.id}")
        attendance = self.client.get(
            f"/neostaffing/reports?report_type=attendance&operation_id={operation.id}&attendance_date=2026-07-03&attendance_status=call_in"
        )

        self.assertEqual(staffing.status_code, 200)
        self.assertIn(b"RF100", staffing.data)
        self.assertNotIn(b"RF101", staffing.data)
        self.assertIn(b"Part Time", staffing.data)
        self.assertEqual(attendance.status_code, 200)
        self.assertIn(b"RF100", attendance.data)
        self.assertIn(b"Call In", attendance.data)
        self.assertNotIn(b"RF101", attendance.data)

    def test_user_without_neostaffing_access_cannot_open_dashboard(self):
        user = self._user("no_staffing")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_neogateway_only_user_cannot_open_neostaffing(self):
        user = self._user("gateway_only")
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_legacy_app_management_home_redirects_to_people(self):
        for role in ("master", "grandmaster"):
            with self.subTest(role=role):
                client = self.app.test_client()
                user = self._user(f"staffing_{role}")
                self._grant_app_access(user, "neostaffing", role)
                db.session.commit()
                client.post(
                    "/login",
                    data={"username": user.username, "password": "Password123!"},
                    follow_redirects=False,
                )

                response = client.get("/neostaffing/app-management")

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, "/neostaffing/people")

    def test_lower_neostaffing_role_redirects_from_legacy_app_management(self):
        user = self._user("staffing_watcher")
        self._grant_app_access(user, "neostaffing", "watcher")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/app-management", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing/people")

    def test_legacy_people_management_get_pages_redirect_to_current_flow(self):
        paths = {
            "/neostaffing/app-management": "/neostaffing/people",
            "/neostaffing/app-management/planned-staffing": "/neostaffing/org-chart",
            "/neostaffing/app-management/people": "/neostaffing/people",
            "/neostaffing/app-management/work-assignments": "/neostaffing/people",
            "/neostaffing/app-management/management-assignments": "/neostaffing/org-chart",
        }
        master = self._user("staffing_crud_master")
        self._grant_app_access(master, "neostaffing", "master")
        operator = self._user("staffing_crud_operator")
        self._grant_app_access(operator, "neostaffing", "operator")
        gateway_only = self._user("staffing_crud_gateway_only")
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=gateway_only.id,
                gateway_id=gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()

        for path, target in paths.items():
            with self.subTest(path=path, access="master"):
                master_client = self._logged_in_client(master.username)
                response = master_client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.location, target)
            with self.subTest(path=path, access="operator"):
                operator_client = self._logged_in_client(operator.username)
                blocked = operator_client.get(path, follow_redirects=False)
                self.assertEqual(blocked.status_code, 302)
                self.assertEqual(blocked.location, target)
            with self.subTest(path=path, access="gateway-only"):
                gateway_client = self._logged_in_client(gateway_only.username)
                blocked = gateway_client.get(path, follow_redirects=False)
                self.assertEqual(blocked.status_code, 302)
                self.assertEqual(blocked.location, "/portal")

    def test_people_page_removes_legacy_management_menu(self):
        user = self._user("staffing_links_master")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        db.session.commit()
        self._login(user.username)

        response = self.client.get(f"/neostaffing/people?sort_id={sort.id}&operation_id={operation.id}&department_id={department.id}&work_area_id={work_area.id}")

        self.assertEqual(response.status_code, 200)
        for stale_label in (
            b"MANAGE HOME",
            b"PLANNED STAFFING",
            b"WORK ASSIGNMENTS",
            b"PERMISSIONS",
            b"APP MANAGEMENT",
            b"PEOPLE CONTROL DECK",
        ):
            self.assertNotIn(stale_label, response.data)
        self.assertIn(b"Select Sort", response.data)
        self.assertIn(b"ADD EMPLOYEE", response.data)
        self.assertIn(b"ROSTER", response.data)
        self.assertIn(b"Employee Status", response.data)
        self.assertNotIn(b'action-button action-button-secondary" href="/neostaffing/attendance"', response.data)
        self.assertNotIn(b'href="/neostaffing/people/attendance"', response.data)

    def test_simulator_can_edit_people_and_assign_management_but_not_org_structure(self):
        simulator = self._user("staffing_simulator_permissions")
        self._grant_app_access(simulator, "neostaffing", "simulator")
        master = self._user("staffing_master_structure_permissions")
        self._grant_app_access(master, "neostaffing", "master")
        _sort, operation, _department, work_area = self._staffing_hierarchy()
        supervisor = staffing_service.create_person(
            {
                "employee_id": "MG100",
                "first_name": "Manage",
                "last_name": "Assign",
                "seniority_date": "2019-01-01",
                "classification": "part_time_supervisor",
            }
        )
        self._link_user_for_person(supervisor, "staffing_mg100")
        db.session.commit()

        simulator_client = self._logged_in_client(simulator.username)
        people_page = simulator_client.get("/neostaffing/people")
        created = simulator_client.post(
            "/neostaffing/app-management/people",
            data={
                "employee_id": "SIM100",
                "first_name": "Sim",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "employee_status": "active",
            },
            follow_redirects=False,
        )
        person = StaffingPerson.query.filter_by(employee_id="SIM100").one()
        updated = simulator_client.post(
            f"/neostaffing/app-management/people/{person.id}/update",
            data={
                "employee_id": "SIM100",
                "first_name": "Sim",
                "last_name": "Updated",
                "seniority_date": "2020-01-01",
                "classification": "full_time_combo",
                "employee_status": "active",
                "active": "1",
            },
            follow_redirects=False,
        )
        management_page = simulator_client.get("/neostaffing/app-management/management-assignments")
        management = simulator_client.post(
            "/neostaffing/app-management/management-assignments",
            data={"person_id": str(supervisor.id), "unit_id": str(work_area.id)},
            follow_redirects=False,
        )
        blocked_structure = simulator_client.post(
            "/neostaffing/app-management/hierarchy/units",
            data={"unit_type": "department", "parent_id": str(operation.id), "name": "Blocked Dept"},
            follow_redirects=False,
        )

        master_client = self._logged_in_client(master.username)
        allowed_structure = master_client.post(
            "/neostaffing/app-management/hierarchy/units",
            data={"unit_type": "department", "parent_id": str(operation.id), "name": "Allowed Dept"},
            follow_redirects=False,
        )

        self.assertEqual(people_page.status_code, 200)
        self.assertIn(b"Select Sort", people_page.data)
        self.assertEqual(created.status_code, 302)
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(db.session.get(StaffingPerson, person.id).last_name, "Updated")
        self.assertEqual(management_page.status_code, 302)
        self.assertEqual(management_page.location, "/neostaffing/org-chart")
        self.assertEqual(management.status_code, 302)
        self.assertEqual(
            StaffingLeadershipAssignment.query.filter_by(person_id=supervisor.id, unit_id=work_area.id).count(),
            1,
        )
        self.assertEqual(blocked_structure.status_code, 302)
        self.assertEqual(blocked_structure.location, "/neostaffing")
        self.assertIsNone(StaffingUnit.query.filter_by(name="Blocked Dept").first())
        self.assertEqual(allowed_structure.status_code, 302)
        self.assertIsNotNone(StaffingUnit.query.filter_by(name="Allowed Dept").first())

    def test_people_and_org_chart_use_current_operational_layouts(self):
        user = self._user("staffing_card_layout")
        self._grant_app_access(user, "neostaffing", "master")
        sort = StaffingUnit(unit_type="sort", name="Night Sort", display_order=1)
        operation = StaffingUnit(unit_type="operation", name="Shift Operation", parent=sort, display_order=1)
        department = StaffingUnit(
            unit_type="department",
            name="East Shift Department",
            parent=operation,
            display_order=1,
        )
        work_area = StaffingUnit(
            unit_type="work_area",
            name="EBM",
            parent=department,
            display_order=1,
            required_headcount=4,
        )
        person = StaffingPerson(
            employee_id="20001",
            first_name="Jordan",
            last_name="Worker",
            seniority_date=date(2020, 1, 2),
            classification="part_time",
            active=True,
        )
        db.session.add_all([sort, operation, department, work_area, person])
        db.session.flush()
        db.session.add(StaffingWorkAssignment(person=person, work_area=work_area, active=True))
        db.session.commit()
        self._login(user.username)

        hierarchy = self.client.get("/neostaffing/org-chart")
        self.assertEqual(hierarchy.status_code, 200)
        self.assertIn(b"FULL TREE", hierarchy.data)
        self.assertIn(b"neostaffing-tree-editor", hierarchy.data)
        self.assertIn(b"+ Sort", hierarchy.data)
        self.assertIn(b'data-org-chart-workspace', hierarchy.data)
        self.assertIn(b'data-org-chart-workspace-empty', hierarchy.data)
        self.assertIn(b'neostaffing-tree-detail is-empty', hierarchy.data)
        self.assertNotIn(b"is-tree-only", hierarchy.data)
        self.assertNotIn(b"<h2>DETAIL</h2>", hierarchy.data)
        self.assertNotIn(b"ADD UNDER", hierarchy.data)
        self.assertNotIn(b"ADD SORT", hierarchy.data)

        people_page = self.client.get(f"/neostaffing/people?work_area_id={work_area.id}")
        self.assertEqual(people_page.status_code, 200)
        self.assertIn(b"Select Sort", people_page.data)
        self.assertIn(b"ADD EMPLOYEE", people_page.data)
        self.assertIn(b"ROSTER", people_page.data)
        self.assertIn(b"neostaffing-people-card", people_page.data)
        self.assertNotIn(b"PEOPLE CONTROL DECK", people_page.data)
        self.assertNotIn(b"WORK AREA ASSIGNMENT DECK", people_page.data)

    def test_planned_staffing_page_edits_validates_and_filters_work_areas(self):
        user = self._user("staffing_required_master")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        employee = staffing_service.create_person(
            {
                "employee_id": "23001",
                "first_name": "Assigned",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(employee, work_area)
        db.session.commit()
        self._login(user.username)

        page = self.client.get(f"/neostaffing/app-management/planned-staffing?work_area_id={work_area.id}", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page.location, f"/neostaffing/org-chart?unit_id={work_area.id}")

        update = self.client.post(
            f"/neostaffing/app-management/planned-staffing/{work_area.id}/update",
            data={
                "required_headcount": "5",
                "sort_id": str(sort.id),
                "operation_id": str(operation.id),
                "department_id": str(department.id),
                "work_area_id": str(work_area.id),
            },
            follow_redirects=True,
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(db.session.get(StaffingUnit, work_area.id).required_headcount, 5)
        self.assertIn(b"EBM", update.data)
        self.assertIn(b"5", update.data)
        self.assertIn(b"Planned staffing updated.", update.data)

        invalid = self.client.post(
            f"/neostaffing/app-management/planned-staffing/{work_area.id}/update",
            data={"required_headcount": "-1", "work_area_id": str(work_area.id)},
            follow_redirects=True,
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(db.session.get(StaffingUnit, work_area.id).required_headcount, 5)
        self.assertIn(b"Planned staffing cannot be negative.", invalid.data)

        lower = self._user("staffing_required_operator")
        self._grant_app_access(lower, "neostaffing", "operator")
        db.session.commit()
        operator_client = self._logged_in_client(lower.username)
        blocked = operator_client.get("/neostaffing/app-management/planned-staffing", follow_redirects=False)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked.location, "/neostaffing/org-chart")

        self.assertIsNotNone(second_work_area)

    def test_org_chart_drilldown_shows_work_area_detail(self):
        user = self._user("staffing_board_drilldown")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": department.id,
                "required_headcount": "2",
            },
        )
        default_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "Default Area", "parent_id": department.id}
        )
        employee = staffing_service.create_person(
            {
                "employee_id": "24001",
                "first_name": "Detail",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(employee, work_area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")
        defaulted = self.client.get(f"/neostaffing/org-chart?unit_id={default_area.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Required HC", response.data)
        self.assertIn(b"Assigned Count", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b'value="2"', response.data)
        self.assertIn(b"Set Headcount", response.data)
        self.assertEqual(defaulted.status_code, 200)
        self.assertIn(b"Default Area", defaulted.data)
        self.assertIn(b"Required HC", defaulted.data)

    def test_org_chart_detail_shows_management_and_assigned_counts(self):
        user = self._user("staffing_org_detail")
        self._grant_app_access(user, "neostaffing", "master")
        _sort, _operation, _department, work_area = self._staffing_hierarchy()
        employee = staffing_service.create_person(
            {
                "employee_id": "OC100",
                "first_name": "Assigned",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        supervisor = staffing_service.create_person(
            {
                "employee_id": "OC200",
                "first_name": "Scope",
                "last_name": "Leader",
                "seniority_date": "2018-01-01",
                "classification": "part_time_supervisor",
            }
        )
        self._link_user_for_person(supervisor, "staffing_oc200")
        staffing_service.assign_work_area(employee, work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MANAGEMENT", response.data)
        self.assertIn(b"Scope Leader", response.data)
        self.assertIn(b"Assigned Count", response.data)
        self.assertIn(b"1", response.data)

    def test_org_chart_management_and_structure_controls_follow_permissions(self):
        watcher = self._user("staffing_org_watcher")
        self._grant_app_access(watcher, "neostaffing", "watcher")
        simulator = self._user("staffing_org_simulator")
        self._grant_app_access(simulator, "neostaffing", "simulator")
        master = self._user("staffing_org_master")
        self._grant_app_access(master, "neostaffing", "master")
        _sort, operation, _department, work_area = self._staffing_hierarchy()
        supervisor = staffing_service.create_person(
            {
                "employee_id": "OG200",
                "first_name": "Org",
                "last_name": "Supervisor",
                "seniority_date": "2018-01-01",
                "classification": "part_time_supervisor",
            }
        )
        self._link_user_for_person(supervisor, "staffing_og200")
        db.session.commit()

        watcher_page = self._logged_in_client(watcher.username).get(
            f"/neostaffing/org-chart?unit_id={work_area.id}"
        )
        watcher_tree = self._logged_in_client(watcher.username).get("/neostaffing/org-chart")
        simulator_client = self._logged_in_client(simulator.username)
        simulator_page = simulator_client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")
        assigned = simulator_client.post(
            "/neostaffing/app-management/management-assignments",
            data={
                "person_id": str(supervisor.id),
                "unit_id": str(work_area.id),
                "return_unit_id": str(work_area.id),
            },
            follow_redirects=False,
        )
        blocked_structure = simulator_client.post(
            "/neostaffing/app-management/hierarchy/units",
            data={"unit_type": "department", "parent_id": str(operation.id), "name": "Blocked Org Dept"},
            follow_redirects=False,
        )

        master_client = self._logged_in_client(master.username)
        master_tree = master_client.get("/neostaffing/org-chart")
        master_page = master_client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")
        created_structure = master_client.post(
            "/neostaffing/app-management/hierarchy/units",
            data={"unit_type": "department", "parent_id": str(operation.id), "name": "Allowed Org Dept"},
            follow_redirects=False,
        )

        self.assertEqual(watcher_page.status_code, 200)
        self.assertNotIn(b"ASSIGN MANAGEMENT", watcher_page.data)
        self.assertNotIn(b"ASSIGN PT SUPERVISOR", watcher_page.data)
        self.assertNotIn(b"STRUCTURE ACTIONS", watcher_page.data)
        self.assertNotIn(b"+ People", watcher_page.data)
        self.assertNotIn(b"+ PT Sup", watcher_page.data)
        self.assertNotIn(b"Add/Assign People", watcher_page.data)
        self.assertEqual(watcher_tree.status_code, 200)
        self.assertNotIn(b"+ Sort", watcher_tree.data)
        self.assertEqual(simulator_page.status_code, 200)
        self.assertIn(b"ASSIGN PT SUPERVISOR", simulator_page.data)
        self.assertIn(b"ASSIGN MANAGEMENT", simulator_page.data)
        self.assertNotIn(b"+ People", simulator_page.data)
        self.assertIn(b"+ PT Sup", simulator_page.data)
        self.assertNotIn(b"Add/Assign People", simulator_page.data)
        self.assertIn(b"Linked User", simulator_page.data)
        self.assertNotIn(b"STRUCTURE ACTIONS", simulator_page.data)
        self.assertNotIn(b"SAVE UNIT", simulator_page.data)
        self.assertEqual(assigned.status_code, 302)
        self.assertEqual(assigned.location, f"/neostaffing/org-chart?unit_id={work_area.id}")
        assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=supervisor.id,
            unit_id=work_area.id,
        ).one()
        self.assertEqual(assignment.person, supervisor)
        self.assertEqual(blocked_structure.status_code, 302)
        self.assertEqual(blocked_structure.location, "/neostaffing")
        self.assertIsNone(StaffingUnit.query.filter_by(name="Blocked Org Dept").first())
        self.assertEqual(master_page.status_code, 200)
        self.assertIn(b"+ Sort", master_tree.data)
        self.assertIn(b"STRUCTURE ACTIONS", master_page.data)
        self.assertIn(b"SAVE UNIT", master_page.data)
        self.assertIn(b"DEACTIVATE", master_page.data)
        self.assertIn(b"MOVE", master_page.data)
        self.assertEqual(created_structure.status_code, 302)
        self.assertIsNotNone(StaffingUnit.query.filter_by(name="Allowed Org Dept").first())

    def test_portal_tile_opens_neostaffing_for_approved_user(self):
        user = self._user("staffing_portal")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/portal")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"Approved Operator", response.data)
        self.assertIn(b'href="/neostaffing"', response.data)

    def test_seniority_view_loads_for_approved_user_but_is_not_primary_landing_tile(self):
        user = self._user("staffing_seniority_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        dashboard = self.client.get("/neostaffing")
        response = self.client.get("/neostaffing/seniority")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SENIORITY", response.data)
        self.assertIn(b"FILTERS", response.data)
        self.assertIn(b"TOTAL EMPLOYEES", response.data)
        self.assertIn(b"Include Management", response.data)
        self.assertIn(b'href="/neostaffing/people"', dashboard.data)
        self.assertIn(b'href="/neostaffing/org-chart"', dashboard.data)
        self.assertIn(b'href="/neostaffing/reports"', dashboard.data)
        self.assertNotIn(b'href="/neostaffing/seniority"', dashboard.data)

    def test_seniority_view_blocks_user_without_neostaffing_access(self):
        user = self._user("staffing_seniority_blocked")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/seniority", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_seniority_view_renders_ranked_filterable_data(self):
        user = self._user("staffing_seniority_data")
        self._grant_app_access(user, "neostaffing", "operator")
        sort, operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        avery = staffing_service.create_person(
            {
                "employee_id": "E710",
                "first_name": "Avery",
                "last_name": "Spotter",
                "seniority_date": "2019-02-01",
                "classification": "part_time",
            }
        )
        morgan = staffing_service.create_person(
            {
                "employee_id": "E711",
                "first_name": "Morgan",
                "last_name": "Loader",
                "seniority_date": "2021-03-01",
                "classification": "full_time_combo",
            }
        )
        staffing_service.assign_work_area(morgan, second_work_area)
        staffing_service.assign_work_area(avery, work_area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get(f"/neostaffing/seniority?operation_id={operation.id}")
        searched = self.client.get(
            f"/neostaffing/seniority?operation_id={operation.id}&search=avery"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Night Sort", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b"E710", response.data)
        self.assertIn(b"Spotter, Avery", response.data)
        self.assertIn(b"E711", response.data)
        self.assertIn(b"Loader, Morgan", response.data)
        self.assertIn(b"RANK", response.data)
        self.assertIn(b"PART TIME", response.data)
        self.assertIn(b"COMBO", response.data)
        self.assertIn(b"EBM", response.data)
        self.assertIn(b"WBM", response.data)
        self.assertEqual(searched.status_code, 200)
        self.assertIn(b"E710", searched.data)
        self.assertNotIn(b"E711", searched.data)

    def test_people_view_loads_for_approved_user_and_links_from_dashboard(self):
        user = self._user("staffing_people_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        dashboard = self.client.get("/neostaffing")
        response = self.client.get("/neostaffing/people")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"People", response.data)
        self.assertIn(b"Select Sort", response.data)
        self.assertNotIn(b"Step 1", response.data)
        self.assertNotIn(b"Step 2", response.data)
        self.assertNotIn(b"Select Operation", response.data)
        self.assertIn(b"SELECT A UNIT", response.data)
        self.assertIn(b"mobile-bottom-nav", response.data)
        self.assertNotIn(b"neostaffing-mobile-tabs", response.data)
        self.assertNotIn(b'action-button action-button-secondary" href="/neostaffing/attendance"', response.data)
        self.assertNotIn(b"<span>Limit</span>", response.data)
        self.assertNotIn(b"TOTAL EMPLOYEES", response.data)
        self.assertNotIn(b"ACTIVE EMPLOYEES", response.data)
        self.assertNotIn(b"INACTIVE EMPLOYEES", response.data)
        self.assertNotIn(b"SUPERVISORS", response.data)
        self.assertNotIn(b"MANAGERS", response.data)
        self.assertNotIn(b"<span>Status</span>", response.data)
        self.assertNotIn(b"Leadership Only", response.data)
        self.assertIn(b'href="/neostaffing/people"', dashboard.data)

    def test_people_drilldown_reveals_one_unit_level_at_a_time(self):
        user = self._user("staffing_people_drilldown")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        direct_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "Load Planning", "parent_id": operation.id}
        )
        employee = staffing_service.create_person(
            {
                "employee_id": "PD100",
                "first_name": "Direct",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(employee, direct_work_area)
        manager = staffing_service.create_person(
            {
                "employee_id": "PD200",
                "first_name": "Linked",
                "last_name": "Manager",
                "seniority_date": "2018-01-01",
                "classification": "manager",
            }
        )
        self._link_user_for_person(manager, "staffing_pd200")
        db.session.commit()
        self._login(user.username)

        initial = self.client.get("/neostaffing/people")
        by_sort = self.client.get(f"/neostaffing/people?sort_id={sort.id}")
        by_operation = self.client.get(
            f"/neostaffing/people?sort_id={sort.id}&operation_id={operation.id}"
        )
        by_department = self.client.get(
            f"/neostaffing/people?sort_id={sort.id}&operation_id={operation.id}&department_id={department.id}"
        )
        by_direct_work_area = self.client.get(
            f"/neostaffing/people?sort_id={sort.id}&operation_id={operation.id}&work_area_id={direct_work_area.id}"
        )

        self.assertEqual(initial.status_code, 200)
        self.assertIn(b"Select Sort", initial.data)
        self.assertIn(b"data-neostaffing-drilldown-select", initial.data)
        self.assertNotIn(b"Select Operation", initial.data)
        self.assertNotIn(b"Select Department", initial.data)
        self.assertNotIn(b"Step 1", initial.data)
        self.assertNotIn(b"LOAD ROSTER", initial.data)
        self.assertNotIn(b"ADD EMPLOYEE", initial.data)

        self.assertEqual(by_sort.status_code, 200)
        self.assertIn(b"Select Operation", by_sort.data)
        self.assertNotIn(b"Select Department", by_sort.data)
        self.assertNotIn(b"LOAD ROSTER", by_sort.data)
        self.assertIn(b"ASSIGN MANAGEMENT", by_sort.data)
        self.assertNotIn(b"ADD EMPLOYEE", by_sort.data)

        self.assertEqual(by_operation.status_code, 200)
        self.assertIn(b"Select Department", by_operation.data)
        self.assertIn(b"Select Direct Work Area", by_operation.data)
        self.assertIn(b"LOAD ROSTER", by_operation.data)
        self.assertNotIn(b"ADD EMPLOYEE", by_operation.data)

        self.assertEqual(by_department.status_code, 200)
        self.assertIn(b"Select Work Area", by_department.data)
        self.assertNotIn(b"ADD EMPLOYEE", by_department.data)

        self.assertEqual(by_direct_work_area.status_code, 200)
        self.assertIn(b"ADD EMPLOYEE", by_direct_work_area.data)
        self.assertIn(b"ASSIGN MANAGEMENT", by_direct_work_area.data)
        self.assertIn(b"LOAD ROSTER", by_direct_work_area.data)
        self.assertIn(b"PD100", by_direct_work_area.data)
        self.assertIn(b"Load Planning", by_direct_work_area.data)
        self.assertIn(b'<select name="person_id"', by_direct_work_area.data)
        self.assertNotIn(b"management_name", by_direct_work_area.data)
        self.assertNotIn(b"/neostaffing/people/attendance", by_direct_work_area.data)

    def test_people_view_blocks_user_without_neostaffing_access(self):
        user = self._user("staffing_people_blocked")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/people", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_people_view_renders_filterable_roster_and_detail_panel(self):
        user = self._user("staffing_people_data")
        self._grant_app_access(user, "neostaffing", "master")
        _sort, operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        avery = staffing_service.create_person(
            {
                "employee_id": "E810",
                "first_name": "Avery",
                "last_name": "Spotter",
                "seniority_date": "2019-02-01",
                "classification": "part_time",
            }
        )
        morgan = staffing_service.create_person(
            {
                "employee_id": "E811",
                "first_name": "Morgan",
                "last_name": "Loader",
                "seniority_date": "2021-03-01",
                "classification": "full_time_combo",
            }
        )
        supervisor = staffing_service.create_person(
            {
                "employee_id": "E812",
                "first_name": "Sam",
                "last_name": "Lead",
                "seniority_date": "2018-01-01",
                "classification": "part_time_supervisor",
            }
        )
        self._link_user_for_person(supervisor, "staffing_e812")
        staffing_service.assign_work_area(avery, work_area)
        staffing_service.assign_work_area(morgan, second_work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get(
            f"/neostaffing/people?work_area_id={work_area.id}&person_id={avery.id}"
        )
        searched = self.client.get(
            f"/neostaffing/people?work_area_id={work_area.id}&search=avery"
        )
        leadership = self.client.get(
            f"/neostaffing/people?work_area_id={work_area.id}&leadership_only=1&person_id={supervisor.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"E810", response.data)
        self.assertIn(b"Spotter, Avery", response.data)
        self.assertIn(b"EBM", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b"EDIT PERSON", response.data)
        self.assertIn(b"MOVE PERSON", response.data)
        self.assertIn(b"REMOVE PERSON", response.data)
        self.assertIn(b"ROSTER", response.data)
        self.assertIn(b"ADD EMPLOYEE", response.data)
        self.assertNotIn(b"OPEN IN APP MANAGEMENT", response.data)
        self.assertIn(b"Employee Status", response.data)
        self.assertNotIn(b"Roster Status", response.data)
        self.assertNotIn(b"roster_status", response.data)
        self.assertNotIn(b"<span>Status</span>", response.data)
        self.assertEqual(searched.status_code, 200)
        self.assertIn(b"E810", searched.data)
        self.assertNotIn(b"E811", searched.data)
        self.assertEqual(leadership.status_code, 200)
        self.assertIn(b"E812", leadership.data)
        self.assertIn(b"Part Time Supervisor", leadership.data)
        self.assertNotIn(b"E810", leadership.data)

    def test_people_view_is_work_area_roster_with_secondary_filters(self):
        user = self._user("staffing_people_filters")
        self._grant_app_access(user, "neostaffing", "master")
        _sort, _operation, _department, work_area = self._staffing_hierarchy()
        assigned = staffing_service.create_person(
            {
                "employee_id": "PF100",
                "first_name": "Assigned",
                "last_name": "Person",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        unassigned = staffing_service.create_person(
            {
                "employee_id": "PF101",
                "first_name": "Unassigned",
                "last_name": "Person",
                "seniority_date": "2020-01-02",
                "classification": "part_time",
            }
        )
        third = staffing_service.create_person(
            {
                "employee_id": "PF102",
                "first_name": "Third",
                "last_name": "Person",
                "seniority_date": "2020-01-03",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(assigned, work_area)
        staffing_service.assign_work_area(third, work_area)
        db.session.commit()
        self._login(user.username)

        assigned_response = self.client.get(f"/neostaffing/people?work_area_id={work_area.id}")
        filtered_response = self.client.get(f"/neostaffing/people?work_area_id={work_area.id}&search=assigned")

        self.assertEqual(assigned_response.status_code, 200)
        self.assertIn(b"ROSTER", assigned_response.data)
        self.assertIn(b"PF100", assigned_response.data)
        self.assertIn(b"PF102", assigned_response.data)
        self.assertNotIn(b"PF101", assigned_response.data)
        self.assertNotIn(b"<span>Limit</span>", assigned_response.data)
        self.assertNotIn(b"<span>Status</span>", assigned_response.data)
        self.assertEqual(filtered_response.status_code, 200)
        self.assertIn(b"PF100", filtered_response.data)
        self.assertNotIn(b"PF102", filtered_response.data)
        self.assertIsNotNone(third)

    def test_people_quick_assignment_requires_edit_role_and_updates_work_area(self):
        simulator = self._user("staffing_quick_assign_simulator")
        self._grant_app_access(simulator, "neostaffing", "simulator")
        operator = self._user("staffing_quick_assign_operator")
        self._grant_app_access(operator, "neostaffing", "operator")
        _sort, _operation, department, work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        person = staffing_service.create_person(
            {
                "employee_id": "QA100",
                "first_name": "Quick",
                "last_name": "Assign",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()

        operator_client = self._logged_in_client(operator.username)
        blocked = operator_client.post(
            f"/neostaffing/people/{person.id}/assign-work-area",
            data={"work_area_unit_id": str(second_work_area.id)},
            follow_redirects=False,
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked.location, "/neostaffing")
        self.assertEqual(db.session.get(StaffingPerson, person.id).work_assignment.work_area_unit_id, work_area.id)

        simulator_client = self._logged_in_client(simulator.username)
        updated = simulator_client.post(
            f"/neostaffing/people/{person.id}/assign-work-area",
            data={"work_area_unit_id": str(second_work_area.id)},
            follow_redirects=False,
        )

        self.assertEqual(updated.status_code, 302)
        self.assertIn("/neostaffing/people", updated.location)
        self.assertEqual(db.session.get(StaffingPerson, person.id).work_assignment.work_area_unit_id, second_work_area.id)

    def test_people_bulk_actions_assign_skip_management_and_clear_assignments(self):
        simulator = self._user("staffing_bulk_simulator")
        self._grant_app_access(simulator, "neostaffing", "simulator")
        operator = self._user("staffing_bulk_operator")
        self._grant_app_access(operator, "neostaffing", "operator")
        _sort, _operation, department, first_work_area = self._staffing_hierarchy()
        second_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "WBM", "parent_id": department.id}
        )
        part_time = staffing_service.create_person(
            {
                "employee_id": "BA100",
                "first_name": "Bulk",
                "last_name": "Part",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        combo = staffing_service.create_person(
            {
                "employee_id": "BA101",
                "first_name": "Bulk",
                "last_name": "Combo",
                "seniority_date": "2020-01-02",
                "classification": "full_time_combo",
            }
        )
        supervisor = staffing_service.create_person(
            {
                "employee_id": "BA102",
                "first_name": "Bulk",
                "last_name": "Supervisor",
                "seniority_date": "2019-01-01",
                "classification": "part_time_supervisor",
            }
        )
        staffing_service.assign_work_area(part_time, first_work_area)
        db.session.commit()

        operator_client = self._logged_in_client(operator.username)
        operator_page = operator_client.get("/neostaffing/people")
        operator_blocked = operator_client.post(
            "/neostaffing/people/bulk-work-area",
            data={
                "bulk_action": "move",
                "work_area_unit_id": str(second_work_area.id),
                "person_ids": [str(part_time.id), str(combo.id)],
            },
            follow_redirects=False,
        )

        simulator_client = self._logged_in_client(simulator.username)
        simulator_page = simulator_client.get(f"/neostaffing/people?work_area_id={first_work_area.id}")
        assigned = simulator_client.post(
            "/neostaffing/people/bulk-work-area",
            data={
                "bulk_action": "move",
                "work_area_unit_id": str(second_work_area.id),
                "person_ids": [str(part_time.id), str(combo.id), str(supervisor.id)],
            },
            follow_redirects=True,
        )
        cleared = simulator_client.post(
            "/neostaffing/people/bulk-work-area",
            data={
                "bulk_action": "clear",
                "person_ids": [str(part_time.id), str(combo.id)],
            },
            follow_redirects=True,
        )

        self.assertEqual(operator_page.status_code, 200)
        self.assertNotIn(b"APPLY BULK ACTION", operator_page.data)
        self.assertEqual(operator_blocked.status_code, 302)
        self.assertEqual(operator_blocked.location, "/neostaffing")
        self.assertEqual(simulator_page.status_code, 200)
        self.assertIn(b"APPLY BULK ACTION", simulator_page.data)
        self.assertIn(b"Select all visible", simulator_page.data)
        self.assertEqual(assigned.status_code, 200)
        self.assertIn(b"Bulk work-area action updated 2 people.", assigned.data)
        self.assertIn(b"Skipped management classifications", assigned.data)
        self.assertEqual(db.session.get(StaffingPerson, part_time.id).work_assignment.work_area_unit_id, second_work_area.id)
        self.assertEqual(db.session.get(StaffingPerson, combo.id).work_assignment.work_area_unit_id, second_work_area.id)
        self.assertIsNone(db.session.get(StaffingPerson, supervisor.id).work_assignment)
        self.assertEqual(cleared.status_code, 200)
        self.assertFalse(db.session.get(StaffingPerson, part_time.id).work_assignment.active)
        self.assertFalse(db.session.get(StaffingPerson, combo.id).work_assignment.active)

    def test_attendance_route_preselects_scope_and_updates_existing_daily_record(self):
        user = self._user("staffing_attendance_master")
        self._grant_app_access(user, "neostaffing", "master")
        sort, _operation, _department, work_area = self._staffing_hierarchy()
        person = staffing_service.create_person(
            {
                "employee_id": "AT100",
                "first_name": "Daily",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "employee_status": "fmla",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user.username)

        page = self.client.get(f"/neostaffing/attendance?work_area_id={work_area.id}&attendance_date=2026-07-03")
        self.assertEqual(page.status_code, 200)
        self.assertIn(f'<option value="{work_area.id}" selected>'.encode(), page.data)
        self.assertIn(b"AT100", page.data)

        first = self.client.post(
            "/neostaffing/attendance",
            data={
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                f"status_{person.id}": "call_in",
            },
            follow_redirects=True,
        )
        second = self.client.post(
            "/neostaffing/attendance",
            data={
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                f"status_{person.id}": "here",
            },
            follow_redirects=True,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 1)
        record = StaffingDailyAttendance.query.filter_by(person_id=person.id).first()
        self.assertEqual(record.status, "here")
        self.assertEqual(db.session.get(StaffingPerson, person.id).employee_status, "fmla")

    def test_attendance_defaults_to_logged_in_management_scope(self):
        user = self._user("staffing_attendance_scope_user")
        user.employee_id = "MS100"
        user.is_management = True
        user.management_level = "part_time_supervisor"
        self._grant_app_access(user, "neostaffing", "operator")
        _sort, _operation, _department, work_area = self._staffing_hierarchy()
        supervisor = staffing_service.create_person(
            {
                "employee_id": "MS100",
                "first_name": "Scope",
                "last_name": "Supervisor",
                "seniority_date": "2018-01-01",
                "classification": "part_time_supervisor",
            }
        )
        person = staffing_service.create_person(
            {
                "employee_id": "MS101",
                "first_name": "Assigned",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.create_leadership_assignment(supervisor, work_area)
        staffing_service.assign_work_area(person, work_area)
        db.session.commit()
        self._login(user.username)

        page = self.client.get("/neostaffing/attendance")

        self.assertEqual(page.status_code, 200)
        self.assertIn(f'<option value="{work_area.id}" selected>'.encode(), page.data)
        self.assertIn(b"MS101", page.data)

    def test_attendance_operation_scope_includes_direct_and_nested_work_areas(self):
        user = self._user("staffing_attendance_operation")
        self._grant_app_access(user, "neostaffing", "operator")
        sort, operation, _department, nested_work_area = self._staffing_hierarchy()
        direct_work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "Load Planning", "parent_id": operation.id}
        )
        nested_person = staffing_service.create_person(
            {
                "employee_id": "AO100",
                "first_name": "Nested",
                "last_name": "Worker",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        direct_person = staffing_service.create_person(
            {
                "employee_id": "AO101",
                "first_name": "Direct",
                "last_name": "Worker",
                "seniority_date": "2021-01-01",
                "classification": "full_time_combo",
            }
        )
        staffing_service.assign_work_area(nested_person, nested_work_area)
        staffing_service.assign_work_area(direct_person, direct_work_area)
        staffing_service.save_attendance(
            {
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "operation_id": str(operation.id),
                f"status_{nested_person.id}": "call_in",
                f"status_{direct_person.id}": "here",
            },
            user,
        )
        db.session.commit()
        self._login(user.username)

        page = self.client.get(
            f"/neostaffing/attendance?operation_id={operation.id}&attendance_date=2026-07-03"
        )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"AO100", page.data)
        self.assertIn(b"AO101", page.data)
        self.assertIn(b'option value="call_in" selected', page.data)
        self.assertIn(b"Loaded People", page.data)
        self.assertIn(b"2", page.data)

    def test_attendance_all_here_updates_existing_records_and_counts(self):
        user = self._user("staffing_attendance_all_here")
        self._grant_app_access(user, "neostaffing", "operator")
        sort, _operation, _department, work_area = self._staffing_hierarchy()
        first_person = staffing_service.create_person(
            {
                "employee_id": "AH100",
                "first_name": "First",
                "last_name": "Attend",
                "seniority_date": "2020-01-01",
                "classification": "part_time",
                "employee_status": "fmla",
            }
        )
        second_person = staffing_service.create_person(
            {
                "employee_id": "AH101",
                "first_name": "Second",
                "last_name": "Attend",
                "seniority_date": "2021-01-01",
                "classification": "full_time_combo",
            }
        )
        staffing_service.assign_work_area(first_person, work_area)
        staffing_service.assign_work_area(second_person, work_area)
        staffing_service.save_attendance(
            {
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                f"status_{first_person.id}": "call_in",
                f"status_{second_person.id}": "vacation",
            },
            user,
        )
        db.session.commit()
        self._login(user.username)

        saved = self.client.post(
            "/neostaffing/attendance",
            data={
                "attendance_date": "2026-07-03",
                "sort_id": str(sort.id),
                "work_area_id": str(work_area.id),
                "bulk_status": "here",
                f"status_{first_person.id}": "call_in",
                f"status_{second_person.id}": "vacation",
            },
            follow_redirects=True,
        )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(attendance_date=date(2026, 7, 3)).count(), 2)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(status="here").count(), 2)
        self.assertIn(b"Here", saved.data)
        self.assertEqual(db.session.get(StaffingPerson, first_person.id).employee_status, "fmla")

    def _user(self, username):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.title(),
            last_name="User",
            full_name=f"{username.title()} User",
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        user.set_password("Password123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _grant_app_access(self, user, app_code, role):
        access = PortalAppAccess(
            user_id=user.id,
            app_code=app_code,
            status="approved",
            role=role,
            is_active=True,
            approved_at=datetime.utcnow(),
        )
        db.session.add(access)
        db.session.flush()
        return access

    def _login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "Password123!"},
            follow_redirects=False,
        )

    def _logged_in_client(self, username):
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": username, "password": "Password123!"},
            follow_redirects=False,
        )
        return client

    def _link_user_for_person(self, person, username=None):
        username = username or f"user_{person.employee_id.lower()}"
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=person.first_name,
            last_name=person.last_name,
            full_name=person.full_name,
            employee_id=person.employee_id,
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        user.set_password("Password123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _staffing_hierarchy(self):
        sort = staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        operation = staffing_service.create_unit(
            {"unit_type": "operation", "name": "Shift Operation", "parent_id": sort.id}
        )
        department = staffing_service.create_unit(
            {
                "unit_type": "department",
                "name": "East Shift Department",
                "parent_id": operation.id,
            }
        )
        work_area = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "EBM", "parent_id": department.id}
        )
        return sort, operation, department, work_area


if __name__ == "__main__":
    unittest.main()
