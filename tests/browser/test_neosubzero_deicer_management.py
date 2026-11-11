"""Browser saves and live reconciliation on an isolated DUCC roster."""

import re
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingPerson, StaffingUnit, User
from tests.browser import test_neosubzero_ucc as fixtures


class DeicerManagementBrowserTest(unittest.TestCase):
    def test_picker_attendance_refresh_and_early_out(self):
        fixture = fixtures.UccBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            user = User.query.filter_by(username="drawer-admin").one()
            user.employee_id = "DEICE-MGR"
            night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            factory = fixtures.NeoSubZeroUccTest()
            ramp = factory._unit("operation", "Ramp", night)
            department = factory._unit("department", "Aviation Services", ramp)
            deice = factory._unit("work_area", "Deice", department)
            manager = factory._person(user.employee_id, "Deice", "Manager", deice)
            manager.classification = "full_time_supervisor"
            db.session.delete(manager.work_assignments[0])
            db.session.add(StaffingLeadershipAssignment(
                person_id=manager.id, unit_id=department.id, leadership_level="department",
            ))
            person = db.session.get(StaffingPerson, fixture.people[1])
            person.work_assignments[0].work_area_unit_id = deice.id
            for qualification in person.qualifications:
                db.session.delete(qualification)
            db.session.commit()
            person_id, person_name = person.id, person.full_name

        browser = fixture.pw.chromium.launch()
        self.addCleanup(browser.close)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
        board = context.new_page()
        fixture.login(board)
        fixture.ready(board, "/neosubzero/ucc")
        for width in (1366, 1920):
            board.set_viewport_size({"width": width, "height": 1080})
            self.assertLessEqual(board.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
            ramp_widths = []
            for prefix, ramp_name in zip("ABCDE", ("Alpha", "Bravo", "Charlie", "Delta", "Echo")):
                ramp = board.locator(f'[data-ucc-ramp="{ramp_name}"]')
                ramp_widths.append(ramp.bounding_box()["width"])
                rows = []
                for left, right in ((4, 8), (3, 7), (2, 6), (1, 5)):
                    left_y, right_y = [ramp.locator(f'[data-ucc-parking="{prefix}{position:02}"]').bounding_box()["y"]
                                       for position in (left, right)]
                    self.assertAlmostEqual(left_y, right_y, delta=1)
                    rows.append(left_y)
                self.assertTrue(all(top < bottom for top, bottom in zip(rows, rows[1:])))
            self.assertLessEqual(max(ramp_widths) - min(ramp_widths), 1)
        picker = board.locator("#ucc-staffing-picker")

        def assign(slot, field, value, index=0):
            form = board.locator(f'[data-ucc-editor="{slot}"] form').filter(has=board.locator(f'[name="{field}"]')).nth(index)
            form.locator("[data-ucc-staffing-choice]").click()
            with board.expect_response(lambda response: response.request.method == "POST") as saved:
                picker.locator(f'button[data-value="{value}"]').click()
            self.assertEqual(saved.value.status, 200, saved.value.text())
            expect(form.locator('[name="expected_version"]')).not_to_have_value("")
            expect(form.locator(f'[name="{field}"]')).to_have_value(str(value))

        expect(board.locator("[data-ucc-whos-left]")).to_contain_text(person_name)
        assign("Early Out-1", "person_id", person_id)
        expect(board.locator("[data-ucc-whos-left]")).to_contain_text(person_name)
        assign("Remote-1", "person_id", person_id)
        expect(board.locator("[data-ucc-whos-left]")).not_to_contain_text(person_name)
        assign("Early Out-1", "truck_number", "UCC-831")
        assign("Echo-3", "person_id", fixture.people[2], index=1)
        next_slot = board.locator('[data-ucc-editor="Bravo-2"]')
        next_slot.locator("[data-ucc-staffing-choice]").nth(1).click()
        expect(picker.locator(f'[data-value="{person_id}"]')).to_have_count(0)
        board.keyboard.press("Escape")
        next_slot.locator("summary").click()
        next_slot.locator("[data-ucc-enable-moves]").click()
        assign("Bravo-2", "person_id", person_id)
        expect(board.locator('[data-ucc-editor="Remote-1"] [name="person_id"]').first).to_have_value("")

        management = context.new_page()
        fixture.ready(management, "/neosubzero/deicer-management")
        select = management.locator(f'[name="status_{person_id}"]')
        with management.expect_response(lambda response: response.request.method == "POST") as saved:
            select.select_option("call_in")
        self.assertEqual(saved.value.status, 200, saved.value.text())
        expect(management.locator(f'[data-attendance-person="{person_id}"] [data-attendance-state]')).to_have_text("Saved")
        management.reload()
        expect(select).to_have_value("call_in")
        board.bring_to_front()
        for slot in ("Bravo-2", "Early Out-1"):
            unavailable = board.locator(f'[data-ucc-editor="{slot}"] .ucc-staffing-value.ucc-person-unavailable')
            expect(unavailable).to_have_count(1, timeout=15000)
            expect(unavailable).to_have_css("color", "rgb(255, 156, 156)")
        expect(board.locator("[data-ucc-whos-left]")).not_to_contain_text(person_name)
        board.evaluate("document.documentElement.requestFullscreen = undefined")
        board.locator("[data-ucc-tv-enter]").click()
        early = board.locator(".ucc-tv-early")
        expect(early).to_contain_text("UCC-831")
        expect(early.locator(".ucc-person-unavailable")).to_contain_text(person_name)
        expect(early.locator(".ucc-person-unavailable")).to_have_css("color", "rgb(255, 156, 156)")
        expect(board.locator(".ucc-tv-ramp")).to_have_count(5)
        self.assertTrue(re.search(r"Early Out", early.inner_text(), re.IGNORECASE))
