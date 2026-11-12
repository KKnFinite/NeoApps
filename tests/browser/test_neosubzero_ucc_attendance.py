"""Both DUCC surfaces reconcile recorded NeoStaffing attendance while open."""
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    NeoSubZeroTruck, SortDateOperation, StaffingDailyAttendance,
    StaffingLeadershipAssignment, StaffingPerson, StaffingUnit, User,
)
from app.services.live_screen_refresh import save_live_screen_refresh_override
from app.services.neostaffing import create_person, save_attendance
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_staffing import DEICE_QUALIFICATION_KEY, set_staffing_person_qualification
from app.services.neosubzero_ucc import UCC_STAFFING_SECTIONS, set_neosubzero_ucc_assignment
from tests.browser import test_neosubzero_ucc as fixture


class DuccAttendanceBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixture.UccBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        case.refresh.stop()
        current_clock = patch(
            "app.services.node_refresh.current_gateway_local_datetime",
            return_value=datetime(2026, 10, 22, 22),
        )
        current_clock.start()
        self.addCleanup(current_clock.stop)
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            for key in ("neosubzero.ucc", "neosubzero.ucc_tv"):
                save_live_screen_refresh_override(operation.gateway, key, "5")
            sort = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            writer = User.query.filter_by(username="drawer-admin").one()
            writer.employee_id = "DUCC-ATTENDANCE-LEAD"
            manager = create_person({
                "employee_id": writer.employee_id, "first_name": "Fixture", "last_name": "Manager",
                "seniority_date": "2020-01-01", "classification": "division_manager",
            })
            db.session.add(StaffingLeadershipAssignment(person=manager, unit=sort, leadership_level="sort"))
            existing = db.session.get(StaffingPerson, case.people[0])
            area = db.session.get(StaffingUnit, existing.work_assignments[0].work_area_unit_id)
            scope = {"sort_id": str(sort.id), "work_area_id": str(area.id),
                     "sort_date_operation_id": str(operation.id)}
            builder = fixture.NeoSubZeroUccTest()
            people = []
            for index, ramp in enumerate(UCC_STAFFING_SECTIONS):
                for role, first in (("driver", "Alex"), ("flyer", "Taylor")):
                    person = builder._person(f"COLOR-{index}-{role}", first, f"Crew{index}", area)
                    set_staffing_person_qualification(person, DEICE_QUALIFICATION_KEY, True)
                    set_neosubzero_ucc_assignment(operation, ramp, 2, role, person)
                    people.append(person.id)
            db.session.add(StaffingDailyAttendance(
                person_id=people[0], sort_unit_id=sort.id, attendance_date=operation.sort_date,
                work_area_unit_id=area.id, status="here",
            ))
            set_neosubzero_ucc_truck(operation, "Alpha", 2, "12")
            set_neosubzero_ucc_truck(operation, "Echo", 2, "UCC-831")
            NeoSubZeroTruck.query.filter_by(number="UCC-831").one().active = False
            db.session.commit()

        def set_status(status, person_ids=people):
            with case.app.app_context(), patch(
                "app.services.neostaffing.current_night_attendance_operation",
                side_effect=lambda gateway: db.session.get(SortDateOperation, case.operation_id),
            ):
                writer = User.query.filter_by(username="drawer-admin").one()
                count = save_attendance(
                    {**scope, **{f"status_{person_id}": status for person_id in person_ids}}, writer,
                )
                self.assertEqual(count, len(person_ids))
                db.session.commit()

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        viewport = {"width": 1366, "height": 768}
        work, tv = [browser.new_page(viewport=viewport) for _ in range(2)]
        tv.add_init_script("localStorage.setItem('neoapps.neosubzero.ucc.tv-mode','1')")
        navigations = []
        for page in (work, tv):
            case.login(page)
            page.clock.install()
            with page.expect_response(lambda r: urlsplit(r.url).path.endswith("/ucc/revision")) as initial:
                case.ready(page, "/neosubzero/ucc")
            self.assertTrue(initial.value.json()["refresh"]["auto_refresh_enabled"])
            page.on("framenavigated", lambda frame, p=page: navigations.append(frame.url) if frame == p.main_frame else None)

        wallboard = tv.locator(".ucc-tv-wallboard")
        expect(wallboard).to_be_visible()
        working_names = work.locator('[data-ucc-editor$="-2"] .ucc-crew-compact [data-ucc-attendance]')
        tv_names = wallboard.locator('[data-ucc-tv-position="2"] [data-ucc-attendance]')
        expect(working_names).to_have_count(14)
        expect(tv_names).to_have_count(14)
        for name in (working_names.first, tv_names.first):
            expect(name).to_have_css("color", "rgb(146, 223, 166)")
        expect(tv_names.first).to_have_text("D · A. Crew0")
        expect(tv_names.first).to_have_css("font-weight", "750")
        expect(wallboard.locator('.ucc-tv-crew [data-ucc-tv-crew-field="truck"]')).to_have_count(2)
        expect(wallboard.locator('.ucc-tv-ramp').nth(1).locator('.ucc-tv-crew')).to_have_count(1)

        editor = work.locator('[data-ucc-editor="Alpha-2"] .ucc-team-controls')
        work.locator('[data-ucc-editor="Alpha-2"] [data-ucc-crew-edit="driver"]').click()
        focused = editor.locator('[data-ucc-crew-form="driver"] select')
        expect(focused).to_be_focused()
        original_value = focused.input_value()
        original_version = editor.locator('[data-ucc-crew-form="driver"] [name="expected_version"]').input_value()

        def tick(page):
            with page.expect_response(lambda r: urlsplit(r.url).path.endswith("/ucc/revision")):
                page.clock.fast_forward(5000)

        def assert_colors(names, rgb):
            # Wait on the rendered colors of all assigned names, including the focused crew.
            names.page.wait_for_function(
                """({selector, color}) => {
                    const fields = [...document.querySelectorAll(selector)];
                    return fields.length === 14 && fields.every(name => {
                        const style = getComputedStyle(name);
                        return style.color === color && style.backgroundColor === "rgba(0, 0, 0, 0)";
                    });
                }""",
                arg={"selector": names_selector[names], "color": rgb},
            )

        names_selector = {
            working_names: '[data-ucc-editor$="-2"] .ucc-crew-compact [data-ucc-attendance]',
            tv_names: '.ucc-tv-wallboard [data-ucc-tv-position="2"] [data-ucc-attendance]',
        }
        white, green, red = "rgb(244, 248, 252)", "rgb(146, 223, 166)", "rgb(255, 156, 156)"
        set_status("", [people[0]])
        tick(work)
        tick(tv)
        assert_colors(working_names, white)
        assert_colors(tv_names, white)
        expect(focused).to_have_css("color", white)
        with case.app.app_context():
            self.assertIsNone(StaffingDailyAttendance.query.filter_by(person_id=people[0]).first())

        transitions = (
            ("here", green, "rgb(29, 107, 60)"),
            ("call_in", red, "rgb(161, 42, 60)"),
            ("here", green, "rgb(29, 107, 60)"),
            ("", white, "rgb(0, 0, 0)"),
            ("scheduled_off", red, "rgb(161, 42, 60)"),
        )
        for width, light in ((1366, False), (1920, True)):
            for page in (work, tv):
                page.set_viewport_size({"width": width, "height": 768 if width == 1366 else 1080})
            if light:
                wallboard.get_by_role("button", name="Light appearance").click()
                expect(wallboard).to_have_attribute("data-ucc-tv-theme", "light")
                set_status("")
                tick(work)
                tick(tv)
                assert_colors(working_names, white)
                assert_colors(tv_names, "rgb(0, 0, 0)")

            geometry = wallboard.locator(".ucc-tv-stands").first.bounding_box()
            for status, dark_color, light_color in transitions:
                set_status(status)
                tick(work)
                tick(tv)
                assert_colors(working_names, dark_color)
                assert_colors(tv_names, light_color if light else dark_color)
                expect(focused).to_have_css("color", dark_color)
                expect(focused).to_be_focused()
                expect(focused).to_have_value(original_value)
                expect(editor.locator('[data-ucc-crew-form="driver"] [name="expected_version"]')).to_have_value(original_version)
                self.assertEqual(wallboard.locator(".ucc-tv-stands").first.bounding_box(), geometry)
            trucks = wallboard.locator('.ucc-tv-crew [data-ucc-tv-crew-field="truck"]')
            expect(trucks.first).to_have_css("color", "rgb(18, 95, 157)" if light else "rgb(121, 198, 255)")
            expect(trucks.last).to_have_css("color", "rgb(161, 42, 60)" if light else red)
            self.assertEqual(work.evaluate("document.documentElement.scrollWidth - innerWidth"), 0)
            self.assertLessEqual(wallboard.evaluate("el => el.scrollWidth"), width)
        self.assertEqual(navigations, [])

    def test_chromium(self):
        self.exercise("chromium")

    def test_webkit(self):
        self.exercise("webkit")
