from datetime import date, datetime
from io import BytesIO
import unittest

from pypdf import PdfReader
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingVacationDaySelection,
    StaffingVacationManagementSelection,
    StaffingVacationUnionCalendarShare,
    StaffingVacationUnionSelection,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing_vacation as vacation_service
from app.services import neostaffing_vacation_reports as report_service
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingVacationReportsTest(unittest.TestCase):
    YEAR = 2027

    def setUp(self):
        config = type(
            "VacationReportsConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.client = self.app.test_client()
        self.units = self._hierarchy()
        self.owner = self._management_user("owner", "master")
        self.shared = self._user("shared", "operator")
        self.stranger = self._user("stranger", "operator")
        self.pt_earliest = self._union_person(
            "U001", "Zoe", "Able", "2000-01-01", "part_time"
        )
        self.pt_tie = self._union_person(
            "U002", "Amy", "Baker", "2000-01-01", "part_time"
        )
        self.ft = self._union_person(
            "U003", "Finn", "Combo", "2005-01-01", "full_time_combo"
        )
        self.management = self._person(
            "M001", "Morgan", "Manager", "1990-01-01", "manager"
        )
        db.session.add(
            StaffingLeadershipAssignment(
                person=self.management,
                unit=self.units["ramp"],
                leadership_level="operation",
                active=True,
            )
        )
        self.official = vacation_service.create_union_calendar(
            {
                "calendar_type": "official",
                "vacation_year": self.YEAR,
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "include_full_time": "1",
                "staffing_unit_ids": [self.units["blue_area"].id],
                "active": "1",
            },
            self.owner,
        )
        self.view = vacation_service.create_union_calendar(
            {
                "calendar_type": "view_only",
                "vacation_year": self.YEAR,
                "name": "My Ramp View",
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "include_full_time": "1",
                "staffing_unit_ids": [self.units["blue_area"].id],
                "active": "1",
            },
            self.owner,
        )
        db.session.add(
            StaffingVacationUnionCalendarShare(
                calendar=self.view,
                recipient_user_id=self.shared.id,
                shared_by_user_id=self.owner.id,
            )
        )
        self.approved = StaffingVacationUnionSelection(
            staffing_person_id=self.pt_earliest.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 1, 9),
            bank_type="regular",
            status="approved",
            entered_by_user_id=self.owner.id,
        )
        self.pending = StaffingVacationUnionSelection(
            staffing_person_id=self.pt_tie.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 1, 16),
            bank_type="regular",
            status="pending",
            entered_by_user_id=self.owner.id,
        )
        db.session.add_all([self.approved, self.pending])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_vacation_pdf_excludes_pending_and_has_landscape_contract(self):
        report = report_service.vacation_calendar_report_data(
            "union", self.official.id, self.YEAR, self.owner
        )
        all_entries = [
            entry for week in report["weeks"] for entry in week["whole_week_entries"]
        ]
        self.assertIn("Able, Zoe", all_entries)
        self.assertNotIn("Baker, Amy", all_entries)
        self.assertNotIn("PENDING", str(report))
        self.assertNotIn("OVER", str(report))

        pdf = report_service.build_vacation_calendar_pdf(
            report, created_on=datetime(2026, 8, 25, 12, 0)
        )
        reader = PdfReader(pdf)
        page = reader.pages[0]
        self.assertGreater(float(page.mediabox.width), float(page.mediabox.height))
        text = "\n".join(row.extract_text() or "" for row in reader.pages)
        self.assertIn(report["title"], text)
        self.assertIn(str(self.YEAR), text)
        self.assertIn("Created August 25, 2026", text)
        self.assertIn("Able, Zoe", text)
        self.assertNotIn("Baker, Amy", text)

    def test_view_only_owner_and_shared_recipient_can_print_but_stranger_cannot(self):
        owner_report = report_service.vacation_calendar_report_data(
            "union", self.view.id, self.YEAR, self.owner
        )
        shared_report = report_service.vacation_calendar_report_data(
            "union", self.view.id, self.YEAR, self.shared
        )
        self.assertEqual(owner_report["title"], "My Ramp View")
        self.assertEqual(shared_report["title"], "My Ramp View")
        with self.assertRaisesRegex(ValueError, "not available"):
            report_service.vacation_calendar_report_data(
                "union", self.view.id, self.YEAR, self.stranger
            )

    def test_management_calendar_print_uses_dynamic_view_access(self):
        selection = StaffingVacationManagementSelection(
            staffing_person_id=self.management.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 2, 6),
            selected_by_user_id=self.owner.id,
        )
        db.session.add(selection)
        db.session.commit()
        report = report_service.vacation_calendar_report_data(
            "management", self.units["night"].id, self.YEAR, self.owner
        )
        entries = [entry for week in report["weeks"] for entry in week["whole_week_entries"]]
        self.assertIn("Manager, Morgan", entries)

    def test_management_pdf_includes_read_only_pinned_availability(self):
        pinned_vacation = self._person(
            "DM001", "Vera", "Vacation", "1970-01-01", "division_manager"
        )
        pinned_special = self._person(
            "DM002", "Sam", "Special", "1971-01-01", "division_manager"
        )
        pinned_corporate = self._person(
            "DM003", "Cora", "Corporate", "1972-01-01", "division_manager"
        )
        for person in (pinned_vacation, pinned_special, pinned_corporate):
            db.session.add(
                StaffingLeadershipAssignment(
                    person=person,
                    unit=self.units["night"],
                    leadership_level="sort",
                    active=True,
                )
            )
        lower_selection = StaffingVacationManagementSelection(
            staffing_person_id=self.management.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 2, 6),
            selected_by_user_id=self.owner.id,
        )
        pinned_selection = StaffingVacationManagementSelection(
            staffing_person_id=pinned_vacation.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 1, 23),
            selected_by_user_id=self.owner.id,
        )
        db.session.add_all(
            [
                lower_selection,
                pinned_selection,
                StaffingVacationDaySelection(
                    staffing_person_id=pinned_special.id,
                    vacation_year=self.YEAR,
                    vacation_date=date(self.YEAR, 1, 25),
                    item_type="special_assignment",
                    status="scheduled",
                ),
                StaffingVacationDaySelection(
                    staffing_person_id=pinned_corporate.id,
                    vacation_year=self.YEAR,
                    vacation_date=date(self.YEAR, 1, 26),
                    item_type="corporate_class",
                    status="scheduled",
                ),
            ]
        )
        db.session.commit()

        report = report_service.vacation_calendar_report_data(
            "management", self.units["night"].id, self.YEAR, self.owner
        )
        self.assertEqual(len(report["pinned_rows"]), 3)
        pinned_text = str(report["pinned_rows"])
        self.assertIn("VACATION", pinned_text)
        self.assertIn("SPECIAL ASSIGNMENT", pinned_text)
        self.assertIn("CORPORATE CLASS", pinned_text)
        whole_entries = [
            entry for week in report["weeks"] for entry in week["whole_week_entries"]
        ]
        self.assertEqual(whole_entries, ["Manager, Morgan"])

        context = vacation_service.management_vacation_context(
            self.YEAR, self.owner, today=date(self.YEAR, 1, 20)
        )
        area = next(
            row for row in context["areas"] if row["area"].id == self.units["night"].id
        )
        self.assertEqual(
            sum(week["used"] for week in area["week_rows"]),
            1,
        )

        pdf = report_service.build_vacation_calendar_pdf(
            report, created_on=datetime(2026, 8, 25, 12, 0)
        )
        reader = PdfReader(pdf)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertGreater(
            float(reader.pages[0].mediabox.width),
            float(reader.pages[0].mediabox.height),
        )
        for value in (
            "Pinned Next Level - Read Only",
            "Vacation, Vera",
            "Special, Sam",
            "Corporate, Cora",
            "VACATION",
            "SPECIAL ASSIGNMENT",
            "CORPORATE CLASS",
        ):
            self.assertIn(value, text)

    def test_union_seniority_scope_filter_order_numbering_and_management_exclusion(self):
        both = report_service.union_seniority_report_data(
            self.units["ramp"].id, "both"
        )
        self.assertEqual(
            [row["number"] for row in both["rows"]], [1, 2, 3]
        )
        self.assertEqual(
            [row["employee_id"] for row in both["rows"]],
            ["U001", "U002", "U003"],
        )
        self.assertNotIn("M001", [row["employee_id"] for row in both["rows"]])
        self.assertEqual(
            [row["employee_id"] for row in report_service.union_seniority_report_data(
                self.units["ramp"].id, "pt"
            )["rows"]],
            ["U001", "U002"],
        )
        self.assertEqual(
            [row["employee_id"] for row in report_service.union_seniority_report_data(
                self.units["ramp"].id, "ft"
            )["rows"]],
            ["U003"],
        )

    def test_union_seniority_pdf_required_columns_metadata_and_portrait(self):
        report = report_service.union_seniority_report_data(
            self.units["blue_department"].id, "both"
        )
        pdf = report_service.build_union_seniority_pdf(
            report, created_on=datetime(2026, 8, 25, 12, 0)
        )
        reader = PdfReader(pdf)
        page = reader.pages[0]
        self.assertGreater(float(page.mediabox.height), float(page.mediabox.width))
        text = "\n".join(row.extract_text() or "" for row in reader.pages)
        for label in (
            "Union Seniority List",
            "Created August 25, 2026",
            "Blue Outbound",
            "PT + FT Union",
            "Last Name",
            "First Name",
            "Employee ID",
            "Seniority Date",
        ):
            self.assertIn(label, text)
        self.assertIn("1", text)

    def test_report_routes_render_controls_and_enforce_calendar_access(self):
        self._login(self.shared)
        reports = self.client.get("/neostaffing/reports?report_type=vacation_calendars&year=2027")
        self.assertEqual(reports.status_code, 200)
        self.assertIn(b"VACATION CALENDARS", reports.data)
        self.assertIn(b"My Ramp View", reports.data)
        pdf = self.client.get(
            f"/neostaffing/reports/vacation-calendar.pdf?kind=union&calendar_id={self.view.id}&year={self.YEAR}"
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        seniority_page = self.client.get(
            "/neostaffing/reports?report_type=union_seniority"
        )
        self.assertEqual(seniority_page.status_code, 200)
        self.assertIn(b"UNION SENIORITY LIST", seniority_page.data)
        seniority_pdf = self.client.get(
            "/neostaffing/reports/union-seniority.pdf"
            f"?scope_id={self.units['ramp'].id}&union_classification=both"
        )
        self.assertEqual(seniority_pdf.status_code, 200)
        self.assertEqual(seniority_pdf.mimetype, "application/pdf")

        self.client.get("/logout")
        self._login(self.stranger)
        blocked = self.client.get(
            f"/neostaffing/reports/vacation-calendar.pdf?kind=union&calendar_id={self.view.id}&year={self.YEAR}"
        )
        self.assertEqual(blocked.status_code, 302)

    def test_seniority_and_calendar_queries_are_bounded(self):
        statements = []

        def capture(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            report_service.union_seniority_report_data(self.units["ramp"].id, "both")
            seniority_count = len(statements)
            statements.clear()
            report_service.vacation_calendar_report_data(
                "union", self.official.id, self.YEAR, self.owner
            )
            calendar_count = len(statements)
            statements.clear()
            report_service.vacation_calendar_report_data(
                "management", self.units["night"].id, self.YEAR, self.owner
            )
            management_count = len(statements)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(seniority_count, 3)
        self.assertLessEqual(calendar_count, 10)
        self.assertLessEqual(management_count, 13)

    def _hierarchy(self):
        night = StaffingUnit(unit_type="sort", name="Night", display_order=1)
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=night, display_order=1)
        blue = StaffingUnit(unit_type="department", name="Blue Outbound", parent=ramp, display_order=1)
        area = StaffingUnit(unit_type="work_area", name="Blue Ramp", parent=blue, display_order=1)
        db.session.add_all([night, ramp, blue, area])
        db.session.flush()
        return {"night": night, "ramp": ramp, "blue_department": blue, "blue_area": area}

    def _person(self, employee_id, first, last, seniority, classification):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first,
            last_name=last,
            seniority_date=date.fromisoformat(seniority),
            classification=classification,
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        return person

    def _union_person(self, employee_id, first, last, seniority, classification):
        person = self._person(employee_id, first, last, seniority, classification)
        db.session.add(
            StaffingWorkAssignment(
                person=person, work_area=self.units["blue_area"], active=True
            )
        )
        db.session.flush()
        return person

    def _user(self, username, role, employee_id=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.title(),
            last_name="User",
            full_name=f"{username.title()} User",
            employee_id=employee_id or f"EMP-{username}",
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
        db.session.commit()
        return user

    def _management_user(self, username, role):
        employee_id = f"EMP-{username}"
        person = self._person(
            employee_id, username.title(), "Supervisor", "1995-01-01", "manager"
        )
        db.session.add(
            StaffingLeadershipAssignment(
                person=person,
                unit=self.units["ramp"],
                leadership_level="operation",
                active=True,
            )
        )
        db.session.commit()
        return self._user(username, role, employee_id=employee_id)

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
