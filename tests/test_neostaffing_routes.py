from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    PortalAppAccess,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neostaffing as staffing_service


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

    def test_approved_neostaffing_user_can_open_dashboard(self):
        user = self._user("staffing_operator")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DASHBOARD", response.data)
        self.assertIn(b"ORGANIZATION HIERARCHY", response.data)
        self.assertIn(b"STAFFING BOARD", response.data)
        self.assertIn(b"TOTAL PLANNED STAFFING", response.data)
        self.assertIn(b"TOTAL ASSIGNED STAFFING", response.data)
        self.assertIn(b"TOTAL OPEN POSITIONS", response.data)
        self.assertIn(b"TOTAL EXTRA STAFFING", response.data)
        self.assertIn(b"MISSING LEADERSHIP", response.data)
        self.assertIn(b"Search work areas", response.data)
        self.assertIn(b"PEOPLE", response.data)
        self.assertIn(b"SENIORITY", response.data)
        self.assertIn(b"MANAGE", response.data)
        self.assertIn(b"APP MANAGEMENT", response.data)
        self.assertIn(b"neo-brand--apps", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b"Change Characters", response.data)

    def test_dashboard_uses_hierarchy_driven_operations_layout(self):
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
        db.session.add(StaffingWorkAssignment(person=person, work_area=work_area))
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Night Sort", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"EBM", response.data)
        self.assertIn(b"1 / 2", response.data)
        self.assertIn(b"1 OPEN", response.data)
        self.assertIn(b"Planned", response.data)
        self.assertIn(b"Assigned", response.data)
        self.assertIn(b"Open", response.data)
        self.assertIn(b"Extra", response.data)
        self.assertIn(b"Understaffed", response.data)
        self.assertIn(b"SORT ROLLUPS", response.data)
        self.assertIn(b"OPERATION ROLLUPS", response.data)
        self.assertIn(b"DEPARTMENT ROLLUPS", response.data)
        self.assertIn(b"PT Sup", response.data)
        self.assertIn(b"FT Sup", response.data)
        self.assertIn(b"VIEW PEOPLE", response.data)
        self.assertIn(b"VIEW SENIORITY", response.data)
        self.assertIn(b"MANAGE WORK AREA", response.data)

    def test_board_filters_understaffed_missing_leadership_and_search(self):
        user = self._user("staffing_board_filters")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": department.id,
                "required_headcount": "1",
            },
        )
        second_work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "WBM",
                "parent_id": department.id,
                "required_headcount": "3",
            }
        )
        staffing_service.assign_work_area(
            staffing_service.create_person(
                {
                    "employee_id": "21001",
                    "first_name": "Avery",
                    "last_name": "East",
                    "seniority_date": "2020-01-01",
                    "classification": "part_time",
                }
            ),
            work_area,
        )
        staffing_service.assign_work_area(
            staffing_service.create_person(
                {
                    "employee_id": "21002",
                    "first_name": "Morgan",
                    "last_name": "West",
                    "seniority_date": "2020-01-02",
                    "classification": "part_time",
                }
            ),
            second_work_area,
        )
        staffing_service.create_leadership_assignment(
            staffing_service.create_person(
                {
                    "employee_id": "21003",
                    "first_name": "Pat",
                    "last_name": "Lead",
                    "seniority_date": "2019-01-01",
                    "classification": "part_time_supervisor",
                }
            ),
            work_area,
        )
        staffing_service.create_leadership_assignment(
            staffing_service.create_person(
                {
                    "employee_id": "21004",
                    "first_name": "Fran",
                    "last_name": "Supervisor",
                    "seniority_date": "2018-01-01",
                    "classification": "full_time_supervisor",
                }
            ),
            department,
        )
        staffing_service.create_leadership_assignment(
            staffing_service.create_person(
                {
                    "employee_id": "21005",
                    "first_name": "Manny",
                    "last_name": "Manager",
                    "seniority_date": "2017-01-01",
                    "classification": "manager",
                }
            ),
            operation,
        )
        staffing_service.create_leadership_assignment(
            staffing_service.create_person(
                {
                    "employee_id": "21006",
                    "first_name": "Dana",
                    "last_name": "Division",
                    "seniority_date": "2016-01-01",
                    "classification": "division_manager",
                }
            ),
            sort,
        )
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing?understaffed_only=1&missing_leadership_only=1")
        searched = self.client.get("/neostaffing?search=EBM")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"WBM", response.data)
        self.assertIn(b"1 / 3", response.data)
        self.assertIn(b"Missing PT Supervisor", response.data)
        self.assertEqual(searched.status_code, 200)
        self.assertIn(b"EBM", searched.data)
        self.assertIn(b"1 / 1", searched.data)
        self.assertNotIn(b"1 / 3", searched.data)

    def test_board_planned_staffing_gap_analysis_and_rollups(self):
        user = self._user("staffing_gap_board")
        self._grant_app_access(user, "neostaffing", "master")
        sort, operation, department, work_area = self._staffing_hierarchy()
        understaffed = staffing_service.update_unit(
            work_area,
            {
                "unit_type": "work_area",
                "name": "EBM",
                "parent_id": department.id,
                "required_headcount": "4",
            },
        )
        overstaffed = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "East Primary",
                "parent_id": department.id,
                "required_headcount": "1",
            }
        )
        defaulted = staffing_service.create_unit(
            {"unit_type": "work_area", "name": "East Irregs", "parent_id": department.id}
        )
        for index, area in enumerate([understaffed, overstaffed, overstaffed, defaulted], start=1):
            person = staffing_service.create_person(
                {
                    "employee_id": f"2500{index}",
                    "first_name": f"Worker{index}",
                    "last_name": "Gap",
                    "seniority_date": "2020-01-01",
                    "classification": "part_time",
                }
            )
            staffing_service.assign_work_area(person, area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing")
        context = staffing_service.dashboard_context({})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"TOTAL PLANNED STAFFING", response.data)
        self.assertIn(b"TOTAL ASSIGNED STAFFING", response.data)
        self.assertIn(b"TOTAL OPEN POSITIONS", response.data)
        self.assertIn(b"TOTAL EXTRA STAFFING", response.data)
        self.assertIn(b"MOST UNDERSTAFFED", response.data)
        self.assertIn(b"MOST OVERSTAFFED", response.data)
        self.assertIn(b"Planned 4 / Assigned 1 / Gap 3", response.data)
        self.assertIn(b"Planned 1 / Assigned 2 / Extra 1", response.data)
        self.assertIn(b"Planned staffing defaults to assigned staffing.", response.data)
        self.assertEqual(context["summary"]["total_planned"], 6)
        self.assertEqual(context["summary"]["total_assigned"], 4)
        self.assertEqual(context["summary"]["total_open"], 3)
        self.assertEqual(context["summary"]["total_extra"], 1)
        self.assertEqual(context["summary"]["most_understaffed"][0]["unit"].name, "EBM")
        self.assertEqual(context["summary"]["most_overstaffed"][0]["unit"].name, "East Primary")
        overstaffed_card = next(card for card in context["work_area_cards"] if card["unit"].name == "East Primary")
        self.assertEqual(overstaffed_card["coverage"], 200)
        self.assertEqual(overstaffed_card["coverage_bar"], 100)

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

    def test_master_and_grandmaster_can_open_app_management(self):
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

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"APP MANAGEMENT", response.data)
                self.assertIn(b"WORK AREA HIERARCHY", response.data)
                self.assertIn(b"CLASSIFICATION MANAGEMENT", response.data)
                self.assertIn(b"LEADERSHIP", response.data)
                self.assertIn(b"PERMISSIONS", response.data)

    def test_lower_neostaffing_role_cannot_open_app_management(self):
        user = self._user("staffing_watcher")
        self._grant_app_access(user, "neostaffing", "watcher")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/app-management", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing")

    def test_app_management_crud_pages_require_master_or_grandmaster(self):
        paths = (
            "/neostaffing/app-management/hierarchy",
            "/neostaffing/app-management/planned-staffing",
            "/neostaffing/app-management/people",
            "/neostaffing/app-management/work-assignments",
            "/neostaffing/app-management/management-assignments",
        )
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

        for path in paths:
            with self.subTest(path=path, access="master"):
                master_client = self._logged_in_client(master.username)
                response = master_client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200, response.location)
            with self.subTest(path=path, access="operator"):
                operator_client = self._logged_in_client(operator.username)
                blocked = operator_client.get(path, follow_redirects=False)
                self.assertEqual(blocked.status_code, 302)
                self.assertEqual(blocked.location, "/neostaffing")
            with self.subTest(path=path, access="gateway-only"):
                gateway_client = self._logged_in_client(gateway_only.username)
                blocked = gateway_client.get(path, follow_redirects=False)
                self.assertEqual(blocked.status_code, 302)
                self.assertEqual(blocked.location, "/portal")

    def test_app_management_links_to_setup_pages(self):
        user = self._user("staffing_links_master")
        self._grant_app_access(user, "neostaffing", "master")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/neostaffing/app-management")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/neostaffing/app-management/hierarchy"', response.data)
        self.assertIn(b'href="/neostaffing/app-management/planned-staffing"', response.data)
        self.assertIn(b'href="/neostaffing/app-management/people"', response.data)
        self.assertIn(b'href="/neostaffing/app-management/work-assignments"', response.data)
        self.assertIn(b'href="/neostaffing/app-management/management-assignments"', response.data)
        self.assertIn(b"CLASSIFICATION MANAGEMENT", response.data)
        self.assertIn(b"PERMISSIONS", response.data)

    def test_app_management_crud_pages_use_operations_card_layout(self):
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

        for path, marker in (
            ("/neostaffing/app-management/hierarchy", b"UNIT CONTROL DECK"),
            ("/neostaffing/app-management/planned-staffing", b"PLANNED STAFFING DECK"),
            ("/neostaffing/app-management/people", b"PEOPLE CONTROL DECK"),
            ("/neostaffing/app-management/work-assignments", b"WORK AREA ASSIGNMENT DECK"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(marker, response.data)
                self.assertIn(b"neostaffing-record-card", response.data)

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

        page = self.client.get(f"/neostaffing/app-management/planned-staffing?work_area_id={work_area.id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"PLANNED STAFFING DECK", page.data)
        self.assertIn(b"1 Work Areas", page.data)
        self.assertIn(b"EBM", page.data)
        self.assertIn(b"DEFAULTED", page.data)
        self.assertIn(b"Difference", page.data)
        self.assertIn(b"Planned Staffing", page.data)
        self.assertIn(b"Assigned Staffing", page.data)
        self.assertIn(b"Open Positions", page.data)
        self.assertIn(b"Extra Staffing", page.data)

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
        self.assertIn(b"CONFIGURED", update.data)
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
        self.assertEqual(blocked.location, "/neostaffing")

        self.assertIsNotNone(second_work_area)

    def test_board_drilldown_links_detail_panel_and_required_status(self):
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

        response = self.client.get(f"/neostaffing?work_area_id={work_area.id}")
        defaulted = self.client.get(f"/neostaffing?work_area_id={default_area.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Planned Source", response.data)
        self.assertIn(b"Configured", response.data)
        self.assertIn(b"Department", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"Operation", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(
            f'href="/neostaffing/people?sort_id={sort.id}&amp;operation_id={operation.id}&amp;department_id={department.id}&amp;work_area_id={work_area.id}"'.encode(),
            response.data,
        )
        self.assertIn(
            f'href="/neostaffing/seniority?sort_id={sort.id}&amp;operation_id={operation.id}&amp;department_id={department.id}&amp;work_area_id={work_area.id}"'.encode(),
            response.data,
        )
        self.assertIn(
            f'href="/neostaffing/app-management/planned-staffing?sort_id={sort.id}&amp;operation_id={operation.id}&amp;department_id={department.id}&amp;work_area_id={work_area.id}"'.encode(),
            response.data,
        )
        self.assertIn(b"DEFAULT PLANNED STAFFING", response.data)
        self.assertEqual(defaulted.status_code, 200)
        self.assertIn(b"Defaulted", defaulted.data)
        self.assertIn(b"Planned staffing defaults to assigned staffing.", defaulted.data)

    def test_portal_tile_opens_neostaffing_for_approved_user(self):
        user = self._user("staffing_portal")
        self._grant_app_access(user, "neostaffing", "operator")
        db.session.commit()
        self._login(user.username)

        response = self.client.get("/portal")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"APPROVED Operator", response.data)
        self.assertIn(b'href="/neostaffing"', response.data)

    def test_seniority_view_loads_for_approved_user_and_links_from_dashboard(self):
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
        self.assertIn(b'href="/neostaffing/seniority"', dashboard.data)

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
        self.assertIn(b"PEOPLE", response.data)
        self.assertIn(b"FILTERS", response.data)
        self.assertIn(b"TOTAL EMPLOYEES", response.data)
        self.assertIn(b"Leadership Only", response.data)
        self.assertIn(b'href="/neostaffing/people"', dashboard.data)

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
        staffing_service.assign_work_area(avery, work_area)
        staffing_service.assign_work_area(morgan, second_work_area)
        staffing_service.create_leadership_assignment(supervisor, work_area)
        db.session.commit()
        self._login(user.username)

        response = self.client.get(
            f"/neostaffing/people?operation_id={operation.id}&person_id={avery.id}"
        )
        searched = self.client.get(
            f"/neostaffing/people?operation_id={operation.id}&search=avery"
        )
        leadership = self.client.get(
            f"/neostaffing/people?operation_id={operation.id}&leadership_only=1&person_id={supervisor.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"E810", response.data)
        self.assertIn(b"Spotter, Avery", response.data)
        self.assertIn(b"EBM", response.data)
        self.assertIn(b"East Shift Department", response.data)
        self.assertIn(b"Shift Operation", response.data)
        self.assertIn(b"CURRENT WORK ASSIGNMENT", response.data)
        self.assertIn(b"OPEN IN APP MANAGEMENT", response.data)
        self.assertIn(b"VIEW SENIORITY POSITION", response.data)
        self.assertEqual(searched.status_code, 200)
        self.assertIn(b"E810", searched.data)
        self.assertNotIn(b"E811", searched.data)
        self.assertEqual(leadership.status_code, 200)
        self.assertIn(b"E812", leadership.data)
        self.assertIn(b"Work Area Supervisor", leadership.data)
        self.assertNotIn(b"E810", leadership.data)

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
