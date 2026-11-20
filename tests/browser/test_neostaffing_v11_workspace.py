"""Local synthetic V11 screen evidence and interaction checks."""
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    Gateway, SortDateOperation, StaffingAttendanceOutcome, StaffingDailyAttendance, StaffingLeadershipAssignment,
    StaffingReportingRelationship, StaffingRosterClose, StaffingRosterPlacement, StaffingUnit, User,
)
from app.services import neostaffing as staffing
from tests.browser import test_neostaffing_shift_roster as shift_fixture


class StaffingWorkspaceBrowserTest(unittest.TestCase):
    DAY = date(2026, 11, 19)

    @classmethod
    def setUpClass(cls):
        shift_fixture.ShiftRosterBrowserTest.setUpClass()
        cls.fixture = shift_fixture.ShiftRosterBrowserTest.fixture
        cls.evidence = Path("instance/browser-evidence/neostaffing-v11").resolve()
        cls.evidence.mkdir(parents=True, exist_ok=True)
        cls.fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 19, 22)
        with cls.fixture.app.app_context():
            night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            hub = StaffingUnit(unit_type="operation", name="Hub", parent=night)
            outbound = StaffingUnit(unit_type="department", name="Outbound", parent=hub)
            gate_one = StaffingUnit(unit_type="work_area", name="Gate One", parent=outbound, required_headcount=3)
            gate_two = StaffingUnit(unit_type="work_area", name="Gate Two", parent=outbound, required_headcount=2)
            transfer = StaffingUnit(unit_type="work_area", name="Transfer", parent=hub, required_headcount=1)
            gateway = Gateway.query.filter_by(code="RFD").one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_date=cls.DAY, sort_name="night")
            db.session.add_all([hub, outbound, gate_one, gate_two, transfer, operation])
            db.session.flush()
            cls.area_id, cls.destination_id = gate_one.id, gate_two.id
            for index, (first, last, unit, status) in enumerate((
                ("Robin", "Hayes", gate_one, "here"), ("Morgan", "West", gate_one, "call_in"),
                ("Lee", "Emery", gate_two, "here"), ("Cameron", "North", gate_two, ""),
                ("Drew", "Gray", transfer, "here"), ("Rene", "Ames", None, ""),
            ), 1):
                person = staffing.create_person({
                    "first_name": first, "last_name": last, "employee_id": f"110001{index}",
                    "classification": "part_time", "seniority_date": f"2020-01-0{index}",
                })
                if index == 1:
                    cls.employee_id = person.id
                if unit:
                    staffing.assign_work_area(person, unit)
                if status:
                    db.session.add(StaffingDailyAttendance(
                        attendance_date=cls.DAY, sort_unit_id=night.id, work_area_unit_id=unit.id,
                        sort_date_operation_id=operation.id, person_id=person.id, status=status,
                        note="synthetic retained note",
                    ))
            leader = staffing.create_person({"first_name": "Amelia", "last_name": "Baker",
                "employee_id": "1100031", "classification": "division_manager", "seniority_date": "2010-02-01"})
            manager = staffing.create_person({"first_name": "Avery", "last_name": "Clarke",
                "employee_id": "1100032", "classification": "manager", "seniority_date": "2014-01-01"})
            User.query.filter_by(username="drawer-admin").one().employee_id = leader.employee_id
            db.session.add_all([
                StaffingLeadershipAssignment(person_id=leader.id, unit_id=night.id, leadership_level="sort"),
                StaffingLeadershipAssignment(person_id=manager.id, unit_id=hub.id, leadership_level="operation"),
                StaffingReportingRelationship(person_id=manager.id, reports_to_person_id=leader.id, active=True),
            ])
            db.session.commit()
            cls.manager_id = manager.id

    @classmethod
    def tearDownClass(cls):
        shift_fixture.ShiftRosterBrowserTest.tearDownClass()

    def capture_workspace(self, engine, widths):
        browser = getattr(self.fixture.pw, engine).launch()
        self.addCleanup(browser.close)
        for width in widths:
            with self.subTest(engine=engine, width=width):
                context = browser.new_context(viewport={"width": width, "height": 940 if width > 760 else 844},
                                              is_mobile=width < 760, has_touch=width < 760)
                context.route("**/*", lambda route: route.continue_()
                              if urlsplit(route.request.url).hostname == "127.0.0.1" else route.abort())
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                self.fixture().login(page)
                screens = {
                    "01-overview": "/neostaffing", "02-attendance": "/neostaffing/attendance",
                    "03-board": "/neostaffing/board", "04-shift-flow": "/neostaffing/shift-flow",
                    "05-people": f"/neostaffing/people?search=110001&inspect=1&person_id={self.employee_id}",
                    "06-management": f"/neostaffing/org-chart?person_id={self.manager_id}",
                    "07-structure": f"/neostaffing/org-chart?view=structure&unit_id={self.area_id}",
                    "08-daily-area": "/neostaffing/reports?report_type=staffing",
                    "09-attendance-history": f"/neostaffing/reports?report_type=attendance&search=Hayes&person_id={self.employee_id}",
                    "10-seniority": "/neostaffing/reports?report_type=seniority",
                    "11-menu": "/neostaffing/menu",
                }
                for label, path in screens.items():
                    with self.subTest(screen=label):
                        response = page.goto(self.fixture.origin + path)
                        self.assertEqual(response.status, 200, path)
                        self.assertEqual(urlsplit(page.url).path, urlsplit(path).path)
                        page.evaluate("document.fonts.ready")
                        if label != "04-shift-flow":
                            expect(page.locator(".v11-app")).to_be_visible()
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                            expect(page.locator("[data-mobile-drawer]")).to_have_count(0)
                            if width < 760:
                                expect(page.locator(".v11-dock a")).to_have_text(["⌂Home", "✓Attendance", "▤Board", "♙People", "☰Menu"], use_inner_text=False)
                        if label == "02-attendance":
                            expect(page.locator('input[name^="note_"]')).to_have_count(0)
                            expect(page.get_by_role("combobox", name="Attendance for Robin Hayes")).to_have_value("here")
                        if label == "04-shift-flow":
                            expect(page.locator(".shift-ballmat-sides")).to_be_visible()
                            expect(page.locator("[data-final-door-target]:visible")).to_have_count(
                                {390: 3, 430: 4, 1440: 12}[width])
                            grace = page.locator('[data-roster-person]').filter(has_text="Grace Door")
                            expect(grace).to_have_css("background-color", "rgb(44, 112, 78)")
                        if label == "05-people":
                            bounds = page.locator(".neostaffing-people-roster-workspace").bounding_box()
                            content = page.locator(".neostaffing-people-roster-table").bounding_box()
                            self.assertLessEqual(content["x"] + content["width"], bounds["x"] + bounds["width"] + 1)
                            inspector = page.locator(".v11-people-inspector").bounding_box()
                            if width > 760:
                                self.assertLessEqual(bounds["x"] + bounds["width"], inspector["x"])
                            else:
                                self.assertLessEqual(bounds["y"] + bounds["height"], inspector["y"])
                        if label == "06-management" and width < 760:
                            chart = page.locator("[data-org-chart-viewport]")
                            child = chart.locator(".staffing-reporting-card").filter(has_text="Avery Clarke")
                            expect(child).to_be_visible()
                            chart_box, child_box = chart.bounding_box(), child.bounding_box()
                            self.assertLessEqual(child_box["y"] + child_box["height"], chart_box["y"] + chart_box["height"] + 1)
                        if label == "09-attendance-history":
                            expect(page.get_by_role("searchbox", name="Find employee")).to_be_visible()
                            expect(page.locator(".v11-history")).to_contain_text("Here")
                            self.assertNotIn("synthetic retained note", page.content())
                        page.screenshot(path=str(self.evidence / f"{engine}-{width}-{label}.png"), full_page=True)
                self.assertFalse(errors, errors)
                context.close()

    def test_chromium(self):
        self.capture_workspace("chromium", (390, 430, 1440))

    def test_webkit(self):
        self.capture_workspace("webkit", (390, 1440))

    def test_historical_reports_show_sealed_placement_and_individual_marks(self):
        prior = self.DAY - timedelta(days=7)
        opened = datetime.combine(prior, datetime.min.time())
        with self.fixture.app.app_context():
            sort = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            gateway = Gateway.query.filter_by(code="RFD").one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_date=prior, sort_name="night")
            db.session.add(operation)
            db.session.flush()
            db.session.add(StaffingRosterClose(
                sort_date_operation_id=operation.id, gateway_code="RFD", sort_name="night", sort_date=prior,
                sort_unit_id=sort.id, opens_at=opened, closes_at=opened + timedelta(hours=8), sealed_at=opened + timedelta(hours=9),
            ))
            db.session.flush()
            db.session.add_all([
                StaffingRosterPlacement(sort_date_operation_id=operation.id, person_id=self.employee_id,
                    work_area_id=self.destination_id, area_path="Night / Hub / Outbound / Gate Two",
                    employee_id="1100011", employee_name="Robin Hayes", classification="part_time"),
                StaffingAttendanceOutcome(sort_date_operation_id=operation.id, person_id=self.employee_id,
                    gateway_code="RFD", sort_name="night", sort_unit_id=sort.id, sort_date=prior,
                    work_area_id=self.destination_id, employee_id="1100011", employee_name="Robin Hayes",
                    classification="part_time", status="call_in", recorded_at=opened, updated_at=opened),
            ])
            db.session.commit()
        self.fixture.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
        try:
            for engine, width in (("chromium", 1440), ("webkit", 390)):
                with self.subTest(engine=engine, viewport=width):
                    browser = getattr(self.fixture.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={"width": width, "height": 900}, is_mobile=width == 390)
                        self.fixture().login(page)
                        page.goto(self.fixture.origin + "/neostaffing/reports")
                        page.get_by_label("Date", exact=True).fill(str(prior))
                        page.get_by_role("button", name="Run report").click()
                        expect(page.get_by_role("heading", name="Night / Hub / Outbound / Gate Two")).to_be_visible()
                        expect(page.get_by_role("heading", name="Night / Hub / Outbound / Gate One")).to_have_count(0)
                        expect(page.locator(".v11-record-coverage").first).to_contain_text("Complete roster")
                        page.locator("[data-v11-report-select]").select_option("attendance")
                        page.get_by_role("searchbox", name="Find employee").fill("1100011")
                        page.get_by_label("From", exact=True).fill(str(prior))
                        page.get_by_label("Through", exact=True).fill(str(prior))
                        page.get_by_role("button", name="Search").click()
                        page.get_by_role("link", name="Robin Hayes 1100011").click()
                        expect(page.locator(".v11-history")).to_contain_text("Call In")
                        expect(page.locator(".v11-history")).to_contain_text("Night")
                        expect(page.locator(".v11-history")).not_to_contain_text("synthetic retained note")
                        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                    finally:
                        browser.close()
        finally:
            self.fixture.app.config["NEOSTAFFING_HISTORY_ENABLED"] = False

    def test_board_transfer_review_and_status_only_autosave(self):
        browser = self.fixture.pw.chromium.launch()
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        page = context.new_page()
        self.fixture().login(page)
        page.goto(self.fixture.origin + "/neostaffing/board")
        page.get_by_role("button", name="Robin Hayes Here").click()
        page.locator('[data-board-transfer] select').select_option(str(self.destination_id))
        page.get_by_role("button", name="Preview permanent transfer").click()
        preview = page.locator("[data-employee-transfer-dialog]")
        expect(preview).to_be_visible()
        expect(preview.locator('select[name="destination_work_area_id"]')).to_have_value(str(self.destination_id))
        preview.get_by_role("button", name="Cancel").click()
        page.locator("[data-employee-editor]").get_by_role("button", name="Close").click()
        expect(page.locator(f'[data-board-area="{self.area_id}"] [data-board-person="{self.employee_id}"]')).to_be_visible()
        page.get_by_role("navigation", name="Mobile Staffing workspaces").get_by_role("link", name="Attendance").click()
        status = page.get_by_role("combobox", name="Attendance for Cameron North")
        for value in ("scheduled_off", ""):
            with page.expect_response(lambda response: "/attendance/save" in response.url) as mutation:
                status.select_option(value)
            self.assertEqual(mutation.value.status, 200)
            expect(status.locator("..").get_by_role("status")).to_have_text("Saved")
            expect(status).to_have_value(value)
        page.reload()
        expect(status).to_have_value("")

    def test_z_confirm_initial_assignment_from_board(self):
        browser = self.fixture.pw.chromium.launch()
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 940})
        self.fixture().login(page)
        page.goto(self.fixture.origin + "/neostaffing/board")
        page.get_by_role("button", name="Rene Ames").click()
        page.locator('[data-board-transfer] select').select_option(str(self.destination_id))
        page.get_by_role("button", name="Preview assignment").click()
        preview = page.locator("[data-board-assignment]")
        expect(preview).to_contain_text("Assign Rene Ames")
        preview.get_by_role("button", name="Confirm Assignment").click()
        expect(page.locator(f'[data-board-area="{self.destination_id}"]')).to_contain_text("Rene Ames")
        expect(page.locator(".v11-unassigned")).to_have_count(0)
