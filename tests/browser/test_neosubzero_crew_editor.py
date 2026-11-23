"""Real compact crew editor interactions with isolated DUCC data."""
from pathlib import Path
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroTruck, SortDateOperation, StaffingDailyAttendance, StaffingPerson, StaffingUnit
from app.services.live_collaboration import entity_version
from app.services.neosubzero_staffing import DEICE_QUALIFICATION_KEY, set_staffing_person_qualification
from app.services.neosubzero_trucks import manage_truck
from tests.browser import test_neosubzero_ucc as fixtures


EVIDENCE = Path("instance/browser-evidence/ducc-working-dropdowns")


class CrewEditorBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        fixture = fixtures.UccBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            home = db.session.get(StaffingPerson, fixture.people[1]).work_assignments[0].work_area
            duplicate = fixtures.NeoSubZeroUccTest()._person("UCC-DUP", "Terry", "Longname-Deicer-1", home)
            set_staffing_person_qualification(duplicate, DEICE_QUALIFICATION_KEY, True)
            db.session.commit()
            duplicate_id = duplicate.id
        browser = getattr(fixture.pw, engine).launch()
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={"width": 1366, "height": 900}, service_workers="block")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        fixture.login(page)
        page.clock.install(time=fixture.sort_now)
        with page.expect_response(lambda response: "/ucc/revision" in response.url) as initial:
            fixture.ready(page, "/neosubzero/ucc")
        self.assertTrue(initial.value.json()["refresh"]["auto_refresh_enabled"])
        page.locator("[data-ucc-weather-toggle]").click()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        editor = page.locator(".ucc-team-controls:popover-open")
        close = editor.get_by_role("button", name="Close crew editor")
        team = lambda name: page.locator(f'.ucc-team[data-ucc-editor="{name}"]')
        pencil = lambda name, role: team(name).locator(f'[data-ucc-crew-edit="{role}"]')
        line = lambda name, role: team(name).locator(f'[data-ucc-crew-line="{role}"] .ucc-crew-value')
        field = lambda crew, role: crew.locator(f'[data-ucc-crew-form="{role}"] select')
        slot = "Bravo-2"

        def open_slot(name, role):
            pencil(name, role).click()
            expect(editor).to_have_count(1)

        def select(crew, role, value, *, match):
            field_control = field(crew, role)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith(match)) as saved:
                field_control.select_option(str(value))
            self.assertEqual(saved.value.status, 200, saved.value.text())
            expect(crew.locator("[data-ucc-management-save]")).to_have_text("Saved")
            expect(field_control).to_be_enabled()
            expect(field_control).to_have_value(str(value))

        geometry = '''() => Array.from(document.querySelectorAll('.ucc-ramp, [data-ucc-deice-pad], [data-ucc-parking]'),
            node => { const {x,y,width,height} = node.getBoundingClientRect(); return [x + scrollX,y + scrollY,width,height]; })'''
        for width in (1366, 1920):
            page.set_viewport_size({"width": width, "height": 1080 if width == 1920 else 900})
            expect(page.locator("[data-ucc-crew-edit]:visible")).to_have_count(1)
            expect(editor).to_have_count(0)
            for position in range(1, 5):
                empty = team(f"Bravo-{position}")
                expect(empty.locator(".ucc-crew-position:visible")).to_have_text(str(position))
                for role in ("truck", "driver", "flyer"):
                    expect(field(empty, role)).to_be_visible()
                    expect(field(empty, role).locator("option:checked")).to_have_text(role.capitalize())
                    expect(empty.locator(f'[data-ucc-crew-form="{role}"] [data-ucc-selected-text]')).to_have_text(role.capitalize())
                expect(field(empty, "truck").locator('option[value="12"]')).to_have_text("12")
                for person_id in (fixture.people[1], duplicate_id):
                    expect(field(empty, "driver").locator(f'option[value="{person_id}"]')).to_have_text("T. Longname-Deicer-1")
            expect(line("Alpha-1", "driver")).to_have_text("C. Longname-Deicer-0")
            page.screenshot(path=str(EVIDENCE / f"{engine}-{width}-compact.png"))
            pencil("Alpha-1", "driver").scroll_into_view_if_needed()
            before = page.evaluate(geometry)
            open_slot("Alpha-1", "driver")
            self.assertEqual(page.evaluate(geometry), before)
            box = editor.bounding_box()
            self.assertGreaterEqual(box["x"], 0)
            self.assertGreaterEqual(box["y"], 0)
            self.assertLessEqual(box["x"] + box["width"], width)
            self.assertLessEqual(box["y"] + box["height"], page.viewport_size["height"])
            expect(editor).to_have_accessible_name("EDIT CREW — ALPHA POSITION 1")
            expect(field(editor, "driver")).to_be_focused()
            expect(field(editor, "driver").locator("option:checked")).to_have_text("C. Longname-Deicer-0")
            editor.get_by_text("MOVE / SWAP", exact=True).click()
            self.assertEqual(page.evaluate(geometry), before)
            page.screenshot(path=str(EVIDENCE / f"{engine}-{width}-editor.png"))
            page.keyboard.press("Escape")
            expect(editor).to_have_count(0)
            expect(pencil("Alpha-1", "driver")).to_be_focused()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)

        # Exercise both columns and both rows without reusing a truck on two active slots.
        for position in (1, 3, 4):
            crew = team(f"Bravo-{position}")
            select(crew, "truck", "12", match="/truck")
            expect(line(f"Bravo-{position}", "truck")).to_have_text("12")
            open_slot(f"Bravo-{position}", "truck")
            select(crew, "truck", "", match="/truck")
            expect(editor).to_have_count(0)
            expect(field(crew, "truck").locator("option:checked")).to_have_text("Truck")

        select(team(slot), "truck", "12", match="/truck")
        select(team(slot), "driver", fixture.people[1], match="/ucc")
        select(team(slot), "flyer", duplicate_id, match="/ucc")
        expect(line(slot, "truck")).to_have_text("12")
        expect(line(slot, "driver")).to_have_text("T. Longname-Deicer-1")
        expect(line(slot, "flyer")).to_have_text("T. Longname-Deicer-1")

        open_slot(slot, "driver")
        expect(editor).to_have_accessible_name("EDIT CREW — BRAVO POSITION 2")
        driver = editor.locator('[data-ucc-crew-form="driver"] select')
        expect(driver.locator(f'option[value="{fixture.people[0]}"]')).to_have_attribute("hidden", "")
        expect(driver.locator(f'option[value="{fixture.people[1]}"]')).to_have_text("T. Longname-Deicer-1")
        expect(field(editor, "flyer")).to_have_value(str(duplicate_id))
        editor.get_by_text("MOVE / SWAP", exact=True).click()
        editor.get_by_role("button", name="MOVE PERSON INTO SLOT").click()
        driver.select_option(str(fixture.people[0]))
        confirmation = page.locator("[data-ucc-move-dialog]")
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_role("button", name="SWAP")).to_be_enabled()
        confirmation.get_by_role("button", name="CANCEL").click()
        expect(editor).to_have_count(1)
        expect(driver).to_have_value(str(fixture.people[1]))
        close.click()
        page.reload()
        open_slot(slot, "truck")
        expect(editor.locator('[name="truck_number"]')).to_have_value("12")
        expect(driver).to_have_value(str(fixture.people[1]))

        held = []
        page.route("**/neosubzero/truck", lambda route: held.append(route), times=1)
        truck = editor.locator('[name="truck_number"]')
        truck.select_option("UCC-831")
        expect(close).to_be_disabled()
        page.keyboard.press("Escape")
        expect(editor).to_have_count(1)
        self.assertEqual(len(held), 1)
        held[0].fulfill(status=409, json={"ok": False, "error": "Truck assignment changed. Review current values."})
        expect(editor.locator("[data-ucc-management-save]")).to_contain_text("Truck assignment changed")
        expect(truck).to_have_value("UCC-831")
        expect(line(slot, "truck")).to_have_text("12")
        select(team(slot), "truck", "12", match="/truck")

        def move(destination, position, resolution):
            editor.get_by_text("MOVE / SWAP", exact=True).click()
            unit = editor.locator("[data-ucc-unit-form]")
            unit.locator('[name="destination_ramp"]').select_option(destination)
            unit.locator('[name="destination_position"]').select_option(str(position))
            unit.locator('[name="resolution"]').select_option(resolution)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/ucc/unit")) as response:
                unit.get_by_role("button", name="APPLY UNIT MOVE").click()
            self.assertEqual(response.value.status, 200, response.value.text())
            expect(editor.locator("[data-ucc-management-save]")).to_have_text("Saved")

        move("Echo", 4, "move")
        expect(line("Echo-4", "truck")).to_have_text("12")
        open_slot("Echo-4", "truck")
        move("Alpha", 1, "swap")
        expect(line("Alpha-1", "truck")).to_have_text("12")
        expect(line("Echo-4", "driver")).to_have_text("C. Longname-Deicer-0")
        close.click()
        with fixture.app.app_context():
            operation = db.session.get(SortDateOperation, fixture.operation_id)
            truck_row = NeoSubZeroTruck.query.filter_by(number="12").one()
            manage_truck(operation.gateway, "deactivate_truck", {
                "truck_id": truck_row.id, "expected_version": entity_version(truck_row), "confirm": "yes",
            }, actor_id=None)
            person = db.session.get(StaffingPerson, fixture.people[1])
            db.session.add(StaffingDailyAttendance(person_id=person.id, attendance_date=operation.sort_date,
                sort_unit_id=StaffingUnit.query.filter_by(unit_type="sort", name="Night").one().id,
                sort_date_operation_id=operation.id, work_area_unit_id=person.work_assignments[0].work_area_unit_id, status="call_in"))
            db.session.commit()
        with page.expect_response(lambda response: "/ucc/revision" in response.url):
            page.clock.fast_forward(5000)
        expect(line("Alpha-1", "truck")).to_have_class("ucc-crew-value ucc-truck-unavailable", timeout=15000)
        expect(line("Alpha-1", "driver")).to_have_attribute("data-ucc-attendance", "unavailable")
        expect(line("Alpha-1", "driver")).to_have_text("T. Longname-Deicer-1")
        expect(line("Alpha-1", "truck")).to_have_text("12")
        page.evaluate("document.documentElement.requestFullscreen = undefined")
        page.locator("[data-ucc-tv-enter]").click()
        expect(page.locator(".ucc-tv-wallboard")).to_be_visible()
        expect(page.locator("[data-ucc-crew-edit]:visible")).to_have_count(0)
        page.get_by_role("button", name="Light appearance").click()
        expect(page.locator(".ucc-tv-wallboard")).to_have_attribute("data-ucc-tv-theme", "light")
        page.get_by_role("button", name="Exit TV Mode").click()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator("[data-ucc-crew-edit]:visible")).to_have_count(0)
        expect(page.locator(".ucc-pill select:visible").first).to_be_visible()
        page.locator('[data-ucc-view="ramp"]').click()
        expect(page.locator('[data-ucc-ramp="Alpha"] .ucc-ramp-diagram')).to_be_visible()
        self.assertEqual(errors, [])

    def test_chromium(self):
        self.exercise("chromium")

    def test_webkit(self):
        self.exercise("webkit")
